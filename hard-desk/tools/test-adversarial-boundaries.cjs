// In-memory TypeScript test execution; no application/release build is written.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const esbuild = require('esbuild');
const base = path.resolve(__dirname, '../../_local/adversarial-review-2026-10-12/runs');
fs.mkdirSync(base, { recursive: true });
const temp = fs.mkdtempSync(path.join(base, 'node-boundaries-'));
let responder = async () => new Response(JSON.stringify({ ok: true }), { status: 200 });
const electron = {
  app: { getPath: () => temp },
  safeStorage: { isEncryptionAvailable: () => true, encryptString: text => Buffer.from(text), decryptString: bytes => bytes.toString() },
  session: { fromPartition: () => ({ setProxy: async () => {}, fetch: (...args) => responder(...args) }) },
};
function load(name) {
  const sources = process.env.HARD_BOUNDARY_SOURCE || path.join(__dirname, '../src/main');
  const code = esbuild.buildSync({ entryPoints: [path.join(sources, name + '.ts')], bundle: true, platform: 'node', format: 'cjs', external: ['electron'], write: false }).outputFiles[0].text;
  const m = { exports: {} };
  vm.runInNewContext(code, { module: m, exports: m.exports, Buffer, Response, URLSearchParams, AbortController, setTimeout, clearTimeout, require: name => name === 'electron' ? electron : require(name) });
  return m.exports;
}
async function run(name, attack) {
  try { await attack(); console.log('PASS ' + name); return true; }
  catch (error) { console.error('FAIL ' + name + ': ' + error.message); return false; }
}
(async () => {
  const results = [];
  for (const mode of ['field', 'manager']) for (const status of [200, 401]) results.push(await run(`late-${mode}-${status}`, async () => {
    const store = load('store'), net = load('net');
    store.writeSettings({ fieldServer: 'https://synthetic.invalid', server: 'https://synthetic.invalid', fieldDevice: { telegram_id: 101 } });
    store.writeFieldToken('old-field'); store.writeToken('old-manager');
    let release, entered;
    const started = new Promise(resolve => { entered = resolve; });
    const barrier = new Promise(resolve => { release = resolve; });
    responder = async () => { entered(); await barrier; return new Response(JSON.stringify(status === 200 ? { ok: true, private: 'OLD-TEAM-SECRET' } : { error: 'device_revoked' }), { status }); };
    const pending = mode === 'field' ? net.callField('GET', '/api/person', { rep: 'OLD' }) : net.call('GET', '/api/manager/portfolio');
    await started;
    store.writeFieldToken('new-field'); store.writeToken('new-manager');
    store.writeSettings({ fieldDevice: { telegram_id: 202 } });
    release(); const result = await pending;
    assert.equal(store.readFieldToken(), 'new-field', 'old response erased the new field token');
    assert.equal(store.readToken(), 'new-manager', 'old response erased the new manager token');
    assert.equal(result.ok, false, 'old private response delivered to the new identity');
    assert.equal(result.data, undefined);
  }));
  for (const operation of ['upload', 'logout-field', 'logout-manager']) results.push(await run(`late-${operation}`, async () => {
    const store = load('store'), net = load('net');
    store.writeSettings({ fieldServer: 'https://synthetic.invalid', server: 'https://synthetic.invalid', fieldDevice: { telegram_id: 101 } });
    store.writeFieldToken('old-field'); store.writeToken('old-manager');
    let release, entered;
    const started = new Promise(resolve => { entered = resolve; });
    const barrier = new Promise(resolve => { release = resolve; });
    responder = async () => { entered(); await barrier; return new Response(JSON.stringify({ error: 'device_revoked' }), { status: 401 }); };
    const file = path.join(temp, 'receipt.jpg'); fs.writeFileSync(file, 'synthetic image');
    const pending = operation === 'upload' ? net.uploadField('/api/receipt', { rep: 'OLD' }, file, '.jpg') : operation === 'logout-field' ? net.logoutField() : net.logout();
    await started;
    store.writeFieldToken('new-field'); store.writeToken('new-manager');
    store.writeSettings({ fieldDevice: { telegram_id: 202 } });
    release(); await pending;
    assert.equal(store.readFieldToken(), 'new-field', 'old operation erased the new field token');
    assert.equal(store.readToken(), 'new-manager', 'old operation erased the new manager token');
  }));
  results.push(await run('migration-preserves-durable-draft', async () => {
    const source = path.join(temp, 'legacy'), target = path.join(temp, 'migrated');
    fs.mkdirSync(source); fs.mkdirSync(target);
    const settings = { fieldServer: 'https://synthetic.invalid', fieldDevice: { id: 1, telegram_id: 101 } };
    fs.writeFileSync(path.join(source, 'settings.json'), JSON.stringify(settings));
    fs.writeFileSync(path.join(source, 'field.key'), 'synthetic-key');
    const owner = crypto.createHash('sha256').update(settings.fieldServer + '\nuid:101').digest('hex');
    const draftName = 'draft-' + crypto.createHash('sha256').update(owner + '\nsoft\nOLD').digest('hex') + '.bin';
    fs.writeFileSync(path.join(source, draftName), 'synthetic durable unsent note');
    load('migration').importData(source, target, 'soft');
    assert.ok(fs.existsSync(path.join(target, draftName)), 'unsent draft absent after successful import');
    assert.equal(fs.readFileSync(path.join(target, draftName), 'utf8'), 'synthetic durable unsent note');
  }));
  for (const different of ['draft', 'token']) results.push(await run(`migration-does-not-choose-divergent-${different}`, async () => {
    const roaming = path.join(temp, 'roaming-' + different), target = path.join(temp, 'choice-' + different);
    for (const [index, name] of ['Хард-сотрудник', 'Хард'].entries()) {
      const source = path.join(roaming, name); fs.mkdirSync(source, { recursive: true });
      fs.writeFileSync(path.join(source, 'settings.json'), JSON.stringify({ fieldServer: 'https://synthetic.invalid', fieldDevice: { id: 1, telegram_id: 101 } }));
      fs.writeFileSync(path.join(source, 'field.key'), different === 'token' ? 'synthetic-token-' + index : 'same-token');
      fs.writeFileSync(path.join(source, 'draft-' + 'a'.repeat(64) + '.bin'), different === 'draft' ? 'draft-' + index : 'same-draft');
    }
    load('migration').migrateData(target, roaming, 'soft');
    assert.equal(fs.existsSync(path.join(target, 'settings.json')), false, 'one of divergent old profiles was chosen silently');
    assert.equal(fs.existsSync(path.join(target, 'migration-choice.json')), true);
  }));
  results.push(await run('queue-same-operation-different-body', async () => {
    const store = load('store'), net = load('net');
    store.clearCallQueue(); store.writeFieldToken('queue-owner');
    store.writeSettings({ fieldServer: 'https://synthetic.invalid', fieldDevice: { telegram_id: 101 } });
    responder = async () => { throw new Error('ERR_INTERNET_DISCONNECTED'); };
    const original = { kind: 'call', rep: 'OLD', finish: true, op_id: 'same-op', occurred_at: Date.now(), values: { soft_call_status: 'Нет контакта', soft_comment: 'first' } };
    assert.equal((await net.callField('POST', '/api/save', undefined, original)).data.queued, true);
    const changed = await net.callField('POST', '/api/save', undefined, { ...original, values: { ...original.values, soft_comment: 'replacement' } });
    assert.equal(changed.ok, false, 'different body falsely acknowledged as queued');
    assert.equal(store.readCallQueue().entries[0].body.values.soft_comment, 'first');
  }));
  results.push(await run('analyst-column-routes-reach-backend', async () => {
    const transport = load('transport');
    for (const route of ['/reg/columns_reload', '/reg/columns_check', '/reg/columns_save'])
      assert.equal(transport.localPath(route), true, 'supported backend route blocked: ' + route);
    for (const route of ['//@evil/', '/reg/../admin', '/reg/columns_check?secret=1', '/reg/columns_check#fragment'])
      assert.equal(transport.localPath(route), false, 'unsafe route accepted: ' + route);
  }));
  results.push(await run('analyst-marker-export-requires-write-grant', async () => {
    const caps = load('file-capabilities');
    const file = path.join(temp, 'read-selected.xlsx'); fs.writeFileSync(file, 'original selected input');
    caps.grantFile(99, file, false);
    // Baseline main.ts used only a substring /save to identify file writes.
    const authorize = caps.selectedLocalFile || ((owner, route, file) => caps.selectedFile(owner, file, route.includes('/save')));
    assert.throws(() => authorize(99, '/exp/markers', file), 'marker export allowed to overwrite a read-only selection');
    assert.equal(fs.readFileSync(file, 'utf8'), 'original selected input');
    caps.grantFile(99, file, true);
    assert.equal(authorize(99, '/exp/markers', file), fs.realpathSync(file));
  }));
  fs.writeFileSync(path.join(temp, 'RESULTS.json'), JSON.stringify({ passed: results.filter(Boolean).length, failed: results.filter(r => !r).length }, null, 2));
  console.log('Evidence: ' + temp);
  if (results.some(r => !r)) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
