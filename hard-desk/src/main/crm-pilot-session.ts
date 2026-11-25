import { app, net } from 'electron';
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { openCrmRecord, sealCrmRecord } from './windows-dpapi';
import { PILOT_BASE, readExistingLogin, entryFingerprint, EntrySource } from './crm-manager-login';
import { fetchBuffered } from './transport';

type PilotSession = {
  token: string; expires_at: number; employee_uuid: string; name: string; fingerprint: string; source?: EntrySource;
  actor_uid?: number; access_model?: number; policy_revision?: number; grant_id?: string|null; grants?: any[];
};
const exchanging = new Map<string, Promise<PilotSession>>();
const path = () => join(app.getPath('userData'), 'crm-shared-session.bin');
let decoded: {file: string; bytes: Buffer; session: PilotSession} | undefined;
let candidates: any[] = [];
export function pilotGrantCandidates(): any[] { return candidates.map(grant => ({...grant})); }

function saved(): PilotSession | null {
  try {
    const file = path(), bytes = readFileSync(file);
    if (decoded?.file === file && decoded.bytes.equals(bytes)) return decoded.session;
    decoded = undefined;
    if (bytes.length > 32768) return null;
    const value = JSON.parse(openCrmRecord(bytes));
    if (!/^crm1\.[A-Za-z0-9_-]+$/.test(value.token) || !Number.isSafeInteger(value.expires_at) ||
        !/^[a-f0-9-]{36}$/.test(value.employee_uuid) || !/^[a-f0-9]{64}$/.test(value.fingerprint) ||
        value.actor_uid !== undefined && !Number.isSafeInteger(value.actor_uid) ||
        value.access_model !== undefined && ![1,2].includes(value.access_model) ||
        value.grant_id != null && (typeof value.grant_id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(value.grant_id))) return null;
    decoded = {file, bytes, session: value};
    return value;
  } catch { decoded = undefined; return null; }
}

async function exchange(entry: EntrySource, grantId?: string): Promise<PilotSession> {
  const source = readExistingLogin(entry);
  const header = source.kind === 'employee' ? 'X-App-Token' : source.kind === 'analyst-device' ? 'X-Device-Token' : 'X-Pult-Token';
  const response = await fetchBuffered((url, init) => net.fetch(url, init), PILOT_BASE + '/auth/exchange', {
    method: 'POST', headers: {[header]: source.credential, 'X-Client-Version': app.getVersion(),
      ...(source.kind === 'analyst' ? {'X-CRM-Entry': 'analyst'} : {}),
      ...(source.deviceFingerprint ? {'X-Device-Id': source.deviceFingerprint} : {}),
      ...(grantId ? {'X-CRM-Grant': grantId} : {})},
  }, 15000);
  const result = await response.json();
  if (!response.ok || !result.ok) {
    if (result.error === 'grant_required' && Array.isArray(result.details?.grants)) candidates = result.details.grants;
    throw Object.assign(new Error(String(result.text || 'Не удалось подтвердить вход Руководителя.').slice(0, 500)), {
      status: response.status, code: result.error, details: result.details,
    });
  }
  if (response.headers.get('X-CRM-Namespace') !== 'crm-shared-pilot-v1' ||
      typeof result.token !== 'string' || !/^crm1\.[A-Za-z0-9_-]{100,4096}$/.test(result.token) ||
      !/^[a-f0-9-]{36}$/.test(result.employee_uuid) || !Number.isSafeInteger(result.expires_at) ||
      result.expires_at <= Date.now() / 1000 || result.expires_at > Date.now() / 1000 + 1900 ||
      result.actor_uid !== undefined && !Number.isSafeInteger(result.actor_uid) ||
      result.access_model !== undefined && ![1,2].includes(result.access_model) ||
      result.grant_id != null && (typeof result.grant_id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(result.grant_id))) {
    throw new Error('Сервер не подтвердил сессию CRM.');
  }
  if (entryFingerprint(entry) !== source.fingerprint) throw new Error('Сохранённый вход изменился. Повторите подключение.');
  candidates = [];
  return {token: result.token, expires_at: result.expires_at, employee_uuid: result.employee_uuid,
    actor_uid: result.actor_uid, access_model: result.access_model, policy_revision: result.policy_revision,
    grant_id: result.grant_id ?? null, grants: Array.isArray(result.grants) ? result.grants : [],
    name: String(result.name || ''), fingerprint: source.fingerprint, source: entry};
}

export async function pilotCredential(expectedEmployee?: string, force = false, source: EntrySource = 'manager', grantId?: string): Promise<string> {
  let session = force ? null : saved();
  let changed = false;
  if (session && ((session.source || 'manager') !== source || session.expires_at < Date.now() / 1000 + 45 ||
      session.fingerprint !== entryFingerprint(source) || grantId !== undefined && session.grant_id !== grantId)) session = null;
  if (!session) {
    changed = true;
    const key = source + '\n' + (grantId || '');
    if (!exchanging.has(key)) exchanging.set(key, exchange(source, grantId).finally(() => {exchanging.delete(key);}));
    session = await exchanging.get(key)!;
  }
  if (expectedEmployee && session.employee_uuid !== expectedEmployee) {
    throw Object.assign(new Error('В Руководителе вошёл другой пользователь. Подключите его явно; прежняя очередь сохранена.'), {status: 409});
  }
  if (changed) {
    const file = path(), bytes = sealCrmRecord(JSON.stringify(session));
    writeFileSync(file + '.tmp', bytes, {flush: true});
    renameSync(file + '.tmp', file);
    decoded = {file, bytes, session};
  }
  return session.token;
}
