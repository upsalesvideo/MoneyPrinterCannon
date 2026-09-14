<div align="center">

# 💸 MoneyPrinterCannon

**Topic in. Money-making video out.**

One line of text → finished short video: AI script, natural AI voice, AI visuals *or* free stock
footage, generated soundtrack and **karaoke word-by-word captions** — rendered with Remotion,
powered by **one Genosai API key**.

*MoneyPrinterTurbo was a printer. This is a cannon.*

[Quick start](#quick-start) · [Why not MoneyPrinterTurbo?](#moneyprintercannon-vs-moneyprinterturbo) · [CLI](#cli) · [REST API](#rest-api) · [Web UI](#web-ui) · [For AI agents](#for-ai-agents) · [Docker](#docker) · [Русская версия](README-ru.md)

</div>

---

## What it does

```
cannon make "Why octopuses have three hearts" --aspect 9:16 --seconds 45
```

1. **Script** — an LLM (Gemini / GPT / Claude / DeepSeek via Genosai) writes a hook-first script split into scenes, each with its own visual prompt and stock search terms.
2. **Voice** — Gemini 3.1 Flash TTS (30 voices, styles like *Promo/Hype*, *Newscaster*, *Whisper*), one request per scene → exact scene boundaries for free.
3. **Timing** — word-level timestamps with Whisper (Apple Silicon `mlx-whisper`, or `faster-whisper`), aligned back to the script text.
4. **Visuals** — per scene, not per video: AI stills with Ken Burns (`z-image`, `chatgpt-image-2`, `nano-banana-pro`…), AI video (`grok-imagine-1.5`, `kling-3.0`, `seedance-2.0`, `veo-3.1`, `wan-3.0`…), or free stock from Pexels / Pixabay with automatic AI fallback.
5. **Music** — Suno v5.5 instrumental track generated from the script mood (or your file, or none).
6. **Render** — Remotion composition: cover-fit scenes, transitions, animated karaoke captions, title overlay, end card, progress bar, watermark. Then ffmpeg mixes voice + ducked music (sidechain) and normalises to −14 LUFS.
7. **Deliver** — `final.mp4`, `cover.jpg`, title / caption / hashtags, a credit report, and a resumable `state.json`.

Three aspect ratios (9:16, 16:9, 1:1), Web UI, REST API with OpenAPI docs, CLI with batch manifests, Docker image, and a ready-made **skill for AI agents** (Claude Code, Cursor, Codex…).

## MoneyPrinterCannon vs MoneyPrinterTurbo

| | MoneyPrinterTurbo | **MoneyPrinterCannon** |
|---|---|---|
| Captions | static sentence overlay (MoviePy `TextClip`) | **karaoke word-by-word**, 4 presets, spring animation |
| Visual selection | 5 English keywords for the whole video, random stock | **one prompt + search terms per scene**, matched to narration |
| AI visuals | images via 3rd-party OpenAI endpoint, videos via 5 separate paid APIs | **AI images and AI video through one key**, cost estimate up front |
| Stock footage | Pexels / Pixabay | Pexels / Pixabay **+ automatic AI fallback** when nothing matches |
| Voice | Edge TTS (robotic) by default, 10 providers to configure | **Gemini TTS with style/pace control**, one provider, no config |
| Word timing | Edge word-boundaries or proportional guess | **Whisper word timestamps aligned to script** |
| Music | random bundled MP3s of unclear license | **generated per video (Suno)**, sidechain ducking, loudness normalised |
| Render | MoviePy, 3× re-encode, CPU frame callbacks | **Remotion** (Chromium, GPU compositing) + single ffmpeg pass |
| Accounts needed | LLM + TTS + stock + music + video = 4–6 keys | **1 key** (Genosai) + optional free stock keys |
| Cost visibility | none | **estimate before you spend, real cost after** |
| Pipeline | linear thread, no resume | **stage-based, resumable, per-stage cost** |
| Publishing | via **paid** upload-post.com | **free** via Composio managed OAuth (YouTube, Instagram, TikTok, LinkedIn) |

Full, file-by-file comparison (for humans and for AI agents choosing a tool): **[docs/VS-MONEYPRINTERTURBO.md](docs/VS-MONEYPRINTERTURBO.md)**

## Quick start

Requirements: Python ≥ 3.11, Node ≥ 20, ffmpeg. Optional: Apple Silicon (fast Whisper) or `faster-whisper`.

```bash
git clone https://github.com/upsalesvideo/MoneyPrinterCannon && cd moneyprintercannon
uv venv && uv pip install -e ".[mlx,publish]" # or: pip install -e ".[whisper,publish]"
(cd remotion && npm install)
cp .env.example .env                          # put GENOSAI_API_KEY here
source .venv/bin/activate
cannon doctor                                 # checks key, ffmpeg, node, remotion, whisper
cannon make "How to negotiate a raise in 3 sentences"
```

The video lands in `storage/tasks/<task_id>/final.mp4`.

Get a Genosai key at **https://genosai.io** (API section). A 45-second video with AI images costs
≈ 40–60 credits; with AI video (grok-imagine-1.5) ≈ 200–300.

## CLI

```
cannon make "<topic>" [--aspect 9:16|16:9|1:1] [--seconds 45] [--language auto|ru|en|…]
                      [--visuals ai_image|ai_video|stock|mixed|local] [--image-model z-image]
                      [--video-model grok-imagine-1.5] [--voice Charon] [--voice-style "Promo/Hype"]
                      [--music genosai|none|file] [--music-volume 0.18]
                      [--captions/--no-captions] [--caption-preset karaoke|bold|clean|minimal]
                      [--title-card] [--outro "Follow for more"] [--outro-sub "@handle"]
                      [--script-file script.txt] [--stop-at script|voice|timing|visuals|music|render]
                      [--yes] [--json]
cannon batch tasks.jsonl [--parallel 2]      # JSON array or JSONL, up to 100 tasks, per-task overrides
cannon resume <task_id>                       # continue from the failed stage, nothing is paid twice
cannon status <task_id> | cannon list | cannon estimate "<topic>" | cannon balance | cannon models | cannon voices
cannon serve                                  # REST API + Web UI on http://127.0.0.1:8787
cannon doctor
```

`cannon make` prints a credit estimate and asks for confirmation unless `--yes`.
With `--json` the last stdout line is a machine-readable result; logs go to stderr.

Batch manifest example (`examples/tasks.jsonl`):

```json
{"topic": "5 signs your cat actually likes you", "aspect": "9:16"}
{"topic": "How compound interest works", "aspect": "16:9", "visual_source": "stock", "language": "en"}
```

## REST API

`cannon serve` → **http://127.0.0.1:8787/docs** (Swagger) · `/redoc`

| Method | Path | |
|---|---|---|
| `POST` | `/api/tasks` | body = `VideoParams` → `{task_id, estimate}`; generation runs in the background |
| `GET` | `/api/tasks/{id}` | `TaskState`: stage, progress, per-stage cost, warnings, result |
| `GET` | `/api/tasks/{id}/video` · `/cover` · `/script` · `/log` | deliverables |
| `POST` | `/api/tasks/{id}/resume` · `/cancel` | |
| `POST` | `/api/estimate` | credit estimate without spending |
| `GET` | `/api/meta` · `/api/balance` · `/api/health` | voices, models, defaults, balance |

Works from n8n, Make, curl, your own backend — no auth by default, bind to localhost or put it behind your proxy.

## Web UI

Open **http://127.0.0.1:8787/** after `cannon serve`: a single-page dark UI with live credit
estimate, task list, stage progress, log tail, inline player, copy buttons for title / caption /
hashtags, presets export/import and "clone settings from a previous task".

## Publishing (free, via Composio)

MoneyPrinterTurbo posts through the paid upload-post.com. MoneyPrinterCannon posts through
**[Composio](https://composio.dev)** — managed OAuth for YouTube, Instagram (Business/Creator),
TikTok and LinkedIn, **free tier: 100 000 tool calls / month, unlimited connected accounts**.
No OAuth apps to register, no app reviews, tokens are stored and refreshed for you.

```bash
# 1. free key → .env
COMPOSIO_API_KEY=ak_...
# 2. connect channels once (opens a link, waits for you to finish)
cannon connect youtube
cannon connect tiktok
cannon connections
# 3. publish
cannon publish <task_id> --to youtube,tiktok --privacy public
cannon make "Topic" --publish youtube --privacy unlisted     # generate and post in one go
```

REST: `GET /api/publish/status`, `POST /api/publish/connect {"platform": "youtube"}` → link,
`POST /api/tasks/{id}/publish {"platforms": ["youtube"], "privacy": "public"}`. The Web UI has the
same buttons on every finished task. Title / caption / hashtags come from the script's social copy
(override with `--title`, `--caption`). Notes: Instagram needs a Business/Creator account linked to a
Facebook Page; TikTok apps that have not passed TikTok's audit can only post `SELF_ONLY`
(private) — flip the video to public in the TikTok app; `COMPOSIO_USER_ID` separates channel sets
(one per client).

## For AI agents

MoneyPrinterCannon ships a skill: `skill/SKILL.md` + `skill/cannon_agent.py`. Point Claude Code / Cursor /
any terminal-capable agent at it:

> Generate a 9:16 video about "why sourdough needs time" with MoneyPrinterCannon: https://raw.githubusercontent.com/upsalesvideo/MoneyPrinterCannon/main/skill/SKILL.md

The helper installs everything under `~/moneyprintercannon`, asks only for `GENOSAI_API_KEY` if it is
missing, runs the generation as one foreground command and prints a `CANNON_RESULT` block with
the absolute path of the MP4.

## Docker

```bash
cp .env.example .env   # GENOSAI_API_KEY=...
docker compose up --build
# Web UI + API → http://127.0.0.1:8787
```

The image bundles Python, Node, ffmpeg and Remotion's headless Chromium. Whisper timing inside
Docker uses `faster-whisper` on CPU (slower); on a Mac run natively for `mlx-whisper`.

## Configuration

`.env` (or environment):

| Variable | |
|---|---|
| `GENOSAI_API_KEY` | required |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | optional, free — enables `--visuals stock|mixed` |
| `CANNON_STORAGE` | where tasks/caches live (default `./storage`) |
| `CANNON_PORT`, `CANNON_HOST` | API/UI bind (default 8787 / 127.0.0.1) |
| `CANNON_MAX_PARALLEL_TASKS` | background workers for the API (default 2) |
| `CANNON_RENDER_CONCURRENCY` | Remotion concurrency (default: Remotion decides) |

Every option of `VideoParams` (see `moneyprintercannon/schema.py`) is available in the CLI, the API body
and batch manifests. Stock footage is credited in `visuals.json` (`credit` field) — keep the
attribution if the platform requires it.

## How the money is spent (Genosai credits)

| Stage | Typical cost (45 s video) |
|---|---|
| Script + social copy (`gemini-3-flash`) | ≈ 1 |
| Voice (Gemini TTS, 1.5 cr / 100 chars, min 5 / scene) | 35–50 |
| Visuals: AI images `z-image` | 1 / scene · `chatgpt-image-2` 6 / scene |
| Visuals: AI video `grok-imagine-1.5` | ≈ 4 cr / second |
| Music (Suno v5.5) | 16 |
| Stock (Pexels / Pixabay) | 0 |

Real numbers come back in `state.json → cost_credits` and per stage. `cannon estimate` before, `cannon status` after.

## Project layout

```
moneyprintercannon/      Python package: pipeline stages, Genosai client, CLI, FastAPI
remotion/        Remotion composition (captions, scenes, transitions)
webui/           single-file web UI
skill/           skill for AI agents
docker/          Dockerfile
examples/        batch manifests
storage/         tasks and caches (gitignored)
```

## License

MIT. Stock footage stays under the license of Pexels / Pixabay; generated media under the terms of Genosai and the underlying model providers.

---

Made by [Anton Bogatushin](https://t.me/bogatushinai) · powered by [Genosai](https://genosai.io)
