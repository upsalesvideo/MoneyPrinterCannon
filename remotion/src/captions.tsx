import React from 'react';
import {AbsoluteFill, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {
  Aspect,
  CaptionsProps,
  DEFAULT_CAPTIONS,
  SafeZone,
  Word,
  captionFontSize,
  fontStack,
} from './theme';

type Group = {s: number; e: number; words: Word[]};

// maxWords per group, group span ≤ maxSpan s, new group after a pause > gap s
export const groupWords = (
  words: Word[],
  maxWords = 3,
  maxSpan = 1.7,
  gap = 0.8,
): Group[] => {
  const groups: Group[] = [];
  let cur: Word[] = [];
  for (const w of words) {
    if (
      cur.length >= maxWords ||
      (cur.length > 0 && w.e - cur[0].s > maxSpan) ||
      (cur.length > 0 && w.s - cur[cur.length - 1].e > gap)
    ) {
      groups.push({s: cur[0].s, e: cur[cur.length - 1].e, words: cur});
      cur = [];
    }
    cur.push(w);
  }
  if (cur.length) groups.push({s: cur[0].s, e: cur[cur.length - 1].e, words: cur});
  return groups;
};

// Emphasis: numbers and money words (ru + en)
export const isEmphasis = (w: string) =>
  /\d/.test(w) ||
  /[$€₽£%]/.test(w) ||
  /рубл|доллар|миллион|тысяч|деньг|бесплатн|dollar|million|billion|thousand|money|free|cash|profit|price/i.test(
    w,
  );

const strokeShadow = (c: string, px: number) =>
  `-${px}px -${px}px 0 ${c}, ${px}px -${px}px 0 ${c}, -${px}px ${px}px 0 ${c}, ${px}px ${px}px 0 ${c}, ` +
  `-${px}px 0 0 ${c}, ${px}px 0 0 ${c}, 0 -${px}px 0 ${c}, 0 ${px}px 0 ${c}, ` +
  `0 ${px * 2.5}px ${px * 6}px rgba(0,0,0,0.6)`;

export const Captions: React.FC<{
  words: Word[];
  captions: CaptionsProps;
  aspect: Aspect;
  safe: SafeZone;
  font?: string;
}> = ({words, captions, aspect, safe, font}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;

  const preset = captions.preset ?? DEFAULT_CAPTIONS.preset;
  const position = captions.position ?? DEFAULT_CAPTIONS.position;
  const maxWords = captions.maxWords ?? DEFAULT_CAPTIONS.maxWords;
  const accent = captions.accent ?? DEFAULT_CAPTIONS.accent;
  const emphasis = captions.emphasis ?? DEFAULT_CAPTIONS.emphasis;
  const textColor = captions.textColor ?? DEFAULT_CAPTIONS.textColor;
  const strokeColor = captions.strokeColor ?? DEFAULT_CAPTIONS.strokeColor;
  const baseSize = captions.fontSize ?? captionFontSize(aspect);
  const fontSize = preset === 'minimal' ? Math.round(baseSize * 0.78) : baseSize;
  const uppercase = preset === 'minimal' ? false : (captions.uppercase ?? DEFAULT_CAPTIONS.uppercase);

  const groups = React.useMemo(() => groupWords(words, maxWords, 1.7, 0.8), [words, maxWords]);
  // Latest group that has started wins (a new group must not wait for the
  // previous group's hold-out to expire); the hold-out only fills pauses.
  let gi = -1;
  for (let i = groups.length - 1; i >= 0; i--) {
    if (t >= groups[i].s - 0.06) {
      gi = t <= groups[i].e + 0.22 ? i : -1;
      break;
    }
  }
  if (gi === -1) return null;
  const g = groups[gi];

  const appear = spring({
    frame: frame - Math.round(g.s * fps),
    fps,
    config: {damping: 14, stiffness: 260, mass: 0.6},
    durationInFrames: 10,
  });
  // slight alternating tilt for a hand-cut feel (karaoke/bold only)
  const tilt = preset === 'karaoke' || preset === 'bold' ? ((gi % 3) - 1) * 1.6 : 0;
  const strokePx = Math.max(2, Math.round(fontSize / 19));

  const justify =
    position === 'top' ? 'flex-start' : position === 'center' ? 'center' : 'flex-end';
  const margins: React.CSSProperties =
    position === 'top'
      ? {marginTop: safe.marginTop}
      : position === 'bottom'
        ? {marginBottom: safe.marginBottom}
        : {};

  const groupBg =
    preset === 'minimal'
      ? {
          backgroundColor: 'rgba(0,0,0,0.55)',
          borderRadius: Math.round(fontSize * 0.3),
          padding: `${Math.round(fontSize * 0.28)}px ${Math.round(fontSize * 0.5)}px`,
        }
      : {};

  return (
    <AbsoluteFill style={{justifyContent: justify, alignItems: 'center', pointerEvents: 'none'}}>
      <div
        style={{
          ...margins,
          ...groupBg,
          maxWidth: safe.maxWidth,
          transform: `scale(${0.72 + 0.28 * appear}) rotate(${tilt}deg)`,
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          alignItems: 'center',
          gap: `${Math.round(fontSize * 0.08)}px ${Math.round(fontSize * 0.26)}px`,
          fontFamily: fontStack(font),
          fontWeight: preset === 'clean' || preset === 'minimal' ? 600 : 800,
          fontSize,
          lineHeight: 1.14,
          textAlign: 'center',
          textTransform: uppercase ? 'uppercase' : 'none',
          letterSpacing: '0.01em',
          color: textColor,
        }}
      >
        {g.words.map((w, i) => {
          const active = t >= w.s && t <= w.e + 0.12;
          const emph = isEmphasis(w.w);
          const pop = spring({
            frame: frame - Math.round(w.s * fps),
            fps,
            config: {damping: 12, stiffness: 300, mass: 0.5},
            durationInFrames: 8,
          });
          const base: React.CSSProperties = {
            display: 'inline-block',
            whiteSpace: 'nowrap',
          };
          let style: React.CSSProperties = base;

          if (preset === 'karaoke') {
            style = {
              ...base,
              transform: `scale(${active ? 0.85 + 0.25 * pop : 1})`,
              color: active ? '#ffffff' : emph ? emphasis : textColor,
              backgroundColor: active ? accent : 'transparent',
              borderRadius: Math.round(fontSize * 0.23),
              padding: active ? `2px ${Math.round(fontSize * 0.23)}px` : '2px 0',
              textShadow: active ? '0 6px 18px rgba(0,0,0,0.45)' : strokeShadow(strokeColor, strokePx),
            };
          } else if (preset === 'bold') {
            style = {
              ...base,
              transform: `scale(${active ? 0.9 + 0.22 * pop : 1})`,
              color: active ? accent : emph ? emphasis : textColor,
              textShadow: strokeShadow(strokeColor, strokePx),
            };
          } else if (preset === 'clean') {
            style = {
              ...base,
              position: 'relative',
              color: emph ? emphasis : textColor,
              textShadow: '0 2px 6px rgba(0,0,0,0.55), 0 0 2px rgba(0,0,0,0.6)',
              paddingBottom: Math.round(fontSize * 0.1),
            };
          } else {
            // minimal
            style = {
              ...base,
              color: active ? accent : emph ? emphasis : textColor,
              textShadow: '0 1px 3px rgba(0,0,0,0.5)',
            };
          }

          return (
            <span key={i} style={style}>
              {w.w}
              {preset === 'clean' ? (
                <span
                  style={{
                    position: 'absolute',
                    left: 0,
                    right: 0,
                    bottom: 0,
                    height: Math.max(4, Math.round(fontSize * 0.09)),
                    borderRadius: 4,
                    backgroundColor: accent,
                    transform: `scaleX(${active ? pop : 0})`,
                    transformOrigin: 'left center',
                  }}
                />
              ) : null}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
