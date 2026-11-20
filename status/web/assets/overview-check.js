(() => {
  const {esc, noData, stamp} = OverviewData;
  const {
    $, text, col, table, tableGroup, unitGroup, pluck, badge, graphLink, selectedNode, detailUrl, detailView, start,
  } = OverviewPage;
  if (detailView) {
    if (!document.querySelector('template[data-detail="' + CSS.escape(detailView) + '"]')) {
      OverviewRecords.start(detailView, OverviewPage);
    }
    return;
  }

  const alertTable = table('#attention-table', '待处理事项', OverviewInventory.attentionColumns(OverviewPage), '按严重度', pluck('level'));
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
    col('name', '旧版区块', 183), col('state', '承接状态', 140, row => badge(row.state, 'good')),
    col('where', '已放在哪', 210, row => row.links.map(([label, href]) =>
      `<a href="${esc(href)}">${esc(label)}</a>`).join(' · ')),
    col('why', '承接范围', 385),
  ], '按内容', pluck('group'), {empty: '尚无对照记录'});

  function showAttention(snapshot) {
    const rows = OverviewInventory.attention(snapshot);
    const bad = rows.filter(row => row.lv === 'bad').length;
    const warn = rows.length - bad;
    text('#attention-summary', !snapshot ? '运行快照缺失，待处理事项暂无数据。'
      : rows.length ? `${bad} 项待处理，${warn} 项需关注。按严重度排列。` : '本次快照没有待处理或需关注的事项。');
    alertTable.setRows(rows);
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

  const detail = (label, view, anchor = view) => [label, detailUrl(view, '#' + anchor)];
  const home = (label, anchor) => [label, '/#' + anchor];
  const compare = [
    ['待处理事项', '总览', '完整事项、严重度和触发依据。', home('首页 · 待处理', 'inventory-attention')],
    ['容量与升级', '总览', '结论、部署余量、阈值和实测。', detail('容量与升级', 'cost', 'capacity')],
    ['供应商', '总览', '供应商状态、开支、风险和健康。', detail('供应商', 'cost', 'vendors')],
    ['活跃度', '总览', '逐日提交和部署次数。', detail('全年逐日活动', 'activity')],
    ['关键趋势', '总览', '资源使用率放在图表，部署与成本构成放在表格。',
      home('内存与磁盘', 'trend'), detail('近 7 天部署', 'activity', 'deploy-trend'), detail('成本构成', 'cost', 'bills')],
    ['治理登记合规', '运行', '登记率、未登记单元、自洽异常和实判段数。',
      detail('治理登记合规', 'baseline', 'compliance'), detail('未登记与异常', 'baseline', 'violations')],
    ['业务基线纵向切片', '运行', '每条运行线的九段判定和证据。', detail('九段运行矩阵', 'baseline')],
    ['端到端链路', '运行', '代码、持续集成、部署、运行、入口、数据、备份、监控、自愈逐段直读。',
      detail('九段证据', 'baseline'), detail('对外逻辑链', 'flows', 'routes')],
    ['端到端健康历史', '运行', '按悉尼日期保留每个小时的整体健康分。', detail('端到端健康历史', 'baseline', 'baseline-history')],
    ['自动探测到的运行单元', '运行', '容器、系统服务、定时任务、部署记录与单元增减流水。',
      detail('全部探测单元', 'units', 'discovered'), detail('单元增减', 'units', 'unit-changes')],
    ['业务基线矩阵', '业务流', '各项目的自定义阶段、证据、实测覆盖和异常。',
      detail('业务基线矩阵', 'business'), detail('登记与自洽异常', 'business', 'business-registration')],
    ['已知缺陷', '业务流', '缺陷与项目、基线和阶段的关系。', detail('已知缺陷', 'business', 'defects')],
    ['源级爆炸半径', '业务流', '上游源、关联数量、受影响的基线和耦合规则。', detail('源级影响范围', 'business', 'blast-radius')],
    ['健康趋势', '业务流', '逐日归档的通畅、有缺陷、阻断格数。', detail('逐日健康趋势', 'business', 'business-trend')],
    ['项目清单', '运行', '项目归属、内存、存储、数据库、部署和备份。',
      detail('项目清单', 'projects'), detail('组织归属', 'org')],
    ['本站实时访问', '运行', '真实浏览、探测、同步、错误和逐时段样本。', detail('本站实时访问', 'traffic', 'live-traffic')],
    ['主机趋势', '运行', '两条资源曲线、每日统计及留存的每日原始读数。',
      detail('历史趋势', 'history'), detail('每日资源表', 'history', 'daily'), detail('逐日归档', 'history', 'archive'), detail('当前运行读数', 'projects', 'host-facts')],
    ['耦合关系', '运行', '列出存储、部署和依赖关系及项目数。', detail('耦合关系', 'projects', 'dependencies')],
    ['自愈引擎', '运行', '主自愈、元自愈及逐条规则、判定和执行记录。', detail('自愈引擎', 'projects', 'selfheal')],
    ['最近动作', '运行', '自愈和部署事件同表，保留时间和结果。', detail('最近动作', 'projects', 'events')],
    ['账单明细', '成本', '本月扣款、月摊、年化及逐项价格。价格编辑入口保留。',
      home('首页 · 成本', 'inventory-cost'), detail('账单明细', 'cost', 'bills')],
    ['免费额度用量', '成本', '用量、上限、来源和预计触顶日期。', detail('免费额度用量', 'usage')],
    ['外部服务', '成本', '每项外部服务的状态与依据。', detail('外部服务状态', 'usage', 'external')],
    ['AI 供应商账务', '成本', '保留余额、已花费和来源状态。', detail('AI 供应商账务', 'usage', 'supplier-billing')],
    ['仓库宇宙', 'GitHub', '公开节点、项目、子项目和耦合连线转成可排序的表。',
      detail('仓库与项目节点', 'repos', 'repo-nodes'), detail('仓库与项目关系', 'repos', 'repo-links')],
    ['Actions 收费风险', 'GitHub', '官方账单、用量、折扣、实付和按月分钟。',
      detail('Actions 收费风险', 'actions', 'actions-billing'), detail('官方计费明细', 'actions', 'sku'), detail('按月分钟', 'actions', 'actions-monthly')],
    ['贡献网格', 'GitHub', '公开仓逐日提交数据。', detail('全年逐日活动', 'activity')],
    ['仓库', 'GitHub', '公开仓清单和工程读数，私有明细沿用登录入口。', detail('公开仓库清单', 'repos')],
    ['工程可视化', 'GitHub', '仓库指标、语言、自动化运行和结果移到表格。',
      detail('工程运行摘要', 'actions', 'engineering'), detail('逐仓自动化', 'actions', 'actions-runs'),
      detail('逐日自动化', 'actions', 'actions-daily'), detail('语言构成', 'repos', 'languages')],
    ['访问流量', 'GitHub', '逐仓与逐日浏览、克隆、去重、来源、路径和上游发布情况。',
      detail('逐仓访问流量', 'repo-traffic'), detail('逐日访问流量', 'repo-traffic', 'repo-traffic-daily'), detail('上游发布进度', 'repo-traffic', 'traffic-freshness')],
    ['Monorepo 子项目', 'GitHub', '子项目、所在仓、路径和提交量。', detail('仓内子项目', 'repos', 'subprojects')],
    ['续费时间轴', '成本', '日期、剩余天数、到期停机和自动续费状态用表格呈现。', detail('续费清单', 'cost', 'renew')],
    ['订阅历史', '成本', '按登记的扣款日和周期推算历史分期。', detail('订阅历史', 'subscriptions', 'subscription-history')],
    ['盯不住的订阅', '成本', '续费清单逐条标出无日期和已退役订阅。', detail('无日期及退役订阅', 'cost', 'renew')],
  ].map(([name, group, why, ...links]) => ({
    name, group, why, links, where: links.map(([label]) => label).join('、'), state: '已放入新版',
  }));

  compareTable.setRows(compare);
  text('#compare-summary', `旧版 ${compare.length} 个区块均有对应入口。`);
  start(['snapshot', 'arch'], ({snapshot, arch}) => {
    showAttention(snapshot);
    showHealth(arch);
  });
})();
