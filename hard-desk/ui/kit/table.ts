// Таблица с тихими фильтрами и сортировкой — одна на оба пульта.
//
// Сортировка — щелчок по заголовку: по возрастанию → по убыванию → как было.
// Фильтр — воронка у заголовка, видна при наведении и остаётся гореть, когда
// фильтр стоит: список значений столбца с галочками (у чисел — «от/до»).
// Поиск по всем столбцам, «показано N из M», выгрузка показанного в Excel и
// копирование — одной строкой инструментов над таблицей, без крика.
// Состояние (сортировка, фильтры, поиск) живёт по id таблицы и переживает
// перерисовку раздела.
import { api, hard } from "./api";
import { esc, n, toast } from "./dom";

export interface Col<R> {
  key: string;
  title: string;
  num?: boolean;                       // выравнивание вправо и числовое сравнение
  value: (r: R) => unknown;            // для сортировки, фильтра, поиска, выгрузки
  html?: (r: R) => string;             // как рисовать ячейку (по умолчанию — value)
  filter?: boolean;                    // false — без воронки
  width?: string;
  left?: boolean;                      // числовой столбец, но слева (редко)
}

export interface TableOpts<R> {
  id: string;
  cols: Col<R>[];
  key: (r: R) => string;
  onRow?: (r: R, tr: HTMLElement) => void;
  select?: boolean;                    // галочки строк
  onSelect?: (keys: Set<string>, shown: R[]) => void;
  footer?: (shown: R[]) => (string | null)[];   // html ячеек итоговой строки
  exportName?: string;                 // имя книги при выгрузке
  empty?: string;
  rowClass?: (r: R) => string;
  searchPlaceholder?: string;
  externalSearch?: boolean;
  toolsLeft?: string;                  // свои кнопки слева от поиска
  afterRender?: (host: HTMLElement) => void;   // навесить обработчики на поля в ячейках
  profile?: "hard" | "soft";           // encrypted, principal-scoped preferences
  listHtml?: (r: R) => string;           // second read-only representation
  pageSize?: number;
  defaultHidden?: string[];
}

interface State {
  sort: { key: string; dir: 1 | -1 } | null;
  filters: Record<string, { values?: Set<string>; excluded?: Set<string>; min?: number; max?: number }>;
  q: string;
  selected: Set<string>;
  view?: "table" | "list";
  hidden?: string[];
  order?: string[];
  widths?: Record<string, number>;
  page?: number;
  active?: string;
  scrollLeft?: number;
  scrollTop?: number;
}

const states = new Map<string, State>();
const searchText = (value: string) => value.normalize('NFKC').toLowerCase().replace(/ё/g, 'е');
function stateOf(id: string): State {
  let s = states.get(id);
  if (!s) { s = { sort: null, filters: {}, q: "", selected: new Set() }; states.set(id, s); }
  return s;
}

function text(v: unknown): string {
  if (v === null || v === undefined) return "";
  return String(v);
}

function numOf(v: unknown): number {
  if (typeof v === "number") return v;
  const t = String(v ?? "").replace(/\s| /g, "").replace(",", ".").replace(/[^\d.\-]/g, "");
  const x = parseFloat(t);
  return Number.isFinite(x) ? x : NaN;
}

const FUNNEL = '<svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M1.5 2h9L7 6.2V10L5 9V6.2Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';

export class DataTable<R> {
  private rows: R[] = [];
  private shown: R[] = [];
  private st: State;
  private pop: HTMLElement | null = null;
  private loaded = false;
  private index = new Map<R, string>();
  private watch: MutationObserver | null = null;

  constructor(private host: HTMLElement, private o: TableOpts<R>) {
    this.st = stateOf(o.id);
    this.st.hidden ??= o.defaultHidden || [];
  }

