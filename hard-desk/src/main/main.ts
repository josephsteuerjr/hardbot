// Хард — оболочка пультов на движке Hélène (Electron, как Hélène с 28.09).
//
// Окно без системной рамки: шапка своя, как у Hélène, кнопки окна справа. Свернуть и
// закрыть — это «к часам»: окно прячется в трей с той же иконкой, пульт живёт, выход —
// из меню значка. Один экземпляр: второй запуск просто показывает окно.
//
// Страница приходит своим протоколом hard://app — её же файлы лежат в out/ui.
import { BrowserWindow, Notification, Tray, app, clipboard, dialog, ipcMain, nativeImage, nativeTheme,
  net as enet, protocol, screen, shell } from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, extname, join, normalize, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { call, callField, deviceName, enroll, enrollField, flushCallQueue, logout, logoutField, setRevokedHandler, uploadField } from "./net";
import { stopLegacyPult } from "./legacy";
import { writeXlsx } from "./xlsx";
import { local, startBackend } from "./local";
import { runShots } from "./shots";
import { hasFieldWork, readFieldToken, readSettings, readToken, writeSettings } from "./store";
import { FIXED_PRODUCT, PRODUCT_ID } from "./product";
import { migrateData, queueImport } from "./migration";
import { checkUpdates, installUpdate, startUpdates, updateState } from "./updates";
import { trustedUiUrl } from "./transport";
import { grantFile, selectedLocalFile, uploadFile, openFile } from "./file-capabilities";

// Select the data home, then acquire the lock before copying encryption keys.
let fixedInstance = true;
if (FIXED_PRODUCT) {
  app.setName(FIXED_PRODUCT.name);
  const dataPath = process.env.HARD_USER_DATA || join(process.env.LOCALAPPDATA || join(app.getPath("appData"), "..", "Local"), "Hard", FIXED_PRODUCT.dataName, "data");
  mkdirSync(dataPath, { recursive: true });
  app.setPath("userData", dataPath);
  fixedInstance = app.requestSingleInstanceLock();
  // Copy the encryption profile before Chromium loads Local State at startup.
  if (fixedInstance && !process.env.HARD_USER_DATA) {
    try { migrateData(dataPath, app.getPath("appData"), FIXED_PRODUCT.id, {
      localAppData: process.env.LOCALAPPDATA, home: app.getPath("home"), executable: process.execPath,
      shortcuts: [app.getPath("desktop"), join(app.getPath("appData"), "Microsoft", "Windows", "Start Menu", "Programs")],
      readShortcut: path => shell.readShortcutLink(path),
    }); }
    catch { log("Автоматический перенос не завершён; прежние данные сохранены. Доступен выбор папки на экране входа."); }
  }
} else app.setName("Хард");
if (process.env.HARD_USER_DATA) { mkdirSync(process.env.HARD_USER_DATA, { recursive: true }); app.setPath("userData", process.env.HARD_USER_DATA); }

// Режим: явный флаг, имя файла или место. Поставка одна: папка «Хард», лежащая
// внутри комплекта аналитика (рядом с pult/desk_backend.py), открывается пультом
// аналитика; та же папка у руководителя — пультом руководителя.
function inAnalystKit(): boolean {
  if (!app.isPackaged) return false;
  let d = dirname(process.execPath);
  for (let i = 0; i < 3; i++) {
    d = dirname(d);
    if (existsSync(join(d, "pult", "desk_backend.py"))) return true;
  }
  return false;
}
// Режим аналитика — только внутри комплекта (рядом pult/) или по явному флагу.
// Прежнее правило «в пути есть слово "аналит"» открывало пульт руководителя,
// распакованный в папку отдела аналитики, пультом аналитика без питон-стороны.
// Режимы выездника и софтника — только явным флагом: это отдельные приложения
// для людей, а не ещё один пульт в той же папке.
// «Софт» и «Выездник» на ПК — ОДИН режим «приложение сотрудника»: какой экран
// показать (очередь звонков или портфель), решает СЕРВЕР по портфелю человека
// (roles.soft в bootstrap). Сотрудник о режимах не знает вовсе.
type Mode = "manager" | "analyst" | "field";
const MODES: readonly Mode[] = ["manager", "analyst", "field"];

