import { test, expect } from '@playwright/test';

test('已下线的治理路径必须回落到当前首页，且不暴露旧治理界面', async ({ page }) => {
  await page.goto('/');
  await page.waitForLoadState('networkidle');
  const homepage = await page.content();
  const homepageTitle = await page.title();

  await page.goto('/agent-governance.html');
  await page.waitForLoadState('networkidle');
  expect(await page.title()).toBe(homepageTitle);
  await expect(page.locator('#decision-title')).toHaveCount(0);
  expect(await page.content()).toBe(homepage);
});
