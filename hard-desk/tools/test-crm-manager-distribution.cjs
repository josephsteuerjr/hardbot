// Real renderer/main/preload, protected intent, source backend. Synthetic only.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {app,BrowserWindow,dialog,net}=require('electron');
const qa=process.env.HARD_CRM_QA_ROOT;assert(qa,'Explicit isolated QA root required');
const desk=path.resolve(__dirname,'..');fs.mkdirSync(qa,{recursive:true});
process.env.HARD_USER_DATA=path.join(qa,'profile');process.env.HARD_CRM_SOURCE=path.dirname(desk);
app.getVersion=()=>JSON.parse(fs.readFileSync(path.join(desk,'package.json'),'utf8')).version;
const original=net.fetch.bind(net);let loseAck=false,lost=0;
net.fetch=async(url,init)=>{const response=await original(url,init);if(loseAck&&String(url).endsWith('/crm/assignment-commit')&&response.ok){await response.arrayBuffer();loseAck=false;lost++;throw Error('Synthetic lost ACK after commit');}return response;};
dialog.showErrorBox=(title,message)=>{fs.writeFileSync(path.join(qa,'startup-error.txt'),title+'\n'+message);app.exit(1);};
require(path.join(desk,'out/main.js'));
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label){for(let i=0;i<400;i++){if(await fn())return;await pause(100);}throw Error('Timeout: '+label);}
(async()=>{
  let win;
  try{
    await app.whenReady();await until(()=>{win=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()==='hard://app/index.html');return win;},'window');
    const js=code=>win.webContents.executeJavaScript(code,true),exists=s=>js(`!!document.querySelector(${JSON.stringify(s)})`);
    const click=s=>js(`document.querySelector(${JSON.stringify(s)}).click()`);
    const fill=(s,v)=>js(`(()=>{const e=document.querySelector(${JSON.stringify(s)});e.value=${JSON.stringify(v)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    const api=async(action,query={})=>{const r=await js(`window.crm.api('GET',${JSON.stringify(action)},undefined,${JSON.stringify(query)})`);assert(r.ok,r.text);return r.data;};
    const shot=async name=>fs.writeFileSync(path.join(qa,name+'.png'),(await win.webContents.capturePage()).toPNG());
    await until(()=>exists('#demo-role'),'connect');await fill('#demo-role','manager');await click('#demo-connect');
    await until(()=>exists('[data-person]'),'client portfolio');
    assert.deepEqual(await js('[...document.querySelectorAll("nav [data-section]")].map(e=>e.innerText)'),['Портфель','Реестр','Показатели','Команда','Скрипты и задачи','Подключение']);
    await click('[data-section=registry]');await until(()=>exists('[data-allocation=replace]'),'single registry');
    const before=await api('managed-portfolio');assert.equal(before.people.length,3);
    const targets=(await api('assignment-staff')).staff,target=targets.find(p=>p.name==='Сотрудник без аккаунта');assert(target&&!target.has_account);
    for(const c of before.people[0].contracts)await click(`[data-select-row="${c.contract_uuid}"]`);
    await click('[data-allocation=replace]');await until(()=>js('document.querySelector("#allocation-dialog").open'),'editor');
    await fill('#allocation-target',target.employee_uuid);
    await until(()=>js('!document.querySelector("#allocation-submit").disabled'),'preview ready');
    await fill('#allocation-note','Проверка сохранения ввода');
    await until(()=>js('!document.querySelector("#allocation-submit").disabled'),'comment preview');
    await shot('manager-preview');loseAck=true;await click('#allocation-submit');
    await until(()=>js('!document.querySelector("#allocation-submit").disabled&&document.querySelector("#allocation-submit").textContent.includes("Проверить")'),'lost ACK');assert.equal(lost,1);
    const intent=(await js('window.crm.draft("managed-allocation-intent")')).data;assert(intent.operation_id);
    await click('#allocation-submit');await until(()=>js('document.querySelector("#allocation-status").textContent.includes("Применено:")'),'receipt');
    assert.equal((await api('assignment-receipt',{operation_id:intent.operation_id})).changes.length,2);
    await click('#allocation-close');await shot('manager-after-transfer');
    const after=await api('managed-portfolio');assert(after.people[0].contracts.every(c=>c.slots.find(s=>s.capability==='call').employee_uuid===target.employee_uuid));
    assert(after.people[0].contracts.every(c=>c.slots.find(s=>s.capability==='visit').name==='Тестовый сотрудник'));
    await click('#registry-select-all');await click('[data-allocation=remove]');await fill('#allocation-kind','both');
    await until(()=>js('!document.querySelector("#allocation-submit").disabled'),'remove preview');await click('#allocation-submit');
    await until(()=>js('document.querySelector("#allocation-status").textContent.includes("Применено:")'),'remove');await click('#allocation-close');
    const stats=await api('manager-statistics');assert.equal(stats.unassigned_slots,8);assert.equal(stats.contracts,4);assert.deepEqual(stats.debt,{RUB:'70000.00'});
    await click('#registry-unassigned');await until(()=>exists('.registry-grid tbody tr'),'unassigned filter');
    await click('[data-section=manager-statistics]');await until(()=>exists('#report-body table'),'statistics');await shot('manager-statistics');
    await click('[data-section=team]');await until(()=>exists('#team-list table'),'team');await shot('manager-team');
    fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:true,actual_main:true,actual_preload:true,dpapi_intent:true,lost_ack_exact_receipt:true,work_independence:true,bulk_remove:true,unassigned_management:true,unique_debt:true,source_backend:true},null,2));app.quit();
  }catch(e){fs.writeFileSync(path.join(qa,'FAIL.txt'),e.stack||String(e));if(win){fs.writeFileSync(path.join(qa,'failure.png'),(await win.webContents.capturePage()).toPNG());fs.writeFileSync(path.join(qa,'failure.txt'),await win.webContents.executeJavaScript('document.body.innerText').catch(()=>''));}app.exit(1);}
})();
