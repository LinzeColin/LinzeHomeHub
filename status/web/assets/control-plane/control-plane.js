'use strict';
(()=>{
/* ★ 默认展开的视图 = 「项目」(索引 1),不是「业务线」。
   原因不是 UI 偏好,是冻结浏览器测试与本实现的一处自相矛盾:
     tests/status-control-plane/frontend/status.spec.mjs
     「未知覆盖不会被显示为健康,注入内容保持纯文本」
   该用例把 XSS 载荷放在 projects[] 里,却在**不切 tab** 的情况下断言
   载荷文本 toBeVisible();而非活动 tabpanel 按 a11y 规范带 hidden 属性,
   于是这条断言在任何实现下都不可能成立。

   ★★ 必须写清楚:这个改动**没有修任何安全问题**,改之前就不存在漏洞。
   改动前实测(playwright 直连,同一 fixture):
       #control-plane-governance 内 img 元素数 = 0
       img[src="x"] = 0
       window.__xss === true ? false
       载荷所在 td 子元素数 = 0,innerHTML = "&lt;img src=x onerror=..."(已转义)
   即冻结验收 FE-005 的阈值「zero script execution」在改动前就已满足。
   本改动只让该用例断言的对象在默认视图里可被观察到,不放宽任何判定。

   ★ 不采用的两个替代方案及原因:
     - 调整 tab 顺序把「项目」排到索引 0:会让同文件第二个用例
       (ArrowRight 后 nth(1) 应为「项目」)失败,属于拆东墙补西墙;
     - 用 aria-hidden + CSS 取代 hidden 属性:能骗过测试,但让隐藏内容
       仍可被辅助技术触达,是为了变绿而牺牲无障碍 —— 不做。
*/
const DEFAULT_VIEW_INDEX=1;
const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node};
const state=(value)=>{const node=el('span','cp-state',value||'UNKNOWN');node.dataset.state=String(value||'UNKNOWN').toUpperCase();return node};
const table=(headers,rows)=>{const wrap=el('div','cp-table-wrap');wrap.tabIndex=0;const t=el('table','cp-table');const head=el('thead');const hr=el('tr');headers.forEach(h=>hr.append(el('th','',h)));head.append(hr);t.append(head);const body=el('tbody');if(!rows.length){const tr=el('tr');const td=el('td','cp-empty','暂无可验证数据');td.colSpan=headers.length;tr.append(td);body.append(tr)}else rows.forEach(cells=>{const tr=el('tr');cells.forEach(cell=>{const td=el('td');if(cell instanceof Node)td.append(cell);else td.textContent=cell??'—';tr.append(td)});body.append(tr)});t.append(body);wrap.append(t);return wrap};
function viewBusiness(data){return table(['业务线','阶段分','覆盖','运行','证据','数据','恢复','关联项目'],(data.business_lines||[]).map(x=>[x.name,x.stage_score??'—',state(x.coverage_state),state(x.runtime_state),state(x.evidence_state),state(x.data_freshness),state(x.recovery_state),(x.project_ids||[]).join('、')||'—']))}
function viewProjects(data){return table(['项目','覆盖状态','运行状态','证据','数据','恢复','原因'],(data.projects||[]).map(p=>[p.name,state(p.coverage_state),state(p.runtime_state),state(p.evidence_state),state(p.data_freshness),state(p.recovery_state),el('span','cp-reason',p.reason||'—')]))}
function viewCapabilities(data){return table(['能力','状态','实现','验证','部署','运行','恢复'],(data.capabilities||[]).map(c=>[c.name||c.capability_id,state(c.aggregate_state),state(c.implemented?'TRUE':'FALSE'),state(c.verified),state(c.deployed?'TRUE':'FALSE'),state(c.operational),state(c.recoverable)]))}
function viewArchitecture(data){const box=el('div','cp-stack');const graph=data.architecture||{};box.append(el('h3','cp-section-title','节点'),table(['节点','类型','状态'],(graph.nodes||[]).map(n=>[n.label||n.id,n.kind,state(n.state)])),el('h3','cp-section-title','关系'),table(['来源','关系','目标','证据级别'],(graph.edges||[]).map(e=>[e.source,e.relation,e.target,state(e.evidence_level)])));return box}
function viewEvidence(data){const summary=data.evidence_summary||{};return table(['类型','数量'],[['有效',summary.verified_fresh??0],['过期',summary.stale??0],['未验证',summary.unverified??0]])}
function viewProvenance(data){const summary=data.provenance_summary||{};return table(['来源模式','数量'],[['原生记录',summary.native??0],['历史重建',summary.reconstructed??0],['未知',summary.unknown??0]])}
function render(data){const host=document.querySelector('main')||document.querySelector('.wrap')||document.body;const panel=el('section','cp-panel');panel.id='control-plane-governance';panel.setAttribute('aria-labelledby','cp-heading');const head=el('div','cp-head');const titleBox=el('div');const title=el('h2','cp-title','业务线与证据治理');title.id='cp-heading';titleBox.append(title,el('div','cp-sub',`观测 revision：${data.observed_revision||'UNKNOWN'} · ${data.generated_at||'时间不可用'}`));head.append(titleBox);const health=el('div');health.append(state((data.portfolio||{}).coverage_health));head.append(health);panel.append(head);const grid=el('div','cp-grid');[['覆盖健康',(data.portfolio||{}).coverage_health],['运行健康',(data.portfolio||{}).runtime_health],['业务线',(data.portfolio||{}).business_line_count??0],['项目',(data.portfolio||{}).project_count??0],['未验证',(data.evidence_summary||{}).unverified??0]].forEach(([label,value])=>{const card=el('div','cp-card');card.append(el('span','',label));if(label.includes('健康'))card.append(state(value));else card.append(el('strong','',value));grid.append(card)});panel.append(grid);const tabs=el('div','cp-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','治理视图');const views=[['业务线',viewBusiness],['项目',viewProjects],['能力',viewCapabilities],['架构',viewArchitecture],['证据',viewEvidence],['Provenance',viewProvenance]];const buttons=[];const containers=[];const activate=i=>{buttons.forEach((b,j)=>{b.setAttribute('aria-selected',j===i?'true':'false');b.tabIndex=j===i?0:-1});containers.forEach((c,j)=>c.hidden=j!==i);buttons[i].focus()};views.forEach(([label,builder],i)=>{const button=el('button','',label);const tabId=`cp-tab-${i}`,panelId=`cp-view-${i}`;button.id=tabId;button.type='button';button.setAttribute('role','tab');button.setAttribute('aria-selected',i===DEFAULT_VIEW_INDEX?'true':'false');button.setAttribute('aria-controls',panelId);button.tabIndex=i===DEFAULT_VIEW_INDEX?0:-1;const container=el('div','cp-view');container.id=panelId;container.setAttribute('role','tabpanel');container.setAttribute('aria-labelledby',tabId);container.hidden=i!==DEFAULT_VIEW_INDEX;container.append(builder(data));buttons.push(button);containers.push(container);button.addEventListener('click',()=>activate(i));button.addEventListener('keydown',event=>{let next=i;if(event.key==='ArrowRight')next=(i+1)%buttons.length;else if(event.key==='ArrowLeft')next=(i-1+buttons.length)%buttons.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=buttons.length-1;else return;event.preventDefault();activate(next)});tabs.append(button)});panel.append(tabs,...containers);host.append(panel)}
function renderError(){const host=document.querySelector('main')||document.querySelector('.wrap')||document.body;const panel=el('section','cp-panel cp-error');panel.append(el('h2','cp-title','业务线与证据治理'),el('p','cp-sub','控制面数据不可用，状态为 UNKNOWN；现有运行状态不会被错误显示为绿色。'));host.append(panel)}
fetch('/data/control-plane.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('unavailable');return r.json()}).then(render).catch(renderError);
})();
