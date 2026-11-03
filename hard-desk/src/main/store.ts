// Хранилище на этом компьютере: настройки открытым JSON, токен и кэш экранов —
// только зашифрованными (safeStorage = DPAPI учётной записи Windows). Скопированный
// на другую машину или под другого пользователя файл токена ничего не даёт.
import { app, safeStorage } from "electron";
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { createHash, generateKeyPairSync } from "node:crypto";
import { join } from "node:path";
import { PRODUCT_ID } from './product';
import { directSeal,directOpen,isDirect } from './windows-dpapi';

export interface Settings {
  server?: string;          // https://… — берётся из ссылки входа
  device?: { id?: number; name: string; created_at?: string; username?: string; role?: string; telegram_id?: number };
  fieldServer?: string;     // приложение выездника на ПК: свой вход
  // id — только у входа по ссылке; вход по логину-паролю пишет name/employee/username
  fieldDevice?: { id?: number; name: string; employee?: string; username?: string; telegram_id?: number; created_at?: string };
  seenTrayHint?: boolean;
  theme?: "light" | "dark" | "system";
  [k: string]: unknown;
}

const dir = () => app.getPath("userData");
const file = (name: string) => join(dir(), name);

/** One installation key shared by Hard and Soft for this Windows user. */
export function pcIdentity(): { privateKey: string; publicKey: string; fingerprint: string } {
  const base = process.env.HARD_USER_DATA ? join(process.env.HARD_USER_DATA, "shared")
    : join(process.env.LOCALAPPDATA || app.getPath("appData"), "Hard", "shared");
  mkdirSync(base, { recursive: true });
  const target = join(base, "pc-identity.bin");
  if (!existsSync(target)) {
    const pair = generateKeyPairSync("ed25519");
    const publicRaw = pair.publicKey.export({ type: "spki", format: "der" }).subarray(-32);
    const value = JSON.stringify({ privateKey: pair.privateKey.export({ type: "pkcs8", format: "pem" }).toString(),
      publicKey: publicRaw.toString("base64"), fingerprint: createHash("sha256").update(publicRaw).digest("hex") });
    try { writeFileSync(target, seal(value), { flag: "wx", flush: true }); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
  }
  return JSON.parse(open(readFileSync(target)));
}

function ensure(): void {
  mkdirSync(join(dir(), "cache"), { recursive: true });
}

export function readSettings(): Settings {
  try {
    return JSON.parse(readFileSync(file("settings.json"), "utf8")) as Settings;
  } catch {
    return {};
  }
}

export function writeSettings(patch: Partial<Settings>): Settings {
  ensure();
  const next = { ...readSettings(), ...patch };
  for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k];
  writeFileSync(file("settings.json.tmp"), JSON.stringify(next, null, 2));
  renameSync(file("settings.json.tmp"), file("settings.json"));
  return next;
}

function seal(text: string): Buffer {
  if (PRODUCT_ID === 'crm') return directSeal(text);
  if (!safeStorage.isEncryptionAvailable()) throw new Error("шифрование Windows недоступно");
  return safeStorage.encryptString(text);
}

function open(buf: Buffer): string {
  if (PRODUCT_ID === 'crm' && isDirect(buf)) return directOpen(buf);
  return safeStorage.decryptString(buf);
}

/** Recovery material is durable before the first enroll HTTP request. */
export function preparedEnroll(key: string, token: string): string {
  ensure();
  const name = file("enroll-" + createHash("sha256").update(key).digest("hex") + ".bin");
  if (existsSync(name)) return open(readFileSync(name));
  writeFileSync(name + ".tmp", seal(token), { flush: true });
  renameSync(name + ".tmp", name);
  return token;
}

export function readToken(): string {
  try {
    return open(readFileSync(file("device.key")));
  } catch {
    return "";
  }
}

export function writeToken(token: string): void {
  ensure();
  writeFileSync(file("device.key"), seal(token));
}

const cacheName = (key: string) => join(dir(), "cache", createHash("sha256").update(key).digest("hex").slice(0, 32) + ".bin");

export function cacheGet(key: string): { at: string; data: unknown } | null {
  try {
    const hit = JSON.parse(open(readFileSync(cacheName(key))));
    const age = Date.now() - Date.parse(hit.at);
    return Number.isFinite(age) && age >= 0 && age <= 8 * 60 * 60 * 1000 ? hit : null;
  } catch {
    return null;
  }
}

let cacheEpoch = 0;
export const cacheGeneration = (): number => cacheEpoch;

