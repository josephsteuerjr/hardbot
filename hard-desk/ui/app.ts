// Хард — страница окна. Режим «руководитель»: вход по учётке или прежней ссылке, дальше пульт
// открывается сразу. Разделы видны по возможностям, которые считает сервер (caps):
// сотрудник службы безопасности видит «Контроль», руководитель — «Вход команды».
import { api, hard, setEmployeeProfile, type Shell } from "./kit/api";
import { DEFAULT_SERVER } from "../src/main/product";
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
import { employeeBoard, staffTasks } from './views/team-work';
import { onAdminLink, local } from "./analyst/lib";
import { useAnalystCardTransport } from './analyst/card-access';
import { today } from "./analyst/today";
import { people } from "./analyst/people";
import { managers } from "./analyst/managers";
import { accounts } from "./analyst/accounts";
import { conn, maint } from "./analyst/system";
import { mail } from "./analyst/mail";
import { priority, registry } from "./analyst/registry";
import { fieldHelp, portfolio, hardEmployeeSections } from "./views/field";
import { softHelp, softQueue, softEmployeeSections } from "./views/soft";
import { adminControlCall, controlSection } from "./views/control";
import { employeeRegistry, employeeCabinet } from "./views/employee-cabinet";

/** Пульт аналитика: тот же каркас и облик, данные — через питон-сторону. */
async function startAnalyst() {
  useAnalystCardTransport();
  bare(false);
  shell = await hard.state();
  document.title = shell.product;
  if (shell.theme === "light" || shell.theme === "dark") document.documentElement.dataset.theme = shell.theme;
  onAdminLink((s) => { const pill = $("state"); pill.dataset.level = s.level; $("state-text").textContent = s.text; });
  setHead("…");
  let st: any = null;
  try { st = await local("/state"); } catch (e) {
    $("view-inner").innerHTML = `<div class="page enroll"><h1>Пульт аналитика</h1><p>${esc((e as Error).message)}</p></div>`;
    return;
  }
  setWho(`<b>${esc(st.active || "")}</b>${esc(st.url || "адрес не задан")}`);
  const list: Section[] = [today, employeeBoard('analyst'), staffTasks('analyst'), people, managers, accounts, registry, priority,
                           // «Контроль» целиком (риск/обзвон/закрепления/доступ):
                           // аналитик смотрит ВСЮ компанию через устройство пульта.
                           controlSection(adminControlCall, false),
                           mail, conn, maint];
  setSections(list);
  let first = st.account_session ? "dash" : "conn";
  try { if (st.account_session) first = localStorage.getItem("hard.section") || first; } catch { /* */ }
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
  bare(true);setSections([]);$("head").hidden=true;
  $("view-inner").innerHTML=`<div class="page enroll"><h1>Вход руководителя</h1>
    <p>Введите логин и пароль из вашего личного файла. При первом входе задайте свой пароль.</p>
    <input id="manager-server" value="${esc(shell?.server || DEFAULT_SERVER)}" hidden>
    <div class="row"><input class="input" id="manager-login" placeholder="Логин" autocomplete="username"></div>
    <div class="row" style="margin-top:10px"><input class="input" id="manager-pass" type="password" placeholder="Пароль" autocomplete="current-password">
      <button class="btn primary" id="manager-go">Войти</button></div><div class="err" id="manager-error">${esc(note)}</div></div>`;
  const submit=async()=>{
    const username=$<HTMLInputElement>("manager-login").value,password=$<HTMLInputElement>("manager-pass").value;
    if(!username||!password){$("manager-error").textContent="Введите логин и пароль.";return;}
    const button=$<HTMLButtonElement>("manager-go");button.disabled=true;
    try{
      const r=await hard.accountManagerLogin($<HTMLInputElement>("manager-server").value,username,password);
      if(!r.ok){$("manager-error").textContent=r.text || "Вход не выполнен.";return;}
      if(r.data?.must_change_password && !await passwordChangeScreen(r.data.username || username,password))return;
      await openAfterEnrollment();
    }finally{if(button.isConnected)button.disabled=false;}
  };
  $("manager-go").addEventListener("click",()=>void submit());
  $<HTMLInputElement>("manager-pass").addEventListener("keydown",e=>{if(e.key==="Enter")void submit();});
}

