import {mountDeliveryForm} from './crm-delivery';
import {mountRegistry} from './crm-registry';
import {mountManagedPortfolio} from './crm-manager-portfolio';
type API = {
  ready?():Promise<any>;
  state(): Promise<any>;
  api(method: string, action: string, body?: any, query?: any): Promise<any>;
  command(body: any): Promise<any>;
  registryEdit(body:any, expectedOwner?:string): Promise<any>;
  diagnostics?(repair?:boolean):Promise<any>;
  diagnosticsReport?():Promise<any>;
  queue(action?: string, id?: string,expectedOwner?:string): Promise<any>;
  onChange?(callback: (event: {type: 'queue' | 'identity' | 'live';dirty?:boolean;as_of?:string;connected?:boolean}) => void): () => void;
  draft(key: string, value?: any, expectedOwner?:string): Promise<any>;
  telemetry(event: any): Promise<any>;
  connect(input: any): Promise<any>;
  workbook(action: string, input?: any): Promise<any>;
  document(contract: string, id?: string): Promise<any>;
  win(action: string): void;
};
const crm = (window as any).crm as API;
const $ = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.getElementById(id) as T;
const esc = (value: any) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
const date = (value: any) =>
  value ? new Date(value).toLocaleString("ru-RU") : "—";
const amount = (value: any, currency = "") =>
  value == null || value === ""
    ? "Не указано"
    : esc(value) + (currency ? " " + esc(currency) : "");
const labels: Record<string, string> = {
  call: "Звонок",
  source_sync: 'Сверка прежнего приложения',
  registry_edit: 'Правка реестра',
  visit: "Выезд",
  delivery: 'Передать конверт',
  note: "Заметка",
  contact: "Контакты",
  promise: "Обещание",
  task: "Задача",
  reported_payment: "Заявленная оплата",
  payment_confirmed: "Подтверждённое поступление",
  document: "Документ",
  correction: "Исправление",
  manual_edit: "Ручная правка Excel",
  task_complete: "Завершение задачи",
  script_publish: "Публикация скрипта",
};
const historyWhen=(event:any)=>event.time_quality==='legacy_day'?calendarDate(event.payload.legacy_workday):date(event.time_quality==='legacy_unknown'?event.payload.legacy_created_at:event.received_at);
let state: any,
  context: any,
  section = "portfolio",
  card: any,
  tab = "overview",
  scriptOpened: any,
  cardScript: any = null,
  renderGeneration = 0;
let portfolioView='mine', portfolioWork='all', portfolioEmployee='', portfolioSearch='', portfolioOffset=0;
let disposeSection:(()=>void)|undefined;
let portfolioIdentity='',portfolioGeneration=0;
const sectionScroll=new Map<string,number>();
let taskStatus='open',taskEmployee='all',taskWork='all',taskSearch='',taskOffset=0,taskGeneration=0;
let cardOrigin='portfolio',taskSelectClient=false,newTaskAssignee='';
let activePerson='',lastCardAcknowledgements='',cardSyncGeneration=0,formOpening=false,cardRefreshPending=false;
let cardRefreshError='';
let syncTimer:ReturnType<typeof setTimeout> | undefined,cardRetryTimer:ReturnType<typeof setTimeout> | undefined,syncForced=false;
const notice = (text = "", tone: 'error' | 'status' = 'error') => {
  $("notice").textContent = text;
  $('notice').dataset.tone=tone;
};

