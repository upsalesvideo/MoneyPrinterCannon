"""Background music: none | file | genosai (suno-v5.5). Genosai results are cached by
sha1(prompt) in storage/cache/music so re-runs never pay twice.

Note: in the live catalog suno-v5.5 `duration` requires `custom_mode=true`, which changes the
meaning of `prompt` (lyrics/style), so we do NOT send duration: the track is looped/trimmed
and faded by render.mix_audio anyway. Set CANNON_SUNO_DURATION=1 to send it regardless."""
from __future__ import annotations

import hashlib
import math
import os
import shutil
from pathlib import Path
from typing import Optional

from loguru import logger

from .config import CACHE_DIR
from .genosai import GenosaiClient
from .media import is_valid_media
from .schema import Script, VideoParams

MUSIC_MODEL = "suno-v5.5"
SUFFIX = ", instrumental, no vocals, background music for a short video"


def music_prompt_for(script: Script, params: VideoParams) -> str:
    base = (params.music_prompt or script.music_prompt or "").strip()
    if not base:
        base = "calm modern electronic background, light percussion, medium tempo, uplifting"
    if "instrumental" not in base.lower():
        base = base + SUFFIX
    return base[:480]


def music_duration(total_sec: float) -> int:
    return int(max(10, min(360, math.ceil(total_sec) + 5)))


def _cache_path(prompt: str, dur: int) -> Path:
    d = CACHE_DIR / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha1(f"{prompt}|{dur}".encode()).hexdigest() + ".mp3")


def get_music(script: Script, params: VideoParams, task_dir: Path, client: Optional[GenosaiClient], total_sec: float) -> tuple[Optional[Path], float]:
    task_dir = Path(task_dir)
    if params.music == "none":
        return None, 0.0
    mdir = task_dir / "music"
    mdir.mkdir(parents=True, exist_ok=True)
    dest = mdir / "bgm.mp3"
    if dest.is_file() and dest.stat().st_size > 10_000 and is_valid_media(dest):
        logger.info("music: reusing {}", dest.name)
        return dest, 0.0

    if params.music == "file":
        src = Path(params.music_file).expanduser()
        if not src.is_file():
            raise FileNotFoundError(f"music_file not found: {src}")
        shutil.copy2(src, dest)
        return dest, 0.0

    if client is None:
        raise RuntimeError("Genosai client required for music=genosai")
    prompt = music_prompt_for(script, params)
    dur = music_duration(total_sec)
    send_duration = os.environ.get("CANNON_SUNO_DURATION", "") == "1"
    cached = _cache_path(prompt, dur if send_duration else 0)
    if cached.is_file() and cached.stat().st_size > 10_000 and is_valid_media(cached):
        shutil.copy2(cached, dest)
        logger.info("music: cache hit for this prompt")
        return dest, 0.0
    payload: dict = {"prompt": prompt, "instrumental": True}
    if send_duration:
        payload.update({"custom_mode": True, "duration": dur})
    payload = client.filter_input(MUSIC_MODEL, payload)
    logger.info("music: {} {}s prompt={!r}", MUSIC_MODEL, dur, prompt[:80])
    res = client.run_task(MUSIC_MODEL, payload, watchdog=420)
    client.download(res.url, cached, min_bytes=10_000)
    shutil.copy2(cached, dest)
    return dest, res.cost
