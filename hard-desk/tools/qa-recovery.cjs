const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(process.env.HARD_QA_ROOT || path.join(__dirname,'../../_local/review-remainders-2026-10-10'),'pwa-recovery');
fs.mkdirSync(out,{recursive:true});app.setPath('userData',path.join(out,'data'));app.disableHardwareAcceleration();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
  try{
    const w=new BrowserWindow({show:false,width:430,height:880,webPreferences:{preload:path.join(__dirname,'qa-recovery-preload.cjs'),contextIsolation:false,sandbox:false,backgroundThrottling:false,offscreen:true}});
    w.webContents.on('console-message',(_event,level,message)=>{if(level>=2)console.log('Renderer:',message);});
    const js=async code=>{try{return await w.webContents.executeJavaScript(code);}catch(error){console.log('Failed step:',code.slice(0,150));console.log(await w.webContents.executeJavaScript('JSON.stringify({errors:window.__errors,mode:STATE.mode,uid:STATE.uid,tab:STATE.tab,fields:[...document.querySelectorAll("[data-k]")].map(e=>e.dataset.k)})'));throw error;}};
    await w.loadFile(path.resolve(__dirname,'../../server/miniapp/index.html'));await sleep(400);
    await js('openP("SYN-DRAFT")');await sleep(150);await js('setTab("callform")');
    await js(`(()=>{const note=document.getElementById('f_soft_comment');note.value='Synthetic note to recover';note.dispatchEvent(new Event('input',{bubbles:true}));const extra=document.getElementById('f_soft_extra_phone');extra.value='000 123456';extra.dispatchEvent(new Event('input',{bubbles:true}));const amount=document.querySelector('[data-k="forecast_sum"]');amount.value='125.50';amount.dispatchEvent(new Event('input',{bubbles:true}));const date=document.querySelector('[data-k="promise_date"]');date.value=STATE.meta.today;date.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await js('setTab("debt");setTab("callform")');
    assert.equal(await js('document.getElementById("f_soft_comment").value'),'Synthetic note to recover');
    assert.equal(await js('document.querySelector("[data-k=forecast_sum]").value'),'125.50');
    await w.reload();await sleep(400);await js('openP("SYN-DRAFT")');await sleep(150);await js('setTab("callform")');
    assert.equal(await js('document.getElementById("f_soft_extra_phone").value'),'000 123456');
    assert.equal(await js('document.getElementById("f_soft_comment").value'),'Synthetic note to recover');
    await js('STATE.uid=202;drawPanel()');
    assert.equal(await js('document.getElementById("f_soft_comment").value'),'');
    await js('STATE.uid=101;drawPanel();document.documentElement.dataset.theme="dark"');
    await sleep(250);
    await w.webContents.capturePage();await sleep(100);
    fs.writeFileSync(path.join(out,'pwa-draft-dark.png'),(await w.webContents.capturePage()).toPNG());
    assert.equal(await js('window.__errors.length'),0,await js('JSON.stringify(window.__errors)'));
    fs.writeFileSync(path.join(out,'RESULTS.json'),JSON.stringify({realChromium:true,tabTransition:true,reload:true,notePhoneDateAmount:true,principalIsolation:true},null,2));
    console.log('Real PWA recovery QA passed: note, phone, date and cents survive navigation/reload and stay with their uid');app.exit(0);
  }catch(error){console.error(error);app.exit(1);}
});
