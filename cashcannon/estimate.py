"""Credit estimate before spending. The price table is indicative — the real price is
whatever the API reports in `data.cost` / `usage.cost_credits` (summed into
state.cost_credits after generation)."""
from __future__ import annotations

import math
from typing import Optional

from .schema import Estimate, Script, VideoParams

WORDS_PER_SEC = {"ru": 2.4, "en": 2.6}
CHARS_PER_WORD = 6.5
SCENE_SECONDS = 6.0

TTS_PER_100_CHARS = 1.5
TTS_MIN_PER_REQUEST = 5.0
TTS_MAX_CHARS_PER_REQUEST = 900
LLM_CREDITS = 1.5
MUSIC_CREDITS = 16.0

IMAGE_PRICES: dict[str, float] = {
    "z-image": 1,
    "chatgpt-image-2": 6,
    "chatgpt-image-2-5": 6,
    "nano-banana-pro": 18,
    "nano-banana-2": 8,
    "nano-banana-2-lite": 4,
    "nano-banana": 4,
    "grok-imagine": 4,
    "seedream-5-lite": 5.5,
}
IMAGE_PRICE_DEFAULT = 6.0

# per second @ default (720p) unless flat per clip
VIDEO_PRICES_PER_SEC: dict[str, float] = {
    "grok-imagine-1.5": 4,
    "kling-3.0": 20,
    "seedance-2.0-fast": 25,
    "seedance-2.0": 30,
    "seedance-2.5": 30,
    "wan-3.0": 16,
    "minimax-h3": 7,
    "gemini-omni-1.1-flash": 10,
}
VIDEO_PRICES_PER_CLIP: dict[str, float] = {
    "veo-3.1-lite": 30,
    "veo-3.1-fast": 60,
    "veo-3.1-quality": 200,
}
VIDEO_PRICE_PER_SEC_DEFAULT = 20.0


def words_per_sec(language: str) -> float:
    return WORDS_PER_SEC.get((language or "").lower()[:2], 2.5)


def tts_cost_for_text(text: str) -> float:
    """Per request: ceil(chars/100)×1.5, min 5. Long narration is split into ≤900-char requests."""
    n = len(text.strip())
    if n == 0:
        return 0.0
    total = 0.0
    while n > 0:
        chunk = min(n, TTS_MAX_CHARS_PER_REQUEST)
        total += max(TTS_MIN_PER_REQUEST, math.ceil(chunk / 100) * TTS_PER_100_CHARS)
        n -= chunk
    return total


def image_price(model: str) -> float:
    return IMAGE_PRICES.get(model, IMAGE_PRICE_DEFAULT)


def video_price(model: str, seconds: float) -> float:
    if model in VIDEO_PRICES_PER_CLIP:
        return VIDEO_PRICES_PER_CLIP[model]
    return VIDEO_PRICES_PER_SEC.get(model, VIDEO_PRICE_PER_SEC_DEFAULT) * max(1.0, math.ceil(seconds))


def estimate(params: VideoParams, script: Optional[Script] = None) -> Estimate:
    notes: list[str] = [
        "Prices are indicative; the real charge is visible in state.cost_credits after generation "
        "(taken from the API's data.cost / usage.cost_credits).",
    ]
    lang = params.language if params.language != "auto" else _guess_lang(params.topic or params.script)
    if script is not None:
        scene_texts = [s.narration for s in script.scenes]
        n_scenes = len(scene_texts)
        total_words = sum(len(t.split()) for t in scene_texts)
        seconds = total_words / words_per_sec(script.language or lang)
    elif params.script:
        words = params.script.split()
        seconds = len(words) / words_per_sec(lang)
        n_scenes = params.scene_count or max(2, round(seconds / SCENE_SECONDS))
        per = max(1, math.ceil(len(words) / n_scenes))
        scene_texts = [" ".join(words[i : i + per]) for i in range(0, len(words), per)]
        n_scenes = len(scene_texts)
    else:
        seconds = float(params.target_seconds)
        n_scenes = params.scene_count or max(3, round(seconds / SCENE_SECONDS))
        chars_per_scene = (seconds / n_scenes) * words_per_sec(lang) * CHARS_PER_WORD
        scene_texts = ["x" * int(chars_per_scene)] * n_scenes
        notes.append(f"No script yet: assumed {n_scenes} scenes of ≈{seconds / n_scenes:.0f}s.")

    br: dict[str, float] = {}
    br["llm"] = 0.0 if (params.script and not params.social_meta) else LLM_CREDITS
    br["tts"] = 0.0 if params.custom_voice_file else sum(tts_cost_for_text(t) for t in scene_texts)

    vis = 0.0
    src = params.visual_source
    if src == "ai_image":
        vis = n_scenes * image_price(params.image_model)
    elif src == "ai_video":
        per_scene = seconds / max(1, n_scenes)
        vis = n_scenes * video_price(params.video_model, per_scene)
        notes.append(f"AI video: {n_scenes} clips × ≈{per_scene:.0f}s on {params.video_model}.")
    elif src in ("stock", "mixed"):
        vis = 0.0
        notes.append("Stock footage is free; scenes without a match fall back to AI images "
                     f"(+{image_price(params.image_model):g} cr each).")
        if src == "mixed":
            vis = round(n_scenes * 0.3) * image_price(params.image_model)
    elif src == "local":
        vis = 0.0
    br["visuals"] = vis
    br["music"] = MUSIC_CREDITS if params.music == "genosai" else 0.0
    total = sum(br.values())
    return Estimate(total=round(total, 2), breakdown={k: round(v, 2) for k, v in br.items()}, notes=notes)


def _guess_lang(text: str) -> str:
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    lat = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return "ru" if cyr > lat else "en"


if __name__ == "__main__":
    print(estimate(VideoParams(topic="Why cats sleep 16 hours a day")).model_dump_json(indent=1))
