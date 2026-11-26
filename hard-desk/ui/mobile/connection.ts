const esc=(v:any)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const passwordForm=(temporary=false)=>'<form data-mobile-password class="mobile-login"><h2>'+(temporary?'Смените временный пароль':'Изменить пароль')+'</h2><label>Текущий пароль<input name="current" type="password" autocomplete="current-password" required></label><label>Новый пароль<input name="new" type="password" autocomplete="new-password" minlength="8" required></label><label>Повторите новый пароль<input name="repeat" type="password" autocomplete="new-password" minlength="8" required></label><button type="submit" class="primary">Сохранить пароль</button></form>';
const connectionStatus=(response:any,connected:boolean,pending:any)=>{
  if(response.ok&&connected)return {title:'Связь есть',text:'Данные синхронизируются с сервером.',online:true};
  if(response.ok&&pending)return {title:pending.status==='password'?'Задайте личный пароль':'Доступ нужно подтвердить',text:pending.status==='password'?'После этого руководитель сможет подтвердить устройство, если это потребуется.':'Руководитель видит ожидающее устройство. Черновики сохранены.'};
  if(response.ok)return null;
  const code=response.data?.error||'';
  if(response.status===401||response.status===410||['session','identity_changed','pilot_session_invalid'].includes(code))
    return {title:'Нужно войти снова',text:'Войдите под тем же сотрудником. Черновики и очередь сохранены на этом телефоне.'};
  if(response.status===403)return {title:'Доступ нужно подтвердить',text:'Проверьте назначение и подтверждение устройства с руководителем. Локальные данные сохранены.'};
  if(response.status===0)return {title:'Нет связи',text:'Проверьте интернет. Работу можно сохранить на телефоне и отправить после восстановления связи.'};
  if(response.status>=500)return {title:'Сервер временно недоступен',text:'Не повторяйте отправку вслепую. Проверьте «Отправки» и повторите позже — локальные данные сохранены.'};
  return {title:'Подключение не подтверждено',text:response.text||'Проверьте вход и повторите. Локальные данные сохранены.'};
};
import {mountRegistration} from '../crm-registration';

