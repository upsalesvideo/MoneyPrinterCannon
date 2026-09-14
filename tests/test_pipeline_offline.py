"""Orchestrator behaviour with every stage stubbed: state.json, skip-on-resume, stop_at,
failure bookkeeping, cancel, result. No network, no ffmpeg, no Remotion."""
import json
from pathlib import Path

import pytest

from moneyprintercannon import pipeline
from moneyprintercannon.schema import STAGES, Script, TaskResult, VideoParams


@pytest.fixture
def stubbed(monkeypatch):
    calls: list[str] = []
    costs = {"script": 1.0, "voice": 10.0, "timing": 0.0, "visuals": 3.0, "music": 16.0, "render": 0.0}

    def fake_run_stage(stage, task_id, params, state, ctx, get_client, log):
        calls.append(stage)
        d = pipeline.task_dir(task_id)
        if stage == "script":
            s = Script(topic=params.topic, language="en", title="T", scenes=[{"index": 1, "narration": "a b", "visual_prompt": "p"}])
            (d / "script.json").write_text(s.model_dump_json())
            ctx["script"] = s
        elif stage == "voice":
            (d / "voice").mkdir(exist_ok=True)
            (d / "voice" / "voice.wav").write_bytes(b"x")
            (d / "voice" / "voice.json").write_text("[]")
        elif stage == "timing":
            (d / "timing.json").write_text(json.dumps({"backend": "proportional", "words": [], "scenes": [], "voice_duration": 1.0}))
        elif stage == "visuals":
            (d / "visuals").mkdir(exist_ok=True)
            (d / "visuals" / "visuals.json").write_text("[]")
        elif stage == "render":
            (d / "final.mp4").write_bytes(b"x")
            ctx["result"] = TaskResult(video="final.mp4", duration=1.0, title="T")
        return costs[stage]

    monkeypatch.setattr(pipeline, "_run_stage", fake_run_stage)
    monkeypatch.setattr(pipeline, "_load_ctx", lambda stage, task_id, ctx: None)
    return calls


def test_create_and_full_run(stubbed):
    tid = pipeline.create_task(VideoParams(topic="Cats"))
    d = pipeline.task_dir(tid)
    assert (d / "params.json").is_file() and (d / "state.json").is_file()
    st0 = pipeline.load_state(tid)
    assert st0.status == "queued" and st0.estimate_credits > 0
    st = pipeline.run(tid)
    assert st.status == "succeeded" and st.progress == 100
    assert stubbed == list(STAGES)
    assert st.cost_credits == 30.0
    assert all(st.stages[s].status == "succeeded" for s in STAGES)
    assert st.result and st.result.video == "final.mp4"
    assert (d / "log.txt").is_file() and "[render] done" in (d / "log.txt").read_text()
    on_disk = json.loads((d / "state.json").read_text())
    assert on_disk["status"] == "succeeded" and on_disk["cost_credits"] == 30.0
    assert not (d / "state.json.tmp").exists()


def test_stop_at_and_resume_skips_done_stages(stubbed):
    tid = pipeline.create_task(VideoParams(topic="Cats"))
    st = pipeline.run(tid, stop_at="timing")
    assert st.status == "queued" and st.stage == "timing" and st.progress == 40
    assert stubbed == ["script", "voice", "timing"]
    assert st.stages["visuals"].status == "pending"
    stubbed.clear()
    st = pipeline.run(tid)
    assert st.status == "succeeded"
    assert stubbed == ["visuals", "music", "render"]  # earlier stages skipped, not re-paid
    stubbed.clear()
    st = pipeline.run(tid, force=True)
    assert stubbed == list(STAGES)


def test_failure_is_recorded_and_reraised(stubbed, monkeypatch):
    real = pipeline._run_stage

    def boom(stage, *a, **k):
        if stage == "visuals":
            raise RuntimeError("no visual for scenes [1]")
        return real(stage, *a, **k)

    monkeypatch.setattr(pipeline, "_run_stage", boom)
    tid = pipeline.create_task(VideoParams(topic="Cats"))
    with pytest.raises(RuntimeError):
        pipeline.run(tid)
    st = pipeline.load_state(tid)
    assert st.status == "failed" and st.failed_stage == "visuals"
    assert st.stages["visuals"].status == "failed" and "no visual" in st.stages["visuals"].error
    assert st.stages["voice"].status == "succeeded"
    assert st.cost_credits == 11.0  # script + voice were charged
    # resume continues from the failed stage
    monkeypatch.setattr(pipeline, "_run_stage", real)
    st = pipeline.run(tid)
    assert st.status == "succeeded" and st.failed_stage is None and st.error is None


def test_cancel_flag_between_stages(stubbed, monkeypatch):
    tid = pipeline.create_task(VideoParams(topic="Cats"))
    real = pipeline._run_stage

    def cancel_after_voice(stage, task_id, *a, **k):
        r = real(stage, task_id, *a, **k)
        if stage == "voice":
            pipeline.cancel_task(task_id)
        return r

    monkeypatch.setattr(pipeline, "_run_stage", cancel_after_voice)
    with pytest.raises(pipeline.TaskCancelled):
        pipeline.run(tid)
    st = pipeline.load_state(tid)
    assert st.status == "cancelled" and stubbed == ["script", "voice"]


def test_list_delete_and_path_safety(stubbed):
    a = pipeline.create_task(VideoParams(topic="A"))
    b = pipeline.create_task(VideoParams(topic="B"))
    ids = [t.task_id for t in pipeline.list_tasks()]
    assert set(ids) == {a, b}
    pipeline.delete_task(a)
    assert [t.task_id for t in pipeline.list_tasks()] == [b]
    with pytest.raises(pipeline.TaskNotFound):
        pipeline.task_dir("../etc")
    with pytest.raises(pipeline.TaskNotFound):
        pipeline.load_state("nope")
