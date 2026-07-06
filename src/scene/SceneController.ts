import {
  AmbientLight,
  Color,
  DirectionalLight,
  Group,
  PerspectiveCamera,
  Scene,
  WebGLRenderer
} from 'three';
import type { ModelId, ModeId, PointerState, ScrollGravityState } from '../types';
import type { QualityProfile } from '../app/qualityProfile';
import { ParticleField } from './particles/ParticleField';
import { createModel, tintModel } from './models/proceduralModels';
import { createRenderPipeline, type RenderPipeline } from './PostProcessing';
import { getModeSystem, type ModeSystem } from './systems/modeSystems';

export class SceneController {
  private readonly scene = new Scene();
  private readonly camera = new PerspectiveCamera(44, window.innerWidth / window.innerHeight, 0.1, 100);
  private readonly renderer: WebGLRenderer;
  private readonly particles: ParticleField;
  private readonly modelRoot = new Group();
  private readonly pipeline: RenderPipeline;
  private currentSystem: ModeSystem;
  private currentModel: Group;
  private rafTime = 0;

  constructor(canvas: HTMLCanvasElement, quality: QualityProfile) {
    this.currentSystem = getModeSystem('archive');
    this.renderer = new WebGLRenderer({ canvas, antialias: quality.name !== 'low', alpha: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(quality.pixelRatio);
    this.renderer.setClearColor(0x000000, 0);
    this.camera.position.set(0, 0, 6.4);

    const ambient = new AmbientLight(0xffffff, 0.44);
    const key = new DirectionalLight(this.currentSystem.accent, 2.2);
    key.position.set(3, 4, 4);
    const rim = new DirectionalLight(this.currentSystem.secondary, 1.4);
    rim.position.set(-4, -2, 3);
    this.scene.add(ambient, key, rim);

    this.particles = new ParticleField(quality.particleCount, this.currentSystem);
    this.scene.add(this.particles.points);
    this.currentModel = createModel('armillary', this.currentSystem);
    this.modelRoot.add(this.currentModel);
    this.scene.add(this.modelRoot);
    this.pipeline = createRenderPipeline(this.renderer, this.scene, this.camera, quality.bloom);
    this.resize();
  }

  setMode(mode: ModeId): void {
    this.currentSystem = getModeSystem(mode);
    this.scene.background = new Color(this.currentSystem.background).multiplyScalar(0.12);
    this.particles.setMode(this.currentSystem);
    tintModel(this.currentModel, this.currentSystem);
    this.particles.pulse(0.55);
  }

  setModel(model: ModelId): void {
    this.modelRoot.remove(this.currentModel);
    this.disposeGroup(this.currentModel);
    this.currentModel = createModel(model, this.currentSystem);
    this.modelRoot.add(this.currentModel);
    this.particles.pulse(0.8);
  }

  pulse(amount = 1): void {
    this.particles.pulse(amount);
  }

  update(now: number, gravity: ScrollGravityState, pointer: PointerState): void {
    this.rafTime = now;
    const t = now * 0.001;
    this.particles.update(now, gravity, pointer);
    this.modelRoot.position.set(1.62 + this.currentSystem.cameraShift[0], 0.10 + this.currentSystem.cameraShift[1], this.currentSystem.cameraShift[2]);
    this.modelRoot.rotation.x = this.currentSystem.modelTilt[0] + gravity.gravityY * 0.08 + Math.sin(t * 0.7) * 0.025;
    this.modelRoot.rotation.y = this.currentSystem.modelTilt[1] + gravity.gravityX * 0.16 + t * 0.08;
    this.modelRoot.rotation.z = this.currentSystem.modelTilt[2] + gravity.gravityY * 0.06;
    const scale = window.innerWidth < 760 ? 0.78 : 1;
    const pulse = 1 + Math.min(0.12, gravity.energy * 0.035);
    this.modelRoot.scale.setScalar(scale * pulse);
    this.pipeline.render();
  }

  resize(): void {
    const width = window.innerWidth;
    const height = window.innerHeight;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.pipeline.resize(width, height);
  }

  dispose(): void {
    this.particles.dispose();
    this.disposeGroup(this.modelRoot);
    this.pipeline.dispose();
    this.renderer.dispose();
  }

  getLastFrameTime(): number {
    return this.rafTime;
  }

  private disposeGroup(group: Group): void {
    group.traverse((child) => {
      const disposable = child as unknown as {
        geometry?: { dispose?: () => void };
        material?: { dispose?: () => void } | Array<{ dispose?: () => void }>;
      };
      disposable.geometry?.dispose?.();
      if (disposable.material) {
        const materials = Array.isArray(disposable.material) ? disposable.material : [disposable.material];
        materials.forEach((material) => {
          material.dispose?.();
        });
      }
    });
  }
}
