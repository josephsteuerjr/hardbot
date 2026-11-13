const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const {transformSync}=require('esbuild');
const file=path.join(__dirname,'../src/main/crm-active-time.ts');
const drafts=new Map(),sent=new Map();
const queue={draft:(owner,key,...value)=>{if(value.length)drafts.set(owner+key,structuredClone(value[0]));return structuredClone(drafts.get(owner+key));},
  enqueue:(owner,body,action)=>{const key=owner+body.operation_id;if(sent.has(key))assert.deepEqual(sent.get(key).body,body);sent.set(key,{owner,body:structuredClone(body),action});}};
const moduleStub={exports:{}};
vm.runInNewContext(transformSync(fs.readFileSync(file,'utf8'),{loader:'ts',format:'cjs'}).code,
  {module:moduleStub,exports:moduleStub.exports,require:id=>id==='./crm-queue'?queue:require(id)});
const {ActiveTime}=moduleStub.exports;
let current={owner:'server\nA',anchor_id:'anchor-A',installation_id:'installation',elapsed:0,kind:'active'};
const tracker=new ActiveTime(()=>current);tracker.tick();current={...current,elapsed:2000};tracker.tick();
current={...current,elapsed:4000};tracker.tick();
// A 5 minute timer gap must not become 5 active minutes.
current={...current,elapsed:304000};tracker.tick();
assert.equal([...sent.values()][0].body.end_ms,4000);
current={...current,elapsed:306000};tracker.tick();
current={...current,elapsed:308000,kind:null};tracker.tick();
assert.equal([...sent.values()].reduce((n,e)=>n+e.body.end_ms-e.body.start_ms,0),6000);
// Recover only the saved prefix after a crash, using exactly its original ID.
current={...current,elapsed:310000,kind:'active'};tracker.tick();current={...current,elapsed:312000};tracker.tick();
const saved=queue.draft(current.owner,'active-open');assert(saved);
const restarted=new ActiveTime(()=>current);restarted.tick();assert(sent.has(current.owner+saved.operation_id));
const oldOwner=current.owner;
current={...current,owner:'server\nB',anchor_id:'anchor-B',elapsed:0};restarted.tick();
current={...current,elapsed:2000};restarted.tick();restarted.stop();
assert([...sent.values()].filter(e=>e.owner===oldOwner).every(e=>e.body.anchor_id==='anchor-A'));
assert([...sent.values()].filter(e=>e.owner!==oldOwner).every(e=>e.body.anchor_id==='anchor-B'));
console.log(JSON.stringify({passed:true,checks:['monotonic duration','timer gap excluded','focus/lock excluded','durable prefix after crash','fixed replay ID','identity separation']}));
