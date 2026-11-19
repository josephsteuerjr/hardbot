// Read-only reuse of synthetic installed profiles through real Windows DPAPI.
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const assert = require('node:assert/strict'), Module = require('node:module');
const {execFileSync} = require('node:child_process');
const {buildSync} = require('esbuild');
const desk = path.resolve(__dirname, '..'), qa = path.resolve(process.argv[2] || '');
assert(process.platform === 'win32' && qa.startsWith('C:\\htarget\\crm-'));
fs.mkdirSync(qa, {recursive:true});
process.resourcesPath = path.join(desk, 'installer/bin');
process.env.LOCALAPPDATA = path.join(qa, 'local-app-data');
process.env.HARD_CRM_SOURCE = path.dirname(desk);
const fakeElectron = {app:{getPath:()=>qa,getVersion:()=> '3.3.0-pilot.2'}, net:{}};
function load(name) {
  const filename = path.join(desk, 'out', 'qa-' + name + '.cjs');
  const code = buildSync({entryPoints:[path.join(desk,'src/main',name+'.ts')],bundle:true,
    platform:'node',format:'cjs',external:['electron'],write:false}).outputFiles[0].text;
  const mod = new Module(filename,module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const realRequire = mod.require.bind(mod); mod.require = name => name === 'electron' ? fakeElectron : realRequire(name);
  mod._compile(code,filename); return mod.exports;
}
const helper = path.join(process.resourcesPath,'process-check.exe');
const protect = value => execFileSync(helper,['seal'],{input:Buffer.from(value),windowsHide:true});
const login = load('crm-manager-login');
const credential = crypto.randomBytes(32).toString('base64url');
const profile = source => path.join(process.env.LOCALAPPDATA,'Hard',source,'data');
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const checks=[];
function check(name,fn){fn();checks.push(name);}
for(const source of ['soft','hard','analyst'])fs.mkdirSync(profile(source),{recursive:true});
for(const source of ['soft','hard']) {
  fs.writeFileSync(path.join(profile(source),'settings.json'),JSON.stringify({fieldServer:'https://hard.rimborso.ru',
    fieldDevice:{role:'untrusted-owner-role'}}));
  fs.writeFileSync(path.join(profile(source),'field.key'),protect(credential));
}
const analystFile=path.join(profile('analyst'),'backend-settings.json');
const analystRecord = token => ({active:'Production',profiles:{Production:{url:'https://hard.rimborso.ru',
  device_token:'dpapi-v1:'+protect(token).toString('base64')}}});
fs.writeFileSync(analystFile,JSON.stringify(analystRecord('account:'+credential)));
check('Employee profiles reuse protected field tokens without accepting local role',()=>{
  for(const source of ['soft','hard']) {
    const files=['settings.json','field.key'].map(name=>path.join(profile(source),name)),before=files.map(hash);
    const entry=login.readExistingLogin(source);
    assert.equal(entry.credential,credential);assert.equal(entry.kind,'employee');
    assert.deepEqual(files.map(hash),before);
  }
  assert.notEqual(login.entryFingerprint('soft'),login.entryFingerprint('hard'));
});
check('Analyst account session uses active profile and leaves file unchanged',()=>{
  const before=hash(analystFile),entry=login.readExistingLogin('analyst');
  assert.equal(entry.kind,'analyst');assert.equal(entry.credential,credential);
  assert.equal(hash(analystFile),before);
});
check('Legacy analyst entry carries the existing backend device fingerprint',()=>{
  fs.writeFileSync(analystFile,JSON.stringify(analystRecord(credential)));
  const before=hash(analystFile),entry=login.readExistingLogin('analyst');
  assert.equal(entry.kind,'analyst-device');assert.match(entry.deviceFingerprint,/^[a-f0-9]{64}$/);
  assert.equal(hash(analystFile),before);
});
check('Untrusted active analyst origin is rejected despite a trusted mirror',()=>{
  const record=analystRecord('account:'+credential);record.url='https://hard.rimborso.ru';
  record.profiles.Production.url='https://untrusted.invalid';
  fs.writeFileSync(analystFile,JSON.stringify(record));
  assert.throws(()=>login.readExistingLogin('analyst'),error=>error.code==='authority_mismatch');
  fs.writeFileSync(analystFile,JSON.stringify(analystRecord('account:'+credential)));
});
check('Entry discovery exposes only labels and availability',()=>{
  const entries=login.existingLogins();
  assert.equal(entries.filter(entry=>entry.available).length,3);
  assert(!JSON.stringify(entries).includes(credential));
  assert(entries.every(entry=>Object.keys(entry).sort().join(',')==='available,label,source'));
});
(async()=>{
  const requests=[];
  let remoteEmployee='11111111-1111-4111-8111-111111111111',unavailable=false;
  fakeElectron.net.fetch=async(url,init)=>{
    requests.push({url,init});if(unavailable)throw Error('Synthetic offline');
    return new Response(JSON.stringify({ok:true,token:'crm1.'+'C'.repeat(180),
      employee_uuid:remoteEmployee,expires_at:Math.floor(Date.now()/1000)+1800,name:'Synthetic'}),
      {status:200,headers:{'X-CRM-Namespace':'crm-shared-pilot-v1'}});
  };
  const sessions=load('crm-pilot-session');
  await Promise.all([sessions.pilotCredential(undefined,false,'soft'),sessions.pilotCredential(undefined,false,'soft')]);
  assert.equal(requests.length,1);assert.equal(requests[0].init.headers['X-App-Token'],credential);
  assert.equal(requests[0].init.headers['X-Pult-Token'],undefined);
  assert.equal(requests[0].url,login.PILOT_BASE+'/auth/exchange');
  assert.equal(requests[0].init.redirect,'error');
  checks.push('Parallel employee connection exchanges once using the pinned employee gate');
  const sessionFile=path.join(qa,'crm-shared-session.bin'),before=hash(sessionFile);
  remoteEmployee='22222222-2222-4222-8222-222222222222';
  await assert.rejects(()=>sessions.pilotCredential('11111111-1111-4111-8111-111111111111',true,'analyst'),error=>error.status===409);
  assert.equal(hash(sessionFile),before);
  await sessions.pilotCredential(undefined,true,'analyst');
  assert.equal(requests.at(-1).init.headers['X-CRM-Entry'],'analyst');
  assert.equal(requests.at(-1).init.headers['X-Pult-Token'],credential);
  checks.push('Changing to Analyst cannot send another employee queue or overwrite its saved session on mismatch');
  const savedHash=hash(sessionFile);unavailable=true;
  await assert.rejects(()=>sessions.pilotCredential(undefined,true,'analyst'));
  assert.equal(hash(sessionFile),savedHash);
  checks.push('Failed reconnect preserves protected session');
  check('Active checkpoints stay encrypted and recover after restart without a helper per write',()=>{
    const children=require('node:child_process');
    const native=children.execFileSync, queue=load('crm-queue');
    const owner='synthetic-active-checkpoint', value={end_ms:2000};
    const name='draft-'+crypto.createHash('sha256').update(owner+'\nactive-open').digest('hex')+'.bin';
    const target=path.join(qa,'crm',name);
    const calls=[];
    children.execFileSync=(file,args,...options)=>{
      if(path.basename(file)==='process-check.exe')calls.push(args[0]);
      return native(file,args,...options);
    };
    try {
      assert.deepEqual(queue.draft(owner,'active-open',value),value);
      const first=hash(target);
      assert.deepEqual(queue.draft(owner,'active-open',value),value);
      assert.notEqual(hash(target),first);
      assert.deepEqual(calls,['seal']);
      assert.deepEqual(load('crm-queue').draft(owner,'active-open'),value);
      assert.deepEqual(calls,['seal','unseal']);
      fs.writeFileSync(target,Buffer.from('CRM-DPAPI-1\ncorrupt'));
      assert.throws(()=>queue.draft(owner,'active-open'),/Windows/);
    }finally{children.execFileSync=native;}
  });
  const result={passed:checks.length,checks,realWindowsDPAPI:true,productionRequests:0};
  fs.writeFileSync(path.join(qa,'RESULT.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
