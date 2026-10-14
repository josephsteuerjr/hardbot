/** Only files explicitly selected by this window or produced by an export. */
import { existsSync, realpathSync, statSync } from "node:fs";
import { dirname, extname, relative, resolve, sep } from "node:path";

type Grant = { path: string; directory: boolean; write: boolean };
const grants = new Map<number, Grant[]>();
const canonical = (path: string): string => existsSync(path) ? realpathSync(path) : resolve(realpathSync(dirname(path)), path.split(/[\\/]/).pop()!);
const inside = (base: string, path: string) => { const rel = relative(base, path); return !rel.startsWith(".." + sep) && rel !== ".." && !/^[A-Za-z]:/.test(rel); };

export function grantFile(owner: number, path: string, write = false, directory = false): string {
  const value = canonical(path);
  grants.set(owner, [...(grants.get(owner) || []), { path: value, write, directory }]);
  return path;
}

export function selectedFile(owner: number, path: string, write = false): string {
  const value = canonical(path);
  const allowed = (grants.get(owner) || []).some(g => (!write || g.write || g.directory) &&
    (g.path === value || g.directory && inside(g.path, value)));
  if (!allowed) throw new Error("Выберите этот файл или папку штатной кнопкой перед операцией.");
  return value;
}

/** Backend file operations declare their access; names do not imply permission. */
export function selectedLocalFile(owner: number, route: string, path: string): string {
  const reads = new Set(["/settings/import", "/reg/pick", "/reg/columns_check", "/enr/base", "/enr/replay"]);
  const writes = new Set(["/exp/save", "/exp/markers"]);
  if (!reads.has(route) && !writes.has(route)) throw new Error("Маршрут не принимает выбранный файл.");
  return selectedFile(owner, path, writes.has(route));
}

export function uploadFile(owner: number, path: string): string {
  const selected = selectedFile(owner, path);
  if (![".jpg", ".jpeg", ".png", ".webp"].includes(extname(selected).toLowerCase()) || statSync(selected).size > 20 * 1024 * 1024)
    throw new Error("Выберите изображение JPG, PNG или WebP до 20 МБ.");
  return selected;
}

export function openFile(owner: number, path: string): string {
  const selected = selectedFile(owner, path);
  if (![".xlsx", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".json"].includes(extname(selected).toLowerCase()))
    throw new Error("Этот тип файла нельзя открыть из приложения.");
  return selected;
}

export function clearFileGrants(owner: number): void { grants.delete(owner); }
