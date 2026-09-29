// «Руководители» аналитика: кто видит чью команду, роль (руководитель /
// безопасник / и то и другое), вход и компьютеры с пультом руководителя —
// с отзывом по одному или всех разом.
import { ago, ask, esc, n, toast } from "../kit/dom";
import { closePanel, openPanel, type Section } from "../kit/shell";
import { hard } from "../kit/api";
import { DataTable, type Col } from "../kit/table";
import { adm, fn, guard, note } from "./lib";
import { showIssued } from "./links";

const ROLES: [string, string, string][] = [
  ["manager", "Руководитель", "видит свою команду, выдаёт вход сотрудникам"],
  ["security", "Безопасник", "видит команду и «Контроль», ничего не меняет"],
  ["full", "Руководитель и контроль", "всё сразу — для директора направления"],
];
const ROLE_TITLE = Object.fromEntries(ROLES.map(([k, t]) => [k, t]));

let data: any = null;
let staff: Record<string, string[]> = {};
type MRow = { m: any; row: any; link: any; devs: any[] };
let table: DataTable<MRow> | null = null;
let devicesKnown = true;

function linkState(l: any): string {
  if (!l) return "не выдавали";
  if (l.entered) return "вошёл";
  if (l.alive) return l.uses ? "ссылка открыта" : "ссылка выдана";
  return "ссылка истекла";
}

async function load(root: HTMLElement) {
  const [d, offices, devices, links] = await Promise.all([
    adm("GET", "/admin/managers"),
    adm("GET", "/admin/employee_cities.json").catch(() => ({})),
    adm("GET", "/admin/manager_devices.json").then((x) => x.devices || []).catch(() => null),
    adm("GET", "/admin/app_links.json").then((x) => x.links || []).catch(() => []),
  ]);
  data = d;
  devicesKnown = devices !== null;
  staff = {};
  for (const [emp, city] of Object.entries((offices.cities || {}) as Record<string, string>)) (staff[city] ||= []).push(emp);
  const linkBy: Record<string, any> = {};
  for (const l of links as any[]) linkBy[String(l.telegram_id)] = l;
  const devBy: Record<string, any[]> = {};
  for (const x of (devices || []) as any[]) (devBy[String(x.telegram_id)] ||= []).push(x);
  const rows: MRow[] = await Promise.all(((d.managers || []) as any[]).map(async (m) => ({
    m, row: await fn("managers.manager_row", m), link: linkBy[String(m.telegram_id)], devs: devBy[String(m.telegram_id)] || [] })));
  root.innerHTML = `${note("Руководитель видит свою команду в пульте и в приложении. Безопасник видит ту же команду и раздел «Контроль» (следы и контрольный обзвон), но ничего не меняет. «Контроль» руководителям не показывается.")}
    <div class="bulk" id="bulk" style="margin-top:12px"></div><div id="tbl"></div>
    ${devicesKnown ? "" : '<div class="notice" style="margin-top:14px"><span class="grow">Сервер ещё не обновлён: списка компьютеров с пультом руководителя он не отдаёт.</span></div>'}`;
  const cols: Col<MRow>[] = [
    { key: "name", title: "Руководитель", value: (r) => r.row.name,
      html: (r) => `<span class="who">${esc(r.row.name)}</span>${r.row.mismatch ? ' <span class="tag err">ФИО ≠ реестру</span>' : ""}<span class="sub">id ${esc(r.row.tid)}</span>` },
    { key: "role", title: "Роль", value: (r) => ROLE_TITLE[r.m.role || "manager"] || "Руководитель" },
    { key: "scope", title: "Кого видит", value: (r) => r.row.employees,
      html: (r) => `${esc(r.row.employees)}${r.row.cities !== "—" ? `<span class="sub">${esc(r.row.cities)}</span>` : ""}` },
    { key: "entry", title: "Вход", value: (r) => linkState(r.link) },
    { key: "seen", title: "Последний вход", value: (r) => String(r.link?.last_use_at || "").replace("T", " ").slice(0, 16) },
    { key: "pc", title: "Компьютеры", num: true, value: (r) => r.devs.filter((x) => !x.revoked_at).length },
  ];
  table = new DataTable<MRow>(root.querySelector("#tbl")!, {
    id: "analyst-managers", cols, key: (r) => String(r.m.telegram_id), select: true, exportName: "Руководители",
    onRow: (r) => edit(root, r.m, String(r.m.telegram_id), r.devs),
    onSelect: () => drawBulk(root),
    empty: "Добавьте руководителя кнопкой «Добавить».",
  });
  table.setRows(rows);
}

