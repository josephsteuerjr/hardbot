import { api, hard } from '../kit/api';
import { editDatesHtml } from './edit-dates';
import { esc, icon, lightbox, n, toast } from '../kit/dom';
import { go, hasSection, setHead, type Section } from '../kit/shell';
import { DataTable } from '../kit/table';
import { evidenceTag, loadPhotos, openPerson, problem, staleNote, type CaseReference } from './common';

const scope = { profile: 'hard', employee: '', office: '', status: '', q: '' };
const pages: Record<string, number> = {};
let scopeOwner = '';
const ownerScopes = new Map<string, { scope: typeof scope; pages: Record<string, number> }>();
const lanes = [
  { id: 'new', title: 'Не обработаны' }, { id: 'work', title: 'В работе' },
  { id: 'promise', title: 'Договорённости' }, { id: 'paid', title: 'Результат «Оплачен»' },
  { id: 'other', title: 'Другие результаты' },
];
const tabs = [['overview', 'Обзор'], ['contracts', 'Договоры'], ['history', 'История работы'],
  ['payments', 'Оплаты'], ['evidence', 'Проверки и файлы']];
let generation = 0;

function money(value: unknown, unit = ''): string {
  if (value === null || value === undefined || value === '') return 'Сумма не указана';
  const amount = Number(value);
  return `${Number.isFinite(amount) ? amount.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : String(value)} ${unit}`.trim();
}
const debts = (row: any) => row.totals?.length ? row.totals.map((a: any) => money(a.sum, a.unit)).join(' · ') : 'Сумма не указана';
function dateText(value: unknown): string {
  if (!value) return '—';
  const text = String(value), match = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : text;
}
function timeText(value: unknown): string {
  if (!value) return '';
  const raw = String(value);if(/^\d{4}-\d{2}-\d{2}$/.test(raw))return dateText(raw);
  const date = new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(raw) ? raw : raw + 'Z');
  return Number.isFinite(date.getTime()) ? date.toLocaleString('ru-RU', { timeZone: 'Asia/Dushanbe', dateStyle: 'short', timeStyle: 'short' }) : raw;
}
function lane(row: any): string {
  const status = String(row.status || '');
  if (!status || status === 'Не обработан' || status === '—') return 'new';
  if (status.startsWith('Оплачен')) return 'paid';
  if (/Обещает оплатить|Рассрочка|Встреча в офисе/.test(status)) return 'promise';
  if (/В работе|Нет контакта|Отказ от оплаты|Передана инф|Доп. Обогащение/.test(status)) return 'work';
  return 'other';
}
const officeName = (value: string) => !value || value === '!none' ? 'Без офиса' : value;
const reference = (row: any): CaseReference => ({ employee: row.employee, rep: row.rep, profile: scope.profile });

