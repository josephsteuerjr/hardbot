const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(process.env.HARD_QA_ROOT || path.join(__dirname,'../../_local/review-remainders-2026-10-10'),'pwa-call-review');
fs.mkdirSync(out,{recursive:true});app.setPath('userData',path.join(out,'review-data'));app.disableHardwareAcceleration();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
  try{
    const w=new BrowserWindow({show:false,width:1440,height:960,webPreferences:{preload:path.join(__dirname,'qa-call-review-preload.cjs'),contextIsolation:false,sandbox:false,offscreen:true,backgroundThrottling:false}});
    const js=code=>w.webContents.executeJavaScript(code);
    await w.loadFile(path.join(__dirname,'../out/ui/index.html'),{query:{role:'security',theme:'dark'}});await sleep(500);
    await js('document.querySelector("[data-go=control]").click()');await sleep(120);
    await js('document.querySelector("[data-tab=soft]").click()');await sleep(120);
    await js('document.querySelector("#soft-control-table tbody tr").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');await sleep(200);
    assert.ok(await js('!!document.querySelector("[data-review-save]")'));
    await js(`document.querySelector('[data-review-state]').value='closed';document.querySelector('[data-review-source]').value='customer_callback';document.querySelector('[data-review-ref]').value='Synthetic independent callback';document.querySelector('[data-review-decision]').value='disputed';document.querySelector('[data-review-note]').value='Synthetic discrepancy; alternatives require investigation';document.querySelector('[data-review-save]').click()`);
    await sleep(100);
    const body=await js('window.__reviewRequests[0]');assert.equal(body.call_id,9);assert.equal(body.decision,'disputed');assert.equal(body.source,'customer_callback');assert.ok(body.op_id);
    w.webContents.setZoomFactor(1.25);await sleep(300);await w.webContents.capturePage();await sleep(100);
    fs.writeFileSync(path.join(out,'call-review-dark-125.png'),(await w.webContents.capturePage()).toPNG());
    assert.equal(await js('window.__errors.length'),0,await js('JSON.stringify(window.__errors)'));
    fs.writeFileSync(path.join(out,'CALL-REVIEW.json'),JSON.stringify({keyboard:true,scopedCallId:true,independentSource:true,discrepancyDecision:true,zoom125:true},null,2));
    console.log('Real call review QA passed: keyboard, call id, source, discrepancy decision, dark theme and 125%');app.exit(0);
  }catch(error){console.error(error);app.exit(1);}
});
