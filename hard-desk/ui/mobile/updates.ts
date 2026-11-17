import {settled} from './store';

/** Download/activate in the background; never reload an open form or another window. */
export function mobileUpdates(){
 if(!('serviceWorker' in navigator))return;
 const loaded=document.querySelector<HTMLMetaElement>('meta[name=crm-build]')?.content;
 const banner=document.createElement('aside');banner.id='mobile-update';banner.hidden=true;
 banner.setAttribute('role','status');
 const text=document.createElement('span'),button=document.createElement('button');
 button.textContent='Обновить приложение';banner.append(text,button);document.body.append(banner);
 const message=(worker:ServiceWorker,type:string,timeoutMs=10000)=>new Promise<any>((resolve,reject)=>{
  const channel=new MessageChannel(),timeout=setTimeout(()=>{channel.port1.close();reject(Error('Обновление не ответило. Повторите попытку.'));},timeoutMs);
  channel.port1.onmessage=e=>{clearTimeout(timeout);channel.port1.close();resolve(e.data);};
  worker.postMessage({type},[channel.port2]);
 });
 void navigator.serviceWorker.register('./sw.js',{scope:'./',updateViaCache:'none'}).then(registration=>{
  let checking=false,applying=false,remoteVersion=loaded;
  const ready=async()=>{
   if(applying)return;
   const active=registration.active;
   if(registration.waiting){banner.hidden=false;text.textContent='Доступно обновление.';return;}
   if(!active)return;
   try{
    const result=await message(active,'GET_VERSION');
    if(applying||active!==registration.active)return;
    banner.hidden=!loaded||(result.version===loaded&&remoteVersion===loaded);
    if(!banner.hidden)text.textContent='Обновление загружено.';
   }catch{/* An early installed worker has no version handshake. */}
  };
  const serverVersion=async()=>{
   const response=await fetch('./BUILD.json',{cache:'no-store'});
   if(!response.ok)throw Error('Не удалось проверить обновление. Проверьте связь.');
   const data=await response.json();if(!/^[a-f0-9]{16}$/.test(data.version))throw Error('Сервер не подтвердил версию приложения.');
   remoteVersion=data.version;return data.version as string;
  };
  const check=async()=>{
   if(checking||document.hidden)return;checking=true;
   try{await serverVersion();await registration.update();await ready();}catch{/* Offline work remains available. */}
   finally{checking=false;}
  };
  void check();registration.addEventListener('updatefound',()=>{
   registration.installing?.addEventListener('statechange',()=>void ready());
  });
  navigator.serviceWorker.addEventListener('controllerchange',()=>void ready());
  const apply=async()=>{
   if(applying)return;banner.hidden=false;
   if([...document.querySelectorAll('dialog[open], [data-saving="1"], form input:not([type=search]), form textarea')].some(el=>el.getClientRects().length)){
    text.textContent='Сначала завершите или закройте открытую форму.';return;
   }
   button.disabled=true;applying=true;
   try{
    await settled();
    const target=await serverVersion();await registration.update();
    let worker=registration.waiting||registration.active;
    const deadline=Date.now()+25000;
    while(worker&&Date.now()<deadline){
     const data=await message(worker,'GET_VERSION');if(data.version===target)break;
     await new Promise(resolve=>setTimeout(resolve,150));worker=registration.waiting||registration.active;
    }
    if(!worker||(await message(worker,'GET_VERSION')).version!==target)throw Error('Обновление ещё загружается. Повторите попытку.');
    const accepted=await message(worker,'APPLY_UPDATE');
    if(!accepted?.ok){text.textContent='Закройте другие окна CRM и повторите обновление.';return;}
    if(worker.state!=='activated')await new Promise<void>((resolve,reject)=>{
     const timer=setTimeout(()=>{worker.removeEventListener('statechange',changed);reject(Error('Обновление ещё загружается. Повторите попытку.'));},10000);
     const changed=()=>{if(worker.state==='activated'){clearTimeout(timer);worker.removeEventListener('statechange',changed);resolve();}};
     worker.addEventListener('statechange',changed);changed();
    });
    text.textContent='Обновление кэша приложения…';
    const refreshed=await message(worker,'REFRESH_SHELL',45000);
    if(!refreshed?.ok)throw Error(refreshed?.reason==='windows'?'Закройте другие окна CRM и повторите обновление.':refreshed?.text||'Не удалось обновить кэш приложения.');
    const url=new URL(location.href);url.searchParams.set('_crm_refresh',target+'-'+Date.now());history.replaceState(history.state,'',url.href);location.reload();
   }catch(error){text.textContent=(error as Error).message;}
   finally{applying=false;button.disabled=false;}
  };
  button.onclick=()=>void apply();window.addEventListener('crm-force-update',()=>void apply());
  window.addEventListener('pageshow',()=>void check());
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void check();});
  window.addEventListener('online',()=>void check());window.addEventListener('focus',()=>void check());
  setInterval(()=>void check(),60000);
 }).catch(()=>{banner.hidden=false;text.textContent='Офлайн-запуск недоступен. Проверьте связь и перезапустите приложение.';button.hidden=true;});
}
