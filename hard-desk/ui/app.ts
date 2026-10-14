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
import { accounts } from "./analyst/accounts";
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
  const list: Section[] = [today, people, aLinks, managers, accounts, registry, priority,
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
 *  по ссылке входа и портфелю; сам человек о режимах не знает ничего.
 *  С 14.10 вход ещё и по логину-паролю (аккаунт выдаёт владелец) — вторая
 *  вкладка того же экрана; до подтверждения администратором сервер отвечает
 *  pending, и вместо портфеля показывается экран ожидания. */
function enrollFieldScreen(note = "") {
  bare(true);
  setSections([]);
  const host = $("view-inner");
  $("head").hidden = true;
  host.innerHTML = `<div class="page enroll">
    <h1>Вход в приложение</h1>
    <div class="chips" style="margin:0 0 18px">
      <button class="chip" id="tab-link" aria-pressed="true">Ссылка входа</button>
      <button class="chip" id="tab-pass" aria-pressed="false">Логин и пароль</button>
    </div>
    <div id="pane-link">
      <p>Вставьте СВОЮ ссылку входа — её выдаёт руководитель в «Входе команды». Это нужно один раз.</p>
      <p class="hint">Телефон продолжает работать: вход на компьютере его не заменяет.</p>
      <div class="row"><input class="input" id="link" placeholder="https://…/i/…" autocomplete="off" spellcheck="false">
        <button class="btn primary" id="go">Войти</button></div>
    </div>
    <div id="pane-pass" hidden>
      <p>Введите логин и пароль, которые вам выдали вместе с программой.</p>
      <p class="hint">Телефон продолжает работать: вход на компьютере его не заменяет.</p>
      ${shell?.server ? "" : `<div class="row" style="margin-bottom:10px"><input class="input" id="acc-server" placeholder="Адрес сервера — https://… из инструкции" autocomplete="off" spellcheck="false"></div>`}
      <div class="row" style="margin-bottom:10px"><input class="input" id="acc-login" placeholder="Логин" autocomplete="username" spellcheck="false"></div>
      <div class="row"><input class="input" id="acc-pass" type="password" placeholder="Пароль" autocomplete="current-password">
        <button class="btn primary" id="acc-go">Войти</button></div>
    </div>
    <div class="err" id="err">${esc(note)}</div>
    <div class="row" id="err-actions" hidden style="margin-top:12px"></div>
  </div>`;
  const input = $<HTMLInputElement>("link");
  addMigration(host);
  // Отказ входа словами; 409 «остались звонки прежнего сотрудника» получает
  // кнопку принудительной очистки очереди — иначе компьютер оказался бы тупиком.
  const hideActions = () => { const a = $("err-actions"); a.hidden = true; a.innerHTML = ""; };
  const showFailure = (r: { text?: string; status?: number }, retry: () => unknown) => {
    $("err").textContent = r.text || "Не получилось";
    hideActions();
    if (r.status !== 409) return;
    const actions = $("err-actions");
    const clear = document.createElement("button");
    clear.className = "btn small danger";
    clear.textContent = "Очистить очередь прежнего сотрудника";
    clear.addEventListener("click", async () => {
      if (!confirm("Удалить все неотправленные звонки прежнего сотрудника на этом компьютере? Это нельзя отменить.")) return;
      const done = await hard.api("POST", "/api/local/call_queue/clear");
      if (!done.ok) { $("err").textContent = done.text || "Очередь не очищена"; return; }
      $("err").textContent = "Очередь очищена. Пробую войти снова…";
      await retry();
    });
    actions.append(clear);
    actions.hidden = false;
  };
  const tabLink = $<HTMLButtonElement>("tab-link"), tabPass = $<HTMLButtonElement>("tab-pass");
  const pick = (pass: boolean) => {
    tabLink.setAttribute("aria-pressed", String(!pass));
    tabPass.setAttribute("aria-pressed", String(pass));
    $("pane-link").hidden = pass;
    $("pane-pass").hidden = !pass;
    (pass ? $<HTMLInputElement>("acc-login") : input)?.focus();
  };
  tabLink.addEventListener("click", () => pick(false));
  tabPass.addEventListener("click", () => pick(true));
  const btn = $<HTMLButtonElement>("go");
  const submit = async () => {
    const v = input.value.trim();
    if (!v) { input.focus(); return; }
    btn.disabled = true;
    $("err").textContent = "";
    hideActions();
    const r = await hard.enroll(v);
    btn.disabled = false;
    if (!r.ok) { showFailure(r, submit); return; }
    await openAfterEnrollment();
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") void submit(); });
  const accLogin = $<HTMLInputElement>("acc-login"), accPass = $<HTMLInputElement>("acc-pass");
  const accBtn = $<HTMLButtonElement>("acc-go");
  const submitPass = async () => {
    const username = accLogin.value.trim().toLowerCase();
    const serverField = document.getElementById("acc-server") as HTMLInputElement | null;
    const server = (serverField?.value || "").trim().replace(/\/+$/, "");
    if (!username) { accLogin.focus(); return; }
    if (!accPass.value) { accPass.focus(); return; }
    if (serverField && !server) { serverField.focus(); return; }
    if (serverField && !/^https?:\/\/[^\s]+$/.test(server)) {
      $("err").textContent = "Адрес сервера выглядит не так — скопируйте его из инструкции целиком.";
      return;
    }
    accBtn.disabled = true;
    $("err").textContent = "";
    hideActions();
    const r = await hard.accountLogin(server, username, accPass.value);
    accBtn.disabled = false;
    if (!r.ok) { showFailure(r, submitPass); return; }
    if (r.data?.must_change_password) {
      const changed = await passwordChangeScreen(String(r.data?.username || username), accPass.value);
      if (!changed) return;
    }
    await openAfterEnrollment();
  };
  accBtn.addEventListener("click", () => void submitPass());
  for (const field of [accLogin, accPass]) field.addEventListener("keydown", (e) => { if (e.key === "Enter") void submitPass(); });
  input.focus();
}

/** Первый вход по выданному паролю: сервер ставит must_change_password, пока
 *  пароль не сменён. Три поля, POST /api/account/password; после успеха вход
 *  продолжается сам (портфель или экран ожидания подтверждения). */
function passwordChangeScreen(username: string, current: string): Promise<boolean> {
  bare(true);
  setSections([]);
  $("head").hidden = true;
  $("view-inner").innerHTML = `<div class="page enroll">
    <h1>Смена пароля</h1>
    <p>Вы вошли как <b>${esc(username)}</b>. Выданный пароль одноразовый — задайте свой.</p>
    <div class="row" style="margin-bottom:10px"><input class="input" id="pw-current" type="password" placeholder="Выданный пароль" autocomplete="current-password"></div>
    <div class="row" style="margin-bottom:10px"><input class="input" id="pw-new" type="password" placeholder="Новый пароль — от 8 символов, буква и цифра" autocomplete="new-password"></div>
    <div class="row"><input class="input" id="pw-repeat" type="password" placeholder="Повторите новый пароль" autocomplete="new-password">
      <button class="btn primary" id="pw-go">Сменить пароль</button></div>
    <div class="err" id="pw-err"></div>
  </div>`;
  const currentInput = $<HTMLInputElement>("pw-current");
  currentInput.value = current; // только что введён — подставляем, дальше не храним
  return new Promise<boolean>(resolve => {
    const submit = async () => {
      const go = $<HTMLButtonElement>("pw-go");
      const next = $<HTMLInputElement>("pw-new").value, repeat = $<HTMLInputElement>("pw-repeat").value;
      if (!currentInput.value) { currentInput.focus(); return; }
      if (next.length < 8 || !/[0-9]/.test(next) || !/[a-zA-Zа-яА-ЯёЁ]/.test(next)) { $("pw-err").textContent = "Пароль от 8 символов, с буквой и цифрой."; return; }
      if (next !== repeat) { $("pw-err").textContent = "Пароли не совпадают."; return; }
      go.disabled = true;
      $("pw-err").textContent = "";
      const r = await api("/api/account/password", undefined, "POST", { username, current: currentInput.value, new: next });
      go.disabled = false;
      if (!r.ok) { $("pw-err").textContent = r.text || "Не удалось сменить пароль."; return; }
      resolve(true);
    };
    $<HTMLButtonElement>("pw-go").addEventListener("click", () => void submit());
    for (const id of ["pw-current", "pw-new", "pw-repeat"]) $<HTMLInputElement>(id).addEventListener("keydown", (e) => { if (e.key === "Enter") void submit(); });
    currentInput.focus();
  });
}

/** Вход по логину-паролю записан, но владелец ещё не открыл доступ: сервер
 *  отвечает /api/bootstrap {pending:true} — показываем ожидание, портфель
 *  не открываем. «Проверить снова» повторяет обычный запуск приложения. */
function pendingApprovalScreen(employee = "") {
  bare(true);
  setSections([]);
  $("head").hidden = true;
  $("view-inner").innerHTML = `<div class="page enroll">
    <h1>Вход записан</h1>
    ${employee ? `<p class="hint" style="margin:0 0 6px">${esc(employee)}</p>` : ""}
    <p>Вход записан. Доступ появится после подтверждения администратора. Можно закрыть программу.</p>
    <p class="hint">Руководитель передаёт список введших, владелец открывает доступ. Позже нажмите «Проверить снова» — после подтверждения приложение откроется сразу.</p>
    <div class="row"><button class="btn primary" id="pending-check">Проверить снова</button>
      <button class="btn" id="pending-exit">Выйти</button></div>
  </div>`;
  $<HTMLButtonElement>("pending-check").addEventListener("click", () => void startAgent());
  $<HTMLButtonElement>("pending-exit").addEventListener("click", async () => {
    if (!confirm("Выйти? Вход и кэш закроются. Неотправленные записи и черновики сохранятся для того же сотрудника.")) return;
    await hard.logout();
    location.reload();
  });
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
  // Аккаунт «логин-пароль» ждёт подтверждения администратора: токен работает, но
  // данных нет — экран ожидания вместо портфеля (см. pendingApprovalScreen).
  if (who?.pending) { pendingApprovalScreen(String(who.employee || "")); return; }
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
