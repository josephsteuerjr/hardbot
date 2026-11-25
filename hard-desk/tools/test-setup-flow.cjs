// Real disposable setup/removal, real IPC and Windows encryption. QA app ids
// must be built with HARD_SETUP_QA_BUILD=1; no installed product is replaced.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),desk=path.join(root,'hard-desk'),base=process.env.HARD_SETUP_QA_ROOT?path.resolve(process.env.HARD_SETUP_QA_ROOT):path.join(root,'_local/installer-custom-2026-10-13/flow-qa');fs.mkdirSync(base,{recursive:true});const lab=fs.mkdtempSync(path.join(base,'run-'));
const sourceRoot=fs.mkdtempSync(path.join(os.homedir(),'Downloads','Софт — QA перенос '));
const source=path.join(sourceRoot,'data'),target=path.join(lab,'папка с пробелами','program'),data=path.join(lab,'new-data');fs.mkdirSync(source);fs.mkdirSync(data,{recursive:true});
const env={...process.env,HARD_SETUP_QA_DATA:data,HARD_USER_DATA:data};delete env.ELECTRON_RUN_AS_NODE;
const version=JSON.parse(fs.readFileSync(path.join(desk,'package.json'),'utf8')).version;
const outer=path.join(desk,`setup-qa/soft/soft-${version}-setup.exe`);
const frontend=path.join(desk,'setup-build/qa-soft/outer/win-unpacked/hard-setup-soft.exe');
const processes=[];let socket=null;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
function run(exe,args,extra={},timeout=120000){const r=spawnSync(exe,args,{env:{...env,...extra},windowsHide:true,encoding:'utf8',timeout});if(r.error)throw r.error;assert.equal(r.status,0,(r.stderr||'').slice(-500));return r;}
function fixture(dir,make){run(process.execPath,[path.join(desk,'tools/run-electron-test.cjs'),path.join(desk,'tools/test-install-data.cjs')],{HARD_INSTALL_QA_DATA:dir,HARD_INSTALL_QA_MAKE:make?'1':'0',HARD_INSTALL_QA_FIELD_ONLY:'1'},30000);}
function hashes(dir){return Object.fromEntries(fs.readdirSync(dir).filter(n=>n.endsWith('.key')||n.endsWith('.bin')).map(n=>[n,crypto.createHash('sha256').update(fs.readFileSync(path.join(dir,n))).digest('hex')]));}
function launch(exe,args,extra={}){const p=spawn(exe,args,{env:{...env,...extra},windowsHide:false,stdio:['ignore','inherit','inherit']});processes.push(p);return p;}
async function cdp(port){
 let page,all;
 for(let n=0;n<200;n++){try{all=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();page=all.find(x=>x.type==='page'&&x.url.endsWith('/setup/index.html'));if(page)break;}catch{}await pause(100);}
 assert.ok(page,'Wizard renderer did not start');
 const ws=new WebSocket(page.webSocketDebuggerUrl);socket=ws;await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
 let next=0;const waiting=new Map();ws.addEventListener('message',e=>{const d=JSON.parse(String(e.data));if(waiting.has(d.id)){const [resolve,reject]=waiting.get(d.id);waiting.delete(d.id);d.error?reject(new Error(d.error.message)):resolve(d.result);}});
 async function command(method,params={}){const id=++next;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{waiting.delete(id);reject(new Error('QA command timed out: '+method));},120000);waiting.set(id,[v=>{clearTimeout(timer);resolve(v);},e=>{clearTimeout(timer);reject(e);}]);ws.send(JSON.stringify({id,method,params}));});}
 const api={async evaluate(expression){const r=await command('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result?.value;},async screenshot(name){const r=await command('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(lab,name),Buffer.from(r.data,'base64'));},close(){ws.close();socket=null;}};
 for(let i=0;i<100;i++){if(await api.evaluate('typeof window.setup === "object" && document.readyState === "complete" && window.setup.state().then(s=>s.visible)'))return api;await pause(100);}
 throw new Error('Wizard did not become visible on Windows');
}
async function closed(p,expected=0){for(let i=0;i<200&&p.exitCode===null;i++)await pause(100);assert.equal(p.exitCode,expected,'Wizard did not exit cleanly');}
async function quitApp(_port,p){
 await closed(p);await pause(500);
}
(async()=>{
 const asar=require('@electron/asar');const packaged=path.join(desk,'setup-build/qa-soft/outer/win-unpacked/resources/app.asar');
 const keys=JSON.parse(asar.extractFile(packaged,'out/setup-registry.json'));
 // Python computes the independent uuid5 oracle, including the QA suffix.
 const oracle=run('python',['-c',"import uuid;print(uuid.uuid5(uuid.UUID('50e065bc-3134-11e6-9bab-38c9862bdaf3'),'ru.rimborso.hard.soft_setupqa'))"]).stdout.trim();assert.equal(keys.soft,oracle);
 fs.writeFileSync(path.join(source,'settings.json'),JSON.stringify({fieldServer:'https://synthetic.invalid',fieldDevice:{id:101,telegram_id:101,employee:'Synthetic employee'},theme:'light'}));fixture(source,true);const protectedSource=hashes(source);
 console.log('QA: real encrypted source created.');
 let wizard=launch(frontend,['/D='+target,'--remote-debugging-port=23471']);let remote=await cdp(23471);
 const state=await remote.evaluate('window.setup.state()');assert.equal(path.resolve(state.target),target);assert.equal(state.current,false);assert.ok(state.candidates.some(x=>x.source===source));
 await remote.evaluate("document.querySelector('#next').click();true");await remote.screenshot('profile-choice.png');
 const applied=await remote.evaluate('window.setup.apply('+JSON.stringify(source)+')');assert.equal(applied.ok,true,applied.message);assert.ok(fs.existsSync(path.join(target,'hard-soft.exe')));assert.deepEqual(hashes(source),protectedSource);assert.deepEqual(hashes(data),protectedSource);fixture(data,false);
 console.log('QA: selected source installed and decrypted.');
 await remote.evaluate('setTimeout(()=>window.setup.finish(false),100);true');remote.close();await closed(wizard);
 const executable=path.join(target,'hard-soft.exe');const before=hashes(data);
 const testActive=process.env.HARD_SETUP_QA_SKIP_ACTIVE!=='1';
 if(testActive){const active=launch(executable,['--tray','--inspect=23473']);await pause(500);assert.equal(active.exitCode,null);
 let blocked=spawnSync(path.join(target,'resources/process-check.exe'),[target,'hard-soft.exe'],{windowsHide:true,timeout:10000});assert.equal(blocked.status,2,String(blocked.stderr||''));
 wizard=launch(frontend,['/S','--updated','/D='+target,'--remote-debugging-port=23480']);remote=await cdp(23480);
 let blockedText='';
 for(let n=0;n<360;n++){
  if(wizard.exitCode!==null)break;
  try{blockedText=await remote.evaluate("document.querySelector('#main').innerText");}catch{blockedText='window_closed';}
  if(/Операция не завершена|не удалось завершить|Windows не завершила/.test(blockedText))break;
  await pause(500);
 }
 assert.ok(fs.existsSync(executable));assert.deepEqual(hashes(data),before);
 assert.equal(wizard.exitCode,0,'Automatic update did not finish within 180 seconds: '+blockedText);
 await quitApp(23473,active);
 }
 // Verify the actual public SFX argument pass-through, including unquoted /D.
 run(outer,['/S','/D='+target],{},180000);assert.ok(fs.existsSync(executable));assert.deepEqual(hashes(data),before);fixture(data,false);
 const registrationFile=path.join(lab,'registration.json'),helper=path.join(target,'resources/process-check.exe');
 run(helper,['registration',keys.soft,registrationFile]);
 assert.equal(path.resolve(JSON.parse(fs.readFileSync(registrationFile,'utf8')).path),target,'Registration must recover the installed path from the Windows uninstall key');
 const fallbackId=crypto.randomUUID(),fallbackKey='HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\'+fallbackId;
 run('reg.exe',['add',fallbackKey,'/v','UninstallString','/d','"'+path.join(target,'Uninstall hard-soft.exe')+'" /currentuser','/f']);
 try{
  run(helper,['registration',fallbackId,registrationFile]);
  assert.equal(path.resolve(JSON.parse(fs.readFileSync(registrationFile,'utf8')).path),target,'Registration must recover the installed path from UninstallString when InstallLocation is absent');
 }finally{run('reg.exe',['delete',fallbackKey,'/f']);}
 console.log('QA: actual SFX silent reinstall and registry fallback passed; active-app test '+(testActive?'included.':'separate.'));
 wizard=launch(executable,['--hard-uninstall','--remote-debugging-port=23472']);remote=await cdp(23472);
 const removing=await remote.evaluate('window.setup.state()');assert.equal(removing.removing,true);assert.equal(path.resolve(removing.target),target);await remote.screenshot('uninstall.png');
 const removed=await remote.evaluate('window.setup.apply("")');assert.equal(removed.ok,true,removed.message);assert.equal(fs.existsSync(executable),false);assert.deepEqual(hashes(data),before);fixture(data,false);
 await remote.evaluate('setTimeout(()=>window.setup.finish(false),100);true');remote.close();
 fs.writeFileSync(path.join(lab,'RESULTS.json'),JSON.stringify({qaProductId:true,realIpc:true,folderWithSpaces:true,selectedProfileImported:true,realDpapi:true,sourceUnchanged:true,activeAppShutdown:testActive?'passed':'separately tested',silentSfxReinstall:true,windowsUninstallRegistration:true,registryFallbackFromUninstallString:true,customUninstall:true,keysQueueDraftPreserved:true},null,2));
 fs.copyFileSync(path.join(lab,'RESULTS.json'),path.join(base,'RESULTS.json'));fs.writeFileSync(path.join(base,'LATEST.json'),JSON.stringify({lab}));
 console.log('Real custom installer QA passed: source migration, DPAPI, spaced path, SFX silent reinstall and custom removal with preserved work.');
})().catch(error=>{console.error(error.stack);process.exitCode=1;}).finally(async()=>{
 socket?.close();for(const p of processes)if(p.exitCode===null)spawnSync('taskkill.exe',['/PID',String(p.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
 const un=path.join(target,'Uninstall hard-soft.exe');if(fs.existsSync(un))spawnSync(un,['/S','/currentuser'],{env,windowsHide:true,timeout:120000});
 assert.equal(path.dirname(sourceRoot),path.join(os.homedir(),'Downloads'));assert.ok(path.basename(sourceRoot).startsWith('Софт — QA перенос '));fs.rmSync(sourceRoot,{recursive:true,force:true});
});
