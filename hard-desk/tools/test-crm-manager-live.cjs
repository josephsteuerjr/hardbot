// Four independent Electron profiles, one shared PostgreSQL / restricted role.
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process'),assert=require('node:assert/strict');
const root=process.env.HARD_CRM_QA_ROOT;assert(root&&process.env.HARD_CRM_QA_SHARED_ORIGIN&&process.env.HARD_CRM_QA_SECRET_FILE);
fs.mkdirSync(root,{recursive:true});const desk=path.resolve(__dirname,'..'),key=fs.readFileSync(process.env.HARD_CRM_QA_SECRET_FILE,'utf8').trim();
const electron=require('electron'),children=new Map(),ports=new Map(),measurements={};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label,limit=300){for(let i=0;i<limit;i++){if(await fn())return;await pause(100);}throw Error('Timeout: '+label);}
async function control(actor,route,body){const response=await fetch(`http://127.0.0.1:${ports.get(actor)}${route}`,{method:'POST',headers:{'Content-Type':'application/json','X-Control':key},body:JSON.stringify(body||{})});const r=await response.json();assert(r.ok,r.error);return r.result;}
const js=(actor,code)=>control(actor,'/eval',{code});
const click=(actor,s)=>js(actor,`document.querySelector(${JSON.stringify(s)}).click()`);
const exists=(actor,s)=>js(actor,`!!document.querySelector(${JSON.stringify(s)})`);
const fill=(actor,s,v)=>js(actor,`(()=>{const e=document.querySelector(${JSON.stringify(s)});e.value=${JSON.stringify(v)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
const api=async(actor,method,action,body,query)=>{const result=await js(actor,`window.crm.api(${JSON.stringify(method)},${JSON.stringify(action)},${JSON.stringify(body)},${JSON.stringify(query)})`);assert(result.ok,result.text);return result.data;};
async function launch(actor){
  const ready=path.join(root,actor+'.control.json');fs.rmSync(ready,{force:true});
  const log=fs.openSync(path.join(root,actor+'.log'),'a');
  const child=spawn(electron,[path.join(__dirname,'crm-manager-live-worker.cjs')],{cwd:desk,windowsHide:true,env:{...process.env,HARD_CRM_QA_ACTOR:actor},stdio:['ignore',log,log]});children.set(actor,child);
  await until(()=>fs.existsSync(ready),'worker '+actor);ports.set(actor,JSON.parse(fs.readFileSync(ready)).port);
}
async function openPerson(actor,person){
  await js(actor,`document.querySelector('[data-person="${person}"]')?.click()`);
  await until(()=>exists(actor,'[data-action=call]'),'card '+actor);
}
(async()=>{
 try{
  await Promise.all(['m1','m2','a','c'].map(launch));
  await until(()=>exists('m1','[data-managed-person]'),'m1 portfolio');await until(()=>exists('m2','[data-managed-person]'),'m2 portfolio');
  const initial=await api('m1','GET','managed-portfolio'),person=initial.people[0].person_uuid;
  const contracts=initial.people[0].contracts.map(c=>c.contract_uuid),staff=(await api('m1','GET','assignment-staff')).staff;
  const target=staff.find(p=>p.name==='New employee').employee_uuid;
  // Ordinary result before transfer, authored by A and requiring no task.
  const body={schema_version:1,operation_id:require('node:crypto').randomUUID(),kind:'call',contract_ids:contracts,payload:{values:{soft_comment:'Original author A'}},base_revisions:Object.fromEntries(contracts.map(id=>[id,{call:0}]))};
  await js('a',`window.crm.command(${JSON.stringify(body)})`);
  await until(()=>js('a',`window.crm.queue().then(r=>r.data.entries.some(e=>e.body.operation_id===${JSON.stringify(body.operation_id)}&&e.state==='accepted'))`),'A initial call');
  await openPerson('a',person);await click('a','[data-action=call]');await until(()=>exists('a','#v-soft_comment'),'A call editor');
  await fill('a','#v-soft_comment','Unsubmitted protected input A');
  // A second manager keeps an editor open while M1 transfers.
  await click('m2','[data-person-allocate]');await until(()=>exists('m2','#allocation-comment'),'M2 editor');
  await fill('m2','#allocation-comment','Keep manager input');await fill('m2','#allocation-employee',target);
  await until(()=>js('m2','!document.querySelector("#allocation-apply").disabled'),'M2 preview');
  const stale=await api('m2','POST','assignment-preview',{schema_version:1,action:'remove',employee_uuid:null,work_kinds:['call'],selection:{kind:'people',ids:[person],part:'whole',filters:{}},comment:''});
  await click('m1','[data-person-allocate]');await fill('m1','#allocation-employee',target);
  await until(()=>js('m1','!document.querySelector("#allocation-apply").disabled'),'M1 preview');
  const started=performance.now();await click('m1','#allocation-apply');
  await until(async()=>{
    const [m2,a,c]=await Promise.all([
      js('m2','document.querySelector(".managed-table").innerText.includes("New employee")'),
      js('a','document.querySelector("#action-form button[type=submit]").disabled'),
      exists('c','[data-person]')]);return m2&&a&&c;
  },'four actors live');measurements.four_actor_end_to_end_ms=Math.round(performance.now()-started);
  assert.equal(await js('a','document.querySelector("#v-soft_comment").value'),'Unsubmitted protected input A');
  assert.equal(await js('m2','document.querySelector("#allocation-comment").value'),'Keep manager input');
  const conflict=await js('m2',`window.crm.api('POST','assignment-commit',${JSON.stringify({schema_version:1,operation_id:require('node:crypto').randomUUID(),preview_id:stale.preview_id})})`);assert.equal(conflict.status,409);
  const aPortfolio=await api('a','GET','work-portfolio'),cPortfolio=await api('c','GET','work-portfolio');
  assert(aPortfolio.contracts.every(r=>r.work_kinds.includes('visit')&&!r.work_kinds.includes('call')));
  assert(cPortfolio.contracts.every(r=>r.work_kinds.includes('call')&&!r.work_kinds.includes('visit')));
  await control('a','/shot',{name:'protected-input-after-transfer'});await control('m2','/shot',{name:'manager-input-after-live'});
  // New assignee uses the normal call form, without a task.
  await openPerson('c',person);await click('c','[data-action=call]');await until(()=>exists('c','#v-soft_comment'),'C call form');
  await fill('c','#v-soft_comment','New author C');await js('c','document.querySelector("#action-form").requestSubmit()');
  await until(()=>js('c','window.crm.queue().then(r=>r.data.entries.some(e=>e.body.kind==="call"&&e.state==="accepted"))'),'C accepted call');
  const stats=await api('m1','GET','manager-statistics');
  assert.equal(stats.staff.find(p=>p.name==='Employee').facts.call,1);assert.equal(stats.staff.find(p=>p.name==='New employee').facts.call,1);assert.deepEqual(stats.debt,{RUB:'246.90'});
  // Reconnect M2 after missing an entire removal and reassignment.
  await control('m2','/network',{blocked:true});await pause(1500);
  await click('m1','#allocation-close');await click('m1','#managed-page');await click('m1','[data-allocate=remove]');await fill('m1','#allocation-work','both');
  await until(()=>js('m1','!document.querySelector("#allocation-apply").disabled'),'remove both preview');await click('m1','#allocation-apply');
  await until(()=>js('m1','document.querySelector("#allocation-status").textContent.includes("Применено сервером")'),'remove both');await click('m1','#allocation-close');
  assert.equal((await api('m1','GET','manager-statistics')).unassigned_slots,4);
  await click('m1','[data-allocate=assign]');await fill('m1','#allocation-work','both');await fill('m1','#allocation-employee',target);
  await until(()=>js('m1','!document.querySelector("#allocation-apply").disabled'),'reassign both preview');await click('m1','#allocation-apply');
  await until(()=>js('m1','document.querySelector("#allocation-status").textContent.includes("Применено сервером")'),'reassign');await click('m1','#allocation-close');
  await control('m2','/network',{blocked:false});
  await until(()=>js('m2','[...document.querySelectorAll(".managed-table [data-person-allocate]")].every(e=>e.innerText.includes("Выезды: New employee"))'),'M2 reconnect');
  // A restart uses the same protected profile/cursor and current server state.
  fs.writeFileSync(path.join(root,'CHECKPOINT.json'),JSON.stringify({passed_until:'reconnect',measurements},null,2));
  await control('c','/quit');await pause(500);
  if(children.get('c').exitCode===null&&children.get('c').signalCode===null)children.get('c').kill();
  await until(()=>children.get('c').exitCode!==null||children.get('c').signalCode!==null,'C exited');
  await launch('c');await until(()=>exists('c','[data-person]'),'C restart');
  const restarted=await api('c','GET','work-portfolio');assert(restarted.contracts.every(r=>r.work_kinds.includes('call')&&r.work_kinds.includes('visit')));
  await control('m1','/shot',{name:'shared-final-portfolio'});
  fs.writeFileSync(path.join(root,'RESULT.json'),JSON.stringify({passed:true,actors:4,one_backend:'postgresql',restricted_role:'crm_app',actual_main_preload:true,original_and_new_authorship:true,protected_input_preserved:true,manager_editor_preserved:true,independent_work_rights:true,stale_preview_conflict:true,remove_and_reassign:true,reconnect_and_restart:true,measurements},null,2));
 }catch(e){fs.writeFileSync(path.join(root,'FAIL.txt'),e.stack||String(e));for(const actor of ports.keys()){try{await control(actor,'/shot',{name:'failure'});fs.writeFileSync(path.join(root,actor+'.failure.txt'),await js(actor,'document.body.innerText'));}catch{}}process.exitCode=1;}
 finally{await Promise.allSettled([...ports.keys()].map(actor=>control(actor,'/quit')));for(const child of children.values())if(child.exitCode===null)child.kill();}
})();
