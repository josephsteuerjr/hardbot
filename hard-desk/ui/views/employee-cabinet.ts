import { api, setEmployeeProfile } from "../kit/api";
import { esc, n } from "../kit/dom";
import { setHead, type Section } from "../kit/shell";
import { openHardCard } from "./hard-card";
import { openSoftCard } from "./soft";
import { registryDate, registryMoney } from "./registry-detail";
type Profile="hard"|"soft";
const components:[string,string][]=[['debt_overdue','Просроченный долг'],['interest_overdue','Проценты'],['penalty','Неустойка / штрафы'],['duty','Госпошлина'],['dtp','Убытки ДТП'],['rent','Аренда'],['camera_fine','Штрафы с камер'],['dirt','Грязь'],['trash','Мусор'],['new_damage','Новые повреждения'],['evacuation','Эвакуация'],['peni','Пени'],['other','Прочее']];
const columns:[string,string][]=[['contract_id','Договор'],['client','Клиент'],['partner','Контрагент'],['pool','Пул'],['total','Текущий долг'],['dp','ДП'],['kd_date','Дата выдачи / оформления'],['issued_sum','Выданная сумма'],...components,['last_payment_date','Последний платёж: дата'],['last_payment_sum','Последний платёж: сумма'],['soft_status','Статус Софта'],['visit_date','Дата выезда'],['address','Адрес'],['phone','Телефон']];
const numeric=new Set(['total','issued_sum','last_payment_sum',...components.map(c=>c[0])]);
const sortable=new Set(['contract_id','client','partner','pool','total','dp','kd_date','issued_sum',...components.map(c=>c[0])]);
function open(profile:Profile,rep:string){setEmployeeProfile(profile);if(profile==='soft')void openSoftCard(rep);else void openHardCard(rep,undefined,next=>open(profile,next));}
function time(value:string){return value?new Date(value).toLocaleString('ru-RU'):'Нет сведений об импорте';}
function amounts(values:any[]){return values?.length?values.map(x=>registryMoney(x.sum)+' '+(x.unit||'')).join(' · '):'—';}
function profilesHtml(dual:boolean,selected:Profile){return dual?`<label>Портфель <select class="input" data-profile><option value="soft" ${selected==='soft'?'selected':''}>Софт</option><option value="hard" ${selected==='hard'?'selected':''}>Хард</option></select></label>`:'';}

