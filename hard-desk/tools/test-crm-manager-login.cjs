// Real Windows DPAPI; synthetic credentials/profile only. No production requests.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const crypto = require('node:crypto'), Module = require('node:module');
const {execFileSync} = require('node:child_process'), {buildSync} = require('esbuild');
const desk = path.resolve(__dirname, '..'), qa = path.resolve(process.argv[2] || '');
assert(process.platform === 'win32' && process.argv[2] && qa.startsWith('C:\\htarget\\crm-'));
fs.mkdirSync(qa, {recursive:true});
process.resourcesPath = path.join(desk, 'installer/bin');
process.env.LOCALAPPDATA = path.join(qa, 'local-app-data');
const profile = path.join(process.env.LOCALAPPDATA, 'Hard/manager/data'), pilot = path.join(qa, 'pilot');
fs.mkdirSync(profile, {recursive:true}); fs.mkdirSync(pilot, {recursive:true});
let requests = [], reply, networkError;
const fakeElectron = {app: {getPath: () => pilot, getVersion: () => '3.3.0-pilot.2'}, net: {fetch: async (url, init) => {
  requests.push({url, init});
  if (networkError) throw networkError;
  await new Promise(resolve => setTimeout(resolve, 5));
  return new Response(JSON.stringify(reply), {status:200, headers:{'X-CRM-Namespace':'crm-shared-pilot-v1'}});
}}};
function load(name) {
  const filename = path.join(desk, 'out/qa-' + name + '.cjs');
  const code = buildSync({entryPoints:[path.join(desk, 'src/main', name + '.ts')], bundle:true, platform:'node', format:'cjs', external:['electron'], write:false}).outputFiles[0].text;
  const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const realRequire = mod.require.bind(mod); mod.require = name => name === 'electron' ? fakeElectron : realRequire(name);
  mod._compile(code, filename); return mod.exports;
}
const helper = path.join(process.resourcesPath, 'process-check.exe');
const protect = bytes => execFileSync(helper, ['seal'], {input:bytes, windowsHide:true});
const unprotect = bytes => execFileSync(helper, ['unseal'], {input:bytes, windowsHide:true});
const login = load('crm-manager-login'), dpapi = load('windows-dpapi');
const credential = crypto.randomBytes(32).toString('base64url');
const keyFile = path.join(profile,'device.key'), settingsFile = path.join(profile,'settings.json');
const settings = {server:'https://hard.rimborso.ru:8080', role:'made-up-local-role', name:'Untrusted synthetic name'};
const writeSettings = value => fs.writeFileSync(settingsFile, JSON.stringify(value));
function chromium(prefix='v10') {
  const key = crypto.randomBytes(32), nonce = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, nonce);
  const blob = Buffer.concat([Buffer.from(prefix), nonce, cipher.update(credential), cipher.final(), cipher.getAuthTag()]);
  fs.writeFileSync(path.join(profile,'Local State'), JSON.stringify({os_crypt:{encrypted_key:Buffer.concat([Buffer.from('DPAPI'), protect(key)]).toString('base64')}}));
  fs.writeFileSync(keyFile, blob); key.fill(0); return blob;
}
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const checks = [];
async function check(name, fn) {await fn(); checks.push(name);}
(async () => {
  writeSettings(settings);
  await check('Chromium v10/v11 decrypted by real Windows; source hashes unchanged', () => {
    for (const prefix of ['v10','v11']) {
      chromium(prefix);
      const files = ['settings.json','device.key','Local State'].map(name => path.join(profile,name));
      const before = files.map(hash);
      assert.equal(login.readExistingManagerSession().credential, credential);
      assert.deepEqual(files.map(hash), before);
    }
  });
  await check('Legacy DPAPI and direct pilot format remain readable', () => {
    fs.writeFileSync(keyFile, protect(Buffer.from(credential)));
    assert.equal(login.readExistingManagerSession().credential, credential);
    fs.writeFileSync(keyFile, dpapi.directSeal(credential));
    assert.equal(login.readExistingManagerSession().credential, credential);
  });
  await check('Wrong Windows key, corrupted ciphertext and untrusted authority fail closed', () => {
    chromium();
    assert.throws(() => login.readExistingManagerSession(profile, () => {throw Error('different user');}), e => e.code === 'windows_login_unavailable');
    const broken = fs.readFileSync(keyFile); broken[20] ^= 1; fs.writeFileSync(keyFile,broken);
    assert.throws(() => login.readExistingManagerSession(), e => e.code === 'windows_login_unavailable');
    writeSettings({...settings, server:'https://untrusted.invalid'});
    assert.throws(() => login.readExistingManagerSession(), e => e.code === 'authority_mismatch');
    writeSettings(settings); chromium();
  });
  await check('Login changing during DPAPI read is rejected', () => {
    assert.throws(() => login.readExistingManagerSession(profile, bytes => {
      const result = unprotect(bytes); writeSettings({...settings, updated:true}); return result;
    }), e => e.code === 'login_changed');
    writeSettings(settings);
  });
  await check('Redirected Manager profile rejected', () => {
    const junction = path.join(qa, 'redirected-manager');
    fs.symlinkSync(profile,junction,'junction');
    try {assert.throws(() => login.readExistingManagerSession(junction), e => e.code === 'profile_redirected');}
    finally {fs.unlinkSync(junction);}
  });
  await check('Large protected file survives a fresh process; tampering is detected', () => {
    const value = 'Synthetic registry row\n'.repeat(950000);
    const sealed = dpapi.directSeal(value), file = path.join(qa,'large-protected.bin');
    assert(sealed.length > 16 * 1024 * 1024 && dpapi.isDirect(sealed));
    fs.writeFileSync(file,sealed,{flush:true});
    const code = buildSync({entryPoints:[path.join(desk,'src/main/windows-dpapi.ts')],bundle:true,platform:'node',format:'cjs',write:false}).outputFiles[0].text;
    const script = path.join(qa,'read-protected.cjs');
    fs.writeFileSync(script, `process.resourcesPath=${JSON.stringify(process.resourcesPath)};\n` + code + `\nconst fs=require('node:fs'),crypto=require('node:crypto');process.stdout.write(crypto.createHash('sha256').update(module.exports.directOpen(fs.readFileSync(process.argv[2]))).digest('hex'));`);
    assert.equal(execFileSync(process.execPath,[script,file],{windowsHide:true,encoding:'utf8'}),crypto.createHash('sha256').update(value).digest('hex'));
    for (const offset of [sealed.length-20, sealed.length-1, Buffer.byteLength('CRM-DPAPI-AES-1\n')+4]) {
      const broken = Buffer.from(sealed); broken[offset] ^= 1; assert.throws(() => dpapi.directOpen(broken));
    }
    assert.throws(() => dpapi.directOpen(sealed.subarray(0,20)));
    fs.unlinkSync(file); fs.unlinkSync(script);
  });
  chromium();
  const session = load('crm-pilot-session');
  const employee = '11111111-1111-4111-8111-111111111111';
  reply = {ok:true, token:'crm1.' + 'A'.repeat(180), employee_uuid:employee, expires_at:Math.floor(Date.now()/1000)+1800, name:'Synthetic manager'};
  await check('Parallel session requests exchange once on pinned origin with redirects forbidden', async () => {
    assert.deepEqual(await Promise.all([session.pilotCredential(),session.pilotCredential()]),[reply.token,reply.token]);
    assert.equal(requests.length,1); const r = requests[0];
    assert.equal(r.url,login.PILOT_BASE+'/auth/exchange'); assert.equal(r.init.redirect,'error');
    assert.equal(r.init.headers['X-Pult-Token'],credential); assert.equal(r.init.body,undefined);
    assert.equal(await session.pilotCredential(employee),reply.token); assert.equal(requests.length,1);
  });
  const sessionFile = path.join(pilot,'crm-shared-session.bin');
  await check('Saved pilot session contains no original Manager credential', () => {
    const plaintext = dpapi.directOpen(fs.readFileSync(sessionFile));
    assert(!plaintext.includes(credential)); assert.equal(JSON.parse(plaintext).token,reply.token);
  });
  await check('Changed account cannot overwrite previous session or send its queued work', async () => {
    const before = hash(sessionFile); chromium();
    reply = {...reply,employee_uuid:'22222222-2222-4222-8222-222222222222'};
    await assert.rejects(() => session.pilotCredential(employee), e => e.status === 409);
    assert.equal(hash(sessionFile),before);
    assert.equal(await session.pilotCredential(undefined,true),reply.token);
  });
  await check('Expired saved session plus network failure preserves exact previous file', async () => {
    const saved = JSON.parse(dpapi.directOpen(fs.readFileSync(sessionFile)));
    fs.writeFileSync(sessionFile,dpapi.directSeal(JSON.stringify({...saved,expires_at:1})));
    const before = hash(sessionFile); networkError = Error('Synthetic unavailable');
    await assert.rejects(() => session.pilotCredential()); assert.equal(hash(sessionFile),before); networkError=null;
  });
  fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify({passed:checks.length,checks,realWindowsDPAPI:true,productionRequests:0},null,2));
  console.log(JSON.stringify({passed:checks.length,realWindowsDPAPI:true,productionRequests:0}));
})().catch(error => {console.error(error.stack); process.exitCode=1;});
