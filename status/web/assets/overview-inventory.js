/* The homepage and the detail views read the same public snapshots. */
const OverviewInventory = (() => {
  const D = OverviewData;
  const {esc, num, fmt, noData, stamp, sourceEpoch} = D;
  const runningStates = new Set(['在跑', 'online', 'run', 'running', 'active', 'healthy']);
  const unitStates = {
    scheduled: ['按时触发', ''], failed: ['故障', 'bad'], down: ['故障', 'bad'],
    exited: ['已停止', 'warn'], inactive: ['未运行', 'warn'], 'inactive/dead': ['未运行', 'warn'],
    '已停': ['已停止', 'warn'], 'activating/start': ['启动中', 'warn'],
  };
  const status = value => runningStates.has(value) ? ['在跑', 'good']
    : typeof value === 'string' && value.startsWith('停用') ? ['停用', 'warn'] : unitStates[value] || [value || '无记录', ''];
  const money = (value, currency = 'A$') => num(value) === null ? noData : currency + fmt(value, '', 2);
  const list = values => Array.isArray(values) ? values.filter(value => value != null && value !== '').join('、') || null : null;
  const yesNo = (value, yes = '是', no = '否') => value === true ? yes : value === false ? no : null;

  function attention(snapshot) {
    if (!snapshot) return [];
    const levels = {bad: '待处理', warn: '需关注'};
    const entries = snapshot.attention ? [...snapshot.attention] : legacyOverviewAttention(snapshot);
    if (snapshot.attention && Date.now() / 1000 - snapshot.updated_epoch > 180) {
      entries.unshift({lv: 'bad', t: '快照停更', d: '采集已超过 3 分钟，元自愈会尝试重跑。'});
    }
    return entries.filter(row => levels[row.lv]).map(row => ({
      ...row, level: levels[row.lv],
      d: row.t === '备份异常'
        ? '上次备份 ' + stamp(snapshot.ops?.backup?.epoch ?? sourceEpoch(snapshot.ops?.backup?.at, '+00:00'))
        : row.d?.replace('等你给材料', '待补材料'),
    }));
  }

  function attentionColumns({col, badge, pluck}) {
    return [
      col('level', '严重度', 124, row => badge(row.level, row.lv),
        row => row.lv === 'bad' ? 0 : 1, pluck('level')),
      col('t', '事项', 302), col('d', '依据', 570),
    ];
  }

  function billing(snapshot) {
    return (snapshot?.cost?.items || []).map(row => ({
      ...row, state: row.retired ? '已退役' : row.cadence === 'yearly' ? '年付' : row.cadence === 'monthly' ? '月付' : row.cadence,
      renewal: yesNo(row.auto_renew, '已开', '未开'),
      price: num(row.amount) === null ? null : `${row.amount} ${row.currency || ''}`.trim(),
    }));
  }

  function billingColumns({col}) {
    return [
      col('name', '开支项', 164), col('month_cost_aud', '本月应扣 A$', 144, row => esc(money(row.month_cost_aud))),
      col('aud', '月摊 A$', 128, row => esc(money(row.aud))),
      col('month_cost_cny', '本月应扣 ¥', 145, row => esc(money(row.month_cost_cny, '¥'))),
      col('price', '原币价格', 122), col('state', '周期', 109),
      col('purchase', '最初购买', 134), col('this_month_renew', '本月续费日', 145),
      col('renewal', '自动续费', 126), col('note', '说明', 275),
    ];
  }

  const kinds = {systemd: '系统服务', container: '容器', cron: '定时任务', coolify: '部署登记'};
  const graphKinds = {service: 'systemd', container: 'container', timer: 'timer'};
  function inventory(arch, snapshot) {
    const nodes = arch?.nodes || [];
    const units = snapshot?.software?.units || [];
    const projects = new Map((snapshot?.projects || []).map(project => [project.name, project]));
    const nodeById = new Map(nodes.map(node => [node.id, node]));
    const records = new Map();
    for (const node of nodes) {
      const kind = graphKinds[node.kind];
      if (kind) records.set(kind + ':' + node.label, {kind, node, name: node.label});
    }
    for (const unit of units) {
      const key = unit.kind + ':' + unit.id;
      records.set(key, {...records.get(key), kind: unit.kind, unit, name: unit.id});
    }
    // The registration's schedule distinguishes dormant oneshot services from outages.
    const faults = new Set((arch?.dead || []).map(node => node.id));
    const outgoing = new Map();
    for (const edge of arch?.edges || []) {
      const target = nodeById.get(edge.to);
      if (!target) continue;
      if (!outgoing.has(edge.from)) outgoing.set(edge.from, []);
      outgoing.get(edge.from).push(target.label);
    }
    const runtime = [...records.values()].map(({kind, node, unit, name}) => {
      const raw = faults.has(node?.id) ? 'failed' : unit?.state === 'scheduled' ? 'scheduled' : node?.state || unit?.state || '';
      const [state, tone] = status(raw);
      const owner = node?.owner || unit?.owner || '未登记';
      return {
        name, node: node?.id, role: node?.role, kind, type: node?.role_cn || kinds[kind] || kind,
        state, tone, raw, owner, project: projects.get(owner)?.name || owner,
        domain: unit?.domain || list(node?.hosts), links: list(outgoing.get(node?.id)),
        detail: [...new Set([node?.detail, unit?.detail].filter(Boolean))].join(' · ') || null,
        source: node && unit ? '架构与运行快照' : node ? '架构采样' : '运行快照',
      };
    });
    const domains = D.routes(arch || {}, snapshot || {});
    const tunnels = new Map(nodes.filter(node => node.role === 'tunnel').map(node => [node.label, {node}]));
    for (const tunnel of arch?.tunnels || []) tunnels.set(tunnel.name, {...tunnels.get(tunnel.name), tunnel});
    const databases = runtime.filter(row => ['db', 'cache'].includes(row.role));
    const declarations = Array.from(projects.values()).filter(project => project.db && project.db !== '无').map(project => ({
      name: project.db, state: '项目登记', owner: project.name, type: '数据库／数据服务', detail: project.host,
      source: '运行快照',
    }));
    return {
      services: runtime.filter(row => row.kind === 'systemd' || row.role === 'monitor'),
      containers: runtime.filter(row => row.kind === 'container'),
      domains,
      tunnels: [...tunnels].map(([name, {node, tunnel}]) => {
        const [state, tone] = status(node?.state || tunnel?.status);
        return {name, state, tone, connections: tunnel?.connections ?? null, provider: tunnel?.status,
          ingress: list(tunnel?.ingress?.map(route => `${route.host || '默认规则'} → ${route.service || noData}`)),
          detail: node?.detail || null};
      }),
      databases: databases.concat(declarations),
      timers: runtime.filter(row => ['cron', 'timer'].includes(row.kind)),
      cost: billing(snapshot),
      attention: attention(snapshot),
    };
  }

  function serviceMap(rows, P) {
    const groups = new Map();
    for (const row of rows) {
      if (!groups.has(row.project)) groups.set(row.project, new Map());
      const states = groups.get(row.project);
      const item = states.get(row.state) || {count: 0, tone: row.tone};
      item.count++;
      states.set(row.state, item);
    }
    P.text('#service-map-summary', rows.length
      ? `${rows.length} 项 · 来自架构和运行快照`
      : '未取到服务状态，可刷新重试。');
    const ordered = [...groups].sort(([a], [b]) => (a === '未登记') - (b === '未登记') || a.localeCompare(b, 'zh-CN'));
    P.$('#service-map').innerHTML = ordered.map(([name, states]) => {
      const orderedStates = [...states].sort(([, a], [, b]) => marks.indexOf(a.tone) - marks.indexOf(b.tone));
      return `<div class="service-group">
        <a href="#inventory-services" class="group-name" data-service-group="${esc(name)}">${esc(name)}</a>
        <div class="service-strip" aria-hidden="true">${orderedStates.map(([, {count, tone}]) =>
          `<span class="${tone}" style="flex-grow:${count}"></span>`).join('')}</div>
        <ul aria-label="服务状态">${orderedStates.map(([state, {count, tone}]) =>
          `<li>${P.badge(`${state} ${count}`, tone)}</li>`).join('')}</ul>
      </div>`;
    }).join('');
  }
  const marks = ['bad', 'warn', '', 'good'];

  function mount(P) {
    const {$, col, table, pluck, badge} = P;
    const board = $('#home-tables');
    const stateBadge = row => badge(row.state, row.tone);
    const unitCols = [
      col('name', '名称', 218), col('state', '状态', 119, stateBadge),
      col('owner', '归属', 146), col('type', '类型', 125), col('domain', '域名', 375),
      col('links', '下游', 640), col('detail', '说明', 830), col('source', '来源', 137),
    ];
    const specs = [
      ['services', '服务', unitCols, '按归属', 'project'],
      ['containers', '容器', unitCols, '按状态', 'state'],
      ['domains', '域名', [
        col('host', '域名', 196), col('state', '状态', 129, row => badge(row.state, row.state === '断点' ? 'bad' : '')),
        col('tunnel', '隧道', 225), col('proxy', '代理', 225), col('service', '服务', 630),
        col('dependencies', '依赖', 465), col('probes', '探测', 420), col('findings', '依据', 520),
      ], '按状态', 'state', '/check.html?view=flows#routes'],
      ['tunnels', '隧道', [
        col('name', '名称', 167), col('state', '状态', 119, stateBadge), col('connections', '连接数', 113),
        col('provider', '平台状态', 133), col('ingress', '转发规则', 490), col('detail', '说明', 225),
      ], '按状态', 'state'],
      ['databases', '库', [
        col('name', '名称', 167), col('state', '状态', 119, stateBadge), col('owner', '归属', 142),
        col('type', '类型', 152), col('links', '下游', 520), col('detail', '说明', 373), col('source', '来源', 135),
      ], '按类型', 'type'],
      ['timers', '定时任务', unitCols, '按状态', 'state'],
      ['cost', '成本', billingColumns(P), '按周期', 'state', '/check.html?view=cost#bills'],
      ['attention', '待处理', attentionColumns(P), '按严重度', 'level', '/check.html#h-attention'],
    ];
    const tables = new Map(specs.map(([key, name, columns, label, filter, href]) => {
      const card = document.createElement('article');
      card.className = 'inventory-card';
      card.id = 'inventory-' + key;
      card.dataset.inventoryCategory = key;
      card.innerHTML = `<div class="section-head"><h3>${name}</h3>
        ${href ? `<a class="card-detail" href="${href}">明细<span class="sr-only"> · ${name}</span></a>` : ''}</div>
        <div class="table-slot" id="table-${key}"></div>`;
      board.append(card);
      return [key, table(card.querySelector('.table-slot'), name, columns, label, pluck(filter))];
    }));
    $('#service-map').addEventListener('click', event => {
      const control = event.target.closest('[data-service-group]');
      if (control) tables.get('services').pick(control.dataset.serviceGroup, false);
    });
    return (arch, snapshot) => {
      const rows = inventory(arch, snapshot);
      for (const [key, table] of tables) table.setRows(rows[key]);
      serviceMap(rows.services, P);
    };
  }

  return {mount, attention, attentionColumns, billing, billingColumns, money, list, yesNo, status};
})();
