const {app,BrowserWindow,ipcMain}=require('electron');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const out=process.env.HARD_SCRIPT_QA_SHOTS,profile=process.env.HARD_SCRIPT_QA_PROFILE;
if(!out||!profile)throw Error('Use test-call-script-http.py');
const preview=process.env.HARD_SCRIPT_QA_PREVIEW==='1';
const smoke=process.env.HARD_SCRIPT_QA_PREVIEW_SMOKE==='1';
app.setPath('userData',profile);app.disableHardwareAcceleration();app.on('window-all-closed',()=>{if(preview)app.quit()});
const nap=ms=>new Promise(r=>setTimeout(r,ms));
let manager,soft;
ipcMain.on('call-script-window',(event,action)=>{
 const w=BrowserWindow.fromWebContents(event.sender);if(!w||![manager,soft].includes(w))return;
 if(action==='close')w.close();else if(action==='minimize')w.minimize();else if(action==='maximize')w.isMaximized()?w.unmaximize():w.maximize();
});
app.whenReady().then(async()=>{
 const js=(w,code)=>w.webContents.executeJavaScript(code);
 const until=async(w,code)=>{for(let i=0;i<120;i++){if(await js(w,code))return;await nap(50);}throw Error('Timed out: '+code+' '+await js(w,'JSON.stringify(window.__errors)'));};
 const make=async(isManager)=>{
  const w=new BrowserWindow({show:preview&&!smoke,width:1366,height:900,frame:false,webPreferences:{partition:'script-qa-'+(isManager?'manager':'soft'),preload:path.join(__dirname,'test-call-script-preload.cjs'),additionalArguments:isManager?['--call-script-manager']:[],contextIsolation:false,sandbox:false,backgroundThrottling:false,offscreen:!preview||smoke}});
  w.setContentSize(1366,900);
  await w.loadFile(path.join(__dirname,'../out/ui/index.html'),isManager?{}:{query:{mode:'field'}});
  await js(w,'document.fonts.ready');return w;
 };
 const shot=async(w,name)=>{await nap(350);fs.writeFileSync(path.join(out,name),(await w.webContents.capturePage()).toPNG());};
 try{
  manager=await make(true);await until(manager,'!!document.querySelector("[data-go=call-script]")');
  await js(manager,'document.querySelector("[data-go=call-script]").click()');await until(manager,'!!document.querySelector("[data-script-title]")');
  if(preview){
    soft=await make(false);await until(soft,'!!document.querySelector("tbody tr[data-key]")');
    await js(soft,'document.querySelector("tbody tr[data-key]").click()');await until(soft,'!!document.querySelector("#app.with-call-script .script-name")');
    if(smoke){
      console.log('Preview opened both synthetic windows; testing their close controls');
      await js(manager,'window.hard.win("close")').catch(()=>{});
      await js(soft,'window.hard.win("close")').catch(()=>{});
    }
    return;
  }
  assert.match(await js(manager,'document.querySelector(".script-status").textContent'),/Публикации нет/);
  await shot(manager,'01-manager-editor-draft.png');
  await js(manager,'document.querySelector("[data-script-publish]").click()');
  await until(manager,'document.querySelector(".script-status").textContent.includes("Опубликован для команды")');
  await shot(manager,'02-manager-published-preview.png');
  soft=await make(false);await until(soft,'!!document.querySelector("tbody tr[data-key]")');
  await js(soft,'document.querySelector("tbody tr[data-key]").click()');await until(soft,'!!document.querySelector("#app.with-call-script .script-name")');
  const title=await js(soft,'document.querySelector("#soft-call-guide .script-name").textContent');
  await js(soft,'document.querySelector("#c-comment").value="Согласован повторный контакт в 16:30.";document.querySelector("#c-comment").dispatchEvent(new Event("input",{bubbles:true}))');
  await shot(soft,'03-soft-call-script-1366.png');
  await js(soft,'window.__scriptQa.offline=true;document.querySelector("[data-script-reload]").click()');
  await until(soft,'!!document.querySelector("[data-script-load-error]")');
  assert.equal(await js(soft,'document.querySelector("#soft-call-guide .script-name").textContent'),title);
  assert.equal(await js(soft,'document.querySelector("#c-comment").value'),'Согласован повторный контакт в 16:30.');
  await js(soft,'window.__scriptQa.offline=false;document.querySelector("[data-script-reload]").click()');
  await until(soft,'!document.querySelector("[data-script-load-error]")');
  await js(soft,'document.querySelector("#panel-body").scrollTop=480');
  await shot(soft,'05-soft-record-call.png');
  await js(soft,'document.querySelector("#panel-body").scrollTop=0');
  await js(manager,'window.__scriptQa.loseReply=true;document.querySelector("[data-script-title]").value+=" · новая редакция";document.querySelector("[data-script-title]").dispatchEvent(new Event("input",{bubbles:true}));document.querySelector("[data-script-save]").click()');
  await until(manager,'!!document.querySelector("[data-script-error]:not([hidden])")');
  assert.equal(await js(manager,'document.querySelector("[data-script-publish]").disabled'),true);
  await js(manager,'document.querySelector("[data-script-save]").click()');await until(manager,'!document.querySelector("[data-script-publish]").disabled');
  assert.equal(await js(manager,'window.__scriptQa.requests.at(-1).response.data.replayed'),true);
  await js(soft,'document.querySelector("[data-script-reload]").click()');await nap(150);
  assert.equal(await js(soft,'document.querySelector("#soft-call-guide .script-name").textContent'),title);
  await js(manager,'document.querySelector("[data-script-publish]").click()');await until(manager,'document.querySelector(".script-status").textContent.includes("версия 4")');
  assert.equal(await js(soft,'document.querySelector("#soft-call-guide .script-name").textContent'),title);
  await js(soft,'document.querySelector("[data-script-reload]").click()');await until(soft,'document.querySelector("#soft-call-guide .script-name").textContent.includes("новая редакция")');
  assert.equal(await js(soft,'document.querySelector("#c-comment").value'),'Согласован повторный контакт в 16:30.');
  await js(soft,'document.querySelector("[data-script-portfolio]").click()');assert.equal(await js(soft,'getComputedStyle(document.querySelector("#view")).display')==='none',false);
  await js(soft,'document.querySelector("[data-open-call-script]").click()');await until(soft,'document.querySelector("#app").classList.contains("with-call-script")');
  soft.setSize(1100,800);await shot(soft,'04-soft-call-script-1100.png');
  assert.equal(await js(soft,'document.documentElement.scrollWidth>innerWidth'),false);
  await js(manager,'document.querySelector("[data-script-title]").value="Несохранённый текст";document.querySelector("[data-script-title]").dispatchEvent(new Event("input",{bubbles:true}));document.querySelector("[data-script-withdraw]").click()');
  await until(manager,'document.querySelector(".script-status").textContent.includes("Публикации нет")');
  assert.equal(await js(manager,'document.querySelector("[data-script-title]").value'),'Несохранённый текст');
  await js(soft,'document.querySelector("[data-script-reload]").click()');await until(soft,'document.querySelector("#soft-call-guide").textContent.includes("ещё не опубликовал")');
  assert.equal(await js(soft,'document.querySelector("#c-comment").value'),'Согласован повторный контакт в 16:30.');
  await js(soft,'document.querySelector("#c-save").click()');await until(soft,'window.__requests.some(x=>x.body?.kind==="call")');
  assert.equal(await js(soft,'window.__requests.find(x=>x.body?.kind==="call").body.values.soft_comment'),'Согласован повторный контакт в 16:30.');
  await js(manager,'document.querySelector("[data-script-title]").value="<img src=x onerror=alert(1)>";document.querySelector("[data-script-title]").dispatchEvent(new Event("input",{bubbles:true}))');
  assert.equal(await js(manager,'document.querySelector("[data-script-preview] img")!==null'),false);
  for(const w of [manager,soft])assert.equal(await js(w,'window.__errors.length'),0,await js(w,'JSON.stringify(window.__errors)'));
  const result={stage:'verified',actualLoopbackAPI:true,draftPublishSeparation:true,lostReplyIdempotence:true,offlineKeepsCurrentGuide:true,explicitRefresh:true,unsentCallDraftPreserved:true,withdrawKeepsEditorDraft:true,callPayloadPreserved:true,escapedPreview:true,widths:[1366,1100],screenshots:fs.readdirSync(out).filter(n=>n.endsWith('.png'))};
  fs.writeFileSync(path.join(out,'UI-RESULT.private.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));app.exit(0);
 }catch(e){console.error(e);if(manager)await shot(manager,'FAILED-manager.png');if(soft)await shot(soft,'FAILED-soft.png');app.exit(1);}
}).catch(e=>{console.error(e);app.exit(1)});
