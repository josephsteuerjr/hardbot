// «Команда» — сетка из миниаппа на ширину экрана: окно периода, офисы, числа строкой,
// таблица людей с сортировкой, столбики по дням. Строка ведёт в сотрудника — его
// выезды за окно; выезд открывает карточку должника в панели справа.
import { api, hard } from "../kit/api";
import { esc, icon, n, pct, plural, toast } from "../kit/dom";
import { setHead, type Section } from "../kit/shell";
import { DataTable, type Col } from "../kit/table";
import { evidenceTag, officeChips, openPerson, problem, rangeChips, rangeQuery, staleNote, wireRange, type Range } from "./common";

const state = {
  range: { preset: "today" } as Range,
  office: "",
  country: "",
  employee: "",
  mode: "" as "" | "hard" | "soft",
};

const NUMS: [string, string][] = [
  ["visits", "Выездов"], ["persons", "Людей"], ["paid", "Оплачен"], ["promised", "Обещают"], ["overdue", "Просрочено"],
  ["with_photo", "С фото"], ["confirmed", "У адреса"], ["far", "Далеко"], ["late", "Позже"], ["no_geo", "Без гео"],
];

function inOffice(r: any): boolean {
  if (state.office === "!none") return !r.office;
  return !state.office || r.office === state.office;
}

/** Сетка людей: сортировка и тихие фильтры по любому столбцу, итог по показанным. */
function gridTable(host: HTMLElement, d: any, onPick: (emp: string) => void): void {
  const days = (d.by_day as any[]).filter((x) => !x.weekend).length || 1;
  const rows = (d.employees as any[]).filter((r) => r.profile !== "soft" && inOffice(r) && (!state.country || r.country === state.country));
  rows.forEach((r) => { r._plan = (r.plan_per_day || 0) * days; });
  const numCol = (k: string, t: string): Col<any> => ({ key: k, title: t, num: true, value: (r) => Number(r[k]) || 0,
    html: (r) => (r[k] ? n(r[k]) : '<span class="zero">0</span>') });
  const cols: Col<any>[] = [
    { key: "employee", title: "Сотрудник", value: (r) => r.employee,
      // Офис — своей колонкой; под именем — только простой, если он есть.
      html: (r) => `<span class="who">${esc(r.employee)}</span>${r.activity?.idle_days > 1 ? `<span class="sub">простой ${r.activity.idle_days} дн</span>` : ""}` },
    { key: "office", title: "Офис", value: (r) => r.office || "без офиса" },
    ...NUMS.map(([k, t]) => numCol(k, t)),
    { key: "fill_avg", title: "Карточка, мин", num: true, value: (r) => (r.fill_avg ? Math.round(r.fill_avg / 60) : ""),
      html: (r) => (r.fill_avg ? `${Math.round(r.fill_avg / 60)} мин` : "—") },
    { key: "plan", title: "План", num: true, value: (r) => (r._plan ? Math.round((r.visits / r._plan) * 100) : ""),
      html: (r) => (r._plan ? pct(r.visits / r._plan) : "—") },
  ];
  new DataTable<any>(host, {
    id: "manager-team", cols, key: (r) => r.employee, exportName: "Команда", searchPlaceholder: "Найти сотрудника",
    onRow: (r) => onPick(r.employee),
    footer: (shown) => {
      const sum = (k: string) => shown.reduce((s, r) => s + (Number(r[k]) || 0), 0);
      const plan = shown.reduce((s, r) => s + (r._plan || 0), 0);
      return [`Итого · ${n(shown.length)}`, "", ...NUMS.map(([k]) => n(sum(k))), "", plan ? pct(sum("visits") / plan) : "—"];
    },
  }).setRows(rows);
}

function daysHtml(byDay: any[], field = "visits"): string {
  if (!byDay || byDay.length < 2) return "";
  const max = Math.max(1, ...byDay.map((x) => x[field] || 0));
  return `<div class="days">${byDay.map((x, i) => `<div class="day-bar" data-day="${i}" title="${esc(x.label)}: ${n(x[field])}">
    <i style="height:${Math.max(2, Math.round(((x[field] || 0) / max) * 46))}px;${x.weekend ? "opacity:.45" : ""}"></i><span>${esc(String(x.day))}</span></div>`).join("")}</div>`;
}

