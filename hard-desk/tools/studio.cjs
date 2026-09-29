// Дизайн-студия миниаппа: окна-телефоны нескольких размеров, все роли
// дев-стенда, матрица снимков и живая перезагрузка при правке оболочки.
// Браузер машины не нужен и не запускается: всё — в окнах Electron.
//
// Дев-стенд (синтетика; владелец — uid 555):
//   cd server && ADMIN_ID=555 ADMIN_API_KEY=devmaster MINIAPP_URL=http://127.0.0.1:8090 python pult_dev.py
// Снимки всех сцен во всех размерах + лист-обзор (out/studio/):
//   node_modules/electron/dist/electron.exe tools/studio.cjs
// Только часть сцен (по имени, регулярка) и один размер:
//   STUDIO_ONLY=digest STUDIO_SIZES=390x844 …/electron.exe tools/studio.cjs
// Живые окна (роли через запятую), перезагрузка при сохранении index.html:
//   STUDIO_LIVE=1001,222 …/electron.exe tools/studio.cjs
//
// Переменные: STUDIO_BASE (адрес стенда), STUDIO_OUT (папка снимков),
// STUDIO_SIZES («390x844,360x780»), STUDIO_THEMES («light,dark»),
// STUDIO_DSF (плотность, 2), STUDIO_LONG=0 —
// без длинных снимков во всю прокрутку, STUDIO_SHEET=0 — без листа-обзора.
const { app, BrowserWindow, session } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");
const INDEX = path.join(ROOT, "server", "miniapp", "index.html");
const base = process.env.STUDIO_BASE || "http://127.0.0.1:8090";
const out = path.resolve(process.env.STUDIO_OUT || path.join(__dirname, "..", "out", "studio"));
const sizes = (process.env.STUDIO_SIZES || "390x844,360x780").split(",").map((s) => {
  const [w, h] = s.trim().split("x").map(Number); return { w, h, tag: `${w}` };
});
const DSF = +(process.env.STUDIO_DSF || 2);
// Темы: системная светлая и/или тёмная (prefers-color-scheme через отладчик).
const themes = (process.env.STUDIO_THEMES || "light").split(",").map((s) => s.trim()).filter(Boolean);
const LONG = process.env.STUDIO_LONG !== "0";
const only = process.env.STUDIO_ONLY ? new RegExp(process.env.STUDIO_ONLY) : null;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

app.disableHardwareAcceleration();   // иначе захват кадра на этой машине падает (UnknownVizError)

function phoneWindow(size, part, title) {
  const ses = session.fromPartition(part);
  // Сервис-воркер держал бы старую оболочку: студия всегда берёт файл с диска.
  ses.webRequest.onBeforeRequest({ urls: ["*://*/sw.js"] }, (_d, cb) => cb({ cancel: true }));
  const win = new BrowserWindow({
    width: size.w, height: size.h, useContentSize: true, show: true, resizable: false,
    title, backgroundColor: "#f4e4cf",
    webPreferences: { session: ses, preload: path.join(__dirname, "studio-preload.cjs"),
      contextIsolation: false, sandbox: false, backgroundThrottling: false },
  });
  return win;
}

