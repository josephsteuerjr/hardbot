const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const esbuild = require('esbuild');
const vm = require('node:vm');
function load(file) {
  const code = esbuild.buildSync({ entryPoints: [path.join(__dirname, '../src/main', file)], bundle: true, platform: 'node', format: 'cjs', write: false }).outputFiles[0].text;
  const m = { exports: {} }; vm.runInNewContext(code, { module: m, exports: m.exports, require, Buffer }); return m.exports;
}
const { verifyRelease, matchUpdate } = load('update-policy.ts');
const pair = crypto.generateKeyPairSync('ed25519');
const publicKey = pair.publicKey.export({ type: 'spki', format: 'pem' });
const release = { product: 'soft', version: '3.0.1', file: 'soft-3.0.1-setup.exe', sha512: crypto.createHash('sha512').update('test payload').digest('base64'), size: 12 };
function signed(d) { const payload = Buffer.from(JSON.stringify(d)); return { payload: payload.toString('base64'), signature: crypto.sign(null, payload, pair.privateKey).toString('base64') }; }
assert.equal(verifyRelease(signed(release), publicKey, 'soft').version, '3.0.1');
assert.throws(() => verifyRelease(signed(release), publicKey, 'hard'));
assert.throws(() => verifyRelease({ ...signed(release), signature: Buffer.alloc(64).toString('base64') }, publicKey, 'soft'));
assert.throws(() => verifyRelease(signed({ ...release, file: '../manager-3.0.1-setup.exe' }), publicKey, 'soft'));
assert.throws(() => verifyRelease(signed({ ...release, size: 0 }), publicKey, 'soft'));
const update = { version: release.version, files: [{ url: release.file, sha512: release.sha512, size: release.size }] };
matchUpdate(update, release);
assert.throws(() => matchUpdate({ ...update, version: '3.0.2' }, release));
assert.throws(() => matchUpdate({ ...update, files: [{ ...update.files[0], sha512: 'tampered' }] }, release));
assert.throws(() => matchUpdate({ ...update, files: [...update.files, ...update.files] }, release));
const { migrateData, importData } = load('migration.ts');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'hard-migration-test-'));
try {
  const roaming = path.join(temp, 'Roaming'), legacy = path.join(roaming, 'Хард-сотрудник'), target = path.join(temp, 'soft');
  fs.mkdirSync(legacy, { recursive: true });
  const queue = Buffer.from('Synthetic encrypted queue: stable op_id and owner');
  fs.writeFileSync(path.join(legacy, 'settings.json'), JSON.stringify({ fieldServer: 'https://synthetic.invalid', fieldDevice: { id: 42, telegram_id: 555 }, theme: 'dark', server: 'https://other.invalid' }));
  fs.writeFileSync(path.join(legacy, 'field.key'), 'Synthetic DPAPI bytes');
  fs.writeFileSync(path.join(legacy, 'Local State'), '{"os_crypt":{"encrypted_key":"synthetic-profile-key"}}');
  fs.writeFileSync(path.join(legacy, 'calls-pending.bin'), queue);
  migrateData(target, roaming, 'soft');
  assert.deepEqual(fs.readFileSync(path.join(target, 'calls-pending.bin')), queue);
  assert.deepEqual(fs.readFileSync(path.join(target, 'Local State')), fs.readFileSync(path.join(legacy, 'Local State')));
  assert.equal(JSON.parse(fs.readFileSync(path.join(target, 'settings.json'))).fieldDevice.telegram_id, 555);
  assert.equal(JSON.parse(fs.readFileSync(path.join(target, 'settings.json'))).server, undefined);
  fs.writeFileSync(path.join(target, 'field.key'), 'New token from re-enrollment');
  migrateData(target, roaming, 'soft');
  assert.equal(fs.readFileSync(path.join(target, 'field.key'), 'utf8'), 'New token from re-enrollment');
  assert.throws(() => importData(legacy, target, 'soft'));
  assert.deepEqual(fs.readFileSync(path.join(legacy, 'calls-pending.bin')), queue);
  const second = path.join(roaming, 'Хард'); fs.mkdirSync(second);
  fs.writeFileSync(path.join(second, 'settings.json'), JSON.stringify({ fieldServer: 'https://synthetic.invalid', fieldDevice: { telegram_id: 777 } }));
  fs.writeFileSync(path.join(second, 'field.key'), 'Other identity');
  const ambiguous = path.join(temp, 'ambiguous'); migrateData(ambiguous, roaming, 'hard');
  assert.equal(fs.existsSync(path.join(ambiguous, 'field.key')), false);
  assert.equal(fs.existsSync(path.join(ambiguous, 'migration-choice.json')), true);
} finally {
  assert.ok(temp.startsWith(path.join(os.tmpdir(), 'hard-migration-test-')));
  fs.rmSync(temp, { recursive: true, force: true });
}
console.log('Desktop policy: signature, product/channel substitution, tampered metadata, migration/reinstall, queue/identity preservation and ambiguous legacy sources passed.');
