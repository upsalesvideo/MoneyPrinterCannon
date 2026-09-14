"""`cannon` CLI (typer). Logs go to stderr; with --json the only stdout line is the result JSON."""
from __future__ import annotations

import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import typer
from loguru import logger

from . import pipeline
from .config import REMOTION_DIR, STORAGE, settings
from .estimate import estimate as make_estimate
from .schema import STAGES, Stage, VideoParams

app = typer.Typer(name="cannon", help="CashCannon — topic → finished short video (Genosai + Remotion).", no_args_is_help=True,
                  pretty_exceptions_enable=False)


def _setup_logs(json_mode: bool = False, level: Optional[str] = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level or settings.log_level,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}", colorize=not json_mode)


def _say(msg: str, json_mode: bool = False) -> None:
    typer.echo(msg, err=json_mode)


def _params_from_cli(topic: str, **kw) -> VideoParams:
    data = {k: v for k, v in kw.items() if v is not None}
    script_file = data.pop("script_file", None)
    if script_file:
        data["script"] = Path(script_file).expanduser().read_text(encoding="utf-8")
    data["topic"] = topic
    if data.get("local_files"):
        data["local_files"] = [s for chunk in data["local_files"] for s in str(chunk).split(",") if s.strip()]
    return VideoParams.model_validate(data)


def _print_estimate(params: VideoParams, json_mode: bool) -> None:
    est = make_estimate(params)
    lines = [f"Estimate ≈ {est.total:g} credits  (" + ", ".join(f"{k} {v:g}" for k, v in est.breakdown.items()) + ")"]
    try:
        from .genosai import GenosaiClient

        bal = GenosaiClient().balance()
        lines.append(f"Balance: {bal['total']:g} credits")
        if bal["total"] < est.total:
            lines.append("WARNING: balance is below the estimate")
    except Exception as e:  # noqa: BLE001
        lines.append(f"Balance: unavailable ({e})")
    for n in est.notes:
        lines.append(f"  note: {n}")
    _say("\n".join(lines), json_mode)


def _confirm(yes: bool, json_mode: bool) -> None:
    if yes or not sys.stdin.isatty():
        return
    if not typer.confirm("Proceed and spend credits?", default=True, err=json_mode):
        raise typer.Exit(code=2)


def _emit_result(state, json_mode: bool) -> None:
    payload = {"task_id": state.task_id, "status": state.status, "cost_credits": state.cost_credits,
               "dir": str(pipeline.task_dir(state.task_id)), "result": state.result.model_dump() if state.result else None,
               "warnings": state.warnings, "error": state.error, "failed_stage": state.failed_stage, "stage": state.stage}
    if json_mode:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    d = pipeline.task_dir(state.task_id)
    if state.result and state.result.video:
        _say(f"\nDone: {d / state.result.video}  ({state.result.duration:.1f}s, {state.cost_credits:g} credits)")
        if state.result.cover:
            _say(f"Cover: {d / state.result.cover}")
        if state.result.title:
            _say(f"Title: {state.result.title}")
        if state.result.caption:
            _say(f"Caption: {state.result.caption}")
        if state.result.hashtags:
            _say("Tags: " + " ".join("#" + h for h in state.result.hashtags))
        for v in state.result.variants:
            _say(f"Variant: {d / v}")
    else:
        _say(f"\nTask {state.task_id}: {state.status} (stage {state.stage}); dir {d}")
    for w in state.warnings:
        _say(f"warning: {w}")


