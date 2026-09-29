// Поставка: переносимая папка «Хард» (Хард.exe + ресурсы) и zip рядом.
// Установщика нет: папку кладут куда удобно и запускают Хард.exe.
//   Руководителю — папка как есть: пульт руководителя.
//   Аналитику — та же папка ВНУТРИ комплекта (рядом с pult/ и server/): пульт
//   сам найдёт питон-сторону и откроется пультом аналитика.
//   node tools/dist.mjs
import { packager } from "@electron/packager";
import { cpSync, existsSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const here = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(here, "dist");
rmSync(dist, { recursive: true, force: true });

const [appDir] = await packager({
  dir: here,
  out: dist,
  name: "Хард",
  executableName: "Хард",
  platform: "win32",
  arch: "x64",
  icon: join(here, "icons", "icon.ico"),
  asar: true,
  overwrite: true,
  prune: true,
  appCopyright: "Хард",
  win32metadata: { CompanyName: "Хард", FileDescription: "Хард — пульт", ProductName: "Хард", InternalName: "Хард" },
  // В поставку — только собранное: out/ и package.json.
  ignore: (p) => p !== "" && !/^\/(out|package\.json)(\/|$)/.test(p.replace(/\\/g, "/")),
});

const folder = join(dist, "Хард");
rmSync(folder, { recursive: true, force: true });
cpSync(appDir, folder, { recursive: true });
rmSync(appDir, { recursive: true, force: true });
cpSync(join(here, "Как начать.txt"), join(folder, "Как начать.txt"));
if (existsSync(join(folder, "LICENSES.chromium.html"))) rmSync(join(folder, "LICENSES.chromium.html"));

// zip — средствами Windows (tar из System32 умеет zip).
const zip = join(dist, "Хард.zip");
execFileSync(join(process.env.SystemRoot || "C:\\Windows", "System32", "tar.exe"), ["-a", "-c", "-f", zip, "-C", dist, "Хард"], { stdio: "inherit" });
writeFileSync(join(dist, "СОСТАВ.txt"), `Хард — пульт (Electron ${process.versions.electron ?? ""})\nпапка: ${folder}\nархив: ${zip}\n`);
console.log("готово:", folder, "\n", zip);
