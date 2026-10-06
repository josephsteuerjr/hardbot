// «Софт» — приложение софтника (колл-центр) на ПК. Замена личной книги Excel:
// очередь «кому звонить сегодня» (просроченная дата коммуникации, без статуса),
// карточка с телефонами и историей звонков, фиксация звонка одним экраном.
// Гео, фото и подтверждение выезда — свойства поля, здесь их нет намеренно.
import { api, hard } from "../kit/api";
import { esc, n, toast } from "../kit/dom";
import { openPanel, setHead, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { mountModeSwitchIfKit } from "../kit/modeswitch";
import { receiptHtml, wireReceipt } from "./receipt";

const state = {
  persons: [] as any[],
  dropdowns: {} as Record<string, { value: string; label: string }[]>,
  formVersion: 0,
  showAll: false,
};

function money(v: unknown): string {
  const num = Number(v);
  return Number.isFinite(num) && num > 0 ? num.toLocaleString("ru-RU") : "—";
}
function debtText(p: any): string {
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
  title: "Кому звонить",
  icon: "phone",
  kicker: "очередь дня: просроченный перезвон, потом без статуса",
  async render(root) {
    setHead("Кому звонить", "очередь звонков вашего закрепления (Софт)");
    const r = await api("/api/bootstrap", { mode: "soft" });
    if (!r.ok) { root.innerHTML = '<div class="empty"><b>Не загрузилось</b>' + esc(r.text || "") + "</div>"; return; }
    const d: any = r.data;
    state.persons = (d.persons || []) as any[];
    state.dropdowns = (d.meta && d.meta.dropdowns) || {};
    state.formVersion = (d.meta && d.meta.formProtocolVersion) || 0;
    const now = new Date();
  const localToday = d.today || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const today = state.persons.filter(
      (x) => !x.sc || !x.scd || dueDays(x) > 0 || String(x.scd).slice(0, 10) <= localToday);
    const sums: Record<string,number>={};
    for(const p of state.persons) for(const x of p.totals?.length?p.totals:[{unit:p.unit,sum:p.total}])
      sums[x.unit||"ед. не указана"]=(sums[x.unit||"ед. не указана"]||0)+(Number(x.sum)||0);
    const debtSum=Object.entries(sums).map(([unit,sum])=>money(sum)+" "+unit).join(" · ");
    const pending = await api("/api/local/call_queue");
    const queue = pending.data as { pending?: number; errors?: { rep: string; op_id: string; error: string }[] } | undefined;
    root.innerHTML = `
      ${!pending.ok ? `<div class="notice err">${esc(pending.text || "Не удалось прочитать очередь звонков")}</div>` : queue?.pending ?
        `<div class="notice"><span class="grow">Ожидают отправки ${n(queue.pending - (queue.errors || []).length)} звонков; требуют проверки ${(queue.errors || []).length}. Отправка повторяется автоматически при появлении связи.
        ${(queue.errors || []).map(e => `<br>${esc(e.rep)}: ${esc(e.error)} <button class="btn small" data-pending-op="${esc(e.op_id)}">Убрать из очереди</button>`).join("")}</span></div>` : ""}
      <div class="kpis"><div class="kpi accent"><b>${n(today.length)}</b><span>звонить сегодня</span></div>
        <div class="kpi"><b>${n(state.persons.length)}</b><span>должников всего</span></div>
        <div class="kpi"><b>${esc(debtSum)}</b><span>долг портфеля</span></div></div>
      <div class="toolbar"><button class="btn small" data-scope="today">Сегодня (${n(today.length)})</button><button class="btn small" data-scope="all">Все (${n(state.persons.length)})</button></div>
      <div class="section" id="s-tbl"></div>`;
    root.querySelectorAll<HTMLButtonElement>("[data-pending-op]").forEach(button => button.addEventListener("click", async () => {
      if (!confirm("Убрать проблемную запись из очереди? Принятая сервером часть останется в журнале. Исправленный результат сохраните в карточке должника.")) return;
      const result = await api("/api/local/call_queue/remove", undefined, "POST", { op_id: button.dataset.pendingOp });
      if (result.ok) await softQueue.render(root, {}); else toast(result.text || "Не удалось убрать запись");
    }));
    const table=new DataTable<any>(root.querySelector("#s-tbl")!, {
      id: "soft-queue", exportName: "Очередь звонков", key: (x) => x.rep,
      cols: [
        { key: "client", title: "Должник", value: (x) => x.client,
          html: (x) => `<span class="who">${esc(x.client || "")}</span><span class="sub mono">${esc((x.tels || [])[0] || "")}</span>` },
        { key: "debt", title: "Долг", num: true, value: (x) => x.total, html:(x)=>esc(debtText(x)) },
        { key: "sc", title: "Статус", value: (x) => x.sc || "—" },
        { key: "scd", title: "Перезвон", value: (x) => String(x.scd || "").slice(0, 10) || "—" },
        { key: "overdue", title: "Просрочен, дн", num: true, value: (x) => (dueDays(x) > 0 ? dueDays(x) : 0),
          html: (x) => dueDays(x) > 0 ? `<b style="color:var(--bad,#B0472C)">${dueDays(x)}</b>` : "—" },
        { key: "kd", title: "Договоров", num: true, value: (x) => x.n },
        { key: "phones", title: "Телефоны", value: (x) => phonesOf(x) },
      ],
      onRow: (x) => { void openSoftCard(x.rep); },
    });
    const show=()=>{table.setRows(state.showAll?state.persons:today);root.querySelectorAll<HTMLButtonElement>("[data-scope]").forEach(b=>b.classList.toggle("primary",(b.dataset.scope==="all")===state.showAll));};
    root.querySelectorAll<HTMLButtonElement>("[data-scope]").forEach(b=>b.addEventListener("click",()=>{state.showAll=b.dataset.scope==="all";show();}));
    show();
  },
};

/** Карточка софтника: телефоны, состояние звонка, журнал и форма «Звонок». */
async function openSoftCard(rep: string) {
  const panel = openPanel(`<b class="mono">${esc(rep)}</b>`);
  const r = await api("/api/person", { rep, mode:"soft" });
  if (!r.ok) { panel.innerHTML = '<div class="empty"><b>Не открылось</b>' + esc(r.text || "") + "</div>"; return; }
  const d: any = r.data;
  const s: any = d.soft || {};
  const calls: any[] = d.calls || [];
  const phones: string[] = d.phones?.length ? d.phones : [d.phone].filter(Boolean);
  const options = `<option value="">—</option>` + (state.dropdowns.soft_call_status || []).map(
    (o) => `<option value="${esc(o.value)}"${o.value === (s.status || "") ? " selected" : ""}>${esc(o.label)}</option>`).join("");
  panel.innerHTML = `
    <h2 style="margin:0">${esc(d.client || "")}</h2>
    <div class="muted">${esc(d.address || "адреса в реестре нет")}</div>
    <div class="kpis"><div class="kpi accent"><b>${esc(debtText(d))}</b><span>долг всего</span></div>
      <div class="kpi"><b>${n((d.contracts || []).length)}</b><span>договоров</span></div>
      <div class="kpi"><b>${n(calls.length)}</b><span>звонков в журнале</span></div></div>
    <div class="section"><div class="section-head"><h3 class="section-title">📞 Телефоны</h3></div>
      <div class="list">${phones.map((t, index) => `
        <div class="list-row"><div class="grow"><div class="nm mono">${esc(t)}</div>
          <div class="sub">контакт из реестра</div></div><button class="btn small" data-copy-phone="${index}">Копировать</button></div>`).join("") || '<div class="muted">телефона в основных колонках нет</div>'}
      </div>${d.extra_contact ? `<div class="muted" style="margin-top:8px">Дополнительные контакты и адреса</div><div style="white-space:pre-wrap">${esc(d.extra_contact)}</div>` : ''}</div>
    <details style="margin-bottom:12px"><summary>Договоры (${n((d.contracts || []).length)})</summary>${(d.contracts || []).map((c: any) => `<div class="list-row"><span class="mono">${esc(c.contract_id)}</span><span>${esc(c.partner || '')}</span><span>${esc(money(c.total))} ${esc(c.unit || '')}</span></div>`).join('')}</details>
    <div class="section"><div class="section-head"><h3 class="section-title">Записать звонок</h3></div>
      <label class="muted" style="font-size:13px">Статус</label>
      <select class="input" id="c-status" style="width:100%">${options}</select>
      ${s.comment ? `<details style="margin-top:10px"><summary>Накопленный комментарий</summary><div style="white-space:pre-wrap;padding:8px 0">${esc(s.comment)}</div></details>` : ""}
      <label class="muted" style="font-size:13px;margin-top:8px;display:block">Новая заметка к звонку</label>
      <textarea class="input" id="c-comment" maxlength="4000" placeholder="Что выяснили при этом звонке" style="width:100%;min-height:70px"></textarea>
      <div class="row" style="margin-top:8px">
        <div><label class="muted" style="font-size:13px;display:block">Позвонить снова</label>
          <input class="input" id="c-date" type="date" value="${esc(String(s.comm_date || "").slice(0, 10))}" style="max-width:160px"></div>
        <div><label class="muted" style="font-size:13px;display:block">Обещание: дата</label>
          <input class="input" id="c-pdate" type="date" value="${esc(d.softFormTyped?.promise_date || '')}" style="max-width:160px"></div>
        <div><label class="muted" style="font-size:13px;display:block">сумма</label>
          <input class="input" id="c-psum" inputmode="decimal" value="${esc(d.softFormTyped?.forecast_sum || '')}" placeholder="0" style="max-width:120px"></div>
      </div>
      <button class="btn primary" id="c-save" style="margin-top:10px">Сохранить звонок</button>
      <p class="muted" style="font-size:12.5px">«Общий результат» звонок не меняет — его ведёт офис. Обещание и прогноз считаются теми же правилами, что и на выезде.</p></div>
    <div class="section"><div class="section-head"><h3 class="section-title">Журнал звонков</h3></div>
      ${s.history ? `<div class="muted" style="margin-bottom:6px">Историчный комментарий (Софт):</div>
        <div style="white-space:pre-wrap">${esc(s.history)}</div><hr>` : ""}
      <div class="list">${calls.length ? calls.map((c) => `
        <div class="list-row"><div class="grow"><div class="nm">${esc(String(c.at || c.ts || "").replace('T', ' ').slice(0, 16))}${c.employee ? " · " + esc(c.employee) : ""}</div>
          <div class="sub" style="white-space:pre-wrap">${esc(c.payload || "")}</div></div></div>`).join("")
        : '<div class="muted">звонков пока не было</div>'}</div></div>${receiptHtml(d, false)}`;
  panel.querySelectorAll<HTMLButtonElement>("[data-copy-phone]").forEach(button => button.addEventListener("click", async () => {
    await hard.copy(phones[Number(button.dataset.copyPhone)]);
    toast("Номер скопирован");
  }));
  wireReceipt(panel, rep, d, () => { void openSoftCard(rep); });
  const touched = new Set<string>();
  const inputs = [["c-comment", "soft_comment"], ["c-date", "soft_comm_date"], ["c-pdate", "promise_date"], ["c-psum", "forecast_sum"]] as const;
  for (const [id, key] of inputs) panel.querySelector(`#${id}`)?.addEventListener("input", () => touched.add(key));
  panel.querySelector("#c-save")?.addEventListener("click", async () => {
    const status = (panel.querySelector("#c-status") as HTMLSelectElement).value;
    if (!status) { toast("Выберите статус"); return; }
    const values: Record<string, string> = { soft_call_status: status };
    const comment = (panel.querySelector("#c-comment") as HTMLTextAreaElement).value.trim();
    if (comment) values.soft_comment = comment;
    const cdate = (panel.querySelector("#c-date") as HTMLInputElement).value;
    if (touched.has("soft_comm_date")) values.soft_comm_date = cdate;
    const pdate = (panel.querySelector("#c-pdate") as HTMLInputElement).value;
    if (touched.has("promise_date")) values.promise_date = pdate;
    const psum = ((panel.querySelector("#c-psum") as HTMLInputElement).value || "").replace(/\s/g, "").replace(",", ".");
    if (touched.has("forecast_sum") && psum && !/^\d+(?:\.\d+)?$/.test(psum)) { toast("Проверьте сумму обещания"); return; }
    if (touched.has("forecast_sum")) values.forecast_sum = psum;
    const saveButton = panel.querySelector<HTMLButtonElement>("#c-save")!;
    saveButton.disabled = true;
    const res = await api("/api/save", undefined, "POST", {
      rep, values, finish: true, kind: "call", append_comment: true,
      op_id: "desk-call-" + crypto.randomUUID(),
      form_version: state.formVersion,
      input_keys: Object.keys(values),
      cleared_keys: Object.keys(values).filter(key => key !== "soft_call_status" && key !== "soft_comment" && values[key] === ""),
      occurred_at: Date.now(),
    });
    saveButton.disabled = false;
    if (res.ok) {
      if ((res.data as any)?.queued) {
        (panel.querySelector("#c-comment") as HTMLTextAreaElement).value = "";
        toast("Звонок сохранён на компьютере; отправится при появлении связи"); return;
      }
      const rejected = (res.data as any)?.rejected || null;
      toast(rejected ? "Звонок сохранён, но часть полей не принята: " + Object.keys(rejected).join(", ")
                     : "Звонок сохранён");
      void openSoftCard(rep);
    } else toast(res.text || "Не сохранилось");
  });
}

export const softHelp: Section = {
  id: "softhelp",
  title: "Это устройство",
  icon: "control",
  kicker: "вход, выход, что здесь можно",
  async render(root) {
    setHead("Это устройство", "приложение софтника на компьютере");
    root.innerHTML = `<div class="section"><div class="grid2">
      <div class="list-row"><div class="grow"><div class="nm">Вход — по вашей ссылке</div>
        <div class="sub">Руководитель выдаёт её в «Входе команды». Телефон продолжает работать: вход на ПК его не заменяет.</div></div></div>
      <div class="list-row"><div class="grow"><div class="nm">Очередь — вместо фильтра в Excel</div>
        <div class="sub">«Кому звонить» — просроченный перезвон и должники без статуса. Телефон должника — в строке и в карточке.</div></div></div>
      <div class="list-row"><div class="grow"><div class="nm">Очередь и сохранённый результат</div>
        <div class="sub">Без сети звонок ждёт на этом ПК. После приёма сервером аналитик переносит его в вашу книгу через «Обогащение книг», затем перенос из личных книг обновляет общий реестр. Даты и время показаны по офису (UTC+5).</div></div></div>
    </div>
    <div class="section" id="modesec" style="display:none"><div class="section-head"><h3 class="section-title">Режимы этого exe</h3></div>
      <div id="modesw"></div></div>
    <div class="row" style="margin-top:12px"><button class="btn" id="s-logout">Выйти с этого компьютера</button></div></div>`;
  void mountModeSwitchIfKit(root.querySelector("#modesw")!, "field").then(() => {
    const sec = root.querySelector("#modesec") as HTMLElement | null;
    const sw = root.querySelector("#modesw") as HTMLElement | null;
    if (sec && sw && sw.innerHTML.trim()) sec.style.display = "";
  });
    root.querySelector("#s-logout")?.addEventListener("click", async () => {
      if (!confirm("Выйти? Вход, портфель, кэш и неотправленные звонки этого компьютера сотрутся.")) return;
      const hard = await import("../kit/api").then((m) => m.hard);
      await hard.logout();
      location.reload();
    });
  },
};
