// Хранилище на этом компьютере: настройки открытым JSON, токен и кэш экранов —
// только зашифрованными (safeStorage = DPAPI учётной записи Windows). Скопированный
// на другую машину или под другого пользователя файл токена ничего не даёт.
import { app, safeStorage } from "electron";
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

export interface Settings {
  server?: string;          // https://… — берётся из ссылки входа
  device?: { id: number; name: string; created_at?: string };
  fieldServer?: string;     // приложение выездника на ПК: свой вход
  fieldDevice?: { id: number; name: string; employee?: string; telegram_id?: number; created_at?: string };
  seenTrayHint?: boolean;
  theme?: "light" | "dark" | "system";
  [k: string]: unknown;
}

const dir = () => app.getPath("userData");
const file = (name: string) => join(dir(), name);

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
  writeFileSync(file("settings.json"), JSON.stringify(next, null, 2));
  return next;
}

function seal(text: string): Buffer {
  if (!safeStorage.isEncryptionAvailable()) throw new Error("шифрование Windows недоступно");
  return safeStorage.encryptString(text);
}

function open(buf: Buffer): string {
  return safeStorage.decryptString(buf);
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
    return JSON.parse(open(readFileSync(cacheName(key))));
  } catch {
    return null;
  }
}

export function cachePut(key: string, data: unknown): void {
  try {
    ensure();
    writeFileSync(cacheName(key), seal(JSON.stringify({ at: new Date().toISOString(), data })));
  } catch {
    /* кэш — удобство: не записался, значит без связи экран будет пустым */
  }
}

/** Отзыв или «Выйти»: стереть токен, устройство и все кэши экранов. */
export function wipe(): void {
  try { rmSync(file("device.key"), { force: true }); } catch { /* */ }
  try {
    const c = join(dir(), "cache");
    if (existsSync(c)) for (const f of readdirSync(c)) rmSync(join(c, f), { force: true });
  } catch { /* */ }
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
  writeFileSync(temp, seal(JSON.stringify(queue)));
  renameSync(temp, path);
}
export function clearCallQueue(): void {
  for (const name of ["calls-pending.bin", "calls-pending.bin.tmp"]) rmSync(file(name), { force: true });
}

export function wipeField(): void {
  const identity = readSettings().fieldDevice;
  const pending = readCallQueue();
  try { rmSync(file("field.key"), { force: true }); } catch { /* */ }
  try {
    const c = join(dir(), "cache");
    if (existsSync(c)) for (const f of readdirSync(c)) rmSync(join(c, f), { force: true });
  } catch { /* */ }
  // The revoked token and cached cards go away. Retain only the principal
  // needed to recover its encrypted unsent calls after a new link is issued.
  writeSettings({ fieldDevice: pending?.entries.length && identity?.telegram_id
    ? { id: identity.id, name: identity.name, telegram_id: identity.telegram_id }
    : undefined });
}
