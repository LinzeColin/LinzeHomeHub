/**
 * 全仓全项目关系图(Project Atlas)
 *
 * 数据来自 status 站每分钟 cron 派生的 graph.json —— 服务器纯计算,
 * **不调用任何模型、不消耗任何 token、不依赖任何 agent**。本模块只负责把它画出来。
 *
 * 动态性:
 *  - 每 60 秒重新拉取,力导向布局连续演化;
 *  - 与上一份对比,新增节点绿色脉冲、变更节点琥珀脉冲、消失节点红色淡出;
 *  - 「演示」按钮按层次巡览(供应商 -> 项目 -> 仓 -> 子项目),逐层点亮依赖。
 *
 * 零新增依赖:纯 Canvas 2D + requestAnimationFrame。
 */

const DEFAULT_SRC = 'https://status.linzezhang.com/data/graph.json';
const REFRESH_MS = 60_000;

type Kind = 'vendor' | 'project' | 'store' | 'repo' | 'repo_private' | 'subproject' | string;

interface RawNode {
  id: string; label: string; kind: Kind;
  status?: string; url?: string; agent?: string; deploy?: string;
  lang?: string; commits30?: number; path?: string; count?: number;
}
interface RawEdge { s: string; t: string; rel: string }
interface Graph {
  generated_at: string;
  nodes: RawNode[]; edges: RawEdge[];
  counts: { nodes: number; edges: number; by_kind: Record<string, number> };
  changes: { t: number; op: string; id: string; label: string; kind: string }[];
  provenance?: string;
}

interface Vec { x: number; y: number; vx: number; vy: number }
interface Node extends RawNode, Vec { r: number; flash: number; flashKind: string; dying: number }

const STYLE: Record<string, { c: string; r: number; ring: string }> = {
  vendor:       { c: '#7cc7ff', r: 26, ring: 'rgba(124,199,255,.30)' },
  project:      { c: '#8affc1', r: 18, ring: 'rgba(138,255,193,.26)' },
  store:        { c: '#ffd479', r: 14, ring: 'rgba(255,212,121,.24)' },
  repo:         { c: '#c9a6ff', r: 16, ring: 'rgba(201,166,255,.24)' },
  repo_private: { c: '#8c8ca8', r: 14, ring: 'rgba(140,140,168,.22)' },
  subproject:   { c: '#ff9fb2', r: 11, ring: 'rgba(255,159,178,.22)' },
};
const KIND_LABEL: Record<string, string> = {
  vendor: '供应商', project: '项目', store: '数据存储',
  repo: '代码仓', repo_private: '私有仓', subproject: '子项目',
};
const TOUR: Kind[] = ['vendor', 'project', 'store', 'repo', 'subproject'];

const styleOf = (k: string) => STYLE[k] ?? { c: '#9aa', r: 12, ring: 'rgba(150,150,170,.2)' };

