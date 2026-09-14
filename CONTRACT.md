# CashCannon — internal module contract (read before writing any code)

CashCannon = "topic → finished short video" generator. Everything AI goes through the
**Genosai Public API** (one key, `GENOSAI_API_KEY`): LLM script, TTS voice, AI images,
AI video, Suno music. Free stock footage (Pexels / Pixabay) is an optional visual source.
Rendering = **Remotion** (karaoke word-level captions, Ken Burns, transitions) + ffmpeg
(voice/music mix with sidechain ducking, loudnorm −14 LUFS).

Repo layout (fixed):

```
cashcannon/                 python package (src)
  config.py                 settings from env (.env supported), paths
  schema.py                 pydantic models: VideoParams, TaskState, Script, ...  ← SOURCE OF TRUTH
  genosai.py                Genosai API client (chat, tts, photo, video, music, uploads, models, balance)
  llm.py                    script generation (structured JSON), social metadata
  tts.py                    per-scene TTS via Genosai, concat, durations
  timing.py                 word timestamps (mlx-whisper | faster-whisper | proportional fallback) + align to script
  stock.py                  Pexels / Pixabay search + cache + download
  visuals.py                per-scene visual resolution: stock | ai_image | ai_video | mixed | local
  music.py                  bgm: none | file | genosai (Suno)
  render.py                 props.json → Remotion render → ffmpeg mix/loudnorm → final.mp4 + cover.jpg
  pipeline.py               stage orchestrator with state.json, resume, stop_at, cost tracking
  estimate.py               credit estimate before spending
  api.py                    FastAPI app (REST + serves webui/)
  cli.py                    `cannon` CLI
remotion/                   Remotion project (TypeScript, typescript pinned 5.9.3)
  src/index.ts, Root.tsx, Video.tsx, captions.tsx, scenes.tsx, theme.ts
  public/tasks -> ../../storage/tasks   (symlink created by render.py if missing)
webui/index.html            single-file UI (vanilla JS) served by api.py at /
skill/SKILL.md, skill/cannon_agent.py     "skill for AI agents" (like MPT)
docker/Dockerfile, docker-compose.yml
examples/tasks.jsonl
storage/tasks/<task_id>/    per-task working dir (gitignored)
```

## Per-task directory layout

```
storage/tasks/<task_id>/
  params.json        VideoParams as submitted (merged with defaults)
  state.json         TaskState (see schema.py) — updated after every stage; pipeline is resumable from it
  script.json        Script (schema.py) — LLM output
  voice/scene-01.mp3 … per-scene TTS ; voice/voice.wav (48k stereo concat, gaps applied)
  timing.json        Timing (schema.py): words[], scenes[] with absolute start/end seconds
  visuals/scene-01.(mp4|jpg) … + visuals.json (VisualAsset[])
  music/bgm.mp3      optional
  props.json         Remotion input props (see below)
  render/silent.mp4  Remotion output (no audio)
  final.mp4          deliverable
  cover.jpg          deliverable (frame or AI cover)
  log.txt            human log (append)
```

## Pipeline stages (in order) — `pipeline.run(task_id, stop_at=None)`

`script → voice → timing → visuals → music → render`

Each stage: idempotent, skips if its outputs exist and `state.stages[stage].status == "succeeded"`
(unless `force=True`). Writes `state.stages[stage] = {status, started_at, finished_at, cost_credits, error}`,
`state.stage`, `state.progress` (0–100: script 10, voice 30, timing 40, visuals 70, music 78, render 100).
On exception: `state.status="failed"`, `state.failed_stage=stage`, `state.error=str(e)` and re-raise.

## Remotion props.json (contract between render.py and remotion/)

```jsonc
{
  "aspect": "9:16",            // "9:16" | "16:9" | "1:1"
  "width": 1080, "height": 1920, "fps": 30,
  "durationSec": 42.3,         // total incl. title/outro
  "scenes": [
    {"index": 1, "start": 0.0, "end": 5.2,
     "kind": "video" | "image",
     "src": "tasks/<id>/visuals/scene-01.mp4",   // path relative to remotion/public  (staticFile)
     "fit": "cover" | "contain",
     "kenBurns": "in" | "out" | "left" | "right" | "none",   // images only (video: subtle zoom if "in")
     "loop": true,              // video shorter than scene → loop
     "srcDurationSec": 8.0      // for videos (so player can loop correctly)
    }
  ],
  "words": [{"s": 0.12, "e": 0.40, "w": "Hello"}],   // absolute seconds on the final timeline
  "captions": {
    "enabled": true,
    "preset": "karaoke" | "bold" | "clean" | "minimal",
    "position": "bottom" | "center" | "top",
    "fontSize": 78, "maxWords": 3, "uppercase": true,
    "accent": "#10b981", "emphasis": "#fbbf24", "textColor": "#ffffff", "strokeColor": "#0a0e0c"
  },
  "title":  {"text": "Why cats sleep 16 h", "durSec": 0} | null,   // durSec 0 = overlay at start, no time added
  "outro":  {"text": "Follow for more", "sub": "@handle", "durSec": 2.5} | null,  // adds durSec to timeline
  "transition": "cut" | "fade" | "slide" | "zoom",
  "progressBar": true,
  "watermark": {"text": "made with CashCannon"} | null,
  "theme": {"accent": "#10b981", "bg": "#0a0e0c", "font": "Onest"}
}
```

