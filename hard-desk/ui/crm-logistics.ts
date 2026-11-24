type LogisticsAPI = {
  api(method: string, action: string, body?: unknown, query?: unknown): Promise<any>;
  workbook(action: string, input?: unknown): Promise<any>;
  draft(key: string, value?: unknown, expectedOwner?: string): Promise<any>;
};

type CaseRow = {
  contract_uuid: string; revision: number; partner: string; external_id: string; client: string;
  active: number; courier: string | null; courier_label: string; courier_uuid: string | null;
  caller: string | null; transferred_on: string; phone: string; available_phone: string;
  call_status: string; call_comment: string; delivery: {delivery_result?: string};
};

const labels: Record<string, string> = {UZ: 'Узбекистан', TJ: 'Таджикистан'};
const poolStatus = (row: CaseRow) => !row.active ? 'Завершён' : (row.courier || row.courier_label) ? 'В работе у курьеров' : 'Не назначено';
const esc = (v: unknown) => String(v ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]!));
const people = (staff: Record<string, string>, selected?: string | null) =>
  Object.entries(staff).sort((a,b) => a[1].localeCompare(b[1], 'ru')).map(([id,name]) =>
    `<option value="${esc(id)}"${id === selected ? ' selected' : ''}>${esc(name)}</option>`).join('');

