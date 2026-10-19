// Isolated manager-account consumer contract: no real server or profile touched.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const esbuild = require('esbuild');
const pcProof = require('./mock-pc-proof.cjs')();
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'hard-manager-account-'));
const root = path.resolve(__dirname, '..');
let role = 'manager', action = 'ok_pending', pending = true, mustChange = false, bypassRoleGate = false;
let requests = [], hold = null;
const electron = {
  app: { getPath: () => temp },
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: text => Buffer.from(Buffer.from(text).toString('base64')),
    decryptString: bytes => Buffer.from(bytes.toString(), 'base64').toString(),
  },
  session: { fromPartition: () => ({ setProxy: async () => {}, fetch: async (url, opts) => {
    const body = opts.body ? JSON.parse(opts.body) : null;
    if (url.endsWith('/api/account/pc_challenge')) return Response.json(pcProof.issue(body));
    requests.push({ url, method: opts.method, body, headers: opts.headers });
    if (hold) { const barrier = hold; hold = null; await barrier; }
    if (url.endsWith('/api/account/login')) {
      pcProof.verify(body);
      assert.equal(opts.method, 'POST');
      assert.equal(body.audience, 'manager');
      assert.equal(body.device.kind, 'desktop');
      assert.match(body.device.fp, /^[a-f0-9]{64}$/);
      if (!bypassRoleGate && !['manager', 'security', 'full'].includes(role))
        return Response.json({ action: 'blocked', error: 'role_mismatch' }, { status: 403 });
      return Response.json({ action, token: 'account-' + role, uid: 101, role,
        username: 'synthetic', must_change_password: mustChange });
    }
    if (url.endsWith('/api/manager/device/me')) {
      if (pending) return Response.json({ error: 'account_pending' }, { status: 403 });
      return Response.json({ me: { role, scope: 'Synthetic scope' }, caps: { team_links: true, control: false } });
    }
    if (url.endsWith('/api/manager/account_phone_request')) {
      if (pending) return Response.json({ ok: false, error: 'account_pending' }, { status: 403 });
      if (body.device_id !== 91) return Response.json({ ok: false, error: 'phone_request_not_eligible' }, { status: 403 });
      return Response.json({ ok: true, code: 'synthetic-one-time-code', device_id: 91,
        account: 'synthetic-staff', expires_at: new Date(Date.now() + 900_000).toISOString(), expires_in_seconds: 900 });
    }
    if (url.endsWith('/api/account/password')) return Response.json({ ok: true });
    if (url.endsWith('/api/account/sessions')) return Response.json({ ok: true, logins: [] });
    if (url.endsWith('/api/manager/device/logout')) return Response.json({ ok: true });
    throw new Error('Unexpected network path ' + url);
  } }) },
};
function load(name) {
  const code = esbuild.buildSync({ entryPoints: [path.join(root, 'src/main', name + '.ts')], bundle: true,
    platform: 'node', format: 'cjs', external: ['electron'], write: false }).outputFiles[0].text;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, Buffer, Response, URLSearchParams,
    AbortController, setTimeout, clearTimeout, process:{env:{HARD_USER_DATA:temp}}, require: name => name === 'electron' ? electron : require(name) });
  return module.exports;
}
(async () => {
  const store = load('store'), net = load('net');
  store.writeSettings({ server: 'https://synthetic.invalid', device: { id: 8, name: 'Legacy manager' },
    fieldServer: 'https://synthetic.invalid', fieldDevice: { name: 'Staff', telegram_id: 202 } });
  store.writeToken('legacy-manager'); store.writeFieldToken('legacy-staff');
  const queue = { owner: 'staff-only', entries: [{ body: { op_id: 'synthetic-queued-call' } }] };
  store.writeCallQueue(queue);
  store.cachePut('field-offline', { private: 'synthetic' });
  // A valid legacy manager bearer is insufficient; reject before network.
  const beforeRequest = requests.length;
  assert.equal((await net.call('POST', '/api/manager/account_phone_request', undefined, { device_id: 91 })).ok, false);
  assert.equal(requests.length, beforeRequest);
  for (const denied of ['agent', 'analyst']) {
    role = denied;
    const result = await net.accountManagerLogin('https://synthetic.invalid', 'synthetic', 'password');
    assert.equal(result.ok, false);
    assert.equal(store.readToken(), 'legacy-manager');
    assert.equal(store.readSettings().device.id, 8);
  }
  // Defense in depth: even a buggy 200 response for a staff/analyst principal
  // must not replace the existing manager token.
  bypassRoleGate = true;
  for (const denied of ['agent', 'analyst']) {
    role = denied;
    assert.equal((await net.accountManagerLogin('https://synthetic.invalid', 'synthetic', 'password')).ok, false);
    assert.equal(store.readToken(), 'legacy-manager');
  }
  bypassRoleGate = false;
  role = 'manager'; mustChange = true;
  let result = await net.accountManagerLogin('https://synthetic.invalid', 'synthetic', 'password');
  assert.equal(result.ok, true);
  assert.equal(result.data.must_change_password, true);
  assert.equal(result.data.action, 'ok_pending');
  assert.equal(store.readToken(), 'account-manager');
  assert.equal(store.readSettings().device.username, 'synthetic');
  assert.equal(store.readSettings().device.role, 'manager');
  assert.equal(store.readFieldToken(), 'legacy-staff');
  assert.equal(JSON.stringify(store.readCallQueue()), JSON.stringify(queue));
  assert.equal(store.cacheGet('field-offline').data.private, 'synthetic');
  assert.equal((await net.call('GET', '/api/manager/device/me')).text, 'account_pending');
  const pendingCode = await net.call('POST', '/api/manager/account_phone_request', undefined, { device_id: 91 });
  assert.equal(pendingCode.ok, false);
  assert.equal(pendingCode.text, 'account_pending');
  assert.equal((await net.call('POST', '/api/account/password', undefined, { username: 'synthetic', current: 'old', new: 'new12345' })).ok, true);
  assert.equal((await net.call('GET', '/api/account/sessions')).ok, true);
  assert.equal((await net.call('GET', '/admin/devices.json')).ok, false);
  assert.equal(requests.some(x => x.url.includes('/admin/')), false);
  pending = false;
  assert.equal((await net.call('GET', '/api/manager/device/me')).data.me.role, 'manager');
  for (const bad of [{ device_id: 0 }, { device_id: '91' }, { device_id: 91, other: true }]) {
    const count = requests.length;
    assert.equal((await net.call('POST', '/api/manager/account_phone_request', undefined, bad)).ok, false);
    assert.equal(requests.length, count);
  }
  const foreign = await net.call('POST', '/api/manager/account_phone_request', undefined, { device_id: 92 });
  assert.equal(foreign.ok, false);
  assert.equal(foreign.text, 'phone_request_not_eligible');
  const codeResult = await net.call('POST', '/api/manager/account_phone_request', undefined, { device_id: 91 });
  assert.equal(codeResult.ok, true);
  assert.equal(codeResult.data.code, 'synthetic-one-time-code');
  assert.equal(requests.at(-1).body.device_id, 91);
  assert.equal(JSON.stringify(store.readSettings()).includes('synthetic-one-time-code'), false);
  assert.equal(JSON.stringify(store.readCallQueue()).includes('synthetic-one-time-code'), false);
  await net.logout();
  assert.equal(store.readToken(), '');
  assert.equal(store.readSettings().device, undefined);
  assert.equal(store.readFieldToken(), 'legacy-staff');
  for (const allowed of ['security', 'full']) {
    role = allowed; action = 'ok'; mustChange = false;
    result = await net.accountManagerLogin('https://synthetic.invalid', 'synthetic', 'password');
    assert.equal(result.ok, true);
    assert.equal(store.readSettings().device.role, allowed);
  }
  let release;
  hold = new Promise(resolve => { release = resolve; });
  const stale = net.accountManagerLogin('https://synthetic.invalid', 'synthetic', 'password');
  await new Promise(resolve => setTimeout(resolve, 0));
  store.wipe(); release();
  assert.equal((await stale).ok, false);
  assert.equal(store.readToken(), '');
  assert.equal(store.readFieldToken(), 'legacy-staff');
  console.log('Manager account transport: role boundary, pending/approval, password, legacy isolation, stale response passed');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => {
  const resolved = fs.realpathSync(temp);
  assert.ok(resolved.startsWith(fs.realpathSync(os.tmpdir()) + path.sep));
  fs.rmSync(resolved, { recursive: true, force: true });
});