async function portfolio(root: HTMLElement, params: any) {
  const seq = ++generation;
  const shell = await hard.state();
  if (!root.isConnected || seq !== generation) return;
  const owner = JSON.stringify([shell.server, shell.device?.id]);
  if (scopeOwner !== owner) {
    if (scopeOwner) ownerScopes.set(scopeOwner, { scope: { ...scope }, pages: { ...pages } });
    const saved = ownerScopes.get(owner);
    Object.assign(scope, saved?.scope || { profile: 'hard', employee: '', office: '', status: '', q: '' });
    Object.keys(pages).forEach(key => delete pages[key]);
    Object.assign(pages, saved?.pages || {});
    scopeOwner = owner;
  }
  if (params.profile === 'hard' || params.profile === 'soft') scope.profile = params.profile;
  if (params.employee !== undefined) scope.employee = params.employee;
  setHead('CRM', 'дела команды · результаты, договорённости и история работы');
  root.classList.add('manager-crm');root.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  const r = await api('/api/manager/portfolio', { mode: scope.profile });
  if (!root.isConnected || seq !== generation) return;
  if (!r.ok) { root.innerHTML = problem(r.text || 'Дела не загрузились') + '<button class="btn" data-retry>Повторить</button>'; root.querySelector('[data-retry]')?.addEventListener('click', () => void portfolio(root, params)); return; }
  const d = r.data, rows: any[] = (d.items || []).map((row:any)=>({...row,office:row.office || '!none'}));
  const values = (key: string) => [...new Set<string>(rows.map(row => String(row[key] || '')))].sort();
  if (scope.employee && !values('employee').includes(scope.employee)) scope.employee = '';
  if (scope.office && !values('office').includes(scope.office)) scope.office = '';
  if (scope.status && !values('status').includes(scope.status)) scope.status = '';
  const select = (key: 'employee'|'office'|'status', label: string, all: string) => `<label>${label}<select class="input" data-crm-filter="${key}"><option value="">${all}</option>${values(key).map(value => `<option value="${esc(value)}" ${scope[key] === value ? 'selected' : ''}>${esc(key === 'office' ? officeName(value) : value || 'Не обработан')}</option>`).join('')}</select></label>`;
  root.innerHTML = `${staleNote(r.stale)}<div class="mcrm-toolbar"><div class="chips">${['hard','soft'].map(profile => `<button class="chip" data-crm-profile="${profile}" aria-pressed="${scope.profile === profile}">${profile === 'hard' ? 'Хард' : 'Софт'}</button>`).join('')}</div><span class="spacer"></span><button class="btn small" data-crm-table>${icon('registry')}Таблица портфеля</button><button class="btn small" data-crm-refresh>${icon('refresh')}Обновить</button></div>
    <div class="mcrm-scope"><label class="mcrm-search">${icon('people')}<input class="input" data-crm-search placeholder="ФИО, КД, телефон, адрес" value="${esc(scope.q)}" aria-label="Поиск дел"></label>${select('employee','Сотрудник','Вся команда')}${select('office','Офис','Все офисы')}${select('status','Результат','Все результаты')}<button class="link" data-crm-reset>Сбросить отбор</button></div>
    <div class="mcrm-caption"><span>Текущие закрепления на ${esc(dateText(d.asof))}</span><span data-crm-count></span></div><div data-crm-summary></div><div class="mcrm-lanes" data-crm-board></div>`;
  let filtered: any[] = [];
  const draw = () => {
    const q = scope.q.trim().toLocaleLowerCase('ru-RU');
    filtered = rows.filter(row => (!scope.employee || row.employee === scope.employee) && (!scope.office || row.office === scope.office) && (!scope.status || row.status === scope.status) &&
      (!q || [row.client, row.rep, row.employee, row.address, ...(row.ids || []), ...(row.tels || []), ...(row.partners || [])].join(' ').toLocaleLowerCase('ru-RU').includes(q)));
    root.querySelector('[data-crm-count]')!.textContent = `${filtered.length} из ${rows.length} дел`;
    const late = filtered.filter(row => row.promise_date && String(row.promise_date).slice(0,10) < d.asof);
    root.querySelector('[data-crm-summary]')!.innerHTML = `<div class="mcrm-summary"><div><b>${n(filtered.length)}</b><span>дел в отборе</span></div><div><b>${n(filtered.reduce((sum,row) => sum + row.n, 0))}</b><span>договоров</span></div><div><b>${n(filtered.filter(row => lane(row) === 'new').length)}</b><span>без результата</span></div><div><b>${n(late.length)}</b><span>срок обещания прошёл</span></div></div>`;
    root.querySelector('[data-crm-board]')!.innerHTML = lanes.map(group => {
      const selected = filtered.filter(row => lane(row) === group.id), size = 15;
      const page = Math.min(pages[group.id] || 0, Math.max(0, Math.ceil(selected.length / size) - 1)); pages[group.id] = page;
      return `<section class="mcrm-lane" data-lane="${group.id}"><header><h2>${esc(group.title)}</h2><span>${n(selected.length)}</span></header><div class="mcrm-lane-body">${selected.slice(page*size,(page+1)*size).map(row => `<button class="mcrm-case" data-crm-case="${esc(JSON.stringify([row.employee,row.rep]))}"><span class="mcrm-case-person">${esc(row.client || row.rep)}</span><span class="mcrm-case-debt">${esc(debts(row))}</span><span class="mcrm-case-owner">${esc(row.employee)}</span><span class="mcrm-case-status">${esc(row.status || 'Не обработан')}</span><span class="mcrm-case-foot">${n(row.n)} КД${row.next_date ? ' · следующее действие '+esc(dateText(row.next_date)) : row.promise_date ? ' · обещание до '+esc(dateText(row.promise_date)) : ''}</span></button>`).join('') || '<div class="mcrm-empty">В этом отборе дел нет</div>'}</div>${selected.length>size ? `<footer><button class="btn small" data-lane-page="${group.id}" data-delta="-1" ${page ? '' : 'disabled'}>←</button><span>${page*size+1}–${Math.min((page+1)*size,selected.length)}</span><button class="btn small" data-lane-page="${group.id}" data-delta="1" ${(page+1)*size>=selected.length?'disabled':''}>→</button></footer>` : ''}</section>`;
    }).join('');
    root.querySelectorAll<HTMLElement>('[data-crm-case]').forEach(button => button.addEventListener('click', () => {
      const row = filtered.find(item => JSON.stringify([item.employee,item.rep]) === button.dataset.crmCase);
      if (row) void openPerson(row.employee,row.rep,undefined,scope.profile,{cases:filtered.map(reference)});
    }));
    root.querySelectorAll<HTMLButtonElement>('[data-lane-page]').forEach(button => button.addEventListener('click', () => { pages[button.dataset.lanePage!] += Number(button.dataset.delta); draw(); }));
  };
  root.querySelector<HTMLInputElement>('[data-crm-search]')!.addEventListener('input', event => { scope.q = (event.target as HTMLInputElement).value; Object.keys(pages).forEach(key => pages[key]=0); draw(); });
  root.querySelectorAll<HTMLSelectElement>('[data-crm-filter]').forEach(select => select.addEventListener('change', () => { scope[select.dataset.crmFilter as 'employee'|'office'|'status'] = select.value; draw(); }));
  root.querySelectorAll<HTMLButtonElement>('[data-crm-profile]').forEach(button => button.addEventListener('click', () => { Object.assign(scope,{profile:button.dataset.crmProfile!,employee:'',office:'',status:''}); void portfolio(root,{}); }));
  root.querySelector('[data-crm-reset]')?.addEventListener('click', () => { Object.assign(scope,{employee:'',office:'',status:'',q:''}); void portfolio(root,{}); });
  root.querySelector('[data-crm-refresh]')?.addEventListener('click', () => void portfolio(root,{}));
  root.querySelector('[data-crm-table]')?.addEventListener('click', () => void go('team-portfolio',{profile:scope.profile,employee:scope.employee}));
  draw();
}