function autoMode(): Mode {
  return process.argv.includes("--analyst") || process.env.HARD_MODE === "analyst"
    || (!process.argv.includes("--manager") && inAnalystKit())
    ? "analyst" : "manager";
}

function cliMode(): Mode | null {
  for (const m of MODES) {
    if (process.argv.includes("--" + m) || process.env.HARD_MODE === m) return m;
  }
  // --soft прежних ярлыков — то же окно сотрудника (экран определит сервер)
  if (process.argv.includes("--soft") || process.env.HARD_MODE === "soft") return "field";
  return null;
}

function savedMode(): Mode | null {
  const m = readSettings().appMode;
  return MODES.includes(m as Mode) ? (m as Mode) : null;
}

function productOf(m: Mode): string {
  return m === "analyst" ? "Хард — пульт аналитика"
    : m === "field" ? "Хард — приложение сотрудника"
    : "Хард — пульт руководителя";
}

function queryOf(m: Mode): string {
  return m === "manager" ? "" : "?mode=" + m;
}

// Один exe — все роли (владелец 03.10: «полный фарш»). Режим выбирается внутри
// окна («Это устройство» → «Режим этого компьютера») и живёт в настройках;
// флаги --field/--soft/--analyst прошлых bat-запускателей перекрывают выбор
// на один запуск, так что старые ярлыки продолжают работать.
let MODE: Mode = FIXED_PRODUCT?.mode ?? cliMode() ?? savedMode() ?? autoMode();
const PRODUCT = FIXED_PRODUCT?.name ?? productOf(MODE);

if (!FIXED_PRODUCT) app.setName(MODE === "analyst" ? "Хард-аналитик" : MODE === "field" ? "Хард-сотрудник" : "Хард");
if (process.platform === "win32") app.setAppUserModelId(
  FIXED_PRODUCT?.appId ?? (MODE === "analyst" ? "ru.rimborso.hard.analyst" : MODE === "field" ? "ru.rimborso.hard.field"
  : "ru.rimborso.hard.manager"));
if (process.env.HARD_USER_DATA) app.setPath("userData", process.env.HARD_USER_DATA);

protocol.registerSchemesAsPrivileged([
  { scheme: "hard", privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: false } },
]);

const OUT = __dirname;                    // out/: main.js, preload.js, ui/, icons/
const UI = join(OUT, "ui");
const ICONS = join(OUT, "icons");

function log(line: string) {
  const text = `${new Date().toISOString().replace("T", " ").slice(0, 19)} ${line}\n`;
  try { mkdirSync(app.getPath("userData"), { recursive: true }); writeFileSync(join(app.getPath("userData"), "pult.log"), text, { flag: "a" }); } catch { /* */ }
  if (!app.isPackaged) process.stdout.write(text);
}

let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let trayMenu: BrowserWindow | null = null;
let quitting = false;

if (!(FIXED_PRODUCT ? fixedInstance : app.requestSingleInstanceLock())) {
  app.quit();
} else {
  app.on("second-instance", (_event, argv) => { if (!argv.includes("--tray")) show(); });
  app.whenReady().then(start);
}

function paper(): string {
  const t = readSettings().theme;
  const dark = t === "dark" || (t !== "light" && nativeTheme.shouldUseDarkColors);
  return dark ? "#1d1914" : "#f4e4cf";
}

function start() {
  setInterval(() => { void flushCallQueue().catch(error => log("Очередь звонков: " + String(error))); }, 15000);
  const t = readSettings().theme;
  if (t === "dark" || t === "light") nativeTheme.themeSource = t;
  if (process.env.HARD_THEME === "dark" || process.env.HARD_THEME === "light") nativeTheme.themeSource = process.env.HARD_THEME;
  log(`старт ${PRODUCT} ${app.getVersion()} · Electron ${process.versions.electron}`);
  protocol.handle("hard", serveStatic);
  setRevokedHandler(() => {
    log("сервер ответил: компьютер отозван — токен и кэш стёрты");
    win?.webContents.send("hard:revoked");
  });
  wireIpc();
  makeTray();
  startUpdates(s => { if (s.phase === "error") quitting = false; win?.webContents.send("hard:update", s); }, log);
  if (!FIXED_PRODUCT) stopLegacyPult(log);
  if (MODE === "analyst") startBackend(log).catch((e) => log("питон-сторона: " + String(e.message || e)));
  // Автозапуск с Windows открывает пульт сразу в трее: окно — по щелчку.
  if (!process.argv.includes("--tray")) show();
}