function pendingManagerScreen() {
  bare(true);
  setSections([]);
  $("head").hidden = true;
  $("view-inner").innerHTML = `<div class="page enroll"><h1>Вход записан</h1>
    <p>Данные команды и контроль пока закрыты. Аккаунт руководителя/СБ и это устройство подтверждает один администратор.</p>
    <p class="hint">После подтверждения нажмите «Проверить снова». Самостоятельно подтвердить свой вход здесь нельзя.</p>
    <div class="row"><button class="btn primary" id="pending-manager-check">Проверить снова</button>
    <button class="btn" id="pending-manager-exit">Выйти</button></div></div>`;
  $<HTMLButtonElement>("pending-manager-check").addEventListener("click", () => void start());
  $<HTMLButtonElement>("pending-manager-exit").addEventListener("click", async () => {
    await hard.logout(); enrollScreen();
  });
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
  document.title = shell.product;
  if (shell.mode === "field") { await openAfterEnrollment(); return; }
  if (shell.theme === "light" || shell.theme === "dark") document.documentElement.dataset.theme = shell.theme;
  if (!shell.enrolled) { enrollScreen(); return; }
  bare(false);
  setHead("…", "");
  const me = await api("/api/manager/device/me");
  if (!me.ok && me.kind === "revoked") { enrollScreen(me.text); return; }
  if (!me.ok && me.text === "account_pending") { pendingManagerScreen(); return; }
  if (!me.ok && me.text === "password_change_required" && shell.device?.username) {
    if (await passwordChangeScreen(shell.device.username, "")) await start();
    return;
  }
  if (!me.ok) { startError(me.text || "", start); return; }
  const info = me.ok ? me.data : null;
  const caps = info?.caps || { team_links: true, control: false };
  const who = info?.me;
  if (shell.device?.role && who?.role !== shell.device.role) {
    startError("Роль аккаунта и роль пульта расходятся. Данные не открыты; обратитесь к администратору.", start);
    return;
  }
  setWho(who ? `<b>${esc(who.name || "Руководитель")}</b>${esc(ROLE[who.role] || "")}${who.scope ? " · " + esc(who.scope) : ""}` : "");
  document.body.classList.add("desktop");
  const list: Section[] = [managerCRM, employeeBoard('manager'), staffTasks('manager'), teamPortfolio, workspace, activity, team, promises];
  if (caps.control) list.push(control);
  if (caps.team_links) list.push(links(!!shell.device?.username &&
    (who?.role === "manager" || who?.role === "full")));
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
  bare(true); setSections([]); $("head").hidden = true;
  $("view-inner").innerHTML = `<div class="page enroll"><h1>Вход в приложение</h1>
    <p>Введите свой логин и пароль. Если профиля ещё нет, создайте его на этом компьютере.</p>
    <input class="input" id="acc-server" value="${esc(shell?.server || DEFAULT_SERVER)}" aria-label="Адрес сервера" hidden>
    <div class="row"><input class="input" id="acc-login" placeholder="Логин" autocomplete="username"></div>
    <div class="row" style="margin-top:10px"><input class="input" id="acc-pass" type="password" placeholder="Пароль" autocomplete="current-password">
      <button class="btn primary" id="acc-go">Войти</button></div>
    <button class="btn" id="acc-register" style="margin-top:14px">Создать профиль</button>
    <p class="hint">Новый компьютер подтверждает ваш руководитель. Данные и очередь прежнего профиля сохраняются.</p>
    <div class="err" id="err">${esc(note)}</div></div>`;
  const login = $<HTMLInputElement>("acc-login"), password = $<HTMLInputElement>("acc-pass");
  const submit = async () => {
    if (!login.value.trim() || !password.value) { $("err").textContent = "Введите логин и пароль."; return; }
    const button = $<HTMLButtonElement>("acc-go"); button.disabled = true; $("err").textContent = "";
    try {
      const r = await hard.accountLogin($<HTMLInputElement>("acc-server").value, login.value.trim(), password.value);
      if (!r.ok) { $("err").textContent = r.text || "Вход не выполнен."; return; }
      if (r.data?.must_change_password && !await passwordChangeScreen(r.data.username || login.value, password.value)) return;
      await openAfterEnrollment();
    } finally { if (button.isConnected) button.disabled = false; }
  };
  $("acc-go").addEventListener("click", () => void submit());
  for (const f of [login,password]) f.addEventListener("keydown",e => { if(e.key === "Enter") void submit(); });
  $("acc-register").addEventListener("click", () => void registerFieldScreen()); login.focus();
}

