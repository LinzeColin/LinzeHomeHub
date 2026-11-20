/* Search, filtering, sortable headings and mouse/touch/keyboard column sizing. */
class OverviewTable {
  constructor(root, columns, options) {
    const {esc} = OverviewData;
    this.columns = columns;
    this.filter = options.filter;
    this.sort = null;
    this.direction = 1;
    this.rows = [];
    this.visibleRows = [];
    const helpId = root.id + '-help';
    root.classList.add('data-block');
    root.innerHTML = `
      <div class="table-tools">
        <label>搜索 <input type="search" placeholder="搜索…" aria-label="搜索${esc(options.title)}"></label>
        <label>${esc(this.filter.label)} <select aria-label="${esc(options.title)}${esc(this.filter.label)}"></select></label>
        <p class="table-count" aria-live="polite">读取中</p>
      </div>
      <p class="table-help" id="${helpId}">列宽可拖动，键盘用左右方向键。窄屏可左右滑动查列。</p>
      <div class="table-wrap" role="region" aria-label="${esc(options.title)}" aria-describedby="${helpId}" tabindex="0">
        <table class="data-table" aria-label="${esc(options.title)}">
          <colgroup>${columns.map(() => '<col>').join('')}</colgroup>
          <thead><tr>${columns.map((column, i) => `<th scope="col"><div class="th-actions">
            <button type="button" class="th-sort" data-sort="${i}">${esc(column.label)}</button>
            <button type="button" class="th-size" data-size="${i}" title="拖动或用左右方向键调列宽" aria-label="调整${esc(column.label)}列宽">↔</button>
          </div></th>`).join('')}</tr></thead><tbody></tbody>
        </table>
      </div>`;
    this.search = root.querySelector('input');
    this.select = root.querySelector('select');
    this.reset = document.createElement('button');
    this.reset.type = 'button';
    this.reset.className = 'secondary table-reset';
    this.reset.textContent = '清除筛选';
    this.count = root.querySelector('.table-count');
    this.table = root.querySelector('table');
    this.frame = root.querySelector('.table-wrap');
    this.body = root.querySelector('tbody');
    this.cols = [...root.querySelectorAll('col')];
    this.buttons = [...root.querySelectorAll('.th-sort')];
    this.widths = columns.map(column => column.width || 145);
    this.rowClass = options.rowClass || (() => '');
    this.empty = options.empty || '无数据';
    this.resize();
    this.search.addEventListener('input', () => this.render());
    this.select.addEventListener('change', () => this.render());
    this.reset.addEventListener('click', () => {
      this.search.value = '';
      this.select.value = '';
      this.render();
      this.search.focus();
    });
    this.buttons.forEach((button, index) => button.addEventListener('click', () => {
      this.direction = this.sort === index ? -this.direction : 1;
      this.sort = index;
      this.render();
    }));
    root.querySelectorAll('.th-size').forEach((grip, index) => this.bindResize(grip, index));
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
    grip.addEventListener('lostpointercapture', () => { drag = null; });
    grip.addEventListener('keydown', event => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      this.resize(index, this.widths[index] + (event.key === 'ArrowLeft' ? -16 : 16));
    });
  }

  resize(index, width) {
    if (index !== undefined) this.widths[index] = Math.min(900, Math.max(105, width));
    this.table.style.minWidth = this.widths.reduce((a, b) => a + b, 0) + 'px';
    this.cols.forEach((col, i) => { col.style.width = this.widths[i] + 'px'; });
  }

  setRows(rows) {
    this.rows = rows;
    const selected = this.select.value;
    const options = [...new Set(rows.map(this.filter.value).filter(value => value !== null && value !== undefined))];
    this.select.replaceChildren(new Option('全部', ''), ...options.map(value => new Option(value, value)));
    if (options.includes(selected)) this.select.value = selected;
    this.render();
  }

  pick(value) {
    if (![...this.select.options].some(option => option.value === value)) return;
    this.select.value = this.select.value === value ? '' : value;
    this.render();
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
        const first = value(column, a), second = value(column, b);
        if (first == null) return second == null ? 0 : 1;
        if (second == null) return -1;
        return this.direction * (typeof first === 'number' && typeof second === 'number'
          ? first - second : String(first).localeCompare(String(second), 'zh-CN', {numeric: true}));
      });
    }
    this.visibleRows = view;
    this.frame.dataset.long = String(view.length > 12);
    this.count.textContent = `${view.length} / ${this.rows.length} 行`;
    if (query || filter) {
      if (!this.reset.isConnected) this.count.before(this.reset);
    } else {
      this.reset.remove();
    }
    this.body.innerHTML = view.length ? view.map(row => `<tr class="${esc(this.rowClass(row))}">
      ${columns.map(column => `<td>${column.render ? column.render(row) : esc(value(column, row) ?? noData)}</td>`).join('')}
      </tr>`).join('') : `<tr><td colspan="${columns.length}">${esc(query || filter ? '没有符合条件的行' : this.empty)}</td></tr>`;
  }
}
