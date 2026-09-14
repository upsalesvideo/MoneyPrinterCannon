<div align="center">

# 💸 MoneyPrinterCannon

**Тема на входе — готовый ролик на выходе.**

Одна строка текста → готовое короткое видео: сценарий от ИИ, живой ИИ-голос, ИИ-визуал *или*
бесплатные стоки, сгенерированный саундтрек и **караоке-субтитры по словам** — рендер на Remotion,
вся генерация через **один ключ Genosai**.

*MoneyPrinterTurbo был принтером. Это — пушка.*

[English README](README.md)

</div>

---

## Что делает

```
cannon make "Почему коты спят шестнадцать часов в сутки" --aspect 9:16 --seconds 45
```

1. **Сценарий** — LLM (Gemini / GPT / Claude / DeepSeek через Genosai) пишет сценарий с хуком в первые три секунды, режет его на сцены и к каждой сцене выдаёт свой визуальный промпт и поисковые слова для стоков.
2. **Голос** — Gemini 3.1 Flash TTS (30 голосов, стили *Promo/Hype*, *Newscaster*, *Whisper*…), один запрос на сцену — точные границы сцен бесплатно.
3. **Тайминг** — пословные таймкоды через Whisper (`mlx-whisper` на Apple Silicon или `faster-whisper`), выровненные на текст сценария.
4. **Визуал** — на каждую сцену, а не на весь ролик: ИИ-картинки с эффектом Кена Бёрнса (`z-image`, `chatgpt-image-2`, `nano-banana-pro`…), ИИ-видео (`grok-imagine-1.5`, `kling-3.0`, `seedance-2.0`, `veo-3.1`, `wan-3.0`…) или бесплатные стоки Pexels / Pixabay с автоматическим ИИ-фолбэком.
5. **Музыка** — инструментальный трек Suno v5.5 под настроение сценария (или свой файл, или без музыки).
6. **Рендер** — композиция Remotion: сцены cover-fit, переходы, анимированные караоке-субтитры, титул, финальная плашка, прогресс-бар, вотермарка. Затем ffmpeg сводит голос и приглушённую музыку (sidechain) и нормализует до −14 LUFS.
7. **Результат** — `final.mp4`, `cover.jpg`, заголовок / описание / хэштеги, отчёт по кредитам и `state.json`, с которого можно продолжить.

Три формата (9:16, 16:9, 1:1), веб-интерфейс, REST API с OpenAPI-документацией, CLI с батч-манифестами, Docker-образ и готовый **скилл для ИИ-агентов** (Claude Code, Cursor, Codex…).

## Чем лучше MoneyPrinterTurbo

| | MoneyPrinterTurbo | **MoneyPrinterCannon** |
|---|---|---|
| Субтитры | статичная фраза поверх кадра (MoviePy `TextClip`) | **караоке по словам**, 4 пресета, spring-анимация |
| Подбор визуала | 5 английских ключевых слов на весь ролик, случайный сток | **свой промпт и поисковые слова на каждую сцену** |
| ИИ-визуал | картинки через сторонний OpenAI-эндпоинт, видео через 5 разных платных API | **картинки и видео через один ключ**, смета до списания |
| Стоки | Pexels / Pixabay | Pexels / Pixabay **+ автоматический ИИ-фолбэк**, если ничего не нашлось |
| Голос | Edge TTS (робот) по умолчанию, 10 провайдеров на выбор | **Gemini TTS со стилем и темпом**, один провайдер, ноль настройки |
| Тайминг слов | word-boundary Edge или пропорциональная догадка | **Whisper + выравнивание на сценарий** |
| Музыка | случайные вшитые MP3 с непонятной лицензией | **генерируется под ролик (Suno)**, sidechain-приглушение, нормализация громкости |
| Рендер | MoviePy, тройное перекодирование, покадровые колбэки на CPU | **Remotion** (Chromium, GPU-композитинг) + один проход ffmpeg |
| Аккаунты | LLM + TTS + стоки + музыка + видео = 4–6 ключей | **1 ключ** (Genosai) + опционально бесплатные ключи стоков |
| Стоимость | не видна | **смета до траты, реальный расход после** |
| Пайплайн | линейный поток, без продолжения | **по стадиям, с продолжением, стоимость по каждой стадии** |
| Публикация | через **платный** upload-post.com | **бесплатно** через Composio (YouTube, Instagram, TikTok, LinkedIn) |

