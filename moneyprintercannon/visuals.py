"""Per-scene visuals: ai_image | ai_video | stock | mixed | local → visuals/scene-NN.* + visuals.json.

All AI tasks are dispatched together (client.run_many, 1 s gap). Existing valid scene files
are reused on resume (no double payment). Stock failures fall back to AI images per scene.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import shutil
from pathlib import Path
from typing import Optional

from loguru import logger

from .genosai import GenosaiClient, GenosaiValidationError, InsufficientCredits, duration_choices, reference_spec
from .media import is_valid_media, kind_of, probe
from .schema import Scene, Script, Timing, VideoParams, VisualAsset
from .stock import StockUnavailable, download_video, pick_for_scene, stock_available

KEN_CYCLE = ("in", "out", "left", "right")
FALLBACK_VIDEO_RANGE: dict[str, tuple[int, int]] = {
    "grok-imagine-1.5": (1, 15),
    "kling-3.0": (3, 15),
    "seedance-2.0": (4, 15),
    "seedance-2.0-fast": (4, 15),
    "seedance-2.5": (4, 30),
    "wan-3.0": (5, 10),
    "minimax-h3": (6, 10),
    "gemini-omni-1.1-flash": (4, 8),
}
FALLBACK_VIDEO_OPTIONS: dict[str, list[int]] = {
    "veo-3.1-lite": [4, 6, 8],
    "veo-3.1-fast": [4, 6, 8],
    "veo-3.1-quality": [4, 6, 8],
}


def visuals_dir(task_dir: Path) -> Path:
    d = task_dir / "visuals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_visuals(task_dir: Path, assets: list[VisualAsset]) -> None:
    (visuals_dir(task_dir) / "visuals.json").write_text(
        json.dumps([a.model_dump() for a in assets], ensure_ascii=False, indent=1), encoding="utf-8")


def load_visuals(task_dir: Path) -> list[VisualAsset]:
    p = task_dir / "visuals" / "visuals.json"
    if not p.is_file():
        raise FileNotFoundError(p)
    return [VisualAsset.model_validate(d) for d in json.loads(p.read_text(encoding="utf-8"))]


def ken_burns_for(index: int, params: VideoParams) -> str:
    if params.ken_burns == "auto":
        return KEN_CYCLE[(index - 1) % len(KEN_CYCLE)]
    return params.ken_burns


def scene_seconds(timing: Timing, index: int) -> float:
    for s in timing.scenes:
        if s.index == index:
            return max(0.5, s.end - s.start)
    return 5.0


def pick_video_duration(client: Optional[GenosaiClient], model: str, need: float) -> int:
    """Smallest allowed duration ≥ need (capped at the model max; longer scenes loop in Remotion)."""
    options: list[int] = []
    lo = hi = 0
    if client is not None:
        try:
            options, lo, hi = duration_choices(client.model_info(model))
        except Exception as e:  # noqa: BLE001 — catalog problems → static fallback
            logger.debug("duration_choices failed: {}", e)
    if not options and not hi:
        if model in FALLBACK_VIDEO_OPTIONS:
            options = FALLBACK_VIDEO_OPTIONS[model]
        else:
            lo, hi = FALLBACK_VIDEO_RANGE.get(model, (4, 10))
    want = int(math.ceil(need))
    if options:
        fits = [o for o in options if o >= want]
        return min(fits) if fits else max(options)
    return max(lo or 1, min(hi or want, want))


def _existing(task_dir: Path, index: int) -> Optional[Path]:
    for ext in (".mp4", ".jpg", ".png", ".webp", ".mov"):
        p = task_dir / "visuals" / f"scene-{index:02d}{ext}"
        if p.is_file() and p.stat().st_size > 0 and is_valid_media(p):
            return p
    return None


def _asset_from_file(task_dir: Path, index: int, path: Path, source: str, credit: str = "", cost: float = 0.0, params: Optional[VideoParams] = None) -> VisualAsset:
    pr = probe(path)
    kind = "video" if pr.has_video and pr.duration > 0.2 and kind_of(path) == "video" else "image"
    return VisualAsset(index=index, kind=kind, path=str(path.relative_to(task_dir)), source=source,
                       duration=round(pr.duration, 3) if kind == "video" else 0.0, width=pr.width, height=pr.height,
                       credit=credit, cost_credits=cost, ken_burns=(ken_burns_for(index, params) if kind == "image" and params else ""))


def _place(src: Path, dest: Path) -> Path:
    """Hard-link when possible (cache → task), else copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)
    return dest


def _image_input(client: GenosaiClient, scene: Scene, params: VideoParams) -> dict:
    return client.filter_input(params.image_model, {"prompt": scene.visual_prompt, "aspect_ratio": params.aspect, "resolution": "1K"})


