// Three independent Electron profiles, one shared PostgreSQL / restricted role.
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process'),assert=require('node:assert/strict');
const root=process.env.HARD_CRM_QA_ROOT;assert(root&&process.env.HARD_CRM_QA_SHARED_ORIGIN&&process.env.HARD_CRM_QA_SECRET_FILE);
fs.mkdirSync(root,{recursive:true});const desk=path.resolve(__dirname,'..'),key=fs.readFileSync(process.env.HARD_CRM_QA_SECRET_FILE,'utf8').trim();
const electron=require('electron'),children=new Map(),ports=new Map();
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
  await until(()=>fs.existsSync(ready),'worker '+actor,1200);ports.set(actor,JSON.parse(fs.readFileSync(ready)).port);
}
(async()=>{
 try{
  for(const actor of ['m1','m2','a'])await launch(actor);
  const first=await api('a','GET','work-portfolio'),contract=first.contracts[0].contract_uuid;
  const rows=await api('m1','GET','registry',undefined,{view:'all',ids:contract,limit:50});
  const external=rows.rows[0].external_id;
  for(const actor of ['m1','m2']){
   await fill(actor,'#registry-search',external);await click(actor,'#registry-find');
   await until(()=>exists(actor,`[data-row="${contract}"]`),'filtered registry');
  }
  const staff=(await api('m1','GET','assignment-staff')).staff;
  const original=staff.find(x=>x.name==='Employee'),target=staff.find(x=>x.name==='New employee');assert(original&&target);
  const durations=[];
  for(let i=0;i<20;i++){
   const employee=i%2?original:target;
   const p=await api('m1','POST','assignment-preview',{schema_version:1,action:'replace',employee_uuid:employee.employee_uuid,work_kinds:['call'],selection:{kind:'contracts',ids:[contract],part:'filtered',filters:{}},comment:'Windows live QA'});
   const result=await api('m1','POST','assignment-commit',{schema_version:1,operation_id:require('node:crypto').randomUUID(),preview_id:p.preview_id});assert.equal(result.status,'applied');
   const started=performance.now();
   await until(()=>js('m2',`document.querySelector('[data-row="${contract}"] [data-field="responsible_soft"] span')?.textContent===${JSON.stringify(employee.name)}`),'second manager repaint',120);
   durations.push(Math.round(performance.now()-started));
  }
  await fill('m2','#registry-employee',target.employee_uuid);
  await until(()=>js('m2','document.querySelectorAll(".registry-grid tbody tr").length===0'),'empty employee filter');
  for(const employee of [target,original]){
   const preview=await api('m1','POST','assignment-preview',{schema_version:1,action:'replace',employee_uuid:employee.employee_uuid,work_kinds:['call'],selection:{kind:'contracts',ids:[contract],part:'filtered',filters:{}},comment:'Empty filtered view QA'});
   await api('m1','POST','assignment-commit',{schema_version:1,operation_id:require('node:crypto').randomUUID(),preview_id:preview.preview_id});
   await until(()=>js('m2',`document.querySelectorAll('.registry-grid tbody tr').length===${employee===target?1:0}`),'empty view live transition');
  }
  await fill('m2','#registry-employee','');
  await until(()=>exists('m2',`[data-row="${contract}"]`),'filter restored');
  const pageTimes=[];
  for(let i=0;i<5;i++){
   await fill('m2','#registry-search','Scale');const start=performance.now();await click('m2','#registry-find');
   await until(()=>js('m2','document.querySelector("#registry-page").textContent.includes("50000")'),'50k rows');
   pageTimes.push(Math.round(performance.now()-start));assert.equal(await js('m2','document.querySelectorAll(".registry-grid tbody tr").length'),50);
   await fill('m2','#registry-search',external);await click('m2','#registry-find');await until(()=>exists('m2',`[data-row="${contract}"]`),'restore filtered page');
  }
  const p95=[...durations].sort((a,b)=>a-b)[Math.ceil(durations.length*.95)-1];
  await control('m2','/shot',{name:'final-registry'});
  fs.writeFileSync(path.join(root,'RESULT.json'),JSON.stringify({passed:p95<=10000,postgresql:true,restrictedRole:'crm_app',contracts:50002,actors:3,commitToPaintMs:durations,p95Ms:p95,pageToPaintMs:pageTimes,rowsInDOM:50,emptyFilteredViewUpdates:true},null,2));assert(p95<=10000,'Live fallback exceeded 10s');
 }catch(e){fs.writeFileSync(path.join(root,'FAIL.txt'),e.stack||String(e));process.exitCode=1;}
 finally{await Promise.allSettled([...ports.keys()].map(actor=>control(actor,'/quit')));for(const child of children.values())if(child.exitCode===null)child.kill();}
})();