function cardEntries(entries:any[]) {
  const ids=new Set(card?.contracts?.map((row:any)=>row.contract_uuid) || []);
  return entries.filter(entry=>Array.isArray(entry.body?.contract_ids) && entry.body.contract_ids.some((id:string)=>ids.has(id)));
}
async function queueEntries() {
  const result=await crm.queue();
  if(!result.ok)throw new Error(result.text || 'Не удалось прочитать состояние отправки.');
  return result.data?.entries || [];
}
function paintCardSaves(entries:any[]) {
  const host=$('card-saves');if(!host)return;
  const expanded=new Set(Array.from(host.querySelectorAll<HTMLElement>('[data-operation]'))
    .filter(el=>el.querySelector('details[open]')).map(el=>el.dataset.operation));
  const othersOpen=!!host.querySelector('[data-other-saves][open]');
  const rows=cardEntries(entries).slice(-5).reverse();
  const markup=(entry:any)=>{
    const review=entry.state==='attention' || entry.result?.projection_status==='needs_review';
    const status=entry.state==='pending'?'Сохранено на компьютере · ожидает отправки':entry.state==='attention'?'Требует сверки':entry.result?.projection_status==='needs_review'?'История принята · текущие поля требуют сверки':'Принято сервером';
    const payload=entry.body.payload || {};
    const saved=payload.text || payload.title || payload.delivery_comment || Object.entries(payload.values || {}).map(([key,value])=>
      (context.fields?.find((field:any)=>field.key===key)?.title || key)+': '+(value ?? 'Очищено')).join('\n');
    return `<div class="card-save ${review?'needs-review':''}" data-operation="${esc(entry.body.operation_id)}" data-save-state="${entry.state}"><span>${esc(labels[entry.body.kind] || entry.body.kind)}</span><strong>${status}</strong>${entry.error?`<p>${esc(entry.error)}</p>`:''}${saved?`<details><summary>Сохранённый результат</summary><p>${esc(saved)}</p></details>`:''}${entry.result?.projection_status==='needs_review'?'<button class="link" data-save-history>Открыть историю</button>':''}</div>`;
  };
  const needsReview=rows.slice(1).filter(e=>e.state==='attention' || e.result?.projection_status==='needs_review').length;
  host.innerHTML=rows.length?`<h2>Отправка результатов</h2>${markup(rows[0])}${rows.length>1?`<details data-other-saves${othersOpen?' open':''}><summary>Предыдущие отправки: ${rows.length-1}${needsReview?' · требуют сверки: '+needsReview:''}</summary>${rows.slice(1).map(markup).join('')}</details>`:''}<button class="link" id="card-open-queue">Все отправки</button>`:'';
  if(rows[0]?.state==='accepted' && $('notice').textContent==='Действие сохранено на компьютере.')notice();
  bind('card-open-queue',()=>go('queue'));
  host.querySelectorAll<HTMLButtonElement>('[data-save-history]').forEach(button=>button.onclick=()=>{tab='history';void renderCard();});
  host.querySelectorAll<HTMLElement>('[data-operation]').forEach(el=>{
    if(expanded.has(el.dataset.operation))el.querySelector('details')?.setAttribute('open','');
  });
}
function scheduleSync(force=false) {
  syncForced ||= force;
  clearTimeout(syncTimer);
  syncTimer=setTimeout(()=>{const refresh=syncForced;syncForced=false;void syncCurrentView(refresh).catch(error=>{
    if(error.name!=='AbortError')notice(error.message);
  });},100);
}
function retryCard(person:string) {
  cardRefreshPending=true;clearTimeout(cardRetryTimer);
  cardRetryTimer=setTimeout(()=>{if(activePerson===person)scheduleSync(true);},7000);
}
async function syncCurrentView(force=false) {
  if(!context)return;
  if(activePerson){
    if(!card)return;
    const person=activePerson,generation=++cardSyncGeneration;
    const entries=await queueEntries();
    if(person!==activePerson || generation!==cardSyncGeneration)return;
    paintCardSaves(entries);
    const acknowledgements=JSON.stringify(cardEntries(entries).filter(e=>e.state==='accepted')
      .map(e=>[e.body.operation_id,e.result?.event_id,e.result?.projection_status]));
    const refresh=force || cardRefreshPending || acknowledgements!==lastCardAcknowledgements;
    if(!refresh)return;
    const result=await crm.api('GET','card',undefined,{person_uuid:person});
    if(person!==activePerson || generation!==cardSyncGeneration)return;
    if(!result.ok){
      if(result.status===403){
        card.contracts.forEach((r:any)=>{r.allowed_actions=[];});
        const form=$('action-form');
        if(form){form.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled=true;$('form-error').textContent='Назначение изменилось. Ввод сохранён; отправка недоступна.';}
        cardRefreshPending=true;notice('Изменился доступ к клиенту. Сохранённый ввод остаётся у исходной личности.');return;
      }
      retryCard(person);cardRefreshError=result.text || 'Не удалось обновить карточку.';throw new Error(cardRefreshError);
    }
    if(formOpening || $<HTMLDialogElement>('dialog').open){
      cardRefreshPending=true;
      const latest=new Map(result.data.contracts.map((r:any)=>[r.contract_uuid,r.allowed_actions]));
      card.contracts.forEach((r:any)=>{r.allowed_actions=latest.get(r.contract_uuid)||[];});
      const form=$('action-form');
      if(form){
        const kind=form.dataset.work;
        const inputs=[...form.querySelectorAll<HTMLInputElement>('input[name="contracts"]:checked')];
        if(kind&&inputs.some(input=>!contractCan(card.contracts.find((r:any)=>r.contract_uuid===input.value)||{allowed_actions:[]},kind))){
          form.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled=true;
          $('form-error').textContent='Назначение изменилось. Ввод сохранён у вас; отправка этой работы недоступна.';
        }
      }
      return;
    }
    const expanded=Array.from(document.querySelectorAll<HTMLElement>('[data-contract-details]'))
      .filter(el=>el.querySelector('details[open]')).map(el=>el.dataset.contractDetails);
    const openSaves=Array.from(document.querySelectorAll<HTMLElement>('[data-operation]'))
      .filter(el=>el.querySelector('details[open]')).map(el=>el.dataset.operation);
    const othersOpen=!!document.querySelector('[data-other-saves][open]');
    const main=document.querySelector('main')!;const scroll=main.scrollTop;
    clearTimeout(cardRetryTimer);card=result.data;cardRefreshPending=!!result.stale;
    if(result.stale){$('connection').textContent='Сохранённая копия · '+date(result.stale);retryCard(person);}
    else {
      lastCardAcknowledgements=acknowledgements;
      if(cardRefreshError && $('notice').textContent===cardRefreshError)notice();
      cardRefreshError='';
      $('connection').textContent=(state.connection.mode==='synthetic'?'Синтетические данные':state.connection.server)+(state.offline?' · офлайн':'');
    }
    await renderCard();
    for(const host of document.querySelectorAll<HTMLElement>('[data-contract-details]'))
      if(expanded.includes(host.dataset.contractDetails))host.querySelector('details')?.setAttribute('open','');
    for(const host of document.querySelectorAll<HTMLElement>('[data-operation]'))
      if(openSaves.includes(host.dataset.operation))host.querySelector('details')?.setAttribute('open','');
    if(othersOpen)document.querySelector('[data-other-saves]')?.setAttribute('open','');
    main.scrollTop=scroll;
  }else if(section==='tasks' && !$<HTMLDialogElement>('dialog').open){await tasksPage();}
  else if(section==='queue'){await queuePage();}
}
const telemetry = (event_type: string, feature: string, extra: any = {}) => {
  void crm.telemetry({ event_type, feature, ...extra }).catch(() => {});
};
async function request(
  method: string,
  action: string,
  body?: any,
  query?: any,
) {
  const generation = renderGeneration;
  const result = await crm.api(method, action, body, query);
  if (generation !== renderGeneration)
    throw new DOMException("Экран изменился.", "AbortError");
  if (!result.ok)
    throw new Error(
      result.text || result.data?.text || "Не удалось выполнить запрос.",
    );
  if (result.stale)
    $("connection").textContent = "Сохранённая копия · " + date(result.stale);
  return result.data;
}
function can(cap: string) {
  return context?.owner || context?.capabilities?.includes(cap);
}
function contractCan(contract: any, kind: string) {
  return !Array.isArray(contract.allowed_actions) || contract.allowed_actions.includes(kind);
}
function repaintWatermark(data: any) {
  let host = document.querySelector('.card-script-watermark');
  if (!host && data?.script) {
    const workspace = document.querySelector('.card-workspace');
    if (workspace) {
      workspace.classList.add('with-script');
      workspace.insertAdjacentHTML('beforeend', '<aside class="card-script-watermark" aria-label="Скрипт разговора"></aside>');
      host = workspace.querySelector('.card-script-watermark');
    }
  }
  if (host && data?.script) host.innerHTML = `<h2>${esc(data.script.title)}</h2>${data.script.steps.map((step: any) => `<section><h3>${esc(step.title)}</h3><p>${esc(step.text)}</p></section>`).join('')}<small>Версия ${esc(data.published_revision)}</small>`;
}
function button(id: string, title: string, primary = false) {
  return `<button id="${id}"${primary ? ' class="primary"' : ""}>${esc(title)}</button>`;
}
function bind(id: string, fn: () => any) {
  $(id)?.addEventListener("click", () => {
    notice();
    Promise.resolve()
      .then(fn)
      .catch((e) => {if(e.name!=='AbortError')notice(e.message);});
  });
}
const table = (heads: string[], rows: string[]) =>
  `<div class="table-wrap"><table><thead><tr>${heads.map((x) => `<th>${esc(x)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
async function boot() {
  await crm.ready?.();
  state = await crm.state();
  context = state.context;
  if (portfolioIdentity!==context?.employee_uuid) {
    sectionScroll.clear();
    taskSelectClient=false;newTaskAssignee='';cardOrigin='portfolio';
    portfolioIdentity=context?.employee_uuid || '';
    portfolioView=['manager','analyst','observer'].includes(context?.role)?'all':'mine';
    portfolioEmployee=context?.employee_uuid || '';portfolioWork='all';portfolioSearch='';portfolioOffset=0;
    taskStatus='open';taskEmployee=['manager','analyst','observer','owner'].includes(context?.role)?'all':context?.employee_uuid;
    taskWork='all';taskSearch='';taskOffset=0;
    const savedTasks=(await crm.draft('task-view')).data;
    if(savedTasks){
      if(['open','today','overdue','done','all'].includes(savedTasks.status))taskStatus=savedTasks.status;
      if(['all','call','visit','delivery'].includes(savedTasks.work))taskWork=savedTasks.work;
      if(savedTasks.employee==='all' && ['manager','analyst','observer','owner'].includes(context?.role)
          || (context?.staff || []).some((p:any)=>p.employee_uuid===savedTasks.employee))taskEmployee=savedTasks.employee;
    }
    const saved=(await crm.draft('portfolio-view')).data;
    if(saved && ['mine','all'].includes(saved.view)) {
      portfolioView=saved.view;
      portfolioWork=['all','call','visit','both','delivery'].includes(saved.work)?saved.work:'all';
      if((context?.staff || []).some((p:any)=>p.employee_uuid===saved.employee) || saved.employee===context?.employee_uuid)portfolioEmployee=saved.employee;
    }
  }
  $("identity").textContent = context ? ({manager:'Руководитель',observer:'Наблюдатель',courier:'Курьер',analyst:'Аналитик'} as any)[context.role] || 'Сотрудник' : 'Вход в CRM';
  $("version").textContent = state.version;
  $("connection").textContent =
    (state.connection.mode === "synthetic"
      ? "Синтетические данные"
      : state.connection.mode === 'unconfigured' ? 'Не подключено' : state.connection.server) + (state.offline ? " · офлайн" : "");
  const manager=['manager','owner'].includes(context?.role);
  const sections = context?.role==='employee' ? [
    ['portfolio','Портфель'],['registry','Реестр'],['cabinet','Мои показатели'],
    ['active-time','Активное время'],['connection','Подключение'],
  ] : manager ? [
    ['overview','Обзор'],['portfolio','Портфель'],['manager-statistics','Показатели'],['team','Команда'],
    ['script','Скрипт'],['connection','Подключение'],
  ] : [
    ["portfolio", "Портфель"],
    ['tasks','Задачи'],
    ['cabinet','Мои показатели'],
    ["shift", "Смена"],
    ["script", "Скрипт"],
    ["queue", "Очередь"],
    ...(can("exchange") ? [["exchange", "Реестры"]] : []),
    ...(can("analytics") ? [["adoption", "Внедрение"]] : []),
    ["connection", "Подключение"],
    ["help", "Справка"],
  ];
  $("navigation").innerHTML = sections.filter(([id])=>context || ['connection','help'].includes(id))
    .map(
      ([id, label]) =>
        `<button data-section="${id}" class="${section === id ? "active" : ""}">${label}</button>`,
    )
    .join("");
  document
    .querySelectorAll<HTMLButtonElement>("[data-section]")
    .forEach((b) => (b.onclick = () => void go(b.dataset.section!)));
  await go(context ? section : "connection");
  telemetry("client_ready", "shell", {
    duration_ms: Math.round(performance.now()),
  });
}
async function go(next: string) {
  disposeSection?.();disposeSection=undefined;
  const generation=++renderGeneration;
  if(!activePerson)sectionScroll.set(section,document.querySelector('main')!.scrollTop);
  clearTimeout(cardRetryTimer);
  activePerson='';cardSyncGeneration++;lastCardAcknowledgements='';cardRefreshPending=false;
  taskSelectClient=false;newTaskAssignee='';
  section = next;
  notice();
  $("title").textContent = (
    {
      portfolio: "Портфель",
      overview:'Обзор',
      'manager-statistics':'Показатели команды',
      team:'Команда',
      registry:'Реестр',
      'active-time':'Активное время',
      tasks:'Задачи',
      shift: "Смена",
      cabinet:'Показатели сотрудника',
      script: "Скрипт звонка",
      queue: "Очередь",
      exchange: "Реестры",
      adoption: "Внедрение",
      connection: "Подключение",
      help: "Справка",
    } as any
  )[next];
  document
    .querySelectorAll("[data-section]")
    .forEach((b) =>
      b.classList.toggle("active", (b as HTMLElement).dataset.section === next),
    );
  telemetry(
    "feature_opened",
    (
      {
        queue: "shell",
        connection: "shell",
        help: "shell",
        shift: "shift",
        script: "script",
        exchange: "exchange",
        adoption: "adoption",
        portfolio: "portfolio",
        registry:'portfolio',
        'active-time':'shell',
        tasks:'task',
        cabinet:'portfolio',
      } as any
    )[next],
  );
  try {
    await (
      {
        portfolio,
        overview:managerOverview,
        'manager-statistics':managerStatistics,
        team:managerTeam,
        registry:async()=>{
          const expectedOwner=state.owner;
          const stop=await mountRegistry($('view'),{...crm,draft:(key:string,value?:any)=>crm.draft(key,value,expectedOwner),registryEdit:(body:any)=>crm.registryEdit(body,expectedOwner),queue:(action?:string,id?:string)=>crm.queue(action,id,expectedOwner)},()=>generation===renderGeneration);
          if(generation===renderGeneration)disposeSection=stop;else stop();
        },
        'active-time':activeTimePage,
        tasks:tasksPage,
        cabinet,
        shift,
        script,
        queue: queuePage,
        exchange: exchangePage,
        adoption,
        connection,
        help,
      } as any
    )[next]();
  } catch (e) {
    if ((e as Error).name !== "AbortError") notice((e as Error).message);
  }
  if(generation===renderGeneration && !activePerson)document.querySelector('main')!.scrollTop=sectionScroll.get(next) || 0;
}

const debtKeys = ['debt_overdue', 'interest_overdue', 'penalty', 'duty', 'dtp', 'rent',
  'camera_fine', 'dirt', 'trash', 'new_damage', 'evacuation', 'peni', 'other'];
function calendarDate(value: any) {
  const day = String(value || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day)
    ? esc(new Date(day + 'T00:00:00').toLocaleDateString('ru-RU')) : esc(value || 'Не указано');
}
function deliveryMarkup(fields:any){
  return fields.delivery_result?`<div class="delivery-fact"><dt>Передача конверта</dt><dd data-field="delivery-result">${esc(fields.delivery_result)}${fields.delivery_address?'<br>'+esc(fields.delivery_address):''}<br>${esc(fields.delivery_phone || '-')}<br>${esc(fields.delivery_comment)}<br><span class="muted">Принято ${date(fields.delivery_received_at)} · ${esc(fields.delivery_author)}</span></dd></div>`:'';
}
function contractDetails(contract: any) {
  const fields = contract.fields;
  const title = (key: string) => context.fields?.find((field: any) => field.key === key)?.title || key;
  const articles = debtKeys.filter(key => fields[key] !== null && fields[key] !== undefined && fields[key] !== '');
  const people = new Map((contract.assignments || []).map((row: any) => [row.employee_uuid, row.name]));
  const responsibility = people.size ? Array.from(people.values()).join(', ')
    : Array.from(new Set([fields.responsible, fields.responsible_soft].filter(Boolean))).join(', ');
  return `<article class="contract-details" data-contract-details="${esc(contract.contract_uuid)}"><h3>${esc(contract.external_id)} · ${esc(contract.creditor_id)}</h3><dl class="contract-facts"><div><dt>Дата КД</dt><dd data-field="kd_date">${calendarDate(fields.kd_date)}</dd></div><div><dt>Выданная сумма</dt><dd data-field="issued_sum">${amount(fields.issued_sum, contract.currency || 'Валюта не задана')}</dd></div><div><dt>Дней просрочки</dt><dd data-field="dp">${esc(fields.dp ?? 'Не указано')}</dd></div><div><dt>${people.size ? 'Ответственные' : 'Ответственные из реестра'}</dt><dd data-field="responsibility">${esc(responsibility || 'Не назначены')}</dd></div><div><dt>Последняя оплата</dt><dd>${amount(fields.last_payment_sum, contract.currency || '')}${fields.last_payment_date ? ' · ' + calendarDate(fields.last_payment_date) : ''}</dd></div>${deliveryMarkup(fields)}</dl><details><summary>Структура задолженности</summary>${articles.length ? `<dl class="debt-articles">${articles.map(key => `<div><dt>${esc(title(key))}</dt><dd data-field="${esc(key)}">${amount(fields[key], contract.currency || '')}</dd></div>`).join('')}</dl>` : '<p class="muted">Разбивка в источнике не указана</p>'}</details></article>`;
}

function fullBusiness(contract:any) {
  const groups=new Map<string,any[]>();
  const available=new Set(contract.business_source?.present || Object.keys(contract.fields));
  for(const f of context.fields || []){
    const section=f.section || ({call:'Звонки',visit:'Выезды',contact:'Контакты',forecast:'Обещания',delivery:'Передача конверта'} as any)[f.group] || 'Реквизиты и учёт';
    if(!groups.has(section))groups.set(section,[]);
    const value=contract.fields[f.key];
    const text=!available.has(f.key)?'Нет в источнике':value===null || value===undefined || value===''?'Пусто':f.type==='date'?calendarDate(value):esc(value);
    groups.get(section)!.push(`<div data-business-key="${esc(f.key)}"><dt>${esc(f.title)}</dt><dd>${text}</dd></div>`);
  }
  const extras=(contract.business_source?.extras || []).map((f:any)=>({...f,value:f.value===null?null:String(f.value)+(f.currency?' '+f.currency:'')}));
  return `<details class="business-details"><summary>Все сведения · ${esc(contract.external_id)} · ${esc(contract.fields.partner || contract.creditor_id)}${contract.currency?' · '+esc(contract.currency):''}</summary>${Array.from(groups).map(([title,rows])=>`<section><h3>${esc(title)}</h3><dl class="contract-facts">${rows.join('')}</dl></section>`).join('')}${extras.length?`<section><h3>Прочие сведения источника</h3><dl class="contract-facts">${extras.map((f:any)=>`<div><dt>${esc(f.title)}</dt><dd>${f.value===null || f.value===''?'Пусто':esc(f.value)}</dd></div>`).join('')}</dl></section>`:''}</details>`;
}

async function activeTimePage() {
  const activity=(await crm.state()).activity;
  const today=new Date().toLocaleDateString('en-CA',{timeZone:context.timezone || 'Europe/Samara'});
  $('view').innerHTML=`<div class="toolbar"><label>С <input id="active-since" type="date" value="${today}"></label><label>По <input id="active-until" type="date" value="${today}"></label>${button('active-load','Показать')}${button('active-shift','Заявленная смена')}${button('active-queue','Отправка интервалов')}</div><p>Считается время в видимом окне CRM в фокусе. После 120 секунд без взаимодействия начинается простой. Блокировка, сон и работа в другом окне не учитываются. Чтение и телефонный разговор без ввода могут попасть в простой.</p><div id="active-result"></div>`;
  if(activity?.clock_quality==='unconfirmed')$('view').insertAdjacentHTML('afterbegin','<p role="status">Нет подтверждённой точки отсчёта. Новые интервалы пока не учитываются. Подключитесь к API; сохранённые интервалы остаются в очереди.</p>');
  const duration=(ms:number)=>`${Math.floor(ms/3600000)} ч ${Math.floor(ms/60000)%60} мин ${Math.floor(ms/1000)%60} с`;
  const load=async()=>{
    const data=await request('GET','active-time',undefined,{since:$<HTMLInputElement>('active-since').value,until:$<HTMLInputElement>('active-until').value});
    $('active-result').innerHTML=`<div class="summary"><div class="metric">Активное время<strong>${duration(data.active_ms)}</strong></div><div class="metric">Простой в окне CRM<strong>${duration(data.idle_ms)}</strong></div></div><p>Подтверждено сервером · ${esc(data.timezone)} · ${date(data.as_of)}. Пересекающиеся интервалы нескольких устройств учитываются один раз.</p>`+table(['День','Активно','Простой'],data.days.map((d:any)=>`<tr><td>${calendarDate(d.day)}</td><td>${duration(d.active_ms)}</td><td>${duration(d.idle_ms)}</td></tr>`));
  };
  bind('active-load',load);bind('active-shift',()=>go('shift'));bind('active-queue',()=>go('queue'));await load();
}

async function managerOverview(){
  if(!context.distribution?.enabled){
    $('view').innerHTML=`<p role="status">${esc(context.distribution?.reason||'Управляемый портфель не подключён.')}</p><div class="actions">${button('manager-existing-stats','Показатели сотрудника')}${button('manager-team','Команда')}</div>`;
    bind('manager-existing-stats',()=>go('cabinet'));bind('manager-team',()=>go('team'));return;
  }
  const data=await request('GET','manager-statistics');
  $('view').innerHTML=`<div class="summary"><button class="metric" id="manager-free">Не назначены<strong>${data.unassigned_contracts}</strong><small>${data.unassigned_slots} рабочих мест</small></button><button class="metric" id="manager-portfolio">Клиенты<strong>${data.people}</strong><small>${data.contracts} договоров</small></button><div class="metric">Требует сверки<strong>${data.ambiguous_contracts}</strong></div></div><h2>Нагрузка сотрудников</h2>`+
    table(['Сотрудник','Клиенты','Договоры','Рабочие места','Звонки за месяц','Выезды за месяц'],data.staff.map((p:any)=>`<tr><td><button class="link" data-manager-employee="${esc(p.employee_uuid)}">${esc(p.name)}</button>${p.has_account?'':'<br><small>Без аккаунта</small>'}</td><td>${p.people}</td><td>${p.contracts}</td><td>${p.slots}</td><td>${p.facts.call}</td><td>${p.facts.visit}</td></tr>`))+
    `<p class="muted">Срез на ${date(data.as_of)}. Итоги сотрудников могут пересекаться по видам работ; долг команды считается по уникальным договорам.</p>`;
  bind('manager-free',async()=>{const saved=(await crm.draft('managed-portfolio-view')).data||{};await crm.draft('managed-portfolio-view',{...saved,unassigned:'1',offset:0});await go('portfolio');});
  bind('manager-portfolio',()=>go('portfolio'));wireManagerEmployees();
}
function wireManagerEmployees(){
  $('view').querySelectorAll<HTMLButtonElement>('[data-manager-employee]').forEach(b=>b.onclick=async()=>{
    const saved=(await crm.draft('managed-portfolio-view')).data||{};
    await crm.draft('managed-portfolio-view',{...saved,employee_uuid:b.dataset.managerEmployee,unassigned:'0',offset:0});await go('portfolio');
  });
}
async function managerStatistics(){
  if(!context.distribution?.enabled){await cabinet();return;}
  const saved=(await crm.draft('manager-statistics-view')).data||{};
  $('view').innerHTML=`<div class="toolbar"><label>Месяц <input type="month" id="manager-month" value="${esc(saved.month||new Date().toISOString().slice(0,7))}"></label>${button('manager-stats-load','Показать')}</div><div id="manager-statistics-result"></div>`;
  const load=async()=>{
    const month=$<HTMLInputElement>('manager-month').value;await crm.draft('manager-statistics-view',{month});
    const data=await request('GET','manager-statistics',undefined,{month});
    $('manager-statistics-result').innerHTML=`<p class="muted">Срез на ${date(data.as_of)} · ${data.people} клиентов · ${data.contracts} договоров</p><h2>Долг текущего портфеля</h2>${table(['Валюта','Сумма'],Object.entries(data.debt).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`))}<p>Без подтверждённой суммы или валюты: ${data.unknown_debt}</p><h2>Факты работы по исходным авторам</h2><p class="muted">${esc(data.facts_attribution)}</p>${table(['Сотрудник','Звонки','Выезды','Обработано КД','КД сейчас'],data.staff.map((p:any)=>`<tr><td><button class="link" data-manager-employee="${esc(p.employee_uuid)}">${esc(p.name)}</button></td><td>${p.facts.call}</td><td>${p.facts.visit}</td><td>${p.facts.processed_contracts}</td><td>${p.contracts}</td></tr>`))}<h2>Поступления по реестру</h2><p>${esc(data.payments.attribution)}</p>${table(['Валюта','Сумма'],Object.entries(data.payments.totals).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`))}<p>С месячной суммой: ${data.payments.known_contracts}; без подтверждённой суммы: ${data.payments.missing_contracts}. Источник на ${data.payments.source_as_of.map(date).join(', ')||'—'}.</p><div class="actions">${button('manager-source-rows','Договоры текущего портфеля')}${button('manager-personal-report','Показатели и операции сотрудника')}</div>`;
    wireManagerEmployees();bind('manager-source-rows',()=>go('portfolio'));bind('manager-personal-report',()=>go('cabinet'));
  };
  bind('manager-stats-load',load);await load();
}
async function managerTeam(){
  const people=context.distribution?.enabled?(await request('GET','assignment-staff')).staff:context.staff||[];
  $('view').innerHTML=`<h2>Кадровый состав CRM</h2>${table(['Сотрудник','Команда','Работы','Портфель','Вход'],people.map((p:any)=>`<tr><td><button class="link" data-manager-employee="${esc(p.employee_uuid)}">${esc(p.name)}</button><br><small>${esc(p.employee_uuid)}</small></td><td>${esc(p.team_id||context.team_id)}</td><td>${(p.work_kinds||[]).map((w:string)=>esc(labels[w]||w)).join(', ')}</td><td>${p.contracts??'—'} КД</td><td>${p.has_account===false?'Без аккаунта':p.has_account?'Соответствие входа подтверждено':'Состояние входа проверяется отдельно'}</td></tr>`))}<div class="actions">${button('manager-access-load','Аккаунты и устройства')}${button('manager-tasks','Задачи')}${button('manager-queue','Очередь')}</div><div id="manager-access-result"></div>`;
  wireManagerEmployees();bind('manager-tasks',()=>go('tasks'));bind('manager-queue',()=>go('queue'));
  const loadAccess=async()=>{
    const results=await Promise.all([crm.api('GET','manager-access',undefined,{resource:'roster'}),crm.api('GET','manager-access',undefined,{resource:'entered'})]);
    const [roster,entered]=results;
    if(!roster.ok||!entered.ok){$('manager-access-result').textContent=roster.text||entered.text||'Служба входа недоступна.';return;}
    const rows=entered.data.entered||[],staff=roster.data.employees||[];
    $('manager-access-result').innerHTML=`<h2>Аккаунты и устройства</h2>${table(['Сотрудник / логин','Состояние','ПК / телефон','Действия'],rows.map((p:any,i:number)=>`<tr><td>${esc(p.employee)}<br>${esc(p.username||'Профиль не создан')}</td><td>${esc(p.password_review_pending?'Пароль ждёт подтверждения':p.must_change_password?'Требуется смена пароля':p.status||'Проверен службой входа')}</td><td>${esc(p.desktop?.status||p.desktop?.state||'—')} / ${esc(p.phone?.status||p.phone?.state||'—')}</td><td>${p.can_approve?`<button data-team-approve="${i}">Подтвердить вход</button>`:''}${p.can_reset_password?`<button data-team-reset="${i}">Временный пароль</button>`:''}</td></tr>`))}<h2>Полный состав службы доступа</h2>${table(['Сотрудник','Офис','Аккаунт','ПК','Телефон'],staff.map((p:any)=>`<tr><td>${esc(p.employee)}</td><td>${esc(p.office||'Не задан')}</td><td>${esc(p.username||'Без аккаунта')}</td><td>${esc(p.desktop||'Нет устройства')}</td><td>${esc(p.phone||'Нет устройства')}</td></tr>`))}<p class="muted">Кадровое соответствие CRM и аккаунт службы доступа показаны отдельно. Неоднозначные личности не объединяются по имени.</p>`;
    $('manager-access-result').querySelectorAll<HTMLButtonElement>('[data-team-approve]').forEach(b=>b.onclick=async()=>{
      b.disabled=true;const p=rows[Number(b.dataset.teamApprove)];
      const result=await crm.api('POST','manager-access',{resource:'approve',payload:{username:p.username}});
      if(result.ok)await loadAccess();else{notice(result.text);b.disabled=false;}
    });
    $('manager-access-result').querySelectorAll<HTMLButtonElement>('[data-team-reset]').forEach(b=>b.onclick=()=>{
      const p=rows[Number(b.dataset.teamReset)],dialog=$<HTMLDialogElement>('dialog'),op=crypto.randomUUID();
      dialog.innerHTML=`<form id="team-password-form"><h2>Временный пароль</h2><p>${esc(p.employee)} · ${esc(p.username)}</p><label>Новый пароль <input id="team-password" type="password" autocomplete="new-password" required minlength="10"></label><p id="team-password-status" role="status"></p><div class="actions"><button type="button" id="team-password-close">Закрыть</button><button type="submit" class="primary">Сменить пароль</button></div></form>`;
      $('team-password-close').onclick=()=>dialog.close();
      $('team-password-form').onsubmit=async event=>{event.preventDefault();const result=await crm.api('POST','manager-access',{resource:'reset',payload:{username:p.username,password:$<HTMLInputElement>('team-password').value,operation_id:op}});if(result.ok){dialog.close();await loadAccess();}else $('team-password-status').textContent=result.text||'Результат не подтверждён. Повтор использует тот же ID.';};dialog.showModal();
    });
  };
  bind('manager-access-load',loadAccess);
}

