import json

import pytest

from moneyprintercannon.llm import _build_script, detect_language, parse_json_object, split_into_scenes, split_sentences
from moneyprintercannon.schema import VideoParams

GOOD = {"language": "en", "title": "Cats sleep a lot", "scenes": [
    {"index": 1, "narration": "Cats sleep sixteen hours a day.", "visual_prompt": "A cat asleep on a windowsill", "search_terms": ["sleeping cat", "cat", "window"], "motion": "slow push-in"},
    {"index": 2, "narration": "Follow for more.", "visual_prompt": "Cat looking at camera", "search_terms": ["cat face"], "motion": "static"},
], "music_prompt": "lofi calm", "social": {"title": "Cats", "caption": "Why?", "hashtags": ["#cats", "sleep"]}}


def test_parse_plain():
    assert parse_json_object(json.dumps(GOOD))["title"] == "Cats sleep a lot"


def test_parse_with_fences_and_prose():
    text = "Sure! Here is the JSON:\n```json\n" + json.dumps(GOOD, indent=2) + "\n```\nHope this helps."
    assert parse_json_object(text)["scenes"][1]["index"] == 2


def test_parse_trailing_commas_and_garbage():
    text = 'noise {"a": 1, "b": [1,2,],} trailing'
    assert parse_json_object(text) == {"a": 1, "b": [1, 2]}


def test_parse_failures():
    with pytest.raises(ValueError):
        parse_json_object("no json here")
    with pytest.raises(ValueError):
        parse_json_object("")
    with pytest.raises(ValueError):
        parse_json_object("[1,2,3]")


def test_build_script_normalizes():
    p = VideoParams(topic="cats", visual_style="cinematic, soft light")
    s = _build_script(GOOD, p, "en", narrations=None)
    assert [sc.index for sc in s.scenes] == [1, 2]
    for sc in s.scenes:
        assert sc.visual_prompt.endswith("no text, no logos, no watermarks")
        assert "cinematic, soft light" in sc.visual_prompt
        assert 1 <= len(sc.search_terms) <= 3
    assert s.social.hashtags == ["cats", "sleep"]
    assert s.language == "en" and s.music_prompt == "lofi calm"


def test_build_script_ready_text_keeps_words():
    p = VideoParams(script="Первое предложение. Второе предложение здесь. Третье.", language="ru")
    narr = split_into_scenes(p.script, 2)
    obj = {"scenes": [{"index": 1, "visual_prompt": "x"}, {"index": 2, "visual_prompt": "y"}]}
    s = _build_script(obj, p, "ru", narrations=narr)
    assert " ".join(sc.narration for sc in s.scenes).split() == p.script.split()
    assert len(s.scenes) == 2


def test_split_helpers():
    assert split_sentences("A b. C d! E f?") == ["A b.", "C d!", "E f?"]
    text = " ".join(f"Sentence number {i} is here." for i in range(1, 13))
    parts = split_into_scenes(text, 4)
    assert len(parts) == 4
    assert " ".join(parts).split() == text.split()
    assert detect_language("Почему коты спят") == "ru" and detect_language("Why cats sleep") == "en"
