const esc=(v:any)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
type Selection={ids:string[];filters:any;all:boolean;rows:any[]};
export async function mountAllocation(host:HTMLElement,crm:any,selection:()=>Selection,valid:()=>boolean,changed:()=>Promise<void>){
  let disposed=false,preview:any,intent:any,action='replace',busy=false,turn=0,connected=true,job='',timer:any;
  const current=()=>!disposed&&valid();
  host.innerHTML=`<div class="toolbar allocation-actions"><span>Распределение</span><button data-allocation="assign">Назначить</button><button data-allocation="replace">Заменить</button><button data-allocation="remove">Снять</button><button id="allocation-history" class="link">История</button></div><div id="allocation-job" role="status"></div><dialog id="allocation-dialog"><form><h2 id="allocation-title"></h2><div class="toolbar"><label>Работа<select id="allocation-kind"><option value="call">Звонки</option><option value="visit">Выезды</option><option value="both">Звонки и выезды</option></select></label><label id="allocation-target-label">Сотрудник<select id="allocation-target"></select></label></div><label>Комментарий<input id="allocation-note" maxlength="1000"></label><div id="allocation-preview"></div><p id="allocation-status" role="status"></p><div class="actions"><button type="button" id="allocation-close">Закрыть</button><button type="submit" id="allocation-submit">Применить</button></div></form></dialog>`;
  const $=<T extends HTMLElement=HTMLElement>(id:string)=>host.querySelector<T>('#'+id)!;
  const dialog=$<HTMLDialogElement>('allocation-dialog');
  const recover=document.createElement('button');recover.id='allocation-recover';recover.textContent='Проверить незавершённое распределение';recover.hidden=true;
  $('allocation-job').before(recover);
  recover.onclick=()=>host.querySelector<HTMLButtonElement>('[data-allocation=replace]')?.click();
  const request=async(method:string,action:string,body?:any,query?:any)=>{const r=await crm.api(method,action,body,query);if(!r.ok||r.stale)throw Object.assign(Error(r.text||'Нужна подтверждённая связь.'),{result:r});return r.data;};
  const protect=(fn:()=>any)=>()=>{void Promise.resolve().then(fn).catch(e=>{if(current())$('allocation-status').textContent=e.message;});};
  const staff=(await request('GET','assignment-staff')).staff;
  function targets(){const kind=$<HTMLSelectElement>('allocation-kind').value,prior=$<HTMLSelectElement>('allocation-target').value;$<HTMLSelectElement>('allocation-target').innerHTML='<option value="">Выберите сотрудника</option>'+staff.filter((s:any)=>s.eligible&&(kind==='both'?['call','visit'].every(k=>s.work_kinds.includes(k)):s.work_kinds.includes(kind))).map((s:any)=>`<option value="${esc(s.employee_uuid)}">${esc(s.name)} · ${s.contracts} КД</option>`).join('');$<HTMLSelectElement>('allocation-target').value=prior;}
  function lock(){recover.hidden=!intent;['allocation-kind','allocation-target','allocation-note'].forEach(id=>$<HTMLInputElement>(id).disabled=!!intent||busy);$<HTMLButtonElement>('allocation-submit').textContent=intent?'Проверить исход операции':'Применить';}
  async function check(){
    if(intent||busy)return;const n=++turn;preview=null;$<HTMLButtonElement>('allocation-submit').disabled=true;targets();
    const s=selection(),kind=$<HTMLSelectElement>('allocation-kind').value,target=$<HTMLSelectElement>('allocation-target').value;
    if(!s.all&&!s.ids.length){$('allocation-preview').textContent='Выберите строки в Реестре.';return;}
    if(action!=='remove'&&!target){$('allocation-preview').textContent='Выберите сотрудника.';return;}
    const result=await request('POST','assignment-preview',{schema_version:1,action,employee_uuid:action==='remove'?null:target,work_kinds:kind==='both'?['call','visit']:[kind],selection:s.all?{kind:'filter',part:'filtered',filters:s.filters}:{kind:'contracts',ids:s.ids,part:'filtered',filters:s.filters},comment:$<HTMLInputElement>('allocation-note').value});
    if(!current()||n!==turn||!dialog.open)return;preview=result;
    $('allocation-preview').innerHTML=`<p><strong>${result.people}</strong> клиентов · <strong>${result.contracts}</strong> договоров · ${result.slots_count} назначений</p><p>Без изменений: ${result.no_op}.${result.large?' Будет выполнено серверное задание. Можно перейти в другой раздел.':''}</p><details><summary>Проверить состав${result.large?' · первые 200 назначений':''}</summary>${result.slots.map((slot:any)=>{const row=s.rows.find(r=>r.contract_uuid===slot.contract_uuid);return `<p>${esc(row?row.client_name+' · '+row.external_id:slot.external_id||'Договор из всего отбора')} · ${slot.capability==='call'?'Звонки':'Выезды'} · ${esc(staff.find((p:any)=>p.employee_uuid===slot.employee_uuid)?.name||'Не назначен')}</p>`;}).join('')}</details>`;
    $('allocation-status').textContent='Состав проверен до '+new Date(result.expires_at).toLocaleTimeString('ru-RU');$<HTMLButtonElement>('allocation-submit').disabled=!connected;
  }
  async function monitor(){
    clearTimeout(timer);if(!job||!current())return;
    try{
      const result=await request('GET','assignment-job',undefined,{job_id:job});if(!current())return;
      $('allocation-job').innerHTML=`<p>${result.state==='running'?'Выполняется на сервере':'Распределение завершено'} · Применено ${result.applied_slots} · Без изменений ${result.no_op_slots} · Ожидает ${result.pending_slots} · Конфликт ${result.conflict_slots} · Остановлено ${result.cancelled_slots}</p>${result.state==='running'?'<button id="allocation-stop">Остановить оставшиеся пакеты</button>':''}`;
      if(result.state==='running'){$('allocation-stop').onclick=protect(async()=>{await request('POST','assignment-job-cancel',{schema_version:1,job_id:job});await monitor();});timer=setTimeout(()=>void monitor(),1500);}
      else{job='';await crm.draft('managed-active-job',null);await changed();}
    }catch(e){if(current()){$('allocation-job').textContent=(e as Error).message;timer=setTimeout(()=>void monitor(),5000);}}
  }
  async function accepted(result:any){
    intent=null;await crm.draft('managed-allocation-intent',null);lock();
    if(result.status==='job_started'){job=result.job_id;await crm.draft('managed-active-job',job);dialog.close();void monitor();}
    else{$('allocation-status').textContent=`Применено: ${result.changes.length}; без изменений: ${result.no_op}.`;$<HTMLButtonElement>('allocation-submit').disabled=true;await changed();}
  }
  dialog.querySelector('form')!.onsubmit=async e=>{
    e.preventDefault();if(busy||!connected||(!preview&&!intent))return;busy=true;$<HTMLButtonElement>('allocation-submit').disabled=true;
    try{
      if(!intent){intent={schema_version:1,operation_id:crypto.randomUUID(),preview_id:preview.preview_id};const r=await crm.draft('managed-allocation-intent',intent);if(!r.ok){intent=null;throw Error(crm.platform==='mobile'?'Не удалось защитить операцию на телефоне.':'Не удалось защитить операцию на компьютере.');}}
      lock();let result;
      try{result=await request('GET','assignment-receipt',undefined,{operation_id:intent.operation_id});}
      catch(e){if((e as any).result?.status!==404)throw e;result=await request('POST','assignment-commit',intent);}
      if(current())await accepted(result);
    }catch(e){if(current()){$('allocation-status').textContent=(e as Error).message;const status=(e as any).result?.status;if([400,409,422].includes(status)){intent=null;await crm.draft('managed-allocation-intent',null);preview=null;}}}
    finally{busy=false;if(current()){lock();$<HTMLButtonElement>('allocation-submit').disabled=!intent;}}
  };
  host.querySelectorAll<HTMLButtonElement>('[data-allocation]').forEach(b=>b.onclick=protect(async()=>{action=b.dataset.allocation!;intent=(await crm.draft('managed-allocation-intent')).data;preview=null;$('allocation-title').textContent=({assign:'Назначить ответственного',replace:'Заменить ответственного',remove:'Снять ответственного'} as any)[action];$('allocation-target-label').hidden=action==='remove';$('allocation-preview').textContent='';$('allocation-status').textContent=intent?'Сохранена операция с неизвестным исходом.':'';lock();$<HTMLButtonElement>('allocation-submit').disabled=!intent;dialog.showModal();if(!intent)await check();}));
  ['allocation-kind','allocation-target'].forEach(id=>$(id).onchange=protect(check));$('allocation-note').onchange=protect(check);$('allocation-close').onclick=()=>dialog.close();
  $('allocation-history').onclick=protect(async()=>{const result=await request('GET','assignment-history');if(!current())return;const panel=document.createElement('dialog');panel.innerHTML=`<h2>История распределения</h2><div class="allocation-history">${result.rows.map((r:any)=>`<p>${new Date(r.at).toLocaleString('ru-RU')} · ${esc(r.client)} · ${esc(r.external_id)}<br>${r.capability==='call'?'Звонки':'Выезды'}: ${esc(r.before_name||'Не назначен')} → ${esc(r.after_name||'Не назначен')}</p>`).join('')||'Изменений нет.'}</div><button>Закрыть</button>`;host.append(panel);panel.querySelector('button')!.onclick=()=>{panel.close();panel.remove();};panel.showModal();});
  const stop=crm.onChange?.((e:any)=>{if(e.type!=='live')return;connected=!!e.connected;if(!connected){preview=null;$<HTMLButtonElement>('allocation-submit').disabled=true;}else if(e.dirty&&dialog.open&&!intent)void check().catch(e=>{$('allocation-status').textContent=e.message;});});
  intent=(await crm.draft('managed-allocation-intent')).data;lock();
  job=(await crm.draft('managed-active-job')).data||'';if(job)void monitor();
  return ()=>{disposed=true;turn++;clearTimeout(timer);stop?.();};
}