async function portfolio(search = portfolioSearch, offset=portfolioOffset) {
  if(context.distribution?.enabled&&['manager','owner'].includes(context.role)){
    const generation=renderGeneration,expectedOwner=state.owner;
    const stop=await mountManagedPortfolio($('view'),{...crm,draft:(key:string,value?:any)=>crm.draft(key,value,expectedOwner)},
      id=>void openCard(id).catch(error=>{if(error.name!=='AbortError')notice(error.message);}),()=>generation===renderGeneration&&section==='portfolio');
    if(generation===renderGeneration)disposeSection=stop;else stop();return;
  }
  const generation=++portfolioGeneration;
  portfolioSearch=search;portfolioOffset=offset;
  await crm.draft('portfolio-view',{view:portfolioView,work:portfolioWork,employee:portfolioEmployee});
  const personal=portfolioView==='mine';
  const data = await request("GET", personal?'work-portfolio':'portfolio', undefined,
    personal?{search,work:portfolioWork,employee_uuid:portfolioEmployee,offset,limit:100}:{search,limit:500});
  if(generation!==portfolioGeneration || section!=='portfolio')return;
  const modes=`<div class="tabs portfolio-modes"><button data-portfolio-view="mine" class="${personal?'active':''}">Мой портфель</button><button data-portfolio-view="all" class="${personal?'':'active'}">Все клиенты</button></div>`;
  const staff=context.staff || [];
  const selector=personal&&staff.length>1?`<label class="portfolio-person">Сотрудник <select id="portfolio-employee">${staff.map((p:any)=>`<option value="${esc(p.employee_uuid)}"${p.employee_uuid===portfolioEmployee?' selected':''}>${esc(p.name)}</option>`).join('')}</select></label>`:'';
  if(personal) {
    const options=[['all','Все',data.counts.all],['call','Звонки',data.counts.call],['visit','Выезды',data.counts.visit],['both','Оба',data.counts.both],
      ...(data.counts.delivery||context.role==='courier'?[['delivery','Доставка',data.counts.delivery]]:[])];
    const filters=`<div class="work-filters">${options.map(([key,title,count])=>`<button data-work-filter="${key}" class="${portfolioWork===key?'active':''}" aria-pressed="${portfolioWork===key}">${title} <span>${count}</span></button>`).join('')}</div>`;
    const debt=Object.entries(data.currencies || {}).map(([unit,value])=>amount(value,unit)).join(' · ');
    $('view').innerHTML=modes+selector+filters+`<div class="toolbar"><input type="search" id="search" placeholder="Имя, договор, телефон, адрес" value="${esc(search)}">${button('search-go','Найти')}${button('refresh','Обновить')}</div>`+
      `<p class="muted" data-work-total>Договоров: ${data.total_contracts} · Клиентов: ${data.total_people}${debt?' · Долг: '+debt:''}${data.unknown_amounts?' · Без подтверждённой суммы или валюты: '+data.unknown_amounts:''}</p>`+
      table(['Клиент','Договор / кредитор','Работа','Последний звонок','Последний выезд','Долг'],data.contracts.map((row:any)=>{
        const f=row.fields;
        return `<tr data-work-contract="${esc(row.contract_uuid)}"><td><button class="link" data-person="${esc(row.person_uuid)}">${esc(row.client_name || 'Без имени')}</button></td><td>${esc(row.external_id)}<br><span class="muted">${esc(f.partner || row.creditor_id)}</span></td><td>${row.work_kinds.map((kind:string)=>`<span class="work-kind">${esc(labels[kind] || kind)}</span>`).join('')}</td><td>${esc(f.soft_call_status || '—')}${f.soft_comm_date?'<br>'+calendarDate(f.soft_comm_date):''}${f.soft_comment?'<div class="queue-comment">'+esc(f.soft_comment)+'</div>':''}</td><td>${esc(f.call_status || '—')}${f.visit_date?'<br>'+calendarDate(f.visit_date):''}${f.visit_comment?'<div class="queue-comment">'+esc(f.visit_comment)+'</div>':''}</td><td>${amount(f.debt_today ?? f.debt_total,row.currency || 'Валюта не задана')}</td></tr>`;
      }))+
      (data.contracts.length?'':'<p class="empty">По выбранным условиям договоров нет</p>')+
      `<div class="toolbar portfolio-pages"><button id="work-previous"${offset===0?' disabled':''}>Назад</button><span>${data.total_contracts?offset+1:0}–${Math.min(offset+data.contracts.length,data.total_contracts)} из ${data.total_contracts}</span><button id="work-next"${data.has_more?'':' disabled'}>Далее</button></div>`;
    bind('work-previous',()=>portfolio(portfolioSearch,Math.max(0,portfolioOffset-100)));
    bind('work-next',()=>portfolio(portfolioSearch,portfolioOffset+100));
    $('view').querySelectorAll<HTMLButtonElement>('[data-work-filter]').forEach(b=>b.onclick=()=>{portfolioWork=b.dataset.workFilter!;void portfolio(portfolioSearch,0).catch(e=>notice(e.message));});
    const employee=$<HTMLSelectElement>('portfolio-employee');
    if(employee)employee.onchange=()=>{portfolioEmployee=employee.value;portfolioWork='all';void portfolio(portfolioSearch,0).catch(e=>notice(e.message));};
  } else {
  $("view").innerHTML =
    modes+`<div class="toolbar"><input type="search" id="search" placeholder="Имя, договор, телефон, адрес" value="${esc(search)}">${button("search-go", "Найти")}${button("refresh", "Обновить")}</div><div class="muted">Клиентов: ${data.total_people} · Договоров: ${data.total_contracts}${data.truncated ? " · Уточните поиск" : ""}</div>` +
    table(
      ["Клиент", "Договоры", "Остаток по валютам"],
      data.people.map(
        (p: any) =>
          `<tr><td><button class="link" data-person="${esc(p.person_uuid)}">${esc(p.name || "Без имени")}</button></td><td>${p.contracts}</td><td>${
            Object.keys(p.currencies || {}).length
              ? Object.entries(p.currencies)
                  .map(([k, v]) => amount(v, k))
                  .join("<br>")
              : "Откройте договоры"
          }</td></tr>`,
      ),
    );
  }
  if(['manager','owner'].includes(context.role)&&context.distribution?.reason)
    $('view').insertAdjacentHTML('afterbegin',`<p role="status">${esc(context.distribution.reason)}</p>`);
  $('view').querySelectorAll<HTMLButtonElement>('[data-portfolio-view]').forEach(b=>b.onclick=()=>{portfolioView=b.dataset.portfolioView!;void portfolio(portfolioSearch,0).catch(e=>notice(e.message));});
  bind("search-go", () => portfolio($<HTMLInputElement>("search").value,0));
  bind("refresh", () => portfolio());
  $<HTMLInputElement>("search").onkeydown = (e) => {
    if (e.key === "Enter") void portfolio($<HTMLInputElement>("search").value,0).catch(error=>notice(error.message));
  };
  if(context.role==='employee'){
    $('view').insertAdjacentHTML('afterbegin',`<div class="toolbar portfolio-context">${button('portfolio-tasks','Задачи')}${button('portfolio-queue','Отправка результатов')}${button('portfolio-script','Скрипт')}${button('portfolio-help','Справка')}</div>`);
    bind('portfolio-tasks',()=>go('tasks'));bind('portfolio-queue',()=>go('queue'));bind('portfolio-script',()=>go('script'));bind('portfolio-help',()=>go('help'));
  }
  document
    .querySelectorAll<HTMLButtonElement>("[data-person]")
    .forEach((b) => (b.onclick = () => void openCard(b.dataset.person!)));
}

