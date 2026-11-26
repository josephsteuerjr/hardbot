type Republic = { code:string; name:string; cities:string[] };

export function mountRegistration(host:HTMLElement,crm:any){
  const panel=document.createElement('details');
  panel.className=crm.platform==='mobile'?'mobile-diagnostics':'account-entry';
  panel.innerHTML=`<summary>Зарегистрироваться</summary><form class="${crm.platform==='mobile'?'mobile-login':''}">
    <label class="field">Роль<select name="role"><option value="employee">Сотрудник</option><option value="manager">Руководитель</option><option value="analyst">Аналитик</option></select></label>
    <label class="field">ФИО<input name="employee" autocomplete="name" maxlength="120" required></label>
    <div data-registration-area hidden>
      <label class="field">Республика<select name="country" required disabled><option value="">Выберите республику</option></select></label>
      <label class="field">Город<select name="city" disabled></select></label>
    </div>
    <div class="grid"><label class="field">Логин<input name="username" autocomplete="username" autocapitalize="none" spellcheck="false" minlength="3" maxlength="32" required></label>
    <label class="field">Пароль<input name="password" type="password" autocomplete="new-password" minlength="8" required></label></div>
    <label class="field" data-registration-code hidden>Личный код регистрации<input name="code" autocapitalize="none" spellcheck="false" autocomplete="one-time-code" required disabled></label>
    <button type="submit" class="primary" disabled>Зарегистрироваться</button><p data-registration-status role="status" aria-live="polite"></p>
  </form>`;
  host.append(panel);
  const form=panel.querySelector('form')!;
  const select=(name:string)=>form.querySelector<HTMLSelectElement>(`[name=${name}]`)!;
  const input=(name:string)=>form.querySelector<HTMLInputElement>(`[name=${name}]`)!;
  const role=select('role'),country=select('country'),city=select('city'),code=input('code');
  const submit=form.querySelector<HTMLButtonElement>('[type=submit]')!;
  const status=form.querySelector<HTMLElement>('[data-registration-status]')!;
  let republics:Republic[]=[],loaded=false,loading=false,paused=false,busy=false;

  function toggle(selector:string,visible:boolean,...fields:(HTMLInputElement|HTMLSelectElement)[]){
    form.querySelector<HTMLElement>(selector)!.hidden=!visible;
    for(const field of fields)field.disabled=!visible;
  }
  function roleChanged(){
    const employee=role.value==='employee',manager=role.value==='manager';
    toggle('[data-registration-area]',manager,country,city);
    toggle('[data-registration-code]',employee&&paused,code);
    submit.disabled=!loaded||busy;
    if(loaded)status.textContent=employee&&paused?'Личный код выдаёт ответственный за подключение CRM.':'';
  }
  function updateCities(){
    const choices=republics.find(region=>region.code===country.value)?.cities||[];
    city.replaceChildren(new Option('Вся республика',''),...choices.map(name=>new Option(name,name)));
  }
  role.onchange=roleChanged;
  country.onchange=updateCities;
  panel.addEventListener('toggle',async()=>{
    if(!panel.open||loaded||loading)return;
    loading=true;status.textContent='Загружаем настройки регистрации…';
    try{
      const reply=await crm.connect({action:'account-registration-options'});
      if(!reply.ok)throw Error(reply.text||'Регистрация временно недоступна.');
      republics=reply.data.republics;
      paused=reply.data.employee_confirmation_paused;
      country.append(...republics.map(region=>new Option(region.name,region.code)));
      updateCities();loaded=true;roleChanged();
    }catch(error){status.textContent=(error as Error).message;}
    finally{loading=false;}
  });
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!loaded)return;
    const data=Object.fromEntries(new FormData(form));
    const employee=role.value==='employee';
    const action={action:'account-register',...data};
    busy=true;submit.disabled=true;status.textContent='Отправляем регистрацию…';
    try{
      const reply=await crm.connect(action);
      if(!reply.ok)throw Error(reply.text||'Не удалось зарегистрироваться.');
      const login=host.closest('.mobile-connection, .connection')?.querySelector<HTMLInputElement>('#account-login [name=username],#mobile-login [name=username]')
        ||host.querySelector<HTMLInputElement>('#account-login [name=username]');
      if(login)login.value=String(data.username||'');
      form.reset();updateCities();busy=false;roleChanged();
      status.textContent=reply.data.pending?`Заявка отправлена. После подтверждения ${employee?'руководителем':'администратором'} войдите с вашим логином и паролем.`:
        'Регистрация завершена. Войдите с вашим логином и паролем.';
    }catch(error){status.textContent=(error as Error).message;}
    finally{busy=false;input('password').value='';submit.disabled=false;}
  });
}
