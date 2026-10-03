// «Софт» — приложение софтника (колл-центр) на ПК. Замена личной книги Excel:
// очередь «кому звонить сегодня» (просроченная дата коммуникации, без статуса),
// карточка с телефонами и историей звонков, фиксация звонка одним экраном.
// Гео, фото и подтверждение выезда — свойства поля, здесь их нет намеренно.
import { api } from "../kit/api";
import { esc, n, toast } from "../kit/dom";
import { openPanel, setHead, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { mountModeSwitchIfKit } from "../kit/modeswitch";

const state = {
  persons: [] as any[],
  dropdowns: {} as Record<string, { value: string; label: string }[]>,
  formVersion: 0,
};

function money(v: unknown): string {
  const num = Number(v);
  return Number.isFinite(num) && num > 0 ? num.toLocaleString("ru-RU") : "—";
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
  const localToday = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const today = state.persons.filter(
      (x) => !x.sc || !x.scd || dueDays(x) > 0 || String(x.scd).slice(0, 10) <= localToday);
    const debtSum = state.persons.reduce((s, x) => s + (Number(x.total) || 0), 0);
    root.innerHTML = `
      <div class="kpis"><div class="kpi accent"><b>${n(today.length)}</b><span>звонить сегодня</span></div>
        <div class="kpi"><b>${n(state.persons.length)}</b><span>должников всего</span></div>
        <div class="kpi"><b>${money(debtSum)}</b><span>долг портфеля</span></div></div>
      <div class="section" id="s-tbl"></div>`;
    new DataTable<any>(root.querySelector("#s-tbl")!, {
      id: "soft-queue", exportName: "Очередь звонков", key: (x) => x.rep,
      cols: [
        { key: "client", title: "Должник", value: (x) => x.client,
          html: (x) => `<span class="who">${esc(x.client || "")}</span><span class="sub mono">${esc((x.tels || [])[0] || "")}</span>` },
        { key: "debt", title: "Долг", num: true, value: (x) => x.total },
        { key: "sc", title: "Статус", value: (x) => x.sc || "—" },
        { key: "scd", title: "Перезвон", value: (x) => String(x.scd || "").slice(0, 10) || "—" },
        { key: "overdue", title: "Просрочен, дн", num: true, value: (x) => (dueDays(x) > 0 ? dueDays(x) : 0),
          html: (x) => dueDays(x) > 0 ? `<b style="color:var(--bad,#B0472C)">${dueDays(x)}</b>` : "—" },
        { key: "kd", title: "Договоров", num: true, value: (x) => x.n },
        { key: "phones", title: "Телефоны", value: (x) => phonesOf(x) },
      ],
      onRow: (x) => { void openSoftCard(x.rep); },
    }).setRows(state.persons);
  },
};