async function openCard(person: string) {
  clearTimeout(cardRetryTimer);
  clearTimeout(syncTimer);syncForced=false;
  if(!activePerson)sectionScroll.set(section,document.querySelector('main')!.scrollTop);
  portfolioGeneration++;
  taskGeneration++;const generation=++renderGeneration;
  activePerson=person;cardSyncGeneration++;lastCardAcknowledgements='';cardRefreshPending=false;
  card=null;cardScript=null;cardRefreshError='';
  const createTask=taskSelectClient;
  cardOrigin=section==='tasks' || createTask?'tasks':'portfolio';
  taskSelectClient=false;
  $('view').innerHTML='<p role="status">Загрузка карточки…</p>';
  try {
    const loaded = await request("GET", "card", undefined, { person_uuid: person });
    let loadedScript = null;
    if (can('call')) {
      try { loadedScript = await request('GET', 'script'); }
      catch (error) { if((error as Error).name==='AbortError')throw error; }
    }
    if(generation!==renderGeneration || activePerson!==person)return;
    card=loaded;cardScript=loadedScript;
    tab = "overview";
    telemetry("feature_opened", "card");
    await renderCard();
    document.querySelector('main')!.scrollTop=0;
    scheduleSync();
    if(createTask){
      if(can('task') && card.contracts.some((entry:any)=>contractCan(entry,'task')))await action('task');
      else notice('Нет права назначать задачу по этому клиенту.');
    }
  } catch (e) {
    if(generation!==renderGeneration || (e as Error).name==='AbortError')return;
    activePerson='';card=null;cardScript=null;
    $('view').innerHTML=button('card-load-back',cardOrigin==='tasks'?'Вернуться к задачам':'Вернуться к портфелю');
    bind('card-load-back',()=>go(cardOrigin));
    notice((e as Error).message);
  }
}
async function renderCard() {
  $("title").textContent = card.person.name || "Карточка клиента";
  const actions = [
    "call",
    "visit",
    'delivery',
    "note",
    "contact",
    "promise",
    "task",
    "reported_payment",
    "document",
  ].filter(kind => !(context.role === 'courier' && kind === 'task') && can(kind) && card.contracts.some((contract: any) =>
    !Array.isArray(contract.allowed_actions) || contract.allowed_actions.includes(kind)));
  const tabs = [
    ["overview", "Обзор"],
    ["contracts", "Договоры"],
    ["history", "История"],
    ["tasks", "Задачи"],
    ["promises", "Обещания"],
    ["payments", "Оплаты"],
    ["documents", "Документы"],
  ];
  $("view").innerHTML =
    `<div class="toolbar">${button("back", cardOrigin==='tasks'?'← Задачи':"← Портфель")}${button('card-refresh','Обновить карточку')}${actions.map((k) => `<button data-action="${k}"${k === "call" ? ' class="primary"' : ""}>${labels[k]}</button>`).join("")}</div><section id="card-saves" aria-label="Отправка результатов"></section><div class="tabs">${tabs.map(([id, title]) => `<button data-tab="${id}" class="${tab === id ? "active" : ""}">${title}</button>`).join("")}</div><div class="card-workspace${cardScript?.script ? ' with-script' : ''}"><div id="card-content"></div>${cardScript?.script ? `<aside class="card-script-watermark" aria-label="Скрипт разговора"><h2>${esc(cardScript.script.title)}</h2>${cardScript.script.steps.map((step: any) => `<section><h3>${esc(step.title)}</h3><p>${esc(step.text)}</p></section>`).join('')}<small>Версия ${esc(cardScript.published_revision)}</small></aside>` : ''}</div>`;
  bind("back", () => go(cardOrigin==='tasks'?'tasks':'portfolio'));
  bind('card-refresh',()=>syncCurrentView(true));
  document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        tab = b.dataset.tab!;
        void renderCard();
      }),
  );
  document
    .querySelectorAll<HTMLButtonElement>("[data-action]")
    .forEach((b) => (b.onclick = () => void action(b.dataset.action!)));
  const host = $("card-content");
  if (tab === "overview" || tab === "contracts") {
    host.innerHTML = table(
      ["Договор / кредитор", "Контакты", "Остаток", "Последний результат"],
      card.contracts.map(
        (r: any) =>
          `<tr><td><b>${esc(r.external_id)}</b><br><span class="muted">${esc(r.creditor_id)}</span></td><td>${esc(r.fields.phone || r.fields.phone_main || "—")}<br>${esc(r.fields.address || "—")}</td><td class="amount">${amount(r.fields.debt_today ?? r.fields.debt_total, r.currency || "Валюта не задана")}</td><td>${r.fields.soft_call_status || r.fields.soft_comment ? `<div>Звонок: ${esc(r.fields.soft_call_status || '—')}<br><span class="muted">${esc(r.fields.soft_comment || '')}</span></div>` : ''}${r.fields.call_status || r.fields.visit_comment ? `<div>Выезд: ${esc(r.fields.call_status || '—')}<br><span class="muted">${esc(r.fields.visit_comment || '')}</span></div>` : ''}</td></tr>`,
      ),
    );
    host.innerHTML += card.contracts.map((c:any)=>contractDetails(c)+fullBusiness(c)).join('');
    if (tab === "overview")
      host.innerHTML += `<div class="summary"><div class="metric">Договоры<strong>${card.contracts.length}</strong></div><div class="metric">Открытые задачи<strong>${card.entities.filter((e: any) => e.kind === "task" && e.status === "open").length}</strong></div><div class="metric">Заявленные оплаты<strong>${card.entities.filter((e: any) => e.kind === "reported_payment").length}</strong><small>Подтверждение поступления хранится отдельно</small></div></div>`;
  } else if (tab === "history") {
    host.innerHTML = card.history.length
      ? card.history
          .map(
            (e: any) =>
              `<article class="record"><h3>${esc(labels[e.kind] || e.kind)} <span class="tag">${esc(({legacy:'Прежнее приложение',app:'Приложение',excel:'Реестр',unknown:'Источник не определён'} as any)[e.origin] || e.origin)}</span></h3><div class="muted">${historyWhen(e)} · ${esc(e.author || e.payload.legacy_employee || "Автор не указан")} · ${e.contract_ids.length} дог. ${e.projection_status === "needs_review" ? "· Требует сверки" : ""}</div><p>${esc(
                (e.kind === 'delivery' ? [e.payload.delivered ? 'Конверт передан' : 'Конверт не передан',e.payload.delivery_address,e.payload.delivery_phone,e.payload.delivery_comment].filter(Boolean).join('\n') : '') || e.payload.text ||
                  e.payload.title ||
                  Object.entries(e.payload.values || {})
                    .map(
                      ([k, v]) =>
                        (context.fields.find((f: any) => f.key === k)?.title ||
                          k) +
                        ": " +
                        (v ?? "Очищено"),
                    )
                    .join("\n"),
              )}</p>${e.payload.script_ref ? `<p class="muted">Подсказка: версия ${esc(e.payload.script_ref.published_revision)}</p>` : ""}${e.reason ? `<p>Причина: ${esc(e.reason)}</p>` : ""}${["call", "visit", "note", "contact"].includes(e.kind) && can("correct") && e.can_correct !== false ? `<button data-correct="${e.event_id}">Исправить</button>` : ""}</article>`,
          )
          .join("")
      : '<div class="empty">История пока пуста</div>';
    document.querySelectorAll<HTMLButtonElement>("[data-correct]").forEach(
      (b) =>
        (b.onclick = () =>
          void action(
            "correction",
            card.history.find((e: any) => e.event_id === b.dataset.correct),
          )),
    );
  } else {
    const kinds = (
      {
        tasks: ["task"],
        promises: ["promise"],
        payments: ["reported_payment", "payment_confirmed"],
        documents: ["document"],
      } as any
    )[tab];
    const rows = card.entities.filter((e: any) => kinds.includes(e.kind));
    host.innerHTML = rows.length
      ? table(
          ["Вид", "Содержание", "Срок / сумма", "Состояние"],
          rows.map(
            (e: any) =>
              `<tr><td>${esc(labels[e.kind])}</td><td>${esc(e.data.title || e.data.filename || e.data.text || "—")}${e.assignee ? `<br><span class="muted">${esc(e.assignee)}</span>` : ''}</td><td>${e.data.amount != null ? amount(e.data.amount, e.data.currency) : date(e.data.due_at)}</td><td>${e.status === "done" ? "Завершено" : e.kind === 'task' ? 'В работе' : "Зафиксировано"}${e.kind === "document" ? `<br><button class="link" data-document="${e.entity_id}">Сохранить документ</button>` : ""}${e.kind === "task" && e.status === "open" && e.can_complete !== false ? `<br><button class="link" data-complete="${e.entity_id}">Выполнено</button>` : ""}</td></tr>`,
          ),
        )
      : '<div class="empty">Записей пока нет</div>';
    document.querySelectorAll<HTMLButtonElement>("[data-document]").forEach(
      (b) =>
        (b.onclick = async () => {
          const entity = rows.find(
            (v: any) => v.entity_id === b.dataset.document,
          );
          const result = await crm.document(
            entity.data.filename,
            entity.data.document_id,
          );
          if (!result.ok && !result.cancelled) notice(result.text);
        }),
    );
    document.querySelectorAll<HTMLButtonElement>("[data-complete]").forEach(
      (b) =>
        (b.onclick = () => {
          const e = rows.find((v: any) => v.entity_id === b.dataset.complete);
          void submitCommand({
            kind: "task_complete",
            contract_ids: [e.contract_uuid],
            payload: { entity_id: e.entity_id, entity_revision: e.revision },
          });
        }),
    );
  }
  try {paintCardSaves(await queueEntries());}
  catch(error){if($('card-saves'))$('card-saves').textContent=(error as Error).message;}
}

