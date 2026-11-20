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

  function placeBelowHeader(target) {
    if (!target) return;
    const headerBottom = $('.top').getBoundingClientRect().bottom;
    scrollTo(0, Math.max(0, Math.floor(scrollY + target.getBoundingClientRect().top - headerBottom)));
  }

  function mountDetail() {
    const view = params.get('view');
    const template = $$('template[data-detail]').find(entry => entry.dataset.detail === view);
    if (!template) return null;
    const main = $('#main');
    main.replaceChildren(template.content.cloneNode(true), main.querySelector('footer'));
    document.body.dataset.detailView = view;
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
      mark.textContent = section.dataset.mark;
      label.textContent = section.dataset.directory;
      link.append(mark, label);
      item.append(link);
      return item;
    });
    $('#directory-links').replaceChildren(...links);
    placeBelowHeader(anchorTarget(location.hash));
    return view;
  }

  function setupNav() {
    const rail = $('#page-nav'), handheld = matchMedia('(max-width: 800px)');
    const openButton = $('#menu-open'), overlay = $('#menu-close'), fold = $('#rail-toggle');
    const directoryLinks = $$('#directory-links a');
    const links = directoryLinks.filter(link => link.getAttribute('href').startsWith('#'));
    const mobileCurrent = $('.mobile-current');
    const headings = links.map(link => $(link.hash).querySelector('h1, h2'));
    const main = $('#main'), topbar = $('.top');
    let activeIndex = 0, drawerOpen = false;
    directoryLinks.forEach(link => { link.title = link.dataset.icon; });
    const setDrawer = (open, restore = false) => {
      drawerOpen = open;
      document.body.classList.toggle('nav-open', open);
      openButton.setAttribute('aria-expanded', String(open || !handheld.matches));
      openButton.setAttribute('aria-label', open ? '关闭目录' : '打开目录');
      overlay.hidden = !open;
      main.inert = open;
      setCurrent(activeIndex);
      if (open) rail.querySelector('[aria-current]')?.focus({preventScroll: true});
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
    main.addEventListener('focusin', event => {
      const target = event.target;
      if (!target.closest('.table-tools')) return;
      requestAnimationFrame(() => {
        if (document.activeElement !== target) return;
        const safeTop = topbar.getBoundingClientRect().bottom + 6;
        const top = target.getBoundingClientRect().top;
        if (top < safeTop) scrollBy(0, top - safeTop);
      });
    });

    const plainClick = event => event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
    function jump(event, index) {
      if (!plainClick(event)) return;
      event.preventDefault();
      const heading = headings[index];
      const {hash} = links[index];
      if (hash !== location.hash) history.pushState(null, '', hash);
      if (drawerOpen) setDrawer(false);
      placeBelowHeader(heading);
      setCurrent(index);
      heading.tabIndex = -1;
      heading.focus({preventScroll: true});
    }
    links.forEach((link, index) => link.addEventListener('click', event => jump(event, index)));
    directoryLinks.filter(link => !links.includes(link)).forEach(link => link.addEventListener('click', event => {
      if (drawerOpen && plainClick(event)) setDrawer(false);
    }));
    mobileCurrent.addEventListener('click', event => jump(event, activeIndex));
    topbar.addEventListener('click', event => {
      const link = event.target.closest('a[href]');
      if (!link || !plainClick(event) || link.origin !== location.origin || link.pathname !== location.pathname ||
          link.search !== location.search || link.hash) return;
      event.preventDefault();
      if (location.hash) history.pushState(null, '', link.href);
      scrollTo(0, 0);
      mark();
    });

    function setCurrent(index) {
      activeIndex = index;
      const section = links[index];
      const active = handheld.matches && !drawerOpen ? mobileCurrent : section;
      if (mobileCurrent.hash !== section.hash) {
        mobileCurrent.href = section.hash;
        mobileCurrent.textContent = section.dataset.icon;
      }
      if (!active.hasAttribute('aria-current')) {
        rail.querySelector('[aria-current]')?.removeAttribute('aria-current');
        active.setAttribute('aria-current', 'location');
      }
      const area = rail.getBoundingClientRect(), item = active.getBoundingClientRect();
      if (item.top < area.top) rail.scrollTop -= area.top - item.top;
      else if (item.bottom > area.bottom) rail.scrollTop += item.bottom - area.bottom;
    }

    function mark() {
      const edge = topbar.getBoundingClientRect().bottom;
      let active = 0;
      headings.forEach((heading, index) => {
        if (heading.getBoundingClientRect().top <= edge + 1) active = index;
      });
      const atEnd = scrollY > 0 && innerHeight + scrollY >= document.documentElement.scrollHeight - 1;
      if (atEnd) active = headings.length - 1;
      setCurrent(active);
    }
    addEventListener('scroll', mark, {passive: true});
    addEventListener('hashchange', mark);
    addEventListener('pageshow', mark);
    addEventListener('resize', mark);
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
    const landingHash = detailView && performance.getEntriesByType('navigation')[0]?.type === 'navigate' ? location.hash : '';
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
        if (firstRead && landingHash && landingHash === location.hash) placeBelowHeader(anchorTarget(landingHash));
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
