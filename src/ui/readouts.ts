import type { ScrollGravityState } from '../types';

const setText = (id: string, value: string) => {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
};

export function updateReadouts(state: ScrollGravityState): void {
  const max = document.documentElement.scrollHeight - window.innerHeight;
  const progress = max <= 0 ? 0 : Math.max(0, Math.min(1, window.scrollY / max));
  const root = document.documentElement;
  root.style.setProperty('--scroll-progress', `${(progress * 100).toFixed(2)}%`);
  root.style.setProperty('--scroll-shift', `${(Math.sin(progress * Math.PI * 2) * 24).toFixed(2)}px`);
  root.style.setProperty('--artifact-lift', `${(-state.energy * 7).toFixed(2)}px`);
  root.style.setProperty('--voyage-sweep', `${(progress * 360 + state.gravityX * 24).toFixed(2)}deg`);
  const angle = Math.atan2(state.gravityX, -state.gravityY || 0.0001) * 180 / Math.PI;
  root.style.setProperty('--gravity-angle', `${angle.toFixed(2)}deg`);

  setText('gDir', state.direction);
  setText('gVel', Math.max(Math.abs(state.velocityY), Math.abs(state.velocityX)).toFixed(2));
  setText('gX', state.gravityX.toFixed(2));
  setText('gY', state.gravityY.toFixed(2));
}
