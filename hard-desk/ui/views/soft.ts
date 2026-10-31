// «Софт» — приложение софтника (колл-центр) на ПК. Замена личной книги Excel:
// очередь «кому звонить сегодня» (просроченный перезвон, без статуса),
// карточка с телефонами и историей звонков, фиксация звонка одним экраном.
// Гео, фото и подтверждение выезда — свойства поля, здесь их нет намеренно.
import { api, hard } from "../kit/api";
import { tr, currentLanguage, setLanguage } from "../kit/i18n";
import { esc, n, toast } from "../kit/dom";
import { currentId, openPanel, refresh as refreshSection, setHead, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { mountModeSwitchIfKit } from "../kit/modeswitch";
import { receiptHtml, wireReceipt } from "./receipt";
import { editDatesHtml } from "./edit-dates";
import { paymentHtml, wirePayment } from "./payment";
import { cardNavigation, employeeSections, lockCard, personListHtml, promiseText } from "./employee";
import { accountSectionHtml, wireAccountSection } from "./misc";
import { registryContracts } from "./registry-detail";

const state = {
  persons: [] as any[],
  dropdowns: {} as Record<string, { value: string; label: string }[]>,
  formVersion: 0,
  showAll: false,
  partner: "",
  pool: "",
  table: undefined as DataTable<any> | undefined,
};
export const softEmployeeSections=employeeSections("soft",rep=>void openSoftCard(rep));

function money(v: unknown): string {
  const num = Number(v);
  return v != null && v !== "" && Number.isFinite(num) ? num.toLocaleString("ru-RU",{maximumFractionDigits:2}) : "—";
}
function debtText(p: any): string {
  if(p.debt_known===false)return "Сумма не указана";
  return (p.totals?.length ? p.totals : [{sum:p.total,unit:p.unit}])
    .map((x:any)=>money(x.sum)+" "+(x.unit||"ед. не указана")).join(" · ");
}

function dueDays(p: any): number {
  return Number(p.overdue) || 0;
}

function phonesOf(p: any): string {
  return (p.tels || []).slice(0, 2).join(", ");
}

export const softQueue: Section = {
  id: "softqueue",
  title: "Портфель",
  icon: "phone",
  kicker: "очередь дня: просроченный перезвон, потом без статуса",
  async render(root) {
    setHead("Портфель", "кому звонить сегодня · ваше закрепление Софта");
    const [r,prefs,promises] = await Promise.all([api("/api/bootstrap", { mode: "soft" }),api("/api/local/preferences",{profile:"soft",id:"portfolio-scope"}),api("/api/workspace/promises",{profile:"soft"})]);
    if(!root.isConnected)return;
    if (!r.ok) { root.innerHTML = '<div class="empty"><b>Не загрузилось</b>' + esc(r.text || "") + "</div>"; return; }
    const d: any = r.data;
    state.persons = (d.persons || []) as any[];
    if(prefs.data?.value){state.showAll=!!prefs.data.value.showAll;state.partner=prefs.data.value.partner || "";state.pool=prefs.data.value.pool || ""}
    for(const person of state.persons)person.promises=(promises.data?.items || []).filter((p:any)=>(p.contracts || []).some((cid:string)=>(person.ids || [person.rep]).includes(cid)));
    state.dropdowns = (d.meta && d.meta.dropdowns) || {};
    state.formVersion = (d.meta && d.meta.formProtocolVersion) || 0;
    const now = new Date();
  const localToday = d.today || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const partners = [...new Set<string>(state.persons.flatMap(x => x.partners || []))].sort();
    const pools = [...new Set<string>(state.persons.flatMap(x => x.pools || []))].sort((a,b)=>a.localeCompare(b,"ru",{numeric:true}));
    if (state.partner && !partners.includes(state.partner)) state.partner = "";
    if (state.pool && !pools.includes(state.pool)) state.pool = "";
  const today = state.persons.filter(
      (x) => !x.sc || !x.scd || dueDays(x) > 0 || String(x.scd).slice(0, 10) <= localToday);
    const sums: Record<string,number>={};
    for(const p of state.persons) for(const x of p.totals?.length?p.totals:[{unit:p.unit,sum:p.total}])
      sums[x.unit||"ед. не указана"]=(sums[x.unit||"ед. не указана"]||0)+(Number(x.sum)||0);
    const debtSum=Object.entries(sums).map(([unit,sum])=>money(sum)+" "+unit).join(" · ");
    const pending = await api("/api/local/call_queue");
    const queue = pending.data as { pending?: number; errors?: { rep: string; op_id: string; error: string; body: any }[] } | undefined;
    // All originals stay in encrypted storage; one debtor occupies one row on screen.
    const errorGroups = new Map<string, NonNullable<typeof queue>["errors"]>();
    for (const item of queue?.errors || []) {
      const group = errorGroups.get(item.rep) || [];
      group.push(item); errorGroups.set(item.rep, group);
    }
    root.innerHTML = `
      ${r.stale?`<div class="notice">Портфель из локального кэша на ${esc(r.stale)}. Новые записи будут ждать сети.</div>`:""}
      ${!promises.ok?`<div class="notice">Обещания не загрузились: ${esc(promises.text || "проверьте связь или версию сервера")}</div>`:""}
      ${!pending.ok ? `<div class="notice err">${esc(pending.text || "Не удалось прочитать очередь звонков")}</div>` : queue?.pending ?
        `<div class="notice"><span class="grow">Ожидают отправки ${n(queue.pending - (queue.errors || []).length)} записей; требуют проверки ${(queue.errors || []).length}. Отправка повторяется автоматически при появлении связи.
        ${[...errorGroups].map(([rep, items]) => `<div class="queue-error"><b>${esc(rep)}</b> · ${n(items!.length)} записей требуют сверки<details><summary>Показать записи и причины</summary>${items!.map(e => `<div>${esc(e.error)} <button class="btn small" data-pending-view="${esc(e.op_id)}">Разобрать запись</button> <button class="btn small" data-pending-op="${esc(e.op_id)}">Убрать из очереди</button></div>`).join("")}</details></div>`).join("")}</span></div>` : ""}
      <div class="kpis"><div class="kpi accent"><b>${n(today.length)}</b><span>звонить сегодня</span></div>
        <div class="kpi"><b>${n(state.persons.length)}</b><span>должников всего</span></div>
        <div class="kpi money"><b>${esc(debtSum)}</b><span>долг портфеля по валютам</span></div></div>
      <div class="toolbar"><button class="btn small" data-scope="today">Сегодня (${n(today.length)})</button><button class="btn small" data-scope="all">Все (${n(state.persons.length)})</button>
        <label>Контрагент <select class="input" id="s-partner"><option value="">Все контрагенты</option>${partners.map(p => `<option value="${esc(p)}"${p === state.partner ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select></label>
        <label>Пул <select class="input" id="s-pool"><option value="">Все пулы</option>${pools.map(p => `<option value="${esc(p)}"${p === state.pool ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select></label></div>
      <div class="section" id="s-tbl"></div>`;
    root.querySelectorAll<HTMLButtonElement>("[data-pending-op]").forEach(button => button.addEventListener("click", async () => {
      if (!confirm("Убрать проблемную запись из очереди? Принятая сервером часть останется в журнале. Исправленный результат сохраните в карточке должника.")) return;
      const result = await api("/api/local/call_queue/remove", undefined, "POST", { op_id: button.dataset.pendingOp });
      if (result.ok) await softQueue.render(root, {}); else toast(result.text || "Не удалось убрать запись");
    }));
    root.querySelectorAll<HTMLButtonElement>("[data-pending-view]").forEach(button => button.addEventListener("click", () => {
      const item = queue?.errors?.find(e => e.op_id === button.dataset.pendingView);
      if (!item) return;
      const panel = openPanel(`<h2>Запись ${esc(item.rep)}</h2>`);
      panel.innerHTML = `<p class="notice">${esc(item.error)}</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(item.body, null, 2))}</pre>
        <p class="hint">Исходник остаётся в очереди. Исправление создаёт новый результат в карточке; принятая часть прежней записи сохраняется в журнале.</p>
<button class="btn" data-queue-export>${esc(tr("exportSource"))}</button><button class="btn" data-queue-fix>${esc(tr("fixCard"))}</button>`;
      panel.querySelector("[data-queue-export]")?.addEventListener("click", () => void hard.xlsx("Запись для разбора", ["Поле", "Значение"], Object.entries(item.body).map(([k,v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)])));
      panel.querySelector("[data-queue-fix]")?.addEventListener("click", async () => {
        const values = item.body.values || {};
        const fields: Record<string, string> = { soft_call_status: "c-status", soft_comment: "c-comment", soft_extra_phone: "c-extra", soft_next_call_date: "c-date", promise_date: "c-pdate", forecast_sum: "c-psum" };
        await api("/api/local/draft", undefined, "POST", { rep: item.rep, draft: { fields: Object.fromEntries(Object.entries(values).filter(([k]) => fields[k]).map(([k,v]) => [fields[k], { value: String(v), checked: false }])), touched: Object.keys(values) } });
        void openSoftCard(item.rep);
      });
    }));
    const table=new DataTable<any>(root.querySelector("#s-tbl")!, {
      id: "soft-queue", profile:"soft",pageSize:100,listHtml:personListHtml, exportName: "Очередь звонков", key: (x) => x.rep,
      searchPlaceholder:"ФИО, КД, телефон, адрес",empty:"По выбранным фильтрам должников нет.",defaultHidden:["city","contracts","address","reason"],
      cols: [
        { key: "client", title: "Должник", value: (x) => x.client,
          html: (x) => `<span class="who">${esc(x.client || "")}</span><span class="sub mono">${esc((x.tels || [])[0] || "")}</span>` },
        { key: "debt", title: "Долг по валютам", value: debtText, html:(x)=>esc(debtText(x)) },
        { key: "sc", title: "Статус", value: (x) => x.sc || "—" },
        { key: "scd", title: "Встреча/перезвон", value: (x) => String(x.scd || "").slice(0, 10) || "—" },
        { key: "partners", title: "Контрагент", value: (x) => (x.partners || []).join(', ') },
        { key: "pools", title: "Пул", value: (x) => (x.pools || []).join(', ') },
        { key: "overdue", title: "Просрочен, дн", num: true, value: (x) => (dueDays(x) > 0 ? dueDays(x) : 0),
          html: (x) => dueDays(x) > 0 ? `<b style="color:var(--bad,#B0472C)">${dueDays(x)}</b>` : "—" },
        { key: "kd", title: "Договоров", num: true, value: (x) => x.n },
        { key: "phones", title: "Телефоны", value: (x) => phonesOf(x) },
        { key: "promise", title:"Обещания",value:x=>promiseText(x.promises) },
        { key: "contracts", title:"Номера КД",value:x=>(x.ids || [x.rep]).join(", ") },
        { key: "address",title:"Адрес",value:x=>x.address || "" },
        { key: "city",title:"Город",value:x=>x.city || "" },
        { key: "reason",title:"Причина очереди",value:x=>!x.sc?"Нет результата":!x.scd?"Не назначен следующий контакт":String(x.scd).slice(0,10)<localToday?"Просрочен контакт":String(x.scd).slice(0,10)===localToday?"Контакт назначен на сегодня":"Запланирован позже" },
      ],
      onRow: (x) => { void openSoftCard(x.rep); },
    });
    state.table=table;await table.restore();if(!root.isConnected)return;
    const show=()=>{table.setRows((state.showAll?state.persons:today).filter(x => state.pool
      ? (x.pool_partners || []).some((tag:any)=>tag.pool===state.pool&&(!state.partner||tag.partner===state.partner))
      : !state.partner || (x.partners || []).includes(state.partner)));root.querySelectorAll<HTMLButtonElement>("[data-scope]").forEach(b=>b.classList.toggle("primary",(b.dataset.scope==="all")===state.showAll));void api("/api/local/preferences",undefined,"POST",{profile:"soft",id:"portfolio-scope",value:{showAll:state.showAll,partner:state.partner,pool:state.pool}});};
    root.querySelector<HTMLSelectElement>("#s-partner")?.addEventListener("change", e => { state.partner = (e.target as HTMLSelectElement).value; show(); });
    root.querySelector<HTMLSelectElement>("#s-pool")?.addEventListener("change", e => { state.pool = (e.target as HTMLSelectElement).value; show(); });
    root.querySelectorAll<HTMLButtonElement>("[data-scope]").forEach(b=>b.addEventListener("click",()=>{state.showAll=b.dataset.scope==="all";show();}));
    show();
  },
};

/** Карточка софтника: телефоны, состояние звонка, журнал и форма «Звонок». */
export async function openSoftCard(rep: string, preserveDraft = false) {
  if(document.querySelector('#panel-inner [data-saving="1"]')){toast("Дождитесь сохранения записи");return}
  const previous = document.querySelector<HTMLElement>('#panel-inner [data-soft-rep]');
  const draft = new Map<string, { value: string; checked: boolean }>();
  if (preserveDraft && previous?.dataset.softRep === rep) {
    previous.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>('[data-hard-dirty="1"]').forEach(el => {
      if (el.id) draft.set(el.id, { value: el.value, checked: el instanceof HTMLInputElement && el.checked });
    });
  }
  const panel = openPanel(`<b class="mono">${esc(rep)}</b>`);
  panel.dataset.softRep = rep;
  const r = await api("/api/person", { rep, mode:"soft" });
  if (!panel.isConnected) return;
  if (!r.ok) { panel.innerHTML = '<div class="empty"><b>Не открылось</b>' + esc(r.text || "") + "</div>"; return; }
  const d: any = r.data;
  panel.classList.add("employee-card");
  const s: any = d.soft || {};
  const notes: string[] = s.comment_entries || (s.comment ? [s.comment] : []);
  const calls: any[] = d.calls || [];
  const phones: string[] = d.phones?.length ? d.phones : [d.phone].filter(Boolean);
  const options = `<option value="">—</option>` + (state.dropdowns.soft_call_status || []).map(
    (o) => `<option value="${esc(o.value)}"${o.value === (s.status || "") ? " selected" : ""}>${esc(o.label)}</option>`).join("");
  panel.innerHTML = `
    ${editDatesHtml(d)}
    <h2 class="employee-name" style="margin:0">${esc(d.client || "")}</h2>
    <div class="muted">${esc(d.address || "адреса в реестре нет")}</div>
    <div class="kpis employee-kpis"><div class="kpi accent money"><b>${esc(debtText(d))}</b><span>долг по валютам</span></div>
      <div class="kpi"><b>${n((d.contracts || []).length)}</b><span>договоров</span></div>
      <div class="kpi"><b>${n(calls.length)}</b><span>звонков в журнале</span></div></div>
    <div class="section"><div class="section-head"><h3 class="section-title">📞 Телефоны</h3></div>
      <div class="list">${phones.map((t, index) => `
        <div class="list-row"><div class="grow"><div class="nm mono">${esc(t)}</div>
          <div class="sub">контакт из реестра</div></div><button class="btn small" data-copy-phone="${index}">Копировать</button></div>`).join("") || '<div class="muted">телефона в основных колонках нет</div>'}
      </div>${d.extra_contact ? `<div class="muted" style="margin-top:8px">Дополнительные контакты и адреса из реестра</div><div style="white-space:pre-wrap">${esc(d.extra_contact)}</div>` : ''}${s.extra_phone ? `<div class="muted" style="margin-top:8px">Сохранённые дополнительные контакты</div><div style="white-space:pre-wrap">${esc(s.extra_phone)}</div>` : ''}</div>
    ${registryContracts(d)}
    <details class="section"><summary>Оплаты из реестра</summary><div class="section-head"><h3 class="section-title">Оплаты из реестра</h3></div>
      ${(d.payments || []).map((p: any) => `<div class="list-row"><div class="grow"><div class="nm mono">${esc(p.contract_id)}</div><div class="sub">Текущие платежи: ${esc(p.current === '' || p.current == null ? '—' : Number(p.current).toLocaleString('ru-RU')+' '+(p.unit || ''))}<br>${esc(d.paymentTitles?.date || 'Дата последнего платежа')}: ${esc(p.date || '—')}</div></div><div><span class="muted">${esc(d.paymentTitles?.amount || 'Сумма последнего платежа')}</span><br>${esc(p.amount !== '' && p.amount != null ? money(p.amount) + ' ' + (p.unit || '') : '—')}</div></div>`).join('')}</details>
    <details class="section"><summary>Записать оплату вручную / внесённые оплаты</summary>${paymentHtml(d)}</details>
    <div class="section call-form"><div class="section-head"><h3 class="section-title">Записать звонок</h3><span class="tag" id="c-state">Новый звонок</span></div>
      <label class="muted" style="font-size:13px">Статус</label>
      <select class="input" id="c-status" style="width:100%">${options}</select>
      ${notes.length ? `<div class="muted" style="margin-top:10px">Последняя заметка</div><div style="white-space:pre-wrap;max-height:220px;overflow:auto;padding:8px 0">${esc(notes[0])}</div>${notes.length > 1 ? `<details><summary>Предыдущие заметки (${notes.length - 1})</summary><div style="white-space:pre-wrap;padding:8px 0">${esc(notes.slice(1).join("\n\n"))}</div></details>` : ""}` : ""}
      <label class="muted" style="font-size:13px;margin-top:8px;display:block">Новая заметка к звонку</label>
      <textarea class="input" id="c-comment" maxlength="4000" placeholder="Что выяснили при этом звонке" style="width:100%;min-height:70px"></textarea>
      <label class="muted" style="margin-top:8px;display:block">Новый дополнительный номер и контакт</label>
      <input class="input" id="c-extra" maxlength="4000" placeholder="Номер, имя, кем приходится клиенту" style="width:100%">
      <div class="row" style="margin-top:8px">
        <div><label class="muted" style="font-size:13px;display:block">Дата встречи/перезвона</label>
          <input class="input" id="c-date" type="date" value="${esc(String(s.next_call_date || "").slice(0, 10))}" style="max-width:160px"></div>
        <div><label class="muted" style="font-size:13px;display:block">Обещание: дата</label>
          <input class="input" id="c-pdate" type="date" value="${esc(d.softFormTyped?.promise_date || '')}" style="max-width:160px"></div>
        <div><label class="muted" style="font-size:13px;display:block">сумма</label>
          <input class="input" id="c-psum" inputmode="decimal" value="${esc(d.softFormTyped?.forecast_sum || '')}" placeholder="0" style="max-width:120px"></div>
      </div>
      <div class="row" style="margin-top:8px"><label class="hint"><input type="checkbox" id="c-clear-next"> Явно снять дату встречи/перезвона</label><label class="hint"><input type="checkbox" id="c-clear-promise"> Явно снять поля обещания в карточке</label></div>
      <p class="hint">Снятие полей не удаляет прежнее обещание из журнала и не меняет его PTP/KP.</p>
      <div class="card-save-actions row"><button class="btn primary" id="c-save" style="margin-top:10px">Сохранить звонок</button><button class="btn" id="c-save-next" style="margin-top:10px">Сохранить и дальше →</button><button class="btn small" id="c-reload" hidden>Обновить и сверить</button></div><p class="notice err" id="c-error" role="status" hidden></p>
      <p class="muted" style="font-size:12.5px">Дата коммуникации записывается по дню звонка, перезвон — отдельно. «Общий результат» обновляется по статусу звонка.</p></div>
    <div class="section"><div class="section-head"><h3 class="section-title">Журнал звонков</h3></div>
      ${s.history ? `<div class="muted" style="margin-bottom:6px">Историчный комментарий (Софт):</div>
        <div style="white-space:pre-wrap">${esc(s.history)}</div><hr>` : ""}
      <div class="list">${calls.length ? calls.map((c) => `
        <div class="list-row"><div class="grow"><div class="nm">${esc(String(c.at || c.ts || "").replace('T', ' ').slice(0, 16))}${c.employee ? " · " + esc(c.employee) : ""}</div>
          <div class="sub" style="white-space:pre-wrap">${esc(c.payload || "")}</div></div></div>`).join("")
        : '<div class="muted">звонков пока не было</div>'}</div></div>${receiptHtml(d, false)}`;
  cardNavigation(panel,rep,state.table,next=>void openSoftCard(next));
  panel.querySelectorAll<HTMLButtonElement>("[data-copy-phone]").forEach(button => button.addEventListener("click", async () => {
    await hard.copy(phones[Number(button.dataset.copyPhone)]);
    toast("Номер скопирован");
  }));
  const refresh = async () => {
    if (!panel.isConnected || !document.getElementById("app")?.classList.contains("with-panel")) return;
    if (currentId() === "softqueue") await refreshSection();
    if (panel.isConnected && document.getElementById("app")?.classList.contains("with-panel")) await openSoftCard(rep, true);
  };
  wireReceipt(panel, rep, d, refresh);
  wirePayment(panel, refresh, d);
  const officeNow = new Date(Date.now() + 5 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const promiseEdge = new Date(officeNow + 'T12:00:00Z');
  promiseEdge.setUTCDate(promiseEdge.getUTCDate() + 3);
  const promiseMax = promiseEdge.toISOString().slice(0, 10);
  const promiseInput = panel.querySelector<HTMLInputElement>("#c-pdate")!;
  promiseInput.min = officeNow; promiseInput.max = promiseMax;
  const touched = new Set<string>();
  const inputs = [["c-comment", "soft_comment"], ["c-extra", "soft_extra_phone"], ["c-date", "soft_next_call_date"], ["c-pdate", "promise_date"], ["c-psum", "forecast_sum"]] as const;
  for (const [id, key] of inputs) panel.querySelector(`#${id}`)?.addEventListener("input", () => touched.add(key));
  const savedDraft = await api("/api/local/draft", { rep });
  if (!panel.isConnected) return;
  if (!savedDraft.ok) toast(savedDraft.text || tr("draftError"));
  const saved = savedDraft.data?.draft;
  let expectedRevision=saved?.revision || d.office?.revision,pendingOpId="";
  if(saved?.fields)panel.querySelector("#c-state")!.textContent="Черновик";
  if (saved?.fields) for (const [id, value] of Object.entries(saved.fields) as [string, {value: string; checked: boolean}][]) {
    const el = panel.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("#" + id);
    if (el) { el.value = value.value; if (el instanceof HTMLInputElement) el.checked = value.checked; el.dataset.hardDirty = "1"; }
  }
  for (const key of saved?.touched || []) touched.add(key);
  const persist = async () => {
    const fields: Record<string, { value: string; checked: boolean }> = {};
    panel.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>('[data-hard-dirty="1"]').forEach(el => {
      if (["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) && /^(c-|p-|f-rc-)/.test(el.id)) fields[el.id] = { value: el.value, checked: el instanceof HTMLInputElement && el.checked };
    });
    const result = await api("/api/local/draft", undefined, "POST", { rep, draft: Object.keys(fields).length || pendingOpId ? { fields, touched: [...touched], revision:expectedRevision,pending_op_id:pendingOpId || undefined } : null });
    if (!result.ok) toast(result.text || tr("draftError"));
  };
  panel.addEventListener("input", event => {const target=event.target as HTMLElement;if(target.matches("input,textarea,select"))target.dataset.hardDirty="1";if(target.id.startsWith("c-"))panel.querySelector("#c-state")!.textContent="Черновик";void persist()});
  panel.addEventListener("change", event => {const target=event.target as HTMLElement;if(target.matches("input,textarea,select"))target.dataset.hardDirty="1";void persist()});
  panel.addEventListener("hard-form-saved", event => {const detail=(event as CustomEvent).detail;const ids=Array.isArray(detail)?detail:detail?.fields || [];if(!detail?.queued)pendingOpId="";for(const id of ids){const el=panel.querySelector<HTMLElement>("#"+id);if(el)delete el.dataset.hardDirty}void persist()});
  panel.addEventListener("hard-form-submitting",event=>{pendingOpId=(event as CustomEvent<{op_id:string}>).detail.op_id;void persist()});
  panel.addEventListener("hard-form-failed",()=>{pendingOpId="";void persist()});
  if(saved?.fields && d.office?.revision && (!saved.revision || saved.revision!==d.office.revision)){
    const error=panel.querySelector<HTMLElement>("#c-error")!;error.hidden=false;error.innerHTML=`Сверьте черновик с текущей карточкой: её сведения изменились или черновик создан прежней версией. Текущий результат: ${esc(s.status || "не задан")}; следующая дата: ${esc(s.next_call_date || "не задана")}<br><button class="btn small" id="c-use-current">Сверил, продолжить</button>`;
    panel.querySelector<HTMLButtonElement>("#c-save")!.disabled=true;
    panel.querySelector<HTMLButtonElement>("#p-save")!.disabled=true;
    panel.querySelector("#c-use-current")?.addEventListener("click",()=>{expectedRevision=d.office.revision;error.hidden=true;panel.querySelector<HTMLButtonElement>("#c-save")!.disabled=false;panel.querySelector<HTMLButtonElement>("#p-save")!.disabled=false;panel.querySelector<HTMLButtonElement>("#c-save-next")!.disabled=!state.table?.navigate(rep,1);void persist()});
  }
  panel.querySelector("#c-reload")?.addEventListener("click",async()=>{if(!savedDraft.data?.pending)await persist();void openSoftCard(rep,false)});
  const saveCall=async (next=false) => {
    const status = (panel.querySelector("#c-status") as HTMLSelectElement).value;
    if (!status) { toast("Выберите статус"); return; }
    const values: Record<string, string> = { soft_call_status: status };
    const comment = (panel.querySelector("#c-comment") as HTMLTextAreaElement).value.trim();
    if (comment) values.soft_comment = comment;
    const extra = (panel.querySelector("#c-extra") as HTMLInputElement).value.trim();
    if (extra) values.soft_extra_phone = extra;
    const cdate = (panel.querySelector("#c-date") as HTMLInputElement).value;
    if (touched.has("soft_next_call_date") && cdate) values.soft_next_call_date = cdate;
    const clearNext=panel.querySelector<HTMLInputElement>("#c-clear-next")!.checked;
    const clearPromise=panel.querySelector<HTMLInputElement>("#c-clear-promise")!.checked;
    if(clearNext)values.soft_next_call_date="";
    const pdate = (panel.querySelector("#c-pdate") as HTMLInputElement).value;
    if (touched.has("promise_date") && pdate && (pdate < officeNow || pdate > promiseMax)) { toast("Обещание: от сегодня до трёх дней включительно"); return; }
    if (status === "Встреча в офисе" && (!cdate || clearNext)) { toast("Укажите дату встречи в офисе"); return; }
    if (touched.has("promise_date") && pdate) values.promise_date = pdate;
    const psum = ((panel.querySelector("#c-psum") as HTMLInputElement).value || "").replace(/\s/g, "").replace(",", ".");
    if (status.startsWith("Обещает оплатить")) {
      if(clearPromise){toast("Для снятия полей обещания выберите другой результат звонка");return}
      if (!pdate || !/^\d+(?:\.\d{1,2})?$/.test(psum) || Number(psum) <= 0) { toast("Укажите дату и сумму обещания"); return; }
      if (pdate < officeNow || pdate > promiseMax) { toast("Обещание: максимум три дня от сегодня"); return; }
      values.promise_date = pdate; values.forecast_sum = psum;
    }
    if (touched.has("forecast_sum") && psum && !/^\d+(?:\.\d+)?$/.test(psum)) { toast("Проверьте сумму обещания"); return; }
    if (touched.has("forecast_sum") && psum) values.forecast_sum = psum;
    if(clearPromise){values.promise_date="";values.forecast_sum=""}
    const saveButton = panel.querySelector<HTMLButtonElement>("#c-save")!;
    const nextButton = panel.querySelector<HTMLButtonElement>("#c-save-next")!;
    if(saveButton.disabled)return;
    const destination=next?state.table?.navigate(rep,1):undefined;
    pendingOpId="desk-call-"+crypto.randomUUID();lockCard(panel,true);panel.querySelector("#c-state")!.textContent="Отправляем…";await persist();
    const res = await api("/api/save", undefined, "POST", {
      rep, values, finish: true, kind: "call", append_comment: true,
      op_id: pendingOpId,
      form_version: state.formVersion,
      input_keys: Object.keys(values),
      cleared_keys: Object.keys(values).filter(key => key !== "soft_call_status" && key !== "soft_comment" && values[key] === ""),
      occurred_at: Date.now(),
      card_revision: expectedRevision,
    });
    lockCard(panel,false);pendingOpId="";if (!panel.isConnected) return;
    saveButton.disabled = false;
    nextButton.disabled=!state.table?.navigate(rep,1);
    if (res.ok) {
      saveButton.disabled=true;nextButton.disabled=true;
      if((res.data as any)?.person?.office?.revision)expectedRevision=(res.data as any).person.office.revision;
      if (!(res.data as any)?.rejected) {panel.querySelectorAll<HTMLElement>('[id^="c-"][data-hard-dirty]').forEach(el=>delete el.dataset.hardDirty);touched.clear();await persist();}
      if ((res.data as any)?.queued) {
        (panel.querySelector("#c-comment") as HTMLTextAreaElement).value = "";
        panel.querySelector("#c-state")!.textContent="Ждёт отправки";
        panel.querySelector<HTMLElement>("#c-reload")!.hidden=false;
        toast("Звонок сохранён на компьютере; отправится при появлении связи");
        if(destination)void openSoftCard(destination.rep);return;
      }
      const rejected = (res.data as any)?.rejected || null;
      toast(rejected ? "Звонок сохранён, но часть полей не принята: " + Object.keys(rejected).join(", ")
                     : "Звонок сохранён");
      if(!rejected){panel.querySelectorAll<HTMLElement>('[id^="c-"]').forEach(el => delete el.dataset.hardDirty);(panel.querySelector("#c-comment") as HTMLTextAreaElement).value="";}
      if(!rejected&&currentId()==="softqueue")await refreshSection();
      if(destination&&!rejected){void openSoftCard(destination.rep);return}
      void openSoftCard(rep, true);
    } else {
      await persist();
      toast(res.text || "Не сохранилось");panel.querySelector("#c-state")!.textContent="Требует исправления";
      const error=panel.querySelector<HTMLElement>("#c-error")!;error.hidden=false;error.textContent=res.text || "Ввод сохранён; запись не принята";
      if(res.status===409){
        panel.querySelector<HTMLElement>("#c-reload")!.hidden=false;
        for(const id of ["c-save","c-save-next","p-save"]){
          const button=panel.querySelector<HTMLButtonElement>("#"+id);if(button)button.disabled=true;
        }
      }
    }
  };
  panel.querySelector("#c-save")?.addEventListener("click",()=>void saveCall());
  panel.querySelector("#c-save-next")?.addEventListener("click",()=>void saveCall(true));
  panel.querySelector<HTMLButtonElement>("#c-save-next")!.disabled=panel.querySelector<HTMLButtonElement>("#c-save")!.disabled||!state.table?.navigate(rep,1);
  for (const [id, value] of draft) {
    const el = panel.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("#" + id);
    if (!el) continue;
    el.value = value.value;
    if (el instanceof HTMLInputElement) el.checked = value.checked;
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  if(savedDraft.data?.pending&&!savedDraft.data.pending.error){
    pendingOpId=savedDraft.data.pending.op_id;
    panel.querySelector("#c-state")!.textContent=savedDraft.data.pending.kind==="payment"?"Оплата ждёт отправки":"Ждёт отправки";
    panel.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement>("input,textarea,select,#p-save,#f-rc-add").forEach(el=>el.disabled=true);
    panel.querySelector<HTMLButtonElement>("#c-save")!.disabled=true;panel.querySelector<HTMLButtonElement>("#c-save-next")!.disabled=true;panel.querySelector<HTMLElement>("#c-reload")!.hidden=false;
  }
}

export const softHelp: Section = {
  id: "softhelp",
  title: "Это устройство",
  icon: "control",
  kicker: "вход, выход, что здесь можно",
  async render(root) {
    setHead("Это устройство", "приложение софтника на компьютере");
    // Аккаунт «логин-пароль»: журнал «Мои входы» и смена пароля — только
    // вошедшим по логину-паролю (у ссылочного входа аккаунта на сервере нет).
    const account = (await hard.state()).device?.username || "";
    root.innerHTML = `<div class="section"><div class="grid2">
      <div class="list-row"><div class="grow"><div class="nm">Вход — по личному профилю</div>
        <div class="sub">Профиль и компьютер подтверждает ваш руководитель. При замене ПК прежний вход закрывается. Телефон продолжает работать: вход на ПК его не заменяет.</div></div></div>
      <div class="list-row"><div class="grow"><div class="nm">Очередь — вместо фильтра в Excel</div>
        <div class="sub">«Кому звонить» — просроченный перезвон и должники без статуса. Телефон должника — в строке и в карточке.</div></div></div>
      <div class="list-row"><div class="grow"><div class="nm">Очередь и сохранённый результат</div>
        <div class="sub">Без сети звонок ждёт на этом ПК. После приёма сервером аналитик переносит его в вашу книгу через «Обогащение книг», затем перенос из личных книг обновляет общий реестр. Даты и время показаны по офису (UTC+5).</div></div></div>
    </div>
    ${account ? accountSectionHtml() : ""}
    <label>Русский / O‘zbekcha / Тоҷикӣ <select class="input" id="recovery-language">${["ru", "uz", "tg"].map(lang => `<option value="${lang}" ${lang === currentLanguage() ? "selected" : ""}>${lang.toUpperCase()}</option>`).join("")}</select></label>
    <p class="hint">${esc(tr("offlinePolicy"))} ${esc(tr("retained"))}</p>
    <div class="section" id="modesec" style="display:none"><div class="section-head"><h3 class="section-title">Режимы этого exe</h3></div>
      <div id="modesw"></div></div>
    <div class="row" style="margin-top:12px"><button class="btn" id="s-logout">Выйти с этого компьютера</button></div></div>`;
    if (account) wireAccountSection(root, account);
    root.querySelector<HTMLSelectElement>("#recovery-language")?.addEventListener("change", event => { setLanguage((event.target as HTMLSelectElement).value); void softHelp.render(root, {}); });
  void mountModeSwitchIfKit(root.querySelector("#modesw")!, "field").then(() => {
    const sec = root.querySelector("#modesec") as HTMLElement | null;
    const sw = root.querySelector("#modesw") as HTMLElement | null;
    if (sec && sw && sw.innerHTML.trim()) sec.style.display = "";
  });
    root.querySelector("#s-logout")?.addEventListener("click", async () => {
      if (!confirm("Выйти? Вход и кэш закроются. Неотправленные записи и черновики сохранятся для того же сотрудника.")) return;
      const hard = await import("../kit/api").then((m) => m.hard);
      await hard.logout();
      location.reload();
    });
  },
};
