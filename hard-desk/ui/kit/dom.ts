// Мелочи страницы: экранирование, числа словами, значки. Разметка — шаблонами,
// любой текст из данных проходит через esc().

export const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

export function esc(v: unknown): string {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export function n(v: unknown): string {
  const x = Number(v);
  if (!Number.isFinite(x)) return "—";
  return x.toLocaleString("ru-RU");
}

export function pct(v: unknown, digits = 0): string {
  const x = Number(v);
  if (!Number.isFinite(x)) return "—";
  return (x * 100).toFixed(digits).replace(".", ",") + "%";
}

export function plural(x: number, one: string, few: string, many: string): string {
  const a = Math.abs(x) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const WD = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];

export function dayTitle(iso: string, today?: string): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  if (today) {
    const [ty, tm, td] = today.split("-").map(Number);
    const t = new Date(ty, tm - 1, td);
    const diff = Math.round((t.getTime() - dt.getTime()) / 86400000);
    if (diff === 0) return "сегодня";
    if (diff === 1) return "вчера";
  }
  return `${d} ${MONTHS[m - 1]}, ${WD[dt.getDay()]}`;
}

export function shortDate(iso: string): string {
  if (!iso) return "";
  const [, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${d} ${MONTHS[m - 1].slice(0, 3)}`;
}

export function hhmm(d: Date = new Date()): string {
  return d.toTimeString().slice(0, 5);
}

/** «5 мин назад», «вчера в 18:02» — для «последний раз в сети». */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "не заходил";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const m = Math.round((now - t) / 60000);
  if (m < 2) return "сейчас в сети";
  if (m < 60) return `${m} мин назад`;
  const h = Math.round(m / 60);
  if (h < 12) return `${h} ч назад`;
  const d = new Date(t);
  const days = Math.round(h / 24);
  if (days <= 1) return `вчера в ${hhmm(d)}`;
  return `${days} ${plural(days, "день", "дня", "дней")} назад`;
}

const I: Record<string, string> = {
  activity: '<path d="M2 9h3l2-5 3 10 2-5h4"/>',
  team: '<circle cx="6" cy="6" r="2.4"/><circle cx="12.5" cy="7" r="2"/><path d="M1.8 14c.6-2.6 2.3-3.8 4.2-3.8S9.6 11.4 10.2 14M10 11c.8-.7 1.6-1 2.5-1 1.6 0 2.8 1 3.2 3"/>',
  promises: '<rect x="2.5" y="3" width="13" height="12" rx="2"/><path d="M2.5 7h13M6 1.8v2.4M12 1.8v2.4M6 10.5l1.6 1.6L11 9"/>',
  control: '<path d="M9 1.8 3 4v4.6c0 3.6 2.6 6.2 6 7.6 3.4-1.4 6-4 6-7.6V4L9 1.8Z"/><path d="m6.5 9 1.8 1.8L12 7"/>',
  links: '<path d="M7.5 10.5 10.5 7.5M6 8.5 4.5 10a2.5 2.5 0 0 0 3.5 3.5L9.5 12M12 9.5 13.5 8A2.5 2.5 0 0 0 10 4.5L8.5 6"/>',
  report: '<path d="M4 1.8h6.5L14 5.3V16H4Z"/><path d="M10.5 1.8v3.5H14M6.5 9.5h5M6.5 12.5h5"/>',
  device: '<rect x="2" y="3" width="14" height="9.5" rx="1.6"/><path d="M6.5 15.5h5M9 12.5v3"/>',
  close: '<path d="M4 4l10 10M14 4 4 14"/>',
  back: '<path d="M11 3.5 5.5 9l5.5 5.5"/>',
  refresh: '<path d="M14.5 8.5a5.5 5.5 0 1 1-1.6-3.9M14.5 2.5v3.6h-3.6"/>',
  download: '<path d="M9 2.5v9M5.5 8 9 11.5 12.5 8M3 14.5h12"/>',
  copy: '<rect x="5.5" y="5.5" width="9" height="9" rx="1.6"/><path d="M3.5 11.5V4a.5.5 0 0 1 .5-.5h7.5"/>',
  registry: '<path d="M2.5 3.5h13v11h-13ZM2.5 7.5h13M2.5 11h13M7 3.5v11"/>',
  people: '<circle cx="9" cy="6" r="3"/><path d="M3 16c.8-3.2 3.2-4.8 6-4.8s5.2 1.6 6 4.8"/>',
  managers: '<circle cx="7" cy="6" r="2.6"/><path d="M2 15c.7-2.7 2.6-4 5-4 1.2 0 2.3.3 3.1 1M12.5 10.5l1.2 1.2 2.3-2.4"/>',
  enrich: '<path d="M3 13.5 9 4l6 9.5"/><path d="M5.5 10h7"/>',
  channels: '<path d="M3 5.5h12M3 9h12M3 12.5h7"/>',
  service: '<circle cx="9" cy="9" r="2.4"/><path d="M9 1.8v2M9 14.2v2M1.8 9h2M14.2 9h2M3.9 3.9l1.4 1.4M12.7 12.7l1.4 1.4M3.9 14.1l1.4-1.4M12.7 5.3l1.4-1.4"/>',
  today: '<circle cx="9" cy="9" r="6.5"/><path d="M9 5.5V9l2.5 1.5"/>',
};

export function icon(name: string, size = 18): string {
  return `<svg viewBox="0 0 18 18" width="${size}" height="${size}" aria-hidden="true">${I[name] ?? ""}</svg>`;
}

let toastTimer = 0;
export function toast(text: string): void {
  let t = $("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    document.body.appendChild(t);
  }
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => t.classList.remove("show"), 2600);
}

/** Вопрос «да/нет» своим листом: системный confirm() выбивается из окна. */
export function ask(title: string, text: string, ok = "Да", danger = false): Promise<boolean> {
  return new Promise((resolve) => {
    const box = document.createElement("div");
    box.id = "ask";
    box.innerHTML = `<div class="sheet" role="dialog" aria-modal="true"><h3>${esc(title)}</h3><p>${esc(text)}</p>
      <div class="row"><button class="btn" data-no>Отмена</button><button class="btn ${danger ? "danger" : "primary"}" data-yes>${esc(ok)}</button></div></div>`;
    const done = (v: boolean) => { box.remove(); document.removeEventListener("keydown", key); resolve(v); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") done(false); if (e.key === "Enter") done(true); };
    box.addEventListener("click", (e) => { if (e.target === box) done(false); });
    box.querySelector("[data-no]")!.addEventListener("click", () => done(false));
    box.querySelector("[data-yes]")!.addEventListener("click", () => done(true));
    document.addEventListener("keydown", key);
    document.body.appendChild(box);
    (box.querySelector("[data-yes]") as HTMLButtonElement).focus();
  });
}

export function lightbox(src: string): void {
  const box = document.createElement("div");
  box.id = "lightbox";
  box.innerHTML = `<img src="${esc(src)}" alt="">`;
  box.addEventListener("click", () => box.remove());
  document.addEventListener("keydown", function k(e) { if (e.key === "Escape") { box.remove(); document.removeEventListener("keydown", k); } });
  document.body.appendChild(box);
}
