// Runs the production PWA functions in a small browser-like VM. No server, personal data,
// browser download, or production account is needed: every response is synthetic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../../server/miniapp/index.html'), 'utf8');

function functionSource(name) {
  const start = html.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert(start >= 0, `production function ${name} exists`);
  const end = html.indexOf('\n}', start);
  assert(end > start, `production function ${name} has a closing brace`);
  return html.slice(start, end + 2);
}
const functions = [
  'appToken', 'tokenMark', 'markTokenOut', 'tokenIsOut', 'ownedDraftKey', 'wipeDevice',
  'clearPendingToken', 'cleanHashFromUrl', 'dropTokenOnServer',
  'accountLogout', 'askLogout', 'logoutNow', 'renderLangs', 'absorbTokenFromUrl', 'renderPending',
  'checkPending', 'refreshData', 'boot', 'authHeaders', 'cacheCredential',
  'onAuthRejected', 'handleAuthGone', 'pendingToken', 'setPendingToken',
  'purgeProfile', 'resumeProfile',
];
const declarations = ['OUT_KEY', 'LS_KEEP'].map(name => {
  const line = html.match(new RegExp(`^const ${name}=.*;$`, 'm'));
  assert(line, `production declaration ${name} exists`);
  return line[0];
}).join('\n');

function storage() {
  const s = {};
  Object.defineProperties(s, {
    getItem: {value: k => Object.hasOwn(s, k) ? s[k] : null},
    setItem: {value: (k, v) => { s[k] = String(v); }},
    removeItem: {value: k => { delete s[k]; }},
    clear: {value:()=>{for(const k of Object.keys(s))delete s[k];}},
  });
  return s;
}
function page({stored = storage(), hash = '', responses = [], fetchResponses = []} = {}) {
  const location = {href: `https://synthetic.invalid/${hash}`, hash};
  const requests = [];
  const listeners = {window: {}, document: {}};
  const queue = responses.slice();
  const replies = fetchResponses.slice();
  const app = {innerHTML: '', style:{}, classList: {toggle() {}}};
  const langs = {innerHTML: ''};
  const nav = {classList: {contains: () => false}};
  const element = id => {
    if (id === 'app') return app;
    if (id === 'langs') return langs;
    if (id === 'pendingAccess') {
      if (!app.innerHTML.includes('id="pendingAccess"')) return null;
      return {querySelector: () => app.innerHTML.includes('onclick="checkPending()"')
        ? {disabled: false, textContent: '', get isConnected() {
            return app.innerHTML.includes('id="pendingAccess"');
          }} : null};
    }
    if (id === 'navmode') return nav;
    return null;
  };
  const calls = {list: 0, wipe: 0, bootstrap: 0, sheets: 0, toast: []};
  const STATE = {lang: 'ru', mode: 'field', tab: '', cur: null, persons: [], online: true};
  const ctx = {
    URL, URLSearchParams, Date, Math, Object, JSON,
    localStorage: stored, sessionStorage: storage(), location, STATE, LOGIN_GPS:null,
    navigator: {onLine: true}, performance: {now: () => 1},
    document: {hidden: false, addEventListener: (event, handler) => { listeners.document[event] = handler; }},
    window: {addEventListener: (event, handler) => { listeners.window[event] = handler; }},
    history: {replaceState: (_state, _title, url) => {
      location.href = new URL(url, location.href).href;
      location.hash = new URL(location.href).hash;
    }},
    $: element, L: () => ({pendingtitle: 'Ожидание', pendingwhy: 'Ждите', pendingwrong: 'Позже',
      logoutq: 'Выйти?', logoutwhy: 'Работа будет удалена', logoutlost: 'Несохранённых: {n}',
      logout: 'Выйти', cancel: 'Отмена'}),
    esc: s => String(s ?? ''),
    ic: s => s, themeGlyph: () => '', themeTitle: () => '',
    askSheet() { calls.sheets++; },
    hideBottomNav() {}, renderLangs() {}, closeAsk() {}, pendingCount: async () => 0,
    fetch: async (url, options) => { requests.push({url, options});
      if (!replies.length) throw Error('offline');
      return replies.shift(); },
    AbortController, setTimeout, clearTimeout,
    RETENTION_TIMER:null, ACCESS_VERIFIED:0, ACCESS_CREDENTIAL:'',
    enforceRetention:async()=>true, mobileProfile:()=>false,
    PURGING:null, POLICY_NOTICE:'', ensureAccess:async()=>true,
    TG: null, DRAIN: null, NAV: {session: null, buf: []}, PhotoAudit: {free() {}},
    dayStop() {}, exitNav() {}, stopAutoSync() {}, prStop() {}, closePick() {},
    stampQueueOwners: async () => {}, 
    updateOffBar() {}, dayDrainQueue() {}, stopNavWatch() {},
    IDB: {clear: async () => { calls.wipe++; }, count:async()=>0}, caches: {keys: async () => [], delete: async () => true},
    draftTimer: null, draftDirty: false,
    checkShellVersion() {}, geoProbe() {}, tgGeoInit() {},
    storedMode: () => 'field', personalizeManifest() {}, stampTokenUrl() {}, offerSwap() {},
    loadBootstrapSnapshot: async () => null, setBottomNav() {}, setMeta() {},
    cacheBootstrap() {}, clientMetric() {}, warmCards() {}, flushQueue() {},
    renderList() { calls.list++; app.innerHTML = '<main id="portfolio">Portfolio</main>'; },
    renderAppAuthGone() { app.innerHTML = '<main id="loggedOut">Logged out</main>'; },
    toast(message) { calls.toast.push(message); },
    api: async url => { assert(url.startsWith('/api/bootstrap')); calls.bootstrap++;
      assert(queue.length, `unexpected bootstrap ${url}`); const response = queue.shift();
      if (response instanceof Error) throw response;
      return response;
    },
  };
  vm.createContext(ctx);
  const shellVersion = html.match(/^const SHELL_VERSION=\d+;$/m);
  assert(shellVersion, 'production shell version declaration exists');
  vm.runInContext(`${declarations}\n${shellVersion[0]}\nlet WIPE_GEN=0, URLT=null, LAST_REFRESH=0, REDEEMED=true;\nlet START_TAB, START_DATE, START_TAB_USED=false, AUTH_USER='', draftDirty=false;\nlet SAVING=false, PHOTO_BUSY=false; const PHOTO_PREPARING=new Set(), PHOTO_INFLIGHT=new Set();\nfunction formVisible(){return false;}\nconst TRIED_TOKENS=new Set();\nconst PENDING_KEY='pending_token';\n${functions.map(functionSource).join('\n')}`, ctx, {filename: 'production-pwa.js'});
  return {ctx, app, langs, calls, location, requests, listeners, stored,
    run: code => vm.runInContext(code, ctx)};
}

