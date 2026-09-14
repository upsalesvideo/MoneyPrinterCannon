import React from 'react';
import {
  AbsoluteFill,
  Easing,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import './font';
import {Scenes} from './scenes';
import {Captions} from './captions';
import {DEFAULT_THEME, SafeZone, VideoProps, fontStack, safeZone} from './theme';

// ---------- title ----------

const TitleOverlay: React.FC<{
  text: string;
  frames: number;
  safe: SafeZone;
  accent: string;
  bg: string;
  font?: string;
}> = ({text, frames, safe, accent, bg, font}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const inY = spring({frame, fps, config: {damping: 16, stiffness: 180, mass: 0.8}});
  // leave by sliding up (never opacity)
  const outY = interpolate(frame, [frames - 10, frames], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.in(Easing.cubic),
  });
  const y = (1 - inY) * -(safe.titleTop + 260) + outY * -(safe.titleTop + 320);
  const fontSize = Math.round(Math.min(width, height) * 0.058);
  return (
    <AbsoluteFill style={{alignItems: 'center', pointerEvents: 'none'}}>
      <div
        style={{
          position: 'absolute',
          top: safe.titleTop,
          transform: `translateY(${y}px)`,
          maxWidth: width - safe.side * 2,
          backgroundColor: `${bg}e6`,
          borderLeft: `${Math.round(fontSize * 0.22)}px solid ${accent}`,
          borderRadius: Math.round(fontSize * 0.35),
          padding: `${Math.round(fontSize * 0.42)}px ${Math.round(fontSize * 0.7)}px`,
          fontFamily: fontStack(font),
          fontWeight: 800,
          fontSize,
          lineHeight: 1.15,
          color: '#ffffff',
          textAlign: 'center',
          boxShadow: '0 18px 50px rgba(0,0,0,0.45)',
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

// full-screen title card (title.durSec > 0 — python already shifted the timeline)
const TitleCard: React.FC<{
  text: string;
  frames: number;
  accent: string;
  bg: string;
  font?: string;
}> = ({text, frames, accent, bg, font}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const inS = spring({frame, fps, config: {damping: 15, stiffness: 160, mass: 0.9}});
  const outY = interpolate(frame, [frames - 10, frames], [0, -height], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.in(Easing.cubic),
  });
  const fontSize = Math.round(Math.min(width, height) * 0.085);
  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg,
        justifyContent: 'center',
        alignItems: 'center',
        transform: `translateY(${outY}px)`,
      }}
    >
      <AccentGlow accent={accent} />
      <div
        style={{
          maxWidth: width * 0.84,
          transform: `translateY(${(1 - inS) * 60}px) scale(${0.9 + 0.1 * inS})`,
          opacity: inS,
          fontFamily: fontStack(font),
          fontWeight: 800,
          fontSize,
          lineHeight: 1.1,
          color: '#ffffff',
          textAlign: 'center',
          textWrap: 'balance' as never,
        }}
      >
        {text}
      </div>
      <div
        style={{
          marginTop: Math.round(fontSize * 0.5),
          width: Math.round(fontSize * 1.6) * inS,
          height: Math.max(6, Math.round(fontSize * 0.09)),
          borderRadius: 6,
          backgroundColor: accent,
        }}
      />
    </AbsoluteFill>
  );
};

const AccentGlow: React.FC<{accent: string}> = ({accent}) => (
  <>
    <div
      style={{
        position: 'absolute',
        width: '90%',
        aspectRatio: '1 / 1',
        borderRadius: '50%',
        top: '-30%',
        right: '-35%',
        background: `radial-gradient(circle, ${accent}55 0%, ${accent}00 65%)`,
      }}
    />
    <div
      style={{
        position: 'absolute',
        width: '70%',
        aspectRatio: '1 / 1',
        borderRadius: '50%',
        bottom: '-25%',
        left: '-30%',
        background: `radial-gradient(circle, ${accent}33 0%, ${accent}00 65%)`,
      }}
    />
  </>
);

// ---------- outro ----------

