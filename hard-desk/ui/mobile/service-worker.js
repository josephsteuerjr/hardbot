/* Same network-first shell policy as the existing PWA. Business data stays in IndexedDB. */
const VERSION=__CRM_VERSION__,ASSETS=__CRM_ASSETS__,HASHES=__CRM_HASHES__;
const PREFIX='crm-mobile:'+encodeURIComponent(self.registration.scope)+':',CACHE=PREFIX+VERSION;
const assetURL=name=>new URL(name,self.registration.scope).href;
async function refreshShell(){
  // Fetch and verify the whole shell before replacing any cached entry.
  const responses=await Promise.all(ASSETS.map(async name=>{
    const url=new URL(name,self.registration.scope);url.searchParams.set('v',VERSION);
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),20000);
    try{
    const response=await fetch(url,{cache:'no-store',signal:abort.signal});
    if(!response.ok)throw Error('Не удалось загрузить обновление. Проверьте связь.');
    const digest=await crypto.subtle.digest('SHA-256',await response.clone().arrayBuffer());
    const hash=Array.from(new Uint8Array(digest),n=>n.toString(16).padStart(2,'0')).join('');
    if(hash!==HASHES[name])throw Error('Пакет обновления изменился. Повторите обновление.');
    return [name,response];
    }finally{clearTimeout(timer);}
  }));
  const cache=await caches.open(CACHE);
  await Promise.all(responses.map(([name,response])=>cache.put(assetURL(name),response)));
  const keep=new Set(ASSETS.map(assetURL));
  await Promise.all((await cache.keys()).filter(request=>!keep.has(request.url)).map(request=>cache.delete(request)));
}
self.addEventListener('install',event=>event.waitUntil(refreshShell().then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(caches.keys()
  .then(keys=>Promise.all(keys.filter(key=>key.startsWith(PREFIX)&&key!==CACHE).map(key=>caches.delete(key))))
  .then(()=>self.clients.claim())));
self.addEventListener('message',event=>{
  const type=event.data?.type,reply=value=>event.ports[0]?.postMessage(value);
  if(type==='GET_VERSION'){reply({version:VERSION});return;}
  if(!['APPLY_UPDATE','REFRESH_SHELL'].includes(type))return;
  event.waitUntil((async()=>{
    const clients=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    if(clients.filter(client=>client.url.startsWith(self.registration.scope)).length!==1){reply({ok:false,reason:'windows'});return;}
    try{
      if(type==='REFRESH_SHELL')await refreshShell();
      await self.skipWaiting();reply({ok:true,version:VERSION});
    }catch(error){reply({ok:false,reason:'network',text:error.message});}
  })());
});
self.addEventListener('fetch',event=>{
  const request=event.request,url=new URL(request.url),scope=new URL(self.registration.scope);
  if(request.method!=='GET'||url.origin!==scope.origin||!url.pathname.startsWith(scope.pathname))return;
  const name='./'+url.pathname.slice(scope.pathname.length);
  // A live version response must never come from the shell cache.
  if(name==='./BUILD.json')return;
  if(request.mode==='navigate'||url.pathname===scope.pathname||name==='./index.html'){
    event.respondWith((async()=>{
      const cache=await caches.open(CACHE),hit=await cache.match(assetURL('./index.html'));
      const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),hit?5000:30000);
      try{
        const response=await fetch(request,{cache:'no-store',signal:abort.signal});
        if(!response.ok)return hit||response;
        // Do not mix a future HTML page with the current offline bundle.
        const html=await response.clone().text();
        if(html.includes('name="crm-build" content="'+VERSION+'"'))await cache.put(assetURL('./index.html'),response.clone());
        return response;
      }catch(error){if(hit)return hit;throw error;}
      finally{clearTimeout(timer);}
    })());return;
  }
  if(!ASSETS.includes(name))return;
  if(url.searchParams.has('v')&&url.searchParams.get('v')!==VERSION){event.respondWith(fetch(request,{cache:'no-store'}));return;}
  event.respondWith(caches.open(CACHE).then(cache=>cache.match(assetURL(name))).then(hit=>hit||fetch(request,{cache:'no-store'})));
});