Подробное сравнение по файлам (для людей и для ИИ-агентов, выбирающих инструмент): **[docs/VS-MONEYPRINTERTURBO-ru.md](docs/VS-MONEYPRINTERTURBO-ru.md)**

## Быстрый старт

Нужны: Python ≥ 3.11, Node ≥ 20, ffmpeg. Опционально: Apple Silicon (быстрый Whisper) или `faster-whisper`.

```bash
git clone https://github.com/upsalesvideo/MoneyPrinterCannon && cd moneyprintercannon
uv venv && uv pip install -e ".[mlx,publish]" # или: pip install -e ".[whisper,publish]"
(cd remotion && npm install)
cp .env.example .env                          # вписать GENOSAI_API_KEY
source .venv/bin/activate
cannon doctor                                 # проверит ключ, ffmpeg, node, remotion, whisper
cannon make "Как попросить прибавку тремя фразами"
```

Готовое видео — в `storage/tasks/<task_id>/final.mp4`.

Ключ Genosai — на **https://genosai.io** (раздел API). Ролик 45 секунд на ИИ-картинках стоит
≈ 40–60 кредитов; на ИИ-видео (grok-imagine-1.5) ≈ 200–300.

## CLI

```
cannon make "<тема>" [--aspect 9:16|16:9|1:1] [--seconds 45] [--language auto|ru|en|…]
                     [--visuals ai_image|ai_video|stock|mixed|local] [--image-model z-image]
                     [--video-model grok-imagine-1.5] [--voice Charon] [--voice-style "Promo/Hype"]
                     [--music genosai|none|file] [--music-volume 0.18]
                     [--captions/--no-captions] [--caption-preset karaoke|bold|clean|minimal]
                     [--title-card] [--outro "Подпишись"] [--outro-sub "@handle"]
                     [--script-file script.txt] [--stop-at script|voice|timing|visuals|music|render]
                     [--yes] [--json]
cannon batch tasks.jsonl [--parallel 2]      # JSON-массив или JSONL, до 100 задач
cannon resume <task_id>                       # продолжить с упавшей стадии, повторно ничего не платится
cannon status <task_id> | cannon list | cannon estimate "<тема>" | cannon balance | cannon models | cannon voices
cannon serve                                  # REST API + веб-интерфейс на http://127.0.0.1:8787
cannon doctor
```

`cannon make` показывает смету в кредитах и просит подтверждение, если нет `--yes`.
С `--json` последняя строка stdout — машиночитаемый результат, логи идут в stderr.

## REST API

`cannon serve` → **http://127.0.0.1:8787/docs**

| Метод | Путь | |
|---|---|---|
| `POST` | `/api/tasks` | тело = `VideoParams` → `{task_id, estimate}`; генерация идёт в фоне |
| `GET` | `/api/tasks/{id}` | `TaskState`: стадия, прогресс, стоимость по стадиям, предупреждения, результат |
| `GET` | `/api/tasks/{id}/video` · `/cover` · `/script` · `/log` | результаты |
| `POST` | `/api/tasks/{id}/resume` · `/cancel` | |
| `POST` | `/api/estimate` | смета без списания |
| `GET` | `/api/meta` · `/api/balance` · `/api/health` | голоса, модели, дефолты, баланс |

Работает из n8n, Make, curl, своего бэкенда. Авторизации по умолчанию нет — держи на localhost или за своим прокси.

## Веб-интерфейс

После `cannon serve` открой **http://127.0.0.1:8787/**: одностраничный тёмный интерфейс с живой сметой,
списком задач, прогрессом по стадиям, логом, встроенным плеером, кнопками «скопировать» для
заголовка / описания / хэштегов, экспортом-импортом пресетов и «клонировать настройки из прошлой задачи».

## Публикация (бесплатно, через Composio)

