// Снимки миниаппа выездника в окне размера телефона — проверка глазами без
// системного браузера. Ходит по экранам кликами, как человек.
//   PHONE_BASE=http://127.0.0.1:8090 PHONE_OUT=<папка> npx electron tools/phone-shots.cjs
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const base = process.env.PHONE_BASE || "http://127.0.0.1:8090";
const out = process.env.PHONE_OUT || "shots-phone";
const agent = process.env.PHONE_AGENT || "1001";
const boss = process.env.PHONE_BOSS || "444";
const W = 390, H = 844;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

app.commandLine.appendSwitch("force-device-scale-factor", "2");
// Без GPU: захват кадра окна на этой машине иначе падает с UnknownVizError.
app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  fs.mkdirSync(out, { recursive: true });
  const win = new BrowserWindow({ width: W, height: H, useContentSize: true, show: true, resizable: false,
    backgroundColor: "#f4e4cf", webPreferences: { partition: "phone-" + Date.now() } });
  const wc = win.webContents;
  const js = (c) => wc.executeJavaScript(c).catch((e) => console.log("js:", String(e).slice(0, 160)));
  const shot = async (name, wait = 900) => {
    await sleep(wait);
    try {
      fs.writeFileSync(path.join(out, name + ".png"), (await wc.capturePage()).toPNG());
      console.log("снимок", name);
    } catch (e) { console.log("не снялось", name, String(e)); }
  };
  const open = async (uid) => {
    await wc.loadURL(`${base}/?dev_uid=${uid}`);
    try {
      wc.debugger.attach("1.3");
      await wc.debugger.sendCommand("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 0, mobile: true });
      await wc.debugger.sendCommand("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    } catch { /* уже подключён */ }
    await sleep(2500);
  };
  await open(agent);
  await shot("01-list");
  await js(`document.querySelector('.pcard[onclick]')?.click()`);
  await shot("02-card", 1500);
  await js(`[...document.querySelectorAll('.tabs button')].pop()?.click()`);
  await shot("03-form", 1200);
  await js(`document.getElementById('app').scrollTop = 900`);
  await shot("04-form-bottom", 600);
  await js(`document.querySelector('.pick')?.click()`);
  await shot("05-pick-sheet", 900);
  await open(boss);
  await js(`[...document.querySelectorAll('.bottomnav button')].find(b => /Сводка|Хисобот|Ҳисобот/.test(b.textContent))?.click()`);
  await shot("06-digest", 2500);
  await js(`document.getElementById('app').scrollTop = 1100`);
  await shot("07-digest-table", 700);
  app.quit();
});
