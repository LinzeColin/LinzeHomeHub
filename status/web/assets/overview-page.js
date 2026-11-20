const OverviewPage = (() => {
  const {esc, num} = OverviewData;
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const text = (selector, value) => { $(selector).textContent = value; };
  const col = (key, label, width, render, value, search) => ({key, label, width, render, value, search});
  const table = (target, title, columns, filterLabel, value, options) => {
    const root = typeof target === 'string' ? $(target) : target;
    return root ? new OverviewTable(root, columns, {title, filter: {label: filterLabel, value}, ...options}) : null;
  };
  function tableGroup(root, id, name, columns, filterLabel, value, options) {
    const block = document.createElement('div');
    const title = document.createElement('h4');
    const slot = document.createElement('div');
    block.className = 'table-group';
    title.textContent = name;
    slot.id = id;
    slot.className = 'table-slot';
    block.append(title, slot);
    root.append(block);
    return table(slot, name, columns, filterLabel, value, options);
  }
  const unitRoles = new Map(Object.entries({
    domain: 'ingress', tunnel: 'ingress', proxy: 'ingress', auth: 'ingress',
    service: 'services', monitor: 'services', timer: 'timers',
  }));
  const unitGroup = role => unitRoles.get(role) || 'runtime';
  const pluck = key => row => row[key];
  const badge = (label, tone = '') => `<span class="status-pill ${tone}">${esc(label)}</span>`;
  const graphLink = (id, label) => id
    ? `<button type="button" class="graph-link" data-arch-node="${esc(id)}" aria-label="在架构图中定位 ${esc(label)}">${esc(label)}</button>`
    : esc(label);
  const backupEpoch = snapshot => snapshot?.ops?.backup?.epoch
    ?? OverviewData.sourceEpoch(snapshot?.ops?.backup?.at, '+00:00');
  const params = new URLSearchParams(location.search);
  const selectedNode = () => params.get('node');
  const nodeUrl = (path, id, hash) => `${path}${id ? '?node=' + encodeURIComponent(id) : ''}${hash}`;
  const detailUrl = (view, hash, extra = {}) => `/check.html?${new URLSearchParams({view, ...extra})}${hash}`;
  const sources = {
    snapshot: item => item?.host && Array.isArray(item.projects) && num(item.updated_epoch) > 0,
    history: item => item?.min && item?.hour && item?.day,
    arch: item => Array.isArray(item?.nodes) && Array.isArray(item.edges),
  };

  function anchorTarget(hash) {
    if (!hash) return null;
    try { return document.getElementById(decodeURIComponent(hash.slice(1))); } catch { return null; }
  }

  const overviewHeading = target => target?.matches('[data-km-section], .inventory-card')
    ? target.querySelector('h1, h2, h3') : target;

  function land(hash, focus = false) {
    if (!hash || hash === '#top') {
      scrollTo(0, 0);
      if (focus) $('#main h1')?.focus({preventScroll: true});
      return;
    }
    const target = anchorTarget(hash);
    if (!target) return;
    const board = target.closest('.inventory-board');
    if (board) board.scrollLeft += target.getBoundingClientRect().left - board.getBoundingClientRect().left;
    const heading = overviewHeading(target);
    const headerBottom = $('.top').getBoundingClientRect().bottom;
    scrollTo(0, Math.max(0, Math.floor(scrollY + heading.getBoundingClientRect().top - headerBottom)));
    if (focus) {
      heading.tabIndex = -1;
      heading.focus({preventScroll: true});
    }
  }

  function mountDetail() {
    const view = params.get('view');
    const template = $$('template[data-detail]').find(entry => entry.dataset.detail === view);
    const records = !template && typeof OverviewRecords !== 'undefined' && OverviewRecords.create(view);
    if (!template && !records) return null;
    const main = $('#main');
    main.replaceChildren(template ? template.content.cloneNode(true) : records, main.querySelector('footer'));
    document.body.dataset.detailView = view;
    document.title = main.querySelector('h1').textContent;
    $('#check-link').removeAttribute('aria-current');
    const links = $$('main [data-directory]').map(section => {
      const item = document.createElement('li');
      const link = document.createElement('a');
      const mark = document.createElement('span');
      const label = document.createElement('span');
      link.href = '#' + section.id;
      link.dataset.icon = section.dataset.directory;
      mark.className = 'nav-mark';
      mark.setAttribute('aria-hidden', 'true');
      mark.textContent = section.dataset.mark || '▤';
      label.textContent = section.dataset.directory;
      link.append(mark, label);
      item.append(link);
      return item;
    });
    $('#directory-links').replaceChildren(...links);
    return view;
  }

  const plainClick = event => event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

  function setupNav() {
    const rail = $('#page-nav'), handheld = matchMedia('(max-width: 800px)');
    const openButton = $('#menu-open'), overlay = $('#menu-close'), fold = $('#rail-toggle');
    const main = $('#main'), topbar = $('.top');
    const directoryLinks = $$('#directory-links a');
    const links = directoryLinks.filter(link => link.getAttribute('href').startsWith('#') && anchorTarget(link.hash));
    const headings = links.map(link => overviewHeading(anchorTarget(link.hash)));
    const quickLinks = $$('.quick-links a');
    let activeIndex = 0, drawerOpen = false;

    directoryLinks.forEach(link => { link.title = link.dataset.icon; });
    $$('#main > section, #main .inventory-card, #list, #machine, #h-attention').forEach(target => {
      const heading = overviewHeading(target) === target && !target.matches('h3') ? target.querySelector('h2, h3') : overviewHeading(target);
      if (!heading) return;
      heading.tabIndex = -1;
      let line = heading.parentElement;
      if (line === target || heading === target || !line.matches('.headline, .section-head')) {
        line = document.createElement('div');
        line.className = 'section-head';
        heading.before(line);
        line.append(heading);
      }
      const link = document.createElement('a');
      link.href = '#top';
      link.className = 'to-top';
      link.textContent = '回到顶部';
      line.append(link);
    });
    const setDrawer = (open, restore = false) => {
      drawerOpen = open;
      document.body.classList.toggle('nav-open', open);
      openButton.setAttribute('aria-expanded', String(open));
      openButton.setAttribute('aria-label', open ? '关闭目录' : '打开目录');
      overlay.hidden = !open;
      main.inert = open;
      if (open) (links[activeIndex] || directoryLinks[0])?.focus({preventScroll: true});
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
    openButton.addEventListener('click', () => setDrawer(!drawerOpen));
    overlay.addEventListener('click', () => setDrawer(false, true));
    handheld.addEventListener('change', () => setDrawer(false));
    $('#go-back').addEventListener('click', () => {
      if (drawerOpen) setDrawer(false);
      if (history.length > 1) history.back();
      else if (location.pathname !== '/') location.assign('/');
      else land('#top', true);
    });
    fold.addEventListener('click', () => {
      const narrow = !document.body.classList.contains('nav-narrow');
      setNarrow(narrow);
      try { localStorage.setItem('status-narrow-nav', String(narrow)); } catch {}
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && drawerOpen) setDrawer(false, true);
    });
    main.addEventListener('focusin', event => {
      const target = event.target;
      if (!target.matches('a[href], button, input, select, textarea, summary')) return;
      requestAnimationFrame(() => {
        if (document.activeElement !== target) return;
        const safeTop = topbar.getBoundingClientRect().bottom + 6;
        const top = target.getBoundingClientRect().top;
        if (top < safeTop) scrollBy(0, top - safeTop);
      });
    });

    document.addEventListener('click', event => {
      const link = event.target.closest('a[href]');
      if (!link || !plainClick(event) || event.defaultPrevented) return;
      if (drawerOpen && rail.contains(link)) setDrawer(false);
      if (link.origin !== location.origin || link.pathname !== location.pathname || link.search !== location.search) return;
      if (link.hash && !anchorTarget(link.hash)) return;
      if (!link.hash && !topbar.contains(link)) return;
      event.preventDefault();
      if (link.href !== location.href) history.pushState(history.state, '', link.href);
      land(link.hash, true);
      mark();
    });

    function setCurrent(index) {
      activeIndex = index;
      const active = links[index];
      if (active && !active.hasAttribute('aria-current')) {
        rail.querySelector('[aria-current]')?.removeAttribute('aria-current');
        active.setAttribute('aria-current', 'location');
      }
      if (active && (!handheld.matches || drawerOpen)) {
        const area = rail.getBoundingClientRect(), item = active.getBoundingClientRect();
        if (item.top < area.top) rail.scrollTop -= area.top - item.top;
        else if (item.bottom > area.bottom) rail.scrollTop += item.bottom - area.bottom;
      }
      for (const link of quickLinks) {
        const onDetail = Boolean(detailView && new URL(link.href).searchParams.get('view') === detailView);
        const onInventory = !detailView && active?.hash === '#inventory' && link.hash === active.hash;
        if (onDetail || onInventory) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
      }
    }

    function mark() {
      const edge = topbar.getBoundingClientRect().bottom;
      let active = 0;
      headings.forEach((heading, index) => {
        if (Math.floor(heading.getBoundingClientRect().top) <= edge) active = index;
      });
      const atEnd = scrollY > 0 && innerHeight + scrollY >= document.documentElement.scrollHeight - 1;
      if (atEnd) active = headings.length - 1;
      setCurrent(active);
    }
    addEventListener('scroll', mark, {passive: true});
    addEventListener('hashchange', () => { land(location.hash); mark(); });
    addEventListener('pageshow', mark);
    addEventListener('resize', mark);
    land(location.hash);
    mark();
    return mark;
  }

  async function readSource(key) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(`/data/${key}.json`, {cache: 'no-store', signal: controller.signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!sources[key](data)) throw new Error('数据结构不符');
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }

  function start(keys, render) {
    const updateNav = setupNav();
    const button = $('#refresh'), metadata = $('#site-standard');
    const landingHash = location.hash;
    let firstRead = true;
    const data = {};
    let lastAttempt = 0;
    async function read() {
      if (button.disabled) return;
      lastAttempt = Date.now();
      button.disabled = true;
      button.textContent = '读取中…';
      const results = await Promise.allSettled(keys.map(readSource));
      const fresh = new Set();
      results.forEach((result, i) => {
        if (result.status === 'fulfilled') {
          data[keys[i]] = result.value;
          fresh.add(keys[i]);
        }
      });
      if (fresh.has('snapshot') || fresh.has('arch') && !data.snapshot) {
        const schema = JSON.parse(metadata.textContent);
        const epoch = num(data.snapshot?.updated_epoch ?? data.arch?.generated_epoch);
        if (epoch !== null && epoch > 0) {
          schema.data_updated_at = new Date(epoch * 1000).toISOString();
          metadata.textContent = JSON.stringify(schema);
        }
      }
      try {
        render(data, fresh);
      } finally {
        if (firstRead && landingHash && landingHash === location.hash) land(landingHash);
        firstRead = false;
        button.disabled = false;
        button.textContent = '刷新数据';
        document.body.dataset.loaded = 'true';
        updateNav();
      }
    }
    button.addEventListener('click', read);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && Date.now() - lastAttempt >= 60000) read();
    });
    read();
  }

  const detailView = mountDetail();
  document.addEventListener('click', event => {
    const node = event.target.closest('button[data-arch-node]')?.dataset.archNode;
    if (node) location.assign(detailUrl('arch', '#arch', {node}));
  });
  return {
    $, $$, text, col, table, tableGroup, unitGroup, pluck, badge, graphLink, backupEpoch,
    selectedNode, nodeUrl, detailUrl, detailView, params, start,
  };
})();