export async function mountConnection(host:HTMLElement,crm:any,boot:()=>Promise<any>){
  const response=await boot();
  const state=await crm.state();
  const connected=!!state.context,pending=response.data?.pending,synthetic=response.data?.synthetic===true||state.connection.mode==='synthetic';
  const choosingRole=synthetic&&!connected&&!pending;
  let account='';
  if(connected)account='<section class="mobile-account"><div class="mobile-avatar">В</div><div><h2>'+esc(({employee:'Сотрудник',manager:'Руководитель',analyst:'Аналитик',courier:'Курьер',observer:'Наблюдатель'} as any)[state.context.role]||'Рабочее место')+'</h2></div><button id="mobile-logout" class="link">Выйти</button></section>';
  else if(pending)account='<section class="mobile-pending"><h2>'+esc(pending.employee)+'</h2><p>'+esc(pending.username)+'</p>'+(pending.status==='password'?passwordForm(true):'<p>Телефон ожидает подтверждения руководителем или администратором. Подключение проверяется автоматически.</p><button id="mobile-refresh">Проверить доступ</button>')+'<button id="mobile-logout" class="link">Сменить вход</button></section>';
  else if(!synthetic)account='<form id="mobile-login" class="mobile-login" aria-labelledby="title"><label>Логин<input name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required></label><label>Пароль<input name="password" type="password" autocomplete="current-password" required></label><button type="submit" class="primary">Войти</button></form>';
  const choices='<p>Синтетические данные</p><div class="mobile-role-options">'+[['employee','Сотрудник'],['courier','Курьер'],['manager','Руководитель'],['analyst','Аналитик'],['observer','Наблюдатель']].map(([id,name])=>'<button data-mobile-role="'+id+'">'+name+'<span aria-hidden="true">↗</span></button>').join('')+'</div>';
  const demo=synthetic?(choosingRole?'<section class="mobile-demo mobile-demo-entry" aria-label="Выбор роли"><h2>Выберите роль</h2>'+choices+'</section>':'<details class="mobile-demo"><summary>Сменить роль</summary>'+choices+'</details>'):'';
  const notice=connectionStatus(response,connected,pending);
  const status=notice?'<section class="mobile-connection-status" role="status"><span class="mobile-signal '+(notice.online?'online':'')+'"></span><div><strong>'+esc(notice.title)+'</strong><p>'+esc(notice.text)+'</p></div></section>':'';
  let queueText='';
  if(connected){
    const queue=await crm.queue().catch(()=>null),entries=queue?.data?.entries||[];
    const waiting=entries.filter((entry:any)=>entry.state==='pending').length,attention=entries.filter((entry:any)=>entry.state==='attention').length;
    queueText='<p class="mobile-queue-summary">'+(waiting||attention?'На этом телефоне: ожидают отправки — '+waiting+', требуют сверки — '+attention+'.':'Неподтверждённых отправок нет. Проверка выполнена только что.')+'</p>';
  }
  host.innerHTML='<div class="mobile-connection">'+account+(choosingRole?demo:'')+status+queueText+(connected?'<button id="mobile-send" class="mobile-wide">Проверить и отправить очередь</button>':'')+(connected&&!synthetic?'<details class="mobile-diagnostics"><summary>Изменить пароль</summary>'+passwordForm()+'</details>':'')+(!choosingRole?demo:'')+'<details class="mobile-diagnostics"><summary>Диагностика</summary><p id="mobile-diagnostic-status"></p><button id="mobile-check">Проверить подключение</button></details><p id="mobile-connect-result" role="status" aria-live="polite"></p><small class="mobile-build">'+esc(state.version)+'</small></div>';
  const root=host.firstElementChild!,feedback=host.querySelector<HTMLElement>('#mobile-connect-result')!;
  const safe=(fn:()=>Promise<any>)=>async()=>{try{feedback.textContent='';await fn();}catch(e){feedback.textContent=(e as Error).message;feedback.scrollIntoView({block:'nearest'});}};
  host.querySelectorAll<HTMLButtonElement>('[data-mobile-role]').forEach(b=>b.onclick=safe(async()=>{b.disabled=true;const r=await crm.connect({action:'synthetic',role:b.dataset.mobileRole});if(!r.ok){b.disabled=false;throw Error(r.text||'Вход не выполнен.');}}));
  const form=host.querySelector<HTMLFormElement>('#mobile-login');
  if(form){const holder=document.createElement('div');form.after(holder);mountRegistration(holder,crm);}
  if(form)form.onsubmit=e=>{e.preventDefault();void safe(async()=>{const b=form.querySelector<HTMLButtonElement>('[type=submit]')!,data=new FormData(form);b.disabled=true;try{const r=await crm.connect({action:'login',username:data.get('username'),password:data.get('password')});if(!r.ok)throw Error(r.text||'Вход не выполнен.');}finally{b.disabled=false;}})();};
  host.querySelectorAll<HTMLFormElement>('[data-mobile-password]').forEach(form=>{form.onsubmit=e=>{e.preventDefault();void safe(async()=>{
    const data=new FormData(form),b=form.querySelector<HTMLButtonElement>('[type=submit]')!;
    if(data.get('new')!==data.get('repeat'))throw Error('Пароли не совпадают.');
    b.disabled=true;try{const r=await crm.connect({action:'password',current:data.get('current'),new:data.get('new')});if(!r.ok)throw Error(r.text||'Пароль не изменён.');form.reset();feedback.textContent='Пароль изменён.';}finally{b.disabled=false;}
  })();};});
  host.querySelector('#mobile-logout')?.addEventListener('click',safe(async()=>{const r=await crm.connect({action:'logout'});if(!r.ok)throw Error(r.text);location.reload();}));
  host.querySelector('#mobile-send')?.addEventListener('click',safe(async()=>{await crm.queue('flush');const q=await crm.queue(),entries=q.data.entries||[],waiting=entries.filter((e:any)=>e.state==='pending').length,attention=entries.filter((e:any)=>e.state==='attention').length;feedback.textContent=waiting||attention?'Осталось: ожидают отправки — '+waiting+', требуют сверки — '+attention+'. Локальные данные сохранены.':'Все результаты подтверждены сервером. Очередь отправки завершена.';}));
  const refresh=safe(async()=>{await crm.connect({action:'refresh'});});
  host.querySelector('#mobile-refresh')?.addEventListener('click',refresh);
  if(pending?.status==='device'){const tick=async()=>{if(!root.isConnected)return;await refresh();if(root.isConnected)setTimeout(()=>void tick(),5000);};setTimeout(()=>void tick(),5000);}
  host.querySelector<HTMLButtonElement>('#mobile-check')!.onclick=safe(async()=>{const r=await boot();host.querySelector('#mobile-diagnostic-status')!.textContent=r.ok?'Подключение работает.':r.text||'Нет связи.';});
}