  async restore(): Promise<void> {
    if (!this.o.profile || this.loaded) return;
    this.loaded = true;
    const r = await api("/api/local/preferences", { profile: this.o.profile, id: this.o.id });
    const saved = r.data?.value;
    // A new login can have no saved preferences while this renderer still
    // holds the previous login's state under the same table id.
    this.st = { sort: null, filters: {}, q: "", selected: new Set(), hidden: [...(this.o.defaultHidden || [])] };
    states.set(this.o.id, this.st);
    if (!saved || typeof saved !== "object") return;
    const keys = new Set(this.o.cols.map(c => c.key));
    this.st.q = typeof saved.q === "string" ? saved.q.slice(0, 2000) : "";
    this.st.sort = saved.sort && keys.has(saved.sort.key) && [1, -1].includes(saved.sort.dir) ? saved.sort : null;
    this.st.filters = {};
    for (const [key, value] of Object.entries(saved.filters || {}) as [string, any][]) {
      if (!keys.has(key) || !value || typeof value !== "object") continue;
      this.st.filters[key] = {
        values: Array.isArray(value.values) ? new Set(value.values.filter((x: unknown) => typeof x === "string")) : undefined,
        excluded: Array.isArray(value.excluded) ? new Set(value.excluded.filter((x:unknown)=>typeof x==="string")) : undefined,
        min: Number.isFinite(value.min) ? value.min : undefined, max: Number.isFinite(value.max) ? value.max : undefined,
      };
    }
    this.st.view = this.o.listHtml && saved.view === "list" ? "list" : "table";
    this.st.hidden = Array.isArray(saved.hidden) ? saved.hidden.filter((x: string) => keys.has(x) && x !== this.o.cols[0]?.key) : this.st.hidden;
    this.st.order = Array.isArray(saved.order) ? [...new Set<string>(saved.order.filter((x: string) => keys.has(x)))] : [];
    this.st.widths = Object.fromEntries(Object.entries(saved.widths || {}).filter(([k,v]) => keys.has(k) && typeof v === "number" && v >= 80 && v <= 800)) as Record<string, number>;
    this.st.page = Number.isInteger(saved.page) && saved.page >= 0 ? saved.page : 0;
    this.st.active = typeof saved.active === "string" ? saved.active : "";
    this.st.scrollLeft = Number.isFinite(saved.scrollLeft) ? saved.scrollLeft : 0;
    this.st.scrollTop = Number.isFinite(saved.scrollTop) ? saved.scrollTop : 0;
  }

  private persist(): void {
    if (!this.o.profile) return;
    const value = { ...this.st, selected: undefined, filters: Object.fromEntries(Object.entries(this.st.filters)
      .map(([k,v]) => [k, { ...v, values: v.values ? [...v.values] : undefined, excluded:v.excluded?[...v.excluded]:undefined }])) };
    void api("/api/local/preferences", undefined, "POST", { profile: this.o.profile, id: this.o.id, value })
      .then(r => { if (!r.ok && this.host.isConnected) toast(r.text || "Настройки не сохранились"); });
  }

  private columns(): Col<R>[] {
    const order = this.st.order || [];
    const all = [...this.o.cols].sort((a,b) => (order.includes(a.key) ? order.indexOf(a.key) : order.length + this.o.cols.indexOf(a)) -
      (order.includes(b.key) ? order.indexOf(b.key) : order.length + this.o.cols.indexOf(b)));
    const first = this.o.cols[0];
    return [first, ...all.filter(c => c !== first && !this.st.hidden?.includes(c.key))].filter(Boolean);
  }

  navigate(key: string, delta: number): R | undefined {
    if (!this.host.isConnected) return undefined;
    const index=this.shown.findIndex(r=>this.o.key(r)===key);
    return index<0?undefined:this.shown[index+delta];
  }

  setRows(rows: R[]): void {
    this.rows = rows;
    this.index.clear();
    for (const row of rows) this.index.set(row, searchText(this.o.cols.map(c => text(c.value(row))).join("\n")));
    const keys = new Set(rows.map((r) => this.o.key(r)));
    for (const k of [...this.st.selected]) if (!keys.has(k)) this.st.selected.delete(k);
    this.render();
  }

  setQuery(value: string): void {
    this.st.q = value;
    this.st.page = 0;
    this.render();
  }

  selected(): R[] {
    return this.rows.filter((r) => this.st.selected.has(this.o.key(r)));
  }

  /** Отмеченные, а если не отмечено никого — все показанные фильтром. */
  scope(): { rows: R[]; label: string; picked: boolean } {
    const sel = this.selected();
    if (sel.length) return { rows: sel, label: `отмеченным (${sel.length})`, picked: true };
    return { rows: this.shown.slice(), label: this.shown.length === this.rows.length ? `всем (${this.shown.length})` : `показанным (${this.shown.length})`, picked: false };
  }

  clearSelection(): void {
    this.st.selected.clear();
    this.render();
  }

