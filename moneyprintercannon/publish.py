"""Publishing through Composio (free tier: managed OAuth for YouTube / Instagram / TikTok /
LinkedIn, 100k tool calls per month, unlimited connected accounts).

Why Composio and not our own OAuth apps: one API key, no app review per platform, tokens are
stored and refreshed by Composio, local files are uploaded by the SDK (file-uploadable params).

Env: COMPOSIO_API_KEY (required), COMPOSIO_USER_ID (default "default" — one set of connected
accounts per user id; use different ids for different clients / channels).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from loguru import logger

from .schema import Script

PLATFORMS = ("youtube", "instagram", "tiktok", "linkedin")
TOOLKIT: dict[str, str] = {p: p for p in PLATFORMS}  # composio toolkit slug == our platform name
YOUTUBE_CATEGORY_DEFAULT = "22"  # People & Blogs; 27 = Education, 24 = Entertainment, 28 = Science & Tech
YOUTUBE_TITLE_MAX = 100
INSTAGRAM_CAPTION_MAX = 2200
TIKTOK_CAPTION_MAX = 2200


class PublishError(RuntimeError):
    pass


class PublishNotConfigured(PublishError):
    """No COMPOSIO_API_KEY."""


class PlatformNotConnected(PublishError):
    """User has not completed the OAuth link for this toolkit yet."""

    def __init__(self, platform: str, redirect_url: str = ""):
        super().__init__(f"{platform} is not connected" + (f" — open {redirect_url}" if redirect_url else ""))
        self.platform = platform
        self.redirect_url = redirect_url


@dataclass
class PublishResult:
    platform: str
    ok: bool
    url: str = ""
    id: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)
    at: float = field(default_factory=time.time)


@dataclass
class PostCopy:
    title: str
    caption: str
    hashtags: list[str]

    @property
    def hashtag_line(self) -> str:
        return " ".join("#" + h.lstrip("#").replace(" ", "") for h in self.hashtags if h.strip())

    def caption_with_tags(self, limit: int) -> str:
        body = self.caption.strip()
        tags = self.hashtag_line
        text = (body + ("\n\n" + tags if tags else "")).strip()
        return text[:limit].rstrip()


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------
def is_configured() -> bool:
    return bool(os.environ.get("COMPOSIO_API_KEY"))


def user_id() -> str:
    return os.environ.get("COMPOSIO_USER_ID", "default")


_client: Any = None


def client() -> Any:
    """Lazy Composio client; import inside so the package works without the SDK installed."""
    global _client
    if _client is not None:
        return _client
    key = os.environ.get("COMPOSIO_API_KEY", "")
    if not key:
        raise PublishNotConfigured("COMPOSIO_API_KEY is not set — get a free key at https://platform.composio.dev and put it in .env")
    try:
        from composio import Composio  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise PublishNotConfigured("composio SDK is not installed: pip install composio") from e
    _client = Composio(api_key=key)
    return _client


def set_client(c: Any) -> None:
    """Testing hook."""
    global _client
    _client = c


def _unwrap(resp: Any) -> dict:
    """ToolExecutionResponse → plain dict {successful, data, error}."""
    if isinstance(resp, dict):
        return resp
    d: dict = {}
    for k in ("successful", "data", "error"):
        d[k] = getattr(resp, k, None)
    if hasattr(resp, "model_dump"):
        try:
            d.update({k: v for k, v in resp.model_dump().items() if k in ("successful", "data", "error")})
        except Exception:  # noqa: BLE001
            pass
    return d


def execute(slug: str, arguments: dict, uid: Optional[str] = None) -> dict:
    """Run one Composio tool for the user; raise PublishError with the tool's error text on failure."""
    uid = uid or user_id()
    logger.info("composio {} ({})", slug, ", ".join(k for k in arguments if k not in ("videoFilePath", "video_file", "file_to_upload", "file")))
    resp = _unwrap(client().tools.execute(slug, arguments=arguments, user_id=uid))
    if not resp.get("successful"):
        err = resp.get("error") or resp.get("data") or "unknown error"
        raise PublishError(f"{slug}: {str(err)[:600]}")
    data = resp.get("data")
    return data if isinstance(data, dict) else {"data": data}


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------
def connected_platforms(uid: Optional[str] = None) -> dict[str, bool]:
    """{platform: is_active} for the user's Composio connected accounts."""
    uid = uid or user_id()
    out = {p: False for p in PLATFORMS}
    if not is_configured():
        return out
    try:
        resp = client().connected_accounts.list(user_ids=[uid], toolkit_slugs=list(TOOLKIT.values()), statuses=["ACTIVE"])
        items = getattr(resp, "items", None) or (resp.get("items") if isinstance(resp, dict) else []) or []
        for it in items:
            tk = getattr(it, "toolkit", None)
            slug = (getattr(tk, "slug", None) or (tk.get("slug") if isinstance(tk, dict) else None) or "").lower()
            for p, s in TOOLKIT.items():
                if slug == s:
                    out[p] = True
    except Exception as e:  # noqa: BLE001
        logger.warning("composio connected_accounts.list failed: {}", e)
    return out


