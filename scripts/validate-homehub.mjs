import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const root = process.cwd();
const read = (path) => readFileSync(join(root, path), 'utf8');
const mustExist = [
  'package.json',
  'vite.config.ts',
  'wrangler.jsonc',
  'index.html',
  'src/main.ts',
  'src/data/projects.json',
  'src/motion/scrollGravity.ts',
  'src/app/qualityProfile.ts',
  'src/scene/systems/modeSystems.ts',
  'src/physics/PhysicsWorld.ts',
  'PRODUCT.md',
  'DESIGN.md',
  '功能清单.md',
  '开发记录.md',
  '模型参数文件.md',
];

const failures = [];
for (const file of mustExist) {
  if (!existsSync(join(root, file))) failures.push(`missing ${file}`);
}

if (existsSync(join(root, 'index.html'))) {
  const html = read('index.html');
  for (const text of ['Linze Home Hub', 'Archive 档案', 'Nebula 星云', 'Voyage 夜航', 'Garden 花园']) {
    if (!html.includes(text)) failures.push(`index.html missing ${text}`);
  }
  if (html.includes('A living atlas of systems, memory, research, and tools.')) {
    failures.push('index.html contains removed subtitle');
  }
}

if (existsSync(join(root, 'src/data/projects.json'))) {
  const projects = JSON.parse(read('src/data/projects.json'));
  const requiredIds = ['eei', 'memory-atlas', 'pfi', 'serenity-alipay', 'nab'];
  for (const id of requiredIds) {
    if (!projects.some((project) => project.id === id)) failures.push(`projects.json missing ${id}`);
  }
  if (projects.length !== requiredIds.length) failures.push(`projects.json expected ${requiredIds.length} launch surfaces`);
  for (const project of projects) {
    if (project.compatibilityLevel !== 'L2') failures.push(`${project.id} must remain L2`);
    if (!['Live', 'Deploy-ready', 'Protected'].includes(project.deploymentStatus)) {
      failures.push(`${project.id} has invalid deploymentStatus`);
    }
    if (project.liveUrl && project.deploymentStatus !== 'Live') {
      failures.push(`${project.id} liveUrl requires verified Live status`);
    }
    if (!project.fallbackUrl) failures.push(`${project.id} missing fallbackUrl`);
    if (project.futureLevel !== 'L3 gated') failures.push(`${project.id} missing future L3 gate`);
  }
  if (JSON.stringify(projects).includes('lastUpdated')) failures.push('projects.json exposes lastUpdated');
}

if (existsSync(join(root, 'src/scene/systems/modeSystems.ts'))) {
  const modes = read('src/scene/systems/modeSystems.ts');
  for (const mode of ['archive', 'nebula', 'voyage', 'garden']) {
    if (!modes.includes(`id: '${mode}'`)) failures.push(`mode system missing ${mode}`);
  }
  for (const feature of ['spaceImpulse', 'particleTurbulence', 'routeBias', 'pollenLift']) {
    if (!modes.includes(feature)) failures.push(`mode systems missing behavior token ${feature}`);
  }
}

if (existsSync(join(root, 'src/app/qualityProfile.ts'))) {
  const quality = read('src/app/qualityProfile.ts');
  for (const token of ['low', 'medium', 'ultra', 'prefers-reduced-motion', 'quality=']) {
    if (!quality.includes(token)) failures.push(`qualityProfile missing ${token}`);
  }
}

if (existsSync(join(root, 'wrangler.jsonc'))) {
  const wrangler = read('wrangler.jsonc');
  if (!wrangler.includes('"directory": "./dist"')) failures.push('wrangler assets directory must be ./dist');
  if (!wrangler.includes('"not_found_handling": "single-page-application"')) {
    failures.push('wrangler SPA fallback missing');
  }
}

if (existsSync(join(root, 'src/ui/renderProjects.ts'))) {
  const ui = read('src/ui/renderProjects.ts');
  for (const banned of ['Open', 'Docs', 'GitHub', 'lastUpdated']) {
    if (ui.includes(banned)) failures.push(`renderProjects includes banned UI token ${banned}`);
  }
  for (const required of ['compatibilityLevel', 'deploymentStatus', 'futureLevel']) {
    if (!ui.includes(required)) failures.push(`renderProjects missing ${required}`);
  }
}

if (failures.length) {
  console.error(failures.map((failure) => `- ${failure}`).join('\n'));
  process.exit(1);
}

console.log('HomeHub structural validation passed');
