// Сборка: main и preload — CommonJS для Electron, страница — один IIFE-скрипт и
// стили рядом. Всё в out/: его и грузит окно (свой протокол hard://), его же
// кладёт в поставку tools/dist.mjs.
import { build } from "esbuild";
import { cpSync, existsSync, lstatSync, mkdirSync, realpathSync, rmSync } from "node:fs";
import { dirname, join, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "out");
if (existsSync(out)) {
  const rel = relative(realpathSync(here), realpathSync(out));
  if (lstatSync(out).isSymbolicLink() || !rel || rel.startsWith("..") || isAbsolute(rel)) throw new Error("Unsafe output directory");
}
rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "ui"), { recursive: true });

const node = { bundle: true, platform: "node", format: "cjs", target: "node22", external: ["electron"], logLevel: "warning" };
await build({ ...node, entryPoints: [join(here, "src", "main", "main.ts")], outfile: join(out, "main.js") });
await build({ ...node, entryPoints: [join(here, "src", "preload.ts")], outfile: join(out, "preload.js") });
await build({
  entryPoints: [join(here, "ui", "app.ts")],
  bundle: true,
  format: "iife",
  target: ["chrome130"],
  outfile: join(out, "ui", "app.js"),
  logLevel: "warning",
});
await build({
  entryPoints: [join(here, "ui", "styles", "index.css")],
  bundle: true,
  outfile: join(out, "ui", "app.css"),
  loader: { ".woff2": "file", ".svg": "dataurl" },
  assetNames: "fonts/[name]",
  logLevel: "warning",
});
cpSync(join(here, "ui", "index.html"), join(out, "ui", "index.html"));
cpSync(join(here, "icons", "icon.svg"), join(out, "ui", "icon.svg"));
cpSync(join(here, "icons"), join(out, "icons"), { recursive: true });
console.log("out/ готов");
