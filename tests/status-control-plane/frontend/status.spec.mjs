import { test, expect } from '@playwright/test';
const fixture={
  schema_version:1,generated_at:'2026-07-27T00:00:00Z',observed_revision:'fixture-revision',
  portfolio:{coverage_health:'UNKNOWN',runtime_health:'HEALTHY',project_count:2,unknown_is_healthy:false},
  business_lines:[{business_line_id:'bl:alpha',name:'Alpha 业务线',lifecycle:'active',coverage_state:'OBSERVED',runtime_state:'HEALTHY',evidence_state:'UNVERIFIED',data_freshness:'FRESH',recovery_state:'UNKNOWN',project_ids:['project:alpha'],dependencies:[],stage_score:88,reason:'fixture'}],
  projects:[
    {entity_id:'project:alpha',name:'Alpha',lifecycle:'active',coverage_state:'DECLARED_OBSERVED_HEALTHY',runtime_state:'HEALTHY',evidence_state:'VERIFIED_FRESH',data_freshness:'FRESH',recovery_state:'RESTORE_VERIFIED',dependencies:[],reason:''},
    {entity_id:'project:orphan',name:'<img src=x onerror="window.__xss=true">',lifecycle:'active',coverage_state:'DEPLOYED_UNREGISTERED',runtime_state:'HEALTHY',evidence_state:'UNVERIFIED',data_freshness:'FRESH',recovery_state:'UNKNOWN',dependencies:[],reason:'运行中但未登记'}
  ],
  capabilities:[{capability_id:'cap:run',project_id:'project:alpha',name:'Alpha · 运行',aggregate_state:'IMPLEMENTED_UNVERIFIED',declared:true,implemented:true,verified:'UNVERIFIED',packaged:false,deployed:true,operational:'HEALTHY',recoverable:'UNKNOWN'}],architecture:{nodes:[{id:'project:alpha',kind:'project',label:'Alpha',state:'HEALTHY'}],edges:[],provenance_mode:'DERIVED'},conditions:[],evidence_summary:{verified_fresh:1,stale:0,unverified:1},provenance_summary:{native:1,reconstructed:0,unknown:1}
};

test.beforeEach(async ({page})=>{
  await page.route('**/data/control-plane.json',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fixture)}));
});

test('未知覆盖不会被显示为健康，注入内容保持纯文本',async({page})=>{
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'业务线与证据治理'})).toBeVisible();
  const unknown=page.locator('.cp-state[data-state="UNKNOWN"]').first();
  await expect(unknown).toBeVisible();
  await expect(page.getByText('<img src=x onerror="window.__xss=true">')).toBeVisible();
  expect(await page.evaluate(()=>window.__xss===true)).toBeFalsy();
  await expect(page.locator('#control-plane-governance img[src="x"]')).toHaveCount(0);
});

test('移动端无非预期横向溢出且标签可键盘访问',async({page})=>{
  await page.goto('/');
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  const tabs=page.getByRole('tab');
  await tabs.first().focus();
  await page.keyboard.press('ArrowRight');
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected','true');
  await expect(page.getByRole('tabpanel',{name:'项目'})).toBeVisible();
});
