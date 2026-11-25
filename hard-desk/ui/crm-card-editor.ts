const esc=(value:any)=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
type Change={raw:string; group:string; revision:number; before:any};

/** Uses the same protected queue, field grants and revision protocol as the registry. */
export async function editClientCard(dialog:HTMLDialogElement,card:any,crm:any,onSaved:()=>void,isCurrent:()=>boolean){
  const key='manager-card-input:'+card.person.person_uuid;
  const ids=card.contracts.map((row:any)=>row.contract_uuid);
  const queue=await crm.queue();
  if(!queue.ok)throw Error(queue.text||'Не удалось проверить отправку правок.');
  if((queue.data?.entries||[]).some((entry:any)=>entry.action==='registry-edit'&&entry.state!=='accepted'&&!entry.reviewed_at&&entry.body.rows.some((r:any)=>ids.includes(r.contract_uuid))))
    throw Error('В карточке есть отправляемые или конфликтные правки. Откройте очередь для сверки.');
  const response=await crm.api('GET','registry',undefined,{view:'all',ids:ids.join(','),limit:100});
  if(!isCurrent())return;
  if(!response.ok)throw Error(response.text||'Карточка недоступна.');
  const data=response.data,rows=data.rows;
  if(rows.length!==ids.length)throw Error('Состав карточки изменился. Обновите карточку.');
  const saved=(await crm.draft(key)).data||{};
  let changes:Record<string,Record<string,Change>>=saved.changes||{},intent:any=saved.intent;
  let selected=rows[0].contract_uuid,busy=false,saveChain=Promise.resolve(),saveError='';
  if(!isCurrent())return;
  dialog.classList.add('card-editor');
  dialog.innerHTML=`<form><h2>Редактирование карточки</h2><label>Договор <select id="card-edit-contract">${rows.map((r:any)=>`<option value="${esc(r.contract_uuid)}">${esc(r.external_id)} · ${esc(r.fields.partner||r.creditor_id)}</option>`).join('')}</select></label><div class="card-editor-fields"></div><p role="status" id="card-edit-status"></p><div class="actions"><button type="button" id="card-edit-close">Закрыть</button><button type="submit" class="primary" id="card-edit-save">Сохранить правки</button></div></form>`;
  const $=<T extends HTMLElement=HTMLElement>(id:string)=>dialog.querySelector<T>('#'+id)!;
  const status=(message:string)=>{$('card-edit-status').textContent=message;};
  const remember=()=>{
    const snapshot=structuredClone({changes,intent});
    saveChain=saveChain.then(async()=>{const r=await crm.draft(key,snapshot);if(!r.ok)throw Error(r.text||'Не удалось сохранить ввод на компьютере.');saveError='';})
      .catch(e=>{saveError=e.message;status(saveError);});
  };
  const count=()=>Object.values(changes).reduce((sum,r)=>sum+Object.keys(r).length,0);
  const setValue=(row:any,f:any,raw:string)=>{
    const existing=changes[row.contract_uuid]?.[f.key];
    const before=existing?existing.before:row.fields[f.key];
    if(raw===String(before??'')){
      delete changes[row.contract_uuid]?.[f.key];
      if(changes[row.contract_uuid]&&!Object.keys(changes[row.contract_uuid]).length)delete changes[row.contract_uuid];
    }else{
      (changes[row.contract_uuid]||={})[f.key]={raw,group:f.group,revision:existing?.revision??row.revisions[f.group]??0,before};
    }
  };
  function paint(){
    const row=rows.find((r:any)=>r.contract_uuid===selected),groups=new Map<string,any[]>();
    for(const f of data.columns){
      if(!row.writable_fields.includes(f.key))continue;
      const title=f.section||'Сведения';if(!groups.has(title))groups.set(title,[]);groups.get(title)!.push(f);
    }
    dialog.querySelector('.card-editor-fields')!.innerHTML=Array.from(groups).map(([title,fields],i)=>`<details${i===0?' open':''}><summary>${esc(title)}</summary><div class="grid">${fields.map(f=>{
      const value=changes[selected]?.[f.key]?.raw??row.fields[f.key]??'';
      const attrs=`data-card-field="${esc(f.key)}" data-changed="${!!changes[selected]?.[f.key]}"${intent?' disabled':''}`;
      const options=f.options||[];
      const control=options.length?`<select ${attrs}><option value="">Пусто</option>${(!options.includes(String(value))&&value?[value,...options]:options).map((v:string)=>`<option value="${esc(v)}"${v===value?' selected':''}>${esc(v)}</option>`).join('')}</select>`:
        /comment|address|tips/.test(f.key)?`<textarea ${attrs}>${esc(value)}</textarea>`:
        `<input ${attrs} type="${f.type==='date'?'date':'text'}"${f.type==='money'?' inputmode="decimal"':''} value="${esc(value)}">`;
      return `<label>${esc(f.title)}${control}</label>`;
    }).join('')}</div></details>`).join('');
    dialog.querySelectorAll<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement>('[data-card-field]').forEach(control=>control.oninput=()=>{
      if(intent||busy)return;
      const f=data.columns.find((f:any)=>f.key===control.dataset.cardField);
      // A shared person's name is changed atomically across their contracts.
      for(const target of f.key==='client'?rows:[row])setValue(target,f,control.value);
      control.dataset.changed=String(!!changes[selected]?.[f.key]);remember();status(`Изменено полей: ${count()}. Ввод сохраняется на компьютере.`);
    });
  }
  $<HTMLSelectElement>('card-edit-contract').onchange=()=>{selected=$<HTMLSelectElement>('card-edit-contract').value;paint();};
  $('card-edit-close').onclick=()=>{if(!busy)dialog.close();};
  dialog.addEventListener('close',()=>dialog.classList.remove('card-editor'),{once:true});
  dialog.querySelector('form')!.onsubmit=async e=>{
    e.preventDefault();if(busy||!isCurrent())return;busy=true;
    dialog.querySelectorAll<HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement|HTMLButtonElement>('input,textarea,select,button').forEach(e=>e.disabled=true);
    try{
      await saveChain;if(saveError)throw Error(saveError);
      if(!intent){
        const patches=Object.entries(changes).filter(([,value])=>Object.keys(value).length).map(([cid,fields])=>{
          const row=rows.find((r:any)=>r.contract_uuid===cid);if(!row)throw Error('Договор недоступен. Ввод сохранён.');
          const values:Record<string,any>={},clear:string[]=[],base_revisions:Record<string,number>={};
          for(const [key,change] of Object.entries(fields)){
            if(!row.writable_fields.includes(key))throw Error('Право поля изменилось. Ввод сохранён.');
            const field=data.columns.find((f:any)=>f.key===key);let value:any=change.raw;
            if(value===''){value=null;clear.push(key);}
            else if(field.type==='money'){
              value=value.replace(/[ \u00a0]/g,'').replace(',','.');
              if(!/^-?\d{1,15}(\.\d{1,2})?$/.test(value))throw Error(field.title+': укажите сумму с двумя знаками после запятой.');
            }
            values[key]=value;
            if(base_revisions[change.group]!==undefined&&base_revisions[change.group]!==change.revision)throw Error('Ввод содержит разные версии одной группы. Откройте сверку.');
            base_revisions[change.group]=change.revision;
          }
          return {contract_uuid:cid,values,clear,base_revisions};
        });
        if(!patches.length){status('Нет изменений.');return;}
        intent={schema_version:1,operation_id:crypto.randomUUID(),rows:patches};remember();await saveChain;if(saveError)throw Error(saveError);
      }
      const result=await crm.registryEdit(intent);
      if(!result.ok)throw Error(result.text||'Сохранение не подтверждено. Ввод и операция сохранены.');
      // The protected queue owns the exact body from this point onward.
      const cleared=await crm.draft(key,null);if(!cleared.ok)throw Error('Правки уже в очереди. Не удалось очистить черновик.');
      if(isCurrent()){dialog.close();onSaved();}
    }catch(error){status((error as Error).message);paint();}
    finally{busy=false;if(isCurrent()){
      $<HTMLButtonElement>('card-edit-save').disabled=false;$<HTMLButtonElement>('card-edit-close').disabled=false;
      $<HTMLSelectElement>('card-edit-contract').disabled=false;paint();
    }}
  };
  paint();status(intent?'Есть неподтверждённая отправка. Сохранение повторит ту же операцию.':count()?`Восстановлен ввод: ${count()} полей.`:'');dialog.showModal();
}
