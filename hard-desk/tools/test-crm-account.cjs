// Real DPAPI; only the HTTP authority and device-proof transport are synthetic.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),Module=require('node:module');
const {buildSync}=require('esbuild');
const desk=path.resolve(__dirname,'..'),home=path.resolve(process.argv[2]||'');
assert(process.platform==='win32'&&process.argv[2]&&home.startsWith('C:\\htarget\\crm-'));
fs.mkdirSync(home,{recursive:true});process.resourcesPath=path.join(desk,'installer/bin');
const token='synthetic-own-crm-credential-0123456789';let handler,requests=[];
const electron={app:{getPath:()=>home,getVersion:()=>'3.3.0'},net:{fetch:async(url,init)=>{
  requests.push({url,init});const value=await handler(url,init);
  return new Response(JSON.stringify(value.body),{status:value.status||200});
}}};
const source=fs.readFileSync(path.join(desk,'src/main/crm-account.ts'),'utf8').replace("from './net'","from 'crm-net-test'");
const code=buildSync({stdin:{contents:source,resolveDir:path.join(desk,'src/main'),loader:'ts'},bundle:true,platform:'node',format:'cjs',external:['electron','crm-net-test'],write:false}).outputFiles[0].text;
const file=path.join(desk,'out/qa-crm-account.cjs'),mod=new Module(file,module);mod.filename=file;mod.paths=Module._nodeModulePaths(path.dirname(file));
const requireReal=mod.require.bind(mod);mod.require=n=>n==='electron'?electron:n==='crm-net-test'?{desktopFingerprint:()=> 'a'.repeat(64),deviceName:()=> 'Synthetic PC',pcLoginProof:async()=>({challenge_id:'synthetic',signature:'synthetic'})}:requireReal(n);
mod._compile(code,file);const account=mod.exports;
(async()=>{
 handler=async()=>({body:{action:'ok_pending',token,uid:77101,role:'agent',username:'synthetic',must_change_password:true}});
 const pending=await account.accountAction('account-login',{username:'synthetic',password:'Temporary123'});
 assert.equal(pending.data.must_change_password,true);assert(!JSON.stringify(pending).includes(token));assert.throws(()=>account.ownLogin());
 assert.equal(JSON.parse(requests[0].init.body).audience,'crm-desktop');
 const bytes=fs.readFileSync(path.join(home,'crm-account.bin'));assert(!bytes.includes(Buffer.from(token)));assert(!bytes.includes(Buffer.from('Temporary123')));
 handler=async()=>({body:{ok:true}});await account.accountAction('account-password',{current:'Temporary123',new:'Personal456'});assert.throws(()=>account.ownLogin());
 handler=async()=>({body:{ok:true,subject:{actor_uid:999},me:{role:'employee'}}});await assert.rejects(account.accountAction('account-status',{}));
 handler=async()=>({body:{ok:true,subject:{actor_uid:77101},me:{role:'employee'}}});await account.accountAction('account-status',{});
 assert.equal(account.ownLogin().credential,token);assert.equal(account.ownLogin().kind,'employee');
 handler=async()=>({status:503,body:{error:'unavailable'}});await assert.rejects(account.accountAction('account-logout',{}));assert.equal(account.ownLogin().credential,token);
 handler=async()=>({body:{ok:true}});await account.accountAction('account-logout',{});assert.throws(()=>account.ownLogin());
 assert(requests.every(r=>r.url.startsWith('https://hard.rimborso.ru/api/account/')||r.url==='https://hard.rimborso.ru/api/crm-identity'));
 console.log(JSON.stringify({passed:true,realWindowsDPAPI:true,productionRequests:0}));
})().catch(e=>{console.error(e);process.exitCode=1;});