// ------------------------------------------------------------------ статика

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2", ".json": "application/json",
};

async function serveStatic(req: Request): Promise<Response> {
  const u = new URL(req.url);
  const rel = decodeURIComponent(u.pathname).replace(/^\/+/, "") || "index.html";
  const full = normalize(join(UI, rel));
  if (full !== UI && !full.startsWith(UI + sep)) return new Response("нет", { status: 403 });
  if (!existsSync(full)) return new Response("нет такого файла", { status: 404 });
  const res = await enet.fetch(pathToFileURL(full).toString());
  return new Response(res.body, { status: 200, headers: { "content-type": MIME[extname(full).toLowerCase()] ?? "application/octet-stream", "cache-control": "no-cache" } });
}

// ------------------------------------------------------------------ окно

interface Bounds { x?: number; y?: number; width: number; height: number; maximized?: boolean }
const stateFile = () => join(app.getPath("userData"), "window-state.json");

function loadBounds(): Bounds {
  const fallback: Bounds = { width: 1380, height: 880 };
  try {
    const b = JSON.parse(readFileSync(stateFile(), "utf8")) as Bounds;
    if (!(b.width >= 900 && b.height >= 600)) return fallback;
    if (b.x != null && b.y != null) {
      const seen = screen.getAllDisplays().some((d) => {
        const a = d.workArea;
        return b.x! + 80 < a.x + a.width && b.x! + b.width - 80 > a.x && b.y! >= a.y - 20 && b.y! < a.y + a.height - 60;
      });
      if (!seen) return { width: b.width, height: b.height, maximized: b.maximized };
    }
    return b;
  } catch {
    return fallback;
  }
}

function saveBounds(w: BrowserWindow) {
  try { writeFileSync(stateFile(), JSON.stringify({ ...w.getNormalBounds(), maximized: w.isMaximized() })); } catch { /* */ }
}

function icon(name: string) {
  const p = join(ICONS, name);
  return existsSync(p) ? nativeImage.createFromPath(p) : undefined;
}

function createWindow(): BrowserWindow {
  const b = loadBounds();
  const w = new BrowserWindow({
    x: b.x, y: b.y, width: b.width, height: b.height, minWidth: 980, minHeight: 620,
    show: false, frame: false, backgroundColor: paper(), title: PRODUCT, icon: icon("icon.ico"),
    webPreferences: { preload: join(OUT, "preload.js"), contextIsolation: true, sandbox: true, spellcheck: false },
  });
  if (b.maximized) w.maximize();
  w.once("ready-to-show", () => {
    w.show();
    const shots = process.env.HARD_SHOTS;
    if (shots && !app.isPackaged) void runShots(w, shots).then(() => { if (process.env.HARD_SHOT_QUIT) { quitting = true; app.quit(); } });
  });
  w.on("close", (e) => {
    saveBounds(w);
    if (quitting) return;
    e.preventDefault();
    toTray(w);
  });
  w.on("maximize", () => w.webContents.send("hard:win", "maximized"));
  w.on("unmaximize", () => w.webContents.send("hard:win", "restored"));
  w.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//i.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  w.webContents.on("will-navigate", (e, url) => { if (!url.startsWith("hard://")) e.preventDefault(); });
  void w.loadURL("hard://app/index.html" + queryOf(MODE));
  return w;
}

function toTray(w: BrowserWindow) {
  w.hide();
  const s = readSettings();
  if (!s.seenTrayHint && Notification.isSupported()) {
    new Notification({ title: PRODUCT, body: "Пульт свернулся к часам и продолжает работать. Открыть — щелчок по значку.", icon: icon("icon.png") }).show();
    writeSettings({ seenTrayHint: true });
  }
}

function show() {
  if (!win || win.isDestroyed()) win = createWindow();
  else {
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  }
}

function makeTray() {
  const scale = screen.getPrimaryDisplay().scaleFactor || 1;
  const img = icon(scale >= 1.75 ? "tray-32.png" : scale >= 1.25 ? "tray-24.png" : "tray-16.png");
  if (!img) return;
  tray = new Tray(img);
  tray.setToolTip(PRODUCT);
  tray.on("right-click", () => showTrayMenu());
  tray.on("click", () => show());
}

