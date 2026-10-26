// Пульт аналитика: обращения к питон-стороне. adm() — admin-API сервера тем же
// кодом, что у Qt-пульта (токен устройства + отпечаток машины); fn() — готовые
// формулировки из pult.core по имени.
import { hard } from "../kit/api";
import { $, esc, toast } from "../kit/dom";

type Link = { level: "live" | "warn" | "off" | "busy"; text: string };
const listeners = new Set<(s: Link) => void>();
export function onAdminLink(fn: (s: Link) => void): void { listeners.add(fn); }

export class Fail extends Error {}

export async function adm<T = any>(method: string, path: string, body?: unknown, timeout = 30): Promise<T> {
  for (const fn of listeners) fn({ level: "busy", text: "Обновляю…" });
  const r = await hard.local("/admin", { method, path, body, timeout });
  if (!r.ok) {
    const off = /Нет связи|сертификат/i.test(r.error || "");
    for (const fn of listeners) fn({ level: off ? "off" : "warn", text: off ? "Нет связи с сервером" : "Сервер ответил ошибкой" });
    throw new Fail(r.error || "не вышло");
  }
  for (const fn of listeners) fn({ level: "live", text: "На связи · " + new Date().toTimeString().slice(0, 5) });
  return r.data as T;
}

export async function fn<T = any>(name: string, ...args: unknown[]): Promise<T> {
  const r = await hard.local("/fn", { name, args });
  if (!r.ok) throw new Fail(r.error || name);
  return r.data as T;
}

export async function local<T = any>(path: string, body: unknown = {}): Promise<T> {
  const r = await hard.local(path, body);
  if (!r.ok) throw new Fail(r.error || path);
  return r.data as T;
}

/** Keep the request id before starting. A lost start response is recovered by id. */
export async function runJob(path: string, body: unknown, log?: (s: string) => void): Promise<any> {
  const id = crypto.randomUUID();
  localStorage.setItem("hard:last-job", id);
  try {
    await local(path, { ...(body as object), request_id: id });
  } catch {
    // A lost response can follow a committed start. Only read this UUID from now on.
    log?.(`Проверяю состояние операции №${id} после потери ответа…`);
  }
  for (;;) {
    await new Promise(r => setTimeout(r, 700));
    let j: any;
    try {
      j = await local("/job", { id });
    } catch (error) {
      const message = String((error as Error).message || error);
      if (/нет такой задачи|задача не найдена|ID задачи относится|другому контуру|другому.*запросу/i.test(message))
        throw new Fail(`Операция №${id}: ${message}. Сверьте журнал задач перед повторным запуском.`);
      log?.(`Восстанавливаю связь с операцией №${id}. Повторный запуск не выполняется.`);
      continue;
    }
    if (!j || !["running", "done", "failed", "uncertain"].includes(j.state)) {
      log?.(`Проверяю подтверждение операции №${id}…`);
      continue;
    }
    if (log && j.log?.length) log(j.log[j.log.length - 1]);
    if (j.state === "done") return j.result;
    if (["failed", "uncertain"].includes(j.state)) throw new Fail(`Операция №${id}: ${j.error || j.state}`);
  }
}

/** Ошибку — словами в тост, без трейсбеков. */
export function guard<A extends unknown[]>(f: (...a: A) => Promise<unknown>) {
  return async (...a: A) => {
    try { await f(...a); } catch (e) { toast(String((e as Error).message || e)); }
  };
}

/** Таблица «как в Qt-пульте»: колонки из pult.core, строки — кортежи. */
export function tableHtml(cols: [string, string, number?][], rows: unknown[][], opts: {
  numeric?: Set<string>; pick?: boolean; key?: number; selected?: string; check?: Set<string>;
} = {}): string {
  const num = opts.numeric ?? new Set();
  const th = cols.map(([k, t]) => `<th class="${num.has(k) ? "" : "l"}" data-col="${esc(k)}">${esc(t)}</th>`).join("");
  const body = rows.map((r) => {
    const key = String(r[opts.key ?? 0] ?? "");
    const tds = cols.map(([k], i) => `<td class="${num.has(k) ? "" : "l"}">${esc(r[i] ?? "")}</td>`).join("");
    const box = opts.check ? `<td class="l" style="width:28px"><input type="checkbox" data-check="${esc(key)}" ${opts.check.has(key) ? "checked" : ""}></td>` : "";
    return `<tr class="${opts.pick ? "pick" : ""}" data-key="${esc(key)}"${opts.selected === key ? ' aria-selected="true"' : ""}>${box}${tds}</tr>`;
  }).join("");
  return `<div class="table-wrap"><table class="grid"><thead><tr>${opts.check ? "<th></th>" : ""}${th}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/** Сортировка по заголовку и поиск — общие для всех таблиц аналитика. */
export function sortRows(rows: unknown[][], col: number, dir: number): unknown[][] {
  return rows.slice().sort((a, b) => {
    const x = a[col], y = b[col];
    const nx = Number(String(x).replace(/\s/g, "")), ny = Number(String(y).replace(/\s/g, ""));
    const v = Number.isFinite(nx) && Number.isFinite(ny) && String(x) !== "" && String(y) !== "" ? nx - ny
      : String(x ?? "").localeCompare(String(y ?? ""), "ru");
    return v * dir;
  });
}

export function kvHtml(pairs: [string, unknown][]): string {
  return `<div class="kpis">${pairs.map(([k, v]) => `<div class="kpi"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("")}</div>`;
}

export function chipsHtml(items: { key: string; text: string; on: boolean }[], attr: string, toggle = false): string {
  return `<div class="chips">${items.map((c) => `<button class="chip${toggle ? " toggle" : ""}" data-${attr}="${esc(c.key)}" aria-pressed="${c.on}">${esc(c.text)}</button>`).join("")}</div>`;
}

export function note(text: string): string {
  return text ? `<p class="muted" style="font-size:13.5px;max-width:880px;margin:6px 0 0;line-height:1.55;white-space:pre-line">${esc(text)}</p>` : "";
}

export function linkSheet(title: string, url: string, text: string): void {
  const box = document.createElement("div");
  box.id = "ask";
  box.innerHTML = `<div class="sheet" style="width:min(560px,92vw)"><h3>${esc(title)}</h3>
    <div class="url" style="font-size:14px;padding:12px 14px;border:var(--lw) solid var(--line-ink);border-radius:14px;background:var(--surface)">${esc(url)}</div>
    ${text ? `<p style="margin-top:12px;font-size:14px;white-space:pre-line">${esc(text)}</p>` : ""}
    <div class="row"><button class="btn" data-close>Закрыть</button><button class="btn primary" data-copy>Копировать</button></div></div>`;
  box.addEventListener("click", (e) => { if (e.target === box) box.remove(); });
  box.querySelector("[data-close]")!.addEventListener("click", () => box.remove());
  box.querySelector("[data-copy]")!.addEventListener("click", async () => { await hard.copy(url); toast("Ссылка скопирована"); });
  document.body.appendChild(box);
}

export function field(id: string, value = "", placeholder = "", width = 280): string {
  return `<input class="input" id="${esc(id)}" value="${esc(value)}" placeholder="${esc(placeholder)}" style="width:${width}px">`;
}

export function val(id: string): string {
  return ($(id) as HTMLInputElement | null)?.value?.trim() ?? "";
}

/** Одна функция pult.core по списку аргументов — одной поездкой. */
export async function fnMap<T = any>(name: string, list: unknown[][]): Promise<T[]> {
  const r = await hard.local("/fn", { name, map: list });
  if (!r.ok) throw new Fail(r.error || name);
  return r.data as T[];
}
