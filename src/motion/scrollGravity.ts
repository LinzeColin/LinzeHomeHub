import type { PointerState, ScrollGravityState } from '../types';

type Listener = (state: ScrollGravityState) => void;

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
const lerp = (from: number, to: number, amount: number) => from + (to - from) * amount;

export class ScrollGravityController {
  readonly pointer: PointerState = {
    x: window.innerWidth / 2,
    y: window.innerHeight / 2,
    active: false
  };

  state: ScrollGravityState = {
    direction: 'idle',
    velocityY: 0,
    velocityX: 0,
    gravityX: 0,
    gravityY: 0,
    energy: 0,
    spaceActive: false
  };

  private lastScrollY = window.scrollY;
  private lastTime = performance.now();
  private wheelBoostX = 0;
  private wheelBoostY = 0;
  private targetX = 0;
  private targetY = 0;
  private listeners = new Set<Listener>();

  constructor(private readonly reducedMotion: boolean) {}

  start(): void {
    window.addEventListener('scroll', this.handleScroll, { passive: true });
    window.addEventListener('wheel', this.handleWheel, { passive: true });
    window.addEventListener('pointermove', this.handlePointerMove, { passive: true });
    window.addEventListener('pointerleave', this.handlePointerLeave, { passive: true });
  }

  dispose(): void {
    window.removeEventListener('scroll', this.handleScroll);
    window.removeEventListener('wheel', this.handleWheel);
    window.removeEventListener('pointermove', this.handlePointerMove);
    window.removeEventListener('pointerleave', this.handlePointerLeave);
    this.listeners.clear();
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setSpaceActive(active: boolean): void {
    this.state.spaceActive = active;
    if (active) this.state.energy = Math.min(2, this.state.energy + 0.35);
  }

  tick(now = performance.now()): ScrollGravityState {
    const dy = window.scrollY - this.lastScrollY;
    const dt = Math.max(16, now - this.lastTime);
    const rawY = dy / dt;
    const normalizedY = clamp(rawY * 9, -2.5, 2.5);
    const pointerBiasX = ((this.pointer.x / Math.max(1, window.innerWidth)) - 0.5) * 0.7;
    const normalizedX = clamp(pointerBiasX + this.wheelBoostX, -1.2, 1.2);

    this.lastScrollY = window.scrollY;
    this.lastTime = now;
    this.targetY = lerp(this.targetY, normalizedY, 0.22) + this.wheelBoostY;
    this.targetX = lerp(this.targetX, normalizedX, 0.08);
    this.wheelBoostX *= 0.86;
    this.wheelBoostY *= 0.86;

    const motionScale = this.reducedMotion ? 0.18 : 1;
    this.state.gravityX = lerp(this.state.gravityX, clamp(this.targetX, -1.2, 1.2), 0.06) * motionScale;
    this.state.gravityY = lerp(this.state.gravityY, clamp(this.targetY, -1.8, 1.8), 0.06) * motionScale;
    this.state.velocityY = lerp(this.state.velocityY, normalizedY, 0.2);
    this.state.velocityX = lerp(this.state.velocityX, normalizedX, 0.2);
    this.state.energy = Math.max(
      this.state.spaceActive ? 0.9 : 0,
      lerp(this.state.energy, Math.abs(normalizedY) + Math.abs(this.wheelBoostX), this.state.spaceActive ? 0.18 : -0.045)
    );
    this.state.energy = clamp(this.state.energy * (this.state.spaceActive ? 1 : 0.965), 0, 2.2);

    const absY = Math.abs(this.state.velocityY);
    const absX = Math.abs(this.state.velocityX);
    if (absX > 0.65 && absX > absY) this.state.direction = 'side';
    else if (absY < 0.035) this.state.direction = 'idle';
    else this.state.direction = this.state.velocityY > 0 ? 'down' : 'up';

    this.listeners.forEach((listener) => listener(this.state));
    return this.state;
  }

  private handleScroll = () => {
    this.state.energy = Math.min(1.8, this.state.energy + 0.03);
  };

  private handleWheel = (event: WheelEvent) => {
    this.wheelBoostY += clamp(event.deltaY * 0.0018, -1.4, 1.4);
    this.wheelBoostX += clamp(event.deltaX * 0.0018, -1.2, 1.2);
    this.state.energy = Math.min(2.1, this.state.energy + Math.abs(event.deltaY) * 0.0007);
  };

  private handlePointerMove = (event: PointerEvent) => {
    this.pointer.x = event.clientX;
    this.pointer.y = event.clientY;
    this.pointer.active = true;
    document.documentElement.style.setProperty('--mouse-x', `${((event.clientX / window.innerWidth) * 100).toFixed(2)}%`);
    document.documentElement.style.setProperty('--mouse-y', `${((event.clientY / window.innerHeight) * 100).toFixed(2)}%`);
  };

  private handlePointerLeave = () => {
    this.pointer.active = false;
  };
}
