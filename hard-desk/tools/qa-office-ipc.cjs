// Real renderer → preload → main IPC → HTTP → SQLite, isolated synthetic data.
const {app,BrowserWindow,dialog}=require('electron');
const {buildSync}=require('esbuild');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {createHash}=require('node:crypto');
const profile=process.argv.includes('--soft')?'soft':'hard';
const root=path.resolve(__dirname,'../..'),lab=path.join(process.env.HARD_QA_ROOT || path.join(root,'_local/hard-office-2026-10-11'),'live-ipc-'+profile);
const fixtureDirectory=path.join(lab,'fixture-'+Date.now());
fs.mkdirSync(lab,{recursive:true});process.env.HARD_USER_DATA=path.join(lab,'data-'+Date.now());process.argv.push('--tray');
fs.mkdirSync(process.env.HARD_USER_DATA,{recursive:true});app.setPath('userData',process.env.HARD_USER_DATA);
process.env.NODE_PATH=path.join(__dirname,'../node_modules');require('node:module').Module._initPaths();
BrowserWindow.prototype.show=function(){};app.disableHardwareAcceleration();
dialog.showSaveDialog=async()=>({canceled:false,filePath:path.join(lab,'own-report.xlsx')});
const result={profile,realRenderer:true,realIPC:true,realHTTP:true,realSQLite:true};
let python=null,config=null;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const wait=async(fn,message)=>{for(let i=0;i<120;i++){if(await fn())return;await sleep(100)}throw new Error(message)};
async function startServer(port=0){
  python=spawn('python',['-X','utf8',path.join(root,'_local/hard-office-2026-10-11/server-fixture.py'),String(port)],{windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8',HARD_QA_FIXTURE_DIR:fixtureDirectory},stdio:['ignore','pipe','pipe']});
  return await new Promise((resolve,reject)=>{let output='',error='';const timer=setTimeout(()=>reject(new Error('Fixture timeout: '+error.slice(-1000))),20000);python.stderr.on('data',d=>error+=d);python.stdout.on('data',d=>{output+=d;const line=output.split(/\r?\n/).find(line=>line.startsWith('OFFICE-QA '));if(line){clearTimeout(timer);resolve(JSON.parse(line.slice(10)))}});python.once('error',reject);python.once('exit',code=>{if(!output.includes('OFFICE-QA ')){clearTimeout(timer);reject(new Error('Fixture exit '+code+': '+error.slice(-1000)))}})});
}
function loadModule(name){const code=buildSync({entryPoints:[path.join(__dirname,'../src/main/'+name+'.ts')],bundle:true,platform:'node',format:'cjs',write:false,external:['electron']}).outputFiles[0].text;const module={exports:{}};require('node:vm').runInNewContext(code,{module,exports:module.exports,require,Buffer,Response,URLSearchParams,AbortController,setTimeout,clearTimeout});return module.exports;}
buildSync({entryPoints:[path.join(__dirname,'../src/main/main.ts')],outfile:path.join(lab,'main.js'),bundle:true,platform:'node',format:'cjs',target:'node22',external:['electron','electron-updater'],define:{__HARD_PRODUCT__:JSON.stringify(profile)}});
fs.cpSync(path.join(__dirname,'../out/ui'),path.join(lab,'ui'),{recursive:true});fs.copyFileSync(path.join(__dirname,'../out/preload.js'),path.join(lab,'preload.js'));fs.cpSync(path.join(__dirname,'../icons'),path.join(lab,'icons'),{recursive:true});
require(path.join(lab,'main.js'));
app.whenReady().then(async()=>{try{
  config=await startServer();
  app.setPath('userData',process.env.HARD_USER_DATA);const store=loadModule('store');
  const {token:qaToken,...identity}=config.tokens[profile];
  store.writeSettings({fieldServer:'http://127.0.0.1:'+config.port,fieldDevice:identity,theme:'light'});
  store.writeFieldToken(qaToken);
  app.emit('second-instance',{},[]);await sleep(700);
  const w=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().startsWith('hard://app/'));assert.ok(w);
  const js=code=>w.webContents.executeJavaScript(code);
  assert.equal((await js('window.hard.state()')).productId,profile);
  await wait(()=>js('document.querySelectorAll("tbody tr[data-key]").length===2'),'Office portfolio not ready');
  const net=loadModule('net');
  let report=(await net.callField('GET','/api/workspace/report',{profile})).data;
  const before=profile==='hard'?report.stats.office:report.stats.calls;
  await js('document.querySelector("tbody tr[data-key]").click()');await sleep(180);
  const note=profile==='hard'?'o-note':'c-comment',save=profile==='hard'?'o-save':'c-save';
  await wait(()=>js(`!!document.getElementById('${save}')`),'Office card not ready');
  await js(`document.getElementById('${note}').value='Синтетическая IPC запись';document.getElementById('${note}').dispatchEvent(new Event('input',{bubbles:true}))`);
  if(profile==='hard')await js('document.getElementById("o-action").value="information";document.getElementById("o-action").dispatchEvent(new Event("change",{bubbles:true}))');
  else await js('document.getElementById("c-status").value="Нет контакта";document.getElementById("c-status").dispatchEvent(new Event("input",{bubbles:true}))');
  const locked=await js(`(()=>{document.getElementById('${save}').click();const disabled=document.getElementById('${note}').disabled;document.getElementById('panel-close').click();return {disabled,open:document.getElementById('app').classList.contains('with-panel')}})()`);assert.equal(locked.disabled,true);assert.equal(locked.open,true);result.inFlightInputProtected=true;await sleep(400);
  report=(await net.callField('GET','/api/workspace/report',{profile})).data;
  await wait(async()=>{report=(await net.callField('GET','/api/workspace/report',{profile})).data;return (profile==='hard'?report?.stats?.office:report?.stats?.calls)===before+1},'Office save was not accepted');
  assert.equal(profile==='hard'?report.stats.office:report.stats.calls,before+1);assert.equal(store.readCallQueue().entries.length,0);result.onlineSave=true;
  // Stop the actual server, persist one operation with real safeStorage,
  // restart the same SQLite instance, then drain and replay its original body.
  await js('document.getElementById("panel-close").click();document.querySelector("tbody tr[data-key=\\"LIVE-003\\"]").click()');await sleep(180);
  assert.equal(await js(`!!document.getElementById('${save}')`),true);
  python.kill();await new Promise(resolve=>python.once('exit',resolve));
  await js(`document.getElementById('${note}').value='Синтетический офлайн IPC исходник';document.getElementById('${note}').dispatchEvent(new Event('input',{bubbles:true}))`);
  if(profile==='hard')await js('document.getElementById("o-action").value="note";document.getElementById("o-action").dispatchEvent(new Event("change",{bubbles:true}))');
  else await js('document.getElementById("c-status").value="В работе";document.getElementById("c-status").dispatchEvent(new Event("input",{bubbles:true}))');
  // LIVE-003 was not cached before shutdown, so fetch/cache it before stopping
  // is required; a cold card may truthfully be unavailable offline.
  if(await js(`!!document.getElementById('${save}')`)){
    await js(`document.getElementById('${save}').click()`);await sleep(120);
  }else{
    config=await startServer(config.port);
    await js('document.querySelector("tbody tr[data-key=\\"LIVE-003\\"]").click()');await sleep(150);
    python.kill();await new Promise(resolve=>python.once('exit',resolve));
    await js(`document.getElementById('${note}').value='Синтетический офлайн IPC исходник';document.getElementById('${note}').dispatchEvent(new Event('input',{bubbles:true}));${profile==='hard'?'document.getElementById("o-action").value="note";document.getElementById("o-action").dispatchEvent(new Event("change",{bubbles:true}));':'document.getElementById("c-status").value="В работе";document.getElementById("c-status").dispatchEvent(new Event("input",{bubbles:true}));'}document.getElementById('${save}').click()`);await sleep(150);
  }
  const queued=store.readCallQueue();assert.equal(queued.entries.length,1);assert.equal(store.hasFieldWork(),true);
  const original=JSON.parse(JSON.stringify(queued.entries[0].body));
  const encrypted=fs.readFileSync(path.join(process.env.HARD_USER_DATA,'calls-pending.bin'));assert.equal(encrypted.includes(Buffer.from('Синтетический офлайн IPC исходник')),false);
  config=await startServer(config.port);await net.flushCallQueue();assert.equal(store.readCallQueue().entries.length,0);
  const replay=await net.callField('POST',profile==='hard'?'/api/office/action':'/api/save',undefined,original,{sendingQueue:true});assert.equal(replay.ok,true);
  report=(await net.callField('GET','/api/workspace/report',{profile})).data;assert.equal(profile==='hard'?report.stats.office:report.stats.calls,before+2);
  await js(`document.querySelector('[data-go="${profile}-results"]').click()`);await sleep(250);
  await js('document.querySelector("[data-export-report]").click()');await sleep(180);
  assert.equal(fs.existsSync(path.join(lab,'own-report.xlsx')),true);result.realXlsxExport=true;
  fs.writeFileSync(path.join(lab,'report-source.json'),JSON.stringify(report,null,2));
  result.dpapiQueue=true;result.offlineRestartDelivery=true;result.replayOneFact=true;result.stableOperation=true;
  const foreign=await net.callField('POST','/api/office/action',undefined,{...original,kind:'office',action:'note',values:{note:'forged'},rep:'NOT-OWNED',revision:'invalid',op_id:'synthetic-foreign'}, {sendingQueue:true});assert.equal(foreign.status,404);result.foreignWriteDenied=true;
  fs.writeFileSync(path.join(lab,'RESULTS.json'),JSON.stringify(result,null,2));console.log('Real office IPC/HTTP/SQLite acceptance passed: '+profile);app.exit(0);
}catch(error){console.error(error);fs.writeFileSync(path.join(lab,'ERROR.txt'),String(error.stack));app.exit(1)}finally{python?.kill()}});
