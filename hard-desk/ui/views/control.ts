// «Контроль» — только безопасность (и владелец). Три слоя: риск по людям против
// медианы своего офиса, разбор выездов человека по следам и контрольный обзвон
// должников: подтвердить звонком, был ли выезд и не отдавал ли человек деньги в руки.
import { api, hard } from "../kit/api";
import { dayTitle, esc, icon, n, pct, plural, toast } from "../kit/dom";
import { setHead, type Section } from "../kit/shell";
import { evidenceTag, flagsHtml, officeChips, openPerson, problem, rangeChips, rangeQuery, staleNote, wireRange, type Range } from "./common";

const state = {
  tab: "risk" as "risk" | "calls",
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
    <button class="chip" data-tab="calls" aria-pressed="${state.tab === "calls"}">Контрольный обзвон</button></div>`;
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
    <div class="section"><div class="table-wrap"><table class="grid"><thead><tr>
      <th class="l">Сотрудник</th><th>Выездов</th><th>Риск</th><th class="l">Следы против офиса</th><th>Обзвон</th></tr></thead>
      <tbody>${rows.map((x) => `<tr class="pick" data-emp="${esc(x.employee)}">
        <td class="l"><span class="who">${esc(x.employee)}</span><span class="sub">${esc(x.office || "без офиса")}</span></td>
        <td>${n(x.visits)}</td>
        <td><span class="risk" data-level="${x.level}" title="${esc(LEVEL[x.level])}"><b>${x.judged ? n(x.risk) : "—"}</b><span class="risk-bar"><i style="width:${x.risk}%"></i></span></span></td>
        <td class="l" style="white-space:normal"><div class="signals">${x.signals.slice(0, 4).map((s: any) => `<span class="flag${["cash", "callback", "paid"].includes(s.key) ? " strong" : ""}">${signalText(s)}</span>`).join("") || '<span class="muted">следов нет</span>'}</div></td>
        <td>${x.checks.done ? `${n(x.checks.done)}${x.checks.no_visit + x.checks.cash ? ` <span class="tag err">${n(x.checks.no_visit + x.checks.cash)}</span>` : ""}` : '<span class="muted">—</span>'}</td></tr>`).join("")}</tbody></table></div></div>
    <div class="section"><div class="section-head"><h2 class="section-title">Что значат следы</h2></div>
      <div class="grid2">${(d.legend as any[]).map((l) => `<div class="list-row"><div class="grow"><div class="nm">${esc(l.label)}</div><div class="sub">${esc(l.hint)}</div></div></div>`).join("")}</div>
      <p class="muted" style="font-size:13.5px;max-width:760px;margin-top:14px">Каждый след сам по себе ничего не доказывает: человек сравнивается со своим офисом за то же окно. Подтверждает подозрение только звонок должнику — поэтому рядом контрольный обзвон.</p></div>`;
  wire(root);
  root.querySelectorAll<HTMLElement>("tr[data-emp]").forEach((tr) => tr.addEventListener("click", () => {
    state.employee = tr.dataset.emp!; void renderFlags(root);
  }));
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
