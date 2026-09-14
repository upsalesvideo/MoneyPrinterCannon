"""Stage orchestrator: script → voice → timing → visuals → music → render.

Every stage is idempotent (skipped when succeeded and its outputs exist), state.json is
rewritten atomically after each stage, cost is summed from real API answers, log.txt gets a
loguru sink per run. Cancel = `cancel.flag` in the task dir, checked between stages.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from .config import TASKS_DIR, settings
from .estimate import estimate as make_estimate
from .genosai import GenosaiClient
from .schema import STAGES, Script, Stage, StageState, TaskResult, TaskState, Timing, VideoParams, VisualAsset, new_task_id

PROGRESS: dict[str, int] = {"script": 10, "voice": 30, "timing": 40, "visuals": 70, "music": 78, "render": 100}
_state_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class TaskCancelled(RuntimeError):
    pass


class TaskNotFound(FileNotFoundError):
    pass


# ---------------------------------------------------------------------------
# paths / io
# ---------------------------------------------------------------------------
def task_dir(task_id: str) -> Path:
    if not task_id or "/" in task_id or task_id in (".", ".."):
        raise TaskNotFound(task_id)
    d = (TASKS_DIR / task_id).resolve()
    if TASKS_DIR.resolve() not in d.parents:
        raise TaskNotFound(task_id)
    return d


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _lock_for(task_id: str) -> threading.Lock:
    with _locks_guard:
        return _state_locks.setdefault(task_id, threading.Lock())


def save_state(state: TaskState) -> None:
    state.touch()
    with _lock_for(state.task_id):
        _atomic_write(task_dir(state.task_id) / "state.json", state.model_dump_json(indent=1))


def load_state(task_id: str) -> TaskState:
    p = task_dir(task_id) / "state.json"
    if not p.is_file():
        raise TaskNotFound(task_id)
    return TaskState.model_validate_json(p.read_text(encoding="utf-8"))


def load_params(task_id: str) -> VideoParams:
    p = task_dir(task_id) / "params.json"
    if not p.is_file():
        raise TaskNotFound(task_id)
    return VideoParams.model_validate_json(p.read_text(encoding="utf-8"))


def load_script(task_id: str) -> Script:
    return Script.model_validate_json((task_dir(task_id) / "script.json").read_text(encoding="utf-8"))


def load_timing(task_id: str) -> Timing:
    return Timing.model_validate_json((task_dir(task_id) / "timing.json").read_text(encoding="utf-8"))


def list_tasks(limit: int = 50) -> list[TaskState]:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    dirs = [d for d in TASKS_DIR.iterdir() if d.is_dir() and (d / "state.json").is_file()]
    dirs.sort(key=lambda d: (d / "state.json").stat().st_mtime, reverse=True)
    out: list[TaskState] = []
    for d in dirs[:limit]:
        try:
            out.append(load_state(d.name))
        except Exception as e:  # noqa: BLE001 — a corrupt state must not hide the others
            logger.warning("skipping {}: {}", d.name, e)
    return out


def delete_task(task_id: str) -> None:
    d = task_dir(task_id)
    if d.is_dir():
        shutil.rmtree(d)


def cancel_task(task_id: str) -> None:
    (task_dir(task_id) / "cancel.flag").touch()


def _cancelled(d: Path) -> bool:
    return (d / "cancel.flag").is_file()


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
def create_task(params: VideoParams, task_id: Optional[str] = None) -> str:
    params.require_content()
    settings.ensure_dirs()
    tid = task_id or new_task_id()
    d = TASKS_DIR / tid
    d.mkdir(parents=True, exist_ok=False)
    _atomic_write(d / "params.json", params.model_dump_json(indent=1))
    est = make_estimate(params)
    state = TaskState(task_id=tid, status="queued", estimate_credits=est.total, topic=params.topic or params.script[:80])
    save_state(state)
    logger.info("task {} created (estimate ≈ {} cr)", tid, est.total)
    return tid


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def _outputs_exist(stage: str, d: Path) -> bool:
    if stage == "script":
        return (d / "script.json").is_file()
    if stage == "voice":
        return (d / "voice" / "voice.wav").is_file() and (d / "voice" / "voice.json").is_file()
    if stage == "timing":
        return (d / "timing.json").is_file()
    if stage == "visuals":
        return (d / "visuals" / "visuals.json").is_file()
    if stage == "music":
        return True  # music=none produces nothing; status alone decides
    if stage == "render":
        return (d / "final.mp4").is_file()
    return False


def run(task_id: str, stop_at: Optional[Stage] = None, force: bool = False,
        on_progress: Optional[Callable[[TaskState], None]] = None) -> TaskState:
    d = task_dir(task_id)
    params = load_params(task_id)
    state = load_state(task_id)
    sink = logger.add(str(d / "log.txt"), level="INFO", enqueue=True, encoding="utf-8",
                      format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {message}",
                      filter=lambda rec, tid=task_id: rec["extra"].get("task") == tid)
    log = logger.bind(task=task_id)
    client: Optional[GenosaiClient] = None

    def get_client() -> GenosaiClient:
        nonlocal client
        if client is None:
            client = GenosaiClient()
        return client

    state.status = "running"
    state.error = None
    state.failed_stage = None
    if (d / "cancel.flag").is_file():
        (d / "cancel.flag").unlink()
    save_state(state)
    ctx: dict = {}
    try:
        for stage in STAGES:
            if _cancelled(d):
                raise TaskCancelled("cancelled by user")
            st = state.stages.setdefault(stage, StageState())
            if st.status == "succeeded" and _outputs_exist(stage, d) and not force:
                log.info("[{}] skip (already done)", stage)
                _load_ctx(stage, task_id, ctx)
                state.stage = stage
                state.progress = PROGRESS[stage]
                save_state(state)
            else:
                st.status = "running"
                st.started_at = time.time()
                st.error = None
                state.stage = stage
                save_state(state)
                if on_progress:
                    on_progress(state)
                log.info("[{}] start", stage)
                cost = _run_stage(stage, task_id, params, state, ctx, get_client, log)
                st.cost_credits = round(float(cost or 0), 3)
                st.status = "succeeded"
                st.finished_at = time.time()
                state.cost_credits = round(sum(s.cost_credits for s in state.stages.values()), 3)
                state.progress = PROGRESS[stage]
                save_state(state)
                log.info("[{}] done in {:.0f}s, cost {} (total {})", stage, st.finished_at - st.started_at, st.cost_credits, state.cost_credits)
            if on_progress:
                on_progress(state)
            if stop_at and stage == stop_at:
                state.status = "queued"
                save_state(state)
                log.info("stopped after stage {} as requested", stage)
                return state
        state.status = "succeeded"
        state.result = _result(task_id, ctx)
        save_state(state)
        log.info("task finished: {} ({:.1f}s, {} cr)", state.result.video, state.result.duration, state.cost_credits)
        return state
    except TaskCancelled as e:
        state.status = "cancelled"
        state.error = str(e)
        if state.stage:
            state.stages[state.stage].status = "failed" if state.stages[state.stage].status == "running" else state.stages[state.stage].status
        save_state(state)
        log.warning("task cancelled")
        raise
    except Exception as e:
        stage = state.stage or "script"
        st = state.stages.setdefault(stage, StageState())
        st.status = "failed"
        st.error = str(e)[:1000]
        st.finished_at = time.time()
        state.status = "failed"
        state.failed_stage = stage  # type: ignore[assignment]
        state.error = f"{type(e).__name__}: {e}"[:2000]
        save_state(state)
        log.exception("[{}] failed: {}", stage, e)
        raise
    finally:
        logger.remove(sink)


def _load_ctx(stage: str, task_id: str, ctx: dict) -> None:
    d = task_dir(task_id)
    if stage == "script":
        ctx["script"] = load_script(task_id)
    elif stage == "voice":
        from .tts import load_scene_audios

        ctx["scene_audios"] = load_scene_audios(d)
    elif stage == "timing":
        ctx["timing"] = load_timing(task_id)
    elif stage == "visuals":
        from .visuals import load_visuals

        ctx["visuals"] = load_visuals(d)
    elif stage == "music":
        p = d / "music" / "bgm.mp3"
        ctx["music"] = p if p.is_file() else None


def _run_stage(stage: str, task_id: str, params: VideoParams, state: TaskState, ctx: dict,
               get_client: Callable[[], GenosaiClient], log) -> float:
    d = task_dir(task_id)
    if stage == "script":
        from .llm import generate_script

        script, cost = generate_script(params, get_client())
        _atomic_write(d / "script.json", script.model_dump_json(indent=1))
        ctx["script"] = script
        state.topic = script.topic
        return cost
    if stage == "voice":
        from .tts import synthesize

        script: Script = ctx.get("script") or load_script(task_id)
        items, cost = synthesize(script, params, d, None if params.custom_voice_file else get_client())
        _atomic_write(d / "script.json", script.model_dump_json(indent=1))  # narration may have been rephrased
        ctx["script"] = script
        ctx["scene_audios"] = items
        return cost
    if stage == "timing":
        from .timing import build_timing
        from .tts import load_scene_audios

        script = ctx.get("script") or load_script(task_id)
        items = ctx.get("scene_audios") or load_scene_audios(d)
        ctx["timing"] = build_timing(script, items, params, d)
        return 0.0
    if stage == "visuals":
        from .visuals import resolve_visuals

        script = ctx.get("script") or load_script(task_id)
        timing = ctx.get("timing") or load_timing(task_id)
        warnings: list[str] = []
        needs_client = params.visual_source in ("ai_image", "ai_video", "stock", "mixed")
        assets, cost = resolve_visuals(script, timing, params, d, get_client() if needs_client else None, warnings)
        for w in warnings:
            if w not in state.warnings:
                state.warnings.append(w)
        ctx["visuals"] = assets
        return cost
    if stage == "music":
        from .music import get_music

        script = ctx.get("script") or load_script(task_id)
        timing = ctx.get("timing") or load_timing(task_id)
        total = timing.voice_duration + (params.outro_seconds if params.outro_text else 0.0)
        path, cost = get_music(script, params, d, get_client() if params.music == "genosai" else None, total)
        ctx["music"] = path
        state.stages["music"].note = "none" if path is None else str(path.relative_to(d))
        return cost
    if stage == "render":
        _stage_render(task_id, params, state, ctx, log)
        return 0.0
    raise ValueError(stage)


def _stage_render(task_id: str, params: VideoParams, state: TaskState, ctx: dict, log) -> None:
    from .render import build_props, cover_time, make_cover, mix_audio, render_video, verify_output
    from .visuals import load_visuals

    d = task_dir(task_id)
    script: Script = ctx.get("script") or load_script(task_id)
    timing: Timing = ctx.get("timing") or load_timing(task_id)
    visuals: list[VisualAsset] = ctx.get("visuals") or load_visuals(d)
    music = ctx.get("music") if "music" in ctx else ((d / "music" / "bgm.mp3") if (d / "music" / "bgm.mp3").is_file() else None)
    voice = d / "voice" / "voice.wav"
    props = build_props(script, timing, visuals, params, d, music)
    total = float(props["durationSec"])
    silent = render_video(d, props)
    final = mix_audio(d, silent, voice, music, params.music_volume, total)
    dur = verify_output(final, total)
    if params.cover:
        make_cover(final, d / "cover.jpg", at_sec=cover_time(timing))
    variants: list[str] = []
    if params.variants > 1 and len(props["scenes"]) > 2:
        rnd = random.Random(task_id)
        for v in range(2, params.variants + 1):
            alt = json.loads(json.dumps(props))
            body = alt["scenes"][1:]
            visual_keys = ("kind", "src", "kenBurns", "srcDurationSec")
            pool = [{k: s[k] for k in visual_keys} for s in body]
            rnd.shuffle(pool)
            for s, vis in zip(body, pool):
                s.update(vis)
                s["loop"] = bool(s["kind"] == "video" and s["srcDurationSec"] and s["srcDurationSec"] < (s["end"] - s["start"]))
            name = f"final-{v}.mp4"
            log.info("variant {}: reshuffled visuals", v)
            s_out = render_video(d, alt, out_name=f"silent-{v}.mp4", props_name=f"props-{v}.json")
            mix_audio(d, s_out, voice, music, params.music_volume, total, out_name=name)
            variants.append(name)
    ctx["result"] = TaskResult(video="final.mp4", cover="cover.jpg" if (d / "cover.jpg").is_file() else "", duration=round(dur, 3),
                               title=script.social.title or script.title, caption=script.social.caption,
                               hashtags=script.social.hashtags, variants=variants)


def _result(task_id: str, ctx: dict) -> TaskResult:
    if "result" in ctx:
        return ctx["result"]
    d = task_dir(task_id)
    script = ctx.get("script") or load_script(task_id)
    dur = 0.0
    if (d / "final.mp4").is_file():
        from .media import duration as media_duration

        dur = media_duration(d / "final.mp4")
    return TaskResult(video="final.mp4" if (d / "final.mp4").is_file() else "", cover="cover.jpg" if (d / "cover.jpg").is_file() else "",
                      duration=round(dur, 3), title=script.social.title or script.title, caption=script.social.caption,
                      hashtags=script.social.hashtags,
                      variants=sorted(p.name for p in d.glob("final-*.mp4")))


def result(task_id: str) -> TaskResult:
    st = load_state(task_id)
    return st.result or _result(task_id, {})


# alias used by api.py
request_cancel = cancel_task
