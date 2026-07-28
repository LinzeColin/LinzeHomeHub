import { test, expect } from '@playwright/test';

/* 中枢体检页。这一页的唯一职责是回答「中枢有没有在骗人」,
   所以它自己首先不能骗人 —— 下面每条测的都是「不许显示成健康」这个方向。 */

const snapshot = {
  updated_at: new Date(Date.now() - 2 * 60000).toISOString().slice(0, 16).replace('T', ' '),
  projects: [{ name: 'A' }, { name: 'B' }],
  software: { lines: [{ id: 'l1' }], units: [{ id: 'u1' }, { id: 'u2' }] },
  externals: [
    { name: 'Cloudflare', ok: true, note: 'DNS' },
    { name: 'NitroSend', ok: null, note: '未探测 · 无公开状态页' }
  ],
  flow: {
    cells_total: 315, measurable_total: 266, unmeasurable_total: 49, verified_total: 41,
    projects: [
      { project: 'KMFA', verified: 1, measurable: 47, cells_n: 54 },
      { project: 'WeReadPort', verified: 0, measurable: 0, cells_n: 5 }
    ]
  }
};
const controlPlane = {
  portfolio: { coverage_health: 'DEGRADED', runtime_health: 'FAILED', project_count: 2, business_line_count: 1 },
  evidence_summary: { verified_fresh: 0, stale: 0, unverified: 162 }
};

const routeBoth = (page, snap = snapshot, cp = controlPlane) => Promise.all([
  page.route('**/data/snapshot.json*', r => snap
    ? r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(snap) })
    : r.fulfill({ status: 500, body: 'boom' })),
  page.route('**/data/control-plane.json*', r => cp
    ? r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(cp) })
    : r.fulfill({ status: 404, body: 'nope' }))
]);

test('四个问题都渲染，缺口如实显示为数字而不是绿色', async ({ page }) => {
  await routeBoth(page);
  await page.goto('/hub.html');
  await expect(page.getByRole('heading', { name: '中枢体检' })).toBeVisible();
  for (const h of ['看得见多少', '看不见多少', '哪里可能在骗人', '下一步该谁动手']) {
    await expect(page.getByRole('heading', { name: new RegExp(h) })).toBeVisible();
  }
  // 缺口 = 266 - 41 = 225,必须以数字出现,不能被藏起来
  await expect(page.locator('#blind')).toContainText('225');
  await expect(page.locator('#blind')).toContainText('162');   // 未验证证据
  await expect(page.locator('#blind')).toContainText('DEGRADED');
});

test('★ 数据取不到时显示「不确定」，绝不显示为健康', async ({ page }) => {
  await routeBoth(page, null, null);
  await page.goto('/hub.html');
  await expect(page.locator('.err')).toBeVisible();
  await expect(page.locator('.err')).toContainText('不代表系统健康');
  // 页面上不许出现任何「通过」标签 —— 取不到数据不等于没问题
  await expect(page.locator('.tag.pass')).toHaveCount(0);
  await expect(page.locator('#at')).toContainText('数据不可用');
});

test('分母掺水会被判为有问题', async ({ page }) => {
  const bad = JSON.parse(JSON.stringify(snapshot));
  bad.flow.measurable_total = 300;          // 300 + 49 ≠ 315
  await routeBoth(page, bad);
  await page.goto('/hub.html');
  await expect(page.locator('#lies')).toContainText('覆盖率分母对不上');
  await expect(page.locator('.tag.fail').first()).toBeVisible();
});

test('谎报满分会被判为有问题', async ({ page }) => {
  const bad = JSON.parse(JSON.stringify(snapshot));
  bad.flow.verified_total = 266;            // 100%
  await routeBoth(page, bad);
  await page.goto('/hub.html');
  await expect(page.locator('#lies')).toContainText('需人工确认');
});

test('数据陈旧会被判为有问题，而不是继续报健康', async ({ page }) => {
  const stale = JSON.parse(JSON.stringify(snapshot));
  const old = new Date(Date.now() - 3 * 3600 * 1000);
  stale.updated_at = old.toISOString().slice(0, 16).replace('T', ' ');
  await routeBoth(page, stale);
  await page.goto('/hub.html');
  await expect(page.locator('#lies')).toContainText('数据已经陈旧');
});

test('控制面缺失时相关自查标为「查不了」而不是「通过」', async ({ page }) => {
  await routeBoth(page, snapshot, null);
  await page.goto('/hub.html');
  await expect(page.locator('#lies')).toContainText('查不了');
  await expect(page.locator('.tag.unk').first()).toBeVisible();
});

test('每个状态都有文字，不靠颜色分辨', async ({ page }) => {
  await routeBoth(page);
  await page.goto('/hub.html');
  const tags = page.locator('#lies .tag');
  const n = await tags.count();
  expect(n).toBeGreaterThan(0);
  for (let i = 0; i < n; i++) {
    const text = (await tags.nth(i).textContent() || '').trim();
    expect(['通过', '有问题', '查不了']).toContain(text);
  }
});

test('注入内容保持纯文本，不执行', async ({ page }) => {
  const evil = JSON.parse(JSON.stringify(snapshot));
  evil.flow.projects = [{ project: '<img src=x onerror="window.__xss=true">', verified: 0, measurable: 3, cells_n: 3 }];
  await routeBoth(page, evil);
  await page.goto('/hub.html');
  await expect(page.locator('#todo img[src="x"]')).toHaveCount(0);
  expect(await page.evaluate(() => window.__xss === true)).toBeFalsy();
});
