// Иконки PWA миниаппа из той же метки: 192 и 512, без прозрачности (Android
// кладёт иконку на свою подложку, iOS прозрачное заливает чёрным).
//   npx electron tools/pwa-icons.cjs <папка vendor>
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const out = process.argv[process.argv.length - 1];
  const svg = fs.readFileSync(path.join(__dirname, "..", "icons", "icon.svg"), "utf8")
    .replace('<rect x="10" y="10" width="236" height="236" rx="58"', '<rect x="0" y="0" width="256" height="256" rx="0" fill="#f4e4cf"/><rect x="10" y="10" width="236" height="236" rx="58"');
  const w = new BrowserWindow({ width: 600, height: 600, show: false, frame: false, webPreferences: { offscreen: true } });
  await w.loadURL("data:text/html,<html><body style='margin:0;background:%23f4e4cf'></body></html>");
  for (const size of [192, 512]) {
    const src = "data:image/svg+xml;base64," + Buffer.from(svg).toString("base64");
    await w.webContents.executeJavaScript(`new Promise(r => { document.body.innerHTML=''; const i = new Image(${size}, ${size});
      i.onload = () => requestAnimationFrame(() => requestAnimationFrame(r)); i.src = ${JSON.stringify(src)}; i.style.display='block'; document.body.appendChild(i); })`);
    await new Promise((r) => setTimeout(r, 150));
    const img = await w.webContents.capturePage({ x: 0, y: 0, width: size, height: size });
    fs.writeFileSync(path.join(out, `icon-${size}.png`), img.resize({ width: size, height: size, quality: "best" }).toPNG());
  }
  console.log("иконки PWA готовы");
  app.quit();
});