def _video_input(client: GenosaiClient, scene: Scene, params: VideoParams, seconds: float, image_url: str = "") -> dict:
    prompt = scene.visual_prompt if not scene.motion else f"{scene.visual_prompt}. Camera: {scene.motion}"
    dur = pick_video_duration(client, params.video_model, seconds)
    payload: dict = {"prompt": prompt, "aspect_ratio": params.aspect, "resolution": params.video_resolution,
                     "duration": str(dur), "generate_audio": False}
    if image_url:
        field, _, _ = reference_spec(client.model_info(params.video_model))
        payload[field or "image_urls"] = [image_url]
    return client.filter_input(params.video_model, payload)


def video_needs_image(client: GenosaiClient, model: str) -> bool:
    """grok-imagine-1.5 & co. are image-to-video only (references.required in the catalog)."""
    try:
        _, required, _ = reference_spec(client.model_info(model))
        return required
    except Exception:  # noqa: BLE001
        return model.startswith("grok-imagine")


def _sniff_ext(path: Path) -> str:
    """Real container by magic bytes ('' if unknown) — z-image returns PNG behind any name."""
    head = path.read_bytes()[:16]
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp":
        return ".mp4"
    return ""


def _fix_ext(path: Path) -> Path:
    real = _sniff_ext(path)
    if real and real != path.suffix.lower() and not (real == ".jpg" and path.suffix.lower() == ".jpeg"):
        new = path.with_suffix(real)
        if new.exists():
            new.unlink()
        path.rename(new)
        return new
    return path


def _ext_for(url: str, default: str) -> str:
    low = url.lower().split("?")[0]
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"):
        if low.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return default


def _run_ai(client: GenosaiClient, task_dir: Path, scenes: list[Scene], params: VideoParams, timing: Timing, mode: str) -> tuple[dict[int, VisualAsset], list[str]]:
    """Generate for the given scenes in parallel. Returns {index: asset}; failures of single
    scenes come back as warnings (the caller decides on a fallback)."""
    if not scenes:
        return {}, []
    out: dict[int, VisualAsset] = {}
    warnings: list[str] = []
    stills: dict[int, VisualAsset] = {}
    if mode == "ai_video" and video_needs_image(client, params.video_model):
        # image-to-video model: make the key frame first, upload it, then animate it
        stills, w = _run_ai(client, task_dir, scenes, params, timing, "ai_image")
        warnings.extend(w)
        scenes = [sc for sc in scenes if sc.index in stills]
        if not scenes:
            return out, warnings
    jobs: list[tuple[str, dict]] = []
    for sc in scenes:
        if mode == "ai_video":
            url = ""
            if sc.index in stills:
                url = client.upload(task_dir / stills[sc.index].path)
            jobs.append((params.video_model, _video_input(client, sc, params, scene_seconds(timing, sc.index), url)))
        else:
            jobs.append((params.image_model, _image_input(client, sc, params)))
    logger.info("visuals: {} {} tasks on {}", len(jobs), mode, jobs[0][0])
    results = client.run_many(jobs, raise_on_error=False)
    for sc, res in zip(scenes, results):
        if isinstance(res, InsufficientCredits):
            raise res
        still_cost = stills[sc.index].cost_credits if sc.index in stills else 0.0
        if isinstance(res, Exception):
            msg = f"scene {sc.index}: {mode} failed: {str(res)[:160]}"
            logger.warning(msg)
            warnings.append(msg)
            if sc.index in stills:  # keep the key frame as the scene visual (paid already)
                out[sc.index] = stills[sc.index]
            continue
        ext = _ext_for(res.url, ".mp4" if mode == "ai_video" else ".jpg")
        dest = visuals_dir(task_dir) / f"scene-{sc.index:02d}{ext}"
        try:
            client.download(res.url, dest, min_bytes=2000)
            dest = _fix_ext(dest)
            out[sc.index] = _asset_from_file(task_dir, sc.index, dest, f"genosai:{jobs[0][0]}", cost=res.cost + still_cost, params=params)
            if sc.index in stills and out[sc.index].kind == "video":
                still = task_dir / stills[sc.index].path
                still.rename(still.with_name(f"scene-{sc.index:02d}.keyframe{still.suffix}"))
        except Exception as e:  # noqa: BLE001
            msg = f"scene {sc.index}: download failed: {e}"
            logger.warning(msg)
            warnings.append(msg)
            if sc.index in stills:
                out[sc.index] = stills[sc.index]
    return out, warnings


