/* Layered architecture with subgraph selection and role filters. */
function mountArchitecture(arch, onSelect) {
  const {esc, fmt} = OverviewData;
  const graph = document.querySelector('#g');
  const viewport = document.querySelector('#gw');
  const chips = document.querySelector('#chips');
  const selection = document.querySelector('#graph-selection');
  const motion = document.querySelector('#motion');
  const tip = document.querySelector('#tip');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const colors = {
    domain: '#2155a6', tunnel: '#714ba3', proxy: '#ad2947', app: '#167651',
    static: '#167651', auth: '#8c5a19', db: '#146369', cache: '#146369',
    mail: '#a03668', monitor: '#436487', service: '#52647c', external: '#714ba3',
    manager: '#52647c', timer: '#52647c',
  };
  const layerNames = ['域名', '对外通道', '应用', '系统服务', '定时任务', '底座', '外部'];
  const nodes = new Map(arch.nodes.map(node => [node.id, node]));
  const incoming = new Map(), outgoing = new Map();
  for (const edge of arch.edges) {
    if (!incoming.has(edge.to)) incoming.set(edge.to, []);
    if (!outgoing.has(edge.from)) outgoing.set(edge.from, []);
    incoming.get(edge.to).push(edge);
    outgoing.get(edge.from).push(edge);
  }
  const hidden = new Set();
  let focused = null;
  let visible = false;
  let paused = Boolean(navigator.connection?.saveData || (navigator.deviceMemory && navigator.deviceMemory <= 4));
  let xy = new Map();

  const color = role => colors[role] || colors.service;
  const isHub = node => ['tunnel', 'proxy'].includes(node?.role);
  const isOutside = node => ['domain', 'external'].includes(node?.role);
  const isOn = node => node?.state === '在跑';
  function layerOf(node) {
    if (node.role === 'domain') return 0;
    if (isHub(node)) return 1;
    if (node.role === 'service') return 3;
    if (node.role === 'timer') return 4;
    if (node.role === 'external') return 6;
    return ['db', 'cache', 'auth', 'mail', 'monitor', 'manager'].includes(node.role) ? 5 : 2;
  }

  const observer = new IntersectionObserver(entries => {
    visible = entries[0].isIntersecting;
    updateMotion();
  });
  observer.observe(viewport);
  function updateMotion() {
    viewport.dataset.playing = visible && !paused && !document.hidden && !reduced.matches && Boolean(focused);
    motion.textContent = reduced.matches ? '系统已减弱动效' : paused ? '播放连线动效' : '暂停连线动效';
    motion.disabled = reduced.matches;
    motion.setAttribute('aria-pressed', String(paused || reduced.matches));
  }
  const changeMotion = () => { paused = !paused; updateMotion(); };
  const onScroll = () => { tip.hidden = true; };
  motion.addEventListener('click', changeMotion);
  document.addEventListener('visibilitychange', updateMotion);
  reduced.addEventListener('change', updateMotion);
  addEventListener('scroll', onScroll, {passive: true});
  viewport.addEventListener('scroll', onScroll, {passive: true});

  const roles = new Map(arch.nodes.map(node => [node.role, node.role_cn || node.role]));
  chips.innerHTML = [...roles].map(([role, label]) =>
    `<button type="button" class="chip" data-role="${esc(role)}" aria-pressed="true"><i style="background:${color(role)}" aria-hidden="true"></i>${esc(label)}</button>`).join('');
  chips.onclick = event => {
    const button = event.target.closest('button[data-role]');
    if (!button) return;
    const {role} = button.dataset;
    if (hidden.has(role)) hidden.delete(role);
    else hidden.add(role);
    button.setAttribute('aria-pressed', String(!hidden.has(role)));
    if (hidden.has(nodes.get(focused)?.role)) {
      focused = null;
      onSelect(null);
    }
    draw();
  };

  const width = 252, gap = 64, height = 52, rowGap = 10, top = 50, pad = 14;
  const measure = document.createElement('canvas').getContext('2d');
  measure.font = `16px ${getComputedStyle(graph).fontFamily}`;
  const labelWithinCard = value => {
    if (measure.measureText(value).width <= width - 24) return value;
    let part = '';
    for (const char of value) {
      if (measure.measureText(part + char + '…').width > width - 24) break;
      part += char;
    }
    return part + '…';
  };
  const short = (value, limit) => {
    let weight = 0;
    for (const [index, char] of [...String(value)].entries()) {
      weight += /[^\x00-\x7F]/.test(char) ? 2 : 1;
      if (weight > limit) return [...String(value)].slice(0, index).join('') + '…';
    }
    return value;
  };

  function draw() {
    tip.hidden = true;
    const shown = [...nodes.values()].filter(node => !hidden.has(node.role));
    const cols = layerNames.map(() => []);
    for (const node of shown) cols[layerOf(node)].push(node);
    const layers = cols.map((column, i) => ({column, i})).filter(({column}) => column.length);
    const shownIds = new Set(shown.map(n => n.id));
    const edges = arch.edges.filter(e => shownIds.has(e.from) && shownIds.has(e.to));
    const place = () => {
      xy = new Map();
      layers.forEach(({column}, k) => column.forEach((node, j) => xy.set(node.id, {
        x: pad + k * (width + gap), y: top + j * (height + rowGap),
      })));
    };
    place();
    for (let pass = 0; pass < 2; pass += 1) {
      layers.forEach(({column}, k) => {
        if (!k) return;
        const scores = new Map(column.map(node => {
          const parents = (incoming.get(node.id) || []).map(e => xy.get(e.from)).filter(Boolean);
          const prior = parents.filter(parent => parent.x < xy.get(node.id).x);
          return [node.id, prior.length ? prior.reduce((sum, item) => sum + item.y, 0) / prior.length : Infinity];
        }));
        column.sort((a, b) => scores.get(a.id) - scores.get(b.id));
      });
      place();
    }
    const svgWidth = layers.length ? 2 * pad + layers.length * width + (layers.length - 1) * gap : 270;
    const svgHeight = top + Math.max(1, ...cols.map(column => column.length)) * (height + rowGap) + pad;
    graph.setAttribute('width', svgWidth);
    graph.setAttribute('height', svgHeight);
    graph.setAttribute('viewBox', `0 0 ${svgWidth} ${svgHeight}`);
    let markup = layers.map(({column, i}, k) => {
      const x = pad + k * (width + gap);
      return `<rect class="col-band" x="${x - 5}" y="8" width="${width + 10}" height="${svgHeight - 16}" rx="9"/>
        <text class="col-title" x="${x + 11}" y="31">${layerNames[i]}（${column.length}）</text>`;
    }).join('');
    edges.forEach(edge => {
      const a = xy.get(edge.from), b = xy.get(edge.to);
      const start = a.x + width, ay = a.y + height / 2, by = b.y + height / 2;
      const ax = a.x + width / 2, bx = b.x + width / 2;
      const path = b.x > start
        ? `M${start},${ay} C${start + gap / 2},${ay} ${b.x - gap / 2},${by} ${b.x},${by}`
        : `M${ax},${a.y + height} C${ax},${a.y + height + 30} ${bx},${b.y + height + 30} ${bx},${b.y + height}`;
      markup += `<path class="e" data-from="${esc(edge.from)}" data-to="${esc(edge.to)}" d="${path}"><title>${esc(edge.kind)}</title></path>`;
    });
    for (const node of shown) {
      const point = xy.get(node.id);
      const state = [node.role_cn || node.role, node.owner, node.state].filter(Boolean).join(' · ');
      const off = !isOutside(node) && !isOn(node);
      markup += `<g class="n${off ? ' off' : ''}" data-node="${esc(node.id)}" tabindex="0" role="button"
        aria-pressed="false" aria-label="${esc(node.label + '，' + state)}" transform="translate(${point.x},${point.y})">
        <rect class="node-card" width="${width}" height="${height}" rx="8" fill="${off ? '#f4f6fa' : '#fff'}" stroke="${color(node.role)}"/>
        <rect width="4" height="${height}" fill="${color(node.role)}" rx="2"/>
        <text x="12" y="21">${esc(labelWithinCard(node.label))}</text>
        <text class="sub" x="12" y="41">${esc(labelWithinCard(state))}</text>
      </g>`;
    }
    graph.innerHTML = markup || '<text x="14" y="36">没有选中的节点类别</text>';
    applyFocus();
  }

  const connections = (edges, end) => (edges || []).map(edge => nodes.get(edge[end])?.label)
    .filter(Boolean).join('、');
  const clip = (value, range) => Math.max(6, Math.min(value, range));
  function showTip(event, node) {
    const upstream = connections(incoming.get(node.id), 'from');
    const downstream = connections(outgoing.get(node.id), 'to');
    tip.innerHTML = `<strong>${esc(node.label)}</strong>${esc(node.detail || node.role_cn || node.role)}
      ${upstream ? '<br>上游：' + esc(short(upstream, 100)) : ''}
      ${downstream ? '<br>下游：' + esc(short(downstream, 100)) : ''}
      <br>来源：arch.json`;
    tip.hidden = false;
    tip.style.left = clip(event.clientX + 18, innerWidth - tip.offsetWidth - 6) + 'px';
    const below = event.clientY + 18;
    tip.style.top = clip(below + tip.offsetHeight < innerHeight ? below : event.clientY - tip.offsetHeight - 12,
      innerHeight - tip.offsetHeight - 6) + 'px';
  }
  graph.onpointermove = event => {
    const id = event.target.closest('.n')?.dataset.node;
    if (id && event.pointerType !== 'touch') showTip(event, nodes.get(id));
    else tip.hidden = true;
  };
  graph.onpointerleave = () => { tip.hidden = true; };
  graph.onclick = event => {
    const id = event.target.closest('.n')?.dataset.node;
    if (id) focus(id);
  };
  graph.onkeydown = event => {
    const id = event.target.closest('.n')?.dataset.node;
    if (id && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      focus(id);
    }
  };

  function focus(id, reveal = false) {
    const node = nodes.get(id);
    if (!node) return;
    focused = !reveal && focused === id ? null : id;
    if (reveal && hidden.delete(node.role)) {
      chips.querySelectorAll('button[data-role]').forEach(button => {
        if (button.dataset.role === node.role) button.setAttribute('aria-pressed', 'true');
      });
      draw();
    } else {
      applyFocus();
    }
    if (reveal && xy.has(id)) {
      const position = xy.get(id);
      viewport.scrollTo({left: position.x - 10, top: position.y - 35});
    }
    onSelect(focused ? node : null);
  }

  function applyFocus() {
    const keep = new Set();
    const focusNode = nodes.get(focused);
    if (focusNode) {
      keep.add(focused);
      const explore = (index, key, upstream) => {
        const visited = new Set([focused]);
        const queue = [focused];
        while (queue.length) {
          for (const edge of index.get(queue.shift()) || []) {
            if (focusNode.role === 'domain' && edge.hosts && !edge.hosts.includes(focusNode.label)) continue;
            const other = edge[key];
            if (visited.has(other)) continue;
            visited.add(other);
            keep.add(other);
            if (!upstream || !isHub(nodes.get(other)) || isHub(focusNode)) queue.push(other);
          }
        }
      };
      explore(outgoing, 'to', false);
      explore(incoming, 'from', true);
      if (!isHub(focusNode)) [...keep].forEach(id => {
        for (const host of nodes.get(id)?.hosts || []) keep.add('d:' + host);
      });
      const ins = connections(incoming.get(focused), 'from') || '无记录';
      const outs = connections(outgoing.get(focused), 'to') || '无记录';
      selection.textContent = `${focusNode.label} · 上游 ${ins} · 下游 ${outs}`;
    } else {
      selection.textContent = '尚未选择节点。';
    }
    graph.querySelectorAll('.n').forEach(element => {
      const id = element.dataset.node;
      element.classList.toggle('dim', Boolean(focused) && !keep.has(id));
      element.classList.toggle('hl', id === focused);
      element.setAttribute('aria-pressed', String(id === focused));
    });
    graph.querySelectorAll('.e').forEach(element => {
      const {from, to} = element.dataset;
      const highlight = keep.has(from) && keep.has(to);
      element.classList.toggle('hl', highlight);
      element.classList.toggle('dim', Boolean(focused) && !highlight);
      element.classList.toggle('flow', highlight && (isOn(nodes.get(from)) || isOn(nodes.get(to))));
    });
    updateMotion();
  }

  draw();
  return {
    focus,
    dispose() {
      observer.disconnect();
      motion.removeEventListener('click', changeMotion);
      reduced.removeEventListener('change', updateMotion);
      document.removeEventListener('visibilitychange', updateMotion);
      removeEventListener('scroll', onScroll);
      viewport.removeEventListener('scroll', onScroll);
    },
  };
}
