import { ActionRunner, type ActionResult } from '../kit/async-action';
import { DialogManager } from '../kit/dialog';
import { ViewScope } from '../kit/view-scope';

export interface RoleGrant {
  grant_id?: string;
  actor_uid?: number;
  role: string;
  territory: string;
  capabilities: string[];
  employees: string[];
  all_contracts: boolean;
  read_all_contracts: boolean;
  is_default: boolean;
  revision?: number;
}

export interface GrantDiff {
  add: RoleGrant[];
  remove: RoleGrant[];
  update: Array<{ before: RoleGrant; after: RoleGrant }>;
}

export interface RoleEditorHandle {
  refresh(repaint?: boolean): Promise<void>;
  dispose(): void;
}

interface RoleDefinition {
  id: string;
  label: string;
  capabilities: string[];
  manageable: boolean;
}

interface EditorDraft {
  actor_uid: number;
  base_revision: number;
  grants: RoleGrant[];
  operation_id: string;
  preview_sha256?: string;
  preview_grants?: RoleGrant[];
  preview_base_revision?: number;
  diff?: unknown;
}

interface ApiError extends Error {
  status?: number;
  code?: string;
  data?: any;
}

const ROLE_LABELS: Record<string, string> = {
  employee: 'Сотрудник', agent: 'Сотрудник', manager: 'Руководитель',
  observer: 'Наблюдатель', courier: 'Курьер', analyst: 'Аналитик',
  logistic: 'Логист', owner: 'Владелец', root: 'Root', server_operator: 'Оператор сервера',
};
const TERRITORY_LABELS: Record<string, string> = {
  self: 'Только себя', employees: 'Выбранные сотрудники', team: 'Своя команда', all: 'Все сотрудники',
};
const PROTECTED_ROLES = new Set(['owner', 'root', 'server_operator']);
const HISTORY_LABELS: Record<string, string> = {
  grant_created: 'Добавлено полномочие', grant_updated: 'Изменено полномочие',
  grant_revoked: 'Отозвано полномочие', role_revoked: 'Отозвана роль',
  access_model_activated: 'Включена модель grants', access_model_changed: 'Изменена модель доступа',
};

const esc = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]!));
const unique = (values: unknown) => [...new Set(Array.isArray(values) ? values.filter(value => typeof value === 'string' && value.trim()).map(value => value.trim()) : [])].sort();
const stamp = (value: unknown) => value ? new Date(String(value)).toLocaleString('ru-RU') : '—';
const pair = (grant: Pick<RoleGrant, 'role' | 'territory'>) => `${grant.role}\n${grant.territory}`;
const comparable = (grant: RoleGrant) => ({
  grant_id: grant.grant_id, role: grant.role, territory: grant.territory,
  capabilities: grant.capabilities, employees: grant.employees,
  all_contracts: grant.all_contracts, read_all_contracts: grant.read_all_contracts,
  is_default: grant.is_default,
});
const fingerprint = (grants: RoleGrant[]) => JSON.stringify(normalizeGrants(grants).map(comparable));

export function normalizeGrants(value: unknown): RoleGrant[] {
  if (!Array.isArray(value)) return [];
  return value.map(item => {
    const grant = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>;
    const normalized: RoleGrant = {
      role: String(grant.role ?? '').trim(),
      territory: String(grant.territory ?? '').trim(),
      capabilities: unique(grant.capabilities),
      employees: grant.territory === 'employees' ? unique(grant.employees ?? grant.employee_scope) : [],
      all_contracts: grant.all_contracts === true,
      read_all_contracts: grant.read_all_contracts === true,
      is_default: grant.is_default === true,
    };
    if (typeof grant.grant_id === 'string' && grant.grant_id) normalized.grant_id = grant.grant_id;
    if (typeof grant.actor_uid === 'number') normalized.actor_uid = grant.actor_uid;
    if (typeof grant.revision === 'number') normalized.revision = grant.revision;
    return normalized;
  });
}

export function validateRoleGrants(value: unknown): RoleGrant[] {
  const grants = normalizeGrants(value);
  const pairs = new Set<string>();
  let defaults = 0;
  for (const grant of grants) {
    if (!grant.role || !grant.territory) throw new Error('Выберите роль и территорию каждого полномочия.');
    const key = pair(grant);
    if (pairs.has(key)) throw new Error('Одна пара роли и территории не должна повторяться. Измените существующую карточку.');
    pairs.add(key);
    if (grant.territory === 'employees' && !grant.employees.length) throw new Error('Выберите сотрудников для явной территории.');
    if (grant.is_default) defaults += 1;
  }
  if (defaults > 1) throw new Error('Полномочие по умолчанию может быть только одно.');
  return grants;
}

