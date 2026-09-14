# cashcannon-remotion

Remotion renderer for CashCannon: scenes (image Ken Burns / looping video) +
transitions + karaoke word captions + title / outro / progress bar / watermark.
Audio is **not** rendered here — `render.py` mixes voice and music with ffmpeg afterwards.

## Render

```bash
npm install                      # once (typescript is pinned to 5.9.3 — TS 7 breaks @remotion/bundler)

npx remotion render src/index.ts Main out/demo.mp4 \
  --props=public/demo/props.json --codec=h264 --crf=18 --muted --log=error

npm run render                   # same thing (9:16 demo)
npm run render:16x9              # 16:9 demo → out/demo-16x9.mp4
npm run typecheck
```

One composition, id `Main`. `calculateMetadata` reads `width / height / fps / durationSec`
from the props, so the same composition renders 9:16, 16:9 and 1:1 —
`durationInFrames = ceil(durationSec * fps)`.

Media paths in props are relative to `public/` (served through `staticFile`), e.g.
`"demo/scene-1.jpg"` or `"tasks/<id>/visuals/scene-01.mp4"` (`public/tasks` is a symlink
to `storage/tasks`, created by `render.py`). Absolute `http(s)://` URLs also work.

## props.json (see ../CONTRACT.md → "Remotion props.json")

```jsonc
{
  "aspect": "9:16", "width": 1080, "height": 1920, "fps": 30,
  "durationSec": 13,                       // total incl. title/outro
  "scenes": [
    {"index": 1, "start": 0, "end": 2.5, "kind": "image", "src": "demo/scene-1.jpg",
     "fit": "cover", "kenBurns": "in"},                        // in|out|left|right|none
    {"index": 2, "start": 2.5, "end": 8, "kind": "video", "src": "demo/clip-1.mp4",
     "fit": "cover", "kenBurns": "in", "loop": true, "srcDurationSec": 5}
  ],
  "words": [{"s": 0.4, "e": 0.75, "w": "Cats"}],           // absolute seconds
  "captions": {"enabled": true, "preset": "karaoke", "position": "bottom",
               "fontSize": 78, "maxWords": 3, "uppercase": true,
               "accent": "#10b981", "emphasis": "#fbbf24",
               "textColor": "#ffffff", "strokeColor": "#0a0e0c"},
  "title": {"text": "Why cats sleep 16 h", "durSec": 0},   // 0 = overlay plate first 2.5 s
  "outro": {"text": "Follow for more", "sub": "@handle", "durSec": 2.5},
  "transition": "fade",                                     // cut|fade|slide|zoom
  "progressBar": true,
  "watermark": {"text": "made with CashCannon"},
  "theme": {"accent": "#10b981", "bg": "#0a0e0c", "font": "Onest"}
}
```

Behaviour notes

- **Scenes**: `image` → `<Img>` with Ken Burns (scale 1.0→1.12 for `in`, reverse for `out`,
  pan for `left`/`right`). `video` → `<OffthreadVideo muted>`; when `loop` and
  `srcDurationSec < scene length` it is wrapped in `<Loop>`; `kenBurns: "in"` adds a slow
  breathing zoom (1.0→1.05). The last scene is stretched to `durationSec`.
- **Transitions** (12 frames, incoming scene only, never on captions): `fade` opacity,
  `slide` from the right, `zoom` scale 1.25→1 + opacity, `cut` nothing.
- **Title**: `durSec: 0` → plate at the top for 2.5 s, springs in, leaves by sliding up
  (no opacity). `durSec > 0` → full-screen title card for that long (python must have
  shifted the timeline already).
- **Outro**: last `durSec` seconds, full-screen card on `theme.bg` with accent glow,
  big `text` + accent `sub`.
- **Progress bar**: thin accent line at the very top. **Watermark**: small text bottom-right.

## Caption presets

| preset    | look |
| --------- | ---- |
| `karaoke` | Onest 800 UPPERCASE, active word white on an accent plate with a spring pop, numbers / money words in `emphasis`, inactive words with a thick 8-direction stroke, slight alternating tilt per group |
| `bold`    | same type, no plate: active word coloured `accent` + scale pop, stroke on all words |
| `clean`   | white 600 text, thin shadow, active word underlined by an accent bar that grows in |
| `minimal` | ~78 % font size, no uppercase, semi-transparent dark pill under the whole group, active word in `accent` |

Grouping: `maxWords` per group, group span ≤ 1.7 s, new group after a pause > 0.8 s.
Default `fontSize` if omitted: 78 (9:16), 64 (16:9), 66 (1:1). Position `bottom` uses the
9:16 safe zone (block bottom at y≈1590, marginBottom 330); `center` / `top` also supported.

## Font

Onest 600/800 is loaded from `public/fonts/*.woff2` first (works offline), then from
Google Fonts (URLs from `@remotion/google-fonts/Onest`). If both fail the render still
completes with `system-ui` — the loader always calls `continueRender`. (Plain
`@remotion/google-fonts` `loadFont()` would hang for 60 s and fail with no network.)

## Files

```
remotion.config.ts   jpeg frames, overwrite output, ANGLE GL
src/index.ts         registerRoot
src/Root.tsx         <Composition id="Main"> + calculateMetadata
src/Video.tsx        bg + scenes + captions + title + outro + progress bar + watermark
src/scenes.tsx       scene layer, Ken Burns, loop, transitions
src/captions.tsx     groupWords + 4 presets
src/theme.ts         types (props contract), defaults, safe zones, font sizes
src/font.ts          offline-safe Onest loader
src/demoProps.ts     defaultProps for Studio (mirrors public/demo/props.json)
public/demo/         generated test assets + props.json / props-16x9.json
public/fonts/        Onest woff2 (latin + cyrillic, variable weight)
```
