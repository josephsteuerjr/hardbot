// Employee recovery fault injection; independent of the older draft regression.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const cp=require('node:child_process');const {app,BrowserWindow,net,dialog}=require('electron');
const qa=process.env.HARD_CRM_QA_ROOT;assert(qa);fs.mkdirSync(qa,{recursive:true});
const desk=path.resolve(__dirname,'..'),startup=process.env.HARD_CRM_RECOVERY_MODE==='startup';
dialog.showSaveDialog=async()=>({canceled:false,filePath:path.join(qa,'diagnostics-report.json')});
process.env.HARD_USER_DATA=path.join(qa,'profile');process.env.HARD_CRM_SOURCE=path.dirname(desk);
process.env.HARD_CRM_PYTHON='crm-qa-missing-python.exe';
if(startup)process.argv.push('--crm-diagnose');
let helper,blockSave=true,blockRead=false;
const spawn=cp.spawn;cp.spawn=(...args)=>{const child=spawn(...args);if(args[1]?.includes('--crm-pilot'))helper=child;return child;};
const fetch=net.fetch.bind(net);net.fetch=(url,init)=>{
  if(blockSave && String(url).endsWith('/crm/registry-edit') || blockRead && String(url).endsWith('/crm/registry'))throw Error('QA offline');
  return fetch(url,init);
};
require(path.join(desk,'out/main.js'));
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(check,label){for(let n=0;n<300;n++){if(await check())return;await pause(100);}throw Error('Timeout '+label);}
(async()=>{
  let win;
  try{
    await app.whenReady();await until(()=>{win=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().startsWith(startup?'data:text/html':'hard://app/index.html'));return win;},'window');
    const js=code=>win.webContents.executeJavaScript(code,true);
    if(startup){
      await until(()=>js('document.body.innerText.includes("manual_startup_diagnostics")'),'independent diagnostic screen');
      assert.equal(await js('typeof window.crm'),'undefined');
      fs.writeFileSync(path.join(qa,'startup.png'),(await win.webContents.capturePage()).toPNG());
      fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:true,independent_of_preload_renderer_helper:true}));app.quit();return;
    }
    await until(()=>js('!!document.querySelector("#diagnostic-run")'),'entry survives missing helper');
    process.env.HARD_CRM_PYTHON='python';
    const recovered=await js('window.crm.diagnostics(true)');assert(recovered.ok);assert(recovered.data.checks.some(c=>c.code==='helper_ready'));
    await js('document.querySelector("#demo-connect").click()');await until(()=>js('!!document.querySelector("[data-person]")'),'employee');
    const state=await js('window.crm.state()'),owner=state.owner;
    const result=await js('window.crm.api("GET","registry",undefined,{})');assert(result.ok);const row=result.data.rows[0];
    const body={schema_version:1,operation_id:crypto.randomUUID(),rows:[{contract_uuid:row.contract_uuid,values:{soft_comment:'QA preserved work'},clear:[],base_revisions:{call:row.revisions.call||0}}]};
    assert((await js('window.crm.registryEdit('+JSON.stringify(body)+')')).ok);
    await js('window.crm.draft("registry-input",'+JSON.stringify({[row.contract_uuid]:{values:{soft_comment:'QA protected draft'},clear:[],base_revisions:{call:0}}})+')');
    const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
    const folder=path.join(qa,'profile/crm'),business=path.join(folder,'business-'+hash(owner)+'.bin');
    const cache=path.join(folder,'cache-'+hash(owner+'\nregistry{}')+'.bin');
    const corrupt=Buffer.from('QA damaged cache');fs.writeFileSync(cache,corrupt);blockRead=true;
    const offline=await js('window.crm.api("GET","registry",undefined,{})');assert.equal(offline.ok,false);
    assert(fs.readdirSync(folder).some(name=>name.includes('.damaged-')&&fs.readFileSync(path.join(folder,name)).equals(corrupt)));
    blockRead=false;assert((await js('window.crm.api("GET","registry",undefined,{})')).ok);
    const protectedBefore=fs.readFileSync(business);fs.writeFileSync(business,'QA unreadable business');
    const diag=await js('window.crm.diagnostics()');assert(diag.data.checks.some(c=>c.code==='protected_work_unreadable'));
    assert.equal(fs.readFileSync(business,'utf8'),'QA unreadable business');
    // Exact restoration of deliberately corrupted QA fixture only.
    fs.writeFileSync(business,protectedBefore);
    helper.kill();await until(()=>helper.exitCode!==null || helper.signalCode!==null,'helper exit');
    const restart=await js('window.crm.diagnostics(true)');assert(restart.data.checks.some(c=>c.code==='helper_ready'));
    const entries=(await js('window.crm.queue()')).data.entries;assert(entries.some(e=>e.body.operation_id===body.operation_id&&e.state==='pending'));
    assert.equal((await js('window.crm.draft("registry-input")')).data[row.contract_uuid].values.soft_comment,'QA protected draft');
    assert(!fs.readFileSync(path.join(qa,'profile/crm-diagnostics.json'),'utf8').includes('QA preserved work'));
    assert((await js('window.crm.diagnosticsReport()')).ok);
    const report=JSON.parse(fs.readFileSync(path.join(qa,'diagnostics-report.json'),'utf8'));
    assert(report.events.length<=200 && !JSON.stringify(report).includes('QA protected draft'));
    fs.writeFileSync(path.join(qa,'recovery.png'),(await win.webContents.capturePage()).toPNG());
    fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:true,startup_survives_missing_helper:true,helper_restart:true,cache_recreated_with_verified_preservation:true,unreadable_business_not_reset:true,queue_and_draft_preserved:true,no_work_contents_in_diagnostics:true},null,2));app.quit();
  }catch(error){fs.writeFileSync(path.join(qa,'failure.txt'),String(error)+'\n'+(win?await win.webContents.executeJavaScript('document.body.innerText'):''));console.error(error);app.exit(1);}
})();