export function cachePut(key: string, data: unknown, generation = cacheEpoch): void {
  if (generation !== cacheEpoch) return;
  try {
    ensure();
    writeFileSync(cacheName(key), seal(JSON.stringify({ at: new Date().toISOString(), data })));
  } catch {
    /* кэш — удобство: не записался, значит без связи экран будет пустым */
  }
}

export function cacheRemove(key: string): void {
  cacheEpoch++;
  try { rmSync(cacheName(key), { force: true }); } catch { /* unavailable cache is optional */ }
}

export function clearCache(): void {
  cacheEpoch++;
  try {
    const c = join(dir(), "cache");
    if (existsSync(c)) for (const f of readdirSync(c)) rmSync(join(c, f), { force: true });
  } catch { /* best effort removal; network authority still gates fresh reads */ }
}

/** Отзыв или «Выйти»: стереть токен, устройство и все кэши экранов. */
export function wipe(): void {
  try { rmSync(file("device.key"), { force: true }); } catch { /* */ }
  clearCache();
  writeSettings({ device: undefined });
}

// Приложение выездника на ПК — СВОЙ токен и свои поля настроек: один exe на
// машине может быть и пультом руководителя, и приложением выездника; входы
// независимы (на сервере это разные классы устройств).
export function readFieldToken(): string {
  try {
    return open(readFileSync(file("field.key")));
  } catch {
    return "";
  }
}

export function writeFieldToken(token: string): void {
  ensure();
  writeFileSync(file("field.key"), seal(token));
}

export interface PendingCall { body: Record<string, unknown>; error?: string }
export interface CallQueue { owner: string; entries: PendingCall[] }
export function readCallQueue(): CallQueue | null {
  const path = file("calls-pending.bin");
  if (!existsSync(path)) return null;
  return JSON.parse(open(readFileSync(path))) as CallQueue;
}
export function writeCallQueue(queue: CallQueue): void {
  ensure();
  const path = file("calls-pending.bin"), temp = path + ".tmp";
  writeFileSync(temp, seal(JSON.stringify(queue)), { flush: true });
  renameSync(temp, path);
}
export function clearCallQueue(): void {
  for (const name of ["calls-pending.bin", "calls-pending.bin.tmp"]) rmSync(file(name), { force: true });
}

export function readDraft(key: string): unknown {
  const name = file("draft-" + createHash("sha256").update(key).digest("hex") + ".bin");
  return existsSync(name) ? JSON.parse(open(readFileSync(name))) : null;
}

export function hasFieldWork(): boolean {
  if (readCallQueue()?.entries.length) return true;
  for (const name of readdirSync(app.getPath("userData"))) {
    if (!/^draft-[a-f0-9]+\.bin$/.test(name)) continue;
    try {
      const draft = JSON.parse(open(readFileSync(file(name))));
      if (draft?.fields || draft?.office || typeof draft?.comment === "string") return true;
    } catch { return true; } // damaged work must be recovered before installing
  }
  return false;
}

// Durable unsent work may survive an update. Reject damaged storage, rather
// than requiring every valid offline queue to be sent before installation.
export function validateFieldWork(): void {
  readCallQueue();
  for(const name of readdirSync(app.getPath("userData"))){
    if(/^draft-[a-f0-9]+\.bin$/.test(name))JSON.parse(open(readFileSync(file(name))));
  }
}
export function writeDraft(key: string, draft: unknown): void {
  ensure();
  const name = file("draft-" + createHash("sha256").update(key).digest("hex") + ".bin");
  if (draft === null) { rmSync(name, { force: true }); return; }
  const data = JSON.stringify(draft);
  if (data.length > 256000) throw new Error("Черновик слишком большой");
  writeFileSync(name + ".tmp", seal(data), { flush: true }); renameSync(name + ".tmp", name);
}

export function wipeField(opts?: {discardWork?: boolean}): void {
  const identity = readSettings().fieldDevice;
  const pending = opts?.discardWork ? null : readCallQueue();
  if(opts?.discardWork){
    clearCallQueue();
    for(const name of readdirSync(app.getPath("userData"))){
      if(/^draft-[a-f0-9]+\.bin(?:\.tmp)?$/.test(name))rmSync(file(name),{force:true});
    }
  }
  try { rmSync(file("field.key"), { force: true }); } catch { /* */ }
  clearCache();
  // The revoked token and cached cards go away. Retain only the principal
  // needed to recover its encrypted unsent calls after a new link is issued.
  writeSettings({ fieldDevice: pending?.entries.length && identity?.telegram_id
    ? { id: identity.id, name: identity.name, telegram_id: identity.telegram_id }
    : undefined });
}
