import { test, expect } from '@playwright/test';

const signedReadyProjection = {
  schema_version: 3,
  state: 'READY',
  state_zh: '就绪',
  green_allowed: true,
  reasons: [],
  subject_id: 'public-fixture-subject',
  verdict_id: 'public-fixture-verdict',
  signature_state: 'PASS',
  freshness: {
    state: 'CURRENT',
    changed_fields: [],
    evidence: {
      state: 'CURRENT',
      reason: 'CURRENT',
      verified_at: '2026-08-03T00:00:00+00:00',
      expires_at: '2026-08-03T00:30:00+00:00',
      ttl_minutes: 30,
    },
  },
  bootstrap: false,
  observed_at: '2026-08-03T00:00:00+00:00',
  truth_source: 'fixture',
};

async function routeProjection(page, projection) {
  await page.route('**/data/agent-governance.json**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(projection),
  }));
}

test('v3 签名公开投影在治理页显示为可发布且不伪造私有明细', async ({ page }) => {
  await routeProjection(page, signedReadyProjection);
  await page.goto('/agent-governance.html');

  await expect(page.locator('#decision-title')).toHaveText('可发布验收');
  await expect(page.locator('#nav-state')).toHaveText('可发布验收');
  await expect(page.locator('#metric-runs')).toHaveText('—');
  await expect(page.locator('#run-body')).toContainText('最小披露原则不含运行明细');
  await expect(page.locator('#gate-body')).toContainText('通过');
  await expect(page.locator('#candidate-body')).toContainText('最小披露原则不含候选明细');
});

test('v3 公开投影在根页的语义读取对过期证据 fail-closed', async ({ page }) => {
  await page.goto('/');
  const stale = JSON.parse(JSON.stringify(signedReadyProjection));
  stale.freshness.state = 'STALE';
  stale.freshness.evidence.state = 'STALE';
  stale.freshness.evidence.reason = 'VERDICT_EXPIRED';

  await expect.poll(() => page.evaluate(({ ready, staleProjection }) => ({
    ready: governanceProjectionState(ready),
    stale: governanceProjectionState(staleProjection),
  }), { ready: signedReadyProjection, staleProjection: stale })).toEqual({ ready: 'READY', stale: 'STALE' });
});
