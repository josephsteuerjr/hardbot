const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawn,spawnSync}=require('node:child_process');
const ROOT=path.resolve(__dirname,'../..'),D=path.join(ROOT,'hard-desk'),LAB=path.resolve(process.env.HARD_ENR_QA_ROOT||path.join(ROOT,'_local/enrichment-jobs-headless'));fs.mkdirSync(LAB,{recursive:true});
if(!process.versions.electron){
 fs.rmSync(path.join(LAB,'AFTER.json'),{force:true});
 const code=`import {mountRegistryTools} from './ui/analyst/registry';
 import {runJob} from './ui/analyst/lib';import {hard} from './ui/kit/api';
 async function mount(){const root=document.createElement('div');document.querySelector('#view').replaceChildren(root);await mountRegistryTools(root,'enrich')}
 window.__refresh=mount;window.__runJob=runJob;window.__testHard=hard;void mount();`;
 require(path.join(D,'node_modules/esbuild')).buildSync({stdin:{contents:code,resolveDir:D,loader:'ts'},bundle:true,format:'iife',outfile:path.join(LAB,'repro.js')});
 let html=fs.readFileSync(path.join(D,'out/ui/index.html'),'utf8').replace('src="app.js"','src="repro.js"').replace('href="app.css"',`href="${require('node:url').pathToFileURL(path.join(D,'out/ui/app.css')).href}"`);
 fs.writeFileSync(path.join(LAB,'index.html'),html);
 const py=spawn('python',['-X','utf8',path.join(__dirname,'test-enrichment-worker.py')],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8',HARD_ENR_QA_ROOT:LAB}});let buf='',errors='';
 py.stderr.on('data',b=>errors+=b);py.stdout.on('data',b=>{buf+=b;const m=/ENR-FIXTURE (\d+)/.exec(buf);if(m&&!global.started){global.started=true;const env={...process.env,HARD_ENR_FIXTURE_PORT:m[1],HARD_ENR_QA_ROOT:LAB};delete env.ELECTRON_RUN_AS_NODE;
  const result=spawnSync(path.join(D,'node_modules/electron/dist/electron.exe'),[__filename,...process.argv.slice(2),'--expect-fixed'],{env,windowsHide:true,stdio:'inherit'});if(result.error)console.error(result.error);py.kill();
  const receipt=path.join(LAB,'AFTER.json');
  const verified=fs.existsSync(receipt)&&JSON.parse(fs.readFileSync(receipt,'utf8')).results.every(x=>x.worker_state==='done'&&x.visible_result);
  process.exitCode=result.status===0&&verified?0:1;
 }});setTimeout(()=>{if(!global.started){console.error(errors);py.kill();process.exitCode=1}},15000).unref();
}else{
 const {app,BrowserWindow}=require('electron');app.setPath('userData',path.join(LAB,'profile-'+Date.now()));app.disableHardwareAcceleration();app.on('window-all-closed',()=>{});
 app.whenReady().then(async()=>{const results=[];let w;
  try{for(const mode of ['poll','refresh']){
   w=new BrowserWindow({show:false,width:1200,height:800,webPreferences:{preload:path.join(__dirname,'test-enrichment-jobs-preload.cjs'),contextIsolation:false,sandbox:false,offscreen:true,backgroundThrottling:false}});
   const js=c=>w.webContents.executeJavaScript(c),nap=ms=>new Promise(r=>setTimeout(r,ms));
   const until=async c=>{for(let i=0;i<120;i++){if(await js(c))return;await nap(50)}throw Error('Timeout '+c)};
   await w.loadFile(path.join(LAB,'index.html'));await until('!!document.querySelector("[data-scan]")');
   await js(`window.hard.local('/scenario',{mode:${JSON.stringify(mode)}})`);
   await js(`for(const [id,value] of [['e-since','03.02.2026'],['e-until','05.02.2026']]) {
     const input=document.getElementById(id);input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));
   }document.querySelector('[data-base]').click()`);
   await until('document.body.textContent.includes("next-synthetic")');
   assert.deepEqual(await js("['e-since','e-until'].map(id=>document.getElementById(id).value)"),['03.02.2026','05.02.2026']);
   for(const offset of [1,2]) {
     const expected=await js(`(()=>{const date=new Date();date.setDate(date.getDate()-${offset});return date.toLocaleDateString('ru-RU')})()`);
     await js(`document.querySelector('[data-day="${offset}"]').click()`);
     await until(`document.getElementById('e-since').value===${JSON.stringify(expected)} && document.getElementById('e-until').value===${JSON.stringify(expected)}`);
   }
   await js('document.querySelector("[data-scan]").click()');await until('!!document.querySelector("[data-check]")');
   await js('document.querySelector("[data-check]").click()');await nap(400);
   if(mode==='refresh')await js('window.__refresh()');
   await until('document.querySelector("[data-registry-progress]")?.textContent.includes("Подготавливаю список решений")');
   await until('document.body.textContent.includes("HEADLESS_ENRICH_COMPLETED")');
   assert.equal(await js('document.querySelectorAll("[data-dec]").length'),80);
   await js(`const choice=document.querySelector('[data-dec]');choice.value='server';choice.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[data-review-page="1"]').click()`);
   await until('document.body.textContent.includes("Строки 81")');
   await js(`document.querySelector('[data-review-page="-1"]').click()`);
   await until('document.body.textContent.includes("Строки 1–80")');
   assert.equal(await js('document.querySelector("[data-dec]").value'),'server');
   const worker=await js('window.hard.local("/inspect",{})');
   const observed={mode,worker_state:worker.data.jobs.at(-1).state,worker_starts:worker.data.jobs.length,
    visible_result:await js('document.body.textContent.includes("HEADLESS_ENRICH_COMPLETED")'),
    visible_error:await js('document.body.textContent.includes("Synthetic interrupted local response")'),errors:await js('window.__errors')};
   const refused=await js(`(async()=>{const original=window.__testHard.local;let reads=0;window.__testHard.local=async route=>{if(route==='/declined')return {ok:false,error:'Дождитесь завершения текущей операции.'};if(route==='/job')reads++;return original(route,{})};try{await window.__runJob('/declined',{});return {failed:false,reads}}catch(error){return {failed:true,reads,message:error.message}}finally{window.__testHard.local=original}})()`);
   assert(refused.failed && refused.reads===0 && refused.message.includes('Дождитесь завершения'),'Acknowledged admission refusal must not become a missing job');observed.known_refusal_not_polled=true;
   await nap(250);fs.writeFileSync(path.join(LAB,mode+'-before.png'),(await w.webContents.capturePage()).toPNG());results.push(observed);w.destroy();w=null;
  }
  fs.writeFileSync(path.join(LAB,process.argv.includes('--expect-fixed')?'AFTER.json':'BEFORE.json'),JSON.stringify({results},null,2));
  if(process.argv.includes('--expect-fixed'))assert(results.every(x=>x.worker_state==='done'&&x.visible_result));
  else assert(results.every(x=>x.worker_state==='done'&&!x.visible_result),'Expected actual renderer interruption to reproduce');
  console.log(JSON.stringify({reproduced:!process.argv.includes('--expect-fixed'),results},null,2));app.exit(0);
 }catch(e){console.error(e);app.exit(1)}finally{w?.destroy()}
 });
}
