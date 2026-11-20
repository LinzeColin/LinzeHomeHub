(() => {
  const $ = selector => document.querySelector(selector);
  const time = $('#lock-time'), retry = $('#lock-retry'), tooltip = $('#lock-tip');
  const lastKey = 'status-lock-last-epoch';
  const outputs = ['#lock-mem', '#lock-disk', '#lock-load', '#lock-containers'];
  const charts = new Map();
  const validNumber = value => typeof value === 'number' && Number.isFinite(value);
  const ageLimit = 180;
  const format = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Australia/Sydney', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short',
  });
  const displayTime = epoch => validNumber(epoch) && epoch > 0 ? format.format(new Date(epoch * 1000)) : '无记录';
  let last = null;
  try { last = Number(localStorage.getItem(lastKey)) || null; } catch {}
  function metrics(values) {
    outputs.forEach((selector, i) => {
      const value = values[i], el = $(selector);
      el.textContent = validNumber(value) ? value.toFixed(i === 2 ? 2 : 0) + (i < 2 ? '%' : '') : '无数据';
      if (i === 0) el.closest('.meter').dataset.tone = value >= 90 ? 'warn' : '';
      if (i === 1) el.closest('.meter').dataset.tone = value >= 85 ? 'bad' : value >= 75 ? 'warn' : '';
    });
  }

  function plot(days, key, id, title, color) {
    charts.get(id)?.destroy();
    charts.delete(id);
    const rows = days.map(row => validNumber(row[key]) ? row[key] : null);
    const canvas = $(`#lock-${id}-chart`);
    const note = $(`#lock-${id}-note`);
    if (!rows.some(validNumber)) {
      note.textContent = '无数据';
      return;
    }
    note.textContent = `${days[0].date.slice(5)} – ${days.at(-1).date.slice(5)} · %`;
    if (!window.Chart) {
      note.textContent += ' · ' + rows.map(value => value === null ? '无数据' : value + '%').join(' / ');
      return;
    }
    canvas.addEventListener('pointerleave', () => { tooltip.hidden = true; }, {once: true});
    charts.set(id, new Chart(canvas, {
      type: 'line',
      data: {labels: days.map(row => row.date), datasets: [{
        data: rows, borderColor: color, backgroundColor: color, borderWidth: 2,
        pointRadius: 2, pointHitRadius: 15, spanGaps: false,
      }]},
      options: {animation: false, maintainAspectRatio: false, devicePixelRatio: Math.min(2, devicePixelRatio),
        interaction: {mode: 'index', axis: 'x', intersect: false},
        scales: {x: {display: false}, y: {min: 0, max: 100, ticks: {display: false, stepSize: 25}, border: {display: false}}},
        plugins: {legend: {display: false}, tooltip: {enabled: false, external: ({chart, tooltip: model}) => {
          const point = model.dataPoints?.[0];
          if (!model.opacity || !point) { tooltip.hidden = true; return; }
          tooltip.textContent = `${point.label} 悉尼 · ${title} ${point.raw}% · lock.json`;
          tooltip.hidden = false;
          const origin = chart.canvas.getBoundingClientRect();
          tooltip.style.left = Math.max(6, Math.min(innerWidth - tooltip.offsetWidth - 6, origin.left)) + 'px';
          tooltip.style.top = Math.max(6, origin.top - tooltip.offsetHeight - 4) + 'px';
        }}},
      },
    }));
  }

  async function refresh() {
    retry.disabled = true;
    const controller = new AbortController();
    const limit = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch('/data/lock.json', {cache: 'no-store', signal: controller.signal});
      if (!response.ok) throw new Error('锁屏数据不可用');
      const data = await response.json();
      if (!Number.isSafeInteger(data.updated_epoch) || data.updated_epoch <= 0 ||
          !Array.isArray(data.days) || data.days.length !== 7 ||
          data.days.some(row => !/^\d{4}-\d{2}-\d{2}$/.test(row?.date))) throw new Error('锁屏数据格式错误');
      last = data.updated_epoch;
      try { localStorage.setItem(lastKey, String(last)); } catch {}
      const age = Date.now() / 1000 - last;
      time.textContent = `${displayTime(last)} · 悉尼${age > ageLimit ? ' · 采集已超过 3 分钟' : ''}`;
      const standard = $('#site-standard');
      standard.textContent = JSON.stringify({...JSON.parse(standard.textContent),
        data_updated_at: new Date(last * 1000).toISOString()});
      metrics([data.mem_pct, data.disk_pct, data.load1, data.containers_running]);
      plot(data.days, 'mem_peak_pct', 'mem', '内存峰值', '#09746e');
      plot(data.days, 'disk_pct', 'disk', '磁盘已用', '#2155a6');
      retry.hidden = false;
      retry.textContent = '刷新';
    } catch {
      time.textContent = '取数失败 · 上次 ' + displayTime(last);
      metrics([]);
      plot([], 'mem_peak_pct', 'mem', '内存峰值', '#09746e');
      plot([], 'disk_pct', 'disk', '磁盘已用', '#2155a6');
      retry.hidden = false;
      retry.textContent = '重试';
    } finally {
      clearTimeout(limit);
      retry.disabled = false;
      document.body.dataset.loaded = 'true';
    }
  }
  retry.addEventListener('click', refresh);
  refresh();
})();
