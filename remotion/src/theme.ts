// Types + visual constants for the MoneyPrinterCannon renderer.
// The props shape mirrors CONTRACT.md → "Remotion props.json" literally.

export type Aspect = '9:16' | '16:9' | '1:1';
export type KenBurns = 'in' | 'out' | 'left' | 'right' | 'none';
export type Transition = 'cut' | 'fade' | 'slide' | 'zoom';
export type CaptionPreset = 'karaoke' | 'bold' | 'clean' | 'minimal';
export type CaptionPosition = 'bottom' | 'center' | 'top';

export type Scene = {
  index: number;
  start: number; // absolute seconds on the final timeline
  end: number;
  kind: 'video' | 'image';
  src: string; // relative to remotion/public (staticFile) or http(s) URL
  fit?: 'cover' | 'contain';
  kenBurns?: KenBurns;
  loop?: boolean;
  srcDurationSec?: number;
};

export type Word = {s: number; e: number; w: string};

export type CaptionsProps = {
  enabled: boolean;
  preset?: CaptionPreset;
  position?: CaptionPosition;
  fontSize?: number;
  maxWords?: number;
  uppercase?: boolean;
  accent?: string;
  emphasis?: string;
  textColor?: string;
  strokeColor?: string;
};

export type TitleProps = {text: string; durSec?: number} | null;
export type OutroProps = {text: string; sub?: string; durSec?: number} | null;
export type WatermarkProps = {text: string} | null;

export type Theme = {accent?: string; bg?: string; font?: string};

export type VideoProps = {
  aspect: Aspect;
  width: number;
  height: number;
  fps: number;
  durationSec: number;
  scenes: Scene[];
  words: Word[];
  captions: CaptionsProps;
  title?: TitleProps;
  outro?: OutroProps;
  transition?: Transition;
  progressBar?: boolean;
  watermark?: WatermarkProps;
  theme?: Theme;
};

// ---------- defaults ----------

export const DEFAULT_THEME = {
  accent: '#10b981',
  bg: '#0a0e0c',
  font: 'Onest',
};

export const DEFAULT_CAPTIONS = {
  preset: 'karaoke' as CaptionPreset,
  position: 'bottom' as CaptionPosition,
  maxWords: 3,
  uppercase: true,
  accent: '#10b981',
  emphasis: '#fbbf24',
  textColor: '#ffffff',
  strokeColor: '#0a0e0c',
};

export const TRANSITION_FRAMES = 12; // crossfade / slide / zoom length

// Default caption font size per aspect (px at native resolution)
export const captionFontSize = (aspect: Aspect): number => {
  switch (aspect) {
    case '16:9':
      return 64;
    case '1:1':
      return 66;
    default:
      return 78;
  }
};

// Safe zones. For 9:16 the caption block sits between y≈1180 and y≈1590
// (marginBottom 330 like Anton's reel template), clear of platform UI.
export type SafeZone = {
  marginBottom: number; // captions "bottom"
  marginTop: number; // captions "top" (below the title plate)
  maxWidth: number; // caption block width
  titleTop: number; // title overlay plate y
  side: number; // horizontal padding
};

export const safeZone = (aspect: Aspect, width: number, height: number): SafeZone => {
  switch (aspect) {
    case '16:9':
      return {
        marginBottom: Math.round(height * 0.09), // 97 @1080
        marginTop: Math.round(height * 0.2),
        maxWidth: Math.round(width * 0.8),
        titleTop: Math.round(height * 0.07),
        side: Math.round(width * 0.04),
      };
    case '1:1':
      return {
        marginBottom: Math.round(height * 0.12), // 130 @1080
        marginTop: Math.round(height * 0.22),
        maxWidth: Math.round(width * 0.88),
        titleTop: Math.round(height * 0.07),
        side: Math.round(width * 0.05),
      };
    default:
      return {
        marginBottom: 330,
        marginTop: 400,
        maxWidth: 980,
        titleTop: 150,
        side: 50,
      };
  }
};

// Fallback stack: real Onest when it loaded, otherwise the platform sans.
export const fontStack = (font?: string) =>
  `"${font || DEFAULT_THEME.font}", Onest, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif`;