const empty = (text: string) => `<div class="mcrm-empty">${esc(text)}</div>`;
const block = (title: string, content: string) => `<section class="mcrm-block"><h2>${esc(title)}</h2>${content}</section>`;
const facts = (rows: [string,unknown][]) => `<dl class="mcrm-facts">${rows.filter(([,value]) => value !== undefined && value !== null && value !== '').map(([title,value]) => `<dt>${esc(title)}</dt><dd>${esc(value)}</dd>`).join('')}</dl>`;
function history(d: any): any[] {
  const visit = d.profile!=='soft' && d.dates?.visit_date ? [{kind:'Последний выезд',at:d.dates.visit_date,author:'',text:(d.visit || []).filter((field:any)=>field.value!=null&&field.value!=='').map((field:any)=>field.title+': '+field.value).join('\n')}] : [];
  return [...visit,...(d.calls || []).map((event:any) => ({kind:'Звонок Софта',at:event.day,author:event.employee || d.employee,text:event.payload})),
    ...(d.office_history || []).map((event:any) => ({kind:event.title || 'Офис Харда',at:event.occurred_at || event.at || event.day,author:event.employee,text:[event.values.result,event.values.note,event.values.contact,event.values.address,event.values.amount ? money(event.values.amount,event.currency) : '',event.values.promise_date ? 'Обещание до '+dateText(event.values.promise_date) : '',event.values.next_date ? 'Следующее действие '+dateText(event.values.next_date) : ''].filter(Boolean).join('\n')}))]
    .sort((a,b) => String(b.at).localeCompare(String(a.at)));
}
const eventsHtml = (events: any[]) => events.map(event => `<article class="mcrm-event"><div><span class="tag">${esc(event.kind)}</span><time>${esc(timeText(event.at))}</time></div><p>${esc(event.text || 'Результат сохранён')}</p><span>${esc(event.author || '')}</span></article>`).join('');