async function submitCommand(raw: any) {
  const body = { schema_version: 1, operation_id: crypto.randomUUID(), ...raw };
  const result = await crm.command(body);
  if (!result.ok) throw new Error(result.text);
  telemetry(
    "command_queued",
    raw.kind === "task_complete"
      ? "task"
      : raw.kind === "correction"
        ? "card"
        : raw.kind,
    { operation_id: body.operation_id },
  );
  notice("Действие сохранено на компьютере.",'status');
  scheduleSync();
}

async function action(kind: string, original?: any) {
  if(formOpening || $<HTMLDialogElement>('dialog').open)return;
  formOpening=true;
  try {await actionForm(kind,original,renderGeneration);}
  finally {formOpening=false;if(cardRefreshPending && !$<HTMLDialogElement>('dialog').open)scheduleSync();}
}
async function actionForm(kind: string, original: any, viewGeneration:number) {
  const formRevisions=structuredClone(card.contracts.map((c:any)=>[c.contract_uuid,c.revisions]));
  const target = kind === "correction" ? original.kind : kind;
  telemetry("workflow_started", target);
  const dialog = $<HTMLDialogElement>("dialog");
  if (kind === 'delivery') {
    await mountDeliveryForm(dialog, card.contracts.map((row: any)=>({...row,client_name:card.person.name})),
      {fields:context.fields,draft:(key,...value)=>crm.draft(key,...value),command:body=>crm.command(body),close:()=>dialog.close()});
    dialog.showModal();return;
  }
  if (target === "document") {
    const available = card.contracts.filter((entry: any) => contractCan(entry, 'document'));
    const selected = available[0];
    if (!selected) { notice('Нет права прикреплять документ к этому клиенту.'); return; }
    if (available.length > 1) {
      await documentForm();
      return;
    }
    const uploaded = await crm.document(selected.contract_uuid);
    if (!uploaded.ok) {
      if (!uploaded.cancelled) notice(uploaded.text);
      return;
    }
    await submitCommand({
      kind: "document",
      contract_ids: [selected.contract_uuid],
      payload: {
        document_id: uploaded.data.document_id,
        sha256: uploaded.data.sha256,
        filename: uploaded.data.filename,
      },
    });
    return;
  }
  const map: Record<string, string[]> = {
    call: ["soft_call_status", "soft_comment", "soft_comm_date"],
    visit: [
      "call_status",
      "visit_date",
      "visit_comment",
      "contact_with",
      "agreed_payment",
      "who_at_address",
    ],
    contact: ["phone", "phone_main", "address", "extra_address"],
    note: [],
    promise: [],
    task: [],
    reported_payment: [],
  };
  const keys = map[target] || [],
    singular = ["promise", "task", "reported_payment"].includes(target);
  const draftKey =
    card.person.person_uuid +
    ":" +
    kind +
    (original ? ":" + original.event_id : "");
  const saved = (await crm.draft(draftKey)).data;
  // A form owns its script snapshot. Reopening the same draft or opening
  // the inline panel again must not switch text during the call.
  let callScript: any = saved?.script_snapshot ?? (kind === 'call' && cardScript?.script ? structuredClone(cardScript) : null);
  let scriptError = "";
  if (kind === "call" && !callScript) {
    try {
      const published = await request("GET", "script");
      callScript = {
        script: published.script,
        published_revision: published.published_revision,
        published_at: published.published_at,
        script_ref: published.script_ref ?? null,
      };
    } catch (error) {
      scriptError = (error as Error).message;
    }
  }
  if (kind === 'call' && callScript?.script) {
    cardScript = callScript;
    repaintWatermark(callScript);
  }
  let scriptShown = saved?.script_shown === true || (kind === 'call' && !!callScript?.script && !!document.querySelector('.card-script-watermark'));
  const controls = keys
    .map((key) => {
      const def = context.fields.find((f: any) => f.key === key);
      const value =
        saved?.values?.[key] ?? original?.payload?.values?.[key] ?? "";
      const options = context.dropdowns[key];
      return `<div class="field ${key.includes("comment") ? "span" : ""}"><label for="v-${key}">${esc(def?.title || key)}</label>${options ? `<select id="v-${key}" data-field="${key}"><option value="">Не выбрано</option>${options.map((v: string) => `<option${v === value ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>` : key.includes("comment") ? `<textarea id="v-${key}" data-field="${key}">${esc(value)}</textarea>` : `<input id="v-${key}" data-field="${key}" type="${def?.type === "date" ? "date" : "text"}" value="${esc(value)}">`}<label class="check"><input type="checkbox" data-clear="${key}">Очистить поле</label></div>`;
    })
    .join("");
  dialog.innerHTML = `<form id="action-form"><h2>${esc(labels[kind])}</h2><div class="contracts">${card.contracts.map((c: any, i: number) => `<label class="check"><input type="${singular ? "radio" : "checkbox"}" name="contracts" value="${c.contract_uuid}"${!singular || i === 0 ? " checked" : ""}>${esc(c.external_id)} · ${esc(c.creditor_id)}</label>`).join("")}</div><div class="grid">${controls}${["note", "task"].includes(target) ? `<div class="field span"><label>${target === "task" ? "Задача" : "Заметка"}</label><textarea id="free-text">${esc(saved?.text || original?.payload?.text || "")}</textarea></div>` : ""}${["promise", "reported_payment"].includes(target) ? `<div class="field"><label>Сумма</label><input id="money" inputmode="decimal" value="${esc(saved?.amount || "")}"></div><div class="field"><label>Валюта</label><select id="currency"><option>RUB</option><option>TJS</option><option>UZS</option><option>USD</option></select></div>` : ""}${["promise", "task"].includes(target) ? `<div class="field"><label>Срок · ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}</label><input id="due" type="datetime-local" value="${esc(saved?.due || "")}"></div>` : ""}${kind === "correction" ? '<div class="field span"><label>Причина исправления</label><textarea id="reason" required></textarea></div>' : ""}</div><div class="error" id="form-error"></div><div class="actions">${button("cancel", "Закрыть")}${kind === "call" ? button("show-script", "Скрипт") : ""}<button type="submit" class="primary">Сохранить действие</button></div><div id="script-inline"></div></form>`;
  const selectedIds = saved?.contract_ids ?? original?.contract_ids;
  const permitted = new Set(card.contracts.filter((entry: any) => contractCan(entry, target)
    && (kind !== 'correction' || contractCan(entry, 'correct'))).map((entry: any) => entry.contract_uuid));
  let needsContractSelection = Array.isArray(selectedIds) && selectedIds.some((id: string) => !permitted.has(id));
  if (target === 'task') {
    const staff = (context.staff?.length ? context.staff : [{employee_uuid: context.employee_uuid, name: 'Я',
      work_kinds: ['call','visit','delivery'].filter(key => can(key))}]).filter((person:any)=>person.work_kinds?.length);
    dialog.querySelector('.grid')!.insertAdjacentHTML('beforeend', `<div class="field"><label for="task-assignee">Исполнитель</label><select id="task-assignee">${staff.map((person: any) => `<option value="${esc(person.employee_uuid)}">${esc(person.name)}</option>`).join('')}</select></div><div class="field"><label for="task-work">Вид работы</label><select id="task-work"></select></div>`);
    const assignee = $<HTMLSelectElement>('task-assignee'), work = $<HTMLSelectElement>('task-work');
    assignee.value = saved?.assignee_uuid ?? original?.payload?.assignee_uuid ?? (newTaskAssignee || context.employee_uuid);
    newTaskAssignee='';
    const fillWork = () => {
      const person = staff.find((entry: any) => entry.employee_uuid === assignee.value);
      work.innerHTML = (person?.work_kinds || ['call','visit']).map((key: string) => `<option value="${esc(key)}">${esc(key === 'delivery' ? 'Передать конверт' : labels[key])}</option>`).join('');
    };
    fillWork();work.value = saved?.work_kind ?? original?.payload?.work_kind ?? work.value;
    assignee.addEventListener('change', () => { fillWork(); void crm.draft(draftKey, capture()); });
    if (!saved?.due && !original) {
      const today = new Date();
      $<HTMLInputElement>('due').value = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}T23:59`;
    }
  }
  const firstPermitted = card.contracts.find((entry: any) => permitted.has(entry.contract_uuid))?.contract_uuid;
  dialog.querySelectorAll<HTMLInputElement>("input[name=contracts]").forEach((el) => {
    el.checked = permitted.has(el.value) && (Array.isArray(selectedIds) ? selectedIds.includes(el.value) : !singular || el.value === firstPermitted);
    el.disabled = kind === 'correction' || !permitted.has(el.value);
    el.addEventListener('input', () => { needsContractSelection = false; });
  });
  if (needsContractSelection) $('form-error').textContent = 'Права по договору изменились. Выберите доступный договор; прежний черновик сохранён.';
  dialog.querySelectorAll<HTMLInputElement>("[data-clear]").forEach((el) => {
    el.checked = (saved?.clear ?? original?.payload?.clear ?? []).includes(el.dataset.clear);
  });
  const freeText = $<HTMLTextAreaElement>("free-text");
  if (freeText) freeText.value = saved?.text ?? original?.payload?.[target === "task" ? "title" : "text"] ?? "";
  const currencyInput = $<HTMLSelectElement>("currency");
  if (currencyInput) {
    const selected = dialog.querySelector<HTMLInputElement>("input[name=contracts]:checked")?.value;
    const currentCurrency = saved?.currency ?? original?.payload?.currency ?? card.contracts.find((c: any) => c.contract_uuid === selected)?.currency ?? "";
    if (!Array.from(currencyInput.options).some((o) => o.value === currentCurrency))
      currencyInput.add(new Option(currentCurrency || "Выберите валюту", currentCurrency));
    currencyInput.value = currentCurrency;
  }
  const reasonInput = $<HTMLTextAreaElement>("reason");
  if (reasonInput) reasonInput.value = saved?.reason ?? "";
  const capture = () => ({
    contract_ids: needsContractSelection ? selectedIds : Array.from(dialog.querySelectorAll<HTMLInputElement>("input[name=contracts]:checked")).map((el) => el.value),
    clear: Array.from(dialog.querySelectorAll<HTMLInputElement>("[data-clear]:checked")).map((el) => el.dataset.clear),
    values: Object.fromEntries(
      keys.map((k) => [k, ($("v-" + k) as HTMLInputElement).value]),
    ),
    text: ($("free-text") as HTMLTextAreaElement)?.value,
    amount: ($("money") as HTMLInputElement)?.value,
    due: ($("due") as HTMLInputElement)?.value,
    currency: currencyInput?.value,
    reason: reasonInput?.value,
    script_snapshot: callScript,
    script_shown: scriptShown,
    assignee_uuid: $<HTMLSelectElement>('task-assignee')?.value,
    work_kind: $<HTMLSelectElement>('task-work')?.value,
  });
  if (kind === "call" && callScript) await crm.draft(draftKey, capture());
  dialog.querySelectorAll("input,select,textarea").forEach((el) =>
    el.addEventListener("input", () => {
      void crm.draft(draftKey, capture());
    }),
  );
  $("cancel").onclick = (e) => {
    e.preventDefault();
    void crm.draft(draftKey, capture());
    dialog.close();
  };
  if (kind === "call")
    $("show-script").onclick = async (e) => {
      e.preventDefault();
      if (!callScript) {
        $("script-inline").textContent = scriptError || "Для исправления события подсказка нового звонка не открывается.";
        return;
      }
      $("script-inline").innerHTML = scriptMarkup(callScript);
      scriptShown = !!callScript.script;
      await crm.draft(draftKey, capture());
      telemetry("script_shown", "script");
    };
  $('action-form').dataset.work=target;
  $("action-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      if (needsContractSelection) throw new Error('Выберите доступный договор после изменения прав.');
      const contract_ids = Array.from(
        dialog.querySelectorAll<HTMLInputElement>(
          "input[name=contracts]:checked",
        ),
      ).map((el) => el.value);
      if (!contract_ids.length) throw new Error("Выберите договор.");
      if(contract_ids.some(id=>!contractCan(card.contracts.find((r:any)=>r.contract_uuid===id)||{allowed_actions:[]},target)))
        throw new Error('Изменилось назначение этой работы. Ввод сохранён у исходной личности.');
      const values: any = {},
        clear: string[] = [];
      for (const key of keys) {
        const value = ($("v-" + key) as HTMLInputElement).value;
        if (
          (dialog.querySelector(`[data-clear="${key}"]`) as HTMLInputElement)
            .checked
        ) {
          values[key] = null;
          clear.push(key);
        } else if (value !== "") values[key] = value;
      }
      const payload: any = { values, clear };
      if (kind === "call" && scriptShown && callScript?.script_ref)
        payload.script_ref = callScript.script_ref;
      if (target === "note")
        payload.text = $<HTMLTextAreaElement>("free-text").value;
      if (target === "task") {
        payload.title = $<HTMLTextAreaElement>("free-text").value;
        payload.assignee_uuid = $<HTMLSelectElement>('task-assignee').value;
        payload.work_kind = $<HTMLSelectElement>('task-work').value;
      }
      if (["promise", "reported_payment"].includes(target)) {
        payload.amount = $<HTMLInputElement>("money").value.replace(",", ".");
        payload.currency = $<HTMLSelectElement>("currency").value;
        if (!payload.currency) throw new Error("Выберите валюту.");
      }
      if (["promise", "task"].includes(target)) {
        const dateValue = $<HTMLInputElement>("due").value;
        if (!dateValue) throw new Error("Укажите срок.");
        payload.due_at = new Date(dateValue).toISOString();
      }
      if (kind === "correction") payload.target_kind = target;
      const raw: any = {
        kind,
        contract_ids,
        payload,
        base_revisions: Object.fromEntries(
          formRevisions,
        ),
      };
      if (kind === "correction") {
        raw.correction_of = original.event_id;
        raw.reason = $<HTMLTextAreaElement>("reason").value;
      }
      const existing = (await crm.draft(draftKey + ":pending")).data;
      const content = JSON.stringify(raw);
      const operation_id =
        existing?.content === content
          ? existing.operation_id
          : crypto.randomUUID();
      await crm.draft(draftKey + ":pending", { content, operation_id });
      const result = await crm.command({
        schema_version: 1,
        operation_id,
        ...raw,
      });
      if (!result.ok) throw new Error(result.text);
      await crm.draft(draftKey, null);
      await crm.draft(draftKey + ":pending", null);
      dialog.close();
      telemetry("command_queued", target, { operation_id });
      if(target==='task')await go('tasks');
      notice("Действие сохранено на компьютере.",'status');
      scheduleSync();
    } catch (error) {
      $("form-error").textContent = (error as Error).message;
    }
  };
  if(viewGeneration===renderGeneration)dialog.showModal();
}

