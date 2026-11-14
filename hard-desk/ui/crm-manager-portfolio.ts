const esc=(v:any)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const workName:any={call:'Звонки',visit:'Выезды'};
const actionName:any={assign:'Назначить',replace:'Заменить',remove:'Снять'};

/** The registry's table geometry, protected preferences and stable identity keys. */
export async function mountManagedPortfolio(host:HTMLElement,crm:any,openPerson:(id:string)=>void,isCurrent:()=>boolean){
  let disposed=false,generation=0,data:any,staff:any[]=[],busy=false,preview:any,pending:any,activeJob='',connected=true;
  let jobTimer:ReturnType<typeof setTimeout>|undefined;
  let prefs=(await crm.draft('managed-portfolio-view')).data || {};
  prefs={search:String(prefs.search||''),employee_uuid:String(prefs.employee_uuid||''),work:['call','visit'].includes(prefs.work)?prefs.work:'all',
    unassigned:prefs.unassigned==='1'?'1':'0',offset:Math.max(0,Number(prefs.offset)||0),
    zoom:[80,90,100,110,125,150].includes(prefs.zoom)?prefs.zoom:100,widths:prefs.widths||{},
    selected:Array.isArray(prefs.selected)?prefs.selected:[],expanded:Array.isArray(prefs.expanded)?prefs.expanded:[],left:prefs.left||0,top:prefs.top||0};
  let selected=new Set<string>(prefs.selected),expanded=new Set<string>(prefs.expanded),selectionKind=prefs.selectionKind==='contracts'?'contracts':'people',allFilter=false,lastSelected='';
  if(!isCurrent())return ()=>{};
  host.innerHTML=`<div class="registry managed-portfolio"><div class="toolbar"><input id="managed-search" type="search" placeholder="Клиент, договор, кредитор" value="${esc(prefs.search)}"><button id="managed-find">Найти</button><select id="managed-employee" aria-label="Ответственный"><option value="">Все сотрудники</option></select><select id="managed-work" aria-label="Вид работы"><option value="all">Обе работы</option><option value="call">Звонки</option><option value="visit">Выезды</option></select><label class="check"><input type="checkbox" id="managed-unassigned">Не назначены</label><button id="managed-refresh">Обновить</button><label>Масштаб <select id="managed-zoom">${[80,90,100,110,125,150].map(n=>`<option value="${n}">${n}%</option>`).join('')}</select></label></div>
    <div class="toolbar managed-selection"><button id="managed-page">Выбрать страницу</button><button id="managed-all">Весь отбор</button><button id="managed-clear">Снять выделение</button><span id="managed-selection-count"></span><button data-allocate="assign">Назначить</button><button data-allocate="replace">Заменить</button><button data-allocate="remove">Снять</button><button id="managed-history" class="link">История распределения</button></div>
    <div id="managed-job" role="status"></div><p role="status" id="managed-status"></p><div class="registry-scroll managed-scroll"></div><div class="toolbar"><button id="managed-prev">Назад</button><span id="managed-page-label"></span><button id="managed-next">Далее</button></div>
    <dialog id="managed-editor"><form><h2 id="managed-action"></h2><div class="toolbar"><label>Работа <select id="allocation-work"><option value="call">Звонки</option><option value="visit">Выезды</option><option value="both">Обе работы</option></select></label><label>Состав <select id="allocation-part"><option value="whole">Весь доступный клиент</option><option value="filtered">Только договоры из отбора</option></select></label></div><div id="allocation-target"><label>Сотрудник <input id="allocation-search" type="search" placeholder="Имя или команда"><select id="allocation-employee" size="5" aria-label="Новый ответственный"></select></label></div><label>Комментарий <input id="allocation-comment" maxlength="1000"></label><div id="allocation-preview"></div><p role="status" id="allocation-status"></p><div class="actions"><button type="button" id="allocation-close">Закрыть</button><button type="button" id="allocation-check">Проверить исход операции</button><button type="submit" class="primary" id="allocation-apply">Применить</button></div></form></dialog></div>`;
  const $=<T extends HTMLElement=HTMLElement>(id:string)=>host.querySelector<T>('#'+id)!;
  const scroll=host.querySelector<HTMLElement>('.managed-scroll')!;
  const dialog=$<HTMLDialogElement>('managed-editor');
  const valid=()=>!disposed&&isCurrent();
  const status=(text:string)=>{if(valid())$('managed-status').textContent=text;};
  const protect=(fn:()=>any)=>()=>{void Promise.resolve().then(fn).catch(e=>status(e.message));};
  const request=async(method:string,action:string,body?:any,query?:any)=>{
    const r=await crm.api(method,action,body,query);
    if(!r.ok || r.stale)throw Object.assign(Error(r.text||'Нужна подтверждённая связь с сервером.'),{result:r});
    return r.data;
  };
  const filters=()=>({search:prefs.search,employee_uuid:prefs.employee_uuid,work:prefs.work,unassigned:prefs.unassigned});
  const remember=()=>{
    prefs.selected=[...selected];prefs.expanded=[...expanded];prefs.left=scroll.scrollLeft;prefs.top=scroll.scrollTop;
    prefs.selectionKind=selectionKind;
    void crm.draft('managed-portfolio-view',prefs).catch(()=>{});
  };
  function selectionStatus(){
    $('managed-selection-count').textContent=allFilter?`Весь отбор: ${data?.total??'…'} клиентов`:`Выбрано ${selectionKind==='people'?'клиентов':'договоров'}: ${selected.size}`;
    host.querySelectorAll<HTMLButtonElement>('[data-allocate]').forEach(b=>b.disabled=busy||!connected||(!allFilter&&!selected.size));
  }
  const responsible=(p:any,kind:string)=>{
    const slots=p.contracts.map((r:any)=>r.slots.find((s:any)=>s.capability===kind)).filter(Boolean);
    if(slots.some((s:any)=>s.ambiguous))return 'Требует сверки';
    const names=new Set(slots.map((s:any)=>s.name||'Не назначен'));
    return names.size===1?[...names][0]:'Разные';
  };
  const money=(p:any)=>Object.entries(p.currencies).map(([k,v])=>esc(v)+' '+esc(k)).join('<br>')||'Сумма не подтверждена';
  function details(p:any){
    if(!expanded.has(p.person_uuid))return '';
    return p.contracts.map((r:any)=>`<tr class="managed-contract" data-contract="${esc(r.contract_uuid)}"><td><input type="checkbox" data-contract-select="${esc(r.contract_uuid)}" aria-label="Выбрать договор ${esc(r.external_id)}"${selectionKind==='contracts'&&selected.has(r.contract_uuid)?' checked':''}></td><td>${esc(r.external_id)}<br><span class="muted">${esc(r.fields.partner||r.creditor_id)}</span>${r.in_filter?'':'<br><span class="tag">Вне отбора</span>'}</td><td>${r.slots.map((s:any)=>`<div>${workName[s.capability]}: <button class="link" data-slot-contract="${esc(r.contract_uuid)}" data-slot-work="${s.capability}"${s.manageable?'':' disabled'}>${esc(s.ambiguous?'Требует сверки':s.name||'Не назначен')}</button></div>`).join('')}</td><td>${esc(r.fields.soft_call_status||'—')}<br>${esc(r.fields.call_status||'—')}</td><td>${esc(r.fields.debt_today??r.fields.debt_total??'Не указано')} ${esc(r.currency||'Валюта не подтверждена')}</td></tr>`).join('');
  }
  function paint(){
    const left=scroll.scrollLeft||prefs.left,top=scroll.scrollTop||prefs.top;
    scroll.style.setProperty('--registry-scale',String(prefs.zoom/100));
    scroll.style.setProperty('--managed-first-width',((Number(prefs.widths['column-0'])||80)*prefs.zoom/100)+'px');
    scroll.style.setProperty('--managed-second-width',((Number(prefs.widths['column-1'])||300)*prefs.zoom/100)+'px');
    scroll.innerHTML=`<table class="managed-table"><thead><tr><th><span>Выбор</span></th><th><span>Клиент / договор</span></th><th><span>Ответственные</span></th><th><span>Состав / результат</span></th><th><span>Долг по валютам</span></th></tr></thead><tbody>${data.people.map((p:any)=>`<tr data-managed-person="${esc(p.person_uuid)}"><td><input type="checkbox" data-person-select="${esc(p.person_uuid)}" aria-label="Выбрать ${esc(p.name)}"${selectionKind==='people'&&selected.has(p.person_uuid)?' checked':''}></td><td><button class="link" data-expand="${esc(p.person_uuid)}" aria-expanded="${expanded.has(p.person_uuid)}">${expanded.has(p.person_uuid)?'−':'+'}</button> <button class="link" data-open-person="${esc(p.person_uuid)}">${esc(p.name)}</button>${p.partially_managed?'<br><span class="tag">Частично управляемый клиент</span>':''}</td><td><button class="link" data-person-allocate="${esc(p.person_uuid)}">Звонки: ${esc(responsible(p,'call'))}<br>Выезды: ${esc(responsible(p,'visit'))}</button></td><td>Весь клиент: ${p.managed_contracts} КД<br>В отборе: ${p.filtered_contracts} КД</td><td>${money(p)}${p.unknown_amounts?'<br>Без суммы/валюты: '+p.unknown_amounts:''}</td></tr>${details(p)}`).join('')}</tbody></table>`;
    const widths=[80,300,300,220,200];
    scroll.querySelectorAll<HTMLElement>('thead th').forEach((th,index)=>{
      const key='column-'+index,width=Number(prefs.widths[key])||widths[index];th.style.width=width*prefs.zoom/100+'px';
      const handle=document.createElement('span');handle.className='registry-column-resizer';handle.tabIndex=0;handle.role='separator';handle.setAttribute('aria-label','Ширина столбца');
      const resize=(value:number)=>{prefs.widths[key]=Math.min(640,Math.max(70,value));th.style.width=prefs.widths[key]*prefs.zoom/100+'px';if(index<2)scroll.style.setProperty(index===0?'--managed-first-width':'--managed-second-width',prefs.widths[key]*prefs.zoom/100+'px');};
      handle.onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();resize((Number(prefs.widths[key])||width)+(e.key==='ArrowRight'?10:-10));remember();}};
      handle.onpointerdown=e=>{e.preventDefault();handle.setPointerCapture(e.pointerId);const start=e.clientX,initial=Number(prefs.widths[key])||width;handle.onpointermove=event=>resize(initial+(event.clientX-start)/(prefs.zoom/100));handle.onpointerup=()=>{handle.onpointermove=null;remember();};};
      th.append(handle);
    });
    scroll.querySelectorAll<HTMLButtonElement>('[data-open-person]').forEach(b=>b.onclick=()=>{remember();openPerson(b.dataset.openPerson!);});
    scroll.querySelectorAll<HTMLButtonElement>('[data-expand]').forEach(b=>b.onclick=()=>{const id=b.dataset.expand!;expanded.has(id)?expanded.delete(id):expanded.add(id);paint();remember();});
    scroll.querySelectorAll<HTMLInputElement>('[data-person-select]').forEach(input=>input.onclick=event=>{
      const id=input.dataset.personSelect!;if(selectionKind!=='people'){selected.clear();selectionKind='people';}allFilter=false;
      if((event as MouseEvent).shiftKey&&lastSelected){const ids=data.people.map((p:any)=>p.person_uuid),a=ids.indexOf(lastSelected),b=ids.indexOf(id);if(a>=0)ids.slice(Math.min(a,b),Math.max(a,b)+1).forEach((key:string)=>selected.add(key));}
      else input.checked?selected.add(id):selected.delete(id);
      lastSelected=id;paint();remember();
    });
    scroll.querySelectorAll<HTMLInputElement>('[data-contract-select]').forEach(input=>input.onchange=()=>{if(selectionKind!=='contracts'){selected.clear();selectionKind='contracts';}allFilter=false;input.checked?selected.add(input.dataset.contractSelect!):selected.delete(input.dataset.contractSelect!);paint();remember();});
    scroll.querySelectorAll<HTMLButtonElement>('[data-person-allocate]').forEach(b=>b.onclick=()=>{selected=new Set([b.dataset.personAllocate!]);selectionKind='people';allFilter=false;paint();void openEditor('replace');});
    scroll.querySelectorAll<HTMLButtonElement>('[data-slot-contract]').forEach(b=>b.onclick=()=>{selected=new Set([b.dataset.slotContract!]);selectionKind='contracts';allFilter=false;paint();void openEditor('replace',b.dataset.slotWork);});
    $('managed-page-label').textContent=`${data.total?prefs.offset+1:0}–${Math.min(prefs.offset+data.people.length,data.total)} из ${data.total} клиентов`;
    $<HTMLButtonElement>('managed-prev').disabled=prefs.offset===0;$<HTMLButtonElement>('managed-next').disabled=!data.has_more;
    selectionStatus();scroll.scrollLeft=left;scroll.scrollTop=top;
  }
  async function load(){
    const turn=++generation;
    const snapshot=await request('GET','managed-portfolio',undefined,{...filters(),offset:prefs.offset,limit:50});
    if(!valid()||turn!==generation)return;
    data=snapshot;paint();status('Подтверждено сервером: '+new Date(data.as_of).toLocaleTimeString('ru-RU'));
  }
  let action='replace',previewTurn=0,previewTimer:ReturnType<typeof setTimeout>|undefined;
  const allocationSelection=()=>({kind:allFilter?'filter':selectionKind,ids:allFilter?undefined:[...selected],filters:filters(),part:$<HTMLSelectElement>('allocation-part').value});
  function listTargets(){
    const search=$<HTMLInputElement>('allocation-search').value.toLocaleLowerCase(),previous=$<HTMLSelectElement>('allocation-employee').value;
    const work=$<HTMLSelectElement>('allocation-work').value;
    $<HTMLSelectElement>('allocation-employee').innerHTML='<option value="">Выберите сотрудника</option>'+staff.filter(p=>(p.name+' '+p.team_id).toLocaleLowerCase().includes(search)).map(p=>{
      const eligible=p.eligible&&(work==='both'?['call','visit'].every(w=>p.work_kinds.includes(w)):p.work_kinds.includes(work));
      return `<option value="${esc(p.employee_uuid)}"${eligible?'':' disabled'}>${esc(p.name)} · ${esc(p.team_id)} · ${p.contracts} КД${p.has_account?'':' · без аккаунта'}${eligible?'':' · '+esc(p.reason||'Нет права на выбранную работу')}</option>`;
    }).join('');
    $<HTMLSelectElement>('allocation-employee').value=previous;
  }
  async function refreshPreview(){
    const turn=++previewTurn;preview=undefined;$<HTMLButtonElement>('allocation-apply').disabled=true;
    if(pending||!connected)return;
    const work=$<HTMLSelectElement>('allocation-work').value,target=$<HTMLSelectElement>('allocation-employee').value;
    if(action!=='remove'&&!target){$('allocation-preview').textContent='Выберите сотрудника.';return;}
    try{
      const selection=allocationSelection();if(selection.kind==='filter')delete selection.ids;
      const result=await request('POST','assignment-preview',{schema_version:1,action,employee_uuid:action==='remove'?null:target,
        work_kinds:work==='both'?['call','visit']:[work],selection,comment:$<HTMLInputElement>('allocation-comment').value});
      if(!valid()||turn!==previewTurn||!dialog.open)return;
      preview=result;
      $('allocation-preview').innerHTML=`<p>Клиентов: <b>${result.people}</b> · Договоров: <b>${result.contracts}</b> · Рабочих мест: <b>${result.slots_count}</b> · Без изменения: ${result.no_op}</p>${result.large?'<p>Серверное задание: отдельные атомарные пакеты по 200 мест. Остановка сохраняет уже применённые пакеты.</p>':''}<details><summary>${result.large?'Первые 200 мест':'Состав и текущие закрепления'}</summary>${result.slots.map((s:any)=>`<div>${esc(data.people.flatMap((p:any)=>p.contracts).find((r:any)=>r.contract_uuid===s.contract_uuid)?.external_id||s.contract_uuid)} · ${workName[s.capability]} · ${esc(staff.find(p=>p.employee_uuid===s.employee_uuid)?.name||'Не назначен')} · версия ${s.revision}</div>`).join('')}</details>`;
      $('allocation-status').textContent='Состав зафиксирован до '+new Date(result.expires_at).toLocaleTimeString('ru-RU');
      $<HTMLButtonElement>('allocation-apply').disabled=false;
    }catch(e){if(turn===previewTurn){$('allocation-preview').textContent=(e as Error).message;}}
  }
  const schedulePreview=()=>{if(pending)return;previewTurn++;clearTimeout(previewTimer);preview=undefined;$<HTMLButtonElement>('allocation-apply').disabled=true;previewTimer=setTimeout(()=>void refreshPreview(),200);};
  function lockIntent(locked:boolean){
    ['allocation-work','allocation-part','allocation-search','allocation-employee','allocation-comment'].forEach(id=>($<HTMLInputElement>(id).disabled=locked));
    $<HTMLButtonElement>('allocation-check').hidden=!locked;
  }
  async function acknowledge(result:any){
    pending=undefined;await crm.draft('managed-allocation-intent',null);lockIntent(false);
    if(result.status==='job_started'){
      activeJob=result.job_id;await crm.draft('managed-active-job',activeJob);dialog.close();void pumpJob();return;
    }
    $('allocation-status').textContent=`Применено сервером. Изменено: ${result.changes.length}; без изменения: ${result.no_op}. Операция ${result.operation_id}`;
    $<HTMLButtonElement>('allocation-apply').disabled=true;await load();
  }
  async function pumpJob(){
    if(!activeJob||!valid())return;
    try{
      let job=await request('GET','assignment-job',undefined,{job_id:activeJob});
      if(!valid())return;
      if(job.state==='running')job=await request('POST','assignment-job-step',{schema_version:1,job_id:activeJob,pack_index:job.next_pack});
      if(!valid())return;
      $('managed-job').innerHTML=`<p>Задание ${esc(job.job_id)} · Всего: ${job.total_slots} · Применено: ${job.applied_slots} · Без изменения: ${job.no_op_slots} · Ожидает: ${job.pending_slots} · Конфликт: ${job.conflict_slots} · Остановлено: ${job.cancelled_slots}</p>${job.state==='running'?'<button id="managed-job-stop">Остановить неприменённые пакеты</button>':`<strong>${esc(({completed:'Завершено',completed_with_conflicts:'Завершено с конфликтами',cancelled:'Остановлено'} as any)[job.state]||job.state)}</strong>`}`;
      if(job.state==='running'){
        $('managed-job-stop').onclick=async()=>{clearTimeout(jobTimer);await request('POST','assignment-job-cancel',{schema_version:1,job_id:activeJob});await pumpJob();};
        jobTimer=setTimeout(()=>void pumpJob(),250);
      }else{activeJob='';await crm.draft('managed-active-job',null);await load();}
    }catch(e){
      if(!valid())return;
      $('managed-job').innerHTML=`<p>${esc((e as Error).message)}. Принятое сервером задание сохранено; продолжение проверяет актуальные права.</p><button id="managed-job-resume">Проверить и продолжить</button><button id="managed-job-stop">Остановить неприменённые пакеты</button>`;
      $('managed-job-resume').onclick=()=>void pumpJob();
      $('managed-job-stop').onclick=async()=>{await request('POST','assignment-job-cancel',{schema_version:1,job_id:activeJob});await pumpJob();};
    }
  }
  async function sendIntent(){
    if(busy)return;busy=true;$<HTMLButtonElement>('allocation-apply').disabled=true;
    $('allocation-status').textContent='Изменяется…';
    try{
      const result=await crm.api('POST','assignment-commit',pending);
      if(!valid())return;
      if(result.ok&&!result.stale)await acknowledge(result.data);
      else if(result.status===0){lockIntent(true);$('allocation-status').textContent='Исход неизвестен. Сохранён тот же ID операции; проверьте квитанцию.';}
      else{pending=undefined;await crm.draft('managed-allocation-intent',null);lockIntent(false);preview=undefined;$('allocation-status').textContent=result.text||'Не применено. Обновите состав.';}
    }finally{busy=false;selectionStatus();}
  }
  async function openEditor(kind:string,work?:string){
    if(!connected){status('Нет подтверждённой связи. Распределение недоступно.');return;}
    action=kind;preview=undefined;
    $('managed-action').textContent=actionName[kind];$('allocation-target').hidden=kind==='remove';
    $<HTMLSelectElement>('allocation-work').value=work||(prefs.work==='all'?'call':prefs.work);
    $<HTMLSelectElement>('allocation-part').disabled=selectionKind==='contracts';
    $<HTMLInputElement>('allocation-search').value='';listTargets();
    pending=(await crm.draft('managed-allocation-intent')).data;
    lockIntent(!!pending);$('allocation-status').textContent=pending?'Есть операция с неизвестным исходом. Проверьте её квитанцию.':'';
    $<HTMLButtonElement>('allocation-apply').disabled=true;dialog.showModal();
    if(!pending)await refreshPreview();
  }
  dialog.querySelector('form')!.onsubmit=async e=>{
    e.preventDefault();if(!preview||busy||pending)return;
    pending={schema_version:1,operation_id:crypto.randomUUID(),preview_id:preview.preview_id};
    const saved=await crm.draft('managed-allocation-intent',pending);if(!saved.ok){pending=undefined;$('allocation-status').textContent='Не удалось защитить ID операции на компьютере.';return;}
    lockIntent(true);await sendIntent();
  };
  $('allocation-check').onclick=async()=>{
    if(!pending||busy)return;
    try{await acknowledge(await request('GET','assignment-receipt',undefined,{operation_id:pending.operation_id}));}
    catch(e){if((e as any).result?.status===404){$('allocation-status').textContent='Квитанции пока нет. Повторяется исходная операция с тем же ID.';await sendIntent();}else $('allocation-status').textContent=(e as Error).message;}
  };
  $('allocation-close').onclick=()=>dialog.close();
  ['allocation-work','allocation-part','allocation-employee'].forEach(id=>$<HTMLSelectElement>(id).onchange=()=>{if(id==='allocation-work')listTargets();schedulePreview();});
  $<HTMLInputElement>('allocation-search').oninput=listTargets;$<HTMLInputElement>('allocation-comment').oninput=schedulePreview;
  host.querySelectorAll<HTMLButtonElement>('[data-allocate]').forEach(b=>b.onclick=protect(()=>openEditor(b.dataset.allocate!)));
  const changeFilters=()=>{prefs.search=$<HTMLInputElement>('managed-search').value;prefs.employee_uuid=$<HTMLSelectElement>('managed-employee').value;prefs.work=$<HTMLSelectElement>('managed-work').value;prefs.unassigned=$<HTMLInputElement>('managed-unassigned').checked?'1':'0';prefs.offset=0;allFilter=false;remember();return load();};
  $('managed-find').onclick=protect(changeFilters);$('managed-refresh').onclick=protect(load);
  $<HTMLInputElement>('managed-search').onkeydown=e=>{if(e.key==='Enter')protect(changeFilters)();};
  ['managed-employee','managed-work','managed-unassigned'].forEach(id=>$<HTMLSelectElement>(id).onchange=protect(changeFilters));
  $('managed-page').onclick=()=>{selectionKind='people';allFilter=false;data.people.forEach((p:any)=>selected.add(p.person_uuid));paint();remember();};
  $('managed-all').onclick=()=>{allFilter=true;selected.clear();selectionStatus();status(`Выбран весь серверный отбор: ${data.total} клиентов. Большой выбор выполняется серверным заданием с атомарными пакетами.`);};
  $('managed-clear').onclick=()=>{allFilter=false;selected.clear();paint();remember();};
  $('managed-prev').onclick=protect(()=>{prefs.offset=Math.max(0,prefs.offset-50);remember();return load();});
  $('managed-next').onclick=protect(()=>{prefs.offset+=50;remember();return load();});
  $<HTMLSelectElement>('managed-zoom').value=String(prefs.zoom);$<HTMLSelectElement>('managed-zoom').onchange=()=>{prefs.zoom=Number($<HTMLSelectElement>('managed-zoom').value);paint();remember();};
  $('managed-history').onclick=protect(async()=>{const result=await request('GET','assignment-history');status('Последние изменения распределения');const panel=document.createElement('dialog');panel.innerHTML=`<h2>История распределения</h2>${result.rows.map((r:any)=>`<p>${esc(r.at)} · ${esc(r.client)} · ${esc(r.external_id)} / ${esc(r.creditor_id)} · ${workName[r.capability]}: ${esc(r.before_name||'Не назначен')} → ${esc(r.after_name||'Не назначен')}<br><small>${esc(r.operation_id)} · автор ${r.actor_uid}</small></p>`).join('')}<button>Закрыть</button>`;host.append(panel);panel.querySelector('button')!.onclick=()=>{panel.close();panel.remove();};panel.showModal();});
  scroll.onscroll=remember;
  staff=(await request('GET','assignment-staff')).staff;
  if(!valid())return ()=>{};
  $<HTMLSelectElement>('managed-employee').insertAdjacentHTML('beforeend',staff.map(p=>`<option value="${esc(p.employee_uuid)}">${esc(p.name)}</option>`).join(''));
  $<HTMLSelectElement>('managed-employee').value=prefs.employee_uuid;$<HTMLSelectElement>('managed-work').value=prefs.work;$<HTMLInputElement>('managed-unassigned').checked=prefs.unassigned==='1';
  const stop=crm.onChange?.((event:any)=>{
    if(event.type!=='live')return;
    const resumed=!connected&&event.connected===true;connected=event.connected===true;selectionStatus();
    if(!connected){previewTurn++;preview=undefined;$<HTMLButtonElement>('allocation-apply').disabled=true;status('Нет подтверждённой связи. Распределение недоступно; выбор сохранён.');return;}
    if(event.dirty||resumed){
      if(dialog.open){preview=undefined;$<HTMLButtonElement>('allocation-apply').disabled=true;$('allocation-status').textContent='Данные изменились. Выбор и комментарий сохранены; состав проверяется заново.';schedulePreview();}
      void load().catch(e=>status(e.message));
    }
  });
  await load();
  activeJob=(await crm.draft('managed-active-job')).data||'';if(activeJob)void pumpJob();
  return ()=>{remember();disposed=true;generation++;previewTurn++;clearTimeout(previewTimer);clearTimeout(jobTimer);stop?.();};
}