async function registerFieldScreen() {
  const server = shell?.server || DEFAULT_SERVER;
  const result = await hard.accountStaff(server);
  if (!result.ok) { enrollFieldScreen(result.text || "Список сотрудников не загрузился. Проверьте связь и повторите."); return; }
  const employees = result.data?.employees || [];
  $("view-inner").innerHTML = `<div class="page enroll"><h1>Создать профиль</h1>
    <p>Выберите себя. Руководитель подтвердит регистрацию, когда лично сверит вас за этим компьютером.</p>
    <div class="row"><select class="input" id="reg-employee" aria-label="Сотрудник"><option value="">Выберите своё ФИО</option>
      ${employees.map((x:any) => `<option value="${esc(x.employee)}">${esc(x.employee)}${x.office ? ' · '+esc(x.office) : ''}</option>`).join("")}</select></div>
    <div class="row" style="margin-top:10px"><input class="input" id="reg-login" placeholder="Придумайте логин" autocomplete="username"></div>
    <div class="row" style="margin-top:10px"><input class="input" id="reg-password" type="password" placeholder="Пароль: от 8 символов, буква и цифра" autocomplete="new-password"></div>
    <div class="row" style="margin-top:10px"><input class="input" id="reg-repeat" type="password" placeholder="Повторите пароль" autocomplete="new-password"></div>
    <p class="hint">Один профиль подходит для Харда, Софта и телефона. Если вас нет в списке, обратитесь к руководителю.</p>
    <button class="btn primary" id="reg-go">Создать профиль</button> <button class="btn" id="reg-back">Назад</button>
    <div class="err" id="reg-error"></div></div>`;
  $("reg-back").addEventListener("click",()=>enrollFieldScreen());
  $("reg-go").addEventListener("click",async()=>{
    const employee=$<HTMLSelectElement>("reg-employee").value, username=$<HTMLInputElement>("reg-login").value;
    const password=$<HTMLInputElement>("reg-password").value;
    if(!employee || !username || !password) { $("reg-error").textContent="Выберите ФИО, логин и пароль."; return; }
    if(password!==$<HTMLInputElement>("reg-repeat").value) { $("reg-error").textContent="Пароли не совпадают."; return; }
    const button=$<HTMLButtonElement>("reg-go"); button.disabled=true;
    try {
      const r=await hard.accountRegister(server,employee,username,password);
      if(!r.ok) { $("reg-error").textContent=r.text || "Профиль не создан."; return; }
      await openAfterEnrollment();
    } finally { if(button.isConnected)button.disabled=false; }
  });
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
    <p>Вход записан. Доступ подтвердит ваш руководитель. Можно закрыть программу.</p>
    <p class="hint">Если требуется вход и на телефоне, и на ПК, используйте ту же профиль. Руководитель сверит аккаунт и каждое устройство. Пара «телефон + ПК» здесь отображается для сверки, но сервер не требует оба устройства для активации. Затем нажмите «Проверить снова».</p>
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
  document.title = shell.product;
  if (shell.mode !== "field") { await openAfterEnrollment(); return; }
  if (shell.theme === "light" || shell.theme === "dark") document.documentElement.dataset.theme = shell.theme;
  if (!shell.enrolled) { enrollFieldScreen(); return; }
  bare(false);
  setHead("…", "");
  const me = await api("/api/bootstrap");
  if (!me.ok && me.kind === "revoked") { enrollFieldScreen(me.text); return; }
  if (!me.ok) { startError(me.text || "", startAgent); return; }
  const who: any = me.ok ? me.data : null;
  if (who?.must_change_password) {
    if (await passwordChangeScreen(String(who.username || ""), "")) await startAgent();
    return;
  }
  // Аккаунт «логин-пароль» ждёт подтверждения администратора: токен работает, но
  // данных нет — экран ожидания вместо портфеля (см. pendingApprovalScreen).
  if (who?.pending) { pendingApprovalScreen(String(who.employee || "")); return; }
  setLanguage(who?.lang || currentLanguage());
  // Product selects the first workspace; current server roles can add the
  // employee's other portfolio without another installation or login.
  const soft = shell.productId === "soft" || (!shell.fixedProduct && !!who?.roles?.soft);
  if (shell.productId === "hard" && who?.roles?.soft && !who?.roles?.field) {
    startError("У этого входа есть только портфель Софта. Откройте программу «Софт».", startAgent); return;
  }
  if (shell.productId === "soft" && !who?.roles?.soft) {
    startError("У этого входа нет портфеля Софта. Откройте приложение «Хард» или проверьте назначение у руководителя.", startAgent); return;
  }
  setWho(`<b>${esc(who?.employee || "Сотрудник")}</b> · ${soft ? "Софт" : "Хард"}`);
  const profile = soft ? "soft" : "hard";
  const scoped = (section: Section, work: "hard" | "soft"): Section => ({
    ...section,
    async render(root, params) {
      setEmployeeProfile(work);
      await section.render(root, params);
    },
  });
  const primary: Section[] = soft ? [softQueue, staffTasks('employee'), ...softEmployeeSections, softHelp] : [portfolio, staffTasks('employee'), ...hardEmployeeSections, fieldHelp];
  const list = primary.map(section => scoped(section, profile));
  const dual=!!who?.roles?.soft && !!who?.roles?.field;
  list.splice(1,0,scoped(employeeCabinet(profile,dual),profile),scoped(employeeRegistry(profile,dual),profile));
  if (soft && who?.roles?.field) list.splice(1, 0, scoped({
    ...portfolio,
    title: "Выездные клиенты",
    async render(root, params) {
      await portfolio.render(root, params);
      if (root.isConnected) setHead("Выездные клиенты");
    },
  }, "hard"));
  setSections(list);
  await go(soft ? "softqueue" : "portfolio");
  startHeartbeat(async () => {
    const r = await hard.api("GET", "/api/bootstrap");
    return r.kind === "offline" || r.kind === "cert" ? "off" : r.kind === "revoked" ? "revoked" : "live";
  }, () => (who ? refresh() : startAgent()));
}

if (ANALYST) {
  mountShell(`Взыскание СНГ — Аналитик`);
  hard.on("revoked", () => {
    setWho("");setSections([conn]);$("view-inner").replaceChildren();
    void go("conn");toast("Доступ отозван. Локальный вход удалён. Войдите в действующий аккаунт.");
  });
  void startAnalyst();
} else if (FIELD) {
  mountShell(`<b>Хард</b> · приложение сотрудника`);
  void hard.state().then(s => { if (s.fixedProduct) $("sign").textContent = s.product; });
  hard.on("revoked", () => enrollFieldScreen("Доступ компьютера отозван. Войдите в личный профиль и получите подтверждение своего руководителя."));
  void startAgent();
} else {
  mountShell(`<b>Хард</b> · приложение`);
  hard.on("revoked", () => enrollScreen("Доступ компьютера отозван. Войдите в личный профиль и получите подтверждение администратора."));
  void start();
}
