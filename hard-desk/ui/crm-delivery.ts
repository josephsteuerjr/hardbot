// One courier form for Desktop and the existing mobile offline queue adapter.
type API = {
  draft(key: string, value?: any): Promise<any>;
  command(body: any): Promise<any>;
  close?: () => void;
  search?: (text: string) => Promise<any[]>;
  fields?: {key:string;title:string}[];
};
const esc = (value: any) => String(value ?? '').replace(/[&<>"']/g,
  char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]!));

export async function mountDeliveryForm(host: HTMLElement, initial: any[], api: API) {
  let contracts = [...initial];
  const draft = async (key: string, ...value: any[]) => {
    const result = await api.draft(key, ...value);
    if (result?.ok !== true) throw new Error(result?.text || 'Не удалось сохранить черновик на устройстве.');
    return result;
  };
  const key = 'courier:delivery', saved = (await draft(key)).data || {};
  host.innerHTML = `<form data-courier-form><h2>Результат доставки</h2><div class="field"><label for="courier-contract">Номер КД</label>${api.search ? '<input id="courier-search" type="search" placeholder="Найти КД или клиента">' : ''}<select id="courier-contract" required></select><output id="courier-name"></output></div><div class="field"><label for="courier-address">Адрес передачи конверта</label><input id="courier-address" list="courier-addresses" autocomplete="street-address"><datalist id="courier-addresses"></datalist><small>Если конверт не передан, оставьте адрес пустым</small></div><div class="field"><label for="courier-phone">Телефон или прочерк</label><input id="courier-phone" type="tel" maxlength="100" placeholder="—"></div><div class="field"><label for="courier-comment">Кому передан конверт или почему передать не удалось</label><textarea id="courier-comment" required maxlength="4000"></textarea></div><p data-courier-status role="status"></p><div class="actions">${api.close ? '<button type="button" data-courier-close>Закрыть</button>' : ''}<button type="submit" class="primary">Сохранить результат</button></div></form>`;
  const find = <T extends HTMLElement>(selector: string) => host.querySelector<T>(selector)!;
  const select=find<HTMLSelectElement>('#courier-contract'), address=find<HTMLInputElement>('#courier-address');
  const info=document.createElement('details');info.className='courier-client-info';
  find<HTMLOutputElement>('#courier-name').after(info);
  const phone=find<HTMLInputElement>('#courier-phone'), comment=find<HTMLTextAreaElement>('#courier-comment');
  const status=find<HTMLElement>('[data-courier-status]'), save=find<HTMLButtonElement>('button[type=submit]');
  const chosen=()=>contracts.find(row=>row.contract_uuid===select.value);
  const capture=()=>({contract_uuid:select.value,address:address.value,phone:phone.value,comment:comment.value});
  let draftWrites=Promise.resolve();
  const persist=()=>{
    const value=capture();
    draftWrites=draftWrites.catch(()=>{}).then(async()=>{
      if(value.contract_uuid)await draft(key+':'+value.contract_uuid,value);
      await draft(key,value);
    });
    return draftWrites;
  };
  const paint=()=>{
    const selected=chosen();
    info.hidden=!selected;
    find<HTMLOutputElement>('#courier-name').textContent=selected?.client_name || selected?.fields?.client || '';
    const fields=selected?.fields || {};
    find<HTMLDataListElement>('#courier-addresses').innerHTML=Array.from(new Set([fields.address,fields.address_customer,fields.extra_address].filter(Boolean)))
      .map(value=>`<option value="${esc(value)}"></option>`).join('');
    info.innerHTML=selected?'<summary>Сведения о клиенте</summary><dl>'+ (api.fields || []).filter(field=>fields[field.key]!==undefined&&fields[field.key]!==null&&fields[field.key]!=='')
      .map(field=>`<dt>${esc(field.title)}</dt><dd>${esc(fields[field.key])}</dd>`).join('')+'</dl>':'';
    const writable=selected && Array.isArray(selected.allowed_actions) && selected.allowed_actions.includes('delivery');
    save.disabled=!writable;
    status.textContent=selected&&!writable?'Данные доступны для просмотра. Передача конверта не назначена.':'';
  };
  const options=(preferred?:string)=>{
    select.innerHTML='<option value="">Выберите КД</option>'+contracts.map(row=>`<option value="${esc(row.contract_uuid)}">${esc(row.external_id)} · ${esc(row.creditor_id)}</option>`).join('');
    select.value=preferred || '';paint();
  };
  options(saved.contract_uuid);
  address.value=saved.address || '';phone.value=saved.phone ?? '-';comment.value=saved.comment || '';
  let activeContract=select.value,selectionGeneration=0;
  select.addEventListener('change',async()=>{
    const selected=select.value,current=++selectionGeneration;
    const previous={contract_uuid:activeContract,address:address.value,phone:phone.value,comment:comment.value};
    const controls=Array.from(host.querySelectorAll<HTMLInputElement|HTMLButtonElement>('input,select,textarea,button'))
      .filter(control=>control!==save).map(control=>({control,disabled:control.disabled}));
    controls.forEach(({control})=>control.disabled=true);
    save.disabled=true;
    try {
      await draftWrites.catch(()=>{});
      if(activeContract)await draft(key+':'+activeContract,previous);
      const restored=selected?(await draft(key+':'+selected)).data:null;
      if(current!==selectionGeneration||!host.isConnected)return;
      activeContract=selected;
      address.value=restored?.address || '';comment.value=restored?.comment || '';
      phone.value=restored?.phone ?? chosen()?.fields?.phone ?? '-';
      paint();await persist();
    }catch(error){select.value=activeContract;paint();status.textContent=(error as Error).message;}
    finally{controls.forEach(({control,disabled})=>control.disabled=disabled);}
  });
  for(const input of [address,phone,comment])input.addEventListener('input',()=>{void persist().catch(error=>{status.textContent=error.message;});});
  const close=host.querySelector<HTMLButtonElement>('[data-courier-close]');
  if(close)close.onclick=()=>{void persist().then(()=>api.close?.()).catch(error=>{status.textContent=error.message;});};
  const search=host.querySelector<HTMLInputElement>('#courier-search');
  let generation=0,timer:ReturnType<typeof setTimeout>;
  if(search)search.oninput=()=>{
    const current=++generation;clearTimeout(timer);
    timer=setTimeout(async()=>{
      try {
        const rows=await api.search!(search.value);
        if(current!==generation || !host.isConnected)return;
        const previous=chosen();contracts=rows;
        if(previous&&!contracts.some(row=>row.contract_uuid===previous.contract_uuid))contracts.unshift(previous);
        options(previous?.contract_uuid);
      }catch{if(current===generation)status.textContent='Поиск недоступен. Сохранённые КД остаются в списке.';}
    },250);
  };
  find<HTMLFormElement>('form').onsubmit=async event=>{
    event.preventDefault();if(save.disabled)return;
    const selected=chosen();if(!selected)return;
    save.disabled=true;
    try {
      if(!comment.value.trim())throw new Error('Укажите получателя конверта или причину неудачи.');
      const phoneText=phone.value.trim(),digits=phoneText.replace(/\D/g,'').length;
      if(phoneText&&!['-','—','–'].includes(phoneText)&&(!/^[+\d() .-]+$/.test(phoneText)||digits<5||digits>20))
        throw new Error('Введите телефон или прочерк.');
      await persist();
      const raw={schema_version:1,kind:'delivery',contract_ids:[selected.contract_uuid],base_revisions:{},
        payload:{delivered:!!address.value.trim(),delivery_address:address.value.trim(),
          delivery_phone:phone.value.trim() || '-',delivery_comment:comment.value.trim()}};
      const recordKey=key+':'+selected.contract_uuid;
      const content=JSON.stringify(raw),pending=(await draft(recordKey+':pending')).data;
      const operation_id=pending?.content===content?pending.operation_id:crypto.randomUUID();
      await draft(recordKey+':pending',{content,operation_id});
      const result=await api.command({...raw,operation_id});
      if(!result.ok)throw new Error(result.text || 'Не удалось сохранить результат.');
      await draft(recordKey,null);await draft(key,null);await draft(recordKey+':pending',null);
      comment.value='';address.value='';phone.value='-';select.value='';activeContract='';paint();
      status.textContent='Результат сохранён';
    }catch(error){paint();status.textContent=(error as Error).message;}
  };
}
