// Copy once before Chromium opens its encryption profile. The source is retained.
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import type { ProductId } from "./product";
import { discoverProfiles, hasProfile, jsonFile, profilesAt, type SearchLocations } from "./profile-discovery";

export function migrateData(target: string, appData: string, product: ProductId, locations?: Partial<SearchLocations>): void {
  mkdirSync(target, { recursive: true });
  const request = join(target, "migration-request.json");
  if (existsSync(request)) {
    const source = jsonFile(request)?.source;
    if (typeof source !== "string") throw new Error("Некорректный запрос переноса");
    importData(source, target, product); rmSync(request); return;
  }
  if (hasProfile(target, product)) return;
  const candidates = discoverProfiles(product, { appData, ...locations }, target);
  if (new Set(candidates.map(c => c.fingerprint)).size > 1) {
    writeFileSync(join(target, "migration-choice.json"), JSON.stringify({ candidates: candidates.map(c => c.source) }, null, 2)); return;
  }
  if (candidates.length) importData(candidates[0].source, target, product);
  else writeFileSync(join(target, "migration-v1.json"), JSON.stringify({ schema: 2, source: null }));
}

export function importData(source: string, target: string, product: ProductId): void {
  if (resolve(source).toLowerCase() === resolve(target).toLowerCase()) throw new Error("Это действующая папка программы");
  const options = profilesAt(source, product);
  const candidate = options.find(c => c.source.toLowerCase() === resolve(source).toLowerCase()) || (options.length === 1 ? options[0] : null);
  if (!candidate) throw new Error("В папке нет подходящих настроек или найдено несколько входов");
  const backend = candidate.kind === "backend";
  if (backend ? jsonFile(join(target, "backend-settings.json")) && hasProfile(target, "analyst") : hasProfile(target, product))
    throw new Error("В этой программе уже есть настройки. Перенос поверх них запрещён.");
  const field = product === "hard" || product === "soft";
  const s = jsonFile(backend ? candidate.source : join(candidate.source, "settings.json"))!;
  mkdirSync(target, { recursive: true });
  const stage = join(target, ".migration-" + randomUUID()); mkdirSync(stage);
  const published: string[] = [];
  try {
    if (backend) {
      const helper = join(process.resourcesPath || join(__dirname, "..", "..", "installer", "bin"), "process-check.exe");
      execFileSync(helper, ["protect", candidate.source, join(stage, "backend-settings.json")], { windowsHide: true, timeout: 10000, stdio: "ignore" });
    } else {
      const drafts = readdirSync(candidate.source).filter(n => /^draft-[a-f0-9]{64}\.bin$/.test(n));
      const files = ["Local State", ...(field ? ["field.key", "calls-pending.bin"] : ["device.key"]), "window-state.json", ...drafts];
      if (files.some(n => existsSync(join(candidate.source, n)) && /^v1[01]$/.test(readFileSync(join(candidate.source, n)).subarray(0, 3).toString())) && !existsSync(join(candidate.source, "Local State")))
        throw new Error("В прежней папке отсутствует ключ профиля Local State.");
      for (const n of files) if (existsSync(join(candidate.source, n))) cpSync(join(candidate.source, n), join(stage, n));
      const existing = jsonFile(join(target, "settings.json")) || {};
      const settings = field ? { ...existing, fieldServer: s.fieldServer, fieldDevice: s.fieldDevice, theme: s.theme || existing.theme, seenTrayHint: s.seenTrayHint }
        : { ...existing, ...s, fieldServer: undefined, fieldDevice: undefined, appMode: undefined };
      writeFileSync(join(stage, "settings.json"), JSON.stringify(settings, null, 2));
    }
    if (!profilesAt(candidate.source, product).some(c => c.fingerprint === candidate.fingerprint)) throw new Error("Прежние настройки изменились во время переноса");
    for (const n of readdirSync(stage).sort((a, b) => Number(a.endsWith("settings.json")) - Number(b.endsWith("settings.json")))) {
      const dest = join(target, n);
      if (existsSync(dest)) { cpSync(dest, join(stage, ".before-" + n)); cpSync(dest, dest + ".before-migration"); }
      renameSync(join(stage, n), dest);
      published.push(n);
    }
    writeFileSync(join(target, "migration-v1.json"), JSON.stringify({ schema: 2, source: candidate.source, at: new Date().toISOString() }));
    rmSync(join(target, "migration-choice.json"), { force: true });
  } catch (error) {
    for (const n of published.reverse()) {
      const previous = join(stage, ".before-" + n);
      if (existsSync(previous)) renameSync(previous, join(target, n));
      else rmSync(join(target, n), { force: true });
    }
    throw error;
  } finally { rmSync(stage, { recursive: true, force: true }); }
}
export function queueImport(source: string, target: string, product: ProductId = "soft"): void {
  if (hasProfile(target, product)) throw new Error("В этой программе уже есть настройки.");
  if (!profilesAt(source, product).length) throw new Error("Подходящие настройки не найдены");
  writeFileSync(join(target, "migration-request.json.tmp"), JSON.stringify({ source }));
  renameSync(join(target, "migration-request.json.tmp"), join(target, "migration-request.json"));
}
