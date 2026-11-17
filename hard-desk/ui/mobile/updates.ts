import {settled} from './store';

/** Download/activate in the background; never reload an open form or another window. */
export function mobileUpdates(){
 if(!('serviceWorker' in navigator))return;
 const loaded=document.querySelector<HTMLMetaElement>('meta[name=crm-build]')?.content;
 const banner=document.createElement('aside');banner.id='mobile-update';banner.hidden=true;
 banner.setAttribute('role','status');
 const text=document.createElement('span'),button=document.createElement('button');
 button.textContent='Обновить приложение';banner.append(text,button);document.body.append(banner);
 const message=(worker:ServiceWorker,type:string)=>new Promise<any>((resolve,reject)=>{
  const channel=new MessageChannel(),timeout=setTimeout(()=>{channel.port1.close();reject(Error('Обновление не ответило. Повторите попытку.'));},10000);
  channel.port1.onmessage=e=>{clearTimeout(timeout);channel.port1.close();resolve(e.data);};
  worker.postMessage({type},[channel.port2]);
 });
 void navigator.serviceWorker.register('./sw.js',{scope:'./',updateViaCache:'none'}).then(registration=>{
  let checking=false,applying=false;
  const ready=async()=>{
   if(applying)return;
   const active=registration.active;
   if(registration.waiting){banner.hidden=false;text.textContent='Доступно обновление.';return;}
   if(!active)return;
   try{
    const result=await message(active,'GET_VERSION');
    if(applying||active!==registration.active)return;
    banner.hidden=!loaded||result.version===loaded;
    if(!banner.hidden)text.textContent='Обновление загружено.';
   }catch{/* An early installed worker has no version handshake. */}
  };
  const check=async()=>{
   if(checking||document.hidden)return;checking=true;
   try{await registration.update();await ready();}catch{/* Offline work remains available. */}
   finally{checking=false;}
  };
  void ready();registration.addEventListener('updatefound',()=>{
   registration.installing?.addEventListener('statechange',()=>void ready());
  });
  navigator.serviceWorker.addEventListener('controllerchange',()=>void ready());
  button.onclick=async()=>{
   if([...document.querySelectorAll('dialog[open], [data-saving="1"], form input:not([type=search]), form textarea')].some(el=>el.getClientRects().length)){
    text.textContent='Сначала завершите или закройте открытую форму.';return;
   }
   button.disabled=true;applying=true;
   try{
    await settled();
    const worker=registration.waiting||registration.active;
    if(!worker)throw Error('Обновление ещё загружается. Повторите попытку.');
    const accepted=await message(worker,'APPLY_UPDATE');
    if(!accepted?.ok){text.textContent='Закройте другие окна CRM и повторите обновление.';return;}
    if(worker.state!=='activated')await new Promise<void>((resolve,reject)=>{
     const timer=setTimeout(()=>{worker.removeEventListener('statechange',changed);reject(Error('Обновление ещё загружается. Повторите попытку.'));},10000);
     const changed=()=>{if(worker.state==='activated'){clearTimeout(timer);worker.removeEventListener('statechange',changed);resolve();}};
     worker.addEventListener('statechange',changed);changed();
    });
    location.reload();
   }catch(error){text.textContent=(error as Error).message;}
   finally{applying=false;button.disabled=false;}
  };
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void check();});
  window.addEventListener('online',()=>void check());window.addEventListener('focus',()=>void check());
  setInterval(()=>void check(),60000);
 }).catch(()=>{banner.hidden=false;text.textContent='Офлайн-запуск недоступен. Проверьте связь и перезапустите приложение.';button.hidden=true;});
}
