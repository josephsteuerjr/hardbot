const VERSION = '__VERSION__';
const SCOPE = self.registration.scope;
const TEMPORARY_CACHE = 'crm-mobile:' + encodeURIComponent(SCOPE) + ':';

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(key => key.startsWith(TEMPORARY_CACHE)).map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});
self.addEventListener('message', event => {
  if (event.data?.type === 'GET_VERSION') event.ports[0]?.postMessage({version: VERSION});
  if (event.data?.type === 'ACTIVATE_VERIFIED_VERSION' && event.data.version === VERSION) {
    event.waitUntil(self.skipWaiting());
  }
});
self.addEventListener('fetch', event => {
  if (event.request.mode !== 'navigate' || !event.request.url.startsWith(SCOPE)) return;
  event.respondWith(fetch(event.request, {cache: 'no-store'}).catch(() => new Response(
    '<!doctype html><meta charset="utf-8"><title>Взыскание СНГ</title><p>Подключитесь к сети, чтобы открыть CRM.</p>',
    {status: 503, headers: {'Content-Type': 'text/html; charset=utf-8'}}
  )));
});