export function diffRoleGrants(beforeValue: unknown, afterValue: unknown): GrantDiff {
  const before = normalizeGrants(beforeValue);
  const after = normalizeGrants(afterValue);
  const beforeByPair = new Map(before.map(grant => [pair(grant), grant]));
  const afterByPair = new Map(after.map(grant => [pair(grant), grant]));
  const result: GrantDiff = { add: [], remove: [], update: [] };
  for (const grant of after) {
    const previous = beforeByPair.get(pair(grant));
    if (!previous) result.add.push(grant);
    else if (JSON.stringify(comparable(previous)) !== JSON.stringify(comparable(grant))) result.update.push({ before: previous, after: grant });
  }
  result.remove = before.filter(grant => !afterByPair.has(pair(grant)));
  return result;
}

export function editableRoles(catalog: any): RoleDefinition[] {
  const source = catalog?.roles ?? catalog?.role_catalog ?? catalog?.available_roles ?? [];
  const values = Array.isArray(source) ? source : Object.entries(source || {}).map(([id, value]) =>
    value && typeof value === 'object' ? { id, ...value } : { id, capabilities: value });
  const explicit = catalog?.editable_roles ?? catalog?.manageable_roles;
  const allowed = Array.isArray(explicit) ? new Set(explicit.map(String)) : null;
  return values.map((entry: any): RoleDefinition => {
    const id = String(typeof entry === 'string' ? entry : entry?.id ?? entry?.role ?? '');
    const capabilities = unique(typeof entry === 'object' ? entry?.capabilities : undefined);
    return {
      id,
      label: String(typeof entry === 'object' ? entry?.label ?? entry?.title : '') || ROLE_LABELS[id] || id,
      capabilities,
      manageable: !PROTECTED_ROLES.has(id) && (!allowed || allowed.has(id)) &&
        !(typeof entry === 'object' && (entry?.manageable === false || entry?.editable === false || entry?.allowed === false)),
    };
  }).filter(role => role.id && role.manageable);
}

function territoryDefinitions(catalog: any, owner: boolean) {
  const source = catalog?.territories ?? catalog?.territory_catalog ?? ['self', 'employees', 'team', 'all'];
  const values = Array.isArray(source) ? source : Object.keys(source || {});
  return values.map((entry: any) => {
    const id = String(typeof entry === 'string' ? entry : entry?.id ?? entry?.territory ?? '');
    return { id, label: String(typeof entry === 'object' ? entry?.label ?? entry?.title : '') || TERRITORY_LABELS[id] || id };
  }).filter((territory, index) => territory.id && (owner || values[index]?.owner_only !== true));
}

function revisionOf(value: any): number {
  const revision = Number(value?.base_revision ?? value?.revision ?? value?.policy_revision ?? 0);
  return Number.isInteger(revision) && revision >= 0 ? revision : 0;
}

function errorFromResponse(response: any, fallback: string): ApiError {
  const data = response?.data;
  return Object.assign(new Error(data?.text || data?.message || response?.text || data?.error?.message || data?.error || fallback), {
    status: response?.status,
    code: data?.code || data?.error?.code,
    data,
  }) as ApiError;
}

export const ROLE_EDITOR_CSS = `.access-editor{border-top:1px solid var(--line);margin-top:20px;padding-top:16px;min-width:0}
.access-editor-fields{border:0;padding:0;margin:0;min-width:0}
.access-editor-head{display:flex;align-items:start;justify-content:space-between;gap:12px}
.access-editor-head h3{margin:0 0 4px}.access-editor-head p{margin:0;color:var(--ink-3)}
.access-editor-meta{font-size:12px;color:var(--ink-3);overflow-wrap:anywhere}
.access-grant-list{display:grid;gap:10px;margin:14px 0}
.access-grant-card{border:1px solid var(--line-strong);border-radius:8px;padding:12px;background:var(--panel)}
.access-grant-card-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}
.access-grant-card-head strong{font-size:13px}.access-grant-default{display:flex;align-items:center;gap:6px;font-size:12px}
.access-grant-fields{display:grid;grid-template-columns:minmax(150px,1fr) minmax(180px,1fr);gap:10px}
.access-grant-fields label{display:grid;gap:5px;font-size:12px;color:var(--ink-2)}
.access-grant-fields select{width:100%}.access-grant-employees{grid-column:1/-1}
.access-grant-employees select{min-height:92px}.access-grant-readonly{color:var(--ink-3);font-size:12px;margin:8px 0 0}
.access-editor-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.access-editor-status{min-height:1.5em;color:var(--ink-2)}.access-editor-status[data-kind=error]{color:var(--err)}
.access-editor-warnings{color:var(--warn)}.access-editor-warnings p{margin:5px 0}
.access-diff{margin:14px 0;padding:10px 12px;border-left:3px solid var(--accent);background:var(--accent-soft)}
.access-diff p{margin:5px 0}.access-diff small{display:block;overflow-wrap:anywhere;color:var(--ink-3)}
.access-history{margin-top:14px}.access-history-list{max-height:240px;overflow:auto}
.access-history-item{border-top:1px solid var(--line);padding:8px 0}.access-history-item p{margin:2px 0}
.access-history-item small{color:var(--ink-3)}.access-confirm-diff{max-height:45vh;overflow:auto}
@media(max-width:640px){.access-grant-fields{grid-template-columns:1fr}.access-editor-head{display:block}.access-editor-head button{margin-top:8px}}
`;

