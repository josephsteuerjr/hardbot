// Real HTTPS shell/SW acceptance without credentials or production writes.
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const fixture=process.argv.includes('--fixture');
let url=new URL(process.env.HARD_QA_PWA_URL || 'https://synthetic.invalid'),python=null;
assert.equal(url.protocol,'https:');assert.equal(url.username+url.password+url.search+url.hash,'');
const out=path.join(process.env.HARD_QA_ROOT || path.resolve(__dirname,'../../_local/desktop-qa'),'pwa-live');
fs.mkdirSync(out,{recursive:true});app.setPath('userData',path.join(out,'browser-data'));app.disableHardwareAcceleration();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const wait=async(fn,label)=>{for(let n=0;n<100;n++){if(await fn())return;await sleep(100);}throw new Error(label);};
app.whenReady().then(async()=>{
 const w=new BrowserWindow({show:false,width:430,height:900,webPreferences:{offscreen:true,contextIsolation:true,sandbox:true,backgroundThrottling:false}});
 const wc=w.webContents,js=code=>wc.executeJavaScript(code);
 const errors=[];wc.on('console-message',details=>{errors.push({level:details.level,message:details.message});});
 wc.session.setPermissionRequestHandler((_wc,_permission,callback)=>callback(false));
 try{
  if(fixture){
   python=spawn('python',['-X','utf8',path.join(__dirname,'qa-server-fixture.py')],{windowsHide:true,env:{...process.env,HARD_QA_RELEASE_ROLE:'pwa-'+Date.now()},stdio:['ignore','pipe','pipe']});
   const config=await new Promise((resolve,reject)=>{let text='',error='';const timer=setTimeout(()=>reject(new Error('Fixture start timed out: '+error)),15000);python.stderr.on('data',d=>error+=d);python.stdout.on('data',d=>{text+=d;const line=text.split(/\r?\n/).find(x=>x.startsWith('RELEASE-QA '));if(line){clearTimeout(timer);resolve(JSON.parse(line.slice(11)));}});python.on('error',reject);});
   url=new URL('http://127.0.0.1:'+config.port+'/');
   await w.loadURL(url.href+'#t='+config.phone_tokens.soft);
   await wait(()=>js('document.body.innerText.includes("Синтетический клиент А")'),'Authenticated synthetic portfolio not loaded');
  }else{
  await w.loadURL(url.href);
  await wait(()=>js('!!document.getElementById("codein")'),'Link login screen did not load');
  assert.equal(await js('window.isSecureContext'),true);
  await js(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='По логину').click()`);
  assert.equal(await js('!!document.getElementById("loginuser") && !!document.getElementById("loginpass")'),true);
  fs.writeFileSync(path.join(out,'login-phone.png'),(await wc.capturePage()).toPNG());
  await js('authTab("link")');
  }
  await wait(()=>js('navigator.serviceWorker.getRegistration().then(r=>!!r?.active)'),'Service worker not active');
  if(!fixture){
   fs.writeFileSync(path.join(out,'RESULTS.json'),JSON.stringify({url:url.href,realHTTPS:true,noCertificateBypass:true,anonymousOnly:true,linkLogin:true,accountLoginForm:true,serviceWorker:true,authenticatedOfflineTest:'separate isolated fixture',console:errors},null,2));
   console.log('Live PWA HTTPS passed: both login tabs, secure context and active service worker; no real account used.');app.exit(0);return;
  }
  await wc.loadURL(url.href);await wait(()=>js('document.body.innerText.includes("Синтетический клиент А")'),'Online reload failed');
  await wait(()=>js('!!navigator.serviceWorker.controller'),'Service worker does not control page');
  await wait(()=>js('caches.keys().then(xs=>xs.includes("hard-shell-v"+SHELL_VERSION))'),'Current shell cache not populated');
  const state=await js('(async()=>({secure:window.isSecureContext,cacheNames:await caches.keys(),shell:typeof SHELL_VERSION!=="undefined"?SHELL_VERSION:null,manifest:document.querySelector("link[rel=manifest]")?.getAttribute("href")}))()');
  assert.ok(state.cacheNames.includes('hard-shell-v'+state.shell),JSON.stringify(state));
  wc.session.enableNetworkEmulation({offline:true});
  await wc.loadURL(url.href);await wait(()=>js('document.body.innerText.includes("Синтетический клиент А")'),'Offline portfolio reload failed');
  const offline=await js('({portfolio:document.body.innerText.includes("Синтетический клиент А"),secure:window.isSecureContext})');
  assert.equal(offline.secure,true);
  fs.writeFileSync(path.join(out,'offline-shell.png'),(await wc.capturePage()).toPNG());
  wc.session.disableNetworkEmulation();
  fs.writeFileSync(path.join(out,'RESULTS.json'),JSON.stringify({isolatedSyntheticServer:true,secureContext:true,serviceWorker:true,authenticatedOfflinePortfolioReload:true,state,offline,console:errors},null,2));
  console.log('PWA loopback fixture passed: real authentication, shell/SW cache and authenticated portfolio after offline cold reload.');python?.kill();app.exit(0);
 }catch(e){fs.writeFileSync(path.join(out,'ERROR.txt'),String(e.stack));try{fs.writeFileSync(path.join(out,'error.png'),(await wc.capturePage()).toPNG());}catch{}console.error(e);python?.kill();app.exit(1);}
});
