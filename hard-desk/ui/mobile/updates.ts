import {settled} from './store';

/** Opening fetches the current shell; a resumed old page updates itself when work is safe. */
export function mobileUpdates(){
 if(!('serviceWorker' in navigator))return;
 const loaded=document.querySelector<HTMLMetaElement>('meta[name=crm-build]')?.content;
 if(!loaded)return;
 let target=loaded,checking=false,applying=false,retry:ReturnType<typeof setTimeout>;
 const scope=new URL('./',document.baseURI).href;
 const workerURL=(build:string)=>new URL('./sw-'+build+'.js',scope).href;
 const options:RegistrationOptions={scope,updateViaCache:'none'};
 const busy=()=>[...document.querySelectorAll('dialog[open], [data-saving="1"], form input:not([type=search]), form textarea, textarea:focus, input:focus:not([type=search]), [contenteditable=true]:focus')].some(el=>el.getClientRects().length);
 const version=(worker:ServiceWorker)=>new Promise<string>((resolve,reject)=>{
  const channel=new MessageChannel(),timer=setTimeout(()=>{channel.port1.close();reject(Error('worker unavailable'));},3000);
  channel.port1.onmessage=e=>{clearTimeout(timer);channel.port1.close();resolve(e.data?.version);};worker.postMessage({type:'GET_VERSION'},[channel.port2]);
 });
 void (async()=>{
  const previous=await navigator.serviceWorker.getRegistration(scope);
  let registration=previous?.scope===scope?previous:await navigator.serviceWorker.register(workerURL(loaded),options);
  let migratingRoot=false;
  const migrateRoot=()=>{
    const worker=registration.active;
    if(location.pathname!=='/'||migratingRoot||worker?.state!=='activated'||worker.scriptURL!==workerURL(target))return;
    migratingRoot=true;
    void navigator.serviceWorker.register('/crm-root-mobile-worker-1.js',{scope:'/',updateViaCache:'none'})
      .then(root=>root.active?.postMessage({type:'PRUNE_LEGACY_SHELL'}))
      .catch(()=>{migratingRoot=false;});
  };
  const later=()=>{clearTimeout(retry);if(target!==loaded)retry=setTimeout(()=>void apply(),1000);};
  const activateWaiting=()=>{
    if(target!==loaded)registration.waiting?.postMessage({type:'ACTIVATE_VERIFIED_VERSION',version:target});
  };
  const apply=async()=>{
   if(target===loaded||applying||document.hidden)return;
   activateWaiting();
   if(busy()){later();return;}
   applying=true;
   try{
    const expected=target;
    let worker=registration.active;
    if(!worker||worker.state!=='activated'||await version(worker)!==expected){
      worker=registration.waiting;
      if(!worker||worker.state!=='installed'||await version(worker)!==expected)return;
    }
    await settled();
    // Recheck after asynchronous storage writes: a new form may have opened meanwhile.
    if(document.hidden||busy()||target!==expected||
       (registration.active!==worker&&registration.waiting!==worker))return;
    const url=new URL(location.href);url.searchParams.set('_crm_refresh',expected);
    history.replaceState(history.state,'',url.href);location.reload();
   }catch{/* Keep the current document and durable work if storage or the worker is unavailable. */}
   finally{applying=false;later();}
  };
  const check=async()=>{
   if(checking||document.hidden)return;checking=true;
   try{
    const response=await fetch('./BUILD.json',{cache:'no-store'});
    if(!response.ok)return;const build=await response.json();
    if(!/^[a-f0-9]{16}$/.test(build.version))return;
    target=build.version;
    const expected=workerURL(target);
    if(![registration.active,registration.installing,registration.waiting].some(w=>w?.scriptURL===expected))
      registration=await navigator.serviceWorker.register(expected,options);
    activateWaiting();migrateRoot();await apply();
   }catch{/* Offline startup keeps the verified cached shell. */}
   finally{checking=false;}
  };
  const watchInstalling=()=>registration.installing?.addEventListener('statechange',()=>{
    activateWaiting();migrateRoot();void apply();
  });
  watchInstalling();
  registration.addEventListener('updatefound',watchInstalling);
  migrateRoot();
  navigator.serviceWorker.addEventListener('controllerchange',()=>void apply());
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void check();});
  window.addEventListener('pageshow',()=>void check());window.addEventListener('online',()=>void check());window.addEventListener('focus',()=>void check());
  setInterval(()=>void check(),60000);void check();
 })().catch(()=>{/* An unavailable registration must not replace the app with an update screen. */});
}
