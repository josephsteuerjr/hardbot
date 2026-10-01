// «Контроль» — только безопасность (и владелец). Три слоя: риск по людям против
// медианы своего офиса, разбор выездов человека по следам и контрольный обзвон
// должников: подтвердить звонком, был ли выезд и не отдавал ли человек деньги в руки.
import { api, hard } from "../kit/api";
import { dayTitle, esc, icon, n, pct, plural, toast } from "../kit/dom";
import { setHead, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { evidenceTag, flagsHtml, officeChips, openPerson, problem, rangeChips, rangeQuery, staleNote, wireRange, type Range } from "./common";

const state = {
  tab: "risk" as "risk" | "calls" | "assign",
  range: { preset: "30d" } as Range,
  office: "",
  employee: "",
  day: "",
};

const LEVEL: Record<string, string> = { high: "высокий", mid: "средний", low: "низкий" };

function signalText(s: any): string {
  if (s.value !== undefined) return `${esc(s.label)} <span class="muted">${pct(s.value)} · офис ${pct(s.peer)}</span>`;
  return `${esc(s.label)} <span class="muted">· ${n(s.n)}</span>`;
}

function tabs(): string {
  return `<div class="chips"><button class="chip" data-tab="risk" aria-pressed="${state.tab === "risk"}">Риск по людям</button>
    <button class="chip" data-tab="calls" aria-pressed="${state.tab === "calls"}">Контрольный обзвон</button>
    <button class="chip" data-tab="assign" aria-pressed="${state.tab === "assign"}">Закрепления</button></div>`;
}

async function renderRisk(root: HTMLElement) {
  setHead("Контроль", "следы, по которым стоит проверить человека");
  const r = await api("/api/control/overview", { ...rangeQuery(state.range), office: state.office });
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = tabs() + problem(r.text || "Не загрузилось"); wire(root); return; }
  const d = r.data;
  const rows = d.rows as any[];
  const high = rows.filter((x) => x.level === "high").length;
  root.innerHTML = `${staleNote(r.stale)}
    <div class="toolbar">${tabs()}<span class="spacer"></span>
      <button class="btn small" data-xlsx>${icon("download")}Excel</button></div>
    <div class="toolbar">${rangeChips(state.range, d.range, ["7d", "30d", "month"])}<span class="spacer"></span>${officeChips(d.offices, state.office)}</div>
    <div class="kpis"><div class="kpi accent"><b>${n(high)}</b><span>высокий риск</span></div>
      <div class="kpi"><b>${n(rows.filter((x) => x.level === "mid").length)}</b><span>средний</span></div>
      <div class="kpi"><b>${n(rows.reduce((s, x) => s + x.checks.done, 0))}</b><span>проверено звонком</span></div>
      <div class="kpi"><b>${n(rows.reduce((s, x) => s + x.checks.no_visit + x.checks.cash, 0))}</b><span>не подтвердилось</span></div></div>
    <div class="section" id="risk-tbl"></div>
    <div class="section"><div class="section-head"><h2 class="section-title">Что значат следы</h2></div>
      <div class="grid2">${(d.legend as any[]).map((l) => `<div class="list-row"><div class="grow"><div class="nm">${esc(l.label)}</div><div class="sub">${esc(l.hint)}</div></div></div>`).join("")}</div>
      <p class="muted" style="font-size:13.5px;max-width:760px;margin-top:14px">Каждый след сам по себе ничего не доказывает: человек сравнивается со своим офисом за то же окно. Подтверждает подозрение только звонок должнику — поэтому рядом контрольный обзвон.</p></div>`;
  wire(root);
  new DataTable<any>(root.querySelector("#risk-tbl")!, {
    id: "control-risk", exportName: "Контроль — риск", key: (x) => x.employee,
    cols: [
      { key: "emp", title: "Сотрудник", value: (x) => x.employee, html: (x) => `<span class="who">${esc(x.employee)}</span><span class="sub">${esc(x.office || "без офиса")}</span>` },
      { key: "office", title: "Офис", value: (x) => x.office || "без офиса" },
      { key: "visits", title: "Выездов", num: true, value: (x) => x.visits },
      { key: "risk", title: "Риск", num: true, value: (x) => (x.judged ? x.risk : ""),
        html: (x) => `<span class="risk" data-level="${x.level}" title="${esc(LEVEL[x.level])}"><b>${x.judged ? n(x.risk) : "—"}</b><span class="risk-bar"><i style="width:${x.risk}%"></i></span></span>` },
      { key: "level", title: "Уровень", value: (x) => LEVEL[x.level] },
      { key: "signals", title: "Следы против офиса", filter: false, value: (x) => (x.signals as any[]).map((s) => s.label).join(", "),
        html: (x) => `<div class="signals" style="white-space:normal">${(x.signals as any[]).slice(0, 4).map((s) => `<span class="flag${["cash", "callback", "paid"].includes(s.key) ? " strong" : ""}">${signalText(s)}</span>`).join("") || '<span class="muted">следов нет</span>'}</div>` },
      { key: "checks", title: "Обзвон", num: true, value: (x) => x.checks.done,
        html: (x) => (x.checks.done ? `${n(x.checks.done)}${x.checks.no_visit + x.checks.cash ? ` <span class="tag err">${n(x.checks.no_visit + x.checks.cash)}</span>` : ""}` : '<span class="muted">—</span>') },
    ],
    onRow: (x) => { state.employee = x.employee; void renderFlags(root); },
  }).setRows(rows);
  root.querySelector("[data-xlsx]")?.addEventListener("click", async () => {
    const res = await hard.download("/api/control/export.xlsx", rangeQuery(state.range), `Контроль ${d.range.from}—${d.range.to}.xlsx`);
    if (res.ok && res.file) { toast("Книга сохранена"); void hard.open(res.file); } else if (res.text) toast(res.text);
  });
}

