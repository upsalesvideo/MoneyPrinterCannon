import pytest
from pydantic import ValidationError

from moneyprintercannon.schema import STAGES, Script, TaskState, VideoParams, VisualAsset, new_task_id


def test_defaults_produce_vertical_short():
    p = VideoParams(topic="Why cats sleep")
    assert p.aspect == "9:16" and p.size == (1080, 1920)
    assert p.visual_source == "ai_image" and p.image_model == "z-image"
    assert p.music == "genosai" and p.captions and p.caption_preset == "karaoke"
    assert p.voice == "Charon" and p.text_model == "gemini-3-flash"
    assert p.target_seconds == 45 and p.variants == 1


def test_require_content():
    with pytest.raises(ValueError):
        VideoParams().require_content()
    VideoParams(topic="  x ").require_content()
    VideoParams(script="text only").require_content()


def test_strip_and_validation():
    p = VideoParams(topic="  hello  ", script="  ")
    assert p.topic == "hello" and p.script == ""
    with pytest.raises(ValidationError):
        VideoParams(topic="x", aspect="4:3")
    with pytest.raises(ValidationError):
        VideoParams(topic="x", target_seconds=5)
    with pytest.raises(ValidationError):
        VideoParams(topic="x", accent_color="green")


def test_task_state_has_all_stages():
    st = TaskState(task_id=new_task_id())
    assert set(st.stages) == set(STAGES)
    assert st.status == "queued" and st.progress == 0
    js = st.model_dump_json()
    assert TaskState.model_validate_json(js).task_id == st.task_id


def test_script_full_text_and_visual_asset_ken_burns():
    s = Script(topic="t", language="en", scenes=[
        {"index": 1, "narration": "One.", "visual_prompt": "a"},
        {"index": 2, "narration": "Two.", "visual_prompt": "b"},
    ])
    assert s.full_text == "One. Two."
    a = VisualAsset(index=1, kind="image", path="visuals/scene-01.jpg", source="genosai:z-image")
    assert a.ken_burns == ""  # optional, chosen by visuals.py