function drawBulk(root: HTMLElement) {
  const bar = root.querySelector<HTMLElement>("#bulk");
  if (!bar || !table) return;
  const sc = table.scope();
  const ms = sc.rows;
  const lacking = ms.filter((r) => !r.link || (!r.link.alive && !r.link.entered)).length;
  const live = ms.filter((r) => r.link?.alive && r.link?.url);
  const withAccess = ms.filter((r) => r.link?.code || r.link?.entered || r.devs.some((x) => !x.revoked_at)).length;
  bar.innerHTML = `<span class="who">Действия — <b>${esc(sc.label)}</b></span>
    <button class="btn small primary" data-b="issue" ${lacking ? "" : "disabled"}>Выдать тем, у кого нет (${lacking})</button>
    <button class="btn small" data-b="reissue" ${ms.length ? "" : "disabled"}>Выдать заново</button>
    <button class="btn small" data-b="copy" ${live.length ? "" : "disabled"}>Скопировать живые ссылки (${live.length})</button>
    <button class="btn small danger" data-b="revoke" ${withAccess ? "" : "disabled"}>Сбросить доступ (${withAccess})</button>
    <span class="spacer"></span>
    ${sc.picked ? '<button class="link" data-b="clear">снять отметки</button>' : ""}
    <button class="btn small" data-b="new">Добавить</button>
    <button class="ticon" data-b="reload" title="Обновить список"><svg viewBox="0 0 18 18" width="15" height="15"><path d="M14.5 8.5a5.5 5.5 0 1 1-1.6-3.9M14.5 2.5v3.6h-3.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>`;
  const on = (k: string, f: () => Promise<void>) => bar.querySelector(`[data-b="${k}"]`)?.addEventListener("click", guard(f));
  on("clear", async () => table!.clearSelection());
  on("reload", async () => { await load(root); toast("Обновлено"); });
  on("new", async () => edit(root, null, await fn("managers.next_local_manager_id", data.managers || []), []));
  const issue = async (list: MRow[], title: string) => {
    if (!list.length) return;
    if (!(await ask(title, `Выдать вход ${list.length} чел.? Ссылку можно открыть два раза: в пульте руководителя на компьютере и на телефоне. У кого ссылку выдавали, но не открыли, — старая перестанет работать.`, "Выдать"))) return;
    toast("Выдаю ссылки…");
    // Руководителю — только id: с ФИО сервер привязал бы его к сотруднику.
    const d = await adm("POST", "/admin/app_links_bulk.json", { items: list.map((r) => ({ telegram_id: r.m.telegram_id })), uses: 2 }, 180);
    const byId: Record<string, string> = Object.fromEntries(list.map((r) => [String(r.m.telegram_id), r.row.name]));
    const rows = ((d.results || []) as any[]).map((x) => ({ employee: byId[String(x.telegram_id)] || x.employee || String(x.telegram_id),
      who: "руководитель", url: x.url || "", uses: x.max_uses, expires: String(x.expires_at || "").slice(0, 10), error: x.error || "" }));
    table?.clearSelection();
    await load(root);
    await showIssued(`Вход руководителям · ${rows.length} чел.`, rows);
  };
  on("issue", () => issue(ms.filter((r) => !r.link || (!r.link.alive && !r.link.entered)), "Выдать вход"));
  on("reissue", () => issue(ms, "Выдать заново"));
  on("copy", async () => {
    await hard.copy(live.map((r) => `${r.row.name} — ${r.link.url}`).join("\n"));
    toast(`Скопировано ссылок: ${live.length}`);
  });
  on("revoke", async () => {
    const ids = ms.map((r) => r.m.telegram_id);
    if (!(await ask("Сбросить доступ?", `${ids.length} чел. выйдут из приложения на телефонах, их ссылки перестанут работать, пульты на компьютерах отключатся при первом выходе на связь. Вернуть вход — новой ссылкой.`, "Сбросить", true))) return;
    const d = await adm("POST", "/admin/app_revoke_bulk.json", { telegram_ids: ids, scope: "all" }, 120);
    toast(`Доступ сброшен: ${d.people} чел.`);
    table!.clearSelection();
    await load(root);
  });
}

