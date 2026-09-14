"""Settings from environment (.env in repo root is loaded if present). No secrets in code."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORAGE = Path(os.environ.get("CANNON_STORAGE", ROOT / "storage")).resolve()
TASKS_DIR = STORAGE / "tasks"
CACHE_DIR = STORAGE / "cache"
REMOTION_DIR = ROOT / "remotion"
WEBUI_DIR = ROOT / "webui"


def _load_dotenv() -> None:
    for p in (ROOT / ".env", Path.cwd() / ".env"):
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:]
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


class Settings:
    """Read lazily so tests can monkeypatch os.environ."""

    @property
    def genosai_api_key(self) -> str:
        return os.environ.get("GENOSAI_API_KEY", "")

    @property
    def genosai_api_host(self) -> str:
        return os.environ.get("GENOSAI_API_HOST", "https://api.genosai.io").rstrip("/")

    @property
    def pexels_api_key(self) -> str:
        return os.environ.get("PEXELS_API_KEY", "")

    @property
    def pixabay_api_key(self) -> str:
        return os.environ.get("PIXABAY_API_KEY", "")

    @property
    def render_concurrency(self) -> int:
        return int(os.environ.get("CANNON_RENDER_CONCURRENCY", "0") or 0)  # 0 = Remotion default

    @property
    def max_parallel_tasks(self) -> int:
        return int(os.environ.get("CANNON_MAX_PARALLEL_TASKS", "2"))

    @property
    def host(self) -> str:
        return os.environ.get("CANNON_HOST", "127.0.0.1")

    @property
    def port(self) -> int:
        return int(os.environ.get("CANNON_PORT", "8787"))

    @property
    def log_level(self) -> str:
        return os.environ.get("CANNON_LOG_LEVEL", "INFO")

    def ensure_dirs(self) -> None:
        for d in (TASKS_DIR, CACHE_DIR / "search", CACHE_DIR / "videos", CACHE_DIR / "music"):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
