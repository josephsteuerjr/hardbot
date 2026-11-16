const esc=(v:any)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const roles:any={employee:'Сотрудник',agent:'Сотрудник',manager:'Руководитель',observer:'Наблюдатель',courier:'Курьер',analyst:'Аналитик',owner:'Владелец'};
const states:any={active:'Доступ открыт',pending:'Ждёт подтверждения',awaiting:'Ждёт подтверждения',disabled:'Отключён',revoked:'Отозван',none:'Не создан',ambiguous:'Требует сверки'};
const stamp=(v:any)=>v?new Date(v).toLocaleString('ru-RU'):'—';

export async function mountManagerTeam(host:HTMLElement,crm:any,context:any,valid:()=>boolean,openPortfolio:(id:string)=>void){
  let disposed=false,generation=0,rows:any[]=[],selected='',query='',busy=false,profileOpen=false;
  const current=()=>!disposed&&valid();
  host.innerHTML=`<div class="toolbar"><input id="team-search" type="search" placeholder="Сотрудник, логин, роль"><button id="team-refresh">Обновить</button><span id="team-count"></span></div><p id="team-status" role="status"></p><div class="manager-team-layout"><div id="team-list"></div><section id="team-profile"></section></div>`;
  const $=<T extends HTMLElement=HTMLElement>(id:string)=>host.querySelector<T>('#'+id)!;
  const status=(s:string)=>{if(current())$('team-status').textContent=s;};
  const protect=(fn:()=>any)=>()=>{void Promise.resolve().then(fn).catch(e=>status(e.message));};
  const request=async(method:string,resource:string,payload?:any)=>{
    const r=await crm.api(method,'manager-access',method==='POST'?{resource,payload}:undefined,method==='GET'?{resource}:undefined);
    if(!r.ok||r.stale||r.data?.ok===false)throw Object.assign(Error(r.text||r.data?.error||'Служба входа недоступна.'),{status:r.status});return r.data;
  };
  function paint(){
    const filtered=rows.filter(r=>[r.employee,r.account?.username,roles[r.role],r.office].join(' ').toLocaleLowerCase().includes(query));
    $('team-count').textContent=`${filtered.length} сотрудников`;
    $('team-list').innerHTML=`<table class="team-table"><thead><tr><th>Сотрудник</th><th>Роль</th><th>Вход</th></tr></thead><tbody>${filtered.map(r=>`<tr class="${r.key===selected?'active':''}"><td><button class="link" data-team-person="${esc(r.key)}">${esc(r.employee)}</button><small>${esc(r.account?.username||'Аккаунт не создан')}</small></td><td>${esc(roles[r.role]||r.role||'Сотрудник')}</td><td>${esc(r.account?.password_review_pending?'Смена пароля':states[r.account?.status]||states[r.login_status]||'Не создан')}</td></tr>`).join('')}</tbody></table>`;
    host.querySelectorAll<HTMLButtonElement>('[data-team-person]').forEach(b=>b.onclick=()=>{selected=b.dataset.teamPerson!;profileOpen=true;paint();});
    const r=rows.find(r=>r.key===selected);
    if(!r){$('team-profile').innerHTML='<p>Выберите сотрудника.</p>';return;}
    const a=r.account,devices=a?.devices||[];
    $('team-profile').innerHTML=`<h2>${esc(r.employee)}</h2><dl class="team-facts"><dt>Роль</dt><dd>${esc(roles[r.role]||r.role||'Сотрудник')}</dd><dt>Офис</dt><dd>${esc(r.office||'—')}</dd><dt>Логин</dt><dd>${esc(a?.username||'Аккаунт не создан')}</dd><dt>Вход</dt><dd>${esc(a?.password_review_pending?a.must_change_password?'Сотрудник должен сменить временный пароль':'Подтвердите личный пароль на устройстве':states[a?.status]||states[r.login_status]||'Не создан')}</dd><dt>Работы</dt><dd>${(r.crm?.work_kinds||[]).map((w:string)=>w==='call'?'Звонки':'Выезды').join(', ')||'—'}</dd><dt>Портфель</dt><dd>${r.crm?`${r.crm.people} клиентов · ${r.crm.contracts} КД`:'—'}</dd></dl><div class="actions">${r.crm?'<button id="team-portfolio">Открыть портфель</button>':''}${r.can_reset?'<button id="team-password">Сбросить пароль</button>':''}</div><h3>Устройства</h3>${devices.length?devices.map((d:any,i:number)=>`<div class="team-device"><strong>${d.kind==='desktop'?'Компьютер':'Телефон'} · ${esc(d.label||'Без названия')}</strong><p>${esc(states[d.status]||d.status)} · Последний вход ${stamp(d.last_seen||d.at)}</p>${d.client_version?`<p>Версия ${esc(d.client_version)}</p>`:''}<div class="actions">${r.can_approve&&d.status==='pending'?`<button data-team-approve="${i}">Подтвердить устройство</button>`:''}${r.can_revoke?`<button data-team-revoke="${i}">Отозвать доступ</button>`:''}</div></div>`).join(''):'<p>Устройства не подключены.</p>'}${r.can_remove?`<details class="team-roster-actions"><summary>Состав команды</summary><button id="team-board">${r.on_board?'Убрать из списка':'Вернуть в список'}</button></details>`:''}`;
    if(crm.platform==='mobile'){
      $('team-list').hidden=profileOpen;$('team-profile').hidden=!profileOpen;
      if(profileOpen){const back=document.createElement('button');back.className='link mobile-team-back';back.textContent='← Команда';back.onclick=()=>{profileOpen=false;paint();};$('team-profile').prepend(back);}
    }
    if($('team-portfolio'))$('team-portfolio').onclick=()=>openPortfolio(r.crm.employee_uuid);
    if($('team-password'))$('team-password').onclick=protect(()=>password(r));
    if($('team-board'))$('team-board').onclick=protect(()=>confirmAction(r.on_board?'Убрать из списка?':'Вернуть в список?',r.employee,()=>request('POST','board',{action:r.on_board?'hide':'add',employee:r.employee,operation_id:crypto.randomUUID()})));
    host.querySelectorAll<HTMLButtonElement>('[data-team-approve]').forEach(b=>b.onclick=protect(()=>{const d=devices[Number(b.dataset.teamApprove)];return confirmAction('Подтвердить устройство',`${r.employee} · ${d.label||'Устройство'}`,()=>request('POST','approve',{device_id:d.id,observed_pc:d.kind==='desktop'}),d.kind==='desktop'?'Я лично сверил сотрудника и его компьютер':'Я подтвердил сотрудника и его телефон');}));
    host.querySelectorAll<HTMLButtonElement>('[data-team-revoke]').forEach(b=>b.onclick=protect(()=>{const d=devices[Number(b.dataset.teamRevoke)];return confirmAction('Отозвать доступ устройства',`${r.employee} · ${d.label||'Устройство'}. Текущий вход будет закрыт. Для нового входа потребуется подтверждение.`,()=>request('POST','revoke',{device_id:d.id}));}));
  }
  async function confirmAction(title:string,text:string,action:()=>Promise<any>,check?:string){
    const dialog=document.createElement('dialog');dialog.innerHTML=`<form><h2>${esc(title)}</h2><p>${esc(text)}</p>${check?`<label class="check"><input type="checkbox" required>${esc(check)}</label>`:''}<p role="status"></p><div class="actions"><button type="button">Отмена</button><button type="submit">Подтвердить</button></div></form>`;host.append(dialog);
    dialog.querySelector('button')!.onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
    dialog.querySelector('form')!.onsubmit=async e=>{e.preventDefault();const button=dialog.querySelector<HTMLButtonElement>('[type="submit"]')!;button.disabled=true;try{await action();dialog.close();await load();}catch(e){dialog.querySelector('[role="status"]')!.textContent=(e as Error).message;button.disabled=false;}};dialog.showModal();
  }
  async function password(row:any){
    const key='manager-password-intent:'+row.account.username;
    let intent=(await crm.draft(key)).data;
    const dialog=document.createElement('dialog');dialog.innerHTML=`<form><h2>Сбросить пароль</h2><p>${esc(row.employee)} · ${esc(row.account.username)}</p><p>Входы будут закрыты. Сотрудник сменит временный пароль на личный, затем подтвердите его устройство.</p><label>Временный пароль <input name="password" type="password" autocomplete="new-password" minlength="8" required></label><label>Повторите пароль <input name="repeat" type="password" autocomplete="new-password" minlength="8" required></label><p role="status"></p><div class="actions"><button type="button">Закрыть</button><button type="submit">${intent?'Проверить исход операции':'Сбросить пароль'}</button></div></form>`;host.append(dialog);
    const inputs=[...dialog.querySelectorAll<HTMLInputElement>('input')];if(intent)inputs.forEach(i=>{i.value=intent.password;i.disabled=true;});
    dialog.querySelector('button')!.onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
    dialog.querySelector('form')!.onsubmit=async e=>{
      e.preventDefault();const button=dialog.querySelector<HTMLButtonElement>('[type="submit"]')!;button.disabled=true;
      try{
        if(!intent){if(inputs[0].value!==inputs[1].value)throw Error('Пароли не совпадают.');intent={username:row.account.username,password:inputs[0].value,operation_id:crypto.randomUUID()};const saved=await crm.draft(key,intent);if(!saved.ok){intent=null;throw Error(crm.platform==='mobile'?'Не удалось защитить операцию на телефоне.':'Не удалось защитить операцию на компьютере.');}}
        inputs.forEach(i=>i.disabled=true);
        await request('POST','reset',intent);await crm.draft(key,null);intent=null;dialog.close();await load();status('Временный пароль установлен. Передайте его сотруднику.');
      }catch(e){
        if([400,409,422].includes((e as any).status)){await crm.draft(key,null);intent=null;inputs.forEach(i=>i.disabled=false);}
        dialog.querySelector('[role="status"]')!.textContent=(e as Error).message;button.textContent=intent?'Проверить исход операции':'Сбросить пароль';button.disabled=false;
      }
    };dialog.showModal();
  }
  async function load(){
    if(busy)return;busy=true;const n=++generation;
    try{
      const [teamResult,staffResult]=await Promise.allSettled([request('GET','team'),context.distribution?.enabled?crm.api('GET','assignment-staff'):Promise.resolve({ok:true,data:{staff:context.staff||[]}})]);
      if(!current()||n!==generation)return;
      const staff=staffResult.status==='fulfilled'&&staffResult.value.ok?staffResult.value.data.staff:[];
      const team=teamResult.status==='fulfilled'?teamResult.value.employees:[];
      const used=new Set();rows=team.map((r:any)=>{const matches=staff.filter((s:any)=>s.actor_uids?.includes(r.account?.actor_uid));const person=matches.length===1?matches[0]:undefined;if(person)used.add(person.employee_uuid);return {...r,key:'primary:'+r.employee_id,crm:person,role:person?.role||r.account?.role||'employee'};});
      rows.push(...staff.filter((s:any)=>!used.has(s.employee_uuid)).map((s:any)=>({key:'crm:'+s.employee_uuid,employee:s.name,role:s.role,crm:s,account:null})));
      rows.sort((a,b)=>a.employee.localeCompare(b.employee,'ru'));if(!rows.some(r=>r.key===selected))selected=rows[0]?.key||'';paint();
      status(teamResult.status==='rejected'?teamResult.reason.message:'Обновлено '+new Date().toLocaleTimeString('ru-RU'));
    }finally{busy=false;}
  }
  $<HTMLInputElement>('team-search').oninput=()=>{query=$<HTMLInputElement>('team-search').value.toLocaleLowerCase();paint();};$('team-refresh').onclick=protect(load);
  const focus=()=>{if(current()&&!host.querySelector('dialog[open]'))void load().catch(e=>status(e.message));};window.addEventListener('focus',focus);
  const stop=crm.onChange?.((e:any)=>{if(e.type==='live'&&e.dirty)focus();});
  await load();return ()=>{disposed=true;generation++;stop?.();window.removeEventListener('focus',focus);};
}