async function documentForm() {
  const dialog = $<HTMLDialogElement>("dialog");
  dialog.innerHTML = `<h2>Документ</h2><div class="field"><label>Договор</label><select id="document-contract">${card.contracts.map((c: any) => `<option value="${c.contract_uuid}">${esc(c.external_id)} · ${esc(c.creditor_id)}</option>`).join("")}</select></div><div class="actions">${button("document-close", "Закрыть")}${button("document-pick", "Выбрать файл", true)}</div>`;
  dialog.querySelectorAll<HTMLOptionElement>('#document-contract option').forEach(option => {
    if (!card.contracts.some((entry: any) => entry.contract_uuid === option.value && contractCan(entry, 'document'))) option.remove();
  });
  bind("document-close", () => dialog.close());
  bind("document-pick", async () => {
    const id = $<HTMLSelectElement>("document-contract").value;
    const result = await crm.document(id);
    if (!result.ok) {
      if (!result.cancelled) throw new Error(result.text);
      return;
    }
    await submitCommand({
      kind: "document",
      contract_ids: [id],
      payload: {
        document_id: result.data.document_id,
        sha256: result.data.sha256,
        filename: result.data.filename,
      },
    });
    dialog.close();
  });
  dialog.showModal();
}

async function queuePage() {
  const result = await crm.queue();
  if (!result.ok) throw new Error(result.text);
  const entries = result.data.entries;
  const pending = entries.filter((e: any) => e.state !== "accepted" && !e.reviewed_at);
  const exportErrors = (result.data.exports || []).filter(
    (v: any) => !v.completed,
  );
  $("view").innerHTML =
    `<div class="toolbar">${button("queue-refresh", "Обновить")}${button("queue-send", "Отправить", true)}</div><div class="muted">Ожидают решения или отправки: ${pending.length} · Незавершённых выгрузок: ${exportErrors.length} · Подтверждено сервером: ${entries.filter((e:any)=>e.state==='accepted').length}</div>` +
    table(
      ["Действие", "Сохранено", "Состояние", "Результат"],
      entries
        .slice()
        .reverse()
        .map(
          (e: any) =>
            `<tr><td>${esc(({receipt:'Квитанция реестра','registry-edit':'Правка реестра','active-time':e.body.kind==='idle'?'Простой в CRM':'Активное время'} as any)[e.action] || labels[e.body.kind] || e.body.kind)}</td><td>${date(e.queued_at)}</td><td>${e.reviewed_at?'Возвращено в черновик':e.state === "accepted" ? "Принято сервером" : e.state === "attention" ? "Требует решения" : "Ожидает отправки"}</td><td>${esc(e.error || (e.result?.projection_status === "needs_review" ? "История сохранена; текущие поля требуют сверки" : e.state==='accepted' ? "Подтверждено точным ID операции" : ""))}${e.state === "attention" && !e.reviewed_at ? e.action==='registry-edit'?'<br>Сверьте пакет в разделе «Реестр».':`<br><button class="link" data-retry="${e.body.operation_id || e.body.receipt_id}">Повторить исходный запрос</button>` : ""}</td></tr>`,
        ),
    );
  $("view").insertAdjacentHTML(
    "beforeend",
    exportErrors
      .map(
        (v: any) =>
          `<article class="record"><h3>Выгрузка требует проверки</h3><p>${esc(v.path)}</p><p>${esc(v.error || "Запись файла ещё не подтверждена.")}</p></article>`,
      )
      .join(""),
  );
  bind("queue-refresh", queuePage);
  bind("queue-send", async () => {
    await crm.queue("flush");
    await queuePage();
  });
  document.querySelectorAll<HTMLButtonElement>("[data-retry]").forEach(
    (b) =>
      (b.onclick = async () => {
        await crm.queue("retry", b.dataset.retry);
        await queuePage();
      }),
  );
}

async function tasksPage() {
  const generation=++taskGeneration;
  const [data,queue]=await Promise.all([request('GET','tasks',undefined,{status:taskStatus,employee_uuid:taskEmployee,
    work:taskWork,search:taskSearch,offset:taskOffset,limit:100}),crm.queue()]);
  if(generation!==taskGeneration || section!=='tasks')return;
  const entries=queue.ok?(queue.data?.entries || []):[];
  const pending=entries.filter((e:any)=>['task','task_complete'].includes(e.body?.kind) && e.state!=='accepted');
  const team=['manager','owner','analyst','observer'].includes(context.role);
  const filters=[['open','Открытые'],['today','На сегодня'],['overdue','Просроченные'],['done','Завершённые'],['all','Все']];
  const due=(value:string)=>value?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:data.timezone}).format(new Date(value)):'Срок не указан';
  $('title').textContent='Задачи';
  $('view').innerHTML=`<div class="toolbar"><input id="task-search" type="search" placeholder="Клиент, договор или задача" aria-label="Поиск задач" value="${esc(taskSearch)}">${team?`<label>Исполнитель <select id="task-employee"><option value="all">Вся команда</option>${(context.staff || []).map((p:any)=>`<option value="${esc(p.employee_uuid)}"${p.employee_uuid===taskEmployee?' selected':''}>${esc(p.name)}</option>`).join('')}</select></label>`:''}<label>Работа <select id="task-work">${[['all','Любая'],['call','Звонок'],['visit','Выезд'],['delivery','Передать конверт']].map(([id,label])=>`<option value="${id}"${id===taskWork?' selected':''}>${label}</option>`).join('')}</select></label>${button('task-refresh','Обновить')}${can('task') && context.role!=='courier'?button('task-create',team?'Назначить задачу':'Напомнить',true):''}</div><div class="work-filters">${filters.map(([id,label])=>`<button data-task-filter="${id}" class="${id===taskStatus?'active':''}">${label} <span>${data.counts[id] || 0}</span></button>`).join('')}</div><div id="task-send-state">${!queue.ok?'<p role="alert">Не удалось прочитать очередь. Завершение задач временно недоступно.</p>':pending.length?`<div class="task-pending"><strong>Ожидают подтверждения: ${pending.length}</strong>${pending.map((e:any)=>`<div>${esc(e.body.kind==='task'?e.body.payload?.title:'Завершение задачи')} · ${e.state==='attention'?'Требует сверки':'Ожидает отправки'}</div>`).join('')}<button id="tasks-queue" class="link">Открыть очередь</button></div>`:''}</div><p class="muted">${data.total} задач · сроки по ${esc(data.timezone)}</p>${data.tasks.length?table(['Клиент / договор','Задача','Исполнитель','Срок','Состояние'],data.tasks.map((row:any)=>{
    const queued=entries.slice().reverse().find((e:any)=>e.body?.kind==='task_complete' && e.body.payload?.entity_id===row.entity_id);
    const sending=row.status==='open' && queued;
    const status=row.status==='done'?'Завершена':sending?(queued.state==='attention'?'Требует сверки':'Ожидает подтверждения'):row.overdue?'Просрочена':row.due_today?'На сегодня':'Открыта';
    return `<tr data-task-row="${esc(row.entity_id)}"><td><button class="link" data-task-person="${esc(row.person_uuid)}">${esc(row.client)}</button><br>${esc(row.external_id)}<br><span class="muted">${esc(row.creditor)}</span></td><td>${esc(row.title)}<br><span class="muted">${esc(labels[row.work_kind] || row.work_kind)}</span></td><td>${esc(row.assignee)}</td><td>${esc(due(row.due_at))}</td><td>${status}${row.can_complete && !sending && queue.ok?`<br><button class="link" data-task-complete="${esc(row.entity_id)}">Выполнено</button>`:''}</td></tr>`;
  })):'<div class="empty">Задач по выбранным условиям нет</div>'}<div class="toolbar portfolio-pages">${button('task-prev','Назад')}${button('task-next','Далее')}</div>`;
  const persist=()=>crm.draft('task-view',{status:taskStatus,employee:taskEmployee,work:taskWork});
  const refresh=async()=>{taskSearch=$<HTMLInputElement>('task-search').value;taskOffset=0;await persist();await tasksPage();};
  bind('task-refresh',refresh);
  $('task-search').addEventListener('keydown',(event)=>{if(event.key==='Enter')void refresh().catch(e=>notice(e.message));});
  const select=async()=>{taskEmployee=$<HTMLSelectElement>('task-employee')?.value || context.employee_uuid;taskWork=$<HTMLSelectElement>('task-work').value;await refresh();};
  $('task-employee')?.addEventListener('change',()=>void select().catch(e=>notice(e.message)));
  $('task-work').addEventListener('change',()=>void select().catch(e=>notice(e.message)));
  document.querySelectorAll<HTMLButtonElement>('[data-task-filter]').forEach(b=>b.onclick=()=>{taskStatus=b.dataset.taskFilter!;void refresh().catch(e=>notice(e.message));});
  document.querySelectorAll<HTMLButtonElement>('[data-task-person]').forEach(b=>b.onclick=()=>void openCard(b.dataset.taskPerson!));
  document.querySelectorAll<HTMLButtonElement>('[data-task-complete]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;const row=data.tasks.find((r:any)=>r.entity_id===b.dataset.taskComplete);
    try{await submitCommand({kind:'task_complete',contract_ids:[row.contract_uuid],payload:{entity_id:row.entity_id,entity_revision:row.revision}});await tasksPage();notice('Завершение сохранено. Ожидается подтверждение сервера.','status');}
    catch(error){notice((error as Error).message);b.disabled=false;}
  });
  bind('tasks-queue',()=>go('queue'));
  bind('task-create',async()=>{const selected=taskEmployee;await go('portfolio');newTaskAssignee=selected==='all'?'':selected;taskSelectClient=true;notice('Выберите клиента для задачи.');});
  $<HTMLButtonElement>('task-prev').disabled=taskOffset===0;$<HTMLButtonElement>('task-next').disabled=!data.has_more;
  bind('task-prev',async()=>{taskOffset=Math.max(0,taskOffset-100);await tasksPage();});
  bind('task-next',async()=>{taskOffset+=100;await tasksPage();});
}

