import {app,BrowserWindow,dialog} from 'electron';
import {writeFileSync} from 'node:fs';

/** Independent of the renderer, preload, helper, saved credentials and queue. */
export async function showStartupRecovery(code:string){
  await app.whenReady();
  const recovery=new BrowserWindow({width:860,height:560,frame:false,thickFrame:false,backgroundColor:'#f8eee0',
    webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
  recovery.setMenu(null);
  recovery.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  recovery.webContents.on('will-navigate',(event,url)=>{
    event.preventDefault();
    if(url==='hard-diagnostic:close')app.quit();
    if(url==='hard-diagnostic:retry'){app.relaunch();app.exit(0);}
    if(url==='hard-diagnostic:report')void (async()=>{
      const choice=await dialog.showSaveDialog(recovery,{title:'Диагностика запуска',defaultPath:'CRM-диагностика.json',filters:[{name:'JSON',extensions:['json']}]});
      if(!choice.canceled && choice.filePath)try{writeFileSync(choice.filePath,JSON.stringify({stage:'startup',code,version:app.getVersion(),at:new Date().toISOString()},null,2),{flag:'wx',flush:true});}catch{}
    })();
  });
  const html=`<!doctype html><html lang="ru"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><style>body{margin:0;padding:48px;background:#f8eee0;color:#1d1a17;font:17px/1.6 'Segoe UI',sans-serif}h1{font-weight:400}a{display:inline-block;margin:16px 12px 0 0;padding:10px 16px;color:#9a4a1e;border:1px solid #bcae9d;border-radius:6px;text-decoration:none}.drag{-webkit-app-region:drag;height:24px}small{color:#7d6c5a}</style><div class="drag"></div><h1>Запуск CRM не завершён</h1><p>Код: ${/^[a-z_]{1,60}$/.test(code)?code:'startup_failed'}</p><p>Перезапустите приложение. Если ошибка повторяется, сохраните отчёт диагностики и проверьте комплектность установленной программы и доступ к локальному профилю.</p><p>Сохранённая работа остаётся в исходном профиле.</p><a href="hard-diagnostic:retry">Повторить запуск</a><a href="hard-diagnostic:report">Сохранить отчёт</a><a href="hard-diagnostic:close">Закрыть</a><p><small>${app.getVersion()}</small></p></html>`;
  await recovery.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent(html));recovery.show();
}
