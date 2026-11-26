import {app,BrowserWindow,dialog} from 'electron';
import {writeFileSync} from 'node:fs';
import {applyCrmWindowFrame} from './crm-window-frame';

let recovery:BrowserWindow|null=null;
let showing=false;
export function currentRecoveryWindow():BrowserWindow|null {
  return recovery && !recovery.isDestroyed() && recovery.isVisible() ? recovery : null;
}

/** Independent of the renderer, preload, helper, saved credentials and queue. */
export async function showStartupRecovery(code:string){
  await app.whenReady();
  if(showing)return;
  showing=true;
  const reason=/^[a-z_]{1,60}$/.test(code)?code:'startup_failed';
  const retry=()=>{app.relaunch();app.exit(0);};
  try {
  const window=new BrowserWindow({width:860,height:560,frame:false,thickFrame:false,show:false,backgroundColor:'#f8eee0',
    webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
  recovery=window;
  window.setMenu(null);
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-navigate',(event,url)=>{
    event.preventDefault();
    if(url==='hard-diagnostic:close')app.quit();
    if(url==='hard-diagnostic:retry')retry();
    if(url==='hard-diagnostic:report')void (async()=>{
      const choice=await dialog.showSaveDialog(window,{title:'Диагностика запуска',defaultPath:'CRM-диагностика.json',filters:[{name:'JSON',extensions:['json']}]});
      if(!choice.canceled && choice.filePath)try{writeFileSync(choice.filePath,JSON.stringify({stage:'startup',code:reason,version:app.getVersion(),at:new Date().toISOString()},null,2),{flag:'wx',flush:true});}catch{}
    })();
  });
  const html=`<!doctype html><html lang="ru"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><style>body{margin:0;padding:48px;background:#f8eee0;color:#1d1a17;font:17px/1.6 'Segoe UI',sans-serif}h1{font-weight:400}a{display:inline-block;margin:16px 12px 0 0;padding:10px 16px;color:#9a4a1e;border:1px solid #bcae9d;border-radius:6px;text-decoration:none}.drag{-webkit-app-region:drag;height:24px}small{color:#7d6c5a}</style><div class="drag"></div><h1>Запуск CRM не завершён</h1><p>Код: ${reason}</p><p>Перезапустите приложение. Если ошибка повторяется, сохраните отчёт диагностики и проверьте комплектность установленной программы и доступ к локальному профилю.</p><p>Сохранённая работа остаётся в исходном профиле.</p><a href="hard-diagnostic:retry">Повторить запуск</a><a href="hard-diagnostic:report">Сохранить отчёт</a><a href="hard-diagnostic:close">Закрыть</a><p><small>${app.getVersion()}</small></p></html>`;
  await window.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent(html));
  if(window.isDestroyed())return;
  await applyCrmWindowFrame(window);
  if(!window.isDestroyed()){
    window.once('closed',()=>app.quit());
    window.show();
  }
  } catch {
    // Use a native dialog if Chromium cannot even load the recovery page.
    const result=await dialog.showMessageBox({type:'error',title:'CRM СНГ',message:'Запуск CRM не завершён',
      detail:`${reason}\nВерсия ${app.getVersion()}`,buttons:['Повторить запуск','Закрыть'],defaultId:0,cancelId:1});
    if(result.response===0)retry();else app.quit();
  }
}
