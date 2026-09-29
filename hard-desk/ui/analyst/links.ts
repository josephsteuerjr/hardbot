// «Вход» аналитика: персональные ссылки входа PWA. В контуре без Telegram это
// единственный путь сотрудника в систему: таблица людей со статусом входа,
// фильтры, выдать/перевыдать/отозвать, «всем, у кого нет», сохранить списком.
import { hard } from "../kit/api";
import { ask, esc, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { adm, chipsHtml, fn, fnMap, guard, kvHtml, linkSheet, note, sortRows, tableHtml } from "./lib";

const st = { filter: "all", q: "", uses: 2, selected: "", sort: { col: 0, dir: 1 }, revoked: new Set<string>(), base: "" };
let people: any[] = [];
let byName: Record<string, any> = {};

/** Выдать вход одному человеку и показать ссылку листом (общий путь разделов). */
export async function issueLink(body: { employee?: string; telegram_id?: number | string; kind?: string; uses?: number }): Promise<void> {
  const d = await adm("POST", "/admin/app_link.json", { uses: st.uses, ...body });
  if (!d || !d.url) { toast(String(d?.error || "Сервер не выдал ссылку")); return; }
  const text = await fn("people.link_note", d);
  linkSheet("Ссылка входа" + (body.employee ? " · " + body.employee : ""), d.url, text);
}

async function load(root: HTMLElement) {
  const [emps, links, workers] = await Promise.all([
    adm("GET", "/admin/employees").then((d) => d.employees || []),
    adm("GET", "/admin/app_links.json").then((d) => d.links || []).catch(() => []),
    adm("GET", "/admin/activity").then((d) => d.workers || []).catch(() => []),
  ]);
  people = await fn("links.merge_rows", emps, links, workers, [...st.revoked]);
  byName = Object.fromEntries(people.map((p) => [p.employee, p]));
  const u = (links as any[]).find((l) => l.url)?.url;
  if (u) st.base = await fn("links.base_of", u);
  await draw(root);
}

async function draw(root: HTMLElement) {
  const [counts, FILTERS, COLS, tiles, summary] = await Promise.all([
    fn("links.filter_counts", people), fn("links.FILTERS"), fn("links.COLUMNS"),
    fn("links.summary_tiles", people), fn("links.link_summary", people)]);
  const shown = await fnMap<boolean>("links.matches", people.map((p) => [p, st.filter]));
  const visible = people.filter((_, i) => shown[i]);
  let rows = await fnMap<unknown[]>("links.link_row", visible.map((p) => [p]));
  if (st.q) rows = rows.filter((r) => r.join(" ").toLowerCase().includes(st.q.toLowerCase()));
  rows = sortRows(rows, st.sort.col, st.sort.dir);
  const missing = counts.none || 0;
  const cur = byName[st.selected];
  root.innerHTML = `
    ${kvHtml(tiles)}
    ${note(summary)}
    <div class="toolbar" style="margin-top:18px">${chipsHtml((FILTERS as [string, string][]).map(([k, t]) => ({ key: k, text: `${t} · ${counts[k] || 0}`, on: st.filter === k })), "filter")}
      <span class="spacer"></span><input class="input search" id="ln-q" placeholder="Поиск" value="${esc(st.q)}"></div>
    <div class="toolbar">
      <span class="muted" style="font-size:13.5px">Обменов у ссылки:</span>
      ${chipsHtml([1, 2, 5].map((x) => ({ key: String(x), text: String(x), on: st.uses === x })), "uses")}
      <span class="spacer"></span>
      <button class="btn primary" data-all ${missing ? "" : "disabled"}>${missing ? `Выдать вход всем, у кого его нет (${missing})` : "У всех есть вход"}</button>
    </div>
    ${cur ? `<div class="notice"><span class="grow"><b>${esc(cur.employee)}</b></span>
      <button class="btn small" data-issue ${cur.alive ? "disabled" : ""}>Выдать вход</button>
      <button class="btn small" data-again ${cur.code ? "" : "disabled"}>Выдать заново</button>
      <button class="btn small" data-copy ${cur.code ? "" : "disabled"}>Копировать ссылку</button>
      <button class="btn small danger" data-revoke ${cur.telegram_id && (cur.code || cur.entered) ? "" : "disabled"}>Отозвать</button></div>` : '<p class="muted" style="font-size:13.5px">Выберите человека в таблице — появятся действия.</p>'}
    <div id="tbl">${tableHtml(COLS, rows, { pick: true, selected: st.selected })}</div>`;
  wire(root);
}

function wire(root: HTMLElement) {
  root.querySelectorAll<HTMLButtonElement>("[data-filter]").forEach((b) => b.addEventListener("click", () => { st.filter = b.dataset.filter!; void draw(root); }));
  root.querySelectorAll<HTMLButtonElement>("[data-uses]").forEach((b) => b.addEventListener("click", () => { st.uses = Number(b.dataset.uses); void draw(root); }));
  const q = root.querySelector<HTMLInputElement>("#ln-q");
  q?.addEventListener("change", () => { st.q = q.value; void draw(root); });
  root.querySelectorAll<HTMLElement>("#tbl th").forEach((th, i) => th.addEventListener("click", () => {
    st.sort = { col: i, dir: st.sort.col === i ? -st.sort.dir : 1 }; void draw(root);
  }));
  root.querySelectorAll<HTMLElement>("#tbl tr[data-key]").forEach((tr) => tr.addEventListener("click", () => {
    st.selected = tr.dataset.key!; void draw(root);
  }));
  const cur = byName[st.selected];
  root.querySelector("[data-issue]")?.addEventListener("click", guard(async () => {
    await issueLink({ employee: cur.employee, kind: cur.kind || "agent", ...(cur.telegram_id ? { telegram_id: cur.telegram_id } : {}) });
    await load(root);
  }));
  root.querySelector("[data-again]")?.addEventListener("click", guard(async () => {
    if (!(await ask("Выдать заново?", await fn("links.reissue_text", cur), "Выдать"))) return;
    await issueLink({ employee: cur.employee, kind: cur.kind || "agent", ...(cur.telegram_id ? { telegram_id: cur.telegram_id } : {}) });
    await load(root);
  }));
  root.querySelector("[data-copy]")?.addEventListener("click", guard(async () => {
    const url = await fn("links.link_url", cur, st.base);
    if (!url) { toast("Ссылку собрать не из чего — выдайте заново"); return; }
    await hard.copy(url); toast("Ссылка скопирована: " + cur.employee);
  }));
  root.querySelector("[data-revoke]")?.addEventListener("click", guard(async () => {
    if (!(await ask("Отозвать вход?", await fn("links.revoke_text", cur), "Отозвать", true))) return;
    await adm("POST", "/admin/app_revoke", { telegram_id: cur.telegram_id, scope: "all" });
    st.revoked.add(String(cur.telegram_id)); st.revoked.add(cur.employee);
    toast("Вход отозван: " + cur.employee);
    await load(root);
  }));
  root.querySelector("[data-all]")?.addEventListener("click", guard(async () => {
    const names = people.filter((p) => p && true).map((p) => p.employee);
    const need = (await fnMap<boolean>("links.matches", people.map((p) => [p, "none"]))).map((m, i) => (m ? names[i] : "")).filter(Boolean);
    if (!need.length) { toast("У всех уже есть вход"); return; }
    if (!(await ask("Выдать вход всем, у кого его нет", await fn("links.bulk_confirm_text", need.length, st.uses, 0, "всем, у кого его нет"), "Выдать"))) return;
    const items = await fn("links.bulk_items", byName, need);
    const d = await adm("POST", "/admin/app_links_bulk.json", { items, uses: st.uses }, 180);
    const rows = await fn("links.bulk_rows", d.results || []);
    const text = (rows as any[]).map((r) => `${r.employee} — ${r.url || r.error || ""}`).join("\n");
    await hard.copy(text);
    toast((await fn<string>("links.bulk_summary", rows)).split(".")[0] + " · список скопирован");
    await load(root);
  }));
}

export const links: Section = {
  id: "links",
  title: "Вход сотрудников",
  icon: "links",
  kicker: "персональные ссылки входа в приложение",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div><div class="skeleton" style="width:60%"></div>';
    try { await load(root); } catch (e) { root.innerHTML = `<div class="notice err">${esc((e as Error).message)}</div>`; }
  },
};
