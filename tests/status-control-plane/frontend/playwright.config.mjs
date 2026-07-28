import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: '.',
  // ★ 原来写死成 status.spec.mjs —— 新增的 spec 文件会**静默不跑**,
  //   看起来「测试全绿」实际上根本没执行。改成匹配全部 spec。
  testMatch: '*.spec.mjs',
  timeout: 30000,
  expect: { timeout: 5000 },
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  use: { baseURL: 'http://127.0.0.1:8765', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: {
    command: 'python3 serve.py --port 8765',
    url: 'http://127.0.0.1:8765',
    reuseExistingServer: false,
    timeout: 15000
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { ...devices['Pixel 7'], reducedMotion: 'reduce' } }
  ]
});
