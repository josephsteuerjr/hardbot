// Real Chromium forms; network outcomes are injected without contacting production.
const {app,BrowserWindow}=require('electron');
const {buildSync}=require('esbuild'),assert=require('node:assert/strict'),path=require('node:path');
const root=process.env.HARD_CRM_QA_ROOT;assert(root);app.setPath('userData',path.join(root,'profile'));
app.whenReady().then(async()=>{
 const win=new BrowserWindow({show:false,webPreferences:{contextIsolation:true,sandbox:true}});
 try{
  await win.loadURL('data:text/html,<div id="host"></div>');
  const js=s=>win.webContents.executeJavaScript(s,true);
  await js(buildSync({entryPoints:[path.join(__dirname,'../ui/crm-account.ts')],bundle:true,format:'iife',globalName:'Entry',write:false}).outputFiles[0].text);
  await js(buildSync({entryPoints:[path.join(__dirname,'../ui/mobile/connection.ts')],bundle:true,format:'iife',globalName:'Mobile',write:false}).outputFiles[0].text);
  await js(`window.calls=[];window.refreshes=0;window.answer={ok:false,text:'Нет связи. Повторите вход.'};window.draw=(account={})=>Entry.mountAccount(document.querySelector('#host'),{connect:async body=>{calls.push(body);return answer}},account,false,async()=>{refreshes++});draw();`);
  await js(`document.querySelector('[name=username]').value='worker';document.querySelector('[name=password]').value='Temporary123';document.querySelector('form').requestSubmit();`);
  assert.equal(await js(`document.querySelector('#account-message').textContent`),'Нет связи. Повторите вход.');
  assert.equal(await js(`document.querySelector('[name=password]').value`),'');
  assert.equal(await js(`document.querySelector('[name=username]').value`),'worker');
  assert.equal(await js('calls[0].action'),'account-login');
  await js(`draw({available:true,username:'worker',pending:true,must_change_password:true});document.querySelector('[name=current]').value='Temporary123';document.querySelector('[name=new]').value='Personal123';document.querySelector('[name=repeat]').value='Different123';document.querySelector('form').requestSubmit();`);
  assert.equal(await js('calls.length'),1);assert.match(await js(`document.querySelector('#account-message').textContent`),/не совпадают/);
  await js(`answer={ok:true,data:{pending:true}};document.querySelector('[name=repeat]').value='Personal123';document.querySelector('form').requestSubmit();`);
  assert.equal(await js('calls.at(-1).action'),'account-password');assert.equal(await js('refreshes'),1);
  await js(`draw({available:true,username:'worker',pending:true});document.querySelector('#account-status').click();`);
  assert.equal(await js('calls.at(-1).action'),'account-status');assert.equal(await js('refreshes'),2);
  await js(`document.querySelector('#account-logout').click()`);
  assert.equal(await js('calls.at(-1).action'),'account-logout');assert.equal(await js('refreshes'),3);
  for(const platform of ['desktop','mobile']){
   await js(`(async()=>{window.signupOptions={ok:true,data:{
     republics:[{code:'tj',name:'Таджикистан',cities:['Душанбе']}],employee_confirmation_paused:true}};
    window.signup={platform:${JSON.stringify(platform)},state:async()=>({connection:{mode:'shared'}}),connect:async body=>{
      calls.push(body);return body.action==='account-registration-options'?signupOptions:{ok:true,data:{pending:body.role!=='employee'}};}};
    window.host=document.querySelector('#host');
    if(signup.platform==='mobile') await Mobile.mountConnection(host,signup,async()=>({ok:true,data:{}}));
    else Entry.mountAccount(host,signup,{},false,async()=>{});
    window.panel=[...host.querySelectorAll('details')].find(details=>details.querySelector('form [name=role]'));
    window.form=panel.querySelector('form');window.control=name=>form.elements.namedItem(name);
    panel.open=true;panel.dispatchEvent(new Event('toggle'));})()`);
   assert.equal(await js('control("staff")===null'),true);
   assert.equal(await js('control("employee").disabled'),false);
   assert.equal(await js('control("code").required && !control("code").disabled'),true);
   await js(`control('employee').value='Synthetic Worker';control('code').value='fixture-code';
     control('username').value='synthetic-employee';control('password').value='Personal123';form.requestSubmit();`);
   assert.equal(await js('calls.at(-1).employee'),'Synthetic Worker');
   assert.equal(await js(`host.querySelector('#account-login [name=username],#mobile-login [name=username]').value`),'synthetic-employee');
   assert.equal(await js('control("password").value'),'');
   await js(`control('role').value='manager';control('role').dispatchEvent(new Event('change'));
     control('country').value='tj';control('country').dispatchEvent(new Event('change'));`);
   assert.deepEqual(await js('Array.from(control("city").options,o=>o.value)'),['','Душанбе']);
   assert.equal(await js('control("code").disabled'),true);
   await js(`control('employee').value='Synthetic Manager';control('username').value='synthetic-manager';
     control('password').value='Personal123';form.requestSubmit();`);
   assert.equal(await js('calls.at(-1).country'),'tj');assert.equal(await js('calls.at(-1).city'),'');
   assert.match(await js(`form.querySelector('[data-registration-status]').textContent`),/администратором/);
   await js(`control('role').value='analyst';control('role').dispatchEvent(new Event('change'));
      control('employee').value='Synthetic Analyst';control('username').value='synthetic-analyst';
      control('password').value='Personal123';form.requestSubmit();`);
   assert.equal(await js('calls.at(-1).role'),'analyst');assert.equal(await js('calls.at(-1).country'),undefined);
  }
  console.log('PASS: Windows and mobile registration forms, login retry, password and device flow, logout');win.destroy();app.exit(0);
 }catch(e){console.error(e);win.destroy();app.exit(1);}
});
