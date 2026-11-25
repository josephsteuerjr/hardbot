const esc=(v:any)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const stamp=(v:string)=>new Date(v).toLocaleString('ru-RU');
const num=(v:any)=>v===null||v===undefined?'—':Number(v).toLocaleString('ru-RU',{maximumFractionDigits:2});
const metrics=['Встречи','Звонки · клиенты','Обещания · КД','Обещания · сумма','В работе · КД','В работе · сумма','Третьи лица · КД','Третьи лица · сумма','Оплачено · КД','Оплачено · сумма'];

export async function mountManagerReports(host:HTMLElement,crm:any,valid:()=>boolean,quick=true){
  let disposed=false,turn=0,payload:any,sms=false,mode='report',selected='',day='',busy=false,phoneTable=false;
  let country='', optionsLoaded=false;
  const current=()=>!disposed&&valid();
  host.innerHTML=`<div class="toolbar"><label>Дата <input id="report-day" type="date"></label><label>Отсечка <select id="report-cutoff"><option value="">Сейчас</option></select></label><button id="report-refresh">Обновить</button><button id="report-export">Выгрузить Excel</button></div><div class="tabs"><button data-report-tab="report" class="active">Трёхчасовой отчёт</button><button data-report-tab="dynamics">Динамика</button><button data-report-tab="month">Итоги месяца</button></div><div class="toolbar" id="report-channel"><button data-channel="calls" class="active">Звонки</button><button data-channel="sms">SMS</button></div><p id="report-status" role="status"></p><div id="report-body"></div>`;
  const $=<T extends HTMLElement=HTMLElement>(id:string)=>host.querySelector<T>('#'+id)!;
  if(quick){
    host.insertAdjacentHTML('afterbegin','<div class="toolbar" aria-label="Быстрая выгрузка Excel"><button data-preset="daily">Дневной Excel</button><button data-preset="three-hour">Трёхчасовой Excel</button><button id="manager-team-results">Выезды и Софт · Excel</button><span data-preset-status role="status"></span></div><p class="muted">Трёхчасовой отчёт — только звонки и SMS. Выезды и их детализация — в «Команда → Результаты».</p>');
    host.querySelector<HTMLButtonElement>('#manager-team-results')!.onclick=()=>document.querySelector<HTMLButtonElement>('#navigation [data-section="team"]')!.click();
    const buttons=[...host.querySelectorAll<HTMLButtonElement>('[data-preset]')];
    const notice=host.querySelector<HTMLElement>('[data-preset-status]')!;
    for(const button of buttons)button.onclick=async()=>{
      if(button.disabled)return;
      buttons.forEach(item=>item.disabled=true);
      notice.textContent='Готовим Excel…';
      try{
        const result=await crm.workbook('report-preset',{preset:button.dataset.preset});
        if(!result.ok&&!result.cancelled)throw Error(result.text||'Отчёт не удалось скачать.');
        if(current())notice.textContent=result.cancelled?'':result.data?.filename||'Отчёт сохранён.';
      }catch(error){if(current())notice.textContent=(error as Error).message;}
      finally{if(current())buttons.forEach(item=>item.disabled=false);}
    };
  }
  $('report-day').closest('label')!.insertAdjacentHTML('beforebegin','<label id="report-country-field" hidden>Страна <select id="report-country"></select></label>');
  const countrySelect=$<HTMLSelectElement>('report-country');
  const status=(s:string)=>{if(current())$('report-status').textContent=s;};
  const protect=(fn:()=>any)=>()=>{void Promise.resolve().then(fn).catch(e=>status(e.message));};
  const request=async(action:string,query:any={})=>{const r=await crm.api('GET',action,undefined,query);if(!r.ok||r.stale)throw Error(r.text||'Нужна связь с сервером.');return r.data;};
  function table(headers:string[],rows:string[]){return `<div class="manager-report-scroll"><table class="manager-report-table"><thead>${headers.join('')}</thead><tbody>${rows.join('')}</tbody></table></div>`;}
  function evidence(index:number){
    const row=payload.report.sheets.find((s:any)=>s.sms===sms).rows[index],dialog=document.createElement('dialog');
    const records=row.current_records||[];
    dialog.className='manager-report-detail';dialog.innerHTML=`<h2>${esc(row.employee)}</h2><p>Договоры, на которых рассчитан выбранный срез · ${stamp(payload.report.as_of)}</p>${table(['<tr>'+['Клиент','Договор','Результат','Дата связи','Долг','Обещание'].map(s=>`<th>${s}</th>`).join('')+'</tr>'],records.map((r:any)=>`<tr><td>${esc(r.client)}</td><td>${esc(r.contract_id)}</td><td>${esc(r.status)}</td><td>${esc(r.date)}</td><td>${num(r.debt)}</td><td>${num(r.promise)}</td></tr>`))}<p>${records.length} договоров. Показатели учитывают дату связи и вид результата.</p><button>Закрыть</button>`;
    host.append(dialog);dialog.querySelector('button')!.onclick=()=>{dialog.close();dialog.remove();};dialog.showModal();
  }
  function paint(){
    if(!payload||mode==='month')return;
    const report=payload.report,rows=report.sheets.find((s:any)=>s.sms===sms).rows;
    $('report-channel').hidden=false;
    if(mode==='report'){
      const head='<tr><th rowspan="2">Сотрудник</th>'+metrics.map(m=>`<th colspan="3">${m}</th>`).join('')+'</tr>';
      const sub='<tr>'+metrics.map(()=>'<th>Вчера</th><th>Срез</th><th>%</th>').join('')+'</tr>';
      const fullTable=table([head,sub],rows.map((r:any,i:number)=>`<tr><th><button class="link" data-report-person="${i}">${esc(r.employee)}</button></th>${r.current.map((v:any,j:number)=>`<td>${num(r.yesterday[j])}</td><td>${num(v)}</td><td>${r.yesterday[j]==null||v==null||Number(r.yesterday[j])===0?'—':num(Number(v)/Number(r.yesterday[j])*100)+'%'}</td>`).join('')}</tr>`));
      $('report-body').innerHTML=crm.platform==='mobile'&&!phoneTable?`<div class="mobile-report-cards">${rows.map((r:any,i:number)=>`<section><button class="link mobile-report-person" data-report-person="${i}">${esc(r.employee)} <span>↗</span></button><div class="mobile-report-stats">${[1,3,4,9].map(j=>`<div><span>${metrics[j]}</span><strong>${num(r.current[j])}</strong><small>Вчера ${num(r.yesterday[j])}</small></div>`).join('')}</div><details><summary>Все показатели</summary>${metrics.map((title,j)=>`<div class="mobile-report-detail"><span>${title}</span><strong>${num(r.current[j])}</strong><small>Вчера ${num(r.yesterday[j])}</small></div>`).join('')}</details></section>`).join('')}</div><button id="mobile-report-table" class="link">Открыть таблицу отчёта</button>`:fullTable+(crm.platform==='mobile'?'<button id="mobile-report-table" class="link">Показать сводку</button>':'');
      host.querySelector<HTMLButtonElement>('#mobile-report-table')?.addEventListener('click',()=>{phoneTable=!phoneTable;paint();});
      host.querySelectorAll<HTMLButtonElement>('[data-report-person]').forEach(b=>b.onclick=()=>evidence(Number(b.dataset.reportPerson)));
      status(`На ${stamp(report.as_of)} · ${report.currency||'Валюта не подтверждена'}${report.yesterday_incomplete_contracts?' · Вчера: неполная история по '+report.yesterday_incomplete_contracts+' КД':''}`);
      if(!rows.length)$('report-body').textContent='В выбранном срезе нет сотрудников с назначением на звонки.';
    }else{
      const totals=(snapshot:any)=>snapshot.sheets.find((s:any)=>s.sms===sms).rows.reduce((acc:number[],r:any)=>acc.map((v,j)=>v+Number(r.current[j]||0)),Array(10).fill(0));
      let previous:number[]|undefined;
      $('report-body').innerHTML=table(['<tr><th>Снимок</th>'+metrics.map(m=>`<th>${m}</th>`).join('')+'</tr>'],payload.snapshots.map((s:any)=>{
        const values=totals(s),row=`<tr><th><button class="link" data-snapshot="${esc(s.snapshot_id)}">${stamp(s.captured_at)}</button></th>${values.map((v:number,i:number)=>`<td>${num(v)}${previous?`<small class="report-delta">${v-previous[i]>=0?'+':''}${num(v-previous[i])}</small>`:''}</td>`).join('')}</tr>`;previous=values;return row;
      }));
      host.querySelectorAll<HTMLButtonElement>('[data-snapshot]').forEach(b=>b.onclick=protect(async()=>{selected=b.dataset.snapshot!;mode='report';markTabs();await load();}));
      status(payload.snapshots.length?`Сохранённые снимки за ${payload.day}. Под значением — изменение к предыдущему снимку.`:'За выбранную дату сохранённых снимков нет.');
    }
  }
  function markTabs(){
    host.querySelectorAll<HTMLElement>('[data-report-tab]').forEach(b=>b.classList.toggle('active',b.dataset.reportTab===mode));
    $('report-country-field').hidden=mode==='month'||!countrySelect.options.length;
  }
  async function monthly(){
    $('report-channel').hidden=true;
    const month=day.slice(0,7)||new Date().toISOString().slice(0,7),data=await request('manager-statistics',{month});if(!current()||mode!=='month')return;
    $('report-body').innerHTML=`<h2>${esc(month)}</h2>${table(['<tr><th>Сотрудник</th><th>Звонки</th><th>Выезды</th><th>Обработано КД</th><th>КД сейчас</th></tr>'],data.staff.map((p:any)=>`<tr><th>${esc(p.name)}</th><td><button class="link" data-fact-employee="${esc(p.employee_uuid)}" data-fact-kind="call">${p.facts.call}</button></td><td><button class="link" data-fact-employee="${esc(p.employee_uuid)}" data-fact-kind="visit">${p.facts.visit}</button></td><td>${p.facts.processed_contracts}</td><td>${p.contracts}</td></tr>`))}<h2>Текущий портфель</h2><p>${data.people} клиентов · ${data.contracts} КД · не назначено ${data.unassigned_contracts} КД</p>${table(['<tr><th>Валюта</th><th>Долг</th><th>Поступления за месяц</th></tr>'],[...new Set([...Object.keys(data.debt),...Object.keys(data.payments.totals)])].map(k=>`<tr><th>${esc(k)}</th><td>${num(data.debt[k])}</td><td>${num(data.payments.totals[k])}</td></tr>`))}<p>Без суммы долга: ${data.unknown_debt} КД; без подтверждённых поступлений: ${data.payments.missing_contracts} КД.</p>`;
    host.querySelectorAll<HTMLButtonElement>('[data-fact-employee]').forEach(b=>b.onclick=protect(async()=>{
      const panel=document.createElement('dialog');panel.className='manager-report-detail';host.append(panel);let offset=0;
      const show=async()=>{const result=await request('manager-statistics',{month,employee_uuid:b.dataset.factEmployee,kind:b.dataset.factKind,offset});
        if(!current())return;panel.innerHTML=`<h2>${esc(result.employee)} · ${result.kind==='call'?'Звонки':'Выезды'}</h2>${table(['<tr><th>Время</th><th>Клиенты / договоры</th></tr>'],result.rows.map((r:any)=>`<tr><td>${r.time_quality==='legacy_day'?esc(r.day):stamp(r.at)}</td><td>${r.contracts.map((c:any)=>esc(c.name)+(c.external_id?' · '+esc(c.external_id):'')).join('<br>')}</td></tr>`))}<p>${result.total?offset+1:0}–${offset+result.rows.length} из ${result.total}</p><div class="actions"><button data-page-prev${offset?'':' disabled'}>Назад</button><button data-page-next${result.has_more?'':' disabled'}>Далее</button><button data-detail-close>Закрыть</button></div>`;
        panel.querySelector<HTMLButtonElement>('[data-page-prev]')!.onclick=protect(async()=>{offset=Math.max(0,offset-50);await show();});panel.querySelector<HTMLButtonElement>('[data-page-next]')!.onclick=protect(async()=>{offset+=50;await show();});panel.querySelector<HTMLButtonElement>('[data-detail-close]')!.onclick=()=>{panel.close();panel.remove();};};
      await show();if(current())panel.showModal();
    }));
    status(`На ${stamp(data.as_of)}. Звонки и выезды — по исходным авторам за месяц. Долг и поступления — по текущему портфелю.`);
  }
  async function load(){
    const n=++turn;
    $<HTMLButtonElement>('report-export').disabled=true;
    if(mode==='month'){
      await monthly();
      if(current()&&n===turn&&!busy)$<HTMLButtonElement>('report-export').disabled=false;
      return;
    }
    if(!optionsLoaded){
      const options=await request('manager-report',{options:'1'});
      if(!current()||n!==turn)return;
      for(const option of options.countries||[])countrySelect.add(new Option(option.name,option.code));
      if(countrySelect.options.length){
        country=countrySelect.options[0].value;
        if(countrySelect.options.length>1)countrySelect.add(new Option('Все страны',''));
        countrySelect.value=country;
      }
      optionsLoaded=true;markTabs();
    }
    const data=await request('manager-report',{...(country?{country}:{}),...(selected?{snapshot_id:selected}:{}),...(day?{day}:{})});
    if(!current()||n!==turn)return;
    payload=data;day=data.day;$<HTMLInputElement>('report-day').value=day;
    $<HTMLSelectElement>('report-cutoff').innerHTML='<option value="">Сейчас</option>'+data.snapshots.map((s:any)=>`<option value="${esc(s.snapshot_id)}">${stamp(s.captured_at)}</option>`).join('');
    $<HTMLSelectElement>('report-cutoff').value=selected;paint();
    if(!busy)$<HTMLButtonElement>('report-export').disabled=mode==='dynamics'&&!data.snapshots.length;
  }
  $('report-refresh').onclick=protect(load);
  countrySelect.onchange=protect(async()=>{
    country=countrySelect.value;selected='';payload=null;$('report-body').replaceChildren();
    await load();
  });
  $<HTMLInputElement>('report-day').onchange=protect(async()=>{day=$<HTMLInputElement>('report-day').value;selected='';mode='dynamics';markTabs();await load();});
  $<HTMLSelectElement>('report-cutoff').onchange=protect(async()=>{selected=$<HTMLSelectElement>('report-cutoff').value;mode='report';markTabs();await load();});
  host.querySelectorAll<HTMLButtonElement>('[data-report-tab]').forEach(b=>b.onclick=protect(async()=>{mode=b.dataset.reportTab!;markTabs();await load();}));
  host.querySelectorAll<HTMLButtonElement>('[data-channel]').forEach(b=>b.onclick=()=>{sms=b.dataset.channel==='sms';host.querySelectorAll<HTMLElement>('[data-channel]').forEach(t=>t.classList.toggle('active',t===b));paint();});
  $('report-export').onclick=protect(async()=>{
    if(busy)return;
    const revision=turn;
    busy=true;$<HTMLButtonElement>('report-export').disabled=true;
    try{
      const r=await crm.workbook('manager-report',{snapshot_id:selected||undefined,mode,day,month:day.slice(0,7),country});
      if(!r.ok&&!r.cancelled)throw Error(r.text||'Выгрузка не выполнена.');
      if(r.ok&&revision===turn)status('Excel сохранён и проверен.');
    }finally{
      busy=false;
      if(current()&&revision===turn)$<HTMLButtonElement>('report-export').disabled=false;
      else if(current())await load();
    }
  });
  const stop=crm.onChange?.((e:any)=>{if(e.type==='live'&&e.dirty&&!selected&&!busy&&!host.querySelector('dialog[open]'))void load().catch(error=>status(error.message));});
  await load();
  return ()=>{disposed=true;turn++;stop?.();};
}