  private passes(r: R): boolean {
    for (const c of this.o.cols) {
      const f = this.st.filters[c.key];
      if (!f) continue;
      const v = c.value(r);
      if (f.values && !f.values.has(text(v))) return false;
      if (f.excluded?.has(text(v))) return false;
      if (f.min !== undefined || f.max !== undefined) {
        const x = numOf(v);
        if (!Number.isFinite(x)) return false;
        if (f.min !== undefined && x < f.min) return false;
        if (f.max !== undefined && x > f.max) return false;
      }
    }
    const words = searchText(this.st.q).trim().split(/\s+/).filter(Boolean);
    if (!words.every(word => (this.index.get(r) || "").includes(word))) return false;
    return true;
  }

  private compute(): R[] {
    let out = this.rows.filter((r) => this.passes(r));
    const s = this.st.sort;
    if (s) {
      const c = this.o.cols.find((x) => x.key === s.key);
      if (c) {
        out = out.slice().sort((a, b) => {
          const va = c.value(a), vb = c.value(b);
          if (c.num) {
            const x = numOf(va), y = numOf(vb);
            const nx = Number.isFinite(x), ny = Number.isFinite(y);
            if (nx !== ny) return nx ? -1 : 1;           // пустые — всегда в конце
            return ((x || 0) - (y || 0)) * s.dir;
          }
          const ta = text(va), tb = text(vb);
          if (!ta !== !tb) return ta ? -1 : 1;
          return ta.localeCompare(tb, "ru", { numeric: true }) * s.dir;
        });
      }
    }
    return out;
  }

