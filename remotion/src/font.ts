// Onest 600/800 loader that never kills a render.
//
// @remotion/google-fonts' loadFont() keeps a delayRender() handle open until the
// woff2 arrives; with no network the handle times out (60 s) and the render fails.
// So we take the font *metadata* from the package (URLs + unicode ranges) but do
// the loading ourselves: local copy in public/fonts first, Google CDN second,
// and on any failure we continueRender() and fall back to system-ui.

import {continueRender, delayRender, staticFile} from 'remotion';
import {getInfo} from '@remotion/google-fonts/Onest';

export const FONT_FAMILY = 'Onest';

type Subset = 'latin' | 'cyrillic' | 'latin-ext' | 'cyrillic-ext';
const SUBSETS: Subset[] = ['latin', 'cyrillic'];
const LOCAL: Record<string, string> = {
  latin: 'fonts/onest-latin.woff2',
  cyrillic: 'fonts/onest-cyrillic.woff2',
};

let started = false;

const loadOne = async (subset: Subset): Promise<boolean> => {
  const info = getInfo();
  // Onest is a variable font: the same file serves every weight.
  const remote = info.fonts.normal['800']?.[subset] ?? info.fonts.normal['600']?.[subset];
  const candidates = [LOCAL[subset] ? staticFile(LOCAL[subset]) : null, remote].filter(
    Boolean,
  ) as string[];
  const range = info.unicodeRanges[subset];
  for (const url of candidates) {
    try {
      const face = new FontFace(FONT_FAMILY, `url(${url}) format('woff2')`, {
        weight: '100 900',
        style: 'normal',
        unicodeRange: range,
      });
      await face.load();
      (document.fonts as unknown as {add: (f: FontFace) => void}).add(face);
      return true;
    } catch {
      // try the next candidate
    }
  }
  return false;
};

export const ensureFont = (): void => {
  if (started) return;
  started = true;
  if (typeof window === 'undefined' || typeof FontFace === 'undefined') return;
  const handle = delayRender('Loading Onest', {timeoutInMilliseconds: 25000});
  const timer = setTimeout(() => {
    // Hard cap: whatever happened, do not block the render.
    continueRender(handle);
  }, 20000);
  Promise.all(SUBSETS.map(loadOne))
    .then((ok) => {
      if (!ok.some(Boolean)) {
        // eslint-disable-next-line no-console
        console.warn('[moneyprintercannon] Onest could not be loaded, falling back to system-ui');
      }
    })
    .catch(() => undefined)
    .finally(() => {
      clearTimeout(timer);
      continueRender(handle);
    });
};

try {
  ensureFont();
} catch {
  // never let font loading crash the bundle
}