export function initProjectAtlas(root: HTMLElement): void {
  const canvas = root.querySelector<HTMLCanvasElement>('#atlasCanvas');
  const legend = root.querySelector<HTMLElement>('#atlasLegend');
  const meta = root.querySelector<HTMLElement>('#atlasMeta');
  const feed = root.querySelector<HTMLElement>('#atlasFeed');
  const tourBtn = root.querySelector<HTMLButtonElement>('#atlasTour');
  if (!canvas || !legend || !meta || !feed) return;
  const ctx2d = canvas.getContext('2d');
  if (!ctx2d) return;
  // 下面的函数声明会被提升,TS 会丢掉上面的非空收窄 -> 这里固定成非空常量
  const ctx: CanvasRenderingContext2D = ctx2d;
  const elMeta: HTMLElement = meta;
  const elLegend: HTMLElement = legend;
  const elFeed: HTMLElement = feed;

  // 数据源可由 data-atlas-src 覆盖(本地预览 / 自托管时用),默认指向 status 站
  const SRC = root.dataset.atlasSrc || DEFAULT_SRC;

  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let nodes: Node[] = [];
  let edges: RawEdge[] = [];
  let byId = new Map<string, Node>();
  let hover: Node | null = null;
  let tourIdx = -1;
  let tourUntil = 0;
  let w = 0, h = 0, dpr = 1;

  const resize = () => {
    dpr = Math.min(devicePixelRatio || 1, 2);
    const cw = canvas.clientWidth, chh = canvas.clientHeight;
    if (cw < 2 || chh < 2) return;          // 布局尚未完成时不要把画布设成 0
    const first = w === 0;
    w = cw; h = chh;
    canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (first) reseed();                    // 拿到真实尺寸后把节点重新撒到画布中心
  };
  /** 尺寸从 0 变为可用时,把还堆在原点的节点重新布种 */
  function reseed() {
    const cx = w / 2, cy = h / 2;
    nodes.forEach((n, i) => {
      const ang = (i / Math.max(1, nodes.length)) * Math.PI * 2;
      n.x = cx + Math.cos(ang) * (60 + (i % 5) * 26);
      n.y = cy + Math.sin(ang) * (50 + (i % 4) * 22);
      n.vx = 0; n.vy = 0;
    });
  }
  resize();
  addEventListener('resize', resize);
  // 区块在折叠下方 / reveal 动画中时,首帧宽度可能是 0 —— 用 ResizeObserver 兜住
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resize).observe(canvas);

  /** 用上一份布局做种子,让刷新后节点不跳位 */
  function ingest(g: Graph) {
    const prev = byId;
    const next: Node[] = [];
    const cx = w / 2, cy = h / 2;
    g.nodes.forEach((rn, i) => {
      const old = prev.get(rn.id);
      const st = styleOf(rn.kind);
      const ang = (i / Math.max(1, g.nodes.length)) * Math.PI * 2;
      const n: Node = {
        ...rn,
        x: old?.x ?? cx + Math.cos(ang) * (60 + (i % 5) * 26),
        y: old?.y ?? cy + Math.sin(ang) * (50 + (i % 4) * 22),
        vx: old?.vx ?? 0, vy: old?.vy ?? 0,
        r: st.r, flash: 0, flashKind: '', dying: 0,
      };
      const ch = (rn as RawNode & { change?: string }).change;
      if (!old) { n.flash = 1; n.flashKind = 'added'; }
      else if (ch === 'changed') { n.flash = 1; n.flashKind = 'changed'; }
      next.push(n);
    });
    // 消失的节点保留一小会儿做淡出
    for (const [id, old] of prev) {
      if (!g.nodes.some((x) => x.id === id) && old.dying < 1) {
        old.dying = 0.001; old.flashKind = 'removed'; next.push(old);
      }
    }
    nodes = next;
    byId = new Map(nodes.map((n) => [n.id, n]));
    edges = g.edges;

    elMeta.textContent =
      `${g.counts.nodes} 个节点 · ${g.counts.edges} 条依赖 · 更新于 ${g.generated_at} · 每分钟自动同步`;
    elLegend.innerHTML = TOUR.concat('repo_private')
      .filter((k) => g.counts.by_kind[k])
      .map((k) => `<span class="atlas-key" data-kind="${k}"><i style="background:${styleOf(k).c}"></i>${KIND_LABEL[k] ?? k} ${g.counts.by_kind[k]}</span>`)
      .join('');
    const ops: Record<string, string> = { added: '新增', changed: '变更', removed: '移除' };
    elFeed.innerHTML = (g.changes || []).slice(0, 6)
      .map((c) => `<li data-op="${c.op}"><b>${ops[c.op] ?? c.op}</b> ${escapeHtml(c.label)} <span>${KIND_LABEL[c.kind] ?? c.kind}</span></li>`)
      .join('') || '<li class="quiet">暂无结构变更</li>';
  }

  const escapeHtml = (s: string) =>
    s.replace(/[<>&"]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c] as string));

  function step() {
    const cx = w / 2, cy = h / 2;
    // 斥力
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const f = Math.min(2600 / d2, 2.4);
        const d = Math.sqrt(d2);
        a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
      }
      // 向心 + 按层次的环形约束(供应商在内圈,子项目在外圈)
      const ring = { vendor: 0, project: 110, store: 170, repo: 200, repo_private: 200, subproject: 265 }[a.kind] ?? 180;
      const dx = a.x - cx, dy = a.y - cy;
      const dist = Math.hypot(dx, dy) || 1;
      const pull = (dist - ring) * 0.006;
      a.vx -= (dx / dist) * pull * dist * 0.02 + dx * 0.0006;
      a.vy -= (dy / dist) * pull * dist * 0.02 + dy * 0.0006;
    }
    // 引力(连线)
    for (const e of edges) {
      const a = byId.get(e.s), b = byId.get(e.t);
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 1;
      const f = (d - 96) * 0.0016;
      a.vx += (dx / d) * f * d * 0.05; a.vy += (dy / d) * f * d * 0.05;
      b.vx -= (dx / d) * f * d * 0.05; b.vy -= (dy / d) * f * d * 0.05;
    }
    const pad = 26;
    for (const n of nodes) {
      n.vx *= 0.84; n.vy *= 0.84;
      n.x += Math.max(-6, Math.min(6, n.vx));
      n.y += Math.max(-6, Math.min(6, n.vy));
      n.x = Math.max(pad, Math.min(w - pad, n.x));
      n.y = Math.max(pad, Math.min(h - pad, n.y));
      if (n.flash > 0) n.flash = Math.max(0, n.flash - 0.012);
      if (n.dying > 0) n.dying = Math.min(1, n.dying + 0.02);
    }
    nodes = nodes.filter((n) => n.dying < 1);
    byId = new Map(nodes.map((n) => [n.id, n]));
  }

  function draw(t: number) {
    ctx.clearRect(0, 0, w, h);
    const tourKind = tourIdx >= 0 && t < tourUntil ? TOUR[tourIdx] : null;
    const focus = hover;
    const lit = new Set<string>();
    if (focus) {
      lit.add(focus.id);
      for (const e of edges) {
        if (e.s === focus.id) lit.add(e.t);
        if (e.t === focus.id) lit.add(e.s);
      }
    }

    for (const e of edges) {
      const a = byId.get(e.s), b = byId.get(e.t);
      if (!a || !b) continue;
      const on = focus ? (e.s === focus.id || e.t === focus.id)
        : tourKind ? (a.kind === tourKind || b.kind === tourKind) : false;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 - 14;
      ctx.quadraticCurveTo(mx, my, b.x, b.y);
      ctx.strokeStyle = on ? 'rgba(160,220,255,.75)' : 'rgba(140,160,200,.14)';
      ctx.lineWidth = on ? 1.7 : 0.8;
      ctx.stroke();
      if (on && !reduced) {                       // 依赖流向的流动光点
        const p = ((t / 1400) % 1);
        const px = (1 - p) * (1 - p) * a.x + 2 * (1 - p) * p * mx + p * p * b.x;
        const py = (1 - p) * (1 - p) * a.y + 2 * (1 - p) * p * my + p * p * b.y;
        ctx.beginPath(); ctx.arc(px, py, 2.2, 0, 7); ctx.fillStyle = '#bfe6ff'; ctx.fill();
      }
    }

    for (const n of nodes) {
      const st = styleOf(n.kind);
      const dim = (focus && !lit.has(n.id)) || (tourKind && n.kind !== tourKind);
      const alpha = n.dying > 0 ? 1 - n.dying : dim ? 0.28 : 1;
      const pulse = n.flash > 0 && !reduced ? 1 + Math.sin(t / 110) * 0.16 * n.flash : 1;
      const rr = n.r * pulse;
      ctx.globalAlpha = alpha;
      if (n.flash > 0 || n.dying > 0) {
        ctx.beginPath(); ctx.arc(n.x, n.y, rr + 9, 0, 7);
        ctx.fillStyle = n.flashKind === 'removed' ? 'rgba(255,120,120,.22)'
          : n.flashKind === 'changed' ? 'rgba(255,200,110,.22)' : 'rgba(130,255,190,.22)';
        ctx.fill();
      }
      ctx.beginPath(); ctx.arc(n.x, n.y, rr + 5, 0, 7); ctx.fillStyle = st.ring; ctx.fill();
      ctx.beginPath(); ctx.arc(n.x, n.y, rr, 0, 7); ctx.fillStyle = st.c; ctx.fill();
      if (n.kind === 'project' && n.status === 'down') {
        ctx.beginPath(); ctx.arc(n.x, n.y, rr + 8, 0, 7);
        ctx.strokeStyle = '#ff6b6b'; ctx.lineWidth = 2; ctx.stroke();
      }
      ctx.globalAlpha = alpha * (dim ? 0.5 : 1);
      ctx.fillStyle = '#0a0f1c';
      ctx.font = `600 ${n.kind === 'vendor' ? 11 : 10}px -apple-system,"PingFang SC",sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      // 文字放不进圆里就挪到圆下方,避免被裁成「odexPro」这种残字
      const short = n.label.length > 14 ? n.label.slice(0, 13) + '…' : n.label;
      const fits = ctx.measureText(short).width <= rr * 1.75;
      if (fits && rr > 12) {
        ctx.fillText(short, n.x, n.y);
      } else {
        ctx.fillStyle = 'rgba(226,238,255,.9)';
        ctx.strokeStyle = 'rgba(6,12,26,.85)';
        ctx.lineWidth = 3;
        ctx.strokeText(short, n.x, n.y + rr + 10);   // 描边保证压在连线上也看得清
        ctx.fillText(short, n.x, n.y + rr + 10);
      }
      ctx.globalAlpha = 1;
    }

    if (focus) {
      const lines = [
        `${focus.label}  ·  ${KIND_LABEL[focus.kind] ?? focus.kind}`,
        focus.kind === 'project'
          ? `部署 ${focus.deploy || '—'} · Agent 依赖 ${focus.agent || '—'}`
          : focus.kind === 'repo' ? `${focus.lang || '—'} · 30 天 ${focus.commits30 ?? 0} 次提交`
          : focus.kind === 'subproject' ? `${focus.path || ''} · 30 天 ${focus.commits30 ?? 0} 次提交`
          : '',
      ].filter(Boolean);
      const bw = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 22;
      const bx = Math.min(w - bw - 8, focus.x + 16), by = Math.min(h - 46, focus.y + 14);
      ctx.fillStyle = 'rgba(6,12,26,.92)';
      ctx.strokeStyle = 'rgba(150,190,255,.3)';
      ctx.beginPath(); ctx.roundRect(bx, by, bw, 18 * lines.length + 12, 8); ctx.fill(); ctx.stroke();
      ctx.textAlign = 'left'; ctx.fillStyle = '#dbe8ff';
      lines.forEach((l, i) => {
        ctx.font = i === 0 ? '600 12px -apple-system,"PingFang SC",sans-serif' : '11px -apple-system,"PingFang SC",sans-serif';
        ctx.fillStyle = i === 0 ? '#eaf2ff' : 'rgba(200,215,240,.8)';
        ctx.fillText(l, bx + 11, by + 16 + i * 17);
      });
    }
  }

  canvas.addEventListener('pointermove', (ev) => {
    const r = canvas.getBoundingClientRect();
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    hover = nodes.find((n) => Math.hypot(n.x - mx, n.y - my) <= n.r + 6) ?? null;
    canvas.style.cursor = hover?.url ? 'pointer' : hover ? 'default' : '';
  });
  canvas.addEventListener('pointerleave', () => { hover = null; });
  canvas.addEventListener('click', () => {
    if (hover?.url) window.open(hover.url, '_blank', 'noopener,noreferrer');
  });
  tourBtn?.addEventListener('click', () => {
    tourIdx = 0; tourUntil = performance.now() + 2000;
    const advance = () => {
      tourIdx += 1;
      if (tourIdx >= TOUR.length) { tourIdx = -1; return; }
      tourUntil = performance.now() + 2000;
      setTimeout(advance, 2000);
    };
    setTimeout(advance, 2000);
  });

  let last = 0;
  const loop = (t: number) => {
    if (w < 2 || h < 2) resize();
    if (w >= 2 && t - last > (reduced ? 200 : 16)) { step(); draw(t); last = t; }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);

  const pull = async (): Promise<void> => {
    try {
      const res = await fetch(`${SRC}?t=${Date.now()}`, { mode: 'cors', cache: 'no-store' });
      if (!res.ok) throw new Error(String(res.status));
      ingest((await res.json()) as Graph);
      root.dataset.atlasState = 'live';
    } catch {
      root.dataset.atlasState = 'offline';
      if (!nodes.length) elMeta.textContent = '关系图数据暂时取不到,稍后自动重试。';
    }
  };
  // 首帧不能只靠 rAF:页面处于后台/隐藏时 rAF 不触发,会留下一张空画布。
  // 拿到数据后先同步跑几步布局并画一帧,等页面可见时再由 rAF 接管。
  function primeFrame() {
    resize();
    if (w < 2 || !nodes.length) return;
    for (let i = 0; i < 90; i++) step();
    draw(performance.now());
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) primeFrame(); });

  void pull().then(primeFrame);
  setInterval(pull, REFRESH_MS);
}