async function caseWorkspace(root: HTMLElement, params: any) {
  const seq = ++generation;
  root.classList.add('manager-crm','mcrm-case-workspace');
  setHead('Дело', params.employee || '');root.innerHTML='<div class="skeleton"></div><div class="skeleton"></div>';
  const r = await api('/api/manager/person',{employee:params.employee,rep:params.rep,date:params.date,mode:params.profile,current_portfolio:params.currentPortfolio?'1':undefined});
  if(!root.isConnected || seq!==generation)return;
  const back = () => { const origin=params.origin; void go(origin?.id && hasSection(origin.id) ? origin.id : 'crm', origin?.params || {}); };
  if(!r.ok){root.innerHTML=`<button class="btn small" data-case-back>← Вернуться к списку</button>${problem(r.text || 'Дело не открылось')}<button class="btn" data-case-retry>Повторить</button>`;root.querySelector('[data-case-back]')?.addEventListener('click',back);root.querySelector('[data-case-retry]')?.addEventListener('click',()=>void caseWorkspace(root,params));return;}
  const d=r.data, profile=d.profile==='soft'?'soft':'hard';params.profile=profile;
  if(!Array.isArray(params.cases)){
    const list=await api('/api/manager/portfolio',{mode:profile,employee:params.employee});
    if(!root.isConnected || seq!==generation)return;
    params.cases=(list.data?.items || []).map((row:any)=>({employee:row.employee,rep:row.rep,profile}));
  }
  const cases:CaseReference[]=params.cases,index=cases.findIndex(row=>row.employee===params.employee&&row.rep===params.rep);
  const move=(delta:number)=>{const next=cases[index+delta];if(next)void go('crm',{...params,...next,date:undefined,currentPortfolio:true,tab:'overview'});};
  setHead(d.client || params.rep,`${params.employee} · ${profile==='soft'?'Софт':'Хард'} · ${params.rep}`);
  const events=history(d),currentPromises=d.current_promises || [], ev=d.evidence || {};
  root.innerHTML=`${staleNote(r.stale)}<div class="mcrm-case-toolbar"><button class="btn small" data-case-back>${icon('back')}Вернуться к списку</button><span class="muted">${index>=0?`Дело ${index+1} из ${cases.length}`:'Дело из истории'}</span><span class="spacer"></span><button class="btn small" data-case-previous ${index>0?'':'disabled'}>← Предыдущее</button><button class="btn small" data-case-next ${index>=0&&index+1<cases.length?'':'disabled'}>Следующее →</button><button class="btn small" data-case-report>${icon('report')}Отчёт команды</button></div>
    <header class="mcrm-case-hero"><div><span class="mcrm-eyebrow">Текущее состояние дела</span><h2>${esc(d.outcome || 'Не обработан')}</h2><p>${esc(d.address || 'Адрес не указан')}</p></div><div class="mcrm-case-amount"><span>Долг по доступным договорам</span><b>${esc(d.money_text || 'Сумма не указана')}</b><small>${n(d.contracts?.length || d.n || 0)} договоров · ${esc(d.partner || '')}</small></div></header>
    ${currentPromises.length ? `<div class="notice">${currentPromises.map((p:any)=>`Просроченное обещание Софта: до ${esc(dateText(p.promise_date))} · ${esc(p.money_text)} · автор ${esc(p.author)}; по реестру ${esc(money(p.received,p.unit))}`).join('<br>')}</div>`:''}
    <div class="mcrm-tabs" role="tablist" aria-label="Разделы дела">${tabs.map(([id,title])=>`<button role="tab" id="crm-tab-${id}" data-case-tab="${id}" aria-controls="crm-content-${id}">${title}</button>`).join('')}</div><div class="mcrm-tab-content" role="tabpanel" data-case-content></div>`;
  root.querySelector('[data-case-back]')?.addEventListener('click',back);
  root.querySelector('[data-case-previous]')?.addEventListener('click',()=>move(-1));root.querySelector('[data-case-next]')?.addEventListener('click',()=>move(1));
  root.querySelector('[data-case-report]')?.addEventListener('click',()=>void go('report',{returnTo:{id:'crm',params:{...params}}}));
  const content=root.querySelector<HTMLElement>('[data-case-content]')!;
  let historyPage=0;
  const draw=(tab:string)=>{
    params.tab=tabs.some(([id])=>id===tab)?tab:'overview';tab=params.tab;
    content.id='crm-content-'+tab;content.setAttribute('aria-labelledby','crm-tab-'+tab);
    root.querySelectorAll<HTMLButtonElement>('[data-case-tab]').forEach(button=>{const active=button.dataset.caseTab===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});
    if(tab==='overview'){
      const results=(d.visit || []).filter((field:any)=>field.value!==''&&field.value!==null&&field.value!==undefined);
      content.innerHTML=`${editDatesHtml(d)}<div class="mcrm-case-columns"><div>${block('Контакты и сведения',facts([['Телефоны',(d.phones || []).join(' · ') || 'Не указаны'],['Адрес',d.address || 'Не указан'],['Дата рождения',dateText(d.birth_date)],['Контрагенты',d.partner],['Сотрудник',params.employee],['Обещал до',d.dates?.promise_date?dateText(d.dates.promise_date):''],['Встреча',d.dates?.meeting_date?dateText(d.dates.meeting_date):'']]))}${block('Последний результат',results.length ? facts(results.map((field:any)=>[field.title,field.value])) : empty('Результат ещё не записан'))}${block('Комментарий',d.comment ? `<p class="mcrm-note">${esc(d.comment_display ?? d.comment)}</p>` : empty('Комментария пока нет'))}</div><div>${block('Последние события',events.length?eventsHtml(events.slice(0,5))+'<button class="btn small" data-more-history>Вся история</button>':empty('Работа по делу ещё не записана'))}${ev.level?block('Проверка выезда',`${evidenceTag(ev.level)}<p>${esc(ev.note || ev.title || '')}</p><button class="btn small" data-more-evidence>Подробнее и файлы</button>`):''}</div></div>`;
      content.querySelector('[data-more-history]')?.addEventListener('click',()=>draw('history'));content.querySelector('[data-more-evidence]')?.addEventListener('click',()=>draw('evidence'));
    }else if(tab==='contracts'){
      content.innerHTML=`<p class="muted">Договоры, доступные в этом деле. Платежи показаны из загруженного реестра.</p><div class="mcrm-case-grid"></div>`;
      new DataTable<any>(content.querySelector('.mcrm-case-grid')!,{id:'crm-case-contracts:'+params.employee+':'+params.rep,key:row=>row.contract_id,exportName:'Договоры дела',pageSize:50,empty:'Доступных договоров нет.',cols:[
        {key:'id',title:'Договор',value:row=>row.contract_id},{key:'partner',title:'Контрагент',value:row=>row.partner},
        {key:'debt',title:'Долг',num:true,value:row=>row.total,html:row=>esc(money(row.total,row.unit))},
        {key:'status',title:'Результат',value:row=>row.status || 'Не обработан'},
        {key:'date',title:'Последний платёж',value:row=>row.last_payment_date,html:row=>esc(dateText(row.last_payment_date))},
        {key:'last',title:'Сумма платежа',num:true,value:row=>row.last_payment_sum,html:row=>esc(money(row.last_payment_sum,row.unit))},
        {key:'current',title:'Текущие платежи',num:true,value:row=>row.current_payment,html:row=>esc(money(row.current_payment,row.unit))},
      ]}).setRows(d.contracts || []);
    }else if(tab==='history'){
      const visible=events.slice(historyPage*50,(historyPage+1)*50);
      content.innerHTML=block('История работы',visible.length?eventsHtml(visible):empty('Звонков и офисных действий пока нет'))+(events.length>50?`<div class="mcrm-case-toolbar"><button class="btn small" data-history-page="-1" ${historyPage?'':'disabled'}>Новее</button><span>${historyPage*50+1}–${Math.min((historyPage+1)*50,events.length)} из ${events.length}</span><button class="btn small" data-history-page="1" ${(historyPage+1)*50>=events.length?'disabled':''}>Раньше</button></div>`:'');
      content.querySelectorAll<HTMLButtonElement>('[data-history-page]').forEach(button=>button.addEventListener('click',()=>{historyPage+=Number(button.dataset.historyPage);draw('history');}));
    }else if(tab==='payments'){
      const paid=d.reported_payments || [];
      content.innerHTML=block('Записанные сотрудниками оплаты','<p class="muted">Запись сотрудника и выбранный результат требуют сверки с зачислением.</p>'+ (paid.length ? paid.slice().reverse().map((payment:any)=>`<article class="mcrm-payment"><b>${esc(money(payment.amount,payment.currency))}</b><span>КД ${esc(payment.contract_id)} · ${esc(dateText(payment.paid_at))}</span><span>${esc(payment.employee || '')}${payment.result_status?' · '+esc(payment.result_status):''}</span><small>Записано ${esc(timeText(payment.created_at))}</small></article>`).join(''):empty('Ручных оплат пока нет')))+block('Последние платежи из реестра',(d.contracts || []).map((contract:any)=>`<div class="list-row"><div class="grow"><b class="mono">${esc(contract.contract_id)}</b><div class="sub">${esc(dateText(contract.last_payment_date))} · ${esc(money(contract.last_payment_sum,contract.unit))}</div></div></div>`).join('') || empty('Доступных договоров нет'));
    }else{
      content.innerHTML=block('Проверка выезда',ev.level ? `${evidenceTag(ev.level)}<p>${esc(ev.note || '')}</p>${ev.rows?.length ? '<ul>'+ev.rows.map((text:string)=>`<li>${esc(text)}</li>`).join('')+'</ul>':''}` : empty(profile==='soft'?'Для звонков проверка выезда не применяется':'Данных проверки пока нет'))+
        block('Сверка чеков',(d.payment_checks || []).map((check:any)=>`<article class="mcrm-payment"><b>КД ${esc(check.contract_id)} · ${esc(dateText(check.date))}</b><span>Реестр: ${esc(money(check.registry_sum,check.currency))}</span><span>Чеки на эту дату: ${esc(money(check.receipt_sum,check.currency))}</span><span>Разница: ${esc(money(check.difference,check.currency))}</span></article>`).join('') || empty('Сопоставимых данных пока нет'))+
        block('Квитанции',(d.receipts || []).map((receipt:any)=>`<div class="list-row"><div class="grow"><b>${esc(money(receipt.amount,receipt.currency))}</b><div class="sub">${esc(dateText(receipt.paid_at))} · ${esc(receipt.employee || '')}</div>${receipt.payer_note?'<p>'+esc(receipt.payer_note)+'</p>':''}</div>${receipt.file?`<button class="btn small" data-crm-receipt="${esc(receipt.file)}">Открыть снимок</button>`:''}</div>`).join('') || empty('Квитанций пока нет'))+
        '<section class="mcrm-block" id="ph-block" hidden><h2>Фотографии</h2><div class="photos" id="ph"></div></section><p class="muted">Сверка касается последнего платежа. Наличие чека само по себе не подтверждает зачисление.</p>';
      content.querySelectorAll<HTMLButtonElement>('[data-crm-receipt]').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;try{const src=await hard.image('/api/manager/receipt_photo',{file:button.dataset.crmReceipt});if(!button.isConnected)return;if(src)lightbox(src);else toast('Снимок не открылся. Обновите дело и повторите.');}finally{if(button.isConnected)button.disabled=false;}}));
      if(profile!=='soft')void loadPhotos(params.rep,params.date,content);
    }
  };
  root.querySelectorAll<HTMLButtonElement>('[data-case-tab]').forEach(button=>{
    button.addEventListener('click',()=>draw(button.dataset.caseTab!));
    button.addEventListener('keydown',event=>{const index=tabs.findIndex(([id])=>id===button.dataset.caseTab);const next=event.key==='ArrowRight'?(index+1)%tabs.length:event.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:event.key==='Home'?0:event.key==='End'?tabs.length-1:-1;if(next>=0){event.preventDefault();draw(tabs[next][0]);root.querySelector<HTMLButtonElement>(`[data-case-tab="${tabs[next][0]}"]`)?.focus();}});
  });
  draw(params.tab || 'overview');
}

export const managerCRM:Section={id:'crm',title:'CRM',icon:'people',render(root,params){return params.rep ? caseWorkspace(root,params) : portfolio(root,params);},leave(){generation++;}};
