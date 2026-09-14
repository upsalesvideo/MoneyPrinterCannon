"""Script generation via Genosai chat (OpenAI-compatible). Output is a validated `Script`.

Two modes:
  * topic  -> the LLM writes narration + visuals (hook, one idea per scene, CTA).
  * script -> narration is NEVER rewritten: we split the user's text into scenes ourselves
              (by sentences, balanced) and ask the LLM only for visuals / title / music / social.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Optional

from loguru import logger
from pydantic import ValidationError

from .estimate import SCENE_SECONDS, words_per_sec
from .genosai import GenosaiClient
from .schema import Scene, Script, SocialMeta, VideoParams

NO_TEXT = "no text, no logos, no watermarks"
MIN_SCENES, MAX_SCENES = 2, 30

SYSTEM_WRITER = """You are a senior scriptwriter for short vertical videos (TikTok / Reels / Shorts).
You write tight, spoken-word narration that a TTS voice will read aloud, and you design one
visual per scene for an image/video generator and for stock-footage search.

Hard rules for narration:
- Scene 1 is the HOOK: a bold claim, question or surprising fact within the first 3 seconds. Never start with a greeting ("welcome", "hi", "today we will").
- One idea per scene. Concrete facts, numbers, examples — no filler, no fluff.
- Write every number in words (TTS reads digits unpredictably): "twenty five percent", "двадцать пять процентов".
- Each scene narration = 1-3 short sentences, 5-25 words. Plain spoken language, no bullet points, no emojis, no markdown.
- The LAST scene is a short call to action (follow / save / comment / link), one or two sentences.
- Keep the total word count close to the target — it controls video length.

Hard rules for visuals (per scene):
- visual_prompt: English, concrete and cinematic — subject, setting, lighting, lens/camera, mood. One clear composition. Must NOT contain any text, letters, captions, signs, logos or watermarks. Always end with the style guide you are given and the phrase "no text, no logos, no watermarks".
- search_terms: exactly 3 English stock-footage queries of 1-3 words each; the first is the main subject.
- motion: a short camera hint for an AI video generator (e.g. "slow push-in", "handheld pan left", "static, subtle parallax").

Also produce:
- title: a punchy title, at most 8 words, in the narration language.
- music_prompt: English, instrumental only, describes mood/genre/tempo, at most 200 characters, no vocals.
- social: {title, caption (1-3 sentences with a hook, narration language), hashtags (5-10, without '#', lowercase)}.

Return ONLY a JSON object of this exact shape:
{"language": "ru", "title": "...", "scenes": [{"index": 1, "narration": "...", "visual_prompt": "...", "search_terms": ["...","...","..."], "motion": "..."}],
 "music_prompt": "...", "social": {"title": "...", "caption": "...", "hashtags": ["..."]}}"""

SYSTEM_VISUALS = """You are an art director for short vertical videos. You receive a narration that is ALREADY split
into scenes. Do NOT change, shorten, translate or rewrite the narration — it is fixed. For every scene design
one visual for an image/video generator and stock-footage search, plus a title, music prompt and social post.

Rules for visuals (per scene):
- visual_prompt: English, concrete and cinematic — subject, setting, lighting, lens/camera, mood. One clear composition. Must NOT contain any text, letters, captions, signs, logos or watermarks. Always end with the style guide you are given and the phrase "no text, no logos, no watermarks".
- search_terms: exactly 3 English stock-footage queries of 1-3 words each; the first is the main subject.
- motion: a short camera hint for an AI video generator.
Also:
- title: punchy, at most 8 words, in the narration language.
- music_prompt: English, instrumental only, mood/genre/tempo, at most 200 characters.
- social: {title, caption (1-3 sentences, narration language), hashtags (5-10, without '#', lowercase)}.

Return ONLY a JSON object:
{"language": "ru", "title": "...", "scenes": [{"index": 1, "visual_prompt": "...", "search_terms": ["...","...","..."], "motion": "..."}],
 "music_prompt": "...", "social": {"title": "...", "caption": "...", "hashtags": ["..."]}}
The scenes array must have exactly one entry per input scene, same indexes, same order."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def parse_json_object(text: str) -> dict:
    """Robust: strips ``` fences, takes the first '{' … last '}', json.loads. Raises ValueError."""
    if not text or not text.strip():
        raise ValueError("empty LLM response")
    t = text.strip()
    t = re.sub(r"^```(?:json|JSON)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b == -1 or b <= a:
        raise ValueError("no JSON object found in response")
    chunk = t[a : b + 1]
    try:
        obj = json.loads(chunk)
    except json.JSONDecodeError:
        # common LLM slips: trailing commas, smart quotes
        fixed = re.sub(r",\s*([}\]])", r"\1", chunk).replace("“", '"').replace("”", '"')
        obj = json.loads(fixed)
    if not isinstance(obj, dict):
        raise ValueError("JSON root is not an object")
    return obj


def detect_language(text: str) -> str:
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    lat = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return "ru" if cyr > lat else "en"


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[^\s])")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
    return parts or ([text] if text else [])


