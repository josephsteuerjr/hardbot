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
let network = 'offline', sent = [], hold = null;
const electron = {
  app: { getPath: () => temp },
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: text => Buffer.from(Buffer.from(text).toString('base64')),
    decryptString: bytes => Buffer.from(bytes.toString(), 'base64').toString(),
  },
  session: { fromPartition: () => ({ setProxy: async () => {}, fetch: async (_url, options) => {
    if (network === 'offline') throw new Error('ERR_INTERNET_DISCONNECTED');
    sent.push(JSON.parse(options.body));
    if (hold) { const wait = hold; hold = null; await wait; }
    return { ok: network === 'online' || network === 'partial', status: network === '500' ? 500 : network === '404' ? 404 : 200,
      text: async () => 'synthetic rejection', json: async () => network === 'partial' ? { rejected: { forecast_sum: 'format' } } : { ok: true } };
  } }) },
};
function load(name) {
  const code = esbuild.buildSync({ entryPoints: [path.join(root, 'src/main', name + '.ts')], bundle: true,
    platform: 'node', format: 'cjs', external: ['electron'], write: false }).outputFiles[0].text;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, Buffer, setTimeout, clearTimeout,
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
  console.log('Call queue: offline, restart, idempotence, concurrent saves, transient errors, rejection and identity checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => {
  const resolved = fs.realpathSync(temp);
  assert.ok(resolved.startsWith(fs.realpathSync(os.tmpdir()) + path.sep));
  fs.rmSync(resolved, { recursive: true, force: true });
});
