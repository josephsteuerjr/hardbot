const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const lab=process.env.HARD_PROMISES_QA_TEMP || fs.mkdtempSync(path.join(os.tmpdir(),'hard-promises-'));
app.setPath('userData',lab);app.disableHardwareAcceleration();app.on('window-all-closed',()=>{});
const nap=ms=>new Promise(resolve=>setTimeout(resolve,ms));
app.whenReady().then(async()=>{
 let w;
 try{
  w=new BrowserWindow({width:1180,height:820,show:false,webPreferences:{
   preload:path.join(__dirname,'test-promises-profiles-preload.cjs'),contextIsolation:false,sandbox:false,offscreen:true,backgroundThrottling:false}});
  await w.loadFile(path.join(__dirname,'../out/ui/index.html'));
  const js=code=>w.webContents.executeJavaScript(code);
  const until=async code=>{for(let i=0;i<120;i++){if(await js(code))return;await nap(50);}throw Error('UI timeout '+code+' '+await js('JSON.stringify(window.__errors)'));};
  await until('!!document.querySelector("[data-go=promises]")');
  await js('localStorage.removeItem("hard.manager-promises.profile");document.querySelector("[data-go=promises]").click()');
  await until('document.querySelector("[data-promise-mode=soft]")?.getAttribute("aria-pressed")==="true" && !!document.querySelector(".kpis")');
  assert.deepEqual(await js('[...document.querySelectorAll(".kpis b")].map(x=>x.textContent)'),['55','20','35']);
  assert.match(await js('document.querySelector("[data-promise-mode=hard]").textContent'),/\(0\)/);
  assert.match(await js('document.querySelector("[data-promise-mode=soft]").textContent'),/\(55\)/);
  assert.deepEqual(await js('[...new Set(window.__promisesQA.requests.map(x=>x.query.mode))].sort()'),['hard','soft']);
  await js('document.querySelector("[data-promise-mode=hard]").click()');
  await until('document.querySelector("[data-promise-mode=hard]")?.getAttribute("aria-pressed")==="true" && !!document.querySelector(".kpis")');
  assert.deepEqual(await js('[...document.querySelectorAll(".kpis b")].map(x=>x.textContent)'),['0','0','0']);
  await w.webContents.reload();
  await until('document.querySelector("[data-promise-mode=hard]")?.getAttribute("aria-pressed")==="true" && !!document.querySelector(".kpis")');
  await js('document.querySelector("[data-promise-mode=soft]").click()');
  await until('document.querySelector("[data-promise-mode=soft]")?.getAttribute("aria-pressed")==="true" && !!document.querySelector(".kpis")');
  await w.webContents.reload();
  await until('document.querySelector("[data-promise-mode=soft]")?.getAttribute("aria-pressed")==="true" && !!document.querySelector(".kpis")');
  assert.equal(await js('document.querySelector(".kpis b").textContent'),'55');
  await js('(()=>{const style=document.createElement("style");style.textContent="*,*::before,*::after{animation:none!important;transition:none!important}";document.head.append(style)})()');
  await nap(400);
  w.webContents.invalidate();await nap(150);
  fs.mkdirSync(path.join(lab,'evidence'),{recursive:true});
  fs.writeFileSync(path.join(lab,'evidence/promises-soft.png'),(await w.webContents.capturePage()).toPNG());
  await js('window.__promisesQA.failSoft=true;document.querySelector("[data-promise-mode=soft]").click()');
  await until('document.body.textContent.includes("Synthetic Soft unavailable")');
  assert.equal(await js('!!document.querySelector(".kpis")'),false);
  await js('document.querySelector("[data-promise-mode=hard]").click()');
  await until('!!document.querySelector(".kpis")');
  assert.equal(await js('window.__errors.length'),0,await js('JSON.stringify(window.__errors)'));
  fs.writeFileSync(path.join(lab,'RESULTS.json'),JSON.stringify({ok:true,automaticSoftWhenHardEmpty:true,counts:[55,20,35],explicitHardZero:true,rememberedProfile:true,errorNotZero:true,evidence:path.join(lab,'evidence/promises-soft.png')},null,2));
  console.log('Promise profile UI PASS; evidence: '+lab);app.exit(0);
 }catch(error){console.error(error);app.exit(1);}finally{if(w&&!w.isDestroyed())w.destroy();}
}).catch(error=>{console.error(error);app.exit(1);});
