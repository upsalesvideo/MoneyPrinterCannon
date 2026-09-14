import json

import pytest

from cashcannon.schema import Script, Timing, VideoParams
from cashcannon.timing import align_words, build_timing, proportional_words, tokenize
from cashcannon.tts import SceneAudio


def test_proportional_words_inside_bounds_and_monotonic():
    words = proportional_words(tokenize("Один два три четыре пять."), 2.0, 6.0)
    assert len(words) == 5
    assert words[0].s >= 2.25 - 1e-6 and words[-1].e <= 6.0 - 0.25 + 1e-6
    for a, b in zip(words, words[1:]):
        assert b.s >= a.s and a.e > a.s
    # longer words get more time
    assert (words[3].e - words[3].s) > (words[0].e - words[0].s)


def test_align_words_uses_heard_times_and_interpolates():
    tokens = tokenize("Cats sleep sixteen hours a day, really.")
    heard = [{"w": "cats", "s": 0.10, "e": 0.40}, {"w": "sleep", "s": 0.45, "e": 0.80},
             {"w": "hours", "s": 1.30, "e": 1.60}, {"w": "a", "s": 1.62, "e": 1.70}, {"w": "day", "s": 1.72, "e": 2.00}]
    out = align_words(tokens, heard, 2.6)
    assert [w.w for w in out] == tokens
    assert out[0].s == 0.10 and out[1].s == 0.45 and out[3].s == 1.30
    assert 0.45 < out[2].s < 1.30  # "sixteen" interpolated
    assert out[-1].s > out[-2].s and out[-1].e <= 2.6
    for a, b in zip(out, out[1:]):
        assert b.s >= a.s


def test_align_falls_back_when_nothing_matches():
    tokens = tokenize("совсем другой текст тут")
    heard = [{"w": "hello", "s": 0.1, "e": 0.3}, {"w": "world", "s": 0.4, "e": 0.6}]
    out = align_words(tokens, heard, 2.0)
    assert len(out) == 4 and out[0].s >= 0.2


def test_build_timing_proportional_writes_json(tmp_path):
    script = Script(topic="t", language="ru", scenes=[
        {"index": 1, "narration": "Первая сцена про котов.", "visual_prompt": "a"},
        {"index": 2, "narration": "Вторая сцена.", "visual_prompt": "b"},
    ])
    audios = [SceneAudio(index=1, path="", duration=3.0, start=0.0, end=3.0), SceneAudio(index=2, path="", duration=2.0, start=3.25, end=5.25)]
    params = VideoParams(topic="t", timing_backend="proportional")
    t = build_timing(script, audios, params, tmp_path)
    assert isinstance(t, Timing) and t.backend == "proportional"
    assert len(t.words) == 6 and t.voice_duration == 5.25
    assert t.scenes[1].start == 3.25 and t.scenes[1].end == 5.25
    assert all(3.25 <= w.s <= 5.25 for w in t.words[4:])
    saved = json.loads((tmp_path / "timing.json").read_text())
    assert saved["backend"] == "proportional" and len(saved["words"]) == 6


def test_align_uses_whisper_span_for_digits_vs_words():
    # whisper returns "16" where the script says "шестнадцать": the heard span must be reused, not interpolated
    tokens = tokenize("Коты спят шестнадцать часов в сутки.")
    heard = [{"w": "Коты", "s": 0.0, "e": 0.68}, {"w": "спят", "s": 0.68, "e": 1.26}, {"w": "16", "s": 1.26, "e": 1.84},
             {"w": "часов", "s": 1.84, "e": 2.56}, {"w": "в", "s": 2.56, "e": 2.9}, {"w": "сутки.", "s": 2.9, "e": 3.36}]
    out = align_words(tokens, heard, 3.72)
    assert out[2].w == "шестнадцать" and out[2].s == 1.26 and out[2].e == pytest.approx(1.835, abs=0.01)
    # two script words against one heard token share its span proportionally
    tokens2 = tokenize("Цена двадцать пять рублей")
    heard2 = [{"w": "Цена", "s": 0.0, "e": 0.4}, {"w": "25", "s": 0.4, "e": 1.2}, {"w": "рублей", "s": 1.2, "e": 1.7}]
    out2 = align_words(tokens2, heard2, 2.0)
    assert out2[1].s == 0.4 and 0.4 < out2[2].s < 1.2 and out2[3].s == 1.2