def split_into_scenes(text: str, n_scenes: int) -> list[str]:
    """Split a ready script into ≈n balanced scenes without changing words (sentence borders
    only; a sentence longer than a scene budget is split at commas/dashes, last resort by words)."""
    sents = split_sentences(text)
    total_words = sum(len(s.split()) for s in sents)
    n_scenes = max(1, min(n_scenes, total_words))
    budget = total_words / n_scenes
    # explode over-long sentences at soft punctuation so balancing has something to work with
    units: list[str] = []
    for s in sents:
        if len(s.split()) <= budget * 1.6:
            units.append(s)
            continue
        soft = [p.strip() for p in re.split(r"(?<=[,;:—–-])\s+", s) if p.strip()]
        if len(soft) == 1:
            w = s.split()
            per = max(3, math.ceil(len(w) / math.ceil(len(w) / max(3, budget))))
            soft = [" ".join(w[i : i + per]) for i in range(0, len(w), per)]
        units.extend(soft)
    scenes: list[list[str]] = [[]]
    acc = 0
    remaining = n_scenes
    for i, u in enumerate(units):
        wl = len(u.split())
        units_left = len(units) - i
        if scenes[-1] and remaining > 1 and (acc + wl > budget * 1.15 or units_left <= remaining - 1):
            scenes.append([])
            remaining -= 1
            acc = 0
        scenes[-1].append(u)
        acc += wl
    return [" ".join(s) for s in scenes if s]


def _norm_words(text: str) -> list[str]:
    return [re.sub(r"[^\w]", "", w.lower()) for w in text.split() if re.sub(r"[^\w]", "", w)]


def _finish_prompt(p: str, style: str) -> str:
    p = (p or "").strip().rstrip(",. ")
    low = p.lower()
    if style and style.lower() not in low:
        p = f"{p}, {style}"
    if "no text" not in p.lower():
        p = f"{p}, {NO_TEXT}"
    return p


def _target_scene_count(params: VideoParams, seconds: float) -> int:
    if params.scene_count:
        return max(MIN_SCENES, min(MAX_SCENES, params.scene_count))
    return max(MIN_SCENES, min(MAX_SCENES, round(seconds / SCENE_SECONDS)))


def _clean_terms(terms: Any) -> list[str]:
    out: list[str] = []
    if isinstance(terms, str):
        terms = [t for t in re.split(r"[,;/]", terms)]
    for t in terms or []:
        t = re.sub(r"\s+", " ", str(t)).strip().strip("#").lower()
        if t and t not in out:
            out.append(" ".join(t.split()[:3]))
    return out[:3]


def _clean_social(obj: Any) -> SocialMeta:
    if not isinstance(obj, dict):
        return SocialMeta()
    tags = obj.get("hashtags") or []
    if isinstance(tags, str):
        tags = re.split(r"[\s,]+", tags)
    tags = [re.sub(r"^#+", "", str(t)).strip() for t in tags]
    return SocialMeta(title=str(obj.get("title") or "").strip(), caption=str(obj.get("caption") or "").strip(),
                      hashtags=[t for t in tags if t][:12])


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------
def _ask_json(client: GenosaiClient, model: str, system: str, user: str, temperature: float) -> tuple[dict, float]:
    """chat -> parsed dict. One repair round-trip if the JSON is broken."""
    cost = 0.0
    res = client.chat(model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                      temperature=temperature, max_tokens=6000, json_mode=True)
    cost += res.cost
    try:
        return parse_json_object(res.content), cost
    except ValueError as e:
        logger.warning("LLM JSON parse failed ({}), asking the model to repair it", e)
    fix = client.chat(
        model,
        [
            {"role": "system", "content": "You fix broken JSON. Return only the corrected JSON object, nothing else."},
            {"role": "user", "content": f"This was supposed to be one valid JSON object but is broken. Fix it, keep the content:\n\n{res.content[:12000]}"},
        ],
        temperature=0.0, max_tokens=6000, json_mode=True,
    )
    cost += fix.cost
    return parse_json_object(fix.content), cost


def generate_script(params: VideoParams, client: GenosaiClient) -> tuple[Script, float]:
    params.require_content()
    if params.script:
        return _script_from_text(params, client)
    return _script_from_topic(params, client)


def _script_from_topic(params: VideoParams, client: GenosaiClient) -> tuple[Script, float]:
    lang = params.language if params.language != "auto" else detect_language(params.topic)
    wps = words_per_sec(lang)
    target_words = int(round(params.target_seconds * wps))
    n_scenes = _target_scene_count(params, params.target_seconds)
    user = (
        f"Topic: {params.topic}\n"
        f"Narration language: {lang} (ISO code; write the narration in this language).\n"
        f"Target length: {params.target_seconds} seconds ≈ {target_words} words total (±10%).\n"
        f"Number of scenes: exactly {n_scenes} (scene 1 = hook, scene {n_scenes} = call to action).\n"
        f"Aspect: {params.aspect}.\n"
        f"Visual style guide to append to every visual_prompt: \"{params.visual_style}\".\n"
        + (f"Creative direction: {params.style_hint}\n" if params.style_hint else "")
        + "Return the JSON now."
    )
    logger.info("LLM script: topic={!r} lang={} target={}s/{}w scenes={}", params.topic[:60], lang, params.target_seconds, target_words, n_scenes)
    obj, cost = _ask_json(client, params.text_model, SYSTEM_WRITER, user, temperature=0.8)
    script = _build_script(obj, params, lang, narrations=None)
    logger.info("script ok: {} scenes, {} words, cost {}", len(script.scenes), len(script.full_text.split()), cost)
    return script, cost