  render(): void {
    this.shown = this.compute();
    const o = this.o, st = this.st;
    const cols = this.columns();
    const wrap = this.host.querySelector(".table-wrap");
    if (wrap) { st.scrollLeft = wrap.scrollLeft; st.scrollTop = wrap.scrollTop; }
    const pageSize = o.pageSize || this.shown.length || 1;
    st.page = Math.min(st.page || 0, Math.max(0, Math.ceil(this.shown.length / pageSize) - 1));
    const pageRows = this.shown.slice(st.page * pageSize, (st.page + 1) * pageSize);
    const active = Object.keys(st.filters).filter((k) => st.filters[k]);
    const tokens = active.map((k) => {
      const c = o.cols.find((x) => x.key === k)!;
      const f = st.filters[k];
      let what = "";
      if(f.excluded){const list=[...f.excluded].map(v=>v || "пусто");what="кроме "+(list.length>2?`${list.slice(0,2).join(", ")} и ещё ${list.length-2}`:list.join(", "));
      } else if (f.values) {
        const list = [...f.values].map((v) => v || "пусто");
        what = list.length > 2 ? `${list.slice(0, 2).join(", ")} и ещё ${list.length - 2}` : list.join(", ");
      } else {
        what = [f.min !== undefined ? `от ${f.min}` : "", f.max !== undefined ? `до ${f.max}` : ""].filter(Boolean).join(" ");
      }
      return `<button class="ftoken" data-unfilter="${esc(k)}" title="Снять фильтр">${esc(c.title)}: ${esc(what)} <span aria-hidden="true">×</span></button>`;
    }).join("");
    const allOn = o.select && this.shown.length > 0 && this.shown.every((r) => st.selected.has(o.key(r)));
    const th = cols.map((c) => {
      const sorted = st.sort?.key === c.key ? (st.sort.dir > 0 ? "ascending" : "descending") : "";
      const filtered = !!st.filters[c.key];
      const width = st.widths?.[c.key] ? `${st.widths[c.key]}px` : c.width;
      return `<th class="${c.num && !c.left ? "" : "l"}${filtered ? " filtered" : ""}" data-col="${esc(c.key)}"${sorted ? ` aria-sort="${sorted}"` : ""}${width ? ` style="width:${width};min-width:${width};max-width:${width}"` : ""}>
        <span class="th-in"><button class="th-t" data-sort="${esc(c.key)}" aria-label="Сортировать: ${esc(c.title)}">${esc(c.title)}</button>${c.filter === false ? "" : `<button class="funnel" data-funnel="${esc(c.key)}" title="Фильтр: ${esc(c.title)}">${FUNNEL}</button>`}</span></th>`;
    }).join("");
    const body = pageRows.map((r) => {
      const k = o.key(r);
      const box = o.select ? `<td class="l sel"><input type="checkbox" data-pick="${esc(k)}" ${st.selected.has(k) ? "checked" : ""}></td>` : "";
      const tds = cols.map((c) => `<td class="${c.num && !c.left ? "" : "l"}"${st.widths?.[c.key] ? ` style="max-width:${st.widths[c.key]}px"` : ""}>${c.html ? c.html(r) : esc(text(c.value(r)))}</td>`).join("");
      return `<tr class="${o.onRow ? "pick" : ""} ${o.rowClass ? o.rowClass(r) : ""}${st.active === k ? " current-row" : ""}" data-key="${esc(k)}"${o.onRow ? ' tabindex="0"' : ""}${st.selected.has(k) ? ' aria-selected="true"' : ""}>${box}${tds}</tr>`;
    }).join("");
    const foot = o.footer ? o.footer(this.shown) : null;
    const tfoot = foot ? `<tfoot><tr>${o.select ? "<td></td>" : ""}${foot.map((h, i) => `<td class="${o.cols[i]?.num && !o.cols[i]?.left ? "" : "l"}">${h ?? ""}</td>`).join("")}</tr></tfoot>` : "";
    const count = this.shown.length === this.rows.length ? `${n(this.rows.length)}` : `${n(this.shown.length)} из ${n(this.rows.length)}`;
    this.host.innerHTML = `
      <div class="tbar">
        ${o.listHtml ? `<div class="view-tabs" role="group" aria-label="Представление портфеля"><button class="btn small ${st.view !== "list" ? "primary" : ""}" data-view="table" aria-pressed="${st.view !== "list"}">Таблица</button><button class="btn small ${st.view === "list" ? "primary" : ""}" data-view="list" aria-pressed="${st.view === "list"}">Список</button></div>` : ""}
        ${o.toolsLeft || ""}
        <input class="tsearch" data-tsearch ${o.externalSearch ? 'hidden' : ''} placeholder="${esc(o.searchPlaceholder || "Найти в таблице")}" value="${esc(st.q)}">
        ${tokens}${active.length > 1 ? '<button class="ftoken quiet" data-unfilter="*">снять все</button>' : ""}
        <span class="spacer"></span>
        <span class="tcount">${count}</span>
        <button class="ticon" data-tcopy title="Скопировать показанное (вставится в Excel)"><svg viewBox="0 0 18 18" width="15" height="15"><rect x="5.5" y="5.5" width="9" height="9" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 11.5V4a.5.5 0 0 1 .5-.5h7.5" fill="none" stroke="currentColor" stroke-width="1.3"/></svg></button>
        <button class="ticon" data-txlsx title="Скачать показанное в Excel"><svg viewBox="0 0 18 18" width="15" height="15"><path d="M9 2.5v9M5.5 8 9 11.5 12.5 8M3 14.5h12" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>
      </div>
      ${o.profile ? `<details class="column-picker"><summary>Колонки и порядок</summary><div class="column-options">${[...cols, ...o.cols.filter(c => !cols.includes(c))].map(c => `<div class="row"><label class="grow"><input type="checkbox" data-column="${esc(c.key)}" ${st.hidden?.includes(c.key) ? "" : "checked"} ${c === o.cols[0] ? "disabled" : ""}> ${esc(c.title)}</label><input class="input column-width" type="number" min="80" max="800" aria-label="Ширина: ${esc(c.title)}" data-width="${esc(c.key)}" value="${st.widths?.[c.key] || parseInt(c.width || "160",10)}"><button class="btn small" data-move="${esc(c.key)}" title="Переместить выше" ${c === o.cols[0] ? "disabled" : ""}>↑</button></div>`).join("")}</div></details>` : ""}
      ${st.view === "list" && o.listHtml ? `<div class="list-filters">${cols.map(c => `<button class="btn small" data-sort="${esc(c.key)}">${esc(c.title)} ${st.sort?.key === c.key ? st.sort.dir === 1 ? "↑" : "↓" : ""}</button>${c.filter !== false ? `<button class="funnel" data-funnel="${esc(c.key)}" title="Фильтр: ${esc(c.title)}">${FUNNEL}</button>` : ""}`).join("")}</div><div class="portfolio-list">${pageRows.map(r => `<button type="button" class="portfolio-person ${st.active === o.key(r) ? "current-row" : ""}" data-key="${esc(o.key(r))}">${o.listHtml!(r)}</button>`).join("")}</div>` : `<div class="table-wrap${o.profile ? " employee-table" : ""}"><table class="grid dt">
        <thead><tr>${o.select ? `<th class="l sel"><input type="checkbox" data-pickall ${allOn ? "checked" : ""} title="Отметить показанных"></th>` : ""}${th}</tr></thead>
        <tbody>${body}</tbody>${tfoot}</table></div>`}
      ${o.pageSize && this.shown.length > pageSize ? `<div class="row table-pages"><button class="btn small" data-page="-1" ${st.page === 0 ? "disabled" : ""}>Назад</button><span class="muted">${st.page * pageSize + 1}–${Math.min((st.page + 1) * pageSize,this.shown.length)} из ${n(this.shown.length)}</span><button class="btn small" data-page="1" ${(st.page + 1) * pageSize >= this.shown.length ? "disabled" : ""}>Дальше</button></div>` : ""}
      ${this.shown.length ? "" : `<div class="empty" style="padding:28px 0"><b>${this.rows.length ? "Ничего не подошло" : "Пусто"}</b>${this.rows.length ? "Снимите часть фильтров." : esc(o.empty || "")}</div>`}`;
    this.wire();
    const again = this.host.querySelector(".table-wrap");
    if (again) { again.scrollLeft = st.scrollLeft || 0; again.scrollTop = st.scrollTop || 0; again.addEventListener("scroll", () => { st.scrollLeft = again.scrollLeft; st.scrollTop = again.scrollTop; this.persist(); }, { passive: true }); }
    this.persist();
    o.afterRender?.(this.host);
    o.onSelect?.(new Set(st.selected), this.shown);
  }

