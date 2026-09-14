"""Client logic without the network: error mapping, retry-after parsing, input filtering,
duration choices, run_many pool sizing."""
import json

import pytest

from moneyprintercannon import genosai
from moneyprintercannon.genosai import (GenosaiClient, GenosaiError, GenosaiValidationError, InsufficientCredits, RateLimited,
                                _parse_retry_after, duration_choices, reference_spec)


class R:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text or (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


CATALOG = {
    "photo": [{"id": "z-image", "input_options": {"aspect_ratio": {"options": ["1:1", "9:16"]}}, "cost_credits_default": 1}],
    "video": [{"id": "grok-imagine-1.5", "input_options": {"aspect_ratio": {"options": ["auto", "9:16"]}, "resolution": {"options": ["480p", "720p"]},
                                                           "duration": {"options": [str(i) for i in range(1, 16)]}},
               "references": {"field": "image_urls", "max_files": 1, "required": True}},
              {"id": "kling-3.0", "input_options": {"duration": {"min": 3, "max": 15}, "generate_audio": {"default": True}}}],
    "tts": [{"id": "gemini-3.1-flash-tts", "input_options": {"voice": {"options": ["Kore", "Charon"]}, "style": {"options": ["Promo/Hype"]},
                                                              "pace": {"options": ["Natural"]}, "accent": {"options": ["Neutral"]}, "temperature": {"min": 0, "max": 2}}}],
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GENOSAI_API_KEY", "sdk_test_not_real")
    genosai._models_cache.clear()
    genosai._models_cache.update(CATALOG)
    yield GenosaiClient()
    genosai._models_cache.clear()


def test_error_mapping():
    with pytest.raises(InsufficientCredits):
        GenosaiClient._handle(R(402, {"error": "INSUFFICIENT_CREDITS", "message": "top up"}), "x")
    with pytest.raises(GenosaiValidationError) as ei:
        GenosaiClient._handle(R(400, {"error": "VALIDATION_ERROR", "message": "TTS generation failed"}), "x")
    assert "TTS generation failed" in str(ei.value)
    with pytest.raises(RateLimited) as ei2:
        GenosaiClient._handle(R(429, {"error": "RATE_LIMITED", "message": "Too many requests. Try again in 7 seconds"}), "x")
    assert ei2.value.retry_after == 8.0
    with pytest.raises(GenosaiError) as ei3:
        GenosaiClient._handle(R(404, {"error": "MODEL_NOT_FOUND", "message": "Model x is not available via Public API"}), "x")
    assert ei3.value.status == 404 and "MODEL_NOT_FOUND" in str(ei3.value)
    # OpenAI-style envelope from chat/completions
    with pytest.raises(GenosaiError) as ei4:
        GenosaiClient._handle(R(401, {"error": {"message": "bad key", "type": "auth"}}), "x")
    assert "bad key" in str(ei4.value)
    assert GenosaiClient._handle(R(200, {"data": {"ok": 1}}), "x") == {"data": {"ok": 1}}


def test_parse_retry_after():
    assert _parse_retry_after("Try again in 12 second(s)") == 13.0
    assert _parse_retry_after("nothing here", default=4) == 4


def test_filter_input_keeps_required_and_drops_unknown(client):
    tts = client.filter_input("gemini-3.1-flash-tts", {"text": "hi", "voice": "Charon", "style": "Promo/Hype", "pace": "Natural",
                                                       "accent": "Neutral", "temperature": 1.0, "bogus": 1})
    assert tts == {"text": "hi", "voice": "Charon", "style": "Promo/Hype", "pace": "Natural", "accent": "Neutral", "temperature": 1.0}
    img = client.filter_input("z-image", {"prompt": "p", "aspect_ratio": "9:16", "resolution": "1K"})
    assert img == {"prompt": "p", "aspect_ratio": "9:16"}
    bad = client.filter_input("z-image", {"prompt": "p", "aspect_ratio": "21:9"})
    assert "aspect_ratio" not in bad  # value outside the enum → server default
    vid = client.filter_input("grok-imagine-1.5", {"prompt": "p", "aspect_ratio": "9:16", "resolution": "720p", "duration": "6",
                                                   "generate_audio": False, "image_urls": ["https://x/y.jpg"]})
    assert vid == {"prompt": "p", "aspect_ratio": "9:16", "resolution": "720p", "duration": "6", "image_urls": ["https://x/y.jpg"]}
    assert client.filter_input("unknown-model", {"a": 1}) == {"a": 1}


def test_duration_and_reference_helpers(client):
    assert duration_choices(client.model_info("grok-imagine-1.5"))[0][:3] == [1, 2, 3]
    assert duration_choices(client.model_info("kling-3.0")) == ([], 3, 15)
    assert reference_spec(client.model_info("grok-imagine-1.5")) == ("image_urls", True, 1)
    assert reference_spec(client.model_info("z-image")) == ("", False, 0)
    from moneyprintercannon.visuals import pick_video_duration, video_needs_image

    assert pick_video_duration(client, "grok-imagine-1.5", 5.2) == 6
    assert pick_video_duration(client, "grok-imagine-1.5", 40) == 15
    assert pick_video_duration(client, "kling-3.0", 2.0) == 3
    assert pick_video_duration(client, "veo-3.1-lite", 5.0) == 6
    assert video_needs_image(client, "grok-imagine-1.5") and not video_needs_image(client, "kling-3.0")


def test_run_many_pool_and_errors(client, monkeypatch):
    seen = []

    def fake_run_task(model, inp, **kw):
        seen.append(model)
        if inp.get("fail"):
            raise GenosaiValidationError("nope")
        return genosai.TaskResult(urls=["u"], cost=1.0, task_id="t")

    monkeypatch.setattr(client, "run_task", fake_run_task)
    res = client.run_many([("z-image", {"prompt": "a"}), ("z-image", {"prompt": "b", "fail": 1})], raise_on_error=False)
    assert isinstance(res[0], genosai.TaskResult) and isinstance(res[1], GenosaiValidationError)
    with pytest.raises(GenosaiValidationError):
        client.run_many([("z-image", {"prompt": "b", "fail": 1})])
    assert client.run_many([]) == []


def test_chat_adds_json_rule_and_reads_cost(client, monkeypatch):
    captured = {}

    def fake_request(method, path, **kw):
        captured.update(kw["json"])
        return {"choices": [{"message": {"content": "{\"a\": 1}"}}], "usage": {"cost_credits": 0.1}}

    monkeypatch.setattr(client, "_request", fake_request)
    r = client.chat("gemini-3-flash", [{"role": "user", "content": "hi"}])
    assert r.content == "{\"a\": 1}" and r.cost == 0.1 and str(r) == r.content
    assert captured["messages"][0]["role"] == "system" and "JSON" in captured["messages"][0]["content"]
    assert captured["stream"] is False
