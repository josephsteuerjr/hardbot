const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(__dirname,'../../_local/review-remainders-2026-10-10/renderer');
fs.mkdirSync(out,{recursive:true});app.setPath('userData',path.join(out,'hard-money-data'));app.disableHardwareAcceleration();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
  try{
    const w=new BrowserWindow({show:false,width:1440,height:960,webPreferences:{preload:path.join(__dirname,'qa-hard-money-preload.cjs'),contextIsolation:false,sandbox:false,offscreen:true,backgroundThrottling:false}});
    const js=code=>w.webContents.executeJavaScript(code);
    await w.loadFile(path.join(__dirname,'../out/ui/index.html'),{query:{mode:'field'}});await sleep(400);
    const summary=await js('document.querySelector(".kpis").textContent');
    for(const currency of ['TJS','UZS','RUB']) assert.ok(summary.includes(currency),summary);
    assert.ok(summary.includes('150 TJS'),summary);
    assert.ok(!summary.replace(/\s/g,'').includes('200450'),summary);
    await js('document.querySelector("#f-tbl tbody tr").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');await sleep(200);
    const card=await js('document.querySelector("#panel-inner").textContent');
    for(const currency of ['TJS','UZS','RUB']) assert.ok(card.includes(currency),card);
    assert.ok(!card.replace(/\s/g,'').includes('200400'),card);
    assert.equal(await js('window.__errors.length'),0,await js('JSON.stringify(window.__errors)'));
    fs.writeFileSync(path.join(out,'hard-money-dark.png'),(await w.webContents.capturePage()).toPNG());
    fs.writeFileSync(path.join(out,'HARD-MONEY.json'),JSON.stringify({realChromium:true,threeCurrencies:true,portfolioAndCard:true,noMixedScalar:true,keyboard:true},null,2));
    console.log('Real HARD currency QA passed: TJS, UZS and RUB remain separate in portfolio and card');app.exit(0);
  }catch(error){console.error(error);app.exit(1);}
});