const Outro: React.FC<{
  text: string;
  sub?: string;
  accent: string;
  bg: string;
  font?: string;
}> = ({text, sub, accent, bg, font}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const inS = spring({frame, fps, config: {damping: 14, stiffness: 170, mass: 0.8}});
  const subS = spring({frame: frame - 5, fps, config: {damping: 14, stiffness: 170, mass: 0.8}});
  const bar = spring({frame: frame - 3, fps, config: {damping: 18, stiffness: 120, mass: 1}});
  const fontSize = Math.round(Math.min(width, height) * 0.095);
  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg,
        justifyContent: 'center',
        alignItems: 'center',
        opacity: interpolate(frame, [0, 8], [0, 1], {extrapolateRight: 'clamp'}),
      }}
    >
      <AccentGlow accent={accent} />
      <div
        style={{
          maxWidth: width * 0.84,
          transform: `translateY(${(1 - inS) * 70}px) scale(${0.88 + 0.12 * inS})`,
          opacity: inS,
          fontFamily: fontStack(font),
          fontWeight: 800,
          fontSize,
          lineHeight: 1.08,
          color: '#ffffff',
          textAlign: 'center',
          textWrap: 'balance' as never,
        }}
      >
        {text}
      </div>
      <div
        style={{
          marginTop: Math.round(fontSize * 0.45),
          width: Math.round(fontSize * 1.8) * bar,
          height: Math.max(6, Math.round(fontSize * 0.09)),
          borderRadius: 6,
          backgroundColor: accent,
        }}
      />
      {sub ? (
        <div
          style={{
            marginTop: Math.round(fontSize * 0.4),
            transform: `translateY(${(1 - subS) * 40}px)`,
            opacity: subS,
            fontFamily: fontStack(font),
            fontWeight: 600,
            fontSize: Math.round(fontSize * 0.46),
            color: accent,
            textAlign: 'center',
            letterSpacing: '0.02em',
          }}
        >
          {sub}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

// ---------- chrome ----------

const ProgressBar: React.FC<{accent: string; totalFrames: number}> = ({accent, totalFrames}) => {
  const frame = useCurrentFrame();
  const {height} = useVideoConfig();
  const p = Math.min(1, frame / Math.max(1, totalFrames - 1));
  const h = Math.max(6, Math.round(height * 0.004));
  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        height: h,
        width: `${p * 100}%`,
        backgroundColor: accent,
        boxShadow: `0 0 12px ${accent}99`,
      }}
    />
  );
};

const Watermark: React.FC<{text: string; safe: SafeZone; font?: string}> = ({text, safe, font}) => {
  const {width, height} = useVideoConfig();
  const fontSize = Math.round(Math.min(width, height) * 0.024);
  return (
    <div
      style={{
        position: 'absolute',
        right: safe.side,
        bottom: Math.round(height * 0.028),
        fontFamily: fontStack(font),
        fontWeight: 600,
        fontSize,
        color: 'rgba(255,255,255,0.55)',
        textShadow: '0 1px 4px rgba(0,0,0,0.6)',
        letterSpacing: '0.02em',
        pointerEvents: 'none',
      }}
    >
      {text}
    </div>
  );
};

// ---------- composition ----------

export const Video: React.FC<VideoProps> = (props) => {
  const {fps, width, height} = useVideoConfig();
  const theme = {...DEFAULT_THEME, ...(props.theme ?? {})};
  const safe = safeZone(props.aspect, width, height);
  const totalFrames = Math.ceil(props.durationSec * fps);
  const transition = props.transition ?? 'cut';

  const outroDur = props.outro ? (props.outro.durSec ?? 2.5) : 0;
  const outroFrames = Math.round(outroDur * fps);
  const outroFrom = Math.max(0, totalFrames - outroFrames);

  const titleDur = props.title?.durSec ?? 0;
  const titleOverlayFrames = Math.round(2.5 * fps);
  const titleCardFrames = Math.round(titleDur * fps);

  return (
    <AbsoluteFill style={{backgroundColor: theme.bg}}>
      <Scenes scenes={props.scenes} transition={transition} durationSec={props.durationSec} />

      {props.captions?.enabled !== false && props.words?.length ? (
        <Captions
          words={props.words}
          captions={props.captions ?? {enabled: true}}
          aspect={props.aspect}
          safe={safe}
          font={theme.font}
        />
      ) : null}

      {props.title?.text && titleDur === 0 ? (
        <Sequence from={0} durationInFrames={titleOverlayFrames} layout="none">
          <TitleOverlay
            text={props.title.text}
            frames={titleOverlayFrames}
            safe={safe}
            accent={theme.accent}
            bg={theme.bg}
            font={theme.font}
          />
        </Sequence>
      ) : null}

      {props.title?.text && titleDur > 0 ? (
        <Sequence from={0} durationInFrames={titleCardFrames} layout="none">
          <TitleCard
            text={props.title.text}
            frames={titleCardFrames}
            accent={theme.accent}
            bg={theme.bg}
            font={theme.font}
          />
        </Sequence>
      ) : null}

      {props.outro?.text && outroFrames > 0 ? (
        <Sequence from={outroFrom} durationInFrames={outroFrames} layout="none">
          <Outro
            text={props.outro.text}
            sub={props.outro.sub}
            accent={theme.accent}
            bg={theme.bg}
            font={theme.font}
          />
        </Sequence>
      ) : null}

      {props.watermark?.text ? (
        <Watermark text={props.watermark.text} safe={safe} font={theme.font} />
      ) : null}

      {props.progressBar ? <ProgressBar accent={theme.accent} totalFrames={totalFrames} /> : null}
    </AbsoluteFill>
  );
};