def _stock_for(task_dir: Path, scenes: list[Scene], params: VideoParams, timing: Timing, used: set[str]) -> tuple[dict[int, VisualAsset], list[str]]:
    out: dict[int, VisualAsset] = {}
    warnings: list[str] = []
    for sc in scenes:
        need = scene_seconds(timing, sc.index)
        try:
            pick = pick_for_scene(sc, params.aspect, need, used, params.stock_provider)
            if pick is None:
                warnings.append(f"scene {sc.index}: no stock match for {sc.search_terms}")
                continue
            cached = download_video(pick.url)
            dest = _place(cached, visuals_dir(task_dir) / f"scene-{sc.index:02d}.mp4")
            out[sc.index] = _asset_from_file(task_dir, sc.index, dest, pick.provider, credit=pick.credit)
        except StockUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            warnings.append(f"scene {sc.index}: stock failed: {str(e)[:120]}")
    return out, warnings


def _local(task_dir: Path, scenes: list[Scene], params: VideoParams) -> dict[int, VisualAsset]:
    files = [Path(f).expanduser() for f in params.local_files if Path(f).expanduser().is_file() and kind_of(f) in ("image", "video")]
    if not files:
        raise FileNotFoundError("visual_source=local but local_files has no existing image/video files")
    out: dict[int, VisualAsset] = {}
    for sc, src in zip(scenes, itertools.cycle(files)):
        dest = visuals_dir(task_dir) / f"scene-{sc.index:02d}{src.suffix.lower()}"
        shutil.copy2(src, dest)
        out[sc.index] = _asset_from_file(task_dir, sc.index, dest, "local", params=params)
    return out


def resolve_visuals(script: Script, timing: Timing, params: VideoParams, task_dir: Path, client: Optional[GenosaiClient],
                    warnings: Optional[list[str]] = None) -> tuple[list[VisualAsset], float]:
    task_dir = Path(task_dir)
    warnings = warnings if warnings is not None else []
    assets: dict[int, VisualAsset] = {}

    # reuse what already exists (resume)
    try:
        for a in load_visuals(task_dir):
            if (task_dir / a.path).is_file() and is_valid_media(task_dir / a.path):
                assets[a.index] = a
    except FileNotFoundError:
        pass
    todo = [sc for sc in script.scenes if sc.index not in assets]
    if assets:
        logger.info("visuals: reusing {} existing assets", len(assets))

    source = params.visual_source
    if source in ("stock", "mixed") and todo and not stock_available(params.stock_provider):
        msg = f"visual_source={source} but no stock keys configured → ai_image ({params.image_model})"
        logger.warning(msg)
        warnings.append(msg)
        source = "ai_image"

    if source == "local":
        assets.update(_local(task_dir, todo, params))
        todo = []
    elif source in ("stock", "mixed") and todo:
        used: set[str] = set()
        try:
            got, w = _stock_for(task_dir, todo, params, timing, used)
        except StockUnavailable as e:
            got, w = {}, [f"stock unavailable: {e}"]
        assets.update(got)
        warnings.extend(w)
        todo = [sc for sc in todo if sc.index not in assets]
        if todo:
            warnings.append(f"{len(todo)} scene(s) without stock → ai_image fallback ({params.image_model})")
            source = "ai_image"

    if todo and source in ("ai_image", "ai_video"):
        if client is None:
            raise RuntimeError("Genosai client required for AI visuals")
        got, w = _run_ai(client, task_dir, todo, params, timing, source)
        assets.update(got)
        warnings.extend(w)
        todo = [sc for sc in todo if sc.index not in assets]
        if todo and source == "ai_video":
            warnings.append(f"{len(todo)} scene(s) failed on {params.video_model} → ai_image fallback")
            got, w = _run_ai(client, task_dir, todo, params, timing, "ai_image")
            assets.update(got)
            warnings.extend(w)
            todo = [sc for sc in todo if sc.index not in assets]
        if todo:
            # last resort: retry images once (different seed), then give up loudly
            got, w = _run_ai(client, task_dir, todo, params, timing, "ai_image")
            assets.update(got)
            warnings.extend(w)
            todo = [sc for sc in todo if sc.index not in assets]
    if todo:
        raise GenosaiValidationError(f"no visual for scenes {[s.index for s in todo]}: {warnings[-3:]}")

    ordered = [assets[sc.index] for sc in script.scenes]
    save_visuals(task_dir, ordered)
    cost = round(sum(a.cost_credits for a in ordered), 3)
    logger.info("visuals: {} assets ({} video, {} image), cost {}", len(ordered),
                sum(a.kind == "video" for a in ordered), sum(a.kind == "image" for a in ordered), cost)
    return ordered, cost
