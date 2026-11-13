// Actual desktop/preload/DPAPI/source backend; isolated synthetic profile only.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {app,BrowserWindow,dialog,net}=require('electron');
const qa=process.env.HARD_CRM_QA_ROOT,phase=Number(process.env.HARD_CRM_DAY_PHASE);
assert(qa && [1,2].includes(phase),'Explicit isolated QA profile and phase required');
const desk=path.resolve(__dirname,'..');fs.mkdirSync(qa,{recursive:true});
process.env.HARD_USER_DATA=path.join(qa,'profile');process.env.HARD_CRM_SOURCE=path.dirname(desk);
app.getVersion=()=>JSON.parse(fs.readFileSync(path.join(desk,'package.json'),'utf8')).version;
let blocked=true,loseNext=false,lost=0;
const fetch=net.fetch.bind(net);
net.fetch=async(url,init)=>{
  const command=String(url).endsWith('/crm/command');
  if(command && blocked)throw Error('QA disconnected before request');
  const response=await fetch(url,init);
  if(command && loseNext && response.ok){
    await response.arrayBuffer();loseNext=false;blocked=true;lost++;
    throw Error('QA reply lost after actual commit');
  }
  return response;
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
    const screenshot=async name=>fs.writeFileSync(path.join(qa,name+'.png'),(await win.webContents.capturePage()).toPNG());
    const click=selector=>js('document.querySelector('+JSON.stringify(selector)+').click()');
    const fill=(selector,value)=>js('(()=>{const el=document.querySelector('+JSON.stringify(selector)+');el.value='+JSON.stringify(value)+';el.dispatchEvent(new Event("input",{bubbles:true}));})()');
    const queue=async()=> (await js('window.crm.queue()')).data.entries;
    const openForm=async kind=>{await click('[data-action='+kind+']');await until(()=>js('document.querySelector("#dialog").open'),'form '+kind);};
    const submit=async()=>{await js('document.querySelector("#action-form").requestSubmit()');await until(()=>js('!document.querySelector("#dialog").open'),'durable save');};
    if(phase===1){
      await until(()=>js('!!document.querySelector(".connection-testing")'),'unconfigured entry');
      assert.equal((await js('window.crm.state()')).connection.mode,'unconfigured');
      assert.equal((await js('window.crm.state()')).context,null);
      assert.equal(await js('document.querySelectorAll("[data-section=portfolio]").length'),0);
      assert.equal(await js('document.querySelector(".connection-testing").open'),false);
      assert.equal((await js('window.crm.api("GET","portfolio")')).status,401);
      await click('.connection-testing summary');await click('#demo-connect');
    }
    await until(()=>js('!!document.querySelector("[data-person]")'),'personal portfolio');
    const person=await js('document.querySelector("[data-person]").dataset.person');
    await click('[data-person]');await until(()=>js('!!document.querySelector("[data-action=call]")'),'client');
    const card=async()=> (await js('window.crm.api("GET","card",undefined,{person_uuid:'+JSON.stringify(person)+'})')).data;
    if(phase===1){
      await openForm('call');await fill('#v-soft_call_status','Нет контакта');await fill('#v-soft_comment','QA first offline call');await submit();
      await until(async()=> (await queue())[0]?.state==='pending','pending call');
      await until(()=>js('!!document.querySelector("[data-save-state=pending]")'),'pending state in card');
      const operation=(await queue())[0].body.operation_id;
      await openForm('visit');await fill('#v-visit_comment','QA visit draft after restart');await click('#cancel');
      await until(async()=>{
        const saved=await js('window.crm.draft('+JSON.stringify(person+':visit')+')');return saved.data?.values?.visit_comment==='QA visit draft after restart';
      },'protected visit draft');
      const files=fs.readdirSync(path.join(qa,'profile/crm')).filter(name=>name.startsWith('business-'));
      assert.equal(files.length,1);
      assert(!fs.readFileSync(path.join(qa,'profile/crm',files[0])).includes(Buffer.from('QA first offline call')));
      await screenshot('01-offline-card');
      fs.writeFileSync(path.join(qa,'PHASE1.json'),JSON.stringify({stage:'pending_and_draft_saved',operation_id:operation,person}));
      app.quit();return;
    }
    const saved=JSON.parse(fs.readFileSync(path.join(qa,'PHASE1.json'),'utf8'));
    assert.equal(person,saved.person);assert.equal((await queue())[0].body.operation_id,saved.operation_id);
    assert.equal((await queue())[0].state,'pending');
    await openForm('visit');assert.equal(await js('document.querySelector("#v-visit_comment").value'),'QA visit draft after restart');await click('#cancel');
    await openForm('call');await fill('#v-soft_call_status','Нет контакта');await fill('#v-soft_comment','QA second call while first is sending');
    blocked=false;await js('window.crm.queue("flush")');
    await until(()=>js('!!document.querySelector("[data-save-state=accepted]")'),'live acknowledgement');
    assert(await js('document.querySelector("#dialog").open'));
    assert.equal(await js('document.querySelector("#v-soft_comment").value'),'QA second call while first is sending');
    await submit();
    await until(async()=> (await queue())[1]?.state==='accepted','second history accepted');
    let entries=await queue();
    assert.equal(entries[1].result.projection_status,'needs_review');
    assert(Object.values(entries[1].body.base_revisions).every(revision=>revision.call===0));
    await until(()=>js('document.querySelector("#card-saves").innerText.includes("текущие поля требуют сверки")'),'review shown');
    await until(()=>js('document.querySelector("#card-content").innerText.includes("QA first offline call")'),'fresh first result');
    assert((await card()).contracts.every(c=>c.fields.soft_comment==='QA first offline call'));
    await openForm('visit');await fill('#v-visit_comment','QA accepted field visit');await fill('#v-visit_date',new Date().toLocaleDateString('sv-SE'));await submit();
    await until(async()=> (await queue())[2]?.state==='accepted','visit accepted');
    assert.equal((await queue())[2].result.projection_status,'applied');
    await until(()=>js('document.querySelector("#card-content").innerText.includes("QA accepted field visit")'),'live visit result');
    assert((await card()).contracts.every(c=>c.fields.soft_comment==='QA first offline call' && c.fields.visit_comment==='QA accepted field visit'));
    await openForm('call');await fill('#v-soft_call_status','Нет контакта');await fill('#v-soft_comment','QA call with lost acknowledgement');loseNext=true;await submit();
    await until(()=>lost===1,'reply lost after commit');
    entries=await queue();assert.equal(entries[3].state,'pending');
    const operation=entries[3].body.operation_id,before=await card();
    const committed=before.history.find(event=>event.operation_id===operation);assert(committed);
    assert.equal(before.history.length,4);
    blocked=false;await js('window.crm.queue("flush")');
    await until(async()=> (await queue())[3]?.state==='accepted','exact retried acknowledgement');
    entries=await queue();assert.equal(entries[3].body.operation_id,operation);assert.equal(entries[3].result.event_id,committed.event_id);
    assert.equal(entries[3].result.projection_status,'applied');
    await until(()=>js('document.querySelector("#card-content").innerText.includes("QA call with lost acknowledgement")'),'latest applied call');
    const final=await card();assert.equal(final.history.length,4);
    assert(final.contracts.every(c=>c.fields.soft_comment==='QA call with lost acknowledgement' && c.fields.visit_comment==='QA accepted field visit'));
    await screenshot('02-confirmed-card');
    await click('[data-tab=history]');await screenshot('03-operation-history');
    fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:true,actual_main:true,actual_preload:true,source_backend:true,
      protected_queue:true,restart_restores_pending_and_draft:true,input_survives_ack:true,frozen_revision_detects_conflict:true,
      independent_call_and_visit:true,lost_reply_retries_same_event:true,explicit_entry:true,history_events:final.history.length,business_commands:entries.length},null,2));
    app.quit();
  }catch(error){
    console.error(error);
    if(win){await screenshotFailure(win,error);}
    app.exit(1);
  }
}
async function screenshotFailure(win,error){
  fs.writeFileSync(path.join(qa,'failure.png'),(await win.webContents.capturePage()).toPNG());
  fs.writeFileSync(path.join(qa,'failure.txt'),String(error)+'\n'+await win.webContents.executeJavaScript('document.body.innerText'));
}
run();
