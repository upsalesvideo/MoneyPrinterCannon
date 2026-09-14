"""Pydantic models shared by every module. This file is the source of truth for
the public contract (CLI flags, REST bodies, batch manifests, state.json)."""
from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Aspect = Literal["9:16", "16:9", "1:1"]
VisualSource = Literal["ai_image", "ai_video", "stock", "mixed", "local"]
StockProvider = Literal["pexels", "pixabay", "auto"]
MusicSource = Literal["none", "genosai", "file"]
CaptionPreset = Literal["karaoke", "bold", "clean", "minimal"]
CaptionPosition = Literal["bottom", "center", "top"]
Transition = Literal["cut", "fade", "slide", "zoom"]
KenBurns = Literal["auto", "in", "out", "left", "right", "none"]
TimingBackend = Literal["auto", "mlx", "faster", "proportional"]
Stage = Literal["script", "voice", "timing", "visuals", "music", "render"]
STAGES: tuple[Stage, ...] = ("script", "voice", "timing", "visuals", "music", "render")

ASPECT_SIZE: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


class VideoParams(BaseModel):
    """Everything a user can set for one video. Defaults produce a good vertical short."""

    # ---- content -------------------------------------------------------
    topic: str = Field("", description="Video topic / idea. Required unless script is given.")
    script: str = Field("", description="Ready narration text. Empty = let the LLM write it.")
    language: str = Field("auto", description="Narration language, e.g. 'ru', 'en'. auto = same as topic.")
    target_seconds: int = Field(45, ge=10, le=600, description="Desired narration length in seconds.")
    scene_count: int = Field(0, ge=0, le=60, description="0 = LLM decides (≈ one scene per 4-8 s).")
    style_hint: str = Field("", description="Extra creative direction for the script (tone, audience, CTA).")
    text_model: str = Field("gemini-3-flash", description="Genosai text model id.")

    # ---- visuals -------------------------------------------------------
    aspect: Aspect = "9:16"
    visual_source: VisualSource = Field(
        "ai_image",
        description="ai_image: AI stills + Ken Burns (cheap). ai_video: AI clips. "
        "stock: Pexels/Pixabay. mixed: stock first, AI image fallback. local: your files.",
    )
    image_model: str = Field("z-image", description="Genosai photo model for ai_image / fallbacks.")
    video_model: str = Field("grok-imagine-1.5", description="Genosai video model for ai_video.")
    video_resolution: str = Field("720p", description="AI video resolution (model dependent).")
    stock_provider: StockProvider = "auto"
    local_files: list[str] = Field(default_factory=list, description="Paths for visual_source=local.")
    visual_style: str = Field(
        "cinematic, soft natural light, shallow depth of field, rich but realistic colors",
        description="Appended to every visual prompt so all scenes match.",
    )
    ken_burns: KenBurns = "auto"
    transition: Transition = "fade"
    fit: Literal["cover", "contain"] = "cover"

    # ---- voice ---------------------------------------------------------
    voice: str = Field("Charon", description="Gemini TTS voice name.")
    voice_style: str = Field("Promo/Hype", description="Vocal Smile | Newscaster | Whisper | Empathetic | Promo/Hype | Deadpan")
    voice_pace: str = Field("Natural", description="Natural | Rapid Fire | The Drift | Staccato")
    voice_accent: str = Field("Neutral")
    voice_temperature: float = Field(1.0, ge=0, le=2)
    voice_gap_ms: int = Field(250, ge=0, le=2000, description="Silence between scenes.")
    custom_voice_file: str = Field("", description="Use this narration audio instead of TTS (requires script).")

    # ---- timing --------------------------------------------------------
    timing_backend: TimingBackend = "auto"

    # ---- music ---------------------------------------------------------
    music: MusicSource = "genosai"
    music_prompt: str = Field("", description="Suno prompt; empty = derived from the script mood.")
    music_file: str = Field("", description="Local file for music=file.")
    music_volume: float = Field(0.18, ge=0, le=1)

    # ---- captions & branding -------------------------------------------
    captions: bool = True
    caption_preset: CaptionPreset = "karaoke"
    caption_position: CaptionPosition = "bottom"
    caption_font_size: int = Field(0, ge=0, le=200, description="0 = preset default for the aspect.")
    caption_max_words: int = Field(3, ge=1, le=8)
    caption_uppercase: bool = True
    accent_color: str = Field("#10b981", pattern=r"^#[0-9a-fA-F]{6}$")
    emphasis_color: str = Field("#fbbf24", pattern=r"^#[0-9a-fA-F]{6}$")
    title_card: bool = Field(False, description="Show the hook/title as an overlay at the start.")
    outro_text: str = Field("", description="End card text; empty = no end card.")
    outro_sub: str = Field("", description="End card second line (handle, URL).")
    outro_seconds: float = Field(2.5, ge=0, le=10)
    progress_bar: bool = True
    watermark: str = Field("", description="Small text in a corner; empty = none.")

    # ---- output --------------------------------------------------------
    cover: bool = Field(True, description="Also produce cover.jpg (first strong frame).")
    social_meta: bool = Field(True, description="Ask the LLM for title/caption/hashtags.")
    variants: int = Field(1, ge=1, le=5, description="How many alternative cuts (visual reshuffle only).")

    @field_validator("topic", "script", mode="before")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()

    def require_content(self) -> None:
        if not self.topic and not self.script:
            raise ValueError("Either topic or script is required")

    @property
    def size(self) -> tuple[int, int]:
        return ASPECT_SIZE[self.aspect]


