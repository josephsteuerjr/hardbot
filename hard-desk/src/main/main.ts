// Хард — оболочка пультов на движке Hélène (Electron, как Hélène с 28.09).
//
// Окно без системной рамки: шапка своя, как у Hélène, кнопки окна справа. Свернуть и
// закрыть — это «к часам»: окно прячется в трей с той же иконкой, пульт живёт, выход —
// из меню значка. Один экземпляр: второй запуск просто показывает окно.
//
// Страница приходит своим протоколом hard://app — её же файлы лежат в out/ui.
import { BrowserWindow, Menu, Notification, Tray, app, clipboard, dialog, ipcMain, nativeImage, nativeTheme,
  net as enet, protocol, screen, shell } from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { extname, join, normalize, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { call, deviceName, enroll, logout, setRevokedHandler } from "./net";
import { runShots } from "./shots";
import { readSettings, readToken, writeSettings } from "./store";

const MODE: "manager" | "analyst" =
  process.argv.includes("--analyst") || /аналит|analyst/i.test(process.execPath) || process.env.HARD_MODE === "analyst"
    ? "analyst" : "manager";
const PRODUCT = MODE === "analyst" ? "Хард — пульт аналитика" : "Хард — пульт руководителя";

app.setName(MODE === "analyst" ? "Хард-аналитик" : "Хард");
if (process.platform === "win32") app.setAppUserModelId(MODE === "analyst" ? "ru.rimborso.hard.analyst" : "ru.rimborso.hard.manager");
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
let quitting = false;

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => show());
  app.whenReady().then(start);
}

function paper(): string {
  const t = readSettings().theme;
  const dark = t === "dark" || (t !== "light" && nativeTheme.shouldUseDarkColors);
  return dark ? "#1d1914" : "#f4e4cf";
}

function start() {
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
  show();
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
  void w.loadURL("hard://app/index.html" + (MODE === "analyst" ? "?mode=analyst" : ""));
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
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Открыть пульт", click: () => show() },
    { type: "separator" },
    { label: "Выход", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", () => show());
}

app.on("before-quit", () => { quitting = true; });
app.on("window-all-closed", () => { /* живём у часов */ });

// ------------------------------------------------------------------ мост к странице

function wireIpc() {
  ipcMain.on("hard:win", (e, action: string) => {
    const w = BrowserWindow.fromWebContents(e.sender);
    if (!w) return;
    if (action === "minimize") toTray(w);
    else if (action === "maximize") (w.isMaximized() ? w.unmaximize() : w.maximize());
    else if (action === "close") w.close();
  });
  ipcMain.handle("hard:state", () => {
    const s = readSettings();
    return { mode: MODE, product: PRODUCT, version: app.getVersion(), server: s.server || "", device: s.device || null,
             enrolled: !!readToken() && !!s.server, theme: s.theme || "system", deviceName: deviceName(),
             maximized: !!win?.isMaximized() };
  });
  ipcMain.handle("hard:enroll", (_e, link: string) => enroll(String(link || "")));
  ipcMain.handle("hard:logout", async () => { await logout(); return true; });
  ipcMain.handle("hard:api", (_e, method: string, path: string, query?: Record<string, unknown>, body?: unknown) =>
    call(String(method || "GET").toUpperCase(), String(path), query, body));
  ipcMain.handle("hard:image", async (_e, path: string, query?: Record<string, unknown>) => {
    const r = await call("GET", String(path), query, undefined, { binary: true });
    if (!r.ok) return null;
    const { type, buf } = r.data as { type: string; buf: Buffer };
    return `data:${type || "image/jpeg"};base64,${buf.toString("base64")}`;
  });
  ipcMain.handle("hard:download", async (e, path: string, query: Record<string, unknown> | undefined, name: string) => {
    const r = await call("GET", String(path), query, undefined, { binary: true });
    if (!r.ok) return { ok: false, text: r.text };
    const w = BrowserWindow.fromWebContents(e.sender);
    const pick = await dialog.showSaveDialog(w!, { defaultPath: join(app.getPath("downloads"), String(name || "отчёт.xlsx")),
      filters: [{ name: "Книга Excel", extensions: ["xlsx"] }] });
    if (pick.canceled || !pick.filePath) return { ok: false, text: "" };
    writeFileSync(pick.filePath, (r.data as { buf: Buffer }).buf);
    return { ok: true, file: pick.filePath };
  });
  ipcMain.handle("hard:open", (_e, file: string) => shell.openPath(String(file)));
  ipcMain.handle("hard:copy", (_e, text: string) => { clipboard.writeText(String(text ?? "")); return true; });
  ipcMain.handle("hard:theme", (e, t: "light" | "dark" | "system") => {
    nativeTheme.themeSource = t;
    writeSettings({ theme: t });
    BrowserWindow.fromWebContents(e.sender)?.setBackgroundColor(paper());
    return true;
  });
  ipcMain.handle("hard:shot", async (e, file: string) => {
    // Снимок окна для проверки глазами (только сборка разработчика).
    if (app.isPackaged) return false;
    const img = await e.sender.capturePage();
    writeFileSync(String(file), img.toPNG());
    return true;
  });
}
