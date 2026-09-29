// Иконки из SVG: рисует сам Chromium (окно вне экрана), складывает PNG нужных
// размеров и собирает icon.ico (PNG внутри ICO — Windows это понимает с Vista).
//   npx electron tools/icons.cjs
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const dir = path.join(__dirname, "..", "icons");

let win = null;
async function render(svgFile, size) {
  // Одно окно вне экрана на все размеры: крошечные окна Windows не создаёт.
  if (!win) {
    win = new BrowserWindow({ width: 600, height: 600, show: false, frame: false, transparent: true,
      webPreferences: { offscreen: true } });
    await win.loadURL("data:text/html,<html><body style='margin:0;background:transparent'></body></html>");
  }
  const svg = fs.readFileSync(path.join(dir, svgFile), "utf8");
  const src = "data:image/svg+xml;base64," + Buffer.from(svg).toString("base64");
  await win.webContents.executeJavaScript(`new Promise(r => { document.body.innerHTML = '';
    const i = new Image(${size}, ${size}); i.onload = () => requestAnimationFrame(() => requestAnimationFrame(r));
    i.src = ${JSON.stringify(src)}; i.style.display = 'block'; document.body.appendChild(i); })`);
  await new Promise((r) => setTimeout(r, 120));
  const img = await win.webContents.capturePage({ x: 0, y: 0, width: size, height: size });
  return img.resize({ width: size, height: size, quality: "best" }).toPNG();
}

function ico(pngs) {
  const head = Buffer.alloc(6 + 16 * pngs.length);
  head.writeUInt16LE(0, 0); head.writeUInt16LE(1, 2); head.writeUInt16LE(pngs.length, 4);
  let offset = head.length;
  pngs.forEach(({ size, buf }, i) => {
    const e = 6 + 16 * i;
    head.writeUInt8(size >= 256 ? 0 : size, e); head.writeUInt8(size >= 256 ? 0 : size, e + 1);
    head.writeUInt8(0, e + 2); head.writeUInt8(0, e + 3);
    head.writeUInt16LE(1, e + 4); head.writeUInt16LE(32, e + 6);
    head.writeUInt32LE(buf.length, e + 8); head.writeUInt32LE(offset, e + 12);
    offset += buf.length;
  });
  return Buffer.concat([head, ...pngs.map((p) => p.buf)]);
}

app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256];
  const pngs = [];
  for (const s of sizes) pngs.push({ size: s, buf: await render(s <= 32 ? "tray.svg" : "icon.svg", s) });
  fs.writeFileSync(path.join(dir, "icon.ico"), ico(pngs));
  fs.writeFileSync(path.join(dir, "icon.png"), await render("icon.svg", 256));
  fs.writeFileSync(path.join(dir, "icon-512.png"), await render("icon.svg", 512));
  for (const s of [16, 20, 24, 32, 48]) fs.writeFileSync(path.join(dir, `tray-${s}.png`), await render("tray.svg", s));
  console.log("иконки готовы:", dir);
  app.quit();
});
