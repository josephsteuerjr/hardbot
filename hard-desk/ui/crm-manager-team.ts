import {mountRoleEditor,type RoleEditorHandle} from './access/role-editor';
import {plural} from './kit/dom';

const esc=(v:any)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const roles:any={employee:'Сотрудник',agent:'Сотрудник',manager:'Руководитель',observer:'Наблюдатель',courier:'Курьер',analyst:'Аналитик',logistic:'Логист',owner:'Владелец',full:'Владелец'};
const states:any={active:'Доступ открыт',pending:'Ждёт подтверждения',awaiting:'Ждёт подтверждения',disabled:'Отключён',revoked:'Отозван',none:'Не создан',ambiguous:'Требует сверки'};
const workNames: Record<string,string>={call:'Звонки',visit:'Выезды',delivery:'Передачи'};
const stamp=(v:any)=>v?new Date(v).toLocaleString('ru-RU'):'—';
const loginLabel=(row:any)=>row.account?.username || (row.login_status==='ambiguous'?"Требует сверки":row.accountUnknown?'Данные входа недоступны':'Аккаунт не создан');
const loginState=(row:any)=>states[row.account?.status]||states[row.login_status]||(row.accountUnknown?'Недоступно':'Не создан');
const hasCounts=(value:any)=>Number.isInteger(value?.people)&&Number.isInteger(value?.contracts);
const countsText=(value:any)=>hasCounts(value)?`${value.people} ${plural(value.people,'клиент','клиента','клиентов')} · ${value.contracts} КД`:'—';
const accountRole=(account:any,person:any)=>account?.role==='agent'&&person?.role ? person.role : account?.role||person?.role||'employee';

