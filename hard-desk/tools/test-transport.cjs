const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const esbuild = require('esbuild');
function load(name, overrides = {}) {
  const code = esbuild.buildSync({entryPoints:[path.join(__dirname,'../src/main',name+'.ts')],
    bundle:true,platform:'node',format:'cjs',external:['electron'],write:false}).outputFiles[0].text;
  const m={exports:{}};
  vm.runInNewContext(code,{module:m,exports:m.exports,require,Buffer,Response,AbortController,
    setTimeout,clearTimeout,fetch,process,__dirname:path.join(__dirname,'../out'),...overrides});
  return m.exports;
}
(async()=>{
  const {fetchBuffered,localPath,retryLocalRead,trustedUiUrl}=load('transport',{URL});
  assert.equal(trustedUiUrl('hard://app/index.html?mode=field'),true);
  for(const url of ['https://app/index.html','hard://other/index.html','hard://app/tray.html','hard://user@app/index.html','hard://app:123/index.html']) assert.equal(trustedUiUrl(url),false);
  assert.equal(localPath('/mail/send'),true);
  for(const p of ['@other.invalid/','//other.invalid/','/../admin','/state?x=1','/state#x','/state\\other']) assert.equal(localPath(p),false);
  for(const p of ['/bind','/mail/send','/reg/import','/enr/run','/profile']) assert.equal(retryLocalRead(p,{}),false);
  assert.equal(retryLocalRead('/admin',{method:'POST'}),false);
  assert.equal(retryLocalRead('/admin',{method:'GET'}),true);
  const seen=[];
  const server=http.createServer((req,res)=>{
    seen.push(req.url);
    if(req.url==='/redirect'){res.writeHead(302,{Location:'/target'});res.end();}
    else if(req.url==='/headers') {} // blackhole before response headers
    else if(req.url==='/body'){res.writeHead(200,{'Content-Type':'application/json'});res.flushHeaders();res.write('{');}
    else {res.end('{"ok":true}');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  try{
    for(const p of ['/headers','/body']){
      const start=Date.now();
      await assert.rejects(()=>fetchBuffered(fetch,base+p,{},100));
      assert.ok(Date.now()-start<2000);
    }
    await assert.rejects(()=>fetchBuffered(fetch,base+'/redirect',{headers:{'X-App-Token':'synthetic'}},1000));
    assert.equal(seen.includes('/target'),false);
    assert.equal((await (await fetchBuffered(fetch,base+'/ok',{})).json()).ok,true);
  }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}

  let requests=0;
  const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();child.kill=()=>{};
  const spawn=()=>{setTimeout(()=>child.stdout.emit('data','HARD-BACKEND 43210 synthetic-secret\n'),0);return child;};
  const electron={app:{isPackaged:false,on:()=>{},getPath:()=>os.tmpdir()}};
  const local=load('local',{process:{...process,resourcesPath:os.tmpdir()},
    fetch:async()=>{requests++;if(requests%2===1)throw new Error('Response lost after commit');return new Response('{"ok":true,"data":{}}')},
    require:n=>n==='electron'?electron:n==='node:child_process'?{spawn}:require(n)}).local;
  assert.equal((await local('@attacker.invalid/')).ok,false);
  assert.equal(requests,0);
  const write=await local('/mail/send',{book:'synthetic'});
  assert.equal(write.ok,false);assert.match(write.error,/могла выполниться/);assert.equal(requests,1);
  requests=0;
  assert.equal((await local('/state')).ok,true);assert.equal(requests,2);
  const starting=new EventEmitter();starting.stdout=new EventEmitter();starting.stderr=new EventEmitter();
  let killed=false;
  starting.kill=()=>{killed=true;starting.emit('exit',1)};
  const backend=load('local',{process:{...process,resourcesPath:os.tmpdir()},
    require:n=>n==='electron'?electron:n==='node:child_process'?{spawn:()=>starting}:require(n)});
  const startup=backend.startBackend(()=>{});
  backend.stopBackend();await assert.rejects(()=>startup);
  assert.equal(killed,true); // child is owned even before its ready handshake
  console.log('Transport: header/body blackholes, redirects, local URL injection, at-most-once writes and explicit read retries passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
