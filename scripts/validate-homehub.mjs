import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join, extname } from 'node:path';

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
  '文档/00_我在哪.md',
  '文档/01_产品需求.md',
  '文档/02_系统架构.md',
  '文档/03_口径字典.md',
  '文档/04_操作流程.md',
  '文档/05_执行与验收.md',
  '文档/06_运维手册.md',
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
  // Frozen against the current main data baseline. This validator intentionally
  // rejects both omission and unreviewed additions without rewriting project data.
  const verifiedProjects = {
    eei: ['Live', 'https://eei.linzezhang.com', 'L3 gated'],
    'memory-atlas': ['Protected', 'https://memoryatlas.linzezhang.com', 'L3 gated'],
    pfi: ['Live', 'https://pfi.linzezhang.com', 'L3 gated'],
    'serenity-alipay': ['Live', 'https://serenity.linzezhang.com', 'L3 gated'],
    nab: ['Live', 'https://nab.linzezhang.com', 'L2'],
    account: ['Live', 'https://account.linzezhang.com', 'L3 gated'],
  };
  const requiredIds = Object.keys(verifiedProjects);
  for (const id of requiredIds) {
    if (!projects.some((project) => project.id === id)) failures.push(`projects.json missing ${id}`);
  }
  if (projects.length !== requiredIds.length) failures.push(`projects.json expected ${requiredIds.length} launch surfaces`);
  for (const project of projects) {
    if (project.compatibilityLevel !== 'L2') failures.push(`${project.id} must remain L2`);
    if (!['Live', 'Deploy-ready', 'Protected'].includes(project.deploymentStatus)) {
      failures.push(`${project.id} has invalid deploymentStatus`);
    }
    if (project.liveUrl && !['Live', 'Protected'].includes(project.deploymentStatus)) {
      failures.push(`${project.id} liveUrl requires verified Live or Protected status`);
    }
    if (project.deploymentStatus === 'Protected') {
      if (!project.liveUrl) failures.push(`${project.id} Protected status requires a verified liveUrl`);
      if (!/access|allowlist|protected/i.test(project.summary)) {
        failures.push(`${project.id} Protected status must explain its access boundary`);
      }
    }
    if (!project.fallbackUrl) failures.push(`${project.id} missing fallbackUrl`);
    const [expectedStatus, expectedUrl, expectedFutureLevel] = verifiedProjects[project.id] ?? [];
    if (!expectedStatus) {
      failures.push(`${project.id} is not in the frozen project baseline`);
      continue;
    }
    if (project.futureLevel !== expectedFutureLevel) {
      failures.push(`${project.id} futureLevel must match frozen project baseline`);
    }
    if (project.deploymentStatus !== expectedStatus) {
      failures.push(`${project.id} deploymentStatus must match verified deployment evidence`);
    }
    if (project.liveUrl !== expectedUrl) {
      failures.push(`${project.id} liveUrl must match verified deployment evidence`);
    }
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

checkNoRuntimeAI(failures);

if (failures.length) {
  console.error(failures.map((failure) => `- ${failure}`).join('\n'));
  process.exit(1);
}

console.log('HomeHub structural validation passed');

// ---- 零 Agent / 零 Token 守卫 ----
// 运行期代码不得调用任何推理接口。这条规则写在 AGENTS.md,这里让它可被机器判定,
// 否则时间一长就会被悄悄破坏。只扫运行期源码,不扫文档与本脚本自身。
//
// ★ 与 tests/status-control-plane/policy_scan.py **对齐**。
//   在此之前两个守卫对同一个文件给出相反结论:policy_scan 明确按文件豁免了账务探针的
//   厂商域名(查账单不是调模型),而这里是整域名封杀 —— 于是 `npm run validate` 长期红着,
//   红的还是一个被另一个守卫认定为合规的文件。两个守卫互相矛盾时,人只会开始忽略其中一个,
//   那比少一个守卫更糟。
//
//   对齐后的策略,**净强度不降反升**:
//     · 厂商域名:只在账务探针这一个文件里豁免,别处出现照旧违规;
//     · 推理端点(chat/completions 等完整 URL):在**任何**文件里都违规,
//       包括那个被豁免的文件 —— 这一条是原来没有的,现在补上了。
function checkNoRuntimeAI(failures) {
  const roots = ['src', 'status/collector', 'status/deploy', 'status/web', 'status/admin'];
  // 厂商域名/SDK 构造。与 policy_scan.py 的 FORBIDDEN_RUNTIME 同源。
  const bannedVendor = [
    /api\.openai\.com/i,
    /api\.anthropic\.com/i,
    /generativelanguage\.googleapis\.com/i,
    /api\.cohere\.ai/i,
    /api\.mistral\.ai/i,
    /\bopenai\s*\(/i,
    /\bAnthropic\s*\(/i,
  ];
  // 完整的推理端点 URL(host + 推理路径)。允许 /v1/organization/costs,禁止 /v1/chat/completions。
  // 只匹配带 host 的完整 URL,所以探针里那张裸路径黑名单不会误伤自己。
  const bannedInference = [
    /https?:\/\/[^\s"')]*(?:openai|anthropic|deepseek|googleapis|x\.ai|mistral)[^\s"')]*\/(?:chat\/completions|completions|responses|embeddings|images\/generations|messages|generateContent)/i,
  ];
  // ★ 按文件豁免,且只豁免域名那一档。放宽守卫时必须同时写清「放宽到哪为止」,
  //   否则下一个人只会看到「openai 域名是允许的」,口子会一直长大。
  const VENDOR_EXEMPT = 'status/collector/probe_ai_balance.py';
  const exts = new Set(['.ts', '.js', '.mjs', '.py', '.sh', '.html', '.json']);
  const walk = (dir) => {
    let entries;
    try { entries = readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      const full = join(dir, e.name);
      if (e.isDirectory()) {
        if (['node_modules', 'dist', '.git', 'vendor', 'data'].includes(e.name)) continue;
        walk(full);
      } else if (exts.has(extname(e.name))) {
        let text;
        try { text = readFileSync(full, 'utf8'); } catch { continue; }
        const vendorExempt = full.replaceAll('\\', '/').endsWith(VENDOR_EXEMPT);
        if (!vendorExempt) {
          for (const re of bannedVendor) {
            if (re.test(text)) {
              failures.push(`零Token守卫: ${full} 出现运行期 AI 接口调用 (${re})`);
              break;
            }
          }
        }
        // 推理端点不设任何豁免 —— 连账务探针自己也要查
        for (const re of bannedInference) {
          if (re.test(text)) {
            failures.push(`零Token守卫: ${full} 出现推理端点调用,该项无任何豁免 (${re})`);
            break;
          }
        }
      }
    }
  };
  roots.forEach(walk);
}