export async function mountManagerTeam(host:HTMLElement,crm:any,context:any,valid:()=>boolean,openPortfolio:(id:string)=>void){
  let disposed=false,generation=0,rows:any[]=[],selected='',query='',busy=false,profileOpen=false;
  let profileKey='',roleEditor:RoleEditorHandle|undefined,roleEditorGeneration=0;
  const owner=context?.owner===true&&typeof crm.admin==='function';
  const portfolioCounts=new Map<string,{people:number;contracts:number}|null>();
  const current=()=>!disposed&&valid();
  const disposeRoleEditor=()=>{roleEditorGeneration++;roleEditor?.dispose();roleEditor=undefined;};
  host.innerHTML=`<div class="toolbar"><input id="team-search" type="search" placeholder="Сотрудник, логин, роль"><button id="team-refresh">Обновить</button><span id="team-count"></span></div><p id="team-status" role="status"></p><div class="manager-team-layout"><div id="team-list"></div><section id="team-profile"></section></div>`;
  const $=<T extends HTMLElement=HTMLElement>(id:string)=>host.querySelector<T>('#'+id)!;
  const status=(s:string)=>{if(current())$('team-status').textContent=s;};
  const protect=(fn:()=>any)=>()=>{void Promise.resolve().then(fn).catch(e=>status(e.message));};
  const request=async(method:string,resource:string,payload?:any)=>{
    const r=await crm.api(method,'manager-access',method==='POST'?{resource,payload}:undefined,method==='GET'?{resource}:undefined);
    if(!r.ok||r.stale||r.data?.ok===false)throw Object.assign(Error(r.text||r.data?.error||'Служба входа недоступна.'),{status:r.status});return r.data;
  };
  const adminRequest=async(method:string,path:string,body?:any)=>{
    const r=await crm.admin('local',['/admin',{method,path,body}]);
    if(!r?.ok||r.data?.ok===false||r.data?.error)
      throw Error(r.data?.text||r.data?.error||r.text||r.error||'Административная служба входа недоступна.');
    return r.data;
  };
  function paint(){
    const filtered=rows.filter(r=>[r.employee,r.account?.username,roles[r.role],r.office].join(' ').toLocaleLowerCase().includes(query));
    $('team-count').textContent=`${filtered.length} ${plural(filtered.length,'сотрудник','сотрудника','сотрудников')}`;
    $('team-list').innerHTML=`<table class="team-table"><thead><tr><th>Сотрудник</th><th>Роль</th><th>Вход</th></tr></thead><tbody>${filtered.map(r=>`<tr class="${r.key===selected?'active':''}"><td><button class="link" data-team-person="${esc(r.key)}">${esc(r.employee)}</button><small>${esc(loginLabel(r))}</small></td><td>${esc(roles[r.role]||r.role||'Сотрудник')}</td><td>${esc(r.account?.password_review_pending?'Смена пароля':loginState(r))}</td></tr>`).join('')}</tbody></table>`;
    host.querySelectorAll<HTMLButtonElement>('[data-team-person]').forEach(b=>b.onclick=()=>{selected=b.dataset.teamPerson!;profileOpen=true;paint();});
    const r=rows.find(r=>r.key===selected);
    if(!r){profileKey='';disposeRoleEditor();$('team-profile').innerHTML='<p>Выберите сотрудника.</p>';return;}
    const actorProfileKey=`${r.key}:${r.account?.actor_uid||'none'}`;
    if(profileKey!==actorProfileKey){
      profileKey=actorProfileKey;
      $('team-profile').innerHTML='<div id="team-details"></div><div id="team-role-editor"></div>';
      void mountRoles(r,actorProfileKey);
    }
    paintDetails(r);
  }
  function paintDetails(r:any){
    const a=r.account,devices=a?.devices||[];
    const adminReset=r.adminAccount&&!a.owner_root;
    const adminApprove=r.adminAccount&&a.role!=='agent'&&a.created_by!=='self-registration';
    const works=[...new Set<string>(r.crm?.work_kinds||[])].map(kind=>workNames[kind]).filter(Boolean);
    const counts=hasCounts(r.crm)?r.crm:portfolioCounts.get(r.crm?.employee_uuid);
    $('team-details').innerHTML=`<h2>${esc(r.employee)}</h2><dl class="team-facts"><dt>Роль</dt><dd>${esc(roles[r.role]||r.role||'Сотрудник')}</dd><dt>Офис</dt><dd>${esc(r.office||'—')}</dd><dt>Логин</dt><dd>${esc(loginLabel(r))}</dd><dt>Вход</dt><dd>${esc(a?.password_review_pending?a.must_change_password?'Сотрудник должен сменить временный пароль':'Подтвердите личный пароль на устройстве':loginState(r))}</dd><dt>Работы</dt><dd>${works.join(', ')||'—'}</dd><dt>Портфель</dt><dd id="team-portfolio-counts">${countsText(counts)}</dd></dl><div class="actions">${r.crm?'<button id="team-portfolio">Открыть портфель</button>':''}${r.can_reset||adminReset?'<button id="team-password">Сбросить пароль</button>':''}</div><h3>Устройства</h3>${devices.length?devices.map((d:any,i:number)=>`<div class="team-device"><strong>${d.kind==='desktop'?'Компьютер':'Телефон'} · ${esc(d.label||'Без названия')}</strong><p>${esc(states[d.status]||d.status)} · Последний вход ${stamp(d.last_seen||d.at)}</p>${d.client_version?`<p>Версия ${esc(d.client_version)}</p>`:''}<div class="actions">${(r.can_approve||adminApprove)&&d.status==='pending'?`<button data-team-approve="${i}">Подтвердить устройство</button>`:''}${(r.can_revoke||adminReset)&&d.status!=='revoked'?`<button data-team-revoke="${i}">Отозвать доступ</button>`:''}</div></div>`).join(''):r.accountUnknown?'<p>Данные устройств недоступны.</p>':'<p>Устройства не подключены.</p>'}${r.can_remove?`<details class="team-roster-actions"><summary>Состав команды</summary><button id="team-board">${r.on_board?'Убрать из списка':'Вернуть в список'}</button></details>`:''}`;
    if(crm.platform==='mobile'){
      $('team-list').hidden=profileOpen;$('team-profile').hidden=!profileOpen;
      if(profileOpen){const back=document.createElement('button');back.className='link mobile-team-back';back.textContent='← Команда';back.onclick=()=>{profileOpen=false;paint();};$('team-details').prepend(back);}
    }
    if($('team-portfolio'))$('team-portfolio').onclick=()=>openPortfolio(r.crm.employee_uuid);
    if($('team-password'))$('team-password').onclick=protect(()=>adminReset?adminPassword(r):password(r));
    if($('team-board'))$('team-board').onclick=protect(()=>confirmAction(r.on_board?'Убрать из списка?':'Вернуть в список?',r.employee,()=>request('POST','board',{action:r.on_board?'hide':'add',employee:r.employee,operation_id:crypto.randomUUID()})));
    host.querySelectorAll<HTMLButtonElement>('[data-team-approve]').forEach(b=>b.onclick=protect(()=>{const d=devices[Number(b.dataset.teamApprove)];return confirmAction('Подтвердить устройство',`${r.employee} · ${d.label||'Устройство'}`,()=>adminApprove?adminRequest('POST','/admin/account_approve.json',{device_id:d.id}):request('POST','approve',{device_id:d.id,observed_pc:d.kind==='desktop'}),d.kind==='desktop'?'Я лично сверил сотрудника и его компьютер':'Я подтвердил сотрудника и его телефон');}));
    host.querySelectorAll<HTMLButtonElement>('[data-team-revoke]').forEach(b=>b.onclick=protect(()=>{const d=devices[Number(b.dataset.teamRevoke)];return confirmAction('Отозвать доступ устройства',`${r.employee} · ${d.label||'Устройство'}. Текущий вход будет закрыт. Для нового входа потребуется подтверждение.`,()=>adminReset?adminRequest('POST','/admin/account_action.json',{action:'revoke',device_id:d.id}):request('POST','revoke',{device_id:d.id}));}));
    if(r.crm?.employee_uuid&&!hasCounts(r.crm))void loadPortfolioCounts(r.crm.employee_uuid);
  }
  async function loadPortfolioCounts(employee:string){
    if(portfolioCounts.has(employee))return;
    portfolioCounts.set(employee,null);
    const n=generation;
    try{
      const result=await crm.api('GET','work-portfolio',undefined,{employee_uuid:employee,limit:1});
      if(!result?.ok||!current()||n!==generation)return;
      const counts={people:result.data.total_people,contracts:result.data.total_contracts};
      portfolioCounts.set(employee,counts);
      if(rows.find(r=>r.key===selected)?.crm?.employee_uuid===employee)
        $('team-portfolio-counts').textContent=countsText(counts);
    }catch{/* The rest of the profile remains available if the portfolio cannot be read. */}
  }
  async function mountRoles(r:any,actorProfileKey:string){
    const key=r.key,n=++roleEditorGeneration,roleHost=$('team-role-editor');
    roleEditor?.dispose();roleEditor=undefined;
    const actorUid=Number(r.account?.actor_uid);
    if(!Number.isInteger(actorUid)||actorUid<=0){roleHost.innerHTML='<p class="access-editor-meta">Для управления полномочиями нужен связанный аккаунт.</p>';return;}
    roleHost.innerHTML='<p class="access-editor-meta">Загрузка полномочий…</p>';
    try{
      const handle=await mountRoleEditor(roleHost,crm,{actorUid,employeeName:r.employee,owner:context?.role==='owner'||context?.owner===true,
        employees:rows.filter(row=>row.crm?.employee_uuid).map(row=>({employee_uuid:row.crm.employee_uuid,name:row.employee})),
        valid:()=>current()&&profileKey===actorProfileKey&&selected===key,
        onApplied:()=>load(true)});
      if(n!==roleEditorGeneration||!current()||profileKey!==actorProfileKey)handle.dispose();else roleEditor=handle;
    }catch(e){if(current()&&profileKey===actorProfileKey)status((e as Error).message);}
  }
  async function confirmAction(title:string,text:string,action:()=>Promise<any>,check?:string){
    const dialog=document.createElement('dialog');dialog.innerHTML=`<form><h2>${esc(title)}</h2><p>${esc(text)}</p>${check?`<label class="check"><input type="checkbox" required>${esc(check)}</label>`:''}<p role="status"></p><div class="actions"><button type="button">Отмена</button><button type="submit">Подтвердить</button></div></form>`;host.append(dialog);
    dialog.querySelector('button')!.onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
    dialog.querySelector('form')!.onsubmit=async e=>{
      e.preventDefault();
      const button=dialog.querySelector<HTMLButtonElement>('[type="submit"]')!;
      button.disabled=true;
      try{await action();}
      catch(e){dialog.querySelector('[role="status"]')!.textContent=(e as Error).message;button.disabled=false;return;}
      try{await load(true);}
      catch(e){status((e as Error).message);}
      finally{dialog.close();}
    };dialog.showModal();
  }
  async function password(row:any){
    const key='manager-password-intent:'+row.account.username;
    let intent=(await crm.draft(key)).data;
    if(!current())return;
    const dialog=document.createElement('dialog');dialog.innerHTML=`<form><h2>Сбросить пароль</h2><p>${esc(row.employee)} · ${esc(row.account.username)}</p><p>Входы будут закрыты. Сотрудник сменит временный пароль на личный, затем подтвердите его устройство.</p><label>Временный пароль <input name="password" type="password" autocomplete="new-password" minlength="8" required></label><label>Повторите пароль <input name="repeat" type="password" autocomplete="new-password" minlength="8" required></label><p role="status"></p><div class="actions"><button type="button">Закрыть</button><button type="submit">${intent?'Проверить исход операции':'Сбросить пароль'}</button></div></form>`;host.append(dialog);
    const inputs=[...dialog.querySelectorAll<HTMLInputElement>('input')];if(intent)inputs.forEach(i=>{i.value=intent.password;i.disabled=true;});
    dialog.querySelector('button')!.onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
    dialog.querySelector('form')!.onsubmit=async e=>{
      e.preventDefault();const button=dialog.querySelector<HTMLButtonElement>('[type="submit"]')!;button.disabled=true;
      try{
        if(!intent){if(inputs[0].value!==inputs[1].value)throw Error('Пароли не совпадают.');intent={username:row.account.username,password:inputs[0].value,operation_id:crypto.randomUUID()};const saved=await crm.draft(key,intent);if(!saved.ok){intent=null;throw Error(crm.platform==='mobile'?'Не удалось защитить операцию на телефоне.':'Не удалось защитить операцию на компьютере.');}}
        inputs.forEach(i=>i.disabled=true);
        await request('POST','reset',intent);await crm.draft(key,null);intent=null;
      }catch(e){
        if([400,409,422].includes((e as any).status)){await crm.draft(key,null);intent=null;inputs.forEach(i=>i.disabled=false);}
        dialog.querySelector('[role="status"]')!.textContent=(e as Error).message;button.textContent=intent?'Проверить исход операции':'Сбросить пароль';button.disabled=false;return;
      }
      try{await load(true);status('Временный пароль установлен. Передайте его сотруднику.');}
      catch(e){status((e as Error).message);}
      finally{dialog.close();}
    };dialog.showModal();
  }
  function adminPassword(row:any){
    const dialog=document.createElement('dialog');
    dialog.innerHTML=`<form><h2>Сбросить пароль</h2><p>${esc(row.employee)} · ${esc(row.account.username)}</p><p>Текущие входы будут закрыты. Сотрудник получит временный пароль и сменит его при входе.</p><div data-issued></div><p role="status"></p><div class="actions"><button type="button">Закрыть</button><button type="submit">Сбросить пароль</button></div></form>`;
    host.append(dialog);
    const close=dialog.querySelector<HTMLButtonElement>('[type=button]')!;
    const submit=dialog.querySelector<HTMLButtonElement>('[type=submit]')!;
    const message=dialog.querySelector<HTMLElement>('[role=status]')!;
    close.onclick=()=>dialog.close();
    dialog.onclose=()=>dialog.remove();
    dialog.querySelector('form')!.onsubmit=async event=>{
      event.preventDefault();
      if(submit.disabled||!current())return;
      submit.disabled=true;
      try{
        const result=await adminRequest('POST','/admin/account_action.json',{action:'reset',username:row.account.username});
        if(typeof result?.temporary_password!=='string')throw Error('Служба входа не вернула временный пароль.');
        if(!current()||!dialog.open)return;
        const issued=dialog.querySelector<HTMLElement>('[data-issued]')!;
        issued.innerHTML='<label class="field">Временный пароль<input type="text" readonly autocomplete="off"></label>';
        const input=issued.querySelector('input')!;
        input.value=result.temporary_password;
        input.select();
        submit.hidden=true;
        await load(true).catch(error=>status(error.message));
      }catch(error){message.textContent=(error as Error).message;}
    };
    dialog.showModal();
  }
  async function load(force=false){
    if(busy&&!force)return;busy=true;const n=++generation;
    try{
      const [teamResult,staffResult,accountsResult]=await Promise.allSettled([
        request('GET','team'),
        context.distribution?.enabled?crm.api('GET','assignment-staff'):Promise.resolve({ok:true,data:{staff:context.staff||[]}}),
        owner?adminRequest('GET','/admin/accounts.json'):Promise.resolve(null),
      ]);
      if(!current()||n!==generation)return;
      portfolioCounts.clear();
      const staffById=new Map<string,any>();
      const addStaff=(person:any)=>{
        const existing=staffById.get(person.employee_uuid);
        staffById.set(person.employee_uuid,existing?{...existing,...person,
          work_kinds:[...new Set([...(existing.work_kinds||[]),...(person.work_kinds||[])])]}:person);
      };
      if(owner)for(const person of context.staff||[])addStaff(person);
      if(staffResult.status==='fulfilled'&&staffResult.value.ok)
        for(const person of staffResult.value.data.staff||[])addStaff(person);
      const staff=[...staffById.values()];
      const staffByActor=new Map<number,any>();
      for(const person of staff)for(const actor of person.actor_uids||[])
        staffByActor.set(actor,staffByActor.has(actor)?null:person);
      const team=teamResult.status==='fulfilled'?teamResult.value.employees||[]:[];
      const found=accountsResult.status==='fulfilled'&&Array.isArray(accountsResult.value?.accounts);
      const accounts=found?accountsResult.value.accounts:[];
      const accountsById=new Map(accounts.map((account:any)=>[account.id,account]));
      const used=new Set<string>(), linked=new Set<number>();
      rows=team.map((row:any)=>{
        const adminAccount=accountsById.get(row.account_id) as any;
        const account=adminAccount||row.account;
        const person=staffByActor.get(account?.actor_uid);
        if(person)used.add(person.employee_uuid);
        if(adminAccount)linked.add(adminAccount.id);
        return {...row,account,key:'primary:'+row.employee_id,crm:person,
          adminAccount:!!adminAccount,role:accountRole(account,person)};
      });
      for(const account of accounts){
        if(linked.has(account.id))continue;
        const person=staffByActor.get(account.actor_uid);
        if(person)used.add(person.employee_uuid);
        rows.push({key:'account:'+account.id,employee:account.employee,office:account.office,role:accountRole(account,person),
          crm:person,account,adminAccount:true});
      }
      rows.push(...staff.filter(s=>!used.has(s.employee_uuid)).map(s=>({key:'crm:'+s.employee_uuid,
        employee:s.name,role:s.role,crm:s,account:null,accountUnknown:!found})));
      rows.sort((a,b)=>a.employee.localeCompare(b.employee,'ru'));if(!rows.some(r=>r.key===selected))selected=rows[0]?.key||'';paint();
      status(owner&&!found?'Не удалось получить административный список аккаунтов.':
        teamResult.status==='rejected'?teamResult.reason.message:'Обновлено '+new Date().toLocaleTimeString('ru-RU'));
    }finally{if(n===generation)busy=false;}
  }
  $<HTMLInputElement>('team-search').oninput=()=>{query=$<HTMLInputElement>('team-search').value.toLocaleLowerCase();paint();};$('team-refresh').onclick=protect(load);
  const focus=()=>{if(current()&&!host.querySelector('dialog[open]'))void load().catch(e=>status(e.message));};window.addEventListener('focus',focus);
  const stop=crm.onChange?.((e:any)=>{if(e.type==='live'&&e.dirty)focus();});
  await load();return ()=>{disposed=true;generation++;disposeRoleEditor();stop?.();window.removeEventListener('focus',focus);};
}