def _script_from_text(params: VideoParams, client: GenosaiClient) -> tuple[Script, float]:
    text = re.sub(r"\s+", " ", params.script.strip())
    lang = params.language if params.language != "auto" else detect_language(text)
    seconds = len(text.split()) / words_per_sec(lang)
    n_scenes = _target_scene_count(params, seconds)
    narrations = split_into_scenes(text, n_scenes)
    listing = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(narrations))
    user = (
        f"Topic / context: {params.topic or '(see narration)'}\n"
        f"Narration language: {lang}.\nAspect: {params.aspect}.\n"
        f"Visual style guide to append to every visual_prompt: \"{params.visual_style}\".\n"
        + (f"Creative direction: {params.style_hint}\n" if params.style_hint else "")
        + f"Scenes ({len(narrations)}), narration is fixed:\n{listing}\n\nReturn the JSON now."
    )
    logger.info("LLM visuals for a ready script: {} scenes, lang={}", len(narrations), lang)
    obj, cost = _ask_json(client, params.text_model, SYSTEM_VISUALS, user, temperature=0.7)
    script = _build_script(obj, params, lang, narrations=narrations)
    return script, cost


def _build_script(obj: dict, params: VideoParams, lang: str, narrations: Optional[list[str]]) -> Script:
    raw_scenes = obj.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("LLM returned no scenes")
    style = params.visual_style.strip()
    scenes: list[Scene] = []
    if narrations is not None:
        # visuals mode: pair by position; if the LLM miscounted, pad/trim with generic prompts
        for i, narr in enumerate(narrations):
            src = raw_scenes[i] if i < len(raw_scenes) and isinstance(raw_scenes[i], dict) else {}
            vp = str(src.get("visual_prompt") or "").strip() or f"Cinematic illustrative shot for: {narr[:120]}"
            scenes.append(Scene(index=i + 1, narration=narr, visual_prompt=_finish_prompt(vp, style),
                                search_terms=_clean_terms(src.get("search_terms")) or _fallback_terms(narr),
                                motion=str(src.get("motion") or "").strip()))
        if len(raw_scenes) != len(narrations):
            logger.warning("LLM returned {} visual scenes for {} narration scenes; paired by position", len(raw_scenes), len(narrations))
    else:
        for i, src in enumerate(raw_scenes):
            if not isinstance(src, dict):
                continue
            narr = re.sub(r"\s+", " ", str(src.get("narration") or "")).strip()
            if not narr:
                continue
            vp = str(src.get("visual_prompt") or "").strip() or f"Cinematic illustrative shot for: {narr[:120]}"
            scenes.append(Scene(index=len(scenes) + 1, narration=narr, visual_prompt=_finish_prompt(vp, style),
                                search_terms=_clean_terms(src.get("search_terms")) or _fallback_terms(narr),
                                motion=str(src.get("motion") or "").strip()))
        if len(scenes) < MIN_SCENES:
            raise ValueError(f"LLM returned only {len(scenes)} usable scenes")
    lang_out = str(obj.get("language") or lang).strip().lower()[:5] or lang
    if params.language != "auto":
        lang_out = params.language
    music = re.sub(r"\s+", " ", str(obj.get("music_prompt") or "")).strip()[:200]
    title = re.sub(r"\s+", " ", str(obj.get("title") or "")).strip()
    if len(title.split()) > 8:
        title = " ".join(title.split()[:8])
    try:
        return Script.model_validate({
            "topic": params.topic or scenes[0].narration[:80],
            "language": lang_out,
            "title": title,
            "scenes": [s.model_dump() for s in scenes],
            "music_prompt": music,
            "social": _clean_social(obj.get("social")).model_dump(),
        })
    except ValidationError as e:
        raise ValueError(f"script validation failed: {e}") from e


def _fallback_terms(narration: str) -> list[str]:
    words = [w for w in _norm_words(narration) if len(w) > 4][:3]
    return words or ["abstract background"]


if __name__ == "__main__":
    import sys

    p = VideoParams(topic=" ".join(sys.argv[1:]) or "Why cats sleep sixteen hours a day", target_seconds=30)
    s, c = generate_script(p, GenosaiClient())
    print(s.model_dump_json(indent=1, ensure_ascii=False) if hasattr(s, "model_dump_json") else s)
    print("cost", c)
