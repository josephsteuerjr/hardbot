const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(__dirname,'../../_local/architecture-review-2026-10-09/pwa-xss');
fs.mkdirSync(out,{recursive:true});app.setPath('userData',path.join(out,'data'));app.disableHardwareAcceleration();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
  try{
    const w=new BrowserWindow({show:false,webPreferences:{offscreen:true,backgroundThrottling:false,
      preload:path.join(__dirname,'qa-pwa-xss-preload.cjs'),contextIsolation:false,sandbox:false}});
    await w.loadFile(process.argv[2]?path.resolve(process.argv[2]):path.resolve(__dirname,'../../server/miniapp/index.html'));await sleep(400);
    await w.webContents.executeJavaScript('openP("SYN-XSS")');await sleep(150);
    const birthExecuted=await w.webContents.executeJavaScript('window.auditXssExecuted');
    await w.webContents.executeJavaScript('window.auditXssExecuted=false;STATE.cur.birth_date="1980-01-01";STATE.cur.assign_date=window.auditPayload;STATE.cur.visit_date=window.auditPayload;STATE.tab="visit";renderDetail()');await sleep(150);
    const datesExecuted=await w.webContents.executeJavaScript('window.auditXssExecuted');
    const result={birthExecuted,datesExecuted};
    fs.writeFileSync(path.join(out,'RESULTS.json'),JSON.stringify(result,null,2));
    assert.equal(birthExecuted,false,'Imported birth date executed JavaScript');
    assert.equal(datesExecuted,false,'Imported date fields executed JavaScript');
    console.log('Real PWA XSS QA passed: imported birth/assignment/visit dates remain text.');app.exit(0);
  }catch(e){console.error(e);app.exit(1);}
});
