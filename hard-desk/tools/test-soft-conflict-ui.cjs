const {app,BrowserWindow}=require('electron'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const out=path.resolve(process.env.HARD_QA_ROOT || path.join(__dirname,'../../_local'),'soft-conflict');fs.mkdirSync(out,{recursive:true});app.setPath('userData',fs.mkdtempSync(path.join(out,'profile-')));app.disableHardwareAcceleration();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{const w=new BrowserWindow({show:false,width:1440,height:950,webPreferences:{preload:path.join(__dirname,'test-soft-conflict-preload.cjs'),contextIsolation:false,sandbox:false,backgroundThrottling:false,offscreen:true}});w.webContents.on('console-message',(_e,...args)=>console.log('renderer',...args));const js=async s=>{try{return await w.webContents.executeJavaScript(s);}catch(e){console.error('Failed JS:',s);throw e}};
 try{
  await w.loadFile(path.join(__dirname,'../out/ui/index.html'),{query:{mode:'field'}});await sleep(500);
  assert.equal(await js('document.querySelectorAll(".queue-error").length'),1);
  assert.equal(await js('document.querySelectorAll("[data-pending-view]").length'),29);
  assert.match(await js('document.querySelector(".queue-error").textContent'),/29/);
  await js(`document.querySelector('tbody tr[data-key="SYN-A"]').click()`);await sleep(150);
  await js('window.__conflict=true;document.getElementById("c-comment").value="Сохранить этот ввод";document.getElementById("c-comment").dispatchEvent(new Event("input",{bubbles:true}));document.getElementById("c-save").click()');await sleep(150);
  for(const id of ['c-save','c-save-next','p-save'])assert.equal(await js(`document.getElementById(${JSON.stringify(id)}).disabled`),true);
  await js('document.getElementById("c-save").click();document.getElementById("c-save-next").click()');await sleep(50);
  assert.equal(await js('window.__requests.filter(x=>x.path==="/api/save").length'),1);
  assert.equal(await js('document.getElementById("c-comment").value'),'Сохранить этот ввод');
  await js('document.getElementById("c-reload").click()');await sleep(150);
  assert.equal(await js('document.getElementById("c-save").disabled'),true);
  assert.equal(await js('document.getElementById("c-comment").value'),'Сохранить этот ввод');
  await js('document.getElementById("c-use-current").click()');assert.equal(await js('document.getElementById("c-save").disabled'),false);
  assert.equal(await js('window.__originals.length'),29);
  assert.equal(await js('window.__requests.some(x=>x.path.endsWith("/remove")||x.path.endsWith("/clear"))'),false);
  assert.equal(await js('window.__errors.length'),0,await js('JSON.stringify(window.__errors)'));
  fs.writeFileSync(path.join(out,'soft-conflict.png'),(await w.webContents.capturePage()).toPNG());
  fs.writeFileSync(path.join(out,'RESULT.private.json'),JSON.stringify({ok:true,realChromiumDOM:true,originalsRetained:29,groupedRows:1,requiresRefreshAndReview:true,draftRetained:true,repeatRequests:0},null,2));
  console.log('Soft conflict UI passed: 29 originals retained, one grouped row, repeat save blocked, refresh/review required, draft retained.');w.destroy();app.exit(0);
 }catch(e){console.error(e);w.destroy();app.exit(1);}
});
