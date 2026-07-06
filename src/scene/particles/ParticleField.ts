import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Color,
  Points,
  PointsMaterial
} from 'three';
import type { PointerState, ScrollGravityState } from '../../types';
import type { ModeSystem } from '../systems/modeSystems';

type Particle = {
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  vz: number;
  mass: number;
  seed: number;
};

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export class ParticleField {
  readonly points: Points;
  private readonly particles: Particle[] = [];
  private readonly positions: Float32Array;
  private readonly colors: Float32Array;
  private readonly geometry: BufferGeometry;
  private readonly material: PointsMaterial;
  private currentSystem: ModeSystem;
  private shockwave = 0;

  constructor(count: number, system: ModeSystem) {
    this.currentSystem = system;
    this.positions = new Float32Array(count * 3);
    this.colors = new Float32Array(count * 3);
    this.geometry = new BufferGeometry();
    this.material = new PointsMaterial({
      size: count > 2000 ? 0.018 : 0.024,
      vertexColors: true,
      transparent: true,
      opacity: 0.82,
      depthWrite: false,
      blending: AdditiveBlending
    });

    for (let i = 0; i < count; i += 1) {
      const radius = Math.sqrt(Math.random()) * 4.4;
      const angle = Math.random() * Math.PI * 2;
      const z = (Math.random() - 0.5) * 3.2;
      this.particles.push({
        x: Math.cos(angle) * radius + (Math.random() - 0.5) * 2.5,
        y: Math.sin(angle) * radius + (Math.random() - 0.5) * 1.8,
        z,
        vx: (Math.random() - 0.5) * 0.002,
        vy: (Math.random() - 0.5) * 0.002,
        vz: (Math.random() - 0.5) * 0.002,
        mass: 0.5 + Math.random() * 1.8,
        seed: Math.random()
      });
    }

    this.writeBuffers();
    this.geometry.setAttribute('position', new BufferAttribute(this.positions, 3));
    this.geometry.setAttribute('color', new BufferAttribute(this.colors, 3));
    this.points = new Points(this.geometry, this.material);
    this.points.frustumCulled = false;
  }

  setMode(system: ModeSystem): void {
    this.currentSystem = system;
    this.shockwave = Math.max(this.shockwave, 0.55);
  }

  pulse(amount = 1): void {
    this.shockwave = Math.min(2.4, this.shockwave + amount);
  }

  update(time: number, gravity: ScrollGravityState, pointer: PointerState): void {
    const system = this.currentSystem;
    const t = time * 0.001;
    const pointerX = ((pointer.x / window.innerWidth) - 0.5) * 7;
    const pointerY = -((pointer.y / window.innerHeight) - 0.5) * 4.4;
    const modePull = gravity.spaceActive ? 0.050 : pointer.active ? 0.008 : 0;

    for (let i = 0; i < this.particles.length; i += 1) {
      const p = this.particles[i];
      const orbit = Math.sqrt(p.x * p.x + p.y * p.y) + 0.01;
      const swirl = system.particleTurbulence * (0.35 + p.seed);
      let ax = gravity.gravityX * system.particleGravity * 0.008 * p.mass + system.particleDriftX * 0.002;
      let ay = -gravity.gravityY * system.particleGravity * 0.010 * p.mass + system.pollenLift * 0.002;
      let az = Math.sin(t + p.seed * 8) * swirl * 0.003;

      if (system.id === 'archive') {
        ax += (-p.y / orbit) * 0.0009;
        ay += (p.x / orbit) * 0.0009;
      } else if (system.id === 'nebula') {
        ax += Math.sin(t * 0.7 + p.seed * 9 + p.y) * swirl * 0.002;
        ay += Math.cos(t * 0.6 + p.seed * 7 + p.x) * swirl * 0.002;
        az += -p.z * 0.0007;
      } else if (system.id === 'voyage') {
        ax += 0.0018 + Math.sin(t + p.seed * 4) * 0.0008;
        ay += Math.cos(t * 0.6 + p.x) * 0.0009;
      } else if (system.id === 'garden') {
        ay += Math.sin(t + p.seed * 9) * 0.0012 + 0.0012;
        ax += Math.sin(p.y * 2 + t) * 0.001;
      }

      if (modePull > 0) {
        const dx = pointerX - p.x;
        const dy = pointerY - p.y;
        const d = Math.sqrt(dx * dx + dy * dy + p.z * p.z) + 0.18;
        const pull = modePull / d;
        ax += (dx / d) * pull;
        ay += (dy / d) * pull;
        az += (-p.z / d) * pull;
      }

      if (this.shockwave > 0.01) {
        const d = orbit + Math.abs(p.z) + 0.2;
        ax += (p.x / d) * this.shockwave * 0.0024;
        ay += (p.y / d) * this.shockwave * 0.0024;
        az += (p.z / d) * this.shockwave * 0.002;
      }

      p.vx = (p.vx + ax) * 0.982;
      p.vy = (p.vy + ay) * 0.982;
      p.vz = (p.vz + az) * 0.982;
      p.x += p.vx;
      p.y += p.vy;
      p.z += p.vz;

      if (Math.abs(p.x) > 6) p.x *= -0.92;
      if (Math.abs(p.y) > 4) p.y *= -0.92;
      if (Math.abs(p.z) > 3.5) p.z *= -0.92;
    }

    this.shockwave *= 0.94;
    this.writeBuffers();
    this.geometry.attributes.position.needsUpdate = true;
    this.geometry.attributes.color.needsUpdate = true;
  }

  dispose(): void {
    this.geometry.dispose();
    this.material.dispose();
  }

  private writeBuffers(): void {
    const accent = new Color(this.currentSystem.accent);
    const secondary = new Color(this.currentSystem.secondary);
    const rose = new Color(0xf5a3b7);

    for (let i = 0; i < this.particles.length; i += 1) {
      const p = this.particles[i];
      const idx = i * 3;
      this.positions[idx] = p.x;
      this.positions[idx + 1] = p.y;
      this.positions[idx + 2] = p.z;
      const mix = p.seed < 0.52 ? accent : p.seed < 0.82 ? secondary : rose;
      const energy = clamp(0.55 + Math.sin(p.seed * 10) * 0.12 + this.shockwave * 0.12, 0.35, 1);
      this.colors[idx] = mix.r * energy;
      this.colors[idx + 1] = mix.g * energy;
      this.colors[idx + 2] = mix.b * energy;
    }
  }
}