export function employeeRegistry(initial:Profile,dual:boolean):Section{return {id:'employee-registry',title:'Таблица договоров',icon:'team',async render(root){
  setHead('Таблица договоров');let profile=initial,page=1,sort='client',direction='asc',seq=0,query='',partner='';
  const prefs=await api('/api/local/preferences',{profile:initial,id:'registry-visible-columns'});if(!root.isConnected)return;
  const hidden=new Set<string>(prefs.data?.value?.hidden || ['pool','dtp','rent','camera_fine','dirt','trash','new_damage','evacuation','peni','other','last_payment_date','last_payment_sum','visit_date','address','phone']);
  root.innerHTML=`<div class="toolbar cabinet-controls">${profilesHtml(dual,profile)}<input class="input registry-search" data-search type="search" placeholder="ФИО, договор, телефон, адрес"><label>Контрагент <select class="input" data-partner><option value="">Все</option></select></label><button class="btn small" data-refresh>Обновить</button></div>
    <details class="section registry-column-picker"><summary>Колонки</summary><div>${columns.filter(c=>!['contract_id','client'].includes(c[0])).map(([key,title])=>`<label><input type="checkbox" data-column="${key}" ${!hidden.has(key)?'checked':''}> ${title}</label>`).join('')}</div></details><div data-registry-result></div>`;
  const target=root.querySelector<HTMLElement>('[data-registry-result]')!;
  const load=async()=>{const current=++seq;setEmployeeProfile(profile);
    const r=await api('/api/workspace/portfolio',{profile,page,size:100,q:query,partner,sort,direction});if(!root.isConnected||seq!==current)return;
    if(!r.ok){target.innerHTML=`<p class="notice err">${esc(r.text || 'Не удалось загрузить договоры')}</p>`;return}
    const d=r.data,visible=columns.filter(([key])=>!hidden.has(key));page=d.page;
    const select=root.querySelector<HTMLSelectElement>('[data-partner]')!;
    select.innerHTML='<option value="">Все</option>'+d.partners.map((p:string)=>`<option ${p===partner?'selected':''}>${esc(p)}</option>`).join('');
    target.innerHTML=`${r.stale?`<p class="notice">Сохранённые данные на ${esc(time(r.stale))}</p>`:''}<p class="registry-asof">${esc(d.employee)} · найдено ${n(d.total)} из ${n(d.portfolio_total)} договоров · реестр: ${esc(time(d.asof))}</p>
      <div class="registry-scroll"><table class="registry-table"><thead><tr>${visible.map(([key,title])=>`<th class="${key==='contract_id'?'registry-fixed-id':key==='client'?'registry-fixed-client':''}">${sortable.has(key)?`<button class="registry-sort" data-sort="${key}">${esc(title)}${sort===key?(direction==='asc'?' ↑':' ↓'):''}</button>`:esc(title)}</th>`).join('')}</tr></thead><tbody>${d.rows.map((row:any)=>`<tr data-contract="${esc(row.contract_id)}" tabindex="0">${visible.map(([key])=>`<td class="${key==='contract_id'?'registry-fixed-id mono':key==='client'?'registry-fixed-client':''}">${esc(numeric.has(key)?registryMoney(row[key]):['kd_date','last_payment_date','visit_date'].includes(key)?registryDate(row[key]):row[key] ?? '—')}${numeric.has(key)&&row[key]!=null?' '+esc(row.unit):''}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${visible.length}">Договоров по выбранным условиям нет</td></tr>`}</tbody></table></div>
      <div class="registry-pages"><button class="btn small" data-prev ${page===1?'disabled':''}>← Назад</button><span>Страница ${n(page)} из ${n(Math.max(1,Math.ceil(d.total/d.size)))}</span><button class="btn small" data-next ${page*d.size>=d.total?'disabled':''}>Дальше →</button></div>`;
    target.querySelectorAll<HTMLButtonElement>('[data-sort]').forEach(b=>b.onclick=()=>{if(sort===b.dataset.sort)direction=direction==='asc'?'desc':'asc';else {sort=b.dataset.sort!;direction='asc';}page=1;void load();});
    target.querySelector('[data-prev]')?.addEventListener('click',()=>{page--;void load();});target.querySelector('[data-next]')?.addEventListener('click',()=>{page++;void load();});
    target.querySelectorAll<HTMLElement>('[data-contract]').forEach(row=>{const action=()=>open(profile,row.dataset.contract!);row.onclick=action;row.onkeydown=e=>{if(e.key==='Enter')action();};});
  };
  root.querySelector<HTMLSelectElement>('[data-profile]')?.addEventListener('change',e=>{profile=(e.target as HTMLSelectElement).value as Profile;partner='';page=1;void load();});
  let timer:ReturnType<typeof setTimeout>;root.querySelector<HTMLInputElement>('[data-search]')!.oninput=e=>{clearTimeout(timer);query=(e.target as HTMLInputElement).value;page=1;timer=setTimeout(()=>void load(),250);};
  root.querySelector<HTMLSelectElement>('[data-partner]')!.onchange=e=>{partner=(e.target as HTMLSelectElement).value;page=1;void load();};
  root.querySelector('[data-refresh]')!.addEventListener('click',()=>void load());
  root.querySelectorAll<HTMLInputElement>('[data-column]').forEach(c=>c.onchange=()=>{c.checked?hidden.delete(c.dataset.column!):hidden.add(c.dataset.column!);void api('/api/local/preferences',undefined,'POST',{profile:initial,id:'registry-visible-columns',value:{hidden:[...hidden]}});void load();});
  await load();
}};}

export function employeeCabinet(initial:Profile,dual:boolean):Section{return {id:'employee-cabinet',title:'Личный кабинет',icon:'report',async render(root){
  setHead('Личный кабинет');let profile=initial,month=new Date(Date.now()+5*3600000).toISOString().slice(0,7),detail='payments',page=1,seq=0;
  root.innerHTML=`<div class="toolbar cabinet-controls">${profilesHtml(dual,profile)}<label>Месяц <input class="input" data-month type="month" value="${month}" max="${month}"></label><button class="btn small" data-refresh>Обновить</button></div><div data-cabinet-content></div>`;
  const target=root.querySelector<HTMLElement>('[data-cabinet-content]')!;
  const load=async()=>{const current=++seq;setEmployeeProfile(profile);const r=await api('/api/workspace/dashboard',{profile,month,detail,page,size:100});if(!root.isConnected||seq!==current)return;
    if(!r.ok){target.innerHTML=`<p class="notice err">${esc(r.text || 'Кабинет не загрузился')}</p>`;return}
    const d=r.data,s=d.stats,p=d.payments;page=d.page;
    const metrics:[unknown,string][]=[[s.portfolio_contracts,'Договоров в портфеле'],[s.processed_contracts,'Обработано договоров'],...(profile==='soft'?[[s.calls,'Звонков'],[s.rpc,'Результативных контактов (RPC)'],[s.ptp,'Обещаний по звонкам (PTP)'],[s.kp,'Выполнено обещаний (KP)']]:[[s.office,'Офисных действий'],[s.visits,'Выездов'],[s.promised,'Офисных обещаний']]) as [unknown,string][]];
    target.innerHTML=`${r.stale?`<p class="notice">Сохранённые данные на ${esc(time(r.stale))}</p>`:''}<p class="registry-asof">${esc(d.employee)} · ${registryDate(d.from)} — ${registryDate(d.to)} · реестр: ${esc(time(d.asof))}</p>
      <div class="cabinet-payment"><span>Оплаты за месяц по реестру</span><strong>${esc(amounts(p.totals))}</strong><span>Плативших договоров: ${n(s.paying_contracts)} · по текущему закреплению</span>${p.missing_contracts?`<p class="notice">Нет месячных сведений по ${n(p.missing_contracts)} договорам. Итог включает только доступные данные.</p>`:''}</div>
      <div class="kpis cabinet-kpis">${metrics.map(([value,label])=>`<div class="kpi"><b>${n(Number(value)||0)}</b><span>${esc(label)}</span></div>`).join('')}</div>
      <p class="hint">Долг текущего портфеля: ${esc(amounts(d.portfolio_money))}. Действующих обещаний выбранного месяца: ${n(s.promises_active)}; выполнено: ${n(s.promises_kept)}.</p>
      <div class="toolbar">${[['payments','Оплаты'],['operations','Операции'],['promises','Обещания']].map(([id,title])=>`<button class="btn small ${detail===id?'primary':''}" data-detail="${id}">${title}</button>`).join('')}</div>
      <div class="registry-scroll"><table class="registry-table cabinet-details"><thead><tr>${(detail==='payments'?['Договор','Клиент','Контрагент','Оплачено за месяц','Актуальность']:detail==='operations'?['День','Клиент','Договоры','Результат','Источник']:['Клиент','Договоры','Срок','Обещано','Оплата реестра','Состояние']).map(t=>`<th>${t}</th>`).join('')}</tr></thead><tbody>${d.rows.map((x:any)=>`<tr ${x.rep?`data-open-contract="${esc(x.rep)}" tabindex="0"`:''}>${(detail==='payments'?[x.contract_id,x.client,x.partner,registryMoney(x.amount)+' '+x.unit,time(x.observed_at)]:detail==='operations'?[registryDate(x.day),x.client,(x.contracts||[]).join(', '),x.title,x.source]:[x.client,(x.contracts||[]).join(', '),registryDate(x.due),registryMoney(x.amount)+' '+(x.unit||''),registryMoney(x.received),({kept:'Выполнено',partial:'Частично',pending:'Ожидается',overdue:'Просрочено',incomplete:'Не хватает данных'} as Record<string,string>)[x.state]||x.state]).map(v=>`<td>${esc(v??'—')}</td>`).join('')}</tr>`).join('') || '<tr><td colspan="6">За выбранный месяц записей нет</td></tr>'}</tbody></table></div>
      <div class="registry-pages"><button class="btn small" data-prev ${page===1?'disabled':''}>← Назад</button><span>${n(d.total)} записей · страница ${n(page)}</span><button class="btn small" data-next ${page*d.size>=d.total?'disabled':''}>Дальше →</button></div>
      <details class="section"><summary>Как считаются показатели</summary><p>Оплаты — месячные суммы из общего реестра по договорам текущего закрепления. Договор учитывается один раз; повтор импорта заменяет прежнюю сумму. Корректировки включаются в итог. Сообщённые сотрудником оплаты и обещания учитываются отдельно.</p><p>Операции относятся к автору за рабочие дни UTC+5. Обработанные договоры — уникальные договоры текущего портфеля с вашей операцией за месяц. История собственных операций сохраняется после переназначения; чужая карточка не открывается.</p><p>RPC/PTP/KP рассчитаны по действующим правилам отчёта Софта.</p></details>`;
    target.querySelectorAll<HTMLButtonElement>('[data-detail]').forEach(b=>b.onclick=()=>{detail=b.dataset.detail!;page=1;void load();});
    target.querySelectorAll<HTMLElement>('[data-open-contract]').forEach(row=>{row.onclick=()=>open(profile,row.dataset.openContract!);row.onkeydown=e=>{if(e.key==='Enter')open(profile,row.dataset.openContract!);};});
    target.querySelector('[data-prev]')?.addEventListener('click',()=>{page--;void load();});target.querySelector('[data-next]')?.addEventListener('click',()=>{page++;void load();});
  };
  root.querySelector<HTMLSelectElement>('[data-profile]')?.addEventListener('change',e=>{profile=(e.target as HTMLSelectElement).value as Profile;page=1;void load();});
  root.querySelector<HTMLInputElement>('[data-month]')!.onchange=e=>{month=(e.target as HTMLInputElement).value;page=1;void load();};root.querySelector('[data-refresh]')!.addEventListener('click',()=>void load());
  await load();
}};}
