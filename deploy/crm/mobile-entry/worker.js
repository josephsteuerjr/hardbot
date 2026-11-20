const legacyShell = /^hard-shell-v\d+$/;
const mobileURL = new URL('/mobile/', self.location.origin).href;
const mobileCachePrefix = 'crm-mobile:' + encodeURIComponent(mobileURL) + ':';

async function mobileShellReady() {
  for (const name of await caches.keys()) {
    if (!name.startsWith(mobileCachePrefix)) continue;
    const version = name.slice(mobileCachePrefix.length);
    if (!/^[a-f0-9]{16}$/.test(version)) continue;
    const cache = await caches.open(name);
    const files = ['index.html', 'app-' + version + '.js', 'app-' + version + '.css'];
    const assets = await Promise.all(files.map(file => cache.match(mobileURL + file)));
    if (assets.every(asset => asset?.ok)) return true;
  }
  return false;
}

async function pruneLegacyCaches() {
  if (!await mobileShellReady()) return false;
  const names = (await caches.keys()).filter(name => legacyShell.test(name));
  await Promise.all(names.map(name => caches.delete(name)));
  return true;
}

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => {
  event.waitUntil(Promise.all([self.clients.claim(), pruneLegacyCaches()]));
});
self.addEventListener('message', event => {
  if (event.data?.type !== 'PRUNE_LEGACY_SHELL') return;
  event.waitUntil(pruneLegacyCaches().then(ok => event.ports[0]?.postMessage({ok})));
});

async function openShortcut(request) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 4000);
  try {
    const response = await fetch(request, {cache: 'no-store', signal: abort.signal});
    if (response.ok || response.status < 500) return response;
  } catch {
    // The old shortcut can still open the verified mobile cache offline.
  } finally {
    clearTimeout(timer);
  }
  if (await pruneLegacyCaches()) return Response.redirect(mobileURL);

  const names = (await caches.keys()).filter(name => legacyShell.test(name)).reverse();
  for (const cacheName of names) {
    const old = await caches.match(self.location.origin + '/', {cacheName});
    if (old) return old;
  }
  return Response.error();
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET' || request.mode !== 'navigate') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname !== '/' || url.searchParams.get('legacy') === '1') return;
  event.respondWith(openShortcut(request));
});