# ---------------------------------------------------------------------------
# make
# ---------------------------------------------------------------------------
@app.command()
def make(
    topic: str = typer.Argument("", help="Topic / idea (or use --script-file)."),
    aspect: Optional[str] = typer.Option(None, "--aspect", help="9:16 | 16:9 | 1:1"),
    seconds: Optional[int] = typer.Option(None, "--seconds", help="Target narration length."),
    scenes: Optional[int] = typer.Option(None, "--scenes", help="Scene count (0 = auto)."),
    visuals: Optional[str] = typer.Option(None, "--visuals", help="ai_image | ai_video | stock | mixed | local"),
    image_model: Optional[str] = typer.Option(None, "--image-model"),
    video_model: Optional[str] = typer.Option(None, "--video-model"),
    video_resolution: Optional[str] = typer.Option(None, "--video-resolution"),
    local_files: Optional[list[str]] = typer.Option(None, "--local-file", help="For --visuals local (repeatable)."),
    visual_style: Optional[str] = typer.Option(None, "--visual-style"),
    voice: Optional[str] = typer.Option(None, "--voice"),
    voice_style: Optional[str] = typer.Option(None, "--voice-style"),
    voice_pace: Optional[str] = typer.Option(None, "--voice-pace"),
    music: Optional[str] = typer.Option(None, "--music", help="none | genosai | file"),
    music_file: Optional[str] = typer.Option(None, "--music-file"),
    music_prompt: Optional[str] = typer.Option(None, "--music-prompt"),
    music_volume: Optional[float] = typer.Option(None, "--music-volume"),
    captions: Optional[bool] = typer.Option(None, "--captions/--no-captions"),
    caption_preset: Optional[str] = typer.Option(None, "--caption-preset", help="karaoke | bold | clean | minimal"),
    caption_position: Optional[str] = typer.Option(None, "--caption-position"),
    outro: Optional[str] = typer.Option(None, "--outro", help="End card text."),
    outro_sub: Optional[str] = typer.Option(None, "--outro-sub"),
    title_card: Optional[bool] = typer.Option(None, "--title-card/--no-title-card"),
    watermark: Optional[str] = typer.Option(None, "--watermark"),
    language: Optional[str] = typer.Option(None, "--language"),
    style_hint: Optional[str] = typer.Option(None, "--style-hint"),
    text_model: Optional[str] = typer.Option(None, "--text-model"),
    script_file: Optional[str] = typer.Option(None, "--script-file", help="Ready narration text file."),
    custom_voice: Optional[str] = typer.Option(None, "--custom-voice", help="Your narration audio (needs --script-file)."),
    timing_backend: Optional[str] = typer.Option(None, "--timing", help="auto | mlx | faster | proportional"),
    variants: Optional[int] = typer.Option(None, "--variants"),
    stop_at: Optional[str] = typer.Option(None, "--stop-at", help="Stop after this stage: " + " | ".join(STAGES)),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the estimate confirmation."),
    json_out: bool = typer.Option(False, "--json", help="Print the result JSON to stdout (logs → stderr)."),
) -> None:
    """Generate a video from a topic (or a ready script)."""
    _setup_logs(json_out)
    params = _params_from_cli(
        topic, aspect=aspect, target_seconds=seconds, scene_count=scenes, visual_source=visuals, image_model=image_model,
        video_model=video_model, video_resolution=video_resolution, local_files=local_files, visual_style=visual_style,
        voice=voice, voice_style=voice_style, voice_pace=voice_pace, music=music, music_file=music_file,
        music_prompt=music_prompt, music_volume=music_volume, captions=captions, caption_preset=caption_preset,
        caption_position=caption_position, outro_text=outro, outro_sub=outro_sub, title_card=title_card,
        watermark=watermark, language=language, style_hint=style_hint, text_model=text_model, script_file=script_file,
        custom_voice_file=custom_voice, timing_backend=timing_backend, variants=variants,
    )
    try:
        params.require_content()
    except ValueError as e:
        raise typer.BadParameter(str(e))
    if stop_at and stop_at not in STAGES:
        raise typer.BadParameter(f"--stop-at must be one of {STAGES}")
    _print_estimate(params, json_out)
    _confirm(yes, json_out)
    tid = pipeline.create_task(params)
    _say(f"task {tid}", json_out)
    try:
        state = pipeline.run(tid, stop_at=stop_at)  # type: ignore[arg-type]
    except Exception as e:  # noqa: BLE001
        state = pipeline.load_state(tid)
        _emit_result(state, json_out)
        _say(f"FAILED at {state.failed_stage}: {e}", json_out)
        raise typer.Exit(code=1)
    _emit_result(state, json_out)


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------
def _load_manifest(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        items = json.loads(text)
    else:
        items = [json.loads(line) for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not all(isinstance(i, dict) for i in items):
        raise typer.BadParameter("manifest items must be objects")
    if len(items) > 100:
        raise typer.BadParameter("at most 100 tasks per batch")
    return items


@app.command()
def batch(
    manifest: Path = typer.Argument(..., exists=True, readable=True, help="JSON array or JSONL of VideoParams overrides."),
    parallel: int = typer.Option(2, "--parallel", "-p", min=1, max=8),
    defaults: Optional[str] = typer.Option(None, "--defaults", help="JSON with default VideoParams for every item."),
    stop_at: Optional[str] = typer.Option(None, "--stop-at"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Run many videos from a manifest; summary JSON → stdout and storage/batch-<ts>.json."""
    _setup_logs(json_out)
    base = json.loads(defaults) if defaults else {}
    items = _load_manifest(manifest)
    if not items:
        raise typer.BadParameter("empty manifest")
    params_list = [VideoParams.model_validate({**base, **it}) for it in items]
    for p in params_list:
        p.require_content()
    total_est = sum(make_estimate(p).total for p in params_list)
    _say(f"{len(params_list)} tasks, estimate ≈ {total_est:g} credits, parallel {parallel}", json_out)
    _confirm(yes, json_out)
    ids = [pipeline.create_task(p) for p in params_list]

    def _one(tid: str) -> dict:
        try:
            st = pipeline.run(tid, stop_at=stop_at)  # type: ignore[arg-type]
        except Exception as e:  # noqa: BLE001
            st = pipeline.load_state(tid)
            return {"task_id": tid, "status": st.status, "error": str(e)[:300], "failed_stage": st.failed_stage, "cost_credits": st.cost_credits}
        return {"task_id": tid, "status": st.status, "cost_credits": st.cost_credits, "topic": st.topic,
                "video": str(pipeline.task_dir(tid) / st.result.video) if st.result and st.result.video else "",
                "cover": str(pipeline.task_dir(tid) / st.result.cover) if st.result and st.result.cover else "",
                "title": st.result.title if st.result else "", "warnings": st.warnings}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        rows = list(ex.map(_one, ids))
    summary = {"started_at": t0, "elapsed_sec": round(time.time() - t0, 1), "count": len(rows),
               "succeeded": sum(r["status"] == "succeeded" for r in rows), "failed": sum(r["status"] != "succeeded" for r in rows),
               "cost_credits": round(sum(r.get("cost_credits", 0) for r in rows), 3), "tasks": rows}
    out = STORAGE / f"batch-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    typer.echo(json.dumps(summary, ensure_ascii=False) if json_out else f"summary → {out}")
    if summary["failed"]:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# task management
# ---------------------------------------------------------------------------
@app.command()
def resume(task_id: str, force: bool = typer.Option(False, "--force", help="Redo every stage."),
           stop_at: Optional[str] = typer.Option(None, "--stop-at"), json_out: bool = typer.Option(False, "--json")) -> None:
    """Continue a failed / stopped task from its last stage."""
    _setup_logs(json_out)
    try:
        state = pipeline.run(task_id, stop_at=stop_at, force=force)  # type: ignore[arg-type]
    except Exception as e:  # noqa: BLE001
        state = pipeline.load_state(task_id)
        _emit_result(state, json_out)
        _say(f"FAILED at {state.failed_stage}: {e}", json_out)
        raise typer.Exit(code=1)
    _emit_result(state, json_out)


@app.command()
def status(task_id: str, json_out: bool = typer.Option(False, "--json")) -> None:
    """Show state.json of a task."""
    st = pipeline.load_state(task_id)
    if json_out:
        typer.echo(st.model_dump_json())
        return
    _say(f"{st.task_id}  {st.status}  stage={st.stage}  progress={st.progress}%  cost={st.cost_credits:g}  est={st.estimate_credits:g}")
    for name, s in st.stages.items():
        _say(f"  {name:8} {s.status:10} {s.cost_credits:g}" + (f"  {s.error}" if s.error else ""))
    if st.error:
        _say(f"error: {st.error}")
    for w in st.warnings:
        _say(f"warning: {w}")


@app.command("list")
def list_cmd(limit: int = typer.Option(20, "--limit"), json_out: bool = typer.Option(False, "--json")) -> None:
    """List recent tasks."""
    tasks = pipeline.list_tasks(limit)
    if json_out:
        typer.echo(json.dumps([t.model_dump() for t in tasks], ensure_ascii=False))
        return
    for t in tasks:
        _say(f"{t.task_id}  {t.status:9} {t.progress:3}%  {t.cost_credits:7g} cr  {t.topic[:60]}")


@app.command()
def cancel(task_id: str) -> None:
    """Ask a running task to stop after its current stage."""
    pipeline.cancel_task(task_id)
    _say(f"cancel requested for {task_id}")


@app.command()
def delete(task_id: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Delete a task directory."""
    if not yes and not typer.confirm(f"Delete {task_id}?"):
        raise typer.Exit(code=2)
    pipeline.delete_task(task_id)
    _say("deleted")


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------
@app.command("estimate")
def estimate_cmd(topic: str = typer.Argument(""), seconds: Optional[int] = typer.Option(None, "--seconds"),
                 visuals: Optional[str] = typer.Option(None, "--visuals"), image_model: Optional[str] = typer.Option(None, "--image-model"),
                 video_model: Optional[str] = typer.Option(None, "--video-model"), music: Optional[str] = typer.Option(None, "--music"),
                 script_file: Optional[str] = typer.Option(None, "--script-file"), json_out: bool = typer.Option(False, "--json")) -> None:
    """Credit estimate without spending anything."""
    params = _params_from_cli(topic or "estimate", target_seconds=seconds, visual_source=visuals, image_model=image_model,
                              video_model=video_model, music=music, script_file=script_file)
    est = make_estimate(params)
    if json_out:
        typer.echo(est.model_dump_json())
        return
    _say(f"≈ {est.total:g} credits")
    for k, v in est.breakdown.items():
        _say(f"  {k:8} {v:g}")
    for n in est.notes:
        _say(f"  note: {n}")


@app.command()
def balance() -> None:
    """Genosai balance."""
    from .genosai import GenosaiClient

    b = GenosaiClient().balance()
    _say(f"total {b['total']:g}  (main {b['main']:g}, bonus {b['bonus']:g})")


@app.command()
def models(category: Optional[str] = typer.Argument(None, help="photo | video | tts | music | text"),
           json_out: bool = typer.Option(False, "--json")) -> None:
    """Models available via the Genosai public API."""
    from .genosai import GenosaiClient, _enum_values

    cat = GenosaiClient().models()
    if json_out:
        typer.echo(json.dumps(cat if not category else cat.get(category, []), ensure_ascii=False))
        return
    for name, items in cat.items():
        if category and name != category:
            continue
        _say(f"\n[{name}]  ({len(items)})")
        for m in items:
            opts = m.get("input_options") or {}
            bits = []
            for key in ("aspect_ratio", "resolution", "duration"):
                vals = _enum_values(opts.get(key)) if isinstance(opts, dict) else []
                if vals:
                    bits.append(f"{key}: {','.join(str(v) for v in vals[:8])}")
                elif isinstance(opts, dict) and isinstance(opts.get(key), dict) and opts[key].get("max") is not None:
                    bits.append(f"{key}: {opts[key].get('min')}–{opts[key].get('max')}")
            price = m.get("cost_credits_default")
            _say(f"  {m.get('id'):24} {('~' + str(price) + ' cr') if price is not None else '':>10}  " + "; ".join(bits))


@app.command()
def voices() -> None:
    """TTS voices / styles / paces reported by the catalog."""
    from .genosai import GenosaiClient, _enum_values

    info = GenosaiClient().model_info("gemini-3.1-flash-tts") or {}
    opts = info.get("input_options") or {}
    for key in ("voice", "style", "pace", "accent"):
        vals = _enum_values(opts.get(key)) if isinstance(opts, dict) else []
        if vals:
            _say(f"{key}: " + ", ".join(str(v) for v in vals))
    if not opts:
        _say("voice: Charon, Puck, Kore, Leda, Aoede, Fenrir, Orus, Sulafat, Algieba, Callirrhoe … (catalog unavailable)")
        _say("style: Vocal Smile, Newscaster, Whisper, Empathetic, Promo/Hype, Deadpan; pace: Natural, Rapid Fire, The Drift, Staccato")


@app.command()
def serve(host: Optional[str] = typer.Option(None, "--host"), port: Optional[int] = typer.Option(None, "--port"),
          reload: bool = typer.Option(False, "--reload")) -> None:
    """Start the REST API + web UI (uvicorn cashcannon.api:app)."""
    _setup_logs()
    import uvicorn  # lazy: api.py is optional at import time

    uvicorn.run("cashcannon.api:app", host=host or settings.host, port=port or settings.port, reload=reload, log_level="info")


@app.command()
def doctor() -> None:
    """Check the environment: key, ffmpeg, node, remotion, whisper, stock keys."""
    _setup_logs()
    ok = True

    def row(name: str, good: bool, note: str = "", required: bool = True) -> None:
        nonlocal ok
        if required:
            ok = ok and good
        _say(f"  [{'ok' if good else ('!!' if required else '--')}] {name:22} {note}")

    _say("CashCannon doctor")
    row("GENOSAI_API_KEY", bool(settings.genosai_api_key), settings.genosai_api_host)
    if settings.genosai_api_key:
        try:
            from .genosai import GenosaiClient

            c = GenosaiClient()
            b = c.balance()
            row("Genosai balance", True, f"{b['total']:g} credits")
            cats = c.models()
            row("Genosai models", bool(cats), ", ".join(f"{k}:{len(v)}" for k, v in cats.items()))
        except Exception as e:  # noqa: BLE001
            row("Genosai API", False, str(e)[:120])
    row("ffmpeg", bool(shutil.which("ffmpeg")), shutil.which("ffmpeg") or "not found")
    row("ffprobe", bool(shutil.which("ffprobe")), shutil.which("ffprobe") or "not found")
    row("node", bool(shutil.which("node")), shutil.which("node") or "not found (needed for Remotion)")
    row("npx", bool(shutil.which("npx")), shutil.which("npx") or "not found")
    nm = (REMOTION_DIR / "node_modules" / "remotion").exists()
    row("remotion node_modules", nm, str(REMOTION_DIR) + ("" if nm else "  → run: npm install (render.py does it once automatically)"))
    from .timing import available_backends

    backends = available_backends()
    row("whisper backend", backends[0] != "proportional", ", ".join(backends) + ("" if backends[0] != "proportional" else "  → pip install mlx-whisper (Apple) or faster-whisper for word-level captions"), required=False)
    row("PEXELS_API_KEY", bool(settings.pexels_api_key), "set" if settings.pexels_api_key else "not set (optional; stock → Pixabay or AI fallback)", required=False)
    row("PIXABAY_API_KEY", bool(settings.pixabay_api_key), "set" if settings.pixabay_api_key else "not set (optional)", required=False)
    row("storage", True, str(STORAGE))
    raise typer.Exit(code=0 if ok else 1)


if __name__ == "__main__":
    app()
