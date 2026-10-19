import { hard } from '../kit/api';
import { ask, esc, toast } from '../kit/dom';
import { adm } from './lib';
import { go } from '../kit/shell';

export async function credentialFile(username:string,password:string,employee=''):Promise<void>{
  const name=(employee || username).replace(/[<>:"/\\|?*\x00-\x1f]/g,'_');
  const result=await hard.saveText(`Вход — ${name}.txt`,`${employee}\nВход в профиль\nhttps://hard.rimborso.ru\nЛогин: ${username}\nПароль: ${password}\n\nИспользуйте эти данные в своей программе и по общей ссылке с телефона. При первом входе смените выданный пароль.\n`);
  toast(result.ok ? 'Личный файл сохранён.' : result.text || 'Файл не сохранён. Реквизиты доступны до закрытия этого раздела.');
}

export function mountAccountActions(box:HTMLElement,account:any,reload:()=>Promise<void>):void{
  const bar=document.createElement('div');bar.className='toolbar';
  if(account.owner_root){
    bar.innerHTML='<span class="muted">Первичный профиль владельца</span><button class="btn">Сменить свой пароль или восстановить доступ</button>';
    bar.querySelector('button')!.onclick=()=>void go('conn');box.prepend(bar);return;
  }
  bar.innerHTML=`<button class="btn" data-reset>Сбросить пароль</button><button class="btn ${account.status==='disabled'?'':'danger'}" data-status>${account.status==='disabled'?'Восстановить доступ':'Закрыть доступ'}</button><button class="btn danger" data-delete>Удалить аккаунт</button><span data-credential></span>`;
  box.prepend(bar);
  bar.querySelector('[data-reset]')?.addEventListener('click',async()=>{
    if(!await ask('Сбросить пароль',`Все текущие входы ${account.employee} будут закрыты. При проверке отзыва устройства очистят локальный профиль и неотправленную работу. Сохраните новый личный файл и передайте его сотруднику.`, 'Сбросить'))return;
    try{
      const result=await adm('POST','/admin/account_action.json',{action:'reset',username:account.username});
      const button=document.createElement('button');button.className='btn primary';button.textContent='Сохранить личный файл';
      button.onclick=()=>void credentialFile(account.username,result.temporary_password,account.employee);
      bar.querySelector('[data-credential]')!.replaceChildren(button); await credentialFile(account.username,result.temporary_password,account.employee);
    }catch(e){toast((e as Error).message);}
  });
  bar.querySelector('[data-delete]')?.addEventListener('click',async()=>{
    if(!await ask('Удалить аккаунт',`Удалить аккаунт ${account.employee} (${account.username})? Логин, пароль и регистрации устройств будут удалены. Все входы закроются; при подключении устройства очистят локальные данные, включая неотправленную работу. Отправленные рабочие записи и журнал сохранятся. Для возврата потребуется новая регистрация.`, 'Удалить аккаунт'))return;
    const button=bar.querySelector<HTMLButtonElement>('[data-delete]')!;button.disabled=true;
    try{await adm('POST','/admin/account_action.json',{action:'delete',username:account.username,confirm_username:account.username,account_id:account.id});toast('Аккаунт удалён. Все его входы отозваны.');await reload();}
    catch(e){toast((e as Error).message);}
    finally{if(button.isConnected)button.disabled=false;}
  });
  bar.querySelector('[data-status]')?.addEventListener('click',async()=>{
    if(!await ask(account.status==='disabled'?'Восстановить доступ':'Закрыть доступ',account.status==='disabled'
      ? `Профиль ${account.employee} вернётся на подтверждение.`:`Текущие входы ${account.employee} будут отозваны.`, 'Подтвердить'))return;
    try{await adm('POST','/admin/account_action.json',{action:account.status==='disabled'?'enable':'disable',username:account.username});await reload();}
    catch(e){toast((e as Error).message);}
  });
  for(const device of account.devices || []){
    if(device.status==='revoked')continue;
    const b=document.createElement('button');b.className='btn small danger';b.textContent='Отозвать '+device.label;
    b.onclick=async()=>{
      if(!await ask('Отозвать устройство',`Будут закрыты все входы этого устройства: ${device.label}. При подключении оно очистит локальный профиль и неотправленную работу.`, 'Отозвать'))return;
      try{await adm('POST','/admin/account_action.json',{action:'revoke',device_id:device.id});await reload();}catch(e){toast((e as Error).message);}
    };bar.append(b);
  }
}

export async function mountAccessPreparation(root:HTMLElement,reload:()=>Promise<void>):Promise<void>{
  const data=await adm('GET','/admin/staff_roster.json');
  const managers=(data.managers || []).filter((m:any)=>!m.role || ['manager','full'].includes(m.role));
  const items=(data.employees || []).map((r:any)=>({employee:r.employee,office:r.office,offices:JSON.parse(r.offices || '[]'),product_names:JSON.parse(r.product_names || '{}'),direction:r.direction,products:JSON.parse(r.products),manager_uid:r.manager_uid,active:!!r.active}));
  const section=document.createElement('section');section.className='section';
  section.innerHTML=`<details><summary>Создать профиль руководителя</summary><p class="muted">Выберите подтверждённую личность. Закрепления офисов сохраняются. Новый профиль ожидает подтверждения администратора.</p>
    <div class="toolbar"><select class="input" data-manager><option value="">Руководитель</option>${managers.map((m:any)=>`<option value="${m.telegram_id}">${esc(m.name)}</option>`).join('')}</select>
      <input class="input" data-username placeholder="Новый логин"><input class="input" data-manager-name placeholder="Подтверждённое полное ФИО (если отличается)"><button class="btn" data-create>Создать и сохранить личный файл</button><span data-create-file></span></div><p class="hint">Если руководитель есть в справочнике сотрудников, укажите ФИО точно как в справочнике. Его области доступа сохранятся.</p></details>
    <details style="margin-top:14px"><summary>Справочник сотрудников (${items.length})</summary>
      <p class="muted">Сотрудник без портфеля также может создать профиль. Укажите его офис, программы и своего руководителя. Отсутствующие строки автоматически не удаляются.</p>
      <div class="toolbar"><input class="input" data-employee placeholder="ФИО"><input class="input" data-office placeholder="Офис">
        <select class="input" data-direction><option value="tj">Таджикистан</option><option value="uz">Узбекистан</option></select>
        <select class="input" data-roster-manager><option value="">Руководитель</option>${managers.map((m:any)=>`<option value="${m.telegram_id}">${esc(m.name)}</option>`).join('')}</select></div>
      <div class="toolbar"><label><input type="checkbox" data-hard checked> Хард</label><label><input type="checkbox" data-soft> Софт</label>
        <button class="btn" data-roster-preview>Проверить и добавить сотрудника</button></div>
      <div class="toolbar"><input class="input" data-offices placeholder="Все офисы через точку с запятой (необязательно)">
        <input class="input" data-hard-name placeholder="ФИО в Хард, если отличается"><input class="input" data-soft-name placeholder="ФИО в Софт, если отличается"></div>
      <div data-preview role="status"></div>
      <details><summary>Загрузить подготовленный список</summary><p class="muted">Вставьте JSON-массив сотрудников или результат предпросмотра с полем items. Сначала программа покажет количество записей. Отсутствующие сотрудники не удаляются.</p>
        <textarea class="input" data-roster-json rows="5" aria-label="Подготовленный список сотрудников"></textarea><button class="btn" data-roster-import>Проверить список</button><div data-import-result role="status"></div></details></details>`;
  root.prepend(section);
  const value=(name:string)=>(section.querySelector(`[data-${name}]`) as HTMLInputElement).value;
  section.querySelector('[data-roster-import]')?.addEventListener('click',async()=>{
    const button=section.querySelector<HTMLButtonElement>('[data-roster-import]')!;button.disabled=true;
    try{
      const parsed=JSON.parse(value('roster-json')),items=Array.isArray(parsed)?parsed:parsed.items;
      const checked=await adm('POST','/admin/staff_roster.json',{items});
      section.querySelector('[data-import-result]')!.textContent=`Проверено: ${checked.rows} сотрудников. Текущий справочник: ${checked.current_rows}.`;
      if(!await ask('Сохранить справочник',`Будут добавлены или обновлены ${checked.rows} записей. Проверьте ФИО, офисы и соответствие имён портфелей в подготовленном списке. Договоры и факты сохранятся.`, 'Сохранить'))return;
      await adm('POST','/admin/staff_roster.json',{items,apply:true,expected_sha256:checked.sha256});await reload();
    }catch(e){section.querySelector('[data-import-result]')!.textContent=(e as Error).message;}
    finally{if(button.isConnected)button.disabled=false;}
  });
  section.querySelector('[data-create]')?.addEventListener('click',async()=>{
    const uid=Number(value('manager')),username=value('username').trim();
    if(!uid||!username){toast('Выберите руководителя и задайте логин.');return;}
    const display_name=value('manager-name').trim();
    if(!await ask('Создать профиль',`Создать личный профиль ${display_name || managers.find((m:any)=>m.telegram_id===uid)?.name}? Области доступа сохранятся.`, 'Создать'))return;
    try{
      const r=await adm('POST','/admin/account_action.json',{action:'create',uid,username,display_name});
      const b=document.createElement('button');b.className='btn primary';b.textContent='Сохранить личный файл';
      b.onclick=()=>void credentialFile(username,r.temporary_password,r.employee);section.querySelector('[data-create-file]')!.replaceChildren(b);
      await credentialFile(username,r.temporary_password,r.employee);
    }catch(e){toast((e as Error).message);}
  });
  section.querySelector('[data-roster-preview]')?.addEventListener('click',async()=>{
    const products=['hard','soft'].filter(p=>(section.querySelector(`[data-${p}]`) as HTMLInputElement).checked);
    const employee=value('employee').trim(),office=value('office').trim(),manager=Number(value('roster-manager'));
    if(!employee||!office||!manager||!products.length){toast('Заполните ФИО, офис, программы и руководителя.');return;}
    const previous=items.find((r:any)=>r.employee===employee),product_names={...previous?.product_names};
    for(const product of ['hard','soft'])if(value(product+'-name').trim())product_names[product]=value(product+'-name').trim();
    const offices=value('offices').trim()?value('offices').split(';').map(x=>x.trim()).filter(Boolean):previous?.offices || [office];
    const next=items.filter((r:any)=>r.employee!==employee).concat({employee,office,offices,product_names,products,direction:value('direction'),manager_uid:manager,active:true});
    try{
      const p=await adm('POST','/admin/staff_roster.json',{items:next});
      section.querySelector('[data-preview]')!.textContent=`Проверено: ${p.rows} сотрудников. Будет добавлена или обновлена запись ${employee}.`;
      if(!await ask('Обновить справочник',`Сохранить ${employee}, офис ${office}? Договоры и факты не изменяются.`, 'Сохранить'))return;
      await adm('POST','/admin/staff_roster.json',{items:next,apply:true,expected_sha256:p.sha256});toast('Справочник обновлён.');await reload();
    }catch(e){toast((e as Error).message);}
  });
}