async function renderFlags(root: HTMLElement) {
  const emp = state.employee;
  setHead(emp, "выезды со следами");
  root.innerHTML = '<div class="skeleton" style="width:40%"></div><div class="skeleton"></div>';
  const r = await api("/api/control/flags", { ...rangeQuery(state.range), employee: emp });
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = problem(r.text || "Не загрузилось"); return; }
  const d = r.data;
  const all = d.visits as any[];
  const flagged = all.filter((v) => v.flags.length);
  let day = "";
  const rows = flagged.map((v) => {
    const head = v.day !== day ? `<div class="feed-day">${esc(dayTitle(v.day))}</div>` : "";
    day = v.day;
    return `${head}<div class="ev visit pick" data-rep="${esc(v.rep)}" data-day="${esc(v.day)}"><time>${esc(v.time)}</time><span class="mark"></span>
      <div><div class="line1"><b>${esc(v.debtor || v.rep)}</b> — ${esc(v.title)}${v.agreed ? ` · ${esc(v.agreed)}` : ""}</div>
      ${v.detail ? `<div class="line2">${esc(v.detail)}</div>` : ""}${flagsHtml(v.flags)}</div>
      <div class="side">${evidenceTag(v.evidence)}${v.late_h ? `<br>доехал через ${n(v.late_h)} ч` : ""}</div></div>`;
  }).join("");
  root.innerHTML = `<div class="toolbar"><button class="btn small" data-back>← Все люди</button>${rangeChips(state.range, d.range, ["7d", "30d", "month"])}</div>
    <div class="kpis"><div class="kpi"><b>${n(all.length)}</b><span>выездов за окно</span></div>
      <div class="kpi accent"><b>${n(flagged.length)}</b><span>со следами</span></div>
      <div class="kpi"><b>${n(all.filter((v) => v.flags.includes("cash")).length)}</b><span>«наличные» в тексте</span></div>
      <div class="kpi"><b>${n(all.filter((v) => v.flags.includes("far")).length)}</b><span>далеко от адреса</span></div></div>
    <div class="section feed">${rows || '<div class="empty"><b>Чисто</b>Выездов со следами за это окно нет.</div>'}</div>`;
  root.querySelector("[data-back]")?.addEventListener("click", () => { state.employee = ""; void renderRisk(root); });
  wireRange(root, state.range, () => void renderFlags(root));
  root.querySelectorAll<HTMLElement>(".ev[data-rep]").forEach((row) => row.addEventListener("click", () => {
    root.querySelectorAll('.ev[aria-selected="true"]').forEach((x) => x.removeAttribute("aria-selected"));
    row.setAttribute("aria-selected", "true");
    void openPerson(emp, row.dataset.rep!, row.dataset.day);
  }));
}

