/* Pure projections of the public status files. Also accepts the pre-1478 schema. */
const OverviewData = (() => {
  const noData = '无数据';
  const zone = 'Australia/Sydney';
  const dayFormat = new Intl.DateTimeFormat('en-CA', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
  });
  const clockFormat = new Intl.DateTimeFormat('en-GB', {
    timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });
  const dates = new Intl.DateTimeFormat('zh-CN', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short',
  });

  function num(value) {
    if (typeof value !== 'number' && (typeof value !== 'string' || !value.trim())) return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function percent(value) {
    const number = num(value);
    return number !== null && number >= 0 && number <= 100 ? number : null;
  }

  const esc = value => String(value ?? '').replace(/[&<>"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
  })[char]);

  function fmt(value, unit = '', digits = 0) {
    const n = num(value);
    return n === null ? noData : n.toFixed(digits) + unit;
  }

  const day = epoch => dayFormat.format(new Date(epoch * 1000));
  const clock = epoch => clockFormat.format(new Date(epoch * 1000));
  const stamp = epoch => num(epoch) > 0 ? dates.format(new Date(epoch * 1000)) : noData;

  function sourceEpoch(value, offset = '+08:00') {
    if (num(value) > 0) return num(value);
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(value)) return null;
    const iso = value.trim().replace(' ', 'T');
    return num(Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/.test(iso) ? iso : iso + offset) / 1000);
  }

  function lastDates(end, count) {
    // Arithmetic on calendar labels keeps both Sydney daylight-saving days intact.
    const midnight = Date.parse(day(end) + 'T00:00:00Z');
    return Array.from({length: count}, (_, i) =>
      new Date(midnight - (count - 1 - i) * 86400000).toISOString().slice(0, 10));
  }

  function running(snapshot) {
    if ('containers_running' in (snapshot.host || {})) return num(snapshot.host.containers_running);
    const docker = (snapshot.software?.units || []).filter(unit => unit.kind === 'container');
    return docker.length ? docker.filter(unit => unit.state === 'running').length : null;
  }

  function series(history, tier, end) {
    const raw = history[tier] || {};
    const epochs = raw.t || [];
    // The legacy collector left-pads retained load values when upgrading old archives.
    const loads = raw.load || [];
    const offset = Math.max(0, epochs.length - loads.length);
    return epochs.map((t, i) => ({
      t: num(t), date: num(t) > 0 ? day(t) : '',
      mem: percent(raw.mem?.[i]), disk: percent(raw.disk?.[i]), load: num(loads[i - offset]),
    })).filter(p => p.date && p.t <= end);
  }

  const hasResources = point => point.mem !== null || point.disk !== null;

  function plot(history, period, end, snapshot) {
    const current = snapshot && num(snapshot.updated_epoch) === end ? {
      t: end, date: day(end), mem: percent(snapshot.host?.mem_pct), disk: percent(snapshot.host?.disk_pct),
      source: '运行快照（snapshot.json）',
    } : null;
    let points;
    if (period === 30) {
      // Prefer Sydney-day records, use the latest readings from retained samples for older days.
      const archive = new Map(series(history, 'day', end).map(p => [p.date, p]));
      const recent = new Map();
      for (const point of [...series(history, 'hour', end), ...series(history, 'min', end)]) {
        if (!hasResources(point) || point.t < (recent.get(point.date)?.t ?? 0)) continue;
        recent.set(point.date, point);
      }
      points = lastDates(end, period).map(date => {
        const exact = history.daily_sydney?.[date];
        const sampled = recent.get(date);
        const old = archive.get(date);
        const t = Date.parse(date + 'T00:00:00Z') / 1000;
        if (current?.date === date && hasResources(current)) return {...current, t};
        return {
          date, t,
          mem: percent(exact?.mem_last) ?? sampled?.mem ?? old?.mem ?? null,
          disk: percent(exact?.disk_pct) ?? sampled?.disk ?? old?.disk ?? null,
          source: '历史数据（history.json） · ' + (exact ? '悉尼日统计' : '可用旧档'),
        };
      });
    } else {
      const tier = period === 1 ? 'min' : 'hour';
      const source = '历史数据（history.json） · ' + (tier === 'min' ? '10 分钟平均' : '小时档');
      points = series(history, tier, end).filter(p => p.t >= end - period * 86400)
        .map(p => ({...p, source}));
      if (tier === 'min') {
        // 24 小时按 10 分钟取平均，线不再满是锯齿（Owner 11-20「可视化画得太难看了」）
        const buckets = new Map();
        for (const p of points) {
          const k = Math.floor(p.t / 600) * 600;
          const b = buckets.get(k) || {t: k, n: 0, memSum: 0, memN: 0, diskSum: 0, diskN: 0, last: p};
          if (p.mem !== null && p.mem !== undefined) { b.memSum += p.mem; b.memN += 1; }
          if (p.disk !== null && p.disk !== undefined) { b.diskSum += p.disk; b.diskN += 1; }
          b.last = p; buckets.set(k, b);
        }
        points = [...buckets.values()].sort((a, b) => a.t - b.t).map(b => ({...b.last, t: b.t,
          mem: b.memN ? b.memSum / b.memN : null, disk: b.diskN ? b.diskSum / b.diskN : null,
          source: '历史数据（history.json） · 10 分钟平均'}));
      }
      if (current && hasResources(current)) {
        const bucketSeconds = tier === 'hour' ? 3600 : 600;
        const currentBucket = Math.floor(end / bucketSeconds) * bucketSeconds;
        if (points.at(-1)?.t >= currentBucket) points.pop();
        points.push(current);
      }
    }
    const first = points.findIndex(hasResources);
    while (points.length && !hasResources(points.at(-1))) points.pop();
    return first === -1 ? [] : points.slice(first);
  }

  function currentRow(snapshot) {
    const host = snapshot.host;
    const mem = percent(host.mem_pct);
    const load = num(host.load1);
    const match = /^(\d+)\/(\d+)$/.exec(snapshot.summary?.services_online ?? '');
    const online = num(match?.[1]), total = num(match?.[2]);
    return {
      date: '现在', current: true, memAvg: mem, memPeak: mem, disk: percent(host.disk_pct),
      diskBytes: num(host.disk_used_b), loadAvg: load, loadPeak: load,
      containers: running(snapshot), probes: online !== null && total > 0 && online <= total ? 100 * online / total : null,
      source: `运行快照（${stamp(snapshot.updated_epoch)} · 悉尼 · 负载为 1 分钟均载）`,
    };
  }

  function daily(history, period, end) {
    const count = period === 30 ? period : 1 + (Date.parse(day(end)) - Date.parse(day(end - period * 86400))) / 86400000;
    const dates = lastDates(end, count);
    const groups = new Map(dates.map(date => [date, {hour: [], min: []}]));
    for (const tier of ['hour', 'min']) {
      for (const sample of series(history, tier, end)) groups.get(sample.date)?.[tier].push(sample);
    }
    const mean = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
    const peak = values => values.length ? Math.max(...values) : null;
    const values = (samples, key) => samples.map(sample => sample[key]).filter(value => value !== null);
    return dates.slice().reverse().map(date => {
      const {hour, min} = groups.get(date);
      const exact = history.daily_sydney?.[date];
      const hasBeforeMin = hour.some(p => p.t + 3600 <= (min[0]?.t ?? Infinity));
      const sampleMean = hasBeforeMin ? hour : min;
      const memHistory = values(hour, 'mem').concat(values(min, 'mem'));
      const loadHistory = values(hour, 'load').concat(values(min, 'load'));
      const maxMem = peak([...memHistory, num(exact?.mem_peak)].filter(v => v !== null));
      const disk = exact?.disk_pct ?? [...hour, ...min].filter(p => p.disk !== null)
        .sort((a, b) => b.t - a.t)[0]?.disk ?? null;
      const maxLoad = peak([...loadHistory, num(exact?.load_peak)].filter(v => v !== null));
      return {
        date,
        memAvg: exact?.mem_samples ? exact.mem_sum / exact.mem_samples : mean(values(sampleMean, 'mem')),
        memPeak: maxMem,
        disk, diskBytes: exact?.disk_used_b ?? null,
        loadAvg: exact?.load_samples ? exact.load_sum / exact.load_samples
          : hasBeforeMin ? null : mean(values(min, 'load')),
        loadPeak: maxLoad,
        containers: exact?.containers_running ?? null,
        probes: exact?.probes_total ? 100 * exact.probes_up / exact.probes_total : null,
        source: exact ? `日统计（内存 ${exact.mem_samples || 0} 次）` : (hour.length || min.length ? '旧档抽样' : noData),
      };
    });
  }

  function hostOf(value) {
    try {
      const url = new URL(value);
      return ['https:', 'http:'].includes(url.protocol) ? url.hostname.toLowerCase() : '';
    } catch {
      return '';
    }
  }

  const entryRoles = new Set(['proxy', 'tunnel']);
  const linkTypes = new Set(['路由', '转发', '入口']);
  const names = nodes => [...new Set(nodes.map(n => n.label))];
  const uniq = nodes => [...new Map(nodes.map(n => [n.id, n])).values()];
  const inactive = node => node.state && !['在跑', 'active', 'running'].includes(node.state);

  function routes(arch, snapshot) {
    const nodes = new Map((arch.nodes || []).map(n => [n.id, n]));
    const outgoing = new Map();
    for (const e of arch.edges || []) {
      if (!nodes.has(e.to)) continue;
      if (!outgoing.has(e.from)) outgoing.set(e.from, []);
      outgoing.get(e.from).push(e);
    }
    const domains = new Map([...nodes.values()].filter(n => n.role === 'domain')
      .map(n => [n.label.toLowerCase(), {node: n, probes: [], ingress: []}]));
    const entry = host => {
      if (!domains.has(host)) domains.set(host, {probes: [], ingress: []});
      return domains.get(host);
    };
    for (const p of snapshot.projects || []) {
      const host = hostOf(p.url);
      if (host) entry(host).probes.push(p);
    }
    for (const tunnel of arch.tunnels || []) {
      for (const record of tunnel.ingress || []) {
        const host = record.host?.toLowerCase();
        if (host) entry(host).ingress.push({tunnel, record});
      }
    }

    function neighbors(id, filter) {
      return (outgoing.get(id) || []).filter(filter).map(e => nodes.get(e.to));
    }

    function matches(edge, domain) {
      if (edge.hosts) return edge.hosts.some(host => host.toLowerCase() === domain);
      return (nodes.get(edge.to).hosts || []).some(host => host.toLowerCase() === domain);
    }

    return [...domains].sort(([a], [b]) => a.localeCompare(b)).map(([host, {node, probes, ingress}]) => {
      const issues = [];
      const gaps = [];
      const direct = neighbors(node?.id, e => !e.hosts || e.hosts.some(h => h.toLowerCase() === host));
      const tunnels = direct.filter(n => n.role === 'tunnel');
      // The collector omits entry edges for stopped tunnels. Keep the recorded
      // ingress visible if it is the only evidence for a host.
      if (!tunnels.length && !direct.length) {
        for (const {tunnel} of ingress) {
          const stopped = nodes.get('tun:' + tunnel.name);
          if (stopped) tunnels.push(stopped);
        }
      }
      const next = [];
      let hasDirectService = false;
      for (const tunnel of uniq(tunnels)) {
        if (inactive(tunnel)) {
          issues.push(`隧道已停 · ${tunnel.label}`);
          continue;
        }
        const via = neighbors(tunnel.id, e => e.kind === '转发' && matches(e, host));
        if (!via.length) {
          // Old snapshots grouped all forwarding from a shared tunnel in one edge
          // list. Only the standard local proxy ports disambiguate that proxy.
          const ingressToProxy = ingress.some(({tunnel: t, record}) => t.name === tunnel.label
            && /^https?:\/\/(127\.0\.0\.1|localhost):(80|443)\/?$/.test(record.service || ''));
          if (ingressToProxy) {
            const proxies = neighbors(tunnel.id, e => e.kind === '转发' && !e.hosts
              && nodes.get(e.to).role === 'proxy');
            if (proxies.length === 1) via.push(proxies[0]);
          }
        }
        if (!via.length) gaps.push(`隧道未取得转发记录 · ${tunnel.label}`);
        hasDirectService ||= via.some(n => n.role !== 'proxy');
        next.push(...via);
      }

      const proxies = uniq(direct.filter(n => n.role === 'proxy').concat(next.filter(n => n.role === 'proxy')));
      const services = next.filter(n => !entryRoles.has(n.role));
      for (const proxy of proxies) {
        if (inactive(proxy)) issues.push(`代理已停 · ${proxy.label}`);
        let targets = neighbors(proxy.id, e => e.kind === '路由' && matches(e, host));
        if (!targets.length && tunnels.length) {
          // Old records also carry a direct domain-to-container route for a
          // proxied tunnel. Only use it after proving the tunnel reaches the proxy.
          targets = direct.filter(n => !entryRoles.has(n.role));
        }
        if (!targets.length) gaps.push(`代理未取得服务路由 · ${proxy.label}`);
        services.push(...targets);
      }
      if (!tunnels.length) services.push(...direct.filter(n => !entryRoles.has(n.role)));
      const backends = uniq(services);
      if (!backends.length) gaps.push('未取得服务路由');

      const deps = [];
      const seen = new Set(backends.map(n => n.id));
      const queue = [...backends];
      while (queue.length) {
        for (const dep of neighbors(queue.shift().id, e => !linkTypes.has(e.kind))) {
          if (seen.has(dep.id)) continue;
          seen.add(dep.id);
          deps.push(dep);
          queue.push(dep);
        }
      }
      for (const n of [...backends, ...deps].filter(inactive)) issues.push(`运行单元已停 · ${n.label}`);

      const probeLabels = {run: '在线', access: '在线（受访问控制）', down: '探测失败', stale: '业务判据未通过'};
      const probeText = probes.map(p => {
        const path = new URL(p.url).pathname;
        if (p.status === 'down') issues.push(`探测失败 · ${p.name}`);
        return `${p.name}${path === '/' ? '' : ' ' + path} · ${probeLabels[p.status] || '无探测结果'}${p.health_note ? ' · ' + p.health_note : ''}`;
      });
      const cloud = tunnels.length || ingress.length ? 'Cloudflare 隧道记录'
        : probes.some(p => /^Cloudflare edge/.test(p.host || '')) ? 'Cloudflare 边缘（项目登记）' : '未取得记录';
      return {
        host, node: node?.id, cloud,
        tunnel: names(tunnels).join('、') || '未取得记录',
        proxy: names(proxies).join('、') || (hasDirectService ? '隧道直达服务' : '未取得记录'),
        service: names(backends).join('、') || noData,
        dependencies: names(deps).join('、') || '未记录依赖',
        probes: probeText.join(' / ') || '无探测结果',
        findings: [...new Set(issues.concat(gaps))].join(' / ') || '已记录主要路由',
        state: issues.length ? '断点' : gaps.length ? '路由缺证' : '有路由记录',
      };
    });
  }

  function organization(arch, snapshot) {
    const lines = new Map();
    const missing = [];
    const projects = snapshot.projects || [];
    const nodes = arch.nodes || [];
    const projectNames = new Set(projects.map(p => p.name));
    const group = (line, project) => {
      if (!lines.has(line)) lines.set(line, new Map());
      const work = lines.get(line);
      if (!work.has(project)) work.set(project, []);
      return work.get(project);
    };
    const byHost = new Map();
    for (const p of projects) {
      const host = hostOf(p.url);
      if (host && new URL(p.url).pathname === '/') byHost.set(host, p.name);
      const line = p.line || p.workspace || p.owner || (nodes.some(n => n.owner === p.name) ? p.name : '');
      if (line) group(line, p.name);
      else missing.push({label: p.name, type: '项目', project: p.name, detail: host || p.host || noData});
    }
    for (const n of nodes) {
      const project = projectNames.has(n.owner) ? n.owner
        : n.project || (n.role === 'domain' ? byHost.get(n.label) : '');
      if (n.owner) group(n.owner, project || '未登记项目').push(n);
      else missing.push({label: n.label, type: n.role_cn || n.role, project: project || '未登记项目',
        detail: n.detail || noData, id: n.id, role: n.role});
    }
    return {lines, missing};
  }

  return {noData, num, percent, esc, fmt, day, clock, stamp, sourceEpoch, series, plot, currentRow, daily, routes, organization};
})();

