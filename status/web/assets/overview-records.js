/* Read-only tables for the information that used to appear in the legacy charts and tabs. */
const OverviewRecords = (() => {
  const {esc, noData, num, fmt, day, clock, stamp, sourceEpoch} = OverviewData;
  const {money, list, yesNo, attention} = OverviewInventory;
  const rows = value => Array.isArray(value) ? value : [];
  const mb = value => num(value) === null ? null : value >= 1024 ? fmt(value / 1024, ' GiB', 1) : fmt(value, ' MiB');
  const bytes = value => num(value) === null ? null : mb(value / 1024 ** 2);
  const epoch = value => sourceEpoch(value);
  const dateTime = value => /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : stamp(epoch(value));
  const cadence = value => ({yearly: '年付', monthly: '月付'})[value] || value;
  const projectState = value => ({run: '运行中', access: '访问受控', down: '离线', stale: '业务未就绪'})[value] || value;
  const unitKind = value => ({container: '容器', systemd: '系统服务', cron: '定时任务', coolify: '部署登记'})[value] || value;
  const states = {
    ok: '达标', warn: '需关注', bad: '不达标', na: '不适用', unknown: '无判定',
    healthy: '通', degraded: '有缺陷', blocked: '断了', blocked_by_policy: '按规则阻断',
    blocked_by_input: '待材料', not_built: '待建设', failed: '未恢复', acted: '已处理', pending: '待检查',
  };
  const state = value => states[value] || value || noData;
  const safeLink = (url, label) => {
    try {
      const link = new URL(url);
      if (['https:', 'http:'].includes(link.protocol)) {
        return `<a href="${esc(link.href)}" target="_blank" rel="noopener">${esc(label)}</a>`;
      }
    } catch {}
    return esc(label || noData);
  };
  const cell = (value, format) => row => esc(format(row[value]) ?? noData);
  const facts = entries => entries.filter(([, value]) => value != null)
    .map(([name, value, note]) => ({name, value, note}));
  const factCols = [['name', '指标', 180], ['value', '读数', 167], ['note', '口径', 374]];
  const field = (object, key, label, note) => [label, object?.[key], note];
  const section = (id, title, columns, read, options = {}) => ({id, title, columns, read, ...options});
  const measures = (id, title, read, options) => section(id, title, factCols, read, options);

  function bins(records, groupKey, slot, labels, describe) {
    const result = new Map();
    for (const record of records) {
      const name = groupKey(record);
      if (!result.has(name)) result.set(name, {name, values: labels.map(() => [])});
      result.get(name).values[slot(record)].push(describe(record));
    }
    return [...result.values()].reverse();
  }

  const binCols = (first, labels, width = 480) => [
    ['name', first, 167],
    ...labels.map((label, index) => ['bin' + index, label, width,
      row => esc(list(row.values[index]) || noData), row => list(row.values[index])]),
  ];

  const weeks = ['1–7 日', '8–14 日', '15–21 日', '22 日至月末'];
  function activity(s) {
    const dates = new Map();
    for (const [key, days] of [['commits', s.github?.calendar?.days], ['deploys', s.deploy_calendar?.days]]) {
      for (const {d, c} of rows(days)) dates.set(d, {d, ...dates.get(d), [key]: c});
    }
    return bins([...dates.values()].sort((a, b) => a.d.localeCompare(b.d)),
      row => row.d.slice(0, 7), row => Math.min(3, Math.floor((Number(row.d.slice(8)) - 1) / 7)), weeks,
      row => `${row.d.slice(8)} 日 ${row.commits ?? '—'}/${row.deploys ?? '—'}`);
  }

  function currentFacts(s) {
    const h = s.host || {}, ops = s.ops || {}, gh = s.github || {}, sum = s.summary || {}, heal = s.selfheal || {};
    const alerts = attention(s);
    const bad = alerts.filter(row => row.lv === 'bad').length;
    const warn = alerts.length - bad;
    const score = Math.max(0, 100 - Math.min(60, bad * 18) - Math.min(25, warn * 6));
    return facts([
      ['健康分', score, `${bad} 项待处理 · ${warn} 项需关注`],
      field(sum, 'services_online', '服务在线', '来自运行快照的探测与容器判定'),
      ['自动修复就绪', heal.armed == null ? null : `${heal.armed} / ${heal.total ?? noData}`, '含主自愈和元自愈'],
      field(gh, 'commits_7d', '近 7 天提交', gh.commits_30d == null ? null : `近 30 天 ${gh.commits_30d} 次`),
      field(h, 'load1', '主机负载', '1 分钟平均'),
      field(h, 'uptime_days', '在线天数', ops.restarts == null ? null : `容器重启 ${ops.restarts} 次`),
      ['证书剩余天数', ops.cert?.days, ops.cert?.date],
      ['磁盘已用／容量', h.disk_used_b == null ? null : `${bytes(h.disk_used_b)} / ${bytes(h.disk_total_b) ?? noData}`],
      ['内存与磁盘统计分母', h.res_base?.mem_total_mb == null ? null : `${mb(h.res_base.mem_total_mb)} / ${mb(h.res_base.disk_total_mb) ?? noData}`, '项目占比按整机容量计算'],
      ['未归属资源', h.unclaimed?.mem_mb == null ? null : `${mb(h.unclaimed.mem_mb)} / ${mb(h.unclaimed.storage_mb) ?? noData}`,
        h.unclaimed && `内存 ${fmt(h.unclaimed.mem_pct, '%', 1)}、存储 ${fmt(h.unclaimed.storage_pct, '%', 1)} · ${h.unclaimed.containers ?? noData} 个容器、${h.unclaimed.units ?? noData} 个系统服务`],
    ]);
  }

  function dependencies(s) {
    const groups = new Map();
    for (const project of rows(s.projects)) {
      for (const [kind, value] of [['存储', project.store], ['外部执行依赖', project.agent], ['部署方式', project.deploy]]) {
        const name = value || '无登记';
        const key = kind + ':' + name;
        if (!groups.has(key)) groups.set(key, {kind, name, projects: []});
        groups.get(key).projects.push(project.name);
      }
    }
    return [...groups.values()].map(row => ({...row, count: row.projects.length, projects: list(row.projects)}))
      .concat(rows(s.software?.blast_radius).map(x => ({kind: '共享运行资源', name: x.res, count: x.n, projects: list(x.lines)})));
  }

  const projectCols = [
    ['name', '项目', 175, r => safeLink(r.url, r.name)], ['status', '状态', 133, cell('status', projectState)],
    ['host', '运行主机', 141], ['parts', '组成', 118, cell('parts', list)],
    ['mem_mb', '内存', 146, r => esc(`${mb(r.mem_mb) ?? noData} · ${fmt(r.mem_pct, '%', 1)}${r.mem_pct > 10 ? ' · 超过 10% 配额' : ''}`)],
    ['storage_mb', '存储', 146, r => esc(`${mb(r.storage_mb) ?? noData} · ${fmt(r.storage_pct, '%', 1)}${r.storage_pct > 10 ? ' · 超过 10% 配额' : ''}`)],
    ['db', '数据库', 155], ['store', '文件存储', 165], ['deploy', '部署方式', 160], ['backup', '备份方式', 201],
    ['agent', '外部执行依赖', 140], ['containers', '容器数', 124],
  ];

  const unitCols = [
    ['id', '单元', 226], ['kind', '类型', 128, cell('kind', unitKind)],
    ['state', '状态', 139, cell('state', value => OverviewInventory.status(value)[0])],
    ['owner', '归属', 153], ['domain', '域名', 184], ['policy', '登记方式', 170], ['detail', '说明', 374],
  ];

  function compliance(s) {
    const w = s.software;
    if (!w) return [];
    const judgments = rows(w.lines).flatMap(row => Object.values(row.cells || {}));
    const assessed = judgments.filter(cell => cell.s !== 'na').length;
    const failed = judgments.filter(cell => cell.s === 'bad').length;
    return facts([
      ['端到端健康', w.score, `${rows(w.lines).length} 条业务与底座线`],
      ['登记合规', w.units_registered == null ? null : `${w.units_registered} / ${w.units_total ?? noData}`,
        w.units_total > 0 ? fmt(w.units_registered / w.units_total * 100, '%', 1) : null],
      ['未登记违规', w.unregistered?.length, w.cloudflare_note],
      ['实判段', `${assessed} / ${judgments.length}`, `${failed} 段不达标，${judgments.length - assessed} 段不适用`],
      ...Object.entries(w.by_kind || {}).map(([kind, count]) => [`探测到的${unitKind(kind)}`, count]),
    ]);
  }

  const softwareSteps = [
    ['code', '代码源'], ['ci', '持续集成'], ['deploy', '部署'], ['run', '运行'], ['entry', '入口'],
    ['data', '数据'], ['backup', '备份'], ['monitor', '监控'], ['heal', '自愈'],
  ];
  const stageText = cell => cell ? [state(cell.s), cell.v].filter(Boolean).join(' · ') : null;
  const flowState = cell => cell.measured ? `实测 ${state(cell.measured)}` : '缺机器实测';
  const flowText = cell => cell ? [
    stageText(cell), flowState(cell), cell.declared && `自报 ${state(cell.declared)}`,
    cell.weak && '仅弱证据', cell.mismatch && '自报与实测不符', cell.defect && `缺陷 ${cell.defect}`,
    cell.coupling_violation?.length && `上游断点 ${list(cell.coupling_violation)}`,
  ].filter(Boolean).join(' · ') : null;

  function businessRows(s) {
    return rows(s.flow?.projects).flatMap(project => rows(project.baselines).map(line => ({
      ...line, project: project.project, source: project.source,
      coverage: line.verified == null ? null : `${line.verified} / ${line.cells_n ?? noData}`,
      steps: rows(project.stages).map(key => {
        const detail = flowText(line.cells?.[key]);
        return `${project.stage_names?.[key] || key} · ${detail || noData}`;
      }),
    })));
  }

  function businessCols(s) {
    const total = Math.max(0, ...rows(s.flow?.projects).map(p => rows(p.stages).length));
    return [
      ['project', '项目', 144], ['name', '业务基线', 216], ['priority', '优先级', 105],
      ['state', '状态', 126, cell('state', state)], ['coverage', '实测覆盖', 129], ['since', '起始日期', 135],
      ...Array.from({length: total}, (_, i) => [`step${i}`, `阶段 ${i + 1}`, 820, null, row => row.steps[i]]),
      ['note', '说明', 750], ['source', '登记来源', 419],
    ];
  }

  const blocks = ['00–05 时', '06–11 时', '12–17 时', '18–23 时'];
  const quarters = ['00–14 分', '15–29 分', '30–44 分', '45–59 分'];
  const hour = epoch => Number(clock(epoch).slice(0, 2));
  const minute = epoch => Number(clock(epoch).slice(3));
  const sampleValues = row => [row.h, row.p, row.s, row.e].map(value => value ?? '—').join('/');
  const byDay = (records, describe) => bins(records.filter(row => num(row.t) > 0), row => day(row.t),
    row => Math.floor(hour(row.t) / 6), blocks, row => `${clock(row.t)} ${describe(row)}`);
  const byHour = records => bins(records.filter(row => num(row.t) > 0),
    row => stamp(Math.floor(row.t / 3600) * 3600), row => Math.floor(minute(row.t) / 15), quarters,
    row => `${clock(row.t)} ${sampleValues(row)}`);

  function subscriptionPeriods(s) {
    if (!(num(s.updated_epoch) > 0)) return [];
    const today = day(s.updated_epoch);
    const cutoff = new Date(today + 'T00:00:00Z');
    cutoff.setUTCDate(1);
    cutoff.setUTCMonth(cutoff.getUTCMonth() - 5);
    const key = date => date.toISOString().slice(0, 10);
    return rows(s.ops?.subs?.items).flatMap(item => {
      let end = new Date(item.date + 'T00:00:00Z');
      const periods = [];
      const months = item.end_only ? 6 : item.cadence === 'yearly' ? 12 : item.cadence === 'monthly' ? 1 : 0;
      if (!months || !Number.isFinite(+end)) return periods;
      do {
        const start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - months, 1));
        const finalDay = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)).getUTCDate();
        start.setUTCDate(Math.min(finalDay, end.getUTCDate()));
        if (start > new Date(today + 'T00:00:00Z')) break;
        if (end >= cutoff) periods.push({
          name: item.name, start: key(start), end: key(end), cadence: item.end_only ? '到期终止' : cadence(item.cadence),
          state: key(end) <= today ? '往期' : item.days <= 7 ? '临近到期' : '当前',
        });
        end = start;
      } while (end >= cutoff && !item.end_only);
      return periods;
    });
  }

  const g = s => s.github || {};
  const graph = s => s.graph || {};
  const billing = s => g(s).billing || {};
  const actions = s => g(s).actions || {};
  const traffic = s => g(s).traffic || {};
  const graphKinds = {repo: '公开仓', repo_private: '私有仓汇总', project: '项目', store: '存储', vendor: '供应商', subproject: '子项目'};
  const qty = (amount, unit) => num(amount) === null ? null : unit === 'bytes' ? bytes(amount) : `${amount}${unit ? ' ' + unit : ''}`;
  const sampleGuide = '每个时点按真实浏览／存活探测／页面同步／错误列出请求数。时间为悉尼时间。';
  const records = {
    activity: ['提交与部署活动', [
      section('activity', '全年逐日活动', binCols('月份', weeks), activity, {height: 404,
        note: '每天按提交数／部署数列出。破折号表示该来源没有记录。'}),
      section('deploy-trend', '近 7 天部署', [['name', '采集日', 157], ['count', '部署次数', 142]], s =>
        rows(s.deploy?.d7_labels).map((name, i) => ({name, count: s.deploy.d7_data?.[i]})), {note: '按部署采集器的日标签显示。'}),
    ]],
    projects: ['项目运行与自愈', [
      section('projects', '项目清单', projectCols, s => rows(s.projects), {filter: ['运行主机', 'host'], height: 352,
        note: '内存与存储占比按整机容量计算。标出超过单项目 10% 配额的记录。'}),
      section('dependencies', '耦合关系', [
        ['name', '依赖', 360], ['kind', '维度', 144], ['count', '关联数量', 122], ['projects', '关联项目或运行线', 650],
      ], dependencies, {filter: ['维度', 'kind'], height: 530}),
      section('selfheal', '自愈引擎', [
        ['name', '规则', 179], ['set', '守护范围', 152, cell('set', value => value === 'meta' ? '元自愈' : '主自愈')],
        ['state', '判定', 122, cell('state', state)], ['armed', '就绪', 106, cell('armed', yesNo)], ['engine', '执行器', 131],
        ['threshold', '阈值', 204], ['detail', '依据', 350], ['actions_total', '累计动作', 128], ['last_action', '最近动作', 184],
      ], s => rows(s.selfheal?.rules).map(row => ({...row, set: row.set || 'main'})),
      {filter: ['判定', 'state'], note: s => s.selfheal && `主自愈 ${s.selfheal.main_armed ?? noData}/${s.selfheal.main_total ?? noData} · 元自愈 ${s.selfheal.meta_armed ?? noData}/${s.selfheal.meta_total ?? noData}。`}),
      section('events', '最近动作', [
        ['at', '时间（悉尼）', 216, cell('at', dateTime), r => epoch(r.at)],
        ['kind', '动作来源', 130], ['name', '规则或应用', 195], ['msg', '执行结果', 352],
      ], s => rows(s.selfheal?.recent).map(row => ({...row, kind: '自愈', name: row.rule}))
        .concat(rows(s.deploy?.log).map(row => ({...row, kind: '部署', name: row.app, msg: yesNo(row.ok, '成功', '失败')}))),
      {filter: ['动作来源', 'kind']}),
      measures('host-facts', '运行读数', currentFacts, {height: 284}),
    ], [['本站实时访问', 'traffic#live-traffic']]],
    traffic: ['本站实时访问', [
      measures('live-traffic', '各类请求统计', s => {
        const l = s.live || {};
        return facts([
          ['真实浏览', l.r60?.h, l.r24?.h == null ? null : `24 小时 ${l.r24.h} 次`], ['近 5 分钟平均', qty(l.rpm, '次/分钟')],
          ['存活探测（1 小时）', l.r60?.p], ['页面同步（1 小时）', l.r60?.s], ['错误（24 小时）', l.r24?.e, '401 和 403 不计'],
          ['采样时间', l.at > 0 ? stamp(l.at) : null, l.note],
        ]);
      }),
      section('live-sites', '各站点访问', [
        ['site', '站点', 197], ['h60', '真实浏览 1h', 156], ['h24', '真实浏览 24h', 168],
        ['all60', '全部请求 1h', 156], ['all24', '全部请求 24h', 168], ['err24', '错误 24h', 135],
      ], s => rows(s.live?.sites)),
      section('live-minutes', '近 3 小时逐分钟请求', binCols('小时', quarters, 570),
        s => byHour(rows(s.live?.minutes)), {height: 375, note: sampleGuide}),
      section('live-hours', '近 30 天逐小时请求', binCols('日期', blocks, 565),
        s => byDay(rows(s.live?.hours), sampleValues), {height: 600, note: sampleGuide}),
    ]],
    baseline: ['端到端运行基线', [
      measures('compliance', '治理登记合规', compliance, {note: '采集器对照 Docker、系统服务、定时任务和部署记录。'}),
      section('baseline', '九段运行矩阵与证据', [
        ['name', '业务线', 191], ['score', '健康分', 129], ['judged', '实判段数', 136],
        ['host', '运行位置', 146], ['units', '单元数', 116],
        ...softwareSteps.map(([key, label]) => [key, label, 235, null, row => stageText(row.cells?.[key])]),
      ], s => rows(s.software?.lines), {filter: ['总判定', 'state'], height: 525,
        note: '每条线从代码到自愈的九段证据全部列出。不适用的段不计入分数。'}),
      section('violations', '未登记单元与自洽异常', unitCols.slice(0, 3).concat([['detail', '依据', 385]]), s =>
        rows(s.software?.unregistered).concat(rows(s.software?.integrity).map(row => ({...row, id: row.project, state: '数据异常'}))),
      {filter: ['类型', 'kind']}),
      section('baseline-history', '端到端健康历史', binCols('悉尼日期', blocks, 400), s =>
        byDay(rows(s.baseline?.hours).map((t, i) => ({t, score: s.baseline.overall?.[i]})), row => fmt(row.score, ' 分')),
      {height: 486, note: s => s.baseline?.since ? `逐小时归档 · 最早 ${day(s.baseline.since)}。表内为整体健康分。` : '显示已采集的逐小时整体健康分。'}),
    ], [['自动探测到的运行单元', 'units#discovered'], ['运行单元增减', 'units#unit-changes']]],
    units: ['运行单元与变更', [
      section('discovered', '自动探测到的运行单元', unitCols, s => rows(s.software?.units),
        {filter: ['类型', 'kind'], height: 662}),
      section('unit-changes', '运行单元增减', [
        ['t', '时间（悉尼）', 275, cell('t', stamp)], ['op', '动作', 110, cell('op', x => x === 'added' ? '新增' : '消失')],
        ['id', '单元', 325], ['kind', '类型', 132, cell('kind', unitKind)], ['owner', '原归属', 146], ['eph', '临时构建', 140, cell('eph', yesNo)],
      ], s => {
        const ledger = s.software?.ledger;
        const all = rows(ledger?.events).concat(rows(ledger?.recent_removed));
        return [...new Map(all.map(row => [`${row.t}/${row.id}/${row.op}`, row])).values()].sort((a, b) => b.t - a.t);
      }, {filter: ['动作', 'op'], height: 600, note: s => s.software?.ledger?.note}),
    ]],
    business: ['业务流实测', [
      section('business', '业务基线矩阵', businessCols, businessRows, {filter: ['项目', 'project'], height: 748,
        note: s => s.flow?.available ? `机器实测覆盖 ${s.flow.verified_total ?? noData}/${s.flow.measurable_total ?? noData} 个可测阶段。按项目定义的阶段顺序列出证据。` : '业务流数据未取到。'}),
      section('defects', '已知缺陷', [
        ['project', '项目', 166], ['id', '缺陷编号', 138], ['baseline', '基线', 176],
        ['stage', '阶段', 123], ['desc', '说明', 650], ['since', '起始', 136],
      ], s => rows(s.flow?.projects).flatMap(p => rows(p.defects).map(row => ({
        ...row, project: p.project, baseline: rows(p.baselines).find(line => line.id === row.baseline)?.name || row.baseline,
      }))), {filter: ['项目', 'project'], height: 343}),
      section('blast-radius', '源级影响范围', [
        ['project', '项目', 164], ['id', '上游源', 227], ['n', '涉及基线', 127],
        ['lines', '受影响基线', 304, cell('lines', list)], ['rule', '耦合规则', 354],
      ], s => rows(s.flow?.projects).flatMap(p => rows(p.sources).map(row => ({...row, project: p.project, rule: p.coupling_rule}))),
      {filter: ['项目', 'project'], height: 415}),
      section('business-trend', '逐日健康趋势', [
        ['d', '归档日期', 155], ['healthy', '通', 111], ['degraded', '有缺陷', 126], ['bad', '阻断', 115],
      ], s => rows(s.flow?.trend), {height: 352, note: '同一格一天内取最差状态。'}),
      section('business-registration', '登记与自洽异常', [
        ['project', '项目', 176], ['kind', '情况', 136], ['repo', '仓库', 150], ['expect', '登记位置', 207], ['detail', '原因', 330],
      ], s => rows(s.flow?.unregistered).map(row => ({...row, kind: '未登记', detail: row.why}))
        .concat(rows(s.flow?.integrity).map(row => ({...row, kind: row.kind || '数据异常'}))),
      {filter: ['情况', 'kind'], note: s => g(s).ungoverned_count == null ? '' :
        `目录治理扫描 ${g(s).ungoverned_scanned ?? noData} 个，${g(s).ungoverned_count} 个未纳入，${g(s).ungoverned_exempt ?? noData} 个已豁免。私有明细可在登录后的 GitHub 页查看。`}),
    ]],
    usage: ['额度与外部服务', [
      section('usage', '免费额度用量', [
        ['label', '额度项', 180], ['used', '已用', 125, r => esc(qty(r.used, r.unit) ?? noData)],
        ['limit', '上限', 125, r => esc(qty(r.limit, r.unit) ?? noData)],
        ['pct', '占用率', 130, cell('pct', x => fmt(x, '%', 2))], ['source', '来源', 130],
        ['eta_date', '预计触顶', 126], ['eta_note', '估算依据', 242], ['note', '说明', 300],
        ['latest_epoch', '最新样本（悉尼）', 216, cell('latest_epoch', stamp)],
      ], s => rows(s.usage).map(row => ({...row,
        pct: num(row.used) === null || !(num(row.limit) > 0) ? null : row.used / row.limit * 100,
        source: row.source === 'auto' ? '自动' : row.checked ? `人工 ${row.checked}` : row.source,
      })), {height: 258, note: '预计触顶按采集器记录的历史增速推算。'}),
      section('external', '外部服务状态', [['name', '服务', 169], ['ok', '状态', 138, cell('ok', v => yesNo(v, '正常', '异常'))], ['note', '依据', 420]],
        s => rows(s.externals), {note: s => s.externals_at && `检查时间 ${dateTime(s.externals_at)}。`}),
      section('supplier-billing', 'AI 供应商账务', [
        ['vendor', '供应商', 160], ['kind', '账务类型', 143], ['state', '数据状态', 163],
        ['amount', '金额', 131], ['currency', '币种', 115], ['note', '来源说明', 426],
      ], s => rows(s.ai_accounts?.items).map(row => ({...row,
        kind: row.kind === 'balance' ? '余额' : '本月已花费',
        amount: row.state === 'ok' ? row.amount : null,
        state: ({ok: '已读', unconfigured: '未配置', failed: '读取失败', unknown_shape: '格式未知', refused: '已拒绝'})[row.state] || noData,
      })), {filter: ['数据状态', 'state'], note: s => s.ai_accounts?.checked_at && `账务采样 ${dateTime(s.ai_accounts.checked_at)}。`}),
    ]],
    subscriptions: ['订阅历史分期', [
      section('subscription-history', '订阅历史', [
        ['name', '订阅', 188], ['start', '分期开始', 140], ['end', '分期结束', 140],
        ['cadence', '周期', 132], ['state', '分期状态', 140],
      ], subscriptionPeriods, {filter: ['订阅', 'name'], height: 364,
        note: '以快照日期为参照，从下次续费日和登记周期倒推近六个月的区间。分期属于推算。'}),
    ], [['未来续费与无日期订阅', 'cost#renew']]],
    repos: ['GitHub 仓库与关系', [
      section('repos', '公开仓库清单', [
        ['name', '公开仓', 168, r => safeLink(r.url, r.name)], ['top_lang', '主语言', 133],
        ['commits_7d', '7 天提交', 126], ['commits_30d', '30 天提交', 129],
        ['momentum', '提交动能', 134, cell('momentum', v => fmt(v, '%', 1))],
        ['branches', '分支', 105], ['open_pr', '待合并请求', 135], ['open_issue', '待处理议题', 135],
        ['size_kb', '仓库体积', 133, cell('size_kb', v => num(v) === null ? null : mb(v / 1024))],
        ['release_bytes', '发布占用', 136, cell('release_bytes', bytes)], ['ci_conclusion', '持续集成', 130],
        ['pushed_at', '最近推送（悉尼）', 211, cell('pushed_at', dateTime)], ['default_branch', '默认分支', 138],
      ], s => rows(g(s).public_repos).map(row => ({...row,
        momentum: row.commits_30d * 7 / 30 >= 1 ? (row.commits_7d / (row.commits_30d * 7 / 30) - 1) * 100 : null,
        ci_conclusion: row.ci_ok === true ? '通过' : row.ci_conclusion || row.ci_state,
      })), {filter: ['主语言', 'top_lang'], height: 296, note: s => g(s).repos_total != null &&
        `${g(s).repos_total} 个仓库 · ${g(s).public ?? noData} 个公开 · ${g(s).private ?? noData} 个私有。私有仓明细仅在登录页显示。`,
      related: [['登录查看私有仓', '/admin/github']]}),
      section('repo-links', '仓库与项目关系', [
        ['from', '起点', 245], ['to', '终点', 245], ['rel', '关系', 140],
        ['w', '同日变更耦合', 160, cell('w', v => num(v) === null ? noData : fmt(v * 100, '%', 1))],
        ['trend', '近 7 日耦合变化', 173, cell('trend', v => num(v) === null ? noData : fmt(v * 100, ' 个百分点', 1))], ['why', '依据', 490],
      ], s => {
        const names = new Map(rows(graph(s).nodes).map(n => [n.id, n.label]));
        return rows(graph(s).edges).map(row => ({...row, from: names.get(row.s), to: names.get(row.t)}));
      }, {filter: ['关系', 'rel'], height: 704}),
      section('repo-nodes', '仓库与项目节点', [
        ['label', '名称', 173], ['kind', '类型', 139], ['role', '用途', 197], ['host', '主机', 141],
        ['deploy', '部署', 157], ['status', '项目状态', 140, cell('status', projectState)],
        ['days14', '近 14 天每日提交', 720, cell('days14', points => list(rows(points).map(x => `${x.d} ${x.c} 次`)))],
        ['active_days14', '活跃天数', 129], ['lang_mix', '语言构成', 320,
          cell('lang_mix', values => list(rows(values).map(l => `${l.n} ${l.p}%`)))],
        ['views14', '14 天浏览', 128], ['views_uniq14', '独立浏览', 128],
        ['clones14', '14 天克隆', 128], ['clones_uniq14', '独立克隆', 128], ['path', '子项目路径', 166],
      ], s => rows(graph(s).nodes).map(row => ({...row, kind: graphKinds[row.kind] || row.kind})),
      {filter: ['类型', 'kind'], height: 635}),
      section('subprojects', '仓内子项目', [
        ['project', '子项目', 170], ['repo', '所在仓', 164], ['path', '路径', 265],
        ['commits_30d', '30 天提交数', 145], ['last_commit_at', '最近提交（悉尼）', 210, cell('last_commit_at', dateTime)],
      ], s => rows(g(s).subprojects), {filter: ['所在仓', 'repo'], height: 275}),
      section('languages', '公开仓语言构成', [['name', '语言', 153], ['bytes', '代码字节', 163]], s => {
        const counts = new Map();
        for (const repo of rows(g(s).public_repos)) {
          for (const [lang, size] of Object.entries(repo.languages || {})) {
            if (num(size) !== null) counts.set(lang, (counts.get(lang) || 0) + Number(size));
          }
        }
        return [...counts].map(([name, bytes]) => ({name, bytes})).sort((a, b) => b.bytes - a.bytes);
      }),
    ], [['自动化与账单', 'actions#actions-billing'], ['访问流量', 'repo-traffic#repo-traffic'], ['贡献网格数据', 'activity#activity']]],
    actions: ['GitHub 自动化与账单', [
      measures('actions-billing', 'Actions 收费风险', s => {
        const b = billing(s), a = actions(s);
        return facts([
          ['账单状态', b.available ? '已读到官方账单' : 'UNAVAILABLE', b.note],
          ['实际扣款', b.available ? money(b.net_total, 'US$') : null],
          ['折扣前', b.available ? money(b.gross_total, 'US$') : null],
          ['已抵扣', b.available ? money(b.saved, 'US$') : null],
          ['本月分钟数', b.available ? b.current_month_minutes : null, b.current_month],
          ['私有仓计费条目', b.available ? b.private_billed_count : null, a.private_status],
          ['计费仓', b.available ? list(b.billed_repos) : null],
          field(a, 'free_tier_minutes', '免费分钟数', a.cost_note),
        ]);
      }),
      section('sku', '官方计费明细', [
        ['sku', '计费项', 184], ['product', '产品', 152], ['qty', '用量', 129], ['unit', '单位', 115],
        ['gross', '原价 US$', 142, cell('gross', v => money(v, 'US$'))], ['discount', '折扣 US$', 142, cell('discount', v => money(v, 'US$'))],
        ['net', '实付 US$', 142, cell('net', v => money(v, 'US$'))],
      ], s => billing(s).available ? rows(billing(s).by_sku) : []),
      section('actions-monthly', '按月计费分钟', [['m', '月份', 148], ['q', '分钟数', 151]],
        s => billing(s).available ? rows(billing(s).actions_minutes_by_month) : []),
      measures('engineering', '工程运行摘要', s => {
        const gh = g(s), a = actions(s), c = a.conclusions || {}, t = gh.throughput || {};
        const completed = num(c.success) !== null && num(c.failure) !== null ? c.success + c.failure : 0;
        return facts([
          field(gh, 'commits_7d', '近 7 天提交', gh.commits_30d == null ? null : `30 天 ${gh.commits_30d} 次`),
          field(t, 'pr_merged_30d', '近 30 天合并请求', t.pr_open == null ? null : `待处理 ${t.pr_open} 条`),
          ['持续集成失败率', completed > 0 ? fmt(c.failure / completed * 100, '%', 1) : null, `${c.failure ?? noData} / ${completed} 已完成`],
          ...[['success', '成功'], ['failure', '失败'], ['cancelled', '取消'], ['skipped', '跳过']].map(([key, label]) => field(c, key, label)),
          field(a, 'public_runs_30d', '公开仓 30 天运行数'),
          ['仓库体积', gh.git_size_kb == null ? null : mb(gh.git_size_kb / 1024)], ['发布占用', bytes(gh.release_bytes)],
          ['GitHub API 额度', gh.rate?.remaining == null ? null : `${gh.rate.remaining} / ${gh.rate.limit ?? noData}`],
        ]);
      }, {height: 300}),
      section('actions-runs', '逐仓自动化运行', [
        ['name', '仓库', 185], ['runs_30d', '30 天运行', 137], ['success', '成功', 114],
        ['failure', '失败', 114], ['cancelled', '取消', 114], ['skipped', '跳过', 114],
      ], s => rows(actions(s).by_repo)),
      section('actions-daily', '逐日自动化运行', [['d', '日期', 162], ['c', '运行次数', 142]],
        s => rows(actions(s).daily14), {height: 285}),
    ]],
    'repo-traffic': ['GitHub 访问流量', [
      section('repo-traffic', '逐仓浏览与克隆', [
        ['name', '仓库', 168], ['views_14d', '14 天浏览', 135], ['views_uniq_14d', '独立浏览', 134],
        ['clones_14d', '14 天克隆', 134], ['clones_uniq_14d', '独立克隆', 136], ['referrers', '来源', 316,
          cell('referrers', values => list(rows(values).map(x => `${x.r} ${x.c ?? ''}`.trim())))],
        ['top_paths', '访问路径', 350, cell('top_paths', values => list(rows(values).map(x => `${x.p} ${x.c ?? ''}`.trim())))],
      ], s => rows(traffic(s).per_repo), {height: 382}),
      measures('traffic-freshness', '流量统计与发布进度', s => {
        const t = traffic(s), f = t.freshness || {};
        return facts([
          field(t.totals_14d, 'views', '14 天浏览'), field(t.totals_14d, 'clones', '14 天克隆'),
          field(f, 'upstream_through', '上游已发布到'), field(f, 'lag_days', '上游滞后天数'),
          ['待发布日期', list(f.pending_days), '未发布的日期留空'],
          ['最近到达', f.last_arrival_epoch > 0 ? stamp(f.last_arrival_epoch) : null],
          ['预计下批', f.next_eta_epoch > 0 ? stamp(f.next_eta_epoch) : null, '按已测间隔推算'],
          field(t, 'archived_days', '已归档天数', t.archive_since), field(g(s).capability, 'traffic', '读取能力', t.note),
        ]);
      }),
      section('repo-traffic-daily', '逐日浏览与克隆', binCols('月份', weeks), s => {
        const tr = traffic(s);
        const published = rows(tr.daily).map(row => ({...row, values: `${row.v ?? '—'}/${row.c ?? '—'}`}));
        const dates = new Map(published.map(row => [row.d, row]));
        for (const d of rows(tr.freshness?.pending_days)) if (!dates.has(d)) dates.set(d, {d, values: '待发布'});
        return bins([...dates.values()].sort((a, b) => a.d.localeCompare(b.d)), row => row.d.slice(0, 7),
          row => Math.min(3, Math.floor((Number(row.d.slice(8)) - 1) / 7)), weeks, row => `${row.d.slice(8)} 日 ${row.values}`);
      }, {height: 392, note: '每天按浏览数／克隆数列出。'}),
    ]],
  };

  function linkList(links) {
    return links?.length ? `<div class="record-links">${links.map(([label, dest]) =>
      `<a href="${dest.startsWith('/') ? esc(dest) : '/check.html?view=' + esc(dest)}">${esc(label)}</a>`).join('')}</div>` : '';
  }

  function create(view) {
    if (!Object.hasOwn(records, view)) return null;
    const [title, sections, related] = records[view];
    const template = document.createElement('template');
    template.innerHTML = `
      <div class="headline"><h1>${title}</h1><button type="button" class="secondary" id="refresh">刷新数据</button></div>
      <p class="summary" id="table-guide">宽表可左右滑动。点列名排序，拖动箭头或按方向键调整列宽。</p>
      <p class="summary" id="records-source" role="status">正在读取运行快照…</p>
      ${sections.map(spec => `<section id="${spec.id}" aria-labelledby="h-${spec.id}" data-km-section data-directory="${spec.title}">
        <h2 id="h-${spec.id}">${spec.title}</h2>
        ${spec.note ? `<p class="summary" id="note-${spec.id}"></p>` : ''}
        <div id="records-${spec.id}" class="table-slot"${spec.height ? ` style="--table-height:${spec.height}px"` : ''}></div>
        ${linkList(spec.related)}
      </section>`).join('')}
      ${linkList(related)}`;
    return template.content;
  }

  function start(view, P) {
    const [, sections] = records[view];
    const rendered = new Map();
    P.start(['snapshot'], ({snapshot}, fresh) => {
      P.text('#records-source', snapshot
        ? `${stamp(snapshot.updated_epoch)} · 运行快照${fresh.has('snapshot') ? '' : ' · 刷新失败，仍显示上次读数'}`
        : '运行快照取数失败。可刷新重试。');
      for (const spec of sections) {
        if (spec.note) P.text('#note-' + spec.id, (typeof spec.note === 'function' ? spec.note(snapshot || {}) : spec.note) || '');
        const columns = typeof spec.columns === 'function' ? spec.columns(snapshot || {}) : spec.columns;
        const signature = columns.map(([key]) => key).join('|');
        if (rendered.get(spec.id)?.signature !== signature) {
          const [label, key] = spec.filter || ['按' + columns[0][1], columns[0][0]];
          const filterCol = columns.find(([field]) => field === key);
          const filter = row => {
            const value = row[key];
            if (value == null) return null;
            if (key === 'state') return state(value);
            if (key === 'kind') return unitKind(value);
            if (key === 'op') return value === 'added' ? '新增' : '消失';
            if (filterCol?.[4]) return String(filterCol[4](row));
            return String(value);
          };
          const table = P.table('#records-' + spec.id, spec.title, columns.map(column => P.col(...column)),
            spec.filter ? '按' + label : label, filter, {empty: '没有可列出的记录'});
          rendered.set(spec.id, {signature, table});
        }
        rendered.get(spec.id).table.setRows(snapshot ? spec.read(snapshot) : []);
      }
    });
  }

  return {create, start};
})();
