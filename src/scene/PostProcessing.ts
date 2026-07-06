import {
  Color,
  PerspectiveCamera,
  Scene,
  Vector2,
  WebGLRenderer
} from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';

export type RenderPipeline = {
  render: () => void;
  resize: (width: number, height: number) => void;
  dispose: () => void;
};

export function createRenderPipeline(
  renderer: WebGLRenderer,
  scene: Scene,
  camera: PerspectiveCamera,
  useBloom: boolean
): RenderPipeline {
  if (!useBloom) {
    return {
      render: () => renderer.render(scene, camera),
      resize: () => undefined,
      dispose: () => undefined
    };
  }

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(
    new Vector2(window.innerWidth, window.innerHeight),
    0.72,
    0.62,
    0.14
  );
  bloom.clearColor = new Color(0x030713);
  composer.addPass(bloom);

  return {
    render: () => composer.render(),
    resize: (width, height) => composer.setSize(width, height),
    dispose: () => composer.dispose()
  };
}