async function emulate(wc, w, h, theme) {
  try { wc.debugger.attach("1.3"); } catch { /* уже подключён */ }
  if (theme) await wc.debugger.sendCommand("Emulation.setEmulatedMedia",
    { features: [{ name: "prefers-color-scheme", value: theme }] });
  await wc.debugger.sendCommand("Emulation.setDeviceMetricsOverride",
    { width: w, height: h, deviceScaleFactor: DSF, mobile: true });
  await wc.debugger.sendCommand("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
}

// Сцена получает вот это: открыть роль, выполнить код в странице, дождаться
// тишины в сети, снять кадр (экран или во всю длину прокрутки).
function driver(win, size, shots) {
  const wc = win.webContents;
  const js = (code) => wc.executeJavaScript(code).catch((e) => { console.log("  js:", String(e).slice(0, 200)); });
  const settle = async (min = 350, max = 8000) => {
    const t0 = Date.now();
    await sleep(min);
    let quiet = 0;
    while (Date.now() - t0 < max) {
      const n = await wc.executeJavaScript("window.__studioInflight ? window.__studioInflight() : 0").catch(() => 0);
      quiet = n ? 0 : quiet + 1;
      if (quiet >= 3) break;
      await sleep(120);
    }
    await sleep(450);   // анимация «lift» — 220 мс, лист снизу — тоже
  };
  const capture = async () => {
    const r = await wc.debugger.sendCommand("Page.captureScreenshot", { format: "png" });
    return Buffer.from(r.data, "base64");
  };
  const d = {
    size,
    async open(uid, extra = "") {
      // Эмуляция — после первой загрузки: отладчик на пустом окне роняет Electron.
      await wc.loadURL(`${base}/?dev_uid=${uid}${extra}`);
      await emulate(wc, size.w, size.h, size.theme);
      await js("lockAppViewport && lockAppViewport()");
      await settle(600);
    },
    js, settle,
    async click(text, sel = "button") {
      const ok = await js(`(()=>{const re=new RegExp(${JSON.stringify(text)});
        const b=[...document.querySelectorAll(${JSON.stringify(sel)})].find(x=>re.test(x.textContent));
        if(b){ b.click(); return true; } return false; })()`);
      if (!ok) console.log(`  нет кнопки /${text}/ в ${sel}`);
      await settle();
      return ok;
    },
    async shot(name, opts = {}) {
      const file = `${name}@${size.tag}${size.theme === "dark" ? "-dark" : ""}`;
      fs.writeFileSync(path.join(out, file + ".png"), await capture());
      shots.push({ name, size: size.tag + (size.theme === "dark" ? " · тёмная" : ""), file: file + ".png", long: false });
      console.log("  снимок", file);
      if (!(opts.long ?? LONG)) return;
      // Во всю длину: высота окна = шапка + вся прокрутка #app. Оболочка держит
      // высоту в --app-height, её выставляем тем же числом и потом возвращаем.
      const full = await js(`(()=>{const a=document.getElementById('app');
        const top=a.getBoundingClientRect().top; const nav=document.getElementById('bottomnav');
        const navh=(nav&&getComputedStyle(nav).display!=='none')?nav.offsetHeight:0;
        return Math.ceil(top + a.scrollHeight + Math.max(0,navh-100));})()`);
      if (!full || full <= size.h + 40) return;
      const h = Math.min(full, 12000);
      await wc.debugger.sendCommand("Emulation.setDeviceMetricsOverride",
        { width: size.w, height: h, deviceScaleFactor: DSF, mobile: true });
      await js(`document.documentElement.style.setProperty('--app-height','${h}px')`);
      await sleep(400);
      fs.writeFileSync(path.join(out, file + "-long.png"), await capture());
      shots.push({ name, size: size.tag, file: file + "-long.png", long: true });
      console.log("  длинный", file + "-long", h + "px");
      await wc.debugger.sendCommand("Emulation.setDeviceMetricsOverride",
        { width: size.w, height: size.h, deviceScaleFactor: DSF, mobile: true });
      await js(`document.documentElement.style.setProperty('--app-height','${size.h}px')`);
      await sleep(200);
    },
  };
  return d;
}

// Лист-обзор: все снимки экрана (не длинные) сеткой, подписи — сцена и ширина.
// Рисуется в скрытом окне вне экрана и снимается одним кадром.
async function contactSheet(shots) {
  const list = shots.filter((s) => !s.long);
  if (!list.length) return;
  const cols = Math.min(6, list.length), cellW = 300;
  const cells = list.map((s) => {
    const img = fs.readFileSync(path.join(out, s.file)).toString("base64");
    return `<figure><img src="data:image/png;base64,${img}"><figcaption>${s.name} · ${s.size}</figcaption></figure>`;
  }).join("");
  const html = `<!doctype html><meta charset="utf-8"><style>
    body{margin:0;padding:16px;background:#e9d6bd;font:13px Segoe UI,sans-serif;color:#1d1a17}
    main{display:grid;grid-template-columns:repeat(${cols},${cellW}px);gap:16px}
    figure{margin:0} img{width:${cellW}px;display:block;border:1px solid #997766;border-radius:10px}
    figcaption{padding:4px 2px}</style><main>${cells}</main>`;
  const tmp = path.join(out, "_sheet.html");
  fs.writeFileSync(tmp, html);
  const rows = Math.ceil(list.length / cols);
  const width = 32 + cols * cellW + (cols - 1) * 16;
  const height = 32 + rows * (cellW * 2.2 + 40);
  const win = new BrowserWindow({ show: false, width, height: 800, webPreferences: { offscreen: true } });
  await win.loadFile(tmp);
  const wc = win.webContents;
  wc.debugger.attach("1.3");
  const real = await wc.executeJavaScript("document.documentElement.scrollHeight");
  await wc.debugger.sendCommand("Emulation.setDeviceMetricsOverride",
    { width, height: Math.max(real, 400), deviceScaleFactor: 1, mobile: false });
  await sleep(500);
  const r = await wc.debugger.sendCommand("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(path.join(out, "_sheet.png"), Buffer.from(r.data, "base64"));
  fs.unlinkSync(tmp);
  win.destroy();
  console.log("лист-обзор", path.join(out, "_sheet.png"), `${width}×${height}`);
}

async function runShots() {
  const scenes = require("./studio-scenes.cjs");
  fs.mkdirSync(out, { recursive: true });
  for (const f of fs.readdirSync(out)) if (f.endsWith(".png")) fs.unlinkSync(path.join(out, f));
  const shots = [];
  for (const size0 of sizes) for (const theme of themes) {
    const size = { ...size0, theme };
    for (const sc of scenes) {
      if (only && !only.test(sc.name)) continue;
      console.log(`сцена ${sc.name} @${size.tag}`);
      // Каждая сцена — в своём хранилище: вход, кэш и черновики не перетекают.
      const win = phoneWindow(size, `studio-${sc.name}-${size.tag}-${Date.now()}`, sc.name);
      try { await sc.run(driver(win, size, shots)); }
      catch (e) { console.log("  сцена упала:", String(e && e.stack || e).slice(0, 400)); }
      win.destroy();
    }
  }
  fs.writeFileSync(path.join(out, "_shots.json"), JSON.stringify(shots, null, 1));
  if (process.env.STUDIO_SHEET !== "0") await contactSheet(shots);
  app.quit();
}

// Живой режим: окно на каждую роль, правка index.html перезагружает все.
async function runLive(uids) {
  const size = sizes[0];
  const wins = uids.map((uid, i) => {
    const win = phoneWindow(size, `studio-live-${uid}`, `Студия · ${uid}`);
    win.setPosition(20 + i * (size.w + 16), 20);
    const go = async () => { await win.loadURL(`${base}/?dev_uid=${uid}`); await emulate(win.webContents, size.w, size.h, themes[0]); };
    go();
    return { win, go };
  });
  let t = null;
  fs.watch(INDEX, () => {
    clearTimeout(t);
    t = setTimeout(() => { console.log("правка оболочки — перезагружаю"); wins.forEach((w) => w.win.webContents.reloadIgnoringCache()); }, 250);
  });
  console.log("живые окна:", uids.join(", "), "— слежу за", INDEX);
}

app.whenReady().then(() => {
  const live = process.env.STUDIO_LIVE;
  if (live) runLive(live.split(",").map((s) => s.trim()).filter(Boolean));
  else runShots();
});
app.on("window-all-closed", () => { if (process.env.STUDIO_LIVE) app.quit(); });
