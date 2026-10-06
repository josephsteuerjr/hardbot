// «Реестр» аналитика: колонки реестра, импорт общего реестра, обогащение личных
// книг живым Excel, выгрузки, период и отозванные. Логика — в питон-стороне
// (pult/desk_registry.py), долгое идёт задачей с журналом по ходу.
import { hard } from "../kit/api";
import { ask, esc, n, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { DataTable, tupleCols } from "../kit/table";
import { adm, chipsHtml, fn, fnMap, guard, kvHtml, local, note } from "./lib";

const XLS = [{ name: "Excel", extensions: ["xlsx", "xlsm", "xlsb", "xls"] }];
const st = {
  tab: "import" as "import" | "enrich" | "columns" | "export" | "recalled",
  imp: null as any, sel: new Set<string>(), full: false, restore: false, comments: false, preview: null as any, applied: null as any,
  impQuery: "", impFilter: "all", impSort: "name",
  enr: null as any, matched: [] as any[], enrSel: new Set<string>(), since: "", until: "", check: null as any, decisions: {} as Record<string, string>, applyRes: null as any,
  cols: null as any, colDecisions: [] as any[], scan: null as any,
  expEmps: [] as any[], expSel: new Set<string>(), expSince: "", expUntil: "",
  rec: null as any, recEmp: "",
};

/** Задача питон-стороны: ждём конца, журнал — в строку статуса. */
async function job(path: string, body: unknown, log?: (s: string) => void): Promise<any> {
  const { job: id } = await local(path, body);
  for (;;) {
    await new Promise((r) => setTimeout(r, 700));
    const j = await local("/job", { id });
    if (log && j.log?.length) log(j.log[j.log.length - 1]);
    if (j.state === "done") return j.result;
    if (j.state === "failed") throw new Error(j.error || "не вышло");
  }
}

function tabs(): string {
  return chipsHtml([["import", "Импорт реестра"], ["enrich", "Обогащение книг"], ["columns", "Колонки реестра"],
    ["export", "Выгрузки и период"], ["recalled", "Отозванные"]].map(([k, t]) => ({ key: k, text: t, on: st.tab === k })), "tab");
}

function pairs(p: [string, unknown][]): string {
  return p && p.length ? kvHtml(p.map(([k, v]) => [k, typeof v === "number" ? n(v) : v])) : "";
}

// ------------------------------------------------------------------ импорт

async function importTab(el: HTMLElement) {
  const book = st.imp;
  const employees = (book?.employees || []).filter((e: any) => {
    const file = Number(e.file_count || 0), db = Number(e.db_count || 0);
    return (!st.impQuery || e.name.toLocaleLowerCase("ru").includes(st.impQuery.toLocaleLowerCase("ru"))) &&
      (st.impFilter === "all" || st.impFilter === "selected" && st.sel.has(e.key) ||
       st.impFilter === "existing" && file && db || st.impFilter === "new" && file && !db || st.impFilter === "db_only" && !file && db);
  }).sort((a: any, b: any) => {
    const numeric = st.impSort === "file" ? b.file_count - a.file_count : st.impSort === "db" ? b.db_count - a.db_count :
      st.impSort === "selected" ? Number(st.sel.has(b.key)) - Number(st.sel.has(a.key)) : 0;
    return numeric || a.name.localeCompare(b.name, "ru");
  });
  const warn: string[] = [];
  if (st.full) warn.push("ПОЛНЫЙ СРЕЗ: у выбранных сотрудников будут сняты договоры, которых нет среди их выбранных строк. Остальные не затрагиваются. Файл неполный — снимите галочку.");
  if (st.restore) warn.push("ВОЗВРАТ РУЧНЫХ СНЯТИЙ: вернутся только договоры, которые снова есть в выбранных строках с тем же сотрудником.");
  if (st.comments) warn.push("Текущие комментарии будут заменены значениями файла, включая пустые.");
  if (warn.length) warn.push("Точные числа пульт покажет в предпросмотре до записи.");
  el.innerHTML = `${note("Общий реестр: опубликованный master или утверждённое тестовое подмножество. Личные ФИО.xlsb сюда не загружаются. Сначала предпросмотр — запись отдельной кнопкой.")}
    <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-pick>Выбрать реестр…</button><span class="muted" id="imp-cap">${esc(book?.caption || "")}</span></div>
    ${book ? `<div class="toolbar"><span class="muted" style="font-size:13.5px">Тип сотрудников:</span>
        <select class="input" data-profile style="width:auto">${[["hard","Хард"],["soft","Софт"],["both","Оба типа"]].map(([k,v])=>`<option value="${k}" ${book.profile===k?'selected':''}>${v}</option>`).join("")}</select>
        <span class="muted" style="font-size:13.5px">Кого грузить:</span>
        <button class="btn small" data-sel="none">снять</button><button class="btn small" data-sel="existing">есть в БД</button><button class="btn small" data-sel="all">все</button>
        <span class="spacer"></span><span class="muted" style="font-size:13px">отмечено ${n(st.sel.size)} из ${n(book.employees.length)}</span></div>
      <div class="toolbar"><input class="input search" data-imp-query placeholder="Поиск сотрудника" value="${esc(st.impQuery)}">
        <select class="input" data-imp-filter style="width:auto">${[["all","Все сотрудники"],["selected","Отмеченные"],["existing","Есть в файле и БД"],["new","Новые для БД"],["db_only","Только в БД"]].map(([k,v])=>`<option value="${k}" ${st.impFilter===k?'selected':''}>${v}</option>`).join("")}</select>
        <select class="input" data-imp-sort style="width:auto">${[["name","По ФИО"],["file","По договорам файла"],["db","По договорам в БД"],["selected","Сначала отмеченные"]].map(([k,v])=>`<option value="${k}" ${st.impSort===k?'selected':''}>${v}</option>`).join("")}</select>
        <span class="muted">видно ${n(employees.length)}</span></div>
      <div class="grid2" style="max-height:340px;overflow:auto;border-top:var(--lw) solid var(--line)">${employees.map((e: any) => `<label class="list-row" style="cursor:pointer;padding:8px 4px">
        <input type="checkbox" data-emp="${esc(e.key)}" ${st.sel.has(e.key) ? "checked" : ""}><span class="grow"><span class="nm">${esc(e.name)}</span><br><span class="sub">${esc(e.note)}</span></span></label>`).join("")}</div>
      <div class="block">
        <label class="list-row" style="cursor:pointer"><input type="checkbox" data-opt="full" ${st.full ? "checked" : ""}><span class="grow">Для выбранных сотрудников это полный срез — снять у них договоры, которых нет в выбранных строках файла</span></label>
        <label class="list-row" style="cursor:pointer"><input type="checkbox" data-opt="restore" ${st.restore ? "checked" : ""}><span class="grow">Вернуть ранее вручную снятые договоры, если они снова есть в выбранных строках с тем же ответственным</span></label>
        <label class="list-row" style="cursor:pointer"><input type="checkbox" data-opt="comments" ${st.comments ? "checked" : ""}><span class="grow">Начало месяца: заменить комментарии выезда значениями файла (пустая ячейка очистит комментарий)</span></label>
        <p class="muted" style="font-size:13px">Историчные комментарии грузятся сами, если колонка есть в файле: значение повторяет файл точно, пустая ячейка очищает.</p></div>
      ${warn.length ? `<div class="notice" style="white-space:pre-line"><span class="grow">${esc(warn.join("\n"))}</span></div>` : ""}
      <div class="toolbar"><button class="btn primary" data-preview ${st.sel.size ? "" : "disabled"}>Предпросмотр выбранных (${n(st.sel.size)})</button>
        <button class="btn" data-apply ${st.preview && !st.preview.error ? "" : "disabled"}>${esc(st.preview?.apply || "Применить импорт")}</button>
        <span class="muted" id="imp-log"></span></div>
      ${st.preview ? (st.preview.error ? `<div class="notice err" style="white-space:pre-line"><span class="grow">${esc(st.preview.error)}</span></div>`
        : `${pairs(st.preview.pairs)}<div class="notice" style="white-space:pre-line;margin-top:12px"><span class="grow">${esc((st.preview.notes || []).join("\n"))}</span></div>`) : ""}
      ${st.applied ? `${pairs(st.applied.pairs)}<div class="notice" style="white-space:pre-line;margin-top:12px"><span class="grow">${esc((st.applied.notes || []).join("\n"))}</span></div>` : ""}` : ""}`;
  const redraw = () => importTab(el);
  const invalidate = () => { st.preview = null; st.applied = null; };
  el.querySelector<HTMLInputElement>("[data-imp-query]")?.addEventListener("input", (event) => {
    const input = event.target as HTMLInputElement;
    const pos = input.selectionStart;
    st.impQuery = input.value; void redraw();
    const next = el.querySelector<HTMLInputElement>("[data-imp-query]"); next?.focus(); next?.setSelectionRange(pos, pos);
  });
  for (const [attr, key] of [["data-imp-filter", "impFilter"], ["data-imp-sort", "impSort"]] as const) {
    el.querySelector<HTMLSelectElement>(`[${attr}]`)?.addEventListener("change", (event) => { st[key] = (event.target as HTMLSelectElement).value; void redraw(); });
  }
  el.querySelector("[data-pick]")?.addEventListener("click", guard(async () => {
    const path = await hard.pick("file", { title: "Реестр", filters: XLS });
    if (!path) return;
    const cap = el.querySelector("#imp-cap")!;
    cap.textContent = "Читаю…";
    try {
      st.imp = await job("/reg/pick", { path }, (s) => { cap.textContent = s; });
    } catch (e) {
      st.imp = null; cap.textContent = `Файл не разобран: ${(e as Error).message}`; return;
    }
    st.sel = new Set(st.imp.selected); st.full = false; st.restore = false; st.comments = false; invalidate(); redraw();
  }));
  el.querySelector<HTMLSelectElement>("[data-profile]")?.addEventListener("change", guard(async () => {
    st.imp=await local("/reg/select", {profile:(el.querySelector("[data-profile]") as HTMLSelectElement).value});
    st.sel=new Set(st.imp.selected); st.full=false; invalidate(); redraw();
  }));
  el.querySelectorAll<HTMLButtonElement>("[data-sel]").forEach((b) => b.addEventListener("click", guard(async () => {
    const r = await local("/reg/select", { mode: b.dataset.sel, full: st.full });
    st.sel = new Set(r.selected); invalidate(); redraw();
  })));
  el.querySelectorAll<HTMLInputElement>("[data-emp]").forEach((cb) => cb.addEventListener("change", guard(async () => {
    if (cb.checked) st.sel.add(cb.dataset.emp!); else st.sel.delete(cb.dataset.emp!); invalidate();
    await local("/reg/select", { selected: [...st.sel] }); redraw();
  })));
  el.querySelectorAll<HTMLInputElement>("[data-opt]").forEach((cb) => cb.addEventListener("change", () => {
    const k = cb.dataset.opt as "full" | "restore" | "comments"; st[k] = cb.checked; invalidate();
    if (k === "full" && cb.checked) toast("Полный режим включён для выбранных сотрудников");
    redraw();
  }));
  const body = () => ({ selected: [...st.sel], full: st.full, restore_manual: st.restore, replace_comments: st.comments });
  const logEl = () => el.querySelector("#imp-log")!;
  el.querySelector("[data-preview]")?.addEventListener("click", guard(async () => {
    invalidate(); logEl().textContent = "Проверяю…";
    st.preview = await job("/reg/preview", body(), (s) => { logEl().textContent = s; });
    toast(st.preview.error ? "Импорт не прошёл" : "Предпросмотр готов" + (st.full ? `: будет снято ${n(st.preview.unassigned)}` : ""));
    redraw();
  }));
  el.querySelector("[data-apply]")?.addEventListener("click", guard(async () => {
    logEl().textContent = "Применяю…";
    const r = await job("/reg/apply", body(), (s) => { logEl().textContent = s; });
    if (r.error) { st.preview = { error: r.error }; toast("Импорт не прошёл"); redraw(); return; }
    st.preview = null; st.applied = r; toast(r.toast); redraw();
  }));
}

// ------------------------------------------------------------------ обогащение

function decisionKey(c: any): string { return JSON.stringify([c.book_path || "", c.contract_id, c.key]); }

function conflictTable(list: any[], decide: boolean, labels: Record<string, string>, cols: [string, string][]): string {
  const head = cols.map(([, t]) => `<th class="l">${esc(t)}</th>`).join("") + (decide ? '<th class="l">Решение</th>' : "");
  return `<div class="table-wrap"><table class="grid"><thead><tr>${head}</tr></thead><tbody>${list.map((c) => {
    const k = decisionKey(c);
    return `<tr>${(c.row as unknown[]).map((v) => `<td class="l" style="white-space:normal">${esc(v)}</td>`).join("")}${decide ? `<td class="l">
      <select class="input" data-dec="${esc(k)}" style="height:30px">${Object.entries(labels).map(([dk, dt]) => `<option value="${dk}" ${(st.decisions[k] || "file") === dk ? "selected" : ""}>${esc(dt)}</option>`).join("")}</select></td>` : ""}</tr>`;
  }).join("")}</tbody></table></div>`;
}

async function enrichTab(el: HTMLElement) {
  st.enr ||= await local("/enr/state");
  if (!st.since && !st.until) { st.since = st.enr.since || ""; st.until = st.enr.until || ""; }
  const e = st.enr;
  const cols = e.conflict_columns as [string, string][];
  el.innerHTML = `${note("Переносит сохраненные визиты и звонки в личные книги Хард и Софт. Рабочая книга называется ФИО.xlsb; копии и датированные файлы рядом не участвуют. Правки книги защищены реестром доставки. Сначала проверка, запись — отдельной кнопкой.")}
    <div class="toolbar" style="margin-top:12px"><button class="btn" data-base>Папка с республиками…</button><span class="muted">${esc(e.base ? "база: " + e.base : "базовая папка не выбрана")}</span>
      <span class="spacer"></span><button class="btn primary" data-scan ${e.base ? "" : "disabled"}>Проверить соответствия</button></div>
    <div class="toolbar"><span class="muted" style="font-size:13.5px">Результаты с</span><input class="input" id="e-since" value="${esc(st.since)}" placeholder="ДД.ММ.ГГГГ" style="width:130px">
      <span class="muted" style="font-size:13.5px">по</span><input class="input" id="e-until" value="${esc(st.until)}" placeholder="ДД.ММ.ГГГГ" style="width:130px">
      <button class="btn small" data-today>Только сегодня</button><span class="muted" style="font-size:12.5px">пусто = без границы</span></div>
    <div id="e-scan-out">${st.scan ? `${pairs(st.scan.pairs)}${st.scan.warning ? `<div class="notice" style="white-space:pre-line;margin-top:12px"><span class="grow">${esc(st.scan.warning)}</span></div>` : ""}` : ""}</div>
    ${st.matched.length ? `<div class="toolbar"><span class="muted" style="font-size:13.5px">Кого обогащать:</span>
        <button class="btn small" data-esel="none">снять</button><button class="btn small" data-esel="all">все</button>
        <span class="spacer"></span><span class="muted" style="font-size:13px">отмечено ${n(st.enrSel.size)} из ${n(st.matched.length)}</span></div>
      <div class="grid2" style="max-height:300px;overflow:auto;border-top:var(--lw) solid var(--line)">${st.matched.map((m) => `<label class="list-row" style="cursor:pointer;padding:8px 4px">
        <input type="checkbox" data-ek="${esc(m.key)}" ${st.enrSel.has(m.key) ? "checked" : ""}><span class="grow"><span class="nm">${esc(m.employee)}</span><br><span class="sub">${esc(m.note)}</span></span></label>`).join("")}</div>
      <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-check ${st.enrSel.size ? "" : "disabled"}>Проверить конфликты (без записи)</button>
        ${e.writes ? `<button class="btn" data-apply ${st.enrSel.size ? "" : "disabled"}>Записать выбранных (${n(st.enrSel.size)})</button>` : ""}
        <span class="muted" id="e-log"></span></div>` : ""}
    ${st.check ? `<pre style="white-space:pre-wrap;font:13px/1.5 var(--mono);color:var(--ink-2)">${esc((st.check.lines || []).join("\n"))}</pre>
      ${st.check.conflicts.length ? `<div class="section"><div class="section-head"><h2 class="section-title">Конфликты — решите, что писать (${n(st.check.conflicts.length)})</h2></div>${note(e.decision_hint)}
        <div class="toolbar" style="margin-top:8px"><button class="btn small" data-dall="file">Везде оставить как в книге</button><button class="btn small" data-dall="server">Везде записать сервер</button></div>
        ${conflictTable(st.check.conflicts, true, e.decision_labels, cols)}</div>` : ""}
      ${st.check.office.length ? `<div class="section"><div class="section-head"><h2 class="section-title">Правки офиса (${n(st.check.office.length)})</h2></div>${conflictTable(st.check.office, false, {}, cols)}</div>` : ""}` : ""}
    ${st.applyRes ? `<div class="section"><div class="section-head"><h2 class="section-title">Итог записи</h2></div>
      <pre style="white-space:pre-wrap;font:13px/1.5 var(--mono);color:var(--ink-2)">${esc((st.applyRes.lines || []).join("\n"))}</pre>
      ${st.applyRes.ledger_error ? `<div class="notice err"><span class="grow">${esc(st.applyRes.ledger_error)}</span><button class="btn small" data-replay>Повторить отправку из квитанции</button></div>` : ""}</div>` : ""}`;
  const redraw = () => enrichTab(el);
  const win = () => { st.since = (el.querySelector("#e-since") as HTMLInputElement).value.trim(); st.until = (el.querySelector("#e-until") as HTMLInputElement).value.trim(); };
  el.querySelector("#e-since")?.addEventListener("change", () => { win(); st.check = null; });
  el.querySelector("#e-until")?.addEventListener("change", () => { win(); st.check = null; });
  el.querySelector("[data-today]")?.addEventListener("click", () => {
    const d = new Date(); const t = `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${d.getFullYear()}`;
    st.since = t; st.until = t; st.check = null; redraw();
  });
  el.querySelector("[data-base]")?.addEventListener("click", guard(async () => {
    const p = await hard.pick("folder", { title: "Папка, в которой лежат папки республик", defaultPath: e.base || undefined });
    if (!p) return;
    st.enr = await local("/enr/base", { path: p }); st.since = st.enr.since || ""; st.until = st.enr.until || ""; redraw();
  }));
  el.querySelector("[data-scan]")?.addEventListener("click", guard(async () => {
    const out = el.querySelector("#e-scan-out")!;
    out.innerHTML = '<p class="muted">Проверяю: данные сервера + папки сотрудников…</p>';
    const r = await job("/enr/preview", {}, (s) => { out.innerHTML = `<p class="muted">${esc(s)}</p>`; });
    st.scan = r; st.matched = r.matched; st.enrSel = new Set(r.matched.map((m: any) => m.key)); st.check = null; st.applyRes = null; redraw();
  }));
  el.querySelectorAll<HTMLButtonElement>("[data-esel]").forEach((b) => b.addEventListener("click", () => {
    st.enrSel = b.dataset.esel === "all" ? new Set(st.matched.map((m) => m.key)) : new Set(); st.check = null; redraw();
  }));
  el.querySelectorAll<HTMLInputElement>("[data-ek]").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) st.enrSel.add(cb.dataset.ek!); else st.enrSel.delete(cb.dataset.ek!); st.check = null; redraw();
  }));
  const logEl = () => el.querySelector("#e-log")!;
  el.querySelector("[data-check]")?.addEventListener("click", guard(async () => {
    win(); logEl().textContent = "Проверяю книги…";
    st.check = await job("/enr/check", { keys: [...st.enrSel], since: st.since, until: st.until }, (s) => { logEl().textContent = s; });
    st.decisions = {}; toast(`Проверено: конфликтов ${st.check.conflicts.length}`); redraw();
  }));
  el.querySelectorAll<HTMLSelectElement>("[data-dec]").forEach((s) => s.addEventListener("change", () => { st.decisions[s.dataset.dec!] = s.value; }));
  el.querySelectorAll<HTMLButtonElement>("[data-dall]").forEach((b) => b.addEventListener("click", () => {
    for (const c of st.check.conflicts) st.decisions[decisionKey(c)] = b.dataset.dall!; redraw();
  }));
  el.querySelector("[data-apply]")?.addEventListener("click", guard(async () => {
    win();
    const decisions = Object.entries(st.decisions).map(([k, d]) => { const [book_path, contract_id, key] = JSON.parse(k); return { book_path, contract_id, key, decision: d }; });
    if (st.check) for (const c of st.check.conflicts) if (!st.decisions[decisionKey(c)]) decisions.push({ book_path: c.book_path, contract_id: c.contract_id, key: c.key, decision: "file" });
    const body = { keys: [...st.enrSel], since: st.since, until: st.until, decisions };
    const conf = await local("/enr/confirm", body);
    if (conf.whole && !(await ask(e.whole_title, e.whole_text, "Вся история", true))) return;
    if (!(await ask("Обогащение реестров", conf.text, "Записать"))) return;
    logEl().textContent = "Открываю книги…";
    st.applyRes = await job("/enr/apply", body, (s) => { logEl().textContent = s; });
    st.check = null; toast(st.applyRes.toast); redraw();
  }));
  el.querySelector("[data-replay]")?.addEventListener("click", guard(async () => {
    const r = await job("/enr/replay", {}); toast(r.toast); st.applyRes.ledger_error = ""; redraw();
  }));
}