# ---------------------------------------------------------------------------
# LLM output
# ---------------------------------------------------------------------------
class Scene(BaseModel):
    index: int
    narration: str = Field(..., description="What the voice says in this scene (1-3 sentences).")
    visual_prompt: str = Field(..., description="English image/video prompt for this scene.")
    search_terms: list[str] = Field(default_factory=list, description="1-3 word English stock queries.")
    motion: str = Field("", description="Optional camera/motion hint for AI video.")


class SocialMeta(BaseModel):
    title: str = ""
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)


class Script(BaseModel):
    topic: str
    language: str
    title: str = Field("", description="Short hook / title (≤ 8 words).")
    scenes: list[Scene]
    music_prompt: str = Field("", description="Suno prompt for the background track.")
    social: SocialMeta = Field(default_factory=SocialMeta)

    @property
    def full_text(self) -> str:
        return " ".join(s.narration.strip() for s in self.scenes)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
class Word(BaseModel):
    s: float
    e: float
    w: str


class SceneTiming(BaseModel):
    index: int
    start: float
    end: float
    audio_file: str = ""


class Timing(BaseModel):
    backend: str
    words: list[Word]
    scenes: list[SceneTiming]
    voice_duration: float


# ---------------------------------------------------------------------------
# Visuals
# ---------------------------------------------------------------------------
class VisualAsset(BaseModel):
    index: int
    kind: Literal["video", "image"]
    path: str  # relative to task dir, e.g. visuals/scene-01.mp4
    source: str  # pexels | pixabay | genosai:z-image | local | ...
    duration: float = 0.0  # for videos
    width: int = 0
    height: int = 0
    credit: str = ""  # attribution (stock)
    cost_credits: float = 0.0
    ken_burns: str = ""  # images: in | out | left | right | none (chosen by visuals.py)


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------
class StageState(BaseModel):
    status: Literal["pending", "running", "succeeded", "failed", "skipped"] = "pending"
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cost_credits: float = 0.0
    error: Optional[str] = None
    note: str = ""


class TaskResult(BaseModel):
    video: str = ""  # relative path inside task dir
    cover: str = ""
    duration: float = 0.0
    title: str = ""
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    variants: list[str] = Field(default_factory=list)


class TaskState(BaseModel):
    task_id: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"] = "queued"
    stage: Optional[Stage] = None
    failed_stage: Optional[Stage] = None
    progress: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    stages: dict[str, StageState] = Field(default_factory=lambda: {s: StageState() for s in STAGES})
    cost_credits: float = 0.0
    estimate_credits: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    result: Optional[TaskResult] = None
    topic: str = ""

    def touch(self) -> None:
        self.updated_at = time.time()


class Estimate(BaseModel):
    """Credit estimate before spending anything."""

    total: float
    breakdown: dict[str, float]
    notes: list[str] = Field(default_factory=list)


def new_task_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
