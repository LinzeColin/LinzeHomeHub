// 将本文件粘贴到 iPhone 的 Scriptable。锁屏选矩形或圆形，主屏选中号。
const endpoint = 'https://status.linzezhang.com/data/lock.json';
const lockPage = 'https://status.linzezhang.com/lock.html';
const timeKey = 'linze-status-lock-last-epoch';
const family = config.runsInWidget ? config.widgetFamily : 'medium';
const accessory = family === 'accessoryRectangular' || family === 'accessoryCircular';
const widget = new ListWidget();
widget.url = lockPage;
widget.setPadding(...(accessory ? [0, 1, 0, 1] : [10, 12, 10, 12]));
widget.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);

const dark = Device.isUsingDarkAppearance();
const ink = accessory ? Color.white() : dark ? new Color('#eaf2ff') : new Color('#172a49');
const muted = accessory ? Color.white() : dark ? new Color('#aabdd4') : new Color('#465a77');
const blue = accessory ? Color.white() : dark ? new Color('#8ebdff') : new Color('#2155a6');
const green = accessory ? Color.white() : dark ? new Color('#70dbbd') : new Color('#09746e');
if (!accessory) widget.backgroundColor = dark ? new Color('#14233b') : new Color('#f2f6fd');

function reading(value, unit = '', digits = 0) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) + unit : '—';
}

function addText(parent, value, size, color = ink, bold = false) {
  const text = parent.addText(value);
  text.font = bold ? Font.boldSystemFont(size) : Font.systemFont(size);
  text.textColor = color;
  text.lineLimit = 1;
  text.minimumScaleFactor = 0.63;
  return text;
}

function time(epoch, date = true) {
  return epoch ? new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Australia/Sydney', ...(date ? {month: '2-digit', day: '2-digit'} : {}),
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(new Date(epoch * 1000)) : '无记录';
}

async function getData() {
  const request = new Request(endpoint);
  request.timeoutInterval = 8;
  const data = await request.loadJSON();
  if (request.response.statusCode !== 200 || !Number.isSafeInteger(data.updated_epoch) ||
      data.updated_epoch <= 0 || !Array.isArray(data.days) || data.days.length !== 7) {
    throw new Error('无有效快照');
  }
  return data;
}

function spark(values, width, height, tint) {
  const context = new DrawContext();
  context.size = new Size(width, height);
  context.opaque = false;
  context.respectScreenScale = true;
  const line = new Path();
  const points = [];
  let connected = false;
  values.forEach((value, i) => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 100) {
      connected = false;
      return;
    }
    const p = new Point(3 + i * (width - 6) / 6, height - 3 - value * (height - 6) / 100);
    if (connected) line.addLine(p);
    else line.move(p);
    points.push(p);
    connected = true;
  });
  if (!points.length) return null;
  context.addPath(line);
  context.setStrokeColor(tint);
  context.setLineWidth(1.6);
  context.strokePath();
  context.setFillColor(tint);
  for (const point of points) context.fillEllipse(new Rect(point.x - 1.5, point.y - 1.5, 3, 3));
  return context.getImage();
}

function addSpark(parent, values, size, tint) {
  const plot = spark(values, size.width, size.height, tint);
  if (plot) {
    const view = parent.addImage(plot);
    view.imageSize = size;
  } else {
    addText(parent, '7 天无数据', 8, muted);
  }
}

function circular(data) {
  const size = 58;
  const context = new DrawContext();
  context.size = new Size(size, size);
  context.opaque = false;
  context.respectScreenScale = true;
  context.setLineWidth(3.5);
  context.setStrokeColor(new Color('#ffffff', 0.32));
  context.strokeEllipse(new Rect(4, 4, size - 8, size - 8));
  const pct = data.disk_pct;
  if (typeof pct === 'number' && pct >= 0 && pct <= 100) {
    const progress = new Path();
    for (let i = 0; i <= pct; i += 1) {
      const theta = (i / 100 - 0.25) * 2 * Math.PI;
      const p = new Point(size / 2 + 25 * Math.cos(theta), size / 2 + 25 * Math.sin(theta));
      if (i === 0) progress.move(p);
      else progress.addLine(p);
    }
    context.addPath(progress);
    context.setStrokeColor(ink);
    context.strokePath();
  }
  context.setTextColor(ink);
  context.setTextAlignedCenter();
  context.setFont(Font.boldSystemFont(16));
  context.drawTextInRect(reading(pct, '%'), new Rect(4, 16, size - 8, 20));
  context.setFont(Font.systemFont(9));
  context.drawTextInRect('磁盘', new Rect(4, 35, size - 8, 12));
  const image = widget.addImage(context.getImage());
  image.imageSize = new Size(size, size);
  image.centerAlignImage();
}

function rectangular(data) {
  const row = widget.addStack();
  for (const [name, current, history, tint] of [
    ['内存', data.mem_pct, 'mem_peak_pct', green], ['磁盘', data.disk_pct, 'disk_pct', blue],
  ]) {
    const column = row.addStack();
    column.layoutVertically();
    addText(column, name + ' ' + reading(current, '%'), 13, ink, true);
    addSpark(column, data.days.map(day => day[history]), new Size(64, 20), tint);
    row.addSpacer(6);
  }
  addText(widget, '7 天 · 悉尼 ' + time(data.updated_epoch, false), 9, muted);
}

function medium(data) {
  addText(widget, '服务器快照', 15, ink, true);
  const values = widget.addStack();
  for (const [name, value] of [
    ['内存', reading(data.mem_pct, '%')], ['磁盘', reading(data.disk_pct, '%')],
    ['1 分钟负载', reading(data.load1, '', 2)], ['在跑容器', reading(data.containers_running)],
  ]) {
    const tile = values.addStack();
    tile.layoutVertically();
    addText(tile, name, 11, muted);
    addText(tile, value, 21, ink, true);
    values.addSpacer();
  }
  widget.addSpacer(4);
  const plots = widget.addStack();
  for (const [title, key, tint] of [
    ['内存峰值', 'mem_peak_pct', green], ['磁盘已用', 'disk_pct', blue],
  ]) {
    const column = plots.addStack();
    column.layoutVertically();
    addText(column, title + ' · 7 天', 9, muted);
    addSpark(column, data.days.map(day => day[key]), new Size(110, 24), tint);
    plots.addSpacer();
  }
  widget.addSpacer(2);
  addText(widget, '悉尼 ' + time(data.updated_epoch) + ' · ' + reading(data.pending_count, ' 项待处理'), 10, muted);
}

let last = null;
try { if (Keychain.contains(timeKey)) last = Number(Keychain.get(timeKey)) || null; } catch {}
let data;
try { data = await getData(); } catch {}
if (data) {
  last = data.updated_epoch;
  try { Keychain.set(timeKey, String(last)); } catch {}
}
if (!data || Date.now() / 1000 - last > 180) {
  widget.addSpacer();
  const failure = addText(widget, data ? '数据停更' : '取数失败', accessory ? 12 : 18, ink, true);
  if (family === 'accessoryCircular') failure.centerAlignText();
  const lastTime = addText(widget, '上次 ' + time(last, family !== 'accessoryCircular'), accessory ? 9 : 13, muted);
  if (family === 'accessoryCircular') lastTime.centerAlignText();
  widget.addSpacer();
} else if (family === 'accessoryCircular') {
  circular(data);
} else if (family === 'accessoryRectangular') {
  rectangular(data);
} else {
  medium(data);
}

if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
