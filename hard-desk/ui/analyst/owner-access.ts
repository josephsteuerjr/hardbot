import { ask, esc, toast } from "../kit/dom";
import { refresh } from "../kit/shell";
import { local } from "./lib";

export function mountOwnerAccess(root: HTMLElement, state: any): void {
  root.querySelector('#c-code')?.closest('.section')?.remove();
  const section=document.createElement("section"); section.className="section";
  section.innerHTML=`<h2 class="section-title">Профиль аналитика</h2>
    <p class="muted">${state.account_session ? "Вы вошли в личный профиль." : "Войдите в личный профиль. Первичный профиль владельца создаётся после проверки root-доступа к серверу."}</p>
    <div class="toolbar"><input class="input" id="owner-login" placeholder="Логин" autocomplete="username">
      <input class="input" id="owner-password" type="password" placeholder="Пароль профиля" autocomplete="current-password">
      <button class="btn primary" id="owner-signin">Войти</button><button class="btn" id="owner-change-open">Сменить свой пароль</button></div>
    <div id="owner-change" hidden><p>Задайте новый пароль профиля.</p>
      <div class="toolbar"><input class="input" id="owner-next-password" type="password" placeholder="Новый пароль" autocomplete="new-password">
      <input class="input" id="owner-repeat-password" type="password" placeholder="Повторите пароль" autocomplete="new-password">
      <button class="btn primary" id="owner-change-save">Сменить пароль</button></div></div>
    <details><summary>Создать первичный профиль владельца</summary>
      <p class="muted">Укажите IP и данные SSH: root либо пользователя с sudo. Программа проверит права UID 0 и соответствие выбранному серверу, сохранит резервную копию и создаст личный профиль. Пароли SSH и sudo не сохраняются.</p>
      <div class="toolbar"><input class="input" id="owner-ip" placeholder="IP сервера" autocomplete="off">
        <input class="input" id="owner-ssh-login" value="root" placeholder="Пользователь SSH" autocomplete="off">
        <input class="input" id="owner-root-password" type="password" placeholder="Пароль SSH" autocomplete="off"></div>
      <div class="toolbar"><input class="input" id="owner-sudo-password" type="password" placeholder="Пароль sudo, если отличается" autocomplete="off"><button class="btn" id="owner-check-access">Проверить доступ без создания профиля</button></div>
      <div class="toolbar"><input class="input" id="owner-new-login" placeholder="Логин нового профиля" autocomplete="username">
        <input class="input" id="owner-display-name" placeholder="Имя в программе (необязательно)" autocomplete="name">
        <input class="input" id="owner-new-password" type="password" placeholder="Личный пароль: от 8 символов, буква и цифра" autocomplete="new-password">
        <button class="btn" id="owner-create">Проверить сервер и создать профиль</button></div>
      <label><input type="checkbox" id="owner-recovery"> Восстановить существующий профиль: сменить пароль и закрыть прежние входы</label>
      <p class="hint">Создание доступно один раз. Для восстановления отметьте флажок выше: прежние входы будут закрыты, новый компьютер получит доступ после проверки root или sudo.</p></details>
    <div class="err" id="owner-error" role="status"></div>`;
  root.prepend(section);
  const input=(id:string)=>section.querySelector<HTMLInputElement>("#"+id)!;
  const error=section.querySelector<HTMLElement>("#owner-error")!;
  section.querySelector('#owner-check-access')?.addEventListener('click',async()=>{
    const button=section.querySelector<HTMLButtonElement>('#owner-check-access')!;button.disabled=true;error.textContent='';
    try{
      const ip=input('owner-ip').value.trim(),ssh_username=input('owner-ssh-login').value.trim();
      if(!ip || !ssh_username || !input('owner-root-password').value)throw new Error('Заполните IP, пользователя и пароль SSH.');
      const probe=await local('/account/root_probe',{ip});
      if(!await ask('Проверка сервера',`IP: ${ip}. Ключ SSH: ${probe.host_key}. Сверьте его с известным ключом сервера. Проверить права без создания профиля?`,'Проверить'))return;
      const root_password=input('owner-root-password').value,sudo_password=input('owner-sudo-password').value;
      input('owner-root-password').value='';input('owner-sudo-password').value='';
      const result=await local('/account/root_access_check',{ip,ssh_username,root_password,sudo_password,host_key:probe.host_key});
      if(result.effective_uid!==0)throw new Error('Права UID 0 не подтверждены.');
      error.textContent=`Доступ ${result.authority==='sudo'?'через sudo':'root'} подтверждён: UID 0. Профили не изменялись.`;
    }catch(e){error.textContent=(e as Error).message;}finally{if(button.isConnected)button.disabled=false;}
  });
  let changeCredentials:{username:string,password:string}|null=null;
  const changePanel=section.querySelector<HTMLElement>('#owner-change')!;
  section.querySelector('#owner-change-open')?.addEventListener('click',()=>{
    changeCredentials=null;changePanel.hidden=false;
    error.textContent='Укажите свой логин и текущий пароль профиля, затем новый пароль.';
  });
  section.querySelector('#owner-change-save')?.addEventListener('click',async()=>{
    const button=section.querySelector<HTMLButtonElement>('#owner-change-save')!;
    const credentials=changeCredentials || {username:input('owner-login').value.trim(),password:input('owner-password').value};
    const next=input('owner-next-password').value;
    if(!credentials.username || !credentials.password){error.textContent='Укажите логин и текущий пароль профиля.';return;}
    if(next!==input('owner-repeat-password').value){error.textContent='Пароли не совпадают.';return;}
    button.disabled=true;
    try{
      await local('/account/password',{username:credentials.username,current:credentials.password,new:next});
      changeCredentials=null;
      for(const id of ['owner-password','owner-next-password','owner-repeat-password'])input(id).value='';
      toast('Пароль изменён.');await refresh();
    }catch(e){error.textContent=(e as Error).message;}finally{if(button.isConnected)button.disabled=false;}
  });
  section.querySelector("#owner-signin")?.addEventListener("click",async()=>{
    const button=section.querySelector<HTMLButtonElement>("#owner-signin")!;button.disabled=true;error.textContent="";
    try{
      const username=input("owner-login").value, password=input("owner-password").value;
      const result=await local("/account/login",{username,password}); input("owner-password").value="";
      if(result.must_change_password){
        error.textContent="Смените выданный пароль перед началом работы.";
        changePanel.hidden=false;changeCredentials={username:result.username,password};
        return;
      }
      toast(result.pending ? "Ожидается подтверждение компьютера." : "Вход выполнен."); await refresh();
    }catch(e){error.textContent=(e as Error).message;}finally{if(button.isConnected)button.disabled=false;}
  });
  let operationId="owner-"+crypto.randomUUID();
  section.querySelector("#owner-create")?.addEventListener("click",async()=>{
    const button=section.querySelector<HTMLButtonElement>("#owner-create")!;button.disabled=true;error.textContent="";
    try{
      const ip=input("owner-ip").value.trim(),username=input("owner-new-login").value.trim(),password=input("owner-new-password").value;
      const ssh_username=input('owner-ssh-login').value.trim();
      if(!ip || !username || !password || !ssh_username || !input("owner-root-password").value)throw new Error("Заполните IP, пользователя и пароль SSH, логин и личный пароль профиля.");
      const probe=await local("/account/root_probe",{ip});
      const recovery=input('owner-recovery').checked;
      if(!await ask("Проверка сервера",`IP: ${ip}. Ключ SSH: ${probe.host_key}. Сверьте его с известным ключом сервера. ${recovery?'Восстановить профиль и закрыть прежние входы?':'Продолжить создание профиля?'}`,recovery?'Восстановить':'Создать профиль'))return;
      const rootPassword=input("owner-root-password").value; input("owner-root-password").value="";
      const sudo_password=input('owner-sudo-password').value;input('owner-sudo-password').value='';
      const result=await local("/account/root_bootstrap",{ip,root_password:rootPassword,host_key:probe.host_key,username,password,operation_id:operationId,
        employee:input('owner-display-name').value.trim(),recovery,ssh_username,sudo_password});
      input("owner-new-password").value="";
      error.textContent=`Профиль ${result.recovery?'восстановлен':'создан'}. Операция ${result.operation_id}. Резервная копия сохранена на сервере. Войдите со своим логином и паролем.`;
      input("owner-login").value=result.username || username; operationId="owner-"+crypto.randomUUID();
    }catch(e){error.textContent=(e as Error).message+` Номер операции: ${operationId}. При потере ответа сначала проверьте журнал и существование профиля.`;}
    finally{if(button.isConnected)button.disabled=false;}
  });
}
