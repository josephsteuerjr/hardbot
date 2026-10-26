// «Аккаунты и устройства» аналитика: вход по логину и паролю. Кто уже ввёл
// (телефон и компьютер), статус аккаунта и devices с подтверждением от имени
// владельца; журнал последних входов. Новых admin-ручек нет — только
// /admin/accounts.json и /admin/account_approve.json.
import { ask, esc, n, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { DataTable, type Col } from "../kit/table";
import { adm, guard, note, tableHtml } from "./lib";
import { mountAccountActions, mountAccessPreparation } from './access-management';

const KIND: Record<string, string> = { phone: "телефон", desktop: "компьютер" };
const ROLE: Record<string, string> = { agent: "сотрудник", employee: "сотрудник", manager: "руководитель", security: "служба безопасности", full: "руководитель и контроль", analyst: "аналитик" };
const STATUS: Record<string, string> = { awaiting: "ждёт открытия", active: "открыт", blocked: "заблокирован", disabled: "отключён" };

const fmt = (v: unknown): string => String(v ?? "").replace("T", " ").slice(0, 16);
const kind = (k: unknown): string => KIND[String(k ?? "")] || String(k ?? "");
const roleTitle = (a: any): string => ROLE[a?.role] || a?.role || "";
const statusTitle = (a: any): string => STATUS[a?.status] || a?.status || "—";
const devApproved = (x: any): boolean => !!x && x.status === "active";
const clientText = (device: any): string => {
  const c=device.client;
  if(!c)return 'Версия пока не сообщалась';
  const version=c.source==='pwa' ? `Оболочка ${c.shell_version}` :
    `${c.source==='desktop'?'Программа':'Клиент сообщил'} ${c.client_version || '—'}`;
  return `${version}${c.product ? ' · '+c.product : ''} · замечена ${fmt(c.last_seen_at)} UTC`;
};
/** GPS-строка сервера — JSON {"lat","lon","accuracy"} — показываем координатами, не сырым текстом. */
const gpsText = (x: any): string => {
  const raw = x?.gps;
  if (!raw) return "GPS нет";
  try { const g = JSON.parse(String(raw)); return g && typeof g.lat === "number" ? `GPS ${g.lat}, ${g.lon}` : "GPS есть"; }
  catch { return "GPS есть"; }
};

/** «тел ✓ / ПК —» — на чём уже вводили логин-пароль. */
function devicesLine(devs: any[]): string {
  const state = (k: string) => {
    const slots = (devs || []).filter(x => String(x?.kind) === k && x.status !== 'revoked');
    return slots.some(x=>x.status==='pending')?'ожидает':slots.some(x=>x.status==='active')?'подтверждён':'нет заявки';
  };
  return `Телефон: ${state('phone')} / ПК: ${state('desktop')}`;
}

type Row = { a: any };
let all: any[] = [];
let selected: string | null = null;
let approver = "";
let cutoverActive = false;
let passwordReviewOwner = false;
let accountQuery = '', accountExpanded = false;

/** Карточка выбранного аккаунта: устройства с подтверждением и журнал входов. */
function details(root: HTMLElement, a: any) {
  const box = root.querySelector<HTMLElement>("#acc");
  if (!box) return;
  const devs: any[] = a.devices || [];
  const logins: any[] = (a.logins || []).slice(0, 10);
  const awaiting = a.status === "awaiting";
  const passwordReview = a.password_review_pending === true;
  const phoneRequestOnly = !passwordReview && (a.created_by === "self-registration" || cutoverActive && a.role==='agent');
  box.innerHTML = `<div class="section" style="margin-top:18px">
    <div class="section-head"><h2 class="section-title">${esc(a.employee || a.username || "Аккаунт")}</h2></div>
    <dl class="kv" style="grid-template-columns:200px 1fr;max-width:760px;font-size:15px">
      <dt>Логин</dt><dd class="mono">${esc(a.username || "—")}</dd>
      <dt>Офис</dt><dd>${esc(a.office || "—")}</dd>
      <dt>Роль</dt><dd>${esc(roleTitle(a) || "—")}</dd>
      <dt>Статус</dt><dd><span class="tag ${a.status === "active" ? "ok" : "warn"}">${esc(statusTitle(a))}</span>${a.must_change_password ? ' <span class="sub">сменит пароль при входе</span>' : ""}</dd>
      <dt>Создан</dt><dd>${esc(fmt(a.created_at) || "—")}</dd>
      <dt>Активирован</dt><dd>${esc(fmt(a.activated_at) || "—")}</dd></dl>
    <div class="toolbar" style="margin-top:12px">
      ${!phoneRequestOnly&&awaiting?`<button class="btn small primary" data-open ${approver&&(!passwordReview||(passwordReviewOwner&&!a.must_change_password))?'':'disabled'}>${passwordReview?'Подтвердить доступ после смены пароля':'Открыть доступ'}</button>`:''}
      <span class="muted" style="font-size:13px">${phoneRequestOnly?'Устройства сотрудника подтверждает его руководитель во «Входе команды».':approver ? `Подтверждает: ${esc(approver)}. Достаточно одного администратора.` : "Для подтверждений администратор этого устройства должен быть назначен на сервере."}</span></div>
    ${phoneRequestOnly ? '<p class="notice">Руководитель открывает «Вход команды», находит заявку и нажимает «Подтвердить телефон» или «Подтвердить ПК». Телефон можно подтвердить без предварительного ПК. Компьютер подтверждают после личной сверки сотрудника за этим ПК. Геопозиция не требуется.</p>' : ""}
    ${passwordReview?`<p class="notice">${a.must_change_password?'Сотрудник ещё не сменил временный пароль на личный.':'Сотрудник сменил пароль. Доступ ожидает вашего подтверждения.'} Подтвердить может только владелец.</p>`:''}
    <div class="block"><h3>Устройства (${n(devs.length)})</h3>
      ${devs.length ? devs.map((x) => `<div class="list-row">
        <div class="grow"><div class="nm">${esc(x.label || kind(x.kind) || "устройство")}</div>
          <div class="sub">${esc(clientText(x))}</div>
          <div class="sub">${esc(kind(x.kind))}${x.first_seen ? ` · с ${esc(fmt(x.first_seen))}` : ""} · ${esc(gpsText(x))}</div>
          <div class="sub">Подтвердил: ${esc(x.approved_by || "—")}${x.approved_at ? ` · ${esc(fmt(x.approved_at))}` : ""}</div></div>
        ${x.status === "revoked" ? '<span class="tag">отозвано</span>'
          : devApproved(x) ? '<span class="tag ok">подтверждено</span>'
          : passwordReview ? '<span class="tag warn">Сначала подтвердите смену пароля</span>' : phoneRequestOnly ? '<span class="tag warn">Ждёт своего руководителя</span>' : `<button class="btn small primary" data-approve="${esc(String(x.id))}" ${approver ? "" : "disabled"}>Подтвердить ${x.kind==='phone'?'телефон':'ПК'}</button>`}</div>`).join("")
        : '<p class="muted" style="font-size:13.5px">Устройств нет: логин-пароль на этом аккаунте ещё не вводили.</p>'}</div>
    <div class="block"><h3>Журнал подтверждений доступа</h3>
      ${(a.approval_events || []).length ? tableHtml([["at", "Время"], ["actor", "Кто подтвердил"], ["action", "Действие"], ["decision", "Решение"], ["detail", "Аккаунт / устройство"], ["ip", "IP"]],
        a.approval_events.map((e: any) => [fmt(e.at), e.actor, e.action, e.decision, e.detail || "", e.ip || ""])) : '<p class="muted">Подтверждений пока не было.</p>'}</div>
    <div class="block"><h3>Журнал входов (последние ${n(Math.min(10, logins.length))})</h3>
      ${logins.length ? tableHtml([["kind", "Тип"], ["at", "Время"], ["entrance", "Вход"], ["label", "Устройство"], ["ip", "IP"]],
        logins.map((l) => [kind(l.kind || l.device_kind), fmt(l.at), l.entrance || "", l.label || "", l.ip || ""]))
        : '<p class="muted" style="font-size:13.5px">Входов пока не было.</p>'}</div>
  </div>`;
  box.querySelector<HTMLButtonElement>("[data-open]")?.addEventListener("click", guard(async () => {
    if (!(await ask("Открыть доступ?", `Аккаунт «${a.username}» (${a.employee || ""}) сможет входить по логину и паролю.`, "Открыть"))) return;
    const r = await adm<any>("POST", "/admin/account_approve.json", { username: a.username });
    toast(r?.active ? "Доступ открыт" : "Ожидается подтверждение администратора.");
    await load(root);
  }));
  box.querySelectorAll<HTMLButtonElement>("[data-approve]").forEach((b) => b.addEventListener("click", guard(async () => {
    const x = devs.find((d) => String(d.id) === b.dataset.approve);
    if (!x) return;
    if (!(await ask("Подтвердить устройство?", `${x.label || kind(x.kind)} получит доступ к аккаунту «${a.username}».`, "Подтвердить"))) return;
    const r = await adm<any>("POST", "/admin/account_approve.json", { device_id: x.id });
    toast(r?.active ? "Устройство подтверждено" : "Ожидается подтверждение администратора.");
    await load(root);
  })));
  mountAccountActions(box,a,()=>load(root));
  box.scrollIntoView({behavior:'smooth',block:'start'});
}

function drawBulk(root: HTMLElement) {
  const bar = root.querySelector<HTMLElement>("#bulk");
  if (!bar) return;
  const awaiting = all.filter((a) => a.status === "awaiting").length;
  const pending=all.reduce((s,a)=>s+(a.devices||[]).filter((d:any)=>d.status==='pending').length,0);
  bar.innerHTML = `<span class="who">Аккаунтов: <b>${n(all.length)}</b> · ждут открытия: <b>${n(awaiting)}</b> · устройств ждут подтверждения: <b>${n(pending)}</b></span>
    <span class="spacer"></span>
    <button class="ticon" data-b="reload" title="Обновить список"><svg viewBox="0 0 18 18" width="15" height="15"><path d="M14.5 8.5a5.5 5.5 0 1 1-1.6-3.9M14.5 2.5v3.6h-3.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>`;
  bar.querySelector("[data-b=reload]")?.addEventListener("click", guard(async () => { await load(root); toast("Обновлено"); }));
}

async function load(root: HTMLElement) {
  const d = await adm("GET", "/admin/accounts.json");
  approver = String(d.approver || "");
  cutoverActive = d.cutover_active === true;
  passwordReviewOwner = d.password_review_owner === true;
  all = ((d.accounts || []) as any[]).slice().sort((a, b) => String(a.username || "").localeCompare(String(b.username || ""), "ru"));
  selected = all.some((a) => String(a.username) === selected) ? selected : null;
  const pending=all.filter(a=>a.status==='awaiting'||(a.devices||[]).some((d:any)=>d.status==='pending'));
  root.innerHTML = `${note("Чтобы открыть карточку и действия, нажмите «Открыть профиль» в строке. Аккаунт и каждое устройство подтверждаются отдельно. Сотрудник может зарегистрироваться с телефона или на ПК; вход подтверждает свой руководитель во «Входе команды». Руководителей и служебные профили подтверждает администратор. Геопозиция для входа не требуется.")}
    <div class="section"><h3>Ждут подтверждения: ${pending.length} профилей</h3>${pending.length?pending.map(a=>`<div class="list-row"><div class="grow"><b>${esc(a.employee||a.username)}</b><div class="sub">${esc(devicesLine(a.devices))}</div></div><button class="btn primary" data-profile="${esc(a.username)}">Открыть профиль</button></div>`).join(''):'<p class="muted">Необработанных заявок нет. Новая заявка появится после регистрации или входа на устройстве.</p>'}</div>
    <div class="bulk" id="bulk" style="margin-top:12px"></div>
    <input class="input" type="search" id="account-search" aria-label="Поиск профиля" placeholder="ФИО, логин, офис или устройство" value="${esc(accountQuery)}">
    <details id="account-all" ${accountExpanded||accountQuery?'open':''}><summary>Все профили (${all.length})</summary><div id="tbl"></div></details>
    <div id="acc"></div>`;
  const cols: Col<Row>[] = [
    { key: "login", title: "Логин", value: (r) => r.a.username || "", html: (r) => `<span class="who mono">${esc(r.a.username || "")}</span>` },
    { key: "emp", title: "ФИО", value: (r) => r.a.employee || "" },
    { key: "office", title: "Офис", value: (r) => r.a.office || "" },
    { key: "role", title: "Роль", value: (r) => roleTitle(r.a) },
    { key: "status", title: "Статус", value: (r) => statusTitle(r.a),
      html: (r) => `<span class="tag ${r.a.status === "active" ? "ok" : r.a.status === "awaiting" ? "warn" : ""}">${esc(statusTitle(r.a))}</span>` },
    { key: "devs", title: "Устройства", value: (r) => devicesLine(r.a.devices)+' '+(r.a.devices||[]).map((d:any)=>d.label).join(' '), html: (r) => `<span class="who">${esc(devicesLine(r.a.devices))}</span>` },
    { key:'actions',title:'Действия',value:()=>'',filter:false,html:r=>`<button class="btn small" data-profile="${esc(r.a.username)}">Открыть профиль</button>` },
  ];
  const table = new DataTable<Row>(root.querySelector("#tbl")!, {
    id: "analyst-accounts", cols, key: (r) => String(r.a.username || ""), exportName: "Аккаунты и устройства",
    externalSearch: true,
    onRow: (r) => { selected = String(r.a.username || ""); details(root, r.a); },
    empty: "Аккаунтов с логином и паролем пока нет.",
  });table.setRows(all.map((a) => ({ a })));table.setQuery(accountQuery);
  const full=root.querySelector<HTMLDetailsElement>('#account-all')!;
  full.addEventListener('toggle',()=>{accountExpanded=full.open;});
  root.querySelector<HTMLInputElement>('#account-search')!.addEventListener('input',event=>{
    accountQuery=(event.target as HTMLInputElement).value;table.setQuery(accountQuery);if(accountQuery.trim())full.open=true;
  });
  root.querySelector('#tbl')?.addEventListener('click',event=>{
    const button=(event.target as HTMLElement).closest<HTMLElement>('[data-profile]');
    const account=button&&all.find(a=>a.username===button.dataset.profile);
    if(account){selected=account.username;details(root,account);}
  });
  root.querySelectorAll<HTMLButtonElement>('.section [data-profile]').forEach(button=>button.addEventListener('click',()=>{
    const account=all.find(a=>a.username===button.dataset.profile);if(account){selected=account.username;details(root,account);}
  }));
  // После подтверждения список перезагружается — карточку выбранного рисуем заново.
  const again = all.find((a) => String(a.username) === selected);
  if (again) details(root, again);
  drawBulk(root);
  await mountAccessPreparation(root,()=>load(root));
}

export const accounts: Section = {
  id: "accounts",
  title: "Профили и устройства",
  icon: "device",
  kicker: "Регистрация, подтверждения, пароли и отзыв устройств",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div>';
    try { await load(root); } catch (e) { root.innerHTML = `<div class="notice err">${esc((e as Error).message)}</div>`; }
  },
};
