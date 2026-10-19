import { ask, esc, n, toast } from '../kit/dom';
import { closePanel, openPanel, go, type Section } from '../kit/shell';
import { DataTable, type Col } from '../kit/table';
import { adm, fn, guard, note } from './lib';
const ROLES:[string,string,string][]=[['manager','Руководитель','Подтверждает сотрудников своей команды'],['security','Служба безопасности','Читает данные команды и контроль'],['full','Руководитель и контроль','Доступ руководителя и службы безопасности']];
const ROLE_TITLE=Object.fromEntries(ROLES.map(([k,t])=>[k,t]));
let data:any=null;let staff:Record<string,string[]>={};
async function load(root:HTMLElement){
  const [d,offices]=await Promise.all([adm('GET','/admin/managers'),adm('GET','/admin/employee_cities.json')]);
  data=d;staff={};for(const [employee,city] of Object.entries(offices.cities || {})) (staff[String(city)] ||= []).push(employee);
  root.innerHTML=note('Области доступа руководителей сохраняются. Логины, пароли, подтверждения и отзыв устройств — в разделе «Профили и устройства».')+
    '<div class="toolbar"><button class="btn" data-new>Добавить руководителя</button><button class="btn" data-profiles>Профили и устройства</button></div><div id="mgr-table"></div>';
  const rows=await Promise.all((d.managers || []).map(async(m:any)=>({m,row:await fn('managers.manager_row',m)})));
  const cols:Col<any>[]=[{key:'name',title:'Руководитель',value:r=>r.m.name},{key:'role',title:'Роль',value:r=>ROLE_TITLE[r.m.role || 'manager'] || r.m.role},
    {key:'scope',title:'Команда',value:r=>r.row.employees},{key:'cities',title:'Офисы',value:r=>r.row.cities}];
  new DataTable<any>(root.querySelector('#mgr-table')!,{id:'managers',cols,key:r=>String(r.m.telegram_id),exportName:'Руководители',onRow:r=>edit(root,r.m,String(r.m.telegram_id),[])}).setRows(rows);
  root.querySelector('[data-new]')?.addEventListener('click',guard(async()=>edit(root,null,String(d.next_local_id || await fn('managers.next_local_manager_id',d.managers || [])),[])));
  root.querySelector('[data-profiles]')?.addEventListener('click',()=>void go('accounts'));
}
function chipList(items: string[], on: Set<string>, attr: string, count?: (x: string) => number): string {
  return `<div class="chips" style="gap:6px">${items.map((x) => `<button class="chip toggle" data-${attr}="${esc(x)}" aria-pressed="${on.has(x)}">${esc(x)}${count && count(x) ? `<span class="n">${n(count(x))}</span>` : ""}</button>`).join("")}</div>`;
}

function edit(root: HTMLElement, m: any, tid: string, devices: any[], link: any = null) {
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
      <p class="muted">Личный профиль и устройства доступны в разделе «Профили и устройства».</p>
      <div class="toolbar" style="margin-top:22px"><button class="btn primary" data-save>Сохранить</button>
        ${m ? '<span class="spacer"></span><button class="btn danger" data-del>Удалить</button>' : ""}</div>`);
    body.querySelector<HTMLInputElement>("#mg-name")!.addEventListener("input", (e) => { form.name = (e.target as HTMLInputElement).value; });
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
  };
  draw();
}

export const managers: Section = {
  id: "mgrs",
  title: "Руководители",
  icon: "managers",
  kicker: "Роли, офисы и состав команд",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div>';
    try { await load(root); } catch (e) { root.innerHTML = `<div class="notice err">${esc((e as Error).message)}</div>`; }
  },
};
