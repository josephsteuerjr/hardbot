import { app, net } from 'electron';
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { directOpen, directSeal } from './windows-dpapi';
import { PILOT_BASE, readExistingManagerSession, managerFingerprint } from './crm-manager-login';
import { fetchBuffered } from './transport';

type PilotSession = { token: string; expires_at: number; employee_uuid: string; name: string; fingerprint: string };
let exchanging: Promise<PilotSession> | null = null;
const path = () => join(app.getPath('userData'), 'crm-shared-session.bin');

function saved(): PilotSession | null {
  try {
    const bytes = readFileSync(path());
    if (bytes.length > 32768) return null;
    const value = JSON.parse(directOpen(bytes));
    if (!/^crm1\.[A-Za-z0-9_-]+$/.test(value.token) || !Number.isSafeInteger(value.expires_at) ||
        !/^[a-f0-9-]{36}$/.test(value.employee_uuid) || !/^[a-f0-9]{64}$/.test(value.fingerprint)) return null;
    return value;
  } catch { return null; }
}

async function exchange(): Promise<PilotSession> {
  const source = readExistingManagerSession();
  const response = await fetchBuffered((url, init) => net.fetch(url, init), PILOT_BASE + '/auth/exchange', {
    method: 'POST', headers: {'X-Pult-Token': source.credential, 'X-Client-Version': app.getVersion()},
  }, 15000);
  const result = await response.json();
  if (!response.ok || !result.ok) throw Object.assign(new Error(String(result.text || 'Не удалось подтвердить вход Руководителя.').slice(0, 500)), {status: response.status});
  if (response.headers.get('X-CRM-Namespace') !== 'crm-shared-pilot-v1' ||
      typeof result.token !== 'string' || !/^crm1\.[A-Za-z0-9_-]{100,4096}$/.test(result.token) ||
      !/^[a-f0-9-]{36}$/.test(result.employee_uuid) || !Number.isSafeInteger(result.expires_at) ||
      result.expires_at <= Date.now() / 1000 || result.expires_at > Date.now() / 1000 + 1900) {
    throw new Error('Сервер не подтвердил отдельную сессию пилота.');
  }
  if (managerFingerprint() !== source.fingerprint) throw new Error('Вход Руководителя изменился. Повторите подключение.');
  return {token: result.token, expires_at: result.expires_at, employee_uuid: result.employee_uuid,
    name: String(result.name || ''), fingerprint: source.fingerprint};
}

export async function pilotCredential(expectedEmployee?: string, force = false): Promise<string> {
  let session = force ? null : saved();
  let changed = false;
  if (session && (session.expires_at < Date.now() / 1000 + 45 || session.fingerprint !== managerFingerprint())) session = null;
  if (!session) {
    changed = true;
    if (!exchanging) exchanging = exchange().finally(() => {exchanging = null;});
    session = await exchanging;
  }
  if (expectedEmployee && session.employee_uuid !== expectedEmployee) {
    throw Object.assign(new Error('В Руководителе вошёл другой пользователь. Подключите его явно; прежняя очередь сохранена.'), {status: 409});
  }
  if (changed) {
    writeFileSync(path() + '.tmp', directSeal(JSON.stringify(session)), {flush: true});
    renameSync(path() + '.tmp', path());
  }
  return session.token;
}
