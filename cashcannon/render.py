"""props.json → Remotion render (silent) → ffmpeg mix (voice + ducked music, loudnorm)
→ final.mp4 + cover.jpg."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from .config import REMOTION_DIR, TASKS_DIR, settings
from .media import MediaError, probe, run_ffmpeg
from .schema import ASPECT_SIZE, Script, Timing, VideoParams, VisualAsset

FPS = 30
CAPTION_FONT_SIZE = {"9:16": 78, "16:9": 56, "1:1": 64}
THEME_BG = "#0a0e0c"
THEME_FONT = "Onest"
RENDER_TIMEOUT = 30 * 60


# ---------------------------------------------------------------------------
# props
# ---------------------------------------------------------------------------
def build_props(script: Script, timing: Timing, visuals: list[VisualAsset], params: VideoParams, task_dir: Path,
                music_path: Optional[Path] = None) -> dict:
    task_dir = Path(task_dir)
    task_id = task_dir.name
    w, h = ASPECT_SIZE[params.aspect]
    by_index = {a.index: a for a in visuals}
    voice_dur = float(timing.voice_duration or (timing.scenes[-1].end if timing.scenes else 0.0))
    scenes: list[dict] = []
    for i, st in enumerate(timing.scenes):
        a = by_index.get(st.index)
        if a is None:
            raise ValueError(f"no visual asset for scene {st.index}")
        start = float(st.start) if i else 0.0
        end = voice_dur if i == len(timing.scenes) - 1 else float(timing.scenes[i + 1].start)
        end = max(end, float(st.end))
        length = max(0.1, end - start)
        if a.kind == "video":
            kb = "in" if params.ken_burns in ("auto", "in") else "none"
        else:
            kb = a.ken_burns or ("in" if params.ken_burns == "auto" else params.ken_burns)
        scenes.append({
            "index": st.index,
            "start": round(start, 3),
            "end": round(end, 3),
            "kind": a.kind,
            "src": f"tasks/{task_id}/{a.path}",
            "fit": params.fit,
            "kenBurns": kb,
            "loop": bool(a.kind == "video" and a.duration and a.duration < length),
            "srcDurationSec": round(a.duration, 3) if a.kind == "video" else 0,
        })
    outro = None
    if params.outro_text:
        outro = {"text": params.outro_text, "sub": params.outro_sub, "durSec": float(params.outro_seconds)}
    total = voice_dur + (outro["durSec"] if outro else 0.0)
    return {
        "aspect": params.aspect,
        "width": w,
        "height": h,
        "fps": FPS,
        "durationSec": round(total, 3),
        "scenes": scenes,
        "words": [{"s": wd.s, "e": wd.e, "w": wd.w} for wd in timing.words],
        "captions": {
            "enabled": bool(params.captions),
            "preset": params.caption_preset,
            "position": params.caption_position,
            "fontSize": params.caption_font_size or CAPTION_FONT_SIZE[params.aspect],
            "maxWords": params.caption_max_words,
            "uppercase": bool(params.caption_uppercase),
            "accent": params.accent_color,
            "emphasis": params.emphasis_color,
            "textColor": "#ffffff",
            "strokeColor": THEME_BG,
        },
        "title": {"text": script.title or script.topic, "durSec": 0} if params.title_card and (script.title or script.topic) else None,
        "outro": outro,
        "transition": params.transition,
        "progressBar": bool(params.progress_bar),
        "watermark": {"text": params.watermark} if params.watermark else None,
        "theme": {"accent": params.accent_color, "bg": THEME_BG, "font": THEME_FONT},
    }


def write_props(task_dir: Path, props: dict, name: str = "props.json") -> Path:
    p = Path(task_dir) / name
    p.write_text(json.dumps(props, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# remotion
# ---------------------------------------------------------------------------
def ensure_public_link() -> Path:
    """remotion/public/tasks -> storage/tasks (symlink) so staticFile('tasks/<id>/…') resolves."""
    pub = REMOTION_DIR / "public"
    pub.mkdir(parents=True, exist_ok=True)
    link = pub / "tasks"
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if link.resolve() == TASKS_DIR.resolve():
            return link
        link.unlink()
    elif link.exists():
        if link.is_dir() and not any(link.iterdir()):
            link.rmdir()
        else:
            raise RuntimeError(f"{link} exists and is not a symlink to {TASKS_DIR}; move it away")
    os.symlink(TASKS_DIR, link, target_is_directory=True)
    logger.info("linked {} -> {}", link, TASKS_DIR)
    return link


def _append_log(task_dir: Path, text: str) -> None:
    with (Path(task_dir) / "log.txt").open("a", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def ensure_node_modules() -> None:
    if (REMOTION_DIR / "node_modules" / "remotion").exists():
        return
    if not shutil.which("npm"):
        raise RuntimeError("npm not found; install Node.js 18+ to render")
    logger.info("remotion: node_modules missing → npm install (one time)")
    r = subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=REMOTION_DIR, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"npm install failed: {r.stderr[-800:]}")


def render_video(task_dir: Path, props: dict, out_name: str = "silent.mp4", props_name: str = "props.json") -> Path:
    task_dir = Path(task_dir)
    ensure_public_link()
    ensure_node_modules()
    props_path = write_props(task_dir, props, props_name)
    out = task_dir / "render" / out_name
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["npx", "remotion", "render", "src/index.ts", "Main", str(out), f"--props={props_path}",
           "--codec=h264", "--crf=16", "--muted", "--log=error"]
    if settings.render_concurrency:
        cmd.append(f"--concurrency={settings.render_concurrency}")
    logger.info("remotion render → {} ({:.1f}s, {} scenes)", out.name, props["durationSec"], len(props["scenes"]))
    t0 = time.time()
    _append_log(task_dir, "$ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=REMOTION_DIR, capture_output=True, text=True, timeout=RENDER_TIMEOUT,
                       env={**os.environ, "CI": "1"})
    if r.stdout:
        _append_log(task_dir, r.stdout[-6000:])
    if r.stderr:
        _append_log(task_dir, r.stderr[-6000:])
    if r.returncode != 0 or not out.is_file():
        raise RuntimeError(f"remotion render failed ({r.returncode}): {(r.stderr or r.stdout)[-800:]}")
    logger.info("remotion done in {:.0f}s", time.time() - t0)
    return out


# ---------------------------------------------------------------------------
# audio mix + mux
# ---------------------------------------------------------------------------
def mix_audio(task_dir: Path, silent_mp4: Path, voice_wav: Path, bgm: Optional[Path], music_volume: float, total_sec: float,
              out_name: str = "final.mp4") -> Path:
    """voice + (music looped, trimmed, faded, ducked by voice) → loudnorm -14 LUFS → mux with the silent video."""
    task_dir = Path(task_dir)
    log = task_dir / "log.txt"
    mix_wav = task_dir / "render" / (Path(out_name).stem + ".mix.wav")
    mix_wav.parent.mkdir(parents=True, exist_ok=True)
    total = max(0.5, float(total_sec))
    fade_start = max(0.0, total - 2.0)
    voice_chain = f"[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur={total:.3f},atrim=0:{total:.3f}"
    if bgm is not None and Path(bgm).is_file() and music_volume > 0:
        filt = (
            f"{voice_chain},asplit=2[vkey][vout];"
            f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
            f"volume={music_volume:.3f},afade=t=out:st={fade_start:.3f}:d=2[m];"
            f"[m][vkey]sidechaincompress=threshold=0.045:ratio=9:attack=15:release=350[mduck];"
            f"[vout][mduck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]"
        )
        args = ["-i", str(voice_wav), "-stream_loop", "-1", "-i", str(bgm), "-filter_complex", filt, "-map", "[a]",
                "-t", f"{total:.3f}", "-c:a", "pcm_s16le", str(mix_wav)]
    else:
        filt = f"{voice_chain},loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]"
        args = ["-i", str(voice_wav), "-filter_complex", filt, "-map", "[a]", "-t", f"{total:.3f}", "-c:a", "pcm_s16le", str(mix_wav)]
    run_ffmpeg(args, log=log, timeout=900)
    final = task_dir / out_name
    run_ffmpeg(["-i", str(silent_mp4), "-i", str(mix_wav), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(final)], log=log, timeout=900)
    mix_wav.unlink(missing_ok=True)
    return final


def make_cover(final_mp4: Path, cover: Path, at_sec: float = 1.0) -> Path:
    pr = probe(final_mp4)
    t = min(max(0.0, at_sec), max(0.0, pr.duration - 0.1))
    run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(final_mp4), "-frames:v", "1", "-q:v", "2", str(cover)])
    return cover


def cover_time(timing: Timing) -> float:
    if timing.scenes:
        s = timing.scenes[0]
        return max(0.5, min(1.0, (s.start + s.end) / 2))
    return 1.0


def verify_output(final_mp4: Path, expected_sec: float) -> float:
    pr = probe(final_mp4)
    if not pr.has_video:
        raise MediaError(f"{final_mp4.name}: no video stream")
    if not pr.has_audio:
        raise MediaError(f"{final_mp4.name}: no audio stream")
    if expected_sec and abs(pr.duration - expected_sec) > max(1.0, expected_sec * 0.05):
        logger.warning("{}: duration {:.2f}s differs from expected {:.2f}s", final_mp4.name, pr.duration, expected_sec)
    return pr.duration
