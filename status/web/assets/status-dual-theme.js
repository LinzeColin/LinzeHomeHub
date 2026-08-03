/* STATUS_AGENT_V3_MANAGED: v0.0.0.3
 * status.linzezhang.com dual visual theme controller.
 * UI-only. It does not call fetch, mutate business payloads, or alter backend/runtime contracts.
 */
(() => {
  'use strict';

  const VERSION = 'v0.0.0.3';
  const STORAGE_KEY = 'statusVisualTheme';
  const THEMES = Object.freeze(['galaxy', 'titanium']);
  const DEFAULT_THEME = 'galaxy';
  const CONTROL_PANEL_TITLE = 'Agent Control Panel';
  const CONTROL_PANEL_DOCUMENT_TITLE = 'Agent Control Panel · status.linzezhang.com';
  const root = document.documentElement;
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
  let active = THEMES.includes(root.dataset.visualTheme) ? root.dataset.visualTheme : DEFAULT_THEME;
  let animator = null;

  const safeGet = () => {
    try {
      const value = localStorage.getItem(STORAGE_KEY);
      return THEMES.includes(value) ? value : null;
    } catch (_) {
      return null;
    }
  };

  const safeSet = (value) => {
    try { localStorage.setItem(STORAGE_KEY, value); } catch (_) {}
  };

  const themeLabel = (value) => value === 'titanium' ? '钛金流场' : '深空指挥舱';

  function updateButtons() {
    document.querySelectorAll('[data-status-visual-theme]').forEach((button) => {
      const selected = button.dataset.statusVisualTheme === active;
      button.setAttribute('aria-pressed', String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
  }

  function createSwitcher() {
    if (document.getElementById('statusVisualThemeSwitch')) return;
    const host = document.querySelector('.topbar .ext') || document.querySelector('.topbar') || document.body;
    const colorButton = document.getElementById('themeBtn');
    const switcher = document.createElement('div');
    switcher.id = 'statusVisualThemeSwitch';
    switcher.setAttribute('role', 'group');
    switcher.setAttribute('aria-label', '视觉主题');
    switcher.innerHTML = [
      '<button class="status-visual-theme-option" type="button" data-status-visual-theme="galaxy" aria-pressed="true" title="切换到深空指挥舱（默认）"><span>深空</span></button>',
      '<button class="status-visual-theme-option" type="button" data-status-visual-theme="titanium" aria-pressed="false" title="切换到钛金流场"><span>钛金</span></button>'
    ].join('');
    if (colorButton && colorButton.parentNode === host) host.insertBefore(switcher, colorButton);
    else host.appendChild(switcher);

    switcher.addEventListener('click', (event) => {
      const button = event.target.closest('[data-status-visual-theme]');
      if (button) applyTheme(button.dataset.statusVisualTheme, { persist: true, announce: true });
    });
    switcher.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const current = THEMES.indexOf(active);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? THEMES.length - 1 :
        (current + (event.key === 'ArrowRight' ? 1 : -1) + THEMES.length) % THEMES.length;
      applyTheme(THEMES[next], { persist: true, announce: true });
      switcher.querySelector(`[data-status-visual-theme="${THEMES[next]}"]`)?.focus();
    });
  }

  function createAmbient() {
    if (!document.getElementById('statusVisualAmbient')) {
      const ambient = document.createElement('div');
      ambient.id = 'statusVisualAmbient';
      ambient.setAttribute('aria-hidden', 'true');
      ambient.innerHTML = '<canvas id="statusVisualCanvas"></canvas>';
      document.body.prepend(ambient);
    }
    if (!document.getElementById('statusVisualOrnament')) {
      const head = document.querySelector('.head');
      if (head) {
        const ornament = document.createElement('div');
        ornament.id = 'statusVisualOrnament';
        ornament.setAttribute('aria-hidden', 'true');
        ornament.innerHTML = [
          '<i class="status-visual-ring"></i><i class="status-visual-ring"></i><i class="status-visual-ring"></i><i class="status-visual-core"></i>',
          '<span class="status-visual-cube">',
          '<i class="status-visual-face"></i><i class="status-visual-face"></i><i class="status-visual-face"></i>',
          '<i class="status-visual-face"></i><i class="status-visual-face"></i><i class="status-visual-face"></i>',
          '</span>'
        ].join('');
        head.appendChild(ornament);
      }
    }
  }

  function preserveControlPanelIdentity() {
    const apply = () => {
      const pageTitle = document.getElementById('ptitle');
      if (document.title !== CONTROL_PANEL_DOCUMENT_TITLE) document.title = CONTROL_PANEL_DOCUMENT_TITLE;
      if (pageTitle && pageTitle.textContent !== CONTROL_PANEL_TITLE) pageTitle.textContent = CONTROL_PANEL_TITLE;
    };
    apply();
    const pageTitle = document.getElementById('ptitle');
    if (!pageTitle || !window.MutationObserver) return;
    const observer = new MutationObserver(apply);
    observer.observe(pageTitle, { childList: true, characterData: true, subtree: true });
  }

  function announce(value) {
    const existing = document.getElementById('statusVisualThemeAnnouncement');
    const live = existing || document.body.appendChild(Object.assign(document.createElement('span'), {
      id: 'statusVisualThemeAnnouncement',
      className: 'sr-only'
    }));
    live.setAttribute('aria-live', 'polite');
    live.textContent = `已切换到${themeLabel(value)}`;
  }

  function applyTheme(value, options = {}) {
    const next = THEMES.includes(value) ? value : DEFAULT_THEME;
    const changed = next !== active;
    active = next;
    root.dataset.statusVisualTransition = changed ? 'true' : 'false';
    root.dataset.visualTheme = next;
    document.body?.classList.toggle('status-theme-galaxy', next === 'galaxy');
    document.body?.classList.toggle('status-theme-titanium', next === 'titanium');
    updateButtons();
    if (options.persist) safeSet(next);
    if (options.announce && changed) announce(next);
    window.setTimeout(() => { root.dataset.statusVisualTransition = 'false'; }, 240);
    window.dispatchEvent(new CustomEvent('status:visual-theme-change', { detail: { theme: next, version: VERSION } }));
    if (animator) animator.setTheme(next);
    if (window.__STATUS_DUAL_THEME__) window.__STATUS_DUAL_THEME__.activeVisualTheme = next;
  }

  class AmbientRenderer {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas?.getContext?.('2d', { alpha: true });
      this.theme = active;
      this.items = [];
      this.frame = 0;
      this.running = false;
      this.visible = true;
      this.resizeObserver = null;
      this.onResize = () => this.resize();
      this.onVisibility = () => {
        this.visible = !document.hidden;
        if (this.visible) this.start();
      };
    }

    start() {
      if (!this.ctx || reduced?.matches || this.running || !this.visible) return;
      this.running = true;
      this.resize();
      this.frame = requestAnimationFrame((time) => this.draw(time));
    }

    stop() {
      this.running = false;
      if (this.frame) cancelAnimationFrame(this.frame);
      this.frame = 0;
      if (this.ctx) this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }

    mount() {
      if (!this.ctx) return;
      addEventListener('resize', this.onResize, { passive: true });
      document.addEventListener('visibilitychange', this.onVisibility);
      if (window.ResizeObserver) {
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(document.documentElement);
      }
      this.start();
    }

    setTheme(value) {
      this.theme = value;
      this.seed();
      if (!reduced?.matches) this.start();
    }

    resize() {
      if (!this.canvas || !this.ctx) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 1.6);
      const width = Math.max(1, innerWidth);
      const height = Math.max(1, innerHeight);
      this.canvas.width = Math.round(width * dpr);
      this.canvas.height = Math.round(height * dpr);
      this.canvas.style.width = `${width}px`;
      this.canvas.style.height = `${height}px`;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.width = width;
      this.height = height;
      this.seed();
    }

    seed() {
      const count = Math.max(18, Math.min(72, Math.floor((this.width || innerWidth) / 18)));
      this.items = Array.from({ length: count }, (_, index) => ({
        x: Math.random() * (this.width || innerWidth),
        y: Math.random() * (this.height || innerHeight),
        vx: (Math.random() - .5) * (this.theme === 'galaxy' ? .16 : .24),
        vy: (Math.random() - .5) * (this.theme === 'galaxy' ? .12 : .18),
        size: .7 + Math.random() * 1.5,
        phase: Math.random() * Math.PI * 2,
        group: index % 3
      }));
    }

    draw(time = 0) {
      if (!this.running || !this.ctx) return;
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.width, this.height);
      const dark = document.documentElement.dataset.theme === 'dark' ||
        (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches);
      for (const item of this.items) {
        item.x += item.vx;
        item.y += item.vy;
        if (item.x < -12) item.x = this.width + 12;
        if (item.x > this.width + 12) item.x = -12;
        if (item.y < -12) item.y = this.height + 12;
        if (item.y > this.height + 12) item.y = -12;
        const pulse = .45 + Math.sin(time / 900 + item.phase) * .20;
        if (this.theme === 'galaxy') {
          const colors = dark ? ['126,214,255', '180,147,255', '240,201,111'] : ['23,104,207', '112,82,176', '151,103,15'];
          ctx.fillStyle = `rgba(${colors[item.group]},${Math.max(.08, pulse * .34)})`;
          ctx.beginPath();
          ctx.arc(item.x, item.y, item.size, 0, Math.PI * 2);
          ctx.fill();
        } else {
          const colors = dark ? ['82,229,231', '255,155,85', '181,199,209'] : ['8,127,134', '169,88,39', '83,103,111'];
          ctx.strokeStyle = `rgba(${colors[item.group]},${Math.max(.06, pulse * .23)})`;
          ctx.lineWidth = .7;
          ctx.beginPath();
          for (let corner = 0; corner < 6; corner += 1) {
            const angle = Math.PI / 3 * corner;
            const x = item.x + Math.cos(angle) * item.size * 2.4;
            const y = item.y + Math.sin(angle) * item.size * 2.4;
            if (corner === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
          }
          ctx.closePath();
          ctx.stroke();
        }
      }
      this.frame = requestAnimationFrame((next) => this.draw(next));
    }
  }

  function init() {
    createSwitcher();
    createAmbient();
    preserveControlPanelIdentity();
    active = safeGet() || (THEMES.includes(root.dataset.visualTheme) ? root.dataset.visualTheme : DEFAULT_THEME);
    animator = new AmbientRenderer(document.getElementById('statusVisualCanvas'));
    animator.mount();
    applyTheme(active, { persist: false, announce: false });

    document.addEventListener('keydown', (event) => {
      const target = event.target;
      if (event.key.toLowerCase() !== 't' || event.metaKey || event.ctrlKey || event.altKey ||
          target?.matches?.('input, textarea, select, [contenteditable="true"]')) return;
      applyTheme(active === 'galaxy' ? 'titanium' : 'galaxy', { persist: true, announce: true });
    });

    addEventListener('storage', (event) => {
      if (event.key === STORAGE_KEY && THEMES.includes(event.newValue)) applyTheme(event.newValue, { persist: false, announce: false });
    });

    reduced?.addEventListener?.('change', (event) => event.matches ? animator.stop() : animator.start());

    window.__STATUS_DUAL_THEME__ = {
      schemaVersion: 'status.dual_theme.v1',
      version: VERSION,
      visualThemes: [...THEMES],
      defaultVisualTheme: DEFAULT_THEME,
      activeVisualTheme: active,
      colorModes: ['light', 'dark'],
      colorModeControl: '#themeBtn',
      navigationPosition: 'top',
      pageIdentity: CONTROL_PANEL_TITLE,
      storageKeys: [STORAGE_KEY, 'theme'],
      deprecatedVisualThemes: [],
      runtimeModelCalls: 0,
      businessDataWrites: 0,
      backendChanges: 0,
      networkRequestsAdded: 0
    };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
