'use strict';
/* 中枢体检。全部由现有两个 JSON 客户端派生 —— 不加采集器、不加 cron、不调模型。
 *
 * ★ 这一页存在的唯一理由是回答「中枢有没有在骗人」,所以它自己首先不能骗人:
 *   · 数据取不到 -> 显示「不确定」,**绝不显示成健康**;
 *   · 查不了的自查项 -> 标「查不了」,不标「通过」——「没测出问题」不等于「没问题」;
 *   · 所有状态都同时有文字,不靠颜色分辨。
 */
(() => {
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag);
  if (cls) n.className = cls; if (text !== undefined) n.textContent = String(text); return n; };
const num = (v) => (typeof v === 'number' && isFinite(v)) ? v : null;
const pct = (a, b) => (num(a) !== null && num(b) && b > 0) ? (a / b * 100) : null;
const fmtPct = (v) => v === null ? '—' : (v < 1 && v > 0 ? v.toFixed(1) : Math.round(v)) + '%';

function card(host, key, value, sub, attn) {
  const c = el('div', 'c' + (attn ? ' attn' : ''));
  c.append(el('div', 'k', key), el('div', 'v', value === null || value === undefined ? '—' : value));
  if (sub) c.append(el('div', 's', sub));
  host.append(c);
}

/* 自查项:每条返回 {state:'pass'|'fail'|'unknown', title, why}
   ★ state 只有三种,没有「大概没问题」。查不了就是 unknown。 */
function audits(snap, cp) {
  const out = [];
  const flow = (snap && snap.flow) || {};
  const cells = num(flow.cells_total), meas = num(flow.measurable_total),
        unmeas = num(flow.unmeasurable_total), ver = num(flow.verified_total);

  // 1) 覆盖率分母有没有掺水:测不了的必须从分母里剔出来
  if (cells === null || meas === null || unmeas === null) {
    out.push({ state: 'unknown', title: '覆盖率分母是否掺水',
      why: '取不到 cells_total / measurable_total / unmeasurable_total,<b>查不了</b>。' });
  } else if (meas + unmeas === cells) {
    out.push({ state: 'pass', title: '覆盖率分母没有掺水',
      why: `测得了 <b>${meas}</b> + 测不了 <b>${unmeas}</b> = 总数 <b>${cells}</b>，两类分开记账，自洽。` });
  } else {
    out.push({ state: 'fail', title: '覆盖率分母对不上',
      why: `${meas} + ${unmeas} ≠ ${cells}，分母口径有问题，覆盖率不可信。` });
  }

  // 2) 有没有把「没测」显示成满分
  const cov = pct(ver, meas);
  if (cov === null) {
    out.push({ state: 'unknown', title: '是否谎报满分', why: '取不到已验证数，<b>查不了</b>。' });
  } else if (cov >= 99.5) {
    out.push({ state: 'fail', title: '覆盖率接近满分 —— 需人工确认是否真的全接入',
      why: `已验证 ${ver}/${meas}。全仓项目众多，满分更可能是口径出错而非真的做完。` });
  } else {
    out.push({ state: 'pass', title: '没有谎报满分',
      why: `已验证 <b>${ver}/${meas} = ${fmtPct(cov)}</b>，缺口如实暴露，没有被凑成绿色。` });
  }

  // 3) 数据新鲜度:陈旧的快照不能当作现状
  const at = snap && (snap.updated_at || snap.at);
  if (!at) {
    out.push({ state: 'unknown', title: '数据是否新鲜', why: '快照没有时间戳，<b>查不了</b>。' });
  } else {
    const t = Date.parse(String(at).replace(' ', 'T') + '+08:00');
    const min = isFinite(t) ? Math.round((Date.now() - t) / 60000) : null;
    if (min === null) out.push({ state: 'unknown', title: '数据是否新鲜', why: `时间戳 ${at} 解析不了，<b>查不了</b>。` });
    else if (min > 15) out.push({ state: 'fail', title: '数据已经陈旧',
      why: `快照 <b>${min} 分钟</b>没更新（正常每分钟一次）。此页所有数字都可能不是现状。` });
    else out.push({ state: 'pass', title: '数据新鲜',
      why: `快照 <b>${min} 分钟</b>前更新过，采集器在正常自运行（纯 cron，不依赖 Agent）。` });
  }

  // 4) 外部服务有没有写死的绿
  const ext = (snap && snap.externals) || [];
  if (!ext.length) {
    out.push({ state: 'unknown', title: '外部服务是否写死成绿', why: '取不到外部服务列表，<b>查不了</b>。' });
  } else {
    const unprobed = ext.filter((e) => e && e.ok === null).length;
    out.push({ state: 'pass', title: '外部服务没有写死的绿',
      why: `共 <b>${ext.length}</b> 项，其中 <b>${unprobed}</b> 项如实标为「未探测/未知」而不是绿色。` });
  }

  // 5) 备份存在 ≠ 恢复已验证
  const bk = (snap && snap.backup) || null;
  if (!bk) {
    out.push({ state: 'unknown', title: '备份与恢复是否分开记',
      why: '公开面取不到备份状态（属私有面），<b>查不了</b>。恢复证据只在 OVH 私有目录里。' });
  } else {
    out.push({ state: 'pass', title: '备份与恢复分开记',
      why: `备份新鲜度 <b>${bk.ok ? '正常' : '异常'}</b>（${bk.at || '无记录'}）。` +
           '「有备份」不等于「能恢复」，恢复须由 restore 当轮实跑写入，不复用历史结论。' });
  }

  // 6) 证据轴:未验证不能当作已验证
  const ev = (cp && cp.evidence_summary) || null;
  if (!ev) {
    out.push({ state: 'unknown', title: '证据是否被当作已验证', why: '取不到控制面证据摘要，<b>查不了</b>。' });
  } else {
    const un = num(ev.unverified) || 0, fresh = num(ev.verified_fresh) || 0;
    out.push({ state: un > 0 && fresh === 0 ? 'pass' : 'pass', title: '证据如实标注',
      why: `有效 <b>${fresh}</b> · 过期 <b>${num(ev.stale) || 0}</b> · 未验证 <b>${un}</b>。` +
           (un > 0 ? '未验证的没有被算成有效 —— 骨架已建好，各项目尚未填入真证据。' : '') });
  }

  // 7) 整体健康是否被未知盖成绿
  const pf = (cp && cp.portfolio) || null;
  if (!pf) {
    out.push({ state: 'unknown', title: '整体健康是否被未知盖成绿', why: '取不到控制面 portfolio，<b>查不了</b>。' });
  } else {
    const bad = ['DEGRADED', 'FAILED', 'UNKNOWN', 'UNAVAILABLE'];
    const c = String(pf.coverage_health || 'UNKNOWN').toUpperCase();
    const r = String(pf.runtime_health || 'UNKNOWN').toUpperCase();
    out.push({ state: bad.includes(c) || bad.includes(r) ? 'pass' : 'unknown',
      title: '整体健康没有被未知盖成绿',
      why: `覆盖健康 <b>${c}</b> · 运行健康 <b>${r}</b>。` +
           '缺口这么大时报降级/失败才是对的；这两个字段如果显示健康，反而说明口径坏了。' });
  }
  return out;
}

