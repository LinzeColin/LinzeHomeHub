import type { QualityName } from '../types';

export type QualityProfile = {
  name: QualityName;
  particleCount: number;
  physicsBodies: number;
  pixelRatio: number;
  bloom: boolean;
  reducedMotion: boolean;
};

const isQualityName = (value: string | null): value is QualityName =>
  value === 'low' || value === 'medium' || value === 'ultra';

export function detectQualityProfile(): QualityProfile {
  const params = new URLSearchParams(window.location.search);
  const forced = params.get('quality'); // supports ?quality=low|medium|ultra
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cores = navigator.hardwareConcurrency ?? 4;
  const narrow = window.innerWidth < 760;
  const dpr = window.devicePixelRatio || 1;

  let name: QualityName = 'medium';
  if (isQualityName(forced)) {
    name = forced;
  } else if (reducedMotion || narrow || cores <= 4) {
    name = 'low';
  } else if (cores >= 8 && dpr <= 2 && window.innerWidth >= 1180) {
    name = 'ultra';
  }

  if (name === 'low') {
    return { name, particleCount: 700, physicsBodies: 6, pixelRatio: 1, bloom: false, reducedMotion };
  }
  if (name === 'ultra') {
    return { name, particleCount: 3200, physicsBodies: 16, pixelRatio: Math.min(dpr, 2), bloom: !reducedMotion, reducedMotion };
  }
  return { name, particleCount: 1500, physicsBodies: 10, pixelRatio: Math.min(dpr, 1.5), bloom: !reducedMotion, reducedMotion };
}
