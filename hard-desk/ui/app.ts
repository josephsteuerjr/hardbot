// Хард — страница окна. Режим «руководитель»: вход один раз по ключу, дальше пульт
// открывается сразу. Разделы видны по возможностям, которые считает сервер (caps):
// безопасник видит «Контроль», руководитель — «Вход команды».
import { api, hard, type Shell } from "./kit/api";
import { currentLanguage, setLanguage } from "./kit/i18n";
import { $, esc, toast } from "./kit/dom";
import { bare, go, mountShell, refresh, setHead, setSections, setWho, type Section } from "./kit/shell";
import { startHeartbeat } from "./kit/heartbeat";
import { activity } from "./views/activity";
import { workspace } from "./views/workspace";
import { teamPortfolio } from "./views/team-portfolio";
import { managerCRM } from "./views/manager-crm";
import { mountDesktopTools } from "./kit/desktop-tools";
import { control } from "./views/control";
import { device, links, promises, report } from "./views/misc";
import { team } from "./views/team";
import { onAdminLink, local } from "./analyst/lib";
import { today } from "./analyst/today";
import { people } from "./analyst/people";
import { links as aLinks } from "./analyst/links";
import { managers } from "./analyst/managers";
import { conn, maint } from "./analyst/system";
import { mail } from "./analyst/mail";
import { priority, registry } from "./analyst/registry";
import { fieldHelp, portfolio, hardEmployeeSections } from "./views/field";
import { softHelp, softQueue, softEmployeeSections } from "./views/soft";
import { adminControlCall, controlSection } from "./views/control";

/** Пульт аналитика: тот же каркас и облик, данные — через питон-сторону. */
async function startAnalyst() {
  bare(false);
  shell = await hard.state();
  if (shell.theme === "light" || shell.theme === "dark") document.documentElement.dataset.theme = shell.theme;
  onAdminLink((s) => { const pill = $("state"); pill.dataset.level = s.level; $("state-text").textContent = s.text; });
  setHead("…");
  let st: any = null;
  try { st = await local("/state"); } catch (e) {
    $("view-inner").innerHTML = `<div class="page enroll"><h1>Пульт аналитика</h1><p>${esc((e as Error).message)}</p></div>`;
    return;
  }
  setWho(`<b>${esc(st.active || "")}</b>${esc(st.url || "адрес не задан")}`);
  const list: Section[] = [today, people, aLinks, managers, registry, priority,
                           // «Контроль» целиком (риск/обзвон/закрепления/доступ):
                           // аналитик смотрит ВСЮ компанию через устройство пульта.
                           controlSection(adminControlCall, false),
                           mail, conn, maint];
  setSections(list);
  let first = st.bound ? "dash" : "conn";
  try { if (st.bound) first = localStorage.getItem("hard.section") || first; } catch { /* */ }
  if (!list.some((s) => s.id === first)) first = "dash";
  await go(first);
  if (st.bound) startHeartbeat(async () => {
    const r = await hard.local("/admin", { method: "GET", path: "/admin/devices.json", timeout: 15 });
    if (r.ok) return "live";
    const e = r.error || "";
    if (/отозван|не принят/i.test(e)) return "revoked";
    return /Нет связи|ертификат/i.test(e) ? "off" : "live";
  }, () => refresh());
}

let shell: Shell;

async function openAfterEnrollment() {
  const actual = await hard.state();
  const query = actual.mode === "field" ? "?mode=field" : actual.mode === "analyst" ? "?mode=analyst" : "";
  if (location.search !== query) { location.search = query; return; }
  if (actual.mode === "field") await startAgent(); else await start();
}

function startError(text: string, retry: () => Promise<void>) {
  bare(false);
  setSections([]);
  setHead("Подключение");
  $("view-inner").innerHTML = `<div class="page enroll"><p>${esc(text || "Не удалось загрузить ваш доступ. Проверьте связь и повторите.")}</p><button class="btn" id="retry-start">Повторить</button></div>`;
  $("retry-start").addEventListener("click", () => void retry());
}

const ROLE: Record<string, string> = { manager: "руководитель", security: "безопасность", full: "руководитель · контроль" };

