import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { execFile, execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { FIXED_PRODUCT } from "../main/product";
import { discoverProfiles, hasProfile, jsonFile, profilesAt, type OldProfile } from "../main/profile-discovery";
import { importData } from "../main/migration";
import { pathToFileURL } from "node:url";
// Electron presents app.asar as a virtual directory. Copy its physical bytes
// through original-fs when preparing the independent removal runtime.
const rawFs = require("original-fs") as typeof import("node:fs");

let win: BrowserWindow | null = null, busy = false, completed = false, removing = false;
let target = "", candidates: OldProfile[] = [], profile = "", preview = false;
const product = FIXED_PRODUCT!;
const helper = () => join(process.resourcesPath, "process-check.exe");
const exeName = () => "hard-" + product.id + ".exe";
function option(name: string): string { const i = process.argv.indexOf(name); return i >= 0 ? process.argv[i + 1] || "" : ""; }
function checkRoot(root: string): void {
  if (!isAbsolute(root) || !existsSync(join(root, "Uninstall hard-" + product.id + ".exe")) || !existsSync(join(root, exeName())) || resolve(root).toLowerCase() === resolve(profile).toLowerCase()) throw new Error("Не найдена установленная программа");
}
function execute(exe: string, args: string[], timeout = 180000): Promise<number> {
  return new Promise((resolve, reject) => execFile(exe, args, { windowsHide: true, timeout, encoding: "utf8", maxBuffer: 1024 * 1024 }, error => {
    if (!error) resolve(0); else if (typeof error.code === "number") resolve(error.code); else reject(new Error("Операция не завершилась. Проверьте, закрыта ли программа, и повторите."));
  }));
}
function windowsQuote(value: string): string {
  return '"' + value.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/g, '$1$1') + '"';
}
// NSIS requires /D= as the unquoted final tail, including spaces. Normal CRT
// argument quoting silently ignores it and can install into the wrong folder.
function executeNsis(exe: string, args: string[]): Promise<number> {
  return new Promise((resolve, reject) => {
    const child = spawn(exe, args.map(a => a.startsWith("/D=") || a.startsWith("_?=") || !/[\s"]/.test(a) ? a : windowsQuote(a)), {
      windowsHide: true, windowsVerbatimArguments: true, argv0: windowsQuote(exe), stdio: "ignore",
    });
    child.once("error", () => reject(new Error("Не удалось запустить установку. Повторите операцию.")));
    child.once("exit", code => resolve(code ?? 2));
  });
}
function registration(): string {
  // electron-builder's stable UUIDv5 key, derived from the public product id.
  const keys: Record<string, string> = JSON.parse(readFileSync(join(__dirname, "setup-registry.json"), "utf8"));
  const file = join(app.getPath("userData"), "registration.json");
  execFileSync(helper(), ["registration", keys[product.id], file], { windowsHide: true, timeout: 8000, stdio: "ignore" });
  return String(jsonFile(file)?.path || "");
}
export function startUninstaller(): void {
  if (!product) { app.exit(2); return; }
  const ready = option("--hard-uninstall-ready");
  if (ready) { removing = true; target = resolve(ready); boot(); return; }
  // Never keep a running frontend inside the directory being removed.
  const root = dirname(process.execPath), temporary = mkdtempSync(join(tmpdir(), "hard-wizard-"));
  try {
    for (const entry of readdirSync(root, { withFileTypes: true })) {
      if (entry.isFile() && !/^Uninstall |\.log$|^hard-install\.json$/i.test(entry.name)) cpSync(join(root, entry.name), join(temporary, entry.name));
    }
    cpSync(join(root, "locales"), join(temporary, "locales"), { recursive: true });
    mkdirSync(join(temporary, "resources"));
    for (const file of ["app.asar", "process-check.exe"]) rawFs.copyFileSync(join(root, "resources", file), join(temporary, "resources", file));
    const forwarded = process.argv.filter(arg => /^\/S$/i.test(arg) || /^--remote-debugging-port=\d+$/.test(arg));
    const p = spawn(join(temporary, exeName()), ["--hard-uninstall-ready", root, ...forwarded], { detached: true, windowsHide: true, stdio: "ignore" }); p.unref();
    app.exit(0);
  } catch { app.exit(2); }
}
export function startInstaller(): void { if (!product) { app.exit(2); return; } boot(); }
function boot(): void {
  preview = process.argv.includes("--setup-preview");
  app.setName(product.name + " — мастер");
  const user = mkdtempSync(join(tmpdir(), "hard-wizard-profile-"));
  app.setPath("userData", user);
  app.once("will-quit", () => {
    try {
      const cleanup = join(tmpdir(), "hard-cleanup-" + process.pid + ".exe"); cpSync(helper(), cleanup);
      for (const dir of [user, ...(process.argv.includes("--hard-uninstall-ready") ? [dirname(process.execPath)] : [])]) {
        if (dirname(resolve(dir)).toLowerCase() !== resolve(tmpdir()).toLowerCase() || !/^hard-wizard-/.test(dir.split(/[\\/]/).pop() || "")) continue;
        const p = spawn(cleanup, ["cleanup", dir, String(process.pid)], { detached: true, windowsHide: true, stdio: "ignore" }); p.unref();
      }
    } catch { /* OS temporary-directory cleanup remains available */ }
  });
  profile = process.env.HARD_SETUP_QA_DATA || join(process.env.LOCALAPPDATA || "", "Hard", product.id, "data");
  app.whenReady().then(async () => {
    if (!removing) target = registration() || join(process.env.LOCALAPPDATA || "", "Programs", "hard-" + product.id);
    const d = process.argv.find(a => /^\/D=/i.test(a)); if (d && !removing) target = resolve(d.slice(3));
    if (removing) { checkRoot(target); const registered = registration(); if (!registered || resolve(registered).toLowerCase() !== resolve(target).toLowerCase()) throw new Error("Папка не совпадает с установленной программой"); }
    if (!preview && process.argv.some(a => /^\/S$/i.test(a))) {
      const code = await apply(""); app.exit(code.ok ? 0 : 2); return;
    }
    if (!removing) candidates = discoverProfiles(product.id, { appData: app.getPath("appData"), localAppData: process.env.LOCALAPPDATA, home: app.getPath("home"),
      shortcuts: [app.getPath("desktop"), join(app.getPath("appData"), "Microsoft", "Windows", "Start Menu", "Programs")], readShortcut: p => shell.readShortcutLink(p) }, profile);
    win = new BrowserWindow({ width: 960, height: 700, minWidth: 820, minHeight: 610, frame: false, show: false, backgroundColor: "#f4e4cf", title: product.name + (removing ? " — удаление" : " — установка"),
      webPreferences: { preload: join(__dirname, "setup-preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false } });
    const url = pathToFileURL(join(__dirname, "setup", "index.html")).href;
    function trusted(e: Electron.IpcMainInvokeEvent): void { if (e.sender !== win?.webContents || e.senderFrame !== win.webContents.mainFrame || e.senderFrame.url !== url) throw new Error("Недопустимый запрос"); }
    ipcMain.handle("setup:state", e => { trusted(e); return state(); });
    ipcMain.handle("setup:choose", async (e, kind: unknown) => {
      trusted(e); if (busy || completed || removing || hasProfile(profile, product.id)) return state();
      if (kind !== "folder" && kind !== "file") return state();
      const result = await dialog.showOpenDialog(win!, { title: kind === "folder" ? "Где лежала прежняя программа?" : "Ярлык или EXE прежней программы", properties: kind === "folder" ? ["openDirectory"] : ["openFile"],
        filters: kind === "file" ? [{ name: "Программа, ярлык или настройки", extensions: ["lnk", "exe", "json"] }] : undefined });
      if (!result.canceled) {
        const found = profilesAt(result.filePaths[0], product.id, p => shell.readShortcutLink(p));
        if (!found.length) return { ...state(), message: "Здесь подходящих настроек не нашлось. Выберите папку комплекта или его ярлык." };
        candidates = [...new Map([...found, ...candidates].map(c => [c.source, c])).values()];
      }
      return state();
    });
    ipcMain.handle("setup:apply", async (e, source: unknown) => { trusted(e); if (typeof source !== "string") return { ok: false }; return apply(source); });
    ipcMain.handle("setup:finish", (e, launch: unknown) => { trusted(e); if (busy) return false; if (completed && !removing && launch === true) { const p = spawn(join(target, exeName()), [], { detached: true, stdio: "ignore", windowsHide: true }); p.unref(); } app.quit(); return true; });
    ipcMain.on("setup:window", (e, action: string) => { if (e.sender !== win?.webContents) return; if (action === "minimize") win?.minimize(); if (action === "close" && !busy) app.quit(); });
    win.on("close", e => { if (busy) e.preventDefault(); });
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.webContents.on("will-navigate", (e, next) => { if (next !== url) e.preventDefault(); });
    await win.loadURL(url); win.show();
    if (preview && process.env.HARD_SETUP_SHOT) { await new Promise(r => setTimeout(r, 800)); const shot = await win.webContents.capturePage(); writeFileSync(process.env.HARD_SETUP_SHOT, shot.toPNG()); app.quit(); }
  }).catch(() => { if (win) win.webContents.send("setup:progress", "Не удалось открыть мастер. Повторите запуск установщика."); else app.exit(2); });
  app.on("window-all-closed", () => app.quit());
}
function state() { return { name: product.name, version: app.getVersion(), removing, target, current: hasProfile(profile, product.id),
  candidates: candidates.map(c => ({ source: c.source, label: c.label, last: c.last, kind: c.kind })), preview }; }
function progress(text: string) { win?.webContents.send("setup:progress", text); }
async function apply(source: string): Promise<{ ok: boolean; message?: string }> {
  if (busy || completed || preview) return { ok: false, message: preview ? "Это предварительный просмотр" : "Дождитесь окончания операции" };
  busy = true;
  try {
    const candidate = source ? candidates.find(c => c.source === source) : null;
    if (source && (!candidate || hasProfile(profile, product.id))) throw new Error("Действующий вход уже сохранён. Перенос поверх него запрещён.");
    if (candidate && !profilesAt(source, product.id).some(c => c.fingerprint === candidate.fingerprint)) throw new Error("Прежние настройки изменились. Выберите их заново.");
    progress("Проверяем, закрыта ли программа…");
    const check = await execute(helper(), [target, exeName()], 10000);
    if (check !== 0) throw new Error("Закройте " + product.name + " через значок у часов и нажмите «Повторить».");
    if (removing) {
      checkRoot(target); progress("Удаляем программу… Настройки и очередь остаются.");
      // Run an independent copy with NSIS's explicit _?= root. Otherwise the
      // uninstaller relaunches itself and its first process may exit early.
      const native = join(app.getPath("userData"), "native-remove.exe");
      rawFs.copyFileSync(join(target, "Uninstall hard-" + product.id + ".exe"), native);
      const code = await executeNsis(native, ["/S", "/currentuser", "_?=" + target]);
      for (let i = 0; i < 100 && existsSync(join(target, exeName())); i++) await new Promise(r => setTimeout(r, 200));
      if (code !== 0 || existsSync(join(target, exeName())) || registration()) throw new Error("Удаление не завершено. Настройки сохранены; повторите операцию.");
    } else {
      const payload = join(process.resourcesPath, "application-installer.exe");
      const manifest = jsonFile(join(process.resourcesPath, "setup-payload.json"));
      if (!manifest || manifest.product !== product.id || manifest.version !== app.getVersion() || createHash("sha512").update(readFileSync(payload)).digest("base64") !== manifest.sha512) throw new Error("Установщик повреждён. Используйте новую копию файла.");
      progress("Устанавливаем " + product.name + "…");
      const code = await executeNsis(payload, ["/S", "/currentuser", ...(process.argv.includes("--updated") ? ["--updated"] : []), "/D=" + target]);
      if (code !== 0 || !existsSync(join(target, exeName()))) throw new Error("Установка не завершена. Закройте программу и повторите.");
      if (candidate) { progress("Переносим выбранные настройки…"); importData(candidate.source, profile, product.id); }
    }
    completed = true; return { ok: true };
  } catch (error) { return { ok: false, message: (error as Error).message }; }
  finally { busy = false; }
}
