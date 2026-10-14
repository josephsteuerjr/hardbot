const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(process.env.HARD_QA_ROOT || path.join(__dirname,'../../_local/architecture-review-2026-10-09'),'soft-renderer');
fs.mkdirSync(out,{recursive:true});app.setPath('userData',path.join(out,'data'));app.disableHardwareAcceleration();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
  const w=new BrowserWindow({show:false,width:1440,height:950,webPreferences:{preload:path.join(__dirname,'qa-soft-preload.cjs'),contextIsolation:false,sandbox:false,backgroundThrottling:false,offscreen:true}});
  const js=code=>w.webContents.executeJavaScript(code);
  try{
    await w.loadFile(path.join(__dirname,'../out/ui/index.html'),{query:{mode:'field'}});await sleep(600);
    await js('(()=>{const s=document.createElement("style");s.textContent="*,*::before,*::after{transition:none!important;animation:none!important}#app{--rail-track:var(--rail-w)!important;--panel-track:0px!important}#app.with-panel{--panel-track:var(--panel-w)!important}";document.head.append(s)})()');
    assert.equal(await js('document.querySelectorAll("tbody tr").length'),2);
    await js('document.getElementById("s-partner").value="Бета";document.getElementById("s-partner").dispatchEvent(new Event("change"))');
    assert.equal(await js('document.querySelectorAll("tbody tr").length'),1);
    await js('document.querySelector("tbody tr").click()');await sleep(100);
    assert.equal(await js('document.querySelectorAll("#p-contract option").length'),2);
    await js(`document.getElementById('c-comment').value='Заметка до записи оплаты';document.getElementById('c-comment').dispatchEvent(new Event('input',{bubbles:true}));
      document.getElementById('p-amount').value='125,50';document.getElementById('p-date').value=new Date(Date.now()+5*3600000).toISOString().slice(0,10);document.getElementById('p-save').click()`);
    await sleep(150);
    assert.equal(await js('document.getElementById("c-comment").value'),'Заметка до записи оплаты');
    const payment=await js('window.__requests.find(r=>r.body?.kind==="payment").body');assert.equal(payment.rep,'SYN-A');assert.equal(payment.amount,'125.50');
    await js('window.__offline=true;document.getElementById("c-save").click()');await sleep(100);
    const call=await js('window.__requests.find(r=>r.body?.kind==="call").body');assert.equal(call.append_comment,true);assert.ok(call.op_id);assert.equal(call.values.soft_comment,'Заметка до записи оплаты');
    assert.equal(await js('document.getElementById("c-comment").value'),'');
    assert.equal(await js('document.getElementById("app").classList.contains("with-panel")'),true);
    assert.equal(await js('window.__errors.length'),0,await js('JSON.stringify(window.__errors)'));
    await w.webContents.capturePage();await sleep(150);
    fs.writeFileSync(path.join(out,'soft-card.png'),(await w.webContents.capturePage()).toPNG());
    fs.writeFileSync(path.join(out,'RESULTS.json'),JSON.stringify({partnerKeepsWholePerson:true,callDraftSurvivesPayment:true,manualPaymentBody:true,offlineCallBody:true},null,2));
    console.log('Soft renderer QA passed: partner/person grouping, preserved note across payment, selected contract, cents and offline call body.');app.exit(0);
  }catch(e){console.error(e);app.exit(1);}
});
