// Network/cache concurrency against real transport and encrypted store; synthetic principals only.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm');
const esbuild=require('esbuild'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'hard-read-revalidation-'));
let fetcher;
const electron={app:{getPath:()=>temp},safeStorage:{isEncryptionAvailable:()=>true,encryptString:s=>Buffer.from(s),decryptString:b=>b.toString()},session:{fromPartition:()=>({setProxy:async()=>{},fetch:(...args)=>fetcher(...args)})}};
function load(name){const code=esbuild.buildSync({entryPoints:[path.join(__dirname,'../src/main',name+'.ts')],bundle:true,platform:'node',format:'cjs',external:['electron'],write:false}).outputFiles[0].text;const m={exports:{}};vm.runInNewContext(code,{module:m,exports:m.exports,Buffer,Response,URLSearchParams,AbortController,setTimeout,clearTimeout,process:{env:{HARD_USER_DATA:temp}},require:n=>n==='electron'?electron:require(n)});return m.exports;}
const tick=()=>new Promise(r=>setTimeout(r,0));
(async()=>{try{
 const store=load('store'),net=load('net');store.writeSettings({server:'https://synthetic.invalid',fieldServer:'https://synthetic.invalid'});store.writeToken('manager');store.writeFieldToken('staff');
 for(const field of [false,true]){
  const call=(...args)=>field?net.callField(...args):net.call(...args),route=field?'/api/person':'/api/manager/person';
  let release,reads=0;fetcher=async(url)=>{if(url.includes('MISSING'))return new Response('missing',{status:404});if(++reads===1)await new Promise(r=>release=r);return Response.json({ok:true,value:reads===1?'old':'fresh'});};
  const pending=call('GET',route,{rep:'VISIBLE'});await tick();await call('GET',route,{rep:'MISSING'});release();const result=await pending;
  assert.equal(result.ok,true,`${field?'staff':'manager'} legitimate read became a false login error`);assert.equal(reads,2);assert.equal(result.data.value,'fresh');
  // A new authenticated denial must win over the old successful payload.
  reads=0;fetcher=async(url)=>{if(url.includes('MISSING'))return new Response('missing',{status:404});if(++reads===1){await new Promise(r=>release=r);return Response.json({ok:true,private:'old'});}return new Response('forbidden',{status:403});};
  const denied=call('GET',route,{rep:'VISIBLE'});await tick();await call('GET',route,{rep:'MISSING'});release();assert.equal((await denied).status,403);assert.equal(reads,2);
  fetcher=async()=>{throw new Error('offline');};assert.equal((await call('GET',route,{rep:'VISIBLE'})).ok,false,'invalidated payload revived offline');
  // Do not reissue a write, or any request for a replaced principal.
  for(const method of ['GET','POST']){reads=0;fetcher=async url=>{if(url.includes('MISSING'))return new Response('missing',{status:404});reads++;await new Promise(r=>release=r);return Response.json({ok:true});};const pending=call(method,route,{rep:'VISIBLE'});await tick();if(method==='GET'){if(field)store.writeFieldToken('new-staff');else store.writeToken('new-manager');}else await call('GET',route,{rep:'MISSING'});release();assert.equal((await pending).ok,false);assert.equal(reads,1);}
  // Cache churn is bounded, rather than an unending read loop.
  reads=0;fetcher=async url=>{if(url.includes('MISSING'))return new Response('missing',{status:404});reads++;await call('GET',route,{rep:'MISSING'});return Response.json({ok:true});};assert.equal((await call('GET',route,{rep:'VISIBLE'})).ok,false);assert.equal(reads,2);
 }
 console.log('Read revalidation passed: manager/staff, 404 cache churn, current403, no stale offline grant, token replacement, no POST retry, bounded retry.');
}finally{fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1;});
