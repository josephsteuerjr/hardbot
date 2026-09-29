// «Руководители» аналитика: кто видит чью команду, роль (руководитель /
// безопасник / и то и другое), вход и компьютеры с пультом руководителя —
// с отзывом по одному или всех разом.
import { ago, ask, esc, n, toast } from "../kit/dom";
import { closePanel, openPanel, type Section } from "../kit/shell";
import { issueLink } from "./links";
import { adm, fn, guard, note } from "./lib";

const ROLES: [string, string, string][] = [
  ["manager", "Руководитель", "видит свою команду, выдаёт вход сотрудникам"],
  ["security", "Безопасник", "видит команду и «Контроль», ничего не меняет"],
  ["full", "Руководитель и контроль", "всё сразу — для директора направления"],
];
const ROLE_TITLE = Object.fromEntries(ROLES.map(([k, t]) => [k, t]));

let data: any = null;
let staff: Record<string, string[]> = {};

async function load(root: HTMLElement) {
  const [d, offices, devices] = await Promise.all([
    adm("GET", "/admin/managers"),
    adm("GET", "/admin/employee_cities.json").catch(() => ({})),
    adm("GET", "/admin/manager_devices.json").then((x) => x.devices || []).catch(() => null),
  ]);
  data = d;
  staff = {};
  for (const [emp, city] of Object.entries((offices.cities || {}) as Record<string, string>)) (staff[city] ||= []).push(emp);
  const rows = await Promise.all(((d.managers || []) as any[]).map(async (m) => ({ m, row: await fn("managers.manager_row", m) })));
  const devBy: Record<string, any[]> = {};
  for (const x of (devices || []) as any[]) (devBy[String(x.telegram_id)] ||= []).push(x);
  root.innerHTML = `${note("Руководитель видит свою команду в пульте и в приложении. Безопасник видит ту же команду и раздел «Контроль» (следы и контрольный обзвон), но ничего не меняет. «Контроль» руководителям не показывается.")}
    <div class="toolbar" style="margin-top:14px"><span class="spacer"></span><button class="btn primary" data-new>Добавить</button></div>
    ${rows.map(({ m, row }) => {
      const devs = devBy[String(m.telegram_id)] || [];
      const live = devs.filter((x) => !x.revoked_at).length;
      return `<div class="list-row" data-tid="${esc(m.telegram_id)}">
        <div class="grow"><div class="nm">${esc(row.name)} <span class="tag" style="margin-left:6px">${esc(ROLE_TITLE[m.role || "manager"] || "Руководитель")}</span>${row.mismatch ? ' <span class="tag err">ФИО ≠ реестру</span>' : ""}</div>
          <div class="sub">${esc(row.employees)}${row.cities !== "—" ? " · " + esc(row.cities) : ""} · id ${esc(row.tid)}${devices ? ` · пульт на ${n(live)} ${live === 1 ? "компьютере" : "компьютерах"}` : ""}</div></div>
        <button class="btn small" data-link>Выдать вход</button><button class="btn small" data-edit>Изменить</button></div>`;
    }).join("") || '<div class="empty"><b>Пока никого</b>Добавьте руководителя кнопкой выше.</div>'}
    ${devices === null ? '<div class="notice" style="margin-top:14px"><span class="grow">Сервер ещё не обновлён: списка компьютеров с пультом руководителя он не отдаёт.</span></div>' : ""}`;
  root.querySelector("[data-new]")?.addEventListener("click", guard(async () => {
    edit(root, null, await fn("managers.next_local_manager_id", d.managers || []), []);
  }));
  root.querySelectorAll<HTMLElement>(".list-row[data-tid]").forEach((row) => {
    const tid = row.dataset.tid!;
    const m = (d.managers as any[]).find((x) => String(x.telegram_id) === tid);
    row.querySelector("[data-link]")?.addEventListener("click", guard(() => issueLink({ telegram_id: Number(tid) })));
    row.querySelector("[data-edit]")?.addEventListener("click", () => edit(root, m, tid, devBy[tid] || []));
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
