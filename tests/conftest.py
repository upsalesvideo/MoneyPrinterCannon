"""Shared fixtures: isolated storage + tiny synthetic media (no network, no Genosai)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Point config paths at a temp dir so tests never touch the real storage/."""
    from moneyprintercannon import config

    storage = tmp_path / "storage"
    monkeypatch.setattr(config, "STORAGE", storage)
    monkeypatch.setattr(config, "TASKS_DIR", storage / "tasks")
    monkeypatch.setattr(config, "CACHE_DIR", storage / "cache")
    for mod in ("moneyprintercannon.stock", "moneyprintercannon.music", "moneyprintercannon.pipeline", "moneyprintercannon.render", "moneyprintercannon.cli"):
        try:
            m = __import__(mod, fromlist=["x"])
        except Exception:
            continue
        for name in ("STORAGE", "TASKS_DIR", "CACHE_DIR"):
            if hasattr(m, name):
                monkeypatch.setattr(m, name, getattr(config, name))
    config.settings.ensure_dirs()
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    yield storage


def ffmpeg_available() -> bool:
    from shutil import which

    return which("ffmpeg") is not None and which("ffprobe") is not None


@pytest.fixture
def tone_wav(tmp_path) -> Path:
    """3.0 s sine 'voice' (48k stereo)."""
    if not ffmpeg_available():
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-ar", "48000", "-ac", "2", str(out)], check=True)
    return out
