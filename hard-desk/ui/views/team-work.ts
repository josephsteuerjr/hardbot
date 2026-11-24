import { api } from '../kit/api';
import { ask, esc, toast } from '../kit/dom';
import { go, setHead, type Section } from '../kit/shell';
import { adm } from '../analyst/lib';

type Kind = 'manager' | 'analyst' | 'employee';
const status: Record<string, string> = {open: 'В работе', submitted: 'Ждёт подтверждения', confirmed: 'Подтверждена'};
const loginStatus: Record<string, string> = {none: 'Профиль не создан', active: 'Доступ открыт', awaiting: 'Ждёт подтверждения', disabled: 'Доступ отключён', blocked: 'Доступ заблокирован', ambiguous: 'Нужно уточнить профиль'};
const deviceStatus: Record<string, string> = {none: 'нет входа', pending: 'ждёт подтверждения', active: 'подтверждён'};
const eventTitles: Record<string, string> = {create: 'Назначена', note: 'Заметка', step: 'Добавлен этап', toggle_step: 'Этап', submit: 'Результат сдан', confirm: 'Результат подтверждён', return: 'Возвращена в работу'};
const dateText = (value: string) => value ? new Date(value).toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'}) : '—';

function transport(kind: Kind, resource: 'board' | 'tasks') {
  const tail = resource === 'board' ? 'employee_board' : 'staff_tasks';
  const path = kind === 'analyst' ? `/admin/${tail}.json` : kind === 'manager' ? `/api/manager/${tail}` : `/api/${tail}`;
  return async (body?: any, id?: number): Promise<any> => {
    if (kind === 'analyst') return adm(body ? 'POST' : 'GET', path + (id ? '?id=' + id : ''), body);
    const r = await api(path, id ? {id} : undefined, body ? 'POST' : 'GET', body);
    if (!r.ok || r.stale) throw new Error(r.stale ? 'Нет связи. Для работы с доской и задачами подключитесь к серверу.' : r.text || 'Не удалось загрузить данные.');
    return r.data;
  };
}

/** Keep an uncertain write's UUID across redraws within the same window session. */
async function write(send: (body: any) => Promise<any>, key: string, body: any) {
  const storageKey = 'hard.team-work.pending.' + key;
  const encoded = JSON.stringify(body);
  let pending: any;
  try { pending = JSON.parse(sessionStorage.getItem(storageKey) || 'null'); } catch { /* */ }
  if (!pending || pending.encoded !== encoded) pending = {encoded, operation_id: crypto.randomUUID()};
  sessionStorage.setItem(storageKey, JSON.stringify(pending));
  await send({...body, operation_id: pending.operation_id});
  sessionStorage.removeItem(storageKey);
}

function action(root: HTMLElement, selector: string, fn: (button: HTMLButtonElement) => Promise<void>) {
  root.querySelectorAll<HTMLButtonElement>(selector).forEach(button => button.addEventListener('click', async () => {
    if (button.disabled) return;
    button.disabled = true;
    try { await fn(button); } catch (error) { toast((error as Error).message); }
    finally { if (button.isConnected) button.disabled = false; }
  }));
}

