import math
from cashcannon.estimate import estimate, words_per_sec, image_price, tts_cost_for_text, video_price
from cashcannon.schema import Script, VideoParams


def test_tts_cost_rules():
    assert tts_cost_for_text("") == 0
    assert tts_cost_for_text("short") == 5  # min per request
    assert tts_cost_for_text("x" * 500) == 7.5  # 5 × 1.5
    assert tts_cost_for_text("x" * 1000) == 13.5 + 5  # 900 chars → 13.5, remaining 100 → min 5


def test_prices():
    assert image_price("z-image") == 1 and image_price("nano-banana-pro") == 18 and image_price("unknown") == 6
    assert video_price("grok-imagine-1.5", 5) == 20
    assert video_price("veo-3.1-lite", 5) == 30
    assert video_price("kling-3.0", 4.2) == 100


def test_estimate_without_script_defaults():
    est = estimate(VideoParams(topic="Why cats sleep sixteen hours", target_seconds=45))
    assert est.breakdown["music"] == 16
    assert est.breakdown["visuals"] == 8  # ≈ 8 scenes × z-image 1
    assert est.breakdown["tts"] >= 8 * 5
    assert est.total == round(sum(est.breakdown.values()), 2)
    assert any("state.cost_credits" in n for n in est.notes)


def test_estimate_with_script_and_video():
    s = Script(topic="t", language="en", scenes=[
        {"index": i, "narration": "word " * 12, "visual_prompt": "p"} for i in range(1, 4)
    ])
    est = estimate(VideoParams(topic="t", visual_source="ai_video", video_model="grok-imagine-1.5", music="none"), s)
    assert est.breakdown["music"] == 0
    assert est.breakdown["tts"] == 15  # 3 scenes × min 5
    secs = math.ceil(12 / words_per_sec("en"))  # scene length from the calibrated speech rate
    assert est.breakdown["visuals"] == 3 * secs * 4  # 3 clips × ceil(scene s) × 4 cr/s


def test_estimate_stock_and_custom_voice():
    est = estimate(VideoParams(topic="t", visual_source="stock", custom_voice_file="/x.wav", music="file"))
    assert est.breakdown["visuals"] == 0 and est.breakdown["tts"] == 0 and est.breakdown["music"] == 0