function modeChips(mode: string, profiles = ["hard", "soft"]): string {
  return `<div class="chips">${profiles.map(p => `<button class="chip" data-mode="${p}" aria-pressed="${mode === p}">${p === "soft" ? "Софт · звонки" : "Хард · выезды"}</button>`).join("")}</div>`;
}

function softKpis(t: any): string {
  return `<div class="kpis">${[[n(t.calls), "звонков"], [n(t.persons), "людей в звонках"],
    [n(t.portfolio), "людей в портфеле"], [n(t.contracts), "договоров в портфеле"],
    [t.paid_sum_text || "0", "оплаты по реестру"], [t.wait_text || "0", "прогноз · ждём"],
    [t.doubt_text || "0", "прогноз · сомнение"],
    [n(t.rpc || 0), "RPC · результативные звонки"], [n(t.ptp || 0), "PTP · обещания"],
    [n(t.kp || 0), "KP · исполнено в срок"], [n(t.operation?.partial || 0), "частичные оплаты"]].map(([v, label]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(label)}</span></div>`).join("")}</div>` +
    `<div class="chips">${Object.entries(t.operation?.statuses || {}).map(([s, count]) => `<span class="chip">${esc(s)} <b>${n(count)}</b></span>`).join('')}</div>`;
}

function softTable(host: HTMLElement, rows: any[], onPick: (emp: string) => void): void {
  new DataTable<any>(host, {
    id: "manager-soft-team", exportName: "Софт · команда", key: r => r.employee,
    searchPlaceholder: "Найти сотрудника", empty: "В этом срезе нет сотрудников Софта.",
    cols: [
      { key: "employee", title: "Сотрудник", value: r => r.employee },
      { key: "office", title: "Офис", value: r => r.office || "без офиса" },
      ...[["calls", "Звонков"], ["persons", "Людей в звонках"], ["portfolio", "Портфель, людей"], ["contracts", "Договоров"]].map(([key, title]) => ({ key, title, num: true, value: (r: any) => r[key] || 0 })),
      ...[["rpc", "RPC"], ["ptp", "PTP"], ["kp", "KP"]].map(([key, title]) => ({ key, title, num: true, value: (r: any) => r[key] || 0 })),
      ...[["rpc_rate", "RPC, %"], ["ptp_rate", "PTP от RPC, %"], ["kp_rate", "KP от PTP, %"]].map(([key, title]) => ({ key, title, num: true, value: (r: any) => r[key], html: (r: any) => r[key] == null ? '—' : pct(r[key]) })),
      { key: "partial", title: "Частичные оплаты", num: true, value: r => r.operation?.partial || 0 },
      ...[["paid_sum_text", "Оплаты по реестру"], ["wait_text", "Ждём"], ["doubt_text", "Сомнение"]].map(([key, title]) => ({ key, title, value: (r: any) => r[key] || "0" })),
      ...Object.keys(rows[0]?.operation?.statuses || {}).map(title => ({key:'status-'+title,title,num:true,value:(r:any)=>r.operation?.statuses?.[title]||0})),
    ], onRow: r => onPick(r.employee),
  }).setRows(rows);
}

function kpis(t: any): string {
  const k = (v: unknown, label: string, cls = "") => `<div class="kpi ${cls}"><b>${esc(v)}</b><span>${esc(label)}</span></div>`;
  return `<div class="kpis">
    ${k(n(t.visits), "выездов")}${k(n(t.persons), "людей")}${k(n(t.paid), "оплатили", "accent")}
    ${k(n(t.promised), "обещают")}${k(n(t.overdue), "просрочено обещаний")}
    ${k(t.visits ? pct((t.confirmed || 0) / t.visits) : "—", "подтверждено у адреса")}
    ${k(t.money_paid_text || "—", "закрыто", "money")}</div>`;
}

async function renderTeam(root: HTMLElement) {
  setHead("Команда", "сетка по людям и офисам");
  const r = await api("/api/manager/stats", rangeQuery(state.range));
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = problem(r.text || "Не загрузилось"); return; }
  const d = r.data;
  if (!state.mode) state.mode = d.employees?.some((x: any) => x.profile !== "soft") ? "hard" : "soft";
  const soft = state.mode === "soft";
  const softRows = (d.soft?.employees || []).filter((x: any) => inOffice(x) && (!state.country || x.country === state.country));
  const offices = (d.offices || []).map((o: any) => ({ code: o.code, label: o.name, n: o.employees }));
  const countries = (d.countries || []) as any[];
  const totals = state.office ? (d.offices.find((o: any) => o.code === state.office)?.totals || d.totals)
    : state.country ? (countries.find((c) => c.code === state.country)?.totals || d.totals) : d.totals;
  root.innerHTML = `${staleNote(r.stale)}
    <div class="toolbar">${rangeChips(state.range, d.range)}<span class="spacer"></span>
      <button class="btn small" data-xlsx>${icon("download")}Excel</button></div>
    <div class="toolbar">${countries.length > 1 ? `<div class="chips"><button class="chip" data-country="" aria-pressed="${!state.country}">Все страны</button>${countries.map((c) =>
      `<button class="chip" data-country="${esc(c.code)}" aria-pressed="${state.country === c.code}">${esc(c.flag)} ${esc(c.short || c.name)}</button>`).join("")}</div><span class="chip-sep"></span>` : ""}
      ${officeChips(offices, state.office)}</div>
    ${modeChips(state.mode)}
    ${soft ? `<p class="muted">${n(softRows.reduce((s: number, x: any) => s + x.calls, 0))} звонков · ${n(softRows.length)} сотрудников. Оплаты и прогноз — по текущему портфелю реестра.</p>` : kpis(totals)}
    ${soft ? "" : daysHtml(d.by_day)}
    <div class="section"><div id="tbl"></div></div>`;
  wireRange(root, state.range, () => void renderTeam(root));
  root.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach(b => b.addEventListener("click", () => {
    state.mode = b.dataset.mode as "hard" | "soft"; void renderTeam(root);
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-office]").forEach((b) => b.addEventListener("click", () => {
    state.office = b.dataset.office || ""; state.country = ""; void renderTeam(root);
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-country]").forEach((b) => b.addEventListener("click", () => {
    state.country = b.dataset.country || ""; state.office = ""; void renderTeam(root);
  }));
  const pick = (emp: string) => { state.employee = emp; void renderEmployee(root); };
  if (soft) softTable(root.querySelector("#tbl")!, softRows, pick);
  else gridTable(root.querySelector("#tbl")!, d, pick);
  root.querySelectorAll<HTMLElement>("[data-day]").forEach((b) => b.addEventListener("click", () => {
    const x = d.by_day[Number(b.dataset.day)];
    if (x) { state.range = { from: x.date, to: x.date }; void renderTeam(root); }
  }));
  root.querySelector("[data-xlsx]")?.addEventListener("click", async () => {
    const res = await hard.download("/api/manager/stats.xlsx", rangeQuery(state.range), `Статистика ${d.range.from}—${d.range.to}.xlsx`);
    if (res.ok && res.file) { toast("Книга сохранена"); void hard.open(res.file); } else if (res.text) toast(res.text);
  });
}

async function renderEmployee(root: HTMLElement) {
  const emp = state.employee;
  setHead(emp, "выезды за окно");
  root.innerHTML = '<div class="skeleton" style="width:40%"></div><div class="skeleton"></div>';
  const r = await api("/api/manager/stats_employee", { ...rangeQuery(state.range), employee: emp, mode: state.mode });
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = problem(r.text || "Не загрузилось"); return; }
  const d = r.data;
  const soft = d.profile === "soft";
  state.mode = soft ? "soft" : "hard";
  setHead(emp, soft ? "звонки за период" : "выезды за окно");
  const cases = (d.cases || []) as any[];
  root.innerHTML = `${staleNote(r.stale)}
    <div class="toolbar"><button class="btn small" data-back>← Вся команда</button>${rangeChips(state.range, d.range)}</div>
    ${modeChips(state.mode, d.profiles || [state.mode])}
    ${soft ? softKpis(d) : kpis(d)}
    ${daysHtml(d.by_day, soft ? "calls" : "visits")}
    <div class="section"><div class="section-head"><h2 class="section-title">${soft ? "Звонки" : "Выезды"}</h2>
      <span class="section-note">${n(cases.length)} ${soft ? plural(cases.length, "звонок", "звонка", "звонков") : plural(cases.length, "выезд", "выезда", "выездов")} · в портфеле ${n(d.portfolio)} ${plural(d.portfolio || 0, "человек", "человека", "человек")}</span></div>
    <div id="cases"></div>
    </div>`;
  root.querySelector("[data-back]")?.addEventListener("click", () => { state.employee = ""; void renderTeam(root); });
  wireRange(root, state.range, () => void renderEmployee(root));
  root.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach(b => b.addEventListener("click", () => {
    state.mode = b.dataset.mode as "hard" | "soft"; void renderEmployee(root);
  }));
  const EV: Record<string, string> = { confirmed: "у адреса", far: "далеко от адреса", nofix: "GPS не поймался", nogeo: "нет GPS-точки", coarse: "адрес неточный" };
  new DataTable<any>(root.querySelector("#cases")!, {
    id: soft ? "manager-soft-cases" : "manager-cases", exportName: `${soft ? "Звонки" : "Выезды"} — ${emp}`, key: (c) => String(c.id || `${c.date}|${c.rep}`), empty: soft ? "За этот период звонков не было." : "За это окно сотрудник не выезжал.",
    cols: [
      { key: "day", title: "День", value: (c) => c.date, html: (c) => esc(c.day_label) },
      { key: "client", title: "Должник", value: (c) => c.client, html: (c) => `<span class="who">${esc(c.client)}</span><span class="sub mono">${esc(c.rep)}</span>` },
      { key: "outcome", title: "Исход", value: (c) => c.outcome || "—", html: (c) => `${esc(c.outcome || "—")}${c.agreed ? `<span class="sub">${esc(c.agreed)}</span>` : ""}` },
      ...(soft ? [{ key: "comment", title: "Комментарий звонка", value: (c: any) => c.comment || "" },
        { key: "n", title: "Договоров", num: true, value: (c: any) => c.n || 1 }] : [{ key: "ev", title: "Подтверждение", value: (c: any) => EV[c.evidence_level] || "",
        html: (c: any) => `${evidenceTag(c.evidence_level)}${c.late_days ? ` <span class="tag warn">позже на ${n(c.late_days)} дн</span>` : ""}${c.series ? `<span class="sub">${esc(c.series)}</span>` : ""}` },
      { key: "fill", title: "Карточка", value: (c: any) => c.fill_text || "" },
      { key: "photos", title: "Фото", num: true, value: (c: any) => Number(c.photos) || 0 }]),
      { key: "money", title: "Долг", num: true, value: (c) => c.money_text || "" },
    ],
    onRow: (c, tr) => {
      root.querySelectorAll('tr[aria-selected="true"]').forEach((x) => x.removeAttribute("aria-selected"));
      tr.setAttribute("aria-selected", "true");
      void openPerson(emp, c.rep, c.date, soft ? "soft" : undefined);
    },
  }).setRows(cases);
}

export const team: Section = {
  id: "team",
  title: "Команда",
  icon: "team",
  kicker: "сетка по людям и офисам",
  async render(root, params) {
    state.employee = params.employee ? String(params.employee) : "";
    if (state.employee) state.mode = params.mode === "soft" ? "soft" : params.mode === "hard" ? "hard" : "";
    root.innerHTML = '<div class="skeleton" style="width:40%"></div><div class="skeleton"></div><div class="skeleton" style="width:70%"></div>';
    if (state.employee) await renderEmployee(root); else await renderTeam(root);
  },
};
