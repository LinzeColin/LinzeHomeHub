import { test, expect } from '@playwright/test';

// 2026-08-04：「业务线与证据治理」板块（#control-plane-governance）按 owner 要求下线，
// 原先针对该板块的断言（标题、cp-state、tab 键盘导航、XSS 纯文本化）随之移除。
// 这里只保留与该板块无关、仍然有回归价值的整页检查。
// 注意：/data/control-plane.json 并未下线 —— hub.js 仍在消费它，采集器保持运行。

test('移动端无非预期横向溢出', async ({ page }) => {
  await page.goto('/');
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
});