function ensureStyles(host: HTMLElement) {
  const document = host.ownerDocument;
  if (document.querySelector('style[data-access-editor-styles]')) return;
  const style = document.createElement('style');
  style.dataset.accessEditorStyles = '';
  style.textContent = ROLE_EDITOR_CSS;
  document.head.append(style);
}

export async function mountRoleEditor(
  host: HTMLElement,
  crm: any,
  options: {
    actorUid: number;
    employeeName?: string;
    owner?: boolean;
    valid?: () => boolean;
    employees?: Array<{ employee_uuid: string; name: string }>;
    onApplied?: () => void | Promise<void>;
  },
): Promise<RoleEditorHandle> {
  ensureStyles(host);
  const actorUid = options.actorUid;
  const valid = options.valid || (() => true);
  const scope = new ViewScope();
  let disposed = false;
  let serverGrants: RoleGrant[] = [];
  let state: EditorDraft = { actor_uid: actorUid, base_revision: 0, grants: [], operation_id: crypto.randomUUID() };
  let catalog: any = {};
  let roles: RoleDefinition[] = [];
  let territories: Array<{ id: string; label: string }> = [];
  let employees = options.employees || [];
  let history: any[] = [];
  let remoteWarnings: string[] = [];
  let preview: any = null;
  let previewFingerprint = '';
  let canManage = true;
  let busy = false;
  let uncertain = false;
  let editRevision = 0;
  let accessModel = 1;
  let saveChain: Promise<void> = Promise.resolve();
  const current = () => !disposed && valid();

  host.innerHTML = `<section class="access-editor" aria-label="Редактор ролей и территорий">
    <div class="access-editor-head"><div><h3>Роли и территории</h3><p>Каждая карточка — одно самостоятельное полномочие.</p></div><button type="button" data-access-refresh>Обновить</button></div>
    <p class="access-editor-meta" data-access-meta></p><div class="access-editor-warnings" data-access-warnings></div>
    <form data-access-form><fieldset class="access-editor-fields" data-access-fields><div class="access-grant-list" data-access-cards></div>
      <div class="access-editor-actions"><button type="button" data-access-add>Добавить полномочие</button><button type="submit" class="primary" data-access-preview>Проверить изменения</button></div>
    </fieldset></form><section class="access-diff" data-access-diff hidden></section>
    <div class="access-editor-actions"><button type="button" class="primary" data-access-apply hidden>Применить изменения</button></div>
    <p class="access-editor-status" data-access-status role="status" aria-live="polite"></p>
    <details class="access-history"><summary>История полномочий</summary><div class="access-history-list" data-access-history></div></details>
    <dialog data-access-confirm><form method="dialog"><h3>Применить полномочия?</h3><p>${esc(options.employeeName || 'Сотрудник')} · изменения вступят в силу для каждой новой проверки доступа.</p><div class="access-confirm-diff" data-access-confirm-diff></div><p data-access-confirm-status role="status"></p><div class="access-editor-actions"><button type="submit">Отмена</button><button type="button" class="primary" data-access-confirm-apply>Подтвердить</button></div></form></dialog>
  </section>`;
  const $ = <T extends HTMLElement = HTMLElement>(selector: string) => host.querySelector<T>(selector)!;
  const dialog = $<HTMLDialogElement>('[data-access-confirm]');
  const dialogManager = new DialogManager(dialog, { returnFocus: () => $('[data-access-apply]') });
  let runner = createRunner();

  function createRunner() {
    return new ActionRunner(snapshot => {
      if (!current()) return;
      busy = snapshot.phase === 'validating' || snapshot.phase === 'persisting' || snapshot.phase === 'submitting';
      renderControls();
    });
  }

  const api = async (method: string, action: string, body?: any) => {
    const response = await crm.api(method, action, body);
    if (!response?.ok || response.stale || response.data?.ok === false) throw errorFromResponse(response, 'Служба полномочий недоступна.');
    return response.data;
  };
  const readList = async () => api('POST', 'grant-admin', { operation: 'list' });
  const actorState = (value: any) => {
    if (!Array.isArray(value?.actors)) return value;
    const actor = value.actors.find((entry: any) => entry.actor_uid === actorUid);
    if (!actor) throw new Error('Аккаунт не входит в область управления полномочиями.');
    return { ...actor, history: actor.history || value.history, warnings: [...(value.warnings || []), ...(actor.warnings || []), ...(actor.reason ? [actor.reason] : [])] };
  };
  const save = (value: EditorDraft | null) => {
    const result = crm.draft(`role-editor:${actorUid}`, value);
    return Promise.resolve(result).then(response => {
      if (response?.ok === false) throw new Error('Не удалось сохранить черновик полномочий на этом устройстве.');
      return response;
    });
  };
  const persist = () => {
    const snapshot: EditorDraft = {
      ...state,
      grants: normalizeGrants(state.grants),
      preview_grants: preview?.grants ? normalizeGrants(preview.grants) : state.preview_grants,
    };
    saveChain = saveChain.catch(() => undefined).then(() => save(snapshot)).then(() => undefined);
    return saveChain;
  };
  const status = (text: string, kind = '') => {
    if (!current()) return;
    const element = $('[data-access-status]');
    element.textContent = text;
    element.dataset.kind = kind;
  };

  function roleDefinition(id: string) {
    return roles.find(role => role.id === id);
  }

  function employeeCatalog() {
    const remote = catalog?.employees ?? catalog?.staff;
    if (!Array.isArray(remote)) return employees;
    return remote.map((entry: any) => ({
      employee_uuid: String(entry.employee_uuid ?? entry.id ?? ''),
      name: String(entry.name ?? entry.employee ?? entry.label ?? entry.employee_uuid ?? entry.id ?? ''),
    })).filter((entry: any) => entry.employee_uuid);
  }

  function isProtected(grant: RoleGrant) {
    return PROTECTED_ROLES.has(grant.role) || !roleDefinition(grant.role) ||
      !territories.some(territory => territory.id === grant.territory);
  }

  function renderCards() {
    const staff = employeeCatalog();
    $('[data-access-cards]').innerHTML = state.grants.map((grant, index) => {
      const readonly = !canManage || isProtected(grant);
      const availableRoles = roleDefinition(grant.role) || readonly
        ? roles : [...roles, { id: grant.role, label: ROLE_LABELS[grant.role] || grant.role, capabilities: grant.capabilities, manageable: true }];
      const roleOptions = (availableRoles.some(role => role.id === grant.role) ? availableRoles : [...availableRoles, { id: grant.role, label: ROLE_LABELS[grant.role] || grant.role, capabilities: grant.capabilities, manageable: true }])
        .map(role => `<option value="${esc(role.id)}"${role.id === grant.role ? ' selected' : ''}>${esc(role.label)}</option>`).join('');
      const availableTerritories = territories.some(territory => territory.id === grant.territory) ? territories :
        [...territories, { id: grant.territory, label: TERRITORY_LABELS[grant.territory] || grant.territory }];
      const territoryOptions = availableTerritories.map(territory => `<option value="${esc(territory.id)}"${territory.id === grant.territory ? ' selected' : ''}>${esc(territory.label)}</option>`).join('');
      const employeeOptions = staff.map(employee => `<option value="${esc(employee.employee_uuid)}"${grant.employees.includes(employee.employee_uuid) ? ' selected' : ''}>${esc(employee.name)}</option>`).join('');
      return `<article class="access-grant-card" data-access-card="${index}">
        <div class="access-grant-card-head"><strong>${esc(ROLE_LABELS[grant.role] || grant.role)} · ${esc(TERRITORY_LABELS[grant.territory] || grant.territory)}</strong><span>
          <label class="access-grant-default"><input type="radio" name="access-default" data-access-default="${index}"${grant.is_default ? ' checked' : ''}${readonly ? ' disabled' : ''}>По умолчанию</label>
          ${readonly ? '' : `<button type="button" class="link" data-access-remove="${index}">Удалить</button>`}</span></div>
        <div class="access-grant-fields"><label>Роль<select data-access-role="${index}"${readonly ? ' disabled' : ''}>${roleOptions}</select></label>
          <label>Территория<select data-access-territory="${index}"${readonly ? ' disabled' : ''}>${territoryOptions}</select></label>
          <label class="access-grant-employees" data-access-employees="${index}"${grant.territory === 'employees' ? '' : ' hidden'}>Сотрудники территории<select multiple data-access-employee="${index}"${readonly ? ' disabled' : ''}>${employeeOptions}</select></label>
        </div>${readonly ? '<p class="access-grant-readonly">Серверное полномочие показано только для чтения.</p>' : ''}</article>`;
    }).join('') || '<p>Действующих полномочий нет. Добавьте первую пару роли и территории.</p>';
    host.querySelectorAll<HTMLButtonElement>('[data-access-remove]').forEach(button => {
      button.onclick = () => {
        if (busy) return;
        state.grants.splice(Number(button.dataset.accessRemove), 1);
        changed(true);
      };
    });
    host.querySelectorAll<HTMLInputElement>('[data-access-default]').forEach(input => {
      input.onchange = () => {
        readForm();
        state.grants = state.grants.map((grant, index) => ({ ...grant, is_default: index === Number(input.dataset.accessDefault) }));
        changed(false);
      };
    });
  }

  function readForm() {
    state.grants = state.grants.map((grant, index) => {
      const role = host.querySelector<HTMLSelectElement>(`[data-access-role="${index}"]`);
      const territory = host.querySelector<HTMLSelectElement>(`[data-access-territory="${index}"]`);
      const employee = host.querySelector<HTMLSelectElement>(`[data-access-employee="${index}"]`);
      if (!role || !territory) return grant;
      const selectedTerritory = territory.value;
      return {
        ...grant,
        role: role.value,
        territory: selectedTerritory,
        capabilities: role.value === grant.role ? grant.capabilities : roleDefinition(role.value)?.capabilities || [],
        employees: selectedTerritory === 'employees' ? Array.from(employee?.selectedOptions || []).map(option => option.value) : [],
        is_default: host.querySelector<HTMLInputElement>(`[data-access-default="${index}"]`)?.checked === true,
      };
    });
    return state.grants;
  }

  function changed(repaint: boolean) {
    readForm();
    editRevision += 1;
    state.operation_id = crypto.randomUUID();
    uncertain = false;
    preview = null;
    previewFingerprint = '';
    delete state.preview_sha256;
    delete state.preview_grants;
    delete state.preview_base_revision;
    delete state.diff;
    if (repaint) renderCards();
    for (const grant of state.grants) {
      const index = state.grants.indexOf(grant);
      const employees = host.querySelector<HTMLElement>(`[data-access-employees="${index}"]`);
      if (employees) employees.hidden = grant.territory !== 'employees';
      const heading = host.querySelector<HTMLElement>(`[data-access-card="${index}"] .access-grant-card-head strong`);
      if (heading) heading.textContent = grantText(grant);
    }
    renderDiff(diffRoleGrants(serverGrants, state.grants));
    renderControls();
    void persist().catch(error => status(error.message, 'error'));
  }

  function grantText(grant: RoleGrant) {
    const employees = grant.territory === 'employees' ? ` · ${grant.employees.length} сотр.` : '';
    return `${ROLE_LABELS[grant.role] || grant.role} · ${TERRITORY_LABELS[grant.territory] || grant.territory}${employees}${grant.is_default ? ' · по умолчанию' : ''}`;
  }

  function diffMarkup(diff: any, sha = '') {
    if (!diff) return '';
    if (Array.isArray(diff)) return `${diff.map(change => `<p>${esc(change.text || `${change.action || 'Изменить'}: ${change.grant_id || 'модель доступа'}`)}</p>`).join('') || '<p>Изменений нет.</p>'}${sha ? `<small>preview_sha256: ${esc(sha)}</small>` : ''}`;
    const rows: string[] = [];
    for (const grant of diff.add || diff.added || []) rows.push(`<p>+ ${esc(grantText(normalizeGrants([grant])[0]))}</p>`);
    for (const grant of diff.remove || diff.removed || []) rows.push(`<p>− ${esc(grantText(normalizeGrants([grant])[0]))}</p>`);
    for (const change of diff.update || diff.updated || diff.change || []) {
      const after = change.after || change;
      rows.push(`<p>± ${esc(grantText(normalizeGrants([after])[0]))}</p>`);
    }
    return `${rows.join('') || '<p>Изменений нет.</p>'}${sha ? `<small>preview_sha256: ${esc(sha)}</small>` : ''}`;
  }

  function renderDiff(diff: any, sha = '') {
    const element = $('[data-access-diff]');
    element.innerHTML = diffMarkup(diff, sha);
    element.hidden = false;
    $('[data-access-confirm-diff]').innerHTML = diffMarkup(diff);
  }

  function renderWarnings() {
    const warnings = [...remoteWarnings, ...(Array.isArray(preview?.warnings) ? preview.warnings : [])].map(String);
    $('[data-access-warnings]').innerHTML = [...new Set(warnings)].map(warning => `<p>${esc(warning)}</p>`).join('');
  }

  function renderHistory() {
    $('[data-access-history]').innerHTML = history.length ? history.map(change => {
      const after = change.after || change.after_json || {};
      const before = change.before || change.before_json || {};
      const grant = after.role ? after : before;
      return `<article class="access-history-item"><p>${esc(HISTORY_LABELS[change.action] || change.action || 'Изменение')} · ${esc(grant.role ? grantText(normalizeGrants([grant])[0]) : 'модель доступа')}</p><small>${esc(stamp(change.changed_at || change.at || change.created_at))} · ревизия ${esc(change.revision ?? '—')}</small></article>`;
    }).join('') : '<p>Сервер не передал прежнюю историю. Diff применённых здесь операций будет показан в этом списке.</p>';
  }

  function renderMeta() {
    $('[data-access-meta]').textContent = `Модель доступа ${accessModel} · ревизия ${state.base_revision} · operation_id ${state.operation_id}`;
  }

  function renderControls() {
    const dirty = fingerprint(serverGrants) !== fingerprint(state.grants);
    const validPreview = !!preview?.preview_sha256 && previewFingerprint === fingerprint(state.grants);
    $<HTMLButtonElement>('[data-access-add]').disabled = busy || !canManage || !roles.length;
    $<HTMLButtonElement>('[data-access-preview]').disabled = busy || !canManage;
    const serverDiff = Array.isArray(preview?.diff) ? preview.diff.length > 0 : !!preview?.diff && Object.keys(preview.diff).length > 0;
    $<HTMLButtonElement>('[data-access-apply]').hidden = !validPreview;
    $<HTMLButtonElement>('[data-access-apply]').disabled = busy || !dirty && !serverDiff && !preview?.receipt && !uncertain;
    $<HTMLButtonElement>('[data-access-apply]').textContent = uncertain ? 'Проверить исход операции' : 'Применить изменения';
    $<HTMLButtonElement>('[data-access-refresh]').disabled = busy;
    dialogManager.setSubmitting(busy && dialog.open);
    renderMeta();
  }

  function applyList(data: any, repaint: boolean) {
    const grants = Array.isArray(data?.grants) ? data.grants : data?.normalized_grants;
    if (repaint && Array.isArray(grants)) {
      serverGrants = normalizeGrants(grants);
      state.grants = normalizeGrants(grants);
      state.base_revision = revisionOf(data);
      preview = null;
      previewFingerprint = '';
      renderCards();
      renderDiff(diffRoleGrants(serverGrants, state.grants));
    }
    if (Array.isArray(data?.history)) history = data.history;
    else if (Array.isArray(data?.changes)) history = data.changes;
    remoteWarnings = Array.isArray(data?.warnings) ? data.warnings.map(String) : [];
    if (typeof data?.can_manage === 'boolean') canManage = data.can_manage;
    if (typeof data?.manageable === 'boolean') canManage = data.manageable;
    if (Number.isInteger(data?.access_model)) accessModel = data.access_model;
    renderWarnings();
    renderHistory();
    renderControls();
  }

  async function refresh(repaint = false) {
    const data = await scope.read(async () => actorState(await readList()));
    if (!data || !current()) return;
    const dirty = fingerprint(serverGrants) !== fingerprint(state.grants) || !!preview;
    applyList(data, repaint && !dirty);
    if (!repaint && dirty) status('На сервере есть новое состояние. Введённые полномочия и операция сохранены; форма не перезагружалась.');
    else status('Состояние полномочий обновлено.');
  }

  function definitive(error: ApiError) {
    return typeof error.status === 'number' && error.status >= 400 && error.status < 500;
  }

  function preserveConflict(error: ApiError) {
    preview = null;
    previewFingerprint = '';
    delete state.preview_sha256;
    delete state.preview_grants;
    delete state.preview_base_revision;
    delete state.diff;
    const currentRevision = Number(error.data?.base_revision ?? error.data?.revision ?? error.data?.policy_revision ??
      error.data?.details?.base_revision ?? error.data?.details?.revision ?? error.data?.details?.policy_revision);
    if (Number.isInteger(currentRevision) && currentRevision >= 0) state.base_revision = currentRevision;
  }

  async function runPreview() {
    readForm();
    const submittedEdit = editRevision;
    const grants = validateRoleGrants(state.grants);
    state.grants = grants;
    const intent = { operation_id: state.operation_id };
    const result: ActionResult<any> = await runner.run({
      target: `grant-preview:${actorUid}`,
      intent,
      validate: () => { validateRoleGrants(state.grants); },
      persist: persist,
      submit: async () => {
        try {
          const value = await api('POST', 'grant-admin', {
            operation: 'preview', actor_uid: actorUid, base_revision: state.base_revision,
            grants: validateRoleGrants(state.grants), operation_id: state.operation_id,
          });
          return { phase: 'accepted' as const, value };
        } catch (error) {
          if (definitive(error as ApiError)) return { phase: 'error' as const, error };
          throw error;
        }
      },
    });
    if (!current()) return;
    if (submittedEdit !== editRevision) {
      status('Ввод изменился во время предпросмотра. Новые значения сохранены; повторите проверку.');
      renderControls();
      return;
    }
    if (result.phase === 'accepted') {
      uncertain = false;
      preview = result.value?.preview || result.value;
      previewFingerprint = fingerprint(state.grants);
      state.preview_sha256 = preview.preview_sha256;
      state.preview_grants = normalizeGrants(preview.grants || preview.normalized_grants || state.grants);
      state.preview_base_revision = revisionOf(preview) || state.base_revision;
      state.diff = preview.diff;
      await persist();
      renderDiff(preview.diff, preview.preview_sha256);
      renderWarnings();
      status('Предпросмотр готов. Проверьте diff перед применением.');
    } else {
      const error = ('error' in result ? result.error : new Error('Операция полномочий не подтверждена.')) as ApiError;
      if ((error as any)?.status === 409) {
        preserveConflict(error);
        await persist();
        status(`${error.message} Введённые полномочия и operation_id сохранены.`, 'error');
      } else {
        if (result.phase === 'unknown') {
          uncertain = true;
          runner = createRunner();
        }
        status(result.phase === 'unknown' ? `${error.message} Исход неизвестен; операция сохранена для повторной проверки.` : error.message, 'error');
      }
    }
    renderControls();
  }

  async function runApply() {
    if (!preview?.preview_sha256 || previewFingerprint !== fingerprint(state.grants)) return;
    const submittedEdit = editRevision;
    const operationId = state.operation_id;
    const result: ActionResult<any> = await runner.run({
      target: `grant-apply:${actorUid}`,
      intent: { operation_id: operationId },
      validate: () => { validateRoleGrants(state.grants); },
      persist: persist,
      submit: async () => {
        try {
          const value = await api('POST', 'grant-admin', {
            operation: 'apply', actor_uid: actorUid,
            base_revision: state.preview_base_revision ?? state.base_revision,
            grants: normalizeGrants(state.preview_grants || state.grants),
            diff: preview.diff, preview_sha256: preview.preview_sha256,
            operation_id: operationId,
          });
          return { phase: 'accepted' as const, value };
        } catch (error) {
          if (definitive(error as ApiError)) return { phase: 'error' as const, error };
          throw error;
        }
      },
    });
    if (!current()) return;
    if (result.phase === 'accepted') {
      uncertain = false;
      const receipt = result.value?.receipt || result.value;
      const appliedGrants = normalizeGrants(receipt.result_grants || receipt.grants || receipt.normalized_grants || state.preview_grants || state.grants);
      const appliedHistory = Array.isArray(receipt.diff) ? receipt.diff.map((change: any) => ({
        ...change, action: ({ create: 'grant_created', update: 'grant_updated', revoke: 'grant_revoked' } as Record<string, string>)[change.action] || change.action,
        changed_at: receipt.applied_at, revision: receipt.policy_revision,
      })) : [];
      history = receipt.history || [...appliedHistory, ...history];
      remoteWarnings = receipt.warnings || remoteWarnings;
      if (submittedEdit !== editRevision) {
        serverGrants = appliedGrants;
        dialogManager.forceClose();
        await refresh(false);
        status('Прежняя операция применена. Более новый ввод сохранён и требует отдельного предпросмотра.');
        await options.onApplied?.();
        renderControls();
        return;
      }
      await save(null);
      serverGrants = appliedGrants;
      state.grants = normalizeGrants(appliedGrants);
      state.operation_id = crypto.randomUUID();
      preview = null;
      previewFingerprint = '';
      delete state.preview_sha256;
      delete state.preview_grants;
      delete state.preview_base_revision;
      delete state.diff;
      dialogManager.forceClose();
      await refresh(true);
      status(receipt.repeated || receipt.idempotent ? 'Операция уже была применена; получена прежняя квитанция.' : 'Полномочия применены. Квитанция получена.');
      await options.onApplied?.();
    } else {
      const error = ('error' in result ? result.error : new Error('Операция полномочий не подтверждена.')) as ApiError;
      if ((error as any)?.status === 409) {
        preserveConflict(error);
        await persist();
        dialogManager.forceClose();
        status(`${error.message} Ввод сохранён. Повторите предпросмотр; operation_id не потерян.`, 'error');
      } else {
        if (result.phase === 'unknown') {
          uncertain = true;
          runner = createRunner();
        }
        $('[data-access-confirm-status]').textContent = result.phase === 'unknown'
          ? `${error.message} Исход неизвестен. Повторите эту же операцию, чтобы получить квитанцию.`
          : error.message;
        status('Введённые полномочия и операция сохранены.', 'error');
      }
    }
    renderControls();
  }

  $('[data-access-form]').addEventListener('submit', event => {
    event.preventDefault();
    void runPreview().catch(error => status(error.message, 'error'));
  });
  $('[data-access-form]').addEventListener('change', event => {
    const target = event.target as HTMLElement;
    if (target.matches('[data-access-default]')) return;
    if (target.matches('[data-access-role], [data-access-territory], [data-access-employee]')) changed(false);
  });
  $('[data-access-add]').addEventListener('click', () => {
    if (busy || !roles.length) return;
    const role = roles[0];
    state.grants.push({ role: role.id, territory: territories[0]?.id || 'self', capabilities: [...role.capabilities], employees: [], all_contracts: false, read_all_contracts: false, is_default: false });
    changed(true);
  });
  $('[data-access-apply]').addEventListener('click', () => {
    $('[data-access-confirm-status]').textContent = '';
    dialogManager.show();
  });
  $('[data-access-confirm-apply]').addEventListener('click', () => {
    void runApply().catch(error => status(error.message, 'error'));
  });
  $('[data-access-refresh]').addEventListener('click', () => {
    void refresh(true).catch(error => status(error.message, 'error'));
  });
  const stop = crm.onChange?.((event: any) => {
    if (event.type === 'live' && event.dirty) void refresh(false).catch(error => status(error.message, 'error'));
  });

  try {
    const [catalogResult, listResult, draftResult] = await Promise.all([
      api('GET', 'grant-catalog'), scope.read(async () => actorState(await readList())), crm.draft(`role-editor:${actorUid}`),
    ]);
    if (!listResult || !current()) return { refresh, dispose };
    catalog = catalogResult?.catalog || catalogResult || {};
    roles = editableRoles(catalog);
    territories = territoryDefinitions(catalog, options.owner === true);
    serverGrants = normalizeGrants(listResult.grants || listResult.normalized_grants);
    canManage = listResult.can_manage !== false && listResult.manageable !== false && catalog.can_manage !== false;
    accessModel = Number.isInteger(listResult.access_model) ? listResult.access_model : Number(catalog.access_model || 1);
    state = { actor_uid: actorUid, base_revision: revisionOf(listResult), grants: normalizeGrants(serverGrants), operation_id: crypto.randomUUID() };
    history = listResult.history || listResult.changes || [];
    remoteWarnings = Array.isArray(listResult.warnings) ? listResult.warnings.map(String) : [];
    const saved = draftResult?.data as EditorDraft | null;
    if (saved && saved.actor_uid === actorUid && Array.isArray(saved.grants) && saved.operation_id) {
      state = { ...saved, grants: normalizeGrants(saved.grants) };
      if (saved.preview_sha256) {
        preview = { preview_sha256: saved.preview_sha256, grants: saved.preview_grants, diff: saved.diff, warnings: [] };
        previewFingerprint = fingerprint(state.grants);
      }
      remoteWarnings.push('Восстановлен сохранённый черновик; серверный список не заменял введённые полномочия.');
    }
    renderCards();
    renderDiff(preview?.diff || diffRoleGrants(serverGrants, state.grants), preview?.preview_sha256 || '');
    renderWarnings();
    renderHistory();
    renderControls();
    status(saved ? 'Черновик восстановлен. Проверьте diff и исход операции.' : 'Полномочия загружены.');
  } catch (error) {
    if (current()) status((error as Error).message, 'error');
    dispose();
    throw error;
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    stop?.();
    scope.dispose();
    dialogManager.dispose();
  }
  return { refresh, dispose };
}
