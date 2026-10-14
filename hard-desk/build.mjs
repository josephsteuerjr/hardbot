// Сборка: main и preload — CommonJS для Electron, страница — один IIFE-скрипт и
// стили рядом. Всё в out/: его и грузит окно (свой протокол hard://), его же
// кладёт в поставку tools/dist.mjs.
import { build } from "esbuild";
import { cpSync, existsSync, lstatSync, mkdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { products } from "./tools/products.mjs";
import { dirname, join, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "out");
const product = process.env.HARD_PRODUCT || "";
if (product && !["hard", "soft", "manager", "analyst"].includes(product)) throw new Error("Unknown product");
if (existsSync(out)) {
  const rel = relative(realpathSync(here), realpathSync(out));
  if (lstatSync(out).isSymbolicLink() || !rel || rel.startsWith("..") || isAbsolute(rel)) throw new Error("Unsafe output directory");
}
rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "ui"), { recursive: true });

const node = { bundle: true, platform: "node", format: "cjs", target: "node22", external: ["electron", "electron-updater", "original-fs"], logLevel: "warning", define: { __HARD_PRODUCT__: JSON.stringify(product) } };
await build({ ...node, entryPoints: [join(here, "src", "main", "launcher.ts")], outfile: join(out, "main.js") });
await build({ ...node, entryPoints: [join(here, "src", "setup", "entry.ts")], outfile: join(out, "setup-main.js") });
await build({ ...node, entryPoints: [join(here, "src", "setup", "preload.ts")], outfile: join(out, "setup-preload.js") });
mkdirSync(join(out, "setup"), { recursive: true });
await build({entryPoints:[join(here,"setup-ui","setup.ts")],bundle:true,format:"iife",target:["chrome130"],outfile:join(out,"setup","setup.js"),logLevel:"warning"});
await build({entryPoints:[join(here,"setup-ui","setup.css")],bundle:true,outfile:join(out,"setup","setup.css"),loader:{".woff2":"file",".svg":"dataurl"},assetNames:"fonts/[name]",logLevel:"warning"});
cpSync(join(here,"setup-ui","index.html"),join(out,"setup","index.html"));
function registryKey(id) {
  const namespace=Buffer.from('50e065bc313411e69bab38c9862bdaf3','hex');
  const bytes=createHash('sha1').update(namespace).update(id).digest().subarray(0,16);
  bytes[6]=(bytes[6]&15)|80;bytes[8]=(bytes[8]&63)|128;
  const s=bytes.toString('hex');return [s.slice(0,8),s.slice(8,12),s.slice(12,16),s.slice(16,20),s.slice(20)].join('-');
}
writeFileSync(join(out,'setup-registry.json'),JSON.stringify(Object.fromEntries(Object.entries(products).map(([id,p])=>[id,registryKey(p.appId+(process.env.HARD_SETUP_QA_BUILD?'_setupqa':''))]))));
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
cpSync(join(here, "ui", "tray.html"), join(out, "ui", "tray.html"));
await build({ entryPoints: [join(here, "ui", "tray.ts")], bundle: true, format: "iife", target: ["chrome130"], outfile: join(out, "ui", "tray.js"), logLevel: "warning" });
cpSync(join(here, "icons", "icon.svg"), join(out, "ui", "icon.svg"));
cpSync(join(here, "icons"), join(out, "icons"), { recursive: true });
console.log("out/ готов");