function enrollScreen(note = "") {
  bare(true);
  setSections([]);
  const host = $("view-inner");
  $("head").hidden = true;
  host.innerHTML = `<div class="page enroll">
    <img class="enroll-logo" src="icon.svg" alt="" onerror="this.remove()">
    <h1>Вход в приложение</h1>
    <p>${shell?.fixedProduct ? "Подключите рабочее место руководителя или СБ своей персональной ссылкой." : "Вставьте свою персональную ссылку. Приложение откроет вашу рабочую страницу: Софт, Хард или пульт руководителя."}</p>
    <p class="hint">Сотруднику ссылку выдаёт руководитель в «Входе команды», руководителю — аналитик. Два входа для телефона и один для программы на ПК; повтор на уже подключённом устройстве их не расходует.</p>
    <div class="row"><input class="input" id="link" placeholder="https://…/i/…" autocomplete="off" spellcheck="false">
      <button class="btn primary" id="go">Войти</button></div>
    <div class="err" id="err">${esc(note)}</div>
    <div class="hint">Ссылка персональная — другим её не пересылайте. Если компьютер потеряли или сменили, аналитик отзовёт вход: пульт на старом компьютере сотрёт у себя всё при первом же выходе на связь.</div>
  </div>`;
  const input = $<HTMLInputElement>("link");
  addMigration(host);
  const btn = $<HTMLButtonElement>("go");
  const submit = async () => {
    const v = input.value.trim();
    if (!v) { input.focus(); return; }
    btn.disabled = true;
    $("err").textContent = "";
    const r = await hard.enroll(v);
    btn.disabled = false;
    if (!r.ok) { $("err").textContent = r.text || "Не получилось"; return; }
    await openAfterEnrollment();
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") void submit(); });
  input.focus();
}

function addMigration(host: HTMLElement) {
  if (!shell?.fixedProduct) return;
  const b = document.createElement("button"); b.className = "btn small"; b.textContent = "Перенести вход из прежней программы";
  host.querySelector(".enroll")?.append(b);
  b.addEventListener("click", async () => {
    const r = await hard.migrate(); if (r.ok) await openAfterEnrollment(); else toast(r.text || "Перенос не выполнен");
  });
}

async function start() {
  shell = await hard.state();
  if (shell.mode === "field") { await openAfterEnrollment(); return; }
  if (shell.theme === "light" || shell.theme === "dark") document.documentElement.dataset.theme = shell.theme;
  if (!shell.enrolled) { enrollScreen(); return; }
  bare(false);
  setHead("…", "");
  const me = await api("/api/manager/device/me");
  if (!me.ok && me.kind === "revoked") { enrollScreen(me.text); return; }
  if (!me.ok) { startError(me.text || "", start); return; }
  const info = me.ok ? me.data : null;
  const caps = info?.caps || { team_links: true, control: false };
  const who = info?.me;
  setWho(who ? `<b>${esc(who.name || "Руководитель")}</b>${esc(ROLE[who.role] || "")}${who.scope ? " · " + esc(who.scope) : ""}` : "");
  document.body.classList.add("desktop");
  const list: Section[] = [managerCRM, teamPortfolio, workspace, activity, team, promises];
  if (caps.control) list.push(control);
  if (caps.team_links) list.push(links);
  list.push(report(!!caps.control));
  list.push(device(shell, who, () => enrollScreen()));
  setSections(list);
  let first = "crm";
  try { first = localStorage.getItem("hard.section") || first; } catch { /* */ }
  if (!list.some((s) => s.id === first)) first = "crm";
  await go(first);
  // Запуск без связи оставил разделы по умолчанию (без «Контроля» и т. п.) —
  // тогда при возврате связи собираем пульт заново, иначе просто перерисовываем.
  startHeartbeat(async () => {
    const r = await hard.api("GET", "/api/manager/device/me");
    return r.kind === "offline" || r.kind === "cert" ? "off" : r.kind === "revoked" ? "revoked" : "live";
  }, () => (info ? refresh() : start()));
}

const ANALYST = new URLSearchParams(location.search).get("mode") === "analyst";
const FIELD = new URLSearchParams(location.search).get("mode") === "field";
mountDesktopTools();
document.addEventListener("keydown", (e) => { if (e.key === "F5") { e.preventDefault(); void refresh(); } });

/** Приложение сотрудника (выездник и софт — одно окно): роль узнаёт СЕРВЕР
 *  по ссылке входа и портфелю; сам человек о режимах не знает ничего. */
function enrollFieldScreen(note = "") {
  bare(true);
  setSections([]);
  const host = $("view-inner");
  $("head").hidden = true;
  host.innerHTML = `<div class="page enroll">
    <h1>Вход в приложение</h1>
    <p>Вставьте СВОЮ ссылку входа — её выдаёт руководитель в «Входе команды». Это нужно один раз.</p>
    <p class="hint">Телефон продолжает работать: вход на компьютере его не заменяет.</p>
    <div class="row"><input class="input" id="link" placeholder="https://…/i/…" autocomplete="off" spellcheck="false">
      <button class="btn primary" id="go">Войти</button></div>
    <div class="err" id="err">${esc(note)}</div>
  </div>`;
  const input = $<HTMLInputElement>("link");
  addMigration(host);
  const btn = $<HTMLButtonElement>("go");
  const submit = async () => {
    const v = input.value.trim();
    if (!v) { input.focus(); return; }
    btn.disabled = true;
    $("err").textContent = "";
    const r = await hard.enroll(v);
    btn.disabled = false;
    if (!r.ok) { $("err").textContent = r.text || "Не получилось"; return; }
    await openAfterEnrollment();
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") void submit(); });
  input.focus();
}

async function startAgent() {
  document.body.classList.add("desktop", "employee-workspace");
  shell = await hard.state();
  if (shell.mode !== "field") { await openAfterEnrollment(); return; }
  if (shell.theme === "light" || shell.theme === "dark") document.documentElement.dataset.theme = shell.theme;
  if (!shell.enrolled) { enrollFieldScreen(); return; }
  bare(false);
  setHead("…", "");
  const me = await api("/api/bootstrap");
  if (!me.ok && me.kind === "revoked") { enrollFieldScreen(me.text); return; }
  if (!me.ok) { startError(me.text || "", startAgent); return; }
  const who: any = me.ok ? me.data : null;
  setLanguage(who?.lang || currentLanguage());
  // Какой экран показать — решает СЕРВЕР: есть портфель софта → очередь звонков
  // (перевод УЗБ на колл-центр), иначе — портфель выездника. Смешанный случай
  // тоже открывает очередь: визит всё равно закрывается телефоном.
  const soft = shell.productId === "soft" || (!shell.fixedProduct && !!who?.roles?.soft);
  if (shell.productId === "hard" && who?.roles?.soft && !who?.roles?.field) {
    startError("У этого входа есть только портфель Софта. Откройте программу «Софт».", startAgent); return;
  }
  if (shell.productId === "soft" && !who?.roles?.soft) {
    startError("У этого входа нет портфеля Софта. Откройте приложение «Хард» или проверьте назначение у руководителя.", startAgent); return;
  }
  setWho(`<b>${esc(who?.employee || "Сотрудник")}</b> · ${soft ? "Софт" : "Хард"}`);
  const list: Section[] = soft ? [softQueue, ...softEmployeeSections, softHelp] : [portfolio, ...hardEmployeeSections, fieldHelp];
  setSections(list);
  await go(soft ? "softqueue" : "portfolio");
  startHeartbeat(async () => {
    const r = await hard.api("GET", "/api/bootstrap");
    return r.kind === "offline" || r.kind === "cert" ? "off" : r.kind === "revoked" ? "revoked" : "live";
  }, () => (who ? refresh() : startAgent()));
}

if (ANALYST) {
  mountShell(`<b>Хард</b> · пульт аналитика`);
  void startAnalyst();
} else if (FIELD) {
  mountShell(`<b>Хард</b> · приложение сотрудника`);
  void hard.state().then(s => { if (s.fixedProduct) $("sign").textContent = s.product; });
  hard.on("revoked", () => enrollFieldScreen("Вход этого компьютера отозван. Попросите у руководителя новую ссылку."));
  void startAgent();
} else {
  mountShell(`<b>Хард</b> · приложение`);
  hard.on("revoked", () => enrollScreen("Доступ этого компьютера отозван аналитиком. Всё сохранённое стёрто. Попросите новую ссылку входа."));
  void start();
}
