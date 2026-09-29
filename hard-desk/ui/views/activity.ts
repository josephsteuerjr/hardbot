// «Активность» — главное окно руководителя: кто сейчас работает, что делает, где
// следы. Слева команда с сегодняшними счётчиками, справа лента событий. Лента сама
// обновляется раз в 45 секунд и не дёргает прокрутку: новые строки вырастают сверху.
import { api } from "../kit/api";
import { ago, dayTitle, esc, n, plural } from "../kit/dom";
import { preserve, type Section } from "../kit/shell";
import { flagsHtml, officeChips, openPerson, problem, rangeChips, rangeQuery, staleNote, wireRange, type Range } from "./common";

const KINDS: [string, string][] = [["visit", "Выезды"], ["view", "Открыл карточку"], ["photo", "Фото"], ["login", "Входы"]];
const state = {
  range: { preset: "today" } as Range,
  office: "",
  employee: "",
  kinds: new Set(["visit", "view", "photo", "login"]),
  onlyFlags: false,
  q: "",
};
let timer = 0;
let seen = new Set<string>();
let last: any = null;

function eventHtml(e: any, fresh: boolean): string {
  const who = `<span class="who">${esc(e.employee)}</span>`;
  let line1 = "";
  if (e.kind === "visit") {
    line1 = `${who} — выезд к <b>${esc(e.debtor || e.rep)}</b>: ${esc(e.title)}${e.agreed ? ` · ${esc(e.agreed)}` : ""}`;
  } else {
    line1 = `${who} ${esc(e.title)}${e.debtor ? ` — ${esc(e.debtor)}` : ""}`;
  }
  const line2 = e.kind === "visit" ? esc(e.detail || "") : "";
  const side = [e.office ? esc(e.office) : "", e.fill_s != null && e.kind === "visit" ? `карточка ${Math.max(1, Math.round(e.fill_s / 60))} мин` : ""]
    .filter(Boolean).join("<br>");
  const pick = e.rep && e.kind !== "login";
  return `<div class="ev ${esc(e.kind)}${pick ? " pick" : ""}${fresh ? " fresh" : ""}" data-ev="${esc(e.id)}">
    <time>${esc(e.time || "")}</time><span class="mark"></span>
    <div><div class="line1">${line1}</div>${line2 ? `<div class="line2">${line2}</div>` : ""}${flagsHtml(e.flags)}</div>
    <div class="side">${side}</div></div>`;
}

function peopleHtml(rows: any[]): string {
  const now = Date.now();
  const list = rows.map((r) => {
    const online = r.last_seen && now - Date.parse(r.last_seen) < 15 * 60000;
    const meta = [
      r.first ? `${esc(r.first)}–${esc(r.last)}` : "выездов нет",
      r.far ? `<span class="warn">далеко ${n(r.far)}</span>` : "",
      r.late ? `<span class="warn">позже ${n(r.late)}</span>` : "",
    ].filter(Boolean).join(" · ");
    return `<button class="person-row" data-emp="${esc(r.employee)}" aria-pressed="${state.employee === r.employee}">
      <span class="nm"><span class="seen-dot${online ? " on" : ""}"></span>${esc(r.employee)}</span><span class="num">${n(r.visits)}</span>
      <span class="meta"><span>${meta}</span><span style="margin-left:auto">${esc(ago(r.last_seen))}</span></span></button>`;
  }).join("");
  return `<div class="people">
    <div class="section-head" style="margin:4px 0 6px"><h2 class="section-title">Команда</h2>
      <span class="section-note">${n(rows.length)} ${plural(rows.length, "человек", "человека", "человек")}</span></div>
    ${state.employee ? `<button class="link" data-emp="" style="margin:0 0 8px 10px;font-size:13.5px">← вся команда</button>` : ""}
    ${list || '<div class="muted" style="padding:12px 10px">Никого в этом офисе</div>'}</div>`;
}

function feedHtml(events: any[], today: string): string {
  const q = state.q.toLowerCase();
  const list = events.filter((e) => (!state.onlyFlags || (e.flags && e.flags.length))
    && (!q || `${e.employee} ${e.debtor} ${e.rep} ${e.detail}`.toLowerCase().includes(q)));
  if (!list.length) {
    return `<div class="empty"><b>Тихо</b>${state.onlyFlags ? "Событий с пометками за это окно нет." : "За это окно событий нет."}</div>`;
  }
  let out = "";
  let day = "";
  for (const e of list) {
    if (e.day !== day) { day = e.day; out += `<div class="feed-day">${esc(dayTitle(day, today))}</div>`; }
    out += eventHtml(e, seen.size > 0 && !seen.has(e.id));
  }
  return out;
}

