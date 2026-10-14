// Рабочее окно руководителя. Все срезы и карточки проходят прежние серверные права.
import { api, hard } from "../kit/api";
import { ago, esc, n, pct, toast } from "../kit/dom";
import { go, openPanel, setHead, type Section } from "../kit/shell";
import { DataTable, type Col } from "../kit/table";
import { evidenceTag, openPerson, problem, rangeChips, rangeQuery, staleNote, wireRange, type Range } from "./common";

const state = { range: { preset: "today" } as Range, office: "", country: "", profile: "", attention: false };
let sequence = 0;
const number = (x: unknown) => Number(x) || 0;
const sum = (rows: any[], key: string) => rows.reduce((s, r) => s + number(r[key]), 0);
const filtered = (r: any) => (!state.office || (state.office === "!none" ? !r.office : r.office === state.office))
  && (!state.country || r.country === state.country);

async function employeeDetail(employee: string, profile: string) {
  const body = openPanel(`<h2>${esc(employee)}</h2><div class="sub">${profile === "soft" ? "Софт · звонки" : "Хард · выезды"}</div>`);
  const result = await api("/api/manager/stats_employee", { ...rangeQuery(state.range), employee, mode: profile });
  if (!body.isConnected) return;
  if (!result.ok) { body.innerHTML = problem(result.text || "Не удалось открыть сотрудника"); return; }
  const d = result.data, soft = d.profile === "soft";
  body.innerHTML = `${staleNote(result.stale)}<div class="crm-detail-numbers"><b>${n(soft ? d.calls : d.visits)}</b><span>${soft ? "звонков" : "выездов"} за выбранный период</span>
    <b>${n(d.portfolio)}</b><span>людей в портфеле</span></div>
    ${soft ? `<div class="crm-facts">RPC <b>${n(d.rpc)}</b> · PTP <b>${n(d.ptp)}</b> · KP <b>${n(d.kp)}</b></div>
      <p class="hint">KP — полная реестровая оплата обещанной суммы в срок. Ручные оплаты в KP не входят.</p>` : ""}
    <div class="toolbar"><button class="btn small" data-full>Открыть полный разбор →</button><button class="btn small" data-portfolio>Портфель сотрудника</button></div>
    <h3>Факты за период</h3><div class="crm-case-list">${(d.cases || []).map((c: any, i: number) =>
      `<button class="crm-case" data-case="${i}"><span class="crm-case-date">${esc(c.day_label || c.date)}</span><b>${esc(c.client || c.rep)}</b>
      <span>${esc(c.outcome || "Без результата")}</span><span class="sub">${esc(c.comment || c.agreed || "")}</span>
      <span class="sub mono">${esc(c.rep)}</span>${soft ? "" : evidenceTag(c.evidence_level)}</button>`).join("") || '<p class="muted">За этот период результатов нет.</p>'}</div>`;
  body.querySelector("[data-full]")?.addEventListener("click", () => void go("team", { employee, mode: profile }));
  body.querySelector("[data-portfolio]")?.addEventListener("click", () => void go("team-portfolio", {employee,profile}));
  body.querySelectorAll<HTMLElement>("[data-case]").forEach(b => b.addEventListener("click", () => {
    const c = d.cases[Number(b.dataset.case)]; void openPerson(employee, c.rep, c.date, soft ? "soft" : undefined);
  }));
}