def connect_url(platform: str, uid: Optional[str] = None) -> tuple[str, Any]:
    """Start the OAuth link for a platform. Returns (redirect_url, connection_request).
    The user opens the URL in a browser; call wait_for(connection_request) to block until done."""
    if platform not in PLATFORMS:
        raise PublishError(f"unknown platform {platform!r}; choose from {', '.join(PLATFORMS)}")
    uid = uid or user_id()
    req = client().toolkits.authorize(user_id=uid, toolkit=TOOLKIT[platform])
    url = getattr(req, "redirect_url", None) or (req.get("redirect_url") if isinstance(req, dict) else "") or ""
    logger.info("composio connect {} for user {}: {}", platform, uid, url)
    return url, req


def wait_for(connection_request: Any, timeout_sec: int = 300) -> bool:
    try:
        connection_request.wait_for_connection(timeout=timeout_sec)
        return True
    except TypeError:
        try:
            connection_request.wait_for_connection(timeout_sec * 1000)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("wait_for_connection failed: {}", e)
            return False
    except Exception as e:  # noqa: BLE001
        logger.warning("wait_for_connection failed: {}", e)
        return False


def require_connected(platform: str, uid: Optional[str] = None) -> None:
    if not connected_platforms(uid).get(platform):
        url = ""
        try:
            url, _ = connect_url(platform, uid)
        except Exception as e:  # noqa: BLE001
            logger.warning("could not create connect link for {}: {}", platform, e)
        raise PlatformNotConnected(platform, url)


# ---------------------------------------------------------------------------
# per-platform publishers
# ---------------------------------------------------------------------------
def _yt_privacy(privacy: str) -> str:
    return privacy if privacy in ("public", "private", "unlisted") else "private"


def publish_youtube(video: Path, copy: PostCopy, privacy: str = "private", category_id: str = YOUTUBE_CATEGORY_DEFAULT,
                    uid: Optional[str] = None) -> PublishResult:
    args = {
        "videoFilePath": str(video),
        "title": (copy.title or video.stem)[:YOUTUBE_TITLE_MAX],
        "description": copy.caption_with_tags(5000),
        "tags": [h.lstrip("#") for h in copy.hashtags][:30] or ["shorts"],
        "categoryId": category_id,
        "privacyStatus": _yt_privacy(privacy),
    }
    data = execute("YOUTUBE_UPLOAD_VIDEO", args, uid)
    vid = str(data.get("id") or data.get("videoId") or (data.get("response_data") or {}).get("id") or "")
    url = f"https://youtu.be/{vid}" if vid else ""
    return PublishResult("youtube", True, url=url, id=vid, raw=_small(data))


def publish_instagram(video: Path, copy: PostCopy, cover: Optional[Path] = None, share_to_feed: bool = True,
                      uid: Optional[str] = None) -> PublishResult:
    ig_user = "me"
    try:
        info = execute("INSTAGRAM_GET_USER_INFO", {}, uid)
        ig_user = str(info.get("id") or info.get("user_id") or (info.get("response_data") or {}).get("id") or "me")
    except PublishError as e:
        logger.warning("INSTAGRAM_GET_USER_INFO failed ({}), using 'me'", e)
    args: dict[str, Any] = {
        "ig_user_id": ig_user,
        "video_file": str(video),
        "media_type": "REELS",
        "caption": copy.caption_with_tags(INSTAGRAM_CAPTION_MAX),
        "share_to_feed": share_to_feed,
    }
    container = execute("INSTAGRAM_POST_IG_USER_MEDIA", args, uid)
    creation_id = str(container.get("id") or container.get("creation_id") or (container.get("response_data") or {}).get("id") or "")
    if not creation_id:
        raise PublishError(f"INSTAGRAM_POST_IG_USER_MEDIA returned no container id: {json.dumps(container)[:300]}")
    pub = execute("INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH", {"ig_user_id": ig_user, "creation_id": creation_id, "max_wait_seconds": 240}, uid)
    media_id = str(pub.get("id") or (pub.get("response_data") or {}).get("id") or "")
    permalink = str(pub.get("permalink") or "")
    return PublishResult("instagram", True, url=permalink, id=media_id, raw=_small(pub))


def publish_tiktok(video: Path, copy: PostCopy, privacy: str = "SELF_ONLY", uid: Optional[str] = None) -> PublishResult:
    # Unaudited TikTok apps may only post SELF_ONLY (private) — the user flips it to public in the TikTok app.
    args = {
        "file_to_upload": str(video),
        "caption": copy.caption_with_tags(TIKTOK_CAPTION_MAX) or copy.title,
        "publish": True,
        "privacy_level": privacy,
        "is_aigc": True,
    }
    data = execute("TIKTOK_UPLOAD_VIDEO", args, uid)
    pid = str(data.get("publish_id") or (data.get("data") or {}).get("publish_id") if isinstance(data.get("data"), dict) else data.get("publish_id") or "")
    return PublishResult("tiktok", True, id=pid, raw=_small(data))


