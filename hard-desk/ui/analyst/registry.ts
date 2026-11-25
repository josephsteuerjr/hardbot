// «Реестр» аналитика: колонки реестра, импорт общего реестра, обогащение личных
// книг живым Excel, выгрузки, период и отозванные. Логика — в питон-стороне
// (pult/desk_registry.py), долгое идёт задачей с журналом по ходу.
import { hard } from "../kit/api";
import { ask, esc, n, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { DataTable, tupleCols } from "../kit/table";
import { adm, chipsHtml, fn, fnMap, guard, kvHtml, local, note, runJob } from "./lib";
import { mountImportStatus } from "./import-status";

const REVIEW_PAGE_SIZE = 80;
const XLS = [{ name: "Excel", extensions: ["xlsx", "xlsm", "xlsb", "xls"] }];
const st = {
  tab: "import" as "import" | "enrich" | "columns" | "export" | "recalled",
  imp: null as any, sel: new Set<string>(), comments: false, preview: null as any, applied: null as any,
  impQuery: "", impFilter: "all", impSort: "name", excludedIds: "",
  enr: null as any, matched: [] as any[], enrSel: new Set<string>(), since: "", until: "", check: null as any, decisions: {} as Record<string, string>, applyRes: null as any,
  enrPeriodEdited: false, reviewPage: 0, replacingRun: '',
  cols: null as any, colDecisions: [] as any[], scan: null as any,
  expEmps: [] as any[], expSel: new Set<string>(), expSince: "", expUntil: "",
  rec: null as any, recEmp: "",
  busy: false, progress: "", jobStarted: 0,
};
let activeRoot: HTMLElement | null = null;
let queueTimer: ReturnType<typeof setTimeout> | undefined;
let queueGeneration = 0;
let deliveryOperation: any = null;
let deliveryKnown = false;
let deliveryReadFailed = false;
let enrichmentStatus: HTMLElement | null = null;
let importStatus: ReturnType<typeof mountImportStatus> | undefined;
let queueDetailsOpen = false;
let queueReconciliation: any = null;
let hiddenQueueRun = '';
type RegistryTab = typeof st.tab;
let embeddedTab:RegistryTab|undefined;
let presentedQueueRun = '';
const queueChoices = new Map<string,string>();
function queueChoiceKey(op: any, book: any, index: number): string { return JSON.stringify([op.id, book.review_token, book.ordinal, index]); }

function reviewRows(): any[] { return st.check ? [...st.check.conflicts, ...st.check.office] : []; }

function setReviewChoice(row: any, choice: string): void {
  st.decisions[decisionKey(row)] = choice;
  if (['promise_date','forecast_sum'].includes(row.key)) {
    for (const other of reviewRows()) if (other.book_path === row.book_path && other.contract_id === row.contract_id && ['promise_date','forecast_sum'].includes(other.key)) {
      st.decisions[decisionKey(other)] = choice;
    }
  }
}

function updateEnrichmentStatus(): void {
  if (!enrichmentStatus?.isConnected) return;
  const op = deliveryOperation;
  let text: string;
  if (st.busy) text = st.progress;
  else if (deliveryReadFailed) text = 'Состояние очереди недоступно. Повторяю проверку…';
  else if (!deliveryKnown) text = 'Проверяю состояние очереди…';
  else if (op?.processing) {
    const book = (op.books || []).find((b: any) => ['preparing', 'publishing', 'ledger'].includes(b.state));
    const steps: Record<string, string> = { preparing: 'Проверяю книгу в Excel', publishing: 'Сверяю запись', ledger: 'Подтверждаю доставку' };
    text = book ? `[${book.ordinal + 1}/${op.total}] ${steps[book.state]} · ${book.employee}`
      : `Сверяю книги и журнал. Завершено ${op.completed} из ${op.total}.`;
  } else if (st.check) text = `Проверка завершена. Расхождений: ${n(reviewRows().length)}.`;
  else if (st.scan) text = 'Подбор завершён. Можно проверить конфликты в выбранных книгах.';
  else if (op?.remaining) text = `Проход приостановлен. Завершено ${op.completed} из ${op.total}, осталось ${op.remaining}.`;
  else if (op && op.id !== hiddenQueueRun) text = `Обогащение завершено. Книг: ${op.total}. Записано ячеек: ${op.cells}.`;
  else text = 'Обогащение не запущено. Начните с проверки соответствий.';

  const label = enrichmentStatus.querySelector<HTMLElement>('[data-registry-progress]')!;
  if (label.textContent !== text) label.textContent = text;
}

async function showDeliveryQueue(root: HTMLElement, generation: number): Promise<void> {
  if (!root.isConnected || activeRoot !== root || generation !== queueGeneration) return;
  const host = root.querySelector<HTMLElement>('[data-delivery-queue]');
  if (!host) return;
  try {
    const operation = await local<any>('/enr/operation');
    if (generation !== queueGeneration || activeRoot !== root) return;
    deliveryOperation = operation?.id ? operation : null;
    deliveryKnown = true;
    deliveryReadFailed = false;
    updateEnrichmentStatus();
    if (deliveryOperation?.id !== presentedQueueRun) {
      presentedQueueRun = deliveryOperation?.id || '';
      queueDetailsOpen = false;
    }
    const signature = JSON.stringify([deliveryOperation,queueReconciliation,hiddenQueueRun]);
    if (host.dataset.signature !== signature && !(host.contains(document.activeElement) && document.activeElement?.tagName === 'SELECT')) {
      host.dataset.signature = signature;
      const op = deliveryOperation;
      host.innerHTML = !op || hiddenQueueRun===op.id ? '' : `${st.tab === 'enrich' ? '' : `<div class="notice${op.attention ? ' err' : ''}" role="status"><span class="grow">${esc(op.remaining ? `Завершено книг: ${op.completed} из ${op.total}. Осталось: ${op.remaining}.` : `Обогащение завершено. Книг: ${op.total}. Записано ячеек: ${op.cells}.`)}</span></div>`}
        <details data-queue-details ${queueDetailsOpen ? 'open' : ''}><summary>Подробности и действия${op.remaining ? ` · осталось ${n(op.remaining)}` : ''}</summary>
        <div class="toolbar">
          ${op.remaining ? '<button class="btn small" data-queue-retry>Повторить проверку ошибок</button>' : '<button class="btn small" data-queue-dismiss>Скрыть итог</button>'}
          ${op.missing ? '<button class="btn small" data-queue-reconcile>Сверить с общим реестром…</button>' : ''}
        </div>
        ${op.busy ? '<p>Открытые книги будут обработаны после закрытия Excel.</p>' : ''}
        ${op.excluded ? `<p>Пропущено строк: ${n(op.excluded)}. Эти строки сохранены в журнале.</p>` : ''}
        <div class="analyst-queue-issues">
        ${(op.books || []).filter((b: any) => b.state !== 'done' || b.omissions?.length).map((b: any) => `<div class="list-row" style="display:block;overflow-wrap:anywhere"><b>${esc(b.employee)} — ${esc(String(b.file).split(/[\\/]/).pop())}</b><p>${esc(b.message || 'Ожидает обработки')}</p>
          ${b.omissions?.length ? `<p>Пропущено строк: ${n(b.omissions.length)}. Исходные результаты сохранены в журнале операции.</p><div class="table-wrap"><table class="grid"><thead><tr><th>Договор</th><th>Причина</th></tr></thead><tbody>${b.omissions.map((row:any)=>`<tr><td>${esc(row.contract_id)}</td><td>${esc(row.reason==='absent_from_book'?'Нет строки в личной книге':row.reason==='absent_from_master'?'Договор отсутствует':row.current_owner?`Теперь: ${row.current_owner}`:'Ответственный снят')}</td></tr>`).join('')}</tbody></table></div>` : ''}
          ${b.conflicts?.length ? `<div class="table-wrap"><table class="grid"><thead><tr><th class="l">Договор / поле</th><th class="l">В книге</th><th class="l">В приложении</th><th class="l">Дата факта</th><th class="l">Действие</th></tr></thead><tbody>${b.conflicts.map((c: any, index: number) => `<tr><td>${esc(c.contract_id)} · ${esc(c.column || c.key)}</td><td>${esc(c.file)}</td><td>${esc(c.server)}</td><td>${esc(c.visit_date || '')}</td><td><select class="input" data-queue-choice="${b.ordinal}:${index}"><option value="file">Оставить в книге</option><option value="server" ${queueChoices.get(queueChoiceKey(op,b,index))==='server'?'selected':''}>Записать сервер</option></select></td></tr>`).join('')}</tbody></table></div><button class="btn small" data-queue-resolve="${b.ordinal}">Применить решения</button>` : ''}</div>`).join('')}
        </div>
        ${queueReconciliation?.run_id===op.id ? `<div class="block"><b>Сверка: ${esc(String(queueReconciliation.source_path).split(/[\\/]/).pop())}</b><p>Изменились назначения или договоры отсутствуют: ${n(queueReconciliation.proposals.length)}. Требуют проверки состава книги: ${n(queueReconciliation.blocked.length)}.</p>
          <p>Исключённые строки сохраняются в отчёте и не считаются доставленными. Остальные результаты будут перенесены.</p>
          <div class="table-wrap"><table class="grid"><thead><tr><th>Сотрудник / договор</th><th>Причина</th></tr></thead><tbody>${[...queueReconciliation.proposals,...queueReconciliation.blocked].map((row:any)=>`<tr><td>${esc(row.employee)} · ${esc(row.contract_id)}</td><td>${esc(row.reason==='absent_from_master'?'Договор отсутствует':row.reason==='assignment_changed'?(row.current_owner?`Теперь: ${row.current_owner}`:'Ответственный снят'):row.reason)}</td></tr>`).join('')}</tbody></table></div>
          <button class="btn small" data-queue-reconcile-apply ${queueReconciliation.proposals.length?'':'disabled'}>Принять исключения и продолжить перенос</button></div>` : ''}
        </details>`;
      host.querySelector('[data-queue-details]')?.addEventListener('toggle',event=>{queueDetailsOpen=(event.target as HTMLDetailsElement).open;});
      host.querySelectorAll<HTMLSelectElement>('[data-queue-choice]').forEach(select=>select.addEventListener('change',()=>{
        const [ordinal,index]=select.dataset.queueChoice!.split(':').map(Number);
        const book=op.books.find((b:any)=>b.ordinal===ordinal),row=book.conflicts[index];
        book.conflicts.forEach((other:any,i:number)=>{
          if(i===index || (other.contract_id===row.contract_id && ['promise_date','forecast_sum'].includes(row.key) && ['promise_date','forecast_sum'].includes(other.key))){
            queueChoices.set(queueChoiceKey(op,book,i),select.value);
            host.querySelector<HTMLSelectElement>(`[data-queue-choice="${ordinal}:${i}"]`)!.value=select.value;
          }
        });
      }));
      host.querySelector('[data-queue-dismiss]')?.addEventListener('click',()=>{hiddenQueueRun=op.id;host.dataset.signature='';void showDeliveryQueue(root,generation);});
      host.querySelector('[data-queue-reconcile]')?.addEventListener('click',guard(async()=>{
        const path=await hard.pick('file',{title:'Текущий общий реестр',filters:XLS});if(!path)return;
        queueReconciliation=await job('/enr/reconcile-preview',{path});queueDetailsOpen=true;
        host.dataset.signature='';await showDeliveryQueue(root,generation);
      }));
      host.querySelector('[data-queue-reconcile-apply]')?.addEventListener('click',guard(async()=>{
        await local('/enr/reconcile-apply',{token:queueReconciliation.token});queueReconciliation=null;
        host.dataset.signature='';await showDeliveryQueue(root,generation);
      }));
      host.querySelector('[data-queue-retry]')?.addEventListener('click', guard(async () => {
        await local('/enr/retry', { id: op.id });
        host.dataset.signature = ''; await showDeliveryQueue(root, generation);
      }));
      host.querySelectorAll<HTMLButtonElement>('[data-queue-resolve]').forEach(button => button.addEventListener('click', guard(async () => {
        const book = op.books.find((b: any) => b.ordinal === Number(button.dataset.queueResolve));
        const decisions = book.conflicts.map((c: any, index: number) => ({ book_path: book.file, contract_id: c.contract_id, key: c.key,
          decision: host.querySelector<HTMLSelectElement>(`[data-queue-choice="${book.ordinal}:${index}"]`)!.value }));
        await local('/enr/retry', { id: op.id, review_token: book.review_token, decisions });
        host.dataset.signature = ''; await showDeliveryQueue(root, generation);
      })));
    }
    const replacing = st.tab === 'enrich' && deliveryOperation?.restart_allowed && deliveryOperation.id === st.replacingRun;
    root.querySelectorAll<HTMLButtonElement>(st.tab === 'enrich' ? '[data-apply],[data-check],[data-scan]' : '[data-apply],[data-preview]').forEach(button => {
      const previewOnly = st.tab === 'enrich' && button.matches('[data-scan],[data-check]');
      if (deliveryOperation?.processing || deliveryOperation?.remaining && !replacing && !previewOnly) {
        if (button.dataset.deliveryBlocked === undefined) button.dataset.deliveryBlocked = button.disabled ? '1' : '0';
        button.disabled = true;
      } else if (button.dataset.deliveryBlocked !== undefined) {
        button.disabled = button.dataset.deliveryBlocked === '1'; delete button.dataset.deliveryBlocked;
      }
    });
    root.querySelectorAll<HTMLButtonElement>('[data-pick],[data-queue-reconcile],[data-queue-reconcile-apply],[data-queue-retry]').forEach(button=>{
      if(deliveryOperation?.processing){
        if(button.dataset.deliveryProcessing===undefined)button.dataset.deliveryProcessing=button.disabled?'1':'0';
        button.disabled=true;
      }else if(button.dataset.deliveryProcessing!==undefined){
        button.disabled=button.dataset.deliveryProcessing==='1';delete button.dataset.deliveryProcessing;
      }
    });
  } catch (error) {
    deliveryReadFailed = true;
    updateEnrichmentStatus();
    delete host.dataset.signature;
    host.innerHTML = `<div class="notice err">Не удалось проверить очередь переноса: ${esc(String((error as Error).message || error))}</div>`;
  } finally {
    if (root.isConnected && generation === queueGeneration && activeRoot === root) {
      clearTimeout(queueTimer); queueTimer = setTimeout(() => void showDeliveryQueue(root, generation), 2000);
    }
  }
}

function showProgress(): void {
  if (!activeRoot?.isConnected) return;
  updateEnrichmentStatus();
  importStatus?.show(st.busy ? {started: st.jobStarted, text: st.progress} : undefined);
  const status = st.tab !== 'enrich' && activeRoot.querySelector<HTMLElement>('[data-registry-progress]');
  if (status) { status.hidden = !st.busy; status.textContent = st.progress; }
  const busy = st.busy || importStatus?.working();
  activeRoot.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | HTMLButtonElement>('#rtab input,#rtab textarea,#rtab select,#rtab button').forEach(control => {
    if (busy) {
      if (control.dataset.jobDisabled === undefined) control.dataset.jobDisabled = control.disabled ? '1' : '0';
      control.disabled = true;
    } else if (control.dataset.jobDisabled !== undefined) {
      control.disabled = control.dataset.jobDisabled === '1'; delete control.dataset.jobDisabled;
    }
  });
}

/** Задача питон-стороны: ждём конца, журнал — в строку статуса. */
async function job(path: string, body: unknown, log?: (s: string) => void): Promise<any> {
  if (st.busy || importStatus?.working()) throw new Error('Операция уже выполняется. Дождитесь её результата.');
  st.busy = true;
  st.jobStarted = Date.now();
  const messages: Record<string, string> = {
    '/reg/pick': 'Читаю общий реестр…',
    '/reg/preview': 'Готовлю данные для предпросмотра импорта…',
    '/reg/apply': 'Проверяю файл перед отправкой импорта…',
    '/enr/preview': 'Запрашиваю данные для подбора книг…',
    '/enr/check': 'Запускаю проверку конфликтов…',
    '/enr/apply': 'Подготавливаю запись в книги…',
  };
  st.progress = messages[path] || 'Операция выполняется…';
  showProgress();
  try {
    return await runJob(path, body, line => {
      st.progress = line; showProgress(); log?.(line);
    });
  } finally {
    st.busy = false;
    showProgress();
    if (path === '/reg/apply') void importStatus?.refresh();
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
  warn.push("ПОЛНЫЙ СРЕЗ: у выбранных сотрудников будут сняты договоры, которых нет среди их выбранных строк. Остальные не затрагиваются.");
  warn.push("ВОЗВРАТ РУЧНЫХ СНЯТИЙ: вернутся только договоры, которые снова есть в выбранных строках с тем же сотрудником.");
  if (st.comments) warn.push("Текущие комментарии будут заменены значениями файла, включая пустые.");
  warn.push("Офисные правки результатов Софта будут применены к завершённым звонкам. Предпросмотр покажет точные замены.");
  if (warn.length) warn.push("Точные числа пульт покажет в предпросмотре до записи.");
  el.innerHTML = `${note("Выберите общий реестр для импорта. Личные книги сотрудников загружаются в разделе «Обогащение книг». Перед применением проверьте предпросмотр изменений.")}
    <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-pick>Выбрать реестр…</button><span class="muted" id="imp-cap">${esc(book?.caption || "")}</span></div>
    ${book ? `<div class="toolbar"><span class="muted" style="font-size:13.5px">Тип сотрудников:</span>
        <select class="input" data-profile style="width:auto">${[["hard","Хард"],["soft","Софт"],["both","Оба типа"]].map(([k,v])=>`<option value="${k}" ${book.profile===k?'selected':''}>${v}</option>`).join("")}</select>
        <span class="muted" style="font-size:13.5px">Кого грузить:</span>
        <button class="btn small" data-sel="none">снять</button><button class="btn small" data-sel="existing">есть в БД</button><button class="btn small" data-sel="all">все</button>
        <span class="spacer"></span><span class="muted" style="font-size:13px">отмечено ${n(st.sel.size)} из ${n(book.employees.length)}</span></div>
      <details><summary>Исключить отдельные договоры из импорта</summary><p class="hint">Введите номера в точности как в исходном файле, по одному на строку. Предпросмотр покажет число исключённых строк. Остальные данные файла сохраняются.</p>
        <textarea class="input" data-excluded-ids rows="3" aria-label="Номера договоров для исключения">${esc(st.excludedIds)}</textarea></details>
      <div class="toolbar"><input class="input search" data-imp-query placeholder="Поиск сотрудника" value="${esc(st.impQuery)}">
        <select class="input" data-imp-filter style="width:auto">${[["all","Все сотрудники"],["selected","Отмеченные"],["existing","Есть в файле и БД"],["new","Новые для БД"],["db_only","Только в БД"]].map(([k,v])=>`<option value="${k}" ${st.impFilter===k?'selected':''}>${v}</option>`).join("")}</select>
        <select class="input" data-imp-sort style="width:auto">${[["name","По ФИО"],["file","По договорам файла"],["db","По договорам в БД"],["selected","Сначала отмеченные"]].map(([k,v])=>`<option value="${k}" ${st.impSort===k?'selected':''}>${v}</option>`).join("")}</select>
        <span class="muted">видно ${n(employees.length)}</span></div>
      <div class="grid2" style="max-height:340px;overflow:auto;border-top:var(--lw) solid var(--line)">${employees.map((e: any) => `<label class="list-row" style="cursor:pointer;padding:8px 4px">
        <input type="checkbox" data-emp="${esc(e.key)}" ${st.sel.has(e.key) ? "checked" : ""}><span class="grow"><span class="nm">${esc(e.name)}</span><br><span class="sub">${esc(e.note)}</span></span></label>`).join("")}</div>
      <div class="block">
        <label class="list-row" style="cursor:pointer"><input type="checkbox" data-opt="comments" ${st.comments ? "checked" : ""}><span class="grow">Начало месяца: заменить комментарии выезда значениями файла (пустая ячейка очистит комментарий)</span></label>
        <p class="muted" style="font-size:13px">Историчные комментарии грузятся сами, если колонка есть в файле: значение повторяет файл точно, пустая ячейка очищает.</p></div>
      ${warn.length ? `<div class="notice" style="white-space:pre-line"><span class="grow">${esc(warn.join("\n"))}</span></div>` : ""}
      <div class="toolbar"><button class="btn primary" data-preview ${st.sel.size ? "" : "disabled"}>Предпросмотр выбранных (${n(st.sel.size)})</button>
        <button class="btn" data-apply ${st.preview && !st.preview.error ? "" : "disabled"}>${esc(st.preview?.apply || "Применить импорт")}</button></div>
      ${st.preview ? (st.preview.error ? `<div class="notice err" style="white-space:pre-line"><span class="grow">${esc(st.preview.error)}</span></div>`
        : `${pairs(st.preview.pairs)}<div class="notice" style="white-space:pre-line;margin-top:12px"><span class="grow">${esc((st.preview.notes || []).join("\n"))}</span></div>`) : ""}
      ${st.applied ? `${pairs(st.applied.pairs)}<div class="notice" style="white-space:pre-line;margin-top:12px"><span class="grow">${esc((st.applied.notes || []).join("\n"))}</span></div>` : ""}` : ""}`;
  const redraw = () => { void importTab(el); showProgress(); };
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
    st.sel = new Set(st.imp.selected); st.comments = false; invalidate(); redraw();
  }));
  el.querySelector<HTMLSelectElement>("[data-profile]")?.addEventListener("change", guard(async () => {
    st.imp=await local("/reg/select", {profile:(el.querySelector("[data-profile]") as HTMLSelectElement).value});
    st.sel=new Set(st.imp.selected); invalidate(); redraw();
  }));
  el.querySelectorAll<HTMLButtonElement>("[data-sel]").forEach((b) => b.addEventListener("click", guard(async () => {
    const r = await local("/reg/select", { mode: b.dataset.sel, full: true });
    st.sel = new Set(r.selected); invalidate(); redraw();
  })));
  el.querySelectorAll<HTMLInputElement>("[data-emp]").forEach((cb) => cb.addEventListener("change", guard(async () => {
    if (cb.checked) st.sel.add(cb.dataset.emp!); else st.sel.delete(cb.dataset.emp!); invalidate();
    await local("/reg/select", { selected: [...st.sel] }); redraw();
  })));
  el.querySelectorAll<HTMLInputElement>("[data-opt]").forEach((cb) => cb.addEventListener("change", () => {
    st.comments = cb.checked; invalidate();
    redraw();
  }));
  el.querySelector<HTMLTextAreaElement>('[data-excluded-ids]')?.addEventListener('input',event=>{
    st.excludedIds=(event.target as HTMLTextAreaElement).value;st.preview=null;
    el.querySelector<HTMLButtonElement>('[data-apply]')?.setAttribute('disabled','');
  });
  const body = () => ({ selected: [...st.sel], full: true, restore_manual: true, replace_comments: st.comments, soft_edits: true,
    exclude_contracts: st.excludedIds.split(/[\r\n,;]+/).map(x=>x.trim()).filter(Boolean) });
  el.querySelector("[data-preview]")?.addEventListener("click", guard(async () => {
    invalidate();
    st.preview = await job("/reg/preview", body());
    toast(st.preview.error ? "Импорт не прошёл" : `Предпросмотр готов: будет снято ${n(st.preview.unassigned)}`);
    redraw();
  }));
  el.querySelector("[data-apply]")?.addEventListener("click", guard(async () => {
    const r = await job("/reg/apply", body());
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

async function scanEnrichment(root: HTMLElement) {
  const out = root.querySelector("#e-scan-out")!;
  out.innerHTML = '<p class="muted">Проверяю: данные сервера + папки сотрудников…</p>';
  const r = await job("/enr/preview", {}, (line) => {
    out.innerHTML = `<p class="muted">${esc(line)}</p>`;
  });
  st.replacingRun = r.replaces || '';
  st.scan = r; st.matched = r.matched; st.enrSel = new Set(r.matched.map((m: any) => m.key));
  st.check = null; st.decisions = {}; st.reviewPage = 0; st.applyRes = null;
  if (activeRoot?.isConnected && st.tab === 'enrich') await draw(activeRoot);
}

async function enrichTab(el: HTMLElement) {
  if ((await hard.state()).platform === "mobile") {
    el.innerHTML = `<h2>Обогащение исходных книг</h2><p>Откройте «Обмен → Обогащение книг» в Windows-программе «Аналитик». Изменение исходных XLSB/XLSM выполняется на компьютере с Excel.</p><p>На телефоне можно импортировать реестр и скачать новые XLSX в «Книгах CRM».</p>`;
    return;
  }
  if (!st.enr) {
    st.enr = await local("/enr/state");
    st.since = st.enr.since || ""; st.until = st.enr.until || "";
  }
  const e = st.enr;
  const reviews = reviewRows();
  const firstReview = st.reviewPage * REVIEW_PAGE_SIZE;
  const visibleReviews = reviews.slice(firstReview, firstReview + REVIEW_PAGE_SIZE);
  const reviewsByKey = new Map(visibleReviews.map(row => [decisionKey(row), row]));
  const cols = e.conflict_columns as [string, string][];
  el.innerHTML = `${note("Переносит сохраненные визиты и звонки в личные книги Хард и Софт. Рабочая книга называется ФИО.xlsb; копии и датированные файлы рядом не участвуют. Правки книги защищены реестром доставки. Сначала проверка, запись — отдельной кнопкой.")}
    <div class="toolbar" style="margin-top:12px"><button class="btn" data-base>Папка с республиками…</button><span class="muted">${esc(e.base ? "база: " + e.base : "базовая папка не выбрана")}</span>
      <span class="spacer"></span><button class="btn primary" data-scan ${e.base ? "" : "disabled"}>Проверить соответствия</button></div>
    <div class="toolbar"><span class="muted" style="font-size:13.5px">Результаты с</span><input class="input" id="e-since" value="${esc(st.since)}" placeholder="ДД.ММ.ГГГГ" style="width:130px">
      <span class="muted" style="font-size:13.5px">по</span><input class="input" id="e-until" value="${esc(st.until)}" placeholder="ДД.ММ.ГГГГ" style="width:130px">
      <button class="btn small" data-day="0">Сегодня</button><button class="btn small" data-day="1">Вчера</button>
      <button class="btn small" data-day="2">Позавчера</button><span class="muted" style="font-size:12.5px">пусто = без границы</span></div>
    <div id="e-scan-out">${st.scan ? `${pairs(st.scan.pairs)}${st.scan.warning ? `<div class="notice" style="white-space:pre-line;margin-top:12px"><span class="grow">${esc(st.scan.warning)}</span></div>` : ""}` : ""}</div>
    ${st.matched.length ? `<div class="toolbar"><span class="muted" style="font-size:13.5px">Кого обогащать:</span>
        <button class="btn small" data-esel="none">снять</button><button class="btn small" data-esel="all">все</button>
        <span class="spacer"></span><span class="muted" style="font-size:13px">отмечено ${n(st.enrSel.size)} из ${n(st.matched.length)}</span></div>
      <p class="muted" style="font-size:13px">Сотрудники с результатами за всю историю. Выбранные даты ограничивают перенос фактов в книги.</p>
      <div class="grid2">${([['hard','Хард'],['soft','Софт']] as const).map(([profile,title]) => {
        const members = st.matched.filter((m) => (m.profile || (m.key.startsWith('soft:') ? 'soft' : 'hard')) === profile);
        return `<div><h3>${title} — ${n(members.length)} <span class="muted" style="font-size:13px">отмечено ${n(members.filter((m) => st.enrSel.has(m.key)).length)}</span></h3>
          <div style="max-height:260px;overflow:auto;border-top:var(--lw) solid var(--line)">${members.map((m) => `<label class="list-row" style="cursor:pointer;padding:8px 4px">
          <input type="checkbox" data-ek="${esc(m.key)}" ${st.enrSel.has(m.key) ? "checked" : ""}><span class="grow"><span class="nm">${esc(m.employee)}</span><br><span class="sub">${esc(m.note)}</span></span></label>`).join('') || '<p class="muted">Нет сотрудников с сохранёнными результатами.</p>'}</div></div>`;
      }).join('')}</div>
      <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-check ${st.enrSel.size ? "" : "disabled"}>Проверить конфликты (без записи)</button>
        ${e.writes ? `<button class="btn" data-apply ${st.enrSel.size && st.check ? "" : "disabled"}>Записать выбранных (${n(st.enrSel.size)})</button>` : ""}
        <span class="muted" id="e-log"></span></div>` : ""}
    <div data-enr-review>${st.check ? `<pre style="white-space:pre-wrap;font:13px/1.5 var(--mono);color:var(--ink-2)">${esc((st.check.lines || []).join("\n"))}</pre>
      ${reviews.length ? `<div class="section"><div class="section-head"><h2 class="section-title">Выберите значения до записи (${n(reviews.length)})</h2></div>${note(e.decision_hint)}<p class="muted">Сумма и дата обещания сохраняются вместе.</p>
        <div class="toolbar" style="margin-top:8px"><button class="btn small" data-dall="file">Везде оставить как в книге</button><button class="btn small" data-dall="server">Везде записать сервер</button></div>
        ${reviews.length > REVIEW_PAGE_SIZE ? `<div class="toolbar"><button class="btn small" data-review-page="-1" ${firstReview ? "" : "disabled"}>Назад</button>
          <span role="status">Строки ${n(firstReview + 1)}–${n(firstReview + visibleReviews.length)} из ${n(reviews.length)}</span>
          <button class="btn small" data-review-page="1" ${firstReview + visibleReviews.length < reviews.length ? "" : "disabled"}>Далее</button></div>` : ""}
        ${conflictTable(visibleReviews, true, e.decision_labels, cols)}</div>` : ""}` : ""}</div>
    ${st.applyRes ? `<div class="section"><div class="section-head"><h2 class="section-title">Итог записи</h2></div>
      <pre style="white-space:pre-wrap;font:13px/1.5 var(--mono);color:var(--ink-2)">${esc((st.applyRes.lines || []).join("\n"))}</pre>
      ${st.applyRes.ledger_error ? `<div class="notice err"><span class="grow">${esc(st.applyRes.ledger_error)}</span><button class="btn small" data-replay>Повторить отправку из квитанции</button></div>` : ""}</div>` : ""}`;
  const redraw = () => {
    if (el.isConnected) return enrichTab(el);
    if (activeRoot?.isConnected && st.tab === 'enrich') return draw(activeRoot);
  };
  const win = () => { st.since = (el.querySelector("#e-since") as HTMLInputElement).value.trim(); st.until = (el.querySelector("#e-until") as HTMLInputElement).value.trim(); };
  const clearReview = () => {
    st.check = null; st.decisions = {}; st.reviewPage = 0;
    el.querySelector("[data-enr-review]")?.replaceChildren();
    el.querySelector<HTMLButtonElement>("[data-apply]")?.setAttribute("disabled", "");
  };
  el.querySelectorAll<HTMLInputElement>("#e-since, #e-until").forEach(input => {
    input.addEventListener("input", () => {
      win(); st.enrPeriodEdited = true; clearReview();
    });
  });
  el.querySelectorAll<HTMLButtonElement>("[data-day]").forEach(button => button.addEventListener("click", () => {
    const d = new Date();
    d.setDate(d.getDate() - Number(button.dataset.day));
    const day = `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${d.getFullYear()}`;
    st.since = day; st.until = day; st.enrPeriodEdited = true; clearReview(); redraw();
  }));
  el.querySelector("[data-base]")?.addEventListener("click", guard(async () => {
    win();
    const p = await hard.pick("folder", { title: "Папка, в которой лежат папки республик", defaultPath: e.base || undefined });
    if (!p) return;
    st.enr = await local("/enr/base", { path: p });
    if (st.enr.base !== e.base) {
      st.scan = null;
      st.matched = [];
      st.enrSel.clear();
      clearReview();
      st.applyRes = null;
    }
    if (!e.base && !st.enrPeriodEdited) {
      st.since = st.enr.since || ""; st.until = st.enr.until || "";
    }
    redraw();
  }));
  el.querySelector("[data-scan]")?.addEventListener("click", guard(async () => {
    if (activeRoot) await scanEnrichment(activeRoot);
  }));
  el.querySelectorAll<HTMLButtonElement>("[data-esel]").forEach((b) => b.addEventListener("click", () => {
    st.enrSel = b.dataset.esel === "all" ? new Set(st.matched.map((m) => m.key)) : new Set(); clearReview(); redraw();
  }));
  el.querySelectorAll<HTMLInputElement>("[data-ek]").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) st.enrSel.add(cb.dataset.ek!); else st.enrSel.delete(cb.dataset.ek!); clearReview(); redraw();
  }));
  const logEl = () => el.querySelector("#e-log")!;
  el.querySelector("[data-check]")?.addEventListener("click", guard(async () => {
    win(); clearReview(); logEl().textContent = "Проверяю книги…";
    st.check = await job("/enr/check", { keys: [...st.enrSel], since: st.since, until: st.until }, (s) => { logEl().textContent = s; });
    toast(`Проверено: расхождений ${reviewRows().length}`); redraw();
  }));
  el.querySelectorAll<HTMLSelectElement>("[data-dec]").forEach((s) => s.addEventListener("change", () => {
    const row = reviewsByKey.get(s.dataset.dec!);
    if(row)setReviewChoice(row,s.value);
    el.querySelectorAll<HTMLSelectElement>('[data-dec]').forEach(other=>{other.value=st.decisions[other.dataset.dec!] || 'file';});
  }));
  el.querySelectorAll<HTMLButtonElement>("[data-dall]").forEach((b) => b.addEventListener("click", () => {
    for (const row of reviews) st.decisions[decisionKey(row)] = b.dataset.dall!;
    redraw();
  }));
  el.querySelectorAll<HTMLButtonElement>("[data-review-page]").forEach(button => button.addEventListener("click", () => {
    st.reviewPage += Number(button.dataset.reviewPage);
    redraw();
  }));
  el.querySelector("[data-apply]")?.addEventListener("click", guard(async () => {
    win();
    const decisions = Object.entries(st.decisions).map(([k, d]) => { const [book_path, contract_id, key] = JSON.parse(k); return { book_path, contract_id, key, decision: d }; });
    if (st.check) for (const c of reviewRows()) if (!st.decisions[decisionKey(c)]) decisions.push({ book_path: c.book_path, contract_id: c.contract_id, key: c.key, decision: "file" });
    const body = { keys: [...st.enrSel], since: st.since, until: st.until, decisions };
    const conf = await local("/enr/confirm", body);
    if (conf.whole && !(await ask(e.whole_title, e.whole_text, "Вся история", true))) return;
    if (!(await ask("Обогащение реестров", conf.text, "Записать"))) return;
    logEl().textContent = "Открываю книги…";
    st.applyRes = await job("/enr/apply", body, (s) => { logEl().textContent = s; });
    st.replacingRun = ""; clearReview(); toast(st.applyRes.toast); redraw();
    if (activeRoot) await showDeliveryQueue(activeRoot, queueGeneration);
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
  activeRoot = root;
  clearTimeout(queueTimer); const generation = ++queueGeneration;
  enrichmentStatus?.remove();
  enrichmentStatus = null;
  importStatus?.dispose();
  importStatus = undefined;
  deliveryKnown = false; deliveryReadFailed = false;
  const enrich = st.tab === 'enrich';
  const header = document.querySelector<HTMLElement>('main > .workspace');
  root.innerHTML = `${embeddedTab ? '' : `<div class="toolbar">${tabs()}</div>`}${enrich || st.tab === 'import' ? '' : '<p class="notice" data-registry-progress role="status" hidden></p>'}<div data-delivery-queue></div><div id="rtab"><div class="skeleton"></div></div>`;
  if (st.tab === 'import') importStatus = mountImportStatus(header || root, showProgress);
  if (enrich) {
    const panel = document.createElement('div');
    panel.className = 'analyst-enrichment-status';
    panel.innerHTML = '<span data-registry-progress role="status"></span>';
    enrichmentStatus = panel;
    if (header) header.append(panel); else root.prepend(panel);
    updateEnrichmentStatus();
  }
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
  showProgress();
  await showDeliveryQueue(root,generation);
}

export const registry: Section = {
  id: "registry",
  title: "Реестр",
  icon: "registry",
  kicker: "импорт, обогащение книг, колонки, выгрузки",
  render(root) { return draw(root); },
  leave() { enrichmentStatus?.remove(); enrichmentStatus = null; importStatus?.dispose(); importStatus = undefined; },
};

export async function mountRegistryTools(root:HTMLElement,tab:RegistryTab):Promise<()=>void>{
  embeddedTab=tab;
  st.tab=tab;
  await draw(root);
  return()=>{if(activeRoot===root){activeRoot=null;queueGeneration++;clearTimeout(queueTimer);embeddedTab=undefined;registry.leave?.();}};
}

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
