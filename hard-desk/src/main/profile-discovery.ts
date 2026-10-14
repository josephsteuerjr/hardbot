import { existsSync, lstatSync, readFileSync, readdirSync, realpathSync, statSync } from "node:fs";
import { dirname, extname, join, resolve } from "node:path";
import { createHash } from "node:crypto";
import { platform } from "node:os";
import type { ProductId } from "./product";

export interface OldProfile { source: string; kind: "electron" | "backend"; label: string; last: number; fingerprint: string }
export interface SearchLocations { appData: string; localAppData?: string; home?: string; executable?: string; shortcuts?: string[]; readShortcut?: (path: string) => { target: string; cwd?: string } }
const legacyName = /хард|софт|hard|soft|пульт|аналит|взыск/i;
const pythonNames = ["backend-settings.json", "настройки пульта.json"];
export function jsonFile(path: string): Record<string, any> | null {
  try { if (statSync(path).size > 4 * 1024 * 1024) return null; const v = JSON.parse(readFileSync(path, "utf8").replace(/^\uFEFF/, "")); return v && typeof v === "object" && !Array.isArray(v) ? v : null; } catch { return null; }
}
export function hasProfile(target: string, product: ProductId): boolean {
  const field = product === "hard" || product === "soft";
  const s = jsonFile(join(target, "settings.json"));
  if (field ? s?.fieldDevice || existsSync(join(target, "field.key")) || existsSync(join(target, "calls-pending.bin"))
    : s?.device || existsSync(join(target, "device.key"))) return true;
  if (product === "analyst") { const b = jsonFile(join(target, "backend-settings.json"));
    if (b && (b.url || b.device_token || Object.values(b.profiles || {}).some((p: any) => p?.url || p?.device_token)
      || Object.entries(b).some(([k,v]) => !["theme","sec","profiles","active","url","device_token","token_protection"].includes(k) && v))) return true; }
  return readdirSafe(target).some(n => /^draft-[a-f0-9]{64}\.bin$/.test(n));
}
function readdirSafe(path: string): string[] { try { return readdirSync(path).slice(0, 600); } catch { return []; } }
function directory(path: string): boolean { try { return lstatSync(path).isDirectory() && !lstatSync(path).isSymbolicLink(); } catch { return false; } }
function canonical(path: string): string { try { return realpathSync(path); } catch { return resolve(path); } }
function inspect(source: string, product: ProductId): OldProfile | null {
  const field = product === "hard" || product === "soft";
  const backend = extname(source).toLowerCase() === ".json";
  const s = jsonFile(backend ? source : join(source, "settings.json"));
  if (!s) return null;
  if (backend) {
    if (product !== "analyst" || !(s.profiles || s.url)) return null;
    if (s.profiles && (typeof s.profiles !== "object" || Array.isArray(s.profiles) || Object.values(s.profiles).some(p => !p || typeof p !== "object" || Array.isArray(p)))) return null;
  } else if (field ? !(s.fieldServer && (s.fieldDevice || existsSync(join(source, "field.key")) || existsSync(join(source, "calls-pending.bin"))))
    : !(s.server && (s.device || existsSync(join(source, "device.key"))))) return null;
  const relevant = backend ? { ...s } : field ? { fieldServer: s.fieldServer, fieldDevice: s.fieldDevice } : { server: s.server, device: s.device };
  const files = backend ? [source] : [join(source, "Local State"), join(source, field ? "field.key" : "device.key"), ...(field ? [join(source, "calls-pending.bin")] : []), ...readdirSafe(source).filter(n => /^draft-[a-f0-9]{64}\.bin$/.test(n)).sort().map(n => join(source, n))];
  const digest = createHash("sha256").update(JSON.stringify(relevant));
  let last = 0;
  for (const file of files) if (existsSync(file)) { digest.update(readFileSync(file)); last = Math.max(last, statSync(file).mtimeMs); }
  return { source: canonical(source), kind: backend ? "backend" : "electron", label: String(field ? s.fieldDevice?.employee || s.fieldDevice?.name || "Прежний вход сотрудника" : backend ? s.active || "Настройки пульта" : s.device?.name || "Прежний вход руководителя"), last, fingerprint: digest.digest("hex") };
}
export function profilesAt(selection: string, product: ProductId, readShortcut?: SearchLocations["readShortcut"]): OldProfile[] {
  let path = resolve(selection);
  if (extname(path).toLowerCase() === ".lnk") {
    if (!readShortcut) throw new Error("Не удалось прочитать ярлык");
    const link = readShortcut(path); if (!link.target || extname(link.target).toLowerCase() !== ".exe") throw new Error("Выберите ярлык программы");
    path = dirname(link.target);
  } else if (extname(path).toLowerCase() === ".exe") path = dirname(path);
  if (extname(path).toLowerCase() === ".json") { const found = inspect(path, product); return found ? [found] : []; }
  const dirs = new Set<string>();
  let parent = path;
  for (let depth = 0; depth < 3; depth++, parent = dirname(parent)) {
    dirs.add(parent);
    for (const suffix of ["data", "userData", "_local", "pult", "Хард", "Хард-сотрудник", "Хард-аналитик", "resources"]) dirs.add(join(parent, suffix));
  }
  for (const name of readdirSafe(path)) if (legacyName.test(name) && directory(join(path, name))) { dirs.add(join(path, name)); dirs.add(join(path, name, "data")); dirs.add(join(path, name, "pult")); dirs.add(join(path, name, "_local")); }
  const results: OldProfile[] = [];
  for (const dir of dirs) {
    const found = inspect(dir, product); if (found) results.push(found);
    if (product === "analyst") for (const name of pythonNames) { const p = inspect(join(dir, name), product); if (p) results.push(p); }
  }
  return [...new Map(results.map(p => [p.source.toLowerCase(), p])).values()];
}
export function discoverProfiles(product: ProductId, locations: SearchLocations, target?: string): OldProfile[] {
  const drives = new Set([locations.appData, locations.localAppData, locations.home].filter(Boolean).map(p => String(p).slice(0, 2).toLowerCase()));
  const local = (p: string) => !/^[\\/]{2}/.test(p) && (platform() !== "win32" || drives.has(resolve(p).slice(0, 2).toLowerCase()));
  const roots = new Set([join(locations.appData, "Хард"), join(locations.appData, "hard-desk"), join(locations.appData, "Хард-сотрудник"), join(locations.appData, "Хард-аналитик")]);
  if (locations.localAppData) roots.add(join(locations.localAppData, "Hard", product, "data"));
  if (locations.executable) roots.add(dirname(locations.executable));
  if (locations.home) for (const folder of ["Desktop", "Downloads", "Documents"]) {
    const base = join(locations.home, folder);
    for (const name of readdirSafe(base)) if (legacyName.test(name) && directory(join(base, name))) roots.add(join(base, name));
  }
  const links: string[] = [];
  function walkLinks(base: string, depth: number) {
    if (links.length >= 300) return;
    for (const name of readdirSafe(base)) {
      const p = join(base, name);
      if (legacyName.test(name) && /\.lnk$/i.test(name)) links.push(p);
      else if (depth && directory(p)) walkLinks(p, depth - 1);
    }
  }
  for (const base of locations.shortcuts || []) if (local(base)) walkLinks(base, 2);
  for (const link of links) try { const v = locations.readShortcut?.(link); if (v?.target && /\.exe$/i.test(v.target) && local(v.target)) { roots.add(dirname(v.target)); if (v.cwd && local(v.cwd)) roots.add(v.cwd); } } catch { /* broken shortcut */ }
  const results: OldProfile[] = [];
  for (const root of roots) if (local(root)) results.push(...profilesAt(root, product));
  const own = target ? canonical(target).toLowerCase() : "";
  return [...new Map(results.filter(p => p.source.toLowerCase() !== own && p.source.toLowerCase() !== join(own, "backend-settings.json").toLowerCase()).map(p => [p.source.toLowerCase(), p])).values()].sort((a, b) => b.last - a.last);
}