async function renderAssign(root: HTMLElement) {
  setHead("Закрепления", "кому приехали договоры — и чьи они были");
  const r = await api("/api/control/assignments", rangeQuery(state.range));
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = tabs() + problem(r.text || "Не загрузилось"); wire(root); return; }
  const d = r.data;
  const rows = d.rows as any[];
  const evs = d.events as any[];
  const KIND: Record<string, string> = { transfer: "передача", assign: "новое", removed: "снято" };
  root.innerHTML = `${staleNote(r.stale)}
    <div class="toolbar">${tabs()}</div>
    <div class="toolbar">${rangeChips(state.range, d.range, ["7d", "30d", "month"])}</div>
    <div class="kpis"><div class="kpi"><b>${n(rows.reduce((s, x) => s + x.received, 0))}</b><span>договоров передано</span></div>
      <div class="kpi accent"><b>${n(rows.reduce((s, x) => s + x.backdated, 0))}</b><span>закреплено задним числом</span></div>
      <div class="kpi"><b>${n(evs.filter((e) => e.kind === "transfer").length)}</b><span>передач между людьми</span></div></div>
    <div class="section" id="assign-tbl"></div>
    <div class="section"><div class="section-head"><h2 class="section-title">События</h2></div>
      <div class="feed">${evs.slice(0, 100).map((e) => `<div class="ev"><time>${esc(e.window_to)}</time><span class="mark"></span>
        <div><div class="line1"><b>${esc(e.employee || "(снят)")}</b> ← ${esc(e.prev_employee || "без ответственного")} · ${KIND[e.kind] || e.kind}${e.claimed_at ? ` <span class="muted">· «дата закрепления» ${esc(e.claimed_at)}</span>` : ""}</div>
        <div class="line2"><span class="mono">${esc(e.contract_id)}</span>${e.debt ? ` · долг ${n(e.debt)}` : ""}</div></div></div>`).join("") || '<div class="empty"><b>Тихо</b>Передач за это окно не было.</div>'}</div>
      <p class="muted" style="font-size:13.5px;max-width:760px;margin-top:12px">События снимает импорт общего реестра (окно факта); «дата закрепления» пишется руками и может отличаться — расхождение больше ${n(d.claimed_lag_days || 7)} дней помечено как задним числом. Медиана долга выше офисной — вопрос, а не приговор: смотрите, у кого это стабильно.</p></div>`;
  wire(root);
  new DataTable<any>(root.querySelector("#assign-tbl")!, {
    id: "control-assign", exportName: "Закрепления", key: (x) => x.employee,
    cols: [
      { key: "emp", title: "Сотрудник", value: (x) => x.employee,
        html: (x) => `<span class="who">${esc(x.employee)}</span><span class="sub">${esc(x.office || "без офиса")}</span>` },
      { key: "received", title: "Получил", num: true, value: (x) => x.received },
      { key: "transfers", title: "Передач", num: true, value: (x) => x.transfers },
      { key: "paying", title: "Платящих", num: true, value: (x) => x.paying },
      { key: "median", title: "Медиана долга", num: true, value: (x) => x.median_debt,
        html: (x) => `${n(x.median_debt)}${x.debt_ratio ? ` <span class="muted">· ×${x.debt_ratio} от офиса (${n(x.office_median)})</span>` : ""}` },
      { key: "backdated", title: "Задним числом", num: true, value: (x) => x.backdated,
        html: (x) => (x.backdated ? `<span class="tag err">${n(x.backdated)}</span>` : '<span class="muted">—</span>') },
      { key: "peer", title: "Чаще всех от", value: (x) => x.top_peer || "",
        html: (x) => (x.top_peer ? `${esc(x.top_peer)} <span class="muted">· ${n(x.top_peer_n)}</span>` : '<span class="muted">—</span>') },
    ],
  }).setRows(rows);
}

