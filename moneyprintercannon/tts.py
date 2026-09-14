"""Per-scene TTS via Genosai (gemini-3.1-flash-tts), concat to voice/voice.wav.

One TTS task per scene (long narration is split into ≤900-char requests and glued), all
dispatched in parallel with the client's 1 s gap. Existing scene files are reused (no
double payment on resume). Scene boundaries come from the real file durations — these are
the exact scene edges used by timing.py and render.py.
"""
from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from .genosai import GenosaiClient, GenosaiValidationError
from .llm import split_sentences
from .media import duration as media_duration
from .media import is_valid_media, run_ffmpeg
from .schema import Script, VideoParams

TTS_MODEL = "gemini-3.1-flash-tts"
MAX_CHARS = 900


@dataclass
class SceneAudio:
    index: int
    path: str  # relative to task dir ("" for custom voice without per-scene files)
    duration: float
    start: float = 0.0
    end: float = 0.0
    chars: int = 0


def voice_dir(task_dir: Path) -> Path:
    d = task_dir / "voice"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_scene_audios(task_dir: Path, items: list[SceneAudio]) -> None:
    (voice_dir(task_dir) / "voice.json").write_text(json.dumps([asdict(i) for i in items], ensure_ascii=False, indent=1), encoding="utf-8")


def load_scene_audios(task_dir: Path) -> list[SceneAudio]:
    p = task_dir / "voice" / "voice.json"
    if not p.is_file():
        raise FileNotFoundError(p)
    return [SceneAudio(**d) for d in json.loads(p.read_text(encoding="utf-8"))]


