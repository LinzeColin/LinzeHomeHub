import projectsData from '../data/projects.json';
import type { ModeId, ModelId, Project } from '../types';
import { detectQualityProfile } from './qualityProfile';
import { ScrollGravityController } from '../motion/scrollGravity';
import { initScrollMotion } from '../motion/scrollMotion';
import { attachCardTilt } from '../motion/cardTilt';
import { SceneController } from '../scene/SceneController';
import { PhysicsWorld } from '../physics/PhysicsWorld';
import { renderProjects } from '../ui/renderProjects';
import { updateReadouts } from '../ui/readouts';

const modes: ModeId[] = ['archive', 'nebula', 'voyage', 'garden'];
const models: ModelId[] = ['armillary', 'island', 'book', 'compass', 'garden', 'core'];
const modelCaptions: Record<ModelId, [string, string]> = {
  armillary: ['星图仪 Armillary', '默认主视觉：暗金星环、深蓝夜航、粒子汇聚，作为首页的核心记忆装置。'],
  island: ['漂浮岛 Floating Island', '像一块悬浮在夜空里的系统地貌，适合未来承载项目分类、数据层和入口节点。'],
  book: ['档案书 Archive Book', '人文风格最强：手稿、档案、记忆页从暗处浮现，削弱 AI 模板感。'],
  compass: ['宇宙罗盘 Cosmic Compass', '更具方向感，适合表达导航、路线、系统入口和未来项目扩张。'],
  garden: ['黑金花园 Black Gold Garden', '把星云和花园结合，保留高级发光，但加入植物与手作感。'],
  core: ['能量核心 Energy Core', '更抽象、更未来，适合炫技展示 shader、粒子和物理反应。']
};

export async function initApp(): Promise<void> {
  const quality = detectQualityProfile();
  document.body.dataset.quality = quality.name;

  const projectContainer = document.getElementById('projectNodes');
  if (projectContainer) renderProjects(projectsData as Project[], projectContainer);

  const canvas = document.getElementById('scene-canvas');
  if (!(canvas instanceof HTMLCanvasElement)) throw new Error('Missing scene canvas');

  const scene = new SceneController(canvas, quality);
  const gravity = new ScrollGravityController(quality.reducedMotion);
  const stopMotion = initScrollMotion(quality);
  const stopTilt = attachCardTilt();
  const physics = await createPhysicsWorld(quality);

  let modeIndex = 0;
  let modelIndex = 0;
  let frame = 0;

  const setMode = (mode: ModeId) => {
    document.body.dataset.mode = mode;
    modeIndex = Math.max(0, modes.indexOf(mode));
    document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach((button) => {
      button.classList.toggle('active', button.dataset.mode === mode);
    });
    scene.setMode(mode);
  };

  const setModel = (model: ModelId) => {
    document.body.dataset.model = model;
    modelIndex = Math.max(0, models.indexOf(model));
    document.querySelectorAll<HTMLButtonElement>('[data-model]').forEach((button) => {
      button.classList.toggle('active', button.dataset.model === model);
    });
    const caption = document.getElementById('modelCaption');
    const [title, copy] = modelCaptions[model];
    if (caption) caption.innerHTML = `<strong>${title}</strong>${copy}`;
    scene.setModel(model);
  };

  document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach((button) => {
    button.addEventListener('click', () => setMode(button.dataset.mode as ModeId));
  });
  document.querySelectorAll<HTMLButtonElement>('[data-model]').forEach((button) => {
    button.addEventListener('click', () => setModel(button.dataset.model as ModelId));
  });

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.code === 'Space') {
      document.body.dataset.space = 'active';
      gravity.setSpaceActive(true);
      scene.pulse(1.1);
      return;
    }
    if (event.key.toLowerCase() === 'v') setMode(modes[(modeIndex + 1) % modes.length]);
    if (event.key.toLowerCase() === 'm') setModel(models[(modelIndex + 1) % models.length]);
  };
  const handleKeyUp = (event: KeyboardEvent) => {
    if (event.code === 'Space') {
      delete document.body.dataset.space;
      gravity.setSpaceActive(false);
    }
  };

  window.addEventListener('keydown', handleKeyDown);
  window.addEventListener('keyup', handleKeyUp);
  window.addEventListener('resize', () => {
    scene.resize();
    physics?.resize();
  }, { passive: true });

  gravity.start();
  setMode('archive');
  setModel('armillary');

  const animate = (now: number) => {
    const gravityState = gravity.tick(now);
    scene.update(now, gravityState, gravity.pointer);
    physics?.update(gravityState, gravity.pointer);
    updateReadouts(gravityState);
    frame = requestAnimationFrame(animate);
  };
  frame = requestAnimationFrame(animate);

  window.addEventListener('beforeunload', () => {
    cancelAnimationFrame(frame);
    window.removeEventListener('keydown', handleKeyDown);
    window.removeEventListener('keyup', handleKeyUp);
    gravity.dispose();
    physics?.dispose();
    scene.dispose();
    stopMotion();
    stopTilt();
  });
}

async function createPhysicsWorld(quality: ReturnType<typeof detectQualityProfile>): Promise<PhysicsWorld | undefined> {
  const lab = document.getElementById('gravityLab');
  const lines = document.getElementById('gravityLines');
  if (!(lab instanceof HTMLElement) || !(lines instanceof SVGSVGElement)) return undefined;
  const world = new PhysicsWorld(lab, lines, quality);
  try {
    await world.init();
    return world;
  } catch (error) {
    console.error('Rapier initialization failed', error);
    return undefined;
  }
}
