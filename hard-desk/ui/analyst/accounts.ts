// «Аккаунты и устройства» аналитика: вход по логину и паролю. Кто уже ввёл
// (телефон и компьютер), статус аккаунта и devices с подтверждением от имени
// владельца; журнал последних входов. Новых admin-ручек нет — только
// /admin/accounts.json и /admin/account_approve.json.
import { ask, esc, n, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { DataTable, type Col } from "../kit/table";
import { adm, guard, note, tableHtml } from "./lib";

const KIND: Record<string, string> = { phone: "телефон", desktop: "компьютер" };
const ROLE: Record<string, string> = { employee: "сотрудник", manager: "руководитель", security: "безопасник", full: "руководитель и контроль" };
const STATUS: Record<string, string> = { awaiting: "ждёт открытия", active: "открыт", blocked: "заблокирован", disabled: "отключён" };

const fmt = (v: unknown): string => String(v ?? "").replace("T", " ").slice(0, 16);
const kind = (k: unknown): string => KIND[String(k ?? "")] || String(k ?? "");
const roleTitle = (a: any): string => ROLE[a?.role] || a?.role || "";
const statusTitle = (a: any): string => STATUS[a?.status] || a?.status || "—";
const devApproved = (x: any): boolean => !!x && (x.status === "approved" || !!x.approved_by);
const gpsText = (x: any): string => typeof x?.gps === "boolean" ? (x.gps ? "GPS есть" : "GPS нет") : String(x?.gps || "GPS нет");

/** «тел ✓ / ПК —» — на чём уже вводили логин-пароль. */
function devicesLine(devs: any[]): string {
  const has = (k: string) => (devs || []).some((x) => String(x?.kind) === k);
  return `${has("phone") ? "тел ✓" : "тел —"} / ${has("desktop") ? "ПК ✓" : "ПК —"}`;
}

type Row = { a: any };
let all: any[] = [];
let selected: string | null = null;

/** Карточка выбранного аккаунта: устройства с подтверждением и журнал входов. */
function details(root: HTMLElement, a: any) {
  const box = root.querySelector<HTMLElement>("#acc");
  if (!box) return;
  const devs: any[] = a.devices || [];
  const logins: any[] = (a.logins || []).slice(0, 10);
  const awaiting = a.status === "awaiting";
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
      <button class="btn small primary" data-open ${awaiting ? "" : "disabled"} title="${awaiting ? "" : "Доступ уже открыт"}">Открыть доступ</button>
      <span class="muted" style="font-size:13px">Команда владельцу: аккаунт сможет входить по логину и паролю.</span></div>
    <div class="block"><h3>Устройства (${n(devs.length)})</h3>
      ${devs.length ? devs.map((x) => `<div class="list-row">
        <div class="grow"><div class="nm">${esc(x.label || kind(x.kind) || "устройство")}</div>
          <div class="sub">${esc(kind(x.kind))}${x.first_seen ? ` · с ${esc(fmt(x.first_seen))}` : ""} · ${esc(gpsText(x))}</div>
          <div class="sub">Подтвердил: ${esc(x.approved_by || "—")}${x.approved_at ? ` · ${esc(fmt(x.approved_at))}` : ""}</div></div>
        ${devApproved(x) ? '<span class="tag ok">подтверждено</span>'
          : `<button class="btn small" data-approve="${esc(String(x.id))}">Подтвердить устройство</button>`}</div>`).join("")
        : '<p class="muted" style="font-size:13.5px">Устройств нет: логин-пароль на этом аккаунте ещё не вводили.</p>'}</div>
    <div class="block"><h3>Журнал входов (последние ${n(Math.min(10, logins.length))})</h3>
      ${logins.length ? tableHtml([["kind", "Тип"], ["at", "Время"], ["entrance", "Вход"], ["label", "Устройство"], ["ip", "IP"]],
        logins.map((l) => [kind(l.kind || l.device_kind), fmt(l.at), l.entrance || "", l.label || "", l.ip || ""]))
        : '<p class="muted" style="font-size:13.5px">Входов пока не было.</p>'}</div>
  </div>`;
  box.querySelector<HTMLButtonElement>("[data-open]")?.addEventListener("click", guard(async () => {
    if (!(await ask("Открыть доступ?", `Аккаунт «${a.username}» (${a.employee || ""}) сможет входить по логину и паролю.`, "Открыть"))) return;
    await adm("POST", "/admin/account_approve.json", { username: a.username });
    toast("Доступ открыт");
    await load(root);
  }));
  box.querySelectorAll<HTMLButtonElement>("[data-approve]").forEach((b) => b.addEventListener("click", guard(async () => {
    const x = devs.find((d) => String(d.id) === b.dataset.approve);
    if (!x) return;
    if (!(await ask("Подтвердить устройство?", `${x.label || kind(x.kind)} получит доступ к аккаунту «${a.username}».`, "Подтвердить"))) return;
    await adm("POST", "/admin/account_approve.json", { device_id: x.id });
    toast("Устройство подтверждено");
    await load(root);
  })));
}

function drawBulk(root: HTMLElement) {
  const bar = root.querySelector<HTMLElement>("#bulk");
  if (!bar) return;
  const awaiting = all.filter((a) => a.status === "awaiting").length;
  bar.innerHTML = `<span class="who">Аккаунтов: <b>${n(all.length)}</b> · ждут открытия: <b>${n(awaiting)}</b></span>
    <span class="spacer"></span>
    <button class="ticon" data-b="reload" title="Обновить список"><svg viewBox="0 0 18 18" width="15" height="15"><path d="M14.5 8.5a5.5 5.5 0 1 1-1.6-3.9M14.5 2.5v3.6h-3.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>`;
  bar.querySelector("[data-b=reload]")?.addEventListener("click", guard(async () => { await load(root); toast("Обновлено"); }));
}

async function load(root: HTMLElement) {
  const d = await adm("GET", "/admin/accounts.json");
  all = ((d.accounts || []) as any[]).slice().sort((a, b) => String(a.username || "").localeCompare(String(b.username || ""), "ru"));
  selected = all.some((a) => String(a.username) === selected) ? selected : null;
  root.innerHTML = `${note("Вход по логину и паролю вводят дважды: на телефоне и на компьютере. Открыть аккаунт и подтвердить устройство может только владелец — кнопки в карточке отправляют ему команду.")}
    <div class="bulk" id="bulk" style="margin-top:12px"></div>
    <div id="tbl"></div>
    <div id="acc"></div>`;
  const cols: Col<Row>[] = [
    { key: "login", title: "Логин", value: (r) => r.a.username || "", html: (r) => `<span class="who mono">${esc(r.a.username || "")}</span>` },
    { key: "emp", title: "ФИО", value: (r) => r.a.employee || "" },
    { key: "office", title: "Офис", value: (r) => r.a.office || "" },
    { key: "role", title: "Роль", value: (r) => roleTitle(r.a) },
    { key: "status", title: "Статус", value: (r) => statusTitle(r.a),
      html: (r) => `<span class="tag ${r.a.status === "active" ? "ok" : r.a.status === "awaiting" ? "warn" : ""}">${esc(statusTitle(r.a))}</span>` },
    { key: "devs", title: "Устройства", value: (r) => devicesLine(r.a.devices), html: (r) => `<span class="who">${esc(devicesLine(r.a.devices))}</span>` },
  ];
  new DataTable<Row>(root.querySelector("#tbl")!, {
    id: "analyst-accounts", cols, key: (r) => String(r.a.username || ""), exportName: "Аккаунты и устройства",
    onRow: (r) => { selected = String(r.a.username || ""); details(root, r.a); },
    empty: "Аккаунтов с логином и паролем пока нет.",
  }).setRows(all.map((a) => ({ a })));
  // После подтверждения список перезагружается — карточку выбранного рисуем заново.
  const again = all.find((a) => String(a.username) === selected);
  if (again) details(root, again);
  drawBulk(root);
}

export const accounts: Section = {
  id: "accounts",
  title: "Аккаунты и устройства",
  icon: "device",
  kicker: "логин-пароль: кто ввёл, подтверждение владельцем",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div>';
    try { await load(root); } catch (e) { root.innerHTML = `<div class="notice err">${esc((e as Error).message)}</div>`; }
  },
};
