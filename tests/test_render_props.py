import json
import subprocess
from pathlib import Path

import pytest

from cashcannon.render import build_props, make_cover, mix_audio, verify_output
from cashcannon.schema import SceneTiming, Script, Timing, VideoParams, VisualAsset, Word
from tests.conftest import ffmpeg_available

REQUIRED_TOP = {"aspect", "width", "height", "fps", "durationSec", "scenes", "words", "captions", "title", "outro",
                "transition", "progressBar", "watermark", "theme"}
REQUIRED_SCENE = {"index", "start", "end", "kind", "src", "fit", "kenBurns", "loop", "srcDurationSec"}


def _fixture():
    script = Script(topic="Cats", language="en", title="Why cats sleep", scenes=[
        {"index": 1, "narration": "Cats sleep a lot.", "visual_prompt": "a"},
        {"index": 2, "narration": "Really a lot.", "visual_prompt": "b"},
        {"index": 3, "narration": "Follow.", "visual_prompt": "c"},
    ])
    timing = Timing(backend="proportional", voice_duration=9.5, words=[Word(s=0.3, e=0.6, w="Cats"), Word(s=0.7, e=1.0, w="sleep")],
                    scenes=[SceneTiming(index=1, start=0, end=3.0), SceneTiming(index=2, start=3.25, end=6.0), SceneTiming(index=3, start=6.25, end=9.5)])
    visuals = [
        VisualAsset(index=1, kind="image", path="visuals/scene-01.jpg", source="genosai:z-image", ken_burns="in"),
        VisualAsset(index=2, kind="video", path="visuals/scene-02.mp4", source="pexels", duration=2.0, width=1080, height=1920),
        VisualAsset(index=3, kind="video", path="visuals/scene-03.mp4", source="pexels", duration=12.0, width=1080, height=1920),
    ]
    return script, timing, visuals


def test_build_props_contract(tmp_path):
    script, timing, visuals = _fixture()
    task_dir = tmp_path / "20260101-000000-abc123"
    task_dir.mkdir()
    params = VideoParams(topic="Cats", title_card=True, outro_text="Follow for more", outro_sub="@me", watermark="made with CashCannon")
    props = build_props(script, timing, visuals, params, task_dir)
    assert REQUIRED_TOP <= set(props)
    assert (props["width"], props["height"], props["fps"]) == (1080, 1920, 30)
    assert props["durationSec"] == pytest.approx(9.5 + 2.5)
    assert len(props["scenes"]) == 3
    for s in props["scenes"]:
        assert REQUIRED_SCENE <= set(s)
        assert s["src"].startswith("tasks/20260101-000000-abc123/visuals/scene-0")
    s1, s2, s3 = props["scenes"]
    assert s1["start"] == 0 and s1["end"] == 3.25 and s1["kind"] == "image" and s1["kenBurns"] == "in" and s1["loop"] is False
    assert s2["kind"] == "video" and s2["loop"] is True and s2["srcDurationSec"] == 2.0  # 2 s clip in a 3 s scene
    assert s3["end"] == 9.5 and s3["loop"] is False  # gaps are covered up to the voice end
    assert props["words"] == [{"s": 0.3, "e": 0.6, "w": "Cats"}, {"s": 0.7, "e": 1.0, "w": "sleep"}]
    assert props["captions"]["preset"] == "karaoke" and props["captions"]["fontSize"] == 78 and props["captions"]["maxWords"] == 3
    assert props["title"] == {"text": "Why cats sleep", "durSec": 0}
    assert props["outro"] == {"text": "Follow for more", "sub": "@me", "durSec": 2.5}
    assert props["watermark"] == {"text": "made with CashCannon"}
    assert props["theme"]["font"] == "Onest" and props["transition"] == "fade"
    json.dumps(props)  # serialisable


def test_build_props_minimal_defaults(tmp_path):
    script, timing, visuals = _fixture()
    params = VideoParams(topic="Cats", aspect="16:9", captions=False, ken_burns="none")
    props = build_props(script, timing, visuals, params, tmp_path / "t1")
    assert props["title"] is None and props["outro"] is None and props["watermark"] is None
    assert props["durationSec"] == 9.5 and props["captions"]["enabled"] is False
    assert props["captions"]["fontSize"] == 56
    assert props["scenes"][1]["kenBurns"] == "none"


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")
def test_mix_and_cover_with_synthetic_media(tmp_path, tone_wav):
    task_dir = tmp_path / "task"
    (task_dir / "render").mkdir(parents=True)
    silent = task_dir / "render" / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=4",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(silent)], check=True)
    noise = task_dir / "bgm.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=d=1.5:c=pink:r=48000:a=0.3",
                    "-c:a", "libmp3lame", "-q:a", "5", str(noise)], check=True)
    final = mix_audio(task_dir, silent, tone_wav, noise, music_volume=0.2, total_sec=4.0)
    dur = verify_output(final, 4.0)
    assert 3.5 <= dur <= 4.3
    cover = make_cover(final, task_dir / "cover.jpg", at_sec=1.0)
    assert cover.is_file() and cover.stat().st_size > 1000
    # no-music path
    final2 = mix_audio(task_dir, silent, tone_wav, None, music_volume=0.2, total_sec=4.0, out_name="final-nomusic.mp4")
    assert verify_output(final2, 4.0) > 3.5
