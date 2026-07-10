import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const baseUrl = process.env.HOMEHUB_URL || 'http://127.0.0.1:4173';
const evidenceDir = process.env.EVIDENCE_DIR || resolve(process.cwd(), 'artifacts');
mkdirSync(evidenceDir, { recursive: true });

const failures = [];
const consoleMessages = [];

const expect = (condition, message) => {
  if (!condition) failures.push(message);
};

const browser = await chromium.launch({
  headless: true,
  args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
page.on('console', (msg) => {
  if (['error', 'warning'].includes(msg.type())) consoleMessages.push(`${msg.type()}: ${msg.text()}`);
});
page.on('pageerror', (error) => {
  consoleMessages.push(`pageerror: ${error.message}`);
});

await page.goto(baseUrl, { waitUntil: 'networkidle' });
await page.waitForSelector('h1:text("Linze Home Hub")', { timeout: 15000 });
await page.waitForTimeout(1400);

const initial = await page.evaluate(() => {
  const canvas = document.querySelector('canvas');
  const body = document.body;
  const text = document.body.innerText;
  return {
    mode: body.dataset.mode,
    model: body.dataset.model,
    quality: body.dataset.quality,
    oldSubtitle: text.includes('A living atlas of systems, memory, research, and tools.'),
    modeButtons: [...document.querySelectorAll('button[data-mode]')].map((el) => el.textContent?.trim()),
    modelButtons: [...document.querySelectorAll('button[data-model]')].map((el) => el.textContent?.trim()),
    projectLinks: [...document.querySelectorAll('.planet-card')].map((el) => ({
      text: el.textContent,
      href: el.getAttribute('href')
    })),
    canvas: canvas ? {
      width: canvas.width,
      height: canvas.height,
      rect: canvas.getBoundingClientRect().toJSON(),
      dataUrlLength: canvas.toDataURL('image/png').length
    } : null,
    overflow: document.documentElement.scrollWidth - window.innerWidth
  };
});

expect(initial.mode === 'archive', 'default mode is not archive');
expect(initial.model === 'armillary', 'default model is not armillary');
expect(Boolean(initial.quality), 'quality profile missing');
expect(initial.oldSubtitle === false, 'removed subtitle is visible');
expect(initial.modeButtons.length === 4, 'mode switcher count mismatch');
expect(initial.modelButtons.length === 6, 'model switcher count mismatch');
expect(initial.projectLinks.length === 5, 'launch constellation count mismatch');
expect(initial.projectLinks.every((link) => link.href && !/last updated/i.test(link.text ?? '')), 'project link contract failed');
expect(initial.projectLinks.every((link) => /L2/.test(link.text ?? '')), 'project L2 badges missing');
expect(initial.projectLinks.filter((link) => /Live/.test(link.text ?? '')).length === 4, 'verified live surface count mismatch');
expect(initial.projectLinks.some((link) => /Protected/.test(link.text ?? '')), 'protected surface missing');
expect(initial.projectLinks.every((link) => !/Deploy-ready/.test(link.text ?? '')), 'stale deploy-ready state remains');
expect(initial.canvas && initial.canvas.width > 0 && initial.canvas.height > 0, 'canvas has no drawing buffer');
expect(initial.canvas && initial.canvas.dataUrlLength > 10000, 'canvas appears blank or unreadable');
expect(initial.overflow <= 1, `desktop horizontal overflow ${initial.overflow}`);
await page.screenshot({ path: resolve(evidenceDir, 'homehub-desktop-initial.png'), fullPage: false });

for (const mode of ['nebula', 'voyage', 'garden', 'archive']) {
  await page.click(`[data-mode="${mode}"]`);
  await page.waitForTimeout(250);
  const current = await page.evaluate(() => document.body.dataset.mode);
  expect(current === mode, `mode switch failed for ${mode}`);
}

await page.keyboard.press('KeyM');
await page.waitForTimeout(250);
expect(await page.evaluate(() => document.body.dataset.model) !== 'armillary', 'keyboard M did not cycle model');
await page.keyboard.press('KeyV');
await page.waitForTimeout(250);
expect(await page.evaluate(() => document.body.dataset.mode) !== 'archive', 'keyboard V did not cycle mode');
await page.keyboard.down('Space');
await page.waitForTimeout(180);
expect(await page.evaluate(() => document.body.dataset.space === 'active'), 'Space did not activate gravity well');
await page.keyboard.up('Space');

await page.evaluate(() => window.scrollTo({ top: 1200, behavior: 'instant' }));
await page.waitForTimeout(700);
const gravity = await page.evaluate(() => ({
  dir: document.getElementById('gDir')?.textContent,
  vel: document.getElementById('gVel')?.textContent,
  gx: document.getElementById('gX')?.textContent,
  gy: document.getElementById('gY')?.textContent
}));
expect(Boolean(gravity.dir), 'gravity direction readout missing after scroll');
expect(Boolean(gravity.vel), 'gravity velocity readout missing after scroll');
expect(Boolean(gravity.gx), 'gravity x readout missing after scroll');
expect(Boolean(gravity.gy), 'gravity y readout missing after scroll');

await page.screenshot({ path: resolve(evidenceDir, 'homehub-desktop-after-interaction.png'), fullPage: true });

const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true });
await mobile.goto(`${baseUrl}?quality=low`, { waitUntil: 'networkidle' });
await mobile.waitForSelector('h1:text("Linze Home Hub")', { timeout: 15000 });
await mobile.waitForTimeout(900);
const mobileState = await mobile.evaluate(() => ({
  mode: document.body.dataset.mode,
  quality: document.body.dataset.quality,
  overflow: document.documentElement.scrollWidth - window.innerWidth,
  cards: document.querySelectorAll('.planet-card').length
}));
expect(mobileState.quality === 'low', 'quality query param did not force low');
expect(mobileState.overflow <= 1, `mobile horizontal overflow ${mobileState.overflow}`);
expect(mobileState.cards === 5, 'mobile launch constellation cards missing');
await mobile.screenshot({ path: resolve(evidenceDir, 'homehub-mobile.png'), fullPage: true });

await mobile.close();
await browser.close();

const expectedHeadlessGpuNoise = [
  'using deprecated parameters for the initialization function',
  'GL Driver Message',
  'THREE.WebGLProgram: Shader Error',
  'WebGL: INVALID_OPERATION',
  'WebGL: CONTEXT_LOST_WEBGL'
];
const seriousConsole = consoleMessages.filter((message) => {
  if (message.includes('was preloaded using link preload')) return false;
  return !expectedHeadlessGpuNoise.some((noise) => message.includes(noise));
});
if (seriousConsole.length) failures.push(`console issues: ${seriousConsole.join(' | ')}`);

if (failures.length) {
  console.error(failures.map((failure) => `- ${failure}`).join('\n'));
  process.exit(1);
}

console.log(`HomeHub visual acceptance passed. Evidence: ${evidenceDir}`);
