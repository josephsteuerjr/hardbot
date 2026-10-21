const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const lab=process.env.HARD_NUMERIC_QA_TEMP||fs.mkdtempSync(path.join(os.tmpdir(),'hard-norms-'));
fs.mkdirSync(lab,{recursive:true});app.setPath('userData',lab);app.disableHardwareAcceleration();app.on('window-all-closed',()=>{});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
 let w;
 try{
  w=new BrowserWindow({show:false,width:1180,height:820,webPreferences:{preload:path.join(__dirname,'test-numeric-plans-preload.cjs'),contextIsolation:false,sandbox:false,offscreen:true,backgroundThrottling:false}});
  await w.loadFile(path.join(__dirname,'../out/ui/index.html'),{query:{mode:'analyst'}});
  const js=code=>w.webContents.executeJavaScript(code);
  const until=async code=>{for(let i=0;i<150;i++){if(await js(code))return;await pause(50);}throw Error('UI timeout: '+code+' '+await js('JSON.stringify(window.__errors)'));};
  await until('!!document.querySelector("[data-go=people]")');await js('document.querySelector("[data-go=people]").click()');
  await until('!!document.querySelector("[data-tab=roles]")');await js('document.querySelector("[data-tab=roles]").click()');
  await until('!!document.querySelector("input[data-plan]")');
  assert.equal(await js('document.querySelector("[data-plan]").value'),'13');
  await js('document.querySelector("[data-plan]").value="7";document.querySelector("[data-plan]").dispatchEvent(new Event("change"))');await until('window.__norms.value===7');
  await js('document.querySelector("[data-plan]").value="1.5";document.querySelector("[data-plan]").dispatchEvent(new Event("change"))');await pause(100);
  assert.equal(await js('window.__norms.value'),7);
  await js('document.querySelector("[data-plan]").value="";document.querySelector("[data-plan]").dispatchEvent(new Event("change"))');await until('window.__norms.value===0');
  await js('document.querySelector("[data-tab=activity]").click()');await until('!!document.querySelector("#tab #tbl")');
  await js('document.querySelector("[data-tab=roles]").click()');await until('!!document.querySelector("input[data-plan]")');
  assert.equal(await js('document.querySelector("[data-plan]").value'),'');
  assert.match(await js('document.querySelector("[data-plan]").placeholder'),/10/);
  assert.deepEqual(await js('window.__errors'),[]);
  fs.writeFileSync(path.join(lab,'numeric-plans.png'),(await w.webContents.capturePage()).toPNG());
  console.log('Numeric plans UI: arbitrary 13/7, invalid fraction rejected, reset and rerender passed');
  w.destroy();app.exit(0);
 }catch(e){console.error(e);if(w)w.destroy();app.exit(1);}
});