Remotion: ONE composition id `Main`; `calculateMetadata` reads props → width/height/durationInFrames.
Render cmd used by render.py:
`npx remotion render src/index.ts Main <out> --props=<props.json> --codec=h264 --crf=16 --muted --concurrency=<n> --log=error`
Voice/music are NOT rendered by Remotion — render.py mixes them with ffmpeg afterwards:
`voice.wav` + optional `bgm.mp3` (ducked: sidechaincompress threshold=0.045 ratio=9 attack=15 release=350, music volume param, loop to length, fade out 2s) → loudnorm I=-14 TP=-1.5 → mux with silent.mp4 (`-c:v copy`).

## Genosai API (prod https://api.genosai.io, header `Authorization: Bearer $GENOSAI_API_KEY`)

- `GET /v1/balance` → `{main,bonus,total}`
- `GET /v1/models` → `{photo[],video[],tts[],music[],text[],embedding[]}` each with `id,input_options,references,cost_credits_default`
- `POST /v1/chat/completions` (OpenAI-compatible: model, messages, temperature, max_tokens; no response_format guaranteed → ask for JSON and parse robustly)
- `POST /v1/createTask` `{"model": id, "input": {...}}` → `{"data":{"taskId","cost"}}`
  - photo input: `prompt, aspect_ratio, resolution("1K"|"2K"|"4K"), image_urls[]`
  - video input: `prompt, aspect_ratio, resolution("480p"|"720p"|"1080p"), duration (string seconds), generate_audio(bool), image_urls[]`
  - tts input: `text, voice, style, pace, accent, temperature`   (model gemini-3.1-flash-tts; 1.5 cr/100 chars, min 5 cr/request; keep each request < 1000 chars)
  - music input: `prompt, instrumental(bool), duration(10..360)`   (model suno-v5.5; 16 cr, returns 2 tracks)
- `GET /v1/taskInfo?taskId=` → `{"data":{"status":"queued|processing|succeeded|failed","result":{"media_urls":[]},"cost":N,"message"}}`
- `POST /v1/uploads` multipart `file` → `{"data":{"url"}}` (one-shot URL)
- Errors: `{"error":"CODE","message":...}` with 400 VALIDATION_ERROR, 401, 402 INSUFFICIENT_CREDITS, 404 MODEL_NOT_FOUND, 429 (message contains "Try again in N second").
- RULES: dispatch createTask with ≥1.0 s gap globally (lock); poll every 5 s; watchdog 300 s → create a NEW task (max 3 attempts); 429 → sleep parsed seconds, does not consume an attempt; fresh `requests.Session()` + `Connection: close`; download media WITHOUT Authorization header. Thread pool size = number of jobs (except model z-image: max 3 workers).

Default models: text `gemini-3-flash`, tts `gemini-3.1-flash-tts` (voice Charon, style "Promo/Hype", pace Natural), image `z-image` (cheap b-roll) / `chatgpt-image-2` (when text-in-image needed e.g. cover), video `grok-imagine-1.5` (cheapest, 4 cr/s @720p) with `seedance-2.0-fast`, `kling-3.0`, `veo-3.1-lite`, `wan-3.0`, `minimax-h3` selectable, music `suno-v5.5`.

## Stock (optional; `PEXELS_API_KEY`, `PIXABAY_API_KEY`)
- Pexels: `GET https://api.pexels.com/videos/search?query=&orientation=portrait|landscape|square&per_page=15&size=medium` header `Authorization: <key>`; choose smallest rendition with min(w,h) >= 1080 (portrait: h>=1920 preferred) else largest.
- Pixabay: `GET https://pixabay.com/api/videos/?key=&q=&per_page=20&video_type=film&min_width=&min_height=` ; renditions large/medium.
- Search cache `storage/cache/search/<sha256(provider,term,aspect,min_dur)>.json` TTL 24h (don't cache empty); file cache `storage/cache/videos/vid-<md5(url no query)>.mp4`.
- If no keys configured and visual_source == "stock" → automatic fallback to `ai_image` with a warning in state.warnings.

## Coding standards
Python 3.11+, type hints, pydantic v2, `requests`, `loguru`; no global mutable config besides `config.settings`.
No API keys in files/logs. Keep functions small; each module has a `__main__` smoke test where sensible.
