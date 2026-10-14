// Проверка реального транспорта/хранилища с заменёнными Electron и сетью.
// Временные синтетические данные, ни рабочих настроек, ни обращения к серверу.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const esbuild = require('esbuild');

const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'hard-call-queue-test-'));
let network = 'offline', sent = [], hold = null, enrollment = null, managerInfo = null;
const electron = {
  app: { getPath: () => temp },
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: text => Buffer.from(Buffer.from(text).toString('base64')),
    decryptString: bytes => Buffer.from(bytes.toString(), 'base64').toString(),
  },
  session: { fromPartition: () => ({ setProxy: async () => {}, fetch: async (_url, options) => {
    if (network === 'offline') throw new Error('ERR_INTERNET_DISCONNECTED');
    if (options.body) sent.push(JSON.parse(options.body));
    if (hold) { const wait = hold; hold = null; await wait; }
    const status = network === '500' ? 500 : network === '404' ? 404 : network === '401' ? 401 : network === '403' ? 403 : network === '429' ? 429 : 200;
    const body = _url.endsWith('/device/enroll') && enrollment ? enrollment : _url.endsWith('/device/me') && managerInfo ? managerInfo : network === 'partial' ? { ok: true, rejected: { forecast_sum: 'format' } } : { ok: true };
    return new Response(network === 'invalid' ? 'not JSON' : JSON.stringify(body), {status});
  } }) },
};
function load(name) {
  const code = esbuild.buildSync({ entryPoints: [path.join(root, 'src/main', name + '.ts')], bundle: true,
    platform: 'node', format: 'cjs', external: ['electron'], write: false }).outputFiles[0].text;
  const module = { exports: {} };
    vm.runInNewContext(code, { module, exports: module.exports, Buffer, Response, URLSearchParams, AbortController, setTimeout, clearTimeout,
    require: name => name === 'electron' ? electron : require(name) });
  return module.exports;
}
(async () => {
  const store = load('store');
  store.writeSettings({ fieldServer: 'https://synthetic.invalid' }); store.writeFieldToken('synthetic-token');
  let net = load('net');
  const call = id => ({ rep: 'SYNTHETIC', kind: 'call', finish: true, op_id: id, occurred_at: 1780000000123,
    values: { soft_call_status: 'Synthetic status', soft_comment: 'private synthetic note' } });
  const first = call('one');
  const queued = await net.callField('POST', '/api/save', undefined, first);
  assert.equal(queued.data.queued, true);
  assert.equal(store.readCallQueue().entries.length, 1);
  assert.equal(fs.readFileSync(path.join(temp, 'calls-pending.bin')).includes(Buffer.from('private synthetic note')), false);
  net = load('net'); // перезапуск транспорта, очередь осталась на диске
  network = 'online';
  await net.flushCallQueue(); await net.flushCallQueue();
  assert.equal(store.readCallQueue().entries.length, 0);
  assert.deepEqual(sent, [first]); // неизменные op_id и время, без повторной отправки
  let release;
  hold = new Promise(resolve => { release = resolve; });
  const a = net.callField('POST', '/api/save', undefined, call('two'));
  await new Promise(resolve => setTimeout(resolve, 0));
  const b = net.callField('POST', '/api/save', undefined, call('three'));
  release(); await Promise.all([a, b]);
  assert.equal(store.readCallQueue().entries.length, 0);
  assert.deepEqual(sent.map(x => x.op_id), ['one', 'two', 'three']);
  network = '500';
  assert.equal((await net.callField('POST', '/api/save', undefined, call('transient'))).data.queued, true);
  assert.equal(store.readCallQueue().entries[0].error, undefined);
  network = 'online'; await net.flushCallQueue();
  network = '429';
  assert.equal((await net.callField('POST', '/api/save', undefined, call('rate-limit'))).data.queued, true);
  assert.equal(store.readCallQueue().entries[0].error, undefined);
  network = 'invalid';
  await net.flushCallQueue();
  assert.equal(store.readCallQueue().entries.length, 1); // HTTP 200 without JSON acknowledgement cannot erase work
  network = 'online'; await net.flushCallQueue();
  network = '404';
  assert.equal((await net.callField('POST', '/api/save', undefined, call('reassigned'))).ok, false);
  assert.ok(store.readCallQueue().entries[0].error);
  network = 'partial';
  await net.callField('POST', '/api/save', undefined, call('partial'));
  assert.equal(store.readCallQueue().entries.length, 2);
  assert.ok(store.readCallQueue().entries[1].error.includes('forecast_sum'));
  const count = sent.length;
  await net.flushCallQueue(); assert.equal(sent.length, count); // ошибки требуют решения, не бесконечного ретрая
  store.writeFieldToken('different-account');
  assert.equal((await net.callField('POST', '/api/save', undefined, call('wrong-account'))).ok, false);
  assert.equal(store.readCallQueue().entries.length, 2); // чужой вход не получает результаты
  store.writeFieldToken('synthetic-token');
  await net.callField('POST', '/api/local/call_queue/remove', undefined, { op_id: 'reassigned' });
  assert.equal(store.readCallQueue().entries.length, 1);
  store.clearCallQueue();
  store.writeSettings({fieldDevice:{id:1,name:'Synthetic PC',employee:'Synthetic Soft',telegram_id:101}});
  network='offline';
  await net.callField('POST','/api/save',undefined,call('stable-principal'));
  const stableOwner=store.readCallQueue().owner;
  store.writeFieldToken('rotated-token-for-same-principal');
  network='online'; await net.flushCallQueue();
  assert.equal(store.readCallQueue().entries.length,0); // token rotation preserves the same identity
  network='offline'; await net.callField('POST','/api/save',undefined,call('recover-after-revoke'));
  network='401'; await net.flushCallQueue();
  assert.equal(store.readFieldToken(),'');
  assert.equal(store.readSettings().fieldDevice.telegram_id,101);
  assert.equal(store.readCallQueue().owner,stableOwner);
  network='online';
  enrollment={token:'new-same-principal-token',device_id:2,employee:'Synthetic Soft',telegram_id:101};
  assert.equal((await net.enrollField('https://synthetic.invalid/i/synthetic-link')).ok,true);
  assert.equal(store.readCallQueue().owner,stableOwner);
  await net.flushCallQueue(); assert.equal(store.readCallQueue().entries.length,0);
  network='offline'; await net.callField('POST','/api/save',undefined,call('do-not-switch-user'));
  enrollment={token:'another-principal-token',device_id:3,employee:'Other Synthetic Soft',telegram_id:202};
  network='online';
  assert.equal((await net.enrollField('https://synthetic.invalid/i/another-link')).ok,false);
  assert.equal(store.readFieldToken(),'new-same-principal-token');
  assert.equal(store.readCallQueue().entries.length,1);
  store.clearCallQueue();
  const payment = {rep:'SYNTHETIC-2',kind:'payment',finish:true,op_id:'manual-payment-one',
    occurred_at:1780000000123,amount:'1500.50',paid_at:'2026-05-20'};
  network='offline';
  assert.equal((await net.callField('POST','/api/save',undefined,payment)).data.queued,true);
  net=load('net');
  assert.deepEqual(JSON.parse(JSON.stringify(store.readCallQueue().entries[0].body)),payment);
  network='online';await net.flushCallQueue();await net.flushCallQueue();
  assert.equal(store.readCallQueue().entries.length,0);
  assert.equal(sent.filter(x=>x.op_id===payment.op_id).length,1);
  network='404';
  assert.equal((await net.callField('POST','/api/save',undefined,{...payment,op_id:'payment-reassigned'})).ok,false);
  assert.ok(store.readCallQueue().entries[0].error);
  store.clearCallQueue();
  network='online';
  await net.callField('GET','/api/bootstrap');
  store.writeSettings({server:'https://synthetic.invalid'}); store.writeToken('manager-one');
  await net.call('GET','/api/manager/device/me');
  store.writeFieldToken('a-new-principal-token'); store.writeToken('manager-two');
  network='offline';
  assert.equal((await net.callField('GET','/api/bootstrap')).ok,false);
  assert.equal((await net.call('GET','/api/manager/device/me')).ok,false);
  network='online';managerInfo={me:{role:'full',scope:'Synthetic team'},caps:{control:true}};
  await net.call('GET','/api/manager/device/me');await net.call('GET','/api/control/overview');
  network='403';assert.equal((await net.call('GET','/api/control/overview')).ok,false);
  network='offline';assert.equal((await net.call('GET','/api/control/overview')).ok,false);
  network='online';
  let releaseOldRead;
  hold=new Promise(resolve=>{releaseOldRead=resolve;});
  const oldRead=net.call('GET','/api/control/overview');
  await new Promise(resolve=>setTimeout(resolve,0));
  network='403';await net.call('GET','/api/control/overview');
  network='online';releaseOldRead();await oldRead;
  network='offline';assert.equal((await net.call('GET','/api/control/overview')).ok,false);
  network='online';await net.call('GET','/api/manager/device/me');await net.call('GET','/api/control/overview');
  managerInfo={me:{role:'manager',scope:'Synthetic team'},caps:{control:false}};
  await net.call('GET','/api/manager/device/me');
  network='offline';assert.equal((await net.call('GET','/api/control/overview')).ok,false);
  network='online';await net.callField('GET','/api/person',{rep:'OLD'});
  network='404';await net.callField('GET','/api/person',{rep:'OLD'});
  network='offline';assert.equal((await net.callField('GET','/api/person',{rep:'OLD'})).ok,false);
  network='online';await net.callField('GET','/api/bootstrap');await net.callField('GET','/api/person',{rep:'OLD'});
  network='404';await net.callField('POST','/api/save',undefined,{rep:'OLD',kind:'call'}, {sendingQueue:true});
  network='offline';assert.equal((await net.callField('GET','/api/bootstrap')).ok,false);
  assert.equal((await net.callField('GET','/api/person',{rep:'OLD'})).ok,false);
  const originalIdentity = store.readSettings().fieldDevice;
  const draft = {fields:{'c-comment':{value:'Synthetic durable draft',checked:false},'c-psum':{value:'12.50',checked:false}},touched:['soft_comment','forecast_sum']};
  await net.callField('POST','/api/local/draft',undefined,{rep:'DRAFT',draft});
  net=load('net');
  assert.equal(JSON.stringify((await net.callField('GET','/api/local/draft',{rep:'DRAFT'})).data.draft),JSON.stringify(draft));
  store.writeSettings({fieldDevice:{...originalIdentity,telegram_id:9999}});
  assert.equal((await net.callField('GET','/api/local/draft',{rep:'DRAFT'})).data.draft,null);
  store.writeSettings({fieldDevice:originalIdentity});
  assert.equal(JSON.stringify((await net.callField('GET','/api/local/draft',{rep:'DRAFT'})).data.draft),JSON.stringify(draft));
  store.cachePut('TTL',{private:'synthetic'});
  const ttlFile=path.join(temp,'cache',require('node:crypto').createHash('sha256').update('TTL').digest('hex').slice(0,32)+'.bin');
  const old={at:new Date(Date.now()-9*3600000).toISOString(),data:{private:'synthetic'}};
  fs.writeFileSync(ttlFile,electron.safeStorage.encryptString(JSON.stringify(old)));
  assert.equal(store.cacheGet('TTL'),null);
  // Office operations use the same encrypted journal but an explicit route;
  // preferences must never become network or updater work.
  network='offline';
  const office={rep:'OFFICE',kind:'office',finish:true,op_id:'office-one',occurred_at:1780000000123,revision:'synthetic',action:'note',values:{note:'Synthetic office private note'}};
  assert.equal((await net.callField('POST','/api/office/action',undefined,office)).data.queued,true);
  assert.ok(store.hasFieldWork());
  assert.equal(fs.readFileSync(path.join(temp,'calls-pending.bin')).includes(Buffer.from(office.values.note)),false);
  await net.callField('POST','/api/local/preferences',undefined,{profile:'hard',id:'field-portfolio',value:{q:'Synthetic private search',view:'list'}});
  net=load('net');
  assert.equal((await net.callField('GET','/api/local/preferences',{profile:'hard',id:'field-portfolio'})).data.value.q,'Synthetic private search');
  assert.equal((await net.callField('GET','/api/local/preferences',{profile:'soft',id:'field-portfolio'})).data.value,null);
  network='online';await net.flushCallQueue();
  assert.deepEqual(sent.at(-1),office);
  await net.callField('POST','/api/local/draft',undefined,{rep:'DRAFT',draft:null});
  assert.equal(store.hasFieldWork(),false);
  assert.equal((await net.callField('GET','/api/workspace/report',{profile:'hard'})).ok,true);
  network='offline';
  const pendingOffice={...office,op_id:'office-crash'};
  const officeDraft={pending_op_id:pendingOffice.op_id,fields:{'o-note':{value:'accepted source',checked:false},'f-rc-sum':{value:'12.50',checked:false}},dirty:['o-note','f-rc-sum'],touched:['note'],revision:'before'};
  await net.callField('POST','/api/local/draft',undefined,{rep:'OFFICE',profile:'hard',draft:officeDraft});
  await net.callField('POST','/api/office/action',undefined,pendingOffice);net=load('net');network='online';await net.flushCallQueue();
  const afterOffice=(await net.callField('GET','/api/local/draft',{rep:'OFFICE',profile:'hard'})).data.draft;
  assert.equal(afterOffice.fields['o-note'],undefined);assert.equal(afterOffice.fields['f-rc-sum'].value,'12.50');assert.equal(afterOffice.pending_op_id,undefined);
  network='offline';
  const pendingPayment={rep:'OTHER-KD',draft_rep:'PAYMENT-CARD',kind:'payment',finish:true,op_id:'payment-crash',occurred_at:1780000000123,amount:'10.25',paid_at:'2026-10-10'};
  await net.callField('POST','/api/local/draft',undefined,{rep:'PAYMENT-CARD',draft:{pending_op_id:pendingPayment.op_id,fields:{'p-amount':{value:'10.25',checked:false},'c-comment':{value:'unsent call',checked:false}},touched:['soft_comment']}});
  await net.callField('POST','/api/save',undefined,pendingPayment);net=load('net');network='online';await net.flushCallQueue();
  const afterPayment=(await net.callField('GET','/api/local/draft',{rep:'PAYMENT-CARD'})).data.draft;
  assert.equal(afterPayment.fields['p-amount'],undefined);assert.equal(afterPayment.fields['c-comment'].value,'unsent call');
  network='offline';
  const pendingCall=call('call-crash');
  await net.callField('POST','/api/local/draft',undefined,{rep:pendingCall.rep,draft:{pending_op_id:pendingCall.op_id,fields:{'c-comment':{value:'accepted call',checked:false}},touched:['soft_comment']}});
  await net.callField('POST','/api/save',undefined,pendingCall);net=load('net');network='online';await net.flushCallQueue();
  assert.equal((await net.callField('GET','/api/local/draft',{rep:pendingCall.rep})).data.draft,null);
  // A newer draft with another operation marker is never erased by an old ack.
  network='offline';const olderCall=call('older-ack');await net.callField('POST','/api/save',undefined,olderCall);
  await net.callField('POST','/api/local/draft',undefined,{rep:olderCall.rep,draft:{pending_op_id:'newer-op',fields:{'c-comment':{value:'newer draft',checked:false}}}});
  network='online';await net.flushCallQueue();assert.equal((await net.callField('GET','/api/local/draft',{rep:olderCall.rep})).data.draft.fields['c-comment'].value,'newer draft');
  network='offline';store.writeSettings({server:'https://synthetic.invalid'});store.writeToken('synthetic-manager-one');
  assert.equal((await net.call('POST','/api/local/preferences',undefined,{profile:'hard',id:'team-portfolio-hard',value:{q:'Synthetic manager search',hidden:['country']}})).ok,true);
  net=load('net');assert.equal((await net.call('GET','/api/local/preferences',{profile:'hard',id:'team-portfolio-hard'})).data.value.q,'Synthetic manager search');
  assert.equal((await net.callField('GET','/api/local/preferences',{profile:'hard',id:'team-portfolio-hard'})).data.value,null);
  store.writeToken('synthetic-manager-two');assert.equal((await net.call('GET','/api/local/preferences',{profile:'hard',id:'team-portfolio-hard'})).data.value,null);
  store.writeToken('synthetic-manager-one');assert.equal((await net.call('GET','/api/local/preferences',{profile:'hard',id:'team-portfolio-hard'})).data.value.q,'Synthetic manager search');
  assert.equal((await net.call('GET','/api/local/preferences',{profile:'hard',id:'../invalid'})).ok,false);
  console.log('Queue/cache/drafts/preferences: calls, payments, office and manager settings; encrypted restart, routes, scope, updater work and offline expiry passed');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => {
  const resolved = fs.realpathSync(temp);
  assert.ok(resolved.startsWith(fs.realpathSync(os.tmpdir()) + path.sep));
  fs.rmSync(resolved, { recursive: true, force: true });
});
