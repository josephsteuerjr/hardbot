// Real Electron/main/preload/DPAPI with isolated synthetic data and two launches.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {app,BrowserWindow,dialog,net,powerMonitor}=require('electron');
const qa=process.env.HARD_CRM_QA_ROOT,phase=Number(process.env.HARD_CRM_DAY_PHASE);
assert(qa && [1,2].includes(phase));const desk=path.resolve(__dirname,'..');
fs.mkdirSync(qa,{recursive:true});process.env.HARD_USER_DATA=path.join(qa,'profile');process.env.HARD_CRM_SOURCE=path.dirname(desk);
app.getVersion=()=>JSON.parse(fs.readFileSync(path.join(desk,'package.json'),'utf8')).version;
const fetch=net.fetch.bind(net);let blocked=true,lose=false,lost=0;
net.fetch=async(url,init)=>{
  if(String(url).endsWith('/crm/registry-edit')){
    if(blocked)throw Error('QA offline');
    const response=await fetch(url,init);
    if(lose && response.ok){await response.arrayBuffer();lose=false;blocked=true;lost++;throw Error('QA lost ACK after commit');}
    return response;
  }
  return fetch(url,init);
};
dialog.showErrorBox=(title,message)=>{fs.writeFileSync(path.join(qa,'startup-error.txt'),title+'\n'+message);app.exit(1);};
require(path.join(desk,'out/main.js'));
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label){for(let n=0;n<350;n++){if(await check())return;await pause(100);}throw Error('Timeout: '+label);}
(async()=>{
  let win;
  try{
    await app.whenReady();await until(()=>{win=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()==='hard://app/index.html');return win;},'window');
    const js=code=>win.webContents.executeJavaScript(code,true);
    const click=s=>js(`document.querySelector(${JSON.stringify(s)}).click()`);
    const exists=s=>js(`!!document.querySelector(${JSON.stringify(s)})`);
    const fill=(s,v)=>js(`(()=>{const e=document.querySelector(${JSON.stringify(s)});e.value=${JSON.stringify(v)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    const api=async(action,query={})=>{const r=await js(`window.crm.api('GET',${JSON.stringify(action)},undefined,${JSON.stringify(query)})`);assert(r.ok,r.text);return r.data;};
    const entries=async()=>((await js('window.crm.queue()')).data.entries || []).filter(e=>e.action==='registry-edit');
    const shot=async(name)=>fs.writeFileSync(path.join(qa,name+'.png'),(await win.webContents.capturePage()).toPNG());
    const openRegistry=async()=>{await click('[data-section=registry]');await until(()=>exists('.registry-grid td[data-field=soft_comment]'),'registry');};
    const edit=async(index,value)=>{
      await js(`document.querySelectorAll('.registry-grid td[data-field=soft_comment]')[${index}].dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))`);
      await fill('.registry-grid textarea',value);
    };
    if(phase===1){
      await until(()=>exists('#diagnostic-run'),'prelogin diagnostics');
      const diag=await js('window.crm.diagnostics()');assert(diag.ok);assert(diag.data.checks.some(c=>c.code==='login_required'));
      await click('#demo-connect');
    }
    await until(()=>exists('[data-person]'),'employee portfolio');
    assert.deepEqual(await js('[...document.querySelectorAll("nav [data-section]")].map(e=>e.innerText)'),['Портфель','Реестр','Мои показатели','Активное время','Подключение']);
    await openRegistry();let rows=(await api('registry')).rows;
    if(phase===1){
      await edit(0,'QA durable registry edit');await click('#registry-save');await until(()=>js('document.querySelector("#registry-preview").open'),'preview');
      await click('#registry-apply');await until(async()=> (await entries())[0]?.state==='pending','pending protected edit');
      await fill('#registry-color','yellow');await fill('#registry-zoom','125');
      await edit(1,'QA unsaved registry draft');await js('document.activeElement.blur()');
      const current=await js('window.crm.state()');
      const wrong=await js('window.crm.registryEdit('+JSON.stringify({schema_version:1,operation_id:'wrong-owner-operation',rows:[]})+',"wrong owner")');assert.equal(wrong.ok,false);
      const files=fs.readdirSync(path.join(qa,'profile/crm')).filter(f=>f.endsWith('.bin'));
      assert(files.every(f=>!fs.readFileSync(path.join(qa,'profile/crm',f)).includes(Buffer.from('QA durable registry edit'))));
      await shot('01-registry-offline');
      fs.writeFileSync(path.join(qa,'PHASE1.json'),JSON.stringify({operation_id:(await entries())[0].body.operation_id,contract:rows[0].contract_uuid,other:rows[1].contract_uuid,owner:current.owner}));
      app.quit();return;
    }
    const checkpoint=JSON.parse(fs.readFileSync(path.join(qa,'PHASE1.json'),'utf8'));
    assert.equal((await entries())[0].body.operation_id,checkpoint.operation_id);
    assert.equal(await js('document.querySelector("#registry-zoom").value'),'125');
    assert.equal(await js('document.querySelector(".registry-grid tbody tr").dataset.color'),'yellow');
    assert((await js('window.crm.draft("registry-input")')).data[checkpoint.other].values.soft_comment==='QA unsaved registry draft');
    blocked=false;lose=true;await js('window.crm.queue("flush")');await until(()=>lost===1,'lost registry acknowledgement');
    assert.equal((await entries())[0].state,'pending');
    const committed=(await api('registry')).rows.find(r=>r.contract_uuid===checkpoint.contract);assert.equal(committed.fields.soft_comment,'QA durable registry edit');
    blocked=false;await js('window.crm.queue("flush")');await until(async()=> (await entries())[0].state==='accepted','exact replay');
    const receipt=(await entries())[0].result;assert.equal(receipt.event_ids.length,1);
    const portfolio=await api('portfolio'),person=portfolio.people.find(p=>p.person_uuid);
    const card=await api('card',{person_uuid:person.person_uuid});
    const allHistory=card.history.filter(e=>e.kind==='registry_edit');
    if(card.contracts.some(c=>c.contract_uuid===checkpoint.contract))assert.equal(allHistory.filter(e=>e.operation_id===checkpoint.operation_id).length,1);
    await click('#registry-cancel');
    // Atomic rejection of a paste crossing read-only columns; no staged subset.
    await js(`(()=>{const cell=document.querySelector('.registry-grid td[data-field=soft_comment]');cell.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));const dt=new DataTransfer();dt.setData('text/plain','One\\tTwo');cell.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,clipboardData:dt}));})()`);
    await until(()=>js('document.querySelector("#registry-status").innerText.includes("недоступные")'),'read-only paste rejected');
    assert.equal(Object.keys((await js('window.crm.draft("registry-input")')).data).length,0);
    // Escape restores the pre-edit draft and clipboard text never executes.
    await edit(0,'=QA text, not formula');await js('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))');
    assert.equal(Object.keys((await js('window.crm.draft("registry-input")')).data).length,0);
    // Stale edit stays out of projections, then explicitly returns to a draft.
    const stale={schema_version:1,operation_id:require('node:crypto').randomUUID(),rows:[{contract_uuid:checkpoint.contract,values:{soft_comment:'QA reviewed correction'},clear:[],base_revisions:{call:0}}]};
    assert((await js('window.crm.registryEdit('+JSON.stringify(stale)+')')).ok);
    await js('window.crm.queue("flush")');
    await until(async()=> (await entries()).some(e=>e.body.operation_id===stale.operation_id&&e.state==='attention'),'stale edit rejected');
    assert.equal((await api('registry')).rows.find(r=>r.contract_uuid===checkpoint.contract).fields.soft_comment,'QA durable registry edit');
    await click('#registry-review');await until(()=>js('document.querySelector("#registry-preview").open'),'conflict comparison');
    assert(await js('document.querySelector("#registry-preview").innerText.includes("QA reviewed correction")'));
    await click('#registry-apply');await until(()=>js('!document.querySelector("#registry-preview").open'),'conflict draft');
    await click('#registry-save');await until(()=>js('document.querySelector("#registry-preview").open'),'reviewed preview');
    await click('#registry-apply');await js('window.crm.queue("flush")');
    await until(async()=> (await api('registry')).rows.find(r=>r.contract_uuid===checkpoint.contract).fields.soft_comment==='QA reviewed correction','reviewed correction accepted');
    const original=(await entries()).find(e=>e.body.operation_id===stale.operation_id);assert(original.reviewed_at && original.state==='attention');
    await shot('02-registry-confirmed');
    const cabinet=await api('cabinet',{month:new Date().toLocaleDateString('sv-SE').slice(0,7)});assert.equal(cabinet.stats.call || 0,0);assert.equal(cabinet.stats.processed_contracts,0);
    await click('[data-section=active-time]');await until(()=>exists('#active-result table'),'active time screen');await shot('03-active-time');
    powerMonitor.emit('suspend');powerMonitor.emit('resume');
    await click('[data-section=portfolio]');await until(()=>exists('[data-person]'),'portfolio');await click('[data-person]');await until(()=>exists('.business-details'),'complete business card');
    await js('document.querySelector(".business-details").open=true');await pause(200);await shot('04-business-card');
    await click('[data-section=connection]');await until(()=>exists('#diagnostic-run'),'diagnostics');
    const diag=await js('window.crm.diagnostics(true)');assert(diag.ok);assert(diag.data.checks.some(c=>c.code==='identity_confirmed'));
    await shot('05-diagnostics');
    const ownerBefore=(await js('window.crm.state()')).owner;
    assert((await js('window.crm.connect({mode:"synthetic",role:"observer"})')).ok);win.webContents.reload();
    await until(async()=> (await js('window.crm.state()')).context?.role==='observer' && await exists('[data-section=portfolio]'),'other identity');
    assert.equal((await js('window.crm.draft("registry-colors")')).data,null);
    assert((await api('registry',{view:'all'})).rows.every(r=>r.writable_fields.length===0));
    assert.equal((await js('window.crm.draft("registry-input",{},'+JSON.stringify(ownerBefore)+')')).ok,false);
    assert((await js('window.crm.connect({mode:"synthetic",role:"employee"})')).ok);win.webContents.reload();
    await until(()=>exists('[data-section=registry]'),'original identity');
    assert.equal((await js('window.crm.draft("registry-colors")')).data[checkpoint.contract],'yellow');
    fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:true,actual_main:true,actual_preload:true,protected_queue:true,restart_draft_and_colors:true,lost_ack_idempotent:true,foreign_owner_rejected:true,personal_colors_separate_identities:true,conflict_restaged_and_confirmed:true,read_only_paste_atomic:true,escape_undo:true,no_fake_work:true,business_card:true,active_time_screen:true,prelogin_and_authenticated_diagnostics:true},null,2));
    app.quit();
  }catch(error){
    fs.writeFileSync(path.join(qa,'failure.txt'),String(error)+'\n'+(win?await win.webContents.executeJavaScript('document.body.innerText'):''));
    if(win)fs.writeFileSync(path.join(qa,'failure.png'),(await win.webContents.capturePage()).toPNG());
    console.error(error);app.exit(1);
  }
})();
