// Production PWA cache/auth functions with synthetic identities and controllable IDB.
// No installed browser profile, production server, private data or hidden test input.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../../server/miniapp/index.html'), 'utf8');
function source(name) {
  const start = html.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert(start >= 0, `missing production function ${name}`);
  const end = html.indexOf('\n}', start);
  assert(end > start, `unterminated ${name}`);
  return html.slice(start, end + 2);
}
const names = ['appToken', 'cacheCredential', 'offlineLeaseValid', 'cacheBootstrap',
  'loadBootstrapSnapshot', 'cardKey', 'storeCard', 'loadCard', 'warmCards',
  'enqueue', 'stampQueueOwners', 'setPrincipal', 'queueBelongs', 'updateOffBar', 'wipeDevice',
  'submitSavedOperation', 'flushQueue',
  'tokenMark', 'markTokenOut', 'tokenIsOut', 'clearPendingToken', 'pendingToken',
  'setPendingToken', 'cleanHashFromUrl', 'onAuthRejected', 'purgeProfile', 'clearLocalData', 'boot', 'sharedLoginChanged',
  'ownedDraftKey', 'currentUid', 'draftKey', 'loadDraft', 'formVisible', 'saveDraft', 'flushDraft'];
function storage() {
  const values = {};
  Object.defineProperties(values, {
    getItem: {value: k => values[k] ?? null},
    setItem: {value: (k, v) => { values[k] = String(v); }},
    removeItem: {value: k => { delete values[k]; }},
    clear: {value:()=>{for(const k of Object.keys(values))delete values[k];}},
  });
  return values;
}
function idb() {
  const maps = {kv: new Map(), cards: new Map(), queue: new Map()};
  return {maps,
    get: async (s, k) => maps[s].get(k),
    all: async s => [...maps[s].values()],
    count:async s=>maps[s].size,
    put: async (s, value, k, valid) => {
      if (valid && !valid()) return false;
      maps[s].set(k === undefined ? value.op_id : k, value);
    },
    clear: async s => { maps[s].clear(); },
    replace: async (s, entries, valid) => {
      if (valid && !valid()) return false;
      maps[s].clear(); for (const [k, v] of entries) maps[s].set(k, v);
    },
  };
}
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; }
function page({db = idb(), local = storage(), responses = []} = {}) {
  let hash = ''; const queue = responses.slice();
  const app = {innerHTML: '', classList: {toggle() {}}};
  const calls = {rendered: [], sent: 0, cardWrites: 0};
  const STATE = {lang: 'ru', mode: 'field', persons: [], cur: null, uid: null, employee: '', online: true};
  const location = {href: 'https://synthetic.invalid/', reload() { calls.reload = true; },
    get hash() { return hash; }, set hash(x) { hash = x; }};
  const ctx = {URL, URLSearchParams, Date, Math, Object, JSON, Promise, Set,
    STATE, location, localStorage: local, sessionStorage: storage(), IDB: db,
    TG: null, DRAIN: null, NAV: {session: null, buf: []}, PhotoAudit: {free() {}},
    window: {MANIFEST_TOKEN: ''}, navigator: {storage: {persisted: async () => true}},
    history: {replaceState: (_s, _t, url) => { location.href = new URL(url, location.href).href; hash = new URL(location.href).hash; }},
    performance: {now: () => 1}, caches: {keys: async () => []},
    $: id => id === 'app' ? app : id === 'navmode' ? {classList: {contains: () => false}} : null,
    L: () => ({bootnone: 'No offline portfolio', bootnonewhy: 'Connect first'}), esc: x => String(x),
    dayStop() {}, stopNavWatch() {}, dayClearOn() {}, dayQueue: () => [], dayQueueSet() {},
    trackQueue: () => [], trackQueueSet() {}, exitNav() {}, stopAutoSync() {},
    prStop() {}, closePick() {}, closeAsk() {}, updateOffBar() {},
    clearTimeout() {}, draftTimer: null, draftDirty: false,
    RETENTION_TIMER:null, ACCESS_VERIFIED:0, ACCESS_CREDENTIAL:'',
    enforceRetention:async()=>true, mobileProfile:()=>false, confirmedContact:()=>0,
    ensureAccess:async()=>true,
    PURGING:null, POLICY_NOTICE:'',
    renderLangs() {}, hideBottomNav() {}, geoProbe() {}, tgGeoInit() {},
    checkShellVersion() {}, personalizeManifest() {}, stampTokenUrl() {}, offerSwap() {},
    storedMode: () => 'field', setMeta() {}, setBottomNav() {}, clientMetric() {},
    warmBar() {}, warmStatusHTML: () => '', askPersistentStorage: async () => true,
    WARM_KEY: 'warm',
    renderPending: () => { app.innerHTML = 'Waiting for approval'; },
    renderOnboarding: () => { app.innerHTML = 'Onboarding'; },
    renderPasswordChange: () => { app.innerHTML = 'Change password'; },
    renderList: () => { const output = JSON.stringify(STATE.persons);
      calls.rendered.push(output); app.innerHTML = output; },
    renderAppAuthGone() { app.innerHTML = 'Access gone'; },
    toast() {},
    renderDrain() {}, prepareCourier:async()=>{}, flushQueue: () => { calls.sent++; },
    api: async url => { assert(url.startsWith('/api/bootstrap'));
      assert(queue.length, 'unexpected bootstrap request');
      const outcome = queue.shift(); if (outcome instanceof Error) throw outcome;
      return outcome;
    },
  };
  vm.createContext(ctx);
  vm.runInContext(`const OUT_KEY='hb_out', LS_KEEP=[OUT_KEY,'hb_device_uuid'], PENDING_KEY='pending_token';
    let WIPE_GEN=0, URLT=null, REDEEMED=true, START_TAB, START_DATE, START_TAB_USED=false;
    let draftTimer=null; const FORM_TOUCHED=new Set(['visit_comment']);
    let _flushing=false;
    const TRIED_TOKENS=new Set();
    const SAVE_INFLIGHT=new Set();
    ${names.map(source).join('\n')}`, ctx, {filename: 'production-pwa.js'});
  return {ctx, app, STATE, db, local, calls, run: code => vm.runInContext(code, ctx)};
}
const A = {uid: 'A-uid', employee: 'Person A', persons: [{rep: 'A-RECORD', client: 'A-private'}], meta: {}, roles: {field: true}};
const B = {uid: 'B-uid', employee: 'Person B', persons: [{rep: 'B-RECORD', client: 'B-private'}], meta: {}, roles: {field: true}};
async function settle() { await new Promise(r => setImmediate(r)); }
(async () => {
  const db = idb(), local = storage(); local.setItem('app_token', 'A-token');
  let p = page({db, local, responses: [A]});
  await p.run('boot()'); await settle();
  assert.match(p.app.innerHTML, /A-private/);
  assert.equal(db.maps.kv.get('bootstrap')._credential, 'token:A-token');
  await p.run(`enqueue({op_id:'A-unsent',kind:'note',rep:'A-RECORD'})`);
  assert.equal(db.maps.queue.get('A-unsent')._owner.uid, 'A-uid');
  db.maps.cards.set('A-RECORD', {_cacheCredential: 'token:A-token', _cacheUid: 'A-uid', client: 'A-card'});

  p.run(`setPendingToken('B-token')`);
  p.ctx.api = async () => { throw Error('401'); };
  await p.run('boot()'); // A rejected, B staged and the installed manifest reloads.
  assert.equal(db.maps.queue.has('A-unsent'), false, 'revocation erases all mobile work under the current retention policy');
  assert.equal(db.maps.kv.has('bootstrap'), false);
  assert.equal(db.maps.cards.size, 0);
  assert.equal(p.local.getItem('app_token'), 'B-token');
  assert.equal(p.calls.reload, true);
  assert.equal(p.app.innerHTML, '', 'A portfolio DOM is removed before B reload');
  assert(!p.calls.rendered.slice(1).some(x => x.includes('A-private')));

  // Recreate a realistic B network failure after the A->B transition, with
  // persisted legacy A snapshot: the credential gate must reject it as well.
  db.maps.kv.set('bootstrap', {...A, _credential: 'token:A-token', at: Date.now()});
  local.setItem('app_token', 'B-token');
  // Historical loader before this change accepted any fresh bootstrap. Run the
  // production boot/render path with ONLY that old loader substituted: prove
  // this fixture reproduces the privacy failure rather than merely checking
  // that an empty screen appears in an unrelated error path.
  const vulnerable = page({db, local, responses: [Error('offline')]});
  vulnerable.run(`loadBootstrapSnapshot=async function(){
    const s=await IDB.get('kv','bootstrap');return offlineLeaseValid(s)?s:null;
  }`);
  await vulnerable.run('boot()');
  assert.match(vulnerable.app.innerHTML, /A-private/, 'pre-fix loader exposes A under B');
  p = page({db, local, responses: [Error('offline')]});
  await p.run('boot()');
  assert.match(p.app.innerHTML, /No offline portfolio/);
  assert(!p.app.innerHTML.includes('A-private'));
  assert.equal(await p.run(`loadCard('A-RECORD')`), null);

  // B can enter only after a successful server response, never by reusing A's cache.
  // Independently test custody of a stranded legacy entry, rather than
  // incorrectly expecting revocation to retain the previous mobile outbox.
  db.maps.queue.set('A-unsent',{op_id:'A-unsent',kind:'note',rep:'A-RECORD',_owner:{uid:'A-uid',employee:'Person A'}});
  p.ctx.api = async () => B;
  await p.run('boot()'); await settle();
  assert.match(p.app.innerHTML, /B-private/);
  assert(!p.app.innerHTML.includes('A-private'));
  assert.equal(db.maps.queue.get('A-unsent')._owner.uid, 'A-uid');
  p.STATE.uid = 'B-uid'; p.STATE.employee = 'Person B';
  assert.equal(p.run(`queueBelongs(${JSON.stringify(db.maps.queue.get('A-unsent'))})`), false);
  assert.equal(p.run(`queueBelongs({op_id:'legacy'})`), false);
  await p.run('flushQueue()');
  assert.equal(p.calls.sent,0,'production flush cannot send A work under B');
  assert.equal(db.maps.queue.get('A-unsent').failed,'server-owner');

  // Same person, rotated bearer: the rejected profile is erased even when
  // a later validated login reports the same UID. Offline access still needs
  // a server-validated snapshot for the new credential.
  local.setItem('app_token', 'A-token');
  const same = page({db: idb(), local, responses: [A]});
  same.ctx.flushQueue=()=>{}; // keep the renewal test focused on cache/queue custody
  await same.run('boot()'); await settle();
  await same.run(`enqueue({op_id:'A-rotation-work',kind:'note',rep:'A-RECORD'})`);
  same.run(`setPendingToken('renewed-A')`);
  same.ctx.api = async () => { throw Error('401'); };
  // Exercise transition itself without forcing second bootstrap to fail.
  same.ctx.window.MANIFEST_TOKEN = 'renewed-A';
  same.ctx.boot = async () => {};
  await same.run('onAuthRejected()');
  assert.equal(local.getItem('app_token'), 'renewed-A');
  assert.equal(same.db.maps.kv.size, 0);
  same.ctx.boot = async () => {};
  const renewed = page({db: same.db, local, responses: [Error('offline'), A]});
  renewed.ctx.flushQueue=()=>{};
  await renewed.run('boot()'); assert.match(renewed.app.innerHTML, /No offline portfolio/);
  await renewed.run('boot()'); await settle();
  assert.equal(renewed.db.maps.queue.has('A-rotation-work'),false,
    'a new credential must not resurrect work erased after revocation');
  renewed.ctx.api = async () => { throw Error('offline'); };
  await renewed.run('boot()'); assert.match(renewed.app.innerHTML, /A-private/);

  // Delay the READ preceding an old A cache write until after A's explicit wipe.
  const raced = page({db: idb(), local: storage()}); raced.local.setItem('app_token', 'A-token');
  const gate = deferred(); const origGet = raced.db.get;
  raced.db.get = (store, key) => store === 'kv' && key === 'bootstrap' ? gate.promise : origGet(store, key);
  const oldWrite = raced.run(`cacheBootstrap(${JSON.stringify(A)})`);
  await settle();
  raced.db.get = origGet;
  await raced.run('wipeDevice({keepQueue:true})');
  gate.resolve(null); await oldWrite;
  assert.equal(raced.db.maps.kv.size, 0, 'late bootstrap writer cannot recreate A after wipe');

  // Delay warmCards between reading old cards and replacing them after wipe.
  raced.STATE.uid = 'A-uid'; raced.STATE.employee = 'Person A';
  raced.local.setItem('app_token', 'A-token');
  raced.ctx.api = async () => ({cards: {'A-RECORD': {rep: 'A-RECORD', client: 'A-card'}}});
  const cardsGate = deferred(), origAll = raced.db.all;
  raced.db.all = store => store === 'cards' ? cardsGate.promise : origAll(store);
  const oldWarm = raced.run('warmCards(false)'); await settle();
  raced.db.all = origAll; await raced.run('wipeDevice({keepQueue:true})');
  cardsGate.resolve([]); await oldWarm;
  assert.equal(raced.db.maps.cards.size, 0, 'late warm batch cannot recreate A after wipe');

  // A second race: the snapshot read and generation check finish BEFORE the
  // wipe, but opening the eventual IDB write transaction is delayed until
  // AFTER it. Guard must run at transaction creation, not just before await.
  const pendingPut = deferred(), queued = deferred(), delayed = page({db:idb(),local:storage()});
  delayed.local.setItem('app_token', 'A-token');
  const realPut = delayed.db.put;
  delayed.db.put = async (store, value, key, valid) => {
    if (store === 'kv') { queued.resolve(); await pendingPut.promise; }
    return realPut(store, value, key, valid);
  };
  const pendingSnapshot = delayed.run(`cacheBootstrap(${JSON.stringify(A)})`);
  await queued.promise;
  await delayed.run('wipeDevice({keepQueue:true})');
  pendingPut.resolve(); await pendingSnapshot;
  assert.equal(delayed.db.maps.kv.size, 0, 'delayed IDB transaction cannot write A after wipe');

  const queuedWrite=deferred(), queueStarted=deferred(), custody=page({db:idb(),local:storage()});
  custody.local.setItem('app_token','A-token');
  custody.STATE.uid='A-uid'; custody.STATE.employee='Person A';
  const ordinaryPut=custody.db.put;
  custody.db.put=async (store,value,key,valid)=>{
    if(store==='queue'){queueStarted.resolve(); await queuedWrite.promise;}
    return ordinaryPut(store,value,key,valid);
  };
  custody.ctx.api=async () => { custody.calls.sent++; return {ok:true}; };
  const oldSend=custody.run(`submitSavedOperation({op_id:'A-late',kind:'save',rep:'A-RECORD'}, {})`);
  await queueStarted.promise;
  await custody.run('wipeDevice({keepQueue:true})');
  custody.local.setItem('app_token','B-token');
  custody.STATE.uid='B-uid'; custody.STATE.employee='Person B';
  queuedWrite.resolve(); await oldSend;
  assert.equal(custody.db.maps.queue.get('A-late')._owner.uid,'A-uid');
  assert.equal(custody.calls.sent,0,'late A save cannot start an HTTP request under B');

  // Two tabs share localStorage but not the DOM. Changing identity elsewhere
  // must blank this already-rendered tab synchronously, without deleting the
  // new owner's shared storage or unsent work.
  const tab = page({db:idb(),local:storage(),responses:[A]});
  tab.local.setItem('app_token','A-token');
  await tab.run('boot()'); await settle();
  assert.match(tab.app.innerHTML,/A-private/);
  tab.local.setItem('app_token','B-token');
  tab.run(`sharedLoginChanged({storageArea:localStorage,key:'app_token',oldValue:'A-token',newValue:'B-token'})`);
  assert.equal(tab.app.innerHTML,'','other tab must not retain A portfolio while B owns the bearer');
  assert.equal(tab.calls.reload,true);
  assert.equal(tab.local.getItem('app_token'),'B-token');

  // Hard/Soft forms save after a 500ms timer. A cross-tab switch must first
  // flush the open A form while its UID/DOM are still present, then preserve
  // labelled unsent work without showing it under B's identity.
  const forms=page({local:storage()});
  forms.local.setItem('app_token','A-token');
  forms.STATE.uid=111; forms.STATE.employee='Person A';
  forms.STATE.cur={rep:'A-RECORD'}; forms.STATE.tab='form';
  forms.app.innerHTML='<div>A-private edit in progress</div>';
  const originalDollar=forms.ctx.$;
  forms.ctx.$=id=>id==='f_visit_comment'?{value:'Hard unsent text'}:originalDollar(id);
  forms.ctx.readForm=()=>({visit_comment:'Hard unsent text'});
  forms.local.setItem('app_token','B-token');
  forms.run(`sharedLoginChanged({storageArea:localStorage,key:'app_token',oldValue:'A-token',newValue:'B-token'})`);
  assert.equal(JSON.parse(forms.local.getItem('draft:111:A-RECORD')).v.visit_comment,'Hard unsent text');
  assert.equal(forms.app.innerHTML,'','A private DOM is hidden before B reload');
  await forms.run('wipeDevice({keepQueue:true})');
  assert(forms.local.getItem('draft:111:A-RECORD'),'A owned Hard draft survives involuntary rebind');
  forms.STATE.uid=222;
  assert.equal(forms.run(`loadDraft('A-RECORD')`),null,'B never mounts A draft');
  forms.STATE.uid=111;
  assert.equal(forms.run(`loadDraft('A-RECORD')`).v.visit_comment,'Hard unsent text');

  forms.STATE.cur={rep:'A-RECORD'}; forms.STATE.tab='callform';
  forms.ctx.captureSoftForm=()=>({fields:[{id:'f_soft_comment',value:'Soft unsent text'}],touched:['comment'],cleared:[]});
  forms.local.setItem('app_token','A-token');
  forms.local.setItem('app_token','B-token');
  forms.run(`sharedLoginChanged({storageArea:localStorage,key:'app_token',oldValue:'A-token',newValue:'B-token'})`);
  assert.match(forms.local.getItem('softdraft:111:A-RECORD'),/Soft unsent text/);
  await forms.run('clearLocalData({keepQueue:true})');
  assert(forms.local.getItem('softdraft:111:A-RECORD'),'A owned Soft draft survives involuntary rebind');
  await forms.run('wipeDevice({keepQueue:false})');
  assert.equal(forms.local.getItem('draft:111:A-RECORD'),null,'explicit logout erases Hard draft');
  assert.equal(forms.local.getItem('softdraft:111:A-RECORD'),null,'explicit logout erases Soft draft');

  // The real network helper also rejects an A response resolved after B takes
  // over, before any caller can pass that stale person to storeCard/rendering.
  const responseGate = deferred();
  const network = {
    AbortController, setTimeout, clearTimeout, WIPE_GEN: 0, credential: 'token:A-token',
    cacheCredential() { return this.credential; }, authHeaders: () => ({}),
    fetch: async () => ({ok: true, json: async () => responseGate.promise}),
  };
  vm.createContext(network); vm.runInContext(source('api'), network);
  const oldResponse = network.api('/api/person?rep=A-RECORD');
  network.WIPE_GEN++; network.credential = 'token:B-token';
  responseGate.resolve({rep:'A-RECORD',client:'A-card'});
  await assert.rejects(oldResponse, /stale response/);
  console.log('PWA identity cache: revoked A -> B offline/online, UID-bound queue, rotation, late writes PASS');
})().catch(e => { console.error(e); process.exitCode = 1; });