def publish_linkedin(video: Path, copy: PostCopy, visibility: str = "PUBLIC", uid: Optional[str] = None) -> PublishResult:
    up = execute("LINKEDIN_UPLOAD_VIDEO", {"file": str(video)}, uid)
    urn = str(up.get("video_urn") or up.get("urn") or up.get("id") or (up.get("response_data") or {}).get("video_urn") or "")
    if not urn:
        raise PublishError(f"LINKEDIN_UPLOAD_VIDEO returned no video_urn: {json.dumps(up)[:300]}")
    post = execute("LINKEDIN_CREATE_VIDEO_POST", {"video_urn": urn, "commentary": copy.caption_with_tags(3000) or copy.title,
                                                  "title": copy.title[:200], "visibility": visibility}, uid)
    post_id = str(post.get("id") or post.get("post_urn") or "")
    url = f"https://www.linkedin.com/feed/update/{post_id}" if post_id.startswith("urn:") else ""
    return PublishResult("linkedin", True, url=url, id=post_id, raw=_small(post))


PUBLISHERS: dict[str, Callable[..., PublishResult]] = {
    "youtube": publish_youtube,
    "instagram": publish_instagram,
    "tiktok": publish_tiktok,
    "linkedin": publish_linkedin,
}


def _small(d: dict, limit: int = 2000) -> dict:
    try:
        s = json.dumps(d, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return {"repr": str(d)[:limit]}
    return d if len(s) <= limit else {"truncated": s[:limit]}


# ---------------------------------------------------------------------------
# task-level entry point
# ---------------------------------------------------------------------------
def copy_from_task(task_dir: Path, title: str = "", caption: str = "", hashtags: Optional[list[str]] = None) -> PostCopy:
    script = Script.model_validate_json((task_dir / "script.json").read_text(encoding="utf-8"))
    return PostCopy(
        title=(title or script.social.title or script.title or script.topic)[:200],
        caption=caption or script.social.caption or script.title or script.topic,
        hashtags=hashtags if hashtags is not None else list(script.social.hashtags),
    )


def publish_task(task_dir: Path, platforms: Iterable[str], privacy: str = "private", title: str = "", caption: str = "",
                 hashtags: Optional[list[str]] = None, uid: Optional[str] = None, variant: int = 1) -> list[PublishResult]:
    """Publish a finished task's video to the given platforms. Results are appended to publish.json."""
    task_dir = Path(task_dir)
    video = task_dir / ("final.mp4" if variant <= 1 else f"final-{variant}.mp4")
    if not video.is_file():
        raise PublishError(f"no video to publish: {video}")
    cover = task_dir / "cover.jpg"
    copy = copy_from_task(task_dir, title, caption, hashtags)
    results: list[PublishResult] = []
    if not is_configured():
        msg = "COMPOSIO_API_KEY is not set — get a free key at https://platform.composio.dev and put it in .env"
        results = [PublishResult(p.strip().lower(), False, error=msg) for p in platforms]
        _append_results(task_dir, results)
        return results
    for p in platforms:
        p = p.strip().lower()
        if p not in PUBLISHERS:
            results.append(PublishResult(p, False, error=f"unknown platform; choose from {', '.join(PLATFORMS)}"))
            continue
        try:
            require_connected(p, uid)
            if p == "youtube":
                r = publish_youtube(video, copy, privacy=privacy, uid=uid)
            elif p == "instagram":
                r = publish_instagram(video, copy, cover=cover if cover.is_file() else None, uid=uid)
            elif p == "tiktok":
                r = publish_tiktok(video, copy, privacy="PUBLIC_TO_EVERYONE" if privacy == "public" else "SELF_ONLY", uid=uid)
            else:
                r = publish_linkedin(video, copy, visibility="PUBLIC" if privacy == "public" else "CONNECTIONS", uid=uid)
            logger.info("published to {}: {}", p, r.url or r.id or "ok")
        except PlatformNotConnected as e:
            r = PublishResult(p, False, error=str(e), url=e.redirect_url)
            logger.warning("{}", e)
        except Exception as e:  # noqa: BLE001
            r = PublishResult(p, False, error=str(e)[:800])
            logger.error("publish to {} failed: {}", p, e)
        results.append(r)
    _append_results(task_dir, results)
    return results


def _append_results(task_dir: Path, results: list[PublishResult]) -> None:
    p = task_dir / "publish.json"
    prev: list = []
    if p.is_file():
        try:
            prev = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    prev.extend(asdict(r) for r in results)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def load_results(task_dir: Path) -> list[dict]:
    p = Path(task_dir) / "publish.json"
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
