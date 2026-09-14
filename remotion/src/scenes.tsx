import React from 'react';
import {
  AbsoluteFill,
  Easing,
  Img,
  Loop,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {KenBurns, Scene, TRANSITION_FRAMES, Transition} from './theme';

const resolveSrc = (src: string) =>
  /^(https?:)?\/\//.test(src) || src.startsWith('data:') ? src : staticFile(src);

// ---------- Ken Burns ----------

const KB_SCALE = 1.12; // slow push
const KB_SHIFT = 0.035; // fraction of width for left/right pans

const kenBurnsTransform = (kind: KenBurns, p: number, width: number): string => {
  switch (kind) {
    case 'in':
      return `scale(${1 + (KB_SCALE - 1) * p})`;
    case 'out':
      return `scale(${KB_SCALE - (KB_SCALE - 1) * p})`;
    case 'left':
      // pan to the left: content drifts from +shift to -shift
      return `scale(${KB_SCALE}) translateX(${(KB_SHIFT - 2 * KB_SHIFT * p) * width}px)`;
    case 'right':
      return `scale(${KB_SCALE}) translateX(${(-KB_SHIFT + 2 * KB_SHIFT * p) * width}px)`;
    default:
      return 'none';
  }
};

// ---------- single scene ----------

const SceneMedia: React.FC<{scene: Scene; lengthFrames: number}> = ({scene, lengthFrames}) => {
  const frame = useCurrentFrame(); // local to the Sequence
  const {fps, width} = useVideoConfig();
  const p = interpolate(frame, [0, Math.max(1, lengthFrames)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const fit = scene.fit ?? 'cover';
  const kb = scene.kenBurns ?? 'none';
  const src = resolveSrc(scene.src);

  const mediaStyle: React.CSSProperties = {
    position: 'absolute',
    inset: 0,
    width: '100%',
    height: '100%',
    objectFit: fit,
  };

  if (scene.kind === 'image') {
    return (
      <AbsoluteFill style={{overflow: 'hidden'}}>
        <Img
          src={src}
          style={{
            ...mediaStyle,
            transform: kenBurnsTransform(kb, p, width),
            transformOrigin: 'center center',
          }}
        />
      </AbsoluteFill>
    );
  }

  // video: gentle "breathing" zoom when kenBurns is "in"
  const breathe = kb === 'in' ? 1 + 0.05 * p : 1;
  const sceneLen = scene.end - scene.start;
  const srcDur = scene.srcDurationSec ?? 0;
  const needsLoop = Boolean(scene.loop) && srcDur > 0 && srcDur < sceneLen;

  const video = (
    <OffthreadVideo
      src={src}
      muted
      pauseWhenBuffering={false}
      onError={(e) => {
        // eslint-disable-next-line no-console
        console.warn('[cashcannon] video failed', scene.src, e);
      }}
      style={mediaStyle}
    />
  );

  return (
    <AbsoluteFill
      style={{
        overflow: 'hidden',
        transform: `scale(${breathe})`,
        transformOrigin: 'center center',
      }}
    >
      {needsLoop ? (
        <Loop durationInFrames={Math.max(1, Math.floor(srcDur * fps))} layout="none">
          {video}
        </Loop>
      ) : (
        video
      )}
    </AbsoluteFill>
  );
};

// ---------- transition wrapper (incoming scene only) ----------

const TransitionIn: React.FC<{
  transition: Transition;
  isFirst: boolean;
  children: React.ReactNode;
}> = ({transition, isFirst, children}) => {
  const frame = useCurrentFrame();
  const {width} = useVideoConfig();
  if (isFirst || transition === 'cut') {
    return <AbsoluteFill>{children}</AbsoluteFill>;
  }
  const t = interpolate(frame, [0, TRANSITION_FRAMES], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });
  let style: React.CSSProperties = {};
  if (transition === 'fade') {
    style = {opacity: t};
  } else if (transition === 'slide') {
    style = {transform: `translateX(${(1 - t) * width}px)`};
  } else if (transition === 'zoom') {
    style = {opacity: t, transform: `scale(${1.25 - 0.25 * t})`};
  }
  return <AbsoluteFill style={style}>{children}</AbsoluteFill>;
};

// ---------- scene layer ----------

export const Scenes: React.FC<{
  scenes: Scene[];
  transition: Transition;
  durationSec: number;
}> = ({scenes, transition, durationSec}) => {
  const {fps} = useVideoConfig();
  const sorted = [...scenes].sort((a, b) => a.start - b.start);
  const totalFrames = Math.ceil(durationSec * fps);
  return (
    <AbsoluteFill>
      {sorted.map((scene, i) => {
        const from = Math.round(scene.start * fps);
        const isLast = i === sorted.length - 1;
        // Last scene is stretched to the end so nothing shows the bare bg
        // (the outro card covers it anyway when present).
        const endFrame = isLast ? Math.max(Math.round(scene.end * fps), totalFrames) : Math.round(scene.end * fps);
        // Previous scene stays under the incoming one for the transition length.
        const tail = transition === 'cut' || isLast ? 0 : TRANSITION_FRAMES;
        const len = Math.max(1, endFrame - from + tail);
        return (
          <Sequence key={scene.index ?? i} from={from} durationInFrames={len} layout="none">
            <TransitionIn transition={transition} isFirst={i === 0}>
              <SceneMedia scene={scene} lengthFrames={len} />
            </TransitionIn>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
