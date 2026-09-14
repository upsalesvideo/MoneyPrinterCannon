"""Word-level timing for karaoke captions.

Backends: mlx (mlx_whisper, Apple Silicon) → faster (faster-whisper) → proportional.
`auto` = first available. Whisper runs on EACH scene file separately (short files, exact
scene edges), heard words are aligned onto the script words with difflib (text from the
script, times from whisper; unmatched words interpolated). Absolute times = local + scene start.

An external interpreter can host whisper: set CANNON_WHISPER_PYTHON=/path/to/python
(with mlx_whisper or faster_whisper installed) and it is used through a subprocess.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from loguru import logger

from .schema import SceneTiming, Script, Timing, VideoParams, Word
from .tts import SceneAudio

MLX_MODEL = os.environ.get("CANNON_MLX_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
FASTER_MODEL = os.environ.get("CANNON_FASTER_WHISPER_MODEL", "large-v3-turbo")
EDGE_PAD = 0.25
MIN_WORD = 0.02

_WHISPER_SNIPPET = r"""
import json, sys
path, backend, lang, mlx_model, faster_model = sys.argv[1:6]
lang = lang or None
words = []
if backend == "mlx":
    import mlx_whisper
    r = mlx_whisper.transcribe(path, path_or_hf_repo=mlx_model, language=lang, word_timestamps=True, verbose=False)
    for seg in r["segments"]:
        for w in seg.get("words", []):
            words.append({"w": w["word"].strip(), "s": float(w["start"]), "e": float(w["end"])})
else:
    from faster_whisper import WhisperModel
    m = WhisperModel(faster_model, device="auto", compute_type="auto")
    segs, _ = m.transcribe(path, language=lang, word_timestamps=True)
    for seg in segs:
        for w in seg.words or []:
            words.append({"w": w.word.strip(), "s": float(w.start), "e": float(w.end)})
