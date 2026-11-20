(() => {
  const {esc, noData, stamp} = OverviewData;
  const {
    $, text, col, table, tableGroup, unitGroup, pluck, badge, graphLink, backupEpoch, selectedNode, detailUrl, detailView, start,
  } = OverviewPage;
  if (detailView) return;

  const alertTable = table('#attention-table', '待处理事项', [
    col('level', '严重度', 125, r => badge(r.level, r.lv === 'bad' ? 'bad' : r.lv === 'warn' ? 'warn' : 'good'),
      r => ({bad: 0, warn: 1, ok: 2})[r.lv], pluck('level')),
    col('t', '事项', 235), col('d', '依据', 300),
  ], '按严重度', pluck('level'));
  const inventoryColumns = [
    col('label', '名字', 258, r => graphLink(r.id, r.label)), col('role_cn', '类型', 124),
    col('state', '状态', 138, r => badge(r.state, r.state === '在跑' ? 'good' : '')),
    col('owner', '归属', 165), col('links', '连到', 380), col('detail', '说明', 420),
  ];
  const type = pluck('role_cn');
  const inventoryTools = $('#inventory-tools');
  const search = inventoryTools.querySelector('input');
  const select = inventoryTools.querySelector('select');
  const inventoryGroups = [
    ['ingress', '对外入口与通道'],
    ['runtime', '应用与底座'],
    ['services', '系统服务与监控'],
    ['timers', '定时任务'],
  ].map(([id, name]) => ({
    id, table: tableGroup($('#inventory-table'), 'units-' + id, name, inventoryColumns, '按类型', type,
      {sharedFilters: {search, select}}),
  }));
  const resetInventory = OverviewTable.bindFilters(search, select, () => {
    inventoryGroups.forEach(group => group.table.render());
    updateInventoryReset();
  });
  function updateInventoryReset() {
    if (search.value.trim() || select.value) inventoryTools.append(resetInventory);
    else resetInventory.remove();
  }
  const compareTable = table('#compare-table', '旧版 34 块对照', [
    col('name', '旧版区块', 180), col('state', '承接状态', 144, r => badge(r.state, r.tone)),
    col('where', '入口', 152, r => `<a href="${esc(r.href)}"${r.tone === 'warn' ? ' target="_blank" rel="noopener" aria-label="旧版（新标签页）"' : ''}>${esc(r.where)}</a>`), col('why', '范围或暂留原因', 315),
  ], '按承接状态', pluck('state'), {empty: '尚无对照记录'});

  function showAttention(snap) {
    if (!snap) {
      text('#attention-summary', '运行快照缺失，待处理事项暂无数据。');
      alertTable.setRows([]);
      return;
    }
    const levels = {bad: '待处理', warn: '需关注', ok: '正常'};
    let entries = snap.attention ? [...snap.attention] : legacyOverviewAttention(snap);
    if (Date.now() / 1000 - snap.updated_epoch > 180 && snap.attention) entries = [{lv: 'bad', t: '快照停更', d: '采集已超过 3 分钟，元自愈会尝试重跑。'}, ...entries];
    const bad = entries.filter(r => r.lv === 'bad').length;
    const warn = entries.filter(r => r.lv === 'warn').length;
    if (entries.some(row => row.t === '备份异常')) entries = entries.map(row => row.t === '备份异常'
      ? {...row, d: '上次备份 ' + stamp(backupEpoch(snap))} : row);
    text('#attention-summary', bad || warn
      ? `${bad} 项待处理，${warn} 项需关注。按旧版判据从严重到一般排列。`
      : '本次快照没有待处理或需关注的事项。');
    alertTable.setRows(entries.filter(r => r.lv !== 'ok').map(r => ({
      ...r, d: r.d?.replace('等你给材料', '待补材料'), level: levels[r.lv] || r.lv,
    })));
  }

  let initialNode = selectedNode();
  function showHealth(arch) {
    if (!arch) {
      text('#health-summary', '架构记录未取到。可刷新重试。');
      $('#healthbox').replaceChildren();
      OverviewTable.setOptions(select, []);
      inventoryGroups.forEach(group => group.table.setRows([]));
      updateInventoryReset();
      return;
    }
    const map = new Map(arch.nodes.map(n => [n.id, n.label]));
    const inventory = arch.nodes.map(n => ({...n, state: n.state || (n.role === 'domain' ? '对外记录' : '无运行状态'),
      owner: n.owner || '未登记', links: arch.edges.filter(e => e.from === n.id).map(e => map.get(e.to)).filter(Boolean).join('、') || '无连线',
      detail: n.detail || noData}));
    const buckets = new Map(inventoryGroups.map(group => [group.id, []]));
    OverviewTable.setOptions(select, inventory.map(type));
    inventory.forEach(node => buckets.get(unitGroup(node.role)).push(node));
    for (const {id, table: list} of inventoryGroups) {
      const rows = buckets.get(id);
      list.setRows(rows);
      const initial = initialNode && rows.find(node => node.id === initialNode);
      if (initial) requestAnimationFrame(() => list.focusRow(initial));
    }
    updateInventoryReset();
    initialNode = null;

    const box = (name, empty, items, label, tone) => `<article class="health-card"><h3>${name}</h3>${
      items.length ? `<p>${badge(`${items.length} 项`, tone)} ${label}</p><ul>${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>`
        : `<p class="good-text">${empty}</p>`}</article>`;
    const dup = (arch.duplicates || []).map(r => `${r.role_cn} ${r.count} 份（允许 ${r.allowed} 份）· ${r.items.join('、')}`);
    const dead = (arch.dead || []).map(r => `${r.label}（${r.state}）`);
    const stopped = (arch.stopped || []).map(r => `${r.label}（${r.state}）`);
    const orphans = arch.orphans || [];
    text('#health-summary', `${stamp(arch.generated_epoch)}（架构采样）。体检沿用部署规则与原架构结果。`);
    $('#healthbox').innerHTML = box('重复的公共部件', '未发现超出允许份数的部件。', dup, '核对角色和允许份数。', 'bad')
      + box('坏掉的服务', '未发现故障单元。定时器拉起的一次性服务按原规则处理。', dead, '需要核对服务故障。', 'bad')
      + box('停用待清理', '本次没有停用项。', stopped, '停用项按规则留 7 天回滚。', 'warn')
      + box('无连线的在跑容器', '本次没有无连线的在跑容器。', orphans, '核对还有谁在用。', 'warn');
  }

  const overview = '/legacy.html#/';
  const runtime = '/legacy.html#/runtime';
  const flow = '/legacy.html#/flow';
  const cost = '/legacy.html#/cost';
  const github = '/legacy.html#/github';
  const timeline = '/legacy.html#/timeline';
  const costDetail = hash => detailUrl('cost', hash);
  const moved = (name, where, href, why) => ({name, where, href, why, state: '新版已承接', tone: 'good'});
  const old = (name, href, why) => ({name, where: '旧版', href, why, state: '暂留旧版', tone: 'warn'});
  const compare = [
    moved('待处理事项', '体检清单', '#h-attention', '沿用严重度和快照告警判据，由各线处理。'),
    moved('容量与升级', '成本与容量', costDetail('#h-capacity'), '显示结论、部署余量、阈值和实测。'),
    moved('供应商', '成本与容量', costDetail('#h-vendors'), '沿用供应商状态、成本、风险和健康。'),
    old('活跃度', overview, '保留一年提交与部署活动视图。'),
    old('关键趋势', overview, '新版历史承接内存和磁盘。部署趋势与成本构成暂留旧版。'),
    old('治理登记合规', runtime, '保留运行单元登记合规率与违规明细。'),
    old('业务基线纵向切片', runtime, '保留代码到自愈的九段实测矩阵。'),
    old('端到端链路', runtime, '保留每条业务线从代码到自愈；新版逻辑链描述对外访问。'),
    old('端到端健康历史', runtime, '保留逐小时业务基线健康分。'),
    old('自动探测到的运行单元', runtime, '新版有架构节点清单。旧版保留 Docker、系统服务、定时任务和 Coolify 的探测台账。'),
    old('业务基线矩阵', flow, '保留各项目的自定义阶段及实测覆盖。'),
    old('已知缺陷', flow, '保留缺陷到具体阶段的关联。'),
    old('源级爆炸半径', flow, '保留上游故障的影响范围和计数。'),
    old('健康趋势', flow, '保留业务格子逐日最差状态。'),
    old('项目清单', runtime, '新版组织图可查归属。各项目内存、存储和部署备份细节仍在旧版。'),
    old('本站实时访问', runtime, '保留访客、探测和页面同步分类。'),
    moved('主机趋势', '历史', '/#history', '内存和磁盘有 24 小时、7 天、30 天曲线。负载和容器见历史表。'),
    old('耦合关系', runtime, '保留业务项目之间的存储与跟进关系。'),
    old('自愈引擎', runtime, '保留主自愈、元自愈及每条规则详情。'),
    old('最近动作', runtime, '保留最近自愈和部署事件。'),
    old('账单明细', cost, '新版承接本月汇总；逐项实付构成、费用对比和价格编辑仍在旧版。'),
    old('免费额度用量', cost, '保留各平台额度、上涨趋势和预计触顶。'),
    old('外部服务', cost, '新版供应商表承接主要供应商。旧版保留全量外部状态。'),
    old('AI 供应商账务', cost, '保留供应商账务和来源状态。'),
    old('仓库宇宙', github, '保留仓与项目之间的交互星图。'),
    old('Actions 收费风险', github, '保留官方账单读数与收费风险。'),
    old('贡献网格', github, '保留公开仓逐日贡献网格。'),
    old('仓库', github, '保留仓库清单与工程指标。'),
    old('工程可视化', github, '保留工程对比图表。'),
    old('访问流量', github, '保留逐仓查看、克隆、来源与路径。'),
    old('Monorepo 子项目', github, '保留子项目与来源仓关系。'),
    moved('续费时间轴', '成本与容量', costDetail('#h-renew'), '按同一订阅账显示日期、剩余天数、停机和自动续费状态。'),
    old('订阅历史', timeline, '保留旧版的历史分期和时间轴。'),
    moved('盯不住的订阅', '成本与容量', costDetail('#h-renew'), '单独标出无日期订阅与退役订阅。'),
  ];

  compareTable.setRows(compare);
  text('#compare-summary', `已承接 ${compare.filter(row => row.tone === 'good').length} / ${compare.length} 块。`);
  start(['snapshot', 'arch'], ({snapshot, arch}) => {
    showAttention(snapshot);
    showHealth(arch);
  });
})();
