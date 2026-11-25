const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm');
const esbuild=require('esbuild'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'hard-dual-routing-'));
let offline=false,requests=[];
const electron={app:{getPath:()=>temp,getVersion:()=> '3.2.16'},safeStorage:{isEncryptionAvailable:()=>true,encryptString:s=>Buffer.from(s),decryptString:b=>b.toString()},session:{fromPartition:()=>({setProxy:async()=>{},fetch:async(url,options)=>{
  requests.push({url,product:options.headers['X-Work-Product'],body:options.body});
  if(offline)throw Error('offline');
  return Response.json({ok:true,product:options.headers['X-Work-Product']});
}})}};
function load(name){const code=esbuild.buildSync({entryPoints:[path.join(__dirname,'../src/main',name+'.ts')],bundle:true,platform:'node',format:'cjs',external:['electron'],define:{__HARD_PRODUCT__:'"soft"'},write:false}).outputFiles[0].text;const m={exports:{}};vm.runInNewContext(code,{module:m,exports:m.exports,Buffer,Response,URLSearchParams,AbortController,setTimeout,clearTimeout,process:{env:{HARD_USER_DATA:temp}},require:n=>n==='electron'?electron:require(n)});return m.exports;}
(async()=>{try{
 const store=load('store'),net=load('net');store.writeSettings({fieldServer:'https://synthetic.invalid',fieldDevice:{telegram_id:810102}});store.writeFieldToken('same-existing-session');
 const keyBefore=fs.readFileSync(path.join(temp,'field.key'));
 assert.equal((await net.callField('GET','/api/person',{rep:'SAME',mode:'hard'})).data.product,'hard');
 assert.equal((await net.callField('GET','/api/person',{rep:'SAME',mode:'soft'})).data.product,'soft');
 assert.equal((await net.callField('GET','/api/bootstrap')).data.product,'soft');
 assert.equal((await net.callField('GET','/api/person',{rep:'OTHER',profile:'hard'})).data.product,'hard');
 offline=true;
 assert.equal((await net.callField('GET','/api/person',{rep:'SAME',mode:'hard'})).data.product,'hard');
 assert.equal((await net.callField('GET','/api/person',{rep:'SAME',mode:'soft'})).data.product,'soft');
 const op=(kind,id)=>({rep:'SYNTHETIC',kind,finish:true,op_id:id,occurred_at:1780000000123,action:'information',values:{note:'Synthetic'}});
 assert.equal((await net.callField('POST','/api/save',{mode:'soft'},op('call','soft-queued'))).data.queued,true);
 assert.equal((await net.callField('POST','/api/office/action',{mode:'hard'},op('office','hard-queued'))).data.queued,true);
 assert.equal(store.readCallQueue().entries.length,2);
 offline=false;requests=[];await load('net').flushCallQueue();
 const posted=requests.filter(r=>r.body).map(r=>({product:r.product,kind:JSON.parse(r.body).kind}));
 assert.deepEqual(posted,[{product:'soft',kind:'call'},{product:'hard',kind:'office'}]);
 assert.equal(store.readCallQueue().entries.length,0);
 assert.deepEqual(fs.readFileSync(path.join(temp,'field.key')),keyBefore);
 console.log('PASS: same Soft session opens Hard; profile caches separated; offline calls/office queue survive restart and retain own product; token unchanged.');
}finally{fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1;});
