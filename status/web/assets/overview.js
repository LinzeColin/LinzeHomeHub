(() => {
  const D = OverviewData;
  const {esc, fmt, num, noData, stamp} = D;
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const text = (selector, value) => { $(selector).textContent = value; };
  const sources = {
    snapshot: item => item?.host && Array.isArray(item.projects) && num(item.updated_epoch) > 0,
    history: item => item?.min && item?.hour && item?.day,
    arch: item => Array.isArray(item?.nodes) && Array.isArray(item.edges),
  };
  const state = {period: 7, epoch: null, snapshot: null, history: null, arch: null, snapshotAlert: '', historyEmbedded: false};
  const diskClean = 85;
  let graph, historyChart, dailyRows = [];
  let inventoryFilterBeforeFocus = null;
  let lastAttempt = 0;

  const col = (key, label, width, render, value, search) => ({key, label, width, render, value, search});
  const table = (id, title, columns, filterLabel, value, options) =>
    new OverviewTable($(id), columns, {title, filter: {label: filterLabel, value}, ...options});
  const badge = (label, tone = '') => `<span class="status-pill ${tone}">${esc(label)}</span>`;
  const pluck = key => row => row[key];
  const brief = text => String(text ?? noData).replaceAll('**', '');
  const aud = value => num(value) === null ? noData : 'A$' + fmt(value, '', 2);
  const gb = bytes => num(bytes) === null ? noData : fmt(num(bytes) / 1024 ** 3, ' GiB', 1);
  const graphLink = (id, value) => id ? `<button type="button" class="graph-link" data-arch-node="${esc(id)}">${esc(value)}</button>` : esc(value);

  const alertTable = table('#attention-table', '待处理事项', [
    col('level', '严重度', 125, r => badge(r.level, r.lv === 'bad' ? 'bad' : r.lv === 'warn' ? 'warn' : 'good'),
      r => ({bad: 0, warn: 1, ok: 2})[r.lv], pluck('level')),
    col('t', '事项', 235), col('d', '依据', 300),
  ], '按严重度', pluck('level'));
  const memLoad = (average, peak, unit, digits) => r => {
    const first = esc(fmt(r[average], unit, digits));
    return r.current ? first : `${first} / ${esc(fmt(r[peak], unit, digits))}`;
  };
  const historyColumns = [
    col('date', '日期', 125, r => `<span title="${esc(r.source)}">${esc(r.date)}</span>`),
    col('memAvg', '内存均值／峰值', 174, memLoad('memAvg', 'memPeak', '%', 1)),
    col('disk', '磁盘已用', 140, r => esc(fmt(r.disk, '%', 1) + (r.diskBytes == null ? '' : ' · ' + gb(r.diskBytes)))),
    col('loadAvg', '负载均值／峰值', 173, memLoad('loadAvg', 'loadPeak', '', 2)),
    col('containers', '在跑容器', 135), col('probes', '探测在线率', 153, r => esc(fmt(r.probes, '%', 1))),
  ];
  const dailyTable = table('#daily-table', '每日资源历史', historyColumns, '按日期', pluck('date'), {
    isPinned: row => row.current === true, rowClass: row => row.current ? 'current-row' : '',
  });
  const orgTable = table('#org-table', '归属未知项', [
    col('label', '项目或运行单元', 238, r => graphLink(r.id, r.label)), col('type', '类型', 125),
    col('project', '已知项目', 160), col('detail', '登记或识别依据', 240),
  ], '按类型', pluck('type'), {rowClass: () => 'flag-row'});
  const routeTable = table('#route-table', '对外逻辑链', [
    col('host', '对外域名', 240, r => graphLink(r.node, r.host)),
    col('state', '链路状态', 142, r => badge(r.state, r.state === '断点' ? 'bad' : r.state === '路由缺证' ? 'warn' : 'good')),
    col('cloud', 'Cloudflare', 155), col('tunnel', '隧道', 145), col('proxy', '代理', 174),
    col('service', '服务', 210), col('dependencies', '库或外部依赖', 215),
    col('probes', '探测结果', 220), col('findings', '断点或缺证', 265,
      r => `<span class="${r.state === '有路由记录' ? '' : 'bad-text'}">${esc(r.findings)}</span>`),
  ], '按状态', pluck('state'), {rowClass: r => r.state === '有路由记录' ? '' : 'flag-row'});
  const lineageTable = table('#lineage-table', '采集与备份数据链', [
    col('from', '采集器／上游', 178), col('file', '产物或下游', 180, r => r.url
      ? `<a download href="${esc(r.url)}">${esc(r.file)}</a>` : esc(r.file)),
    col('section', '页面区块／用途', 192), col('schedule', '频率', 158),
    col('at', '最新记录（悉尼）', 208, r => esc(stamp(r.at))),
    col('retention', '留存', 180), col('source', '依据', 223)
  ], '按用途', pluck('group'));
  const vendorTable = table('#vendor-table', '供应商', [
    col('name', '供应商', 177, r => `<strong>${esc(r.name)}</strong><br>${esc(r.role || '')}`),
    col('status', '状态', 180, r => badge(r.status, r.tone)), col('cost', '成本', 255),
    col('risks', '风险', 204), col('health', '监控与健康', 245),
  ], '按状态', pluck('status'));
  const renewTable = table('#renew-table', '续费清单', [
    col('name', '订阅', 183), col('date', '登记日期', 148),
    col('days', '距快照日', 129, r => esc(fmt(r.days, ' 天'))),
    col('kind', '日期含义', 150, r => badge(r.kind, r.tone)),
    col('note', '续费状态与说明', 245),
  ], '按订阅', pluck('name'));
  const capacityTable = table('#capacity-table', '容量与升级', [
    col('dim', '维度', 120), col('status', '判定', 125, r => badge(r.status, r.tone)),
    col('fact', '实测值', 192), col('rule', '阈值', 200), col('read', '判读', 280),
  ], '按判定', pluck('status'));
  const inventoryTable = table('#inventory-table', '运行单元清单', [
    col('label', '名字', 226, r => graphLink(r.id, r.label)), col('role_cn', '类型', 124),
    col('state', '状态', 138, r => badge(r.state, r.state === '在跑' ? 'good' : '')),
    col('owner', '归属', 134), col('links', '连到', 208), col('detail', '说明', 235),
  ], '按类型', pluck('role_cn'));
  const compareTable = table('#compare-table', '旧版 34 块对照', [
    col('name', '旧版区块', 180), col('state', '承接状态', 144, r => badge(r.state, r.tone)),
    col('where', '入口', 152, r => `<a href="${esc(r.href)}">${esc(r.where)}</a>`), col('why', '范围或暂留原因', 315),
  ], '按承接状态', pluck('state'), {empty: '尚无对照记录'});

  document.addEventListener('click', event => {
    const link = event.target.closest('button[data-arch-node]');
    if (!link || !graph) return;
    graph.focus(link.dataset.archNode, true);
    if (location.hash !== '#arch') location.hash = 'arch';
    else $('#arch').scrollIntoView();
    const heading = $('#h-arch');
    heading.tabIndex = -1;
    heading.focus({preventScroll: true});
  });

  function setupNav() {
    const nav = $('#page-nav'), handheld = matchMedia('(max-width: 800px)');
    const openButton = $('#menu-open'), overlay = $('#menu-close'), fold = $('#rail-toggle');
    const navLinks = $$('nav a[href^="#"]');
    const targets = navLinks.map(link => $(link.hash));
    navLinks.forEach(link => { link.title = link.dataset.icon; });
    const setDrawer = (open, restore = false) => {
      document.body.classList.toggle('nav-open', open);
      openButton.setAttribute('aria-expanded', String(open));
      openButton.setAttribute('aria-label', open ? '关闭目录' : '打开目录');
      overlay.hidden = !open;
      nav.inert = handheld.matches && !open;
      if (open) (nav.querySelector('[aria-current]') || navLinks[0]).focus();
      if (restore) openButton.focus();
    };
    const setNarrow = narrow => {
      document.body.classList.toggle('nav-narrow', narrow);
      fold.setAttribute('aria-pressed', String(narrow));
      fold.setAttribute('aria-label', narrow ? '恢复完整目录' : '切成窄目录');
      fold.title = fold.getAttribute('aria-label');
    };
    let saved = false;
    try { saved = localStorage.getItem('status-narrow-nav') === 'true'; } catch {}
    setNarrow(saved);
    setDrawer(false);
    openButton.addEventListener('click', () => setDrawer(!document.body.classList.contains('nav-open')));
    overlay.addEventListener('click', () => setDrawer(false, true));
    handheld.addEventListener('change', () => setDrawer(false));
    fold.addEventListener('click', () => {
      const narrow = !document.body.classList.contains('nav-narrow');
      setNarrow(narrow);
      try { localStorage.setItem('status-narrow-nav', String(narrow)); } catch {}
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && document.body.classList.contains('nav-open')) setDrawer(false, true);
    });
    navLinks.forEach((link, index) => link.addEventListener('click', () => {
      setCurrent(index);
      if (handheld.matches) {
        setDrawer(false);
        const heading = targets[index].querySelector('h1, h2');
        heading.setAttribute('tabindex', '-1');
        heading.focus({preventScroll: true});
      }
    }));
    function setCurrent(active) {
      navLinks.forEach((link, i) => {
        if (i === active) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
      });
    }
    function mark() {
      const top = $('.top').getBoundingClientRect().bottom + 12;
      let active = 0;
      targets.forEach((section, index) => {
        if (section.getBoundingClientRect().top <= top) active = index;
      });
      if (innerHeight + scrollY >= document.documentElement.scrollHeight - 2) active = targets.length - 1;
      setCurrent(active);
    }
    addEventListener('scroll', mark, {passive: true});
    addEventListener('hashchange', mark);
    addEventListener('resize', mark);
    mark();
  }

  function meter(id, value, unit, note, tone, digits = 0) {
    const box = $(`[data-metric="${id}"]`);
    box.dataset.tone = tone || '';
    box.querySelector('.reading').innerHTML = value === null
      ? `<small>${noData}</small>` : `${fmt(value, '', digits)}<small>${esc(unit)}</small>`;
    box.querySelector('.reading-note').textContent = value === null ? noData : note;
    const bar = box.querySelector('.bar span');
    if (bar) bar.style.width = Math.min(100, Math.max(0, value ?? 0)) + '%';
  }

  function showCurrent() {
    const snap = state.snapshot, host = snap?.host || {};
    const mem = D.percent(host.mem_pct), disk = D.percent(host.disk_pct);
    const memTone = mem >= 90 ? 'warn' : '';
    const diskTone = disk >= diskClean ? 'bad' : disk >= 75 ? 'warn' : '';
    meter('mem', mem, '%', memTone ? '需关注 · 已达 90%' : '使用率 · 90% 提醒', memTone);
    meter('disk', disk, '%', (diskTone === 'bad' ? '已达自愈阈值 · ' : diskTone === 'warn' ? '偏高 · ' : '') + gb(host.disk_used_b), diskTone);
    text('#snapshot-time', snap
      ? `${stamp(snap.updated_epoch)} · 悉尼 · 运行快照${state.snapshotAlert || (snapshotStale(snap) ? ' · 采集已超过 3 分钟' : '')}`
      : '运行快照取数失败。可刷新重试。');
  }

  const snapshotStale = snap => Date.now() / 1000 - snap.updated_epoch > 180;

  function showAttention() {
    const snap = state.snapshot;
    if (!snap) {
      text('#attention-summary', '运行快照缺失，待处理事项暂无数据。');
      alertTable.setRows([]);
      return;
    }
    const levels = {bad: '待处理', warn: '需关注', ok: '正常'};
    let entries = snap.attention ? [...snap.attention] : legacyOverviewAttention(snap);
    if (snapshotStale(snap) && snap.attention) entries = [{lv: 'bad', t: '快照停更', d: '采集已超过 3 分钟，元自愈会尝试重跑。'}, ...entries];
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

  const canvasRate = navigator.connection?.saveData || (navigator.deviceMemory && navigator.deviceMemory <= 4)
    ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const resourceSeries = [
    {key: 'mem', name: '内存', stroke: '#2563eb', ink: '#1d4ed8'},
    {key: 'disk', name: '磁盘', stroke: '#d97706', ink: '#994b05'},
  ];
  const periodNames = {1: '24 小时', 7: '7 天', 30: '30 天'};
  const percentLabel = value => fmt(value, '%', Number.isInteger(value) ? 0 : 1);
  const graphTip = $('#tip');
  function liquidTip({chart, tooltip}, lines) {
    if (!tooltip.opacity || !lines.length) { graphTip.hidden = true; return; }
    const rect = chart.canvas.getBoundingClientRect();
    graphTip.innerHTML = lines.map((line, i) => i === 0 ? `<strong>${esc(line)}</strong>` : esc(line)).join('<br>');
    graphTip.hidden = false;
    const x = rect.left + tooltip.caretX + 15, y = rect.top + tooltip.caretY + 15;
    graphTip.style.left = Math.max(6, Math.min(x, innerWidth - graphTip.offsetWidth - 6)) + 'px';
    graphTip.style.top = Math.max(6, y + graphTip.offsetHeight < innerHeight ? y : y - graphTip.offsetHeight - 30) + 'px';
  }
  const clearTip = () => { graphTip.hidden = true; };
  addEventListener('scroll', clearTip, {passive: true});
  $('#history-chart').addEventListener('pointerleave', clearTip);

  if (window.Chart) {
    Chart.Interaction.modes.resourcePoint = (chart, event, options, useFinalPosition) =>
      Chart.Interaction.modes.nearest(chart, event, options, useFinalPosition).slice(0, 1);
  }

  const historyAnnotations = {
    id: 'historyAnnotations',
    beforeDatasetsDraw({ctx, chartArea, scales}) {
      const y = scales.y.getPixelForValue(diskClean);
      ctx.save();
      ctx.strokeStyle = '#c4ccd8';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.stroke();
      ctx.restore();
    },
    afterDatasetsDraw(chart) {
      const {ctx, chartArea, scales} = chart;
      const labels = chart.data.datasets.flatMap((dataset, i) => {
        const last = dataset.data.findLastIndex(point => point.y !== null);
        if (last < 0) return [];
        const {x, y} = chart.getDatasetMeta(i).data[last];
        return [{x, y, text: `${dataset.label} ${percentLabel(dataset.data[last].y)}`, ink: resourceSeries[i].ink}];
      });
      labels.push({x: chartArea.right, y: scales.y.getPixelForValue(diskClean),
        text: `自愈清理线 ${diskClean}%`, ink: '#536174', threshold: true});
      labels.sort((a, b) => a.y - b.y);

      // Work in CSS pixels so the labels keep their separation at every display density.
      let ceiling = chartArea.bottom - 7;
      for (const label of [...labels].reverse()) {
        label.baseline = Math.min(label.y, ceiling);
        ceiling = label.baseline - 18;
      }
      let floor = chartArea.top + 7;
      ctx.save();
      ctx.textBaseline = 'middle';
      for (const label of labels) {
        const y = Math.max(label.baseline, floor);
        floor = y + 18;
        if (Math.abs(y - label.y) > 4) {
          ctx.strokeStyle = '#aeb9c7';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(label.x + 2, label.y);
          ctx.lineTo(label.x + 8, y);
          ctx.stroke();
        }
        ctx.font = (label.threshold ? '500 11px' : '650 12px') + ' system-ui, sans-serif';
        ctx.fillStyle = label.ink;
        ctx.fillText(label.text, label.x + 11, y);
      }
      ctx.restore();
    },
  };

  function trendSummary(points, period) {
    const disk = points.filter(point => point.disk !== null);
    const readings = disk.length ? disk : points.filter(point => point.mem !== null);
    if (!readings.length) return '所选时段没有内存或磁盘样本。';
    const key = disk.length ? 'disk' : 'mem';
    const name = disk.length ? '磁盘' : '内存';
    const first = readings[0][key], last = readings.at(-1)[key];
    const trend = readings.length === 1 ? `${name}仅采到一笔，使用率为 ${percentLabel(last)}`
      : first === last ? `${name}在所选 ${periodNames[period]}的首末读数都是 ${percentLabel(last)}`
        : `${name}在所选 ${periodNames[period]}内从 ${percentLabel(first)} ${last > first ? '升' : '降'}到 ${percentLabel(last)}`;
    return trend + (disk.length && last >= diskClean ? '，最新读数已达自愈清理线。' : '。');
  }

  function showHistory() {
    const {history, epoch, period, snapshot} = state;
    const points = history && epoch ? D.plot(history, period, epoch, snapshot) : [];
    dailyRows = history && epoch ? D.daily(history, period, epoch) : [];
    if (snapshot) dailyRows.unshift(D.currentRow(snapshot));
    dailyTable.setRows(dailyRows);
    $('#history-csv').disabled = !dailyRows.length;
    text('#history-summary', history && epoch
      ? trendSummary(points, period)
      : '历史文件取数失败。可刷新重试。');
    const sampling = {1: '10 分钟平均', 7: '小时采样', 30: '按日取最后读数'}[period];
    const drawable = points.length > 1 && points[0].t < points.at(-1).t && Boolean(window.Chart);
    text('#history-context', points.length
      ? [`悉尼时间 · ${sampling}`, drawable ? '点选可筛选下方日期' : '', state.historyEmbedded ? '来源为运行快照内的历史' : ''].filter(Boolean).join(' · ')
      : '');
    $('#history-chart').parentElement.hidden = !drawable;
    $('#history-empty').hidden = drawable || !points.length;
    if (!drawable && points.length) text('#history-empty', window.Chart
      ? '样本不足以绘制趋势。下表可查看已有读数。'
      : '图表组件未加载。下表可查看已有读数。');
    clearTip();
    historyChart?.destroy();
    if (!drawable) return;
    const datasets = resourceSeries.map(({key, name, stroke}) => ({
      label: name, data: points.map(point => ({x: point.t, y: point[key]})),
      borderColor: stroke, backgroundColor: stroke, borderWidth: 2.5,
      cubicInterpolationMode: 'monotone', fill: false, spanGaps: false, pointRadius: 0,
      pointHoverRadius: 4, pointHoverBackgroundColor: stroke, pointHoverBorderColor: '#fff', pointHoverBorderWidth: 1.5,
    }));
    const tickColor = '#54657e';
    historyChart = new Chart($('#history-chart'), {
      type: 'line', data: {datasets}, plugins: [historyAnnotations], options: {
        parsing: false, normalized: true, animation: false, maintainAspectRatio: false, devicePixelRatio: canvasRate,
        interaction: {intersect: false, mode: 'resourcePoint'},
        layout: {padding: {top: 4, right: 107}},
        scales: {
          x: {
            type: 'linear', min: points[0].t, max: points.at(-1).t, offset: false,
            afterBuildTicks(scale) {
              const count = scale.chart.width < 450 ? 4 : 5;
              scale.ticks = Array.from({length: count}, (_, i) =>
                ({value: Math.round(scale.min + (scale.max - scale.min) * i / (count - 1))}));
            },
            ticks: {autoSkip: false, align: 'inner', maxRotation: 0, padding: 9, color: tickColor,
              callback: epoch => period === 1 ? D.clock(epoch)
                : period === 30 ? new Date(epoch * 1000).toISOString().slice(5, 10) : D.day(epoch).slice(5)},
            grid: {display: false}, border: {display: false},
          },
          y: {
            min: 0, max: 100,
            ticks: {stepSize: 50, padding: 9, color: tickColor, callback: value => value + '%'},
            grid: {color: '#e5e7eb', drawTicks: false}, border: {display: false},
          },
        },
        plugins: {legend: {display: false}, tooltip: {enabled: false, external: context => {
          const picked = context.tooltip.dataPoints?.[0];
          if (!picked) { clearTip(); return; }
          const point = points[picked.dataIndex];
          liquidTip(context, [period === 30 ? point.date + ' · 悉尼' : stamp(point.t),
            `${picked.dataset.label} ${percentLabel(picked.parsed.y)}`, point.source]);
        }}},
        onClick: (_event, elements) => {
          if (elements.length) dailyTable.pick(points[elements[0].index].date);
        },
      },
    });
  }

  $('#history-csv').addEventListener('click', () => {
    const csvQuote = value => '"' + String(value).replaceAll('"', '""') + '"';
    const rows = [
      ['日期（悉尼）', '内存均值 (%)', '内存峰值 (%)', '磁盘已用 (%)', '磁盘已用 (字节)',
        '负载均值', '负载峰值', '在跑容器 (个)', '探测在线率 (%)', '采样口径'],
      ...dailyRows.map(r => [r.date, fmt(r.memAvg, '', 1), fmt(r.memPeak, '', 1), fmt(r.disk, '', 1),
        fmt(r.diskBytes), fmt(r.loadAvg, '', 2), fmt(r.loadPeak, '', 2), fmt(r.containers), fmt(r.probes, '', 1), r.source]),
    ];
    const blob = new Blob(['\uFEFF', rows.map(row => row.map(csvQuote).join(',')).join('\r\n'), '\r\n'], {type: 'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `服务器历史-${D.day(state.epoch)}-悉尼.csv`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  });
  $$('[data-period]').forEach(button => button.addEventListener('click', () => {
    state.period = Number(button.dataset.period);
    $$('[data-period]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    showHistory();
  }));

  function showOrg() {
    const {lines, missing} = D.organization(state.arch || {}, state.snapshot || {});
    if (!state.arch && !state.snapshot) {
      text('#org-summary', '架构和运行快照取数失败。');
      text('#org-missing', '归属清点无数据。');
    } else {
      const projects = state.snapshot?.projects.length;
      const catalog = projects == null ? '项目登记未取到' : `登记项目 ${projects} 个`;
      const linesText = state.arch ? `已记录 ${lines.size} 个归属分组` : '架构记录未取到';
      text('#org-summary', `${linesText} · ${catalog}。按已写明的归属展示。`);
      const nodes = missing.filter(row => row.id).length;
      text('#org-missing', `归属未知 ${missing.length} 项（${nodes} 个架构节点，${missing.length - nodes} 个项目登记）。`);
    }
    $('#org-tree').innerHTML = [...lines].sort(([a], [b]) => a.localeCompare(b)).map(([line, projects]) => `
      <article class="org-line"><h3>${esc(line)}</h3><ul>${[...projects].map(([project, units]) => `
        <li><strong>${esc(project)}</strong>
        ${units.length ? `<ul class="unit-list">${units.map(n =>
          `<li>${graphLink(n.id, n.label)} · ${esc(n.role_cn || n.role)}</li>`).join('')}</ul>` : '<p>暂未关联架构节点</p>'}</li>`).join('')}</ul></article>`).join('');
    orgTable.setRows(missing);
  }

  function showArchitecture() {
    const arch = state.arch;
    if (!arch) {
      text('#arch-summary', '架构数据取数失败。可刷新重试。');
      text('#health-summary', '架构数据缺失，暂时无法判定。');
      inventoryTable.setRows([]);
      return;
    }
    const active = arch.counts || {};
    text('#arch-summary', `${active['域名'] ?? noData} 个域名 · ${active['隧道'] ?? noData} 条在用隧道 · ${active['系统服务'] ?? noData} 个系统服务 · ${arch.edges.length} 条连线。 ${stamp(arch.generated_epoch)}（悉尼）${arch.cloudflare_note ? ' · 云入口：' + arch.cloudflare_note : ''}`);
    const map = new Map(arch.nodes.map(n => [n.id, n.label]));
    const inventory = arch.nodes.map(n => ({...n, state: n.state || (n.role === 'domain' ? '对外记录' : '无运行状态'),
      owner: n.owner || '未登记', links: arch.edges.filter(e => e.from === n.id).map(e => map.get(e.to)).filter(Boolean).join('、') || '无连线',
      detail: n.detail || noData}));
    inventoryTable.setRows(inventory);
    graph?.dispose();
    graph = mountArchitecture(arch, node => {
      if (node) {
        inventoryFilterBeforeFocus ??= inventoryTable.search.value;
        inventoryTable.search.value = node.label;
      } else if (inventoryFilterBeforeFocus !== null) {
        inventoryTable.search.value = inventoryFilterBeforeFocus;
        inventoryFilterBeforeFocus = null;
      }
      inventoryTable.render();
    });

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

  function backupEpoch(snapshot) {
    return snapshot?.ops?.backup?.epoch ?? D.sourceEpoch(snapshot?.ops?.backup?.at, '+00:00');
  }

  function showFlows() {
    const {arch, snapshot, history} = state;
    const routes = D.routes(arch || {}, snapshot || {});
    routeTable.setRows(routes);
    const bad = routes.filter(row => row.state === '断点').length;
    const gaps = routes.filter(row => row.state === '路由缺证').length;
    text('#route-summary', arch || snapshot ? `${routes.length} 个对外域名，${bad} 条有断点，${gaps} 条仍缺路由证据。探测失败只标端点。横向滑动查后端与依赖。`
      : '架构和探测均未取到，可刷新重试。');
    const backup = snapshot?.usage?.find(item => item.key === 'github_backup');
    const snapshotAt = snapshot?.updated_epoch;
    const historyAt = history?.min?.t?.at(-1);
    lineageTable.setRows([
      {group: '运行采集', from: '主采集器', file: 'snapshot.json', url: '/data/snapshot.json', section: '现在、组织、逻辑链、成本',
        schedule: '每 1 分钟', at: snapshotAt, retention: '覆盖上一次', source: 'status README 与运行快照'},
      {group: '运行采集', from: '架构采集器', file: 'arch.json / arch.mmd', url: '/data/arch.json', section: '组织、架构、逻辑链、体检、清单',
        schedule: '每 5 分钟', at: arch?.generated_epoch, retention: '覆盖上一次', source: 'linze-status-arch.cron'},
      {group: '运行采集', from: '主采集器 · 主机样本', file: 'history.json', url: '/data/history.json', section: '历史曲线、每日表',
        schedule: '每分钟采样', at: historyAt, retention: '分钟 1,440 点 · 小时 744 点 · 旧日档不限期 · 悉尼日统计 31 天',
        source: '主采集器的留存逻辑'},
      {group: '运行采集', from: 'GitHub 采集器', file: 'github_public.json', url: '/data/github_public.json', section: '旧版 GitHub、待处理事项中的 CI',
        schedule: '快采每分钟 · 流量每 15 分钟 · 深采每小时', at: snapshot?.github?.collected_epoch,
        retention: '公开文件覆盖上一次', source: 'linze-github.cron 与运行快照'},
      {group: '备份', from: '数据库和 status 历史', file: '本机加密备份', section: '异地备份上游',
        schedule: '每日 · 悉尼夏令时 14:40／标准时 13:40', at: backupEpoch(snapshot), retention: '最近 7 份',
        source: '异地备份脚本与运行快照'},
      {group: '备份', from: '本机加密备份', file: 'GitHub Release', section: '异地备份主通道',
        schedule: '每日', at: backup?.latest_epoch, retention: backup?.limit ? `最近 ${backup.limit} 份` : '脚本保留 30 份',
        source: '异地备份脚本与运行快照的滚动用量'},
      {group: '备份', from: '异地加密备份', file: 'Cloudflare R2', section: '加密整包通道已停写',
        schedule: '停写', at: null, retention: '存量保留期无数据', source: '工作区零付费台账与异地备份脚本'},
      {group: '镜像', from: 'status 运行数据', file: 'Cloudflare R2 数据镜像', section: '独立的 status 数据镜像',
        schedule: '工作区台账登记每 5 分钟', at: null, retention: noData, source: '工作区 R2 周期任务台账'},
    ]);
  }

  function showCosts() {
    const snapshot = state.snapshot;
    const cost = snapshot?.cost;
    text('#cost-summary', cost ? `价格库按 ${D.day(snapshot.updated_epoch).slice(0, 7)} 折算本月扣款。月摊将年费分到每个月。实扣以账单为准。`
      : '成本快照取数失败。可刷新重试。');
    $('#cost-meters').innerHTML = cost ? [
      ['当月成本', aud(cost.month_cash_aud), num(cost.month_cash_cny) === null ? noData : '约 ¥' + fmt(cost.month_cash_cny, '', 2)],
      ['月摊', aud(cost.monthly_aud), '每月折算'],
    ].map(([label, value, note]) => `<div class="cost-metric">${label}<strong>${esc(value)}</strong><span>${esc(note)}</span></div>`).join('') : '';
    const latestBackup = snapshot?.usage?.find(item => item.key === 'github_backup')?.latest_epoch;
    vendorTable.setRows((snapshot?.inventory || []).map(vendor => ({
      ...vendor, status: vendor.status?.note || noData,
      tone: vendor.status?.ok === true ? 'good' : vendor.status?.ok === false ? 'bad' : '',
      risks: vendor.risks?.map(r => r.text).join(' · ') || noData,
      health: vendor.health?.map(({label, value}) => `${label} ${label === '最新备份'
        ? stamp(latestBackup || D.sourceEpoch(value)) : value}`).join(' · ') || noData,
    })));

    const capacity = snapshot?.capacity;
    const tones = {ok: 'good', warn: 'warn', bad: 'bad', tight: 'warn', crit: 'bad'};
    const levels = {ok: '达标', watch: '留意', tight: '紧张', crit: '告急'};
    if (!capacity) text('#capacity-verdict', '无容量判定。');
    else {
      const h = capacity.headroom || {};
      const tier = capacity.current_tier || {};
      $('#capacity-verdict').innerHTML = `<div>${badge(capacity.verdict, tones[capacity.level])} ${esc(brief(capacity.why))}</div>
        <p>${esc(fmt(tier.cores, ' 核'))} · 部署余量约 ${esc(fmt(h.slots, ' 个典型应用'))} · 可回收 ${esc(gb(h.reclaimable_b))}
        · 到 85% 预计 ${esc(fmt(capacity.eta_days, ' 天'))}</p>
        <p>${esc(brief(h.note))}</p><p>${esc(brief(capacity.target?.name || capacity.tiers_note))}</p>`;
    }
    capacityTable.setRows((capacity?.signals || []).map(signal => ({
      ...signal, status: levels[signal.level] || signal.level, tone: tones[signal.level], rule: brief(signal.rule), read: brief(signal.read),
    })));

    const sub = snapshot?.ops?.subs;
    const series = (sub?.items || []).map(item => {
      const base = state.snapshot.updated_epoch ? D.day(state.snapshot.updated_epoch) : null;
      const days = base ? num((Date.parse(item.date) - Date.parse(base)) / 86400000) : null;
      return {
        ...item, days, kind: item.end_only ? '服务终止' : '预计扣款', tone: tones[item.level],
        note: item.end_only ? '到期停机' : item.auto_renew ? '自动续费已开' : '自动续费未开',
      };
    });
    renewTable.setRows(series
      .concat((sub?.blind || []).map(row => ({...row, kind: '无日期', tone: 'warn', note: row.why || noData})))
      .concat((sub?.retired || []).map(row => ({...row, date: row.retired, kind: '已退役', note: '退出续费跟踪'}))));
    text('#renew-summary', sub ? `${series.length} 条有跟踪日期，${sub.blind?.length || 0} 条缺日期。天数距快照的悉尼日。`
      : '订阅账无数据。');
  }

  const overview = '/legacy.html#/';
  const runtime = '/legacy.html#/runtime';
  const flow = '/legacy.html#/flow';
  const cost = '/legacy.html#/cost';
  const github = '/legacy.html#/github';
  const timeline = '/legacy.html#/timeline';
  const moved = (name, where, href, why) => ({name, where, href, why, state: '新版已承接', tone: 'good'});
  const old = (name, href, why) => ({name, where: '旧版', href, why, state: '暂留旧版', tone: 'warn'});
  const compare = [
    moved('待处理事项', '体检清单', '#h-attention', '沿用严重度和快照告警判据，由各线处理。'),
    moved('容量与升级', '成本与容量', '#h-capacity', '显示结论、部署余量、阈值和实测。'),
    moved('供应商', '成本与容量', '#h-vendors', '沿用供应商状态、成本、风险和健康。'),
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
    moved('主机趋势', '历史', '#history', '内存和磁盘有 24 小时、7 天、30 天曲线。负载和容器见历史表。'),
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
    moved('续费时间轴', '成本与容量', '#h-renew', '按同一订阅账显示日期、剩余天数、停机和自动续费状态。'),
    old('订阅历史', timeline, '保留旧版的历史分期和时间轴。'),
    moved('盯不住的订阅', '成本与容量', '#h-renew', '单独标出无日期订阅与退役订阅。'),
  ];

  async function json(key) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(`/data/${key}.json`, {cache: 'no-store', signal: controller.signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const document = await response.json();
      if (!sources[key](document)) throw new Error('数据结构不符');
      return document;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function read() {
    const button = $('#refresh');
    if (button.disabled) return;
    lastAttempt = Date.now();
    button.disabled = true;
    button.textContent = '读取中…';
    const keys = Object.keys(sources);
    const results = await Promise.allSettled(keys.map(json));
    for (const [i, key] of keys.entries()) {
      if (results[i].status === 'fulfilled') state[key] = results[i].value;
    }
    state.snapshotAlert = results[0].status === 'rejected' && state.snapshot ? ' · 刷新失败，保留上次已读快照' : '';
    state.historyEmbedded = results[1].status === 'rejected' && Boolean(state.snapshot?.history);
    if (state.historyEmbedded) state.history = state.snapshot.history;
    const historyEpochs = ['min', 'hour', 'day'].map(tier => num(state.history?.[tier]?.t?.at(-1)) ?? 0);
    state.epoch = Math.max(num(state.snapshot?.updated_epoch) ?? 0, ...historyEpochs) || null;
    const metadata = $('#site-standard');
    if (state.snapshot?.updated_epoch) {
      const schema = JSON.parse(metadata.textContent);
      schema.data_updated_at = new Date(state.snapshot.updated_epoch * 1000).toISOString();
      metadata.textContent = JSON.stringify(schema);
    }
    try {
      showCurrent();
      showHistory();
      if (results[2].status === 'fulfilled' || !graph) showArchitecture();
      showAttention();
      showOrg();
      showFlows();
      showCosts();
    } finally {
      button.disabled = false;
      button.textContent = '刷新数据';
      document.body.dataset.loaded = 'true';
    }
  }

  setupNav();
  compareTable.setRows(compare);
  text('#compare-summary', `${compare.length} 块中 ${compare.filter(row => row.tone === 'good').length} 块已承接。其余仍可从旧版逐项查看。`);
  $('#refresh').addEventListener('click', read);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - lastAttempt >= 60000) read();
  });
  read();
})();