function removalChoice(employee: any): Promise<'hide' | 'remove' | null> {
  return new Promise(resolve => {
    const box = document.createElement('div');
    box.id = 'ask';
    box.innerHTML = `<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="remove-choice-title">
      <h3 id="remove-choice-title">Удалить также профиль?</h3>
      <p>${esc(employee.employee)}${employee.username ? ' · ' + esc(employee.username) : ''}</p>
      <p>Можно убрать сотрудника только с доски: аккаунт и входы сохранятся.
      При удалении профиля входы на ПК и телефоне будут закрыты. История работы, задачи и договоры сохранятся.</p>
      ${employee.can_delete_profile === false ? '<p>Профиль требует уточнения. Доступно только удаление с доски.</p>' : ''}
      <div class="row" style="flex-wrap:wrap"><button class="btn" data-cancel>Отмена</button>
      <button class="btn primary" data-keep-profile>Убрать без удаления</button>
      <button class="btn danger" data-delete-profile ${employee.can_delete_profile === false ? 'disabled' : ''}>Убрать и удалить профиль</button></div></div>`;
    const previousFocus = document.activeElement as HTMLElement | null;
    const done = (choice: 'hide' | 'remove' | null) => {
      box.remove(); document.removeEventListener('keydown', onKey);
      if (previousFocus?.isConnected) previousFocus.focus();
      resolve(choice);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') done(null);
      if (event.key === 'Tab') {
        const buttons = [...box.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        event.preventDefault(); buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
      }
    };
    box.querySelector('[data-cancel]')!.addEventListener('click', () => done(null));
    box.querySelector('[data-keep-profile]')!.addEventListener('click', () => done('hide'));
    box.querySelector('[data-delete-profile]')!.addEventListener('click', () => done('remove'));
    box.addEventListener('click', event => { if (event.target === box) done(null); });
    document.addEventListener('keydown', onKey);
    document.body.append(box);
    box.querySelector<HTMLButtonElement>('[data-keep-profile]')!.focus();
  });
}

export function employeeBoard(kind: 'manager' | 'analyst'): Section {
  const send = transport(kind, 'board');
  let query = '';
  const render = async (root: HTMLElement) => {
    setHead('Сотрудники');
    try {
      const d = await send();
      if (!root.isConnected) return;
      const employees: any[] = d.employees || [];
      const onBoard = (x: any) => x.on_board ?? !!x.active;
      const active = employees.filter(onBoard);
      const candidates = employees.filter(x => !onBoard(x));
      root.innerHTML = `<div class="toolbar"><input class="input" type="search" data-board-search aria-label="Найти сотрудника или логин" placeholder="Сотрудник или логин" value="${esc(query)}"><span class="spacer"></span><button class="btn small" data-reload>Обновить</button></div>
        ${d.can_edit ? `<form class="toolbar team-add" data-add-form><label>Добавить из реестра <select class="input" data-add-person required aria-label="Сотрудник из реестра"><option value="">Выберите сотрудника</option>${candidates.map((x, i) => `<option value="${i}">${esc(x.employee)}${x.office ? ' · ' + esc(x.office) : ''}</option>`).join('')}</select></label><button class="btn small" type="submit" ${candidates.length ? '' : 'disabled'}>+ Добавить</button></form>` : ''}
        <div class="employee-board" data-board></div>`;
      const draw = () => {
        const shown = active.filter(x => `${x.employee} ${x.username} ${x.office}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
        const groups = [['tj', 'Таджикистан'], ['uz', 'Узбекистан'], ...shown.some(x => !['tj', 'uz'].includes(x.direction)) ? [['', 'Республика не указана']] : []];
        const host = root.querySelector<HTMLElement>('[data-board]')!;
        host.innerHTML = groups.map(([code, title]) => {
          const rows = shown.filter(x => code ? x.direction === code : !['tj', 'uz'].includes(x.direction));
          return `<section class="employee-country ${code ? '' : 'unmapped'}"><div class="section-head"><h2>${title}</h2><span class="muted">${rows.length}</span></div>${rows.map(x => {
            const index = active.indexOf(x);
            const profile = kind === 'analyst' && x.username
              ? `<button type="button" class="link" data-board-profile="${index}">${esc(x.employee)}</button>`
              : `<b class="grow">${esc(x.employee)}</b>`;
            return `<article class="employee-tile"><div class="row">${profile}${kind === 'manager' && d.can_edit ? `<button class="btn small" data-assign="${index}">+ Задача</button>` : ''}</div>
              ${x.office ? `<div class="sub">${esc(x.office)} · ${esc(x.products.map((p: string) => p === 'soft' ? 'Софт' : 'Хард').join(' / '))}</div>` : `<div class="sub">${esc(x.products.map((p: string) => p === 'soft' ? 'Софт' : 'Хард').join(' / '))}</div>`}
              <div class="employee-login"><span class="mono">${esc(x.username || '—')}</span> · ${esc(x.password_review_pending ? 'Ждёт подтверждения смены пароля' : loginStatus[x.login_status] || x.login_status)}${x.must_change_password ? ' · требуется смена пароля' : ''}<br>ПК: ${esc(deviceStatus[x.desktop])} · Телефон: ${esc(deviceStatus[x.phone])}${x.last_seen ? `<br>Последняя связь: ${esc(dateText(x.last_seen))}` : ''}</div>
              ${x.can_remove ? `<button class="btn small employee-remove" data-remove-person="${index}">Убрать</button>` : ''}</article>`;
          }).join('') || '<p class="muted">Сотрудников нет</p>'}</section>`;
        }).join('');
        action(host, '[data-board-profile]', async b => { await go('accounts', {username: active[Number(b.dataset.boardProfile)].username}); });
        action(host, '[data-assign]', async b => { await go('staff-tasks', {employee: active[Number(b.dataset.assign)].employee, create: true}); });
        action(host, '[data-remove-person]', async b => {
          const x = active[Number(b.dataset.removePerson)];
          const choice = await removalChoice(x);
          if (!choice) return;
          await write(send, `${kind}.${choice}.${x.employee}`, choice === 'hide'
            ? {action: 'hide', employee: x.employee}
            : {action: 'remove', employee: x.employee, account_id: x.account_id, confirm: 'remove_and_delete_profile'});
          await render(root);
        });
      };
      draw();
      root.querySelector<HTMLInputElement>('[data-board-search]')!.addEventListener('input', e => { query = (e.target as HTMLInputElement).value; draw(); });
      action(root, '[data-reload]', async () => render(root));
      root.querySelector<HTMLFormElement>('[data-add-form]')?.addEventListener('submit', async e => {
        e.preventDefault();
        const select = root.querySelector<HTMLSelectElement>('[data-add-person]')!;
        const button = root.querySelector<HTMLButtonElement>('[data-add-form] button')!;
        if (!select.value || button.disabled) return;
        button.disabled = true;
        const x = candidates[Number(select.value)];
        try { await write(send, `${kind}.add.${x.employee}`, {action: 'add', employee: x.employee}); await render(root); }
        catch (error) { toast((error as Error).message); button.disabled = false; }
      });
    } catch (error) { root.innerHTML = `<div class="notice err">${esc((error as Error).message)}</div><button class="btn" data-retry>Повторить</button>`; action(root, '[data-retry]', async () => render(root)); }
  };
  return {id: 'employee-board', title: 'Доска сотрудников', icon: 'people', render};
}

export function staffTasks(kind: Kind): Section {
  const send = transport(kind, 'tasks');
  let selected = 0, filter = 'active', employee = '';
  const render = async (root: HTMLElement, params: Record<string, any> = {}) => {
    if (params.employee) employee = params.employee;
    if (params.id) selected = Number(params.id);
    setHead('Задачи');
    try {
      if (params.create) { await create(root); return; }
      if (selected) { await detail(root); return; }
      const d = await send();
      if (!root.isConnected) return;
      const rows = (d.tasks || []).filter((x: any) => (!employee || x.employee === employee) &&
        (filter === 'all' || (filter === 'active' ? x.status !== 'confirmed' : filter === 'overdue' ? x.overdue : x.status === filter)));
      root.innerHTML = `<div class="toolbar"><select class="input" data-task-filter aria-label="Статус задач">${[['active', 'Незавершённые'], ['open', 'В работе'], ['submitted', 'Ждут подтверждения'], ['overdue', 'Просроченные'], ['confirmed', 'Подтверждённые'], ['all', 'Все задачи']].map(([v, label]) => `<option value="${v}" ${filter === v ? 'selected' : ''}>${label}</option>`).join('')}</select>${employee ? `<button class="btn small" data-clear-employee>${esc(employee)} ×</button>` : ''}<span class="spacer"></span><button class="btn small" data-reload>Обновить</button>${d.can_create ? '<button class="btn primary small" data-create>+ Задача</button>' : ''}</div>
        <div class="task-list">${rows.map((x: any) => `<button class="task-row" data-task="${x.id}"><span class="row"><b class="grow">${esc(x.title)}</b><span class="tag ${x.overdue ? 'warn' : x.status === 'confirmed' ? 'ok' : ''}">${esc(status[x.status])}${x.overdue ? ' · просрочена' : ''}</span></span><span class="sub">${esc(x.creator)} → ${esc(x.employee)}</span><span class="sub">Срок: ${esc(dateText(x.due_at))} · Этапы: ${x.steps.filter((s: any) => s.done).length}/${x.steps.length}</span></button>`).join('') || '<p class="muted">Задач нет</p>'}</div>`;
      action(root, '[data-task]', async b => { selected = Number(b.dataset.task); await detail(root); });
      action(root, '[data-create]', async () => create(root));
      action(root, '[data-clear-employee]', async () => { employee = ''; await render(root); });
      action(root, '[data-reload]', async () => render(root));
      root.querySelector<HTMLSelectElement>('[data-task-filter]')!.addEventListener('change', e => { filter = (e.target as HTMLSelectElement).value; void render(root); });
    } catch (error) { root.innerHTML = `<div class="notice err">${esc((error as Error).message)}</div><button class="btn" data-retry>Повторить</button>`; action(root, '[data-retry]', async () => render(root)); }
  };
  const back = (root: HTMLElement) => action(root, '[data-back]', async () => { selected = 0; await render(root); });
  const create = async (root: HTMLElement) => {
    const d = await transport(kind, 'board')();
    if (!root.isConnected) return;
    const rows = (d.employees || []).filter((x: any) => x.active);
    root.innerHTML = `<button class="btn small" data-back>← Задачи</button><form class="task-form" data-new-task>
      <label>Кому<select class="input" name="employee" required><option value="">Выберите сотрудника</option>${rows.map((x: any) => `<option ${employee === x.employee ? 'selected' : ''} value="${esc(x.employee)}">${esc(x.employee)}</option>`).join('')}</select></label>
      <label>Задача<input class="input" name="title" maxlength="240" required></label>
      <label>Описание<textarea class="input" name="description" maxlength="8000" rows="4"></textarea></label>
      <label>Срок · время этого компьютера<input class="input" name="due_at" type="datetime-local" required></label><button class="btn primary" type="submit">Назначить</button><p class="err" data-error role="status"></p></form>`;
    back(root);
    const form = root.querySelector<HTMLFormElement>('[data-new-task]')!;
    form.addEventListener('submit', async e => {
      e.preventDefault(); const button = form.querySelector<HTMLButtonElement>('button[type=submit]')!;
      if (button.disabled) return; button.disabled = true;
      try {
        const values = Object.fromEntries(new FormData(form));
        await write(send, kind + '.create-task', {action: 'create', ...values, due_at: new Date(String(values.due_at)).toISOString()});
        employee = ''; selected = 0; filter = 'active'; await render(root);
      } catch (error) { form.querySelector<HTMLElement>('[data-error]')!.textContent = (error as Error).message; button.disabled = false; }
    });
  };
  const detail = async (root: HTMLElement) => {
    const d = await send(undefined, selected), x = d.task;
    if (!root.isConnected) return;
    root.innerHTML = `<div class="toolbar"><button class="btn small" data-back>← Задачи</button><span class="spacer"></span><button class="btn small" data-reload>Обновить</button></div>
      <article class="task-detail"><div class="section-head"><h2>${esc(x.title)}</h2><span class="tag ${x.overdue ? 'warn' : ''}">${esc(status[x.status])}</span></div>
      <p>${esc(x.creator)} → ${esc(x.employee)}</p><p class="${x.overdue ? 'err' : 'muted'}">Срок: ${esc(dateText(x.due_at))}${x.overdue ? ' · просрочена' : ''}</p><p class="task-text">${esc(x.description)}</p>
      <h3>Этапы</h3><div class="task-steps">${x.steps.map((s: any) => `<label class="task-step"><input type="checkbox" data-step="${s.id}" ${s.done ? 'checked' : ''} ${x.can_contribute && x.status === 'open' ? '' : 'disabled'}><span>${esc(s.text)}<span class="sub">${esc(s.author)}</span></span></label>`).join('') || '<p class="muted">Этапов нет</p>'}</div>
      ${x.can_contribute ? `<div class="task-compose"><label>Заметка, этап или результат<textarea class="input" data-task-text maxlength="4000" rows="3"></textarea></label><div class="row"><button class="btn small" data-task-action="note">+ Заметка</button>${x.status === 'open' ? '<button class="btn small" data-task-action="step">+ Этап</button>' : ''}${x.can_work && x.status === 'open' ? '<button class="btn primary small" data-task-action="submit">Сдать результат</button>' : ''}${x.can_manage && x.status === 'submitted' ? '<button class="btn primary small" data-task-action="confirm">Подтвердить результат</button><button class="btn small" data-task-action="return">Вернуть в работу</button>' : ''}</div><p class="err" data-error role="status"></p></div>` : ''}
      <h3>История</h3><div class="task-events">${x.events.map((e: any) => `<div class="task-event"><div class="sub">${esc(dateText(e.at))} · ${esc(e.author)} · ${esc(eventTitles[e.kind] || e.kind)}</div><div class="task-text">${esc(e.text)}</div></div>`).join('')}</div></article>`;
    back(root); action(root, '[data-reload]', async () => detail(root));
    action(root, '[data-task-action]', async b => {
      const text = root.querySelector<HTMLTextAreaElement>('[data-task-text]')!.value.trim();
      const actionName = b.dataset.taskAction!;
      if (actionName !== 'confirm' && !text) { toast('Укажите текст.'); return; }
      if (actionName === 'confirm' && !await ask('Подтвердить результат?', `${x.title}\n${x.employee}\nЗадача будет завершена.`, 'Подтвердить')) return;
      try { await write(send, `${kind}.task.${x.id}`, {id: x.id, revision: x.revision, action: actionName, text}); await detail(root); }
      catch (error) { root.querySelector<HTMLElement>('[data-error]')!.textContent = (error as Error).message; }
    });
    root.querySelectorAll<HTMLInputElement>('[data-step]').forEach(input => input.addEventListener('change', async () => {
      input.disabled = true;
      try { await write(send, `${kind}.task.${x.id}`, {id: x.id, revision: x.revision, action: 'toggle_step', step_id: Number(input.dataset.step), done: input.checked}); await detail(root); }
      catch (error) { input.checked = !input.checked; input.disabled = false; toast((error as Error).message); }
    }));
  };
  return {id: 'staff-tasks', title: 'Задачи', icon: 'promises', render};
}
