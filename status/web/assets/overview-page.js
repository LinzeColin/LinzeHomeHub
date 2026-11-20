const OverviewPage = (() => {
  const {esc, num} = OverviewData;
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const text = (selector, value) => { $(selector).textContent = value; };
  const col = (key, label, width, render, value, search) => ({key, label, width, render, value, search});
  const table = (id, title, columns, filterLabel, value, options) =>
    new OverviewTable($(id), columns, {title, filter: {label: filterLabel, value}, ...options});
  const pluck = key => row => row[key];
  const badge = (label, tone = '') => `<span class="status-pill ${tone}">${esc(label)}</span>`;
  const backupEpoch = snapshot => snapshot?.ops?.backup?.epoch
    ?? OverviewData.sourceEpoch(snapshot?.ops?.backup?.at, '+00:00');
  const selectedNode = () => new URLSearchParams(location.search).get('node');
  const nodeUrl = (path, id, hash) => `${path}${id ? '?node=' + encodeURIComponent(id) : ''}${hash}`;
  const sources = {
    snapshot: item => item?.host && Array.isArray(item.projects) && num(item.updated_epoch) > 0,
    history: item => item?.min && item?.hour && item?.day,
    arch: item => Array.isArray(item?.nodes) && Array.isArray(item.edges),
  };

  function setupNav() {
    const rail = $('#page-nav'), handheld = matchMedia('(max-width: 800px)');
    const openButton = $('#menu-open'), overlay = $('#menu-close'), fold = $('#rail-toggle');
    const links = $$('#directory-links a');
    const mobileCurrent = $('.mobile-current');
    const headings = links.map(link => $(link.hash).querySelector('h1, h2'));
    const main = $('#main'), topbar = $('.top');
    let activeIndex = 0, drawerOpen = false;
    links.forEach(link => { link.title = link.dataset.icon; });
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
    links.forEach((link, index) => link.addEventListener('click', () => {
      setCurrent(index);
      if (!handheld.matches) return;
      setDrawer(false);
      const heading = headings[index];
      heading.tabIndex = -1;
      heading.focus({preventScroll: true});
    }));

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
      if (fresh.has('snapshot')) {
        const schema = JSON.parse(metadata.textContent);
        schema.data_updated_at = new Date(data.snapshot.updated_epoch * 1000).toISOString();
        metadata.textContent = JSON.stringify(schema);
      }
      try {
        render(data, fresh);
      } finally {
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

  return {$, $$, text, col, table, pluck, badge, backupEpoch, selectedNode, nodeUrl, start};
})();
