// «Вход сотрудников»: персональные ссылки входа в приложение. В контуре без Telegram
// это единственный путь сотрудника в систему. Таблица с фильтрами и галочками,
// массовые действия — к отмеченным, а если не отмечено никого — к показанным:
// выдать тем, у кого нет, выдать заново, сбросить доступ, скопировать или скачать
// ссылки разом.
import { hard } from "../kit/api";
import { ask, esc, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { DataTable, type Col } from "../kit/table";
import { showIssued } from "../kit/issued";
export { showIssued };
import { adm, chipsHtml, fn, fnMap, guard, kvHtml, note } from "./lib";

type Row = { p: any; t: unknown[] };
const st = { filter: "all", uses: 2, revoked: new Set<string>(), base: "" };
let people: any[] = [];
let byName: Record<string, any> = {};
let table: DataTable<Row> | null = null;

/** Выдать вход одному человеку и показать ссылку тем же листом. */
export async function issueLink(body: { employee?: string; telegram_id?: number | string; kind?: string; uses?: number }): Promise<void> {
  const d = await adm("POST", "/admin/app_link.json", { uses: st.uses, ...body });
  if (!d || !d.url) { toast(String(d?.error || "Сервер не выдал ссылку")); return; }
  await showIssued("Ссылка входа", [{ employee: body.employee || d.employee || "", who: "", url: d.url, uses: d.max_uses, expires: String(d.expires_at || "").slice(0, 10) }]);
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
  const tuples = await fnMap<unknown[]>("links.link_row", visible.map((p) => [p]));
  const rows: Row[] = visible.map((p, i) => ({ p, t: tuples[i] }));
  root.innerHTML = `
    ${kvHtml(tiles)}${note(summary)}
    <div class="toolbar" style="margin-top:16px">${chipsHtml((FILTERS as [string, string][]).map(([k, t]) => ({ key: k, text: `${t} · ${counts[k] || 0}`, on: st.filter === k })), "filter")}
      <span class="spacer"></span><span class="muted" style="font-size:13px">Открытий у новой ссылки:</span>
      ${chipsHtml([1, 2, 5].map((x) => ({ key: String(x), text: String(x), on: st.uses === x })), "uses")}</div>
    <div class="bulk" id="bulk"></div>
    <div id="tbl"></div>`;
  root.querySelectorAll<HTMLButtonElement>("[data-filter]").forEach((b) => b.addEventListener("click", () => { st.filter = b.dataset.filter!; void draw(root); }));
  root.querySelectorAll<HTMLButtonElement>("[data-uses]").forEach((b) => b.addEventListener("click", () => { st.uses = Number(b.dataset.uses); void draw(root); }));
  const cols: Col<Row>[] = (COLS as [string, string][]).map(([k, t], i) => ({ key: k, title: t, value: (r: Row) => r.t[i] }));
  table = new DataTable<Row>(root.querySelector("#tbl")!, {
    id: "analyst-links", cols, key: (r) => r.p.employee, select: true, exportName: "Вход сотрудников",
    onSelect: () => drawBulk(root),
  });
  table.setRows(rows);
}

function drawBulk(root: HTMLElement) {
  const bar = root.querySelector<HTMLElement>("#bulk");
  if (!bar || !table) return;
  const sc = table.scope();
  const people = sc.rows.map((r) => r.p);
  const lacking = people.filter((p) => !p.alive && !p.entered).length;
  // Копировать есть смысл только живые ссылки: израсходованная никого не впустит.
  const live = people.filter((p) => p.alive && (p.url || p.code));
  const withLink = live.length;
  const canRevoke = people.filter((p) => p.telegram_id && (p.code || p.entered)).length;
  bar.innerHTML = `<span class="who">Действия — <b>${esc(sc.label)}</b></span>
    <button class="btn small primary" data-b="issue" ${lacking ? "" : "disabled"}>Выдать тем, у кого нет (${lacking})</button>
    <button class="btn small" data-b="reissue" ${people.length ? "" : "disabled"}>Выдать заново</button>
    <button class="btn small" data-b="copy" ${withLink ? "" : "disabled"}>Скопировать живые ссылки (${withLink})</button>
    <button class="btn small" data-b="xlsx" ${people.length ? "" : "disabled"}>Скачать Excel</button>
    <button class="btn small danger" data-b="revoke" ${canRevoke ? "" : "disabled"}>Сбросить доступ (${canRevoke})</button>
    <span class="spacer"></span>
    ${sc.picked ? '<button class="link" data-b="clear">снять отметки</button>' : ""}
    <button class="ticon" data-b="reload" title="Обновить список"><svg viewBox="0 0 18 18" width="15" height="15"><path d="M14.5 8.5a5.5 5.5 0 1 1-1.6-3.9M14.5 2.5v3.6h-3.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>`;
  const on = (k: string, f: () => Promise<void>) => bar.querySelector(`[data-b="${k}"]`)?.addEventListener("click", guard(f));
  on("clear", async () => table!.clearSelection());
  on("reload", async () => { await load(root); toast("Обновлено"); });
  on("issue", async () => {
    const names = people.filter((p) => !p.alive && !p.entered).map((p) => p.employee);
    if (!(await ask("Выдать вход", await fn("links.bulk_confirm_text", names.length, st.uses, 0, "тем, у кого его нет"), "Выдать"))) return;
    await bulkIssue(root, names);
  });
  on("reissue", async () => {
    const names = people.map((p) => p.employee);
    const entered = people.filter((p) => p.entered).length;
    if (!(await ask("Выдать заново", await fn("links.bulk_confirm_text", names.length, st.uses, entered, sc.label.replace(/ \(\d+\)$/, "")), "Выдать заново"))) return;
    await bulkIssue(root, names);
  });
  on("copy", async () => {
    const lines: string[] = [];
    for (const p of live) {
      const url = p.url || (await fn<string>("links.link_url", p, st.base));
      if (url) lines.push(`${p.employee} — ${url}`);
    }
    await hard.copy(lines.join("\n"));
    toast(`Скопировано ссылок: ${lines.length}`);
  });
  on("xlsx", async () => {
    const tuples = await fnMap<unknown[]>("links.link_row", people.map((p) => [p]));
    const COLS = (await fn("links.COLUMNS")) as [string, string][];
    const res = await hard.xlsx("Вход сотрудников", [...COLS.map(([, t]) => t), "Ссылка"],
      people.map((p, i) => [...COLS.map((_, j) => (tuples[i][j] ?? "") as string), p.url || ""]));
    if (res.ok && res.file) { toast("Книга сохранена"); void hard.open(res.file); }
  });
  on("revoke", async () => {
    const ids = people.filter((p) => p.telegram_id && (p.code || p.entered)).map((p) => p.telegram_id);
    if (!(await ask("Сбросить доступ?", `${ids.length} чел. выйдут из приложения на всех телефонах, их ссылки перестанут работать. Вернуть вход — новой ссылкой.`, "Сбросить", true))) return;
    const d = await adm("POST", "/admin/app_revoke_bulk.json", { telegram_ids: ids, scope: "all" }, 120);
    for (const p of people) if (ids.includes(p.telegram_id)) { st.revoked.add(String(p.telegram_id)); st.revoked.add(p.employee); }
    toast(`Доступ сброшен: ${d.people} чел.`);
    table!.clearSelection();
    await load(root);
  });
}

async function bulkIssue(root: HTMLElement, names: string[]) {
  const items = await fn("links.bulk_items", byName, names);
  toast("Выдаю ссылки…");
  const d = await adm("POST", "/admin/app_links_bulk.json", { items, uses: st.uses }, 180);
  const rows = await fn<any[]>("links.bulk_rows", d.results || []);
  for (const n of names) { st.revoked.delete(n); }
  table?.clearSelection();
  await load(root);
  await showIssued(`Ссылки входа · ${rows.length} чел.`, rows);
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
