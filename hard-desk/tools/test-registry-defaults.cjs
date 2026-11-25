const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const D=path.resolve(__dirname,'..'),LAB=path.resolve(process.env.HARD_REGISTRY_DEFAULTS_QA||path.join(D,'../_local/registry-defaults-qa'));
if(!process.versions.electron){
 fs.mkdirSync(LAB,{recursive:true});
 require(path.join(D,'node_modules/esbuild')).buildSync({stdin:{contents:`import {mountRegistryTools} from './ui/analyst/registry';const view=document.getElementById('view');view.classList.add('analyst-tools');void mountRegistryTools(view,'import');`,resolveDir:D,loader:'ts'},bundle:true,format:'iife',outfile:path.join(LAB,'test.js')});
 const html=fs.readFileSync(path.join(D,'ui/crm-index.html'),'utf8').replace('src="app.js"','src="test.js"').replace('href="app.css"',`href="${require('node:url').pathToFileURL(path.join(D,'out/ui/app.css')).href}"`);
 fs.writeFileSync(path.join(LAB,'index.html'),html);
 const env={...process.env,HARD_REGISTRY_DEFAULTS_QA:LAB};delete env.ELECTRON_RUN_AS_NODE;
 const r=spawnSync(path.join(D,'node_modules/electron/dist/electron.exe'),[__filename],{env,windowsHide:true,stdio:'inherit'});
 if(r.error)throw r.error;process.exitCode=r.status||0;
}else{
 const {app,BrowserWindow}=require('electron');app.setPath('userData',path.join(LAB,'profile-'+Date.now()));app.disableHardwareAcceleration();
 app.whenReady().then(async()=>{let w;try{
  w=new BrowserWindow({show:false,width:1350,height:1000,webPreferences:{preload:path.join(__dirname,'test-registry-defaults-preload.cjs'),contextIsolation:false,sandbox:false,offscreen:true,backgroundThrottling:false}});
  const js=c=>w.webContents.executeJavaScript(c),nap=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async code=>{for(let i=0;i<120;i++){if(await js(code))return;await nap(50)}throw Error('Timeout: '+code)};
  await w.loadFile(path.join(LAB,'index.html'));await until('!!document.querySelector("[data-pick]")');
  const pick=async()=>{await js('document.querySelector("[data-pick]").click()');await until('!!document.querySelector("[data-opt=comments]") && !document.querySelector("[data-pick]").disabled');};
  const options=()=>js('Object.fromEntries([...document.querySelectorAll("[data-opt]")].map(x=>[x.dataset.opt,x.checked]))');
  await pick();assert.deepEqual(await options(),{comments:false});
  await js('document.querySelector("[data-preview]").click()');await until('window.__requests.some(r=>r.path==="/reg/preview") && !document.querySelector("[data-pick]").disabled');
  const body=await js('window.__requests.find(r=>r.path==="/reg/preview").body');
  assert.deepEqual([body.full,body.restore_manual,body.replace_comments,body.soft_edits],[true,true,false,true]);
  assert.deepEqual(body.selected,['synthetic']);
  await js('document.querySelector("[data-opt=comments]").click()');assert.equal((await options()).comments,true,'Comments remain an explicit opt-in');
  await pick();assert.deepEqual(await options(),{comments:false},'New file resets only the optional comment switch');
  await js('document.querySelector("[data-profile]").value="soft";document.querySelector("[data-profile]").dispatchEvent(new Event("change"))');
  await until('document.querySelector("[data-profile]").value==="soft"');assert.deepEqual(await options(),{comments:false});
  const progress='document.querySelector("main > .workspace .analyst-import-status")';
  await js('document.querySelector("[data-preview]").click()');
  await until('!document.querySelector("[data-apply]").disabled');
  await js('document.querySelector("[data-apply]").click()');
  await until(`${progress}?.textContent.includes("Жду подтверждение записи.") && !${progress}.querySelector("small").hidden`);
  await w.loadFile(path.join(LAB,'index.html'));
  await until(`${progress}?.textContent.includes("Жду подтверждение записи.") && document.querySelector("[data-pick]").disabled`);
  await js('window.__finishImport=true');
  await until(`${progress}?.textContent.includes("обновлено 3 / снято 1") && ${progress}.querySelector("small").hidden`);
  assert.equal(await js('window.__requests.some(r=>r.path==="/reg/apply")'),false,'Restoring status must never resubmit the import');
  await w.loadFile(path.join(LAB,'index.html'));
  await until(`${progress}?.textContent.includes("обновлено 3 / снято 1") && !document.querySelector("[data-pick]").disabled`);
  assert.deepEqual(await js('window.__errors'),[]);
  fs.writeFileSync(path.join(LAB,'defaults.png'),(await w.webContents.capturePage()).toPNG());
  fs.writeFileSync(path.join(LAB,'RESULT.json'),JSON.stringify({ok:true,defaults:await options(),previewFlags:[body.full,body.restore_manual,body.replace_comments,body.soft_edits],employeeSelectionPreserved:true},null,2));
  console.log('Registry import: defaults and comments, live phase, recovered running job, and persistent receipt PASS');app.exit(0);
 }catch(e){console.error(e);app.exit(1)}finally{w?.destroy()}});
}