  private wire(): void {
    const h = this.host, st = this.st, o = this.o;
    const search = h.querySelector<HTMLInputElement>("[data-tsearch]")!;
    search.addEventListener("input", () => {
      st.q = search.value;
      st.page = 0;
      const pos = search.selectionStart;
      this.render();
      const again = h.querySelector<HTMLInputElement>("[data-tsearch]")!;
      again.focus(); again.setSelectionRange(pos, pos);
    });
    h.querySelectorAll<HTMLButtonElement>("[data-view]").forEach(button => button.addEventListener("click", () => { st.view = button.dataset.view as "table" | "list"; this.render(); }));
    h.querySelectorAll<HTMLButtonElement>("[data-page]").forEach(button => button.addEventListener("click", () => { st.page = (st.page || 0) + Number(button.dataset.page); this.render(); h.querySelector<HTMLInputElement>("[data-tsearch]")?.focus(); }));
    h.querySelectorAll<HTMLInputElement>("[data-column]").forEach(input => input.addEventListener("change", () => { st.hidden = this.o.cols.filter(c => c.key !== this.o.cols[0].key && (c.key === input.dataset.column ? !input.checked : st.hidden?.includes(c.key))).map(c => c.key); this.render(); }));
    h.querySelectorAll<HTMLInputElement>("[data-width]").forEach(input => input.addEventListener("change", () => { st.widths = {...st.widths, [input.dataset.width!]: Math.max(80,Math.min(800,Number(input.value)||160))}; this.render(); }));
    h.querySelectorAll<HTMLButtonElement>("[data-move]").forEach(button => button.addEventListener("click", () => { const order = [...this.columns().map(c => c.key), ...this.o.cols.filter(c => st.hidden?.includes(c.key)).map(c => c.key)]; const index = order.indexOf(button.dataset.move!); if(index > 1) [order[index-1],order[index]]=[order[index],order[index-1]]; st.order = order; this.render(); }));
    h.querySelectorAll<HTMLElement>("[data-sort]").forEach((el) => el.addEventListener("click", () => {
      const k = el.dataset.sort!;
      const c = o.cols.find((x) => x.key === k)!;
      if (st.sort?.key !== k) st.sort = { key: k, dir: c.num ? -1 : 1 };
      else if (st.sort.dir === (c.num ? -1 : 1)) st.sort = { key: k, dir: c.num ? 1 : -1 };
      else st.sort = null;
      this.render();
    }));
    h.querySelectorAll<HTMLButtonElement>("[data-funnel]").forEach((b) => b.addEventListener("click", (e) => {
      e.stopPropagation();
      this.openFilter(b.dataset.funnel!, b);
    }));
    h.querySelectorAll<HTMLButtonElement>("[data-unfilter]").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.unfilter === "*") st.filters = {}; else delete st.filters[b.dataset.unfilter!];
      this.render();
    }));
    h.querySelectorAll<HTMLInputElement>("[data-pick]").forEach((cb) => {
      cb.addEventListener("click", (e) => e.stopPropagation());
      cb.addEventListener("change", () => {
        if (cb.checked) st.selected.add(cb.dataset.pick!); else st.selected.delete(cb.dataset.pick!);
        this.render();
      });
    });
    h.querySelector<HTMLInputElement>("[data-pickall]")?.addEventListener("change", (e) => {
      const on = (e.target as HTMLInputElement).checked;
      for (const r of this.shown) { const k = o.key(r); if (on) st.selected.add(k); else st.selected.delete(k); }
      this.render();
    });
    if (o.onRow) {
      h.querySelectorAll<HTMLElement>("tbody tr[data-key]").forEach(tr => tr.addEventListener("keydown", e => {
        if (e.target === tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); tr.click(); }
      }));
      h.querySelectorAll<HTMLElement>("tbody tr[data-key],.portfolio-person[data-key]").forEach((tr) => tr.addEventListener("click", (e) => {
        if(document.querySelector('#panel-inner [data-saving="1"]')){toast("Дождитесь сохранения записи");return}
        if(!(e.target as HTMLElement).closest("input,select,a"))tr.focus({preventScroll:true});
        if (tr.matches(".portfolio-person") && (e.target as HTMLElement).closest(".portfolio-person") === tr) {
          const row = this.rows.find(x => o.key(x) === tr.dataset.key);
          if (row) { st.active = o.key(row); h.querySelectorAll(".current-row").forEach(el=>el.classList.remove("current-row"));tr.classList.add("current-row");this.persist();o.onRow!(row,tr); } return;
        }
        if ((e.target as HTMLElement).closest("input,button,a,select")) return;
        const r = this.rows.find((x) => o.key(x) === tr.dataset.key);
        if (r) { st.active = o.key(r); h.querySelectorAll(".current-row").forEach(el => el.classList.remove("current-row")); tr.classList.add("current-row"); this.persist(); o.onRow!(r, tr); }
      }));
    }
    h.querySelector("[data-tcopy]")?.addEventListener("click", async () => {
      const cols = this.columns();
      const lines = [cols.map((c) => c.title).join("\t"),
        ...this.shown.map((r) => cols.map((c) => text(c.value(r)).replace(/[\t\n]/g, " ")).join("\t"))];
      await hard.copy(lines.join("\n"));
      toast(`Скопировано строк: ${this.shown.length}`);
    });
    h.querySelector("[data-txlsx]")?.addEventListener("click", async () => {
      const cols = this.columns();
      const res = await hard.xlsx(o.exportName || o.id, cols.map((c) => c.title),
        this.shown.map((r) => cols.map((c) => { const v = c.value(r); return typeof v === "number" ? v : text(v); })));
      if (res.ok && res.file) { toast("Книга сохранена"); void hard.open(res.file); }
    });
  }

  private openFilter(key: string, anchor: HTMLElement): void {
    this.closeFilter();
    const c = this.o.cols.find((x) => x.key === key)!;
    const st = this.st;
    // Значения — по строкам, прошедшим ОСТАЛЬНЫЕ фильтры: список не предлагает
    // того, что всё равно не покажется.
    const saved = st.filters[key];
    delete st.filters[key];
    const pool = this.rows.filter((r) => this.passes(r));
    if (saved) st.filters[key] = saved;
    const counts = new Map<string, number>();
    for (const r of pool) { const v = text(c.value(r)); counts.set(v, (counts.get(v) || 0) + 1); }
    const values = [...counts.keys()].sort((a, b) => c.num ? (numOf(a) || 0) - (numOf(b) || 0) : a.localeCompare(b, "ru", { numeric: true }));
    const asRange = c.num && values.length > 12;
    const pop = document.createElement("div");
    pop.className = "fpop";
    const cur = saved?.values;
    const checked=new Set(values.filter(v=>cur?cur.has(v):!saved?.excluded?.has(v)));
    pop.innerHTML = asRange
      ? `<div class="fpop-h">${esc(c.title)}</div>
         <div class="fpop-range"><input class="input" data-min placeholder="от" value="${saved?.min ?? ""}"><input class="input" data-max placeholder="до" value="${saved?.max ?? ""}"></div>
         <div class="fpop-foot"><button class="link" data-reset>Сбросить</button><span class="spacer"></span><button class="btn small primary" data-apply>Готово</button></div>`
      : `<div class="fpop-h">${esc(c.title)}</div>
         ${values.length > 8 ? '<input class="input fpop-q" data-fq placeholder="Найти значение">' : ""}
         <div class="fpop-acts"><button class="link" data-all>все</button><button class="link" data-none>ничего</button></div>
         <div class="fpop-list"></div><div class="hint" data-filter-count></div>
         <div class="fpop-foot"><button class="link" data-reset>Сбросить</button><span class="spacer"></span><button class="btn small primary" data-apply>Готово</button></div>`;
    document.body.appendChild(pop);
    const r = anchor.getBoundingClientRect();
    const left = Math.min(window.innerWidth - pop.offsetWidth - 12, Math.max(12, r.left - 12));
    pop.style.left = `${left}px`;
    pop.style.top = `${Math.min(window.innerHeight - pop.offsetHeight - 12, r.bottom + 6)}px`;
    this.pop = pop;
    const apply = () => {
      if (asRange) {
        const mn = numOf((pop.querySelector("[data-min]") as HTMLInputElement).value);
        const mx = numOf((pop.querySelector("[data-max]") as HTMLInputElement).value);
        if (!Number.isFinite(mn) && !Number.isFinite(mx)) delete st.filters[key];
        else st.filters[key] = { min: Number.isFinite(mn) ? mn : undefined, max: Number.isFinite(mx) ? mx : undefined };
      } else {
        if (checked.size === values.length) delete st.filters[key];
        else if(checked.size>values.length/2)st.filters[key]={excluded:new Set(values.filter(v=>!checked.has(v)))};
        else st.filters[key] = { values: new Set(checked) };
      }
      this.closeFilter();
      this.render();
    };
    pop.querySelector("[data-apply]")!.addEventListener("click", apply);
    pop.querySelector("[data-reset]")!.addEventListener("click", () => { delete st.filters[key]; this.closeFilter(); this.render(); });
    const fq = pop.querySelector<HTMLInputElement>("[data-fq]");
    const matching=()=>{const q=fq?.value.trim().toLowerCase() || "";return values.filter(v=>!q||v.toLowerCase().includes(q))};
    const drawValues=()=>{if(asRange)return;const match=matching();pop.querySelector(".fpop-list")!.innerHTML=match.slice(0,500).map(v=>`<label class="fpop-row"><input type="checkbox" value="${esc(v)}" ${checked.has(v)?"checked":""}><span class="grow">${esc(v || "пусто")}</span><span class="fpop-n">${n(counts.get(v))}</span></label>`).join("");pop.querySelector("[data-filter-count]")!.textContent=match.length>500?`Показано 500 из ${n(match.length)} значений. Уточните поиск. «Все/ничего» действует на все найденные значения.`:""};
    pop.querySelector(".fpop-list")?.addEventListener("change",event=>{const input=event.target as HTMLInputElement;if(input.checked)checked.add(input.value);else checked.delete(input.value)});
    pop.querySelector("[data-all]")?.addEventListener("click",()=>{for(const v of matching())checked.add(v);drawValues()});
    pop.querySelector("[data-none]")?.addEventListener("click",()=>{for(const v of matching())checked.delete(v);drawValues()});
    fq?.addEventListener("input",drawValues);drawValues();
    (fq || pop.querySelector<HTMLInputElement>("[data-min]"))?.focus();
    pop.addEventListener("keydown", (e) => { if (e.key === "Enter") {e.preventDefault();e.stopPropagation();apply();} if (e.key === "Escape") {e.stopPropagation();this.closeFilter();} });
    this.watch=new MutationObserver(()=>{if(!this.host.isConnected)this.closeFilter()});this.watch.observe(document.body,{childList:true,subtree:true});
    setTimeout(() => document.addEventListener("mousedown", this.outside, true), 0);
  }

  private outside = (e: MouseEvent) => {
    if (this.pop && !this.pop.contains(e.target as Node)) this.closeFilter();
  };

  private closeFilter(): void {
    this.watch?.disconnect();this.watch=null;
    document.removeEventListener("mousedown", this.outside, true);
    this.pop?.remove();
    this.pop = null;
  }
}

/** Столбцы из кортежей pult.core (как у Qt-пульта): [ключ, подпись, ширина]. */
export function tupleCols(cols: [string, string, number?][], numeric: Set<string> = new Set()): Col<unknown[]>[] {
  return cols.map(([k, t], i) => ({ key: k, title: t, num: numeric.has(k), value: (r: unknown[]) => r[i] }));
}