def chunk_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split by sentences into ≤limit-char requests (a giant sentence is split by words)."""
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    cur = ""
    for s in split_sentences(text):
        if len(s) > limit:
            words = s.split()
            piece = ""
            for w in words:
                if len(piece) + len(w) + 1 > limit:
                    chunks.append(piece)
                    piece = w
                else:
                    piece = f"{piece} {w}".strip()
            s = piece
        if len(cur) + len(s) + 1 > limit and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def tts_input(text: str, params: VideoParams, client: Optional[GenosaiClient] = None) -> dict:
    payload = {
        "text": text,
        "voice": params.voice,
        "style": params.voice_style,
        "pace": params.voice_pace,
        "accent": params.voice_accent,
        "temperature": params.voice_temperature,
    }
    if client is not None:
        try:
            filtered = client.filter_input(TTS_MODEL, payload)
            if "text" in filtered:
                return filtered
        except Exception as e:  # noqa: BLE001 — catalog problems must not block TTS
            logger.debug("filter_input skipped: {}", e)
    return payload


def _rephrase(text: str, params: VideoParams, client: GenosaiClient) -> tuple[str, float]:
    """The TTS sometimes rejects a harmless sentence (400 'TTS generation failed'). A light
    rephrase fixes it; retries do not."""
    res = client.chat(
        params.text_model,
        [
            {"role": "system", "content": "You lightly rephrase text for a text-to-speech engine. Keep the meaning, "
                                          "language and approximate length. Change wording slightly, split or merge "
                                          "sentences if useful, write numbers in words. Return JSON {\"text\": \"...\"}."},
            {"role": "user", "content": text},
        ],
        temperature=0.6, max_tokens=800, json_mode=True,
    )
    from .llm import parse_json_object  # local import to avoid a cycle at module load

    new = str(parse_json_object(res.content).get("text") or "").strip()
    if not new or new == text:
        raise GenosaiValidationError(f"TTS rejected the text and rephrasing did not help: {text[:80]}")
    return new, res.cost


def _concat_wav(parts: list[Path], out: Path, gap_ms: int, log: Optional[Path] = None) -> None:
    """Concat audio files with `gap_ms` silence between them → 48 kHz stereo wav."""
    args: list[str] = []
    labels = []
    n = 0
    for i, p in enumerate(parts):
        args += ["-i", str(p)]
        labels.append(f"[{n}:a]")
        n += 1
        if i < len(parts) - 1 and gap_ms > 0:
            args += ["-f", "lavfi", "-t", f"{gap_ms / 1000:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            labels.append(f"[{n}:a]")
            n += 1
    pre = "".join(f"{lbl}aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[x{i}];" for i, lbl in enumerate(labels))
    filt = pre + "".join(f"[x{i}]" for i in range(len(labels))) + f"concat=n={len(labels)}:v=0:a=1[out]"
    run_ffmpeg([*args, "-filter_complex", filt, "-map", "[out]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], log=log)


def _to_mp3(src: Path, dst: Path) -> None:
    run_ffmpeg(["-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(dst)])


def synthesize(script: Script, params: VideoParams, task_dir: Path, client: GenosaiClient) -> tuple[list[SceneAudio], float]:
    task_dir = Path(task_dir)
    vdir = voice_dir(task_dir)
    log = task_dir / "log.txt"
    if params.custom_voice_file:
        return _use_custom_voice(script, params, task_dir), 0.0

    cost = 0.0
    scene_files: dict[int, Path] = {}
    # 1) which (scene, chunk) requests are still needed
    jobs: list[tuple[int, int, str]] = []  # (scene index, chunk no, text)
    chunks_per_scene: dict[int, list[str]] = {}
    for sc in script.scenes:
        out = vdir / f"scene-{sc.index:02d}.mp3"
        if out.is_file() and out.stat().st_size > 1000 and is_valid_media(out):
            scene_files[sc.index] = out
            continue
        chunks = chunk_text(sc.narration)
        chunks_per_scene[sc.index] = chunks
        for j, ch in enumerate(chunks):
            part = vdir / f"scene-{sc.index:02d}.part{j}.mp3"
            if part.is_file() and part.stat().st_size > 1000 and is_valid_media(part):
                continue
            jobs.append((sc.index, j, ch))
    if scene_files:
        logger.info("voice: reusing {} existing scene files", len(scene_files))

    # 2) run everything in parallel; validation failures get one rephrase round
    pending = jobs
    for round_no in range(2):
        if not pending:
            break
        logger.info("voice: {} TTS requests (round {})", len(pending), round_no + 1)
        results = client.run_many([(TTS_MODEL, tts_input(t, params, client)) for _, _, t in pending], raise_on_error=False)
        retry: list[tuple[int, int, str]] = []
        for (idx, j, text), res in zip(pending, results):
            if isinstance(res, GenosaiValidationError):
                if round_no == 1:
                    raise GenosaiValidationError(f"scene {idx}: TTS rejected the text twice: {res}")
                logger.warning("scene {}: TTS rejected the phrase ({}); rephrasing", idx, str(res)[:100])
                new_text, c = _rephrase(text, params, client)
                cost += c
                chunks_per_scene[idx][j] = new_text
                retry.append((idx, j, new_text))
                continue
            if isinstance(res, Exception):
                raise res
            cost += res.cost
            dest = vdir / f"scene-{idx:02d}.part{j}.mp3"
            raw = dest.with_suffix(".raw")
            client.download(res.url, raw, min_bytes=500)
            _to_mp3(raw, dest)  # the API returns WAV (pcm 24 kHz mono); keep task files uniform
            raw.unlink(missing_ok=True)
        pending = retry
    # narration changed by rephrasing → keep script.json in sync with what was voiced
    for sc in script.scenes:
        if sc.index in chunks_per_scene:
            new_narr = " ".join(chunks_per_scene[sc.index])
            if new_narr != sc.narration:
                sc.narration = new_narr

    # 3) glue chunks into scene files
    for sc in script.scenes:
        if sc.index in scene_files:
            continue
        parts = [vdir / f"scene-{sc.index:02d}.part{j}.mp3" for j in range(len(chunks_per_scene[sc.index]))]
        out = vdir / f"scene-{sc.index:02d}.mp3"
        if len(parts) == 1:
            shutil.move(parts[0], out)
        else:
            tmp = vdir / f"scene-{sc.index:02d}.tmp.wav"
            _concat_wav(parts, tmp, gap_ms=120, log=log)
            _to_mp3(tmp, out)
            tmp.unlink(missing_ok=True)
            for p in parts:
                p.unlink(missing_ok=True)
        scene_files[sc.index] = out

    items = _assemble(script, params, task_dir, scene_files, log)
    logger.info("voice: {} scenes, {:.1f}s total, cost {}", len(items), items[-1].end if items else 0, cost)
    return items, cost


def _assemble(script: Script, params: VideoParams, task_dir: Path, scene_files: dict[int, Path], log: Path) -> list[SceneAudio]:
    vdir = voice_dir(task_dir)
    ordered = [scene_files[sc.index] for sc in script.scenes]
    _concat_wav(ordered, vdir / "voice.wav", gap_ms=params.voice_gap_ms, log=log)
    items: list[SceneAudio] = []
    t = 0.0
    gap = params.voice_gap_ms / 1000.0
    for sc, p in zip(script.scenes, ordered):
        d = media_duration(p)
        items.append(SceneAudio(index=sc.index, path=str(p.relative_to(task_dir)), duration=round(d, 3),
                                start=round(t, 3), end=round(t + d, 3), chars=len(sc.narration)))
        t += d + gap
    total = media_duration(vdir / "voice.wav")
    if items:
        items[-1].end = round(min(items[-1].end, total), 3) if total else items[-1].end
    save_scene_audios(task_dir, items)
    return items


def _use_custom_voice(script: Script, params: VideoParams, task_dir: Path) -> list[SceneAudio]:
    src = Path(params.custom_voice_file).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"custom_voice_file not found: {src}")
    vdir = voice_dir(task_dir)
    out = vdir / "voice.wav"
    run_ffmpeg(["-i", str(src), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], log=task_dir / "log.txt")
    total = media_duration(out)
    chars = [max(1, len(s.narration)) for s in script.scenes]
    gap = params.voice_gap_ms / 1000.0
    speech = max(0.1, total - gap * (len(chars) - 1))
    items: list[SceneAudio] = []
    t = 0.0
    for sc, c in zip(script.scenes, chars):
        d = speech * c / sum(chars)
        items.append(SceneAudio(index=sc.index, path="", duration=round(d, 3), start=round(t, 3), end=round(t + d, 3), chars=c))
        t += d + gap
    items[-1].end = round(total, 3)
    save_scene_audios(task_dir, items)
    logger.info("voice: custom file {} ({:.1f}s), scene bounds are proportional until timing refines them", src.name, total)
    return items


def total_duration(items: list[SceneAudio]) -> float:
    return items[-1].end if items else 0.0


if __name__ == "__main__":
    print(chunk_text("Раз. " * 400)[:2])
    print(math.ceil(1.2))
