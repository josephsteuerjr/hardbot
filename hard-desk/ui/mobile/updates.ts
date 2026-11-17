import {settled} from './store';

/** Updates are explicit. Other open tabs and forms retain their current shell. */
export function mobileUpdates(){
 if(!('serviceWorker' in navigator))return;
 let applying=false;
 const banner=document.createElement('aside');banner.id='mobile-update';banner.hidden=true;
 banner.setAttribute('role','status');
 const text=document.createElement('span'),button=document.createElement('button');
 button.textContent='Обновить приложение';banner.append(text,button);document.body.append(banner);
 navigator.serviceWorker.addEventListener('controllerchange',()=>{if(applying)location.reload();});
 void navigator.serviceWorker.register('./sw.js',{scope:'./',updateViaCache:'none'}).then(registration=>{
  const ready=()=>{if(registration.waiting&&navigator.serviceWorker.controller){banner.hidden=false;text.textContent='Доступно обновление.';}};
  ready();registration.addEventListener('updatefound',()=>{
   registration.installing?.addEventListener('statechange',ready);
  });
  button.onclick=async()=>{
   // Do not destroy forms or a file picker. Their original drafts remain usable.
   if([...document.querySelectorAll('dialog[open], [data-saving="1"], form input:not([type=search]), form textarea')].some(el=>el.getClientRects().length)){
    text.textContent='Сначала завершите или закройте открытую форму.';return;
   }
   button.disabled=true;
   try{
    await settled();
    if(!registration.waiting){text.textContent='Обновление уже применено. Закройте и откройте приложение.';return;}
    const channel=new MessageChannel();
    const accepted=await new Promise<boolean>((resolve,reject)=>{
     const timeout=setTimeout(()=>{channel.port1.close();reject(Error('Обновление не ответило. Повторите попытку.'));},10000);
     channel.port1.onmessage=e=>{clearTimeout(timeout);channel.port1.close();resolve(e.data?.ok===true);};
     applying=true;registration.waiting!.postMessage({type:'APPLY_UPDATE'},[channel.port2]);
    });
    if(!accepted){applying=false;text.textContent='Закройте другие окна CRM и повторите обновление.';}
   }catch(error){applying=false;text.textContent=(error as Error).message;}
   finally{button.disabled=false;}
  };
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void registration.update().then(ready).catch(()=>{});});
 }).catch(()=>{banner.hidden=false;text.textContent='Офлайн-запуск недоступен. Проверьте связь и перезапустите приложение.';button.hidden=true;});
}
