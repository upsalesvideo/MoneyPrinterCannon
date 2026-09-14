"""Small ffmpeg/ffprobe helpers shared by tts, visuals and render."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


class MediaError(RuntimeError):
    pass


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


@dataclass
class Probe:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    has_video: bool = False
    has_audio: bool = False
    codec: str = ""


def probe(path: str | Path) -> Probe:
    """ffprobe -> Probe. Raises MediaError if the file is unreadable."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        raise MediaError(f"missing or empty file: {p}")
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise MediaError(f"ffprobe failed for {p.name}: {r.stderr.strip()[:200]}")
    d = json.loads(r.stdout or "{}")
    out = Probe()
    try:
        out.duration = float((d.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        out.duration = 0.0
    for s in d.get("streams") or []:
        if s.get("codec_type") == "video" and not _is_cover_art(s):
            out.has_video = True
            out.width = int(s.get("width") or 0)
            out.height = int(s.get("height") or 0)
            out.codec = str(s.get("codec_name") or "")
            if not out.duration:
                try:
                    out.duration = float(s.get("duration") or 0)
                except (TypeError, ValueError):
                    pass
        elif s.get("codec_type") == "audio":
            out.has_audio = True
    return out


def _is_cover_art(stream: dict) -> bool:
    disp = stream.get("disposition") or {}
    return bool(disp.get("attached_pic")) or stream.get("codec_name") in ("mjpeg", "png") and stream.get("avg_frame_rate") in ("0/0", None)


def duration(path: str | Path) -> float:
    return probe(path).duration


def is_valid_media(path: str | Path) -> bool:
    try:
        pr = probe(path)
        return pr.has_video or pr.has_audio or pr.duration > 0 or pr.width > 0
    except MediaError:
        return False


def image_size(path: str | Path) -> tuple[int, int]:
    pr = probe(path)
    return pr.width, pr.height


def run_ffmpeg(args: list[str], log: Optional[Path] = None, timeout: int = 600) -> None:
    """ffmpeg -y -hide_banner -loglevel error <args>; stderr goes to the exception (and log)."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if log is not None:
        with log.open("a", encoding="utf-8") as f:
            f.write("$ " + " ".join(cmd) + "\n")
            if r.stderr:
                f.write(r.stderr[-4000:] + "\n")
    if r.returncode != 0:
        logger.error("ffmpeg failed: {}", r.stderr.strip()[-1500:])
        raise MediaError(f"ffmpeg failed ({r.returncode}): {r.stderr.strip()[-500:]}")


def kind_of(path: str | Path) -> str:
    """'image' | 'video' | 'audio' | '' by extension."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return ""