async function render(root: HTMLElement) {
  const seq = ++sequence;
  setHead("Рабочий обзор", "команда · результаты · требующие внимания случаи");
  root.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  const [stats, activity, hardPromises, softPromises] = await Promise.all([
    api("/api/manager/stats", rangeQuery(state.range)),
    api("/api/manager/activity", { ...rangeQuery(state.range), limit: 80 }),
    api("/api/manager/promises"),
    api("/api/manager/promises", { mode: "soft" }),
  ]);
  if (!root.isConnected || seq !== sequence) return;
  if (!stats.ok) { root.innerHTML = problem(stats.text || "Обзор не загрузился"); return; }
  const d = stats.data;
  const hardRows = (d.employees || []).filter((r: any) => r.profile !== "soft" && filtered(r));
  const softRows = (d.soft?.employees || []).filter(filtered);
  if (!state.profile) state.profile = (d.employees || []).some((r: any) => r.profile !== "soft") ? "hard" : "soft";
  const soft = state.profile === "soft", rows = soft ? softRows : hardRows;
  const promises = soft ? softPromises : hardPromises;
  const employees = new Set(rows.map((r: any) => r.employee));
  const overdue = promises.ok ? (promises.data.employees || []).filter((r: any) => employees.has(r.employee))
    .flatMap((r: any) => (r.items || []).map((i: any) => ({ ...i, employee: r.employee }))) : [];
  const events = activity.ok ? (activity.data.events || []).filter((e: any) => employees.has(e.employee)
    && (soft ? ["call", "receipt", "promise", "view", "login"].includes(e.kind) : e.kind !== "call")) : [];
  const inactive = rows.filter((r: any) => !number(soft ? r.calls : r.visits));
  const attentionNames = new Set([...overdue.map((r: any) => r.employee), ...inactive.map((r: any) => r.employee)]);
  const shown = state.attention ? rows.filter((r: any) => attentionNames.has(r.employee)) : rows;
  const scope = [d.scope, d.range?.from && `${d.range.from} — ${d.range.to}`].filter(Boolean).join(" · ");
  const kpi = (value: string, title: string, note: string, action: string, cls = "") =>
    `<button class="crm-kpi ${cls}" data-action="${action}"><span>${title}</span><b>${value}</b><small>${note} <span aria-hidden="true">↗</span></small></button>`;
  root.classList.add("crm-workspace");
  root.innerHTML = `${staleNote(stats.stale)}<div class="crm-context"><span>${esc(scope)}</span><span>Реестр оплат: ${esc(d.pay_asof || "дата не получена")}</span></div>
    <div class="crm-filters"><div>${rangeChips(state.range, d.range)}</div><div class="crm-filter-row">
      <div class="chips"><button class="chip" data-profile="hard" aria-pressed="${!soft}">Хард · выезды</button><button class="chip" data-profile="soft" aria-pressed="${soft}">Софт · звонки</button></div>
      <label>Страна <select class="input" data-country><option value="">Все доступные</option>${(d.countries || []).map((c: any) => `<option value="${esc(c.code)}" ${state.country === c.code ? "selected" : ""}>${esc(c.short || c.name)}</option>`).join("")}</select></label>
      <label>Офис <select class="input" data-office><option value="">Все доступные</option>${(d.offices || []).map((o: any) => `<option value="${esc(o.code)}" ${state.office === o.code ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select></label>
      <span class="spacer"></span><button class="btn small" data-export title="Отчёт по всей доступной команде за выбранный период">Общий отчёт Excel</button><button class="btn small" data-refresh>Обновить</button></div></div>
    <div class="crm-kpi-grid">${kpi(n(sum(rows, soft ? "calls" : "visits")), soft ? "Звонки" : "Выезды", "Факты за выбранный период", "team")}
      ${kpi(n(sum(rows, "portfolio")), "Портфель", "Людей у команды", "team")}
      ${kpi(n(soft ? sum(rows, "rpc") : sum(rows, "promised")), soft ? "RPC" : "Обещания", soft ? "Результативные звонки" : "Обещают оплатить", "team")}
      ${kpi(n(soft ? sum(rows, "kp") : sum(rows, "paid")), soft ? "KP" : "Оплатили", soft ? "Полная оплата обещания в срок" : "Результат выезда за период", "team", "success")}
      ${kpi(promises.ok ? n(overdue.length) : "—", "Просроченные обещания", "Текущая просрочка · все даты", "promises", "attention")}</div>
    <div class="crm-board"><section class="crm-team"><div class="crm-section-title"><div><h2>Команда и результаты</h2><p>${n(rows.length)} сотрудников · ${soft ? "звонки, обещания и реестровые оплаты" : "выезды, подтверждения и план"}</p></div>
      <button class="chip" data-attention aria-pressed="${state.attention}">Требуют внимания · ${n(attentionNames.size)}</button></div><div id="crm-table"></div>
      <p class="hint">Строка открывает сотрудника и его факты. Поиск, фильтры столбцов и Excel действуют на показанные строки.</p></section>
    <aside class="crm-sidebar"><section><div class="crm-section-title"><h2>Требуют внимания</h2></div>
      ${!promises.ok ? `<p class="hint">Просрочка не загрузилась: ${esc(promises.text || "нет связи")}</p>` : ""}
      ${overdue.slice(0, 5).map((p: any, i: number) => `<button class="crm-task" data-promise="${i}"><span class="tag warn">${n(p.days_overdue)} дн</span><b>${esc(p.client || p.rep)}</b><span>${esc(p.employee)}</span><small>${esc(p.money_text || "")} · ${esc(p.promise_date || "")}</small></button>`).join("")}
      ${inactive.slice(0, 3).map((r: any) => `<button class="crm-task quiet" data-employee="${esc(r.employee)}"><b>${esc(r.employee)}</b><span>Нет ${soft ? "звонков" : "выездов"} за выбранный период</span></button>`).join("")}
      ${!overdue.length && !inactive.length && promises.ok ? '<p class="muted">В этом срезе нет просрочки и сотрудников без результатов.</p>' : ""}
      <button class="link" data-action="promises">Все просроченные обещания →</button></section>
    <section><div class="crm-section-title"><h2>Последние события</h2><button class="link" data-action="activity">Вся лента →</button></div>
      ${!activity.ok ? problem(activity.text || "Лента не загрузилась") : events.slice(0, 8).map((e: any, i: number) => `<button class="crm-event" data-event="${i}"><time>${esc(e.time || "")}</time><b>${esc(e.employee)}</b><span>${esc(e.title || e.kind)}</span><small>${esc(e.debtor || e.detail || ago(e.at))}</small></button>`).join("") || '<p class="muted">Событий за этот период нет.</p>'}</section></aside></div>`;
  const metric = (key: string, title: string): Col<any> => ({ key, title, num: true, value: r => number(r[key]) });
  const cols: Col<any>[] = [
    { key: "employee", title: "Сотрудник", value: r => r.employee, html: r => `<span class="who">${esc(r.employee)}</span><span class="sub">${esc(r.activity?.last_seen ? ago(r.activity.last_seen) : r.country || "")}</span>` },
    { key: "office", title: "Офис", value: r => r.office || "без офиса" }, metric("portfolio", "Портфель"),
    ...(soft ? [metric("calls", "Звонки"), metric("rpc", "RPC"), metric("ptp", "PTP"), metric("kp", "KP"),
      { key: "paid_sum_text", title: "Реестровые оплаты", value: (r: any) => r.paid_sum_text || "0" },
      { key: "wait_text", title: "Прогноз · ждём", value: (r: any) => r.wait_text || "0" }]
      : [metric("visits", "Выезды"), metric("persons", "Людей"), metric("paid", "Оплатили"), metric("promised", "Обещают"),
        metric("confirmed", "У адреса"), metric("overdue", "Просрочка"),
        { key: "plan", title: "План", num: true, value: (r: any) => {
          const days = (d.by_day || []).filter((x: any) => !x.weekend).length;
          return r.plan_per_day && days ? number(r.visits) / (r.plan_per_day * days) : null;
        }, html: (r: any) => { const days = (d.by_day || []).filter((x: any) => !x.weekend).length;
          return r.plan_per_day && days ? pct(number(r.visits) / (r.plan_per_day * days)) : "—"; } }]),
  ];
  new DataTable<any>(root.querySelector("#crm-table")!, { id: `crm-${state.profile}`, cols, key: r => r.employee,
    empty: "В этом срезе сотрудников нет.", exportName: "Рабочий обзор", searchPlaceholder: "Найти сотрудника или офис",
    onRow: r => void employeeDetail(r.employee, state.profile),
    footer: visible => cols.map((c, i) => i === 0 ? `Итого · ${n(visible.length)}` : c.num && c.key !== "plan" ? n(visible.reduce((s, r) => s + number(c.value(r)), 0)) : ""),
  }).setRows(shown);
  wireRange(root, state.range, () => void render(root));
  root.querySelectorAll<HTMLElement>("[data-action]").forEach(b => b.addEventListener("click", () => void go(b.dataset.action!,
    { range: { ...state.range }, office: state.office, country: state.country, profile: state.profile, employees: [...employees] })));
  root.querySelectorAll<HTMLElement>("[data-profile]").forEach(b => b.addEventListener("click", () => { state.profile = b.dataset.profile!; void render(root); }));
  for (const key of ["office", "country"] as const) root.querySelector<HTMLSelectElement>(`[data-${key}]`)?.addEventListener("change", e => {
    state[key] = (e.target as HTMLSelectElement).value; void render(root);
  });
  root.querySelector("[data-attention]")?.addEventListener("click", () => { state.attention = !state.attention; void render(root); });
  root.querySelector("[data-refresh]")?.addEventListener("click", () => void render(root));
  root.querySelectorAll<HTMLElement>("[data-employee]").forEach(b => b.addEventListener("click", () => void employeeDetail(b.dataset.employee!, state.profile)));
  root.querySelectorAll<HTMLElement>("[data-promise]").forEach(b => b.addEventListener("click", () => {
    const p = overdue[Number(b.dataset.promise)]; void openPerson(p.employee, p.rep, undefined, state.profile);
  }));
  root.querySelectorAll<HTMLElement>("[data-event]").forEach(b => b.addEventListener("click", () => {
    const e = events[Number(b.dataset.event)]; if (e.rep) void openPerson(e.employee, e.rep, e.day, e.kind === "call" ? "soft" : undefined);
    else void employeeDetail(e.employee, state.profile);
  }));
  root.querySelector("[data-export]")?.addEventListener("click", async () => {
    const r = await hard.download("/api/manager/stats.xlsx", rangeQuery(state.range), `Статистика ${d.range.from}—${d.range.to}.xlsx`);
    if (r.ok && r.file) { toast("Книга сохранена"); void hard.open(r.file); } else if (r.text) toast(r.text);
  });
}

export const workspace: Section = { id: "workspace", title: "Рабочий обзор", icon: "activity", render,
  leave() { sequence++; } };