function showTrayMenu() {
  if (trayMenu && !trayMenu.isDestroyed()) { trayMenu.close(); return; }
  const cursor = screen.getCursorScreenPoint(), area = screen.getDisplayNearestPoint(cursor).workArea;
  const w = new BrowserWindow({ width: 270, height: 220, frame: false, resizable: false, show: false,
    alwaysOnTop: true, skipTaskbar: true, backgroundColor: paper(),
    x: Math.max(area.x, Math.min(cursor.x, area.x + area.width - 270)),
    y: Math.max(area.y, Math.min(cursor.y - 220, area.y + area.height - 220)),
    webPreferences: { preload: join(OUT, "preload.js"), contextIsolation: true, sandbox: true },
  });
  trayMenu = w;
  void w.loadURL("hard://app/tray.html");
  w.once("ready-to-show", () => { w.show(); w.focus(); });
  w.on("blur", () => w.close());
  w.on("closed", () => { if (trayMenu === w) trayMenu = null; });
}

app.on("before-quit", () => { quitting = true; });
app.on("window-all-closed", () => { /* живём у часов */ });

// ------------------------------------------------------------------ мост к странице

function wireIpc() {
  const handle = (channel: string, listener: (e: Electron.IpcMainInvokeEvent, ...args: any[]) => unknown) =>
    ipcMain.handle(channel, (e, ...args) => {
      const owner = channel === "hard:state" && e.sender === trayMenu?.webContents ? trayMenu : win;
      const page = owner === trayMenu ? "tray.html" : "index.html";
      if (!owner || e.sender !== owner.webContents || e.senderFrame !== owner.webContents.mainFrame
        || !trustedUiUrl(e.senderFrame?.url || "", page)) throw new Error("Запрос пришёл из недоверенного окна");
      return listener(e, ...args);
    });
  ipcMain.on("hard:edit", (e, action: string) => {
    if (e.sender !== win?.webContents) return;
    if (action === "copy") e.sender.copy(); else if (action === "cut") e.sender.cut();
    else if (action === "paste") e.sender.paste(); else if (action === "undo") e.sender.undo();
    else if (action === "selectAll") e.sender.selectAll();
  });
  ipcMain.on("hard:trayAction", (e, action: string) => {
    if (e.sender !== trayMenu?.webContents) return;
    trayMenu?.close();
    if (action === "open") show();
    else if (action === "check") { show(); void checkUpdates(); }
    else if (action === "quit") { quitting = true; app.quit(); }
  });
  handle("hard:update", async (e, action: string) => {
    if (e.sender !== win?.webContents) return { ok: false };
    if (action === "check") return checkUpdates();
    if (action === "install") return installUpdate(async () => {
      if (!win || win.isDestroyed()) return false;
      if (MODE === "field" && hasFieldWork()) return false;
      if (MODE === "analyst") {
        const r = await local("/busy");
        if (!r.ok || (r.data as { busy: boolean }).busy) return false;
      }
      return !await win.webContents.executeJavaScript('!!document.querySelector(\'[data-hard-dirty="1"]\')');
    }, () => { quitting = true; });
    return updateState();
  });
  handle("hard:migrate", async e => {
    if (e.sender !== win?.webContents || !PRODUCT_ID) return { ok: false };
    try {
      if (MODE === "analyst") {
        const r = await dialog.showOpenDialog(win!, { title: "Настройки прежнего пульта", properties: ["openFile"], filters: [{ name: "Настройки", extensions: ["json"] }] });
        if (r.canceled) return { ok: false, text: "Перенос отменён" };
        const imported = await local("/settings/import", { path: r.filePaths[0] });
        return { ok: imported.ok, text: imported.error };
      }
      const r = await dialog.showOpenDialog(win!, { title: "Папка данных прежней программы (AppData)", defaultPath: app.getPath("appData"), properties: ["openDirectory"] });
      if (r.canceled) return { ok: false, text: "Перенос отменён" };
      queueImport(r.filePaths[0], app.getPath("userData"), PRODUCT_ID);
      setTimeout(() => { quitting = true; app.relaunch(); app.quit(); }, 100);
      return { ok: true };
    } catch { return { ok: false, text: "Перенос не завершён. Проверьте выбранную папку; действующие настройки не заменялись." }; }
  });
  ipcMain.on("hard:win", (e, action: string) => {
    const w = BrowserWindow.fromWebContents(e.sender);
    if (!w) return;
    if (action === "minimize") toTray(w);
    else if (action === "maximize") (w.isMaximized() ? w.unmaximize() : w.maximize());
    else if (action === "close") w.close();
  });
  handle("hard:state", () => {
    const s = readSettings();
    const field = MODE === "field";
    return { mode: MODE, product: FIXED_PRODUCT?.name ?? productOf(MODE), productId: PRODUCT_ID, fixedProduct: !!FIXED_PRODUCT, kit: inAnalystKit(),
             version: app.getVersion(),
             server: (field ? s.fieldServer : s.server) || "",
             device: (field ? s.fieldDevice : s.device) || null,
             enrolled: field ? !!readFieldToken() && !!s.fieldServer : !!readToken() && !!s.server,
             theme: s.theme || "system", deviceName: deviceName(),
             maximized: !!win?.isMaximized() };
  });
  // Переключатель режимов — ТОЛЬКО в пульте аналитика (комплект): сотрудникам
  // СНГ роль определяет их ссылка, и они о режимах не знают.
  handle("hard:setMode", (_e, m: string) => {
    if (FIXED_PRODUCT) return { ok: false, text: "Откройте отдельную программу для этой роли" };
    const next = MODES.includes(m as Mode) ? (m as Mode) : null;
    if (!next) return { ok: false, text: "неизвестный режим" };
    if (next === "analyst" && !inAnalystKit())
      return { ok: false, text: "пульт аналитика живёт только внутри комплекта аналитика" };
    writeSettings({ appMode: next });
    MODE = next;
    if (tray) tray.setToolTip(productOf(next));
    if (win && !win.isDestroyed()) void win.loadURL("hard://app/index.html" + queryOf(next));
    return { ok: true };
  });
  // Вход по ссылке БЕЗ знания роли: класс ссылки различает сервер (обе
  // стороны НЕ тратят чужую ссылку, 403). Сначала пробуем вход текущего
  // режима, при 403 — второй класс; успех другого класса переворачивает окно.
  handle("hard:enroll", async (_e, link: string) => {
    const raw = String(link || "");
    const pult = MODE !== "field";
    const first = pult ? await enroll(raw) : await enrollField(raw);
    if (first.ok) return first;
    if (first.status === 403 && !FIXED_PRODUCT) {
      const second = pult ? await enrollField(raw) : await enroll(raw);
      if (second.ok) {
        const next: Mode = pult ? "field" : "manager";
        writeSettings({ appMode: next });
        MODE = next;
        if (tray) tray.setToolTip(productOf(next));
        if (win && !win.isDestroyed()) win.setTitle(productOf(next));
        return second;
      }
      return second;
    }
    return first;
  });
  handle("hard:logout", async () => {
    if (MODE === "field") await logoutField(); else await logout();
    return true;
  });
  handle("hard:api", (_e, method: string, path: string, query?: Record<string, unknown>, body?: unknown) =>
    MODE === "field"
      ? callField(String(method || "GET").toUpperCase(), String(path), query, body)
      : call(String(method || "GET").toUpperCase(), String(path), query, body));
  handle("hard:image", async (_e, path: string, query?: Record<string, unknown>) => {
    const r = MODE === "field"
      ? await callField("GET", String(path), query, undefined, { binary: true })
      : await call("GET", String(path), query, undefined, { binary: true });
    if (!r.ok) return null;
    const { type, buf } = r.data as { type: string; buf: Buffer };
    return `data:${type || "image/jpeg"};base64,${buf.toString("base64")}`;
  });
  // Чек с диска: страница выбирает файл (hard.pick), байты идут отсюда —
  // токен остаётся в главном процессе, как у всего остального.
  handle("hard:upload", (e, path: string, query: Record<string, unknown>, filePath: string, ext: string) =>
    uploadField(String(path), query || {}, uploadFile(e.sender.id, String(filePath || "")), String(ext || ".jpg")));
  handle("hard:download", async (e, path: string, query: Record<string, unknown> | undefined, name: string) => {
    const r = await call("GET", String(path), query, undefined, { binary: true });
    if (!r.ok) return { ok: false, text: r.text };
    const w = BrowserWindow.fromWebContents(e.sender);
    const pick = await dialog.showSaveDialog(w!, { defaultPath: join(app.getPath("downloads"), String(name || "отчёт.xlsx")),
      filters: [{ name: "Книга Excel", extensions: ["xlsx"] }] });
    if (pick.canceled || !pick.filePath) return { ok: false, text: "" };
    writeFileSync(pick.filePath, (r.data as { buf: Buffer }).buf);
    return { ok: true, file: grantFile(e.sender.id, pick.filePath) };
  });
  handle("hard:open", (e, file: string) => {
    try { return shell.openPath(openFile(e.sender.id, String(file))); }
    catch (error) { return String((error as Error).message || error); }
  });
  // Любая таблица окна — книгой Excel: тот же вид у всех выгрузок пультов.
  handle("hard:xlsx", async (e, name: string, headers: string[], rows: (string | number | null)[][]) => {
    const w = BrowserWindow.fromWebContents(e.sender);
    const safe = String(name || "Таблица").replace(/[\\\/:*?"<>|]/g, " ").trim() || "Таблица";
    const stamp = new Date().toISOString().slice(0, 10);
    const pick = await dialog.showSaveDialog(w!, { defaultPath: join(app.getPath("downloads"), `${safe} ${stamp}.xlsx`),
      filters: [{ name: "Книга Excel", extensions: ["xlsx"] }] });
    if (pick.canceled || !pick.filePath) return { ok: false };
    try {
      writeXlsx(pick.filePath, safe, (headers || []).map(String), Array.isArray(rows) ? rows : []);
    } catch (err) {
      return { ok: false, text: "Книга не записалась: " + String((err as Error).message || err) };
    }
    return { ok: true, file: grantFile(e.sender.id, pick.filePath) };
  });
  handle("hard:copy", (_e, text: string) => { clipboard.writeText(String(text ?? "")); return true; });
  // Запуск вместе с Windows — свёрнутым к часам (--tray): рассылка книг почтой
  // работает, только пока пульт запущен, и не должна зависеть от того, открыл
  // ли его человек после перезагрузки.
  handle("hard:autostart", (_e, on?: boolean) => {
    if (typeof on === "boolean") app.setLoginItemSettings({ openAtLogin: on, args: ["--tray"] });
    return app.getLoginItemSettings({ args: ["--tray"] }).openAtLogin;
  });
  handle("hard:theme", (e, t: "light" | "dark" | "system") => {
    nativeTheme.themeSource = t;
    writeSettings({ theme: t });
    BrowserWindow.fromWebContents(e.sender)?.setBackgroundColor(paper());
    return true;
  });
  // Пульт аналитика: питон-сторона и выбор файлов/папок.
  handle("hard:local", async (e, path: string, body: unknown) => {
    if (MODE !== "analyst") return { ok: false, error: "Откройте пульт аналитика для этой операции" };
    const data = body as Record<string, unknown> | undefined;
    try {
      if (data && typeof data.path === "string" && path !== "/admin")
        body = { ...data, path: selectedLocalFile(e.sender.id, path, data.path) };
      const result = await local(String(path), body);
      return result;
    } catch (error) { return { ok: false, error: String((error as Error).message || error) }; }
  });
  handle("hard:pick", async (e, kind: "file" | "folder" | "save", opts: { title?: string; filters?: Electron.FileFilter[]; defaultPath?: string } = {}) => {
    const w = BrowserWindow.fromWebContents(e.sender)!;
    if (kind === "save") {
      const r = await dialog.showSaveDialog(w, { title: opts.title, defaultPath: opts.defaultPath, filters: opts.filters });
      return r.canceled || !r.filePath ? "" : grantFile(e.sender.id, r.filePath, true);
    }
    const r = await dialog.showOpenDialog(w, { title: opts.title, defaultPath: opts.defaultPath, filters: opts.filters,
      properties: kind === "folder" ? ["openDirectory"] : ["openFile"] });
    return r.canceled || !r.filePaths[0] ? "" : grantFile(e.sender.id, r.filePaths[0], false, kind === "folder");
  });
  handle("hard:shot", async (e, file: string) => {
    // Снимок окна для проверки глазами (только сборка разработчика).
    if (app.isPackaged) return false;
    const img = await e.sender.capturePage();
    writeFileSync(String(file), img.toPNG());
    return true;
  });
}
