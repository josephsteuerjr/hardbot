const fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process'),assert=require('node:assert/strict');
const desk=path.resolve(__dirname,'..'),qa=process.env.HARD_ENR_QUEUE_QA_ROOT,phase=process.argv[2];
if(!process.versions.electron){
 const code="import {registry} from './ui/analyst/registry';import {mountShell,bare,setSections,go,refresh} from './ui/kit/shell';mountShell('QA');bare(false);setSections([registry]);window.__refresh=refresh;go('registry');";
 require(path.join(desk,'node_modules/esbuild')).buildSync({stdin:{contents:code,resolveDir:desk,loader:'ts'},bundle:true,format:'iife',outfile:path.join(qa,'queue-ui.js')});
 let html=fs.readFileSync(path.join(desk,'out/ui/index.html'),'utf8').replace('src="app.js"','src="queue-ui.js"').replace('href="app.css"',`href="${require('node:url').pathToFileURL(path.join(desk,'out/ui/app.css')).href}"`);fs.writeFileSync(path.join(qa,'queue-ui.html'),html);
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 const result=spawnSync(path.join(desk,'node_modules/electron/dist/electron.exe'),[__filename,phase],{env,windowsHide:true,stdio:'inherit'});process.exitCode=result.status===0?0:1;
}else{
 const {app,BrowserWindow}=require('electron');app.setPath('userData',path.join(qa,'renderer-profile-'+phase));app.disableHardwareAcceleration();
 app.whenReady().then(async()=>{let win;try{
  win=new BrowserWindow({show:false,width:1260,height:850,webPreferences:{preload:path.join(__dirname,'test-enrichment-queue-preload.cjs'),contextIsolation:false,sandbox:false,offscreen:true,backgroundThrottling:false}});
  const js=code=>win.webContents.executeJavaScript(code),nap=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  async function until(code){for(let i=0;i<200;i++){if(await js(code))return;await nap(75)}throw Error('Timeout '+code)}
  await win.loadFile(path.join(qa,'queue-ui.html'));await until('!!document.querySelector("[data-delivery-queue]")');
  await until(phase==='waiting'?'document.querySelector("[data-delivery-queue]").textContent.includes("Перенос не завершён")':'document.querySelector("[data-delivery-queue]").textContent.includes("Обогащение завершено")');
  await js('document.querySelector("[data-pick]").click()');await until('!!document.querySelector("[data-preview]")');
  if(phase==='waiting'){
   await until('document.querySelector("[data-preview]").disabled && document.querySelector("[data-apply]").disabled');
   await js('window.__refresh()');await until('document.querySelector("[data-delivery-queue]").textContent.includes("Перенос не завершён")');
  }else assert.equal(await js('document.querySelector("[data-apply]").disabled'),true,'No stale preview may become executable when queue completes');
  const text=await js('document.querySelector("[data-delivery-queue]").textContent');
  assert(text.includes(phase==='waiting'?'Осталось: 1':'Проверено книг: 2'));assert.deepEqual(await js('window.__errors'),[]);
  await nap(200);fs.writeFileSync(path.join(qa,'queue-'+phase+'.png'),(await win.webContents.capturePage()).toPNG());
  fs.writeFileSync(path.join(qa,'queue-ui-'+phase+'.json'),JSON.stringify({phase,visible:true,refresh_preserved:true,no_stale_apply:true,text},null,2));console.log('Native queue renderer verified: '+phase);app.exit(0);
 }catch(error){console.error(error);app.exit(1)}finally{win?.destroy()}});
}
