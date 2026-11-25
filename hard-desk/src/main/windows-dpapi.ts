import { safeStorage } from "electron";
import { existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

const HEADER = Buffer.from("CRM-DPAPI-1\n");
const ENVELOPE = Buffer.from("CRM-DPAPI-AES-1\n");
const ELECTRON_RECORD = Buffer.from("CRM-ELECTRON-1\n");
const MAX_BYTES = 256 * 1024 * 1024;
let checkpointKey: { key: Buffer; wrapped: Buffer } | undefined;
function transform(mode: "seal" | "unseal", bytes: Buffer): Buffer {
  const packaged = join(process.resourcesPath, "process-check.exe");
  const helper = existsSync(packaged)
    ? packaged
    : join(__dirname, "..", "installer", "bin", "process-check.exe");
  try {
    const result = execFileSync(helper, [mode], {
      input: bytes,
      windowsHide: true,
      timeout: 15000,
      maxBuffer: 17 * 1024 * 1024,
    });
    if (mode === 'seal' && result.length === 0) throw new Error('Empty encrypted record');
    return result;
  } catch {
    throw new Error(
      "Windows не подтвердил шифрование данных. Исходный файл сохранён.",
    );
  }
}
function checkedBytes(text: string): Buffer {
  const bytes = Buffer.from(text, "utf8");
  if (bytes.length > MAX_BYTES) throw new Error("Защищённый файл слишком большой. Исходный файл сохранён.");
  return bytes;
}
function sealEnvelope(bytes: Buffer, key: Buffer, wrapped: Buffer): Buffer {
  const nonce = randomBytes(12), length = Buffer.alloc(4);
  length.writeUInt32BE(wrapped.length);
  const prefix = Buffer.concat([ENVELOPE, length, wrapped, nonce]);
  const cipher = createCipheriv("aes-256-gcm", key, nonce);
  cipher.setAAD(prefix);
  return Buffer.concat([prefix, cipher.update(bytes), cipher.final(), cipher.getAuthTag()]);
}
export function directSeal(text: string): Buffer {
  const bytes = checkedBytes(text);
  if (bytes.length <= 8 * 1024 * 1024)
    return Buffer.concat([HEADER, transform("seal", bytes)]);
  // The random key is wrapped by this Windows user and stored in the SAME
  // atomically replaced file. No external first-run key can be lost on a crash.
  const key = randomBytes(32);
  try { return sealEnvelope(bytes, key, transform("seal", key)); }
  finally { key.fill(0); }
}
/** Each checkpoint includes the Windows-wrapped key and can be opened after a restart. */
export function directSealCheckpoint(text: string): Buffer {
  const bytes = checkedBytes(text);
  if (!checkpointKey) {
    const key = randomBytes(32);
    try { checkpointKey = { key, wrapped: transform("seal", key) }; }
    catch (error) { key.fill(0); throw error; }
  }
  return sealEnvelope(bytes, checkpointKey.key, checkpointKey.wrapped);
}
export function isDirect(bytes: Buffer): boolean {
  return bytes.subarray(0, HEADER.length).equals(HEADER) || bytes.subarray(0, ENVELOPE.length).equals(ENVELOPE);
}

export function isCrmRecord(bytes: Buffer): boolean {
  return isDirect(bytes) || bytes.subarray(0, ELECTRON_RECORD.length).equals(ELECTRON_RECORD);
}

let useProfileEncryption = false;
export function sealCrmRecord(text: string, primary = directSeal): Buffer {
  if (Buffer.byteLength(text) > MAX_BYTES)
    throw new Error('Защищённый файл слишком большой. Исходный файл сохранён.');
  // Avoid retrying an unavailable helper during every cache and queue write.
  if (!useProfileEncryption) {
    try { return primary(text); }
    catch { useProfileEncryption = true; }
  }
  try {
    if (process.platform === 'win32' && safeStorage.isEncryptionAvailable()) {
      const bytes = safeStorage.encryptString(text);
      if (safeStorage.decryptString(bytes) === text) return Buffer.concat([ELECTRON_RECORD, bytes]);
    }
  } catch { /* Neither provider could verify the encrypted record. */ }
  throw new Error('Windows не смог защитить данные входа. Войдите в CRM через браузер.');
}

export function openCrmRecord(bytes: Buffer): string {
  if (bytes.subarray(0, ELECTRON_RECORD.length).equals(ELECTRON_RECORD))
    return safeStorage.decryptString(bytes.subarray(ELECTRON_RECORD.length));
  return directOpen(bytes);
}
export function directOpen(bytes: Buffer): string {
  if (!isDirect(bytes))
    throw new Error("Неизвестный формат защищённых данных.");
  if (bytes.subarray(0, HEADER.length).equals(HEADER))
    return transform("unseal", bytes.subarray(HEADER.length)).toString("utf8");
  if (bytes.length < ENVELOPE.length + 4 || bytes.length > MAX_BYTES + 4096)
    throw new Error("Повреждён защищённый файл. Исходный файл сохранён.");
  const size = bytes.readUInt32BE(ENVELOPE.length), offset = ENVELOPE.length + 4;
  if (size < 32 || size > 2048 || bytes.length < offset + size + 28)
    throw new Error("Повреждён защищённый файл. Исходный файл сохранён.");
  const end = offset + size + 12;
  const key = transform("unseal", bytes.subarray(offset, offset + size));
  try {
    if (key.length !== 32) throw new Error("Некорректный ключ Windows.");
    const cipher = createDecipheriv("aes-256-gcm", key, bytes.subarray(end - 12, end));
    cipher.setAAD(bytes.subarray(0, end));
    cipher.setAuthTag(bytes.subarray(-16));
    return Buffer.concat([cipher.update(bytes.subarray(end, -16)), cipher.final()]).toString("utf8");
  } finally { key.fill(0); }
}

/** Read existing Windows-protected material in the main process only. */
export function unprotectWindows(bytes: Buffer): Buffer {
  return transform("unseal", bytes);
}
