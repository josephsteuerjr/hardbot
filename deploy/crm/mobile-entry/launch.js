const serviceWorkers = navigator.serviceWorker;
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function activate(path, scope) {
  const script = new URL(path, location.origin).href;
  const registration = await serviceWorkers.register(script, {scope, updateViaCache: 'none'});
  const deadline = Date.now() + 5500;
  while (Date.now() < deadline) {
    const worker = registration.active;
    if (worker?.state === 'activated' && worker.scriptURL === script) return worker;
    await delay(100);
  }
  return null;
}

async function prepareMobile() {
  const response = await fetch('/mobile/BUILD.json', {cache: 'no-store'});
  if (!response.ok) return null;
  const {version} = await response.json();
  if (typeof version !== 'string' || !/^[a-f0-9]{16}$/.test(version)) return null;
  return activate('/mobile/sw-' + version + '.js', '/mobile/');
}

async function prepareShortcut() {
  if (!serviceWorkers) return;
  const [root, mobile] = await Promise.all([
    activate('/crm-root-mobile-worker-1.js', '/'),
    prepareMobile(),
  ]);
  if (!root || !mobile) return;
  const channel = new MessageChannel();
  try {
    await Promise.race([
      new Promise(resolve => {
        channel.port1.onmessage = resolve;
        root.postMessage({type: 'PRUNE_LEGACY_SHELL'}, [channel.port2]);
      }),
      delay(800),
    ]);
  } finally {
    channel.port1.close();
  }
}

void Promise.race([prepareShortcut(), delay(6500)])
  .catch(() => {})
  .finally(() => location.replace('/mobile/'));