function chipList(items: string[], on: Set<string>, attr: string, count?: (x: string) => number): string {
  return `<div class="chips" style="gap:6px">${items.map((x) => `<button class="chip toggle" data-${attr}="${esc(x)}" aria-pressed="${on.has(x)}">${esc(x)}${count && count(x) ? `<span class="n">${n(count(x))}</span>` : ""}</button>`).join("")}</div>`;
}

function edit(root: HTMLElement, m: any, tid: string, devices: any[]) {
  const form = {
    name: m?.name || "", role: m?.role || "manager", direction: m?.direction || "",
    offices: new Set<string>(m?.office_cities || []), employees: new Set<string>(m?.employees || []),
    cities: new Set<string>(m?.cities || []),
  };
  const officeList = [...new Set([...Object.keys(staff), ...form.offices])].sort();
  const empList = ((data.employees || []) as any[]).map((e) => e.employee);
  const empCount = (x: string) => ((data.employees || []) as any[]).find((e) => e.employee === x)?.contracts || 0;
  const draw = () => {
    const body = openPanel(`<h2>${esc(m ? m.name || "Руководитель" : "Новый руководитель")}</h2><div class="sub">id ${esc(tid)}</div>`,
      `<div class="block" style="margin-top:6px"><label class="muted" style="font-size:13px">Имя</label><br>
        <input class="input" id="mg-name" value="${esc(form.name)}" style="width:100%;margin-top:4px"></div>
      <div class="block"><h3>Роль</h3>${ROLES.map(([k, t, h]) => `<label class="list-row" style="cursor:pointer;padding:8px 4px">
        <input type="radio" name="mg-role" value="${k}" ${form.role === k ? "checked" : ""}><span class="grow"><span class="nm">${esc(t)}</span><br><span class="sub">${esc(h)}</span></span></label>`).join("")}</div>
      <div class="block"><h3>Кого видит</h3>
        <select class="input" id="mg-dir" style="width:100%">${[["", "— по офисам и списку —"], ["tj", "Таджикистан"], ["uz", "Узбекистан"], ["all", "СНГ (все сотрудники)"]].map(([k, t]) =>
          `<option value="${k}" ${form.direction === k ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
        ${form.direction ? `<p class="muted" style="font-size:13px">Состав направления сервер считает сам — отмечать людей не нужно.</p>` : `
        <p class="muted" style="font-size:13px;margin:12px 0 6px">Офисы (штат живой):</p>${chipList(officeList, form.offices, "office", (x) => (staff[x] || []).length)}
        <p class="muted" style="font-size:13px;margin:12px 0 6px">И поимённо:</p>${chipList(empList, form.employees, "emp", empCount)}`}</div>
      ${m ? `<div class="block"><h3>Компьютеры с пультом</h3>${devices.length ? devices.map((x) => `<div class="list-row">
          <div class="grow"><div class="nm">${esc(x.device || "компьютер")}</div><div class="sub">с ${esc(String(x.created_at || "").slice(0, 10))} · ${x.revoked_at ? "отозван, ждёт стука" : esc(ago(x.last_seen_at ? x.last_seen_at + "Z" : null))}</div></div>
          ${x.revoked_at ? '<span class="tag warn">отозван</span>' : `<button class="btn small danger" data-revoke="${x.id}">Отозвать</button>`}</div>`).join("")
          + `<div class="toolbar" style="margin-top:10px"><button class="btn small danger" data-revoke-all>Отозвать все</button></div>`
        : '<p class="muted" style="font-size:13.5px">Пульт руководителя ещё не запускали. Ключ — та же ссылка входа.</p>'}</div>` : ""}
      <div class="toolbar" style="margin-top:22px"><button class="btn primary" data-save>Сохранить</button>
        ${m ? '<span class="spacer"></span><button class="btn danger" data-del>Удалить</button>' : ""}</div>`);
    body.querySelector<HTMLInputElement>("#mg-name")!.addEventListener("input", (e) => { form.name = (e.target as HTMLInputElement).value; });
    body.querySelectorAll<HTMLInputElement>('[name="mg-role"]').forEach((r) => r.addEventListener("change", () => { form.role = r.value; }));
    body.querySelector<HTMLSelectElement>("#mg-dir")!.addEventListener("change", (e) => { form.direction = (e.target as HTMLSelectElement).value; draw(); });
    body.querySelectorAll<HTMLButtonElement>("[data-office]").forEach((b) => b.addEventListener("click", () => {
      const x = b.dataset.office!; if (form.offices.has(x)) form.offices.delete(x); else form.offices.add(x); b.setAttribute("aria-pressed", String(form.offices.has(x)));
    }));
    body.querySelectorAll<HTMLButtonElement>("[data-emp]").forEach((b) => b.addEventListener("click", () => {
      const x = b.dataset.emp!; if (form.employees.has(x)) form.employees.delete(x); else form.employees.add(x); b.setAttribute("aria-pressed", String(form.employees.has(x)));
    }));
    body.querySelector("[data-save]")?.addEventListener("click", guard(async () => {
      if (!form.name.trim()) { toast("Впишите имя"); return; }
      const b = await fn("managers.manager_body", tid, form.name, [...form.cities], form.direction ? [] : [...form.employees], form.direction,
        m ? { push_visits: m.push_visits, push_digest: m.push_digest, push_promises: m.push_promises } : null, form.direction ? [] : [...form.offices]);
      await adm("POST", "/admin/manager", { ...b, role: form.role });
      toast("Сохранено"); closePanel(); await load(root);
    }));
    body.querySelector("[data-del]")?.addEventListener("click", guard(async () => {
      if (!(await ask("Удалить руководителя?", await fn("managers.delete_text", tid) + " Его компьютеры с пультом тоже будут отключены.", "Удалить", true))) return;
      await adm("POST", "/admin/manager_delete", { telegram_id: Number(tid) });
      toast("Удалён"); closePanel(); await load(root);
    }));
    body.querySelectorAll<HTMLButtonElement>("[data-revoke]").forEach((b) => b.addEventListener("click", guard(async () => {
      if (!(await ask("Отозвать компьютер?", "Пульт на нём сотрёт у себя вход и всё сохранённое при первом выходе на связь.", "Отозвать", true))) return;
      await adm("POST", "/admin/manager_device/revoke.json", { id: Number(b.dataset.revoke) });
      toast("Отозван"); await load(root); closePanel();
    })));
    body.querySelector("[data-revoke-all]")?.addEventListener("click", guard(async () => {
      if (!(await ask("Отозвать все компьютеры?", "Все пульты этого человека отключатся. Войти снова — новой ссылкой.", "Отозвать все", true))) return;
      await adm("POST", "/admin/manager_device/revoke.json", { telegram_id: Number(tid), all: true });
      toast("Отозваны"); await load(root); closePanel();
    }));
  };
  draw();
}

export const managers: Section = {
  id: "mgrs",
  title: "Руководители",
  icon: "managers",
  kicker: "роли, охват, вход и компьютеры",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div>';
    try { await load(root); } catch (e) { root.innerHTML = `<div class="notice err">${esc((e as Error).message)}</div>`; }
  },
};
