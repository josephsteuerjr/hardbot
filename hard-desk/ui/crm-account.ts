/** Windows account entry. Passwords stay in the form only until the request ends. */
export function mountAccount(host:HTMLElement,crm:any,account:any,connected:boolean,refresh:(action:string)=>Promise<void>) {
  const esc=(s:any)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
  if (connected && !account?.available) {
    host.innerHTML='<button id="account-switch">Войти под другой учётной записью</button>';
    host.querySelector('#account-switch')!.addEventListener('click',()=>mountAccount(host,crm,account,false,refresh));
    return;
  }
  host.innerHTML=`<section class="account-entry"><h2>${account?.available?'Вход в CRM':'Войти в CRM'}</h2>
    ${account?.available?`<p>${esc(account.username)}</p>${account.must_change_password?`<form id="account-password"><div class="grid"><label class="field">Временный пароль<input name="current" type="password" autocomplete="current-password" required></label><label class="field">Новый пароль<input name="new" type="password" autocomplete="new-password" required></label><label class="field">Повторите новый пароль<input name="repeat" type="password" autocomplete="new-password" required></label></div><button class="primary">Сменить пароль</button></form>`:`${account.pending?'<p>Устройство ожидает подтверждения руководителем.</p>':''}<button id="account-status" class="primary">${connected?'Проверить вход':'Продолжить'}</button>`}<button id="account-logout">Выйти из учётной записи</button>`:
    `<form id="account-login"><div class="grid"><label class="field">Логин<input name="username" autocomplete="username" value="${esc(account?.username)}" required></label><label class="field">Пароль<input name="password" type="password" autocomplete="current-password" required></label></div><button class="primary">Войти</button></form>`}
    <p id="account-message" role="status">${esc(account?.error||'')}</p></section>`;
  let busy=false;
  const run=async(action:string,body:any={})=>{
    if(busy)return;busy=true;
    host.querySelectorAll<HTMLButtonElement>('button').forEach(b=>b.disabled=true);
    const message=host.querySelector<HTMLElement>('#account-message')!;message.textContent='Проверяем вход…';
    try{
      const result=await crm.connect({action,...body});
      if(!result.ok)throw Error(result.text||'Не удалось подтвердить вход. Повторите попытку.');
      await refresh(action);
    }catch(e){message.textContent=(e as Error).message;}
    finally{busy=false;host.querySelectorAll<HTMLInputElement>('input[type=password]').forEach(e=>e.value='');host.querySelectorAll<HTMLButtonElement>('button').forEach(b=>b.disabled=false);}
  };
  host.querySelector<HTMLFormElement>('#account-login')?.addEventListener('submit',e=>{
    e.preventDefault();const values=new FormData(e.currentTarget as HTMLFormElement);
    void run('account-login',{username:values.get('username'),password:values.get('password')});
  });
  host.querySelector<HTMLFormElement>('#account-password')?.addEventListener('submit',e=>{
    e.preventDefault();const values=new FormData(e.currentTarget as HTMLFormElement);
    if(values.get('new')!==values.get('repeat')){host.querySelector('#account-message')!.textContent='Новые пароли не совпадают.';return;}
    void run('account-password',{current:values.get('current'),new:values.get('new')});
  });
  host.querySelector('#account-status')?.addEventListener('click',()=>void run('account-status'));
  host.querySelector('#account-logout')?.addEventListener('click',()=>void run('account-logout'));
}
