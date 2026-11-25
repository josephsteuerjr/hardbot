const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),lab=path.resolve(root,'../../ui-edit-dates');
if(!process.versions.electron){
  fs.mkdirSync(lab,{recursive:true});
  require('esbuild').buildSync({stdin:{contents:`import {editDatesHtml} from './ui/views/edit-dates';window.renderDates=editDatesHtml;document.body.innerHTML=editDatesHtml({edit_dates:[{contract_id:'QA-1',excel_visit_edited_on:'2026-10-29',app_visit_edited_at:'2026-10-29T08:15:00Z',excel_call_edited_on:null,app_call_edited_at:null},{contract_id:'QA-2',excel_visit_edited_on:null,app_visit_edited_at:null,excel_call_edited_on:'2026-10-28',app_call_edited_at:'2026-10-29T09:25:00Z'}]});document.querySelector('details').open=true;`,resolveDir:root,loader:'ts'},bundle:true,format:'iife',outfile:path.join(lab,'test.js')});
  const css=require('node:url').pathToFileURL(path.join(root,'out/ui/app.css')).href;
  fs.writeFileSync(path.join(lab,'index.html'),`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="${css}"><body style="padding:24px;max-width:900px"><script src="test.js"></script>`);
  const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
  const result=require('node:child_process').spawnSync(path.join(root,'node_modules/electron/dist/electron.exe'),[__filename],{env,windowsHide:true,stdio:'inherit'});
  if(result.error)throw result.error;process.exit(result.status??1);
}else{
  const {app,BrowserWindow}=require('electron');app.setPath('userData',path.join(lab,'profile'));app.disableHardwareAcceleration();
  app.whenReady().then(async()=>{let win;try{
    win=new BrowserWindow({show:false,width:1000,height:700,webPreferences:{offscreen:true,contextIsolation:false,sandbox:false}});
    await win.loadFile(path.join(lab,'index.html'));
    await win.webContents.executeJavaScript('document.fonts.ready');
    await new Promise(resolve=>setTimeout(resolve,450));
    const text=await win.webContents.executeJavaScript('document.body.innerText');
    assert.match(text,/29\.10\.2026/);assert.match(text,/Ручная правка Excel/);assert.match(text,/Правка в приложении/);
    assert.equal(await win.webContents.executeJavaScript('document.querySelectorAll("tbody tr").length'),4);
    assert.equal(await win.webContents.executeJavaScript('renderDates({})'), '');
    const bad=await win.webContents.executeJavaScript('renderDates({edit_dates:[{contract_id:"<script>bad</script>",excel_visit_edited_on:"bad",app_visit_edited_at:"<b>bad</b>"}]})');
    assert.ok(!bad.includes('<script>'));assert.ok(!bad.includes('<b>bad</b>'));
    fs.writeFileSync(path.join(lab,'dates.png'),(await win.webContents.capturePage()).toPNG());
    fs.writeFileSync(path.join(lab,'RESULT.json'),JSON.stringify({ok:true,contracts:2,blocks:4,blankDatesPreserved:true,scriptEscaping:true},null,2));
    console.log('Edit dates renderer PASS');app.exit(0);
  }catch(e){console.error(e);app.exit(1)}finally{win?.destroy()}});
}