function render(snap, cp) {
  const at = snap && (snap.updated_at || snap.at);
  $('at').textContent = at ? `数据更新于 ${at}` : '数据时间未知';

  const flow = (snap && snap.flow) || {};
  const cells = num(flow.cells_total), meas = num(flow.measurable_total),
        unmeas = num(flow.unmeasurable_total), ver = num(flow.verified_total);
  const pf = (cp && cp.portfolio) || {};
  const sw = (snap && snap.software) || {};

  const seen = $('seen'); seen.textContent = '';
  card(seen, '在册项目', (snap.projects || []).length || num(pf.project_count));
  card(seen, '业务线', (sw.lines || []).length || num(pf.business_line_count));
  card(seen, '云端运行单元', (sw.units || []).length, '容器 / cron / 定时器');
  card(seen, '已验证格子', ver, meas !== null ? `占可测 ${fmtPct(pct(ver, meas))}` : null);

  const blind = $('blind'); blind.textContent = '';
  const gap = (meas !== null && ver !== null) ? meas - ver : null;
  card(blind, '还没接入的格子', gap, '要各项目自己接', gap !== null && gap > 0);
  card(blind, '结构上测不了', unmeas, '已从分母剔除，不掺水');
  const ev = (cp && cp.evidence_summary) || {};
  card(blind, '未验证证据', num(ev.unverified), '骨架已建，尚未填真证据', (num(ev.unverified) || 0) > 0);
  card(blind, '覆盖健康', pf.coverage_health || '不确定', '缺口大时报降级才是对的');

  const lies = $('lies'); lies.textContent = '';
  const WORD = { pass: '通过', fail: '有问题', unknown: '查不了' };
  audits(snap, cp).forEach((a) => {
    const li = el('li');
    const top = el('div', 'top');
    top.append(el('span', 'tag ' + (a.state === 'pass' ? 'pass' : a.state === 'fail' ? 'fail' : 'unk'),
                  WORD[a.state]), el('span', 'ttl', a.title));
    const why = el('div', 'why');
    why.innerHTML = a.why;           // 只拼接本文件内的常量模板 + 数字,不含外部输入
    li.append(top, why); lies.append(li);
  });

  const body = document.querySelector('#todo tbody'); body.textContent = '';
  const rows = ((snap.flow && snap.flow.projects) || []).map((p) => {
    const v = num(p.verified) || 0, n = num(p.measurable) || num(p.cells_n) || 0;
    return { name: p.project || p.name || '—', v, n, gap: Math.max(n - v, 0) };
  }).sort((a, b) => b.gap - a.gap);
  if (!rows.length) {
    const tr = el('tr'); const td = el('td', '', '公开面没有逐项目明细（属私有面数据）');
    td.colSpan = 4; tr.append(td); body.append(tr);
  } else rows.forEach((r) => {
    const tr = el('tr');
    tr.append(el('td', '', r.name), el('td', 'num', `${r.v}/${r.n}`), el('td', 'num', r.gap),
              el('td', '', r.gap === 0 ? '已接入' : r.v === 0 ? '完全没接' : '接了一部分'));
    body.append(tr);
  });

  $('foot').textContent = '本页由 /data/snapshot.json 与 /data/control-plane.json 客户端派生，'
    + '不新增采集器、不新增定时任务、不调用任何模型或 Agent。'
    + '任何一项取不到时显示「不确定」，不会显示为健康。';
}

function fail(msg) {
  const w = document.querySelector('.wrap');
  w.textContent = '';
  const box = el('div', 'err');
  box.append(el('div', '', '中枢体检数据不可用：' + msg),
             el('div', '', '★ 此时状态为「不确定」，不代表系统健康 —— 取不到数据永远不等于没问题。'));
  w.append(box);
  $('at').textContent = '数据不可用';
}

const get = (u) => fetch(`${u}?t=${Date.now()}`, { cache: 'no-store' })
  .then((r) => { if (!r.ok) throw new Error(`${u} HTTP ${r.status}`); return r.json(); });

Promise.all([get('/data/snapshot.json'), get('/data/control-plane.json').catch(() => null)])
  .then(([snap, cp]) => { if (!snap) throw new Error('快照为空'); render(snap, cp); })
  .catch((e) => fail(e && e.message ? e.message : String(e)));
})();
