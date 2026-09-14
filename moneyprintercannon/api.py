"""FastAPI app: REST API + serves webui/ at /.

Pipeline / estimate / genosai modules are imported lazily inside request handlers so
this module imports (and the UI loads) even while those modules are still missing —
in that case the affected endpoints answer 503 with a readable message.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from moneyprintercannon import __version__
from moneyprintercannon.config import TASKS_DIR, WEBUI_DIR, settings
from moneyprintercannon.schema import (
    STAGES,
    Estimate,
    TaskState,
    VideoParams,
)

TASK_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
ALLOWED_FILE_EXT = {".mp4", ".jpg", ".png", ".json", ".txt", ".wav", ".mp3"}

VOICES = [
    "Achernar", "Achird", "Algenib", "Algieba", "Alnilam", "Aoede", "Autonoe", "Callirrhoe",
    "Charon", "Despina", "Enceladus", "Erinome", "Fenrir", "Gacrux", "Iapetus", "Kore",
    "Laomedeia", "Leda", "Orus", "Puck", "Pulcherrima", "Rasalgethi", "Sadachbia", "Sadaltager",
    "Schedar", "Sulafat", "Umbriel", "Vindemiatrix", "Zephyr", "Zubenelgenubi",
]
VOICE_STYLES = ["Vocal Smile", "Newscaster", "Whisper", "Empathetic", "Promo/Hype", "Deadpan"]
VOICE_PACES = ["Natural", "Rapid Fire", "The Drift", "Staccato"]
ASPECTS = ["9:16", "16:9", "1:1"]
VISUAL_SOURCES = ["ai_image", "ai_video", "stock", "mixed", "local"]
CAPTION_PRESETS = ["karaoke", "bold", "clean", "minimal"]
POSITIONS = ["bottom", "center", "top"]
TRANSITIONS = ["cut", "fade", "slide", "zoom"]
LANGUAGES = ["auto", "ru", "en", "es", "de", "fr", "pt", "it", "tr", "zh"]

app = FastAPI(
    title="MoneyPrinterCannon API",
    version=__version__,
    description="Topic in. Money-making video out. REST API for the MoneyPrinterCannon short-video generator.",
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Errors → {"error": CODE, "message": "..."}
# ---------------------------------------------------------------------------
class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, **extra: Any):
        super().__init__(status_code=status_code, detail={"error": code, "message": message, **extra})


@app.exception_handler(HTTPException)
async def _http_exc(_: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        body = detail
    else:
        body = {"error": "HTTP_%d" % exc.status_code, "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def _validation_exc(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("body",))
    msg = first.get("msg", "Invalid request")
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "message": f"{loc}: {msg}" if loc else msg,
            "details": json.loads(json.dumps(errors, default=str)),
        },
    )


@app.exception_handler(Exception)
async def _any_exc(_: Request, exc: Exception):
    logger.exception("Unhandled error in API")
    return JSONResponse(status_code=500, content={"error": "INTERNAL_ERROR", "message": str(exc)})


# ---------------------------------------------------------------------------
# Lazy module access
# ---------------------------------------------------------------------------
def _pipeline():
    try:
        from moneyprintercannon import pipeline  # noqa: WPS433
    except Exception as e:  # ImportError or a broken module
        raise ApiError(503, "PIPELINE_UNAVAILABLE",
                       f"moneyprintercannon.pipeline is not available yet ({e.__class__.__name__}: {e}). "
                       "The generator backend is still being built — read-only endpoints keep working.")
    return pipeline


def _estimator():
    try:
        from moneyprintercannon import estimate as est  # noqa: WPS433
    except Exception as e:
        raise ApiError(503, "ESTIMATE_UNAVAILABLE",
                       f"moneyprintercannon.estimate is not available yet ({e.__class__.__name__}: {e}).")
    return est


def _genosai_client():
    """Returns a GenosaiClient or None when the module is missing."""
    try:
        from moneyprintercannon.genosai import GenosaiClient  # noqa: WPS433
    except Exception:
        return None
    try:
        return GenosaiClient()
    except Exception as e:
        logger.warning("GenosaiClient() failed: {}", e)
        return None


def _genosai_get(path: str) -> dict:
    """Minimal direct call used only as a fallback while genosai.py is not there."""
    import requests  # local import: keep api importable without network deps at module level

    if not settings.genosai_api_key:
        raise RuntimeError("GENOSAI_API_KEY is not configured")
    with requests.Session() as s:
        r = s.get(
            settings.genosai_api_host + path,
            headers={"Authorization": f"Bearer {settings.genosai_api_key}", "Connection": "close"},
            timeout=20,
        )
    if r.status_code >= 400:
        try:
            j = r.json()
            raise RuntimeError(f"{j.get('error', r.status_code)}: {j.get('message', r.text[:200])}")
        except ValueError:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    return j.get("data", j) if isinstance(j, dict) else j


def _fetch_balance() -> dict:
    client = _genosai_client()
    if client is not None and hasattr(client, "balance"):
        data = client.balance()
    else:
        data = _genosai_get("/v1/balance")
    if not isinstance(data, dict):
        raise RuntimeError("unexpected balance payload")
    out = {k: data.get(k) for k in ("main", "bonus", "total")}
    if out["total"] is None and out["main"] is not None:
        out["total"] = (out["main"] or 0) + (out["bonus"] or 0)
    return out


def _fetch_models() -> dict[str, list[dict]]:
    client = _genosai_client()
    if client is not None and hasattr(client, "models"):
        raw = client.models()
    else:
        raw = _genosai_get("/v1/models")
    if not isinstance(raw, dict):
        raise RuntimeError("unexpected models payload")
    out: dict[str, list[dict]] = {}
    for cat in ("photo", "video", "text", "tts", "music"):
        items = raw.get(cat) or []
        lst = []
        for m in items:
            if isinstance(m, str):
                lst.append({"id": m, "name": m})
                continue
            if not isinstance(m, dict) or not m.get("id"):
                continue
            entry = {"id": m["id"], "name": m.get("name") or m.get("title") or m["id"]}
            cost = m.get("cost_credits_default")
            if isinstance(cost, (int, float)):
                entry["cost"] = cost
            lst.append(entry)
        out[cat] = lst
    return out


_meta_cache: dict[str, Any] = {"models": None, "models_at": 0.0, "models_error": None}
_META_TTL = 300.0


def _models_cached() -> tuple[dict[str, list[dict]], Optional[str]]:
    now = time.time()
    if _meta_cache["models"] is not None and now - _meta_cache["models_at"] < _META_TTL:
        return _meta_cache["models"], _meta_cache["models_error"]
    try:
        models = _fetch_models()
        _meta_cache.update(models=models, models_at=now, models_error=None)
        return models, None
    except Exception as e:
        err = str(e)
        empty = {c: [] for c in ("photo", "video", "text", "tts", "music")}
        # keep a short negative cache so a dead network does not stall every UI load
        _meta_cache.update(models=empty, models_at=now - _META_TTL + 30, models_error=err)
        return empty, err


# ---------------------------------------------------------------------------
# Task files / state helpers (disk fallbacks so read endpoints work without pipeline.py)
# ---------------------------------------------------------------------------
def _task_dir(task_id: str, must_exist: bool = True) -> Path:
    if not TASK_ID_RE.match(task_id):
        raise ApiError(400, "BAD_TASK_ID", "task_id must look like YYYYMMDD-HHMMSS-xxxxxx")
    d = TASKS_DIR / task_id
    if must_exist and not d.is_dir():
        raise ApiError(404, "TASK_NOT_FOUND", f"Task {task_id} does not exist")
    return d


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_state(task_id: str) -> TaskState:
    d = _task_dir(task_id)
    try:
        from moneyprintercannon import pipeline  # noqa: WPS433

        if hasattr(pipeline, "load_state"):
            return pipeline.load_state(task_id)
    except ApiError:
        raise
    except Exception as e:
        logger.debug("pipeline.load_state unavailable ({}), reading state.json", e)
    p = d / "state.json"
    if not p.is_file():
        raise ApiError(404, "STATE_NOT_FOUND", f"Task {task_id} has no state.json yet")
    return TaskState.model_validate(_read_json(p))


def _load_params(task_id: str) -> VideoParams:
    d = _task_dir(task_id)
    try:
        from moneyprintercannon import pipeline  # noqa: WPS433

        if hasattr(pipeline, "load_params"):
            return pipeline.load_params(task_id)
    except ApiError:
        raise
    except Exception:
        pass
    p = d / "params.json"
    if not p.is_file():
        raise ApiError(404, "PARAMS_NOT_FOUND", f"Task {task_id} has no params.json")
    return VideoParams.model_validate(_read_json(p))


def _list_states(limit: int) -> list[TaskState]:
    try:
        from moneyprintercannon import pipeline  # noqa: WPS433

        if hasattr(pipeline, "list_tasks"):
            return list(pipeline.list_tasks(limit=limit))
    except Exception as e:
        logger.debug("pipeline.list_tasks unavailable ({}), scanning {}", e, TASKS_DIR)
    if not TASKS_DIR.is_dir():
        return []
    dirs = sorted((p for p in TASKS_DIR.iterdir() if p.is_dir() and TASK_ID_RE.match(p.name)),
                  key=lambda p: p.name, reverse=True)
    out: list[TaskState] = []
    for d in dirs:
        sp = d / "state.json"
        if not sp.is_file():
            continue
        try:
            out.append(TaskState.model_validate(_read_json(sp)))
        except Exception as e:
            logger.warning("bad state.json in {}: {}", d, e)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Background runner (module singleton)
# ---------------------------------------------------------------------------
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_running: dict[str, Future] = {}


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=max(1, settings.max_parallel_tasks), thread_name_prefix="cannon-task"
            )
        return _executor


def _is_running_here(task_id: str) -> bool:
    fut = _running.get(task_id)
    return fut is not None and not fut.done()


def _submit_run(task_id: str, stop_at: Optional[str], force: bool) -> None:
    pipeline = _pipeline()
    if _is_running_here(task_id):
        raise ApiError(409, "ALREADY_RUNNING", f"Task {task_id} is already running in this process")

    def _job() -> None:
        try:
            logger.info("task {} started (stop_at={}, force={})", task_id, stop_at, force)
            pipeline.run(task_id, stop_at=stop_at, force=force)
            logger.info("task {} finished", task_id)
        except Exception as e:  # pipeline already persisted status=failed
            logger.error("task {} failed: {}", task_id, e)

    _running[task_id] = _get_executor().submit(_job)


def _check_stop_at(stop_at: Optional[str]) -> Optional[str]:
    if stop_at in (None, ""):
        return None
    if stop_at not in STAGES:
        raise ApiError(400, "BAD_STOP_AT", f"stop_at must be one of {', '.join(STAGES)}")
    return stop_at


# ---------------------------------------------------------------------------
# Routes: meta
# ---------------------------------------------------------------------------
@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "genosai_key_configured": bool(settings.genosai_api_key),
        "stock_configured": {
            "pexels": bool(settings.pexels_api_key),
            "pixabay": bool(settings.pixabay_api_key),
        },
        "tasks_dir": str(TASKS_DIR),
        "max_parallel_tasks": settings.max_parallel_tasks,
    }


@app.get("/api/meta", tags=["meta"])
def meta() -> dict:
    models, models_error = _models_cached()
    balance = None
    balance_error = None
    if settings.genosai_api_key:
        try:
            balance = _fetch_balance()
        except Exception as e:
            balance_error = str(e)
    else:
        balance_error = "GENOSAI_API_KEY is not configured"
    return {
        "version": __version__,
        "aspects": ASPECTS,
        "visual_sources": VISUAL_SOURCES,
        "caption_presets": CAPTION_PRESETS,
        "positions": POSITIONS,
        "transitions": TRANSITIONS,
        "languages": LANGUAGES,
        "voices": VOICES,
        "voice_styles": VOICE_STYLES,
        "voice_paces": VOICE_PACES,
        "stages": list(STAGES),
        "defaults": VideoParams().model_dump(),
        "models": {k: models.get(k, []) for k in ("photo", "video", "text")},
        "models_error": models_error,
        "balance": balance,
        "balance_error": balance_error,
        "genosai_key_configured": bool(settings.genosai_api_key),
        "stock_configured": {
            "pexels": bool(settings.pexels_api_key),
            "pixabay": bool(settings.pixabay_api_key),
        },
    }


@app.get("/api/balance", tags=["meta"])
def balance() -> dict:
    if not settings.genosai_api_key:
        raise ApiError(503, "NO_API_KEY", "GENOSAI_API_KEY is not configured. Run: export GENOSAI_API_KEY=sdk_...")
    try:
        return _fetch_balance()
    except Exception as e:
        raise ApiError(502, "GENOSAI_ERROR", f"Could not fetch balance from Genosai: {e}")


@app.post("/api/estimate", tags=["tasks"], response_model=Estimate)
def estimate(params: VideoParams) -> Estimate:
    est = _estimator()
    try:
        return est.estimate(params)
    except ApiError:
        raise
    except ValueError as e:
        raise ApiError(400, "BAD_PARAMS", str(e))
    except Exception as e:
        logger.exception("estimate failed")
        raise ApiError(500, "ESTIMATE_FAILED", f"Estimate failed: {e}")


# ---------------------------------------------------------------------------
# Routes: tasks
# ---------------------------------------------------------------------------
@app.post("/api/tasks", tags=["tasks"], status_code=201)
def create_task(
    params: VideoParams,
    stop_at: Optional[str] = Query(None, description="Stop after this stage (script|voice|timing|visuals|music|render)"),
) -> dict:
    try:
        params.require_content()
    except ValueError as e:
        raise ApiError(400, "BAD_PARAMS", str(e))
    stop = _check_stop_at(stop_at)
    pipeline = _pipeline()

    estimate_obj: Optional[Estimate] = None
    estimate_error: Optional[str] = None
    try:
        estimate_obj = _estimator().estimate(params)
    except ApiError as e:
        estimate_error = e.detail["message"] if isinstance(e.detail, dict) else str(e.detail)
    except Exception as e:
        estimate_error = str(e)

    try:
        task_id = pipeline.create_task(params)
    except ValueError as e:
        raise ApiError(400, "BAD_PARAMS", str(e))
    _submit_run(task_id, stop, force=False)
    return {
        "task_id": task_id,
        "estimate": estimate_obj.model_dump() if estimate_obj else None,
        "estimate_error": estimate_error,
        "stop_at": stop,
    }


@app.get("/api/tasks", tags=["tasks"], response_model=list[TaskState])
def list_tasks(limit: int = Query(50, ge=1, le=500)) -> list[TaskState]:
    return _list_states(limit)


@app.get("/api/tasks/{task_id}", tags=["tasks"], response_model=TaskState)
def get_task(task_id: str) -> TaskState:
    return _load_state(task_id)


@app.get("/api/tasks/{task_id}/params", tags=["tasks"], response_model=VideoParams)
def get_task_params(task_id: str) -> VideoParams:
    return _load_params(task_id)


@app.get("/api/tasks/{task_id}/script", tags=["tasks"])
def get_task_script(task_id: str) -> Any:
    p = _task_dir(task_id) / "script.json"
    if not p.is_file():
        raise ApiError(404, "SCRIPT_NOT_READY", "script.json is not written yet (script stage has not finished)")
    return _read_json(p)


@app.get("/api/tasks/{task_id}/log", tags=["tasks"], response_class=PlainTextResponse)
def get_task_log(task_id: str, tail: int = Query(200, ge=1, le=5000)) -> str:
    p = _task_dir(task_id) / "log.txt"
    if not p.is_file():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-tail:]) + ("\n" if lines else "")


def _file_response(path: Path, media_type: Optional[str] = None, download_name: Optional[str] = None) -> FileResponse:
    # Starlette's FileResponse honours Range requests (needed by <video>) and sets ETag/Last-Modified.
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    return FileResponse(str(path), media_type=media_type, headers=headers, filename=download_name)


def _variant_name(n: int) -> str:
    return "final.mp4" if n <= 1 else f"final-{n}.mp4"


@app.get("/api/tasks/{task_id}/video", tags=["files"])
def get_task_video(task_id: str, download: bool = False):
    return get_task_video_variant(task_id, 1, download)


@app.get("/api/tasks/{task_id}/video/{n}", tags=["files"])
def get_task_video_variant(task_id: str, n: int, download: bool = False):
    if n < 1 or n > 5:
        raise ApiError(400, "BAD_VARIANT", "variant must be 1..5")
    d = _task_dir(task_id)
    p = d / _variant_name(n)
    if not p.is_file():
        raise ApiError(404, "VIDEO_NOT_READY", f"{p.name} is not rendered yet")
    name = f"moneyprintercannon-{task_id}" + ("" if n <= 1 else f"-v{n}") + ".mp4"
    return _file_response(p, "video/mp4", name if download else None)


@app.get("/api/tasks/{task_id}/cover", tags=["files"])
def get_task_cover(task_id: str):
    d = _task_dir(task_id)
    for name, mt in (("cover.jpg", "image/jpeg"), ("cover.png", "image/png")):
        p = d / name
        if p.is_file():
            return _file_response(p, mt)
    raise ApiError(404, "COVER_NOT_READY", "cover is not produced yet")


@app.get("/api/tasks/{task_id}/files/{name}", tags=["files"])
def get_task_file(task_id: str, name: str):
    d = _task_dir(task_id)
    if not FILE_NAME_RE.match(name) or ".." in name or "/" in name or "\\" in name:
        raise ApiError(400, "BAD_FILE_NAME", "file name may contain only letters, digits, dot, dash, underscore")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_FILE_EXT:
        raise ApiError(400, "FILE_TYPE_NOT_ALLOWED", f"allowed: {', '.join(sorted(ALLOWED_FILE_EXT))}")
    p = (d / name).resolve()
    if d.resolve() not in p.parents:
        raise ApiError(400, "BAD_PATH", "path escapes the task directory")
    if not p.is_file():
        raise ApiError(404, "FILE_NOT_FOUND", f"{name} not found in task {task_id}")
    media = {
        ".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png", ".json": "application/json",
        ".txt": "text/plain; charset=utf-8", ".wav": "audio/wav", ".mp3": "audio/mpeg",
    }[ext]
    return _file_response(p, media)


@app.post("/api/tasks/{task_id}/resume", tags=["tasks"])
def resume_task(
    task_id: str,
    stop_at: Optional[str] = Query(None),
    force: bool = Query(False, description="Re-run stages even if they succeeded"),
) -> dict:
    state = _load_state(task_id)
    if state.status == "running" and _is_running_here(task_id):
        raise ApiError(409, "ALREADY_RUNNING", "Task is already running")
    _submit_run(task_id, _check_stop_at(stop_at), force=force)
    return {"task_id": task_id, "resumed": True, "from_stage": state.failed_stage or state.stage}


@app.post("/api/tasks/{task_id}/cancel", tags=["tasks"])
def cancel_task(task_id: str) -> dict:
    _task_dir(task_id)
    pipeline = _pipeline()
    try:
        pipeline.request_cancel(task_id)
    except FileNotFoundError:
        raise ApiError(404, "TASK_NOT_FOUND", f"Task {task_id} does not exist")
    return {"task_id": task_id, "cancel_requested": True}


@app.delete("/api/tasks/{task_id}", tags=["tasks"])
def delete_task(task_id: str, force: bool = Query(False, description="Cancel first if still running")) -> dict:
    _task_dir(task_id)
    pipeline = _pipeline()
    if _is_running_here(task_id):
        if not force:
            raise ApiError(409, "TASK_RUNNING", "Task is running. Cancel it first or pass ?force=true")
        try:
            pipeline.request_cancel(task_id)
        except Exception as e:
            logger.warning("request_cancel before delete failed: {}", e)
        fut = _running.get(task_id)
        if fut is not None:
            try:
                fut.result(timeout=30)
            except Exception:
                pass
    try:
        pipeline.delete_task(task_id)
    except FileNotFoundError:
        raise ApiError(404, "TASK_NOT_FOUND", f"Task {task_id} does not exist")
    _running.pop(task_id, None)
    return {"task_id": task_id, "deleted": True}


# ---------------------------------------------------------------------------
# Publishing (Composio, free tier)
# ---------------------------------------------------------------------------
def _publisher():
    try:
        from moneyprintercannon import publish as pub
    except Exception as e:  # noqa: BLE001
        raise ApiError(503, "PUBLISH_UNAVAILABLE", f"publish module failed to import: {e}")
    return pub


@app.get("/api/publish/status", tags=["publish"])
def publish_status() -> dict:
    pub = _publisher()
    return {"configured": pub.is_configured(), "user_id": pub.user_id(), "platforms": list(pub.PLATFORMS),
            "connected": pub.connected_platforms() if pub.is_configured() else {p: False for p in pub.PLATFORMS},
            "hint": None if pub.is_configured() else "Set COMPOSIO_API_KEY in .env (free key at https://platform.composio.dev)"}


@app.post("/api/publish/connect", tags=["publish"])
def publish_connect(body: dict) -> dict:
    pub = _publisher()
    platform = str((body or {}).get("platform", "")).lower()
    if platform not in pub.PLATFORMS:
        raise ApiError(400, "BAD_PLATFORM", f"platform must be one of {', '.join(pub.PLATFORMS)}")
    if not pub.is_configured():
        raise ApiError(503, "PUBLISH_NOT_CONFIGURED", "COMPOSIO_API_KEY is not set")
    try:
        url, _ = pub.connect_url(platform)
    except Exception as e:  # noqa: BLE001
        raise ApiError(502, "COMPOSIO_ERROR", str(e)[:500])
    return {"platform": platform, "redirect_url": url}


@app.post("/api/tasks/{task_id}/publish", tags=["publish"])
def publish_task_route(task_id: str, body: dict) -> dict:
    pub = _publisher()
    d = _task_dir(task_id)
    plats = body.get("platforms") or []
    if isinstance(plats, str):
        plats = [x.strip() for x in plats.split(",") if x.strip()]
    if not plats:
        raise ApiError(400, "NO_PLATFORMS", "platforms is required")
    if not pub.is_configured():
        raise ApiError(503, "PUBLISH_NOT_CONFIGURED", "COMPOSIO_API_KEY is not set")
    try:
        results = pub.publish_task(d, plats, privacy=str(body.get("privacy") or "private"), title=str(body.get("title") or ""),
                                   caption=str(body.get("caption") or ""), hashtags=body.get("hashtags"), variant=int(body.get("variant") or 1))
    except pub.PublishError as e:
        raise ApiError(400, "PUBLISH_ERROR", str(e))
    return {"task_id": task_id, "results": [r.__dict__ for r in results]}


@app.get("/api/tasks/{task_id}/publish", tags=["publish"])
def publish_results(task_id: str) -> dict:
    pub = _publisher()
    return {"task_id": task_id, "results": pub.load_results(_task_dir(task_id))}


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    p = WEBUI_DIR / "index.html"
    if not p.is_file():
        raise ApiError(404, "WEBUI_MISSING", f"webui/index.html not found at {p}")
    return FileResponse(str(p), media_type="text/html", headers={"Cache-Control": "no-cache"})


if WEBUI_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEBUI_DIR)), name="static")


def main() -> None:
    """`python -m moneyprintercannon.api` — run the dev server with settings from env."""
    import uvicorn

    settings.ensure_dirs()
    uvicorn.run("moneyprintercannon.api:app", host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
