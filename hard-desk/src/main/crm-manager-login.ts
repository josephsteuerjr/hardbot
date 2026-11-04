/** Read-only reuse of the current Windows user's existing Manager login.
 * This module is main-process-only; no credential or profile path is exposed
 * through preload. Local role/name fields are never authorization evidence.
 */
import { createDecipheriv, createHash } from 'node:crypto';
import { lstatSync, readFileSync, existsSync } from 'node:fs';
import { join, dirname, resolve, parse } from 'node:path';
import { directOpen, isDirect, unprotectWindows } from './windows-dpapi';

export const PILOT_BASE = 'https://hard.rimborso.ru/crm-pilot';
const AUTHORITY_ORIGINS = new Set(['https://hard.rimborso.ru', 'https://hard.rimborso.ru:8080',
  // Existing profiles can retain the project's former address. It is only
  // local provenance: exchange ALWAYS targets PILOT_BASE on the current host.
  'https://rim-borso.duckdns.org']);
function trustedAuthority(value: unknown): boolean {
  try {
    if (typeof value !== 'string') return false;
    const url = new URL(value);
    return AUTHORITY_ORIGINS.has(url.origin) && url.pathname === '/' &&
      !url.username && !url.password && !url.search && !url.hash;
  } catch { return false; }
}

export class ManagerLoginError extends Error {
  constructor(public code: string, message: string) { super(message); }
}

export function managerProfile(): string {
  if (!process.env.LOCALAPPDATA) throw new ManagerLoginError('profile_missing', 'Windows не сообщил папку профиля.');
  return join(process.env.LOCALAPPDATA, 'Hard', 'manager', 'data');
}

function checkedFile(profile: string, name: string, maximum: number): Buffer {
  const target = resolve(profile, name);
  if (dirname(target) !== resolve(profile)) throw new Error('Invalid profile filename');
  let cursor = target;
  while (cursor !== parse(cursor).root) {
    const info = lstatSync(cursor);
    if (info.isSymbolicLink()) throw new ManagerLoginError('profile_redirected', 'Папка входа перенаправлена. Автоматическое подключение остановлено.');
    cursor = dirname(cursor);
  }
  const info = lstatSync(target);
  if (!info.isFile() || info.size > maximum) throw new ManagerLoginError('profile_invalid', 'Сохранённый вход имеет неизвестный формат.');
  return readFileSync(target);
}

export function managerFingerprint(profile = managerProfile()): string {
  return createHash('sha256').update(checkedFile(profile, 'device.key', 16384)).digest('hex');
}

export function existingManagerStatus(): { available: boolean; text: string } {
  try {
    const profile = managerProfile();
    if (!existsSync(join(profile, 'device.key'))) return {available: false, text: 'Сначала войдите в установленный Руководитель на этом компьютере.'};
    const settings = JSON.parse(checkedFile(profile, 'settings.json', 1024 * 1024).toString('utf8'));
    if (!trustedAuthority(settings.server)) return {available: false, text: 'Сохранённый Руководитель подключён к другому серверу.'};
    managerFingerprint(profile);
    return {available: true, text: 'Найден сохранённый вход Руководителя. Личность и права проверит сервер.'};
  } catch { return {available: false, text: 'Не удалось прочитать сохранённый вход Руководителя.'}; }
}

export function readExistingManagerSession(profile = managerProfile(), unprotect = unprotectWindows): { credential: string; fingerprint: string } {
  const settingsRaw = checkedFile(profile, 'settings.json', 1024 * 1024);
  const settings = JSON.parse(settingsRaw.toString('utf8'));
  if (!trustedAuthority(settings.server)) throw new ManagerLoginError('authority_mismatch', 'Сохранённый вход относится к другому серверу.');
  const blob = checkedFile(profile, 'device.key', 16384);
  let credential: string, stateRaw: Buffer | undefined, key: Buffer | undefined;
  try {
    if (isDirect(blob)) credential = directOpen(blob);
    else if (['v10', 'v11'].includes(blob.subarray(0, 3).toString('ascii'))) {
      if (blob.length < 32) throw new Error('Invalid encrypted token');
      stateRaw = checkedFile(profile, 'Local State', 16 * 1024 * 1024);
      const wrapped = Buffer.from(JSON.parse(stateRaw.toString('utf8')).os_crypt.encrypted_key, 'base64');
      if (wrapped.subarray(0, 5).toString('ascii') !== 'DPAPI') throw new Error('Unknown Windows key');
      key = unprotect(wrapped.subarray(5));
      if (key.length !== 32) throw new Error('Invalid Windows key');
      const cipher = createDecipheriv('aes-256-gcm', key, blob.subarray(3, 15));
      cipher.setAuthTag(blob.subarray(-16));
      credential = Buffer.concat([cipher.update(blob.subarray(15, -16)), cipher.final()]).toString('utf8');
    } else {
      credential = unprotect(blob).toString('utf8');
    }
    if (!/^[A-Za-z0-9_-]{20,512}$/.test(credential)) throw new Error('Invalid token');
  } catch {
    throw new ManagerLoginError('windows_login_unavailable', 'Windows не подтвердил сохранённый вход. Откройте Руководитель под тем же пользователем Windows.');
  } finally { key?.fill(0); }
  if (!settingsRaw.equals(checkedFile(profile, 'settings.json', 1024 * 1024)) ||
      !blob.equals(checkedFile(profile, 'device.key', 16384)) ||
      (stateRaw && !stateRaw.equals(checkedFile(profile, 'Local State', 16 * 1024 * 1024)))) {
    throw new ManagerLoginError('login_changed', 'Вход Руководителя изменился во время проверки. Повторите подключение.');
  }
  return {credential, fingerprint: createHash('sha256').update(blob).digest('hex')};
}
