// Real renderer/preload/main IPC/HTTP/SQLite. All identities and files are synthetic.
const {app,BrowserWindow,dialog,shell,ipcMain}=require('electron');
const {buildSync}=require('esbuild');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const role=process.argv.find(a=>a.startsWith('--role='))?.split('=')[1] || 'soft';
const legacyServer=process.argv.includes('--legacy-server');
const fixtureId=role+(legacyServer?'-legacy':'')+'-'+Date.now();
const root=path.resolve(__dirname,'../..'),base=process.env.HARD_QA_ROOT || path.join(root,'_local/release-completion-2026-10-12');
const lab=path.join(base,'live-'+role+(legacyServer?'-legacy':''));fs.mkdirSync(lab,{recursive:true});
const product=role==='soft'?'soft':'manager';
process.env.HARD_USER_DATA=path.join(lab,'data-'+Date.now());process.argv.push('--tray');
fs.mkdirSync(process.env.HARD_USER_DATA,{recursive:true});app.setPath('userData',process.env.HARD_USER_DATA);
process.env.NODE_PATH=path.join(__dirname,'../node_modules');require('node:module').Module._initPaths();
app.disableHardwareAcceleration();
let dialogs=0;dialog.showSaveDialog=async()=>{dialogs++;return {canceled:false,filePath:path.join(lab,'team-report.xlsx')}};
const downloads=[];
shell.openPath=async()=>'';
const bodies=[];const originalHandle=ipcMain.handle.bind(ipcMain);
ipcMain.handle=(channel,handler)=>originalHandle(channel,async(event,...args)=>{
  if(channel==='hard:download')downloads.push(JSON.parse(JSON.stringify(args)));
  const body=args.find(value=>value?.kind==='payment') || args.find(value=>value?.body?.kind==='payment')?.body;
  if(body)bodies.push(JSON.parse(JSON.stringify(body)));
  const result=await handler(event,...args);
  if(legacyServer && channel==='hard:api' && args[1]==='/api/person' && result.data){
    const compatible=JSON.parse(JSON.stringify(result));delete compatible.data.payment_revision;return compatible;
  }
  return result;
});
let python=null,config=null;const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const wait=async(fn,message)=>{for(let i=0;i<100;i++){if(await fn())return;await sleep(100)}throw new Error(message)};
async function server(port=0){
  python=spawn('python',['-X','utf8',path.join(__dirname,'qa-server-fixture.py'),String(port)],{windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8',HARD_QA_RELEASE_ROLE:fixtureId},stdio:['ignore','pipe','pipe']});
  return await new Promise((resolve,reject)=>{let output='',error='';const timer=setTimeout(()=>reject(new Error('Server timeout: '+error.slice(-1200))),20000);
    python.stderr.on('data',d=>error+=d);python.stdout.on('data',d=>{output+=d;const line=output.split(/\r?\n/).find(v=>v.startsWith('RELEASE-QA '));if(line){clearTimeout(timer);resolve(JSON.parse(line.slice(11)))}});
    python.once('error',reject);python.once('exit',code=>{if(!output.includes('RELEASE-QA ')){clearTimeout(timer);reject(new Error('Server exit '+code+': '+error.slice(-1200)))}})});
}
function moduleOf(name){const code=buildSync({entryPoints:[path.join(__dirname,'../src/main/'+name+'.ts')],bundle:true,platform:'node',format:'cjs',write:false,external:['electron']}).outputFiles[0].text;
  const module={exports:{}};require('node:vm').runInNewContext(code,{module,exports:module.exports,require,Buffer,Response,URLSearchParams,AbortController,setTimeout,clearTimeout});return module.exports;}
buildSync({entryPoints:[path.join(__dirname,'../src/main/main.ts')],outfile:path.join(lab,'main.js'),bundle:true,platform:'node',format:'cjs',target:'node22',external:['electron','electron-updater'],define:{__HARD_PRODUCT__:JSON.stringify(product)}});
fs.cpSync(path.join(__dirname,'../out/ui'),path.join(lab,'ui'),{recursive:true});fs.copyFileSync(path.join(__dirname,'../out/preload.js'),path.join(lab,'preload.js'));fs.cpSync(path.join(__dirname,'../icons'),path.join(lab,'icons'),{recursive:true});
require(path.join(lab,'main.js'));
app.whenReady().then(async()=>{try{
  config=await server();const store=moduleOf('store');app.setPath('userData',process.env.HARD_USER_DATA);
  const {token,...identity}=config.tokens[role];
  if(role==='soft'){store.writeSettings({fieldServer:'http://127.0.0.1:'+config.port,fieldDevice:identity,theme:'light'});store.writeFieldToken(token)}
  else {store.writeSettings({server:'http://127.0.0.1:'+config.port,device:identity,theme:role==='security'?'dark':'light'});store.writeToken(token)}
  app.emit('second-instance',{},[]);await sleep(900);
  const w=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().startsWith('hard://app/'));assert.ok(w);
  w.webContents.setBackgroundThrottling(false);
  const js=code=>w.webContents.executeJavaScript(code);const net=moduleOf('net');
  await js('(()=>{const style=document.createElement("style");style.textContent="*,*::before,*::after{animation:none!important;transition:none!important}";document.head.append(style)})()');
  const capture=async name=>{console.log('Capturing',name);await sleep(300);fs.writeFileSync(path.join(lab,name),(await w.webContents.capturePage()).toPNG());console.log('Captured',name)};
  const result={role,realRenderer:true,realIPC:true,realHTTP:true,realSQLite:true};
  await js('window.__qaErrors=[];window.addEventListener("error",e=>window.__qaErrors.push(e.message));window.addEventListener("unhandledrejection",e=>window.__qaErrors.push(String(e.reason)))');
  if(role==='soft'){
    await wait(()=>js('!!document.querySelector("tbody tr[data-key]")'),'Soft portfolio missing');
    const before=(await net.callField('GET','/api/workspace/report',{profile:'soft'})).data.stats.calls;
    await js('document.querySelector("tbody tr[data-key=\\"LIVE-001\\"]").click()');await wait(()=>js('!!document.getElementById("p-save")'),'Payment form missing');
    if(legacyServer){
      assert.equal(await js('document.querySelector("#p-result option[value=\\"Оплачен\\"]").disabled'),true);
      assert.equal(await js('document.getElementById("p-save").parentElement.textContent.includes("требуется обновление сервера")'),true);
      await js('document.getElementById("p-save").closest("details").open=true;document.getElementById("p-amount").value="15,50";document.getElementById("p-save").click()');
      await wait(async()=>{const r=await net.callField('GET','/api/person',{rep:'LIVE-001',mode:'soft'},undefined,{cache:false});return r.data?.reported_payments?.length===1},'Legacy payment missing');
      assert.equal(bodies[0].result_status,'');
      assert.equal((await net.callField('GET','/api/workspace/report',{profile:'soft'})).data.stats.calls,before);
      result.legacyCapabilitiesExplicit=true;result.legacyKeepStatusWorks=true;
      fs.writeFileSync(path.join(lab,'RESULTS.json'),JSON.stringify(result,null,2));
      console.log('Legacy server payment capability acceptance passed');python?.kill();python=null;app.exit(0);return;
    }
    await js('document.getElementById("p-save").closest("details").open=true;document.getElementById("p-contract").value="LIVE-002";document.getElementById("p-amount").value="123,45";document.getElementById("p-result").value="Оплачен";document.getElementById("p-amount").dispatchEvent(new Event("input",{bubbles:true}));document.getElementById("p-result").dispatchEvent(new Event("input",{bubbles:true}));document.getElementById("p-save").click()');
    await wait(async()=>{const r=await net.callField('GET','/api/person',{rep:'LIVE-001',mode:'soft'},undefined,{cache:false});return bodies.length && r.data?.reported_payments?.some(p=>p.op===bodies[0].op_id&&p.amount===123.45&&p.result_status==='Оплачен')},'Payment not persisted');
    const original=bodies.find(b=>b.kind==='payment');assert.equal(original.result_status,'Оплачен');assert.ok(original.payment_revision);
    const replay=await net.callField('POST','/api/save',undefined,original,{sendingQueue:true});assert.equal(replay.ok,true);assert.equal(replay.data.dedup,true);
    const card=(await net.callField('GET','/api/person',{rep:'LIVE-001',mode:'soft'})).data;assert.equal(card.soft.status,'Оплачен');assert.equal(card.reported_payments.filter(p=>p.op===original.op_id).length,1);
    assert.equal((await net.callField('GET','/api/workspace/report',{profile:'soft'})).data.stats.calls,before);
    result.atomicSelectedPayment=true;result.noFakeCall=true;result.replayOneFact=true;
    await wait(()=>js('document.querySelector("tbody tr[data-key=\\"LIVE-001\\"]")?.textContent.includes("Оплачен")'),'Portfolio status not refreshed after payment');
    await wait(()=>js('document.getElementById("c-status")?.value==="Оплачен"'),'Card status not refreshed after payment');
    result.paymentStatusVisibleImmediately=true;
    await js('document.getElementById("p-save").closest("details").open=true');await capture('payment-light.png');
    await js('document.getElementById("p-result").scrollIntoView({block:"center"})');await capture('payment-result.png');
    // A second real offline operation survives encrypted storage and server restart.
    await js('document.getElementById("panel-close").click();document.querySelector("tbody tr[data-key=\\"LIVE-003\\"]").click()');await wait(()=>js('!!document.getElementById("p-save")'),'Second card missing');
    python.kill();await new Promise(r=>python.once('exit',r));
    await js('document.getElementById("p-amount").value="25,50";document.getElementById("p-result").value="Оплачен";document.getElementById("p-amount").dispatchEvent(new Event("input",{bubbles:true}));document.getElementById("p-save").click()');
    await wait(()=>store.readCallQueue().entries.length===1,'Offline payment not queued');
    const queued=store.readCallQueue().entries[0].body;assert.equal(queued.result_status,'Оплачен');assert.ok(queued.payment_revision);
    config=await server(config.port);await net.flushCallQueue();assert.equal(store.readCallQueue().entries.length,0);
    const offline=(await net.callField('GET','/api/person',{rep:'LIVE-003',mode:'soft'})).data;assert.equal(offline.soft.status,'Оплачен');assert.equal(offline.reported_payments.filter(p=>p.op===queued.op_id).length,1);
    result.dpapiOfflineRestart=true;
    fs.writeFileSync(path.join(lab,'card.json'),JSON.stringify(card,null,2));
  }else{
    await wait(()=>js('!!document.querySelector("[data-go=team-portfolio]")'),'Team portfolio menu missing');
    const rails=await js('[...document.querySelectorAll("[data-go]")].map(e=>e.dataset.go)');assert.equal(rails.includes('links'),role!=='security');assert.equal(rails.includes('control'),role==='security');
    await js('document.querySelector("[data-go=team-portfolio]").click()');await wait(()=>js('document.querySelectorAll("#team-portfolio-table tbody tr[data-key]").length===2'),'Full portfolio missing');
    const scoped=(await net.call('GET','/api/manager/portfolio',{mode:'hard'})).data;assert.equal(scoped.contracts,3);assert.equal(scoped.items.some(p=>p.ids.includes('FOREIGN-001')),false);
    await capture('crm-portfolio.png');
    await js('document.querySelector("#team-portfolio-table tbody tr[data-key]").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');await wait(()=>js('!!document.querySelector("[data-case-next]")'),'Card navigation missing');
    await js('document.querySelector("[data-case-next]").click()');await wait(()=>js('document.getElementById("head-kicker").textContent.includes("LIVE-003")'),'Next card failed');
    assert.equal(await js('document.querySelectorAll(".mcrm-case-workspace input,.mcrm-case-workspace textarea,.mcrm-case-workspace select").length'),0);
    await js('document.querySelector("[data-case-back]").click()');
    await wait(()=>js('!!document.querySelector("#team-portfolio-table tbody tr[data-key]")'),'Table return missing');
    await js('document.querySelector("#team-portfolio-table tbody tr[data-key]").click()');
    await wait(()=>js('!!document.querySelector("[data-case-tab=contracts]")'),'Contracts tab missing');
    await js('document.querySelector("[data-case-tab=contracts]").click()');
    assert.equal(await js('document.querySelector(".mcrm-case-workspace").textContent.includes("1 000,25")'),true);
    result.scopeExact=true;result.fullPortfolioWithoutActivity=true;result.readOnlyCard=true;result.keyboardNavigation=true;
    await capture('crm-card.png');
    await js('document.querySelector("[data-case-back]").click();document.querySelector("[data-go=report]").click()');await wait(()=>js('!!document.querySelector("[data-dl=stats]")'),'Report button missing');
    assert.equal(dialogs,0);await js('document.querySelector("[data-preset=today]").click()');assert.equal(dialogs,0);
    await js(`for(const key of ['from','to']){const input=document.querySelector('[data-report-'+key+']');input.value=${JSON.stringify('2026-10-12')};input.dispatchEvent(new Event('change',{bubbles:true}))}`);
    assert.equal(dialogs,0);assert.equal(downloads.length,0);
    await js('document.querySelector("[data-dl=stats]").click()');await wait(()=>dialogs===1&&fs.existsSync(path.join(lab,'team-report.xlsx')),'Report file missing');assert.equal(dialogs,1);
    assert.equal(downloads.length,1);assert.deepEqual(downloads[0][1],{preset:'custom',shift:0,from:'2026-10-12',to:'2026-10-12'});
    result.reportOnlyOnClick=true;result.customDatesPreserved=true;result.realReportXlsx=true;
    const foreign=await net.call('GET','/api/manager/portfolio',{mode:'soft',employee:'Другой сотрудник'});assert.equal(foreign.status,404);
    w.webContents.setZoomFactor(1.25);w.setSize(1366,768);await capture('report-125.png');
  }
  assert.deepEqual(await js('window.__qaErrors'),[]);
  fs.writeFileSync(path.join(lab,'RESULTS.json'),JSON.stringify(result,null,2));console.log('Real release CRM/payment acceptance passed: '+role);python?.kill();python=null;app.exit(0);
}catch(error){fs.writeFileSync(path.join(lab,'ERROR.txt'),String(error.stack));console.error(error);python?.kill();python=null;app.exit(1)}finally{python?.kill()}});