print(json.dumps(words, ensure_ascii=False))
"""


# ---------------------------------------------------------------------------
# backend discovery
# ---------------------------------------------------------------------------
def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001 — import errors of optional deps
        return False


def _external_python() -> str:
    return os.environ.get("CANNON_WHISPER_PYTHON", "").strip()


def _external_has(backend: str) -> bool:
    py = _external_python()
    if not py or not Path(py).exists():
        return False
    mod = "mlx_whisper" if backend == "mlx" else "faster_whisper"
    r = subprocess.run([py, "-c", f"import {mod}"], capture_output=True, text=True)
    return r.returncode == 0


def available_backends() -> list[str]:
    out: list[str] = []
    if _has_module("mlx_whisper") or _external_has("mlx"):
        out.append("mlx")
    if _has_module("faster_whisper") or _external_has("faster"):
        out.append("faster")
    out.append("proportional")
    return out


def pick_backend(requested: str) -> str:
    avail = available_backends()
    if requested == "auto":
        return avail[0]
    if requested in avail:
        return requested
    logger.warning("timing backend {!r} not available (have {}), using proportional", requested, avail)
    return "proportional"


# ---------------------------------------------------------------------------
# whisper
# ---------------------------------------------------------------------------
def whisper_words(path: Path, backend: str, language: Optional[str]) -> list[dict]:
    """[{w, s, e}] in file-local seconds. Words shorter than MIN_WORD are dropped."""
    lang = None if not language or language == "auto" else language[:2]
    words: list[dict]
    if backend == "mlx" and _has_module("mlx_whisper"):
        import mlx_whisper  # type: ignore

        r = mlx_whisper.transcribe(str(path), path_or_hf_repo=MLX_MODEL, language=lang, word_timestamps=True, verbose=False)
        words = [{"w": w["word"].strip(), "s": float(w["start"]), "e": float(w["end"])}
                 for seg in r["segments"] for w in seg.get("words", [])]
    elif backend == "faster" and _has_module("faster_whisper"):
        from faster_whisper import WhisperModel  # type: ignore

        model = _faster_model()
        segs, _ = model.transcribe(str(path), language=lang, word_timestamps=True)
        words = [{"w": w.word.strip(), "s": float(w.start), "e": float(w.end)} for seg in segs for w in (seg.words or [])]
    else:
        py = _external_python()
        if not py:
            raise RuntimeError(f"whisper backend {backend} not importable and CANNON_WHISPER_PYTHON not set")
        r = subprocess.run([py, "-c", _WHISPER_SNIPPET, str(path), backend, lang or "", MLX_MODEL, FASTER_MODEL],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"external whisper failed: {r.stderr[-500:]}")
        words = json.loads(r.stdout.strip().splitlines()[-1])
    return [w for w in words if w["w"] and (w["e"] - w["s"]) >= MIN_WORD]


_faster_cache: dict = {}


def _faster_model():
    from faster_whisper import WhisperModel  # type: ignore

    if "m" not in _faster_cache:
        _faster_cache["m"] = WhisperModel(FASTER_MODEL, device="auto", compute_type="auto")
    return _faster_cache["m"]


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------
def norm(w: str) -> str:
    return re.sub(r"[^0-9a-zа-яёіїєґ]", "", w.lower().replace("ё", "е"))


def tokenize(text: str) -> list[str]:
    return [t for t in re.sub(r"\s+", " ", text.strip()).split(" ") if norm(t)]


def proportional_words(tokens: list[str], start: float, end: float, pad: float = EDGE_PAD) -> list[Word]:
    """Distribute tokens inside [start+pad, end-pad] proportionally to their length."""
    if not tokens:
        return []
    span = max(0.2, end - start)
    p = min(pad, span * 0.15)
    lo, hi = start + p, end - p
    weights = [len(norm(t)) + 1 for t in tokens]
    total = float(sum(weights))
    out: list[Word] = []
    t = lo
    for tok, wgt in zip(tokens, weights):
        d = (hi - lo) * wgt / total
        out.append(Word(s=round(t, 3), e=round(max(t + MIN_WORD, t + d - 0.02), 3), w=tok))
        t += d
    return out


def align_words(tokens: list[str], heard: list[dict], duration: float) -> list[Word]:
    """Script tokens get whisper times; unmatched tokens are interpolated between neighbours."""
    if not tokens:
        return []
    if not heard:
        return proportional_words(tokens, 0.0, duration)
    a = [norm(t) for t in tokens]
    b = [norm(h["w"]) for h in heard]
    starts: list[Optional[float]] = [None] * len(a)
    ends: list[Optional[float]] = [None] * len(a)
    matched = 0
    for tag, a0, a1, b0, b1 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(a1 - a0):
                starts[a0 + k] = float(heard[b0 + k]["s"])
                ends[a0 + k] = float(heard[b0 + k]["e"])
            matched += a1 - a0
        elif tag == "replace":
            # whisper heard *something* here (e.g. "16" for "шестнадцать"): reuse its time span
            if a1 - a0 == b1 - b0:
                for k in range(a1 - a0):
                    starts[a0 + k] = float(heard[b0 + k]["s"])
                    ends[a0 + k] = float(heard[b0 + k]["e"])
            else:
                lo, hi = float(heard[b0]["s"]), float(heard[b1 - 1]["e"])
                weights = [len(a[k]) + 1 for k in range(a0, a1)]
                tot = float(sum(weights))
                t = lo
                for k, wgt in zip(range(a0, a1), weights):
                    d = (hi - lo) * wgt / tot
                    starts[k], ends[k] = t, t + d
                    t += d
    if matched < max(1, len(a) // 4):
        logger.warning("alignment matched only {}/{} words; falling back to proportional for this scene", matched, len(a))
        return proportional_words(tokens, 0.0, duration)
    # anchors at the edges so interpolation always has bounds
    first_heard, last_heard = float(heard[0]["s"]), float(heard[-1]["e"])
    if starts[0] is None:
        starts[0] = max(0.0, min(first_heard, next(s for s in starts if s is not None) - 0.05))
    if starts[-1] is None:
        prev = max(s for s in starts if s is not None)
        starts[-1] = max(prev + MIN_WORD, min(duration - 0.1, last_heard - 0.15))
    i = 0
    while i < len(starts):
        if starts[i] is not None:
            i += 1
            continue
        j = i
        while starts[j] is None:
            j += 1
        lo, hi = starts[i - 1], starts[j]
        span = [len(a[k]) + 1 for k in range(i - 1, j + 1)]
        acc, tot = 0, sum(span[:-1])
        for k in range(i, j):
            acc += span[k - i]
            starts[k] = lo + (hi - lo) * acc / tot  # type: ignore[operator]
        i = j
    out: list[Word] = []
    for k, tok in enumerate(tokens):
        s = float(starts[k])  # type: ignore[arg-type]
        if out and s < out[-1].s + MIN_WORD:
            s = out[-1].s + MIN_WORD
        nxt = float(starts[k + 1]) if k + 1 < len(tokens) else duration  # type: ignore[arg-type]
        e = ends[k] if ends[k] is not None else nxt - 0.02
        e = min(max(s + MIN_WORD, float(e)), max(s + MIN_WORD, nxt - 0.005 if k + 1 < len(tokens) else duration))
        out.append(Word(s=round(s, 3), e=round(e, 3), w=tok))
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build_timing(script: Script, scene_audios: list[SceneAudio], params: VideoParams, task_dir: Path) -> Timing:
    task_dir = Path(task_dir)
    backend = pick_backend(params.timing_backend)
    voice_total = scene_audios[-1].end if scene_audios else 0.0
    voice_wav = task_dir / "voice" / "voice.wav"
    if voice_wav.is_file():
        from .media import duration as media_duration

        voice_total = max(voice_total, media_duration(voice_wav))
    logger.info("timing: backend={} scenes={} voice={:.1f}s", backend, len(scene_audios), voice_total)

    per_scene_files = all(sa.path and (task_dir / sa.path).is_file() for sa in scene_audios)
    words: list[Word] = []
    scenes: list[SceneTiming] = []

    if backend == "proportional" or (not per_scene_files and not voice_wav.is_file()):
        backend = "proportional"
        for sc, sa in zip(script.scenes, scene_audios):
            words += proportional_words(tokenize(sc.narration), sa.start, sa.end)
            scenes.append(SceneTiming(index=sc.index, start=sa.start, end=sa.end, audio_file=sa.path))
    elif per_scene_files:
        for sc, sa in zip(script.scenes, scene_audios):
            toks = tokenize(sc.narration)
            try:
                heard = whisper_words(task_dir / sa.path, backend, script.language)
                local = align_words(toks, heard, sa.duration)
            except Exception as e:  # noqa: BLE001 — never fail the pipeline on whisper
                logger.warning("scene {}: whisper failed ({}); proportional", sc.index, e)
                local = proportional_words(toks, 0.0, sa.duration)
            words += [Word(s=round(w.s + sa.start, 3), e=round(min(w.e + sa.start, sa.end), 3), w=w.w) for w in local]
            scenes.append(SceneTiming(index=sc.index, start=sa.start, end=sa.end, audio_file=sa.path))
    else:
        # custom voice: one file, align the whole script, derive scene edges from words
        toks_per_scene = [tokenize(sc.narration) for sc in script.scenes]
        all_toks = [t for toks in toks_per_scene for t in toks]
        try:
            heard = whisper_words(voice_wav, backend, script.language)
            aligned = align_words(all_toks, heard, voice_total)
        except Exception as e:  # noqa: BLE001
            logger.warning("whisper failed on custom voice ({}); proportional", e)
            aligned = proportional_words(all_toks, 0.0, voice_total)
        words = aligned
        pos = 0
        prev_end = 0.0
        for i, (sc, toks) in enumerate(zip(script.scenes, toks_per_scene)):
            seg = aligned[pos : pos + len(toks)]
            pos += len(toks)
            start = prev_end
            end = voice_total if i == len(script.scenes) - 1 else (seg[-1].e + (aligned[pos].s - seg[-1].e) / 2 if seg and pos < len(aligned) else start)
            scenes.append(SceneTiming(index=sc.index, start=round(start, 3), end=round(max(end, start + 0.1), 3), audio_file=""))
            prev_end = scenes[-1].end

    words = _monotonic(words, voice_total)
    timing = Timing(backend=backend, words=words, scenes=scenes, voice_duration=round(voice_total, 3))
    (task_dir / "timing.json").write_text(timing.model_dump_json(indent=1), encoding="utf-8")
    logger.info("timing: {} words written ({})", len(words), backend)
    return timing


def _monotonic(words: list[Word], total: float) -> list[Word]:
    out: list[Word] = []
    for w in words:
        s, e = w.s, w.e
        if out and s < out[-1].e:
            s = out[-1].e
        if total:
            s = min(s, max(0.0, total - MIN_WORD))
            e = min(e, total)
        e = max(e, s + MIN_WORD)
        out.append(Word(s=round(s, 3), e=round(e, 3), w=w.w))
    return out


if __name__ == "__main__":
    print("backends:", available_backends())
    if len(sys.argv) > 2:
        print(json.dumps(whisper_words(Path(sys.argv[1]), pick_backend("auto"), sys.argv[2]), ensure_ascii=False)[:800])
