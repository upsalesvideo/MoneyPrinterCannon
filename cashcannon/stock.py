"""Free stock footage: Pexels / Pixabay. Search results are cached as JSON (sha256 key,
TTL 24 h, empty results never cached); files in storage/cache/videos/vid-<md5>.mp4."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

import requests
from loguru import logger

from .config import CACHE_DIR, settings
from .media import is_valid_media
from .schema import Scene

SEARCH_TTL = 24 * 3600
ORIENTATION = {"9:16": "portrait", "16:9": "landscape", "1:1": "square"}
UA = "CashCannon/0.1 (+https://github.com/cashcannon)"


class StockUnavailable(RuntimeError):
    """No provider key configured (or provider refused us)."""


@dataclass
class StockVideo:
    url: str
    width: int
    height: int
    duration: float
    provider: str
    credit: str
    page_url: str = ""
    id: str = ""


# ---------------------------------------------------------------------------
# http with retries
# ---------------------------------------------------------------------------
def _get(url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None, timeout: int = 30) -> requests.Response:
    hdrs = {"User-Agent": UA, "Connection": "close", **(headers or {})}
    last: Exception | None = None
    for i in range(1, 4):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            time.sleep(1.5 * i)
            continue
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After") or 5 * i)
            logger.info("stock 429, waiting {:.0f}s", wait)
            time.sleep(min(wait, 60))
            continue
        if r.status_code >= 500:
            last = RuntimeError(f"{r.status_code} {r.text[:100]}")
            time.sleep(1.5 * i)
            continue
        return r
    raise StockUnavailable(f"stock request failed: {last}")


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------
def _cache_key(provider: str, term: str, aspect: str, min_duration: float) -> Path:
    h = hashlib.sha256(f"{provider}|{term.strip().lower()}|{aspect}|{int(min_duration)}".encode()).hexdigest()
    d = CACHE_DIR / "search"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.json"


def _cache_get(p: Path) -> Optional[list[StockVideo]]:
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - float(d.get("ts", 0)) > SEARCH_TTL:
            return None
        items = [StockVideo(**x) for x in d.get("items", [])]
        return items or None
    except (ValueError, TypeError):
        return None


def _cache_put(p: Path, items: list[StockVideo]) -> None:
    if items:
        p.write_text(json.dumps({"ts": time.time(), "items": [asdict(i) for i in items]}, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------
def _orientation_ok(w: int, h: int, aspect: str) -> bool:
    if not w or not h:
        return False
    if aspect == "9:16":
        return h > w
    if aspect == "16:9":
        return w > h
    return True  # square: any, cover-crop handles it


def _pick_rendition(files: Iterable[dict], aspect: str) -> Optional[dict]:
    """Smallest rendition with min(w,h) >= 1080 and the right orientation; else the largest."""
    cands = [f for f in files if f.get("width") and f.get("height") and _orientation_ok(int(f["width"]), int(f["height"]), aspect)]
    if not cands:
        cands = [f for f in files if f.get("width") and f.get("height")]
    if not cands:
        return None
    good = [f for f in cands if min(int(f["width"]), int(f["height"])) >= 1080]
    if good:
        return min(good, key=lambda f: int(f["width"]) * int(f["height"]))
    return max(cands, key=lambda f: int(f["width"]) * int(f["height"]))


def _pexels(term: str, aspect: str, min_duration: float) -> list[StockVideo]:
    key = settings.pexels_api_key
    if not key:
        raise StockUnavailable("PEXELS_API_KEY not set")
    r = _get("https://api.pexels.com/videos/search",
             params={"query": term, "orientation": ORIENTATION[aspect], "per_page": 15, "size": "medium"},
             headers={"Authorization": key})
    if r.status_code in (401, 403):
        raise StockUnavailable(f"Pexels refused the key ({r.status_code})")
    r.raise_for_status()
    out: list[StockVideo] = []
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", []) if (f.get("file_type") or "video/mp4") == "video/mp4"]
        best = _pick_rendition(files, aspect)
        if not best:
            continue
        user = (v.get("user") or {}).get("name") or "Unknown"
        out.append(StockVideo(url=best["link"], width=int(best["width"]), height=int(best["height"]),
                              duration=float(v.get("duration") or 0), provider="pexels",
                              credit=f"Pexels / {user}", page_url=str(v.get("url") or ""), id=str(v.get("id") or "")))
    return [o for o in out if o.duration >= min_duration] or out


def _pixabay(term: str, aspect: str, min_duration: float) -> list[StockVideo]:
    key = settings.pixabay_api_key
    if not key:
        raise StockUnavailable("PIXABAY_API_KEY not set")
    params = {"key": key, "q": term, "per_page": 20, "video_type": "film", "safesearch": "true"}
    if aspect == "9:16":
        params["min_height"] = 1280
    elif aspect == "16:9":
        params["min_width"] = 1280
    else:
        params["min_width"] = 1080
    r = _get("https://pixabay.com/api/videos/", params=params)
    if r.status_code in (400, 401, 403) and "key" in r.text.lower():
        raise StockUnavailable(f"Pixabay refused the key ({r.status_code})")
    r.raise_for_status()
    out: list[StockVideo] = []
    for hit in r.json().get("hits", []):
        vids = hit.get("videos") or {}
        files = [{**vids[k], "quality": k} for k in ("large", "medium", "small") if vids.get(k) and vids[k].get("url")]
        best = _pick_rendition(files, aspect)
        if not best:
            continue
        out.append(StockVideo(url=best["url"], width=int(best["width"]), height=int(best["height"]),
                              duration=float(hit.get("duration") or 0), provider="pixabay",
                              credit=f"Pixabay / {hit.get('user') or 'Unknown'}", page_url=str(hit.get("pageURL") or ""), id=str(hit.get("id") or "")))
    return [o for o in out if o.duration >= min_duration] or out


def resolve_provider(provider: str = "auto") -> str:
    if provider == "auto":
        if settings.pexels_api_key:
            return "pexels"
        if settings.pixabay_api_key:
            return "pixabay"
        raise StockUnavailable("no stock keys configured (PEXELS_API_KEY / PIXABAY_API_KEY)")
    return provider


def stock_available(provider: str = "auto") -> bool:
    try:
        p = resolve_provider(provider)
    except StockUnavailable:
        return False
    return bool(settings.pexels_api_key if p == "pexels" else settings.pixabay_api_key)


def search_videos(term: str, aspect: str = "9:16", min_duration: float = 0, provider: str = "auto") -> list[StockVideo]:
    prov = resolve_provider(provider)
    ck = _cache_key(prov, term, aspect, min_duration)
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    items = _pexels(term, aspect, min_duration) if prov == "pexels" else _pixabay(term, aspect, min_duration)
    _cache_put(ck, items)
    logger.debug("stock {} {!r}: {} results", prov, term, len(items))
    return items


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------
def _strip_query(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def video_cache_path(url: str) -> Path:
    d = CACHE_DIR / "videos"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"vid-{hashlib.md5(_strip_query(url).encode()).hexdigest()}.mp4"


def download_video(url: str) -> Path:
    dest = video_cache_path(url)
    if dest.is_file() and dest.stat().st_size > 0 and is_valid_media(dest):
        return dest
    tmp = dest.with_suffix(".part")
    last: Exception | None = None
    for i in range(1, 4):
        try:
            with requests.get(url, stream=True, timeout=120, headers={"User-Agent": UA, "Connection": "close"}) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
            if tmp.stat().st_size < 10_000 or not is_valid_media(tmp):
                raise RuntimeError("downloaded file is not a valid video")
            tmp.replace(dest)
            return dest
        except (requests.RequestException, RuntimeError, OSError) as e:
            last = e
            tmp.unlink(missing_ok=True)
            time.sleep(2 * i)
    raise StockUnavailable(f"download failed: {last}")


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def pick_for_scene(scene: Scene, aspect: str, need_seconds: float, used_urls: set[str], provider: str = "auto") -> Optional[StockVideo]:
    """Try each search term in order; prefer clips long enough, skip URLs already used."""
    terms = [t for t in scene.search_terms if t.strip()] or [scene.narration[:40]]
    for term in terms:
        try:
            items = search_videos(term, aspect, min_duration=0, provider=provider)
        except StockUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — a bad term must not kill the scene
            logger.warning("stock search {!r} failed: {}", term, e)
            continue
        fresh = [i for i in items if _strip_query(i.url) not in used_urls]
        if not fresh:
            continue
        long_enough = [i for i in fresh if i.duration >= need_seconds]
        pool = long_enough or fresh
        # among long-enough clips prefer the shortest (less trimming); else the longest
        best = min(pool, key=lambda i: i.duration) if long_enough else max(pool, key=lambda i: i.duration)
        used_urls.add(_strip_query(best.url))
        return best
    return None


if __name__ == "__main__":
    import sys

    print(stock_available(), [asdict(v) for v in search_videos(" ".join(sys.argv[1:]) or "city night")][:2])
