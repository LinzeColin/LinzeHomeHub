(() => {
  const D = OverviewData;
  const {esc, fmt, num, noData, stamp} = D;
  const {
    $, $$, text, col, table, tableGroup, unitGroup, pluck, badge, graphLink, backupEpoch, selectedNode, nodeUrl, detailUrl,
    detailView, params, start,
  } = OverviewPage;
  const home = document.body.dataset.pageKind === 'home';
  const sourceKeys = {
    history: ['snapshot', 'history'], arch: ['arch'], org: ['snapshot', 'arch'],
    flows: ['snapshot', 'history', 'arch'], cost: ['snapshot'],
  };
  if (!home && !sourceKeys[detailView]) return;
  const renderInventory = home ? OverviewInventory.mount(OverviewPage) : null;

  const movedAnchors = new Set(['#health', '#h-health', '#h-attention', '#list', '#h-list', '#machine', '#compare', '#h-compare']);
  const detailAnchors = {
    '#arch': 'arch', '#h-arch': 'arch', '#org': 'org', '#h-org': 'org',
    '#flows': 'flows', '#cost': 'cost', '#h-daily': 'history', '#h-unowned': 'org', '#h-routes': 'flows', '#h-dataflow': 'flows',
    '#h-vendors': 'cost', '#h-renew': 'cost', '#h-capacity': 'cost',
  };
  function redirectMovedAnchor() {
    const hash = location.hash;
    if (hash === '#history') {
      window.history.replaceState(window.history.state, '', '#trend');
      return false;
    }
    const detail = detailAnchors[hash];
    if (movedAnchors.has(hash)) location.replace(nodeUrl('/check.html', selectedNode(), hash));
    else if ((!hash || hash === '#arch' || hash === '#h-arch') && selectedNode()) {
      location.replace(detailUrl('arch', '#arch', {node: selectedNode()}));
    }
    else if (detail) {
      const landing = {'#h-arch': '#arch', '#h-org': '#org', '#flows': '#routes', '#cost': '#bills'};
      location.replace(detailUrl(detail, landing[hash] || hash));
    }
    else return false;
    return true;
  }
  if (home) {
    if (redirectMovedAnchor()) return;
    addEventListener('hashchange', redirectMovedAnchor);
  }

  const days = Number(window.history.state?.resourcePeriod ?? params.get('days'));
  const period = [1, 7, 30].includes(days) ? days : 7;
  const state = {period, epoch: null, snapshot: null, history: null, arch: null, snapshotAlert: '', historyEmbedded: false};
  const diskClean = 85;
  let graph, historyChart, dailyRows = [];
  let requestedDate = params.get('date');
  let requestedNode = selectedNode();

  const brief = text => String(text ?? noData).replaceAll('**', '');
  const aud = OverviewInventory.money;
  const gb = bytes => num(bytes) === null ? noData : fmt(num(bytes) / 1024 ** 3, ' GiB', 1);

  const memLoad = (average, peak, unit, digits) => r => {
    const first = esc(fmt(r[average], unit, digits));
    return r.current ? first : `${first} / ${esc(fmt(r[peak], unit, digits))}`;
  };
  const historyColumns = [
    col('date', '日期', 125, r => `<span title="${esc(r.source)}">${esc(r.date)}</span>`),
    col('memAvg', '内存均值／峰值', 174, memLoad('memAvg', 'memPeak', '%', 1)),
    col('disk', '系统盘已用', 150, r => esc(fmt(r.disk, '%', 1) + (r.diskBytes == null ? '' : ' · ' + gb(r.diskBytes)))),
    col('diskX', '附加盘已用', 150, r => esc(fmt(r.diskX, '%', 1) + (r.diskXBytes == null ? '' : ' · ' + gb(r.diskXBytes)))),
    col('diskAll', '两盘合计', 150, r => esc(fmt(r.diskAll, '%', 1) + (r.diskAllBytes == null ? '' : ' · ' + gb(r.diskAllBytes)))),
    col('loadAvg', '负载均值／峰值', 173, memLoad('loadAvg', 'loadPeak', '', 2)),
    col('containers', '在跑容器', 135), col('probes', '探测在线率', 153, r => esc(fmt(r.probes, '%', 1))),
  ];
  const dailyTable = table('#daily-table', '每日资源历史', historyColumns, '按日期', pluck('date'), {
    isPinned: row => row.current === true, rowClass: row => row.current ? 'current-row' : '',
  });
  const archiveTable = table('#history-archive', '逐日原始归档', [
    col('date', '日期', 138), col('mem', '内存', 126, row => esc(fmt(row.mem, '%', 1))),
    col('disk', '系统盘', 126, row => esc(fmt(row.disk, '%', 1))),
    col('diskX', '附加盘', 126, row => esc(fmt(row.diskX, '%', 1))),
    col('diskAll', '两盘合计', 126, row => esc(fmt(row.diskAll, '%', 1))),
    col('load', '15 分钟负载', 167, row => esc(fmt(row.load, '', 2))), col('t', '采样时间（悉尼）', 280, row => esc(stamp(row.t))),
  ], '按日期', pluck('date'));
  const unowned = $('#org-table');
  const unownedTables = unowned && new Map([
    ['ingress', '未写归属的入口与通道'],
    ['runtime', '未写归属的项目、应用与底座'],
    ['services', '未写归属的服务与任务'],
  ].map(([group, title]) => [group, tableGroup(unowned, 'unowned-' + group, title, [
    col('label', '项目或运行单元', 260, r => graphLink(r.id, r.label)), col('type', '类型', 125),
    col('project', '已知项目', 170), col('detail', '登记或识别依据', 460),
  ], '按类型', pluck('type'), {rowClass: () => 'flag-row'})]));
  const routeTable = table('#route-table', '对外逻辑链', [
    col('host', '对外域名', 240, r => graphLink(r.node, r.host)),
    col('state', '链路状态', 142, r => badge(r.state, r.state === '断点' ? 'bad' : r.state === '路由缺证' ? 'warn' : 'good')),
    col('cloud', 'Cloudflare', 155), col('tunnel', '隧道', 145), col('proxy', '代理', 174),
    col('service', '服务', 280), col('dependencies', '库或外部依赖', 315),
    col('probes', '探测结果', 310), col('findings', '断点或缺证', 420,
      r => `<span class="${r.state === '有路由记录' ? '' : 'bad-text'}">${esc(r.findings)}</span>`),
  ], '按状态', pluck('state'), {rowClass: r => r.state === '有路由记录' ? '' : 'flag-row'});
  const lineageTable = table('#lineage-table', '采集与备份数据链', [
    col('from', '采集器／上游', 178), col('file', '产物或下游', 180, r => r.url
      ? `<a download href="${esc(r.url)}">${esc(r.file)}</a>` : esc(r.file)),
    col('section', '页面区块／用途', 192), col('schedule', '频率', 158),
    col('at', '最新记录（悉尼）', 208, r => esc(stamp(r.at))),
    col('retention', '留存', 180), col('source', '依据', 223)
  ], '按用途', pluck('group'));
  const billingTable = table('#billing-table', '账单明细', OverviewInventory.billingColumns(OverviewPage), '按周期', pluck('state'));
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
    const disks = Array.isArray(host.disks) ? host.disks : [];
    const multi = disks.length > 1;
    const mem = D.percent(host.mem_pct);
    // 磁盘大数字按两盘合计（Owner 11-23）；系统盘单独超 85% 只在分盘行里提示，不顶替总数。
    const disk = D.percent(multi ? host.disk_all_pct : host.disk_pct);
    const memTone = mem >= 90 ? 'warn' : '';
    const diskTone = disk >= diskClean ? 'bad' : disk >= 75 ? 'warn' : '';
    meter('mem', mem, '%', memTone ? '需关注 · 已达 90%' : '使用率 · 90% 提醒', memTone);
    const diskNote = multi
      ? `${disks.length} 块盘合计 · 已用 ${gb(host.disk_all_used_b)} / ${gb(host.disk_all_total_b)}`
      : `已用 ${gb(host.disk_used_b)}`;
    meter('disk', disk, '%', (diskTone === 'bad' ? '已达自愈阈值 · ' : diskTone === 'warn' ? '偏高 · ' : '') + diskNote, diskTone, multi ? 1 : 0);
    const diskBox = document.querySelector('[data-metric="disk"]');
    if (diskBox) {
      let list = diskBox.querySelector('.disk-list');
      if (multi) {
        if (!list) { list = document.createElement('ul'); list.className = 'disk-list'; diskBox.appendChild(list); }
        list.innerHTML = disks.map(d => {
          const pct = D.percent(d.pct);
          const tone = pct >= diskClean ? 'bad' : pct >= 75 ? 'warn' : '';
          const hint = d.role === '系统盘' && pct >= diskClean ? '<em>该搬目录到附加盘</em>' : '';
          return `<li data-tone="${tone}"><span>${esc(d.role)} <code>${esc(d.mount)}</code>${hint}</span><b>${esc(fmt(d.pct, '%', 1))}</b>`
            + `<i class="mini-bar" aria-hidden="true"><span style="width:${Math.min(100, pct ?? 0)}%"></span></i>`
            + `<small>${esc(gb(d.used_b))} / ${esc(gb(d.total_b))}</small></li>`;
        }).join('');
      } else if (list) list.remove();
    }
    const live = $('#live-state');
    if (live) {
      const stale = !snap || snapshotStale(snap);
      live.dataset.tone = stale ? 'warn' : 'good';
      live.textContent = !snap ? '取数失败' : stale ? '采集超时' : '实时 · 每分钟采集';
    }
    text('#snapshot-time', snap
      ? `VPS-3 · ${D.day(snap.updated_epoch)} ${D.clock(snap.updated_epoch)} 悉尼${state.snapshotAlert || (snapshotStale(snap) ? ' · 采集已超过 3 分钟' : '')}`
      : '运行快照取数失败。可刷新重试。');
  }

  const snapshotStale = snap => Date.now() / 1000 - snap.updated_epoch > 180;

  const canvasRate = navigator.connection?.saveData || (navigator.deviceMemory && navigator.deviceMemory <= 4)
    ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const resourceSeries = [
    {key: 'mem', name: '内存', stroke: '#2563eb', ink: '#1d4ed8'},
    {key: 'disk', name: '系统盘', stroke: '#d97706', ink: '#994b05'},
    {key: 'diskX', name: '附加盘', stroke: '#0f766e', ink: '#0b5c56'},
    {key: 'diskAll', name: '两盘合计', stroke: '#7c3aed', ink: '#6024c9'},
  ];
  // 点图例名称显示或隐藏那条线（Owner 11-23），记在本机，下次打开还是这样。
  const hiddenSeries = new Set();
  try { JSON.parse(localStorage.getItem('status-hidden-series') || '[]').forEach(key => hiddenSeries.add(key)); } catch {}
  const saveHidden = () => { try { localStorage.setItem('status-hidden-series', JSON.stringify([...hiddenSeries])); } catch {} };
  function renderLegend(points) {
    const box = $('#history-legend');
    if (!box) return;
    box.innerHTML = resourceSeries.map(({key, name, stroke}) => {
      const values = points.filter(p => p[key] != null);
      const last = values.at(-1)?.[key];
      const on = !hiddenSeries.has(key);
      return `<button type="button" class="legend-chip" data-series="${key}" aria-pressed="${on}" style="--swatch:${stroke}">`
        + `<span class="swatch" aria-hidden="true"></span><span>${esc(name)}</span>`
        + `<b>${values.length ? esc(percentLabel(last)) : '暂无读数'}</b></button>`;
    }).join('');
  }
  $('#history-legend')?.addEventListener('click', event => {
    const chip = event.target.closest('[data-series]');
    if (!chip || !historyChart) return;
    const key = chip.dataset.series;
    const index = historyChart.data.datasets.findIndex(set => set.key === key);
    if (index < 0) return;
    const on = !historyChart.isDatasetVisible(index);
    historyChart.setDatasetVisibility(index, on);
    if (on) hiddenSeries.delete(key); else hiddenSeries.add(key);
    saveHidden();
    chip.setAttribute('aria-pressed', String(on));
    historyChart.update();
  });
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
  if ($('#history-chart')) {
    addEventListener('scroll', clearTip, {passive: true});
    $('#history-chart').addEventListener('pointerleave', clearTip);
  }

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
      // 各线的最新读数已经在图例上，线尾只标自愈清理线，免得文字挤在一起（Owner 11-23）。
      const labels = [];
      labels.push({x: chartArea.right, y: scales.y.getPixelForValue(diskClean),
        text: `自愈清理线 ${diskClean}%`, ink: '#536174', threshold: true});
      labels.sort((a, b) => a.y - b.y);

      // Work in CSS pixels so the labels keep their separation at every display density.
      let ceiling = chartArea.bottom - 9;
      for (const label of [...labels].reverse()) {
        label.baseline = Math.min(label.y, ceiling);
        ceiling = label.baseline - 21;
      }
      let floor = chartArea.top + 9;
      ctx.save();
      ctx.textBaseline = 'middle';
      for (const label of labels) {
        const y = Math.max(label.baseline, floor);
        floor = y + 21;
        if (Math.abs(y - label.y) > 4) {
          ctx.strokeStyle = '#aeb9c7';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(label.x + 2, label.y);
          ctx.lineTo(label.x + 8, y);
          ctx.stroke();
        }
        ctx.font = (label.threshold ? '500' : '650') + ' 16px system-ui, sans-serif';
        ctx.fillStyle = label.ink;
        ctx.fillText(label.text, label.x + 11, y);
      }
      ctx.restore();
    },
  };

  function trendSummary(points, period) {
    // 有两盘合计读数就按合计说（Owner 11-23：磁盘看合计），否则按系统盘，再否则按内存。
    const all = points.filter(point => point.diskAll != null);
    const sys = points.filter(point => point.disk !== null);
    const disk = all.length > 1 ? all : sys;
    const readings = disk.length ? disk : points.filter(point => point.mem !== null);
    if (!readings.length) return '所选时段没有内存或磁盘样本。';
    const key = disk === all && all.length > 1 ? 'diskAll' : disk.length ? 'disk' : 'mem';
    const name = {diskAll: '两盘合计', disk: '系统盘', mem: '内存'}[key];
    const first = readings[0][key], last = readings.at(-1)[key];
    const trend = readings.length === 1 ? `${name}仅采到一笔，使用率为 ${percentLabel(last)}`
      : first === last ? `${name}在所选 ${periodNames[period]}的首末读数都是 ${percentLabel(last)}`
        : `${name}在所选 ${periodNames[period]}内从 ${percentLabel(first)} ${last > first ? '升' : '降'}到 ${percentLabel(last)}`;
    return trend + (disk.length && last >= diskClean ? '，最新读数已达自愈清理线。' : '。');
  }

  function showHistory() {
    const {history, epoch, period, snapshot} = state;
    const points = history && epoch ? D.plot(history, period, epoch, snapshot) : [];
    if (dailyTable) {
      dailyRows = history && epoch ? D.daily(history, period, epoch) : [];
      if (snapshot) dailyRows.unshift(D.currentRow(snapshot));
      dailyTable.setRows(dailyRows);
      if (requestedDate) dailyTable.pick(requestedDate, false);
      requestedDate = null;
      $('#history-csv').disabled = !dailyRows.length;
    }
    if (home) setHistoryLink();
    const sampling = {1: '10 分钟平均', 7: '小时采样', 30: '按日取最后读数'}[period];
    const drawable = points.length > 1 && points[0].t < points.at(-1).t && Boolean(window.Chart);
    text('#history-summary', !history || !epoch ? '历史文件取数失败。可刷新重试。'
      : points.length && !drawable ? (window.Chart ? '样本不足以绘制趋势，可查每日资源表。'
        : '图表组件未加载，可查每日资源表。') : trendSummary(points, period));
    text('#history-context', points.length
      ? [`悉尼时间 · ${sampling}`, drawable ? '点图按日期查表' : '', state.historyEmbedded ? '来源为运行快照内的历史' : ''].filter(Boolean).join(' · ')
      : '');
    $('#history-chart').hidden = !drawable;
    clearTip();
    historyChart?.destroy();
    historyChart = null;
    if (!drawable) { renderLegend(points); return; }
    // 四条线都画：附加盘、两盘合计 11-20 加盘前没有读数，那段留空（Owner 11-23）。
    const datasets = resourceSeries.map(({key, name, stroke, ink}) => ({
      key, hidden: hiddenSeries.has(key),
      label: name, ink, data: points.map(point => ({x: point.t, y: point[key] ?? null})),
      borderColor: stroke, backgroundColor: stroke, borderWidth: 2.5,
      cubicInterpolationMode: 'monotone', fill: false, spanGaps: false, pointRadius: 0,
      pointHoverRadius: 4, pointHoverBackgroundColor: stroke, pointHoverBorderColor: '#fff', pointHoverBorderWidth: 1.5,
    }));
    const tickColor = '#54657e';
    const narrowChart = $('#history-chart').parentElement.clientWidth < 460;
    historyChart = new Chart($('#history-chart'), {
      type: 'line', data: {datasets}, plugins: [historyAnnotations], options: {
        parsing: false, normalized: true, animation: false, maintainAspectRatio: false, devicePixelRatio: canvasRate,
        interaction: {intersect: false, mode: 'resourcePoint'},
        layout: {padding: {top: 4, right: narrowChart ? 104 : 122}},
        scales: {
          x: {
            type: 'linear', min: points[0].t, max: points.at(-1).t, offset: false,
            afterBuildTicks(scale) {
              const count = scale.chart.width < 450 ? 3 : 5;
              scale.ticks = Array.from({length: count}, (_, i) =>
                ({value: Math.round(scale.min + (scale.max - scale.min) * i / (count - 1))}));
            },
            ticks: {autoSkip: false, align: 'inner', maxRotation: 0, padding: 7, color: tickColor, font: {size: 16},
              callback: epoch => period === 1 ? D.clock(epoch)
                : period === 30 ? new Date(epoch * 1000).toISOString().slice(5, 10) : D.day(epoch).slice(5)},
            grid: {display: false}, border: {display: false},
          },
          y: {
            min: 0, max: 100,
            ticks: {stepSize: 50, padding: 7, color: tickColor, font: {size: 16}, callback: value => value + '%'},
            grid: {color: '#e5e7eb', drawTicks: false}, border: {display: false},
          },
        },
        plugins: {legend: {display: false}, tooltip: {enabled: false, external: context => {
          const picked = context.tooltip.dataPoints?.[0];
          if (!picked) { clearTip(); return; }
          const point = points[picked.dataIndex];
          const chart = context.chart;
          const rows = chart.data.datasets
            .filter((set, i) => chart.isDatasetVisible(i) && point[set.key] != null)
            .map(set => `${set.label} ${percentLabel(point[set.key])}`);
          liquidTip(context, [period === 30 ? point.date + ' · 悉尼' : stamp(point.t), ...rows, point.source].filter(Boolean));
        }}},
        onClick: (_event, elements) => {
          if (!elements.length) return;
          const date = points[elements[0].index].date;
          if (dailyTable) dailyTable.pick(date);
          else setHistoryLink(requestedDate === date ? null : date);
        },
      },
    });
    renderLegend(points);
  }

  function setHistoryLink(date = requestedDate) {
    const link = $('#history-detail');
    requestedDate = date;
    link.href = detailUrl('history', '#daily', {days: state.period, ...(date ? {date} : {})});
    link.textContent = date ? '查所选日资源表' : '每日资源表与 CSV';
    if (date) link.setAttribute('aria-label', `查看 ${date} 的每日资源表`);
    else link.removeAttribute('aria-label');
  }

  $('#history-csv')?.addEventListener('click', () => {
    const csvQuote = value => '"' + String(value).replaceAll('"', '""') + '"';
    const rows = [
      ['日期（悉尼）', '内存均值 (%)', '内存峰值 (%)', '系统盘已用 (%)', '系统盘已用 (字节)',
        '附加盘已用 (%)', '附加盘已用 (字节)', '两盘合计 (%)', '两盘合计 (字节)',
        '负载均值', '负载峰值', '在跑容器 (个)', '探测在线率 (%)', '采样口径'],
      ...dailyRows.map(r => [r.date, fmt(r.memAvg, '', 1), fmt(r.memPeak, '', 1), fmt(r.disk, '', 1),
        fmt(r.diskBytes), fmt(r.diskX, '', 1), fmt(r.diskXBytes), fmt(r.diskAll, '', 1), fmt(r.diskAllBytes), fmt(r.loadAvg, '', 2), fmt(r.loadPeak, '', 2), fmt(r.containers), fmt(r.probes, '', 1), r.source]),
    ];
    const blob = new Blob(['\uFEFF', rows.map(row => row.map(csvQuote).join(',')).join('\r\n'), '\r\n'], {type: 'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `服务器历史-${D.day(state.epoch)}-悉尼.csv`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  });
  const periods = $$('[data-period]');
  periods.forEach(button => {
    button.setAttribute('aria-pressed', String(Number(button.dataset.period) === state.period));
    button.addEventListener('click', () => {
      state.period = Number(button.dataset.period);
      window.history.replaceState({...window.history.state, resourcePeriod: state.period}, '');
      requestedDate = null;
      periods.forEach(b => b.setAttribute('aria-pressed', String(b === button)));
      showHistory();
    });
  });

  function showOrg() {
    const {lines, missing} = D.organization(state.arch || {}, state.snapshot || {});
    if (!state.arch && !state.snapshot) {
      text('#org-summary', '架构和运行快照取数失败。');
      if (unowned) text('#org-missing', '归属清点无数据。');
    } else {
      const projects = state.snapshot?.projects.length;
      const catalog = projects == null ? '项目登记未取到' : `登记项目 ${projects} 个`;
      const linesText = state.arch ? `${lines.size} 个归属分组` : '架构记录未取到';
      text('#org-summary', `${linesText} · ${catalog} · ${missing.length} 项未写归属。`);
      const nodes = missing.filter(row => row.id).length;
      if (unowned) text('#org-missing', `未写归属 ${missing.length} 项（${nodes} 个架构节点，${missing.length - nodes} 个项目登记）。`);
    }
    if (!unowned) return;
    $('#org-tree').innerHTML = [...lines].sort(([a], [b]) => a.localeCompare(b)).map(([line, projects]) => `
      <article class="org-line"><h3>${esc(line)}</h3><ul>${[...projects].map(([project, units]) => `
        <li><strong>${esc(project)}</strong>
        ${units.length ? `<ul class="unit-list">${units.map(n =>
          `<li>${graphLink(n.id, n.label)} · ${esc(n.role_cn || n.role)}</li>`).join('')}</ul>` : '<p>暂未关联架构节点</p>'}</li>`).join('')}</ul></article>`).join('');
    const buckets = new Map([...unownedTables.keys()].map(group => [group, []]));
    for (const row of missing) {
      const group = unitGroup(row.role);
      buckets.get(group === 'timers' ? 'services' : group).push(row);
    }
    for (const [group, list] of unownedTables) list.setRows(buckets.get(group));
  }

  function showArchitecture() {
    const arch = state.arch;
    if (!arch) {
      text('#arch-summary', '架构数据取数失败。可刷新重试。');
      return;
    }
    const active = arch.counts || {};
    text('#arch-summary', `${arch.nodes.length} 个节点 · ${arch.edges.length} 条连线 · ${active['隧道'] ?? noData} 条在用隧道 · ${stamp(arch.generated_epoch)}`);
    const inventoryLink = $('#graph-inventory');
    graph?.dispose();
    graph = mountArchitecture(arch, node => {
      requestedNode = node?.id || null;
      inventoryLink.href = nodeUrl('/check.html', requestedNode, '#h-list');
      inventoryLink.textContent = node ? '在清单中查看所选节点' : '查看运行单元清单';
    });
    if (requestedNode) graph.focus(requestedNode, true);
  }

  function showFlows() {
    const {arch, snapshot, history} = state;
    const routes = D.routes(arch || {}, snapshot || {});
    routeTable?.setRows(routes);
    const bad = routes.filter(row => row.state === '断点').length;
    const gaps = routes.filter(row => row.state === '路由缺证').length;
    text('#route-summary', arch || snapshot ? `${routes.length} 个对外域名，${bad} 条有断点，${gaps} 条路由缺证。探测失败只标端点。`
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
      {group: '运行采集', from: 'GitHub 采集器', file: 'github_public.json', url: '/data/github_public.json', section: 'GitHub、提交活动、待处理事项中的 CI',
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
    billingTable.setRows(OverviewInventory.billing(snapshot));
    text('#billing-summary', cost ? `按价格库计算本月应扣 ${aud(cost.month_cash_aud)} · 月摊 ${aud(cost.monthly_aud)} · 年化 ${aud(cost.yearly_aud)}。`
      + (num(snapshot.fx?.aud_cny) === null ? '' : ` 1 澳元折合 ${fmt(snapshot.fx.aud_cny, ' 元', 4)}。`)
      : '账单快照无数据。');
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
    const signals = (capacity?.signals || []).map(signal => ({
      ...signal, status: levels[signal.level] || signal.level, tone: tones[signal.level], rule: brief(signal.rule), read: brief(signal.read),
    }));
    if (capacity?.headroom) {
      const room = capacity.headroom;
      const tier = capacity.current_tier || {};
      signals.push(
        {dim: '主机配置', status: '实测', fact: `${fmt(tier.cores, ' 核')} · ${fmt(tier.ram_gb, ' GiB 内存', 1)} · ${fmt(tier.disk_gb, ' GiB 磁盘', 1)}`},
        {dim: '部署余量', status: '估算', fact: `内存可容 ${fmt(room.by_mem, ' 个')} · 磁盘可容 ${fmt(room.by_disk, ' 个')}`,
          rule: '以内存和磁盘可容数量中的较小值为准。', read: brief(room.note)},
        {dim: '磁盘回收', status: '估算', fact: `当前空闲 ${gb(room.free_b)} · 可回收 ${gb(room.reclaimable_b)}`,
          read: brief(capacity.reclaim_hint)},
      );
    }
    capacityTable.setRows(signals);

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

  start(home ? sourceKeys.flows : sourceKeys[detailView], ({snapshot, history, arch}, fresh) => {
    state.snapshot = snapshot;
    state.history = history;
    state.arch = arch;
    state.snapshotAlert = !fresh.has('snapshot') && snapshot ? ' · 刷新失败，保留上次已读快照' : '';
    state.historyEmbedded = !fresh.has('history') && Boolean(snapshot?.history);
    if (state.historyEmbedded) state.history = snapshot.history;
    const historyEpochs = ['min', 'hour', 'day'].map(tier => num(state.history?.[tier]?.t?.at(-1)) ?? 0);
    state.epoch = Math.max(num(snapshot?.updated_epoch) ?? 0, ...historyEpochs) || null;
    if (home) {
      showCurrent();
      showHistory();
      renderInventory(arch, snapshot);
    }
    if (detailView === 'history') {
      showHistory();
      archiveTable.setRows(state.history && state.epoch ? D.series(state.history, 'day', state.epoch).reverse() : []);
    }
    if (detailView === 'arch' && (fresh.has('arch') || !graph)) showArchitecture();
    if (detailView === 'org') showOrg();
    if (detailView === 'flows') showFlows();
    if (detailView === 'cost') showCosts();
  });
})();
