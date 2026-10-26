"""A stalled connection must yield the cached shell, without caching API data."""
import json
import pathlib
import subprocess


def test_navigation_timeout_failure_and_cache_boundaries():
    sw = pathlib.Path(__file__).resolve().parents[1] / 'server/miniapp/sw.js'
    script = r'''
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
async function run(mode,cached=true,path='/'){
 const handlers={},hit={cached:true},result={delays:[],writes:0,fetches:0};
 const ctx={URL,AbortController,Promise,
  self:{location:{origin:'https://example.test'},addEventListener:(k,f)=>handlers[k]=f},
  caches:{match:async()=>cached?hit:undefined,open:async()=>({put:async()=>result.writes++})},
  setTimeout:(fn,ms)=>{result.delays.push(ms);return setTimeout(fn,0)},clearTimeout,
  fetch:async(req,opts)=>{result.fetches++;assert.equal(opts.cache,'no-store');
   if(mode==='hang')return new Promise((_,reject)=>opts.signal.addEventListener('abort',()=>reject(Error('timeout'))));
   if(mode==='failure')throw Error('offline');
   return {ok:mode==='success',clone:()=>({fresh:true}),fresh:true};}
 };
 vm.runInNewContext(source,ctx);
 let response;handlers.fetch({request:{method:'GET',mode:'navigate',url:'https://example.test'+path},respondWith:p=>response=p});
 if(response){try{result.response=await response;}catch(e){result.rejected=true;}}
 return result;
}
(async()=>{
 const hung=await run('hang');assert.equal(hung.response.cached,true);assert.ok(hung.delays[0]<=5000);assert.equal(hung.writes,0);
 for(const mode of ['failure','server-error'])assert.equal((await run(mode)).response.cached,true);
 const fresh=await run('success');assert.equal(fresh.response.fresh,true);assert.equal(fresh.writes,1);
 assert.equal((await run('failure',false)).rejected,true);
 for(const path of ['/api/bootstrap','/admin/accounts.json','/version.json'])assert.equal((await run('success',true,path)).fetches,0);
 console.log(JSON.stringify({ok:true}));
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run(['node', '-e', script, str(sw)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ok']