async function cabinet() {
  const month=new Date().toLocaleDateString('sv-SE').slice(0,7);
  const staff=context.staff || [];
  $('view').innerHTML=`<div class="toolbar"><label>Месяц <input id="cabinet-month" type="month" value="${month}"></label>${staff.length>1?`<label>Сотрудник <select id="cabinet-employee">${staff.map((person:any)=>`<option value="${esc(person.employee_uuid)}"${person.employee_uuid===context.employee_uuid?' selected':''}>${esc(person.name)}</option>`).join('')}</select></label>`:''}${button('cabinet-refresh','Показать')}</div><div id="cabinet-result"></div>`;
  const show=async()=>{
    const data=await request('GET','cabinet',undefined,{month:$<HTMLInputElement>('cabinet-month').value,
      employee_uuid:($('cabinet-employee') as HTMLSelectElement)?.value || context.employee_uuid});
    const stats=data.stats,payments=data.payments;
    $('cabinet-result').innerHTML=`<h2>${esc(data.employee)}</h2><div class="summary">${[['Звонки',stats.call||0],['Выезды',stats.visit||0],['Попытки передачи',stats.delivery||0],['Обещания',stats.promise||0],['Задачи завершены',stats.task_complete||0],['Обработано договоров',stats.processed_contracts],['Договоров в портфеле',stats.portfolio_contracts]].map(([title,value])=>`<div class="metric">${esc(title)}<strong>${esc(value)}</strong></div>`).join('')}</div><h2>Оплаты за месяц по реестру</h2><p class="muted">Текущий постоянный портфель сотрудника. Данные на ${payments.source_as_of.length?payments.source_as_of.map(date).join(', '):'—'}. Договоров с месячной суммой: ${payments.known_contracts}; без данных: ${payments.missing_contracts}.</p>${payments.totals.length?table(['Валюта','Сумма'],payments.totals.map((row:any)=>`<tr><td>${esc(row.currency || 'Валюта не подтверждена: '+row.unit.replace('unconfirmed:registry-partner:','').replace('unconfirmed:',''))}</td><td>${amount(row.amount,row.currency||'')}</td></tr>`)):'<div class="empty">Месячные суммы в источнике отсутствуют</div>'}${table(['Клиент','Договор','Кредитор','Сумма'],payments.rows.map((row:any)=>`<tr><td><button class="link" data-cabinet-person="${esc(row.person_uuid)}">${esc(row.client)}</button></td><td>${esc(row.external_id)}</td><td>${esc(row.creditor)}</td><td>${amount(row.amount,row.currency||'Валюта не подтверждена')}</td></tr>`))}<h2>Мои операции за месяц</h2><p class="muted">Авторство сохраняется при передаче договора.${data.history_without_time?' Исторических записей без точного времени: '+data.history_without_time+'.':''}${data.operations_truncated?' Показаны последние 500 операций.':''}</p>${table(['Время','Действие','Договоров','Содержание'],data.operations.map((row:any)=>`<tr><td>${row.workday ? calendarDate(row.workday) : date(row.occurred_at)}</td><td>${esc(labels[row.kind]||row.kind)}</td><td>${row.contract_ids.length}</td><td>${esc(row.text)}</td></tr>`))}`;
    document.querySelectorAll<HTMLButtonElement>('[data-cabinet-person]').forEach(button=>button.onclick=()=>void openCard(button.dataset.cabinetPerson!));
  };
  bind('cabinet-refresh',show);await show();
}

async function shift() {
  const data = await request("GET", "shifts");
  const current = data.shifts.find((s: any) => !s.ends_at);
  $("view").innerHTML =
    `<div class="toolbar">${!current ? button("shift-start", "Начать смену", true) : button("shift-pause", current.state === "break" ? "Продолжить" : "Перерыв") + button("shift-finish", "Завершить смену", true)}</div><p class="muted">${esc(data.meaning)}</p>` +
    table(
      [
        "Дата смены",
        "Начало",
        "Окончание",
        "Перерывы",
        "Заявленная длительность",
      ],
      data.shifts.map(
        (s: any) =>
          `<tr><td>${esc(s.workday)}</td><td>${date(s.starts_at)}</td><td>${s.unclosed ? "Не закрыта" : date(s.ends_at)}</td><td>${Math.floor(s.break_seconds / 60)} мин.</td><td>${Math.floor(s.declared_seconds / 3600)} ч. ${Math.floor(s.declared_seconds / 60) % 60} мин.</td></tr>`,
      ),
    );
  const send = async (action: string) => {
    await request("POST", "shift", {
      operation_id: crypto.randomUUID(),
      action,
      ...(current
        ? { shift_id: current.shift_id, revision: current.revision }
        : {}),
    });
    await shift();
  };
  bind("shift-start", () => send("start"));
  bind("shift-pause", () =>
    send(current.state === "break" ? "resume" : "break"),
  );
  bind("shift-finish", () => send("finish"));
  const today=new Date().toLocaleDateString('sv-SE');
  $('view').insertAdjacentHTML('beforeend',`<h2>Отчёт по сменам</h2><div class="toolbar"><label>С <input id="shift-since" type="date" value="${today.slice(0,8)+'01'}"></label><label>По <input id="shift-until" type="date" value="${today}"></label>${button('shift-report-show','Показать')}${button('shift-report-export','Сохранить Excel')}</div><div id="shift-report-result"></div>`);
  let report:any;
  const reportShow=async()=>{
    report=await request('GET','shift-report',undefined,{since:$<HTMLInputElement>('shift-since').value,until:$<HTMLInputElement>('shift-until').value});
    $('shift-report-result').innerHTML=`<p class="muted">${esc(report.meaning)} Часовой пояс: ${esc(report.timezone)}.</p>`+table(['Сотрудник','Смен','Работа','Перерывы','Не закрыто'],report.employees.map((row:any)=>`<tr><td>${esc(row.employee)}</td><td>${row.shifts}</td><td>${Math.floor(row.declared_seconds/3600)} ч. ${Math.floor(row.declared_seconds/60)%60} мин.</td><td>${Math.floor(row.break_seconds/60)} мин.</td><td>${row.unclosed}</td></tr>`));
  };
  bind('shift-report-show',reportShow);
  bind('shift-report-export',async()=>{await reportShow();const result=await crm.workbook('shift-report',{report});if(!result.ok&&!result.cancelled)throw new Error(result.text);});
  await reportShow();
}

function scriptMarkup(data: any) {
  return data.script
    ? `<h3>${esc(data.script.title)}</h3><p class="muted">Версия ${data.published_revision} · ${date(data.published_at)}</p>${data.script.steps.map((s: any, i: number) => `<article class="script-step"><h3>${i + 1}. ${esc(s.title)}</h3><p>${esc(s.text)}</p><button type="button" data-script-used>Шаг использован</button></article>`).join("")}`
    : '<div class="empty">Руководитель ещё не опубликовал скрипт</div>';
}
async function script() {
  const data = await request("GET", "script");
  scriptOpened = data;
  telemetry("script_shown", "script");
  $("view").innerHTML =
    scriptMarkup(data) +
    (data.can_edit
      ? `<div class="actions">${button("script-edit", "Редактировать черновик")}${button("script-publish", "Опубликовать черновик", true)}${button("script-withdraw", "Снять публикацию")}</div>`
      : "");
  bind("script-publish", async () => {
    await request("POST", "script", {
      operation_id: crypto.randomUUID(),
      action: "publish",
      base_revision: data.revision,
    });
    await script();
  });
  bind("script-withdraw", async () => {
    await request("POST", "script", {
      operation_id: crypto.randomUUID(),
      action: "withdraw",
      base_revision: data.revision,
    });
    await script();
  });
  bind("script-edit", () => scriptEditor(data));
}
function scriptEditor(data: any) {
  const dialog = $<HTMLDialogElement>("dialog");
  const content = data.draft ||
    data.script || { title: "", steps: [{ title: "", text: "" }] };
  dialog.innerHTML = `<form id="script-form"><h2>Черновик скрипта</h2><div class="field"><label>Название</label><input id="script-title" maxlength="100" value="${esc(content.title)}"></div><div id="script-steps">${content.steps.map((s: any) => stepEditor(s)).join("")}</div>${button("script-add", "Добавить шаг")}<div class="error" id="script-error"></div><div class="actions">${button("script-close", "Закрыть")}<button class="primary" type="submit">Сохранить черновик</button></div></form>`;
  $("script-add").onclick = (e) => {
    e.preventDefault();
    if (dialog.querySelectorAll("[data-script-step]").length < 12)
      $("script-steps").insertAdjacentHTML(
        "beforeend",
        stepEditor({ title: "", text: "" }),
      );
  };
  $("script-close").onclick = (e) => {
    e.preventDefault();
    dialog.close();
  };
  $("script-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const content = {
        title: $<HTMLInputElement>("script-title").value,
        steps: Array.from(dialog.querySelectorAll("[data-script-step]")).map(
          (el) => ({
            title: (el.querySelector("input") as HTMLInputElement).value,
            text: (el.querySelector("textarea") as HTMLTextAreaElement).value,
          }),
        ),
      };
      await request("POST", "script", {
        operation_id: crypto.randomUUID(),
        action: "save",
        base_revision: data.revision,
        content,
      });
      dialog.close();
      await script();
    } catch (error) {
      $("script-error").textContent = (error as Error).message;
    }
  };
  dialog.showModal();
}
function stepEditor(s: any) {
  return `<div class="field script-step" data-script-step><label>Шаг</label><input maxlength="80" value="${esc(s.title)}"><textarea maxlength="6000">${esc(s.text)}</textarea></div>`;
}

async function exchangePage() {
  const period = new Date().toISOString().slice(0, 7);
  const target = state.connection.shared
    ? '<select id="target"><option value="general:pilot">Общий реестр пилота</option></select>'
    : `<input id="target" value="${state.connection.mode === "synthetic" ? "general:demo" : ""}" placeholder="Идентификатор согласованного реестра">`;
  $("view").innerHTML =
    `<div class="grid"><div class="field"><label>Период</label><input id="period" type="month" value="${period}"></div><div class="field"><label>Реестр</label>${target}</div></div><div class="toolbar" style="margin-top:20px">${button("export-new", "Создать реестр", true)}${button("export-template", "Создать копию существующей книги")}${button("import-file", "Сверить изменения Excel")}</div><div id="exchange-result"></div><p class="muted">Пустая ячейка не стирает данные. Для явной очистки введите [[CLEAR]]. Исходная книга сохраняется.</p>`;
  const write = async (use_template: boolean) => {
    const snapshot = await request("POST", "export", {
      operation_id: crypto.randomUUID(),
      period: $<HTMLInputElement>("period").value,
      target_id: $<HTMLInputElement>("target").value,
    });
    const result = await crm.workbook("write", { snapshot, use_template });
    if (!result.ok) {
      if (!result.cancelled) throw new Error(result.text);
      return;
    }
    const receiptText =
      "Квитанция сохранена в защищённой очереди. Статус подтверждения — в разделе «Очередь».";
    $("exchange-result").innerHTML =
      `<article class="record"><h3>Книга записана и повторно прочитана</h3><p>${esc(result.data.path)}</p><p>${esc(receiptText)}</p></article>`;
  };
  bind("export-new", () => write(false));
  bind("export-template", () => write(true));
  bind("import-file", async () => {
    const selected = await crm.workbook("read");
    if (!selected.ok) {
      if (!selected.cancelled) throw new Error(selected.text);
      return;
    }
    const { filename, ...input } = selected.data;
    const plan = await request("POST", "import-preview", input);
    $("exchange-result").innerHTML =
      `<h2>Сверка: ${esc(filename)}</h2><p class="muted">Изменений: ${plan.changes.length} · Конфликтов: ${plan.conflicts} · Отсутствующих строк: ${plan.missing_rows} (без снятия назначений)</p>` +
      table(
        ["Поле", "Исходное", "Сейчас на сервере", "Из Excel", "Решение"],
        plan.changes.map(
          (c: any, i: number) =>
            `<tr><td>${esc(context.fields.find((f: any) => f.key === c.key)?.title || c.key)}</td><td>${esc(c.baseline ?? "—")}</td><td>${esc(c.current ?? "—")}</td><td>${esc(c.incoming ?? "Очистить")}</td><td>${c.conflict ? `<select data-resolution="${i}"><option value="">Выберите</option><option value="server">Оставить сервер</option><option value="excel">Применить Excel</option></select>` : "Применить"}</td></tr>`,
        ),
      ) +
      `<div class="actions">${button("apply-import", "Применить сверенные изменения", true)}</div>`;
    bind("apply-import", async () => {
      const resolutions: any = {};
      for (const el of Array.from(
        document.querySelectorAll<HTMLSelectElement>("[data-resolution]"),
      )) {
        if (!el.value) throw new Error("Выберите решение каждого конфликта.");
        const c = plan.changes[Number(el.dataset.resolution)];
        resolutions[c.contract_uuid + ":" + c.key] = el.value;
      }
      const result = await request("POST", "import-apply", {
        operation_id: crypto.randomUUID(),
        preview_id: plan.preview_id,
        file_sha256: input.file_sha256,
        resolutions,
      });
      $("exchange-result").innerHTML =
        `<article class="record"><h3>Импорт подтверждён</h3><p>Строк: ${result.changed_rows} · Полей: ${result.changed_cells}</p></article>`;
    });
  });
}