async function load(root: HTMLElement, quiet = false) {
  const q: Record<string, unknown> = { ...rangeQuery(state.range), office: state.office, employee: state.employee,
    kinds: [...state.kinds].join(","), limit: 400 };
  const r = await api("/api/manager/activity", q);
  if (!root.isConnected) return;
  if (!r.ok) {
    if (!quiet) root.innerHTML = problem(r.text || "Не загрузилось");
    return;
  }
  const d = r.data;
  last = d;
  const today = d.range?.today || "";
  const toolbar = `<div class="toolbar">${rangeChips(state.range, d.range, ["today", "yesterday", "7d"])}
      <span class="spacer"></span>
      <input class="input search" id="act-q" placeholder="Поиск: сотрудник, должник, договор" value="${esc(state.q)}">
    </div>
    <div class="toolbar">${officeChips(d.offices, state.office)}<span class="spacer"></span>
      <div class="chips">${KINDS.map(([k, t]) => `<button class="chip toggle" data-kind="${k}" aria-pressed="${state.kinds.has(k)}">${t}</button>`).join("")}
      <span class="chip-sep"></span><button class="chip toggle" data-flags aria-pressed="${state.onlyFlags}">Только с пометками</button></div>
    </div>`;
  const body = `${staleNote(r.stale)}<div class="split">${peopleHtml(d.employees)}<div class="feed" id="feed">${feedHtml(d.events, today)}</div></div>`;
  const draw = () => {
    root.innerHTML = toolbar + body;
    wire(root);
  };
  if (quiet) preserve(draw); else draw();
  seen = new Set((d.events as any[]).map((e) => e.id));
}

function wire(root: HTMLElement) {
  const redraw = () => void load(root);
  wireRange(root, state.range, redraw);
  root.querySelectorAll<HTMLButtonElement>("[data-office]").forEach((b) => b.addEventListener("click", () => {
    state.office = b.dataset.office || ""; state.employee = ""; redraw();
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-emp]").forEach((b) => b.addEventListener("click", () => {
    state.employee = state.employee === b.dataset.emp ? "" : (b.dataset.emp || ""); redraw();
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-kind]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.kind!;
    if (state.kinds.has(k) && state.kinds.size > 1) state.kinds.delete(k); else state.kinds.add(k);
    redraw();
  }));
  root.querySelector("[data-flags]")?.addEventListener("click", () => { state.onlyFlags = !state.onlyFlags; redrawFeed(root); });
  const qi = root.querySelector<HTMLInputElement>("#act-q");
  qi?.addEventListener("input", () => { state.q = qi.value; redrawFeed(root); });
  root.querySelector("#feed")?.addEventListener("click", (e) => {
    const row = (e.target as HTMLElement).closest<HTMLElement>(".ev.pick");
    if (!row || !last) return;
    const ev = (last.events as any[]).find((x) => x.id === row.dataset.ev);
    if (!ev) return;
    root.querySelectorAll('.ev[aria-selected="true"]').forEach((x) => x.removeAttribute("aria-selected"));
    row.setAttribute("aria-selected", "true");
    void openPerson(ev.employee, ev.rep, ev.day);
  });
}

function redrawFeed(root: HTMLElement) {
  const f = root.querySelector("#feed");
  if (f && last) {
    f.innerHTML = feedHtml(last.events, last.range?.today || "");
    root.querySelector('[data-flags]')?.setAttribute("aria-pressed", String(state.onlyFlags));
  }
}

export const activity: Section = {
  id: "activity",
  title: "Активность",
  icon: "activity",
  kicker: "что происходит в поле прямо сейчас",
  async render(root) {
    seen = new Set();
    root.innerHTML = '<div class="skeleton" style="width:40%"></div><div class="skeleton"></div><div class="skeleton" style="width:70%"></div>';
    await load(root);
    clearInterval(timer);
    timer = window.setInterval(() => {
      if (document.hidden || !root.isConnected) return;
      void load(root, true);
    }, 45000);
  },
  leave() { clearInterval(timer); },
};
