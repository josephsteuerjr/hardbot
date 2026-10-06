// Поставка: переносимая папка «Хард» (Хард.exe + ресурсы) и zip рядом.
// Установщика нет: папку кладут куда удобно и запускают Хард.exe.
//   Руководителю — папка как есть: пульт руководителя.
//   Аналитику — та же папка ВНУТРИ комплекта (рядом с pult/ и server/): пульт
//   сам найдёт питон-сторону и откроется пультом аналитика.
//   node tools/dist.mjs
import { packager } from "@electron/packager";
import { cpSync, existsSync, lstatSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const here = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(here, "dist");
function removeBuildFolder(path) {
  const target = resolve(path);
  const rel = relative(resolve(dist), target);
  if (rel.startsWith("..") || isAbsolute(rel) || resolve(dist) === resolve(here)) throw new Error("Unsafe build path");
  if (existsSync(target)) {
    if (lstatSync(target).isSymbolicLink()) throw new Error("Build target is a link");
    const real = realpathSync(target);
    const actualRel = relative(realpathSync(here), real);
    if (actualRel.startsWith("..") || isAbsolute(actualRel) || !actualRel) throw new Error("Build target leaves project");
  }
  rmSync(target, { recursive: true, force: true });
}
removeBuildFolder(dist);

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
removeBuildFolder(folder);
cpSync(appDir, folder, { recursive: true });
removeBuildFolder(appDir);
cpSync(join(here, "Как начать.txt"), join(folder, "Как начать.txt"));
// zip — средствами Windows (tar из System32 умеет zip).
const zip = join(dist, "Хард.zip");
execFileSync(join(process.env.SystemRoot || "C:\\Windows", "System32", "tar.exe"), ["-a", "-c", "-f", zip, "-C", dist, "Хард"], { stdio: "inherit" });
writeFileSync(join(dist, "СОСТАВ.txt"), `Хард — ОДИН exe для сотрудников СНГ (Electron ${process.versions.electron ?? ""})
папка: ${folder}
архив: ${zip}
человеку ничего выбирать не нужно: вставляет СВОЮ ссылку — приложение само понимает роль (пульт руководителя/безопасника, приложение выездника или софтника) и открывает нужный экран
входы разных ролей на одном компьютере живут одновременно и друг друга не вытесняют
выезднику/софтнику: тот же Хард.exe, ссылка — у руководителя («Вход команды»); визит закрывается в телефоне, звонок — прямо на ПК
аналитику: своя копия ВНУТРИ комплекта (рядом pult/ и server/) — откроется пультом аналитика; переключатель ролей exe живёт там, в разделе «Подключение»
`);
console.log("готово:", folder, "\n", zip);