(async () => {
  const old = 'synthetic-stale-token';
  const local = storage();
  local.setItem('app_token', old);
  local.setItem('draft:one', 'private pending work');
  const first = page({stored: local, hash: '#t=' + old});
  await first.run('accountLogout()'); // Both account logout and any legacy fallback fail offline.
  assert.deepEqual(first.requests.map(r => r.url), ['/api/account/logout']);
  assert.equal(first.requests[0].options.headers['X-App-Token'], old);
  assert.equal(local.getItem('app_token'), null);
  assert.equal(local.getItem('draft:one'), null, 'explicit logout wipes local drafts');
  assert(first.calls.wipe >= 3, 'device wipe clears local portfolio/cache/queue');
  assert.equal(first.run(`tokenIsOut(${JSON.stringify(old)})`), true);
  assert(!local.getItem('hb_out').includes(old), 'out-mark contains a fingerprint, not bearer');
  const restarted = page({stored: local, hash: '#t=' + old});
  restarted.run('absorbTokenFromUrl()'); // iOS installed start_url on a fresh launch.
  assert.equal(local.getItem('app_token'), null, 'stale installed URL must not resurrect logged-out token');

  const pending = {pending: true, pending_kind: 'account', employee: 'Synthetic employee'};
  const approved = {pending: false, employee: 'Synthetic employee', persons: [], roles: {field: true}, meta: {}};
  const flow = page({stored: storage(), responses: [pending, pending, approved, approved]});
  flow.stored.setItem('app_token', 'synthetic-active-token');
  await flow.run('boot()');
  assert.match(flow.app.innerHTML, /id="pendingAccess"/);
  assert.match(flow.app.innerHTML, /onclick="checkPending\(\)"/);
  await flow.run('checkPending()'); // A pending result keeps the promised retry action visible.
  assert.match(flow.app.innerHTML, /onclick="checkPending\(\)"/);
  // Register and fire the production visibility listener: a thawed installed PWA
  // must notice approval even though the last explicit check was seconds ago.
  const visibilityListener = html.match(/^document\.addEventListener\("visibilitychange",.*resumeProfile.*$/m);
  assert(visibilityListener, 'production visibility listener exists');
  flow.run(visibilityListener[0]);
  flow.listeners.document.visibilitychange();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(flow.calls.list, 1, 'approval transitions through boot to portfolio without reload');
  assert.match(flow.app.innerHTML, /id="portfolio"/);
  assert.equal(flow.calls.bootstrap, 4, 'transition revalidates with boot, not a stale cached response');

  const button = page({stored: storage(), responses: [pending, approved, approved]});
  button.stored.setItem('app_token', 'synthetic-button-token');
  await button.run('boot()');
  await button.run('checkPending()');
  assert.equal(button.calls.list, 1, 'visible check-later button transitions to approved portfolio');
  assert.equal(button.stored.getItem('app_token'), 'synthetic-button-token');
  assert.equal(button.calls.toast.length, 0);
  const telegram = page({responses: [new Error('401')]});
  telegram.ctx.TG = {initData: 'synthetic-telegram-signature'};
  await telegram.run('boot()');
  assert.match(telegram.app.innerHTML, /loggedOut/,
    'strict Telegram WebView rejection must offer login, not an offline dead end');
  telegram.stored.setItem('app_token', 'account-token');
  telegram.run('renderLangs()');
  assert.match(telegram.langs.innerHTML, /askLogout\(\)/,
    'password-authenticated Telegram WebView must expose account logout');
  assert.match(telegram.langs.innerHTML, /askMyLogins\(\)/);
  await telegram.run('askLogout()');
  assert.equal(telegram.calls.sheets, 1, 'account logout confirmation opens inside Telegram');
  const headers = telegram.run('authHeaders()');
  assert.equal(headers['X-App-Token'], 'account-token');
  assert.equal(headers['X-Init-Data'], 'synthetic-telegram-signature');
  assert.equal(telegram.run('cacheCredential()'), 'token:account-token');
  telegram.stored.setItem('draft:tg', 'private work');
  await telegram.run('accountLogout()');
  assert.deepEqual(telegram.requests.map(r => r.url), ['/api/account/logout'],
    'Telegram account logout asks the account endpoint to revoke the bearer');
  assert.equal(telegram.stored.getItem('app_token'), null);
  assert.equal(telegram.stored.getItem('draft:tg'), null);
  assert.doesNotMatch(telegram.langs.innerHTML, /askLogout\(\)/,
    'signed-only Telegram context has no account session controls');
  const signed = page({stored: storage(), fetchResponses: [{ok: true, status: 200}]});
  signed.ctx.TG = {initData: 'synthetic-telegram-signature'};
  signed.stored.setItem('app_token', 'signed-account-token');
  await signed.run('logoutNow()');
  assert.deepEqual(signed.requests.map(r => r.url), ['/api/account/logout'],
    'primary Telegram logout revokes the account session, not just local storage');
  assert.equal(signed.requests[0].options.headers['X-App-Token'], 'signed-account-token');
  assert.equal(signed.stored.getItem('app_token'), null);
  const legacy = page({stored: storage(), fetchResponses: [{ok: false, status: 401}, {ok: true, status: 200}]});
  legacy.stored.setItem('app_token', 'link-token');
  await legacy.run('logoutNow()');
  assert.deepEqual(legacy.requests.map(r => r.url), ['/api/account/logout', '/api/logout'],
    'legacy tokens fall back only after the account endpoint rejects them');
  const revoked = page({stored: storage(), responses: [new Error('401')]});
  revoked.ctx.TG = {initData: 'synthetic-telegram-signature'};
  revoked.stored.setItem('app_token', 'revoked-account-token');
  revoked.app.innerHTML = '<main id="portfolio">Private portfolio</main>';
  await revoked.run('refreshData(true)');
  await new Promise(resolve => setTimeout(resolve, 0)); // onAuthRejected is intentionally scheduled by refreshData
  assert.match(revoked.app.innerHTML, /loggedOut/,
    'open Telegram WebView must discard a rejected account portfolio');
  assert.equal(revoked.stored.getItem('app_token'), null);
  const switcher = page({hash: '#t=attacker-token&fresh=1'});
  switcher.ctx.TG = {initData: 'synthetic-telegram-signature'};
  switcher.stored.setItem('app_token', 'account-token');
  switcher.run('absorbTokenFromUrl()');
  assert.equal(switcher.stored.getItem('app_token'), 'account-token',
    'signed Telegram context must not bypass the live-account swap question');
  assert.equal(switcher.ctx.sessionStorage.getItem('pending_token'), 'attacker-token');
  const privatePage=page({hash:'#t=synthetic-private-account-token'});
  privatePage.stored.setItem('app_token','synthetic-private-account-token');privatePage.stored.setItem('account_role','agent');
  const manifest={href:'old'};privatePage.ctx.document.querySelector=()=>manifest;
  vm.runInContext(functionSource('stampTokenUrl')+'\n'+functionSource('personalizeManifest'),privatePage.ctx);
  privatePage.run('stampTokenUrl();personalizeManifest()');
  assert.equal(privatePage.location.hash,'');assert.equal(manifest.href,'/manifest.webmanifest');
  const manifestScript=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).find(s=>s.includes('MANIFEST_PERSONAL'));
  privatePage.ctx.document.createElement=()=>({});privatePage.ctx.document.head={appendChild:l=>{privatePage.ctx.__link=l;}};
  vm.runInContext(manifestScript,privatePage.ctx);
  assert.equal(privatePage.ctx.__link.href,'/manifest.webmanifest');
  assert.equal(privatePage.ctx.window.MANIFEST_TOKEN,'');
  console.log('PWA account flow: logout/identity/pending refresh; password tokens excluded from URL and install manifest PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
