const assert=require('node:assert/strict');
const {buildSync}=require('esbuild');
const {join,resolve}=require('node:path');
const fs=require('node:fs');
const output=resolve(process.argv[2]);fs.mkdirSync(output,{recursive:true});
buildSync({entryPoints:[join(__dirname,'../src/main/crm-report-scheduler.ts')],outfile:join(output,'scheduler.cjs'),bundle:true,platform:'node',format:'cjs'});
const {ThreeHourReports}=require(join(output,'scheduler.cjs'));
(async()=>{
  let identity={owner:'authority-one',employee:'staff-one',allowed:true},stored={},sources=0,writes=0,fail=true;
  const deps={identity:()=>identity,load:key=>structuredClone(stored[key]),save:(key,value)=>stored[key]=structuredClone(value),
    source:async()=>({as_of:'2026-11-12T00:00:00Z',generation:++sources}),desktop:output,
    write:async(report,path)=>{writes++;assert.equal(report.generation,1);assert(path.includes('Трёхчасовые сводки'));if(fail)throw Error('Excel closed before acknowledgement');return {ok:true,data:{path,sha256:'synthetic',verified:true}};}};
  const at=new Date('2026-11-12T01:00:00Z');await new ThreeHourReports(deps).tick(at);
  assert.equal(sources,1);assert.equal(writes,1);assert(stored['authority-one'].error);
  fail=false;await new ThreeHourReports(deps).tick(at);
  assert.equal(sources,1);assert.equal(writes,2);assert(stored['authority-one'].receipt);
  await new ThreeHourReports(deps).tick(at);assert.equal(writes,2);
  let resolveSource;const gate=new Promise(resolve=>resolveSource=resolve);
  const late=new ThreeHourReports({...deps,source:()=>gate});
  const pending=late.tick(new Date('2026-11-12T04:00:00Z'));
  identity={owner:'authority-two',employee:'staff-two',allowed:true};resolveSource({generation:2});await pending;
  assert.equal(writes,2);assert(!stored['authority-two']);
  identity.allowed=false;await late.tick(at);assert.equal(writes,2);
  console.log('Three-hour scheduler: restart resumes exact captured source, one receipt per slot, identity custody PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
