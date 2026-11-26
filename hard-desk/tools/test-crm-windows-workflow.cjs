// Real desktop, preload, queue and source backend. Synthetic isolated profile.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process'),{createHash}=require('node:crypto');
const {app,BrowserWindow,dialog,net}=require('electron');
const qa=process.env.HARD_CRM_QA_ROOT;assert(qa,'Explicit QA root required');
const desk=path.resolve(__dirname,'..');fs.mkdirSync(qa,{recursive:true});
process.env.HARD_USER_DATA=path.join(qa,'profile');process.env.HARD_CRM_SOURCE=path.dirname(desk);
assert(!fs.existsSync(process.env.HARD_USER_DATA),'Use a fresh isolated profile');
app.getVersion=()=>JSON.parse(fs.readFileSync(path.join(desk,'package.json'),'utf8')).version;
let blocked=false;
let savePath=path.join(qa,'general.xlsx'),openPath=path.join(qa,'manual.xlsx');
dialog.showSaveDialog=async()=>({canceled:false,filePath:savePath});
dialog.showOpenDialog=async()=>({canceled:false,filePaths:[openPath]});
const fetch=net.fetch.bind(net);
net.fetch=(url,init)=>{
  if(blocked && String(url).endsWith('/crm/command'))throw Error('QA disconnected before request');
  return fetch(url,init);
};
dialog.showErrorBox=(title,message)=>{fs.writeFileSync(path.join(qa,'startup-error.txt'),title+'\n'+message);app.exit(1);};
require(path.join(desk,'out/main.js'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label){for(let i=0;i<400;i++){if(await check())return;await pause(100);}throw Error('Timeout: '+label);}
async function run(){
  let win;
  try{
    await app.whenReady();
    await until(()=>{win=BrowserWindow.getAllWindows().find(w=>w.isVisible()&&w.webContents.getURL()==='hard://app/index.html');return win;},'visible CRM window');
    const js=code=>win.webContents.executeJavaScript(code,true);
    const exists=selector=>js('!!document.querySelector('+JSON.stringify(selector)+')');
    const click=selector=>js('document.querySelector('+JSON.stringify(selector)+').click()');
    const fill=(selector,value)=>js('(()=>{const el=document.querySelector('+JSON.stringify(selector)+');el.value='+JSON.stringify(value)+';el.dispatchEvent(new Event("input",{bubbles:true}));el.dispatchEvent(new Event("change",{bubbles:true}));})()');
    const screenshot=async name=>fs.writeFileSync(path.join(qa,name+'.png'),(await win.webContents.capturePage()).toPNG());
    const api=async(action,query={})=>{
      const result=await js('window.crm.api("GET",'+JSON.stringify(action)+',undefined,'+JSON.stringify(query)+')');
      assert(result.ok,result.text);return result.data;
    };
    const entries=async()=> (await js('window.crm.queue()')).data.entries.filter(e=>e.action==='command');
    const allAccepted=async()=>{const q=await entries();return q.length>0 && q.every(e=>e.state==='accepted');};
    const role=async name=>{
      await click('[data-section=connection]');await until(()=>exists('.connection-testing'),'connection');
      await click('.connection-testing summary');await fill('#demo-role',name);await click('#demo-connect');
      await until(async()=> (await js('window.crm.state()')).context?.role===name && await exists('[data-person]'),'role '+name);
      return (await js('window.crm.state()')).context;
    };
    const section=async(name,selector)=>{
      if(name==='tasks'){
        const r=(await js('window.crm.state()')).context.role;
        if(r==='manager'){await click('[data-section=script]');await until(()=>exists('[data-tool=tasks]'),'manager task tab');await click('[data-tool=tasks]');}
        else {await click('[data-section=portfolio]');await until(()=>exists('#portfolio-tasks'),'portfolio tools');await click('#portfolio-tasks');}
      }else if(name==='shift'){
        await click('[data-section=active-time]');await until(()=>exists('#active-shift'),'active time');await click('#active-shift');
      }else await click('[data-section='+name+']');
      await until(()=>exists(selector),name);
    };
    await until(()=>exists('.connection-testing'),'entry');
    assert.equal((await js('window.crm.state()')).context,null);
    assert.equal(await js('document.querySelectorAll("[data-section=portfolio]").length'),0);
    assert.equal(await js('document.querySelector(".connection-testing").open'),false);
    await screenshot('01-entry');
    const manager=await role('manager');
    const employee=manager.staff.find(person=>person.name==='Тестовый сотрудник');assert(employee);
    await section('script','#script-edit');await click('#script-edit');await until(()=>exists('#script-form'),'script editor');
    await fill('#script-title','Порядок разговора');
    await fill('[data-script-step] input','Проверить собеседника');
    await fill('[data-script-step] textarea','QA script published by manager');
    await js('document.querySelector("#script-form").requestSubmit()');
    await until(()=>js('!document.querySelector("#dialog").open'),'saved script');
    await click('#script-publish');await until(async()=> (await api('script')).script?.title==='Порядок разговора','published script');
    await section('tasks','#task-create');await fill('#task-employee',employee.employee_uuid);
    await click('#task-create');await until(()=>exists('[data-person]'),'client chooser');
    const person=await js('document.querySelector("[data-person]").dataset.person');
    await click('[data-person]');await until(()=>exists('#task-assignee'),'task form');
    assert.equal(await js('document.querySelector("#task-assignee").value'),employee.employee_uuid);
    await fill('#free-text','QA позвонить и уточнить оплату');await fill('#task-work','call');
    await js('document.querySelector("#action-form").requestSubmit()');await until(allAccepted,'task accepted');
    await section('tasks','[data-task-row]');
    const task=(await api('tasks',{status:'open',employee_uuid:employee.employee_uuid})).tasks.find(t=>t.title==='QA позвонить и уточнить оплату');assert(task);
    await screenshot('02-manager-task');
    await role('employee');await section('tasks','[data-task-row]');
    assert(await exists('[data-task-row="'+task.entity_id+'"]'));
    await click('[data-task-row="'+task.entity_id+'"] [data-task-person]');await until(()=>exists('[data-action=call]'),'employee card');
    await until(()=>js('document.querySelector(".card-script-watermark")?.innerText.includes("QA script published by manager")'),'watermark');
    for(const [kind,selector,text] of [['call','#v-soft_comment','QA employee call'],['visit','#v-visit_comment','QA employee visit']]){
      await click('[data-action='+kind+']');await until(()=>js('document.querySelector("#dialog").open'),'form '+kind);
      await fill(selector,text);
      if(kind==='call')await fill('#v-soft_call_status','Нет контакта');
      else await fill('#v-visit_date',new Date().toLocaleDateString('sv-SE'));
      await js('document.querySelector("#action-form").requestSubmit()');await until(allAccepted,kind+' accepted');
      await until(()=>js('document.querySelector("#card-content").innerText.includes('+JSON.stringify(text)+')'),'fresh '+kind);
    }
    const card=await api('card',{person_uuid:person});
    assert(card.history.filter(h=>['call','visit'].includes(h.kind)).every(h=>h.employee_uuid===employee.employee_uuid));
    const call=card.history.find(h=>h.kind==='call');assert(call.payload.script_ref,'Call must retain the published script reference');
    await js('document.querySelector("main").scrollTop=150');
    assert(await js('document.querySelector(".window-controls").getBoundingClientRect().top>=0'),'Window controls must remain visible while scrolling');
    await js('document.querySelector("main").scrollTop=0');
    await screenshot('03-employee-card');
    await click('#back');await until(()=>exists('[data-task-row]'),'return to tasks');
    await click('[data-task-complete="'+task.entity_id+'"]');await until(allAccepted,'task completion accepted');
    await click('[data-task-filter=done]');await until(()=>exists('[data-task-row="'+task.entity_id+'"]'),'completed employee task');
    await section('shift','#shift-start');await click('#shift-start');await until(()=>exists('#shift-pause'),'started shift');
    await click('#shift-pause');await until(()=>js('document.querySelector("#shift-pause")?.innerText==="Продолжить"'),'break');
    await click('#shift-pause');await until(()=>js('document.querySelector("#shift-pause")?.innerText==="Перерыв"'),'resumed');
    await click('#shift-finish');await until(()=>exists('#shift-start'),'closed shift');
    await role('manager');await section('tasks','[data-task-filter=done]');await click('[data-task-filter=done]');
    await until(()=>exists('[data-task-row="'+task.entity_id+'"]'),'manager sees completion');
    const completed=(await api('tasks',{status:'done',employee_uuid:employee.employee_uuid})).tasks.find(t=>t.entity_id===task.entity_id);assert.equal(completed?.status,'done');
    await screenshot('04-manager-completed');
    const cabinet=await api('cabinet',{employee_uuid:employee.employee_uuid,month:new Date().toLocaleDateString('sv-SE').slice(0,7)});
    assert.equal(cabinet.stats.call,1);assert.equal(cabinet.stats.visit,1);assert.equal(cabinet.stats.task_complete,1);
    const day=new Date().toLocaleDateString('sv-SE'),report=await api('shift-report',{since:day,until:day});
    assert(report.employees.some(e=>e.employee_uuid===employee.employee_uuid && e.shifts===1 && e.unclosed===0));
    await role('courier');await click('[data-person]');await until(()=>exists('[data-action=delivery]'),'courier client');
    assert.equal(await exists('[data-action=call]'),false);
    assert.equal(await exists('[data-action=visit]'),false);
    await click('[data-action=delivery]');await until(()=>exists('[data-courier-form]'),'courier form');
    const contract=await js('Array.from(document.querySelector("#courier-contract").options).find(o=>o.value)?.value');
    await fill('#courier-contract',contract);await until(()=>js('!document.querySelector("#courier-comment").disabled'),'contract selected');
    await fill('#courier-address','QA delivery address');await fill('#courier-phone','-');await fill('#courier-comment','QA delivered to client');
    blocked=true;await js('document.querySelector("[data-courier-form]").requestSubmit()');
    await until(async()=> (await entries())[0]?.state==='pending','courier durable offline save');
    await click('[data-courier-close]');await until(()=>js('!document.querySelector("#dialog").open'),'courier close');
    blocked=false;await js('window.crm.queue("flush")');await until(allAccepted,'courier acknowledged');
    await until(()=>js('document.querySelector("#card-saves")?.innerText.includes("Принято сервером")'),'courier card acknowledgement');
    await role('observer');await click('[data-person]');await until(()=>exists('[data-tab=history]'),'observer card');
    assert.equal(await exists('[data-action=call]'),false);assert.equal(await exists('[data-action=visit]'),false);
    const own=(await js('window.crm.state()')).context;
    assert.equal(own.role,'observer');
    fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:true,actualMain:true,actualBackend:true,
      managerScript:true,employeeTaskAndCompletion:true,taskReturn:true,shift:true,cabinetAndShiftApi:true,
      courierOfflineDelivery:true,observerReadOnly:true},null,2));
    app.quit();
  }catch(error){
    fs.writeFileSync(path.join(qa,'failure.txt'),error.stack||String(error));
    if(win?.isVisible())try{fs.writeFileSync(path.join(qa,'failure.png'),(await win.webContents.capturePage()).toPNG());}catch{}
    console.error(error);app.exit(1);
  }
}
run();