// Captured snapshots predate the collector's attention field.
function legacyOverviewAttention(s){
  const out=[]; const push=(lv,t,d)=>out.push({lv,t,d});
  const h=s.host||{}, o=s.ops||{}, g=s.github||{};
  (s.projects||[]).filter(p=>p.status==='down').forEach(p=>
    push('bad',`${p.name} 探测失败`,`地址 ${p.url||'内部服务'} 未返回可用状态${p.health_note?'('+p.health_note+')':''}`));
  (s.projects||[]).filter(p=>p.status==='stale').forEach(p=>
    push('warn',`${p.name} 业务判据未通过`,`服务在线但${p.health_note||'数据不新鲜'}`));
  if(h.disk_pct>=85)push('bad','磁盘吃紧',`已用 ${h.disk_pct}%,自愈会在 85% 自动清理`);
  else if(h.disk_pct>=75)push('warn','磁盘偏高',`已用 ${h.disk_pct}%,低于 85% 触发线`);
  if(h.mem_pct>=90)push('warn','内存偏高',`已用 ${h.mem_pct}%`);
  (s.usage||[]).forEach(u=>{if(!u.limit)return;const p=u.used/u.limit*100;
    /* ★ 滚动保留的项(GitHub 备份:满 30 份自动删最旧)满额是设计内常态,份数占比不是风险。
       2026-09-30 之前它「30/30 = 100%」被报成「接近额度」并扣健康分 —— 假红。
       真正该报的只有两件:最新一份太久没更新(备份停了)、最近一次上传失败。 */
    if(u.bounded){
      if(u.latest_epoch&&s.updated_epoch&&u.max_age_h){
        const ageH=(s.updated_epoch-u.latest_epoch)/3600;
        if(ageH>u.max_age_h)push('bad',`${u.label} 已 ${Math.round(ageH)} 小时没更新`,`最新一份备份超过 ${u.max_age_h} 小时,备份可能停了`);
      }
      if(u.last_upload_code&&!String(u.last_upload_code).startsWith('2'))
        push('bad',`${u.label} 最近一次上传失败`,`GitHub 返回 ${u.last_upload_code}`);
      return;
    }
    if(p>=80)push('bad',`${u.label} 接近额度`,`${p.toFixed(1)}% 已用`);
    else if(p>=50)push('warn',`${u.label} 过半`,`${p.toFixed(1)}% 已用${u.eta_date?' · 预计触顶 '+u.eta_date:''}`);});
  if(o.cert&&o.cert.days!=null&&o.cert.days<=14)push('warn','证书临近到期',`${o.cert.date} · 剩 ${o.cert.days} 天(自动续期)`);
  /* ★ 订阅到期:**全部订阅**,提前 7 天变红。
     老口径只看 OVH 一条 —— 7 条订阅覆盖 1 条,实测 ChatGPT Pro20x 剩 7 天时页面一点提示都没有。
     又一次「口径只覆盖子集却当成全局」。 */
  const sb=o.subs||{};
  (sb.items||[]).filter(x=>x.level!=='ok').forEach(x=>
    push(x.level, `${x.name} ${x.days<=0?'已到期':(x.end_only?'即将到期停机':'即将扣费')}`,
      `${x.date} · 剩 ${x.days} 天${x.end_only?' · 到期直接停机':''}${x.auto_renew?' · 自动续费开着':' · 自动续费没开'}`));
  /* ★ 算不出到期日的**必须单独报**。算不出不等于没事 —— 这类最容易漏。 */
  if((sb.blind||[]).length)
    push('warn', `${sb.blind.length} 条订阅盯不住`,
      sb.blind.map(x=>x.name).join('、')+' —— 没登记购买日期，到期前不会有任何提醒');
  if(o.backup&&!o.backup.ok)push('bad','备份异常',`上次备份 ${o.backup.at||'无记录'}`);
  if(g.available&&g.ci_fail>0)push('warn',`${g.ci_fail} 个仓 CI 失败`,'默认分支最近一次运行未通过');
  (s.selfheal&&s.selfheal.rules||[]).filter(r=>!r.armed).forEach(r=>
    push('warn',`自愈未就绪:${r.name}`,r.detail||''));
  if(s.updated_epoch&&(Date.now()/1000-s.updated_epoch)>180)
    push('bad','快照停更','采集器可能已停,元自愈会尝试自动重跑');
  /* ★ 以下三段以前**完全没进这个函数**,所以顶栏会在「建议升级 / 8 个项目未登记 /
     有可疑格」当屏的情况下依然显示「全部健康」—— 徽章只看基础设施,等于替另外三个
     维度背书。健康分同理(它就是数 insights 得出的)。这类「口径只覆盖一部分却对外
     宣称全局」的假绿,是这个站反复犯的同一个错。 */
  const cap=s.capacity;
  if(cap&&cap.level==='bad')push('bad','容量告急:'+cap.verdict,cap.why||'');
  else if(cap&&cap.level==='warn')push('warn','容量紧张:'+cap.verdict,cap.why||'');
  const sw=s.software;
  if(sw&&(sw.unregistered||[]).length)
    push('bad',`${sw.unregistered.length} 个运行单元未登记`,
      '部署即登记:主机上真在跑但没有归属,属治理违规');
  const fl=s.flow;
  if(fl){
    const un=(fl.unregistered||[]).length;
    if(un)push('warn',`${un} 个项目未登记业务流`,'有治理文件却没发布 flow.yaml 到 main');
    const ft=fl.totals||{};
    const stuck=(ft.blocked||0)+(ft.blocked_by_input||0)+(ft.not_built||0);
    if(stuck)push('bad',`业务流 ${stuck} 格不通`,
      `待补材料 ${ft.blocked_by_input||0} · 断了 ${ft.blocked||0} · 没做 ${ft.not_built||0}`);
    const susp=(ft.mismatch||0)+(ft.coupling_violation||0);
    if(susp)push('warn',`业务流 ${susp} 格可疑`,
      `自报≠实测 ${ft.mismatch||0} · 违反耦合 ${ft.coupling_violation||0}`);
    // 「没有实测」本身就是一条待办 —— 不标出来,0% 覆盖的自报绿就会被当成已验证
    // ★ 分母同样用可测格子(见指标卡处的说明),否则这条提醒的门槛会被
    //   「大片没做」拉低,反而在真的没实测时不报。
    const meas=fl.measurable_total||0;
    if(meas&&(fl.verified_total||0)*2<meas)
      push('warn','业务流实测覆盖不足一半',
        `${fl.verified_total||0}/${meas} 格可测的有机器实测,其余仅被测方自报`);
  }
  if(!out.length)push('ok','一切正常','没有待处理事项');
  const rank={bad:0,warn:1,ok:2};
  return out.sort((a,b)=>rank[a.lv]-rank[b.lv]);
}
