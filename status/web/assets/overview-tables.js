/* Search, filtering, sortable headings and mouse/touch/keyboard column sizing. */
class OverviewTable {
  constructor(root, columns, options) {
    const {esc} = OverviewData;
    this.id = root.id;
    this.columns = columns;
    this.filter = options.filter;
    const saved = history.state?.tables?.[this.id];
    this.sort = Number.isInteger(saved?.sort) && columns[saved.sort] ? saved.sort : null;
    this.direction = saved?.direction === -1 ? -1 : 1;
    this.rows = [];
    this.visibleRows = [];
    const shared = options.sharedFilters;
    const count = '<p class="table-count" aria-live="polite">读取中</p>';
    root.classList.add('data-block');
    root.innerHTML = `
      ${shared ? count : `<div class="table-tools">
        <label>搜索 <input type="search" placeholder="搜索…" aria-label="搜索${esc(options.title)}"></label>
        <label>${esc(this.filter.label)} <select aria-label="${esc(options.title)}${esc(this.filter.label)}"></select></label>
        ${count}
      </div>`}
      <div class="table-wrap" role="region" aria-label="${esc(options.title)}"${document.getElementById('table-guide') ? ' aria-describedby="table-guide"' : ''} tabindex="0">
        <table class="data-table" aria-label="${esc(options.title)}">
          <colgroup>${columns.map(() => '<col>').join('')}</colgroup>
          <thead><tr>${columns.map((column, i) => `<th scope="col"><div class="th-actions">
            <button type="button" class="th-sort" data-sort="${i}">${esc(column.label)}</button>
            <button type="button" class="th-size" data-size="${i}" title="拖动或用左右方向键调列宽" aria-label="调整${esc(column.label)}列宽">↔</button>
          </div></th>`).join('')}</tr></thead><tbody></tbody>
        </table>
      </div>`;
    this.search = shared?.search || root.querySelector('input');
    this.select = shared?.select || root.querySelector('select');
    this.sharedFilters = Boolean(shared);
    if (!shared) this.search.value = saved?.query || '';
    this.savedFilter = !shared && saved?.filter;
    this.count = root.querySelector('.table-count');
    this.table = root.querySelector('table');
    this.body = root.querySelector('tbody');
    this.cols = [...root.querySelectorAll('col')];
    this.buttons = [...root.querySelectorAll('.th-sort')];
    this.widths = columns.map((column, index) => saved?.widths?.[index] || column.width || 145);
    this.rowClass = options.rowClass || (() => '');
    this.isPinned = options.isPinned || (() => false);
    this.empty = options.empty || '无数据';
    this.resize();
    if (!shared) this.reset = OverviewTable.bindFilters(this.search, this.select, () => this.apply());
    this.buttons.forEach((button, index) => button.addEventListener('click', () => {
      this.direction = this.sort === index ? -this.direction : 1;
      this.sort = index;
      this.apply();
    }));
    root.querySelectorAll('.th-size').forEach((grip, index) => this.bindResize(grip, index));
  }

  static bindFilters(search, select, render) {
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'secondary table-reset';
    reset.textContent = '清除筛选';
    search.addEventListener('input', render);
    select.addEventListener('change', render);
    reset.addEventListener('click', () => {
      search.value = '';
      select.value = '';
      render();
      search.focus();
    });
    return reset;
  }

  static setOptions(select, values, selected = select.value) {
    const options = [...new Set(values.filter(value => value != null))];
    select.replaceChildren(new Option('全部', ''), ...options.map(value => new Option(value, value)));
    if (options.includes(selected)) select.value = selected;
  }

  bindResize(grip, index) {
    let drag;
    grip.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      grip.focus();
      grip.setPointerCapture(event.pointerId);
      drag = {start: event.clientX, width: this.widths[index], pointerId: event.pointerId};
    });
    grip.addEventListener('pointermove', event => {
      if (drag?.pointerId === event.pointerId) this.resize(index, drag.width + event.clientX - drag.start);
    });
    grip.addEventListener('lostpointercapture', () => { drag = null; this.remember(); });
    grip.addEventListener('keydown', event => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      this.resize(index, this.widths[index] + (event.key === 'ArrowLeft' ? -16 : 16));
      this.remember();
    });
  }

  resize(index, width) {
    if (index !== undefined) this.widths[index] = Math.min(900, Math.max(105, width));
    this.table.style.minWidth = this.widths.reduce((a, b) => a + b, 0) + 'px';
    this.cols.forEach((col, i) => { col.style.width = this.widths[i] + 'px'; });
  }

  setRows(rows) {
    this.rows = rows;
    if (!this.sharedFilters) {
      OverviewTable.setOptions(this.select, rows.map(this.filter.value), this.savedFilter || this.select.value);
      if (rows.length) this.savedFilter = null;
    }
    this.render();
  }

  pick(value, toggle = true) {
    if (![...this.select.options].some(option => option.value === value)) return;
    this.select.value = toggle && this.select.value === value ? '' : value;
    this.apply();
  }

  apply() {
    this.render();
    this.remember();
  }

  remember() {
    const state = history.state || {};
    const settings = {sort: this.sort, direction: this.direction, widths: this.widths};
    if (!this.sharedFilters) {
      settings.query = this.search.value;
      settings.filter = this.select.value;
    }
    history.replaceState({...state, tables: {...state.tables, [this.id]: settings}}, '');
  }

  focusRow(row) {
    const control = this.body.rows[this.visibleRows.indexOf(row)]?.querySelector('button, a[href]');
    if (!control) return;
    control.scrollIntoView({block: 'center', inline: 'nearest'});
    control.focus({preventScroll: true});
  }

  render() {
    const {noData, esc} = OverviewData;
    const query = this.search.value.trim().toLocaleLowerCase();
    const filter = this.select.value;
    const {columns} = this;
    const value = (column, row) => column.value ? column.value(row) : row[column.key];
    const view = this.rows.filter(row =>
      (!filter || this.filter.value(row) === filter)
      && (!query || columns.some(column =>
        String(column.search ? column.search(row) : value(column, row) ?? '').toLocaleLowerCase().includes(query))));
    this.buttons.forEach((button, index) => {
      const th = button.closest('th');
      if (index === this.sort) th.setAttribute('aria-sort', this.direction === 1 ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
    });
    if (this.sort !== null) {
      const column = columns[this.sort];
      view.sort((a, b) => {
        const pinnedFirst = Number(this.isPinned(b)) - Number(this.isPinned(a));
        if (pinnedFirst) return pinnedFirst;
        const first = value(column, a), second = value(column, b);
        if (first == null) return second == null ? 0 : 1;
        if (second == null) return -1;
        return this.direction * (typeof first === 'number' && typeof second === 'number'
          ? first - second : String(first).localeCompare(String(second), 'zh-CN', {numeric: true}));
      });
    }
    this.visibleRows = view;
    this.count.textContent = `${view.length} / ${this.rows.length} 行`;
    if (this.reset) {
      if (query || filter) {
        if (!this.reset.isConnected) this.count.before(this.reset);
      } else {
        this.reset.remove();
      }
    }
    this.body.innerHTML = view.length ? view.map(row => `<tr class="${esc(this.rowClass(row))}">
      ${columns.map(column => `<td>${column.render ? column.render(row) : esc(value(column, row) ?? noData)}</td>`).join('')}
      </tr>`).join('') : `<tr><td colspan="${columns.length}">${esc(query || filter ? '没有符合条件的行' : this.empty)}</td></tr>`;
  }
}