// ------------------------------------------------------------------ колонки

async function columnsTab(el: HTMLElement) {
  st.cols ||= await local("/reg/columns_reload");
  const c = st.cols;
  const opts = (c.options || []) as [string, string][];
  el.innerHTML = `${note("Что делать, когда офис переименовал колонку или добавил новую. Откройте книгу — пульт покажет, какие заголовки система не читает, и предложит связать их с полем. Правка сохраняется на сервере и действует для всех загрузок.")}
    <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-check>Проверить книгу…</button><button class="btn" data-reload>Обновить карту с сервера</button></div>
    ${c.caption ? `<p class="muted">${esc(c.caption)}</p>` : ""}${c.state ? `<div class="notice"><span class="grow">${esc(c.state)}</span></div>` : ""}
    ${(c.rows || []).length ? `<div class="table-wrap"><table class="grid"><thead><tr><th class="l">Колонка книги</th><th class="l">Что с ней делать</th><th class="l">Подпись</th></tr></thead><tbody>
      ${(c.rows as any[]).map((r, i) => `<tr><td class="l">${esc(r.title)}</td><td class="l"><select class="input" data-cd="${i}" style="height:32px;width:320px">${opts.map(([k, t]) => `<option value="${esc(k)}" ${r.decision === k ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></td>
        <td class="l"><input class="input" data-cl="${i}" value="${esc(r.label)}" style="height:32px;width:240px"></td></tr>`).join("")}</tbody></table></div>
      <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-save>Сохранить карту на сервере</button></div>` : ""}
    <p class="muted" style="font-size:13px">${esc(c.summary || "")}</p>`;
  el.querySelector("[data-check]")?.addEventListener("click", guard(async () => {
    const path = await hard.pick("file", { title: "Реестр", filters: XLS });
    if (!path) return;
    st.cols = await job("/reg/columns_check", { path }); await columnsTab(el);
  }));
  el.querySelector("[data-reload]")?.addEventListener("click", guard(async () => { st.cols = await local("/reg/columns_reload"); toast("Карта колонок обновлена"); await columnsTab(el); }));
  el.querySelector("[data-save]")?.addEventListener("click", guard(async () => {
    const decisions = (c.rows as any[]).map((r, i) => ({ title: r.title,
      decision: (el.querySelector(`[data-cd="${i}"]`) as HTMLSelectElement).value,
      label: (el.querySelector(`[data-cl="${i}"]`) as HTMLInputElement).value.trim() || r.title }));
    st.cols = await local("/reg/columns_save", { decisions }); toast("Карта колонок сохранена"); await columnsTab(el);
  }));
}

// ------------------------------------------------------------------ выгрузки и период

function toIso(v: string): string {
  const t = v.trim();
  if (!t) return "";
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t);
  if (m) return t;
  m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(t);
  if (m) return `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`;
  throw new Error(`Дата «${t}»: нужен ГГГГ-ММ-ДД или ДД.ММ.ГГГГ`);
}

async function exportTab(el: HTMLElement) {
  if (!st.expEmps.length) {
    st.expEmps = (await local("/exp/employees")).employees;
    st.expSel = new Set(st.expEmps.map((x: any) => x.key));
  }
  const per = await local("/reg/period");
  el.innerHTML = `<div class="section"><div class="section-head"><h2 class="section-title">Экспорт-слепок</h2></div>
      ${note("Новый xlsx из базы сервера: весь реестр либо выбранные сотрудники; строки можно ограничить выездами или звонками из приложения за период.")}
      <div class="toolbar" style="margin-top:10px"><button class="btn small" data-x="none">снять</button><button class="btn small" data-x="all">все</button>
        <span class="spacer"></span><span class="muted" style="font-size:13.5px">Результаты с</span><input class="input" id="x-since" value="${esc(st.expSince)}" placeholder="ДД.ММ.ГГГГ" style="width:130px">
        <span class="muted" style="font-size:13.5px">по</span><input class="input" id="x-until" value="${esc(st.expUntil)}" placeholder="ДД.ММ.ГГГГ" style="width:130px"></div>
      <div class="grid2" style="max-height:260px;overflow:auto;border-top:var(--lw) solid var(--line)">${st.expEmps.map((x: any) => `<label class="list-row" style="cursor:pointer;padding:7px 4px">
        <input type="checkbox" data-xk="${esc(x.key)}" ${st.expSel.has(x.key) ? "checked" : ""}><span class="grow">${esc(x.name)} <span class="sub">· ${n(x.count)} назнач.</span></span></label>`).join("")}</div>
      <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-export>Подготовить выгрузку…</button><button class="btn" data-markers>Метки курьеров…</button><span class="muted" id="x-log"></span></div></div>
    <div class="section"><div class="section-head"><h2 class="section-title">Период выгрузки</h2></div>
      ${note("По дате закрепления: выездники видят только клиентов из этого окна. Пусто — видны все.")}
      <div class="toolbar" style="margin-top:10px"><input class="input" id="p-from" value="${esc(per.from || "")}" placeholder="с" style="width:140px"><span class="muted">—</span>
        <input class="input" id="p-to" value="${esc(per.to || "")}" placeholder="по" style="width:140px">
        <button class="btn primary" data-pset>Применить</button><button class="btn" data-pclear>Сбросить</button></div>
      <p class="muted">${esc(per.text || "")}</p></div>`;
  const redraw = () => exportTab(el);
  el.querySelectorAll<HTMLButtonElement>("[data-x]").forEach((b) => b.addEventListener("click", () => {
    st.expSel = b.dataset.x === "all" ? new Set(st.expEmps.map((x: any) => x.key)) : new Set(); redraw();
  }));
  el.querySelectorAll<HTMLInputElement>("[data-xk]").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) st.expSel.add(cb.dataset.xk!); else st.expSel.delete(cb.dataset.xk!);
  }));
  el.querySelector("[data-export]")?.addEventListener("click", guard(async () => {
    st.expSince = (el.querySelector("#x-since") as HTMLInputElement).value.trim();
    st.expUntil = (el.querySelector("#x-until") as HTMLInputElement).value.trim();
    const log = el.querySelector("#x-log")!;
    const r = await job("/exp/prepare", { selected: [...st.expSel], since: st.expSince, until: st.expUntil }, (s) => { log.textContent = s; });
    log.textContent = "";
    if (!(await ask("Экспорт реестра", r.confirm, "Сохранить"))) return;
    const path = await hard.pick("save", { title: "Куда сохранить слепок реестра", defaultPath: "registry_export.xlsx", filters: [{ name: "Excel", extensions: ["xlsx"] }] });
    if (!path) return;
    const s = await job("/exp/save", { path }); toast(s.toast);
  }));
  el.querySelector("[data-markers]")?.addEventListener("click", guard(async () => {
    const path = await hard.pick("save", { title: "Куда сохранить метки", defaultPath: "markers.xlsx", filters: [{ name: "Excel", extensions: ["xlsx"] }] });
    if (!path) return;
    const s = await job("/exp/markers", { path }); toast(s.toast);
  }));
  el.querySelector("[data-pset]")?.addEventListener("click", guard(async () => {
    const from = toIso((el.querySelector("#p-from") as HTMLInputElement).value);
    const to = toIso((el.querySelector("#p-to") as HTMLInputElement).value);
    await local("/reg/period", { set: true, from, to }); toast("Период сохранён"); redraw();
  }));
  el.querySelector("[data-pclear]")?.addEventListener("click", guard(async () => {
    await local("/reg/period", { set: true, from: "", to: "" }); toast("Период сброшен"); redraw();
  }));
}

// ------------------------------------------------------------------ отозванные

async function recalledTab(el: HTMLElement, limit = 500) {
  const r = await local("/reg/recalled", { limit, employee: st.recEmp });
  el.innerHTML = `${note("Кого офис снял с работы колонками отзыва. Договор остаётся в базе со всей историей, но исчезает из приложения выездника. Вернётся, когда офис очистит колонки отзыва и реестр загрузят снова.")}
    <div class="toolbar" style="margin-top:10px"><select class="input" id="r-emp" style="width:300px"><option value="">все сотрудники</option>${(r.employees as string[]).map((x) => `<option ${x === st.recEmp ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
      <button class="btn small" data-all>Показать все</button></div>
    ${pairs(r.pairs)}<div style="margin-top:14px" id="rec-tbl"></div>`;
  new DataTable<unknown[]>(el.querySelector("#rec-tbl")!, { id: "analyst-recalled", cols: tupleCols(r.columns, new Set(["debt"])),
    key: (x) => String(x[0]), exportName: "Отозванные договоры" }).setRows(r.rows);
  el.querySelector<HTMLSelectElement>("#r-emp")?.addEventListener("change", (e) => { st.recEmp = (e.target as HTMLSelectElement).value; void recalledTab(el); });
  el.querySelector("[data-all]")?.addEventListener("click", () => void recalledTab(el, 5000));
}

async function draw(root: HTMLElement) {
  root.innerHTML = `<div class="toolbar">${tabs()}</div><div id="rtab"><div class="skeleton"></div></div>`;
  root.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((b) => b.addEventListener("click", () => { st.tab = b.dataset.tab as typeof st.tab; void draw(root); }));
  const el = root.querySelector<HTMLElement>("#rtab")!;
  try {
    if (st.tab === "enrich") await enrichTab(el);
    else if (st.tab === "columns") await columnsTab(el);
    else if (st.tab === "export") await exportTab(el);
    else if (st.tab === "recalled") await recalledTab(el);
    else await importTab(el);
  } catch (e) {
    el.innerHTML = `<div class="notice err"><span class="grow">${esc((e as Error).message)}</span></div>`;
  }
}

export const registry: Section = {
  id: "registry",
  title: "Реестр",
  icon: "registry",
  kicker: "импорт, обогащение книг, колонки, выгрузки",
  render(root) { return draw(root); },
};

// «Приоритет выезда»: очередь по весам — долг, свежесть, кластер.
const pr = { weights: {} as Record<string, number>, limit: 40 };
export const priority: Section = {
  id: "prio",
  title: "Приоритет",
  icon: "enrich",
  kicker: "кого навестить первым",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div><div class="skeleton" style="width:60%"></div>';
    try { await drawPriority(root); } catch (e) { root.innerHTML = `<div class="notice err"><span class="grow">${esc((e as Error).message)}</span></div>`; }
  },
};

async function drawPriority(root: HTMLElement) {
  {
    const [COLS, WEIGHTS] = await Promise.all([fn("priority.COLUMNS"), fn("priority.WEIGHTS")]);
    for (const [k, , v] of WEIGHTS as [string, string, number][]) if (pr.weights[k] === undefined) pr.weights[k] = v;
    const q = await fn<string>("priority.priority_query", pr.weights, pr.limit);
    let d: any;
    try { d = await adm("GET", "/admin/priority" + q, undefined, 60); } catch (e) { root.innerHTML = `<div class="notice err">${esc((e as Error).message)}</div>`; return; }
    const items = (d.items || []) as any[];
    const rows = await fnMap<unknown[]>("priority.priority_row", items.map((it, i) => [i + 1, it]));
    const chips = await fn<string[]>("priority.cluster_chips", d.cities || []);
    root.innerHTML = `${note("Долг и свежесть — постоянные веса; кластер — насколько важно, чтобы рядом были другие должники.")}
      <div class="toolbar" style="margin-top:10px">${(WEIGHTS as [string, string, number][]).map(([k, t]) => `<span class="muted">${esc(t)}</span>
        <input class="input" type="number" data-w="${esc(k)}" value="${pr.weights[k]}" min="0" max="100" style="width:90px">`).join("")}
        <button class="btn primary" data-go>Пересчитать</button></div>
      <div class="flags" style="margin:6px 0 12px">${chips.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
      <div id="prio-tbl"></div>`;
    new DataTable<unknown[]>(root.querySelector("#prio-tbl")!, { id: "analyst-priority",
      cols: tupleCols(COLS, new Set(["debt", "exp", "visits", "score", "n"])), key: (x) => String(x[0]) + String(x[1]),
      exportName: "Приоритет выезда" }).setRows(rows);
    root.querySelector("[data-go]")?.addEventListener("click", () => {
      root.querySelectorAll<HTMLInputElement>("[data-w]").forEach((i) => { pr.weights[i.dataset.w!] = Number(i.value) || 0; });
      void priority.render(root, {});
    });
  }
}
