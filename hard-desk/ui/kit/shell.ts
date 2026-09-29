// Каркас окна: шапка с кнопками окна, полка разделов, заголовок, центр с физикой
// прокрутки Hélène и панель справа (карточка должника, разбор сотрудника).
import { Scroller } from "./scroller";
import { $, esc, icon } from "./dom";
import { hard, onLink } from "./api";

export interface Section {
  id: string;
  title: string;
  icon: string;
  kicker?: string;
  bottom?: boolean;
  badge?: () => string;
  render(el: HTMLElement, params: Record<string, unknown>): void | Promise<void>;
  leave?(): void;
}

let sections: Section[] = [];
let current: Section | null = null;
let currentParams: Record<string, unknown> = {};
let viewScroller: Scroller | null = null;
let panelScroller: Scroller | null = null;
let renderSeq = 0;

export function mountShell(sign: string): void {
  const app = $("app");
  $("sign").innerHTML = sign;
  document.querySelectorAll<HTMLButtonElement>("[data-win]").forEach((b) =>
    b.addEventListener("click", () => hard.win(b.dataset.win!)));
  $("chrome").addEventListener("dblclick", (e) => {
    if ((e.target as HTMLElement).closest("button")) return;
    hard.win("maximize");
  });
  $("toggle-rail").addEventListener("click", () => toggleRail());
  $("panel-close").addEventListener("click", () => closePanel());
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === "b") { e.preventDefault(); toggleRail(); }
    if (e.key === "Escape" && app.classList.contains("with-panel") && !document.getElementById("lightbox")) closePanel();
  });
  viewScroller = new Scroller($("view"), $("view-inner"), { feel: "smooth", overscroll: "rubber", stick: false });
  panelScroller = new Scroller($("panel-body"), $("panel-inner"), { feel: "smooth", overscroll: "rubber", stick: false });
  onLink((s) => {
    const pill = $("state");
    pill.dataset.level = s.level;
    $("state-text").textContent = s.text;
  });
  try {
    if (localStorage.getItem("hard.rail") === "0") app.classList.add("rail-collapsed");
  } catch { /* */ }
}

function toggleRail() {
  const app = $("app");
  app.classList.toggle("rail-collapsed");
  try { localStorage.setItem("hard.rail", app.classList.contains("rail-collapsed") ? "0" : "1"); } catch { /* */ }
}

export function bare(on: boolean): void {
  $("app").classList.toggle("bare", on);
  $("head").hidden = on;
}

export function setSections(list: Section[]): void {
  sections = list;
  drawRail();
}

export function drawRail(): void {
  const item = (s: Section) => `<button class="rail-item" type="button" data-go="${esc(s.id)}"
      ${current?.id === s.id ? 'aria-current="page"' : ""}>${icon(s.icon)}<span>${esc(s.title)}</span>
      ${s.badge ? `<span class="badge">${esc(s.badge())}</span>` : ""}</button>`;
  $("rail-nav").innerHTML = sections.filter((s) => !s.bottom).map(item).join("");
  $("rail-bottom").innerHTML = sections.filter((s) => s.bottom).map(item).join("");
  document.querySelectorAll<HTMLButtonElement>("#rail [data-go]").forEach((b) =>
    b.addEventListener("click", () => go(b.dataset.go!)));
}

export function setWho(html: string): void {
  $("rail-who").innerHTML = html;
}

export function setHead(title: string, kicker = "", tools = ""): void {
  $("head-title").textContent = title;
  $("head-kicker").textContent = kicker;
  $("head-tools").innerHTML = tools;
}

export async function go(id: string, params: Record<string, unknown> = {}): Promise<void> {
  const s = sections.find((x) => x.id === id) ?? sections[0];
  if (!s) return;
  if (current && current !== s) current.leave?.();
  const same = current === s;
  current = s;
  currentParams = params;
  drawRail();
  if (!same) closePanel();
  setHead(s.title, s.kicker ?? "");
  const host = $("view-inner");
  const page = document.createElement("div");
  page.className = "page";
  const seq = ++renderSeq;
  host.replaceChildren(page);
  if (!same) viewScroller?.scrollTo(0, false);
  try { localStorage.setItem("hard.section", s.id); } catch { /* */ }
  await s.render(page, params);
  if (seq !== renderSeq) return;
}

export function refresh(): Promise<void> {
  return current ? go(current.id, currentParams) : Promise.resolve();
}

export function currentId(): string {
  return current?.id ?? "";
}

/** Правка содержимого без прыжка прокрутки. */
export function preserve(fn: () => void): void {
  if (viewScroller) viewScroller.preserve(fn);
  else fn();
}

export function openPanel(titleHtml: string, bodyHtml = '<div class="skeleton"></div><div class="skeleton"></div>'): HTMLElement {
  $("panel-title").innerHTML = titleHtml;
  const inner = $("panel-inner");
  inner.innerHTML = `<div class="page">${bodyHtml}</div>`;
  $("app").classList.add("with-panel");
  panelScroller?.scrollTo(0, false);
  return inner.firstElementChild as HTMLElement;
}

export function closePanel(): void {
  $("app").classList.remove("with-panel");
  document.querySelectorAll('[aria-selected="true"]').forEach((el) => el.removeAttribute("aria-selected"));
}

export function panelOpen(): boolean {
  return $("app").classList.contains("with-panel");
}
