// Real Electron/preload/main-process IPC, hidden windows and isolated state.
const {app,BrowserWindow}=require('electron');
const {buildSync}=require('esbuild');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const out=path.resolve(process.env.HARD_QA_ROOT || path.join(__dirname,'../../_local/architecture-review-2026-10-09'),'ipc-qa');
fs.mkdirSync(out,{recursive:true});
process.env.HARD_USER_DATA=path.join(out,'data');
process.argv.push('--tray');
BrowserWindow.prototype.show=function(){}; // never show a QA window over the user's session
app.disableHardwareAcceleration();
buildSync({entryPoints:[path.join(__dirname,'../src/main/main.ts')],outfile:path.join(out,'main.js'),
  bundle:true,platform:'node',format:'cjs',target:'node22',external:['electron','electron-updater'],
  define:{__HARD_PRODUCT__:JSON.stringify('soft')}});
fs.cpSync(path.join(__dirname,'../out/ui'),path.join(out,'ui'),{recursive:true});
fs.copyFileSync(path.join(__dirname,'../out/preload.js'),path.join(out,'preload.js'));
fs.cpSync(path.join(__dirname,'../icons'),path.join(out,'icons'),{recursive:true});
// Resolve electron-updater from the application dependency directory.
require('node:module').Module._initPaths();
require(path.join(out,'main.js'));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
app.whenReady().then(async()=>{
  try{
    await sleep(200);
    app.emit('second-instance',{},[]);
    await sleep(800);
    const w=BrowserWindow.getAllWindows()[0];assert.ok(w);
    const state=await w.webContents.executeJavaScript('window.hard.state()');
    assert.equal(state.productId,'soft');assert.equal(state.enrolled,false);
    const local=await w.webContents.executeJavaScript('window.hard.local("/state")');
    assert.equal(local.ok,false);assert.match(local.error,/аналитика/);
    const evil=new BrowserWindow({show:false,webPreferences:{preload:path.join(out,'preload.js'),contextIsolation:true,sandbox:true}});
    await evil.loadURL('about:blank');
    const rejected=await evil.webContents.executeJavaScript('window.hard.state().then(()=>false,()=>true)');
    assert.equal(rejected,true);
    const evidence={trustedMain:true,untrustedWindowRejected:true,analystBridgeUnavailableToEmployee:true};
    fs.writeFileSync(path.join(out,'RESULTS.json'),JSON.stringify(evidence,null,2));
    console.log('Real IPC QA passed: trusted main frame, rejected foreign window, employee/analyst separation.');
    app.exit(0);
  }catch(e){console.error(e);app.exit(1);}
});
