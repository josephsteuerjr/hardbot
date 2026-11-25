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
    await until(()=>{win=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()==='hard://app/index.html');return win;},'CRM window');
    const js=code=>win.webContents.executeJavaScript(code,true);
    const exists=selector=>js('!!document.querySelector('+JSON.stringify(selector)+')');
    const click=selector=>js('document.querySelector('+JSON.stringify(selector)+').click()');
    const fill=(selector,value)=>js('(()=>{const el=document.querySelector('+JSON.stringify(selector)+');el.value='+JSON.stringify(value)+';el.dispatchEvent(new Event("input",{bubbles:true}));el.dispatchEvent(new Event("change",{bubbles:true}));})()');
    const screenshot=async name=>fs.writeFileSync(path.join(qa,name+'.png'),(await win.webContents.capturePage()).toPNG());
    const api=async(action,query={})=>{
      const result=await js('window.crm.api("GET",'+JSON.stringify(action)+',undefined,'+JSON.stringify(query)+')');
      assert(result.ok,result.text);return result.data;
    };
    const entries=async()=> (await js('window.crm.queue()')).data.entries;
    const allAccepted=async()=>{const q=await entries();return q.length>0 && q.every(e=>e.state==='accepted');};
    const role=async name=>{
      await click('[data-section=connection]');await until(()=>exists('.connection-testing'),'connection');
      await click('.connection-testing summary');await fill('#demo-role',name);await click('#demo-connect');
      await until(async()=> (await js('window.crm.state()')).context?.role===name && await exists('[data-person]'),'role '+name);
      return (await js('window.crm.state()')).context;
    };
    const section=async(name,selector)=>{await click('[data-section='+name+']');await until(()=>exists(selector),name);};
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
    await section('cabinet','#cabinet-employee');await fill('#cabinet-employee',employee.employee_uuid);await click('#cabinet-refresh');
    await until(()=>js('document.querySelector("#cabinet-result").innerText.includes("QA employee call")'),'team cabinet');
    const cabinet=await api('cabinet',{employee_uuid:employee.employee_uuid,month:new Date().toLocaleDateString('sv-SE').slice(0,7)});
    assert.equal(cabinet.stats.call,1);assert.equal(cabinet.stats.visit,1);assert.equal(cabinet.stats.task_complete,1);
    await section('shift','#shift-report-result');
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
    await role('analyst');await section('portfolio','[data-person]');
    await click('[data-person="'+person+'"]');await until(()=>exists('[data-tab=history]'),'analyst client');
    await click('[data-tab=history]');await until(()=>js('document.querySelector("#card-content").innerText.includes("QA delivered to client")'),'shared delivery history');
    await screenshot('05-shared-history');
    assert.equal(await exists('[data-action=call]'),false);
    await section('exchange','#export-new');assert(await exists('#import-file'));assert(await exists('#export-template'));
    await click('#export-new');
    await until(()=>js('document.querySelector("#exchange-result").innerText.includes("повторно прочитана")'),'native common registry');
    assert(fs.existsSync(savePath));await until(allAccepted,'registry receipt');
    const digest=file=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    const original=digest(savePath),targetContract=card.contracts[0].contract_uuid;
    const edit=`import json,sys,xlwings as xw
application=xw.App(visible=False,add_book=False)
application.display_alerts=False
application.api.AutomationSecurity=3
application.api.EnableEvents=False
try:
    book=application.books.open(sys.argv[1],update_links=False)
    try:
        chunks=book.sheets['_CRM'].range('A1').expand('down').value
        meta=json.loads(''.join(chunks if isinstance(chunks,list) else [chunks]))
        sheet=book.sheets['Портфель'];columns=meta['columns']
        row=next(index for index in range(4,sheet.used_range.last_cell.row+1) if sheet.range((index,columns['_crm_contract_uuid'])).value==sys.argv[3])
        sheet.range((row,columns['phone_main'])).value='+79990000002'
        extra=book.sheets.add('Проверка копии');extra.range('A1').value='Сохранить этот лист';extra.range('B1').formula='=2+3'
        book.save(sys.argv[2])
    finally:book.close()
finally:application.quit()
`;
    execFileSync(process.env.HARD_CRM_PYTHON||'python',['-X','utf8','-c',edit,savePath,openPath,targetContract],{windowsHide:true,timeout:120000,stdio:'pipe'});
    assert.equal(digest(savePath),original,'Editing a copy must preserve original export');
    await click('#import-file');await until(()=>exists('#apply-import'),'analyst preview');
    assert((await js('document.querySelector("#exchange-result").innerText')).includes('Изменений: 1 · Конфликтов: 0'));
    await screenshot('06-analyst-preview');await click('#apply-import');
    await until(()=>js('document.querySelector("#exchange-result").innerText.includes("Импорт подтверждён")'),'analyst apply');
    const reviewed=await api('card',{person_uuid:person});
    assert.equal(reviewed.contracts.find(c=>c.contract_uuid===targetContract).fields.phone_main,'+79990000002');
    assert.equal(reviewed.history.filter(h=>h.kind==='manual_edit').length,1);
    const manual=digest(openPath);savePath=path.join(qa,'template-copy.xlsx');await click('#export-template');
    await until(()=>js('document.querySelector("#exchange-result").innerText.includes("повторно прочитана") && document.querySelector("#exchange-result").innerText.includes("template-copy.xlsx")'),'existing workbook copy');
    assert.equal(digest(openPath),manual,'Template source must remain byte-identical');
    const verify=`import sys,xlwings as xw
application=xw.App(visible=False,add_book=False)
application.display_alerts=False
application.api.AutomationSecurity=3
application.api.EnableEvents=False
try:
    book=application.books.open(sys.argv[1],update_links=False,read_only=True)
    try:
        assert book.sheets['Проверка копии'].range('A1').value=='Сохранить этот лист'
        assert book.sheets['Проверка копии'].range('B1').formula=='=2+3'
        assert book.sheets['Проверка копии'].range('B1').value==5
    finally:book.close()
finally:application.quit()
`;
    execFileSync(process.env.HARD_CRM_PYTHON||'python',['-X','utf8','-c',verify,savePath],{windowsHide:true,timeout:120000,stdio:'pipe'});
    await until(allAccepted,'copy receipt');await screenshot('07-analyst-copy');
    fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:true,actual_main:true,actual_preload:true,source_backend:true,
      explicit_entry:true,manager_script:true,watermark_and_script_reference:true,manager_to_employee_task:true,
      task_return_and_completion:true,team_cabinet:true,shifts_and_team_report:true,courier_offline:true,
      shared_delivery_history:true,analyst_exchange_navigation:true,actual_excel:true,common_registry_export:true,
      reviewed_manual_edit:true,template_source_unchanged:true,custom_sheet_and_formula_preserved:true,role_separation:true},null,2));
    app.quit();
  }catch(error){
    if(win){fs.writeFileSync(path.join(qa,'failure.png'),(await win.webContents.capturePage()).toPNG());fs.writeFileSync(path.join(qa,'failure.txt'),String(error)+'\n'+await win.webContents.executeJavaScript('document.body.innerText'));}
    console.error(error);app.exit(1);
  }
}
run();