/** Карточка софтника: телефоны, состояние звонка, журнал и форма «Звонок». */
async function openSoftCard(rep: string) {
  const panel = openPanel(`<b class="mono">${esc(rep)}</b>`);
  const r = await api("/api/person", { rep });
  if (!r.ok) { panel.innerHTML = '<div class="empty"><b>Не открылось</b>' + esc(r.text || "") + "</div>"; return; }
  const d: any = r.data;
  const s: any = d.soft || {};
  const calls: any[] = d.calls || [];
  const options = `<option value="">—</option>` + (state.dropdowns.soft_call_status || []).map(
    (o) => `<option value="${esc(o.value)}"${o.value === (s.status || "") ? " selected" : ""}>${esc(o.label)}</option>`).join("");
  panel.innerHTML = `
    <h2 style="margin:0">${esc(d.client || "")}</h2>
    <div class="muted">${esc(d.address || "адреса в реестре нет")}</div>
    <div class="kpis"><div class="kpi accent"><b>${money(d.total)}</b><span>долг всего</span></div>
      <div class="kpi"><b>${n((d.contracts || []).length)}</b><span>договоров</span></div>
      <div class="kpi"><b>${n(calls.length)}</b><span>звонков в журнале</span></div></div>
    <div class="section"><div class="section-head"><h3 class="section-title">📞 Телефоны</h3></div>
      <div class="list">${[d.phone].filter(Boolean).map((t) => `
        <div class="list-row"><div class="grow"><div class="nm mono">${esc(t)}</div>
          <div class="sub">основной номер реестра</div></div></div>`).join("") || '<div class="muted">основного номера нет — смотрите «Доп. адрес / номер» в карточке</div>'}
      </div></div>
    <div class="section"><div class="section-head"><h3 class="section-title">Записать звонок</h3></div>
      <label class="muted" style="font-size:13px">Статус</label>
      <select class="input" id="c-status" style="width:100%">${options}</select>
      <label class="muted" style="font-size:13px;margin-top:8px;display:block">Комментарий месяца</label>
      <textarea class="input" id="c-comment" style="width:100%;min-height:70px">${esc(s.comment || "")}</textarea>
      <div class="row" style="margin-top:8px">
        <div><label class="muted" style="font-size:13px;display:block">Позвонить снова</label>
          <input class="input" id="c-date" type="date" value="${esc(String(s.comm_date || "").slice(0, 10))}" style="max-width:160px"></div>
        <div><label class="muted" style="font-size:13px;display:block">Обещание: дата</label>
          <input class="input" id="c-pdate" type="date" style="max-width:160px"></div>
        <div><label class="muted" style="font-size:13px;display:block">сумма</label>
          <input class="input" id="c-psum" inputmode="numeric" placeholder="0" style="max-width:120px"></div>
      </div>
      <button class="btn primary" id="c-save" style="margin-top:10px">Сохранить звонок</button>
      <p class="muted" style="font-size:12.5px">«Общий результат» звонок не меняет — его ведёт офис. Обещание и прогноз считаются теми же правилами, что и на выезде.</p></div>
    <div class="section"><div class="section-head"><h3 class="section-title">Журнал звонков</h3></div>
      ${s.history ? `<div class="muted" style="margin-bottom:6px">Историчный комментарий (Софт):</div>
        <div style="white-space:pre-wrap">${esc(s.history)}</div><hr>` : ""}
      <div class="list">${calls.length ? calls.map((c) => `
        <div class="list-row"><div class="grow"><div class="nm">${esc(String(c.ts || "").slice(0, 10))}${c.employee ? " · " + esc(c.employee) : ""}</div>
          <div class="sub" style="white-space:pre-wrap">${esc(c.payload || "")}</div></div></div>`).join("")
        : '<div class="muted">звонков пока не было</div>'}</div></div>`;
  panel.querySelector("#c-save")?.addEventListener("click", async () => {
    const status = (panel.querySelector("#c-status") as HTMLSelectElement).value;
    if (!status) { toast("Выберите статус"); return; }
    const values: Record<string, string> = { soft_call_status: status };
    const comment = (panel.querySelector("#c-comment") as HTMLTextAreaElement).value.trim();
    if (comment) values.soft_comment = comment;
    const cdate = (panel.querySelector("#c-date") as HTMLInputElement).value;
    if (cdate) values.soft_comm_date = cdate;
    const pdate = (panel.querySelector("#c-pdate") as HTMLInputElement).value;
    if (pdate) values.promise_date = pdate;
    const psum = ((panel.querySelector("#c-psum") as HTMLInputElement).value || "").replace(/\D/g, "");
    if (psum) values.forecast_sum = psum;
    const res = await api("/api/save", undefined, "POST", {
      rep, values, finish: true, kind: "call",
      op_id: "desk-call-" + Date.now().toString(36),
      form_version: state.formVersion,
      input_keys: Object.keys(values),
      occurred_at: Date.now(),
    });
    if (res.ok) {
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
      <div class="list-row"><div class="grow"><div class="nm">Результат едет в книгу сам</div>
        <div class="sub">Статус, комментарий и дата следующего звонка enrichment-ом возвращаются в вашу книгу и общий реестр.</div></div></div>
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
      if (!confirm("Выйти? Вход, портфель и кэш этого компьютера сотрутся.")) return;
      const hard = await import("../kit/api").then((m) => m.hard);
      await hard.logout();
      location.reload();
    });
  },
};
