const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawn,spawnSync}=require('node:child_process');
const ROOT=path.resolve(__dirname,'../..'),D=path.join(ROOT,'hard-desk'),LAB=path.resolve(process.env.HARD_ENR_QA_ROOT||path.join(ROOT,'_local/enrichment-jobs-headless'));fs.mkdirSync(LAB,{recursive:true});
if(!process.versions.electron){
 fs.rmSync(path.join(LAB,'AFTER.json'),{force:true});
 const code=`import {registry} from './ui/analyst/registry';import {mountShell,bare,setSections,go,refresh} from './ui/kit/shell';mountShell('Headless');bare(false);setSections([registry]);window.__refresh=refresh;go('registry');`;
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
   await w.loadFile(path.join(LAB,'index.html'));await until('!!document.querySelector("[data-tab=enrich]")');
   await js(`window.hard.local('/scenario',{mode:${JSON.stringify(mode)}})`);
   await js('document.querySelector("[data-tab=enrich]").click()');await until('!!document.querySelector("[data-scan]")');
   await js('document.querySelector("[data-scan]").click()');await until('!!document.querySelector("[data-check]")');
   await js('document.querySelector("[data-check]").click()');await nap(400);
   if(mode==='refresh')await js('window.__refresh()');
   await nap(4000);
   const worker=await js('window.hard.local("/inspect",{})');
   const observed={mode,worker_state:worker.data.jobs.at(-1).state,worker_starts:worker.data.jobs.length,
    visible_result:await js('document.body.textContent.includes("HEADLESS_ENRICH_COMPLETED")'),
    visible_error:await js('document.body.textContent.includes("Synthetic interrupted local response")'),errors:await js('window.__errors')};
   await nap(250);fs.writeFileSync(path.join(LAB,mode+'-before.png'),(await w.webContents.capturePage()).toPNG());results.push(observed);w.destroy();w=null;
  }
  fs.writeFileSync(path.join(LAB,process.argv.includes('--expect-fixed')?'AFTER.json':'BEFORE.json'),JSON.stringify({results},null,2));
  if(process.argv.includes('--expect-fixed'))assert(results.every(x=>x.worker_state==='done'&&x.visible_result));
  else assert(results.every(x=>x.worker_state==='done'&&!x.visible_result),'Expected actual renderer interruption to reproduce');
  console.log(JSON.stringify({reproduced:!process.argv.includes('--expect-fixed'),results},null,2));app.exit(0);
 }catch(e){console.error(e);app.exit(1)}finally{w?.destroy()}
 });
}