async function adoption() {
  if (!context.cohorts?.length) {
    $("view").innerHTML =
      '<div class="empty">Группа внедрения ещё не задана. Состав и плановые смены загружаются из подтверждённого кадрового источника.</div>';
    return;
  }
  const until = new Date(),
    since = new Date(Date.now() - 28 * 86400000);
  $("view").innerHTML =
    `<div class="toolbar"><select id="cohort">${context.cohorts.map((c: any) => `<option value="${c.cohort_id}">${esc(c.name)}</option>`).join("")}</select><input id="since" type="date" value="${since.toISOString().slice(0, 10)}"><input id="until" type="date" value="${until.toISOString().slice(0, 10)}"><select id="filter-role"><option value="">Все роли</option><option value="employee">Сотрудники</option><option value="manager">Руководители</option><option value="observer">Наблюдатели</option></select><input id="filter-team" placeholder="ID команды"><select id="filter-feature"><option value="">Все функции</option><option value="call">Звонки</option><option value="visit">Выезды</option><option value="task">Задачи</option></select><input id="filter-version" placeholder="Версия приложения">${button("adoption-refresh", "Обновить")}</div><div id="adoption-report"></div>`;
  const load = async () => {
    const report = await request("POST", "adoption", {
      cohort_id: $<HTMLSelectElement>("cohort").value,
      role: $<HTMLSelectElement>("filter-role").value,
      team_id: $<HTMLInputElement>("filter-team").value,
      feature: $<HTMLSelectElement>("filter-feature").value,
      version: $<HTMLInputElement>("filter-version").value,
      since: $<HTMLInputElement>("since").value + "T00:00:00Z",
      until: new Date(
        new Date($<HTMLInputElement>("until").value + "T00:00:00Z").getTime() +
          86400000,
      ).toISOString(),
    });
    const metric = (title: string, value: any) =>
      `<div class="metric">${title}<strong>${value.value == null ? "—" : Math.round(value.value * 100) + "%"}</strong><small>${value.numerator} / ${value.denominator}</small></div>`;
    $("adoption-report").innerHTML =
      `<p class="muted">Данные на ${date(report.as_of)} · ${esc(report.completeness)}</p><div class="summary">${metric("Активация", report.activation)}${metric("Регулярность по сменам", report.regularity)}${metric("Доставка в реестры", report.delivery)}${metric("Диагностическое покрытие", report.coverage)}</div><p class="muted">График не задан: ${report.counts.unknown} · Первая смена впереди: ${report.counts.not_started} · Просроченная доставка: ${report.delivery.overdue} · Без срока доставки: ${report.delivery.without_sla}</p><h2>Рабочие дни</h2><div class="trend">${report.trend.map((d: any) => `<div style="height:${Math.max(3, d.planned ? (100 * d.active) / d.planned : 3)}%" title="${esc(d.workday)}: ${d.active}/${d.planned}"></div>`).join("")}</div>` +
      table(
        [
          "Сотрудник",
          "Плановые / активные смены",
          "Последняя работа",
          "Версии",
          "Ожидает реестра",
          "Полнота",
        ],
        report.employees.map(
          (e: any) =>
            `<tr><td>${esc(e.name)}</td><td>${e.planned_shifts} / ${e.active_shifts}</td><td>${date(e.last_work)}</td><td>${esc(e.versions.join(", ") || "Нет данных")}</td><td>${e.pending_delivery}</td><td>${esc(e.reason || "Данные получены")}</td></tr>`,
        ),
      ) +
      `<h2 style="margin-top:28px">Зафиксированная работа</h2>` +
      table(
        ["Вид", "Через приложение", "Доля"],
        Object.entries(report.origins).map(
          ([kind, v]: any) =>
            `<tr><td>${esc(labels[kind] || kind)}</td><td>${v.counts.app}</td><td>${v.app_share?.value != null ? Math.round(v.app_share.value * 100) + "%" : esc(v.reason)}</td></tr>`,
        ),
      ) +
      `<p class="muted">Метрики версии ${report.metric_version}. Целевые пороги: ${Object.keys(report.cohort.thresholds).length ? "заданы для группы" : "не заданы"}. Диагностика и заявленная длительность смены не являются табелем.</p>`;
    telemetry("report_viewed", "adoption");
  };
  bind("adoption-refresh", load);
  await load();
}

async function connection() {
  state = await crm.state();
  const savedLogins = state.existingLogins || [{source:'manager',label:'Руководитель',available:state.existingManager?.available}];
  const available = savedLogins.filter((entry:any)=>entry.available);
  const current = state.context ? `<h2>Текущее подключение</h2><p>${state.connection.mode==='synthetic'?'Проверочная база · синтетические данные':esc(state.connection.server)}</p>${button('connection-continue','Открыть портфель',true)}` : '';
  $("view").innerHTML =
    `<div class="help connection-page">${current}<h2>Сохранённый вход</h2><p>${available.length?'Выберите вход из установленной рабочей программы. Сервер подтвердит личность и права.':'Сохранённый вход не найден. Войдите в установленную рабочую программу и повторите проверку.'}</p><div class="actions">${available.map((entry:any)=>`<button ${entry.source==='manager'?'id="existing-manager-connect" ':''}data-existing-source="${esc(entry.source)}" class="primary">${esc(entry.label)}</button>`).join('')}${button('connection-refresh','Проверить снова')}</div>${state.connectionNotice ? `<p role="status">${esc(state.connectionNotice)}</p>` : ''}<details class="connection-testing"><summary>Проверочная среда</summary><h2>Синтетические данные</h2><p>Отдельная база на этом компьютере. Результаты не попадают в рабочий портфель.</p><div class="toolbar"><select id="demo-role" aria-label="Роль в проверочной базе"><option value="employee">Сотрудник</option><option value="manager">Руководитель</option><option value="observer">Наблюдатель другой команды</option><option value="courier">Курьер</option><option value="analyst">Аналитик</option></select>${button('demo-connect','Открыть сценарий')}</div><label class="check"><input id="offline" type="checkbox"${state.offline?' checked':''}>Приостановить отправку для проверки офлайн</label><h2>Отдельный сервер</h2><div class="grid"><div class="field span"><label for="server">HTTPS-адрес</label><input id="server" placeholder="Адрес проверочного сервера"></div><div class="field"><label for="remote-role">Роль входа</label><select id="remote-role"><option value="employee">Сотрудник</option><option value="manager">Руководитель</option></select></div><div class="field"><label for="login">Логин</label><input id="login" autocomplete="username"></div><div class="field"><label for="password">Пароль</label><input id="password" type="password" autocomplete="current-password"></div></div><div class="actions">${button('remote-connect','Войти')}</div></details></div>`;
  bind('connection-continue',()=>go('portfolio'));
  $('view').insertAdjacentHTML('afterbegin',`<section class="diagnostics"><h2>Диагностика подключения</h2><div class="actions">${button('diagnostic-run','Проверить и восстановить')}${button('diagnostic-queue','Очередь и сверка')}</div><div id="diagnostic-result" role="status"></div></section>`);
  const diagnose=async(repair=false)=>{
    if(!crm.diagnostics)return;
    const generation=renderGeneration,button=$<HTMLButtonElement>('diagnostic-run');button.disabled=true;
    try{
      const result=await crm.diagnostics(repair);
      if(generation!==renderGeneration || section!=='connection')return;
      if(!result.ok)throw Error(result.text || 'Диагностика не завершилась.');
      $('diagnostic-result').innerHTML=table(['Проверка','Результат'],result.data.checks.map((c:any)=>`<tr><td>${esc(({main:'Запуск',renderer:'Интерфейс',disk:'Диск',profile:'Защищённый профиль',helper:'Локальный компонент',network:'Сеть и API',identity:'Вход и права',queue:'Сохранение и очередь'} as any)[c.stage] || c.stage)}<br><small>${esc(c.code)}</small></td><td>${c.ok?'✓':'—'} ${esc(c.text)}</td></tr>`));
    }finally{if(generation===renderGeneration)button.disabled=false;}
  };
  $('diagnostic-run').insertAdjacentHTML('afterend',button('diagnostic-report','Сохранить отчёт'));
  bind('diagnostic-report',async()=>{const result=await crm.diagnosticsReport?.();if(result && !result.ok)throw Error(result.text);});
  bind('diagnostic-run',()=>diagnose(true));bind('diagnostic-queue',()=>go('queue'));
  void diagnose().catch(e=>notice(e.message));
  bind('connection-refresh',()=>connection());
  $('view').querySelectorAll<HTMLButtonElement>('[data-existing-source]').forEach(element=>{
    bind(element.id || (element.id='saved-login-'+element.dataset.existingSource),async()=>{
      const buttons=$('view').querySelectorAll<HTMLButtonElement>('button');
      buttons.forEach(button=>button.disabled=true);
      try {
        const result=await crm.connect({action:'existing-login',source:element.dataset.existingSource});
        if(!result.ok)throw new Error(result.text || 'Не удалось подтвердить вход.');
        section='portfolio';await boot();
      }finally{buttons.forEach(button=>button.disabled=false);}
    });
  });
  $<HTMLSelectElement>("demo-role").value = state.connection.role;
  $<HTMLInputElement>("offline").onchange = async () => {
    await crm.connect({
      action: "offline",
      value: $<HTMLInputElement>("offline").checked,
    });
    state = await crm.state();
    $("connection").textContent = state.offline
      ? "Проверка офлайн: отправка приостановлена"
      : "Связь включена";
  };
  bind("demo-connect", async () => {
    const r = await crm.connect({
      mode: "synthetic",
      role: $<HTMLSelectElement>("demo-role").value,
    });
    if (!r.ok) throw new Error(r.text);
    telemetry("login_ok", "shell");
    section = "portfolio";
    await boot();
  });
  bind("remote-connect", async () => {
    const r = await crm.connect({
      mode: "remote",
      role: $<HTMLSelectElement>("remote-role").value,
      server: $<HTMLInputElement>("server").value,
      username: $<HTMLInputElement>("login").value,
      password: $<HTMLInputElement>("password").value,
    });
    $<HTMLInputElement>("password").value = "";
    if (!r.ok) throw new Error(r.text);
    telemetry("login_ok", "shell");
    section = "portfolio";
    await boot();
  });
}
async function help() {
  $("view").innerHTML =
    `<div class="help"><h2>Сохранение и обмен</h2><p>Звонки, выезды и остальные действия сначала сохраняются в зашифрованной очереди на этом компьютере. Подтверждение сервера видно в очереди. При изменении назначения исходная запись остаётся для сверки.</p><p>Устаревшее действие сохраняется в истории. Оно не затирает более свежий результат карточки. Исправление оформляется новым событием с причиной.</p><p>Excel получает версию выгрузки и внутренние ID договоров. Ручные изменения проходят сверку с исходной версией и текущим сервером. Пропущенная строка не снимает назначение. Исходная книга сохраняется, запись и чтение новой копии проверяются.</p><h2>Смена и диагностика</h2><p>Начало, перерыв и окончание смены фиксируются серверным временем. Это заявленная длительность, а не расчёт оплаты.</p><p>Для панели внедрения учитываются принятые рабочие действия, заданные плановые смены, квитанции доставки и ограниченные технические события: открытие функций, готовность экрана, коды ошибок и версия. Тексты заметок, контакты клиентов, нажатия клавиш, снимки и геолокация в диагностику не передаются.</p><p>Сырые технические события можно хранить 30–365 дней по настройке владельца. Предлагается 90 дней; автоматическая очистка до выбора политики отключена. Бизнес-история хранится отдельно.</p></div>`;
}
document.addEventListener("click", (e) => {
  const target = (e.target as HTMLElement).closest(
    "[data-script-used]",
  ) as HTMLButtonElement | null;
  if (target && !target.disabled) {
    telemetry("script_step_used", "script");
    target.disabled = true;
    target.textContent = "Использован";
  }
});
document
  .querySelectorAll<HTMLButtonElement>("[data-win]")
  .forEach((b) => (b.onclick = () => crm.win(b.dataset.win!)));
$('dialog').addEventListener('close',()=>scheduleSync());
crm.onChange?.(event=>{
  if(event.type==='identity'){
    clearTimeout(cardRetryTimer);
    renderGeneration++;activePerson='';cardSyncGeneration++;card=null;context=null;
    $<HTMLDialogElement>('dialog').close();
    void boot().catch(error=>notice(error.message));
  }else if(event.type==='live'){
    const environment=context?.distribution?.environment==='isolated'?'Изолированный стенд · ':state?.connection?.mode==='synthetic'?'Синтетические данные · ':'';
    $('connection').textContent=environment+(event.connected?'Подтверждённая связь · '+date(event.as_of):'Связь не подтверждена · ввод сохранён на компьютере');
    if(event.dirty){
      if(activePerson)scheduleSync(true);
      else if(section==='portfolio'&&!context.distribution?.enabled)void portfolio().catch(error=>notice(error.message));
      else if(section==='overview')void managerOverview().catch(error=>notice(error.message));
      else if(section==='manager-statistics')void managerStatistics().catch(error=>notice(error.message));
    }
  }else scheduleSync();
});
window.addEventListener('focus',()=>{if(activePerson)scheduleSync(true);});
void boot().catch((e) => notice(e.message));
