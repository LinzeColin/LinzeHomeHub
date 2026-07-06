import type { SceneController } from './SceneController';

export function disposeScene(scene: SceneController): void {
  scene.dispose();
}