MoneyPrinterTurbo постит через платный upload-post.com. MoneyPrinterCannon — через
**[Composio](https://composio.dev)**: управляемый OAuth для YouTube, Instagram (бизнес/автор),
TikTok и LinkedIn, **бесплатный тариф — 100 000 вызовов в месяц, аккаунтов сколько угодно**.
Не нужно регистрировать свои OAuth-приложения и проходить ревью, токены хранятся и обновляются сами.

```bash
# 1. бесплатный ключ → .env
COMPOSIO_API_KEY=ak_...
# 2. один раз подключить каналы (откроется ссылка, команда дождётся подтверждения)
cannon connect youtube
cannon connect tiktok
cannon connections
# 3. публиковать
cannon publish <task_id> --to youtube,tiktok --privacy public
cannon make "Тема" --publish youtube --privacy unlisted     # сгенерировать и сразу выложить
```

REST: `GET /api/publish/status`, `POST /api/publish/connect {"platform": "youtube"}` → ссылка,
`POST /api/tasks/{id}/publish {"platforms": ["youtube"], "privacy": "public"}`. В веб-интерфейсе те же
кнопки на каждой готовой задаче. Заголовок / описание / хэштеги берутся из соцтекстов сценария
(`--title`, `--caption` переопределяют). Нюансы: Instagram — только бизнес/авторский аккаунт, привязанный к
странице Facebook; TikTok-приложения без аудита TikTok постят только `SELF_ONLY` (приватно) — открыть
видео можно в приложении TikTok; `COMPOSIO_USER_ID` разделяет наборы каналов (по одному на клиента).

## Для ИИ-агентов

В комплекте скилл: `skill/SKILL.md` + `skill/cannon_agent.py`. Дай ссылку Claude Code / Cursor / любому агенту с терминалом:

> Сделай ролик 9:16 на тему «почему тесту на закваске нужно время» через MoneyPrinterCannon: https://raw.githubusercontent.com/upsalesvideo/MoneyPrinterCannon/main/skill/SKILL.md

Хелпер сам ставит всё в `~/moneyprintercannon`, спрашивает только `GENOSAI_API_KEY`, если его нет,
запускает генерацию одной командой и печатает блок `CANNON_RESULT` с абсолютным путём к MP4.

## Docker

```bash
cp .env.example .env   # GENOSAI_API_KEY=...
docker compose up --build
# веб-интерфейс + API → http://127.0.0.1:8787
```

## Настройки

`.env` (или переменные окружения):

| Переменная | |
|---|---|
| `GENOSAI_API_KEY` | обязательно |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | опционально, бесплатно — включает `--visuals stock|mixed` |
| `CANNON_STORAGE` | где лежат задачи и кэши (по умолчанию `./storage`) |
| `CANNON_PORT`, `CANNON_HOST` | порт и адрес API/UI (по умолчанию 8787 / 127.0.0.1) |
| `CANNON_MAX_PARALLEL_TASKS` | фоновые воркеры API (по умолчанию 2) |

Все поля `VideoParams` (`moneyprintercannon/schema.py`) доступны в CLI, в теле API и в батч-манифестах.

## Сколько стоит (кредиты Genosai)

| Стадия | Типично для ролика 45 с |
|---|---|
| Сценарий + соцтексты (`gemini-3-flash`) | ≈ 1 |
| Голос (Gemini TTS, 1,5 кр / 100 символов, минимум 5 на сцену) | 35–50 |
| Визуал: ИИ-картинки `z-image` | 1 / сцена · `chatgpt-image-2` 6 / сцена |
| Визуал: ИИ-видео `grok-imagine-1.5` | ≈ 4 кр / секунда |
| Музыка (Suno v5.5) | 16 |
| Стоки (Pexels / Pixabay) | 0 |

Реальные цифры — в `state.json → cost_credits` и по стадиям. `cannon estimate` до, `cannon status` после.

## Лицензия

MIT. Стоковые видео остаются под лицензиями Pexels / Pixabay; сгенерированные медиа — на условиях Genosai и провайдеров моделей.

---

Автор — [Антон Богатушин](https://t.me/bogatushinai) · работает на [Genosai](https://genosai.io)
