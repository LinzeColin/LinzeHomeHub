import RAPIER from '@dimforge/rapier3d-compat';
import type { QualityProfile } from '../app/qualityProfile';
import type { PointerState, ScrollGravityState } from '../types';

type LabBody = {
  el: HTMLDivElement;
  body: RAPIER.RigidBody;
  radius: number;
  color: string;
};

const colors = ['#e9c46a', '#7bd5ff', '#b8a5ff', '#9de6b8', '#f5a3b7'];

export class PhysicsWorld {
  private world?: RAPIER.World;
  private bodies: LabBody[] = [];
  private bounds = { width: 1, height: 1 };

  constructor(
    private readonly lab: HTMLElement,
    private readonly lines: SVGSVGElement,
    private readonly quality: QualityProfile
  ) {}

  async init(): Promise<void> {
    await RAPIER.init();
    this.world = new RAPIER.World({ x: 0, y: -0.6, z: 0 });
    this.createBodies();
  }

  resize(): void {
    const rect = this.lab.getBoundingClientRect();
    this.bounds = { width: Math.max(1, rect.width), height: Math.max(1, rect.height) };
    this.lines.setAttribute('viewBox', `0 0 ${this.bounds.width} ${this.bounds.height}`);
  }

  update(gravity: ScrollGravityState, pointer: PointerState): void {
    if (!this.world) return;
    this.resize();
    const world = this.world as RAPIER.World & { gravity: { x: number; y: number; z: number } };
    world.gravity = { x: gravity.gravityX * 2.6, y: -0.45 - gravity.gravityY * 2.8, z: 0 };

    const rect = this.lab.getBoundingClientRect();
    const inside = pointer.x >= rect.left && pointer.x <= rect.right && pointer.y >= rect.top && pointer.y <= rect.bottom;
    const localX = pointer.x - rect.left;
    const localY = pointer.y - rect.top;

    for (const item of this.bodies) {
      if (inside || gravity.spaceActive) {
        const pos = item.body.translation();
        const dx = localX - pos.x;
        const dy = localY - pos.y;
        const d = Math.sqrt(dx * dx + dy * dy) + 40;
        const pull = gravity.spaceActive ? 12 / d : 3.5 / d;
        item.body.applyImpulse({ x: dx * pull, y: dy * pull, z: 0 }, true);
      }
      item.body.applyImpulse({ x: gravity.gravityX * 0.18, y: -gravity.gravityY * 0.20, z: 0 }, true);
    }

    this.world.step();
    this.resolveBounds();
    this.renderBodies();
  }

  dispose(): void {
    this.bodies.forEach(({ el }) => el.remove());
    this.bodies = [];
    this.lines.innerHTML = '';
  }

  private createBodies(): void {
    if (!this.world) return;
    this.resize();
    this.dispose();
    const count = this.quality.physicsBodies;
    for (let i = 0; i < count; i += 1) {
      const size = 28 + Math.random() * 42;
      const radius = size / 2;
      const el = document.createElement('div');
      el.className = 'gravity-body';
      const color = colors[i % colors.length];
      el.style.setProperty('--s', `${size}px`);
      el.style.setProperty('--c', color);
      this.lab.appendChild(el);

      const bodyDesc = RAPIER.RigidBodyDesc.dynamic()
        .setTranslation(
          radius + Math.random() * Math.max(120, this.bounds.width - radius * 2),
          150 + Math.random() * Math.max(120, this.bounds.height - 250),
          0
        )
        .setLinvel((Math.random() - 0.5) * 2, (Math.random() - 0.5) * 2, 0)
        .setLinearDamping(0.75)
        .setAngularDamping(0.85);
      const body = this.world.createRigidBody(bodyDesc);
      const colliderDesc = RAPIER.ColliderDesc.ball(radius).setRestitution(0.82).setDensity(0.2);
      this.world.createCollider(colliderDesc, body);
      this.bodies.push({ el, body, radius, color });
    }
  }

  private resolveBounds(): void {
    for (const item of this.bodies) {
      const pos = item.body.translation();
      const vel = item.body.linvel();
      let x = pos.x;
      let y = pos.y;
      let vx = vel.x;
      let vy = vel.y;

      if (x < item.radius) {
        x = item.radius;
        vx = Math.abs(vx) * 0.78;
      } else if (x > this.bounds.width - item.radius) {
        x = this.bounds.width - item.radius;
        vx = -Math.abs(vx) * 0.78;
      }
      if (y < item.radius) {
        y = item.radius;
        vy = Math.abs(vy) * 0.78;
      } else if (y > this.bounds.height - item.radius) {
        y = this.bounds.height - item.radius;
        vy = -Math.abs(vy) * 0.78;
      }
      item.body.setTranslation({ x, y, z: 0 }, true);
      item.body.setLinvel({ x: vx, y: vy, z: 0 }, true);
    }
  }

  private renderBodies(): void {
    let lineMarkup = '';
    for (let i = 0; i < this.bodies.length; i += 1) {
      const a = this.bodies[i];
      const ap = a.body.translation();
      a.el.style.transform = `translate3d(${(ap.x - a.radius).toFixed(1)}px, ${(ap.y - a.radius).toFixed(1)}px, 0)`;

      for (let j = i + 1; j < this.bodies.length; j += 1) {
        const b = this.bodies[j];
        const bp = b.body.translation();
        const dx = bp.x - ap.x;
        const dy = bp.y - ap.y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < 170) {
          lineMarkup += `<line x1="${ap.x.toFixed(1)}" y1="${ap.y.toFixed(1)}" x2="${bp.x.toFixed(1)}" y2="${bp.y.toFixed(1)}" stroke="rgba(233,196,106,${((1 - d / 170) * 0.28).toFixed(3)})" stroke-width="1"/>`;
        }
      }
    }
    this.lines.innerHTML = lineMarkup;
  }
}