async function renderCalls(root: HTMLElement) {
  setHead("Контрольный обзвон", "позвонить должнику и сверить со словами выездника");
  const r = await api("/api/control/callbacks", { date: state.day || undefined, office: state.office });
  if (!root.isConnected) return;
  if (!r.ok) { root.innerHTML = tabs() + problem(r.text || "Не загрузилось"); wire(root); return; }
  const d = r.data;
  state.day = d.date;
  const items = d.items as any[];
  const todo = items.filter((x) => x.state === "todo").length;
  const outcomes = d.outcomes as { key: string; label: string }[];
  root.innerHTML = `${staleNote(r.stale)}
    <div class="toolbar">${tabs()}<span class="spacer"></span>
      <div class="chips"><button class="chip" data-day="${esc(d.prev_date)}">‹</button>
      <span class="muted nowrap" style="font-size:13.5px;padding:0 6px">${esc(dayTitle(d.date, d.today))}</span>
      <button class="chip" data-day="${esc(d.next_date || "")}" ${d.next_date ? "" : "disabled"}>›</button></div></div>
    <div class="toolbar">${officeChips(d.offices, state.office)}</div>
    <div class="kpis"><div class="kpi accent"><b>${n(todo)}</b><span>осталось позвонить</span></div>
      <div class="kpi"><b>${n(items.length - todo)}</b><span>проверено</span></div>
      <div class="kpi"><b>${n(items.filter((x) => x.outcome === "no_visit" || x.outcome === "cash_to_employee").length)}</b><span>не подтвердилось</span></div></div>
    <div class="section"><details><summary class="link" style="cursor:pointer">Что спрашивать</summary>
      <ol class="script" style="margin-top:8px">${(d.script as string[]).map((s) => `<li>${esc(s)}</li>`).join("")}</ol></details></div>
    <div class="section">${items.map((x) => `<div class="call${x.state === "done" ? " done" : ""}" data-vid="${x.visit_id}">
      <div><h4>${esc(x.debtor || x.rep)}</h4>
        <div class="muted" style="font-size:13px">${esc(x.employee)}${x.office ? ` · ${esc(x.office)}` : ""} · выезд ${esc(x.time)} · <span class="mono">${esc(x.rep)}</span></div>
        <div class="phones">${(x.phones as string[]).map((p) => `<button class="phone" data-phone="${esc(p)}" title="Скопировать">${esc(p)}</button>`).join("") || '<span class="muted">телефона в реестре нет</span>'}</div>
        ${x.address ? `<div class="muted" style="font-size:13px;margin-top:6px">${esc(x.address)}</div>` : ""}
        <div class="said"><b>${esc(x.status || "—")}</b>${x.agreed ? ` · ${esc(x.agreed)}` : ""}${x.promise ? ` · обещание ${esc(x.promise.date || "")} ${x.promise.sum ? esc(x.promise.sum) : ""}` : ""}${x.reported ? `<br>${esc(x.reported)}` : ""}</div>
        <div class="why">${esc(x.why === "по следам" ? "выбран по следам:" : x.why)} ${x.why === "по следам" ? flagsHtml(x.flags) : ""}</div></div>
      <div><div class="outcomes">${outcomes.map((o) => `<button class="chip" data-out="${o.key}" aria-pressed="${x.outcome === o.key}">${esc(o.label)}</button>`).join("")}</div>
        <textarea class="input" data-note placeholder="Заметка: что сказал должник" style="width:100%;margin-top:8px;min-height:60px">${esc(x.note)}</textarea>
        ${x.checked_at ? `<div class="muted" style="font-size:12px;margin-top:4px">отмечено ${esc(x.checked_at.replace("T", " ").slice(0, 16))}</div>` : ""}</div>
    </div>`).join("") || '<div class="empty"><b>Звонить некому</b>В этот день выездов не было.</div>'}</div>`;
  wire(root);
  root.querySelectorAll<HTMLButtonElement>("[data-day]").forEach((b) => b.addEventListener("click", () => {
    if (!b.dataset.day) return;
    state.day = b.dataset.day; void renderCalls(root);
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-phone]").forEach((b) => b.addEventListener("click", async () => {
    await hard.copy(b.dataset.phone!); toast("Номер скопирован");
  }));
  root.querySelectorAll<HTMLElement>(".call").forEach((card) => {
    const vid = Number(card.dataset.vid);
    card.querySelectorAll<HTMLButtonElement>("[data-out]").forEach((b) => b.addEventListener("click", async () => {
      const note = card.querySelector<HTMLTextAreaElement>("[data-note]")?.value || "";
      const res = await api("/api/control/callback", undefined, "POST", { visit_id: vid, outcome: b.dataset.out, note });
      if (!res.ok) { toast(res.text || "Не сохранилось"); return; }
      card.querySelectorAll("[data-out]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      card.classList.add("done");
      toast("Записано");
    }));
  });
}

function wire(root: HTMLElement) {
  root.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    state.tab = b.dataset.tab as "risk" | "calls"; state.employee = ""; void draw(root);
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-office]").forEach((b) => b.addEventListener("click", () => {
    state.office = b.dataset.office || ""; void draw(root);
  }));
  wireRange(root, state.range, () => void draw(root));
}

function draw(root: HTMLElement): Promise<void> {
  if (state.tab === "calls") return renderCalls(root);
  if (state.tab === "assign") return renderAssign(root);
  if (state.employee) return renderFlags(root);
  return renderRisk(root);
}

export const control: Section = {
  id: "control",
  title: "Контроль",
  icon: "control",
  kicker: "следы, по которым стоит проверить человека",
  async render(root) {
    state.employee = "";
    root.innerHTML = '<div class="skeleton" style="width:40%"></div><div class="skeleton"></div>';
    await draw(root);
  },
};

export { plural };