export function mountLogistics(host: HTMLElement, api: LogisticsAPI, countries: string[]) {
  if (!countries.length) {
    host.textContent = 'Для этого профиля не настроен курьерский пул.';
    return {refresh: async () => {}, dispose: () => {}};
  }
  const today = new Date().toLocaleDateString('en-CA', {timeZone: 'Asia/Tashkent'});
  host.innerHTML = `<div class="toolbar">
    <label>Страна <select data-country>${countries.map(c => `<option value="${esc(c)}">${esc(labels[c])}</option>`).join('')}</select></label>
    <button type="button" data-import-open>Загрузить Excel</button>
    <button type="button" data-export>Выгрузить XLSX</button></div>
    <details data-import id="logistics-import"><summary>Загрузка и сверка Excel</summary><div class="toolbar">
    <label>Дата передачи с <input type="date" data-since value="${esc(today.slice(0,7))}-01"></label>
    <label>по <input type="date" data-until value="${esc(today)}"></label>
    <button type="button" data-load class="primary">Выбрать Excel</button></div><div data-preview></div></details>
    <form data-filter class="toolbar"><input type="search" data-search aria-label="Поиск" placeholder="Договор, клиент, телефон">
    <select data-phones aria-label="Наличие телефона"><option value="all">Все телефоны</option><option value="with">С телефоном</option><option value="without">Без телефона</option></select>
    <select data-caller aria-label="Софт по курьерам"><option value="">Все исполнители</option></select>
    <select data-courier aria-label="Курьер"><option value="">Все курьеры</option></select>
    <select data-state aria-label="Статус пула"><option value="active">В работе</option><option value="closed">Завершённые</option><option value="all">Все пулы</option></select>
    <button type="submit">Найти</button></form>
    <div class="toolbar"><span data-chosen>Выбрано 0</span>
    <select data-target aria-label="Назначить на обзвон"><option value="">Снять назначение</option></select>
    <button type="button" data-assign disabled>Применить назначение</button><button type="button" data-close-case disabled>Завершить</button></div>
    <p role="status" data-status></p><div data-table></div><dialog data-edit></dialog>`;
  const el = <T extends HTMLElement = HTMLElement>(key: string) => host.querySelector<T>(`[data-${key}]`)!;
  const value = (key: string) => el<HTMLInputElement | HTMLSelectElement>(key).value;
  const status = (message: string) => { el('status').textContent = message; };
  const selected = new Map<string, number>();
  let offset = 0, generation = 0, disposed = false, busy = false, previewGeneration = 0;
  let lastMutation = '', lastOperation = '';
  let latestStaff: {call:Record<string,string>; delivery:Record<string,string>} = {call:{}, delivery:{}};

  const filters = () => ({country:value('country'), search:value('search'), caller:value('caller'),
    courier:value('courier'), phones:value('phones'), state:value('state')});
  const selectionState = () => {
    el('chosen').textContent = `Выбрано ${selected.size}`;
    for (const action of ['assign','close-case']) el<HTMLButtonElement>(action).disabled = busy || !selected.size;
  };
  const replaceOptions = (key: string, initial: string, entries: Record<string, string>) => {
    const select = el<HTMLSelectElement>(key), current = select.value;
    const options = initial + people(entries, current);
    if (select.dataset.options === options) return;
    select.innerHTML = options;
    if ([...select.options].some(option => option.value === current)) select.value = current;
    select.dataset.options = options;
  };
  const refresh = async () => {
    if (disposed || busy || el<HTMLDialogElement>('edit').open) return;
    const version = ++generation;
    const response = await api.api('GET','logistics',undefined,{...filters(),offset,limit:100});
    if (disposed || version !== generation) return;
    if (!response.ok) { status(response.text || 'Не удалось обновить пул.'); return; }
    const {rows,total,has_more,staff} = response.data;
    latestStaff = staff;
    if (!rows.length && offset > 0) { offset = Math.max(0, offset-100); await refresh(); return; }
    replaceOptions('target','<option value="">Снять назначение</option>',staff.call);
    replaceOptions('caller','<option value="">Все исполнители</option><option value="unassigned">Без назначения</option>',staff.call);
    replaceOptions('courier','<option value="">Все курьеры</option>',staff.delivery);
    el('table').innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th><input type="checkbox" aria-label="Выбрать страницу" data-page></th>
      <th>КД / партнёр</th><th>Клиент</th><th>Курьер</th><th>Дата передачи</th><th>Телефон</th>
      <th>Доставка</th><th>Софт по курьерам</th><th>Дозвон</th><th></th></tr></thead><tbody>
      ${(rows as CaseRow[]).map(row => `<tr>
        <td><input type="checkbox" aria-label="Выбрать договор ${esc(row.external_id)}" data-case="${esc(row.contract_uuid)}"${selected.has(row.contract_uuid) ? ' checked' : ''}${row.active ? '' : ' disabled'}></td>
        <td>${esc(row.external_id)}<br><small>${esc(row.partner)}</small></td><td>${esc(row.client)}</td>
        <td>${esc(row.courier || row.courier_label || '—')}</td><td>${esc(row.transferred_on)}</td><td>${esc(row.available_phone || '—')}</td>
        <td>${esc(row.delivery.delivery_result || poolStatus(row))}</td>
        <td>${esc(row.caller || '—')}</td><td>${esc(row.call_status || '—')}${row.call_comment ? `<div class="queue-comment">${esc(row.call_comment)}</div>` : ''}</td>
        <td>${row.active ? `<button type="button" data-row-edit="${esc(row.contract_uuid)}">Изменить</button>` : ''}</td></tr>`).join('')}
      </tbody></table></div>${rows.length ? '' : '<p>По выбранным условиям договоров нет</p>'}
      <div class="toolbar"><button data-prev ${offset ? '' : 'disabled'}>Назад</button>
      <span>${rows.length ? offset+1 : 0}–${offset+rows.length} из ${total}</span><button data-next ${has_more ? '' : 'disabled'}>Далее</button></div>`;
    const rowMap = new Map((rows as CaseRow[]).map(row => [row.contract_uuid,row]));
    const checks = [...el('table').querySelectorAll<HTMLInputElement>('[data-case]:not(:disabled)')];
    const page = el<HTMLInputElement>('page');
    const check = (input: HTMLInputElement) => {
      const id = input.dataset.case!;
      if (!input.checked) selected.delete(id);
      else if (selected.size < 300 || selected.has(id)) selected.set(id,rowMap.get(id)!.revision);
      else { input.checked = false; status('Для одной операции выберите не больше 300 договоров.'); }
      page.checked = !!checks.length && checks.every(input => input.checked);
      selectionState();
    };
    page.checked = !!checks.length && checks.every(input => input.checked);
    page.onchange = () => {
      const checked = page.checked;
      checks.forEach(input => { input.checked = checked; check(input); });
    };
    checks.forEach(input => input.onchange = () => check(input));
    el('table').querySelectorAll<HTMLButtonElement>('[data-row-edit]').forEach(button => {
      button.onclick = () => edit(rowMap.get(button.dataset.rowEdit!)!);
    });
    el('prev').onclick = () => { offset -= 100; void refresh(); };
    el('next').onclick = () => { offset += 100; void refresh(); };
    selectionState();
  };
  async function mutate(action: string, body: Record<string, unknown>) {
    const key = JSON.stringify([action,body]);
    if (key !== lastMutation) { lastMutation = key; lastOperation = crypto.randomUUID(); }
    const r = await api.api('POST',action,{...body,operation_id:lastOperation});
    if (!r.ok) throw new Error(r.text || 'Операция не подтверждена. Повторите с теми же данными.');
    lastMutation = '';
    return r.data;
  }
  async function perform(work: () => Promise<void>, localError?: HTMLElement) {
    if (busy || disposed) return;
    busy = true;
    selectionState();
    try { await work(); }
    catch (error) { if (localError) localError.textContent = (error as Error).message; else status((error as Error).message); }
    finally { busy = false; selectionState(); await refresh(); }
  }
  function edit(row: CaseRow) {
    const dialog = el<HTMLDialogElement>('edit');
    dialog.innerHTML = `<form><h2>Договор ${esc(row.external_id)}</h2><div class="grid">
      <label class="field">Телефон<input name="phone" value="${esc(row.available_phone)}" inputmode="tel" maxlength="64"></label>
      <label class="field">Курьер<select name="courier"><option value="">Без назначения</option>${people(latestStaff.delivery,row.courier_uuid)}</select></label></div>
      <p role="alert"></p><div class="actions"><button type="button">Закрыть</button><button class="primary">Сохранить</button></div></form>`;
    dialog.querySelector<HTMLButtonElement>('button[type=button]')!.onclick = () => dialog.close();
    dialog.querySelector('form')!.onsubmit = event => {
      event.preventDefault();
      const data = new FormData(dialog.querySelector('form')!);
      const phone = data.get('phone'), courier = data.get('courier') || null;
      void perform(async () => {
        const values: Record<string, unknown> = {};
        if (phone !== row.available_phone) values.phone = phone;
        if (courier !== row.courier_uuid) values.courier_uuid = courier;
        if (Object.keys(values).length) await mutate('logistics-update',{country:value('country'),
          selection:[{contract_uuid:row.contract_uuid,revision:row.revision}],values});
        selected.delete(row.contract_uuid);
        dialog.close();
      },dialog.querySelector<HTMLElement>('[role=alert]')!);
    };
    dialog.showModal();
  }
  const updateChosen = (values: Record<string, unknown>) => perform(async () => {
    const result = await mutate('logistics-update',{country:value('country'),
      selection:[...selected].map(([contract_uuid,revision]) => ({contract_uuid,revision})),values});
    selected.clear();
    status(`Обновлено договоров: ${result.changes.length}`);
  });
  el('assign').onclick = () => void updateChosen({caller_uuid:value('target') || null});
  el('close-case').onclick = () => {
    if (window.confirm(`Завершить работу по ${selected.size} выбранным договорам?`)) void updateChosen({close:true});
  };
  el<HTMLFormElement>('filter').onsubmit = event => { event.preventDefault(); offset = 0; void refresh(); };
  el<HTMLSelectElement>('country').onchange = () => {
    selected.clear(); offset = 0; previewGeneration++; el('preview').replaceChildren();
    el<HTMLSelectElement>('caller').value = ''; el<HTMLSelectElement>('courier').value = '';
    selectionState(); void refresh();
  };
  el('import-open').onclick = () => {
    const details = el<HTMLDetailsElement>('import');
    details.open = true;
    el('load').focus();
  };
  el('load').onclick = () => void perform(async () => {
    const selectedCountry = value('country'), version = ++previewGeneration;
    const input = await api.workbook('logistics-read');
    if (input.cancelled || disposed || version !== previewGeneration) return;
    if (!input.ok) throw new Error(input.text || 'Не удалось прочитать реестр.');
    const source = {country:selectedCountry, since:value('since'), until:value('until'),
      workbook_sha256:input.data.workbook_sha256, rows:input.data.rows};
    const r = await api.api('POST','logistics-preview',source);
    if (disposed || version !== previewGeneration) return;
    if (!r.ok) throw new Error(r.text || 'Не удалось сверить строки.');
    const {counts,proof,rows} = r.data;
    const items = new Map<number, {number:string;partner:string}>(source.rows.map((row:any) => [row.line,row]));
    el('preview').innerHTML = `<p>${esc(input.data.filename)} · ${esc(input.data.sheet)} · Готово ${counts.ready || 0} · Уже в пуле ${counts.existing || 0} · На сверку ${counts.review || 0} · Вне периода ${counts.outside || 0}</p>
      <div class="logistics-preview-list">${rows.filter((row:any) => row.status !== 'outside' && row.reason)
      .map((row:any) => `<div>${row.line} · ${esc(items.get(row.line)?.number)} · ${esc(items.get(row.line)?.partner)} · ${esc(row.reason)}</div>`).join('')}</div>
      <button type="button" class="primary" data-apply ${counts.ready ? '' : 'disabled'}>Добавить подтверждённые</button>`;
    el('apply').onclick = () => void perform(async () => {
      if (disposed || version !== previewGeneration || source.country !== value('country')) return;
      if (source.since !== value('since') || source.until !== value('until')) throw new Error('Период изменился. Повторите предпросмотр.');
      const result = await mutate('logistics-apply',{source,proof});
      el('preview').replaceChildren();
      offset = 0;
      status(`В курьерский пул добавлено договоров: ${result.imported}`);
    });
  });
  el('export').onclick = () => void perform(async () => {
    const result = await api.workbook('logistics-export-download',filters());
    if (result.cancelled) return;
    if (!result.ok) throw new Error(result.text || 'Выгрузка не подтверждена.');
    status(`Выгружено договоров: ${result.data.rows}`);
  });
  return {refresh, dispose: () => { disposed = true; generation++; previewGeneration++; }};
}

export async function mountLogisticsCall(dialog: HTMLDialogElement, row: any, api: LogisticsAPI,
  owner: string, statuses: string[], saved: () => Promise<void>) {
  const call = row.logistics_call;
  const key = 'logistics-call:' + row.contract_uuid;
  const draft = (await api.draft(key,undefined,owner)).data;
  const select = statuses.map(status => `<option ${draft?.body?.status === status ? 'selected' : ''}>${esc(status)}</option>`).join('');
  dialog.innerHTML = `<form><h2>Курьерский обзвон</h2><p>${esc(row.external_id)} · ${esc(row.client_name)} · <a href="tel:${esc(call.phone.replace(/[^+\d]/g,''))}">${esc(call.phone)}</a></p>
    <div class="grid"><label class="field">Статус дозвона<select name="status" required><option value="">Выберите результат</option>${select}</select></label>
    <label class="field span">Комментарий<textarea name="comment" maxlength="4000">${esc(draft?.body?.comment)}</textarea></label></div>
    <p role="alert"></p><div class="actions"><button type="button">Закрыть</button><button class="primary">Сохранить</button></div></form>`;
  const form = dialog.querySelector<HTMLFormElement>('form')!;
  const error = form.querySelector<HTMLElement>('[role=alert]')!;
  const send = form.querySelectorAll('button')[1];
  const snapshot = () => {
    const fields = new FormData(form);
    return {contract_uuid:row.contract_uuid, revision:call.revision,
      status:String(fields.get('status') || ''), comment:String(fields.get('comment') || '')};
  };
  let pending = draft;
  form.oninput = () => {
    const body = snapshot();
    pending = {body};
    void api.draft(key,pending,owner).catch(e => { error.textContent = e.message; });
  };
  form.querySelector('button[type=button]')!.addEventListener('click',()=>dialog.close());
  form.onsubmit = async event => {
    event.preventDefault(); send.disabled = true;
    const body = snapshot();
    const attempted = pending?.body;
    const retry = attempted?.operation_id && attempted.status === body.status && attempted.comment === body.comment;
    const result = retry ? attempted : {...body,operation_id:crypto.randomUUID()};
    try {
      pending = {body:result};
      await api.draft(key,pending,owner);
      const response = await api.api('POST','logistics-call',result);
      if (!response.ok) throw Error(response.text || 'Ввод сохранён. Повторите отправку после восстановления связи.');
      await api.draft(key,null,owner);
      dialog.close(); await saved();
    } catch (e) { error.textContent = (e as Error).message; }
    finally { send.disabled = false; }
  };
  dialog.showModal();
}
