import * as vault from './store';
import {mountFieldOffline} from './field-offline';
import {mountFieldWorkspace} from './field-workspace';
import {mountConnection} from './connection';
import {MobileActivity} from './activity';
import {grantLabel} from '../workspace/grant-label';
type Reply={ok:boolean;status:number;data?:any;text?:string;stale?:string;cancelled?:boolean;retryAt?:number};
type Entry={action:string;body:any;queued_at:string;state:'pending'|'attention'|'accepted';result?:any;error?:string;code?:string;sha256?:string;grant_id?:string|null;actor_uid?:string|number;access_model?:number;recover_legacy_receipt?:boolean};
const listeners=new Set<(event:any)=>void>();
const canonical=(v:any):string=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const background=(work:Promise<any>)=>{void work.catch(()=>emit({type:'storage-error',text:'Не удалось сохранить данные на телефоне. Оставьте окно открытым и повторите действие.'}));};
let current:any=null,csrf='',offline=false,flushing:Promise<any>|null=null,liveTimer:ReturnType<typeof setTimeout>,polling=false;
let expiryTimer:ReturnType<typeof setTimeout>;
let cacheTimer:ReturnType<typeof setTimeout>;
let cacheReady:Promise<void>|undefined;
let lastTouch=0;
let queueRetryAt=0;
let initializing:Promise<Reply>|null=null, legacyTried=false;
let navigationTarget='';
let initialPerson='';
const grantId=(context=current?.context)=>context?.access_model===2?context.grant_id||null:null;
const grantScope=(context=current?.context)=>JSON.stringify([context?.actor_uid??null,grantId(context),context?.policy_revision||0]);
const operationScope=(context=current?.context)=>JSON.stringify([context?.actor_uid??null,grantId(context)]);
const hasModule=(id:string,context=current?.context)=>{
  const visit=(nodes:any[]):boolean=>nodes.some(node=>node?.id===id||visit(node?.children||[]));
  return Array.isArray(context?.workspace?.tree)?visit(context.workspace.tree):context?.access_model!==2;
};
const defaultSection=()=>current?.context?.workspace?.defaultModule||current?.context?.workspace?.default_module||(current?.context?.role==='analyst'?'registry':'portfolio');
const serverRole=(context=current?.context)=>context?.access_model===2?context.grants?.find((grant:any)=>grant.grant_id===grantId(context)&&String(grant.actor_uid)===String(context.actor_uid))?.role:context?.role;
const fieldAllowed=()=>['employee','owner'].includes(serverRole())&&hasModule('field')&&(current?.context?.capabilities||[]).includes('visit');
let connectionHost:HTMLElement|undefined,connectionResponse:Reply|undefined;
function renderGrantPicker(){
  const host=connectionHost;host?.querySelector('[data-grant-picker]')?.remove();if(!host)return;
  const connected=!!current?.owner;
  const entryRole=current?.context?.entry_role;
  const allowedRoles=!entryRole?['employee','courier','manager','analyst','observer','owner','logistic']:
    entryRole==='analyst'?['analyst']:['employee','agent'].includes(entryRole)?['employee','courier']:
    ['observer','security'].includes(entryRole)?['observer']:['manager','owner'];
  const grants=connected?(current.context?.grants||[]).filter((grant:any)=>current.context?.account_bound||allowedRoles.includes(grant.role)):connectionResponse?.data?.details?.grants;
  if(!Array.isArray(grants)||grants.length<(connected?2:1)||!connected&&connectionResponse?.data?.error!=='grant_required')return;
  const form=document.createElement('form');form.dataset.grantPicker='';form.className='mobile-login';
  const title=document.createElement('h2');title.textContent='Полномочие';const label=document.createElement('label');label.textContent='Рабочая область';
  const select=document.createElement('select');select.name='grant_id';select.required=true;
  for(const grant of grants){const option=document.createElement('option');option.value=grant.grant_id;option.textContent=grantLabel(grant);option.selected=connected&&grant.grant_id===grantId();select.append(option);}
  const button=document.createElement('button');button.type='submit';button.className='primary';button.textContent=connected?'Применить':'Выбрать и войти';
  label.append(select);form.append(title,label,button);host.querySelector('.mobile-connection')?.prepend(form);
  form.onsubmit=event=>{event.preventDefault();button.disabled=true;void (async()=>{
    const grant_id=select.value;let response:Reply;
    if(connected||connectionResponse?.data?.retained_session)response=await bridge.connect({action:'grant',grant_id});
    else{
      const login=host.querySelector<HTMLFormElement>('#mobile-login');if(!login)throw Error('Сначала введите логин и пароль.');
      const data=new FormData(login);response=await bridge.connect({action:'login',username:data.get('username'),password:data.get('password'),grant_id});
    }
    if(!response.ok){button.disabled=false;const feedback=host.querySelector<HTMLElement>('#mobile-connect-result');if(feedback)feedback.textContent=response.text||'Полномочие не подтверждено.';}
  })().catch(error=>{button.disabled=false;const feedback=host.querySelector<HTMLElement>('#mobile-connect-result');if(feedback)feedback.textContent=(error as Error).message;});};
}
let navigating=false,routeHandler:((route:any)=>Promise<void>)|undefined;
window.addEventListener('popstate',()=>{
  const route=history.state?.crmMobile;
  if(!route||route.owner!==current?.owner||!routeHandler)return;
  navigating=true;background(routeHandler(route).finally(()=>{navigating=false;}));
});
const deviceScope='device';
const channel=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('crm-mobile-events'):null;
const activity=new MobileActivity(api,async(action,body,owner)=>persistEntry(owner,action,body),()=>emit({type:'storage-error',text:'Не удалось сохранить активный интервал. Оставьте окно открытым и повторите подключение.'}));
const emit=(event:any,broadcast=false)=>{for(const fn of listeners)fn(event);if(broadcast)channel?.postMessage(event);};
channel?.addEventListener('message',event=>{
  if(event.data?.type==='workspace-revoked'){
    if(event.data.owner===current?.owner)closeWorkspace(event.data.owner);
  }else if(event.data?.owner===current?.owner)emit(event.data);
  else if(event.data?.type==='identity')background(initialize().then(()=>emit({type:'identity'})));
});
async function checkReadCache(touch:boolean){
  const state=await vault.maintainReadCache(touch);
  clearTimeout(cacheTimer);
  if(state.deadline)cacheTimer=setTimeout(()=>background(checkReadCache(false)),Math.max(0,state.deadline-Date.now()));
  if(state.cleared&&current?.owner){
    navigationTarget='connection';
    emit({type:'identity',owner:current.owner},true);
  }
}
function markReadCacheUse(event:Event){
  if(document.hidden||!event.isTrusted)return;
  const now=Date.now();
  if(now>=lastTouch&&now-lastTouch<60_000)return;
  lastTouch=now;
  background(checkReadCache(true));
}
for(const event of ['pointerdown','keydown','wheel'])
  window.addEventListener(event,markReadCacheUse,{capture:true,passive:true});
function expireSession(){
  const expired=current;
  if(!expired?.owner||expired.expires_at>Date.now())return;
  current=null;csrf='';clearTimeout(expiryTimer);
  background(vault.update(deviceScope,'last-state',saved=>saved?.owner===expired.owner&&saved.expires_at===expired.expires_at?null:saved));
  background(activity.stop());queueMicrotask(()=>emit({type:'identity'}));
}
function scheduleSessionExpiry(){
  clearTimeout(expiryTimer);
  if(!current?.owner)return;
  const remaining=current.expires_at-Date.now();
  if(remaining<=0){expireSession();return;}
  expiryTimer=setTimeout(scheduleSessionExpiry,Math.min(remaining,2_147_483_647));
}
function authority(expected?:string){expireSession();if(!current?.owner||expected&&expected!==current.owner)throw Error('Вход изменился. Ввод сохранён у исходного сотрудника.');return current.owner;}
async function revoke(status:number,expected=current?.owner){if(status===401&&expected&&current?.owner===expected){await vault.update(deviceScope,'last-state',saved=>saved?.owner===expected?null:saved);if(current?.owner!==expected)return;current=null;background(activity.stop());emit({type:'identity'});}}
function closeWorkspace(owner:string,duringBoot=false){
  if(current?.owner!==owner)return;
  current=null;clearTimeout(expiryTimer);clearTimeout(liveTimer);navigationTarget='connection';
  background(activity.stop());
  if(!duringBoot)emit({type:'identity'});
}
async function revokeWorkspace(owner:string,duringBoot:boolean){
  closeWorkspace(owner,duringBoot);
  const revokedAt=Date.now();
  await vault.update(owner,'workspace-revoked',prior=>Math.max(prior||0,revokedAt));
  await vault.update(deviceScope,'last-state',saved=>saved?.owner===owner?null:saved);
  await vault.clearReads(owner);
  channel?.postMessage({type:'workspace-revoked',owner});
}
async function request(path:string,body?:any):Promise<Reply>{
  const startedOwner=current?.owner;
  if(offline||!navigator.onLine)return {ok:false,status:0,text:'Нет связи. Неотправленные действия сохранены на телефоне.'};
  try{
    const response=await fetch('./'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-CRM-CSRF':csrf},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(120000)});
    const data=await response.json();
    if(current?.owner!==startedOwner)return {ok:false,status:401,text:'Вход изменился. Исходная работа сохранена.'};
    const revokedOwner=data.revoked_owner||
      (['grant_revoked','grant_required'].includes(data.error)&&
       (path==='boot'||path==='api'&&body?.action!=='grant')?startedOwner:null);
    if(typeof revokedOwner==='string'&&revokedOwner)await revokeWorkspace(revokedOwner,path==='boot');
    await revoke(response.status,startedOwner);
    if(response.status===403&&data.error==='csrf'){csrf='';return {ok:false,status:0,text:'Подключение обновляется. Исходная операция остаётся в очереди.'};}
    const text=typeof data.text==='string'&&!/^[a-z0-9_-]+$/i.test(data.text)?data.text:undefined;
    const delay=response.headers.get('Retry-After');
    const retryAt=delay?(/^\d+$/.test(delay)?Date.now()+Number(delay)*1000:Date.parse(delay)):undefined;
    return {ok:response.ok,status:response.status,data,text,retryAt};
  }catch{return {ok:false,status:0,text:body===undefined?'Не удалось подключиться.':'Ответ не получен. Исходная операция сохранена на телефоне.'};}
}
async function initialize(){
  initializing ??= loadSession().finally(()=>{initializing=null;});
  return initializing;
}
async function carryLegacyWork(previous:any,next:any){
  if(!previous?.owner||!next?.owner||previous.owner===next.owner||previous.context?.access_model===2)return;
  const prior=previous.context,context=next.context;
  if(prior?.actor_uid===undefined||String(prior.actor_uid)!==String(context.actor_uid)||prior.employee_uuid!==context.employee_uuid)return;
  if(context.access_model===2){
    const sameRole=context.grants?.filter((grant:any)=>grant.role===prior.role)||[];
    if(!sameRole.some((grant:any)=>grant.grant_id===context.grant_id&&(grant.is_default||sameRole.length===1)))return;
  }
  if(await vault.get(next.owner,'work-transfer'))return;
  const old:Entry[]=await vault.get(previous.owner,'queue')||[];
  if(old.some(entry=>entry.actor_uid!==undefined&&String(entry.actor_uid)!==String(context.actor_uid)))return;
  await vault.update(next.owner,'queue',(entries:Entry[]|null)=>{
    const result=new Map((entries||[]).map(entry=>[entry.body.operation_id,entry]));
    for(const entry of old){
      const current=result.get(entry.body.operation_id);
      if(current){
        if(current.action!==entry.action||canonical(current.body)!==canonical(entry.body))
          throw Error('Записи с одним номером содержат разные данные. Исходные записи сохранены.');
        continue;
      }
      result.set(entry.body.operation_id,{...entry,actor_uid:context.actor_uid,access_model:context.access_model||1,
        grant_id:context.grant_id||null,recover_legacy_receipt:context.access_model===2&&entry.action==='command'});
    }
    return [...result.values()];
  });
  await vault.put(next.owner,'work-transfer',{source:previous.owner});
}

async function loadSession():Promise<Reply>{
  const previousOwner=current?.owner,previousScope=grantScope();
  cacheReady??=checkReadCache(!document.hidden).catch(error=>{cacheReady=undefined;throw error;});
  await cacheReady;
  if(!await vault.get(deviceScope,'installation')){
    await vault.update(deviceScope,'installation',value=>value||crypto.randomUUID());
  }
  const savedBefore=await vault.get(deviceScope,'last-state');
  const checkedAt=Date.now();
  let response=await request('boot');
  if(response.ok){
    csrf=response.data.csrf;
    if(!legacyTried&&!response.data.synthetic&&!response.data.session&&!response.data.pending){
      try{
        const credential=localStorage.getItem('app_token'),entry_role=localStorage.getItem('account_role');
        if(credential&&entry_role){
          const resumed=await request('resume',{credential,entry_role});
          legacyTried=resumed.ok||[400,401,403,410].includes(resumed.status);
          if(resumed.ok||resumed.status===409)response=await request('boot');
        }
      }catch{/* Session access stays available when local storage is restricted. */}
    }
  }
  if(response.data?.retained_session)csrf=response.data.csrf;
  if(!response.ok&&savedBefore?.owner&&!response.data?.revoked_owner&&
    ['grant_revoked','grant_required'].includes(response.data?.error)){
    await revokeWorkspace(savedBefore.owner,true);
  }
  if(response.ok){
    csrf=response.data.csrf;
    const next=response.data.session||null;
    if(next?.owner&&await vault.update(next.owner,'workspace-revoked',value=>value>=checkedAt?value:null)){
      closeWorkspace(next.owner,true);
      return {ok:false,status:403,data:{error:'grant_revoked'},text:'Обновите подтверждение рабочего места.'};
    }
    await carryLegacyWork(current||savedBefore,next);
    current=next;
    await vault.put(deviceScope,'last-state',current);
  }
  else if(response.status===0||response.status>=500){
    const saved=await vault.get(deviceScope,'last-state');
    current=saved&&saved.expires_at>Date.now()&&saved.context?.source!=='legacy-analyst-web'&&
      !await vault.get(saved.owner,'workspace-revoked')?saved:null;
  }else if([401,410].includes(response.status)&&savedBefore?.owner){
    await vault.update(deviceScope,'last-state',saved=>
      saved?.owner===savedBefore.owner&&saved.expires_at===savedBefore.expires_at?null:saved);
  }
  if(current?.owner&&current.owner===previousOwner&&grantScope()!==previousScope)await vault.clearReads(current.owner);
  scheduleSessionExpiry();
  if(current?.owner&&response.ok&&!document.hidden&&current.context?.source!=='legacy-analyst-web')
    await activity.start(current.owner,await vault.get(deviceScope,'installation'));
  else if(!current||current.context?.source==='legacy-analyst-web')await activity.stop();
  startPolling();return response;
}
async function api(method:string,action:string,body?:any,query?:any,recoverLegacyReceipt=false):Promise<Reply>{
  const owner=authority(),requestedGrant=grantId(),scope=current.context?.access_model===2?grantScope()+':':'',operation=current.context?.access_model===2?operationScope()+':':'',key='read:'+scope+action+':'+JSON.stringify(query||{});
  let exportKey='';
  if(method==='POST'&&action==='export'){
    const {operation_id,...selection}=body;exportKey='export-intent:'+operation+canonical(selection);
    const prior=await vault.get(owner,exportKey);if(prior)body=prior;else await vault.put(owner,exportKey,body);
  }
  const response=await request('api',{method,action,body:body||{},query:query||{},...(requestedGrant!==null?{grant_id:requestedGrant}:{}),
    ...(recoverLegacyReceipt?{recover_legacy_receipt:true}:{})});
  if(exportKey){if(response.ok)await vault.put(owner,'export-key:'+operation+response.data.manifest.export_id,exportKey);else if([400,409,422].includes(response.status))await vault.put(owner,exportKey,null);}
  if(current?.owner!==owner)return {ok:false,status:401,text:'Вход изменился.'};
  if(method==='GET'&&response.ok&&action!=='operator-status')await vault.put(owner,key,{at:new Date().toISOString(),data:response.data});
  if(method==='GET'&&(response.status===0||response.status>=500)&&!['operator-status','changes','assignment-job','assignment-receipt'].includes(action)){
    const saved=await vault.get(owner,key);if(saved)return {ok:true,status:200,data:saved.data,stale:saved.at};
  }
  return response;
}
async function enqueue(action:string,body:any,expected?:string,sha256?:string){
  const owner=authority(expected);return persistEntry(owner,action,body,sha256);
}
const entryMatchesGrant=(entry:Entry)=>{
  const context=current?.context;if(!context)return false;
  if(context.access_model===2)return entry.access_model===2&&entry.grant_id===grantId(context)&&String(entry.actor_uid)===String(context.actor_uid);
  return entry.access_model!==2&&(entry.actor_uid===undefined||String(entry.actor_uid)===String(context.actor_uid));
};
async function persistEntry(owner:string,action:string,body:any,sha256?:string){
  if(!body?.operation_id)throw Error('Не указан номер операции.');
  const binding={grant_id:grantId(),actor_uid:current?.context?.actor_uid,access_model:current?.context?.access_model===2?2:1};
  await vault.update(owner,'queue',(rows:Entry[]|null)=>{
    rows||=[];const prior=rows.find(r=>r.body.operation_id===body.operation_id);
    if(prior){if(canonical(prior.body)!==canonical(body)||prior.action!==action)throw Error('Номер операции уже связан с другими данными.');return rows;}
    return [...rows,{action,body,queued_at:new Date().toISOString(),state:'pending',sha256,...binding}];
  });
  if(current?.owner===owner){emit({type:'queue',owner},true);background(flush());}return {ok:true,status:202,data:{operation_id:body.operation_id,state:'pending'}};
}
async function command(body:any,expected?:string){
  const owner=authority(expected),entries:Entry[]=await vault.get(owner,'queue')||[],old=entries.find(e=>e.body.operation_id===body.operation_id);
  if(old){const original={...old.body};if(!body.clock&&!body.occurred_at){delete original.clock;delete original.occurred_at;}if(canonical(original)!==canonical(body))throw Error('Номер операции уже связан с другими данными.');background(flush());return {ok:true,status:202,data:{operation_id:body.operation_id}};}
  const clock=activity.clock();return enqueue('command',body.clock||body.occurred_at?body:clock?{...body,clock}:{...body,occurred_at:new Date().toISOString()},owner);
}
async function flush(){
  if(flushing)return flushing;
  const run=async()=>{
    expireSession();
    if(!current?.owner||offline||!navigator.onLine||queueRetryAt>Date.now())return;
    const owner=authority();if(!csrf){await initialize();if(current?.owner!==owner||!csrf)return;}
    const rows:Entry[]=await vault.get(owner,'queue')||[];
    for(const entry of rows.filter(e=>e.state==='pending')){
      if(current?.owner!==owner)break;
      if(!entryMatchesGrant(entry))continue;
      if(entry.action==='command'&&entry.body.kind==='visit'&&entry.body.payload?.field_visit?.photos?.length){
        const ids=entry.body.payload.field_visit.photos;
        const latest:Entry[]=await vault.get(owner,'queue')||[];
        if(latest.some(e=>e.action==='document' && ids.includes(e.body.document_id) && e.state!=='accepted'))continue;
      }
      if(entry.action==='command'&&entry.body.kind==='document'){
        const latest:Entry[]=await vault.get(owner,'queue')||[];
        if(latest.some(e=>e.action==='document'&&e.body.document_id===entry.body.payload.document_id&&e.state!=='accepted'))continue;
      }
      if(!entryMatchesGrant(entry))continue;
      const response=await api('POST',entry.action,entry.body,undefined,entry.recover_legacy_receipt===true);
      if(!response.status||response.status>=500||response.data?.error==='grant_changed'||[401,408,425,429].includes(response.status)){
        if([408,425,429,503].includes(response.status))queueRetryAt=Math.max(Date.now()+5000,response.retryAt||0);
        break;
      }
      if(response.ok&&entry.action==='command'&&
         (response.data?.status!=='accepted'||response.data.operation_id!==entry.body.operation_id||!response.data.event_id))break;
      if(response.ok&&entry.action==='document'){
        const digest=entry.sha256||await bytesSha256(Uint8Array.from(atob(entry.body.content_base64),c=>c.charCodeAt(0)));
        if(response.data?.document_id!==entry.body.document_id||response.data.contract_uuid!==entry.body.contract_uuid||
           response.data.sha256!==digest)break;
      }
      await vault.update(owner,'queue',(latest:Entry[])=>latest.map(e=>e.body.operation_id!==entry.body.operation_id?e:{...e,state:response.ok?'accepted':'attention',result:response.data,error:response.ok?undefined:response.text,code:response.data?.error}));
      emit({type:'queue',owner},true);
    }
    if(current?.owner===owner&&!offline)await request('receipts',{action:'flush'});
  };
  flushing=(navigator.locks?navigator.locks.request('crm-mobile-send',run):run()).finally(()=>{flushing=null;});return flushing;
}
async function queue(action?:string,id?:string,expected?:string){
  const owner=authority(expected);
  if(action==='registry'){
    const entries:Entry[]=await vault.get(owner,'queue')||[];
    return {ok:true,status:200,data:{entries:entries.filter(entry=>entry.action==='registry-edit')}};
  }
  if(action==='retry'||action==='review-registry')await vault.update(owner,'queue',(rows:Entry[]|null)=>(rows||[]).map(e=>e.body.operation_id===id?{...e,state:action==='retry'?'pending':'attention',reviewed_at:action==='review-registry'?new Date().toISOString():undefined}:e));
  if(action==='retry')await request('receipts',{action:'retry',operation_id:id});
  if(action==='retry'||action==='flush')await flush();
  const entries:Entry[]=await vault.get(owner,'queue')||[];
  const server=await request('receipts');if(server.ok){await vault.put(owner,'server-receipts',server.data.entries);entries.push(...server.data.entries);}else entries.push(...(await vault.get(owner,'server-receipts')||[]));
  return {ok:true,status:200,data:{entries:entries.map(e=>{if(e.action!=='document')return e;const {content_base64,...body}=e.body;return {...e,body};}),exports:(await vault.get(owner,'download-jobs')||[]).map(({intentKey,...job}:any)=>job)}};
}
function startPolling(){clearTimeout(liveTimer);liveTimer=setTimeout(async()=>{
  if(!polling&&current?.owner&&!document.hidden){polling=true;try{
    const owner=current.owner;await flush();if(current?.owner!==owner)return;
    const cursor=await vault.get(owner,'live-cursor')||{assignments:0,facts:0};
    const response=await request('api',{method:'GET',action:'changes',query:cursor});
    if(current?.owner!==owner)return;
    if(response.ok){await vault.put(owner,'live-cursor',response.data.cursor);if(response.data.dirty){
      // Facts refresh in place. Evicting all reads here left a cold offline start empty.
      // Assignment/scope changes can remove authority, so those do invalidate reads.
      if(response.data.resync||response.data.cursor.assignments!==cursor.assignments||cursor.scope&&response.data.cursor.scope!==cursor.scope){await vault.clearReads(owner);await api('GET','work-portfolio');}
      const context=await request('api',{method:'GET',action:'context'});
      if(context.ok&&current?.owner===owner){
        if((context.data.access_model||1)!==(current.context?.access_model||1)||grantId(context.data)!==grantId()){
          await initialize();
          emit({type:'identity',owner:current?.owner},true);
          return;
        }
        const before=grantScope();current.context=context.data;
        if(grantScope()!==before)await vault.clearReads(owner);
        await vault.put(deviceScope,'last-state',current);
      }
    }}
    emit({type:'live',connected:response.ok,dirty:!!response.data?.dirty,resync:!!response.data?.resync,as_of:response.data?.as_of||new Date().toISOString(),reason:response.ok?'':response.status===0?'offline':response.data?.error||'server'});
  }catch{emit({type:'live',connected:false,dirty:false,reason:'offline'});}finally{polling=false;}}
  startPolling();
},document.hidden?15000:2000+Math.floor(Math.random()*750));}
export function chooseFile(accept='',multiple=false,folder=false):Promise<File[]>{return new Promise(resolve=>{
  const input=document.createElement('input');input.type='file';input.accept=accept;input.multiple=multiple;input.hidden=true;document.body.append(input);
  if(folder)input.setAttribute('webkitdirectory','');
  let settled=false;const finish=(files:File[])=>{if(settled)return;settled=true;input.remove();resolve(files);};
  input.onchange=()=>finish(Array.from(input.files||[]));input.addEventListener('cancel',()=>finish([]));input.click();
});}
async function pickSource(kind:string,expected:string){
  authority(expected);
  return new Promise<string>(resolve=>{
    const dialog=document.createElement('dialog');dialog.className='mobile-source-picker';
    dialog.innerHTML='<h2>Источник</h2><div data-source-list></div><p data-source-status role="status"></p><div class="actions"><button data-source-phone class="primary">Загрузить с телефона</button><button data-source-close>Отмена</button></div>';
    const list=dialog.querySelector<HTMLElement>('[data-source-list]')!,status=dialog.querySelector<HTMLElement>('[data-source-status]')!;let settled=false;
    const changed=(event:any)=>{if(event.type==='identity')finish('');};
    const finish=(value:string)=>{if(settled)return;settled=true;listeners.delete(changed);dialog.close();dialog.remove();resolve(value);};listeners.add(changed);
    const load=async(parent?:string)=>{
      status.textContent='';const response=await request('sources?kind='+kind+(parent?'&parent='+encodeURIComponent(parent):''));
      if(!response.ok){status.textContent=response.text||'Корпоративные источники недоступны.';return;}
      list.replaceChildren();
      if(parent){const back=document.createElement('button');back.textContent='← Источники';back.onclick=()=>{void load();};list.append(back);}
      if(parent&&kind==='folder'){const choose=document.createElement('button');choose.textContent='Выбрать эту папку';choose.className='primary';choose.onclick=()=>finish(parent);list.append(choose);}
      for(const entry of response.data.entries){const button=document.createElement('button');button.textContent=entry.name+(entry.directory?'  ›':'');button.onclick=()=>{if(entry.directory)void load(entry.file);else finish(entry.file);};list.append(button);}
      if(!parent&&!response.data.entries.length)status.textContent='Выберите файл или папку с телефона.';
    };
    dialog.querySelector<HTMLButtonElement>('[data-source-phone]')!.onclick=async()=>{
      const files=await chooseFile(kind==='folder'?'':'.xlsx,.xlsm,.xlsb,.xls,.csv,.txt',kind==='folder',kind==='folder');if(!files.length)return;
      const button=dialog.querySelector<HTMLButtonElement>('[data-source-phone]')!;button.disabled=true;
      try{authority(expected);if(files.length>500||files.reduce((n,f)=>n+f.size,0)>512*1024*1024)throw Error('Выберите до 500 файлов общим размером до 512 МБ.');const handles:any[]=[];for(const [i,file] of files.entries()){status.textContent=`Загрузка ${i+1} из ${files.length} · ${file.name}`;const handle=await upload(file,expected);handles.push(kind==='folder'?{file:handle,relative_path:file.webkitRelativePath?.split('/').slice(1).join('/')||file.name}:handle);}
        authority(expected);
        if(kind==='folder'){const response=await request('admin',{action:'folder',args:handles});if(!response.ok)throw Error(response.text);finish(response.data.file);}else finish(handles[0]);
      }catch(error){status.textContent=(error as Error).message;button.disabled=false;}
    };
    dialog.querySelector('[data-source-close]')!.addEventListener('click',()=>finish(''));dialog.addEventListener('cancel',event=>{event.preventDefault();finish('');});document.body.append(dialog);dialog.showModal();void load();
  });
}
async function upload(file:File,expected:string){
  authority(expected);
  if(file.size>64*1024*1024)throw Error('Файл больше 64 МБ.');
  const response=await fetch('./upload',{method:'POST',credentials:'same-origin',headers:{'X-CRM-CSRF':csrf,'X-CRM-Filename':encodeURIComponent(file.name)},body:file});
  await revoke(response.status,expected);
  authority(expected);
  const value=await response.json();if(!response.ok)throw Error(value.text||'Файл не загружен.');return value.file;
}
async function download(response:Response,name?:string,expected=current?.owner){
  authority(expected);await revoke(response.status,expected);
  if(!response.ok){const value=await response.json();throw Error(value.text||'Файл не подготовлен.');}
  const blob=await response.blob();authority(expected);const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name||decodeURIComponent(response.headers.get('X-CRM-Filename')||'Выгрузка.xlsx');a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
  return {ok:true,status:200,data:{downloaded:true}};
}
async function workbook(action:string,input:any={}){
  if(action==='team-visits'){
    const expected=authority(),requestedGrant=grantId();
    const result=await request('api',{method:'GET',action:'manager-statistics',query:{...input,download:'visits'},...(requestedGrant!==null?{grant_id:requestedGrant}:{})});
    if(!result.ok)return result;
    authority(expected);if(requestedGrant!==grantId())throw Error('Полномочие изменилось. Повторите выгрузку.');
    const {filename,content_base64,sha256}=result.data;
    const bytes=Uint8Array.from(atob(content_base64),c=>c.charCodeAt(0));
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
    if(hash!==sha256)throw Error('Проверка файла отчёта не прошла.');
    return download(new Response(bytes),filename,expected);
  }
  const owner=authority(),requestedGrant=grantId(),resumed=action==='resume'?(await vault.get(owner,'download-jobs')||[]).find((j:any)=>j.operation_id===input.operation_id):null;
  if(action==='resume'&&!resumed)throw Error('Сохранённая выгрузка не найдена у этого сотрудника.');
  if(resumed&&!entryMatchesGrant(resumed))throw Error('Выгрузка принадлежит другому полномочию.');
  if(resumed)action=resumed.action;
  const scope=current.context?.access_model===2?operationScope()+':':'',intentKey=resumed?.intentKey||'download-intent:'+scope+action+':'+JSON.stringify(input);
  const prior=action==='read'?null:await vault.get(owner,intentKey);let payload=prior?.input||{...input};
  if(resumed&&!prior)throw Error('Исходная выгрузка уже завершена.');
  if(!prior&&action==='table'&&typeof payload.name==='string')payload.name=payload.name.replace(/\.xlsx$/i,'');
  if(action==='read'){const [file]=await chooseFile('.xlsx,.xlsm,.xlsb,.xls');if(!file)return {ok:false,cancelled:true};payload.file=await upload(file,owner);}
  if(!prior&&action==='write'&&input.use_template){const [file]=await chooseFile('.xlsx,.xlsm,.xlsb,.xls');if(!file)return {ok:false,cancelled:true};payload.template=await upload(file,owner);}
  const operation_id=prior?.operation_id||crypto.randomUUID();if(action!=='read'&&!prior)await vault.put(owner,intentKey,{operation_id,input:payload});
  if(action!=='read')await vault.update(owner,'download-jobs',(jobs:any[]|null)=>{jobs||=[];return jobs.some(j=>j.operation_id===operation_id)?jobs:[...jobs,{operation_id,action,intentKey,completed:false,path:input.snapshot?'Реестр CRM · '+input.snapshot.manifest.period:input.name||'Отчёт CRM',grant_id:requestedGrant,actor_uid:current?.context?.actor_uid,access_model:current?.context?.access_model===2?2:1}];});
  try{
  authority(owner);
  const response=await fetch('./workbook',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CRM-CSRF':csrf},body:JSON.stringify({action,input:payload,operation_id,...(requestedGrant!==null?{grant_id:requestedGrant}:{})}),signal:AbortSignal.timeout(180000)});
  await revoke(response.status,owner);
  authority(owner);if(requestedGrant!==grantId())throw Error('Полномочие изменилось. Проверьте исходную выгрузку.');
  if(action==='read'){const value=await response.json();return {ok:response.ok,status:response.status,data:value,text:value.text};}
  if([400,409,422].includes(response.status))await vault.put(owner,intentKey,null);
  const result=await download(response,undefined,owner);await vault.put(owner,intentKey,null);
  await vault.update(owner,'download-jobs',(jobs:any[])=>jobs.map(j=>j.operation_id===operation_id?{...j,completed:true,error:null}:j));
  if(payload.snapshot?.manifest?.export_id){const key=await vault.get(owner,'export-key:'+scope+payload.snapshot.manifest.export_id);if(key)await vault.put(owner,key,null);}
  return {...result,data:{...result.data,path:'Файл скачан',receipt_state:response.headers.get('X-CRM-Receipt-State')}};
  }catch(error){if(action!=='read')await vault.update(owner,'download-jobs',(jobs:any[])=>jobs.map(j=>j.operation_id===operation_id?{...j,error:'Скачивание не подтверждено. Повторите исходную выгрузку.'}:j));throw error;}
}
async function phoneDocument(input:File){
  const ext=input.name.split('.').at(-1)?.toLowerCase()||'',known:Record<string,string>={jpg:'image/jpeg',jpeg:'image/jpeg',png:'image/png',pdf:'application/pdf',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'};
  const type=input.type||known[ext]||'',file=new File([input],input.name,{type,lastModified:input.lastModified});
  if(!file.size)throw Error('Файл пуст. Выберите другой документ.');
  const image=type.startsWith('image/')||['heic','heif','webp','avif'].includes(ext);
  if(image&&(file.size>10*1024*1024||!['image/jpeg','image/png'].includes(type))){
    const url=URL.createObjectURL(file),picture=new Image();
    try{
      picture.src=url;await picture.decode();const scale=Math.min(1,2400/Math.max(picture.naturalWidth,picture.naturalHeight)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(picture.naturalWidth*scale));canvas.height=Math.max(1,Math.round(picture.naturalHeight*scale));
      const ctx=canvas.getContext('2d')!;ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(picture,0,0,canvas.width,canvas.height);
      const jpeg=await new Promise<Blob|null>(resolve=>canvas.toBlob(resolve,'image/jpeg',.88));if(!jpeg)throw Error();
      if(jpeg.size>10*1024*1024)throw Error();return new File([jpeg],input.name.replace(/\.[^.]+$/,'')+'.jpg',{type:'image/jpeg'});
    }catch{throw Error('Не удалось подготовить фото. Выберите JPEG, PNG или PDF.');}finally{URL.revokeObjectURL(url);}
  }
  if(!Object.values(known).includes(type))throw Error('Выберите изображение, PDF или DOCX.');
  if(file.size>10*1024*1024)throw Error('Документ больше 10 МБ.');return file;
}
async function documentFile(contract:string,id?:string,expected?:string){
  const owner=authority(expected);
  if(id){const response=await api('GET','document-read',undefined,{document_id:id});if(!response.ok)return response;return download(new Response(Uint8Array.from(atob(response.data.content_base64),c=>c.charCodeAt(0))),response.data.filename,owner);}
  const [selected]=await chooseFile('image/*,.pdf,.docx');if(!selected)return {ok:false,cancelled:true};
  return queueFile(contract,selected,owner);
}
async function bytesSha256(bytes:Uint8Array<ArrayBuffer>){
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(v=>v.toString(16).padStart(2,'0')).join('');
}
async function queueFile(contract:string,selected:File,owner:string,photoOnly=false,document_id:string=crypto.randomUUID()){
  const file=await phoneDocument(selected);
  if(photoOnly&&!['image/jpeg','image/png'].includes(file.type))throw Error('Выберите фотографию.');
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  const checksum=await bytesSha256(bytes);
  await enqueue('document',{operation_id:document_id,kind:'attachment',document_id,contract_uuid:contract,filename:file.name,media_type:file.type||'application/octet-stream',content_base64:btoa(binary)},owner,checksum);
  return {ok:true,status:202,data:{document_id,sha256:checksum,filename:file.name}};
}
async function prepareVisitPhoto(form:string,id:string,allowed:string[],expected:string){
  const owner=authority(expected);
  const run=async()=>{
    authority(owner);
    const queued=(await vault.get(owner,'queue') as Entry[]|null)?.find(e=>e.action==='document'&&e.body.document_id===id);
    const check=(contract:string)=>{if(!allowed.includes(contract))throw Error('Для договора с сохранённым фото изменились права.');};
    if(queued){check(queued.body.contract_uuid);return {document_id:id,contract:queued.body.contract_uuid,filename:queued.body.filename};}
    const raw=await vault.stagedPhoto(owner,form,id);
    if(!raw)throw Error('Исходное фото отсутствует на телефоне.');
    check(raw.contract);
    const result=await queueFile(raw.contract,raw.file,owner,true,id);
    return {document_id:id,contract:raw.contract,filename:result.data.filename};
  };
  return navigator.locks?navigator.locks.request('crm-mobile-photo:'+id,run):run();
}
async function admin(action:string,args:any[],expected:string){
  authority(expected);
  if(action==='copy'){await navigator.clipboard.writeText(String(args[0]));return true;}
  if(action==='pick'){
    const [kind,opts={}]=args;if(kind==='save')return await request('admin',{action:'reserve',args:[opts.defaultPath||'Выгрузка.xlsx']}).then(r=>r.data?.file||'');
    return pickSource(kind,expected);
  }
  if(action==='saveText'){return download(new Response(String(args[1]),{headers:{'Content-Type':'text/plain;charset=utf-8'}}),String(args[0]));}
  if(action==='xlsx')return workbook('table',{name:args[0],headers:args[1],rows:args[2]});
  if(action==='open'){return download(await fetch('./file/'+encodeURIComponent(args[0]),{credentials:'same-origin'}),undefined,expected);}
  if(action==='autostart')return false;
  if(action==='state')return {mode:'analyst',productId:'crm',fixedProduct:true,product:'Аналитик',version:'3.3.0-mobile.1',platform:'mobile'};
  const response=await request('admin',{action,args});return response.ok?response.data:{ok:false,error:response.text,text:response.text};
}
export const bridge:any={
  renderFieldWorkspace:(host:HTMLElement,openPerson:(person:string)=>Promise<void>,valid:()=>boolean)=>{
    if(current?.owner&&fieldAllowed()){
      return mountFieldWorkspace(host,bridge,current.owner,current.context.employee_uuid,openPerson,valid);
    }
    return ()=>{};
  },
  renderFieldOffline:(host:HTMLElement)=>{
    if(current?.owner&&fieldAllowed()){
      mountFieldOffline(host,bridge,current.owner,current.context.employee_uuid);
    }
  },
  platform:'mobile',ready:initialize,
  navigated:(route:any)=>{if(navigating||!current?.owner)return;const next={...route,owner:current.owner};if(canonical(history.state?.crmMobile)===canonical(next))return;const method=history.state?.crmMobile?.owner===current.owner?'pushState':'replaceState';history[method]({crmMobile:next},'',location.pathname+'#'+route.section+(route.person?'/'+route.person:''));},
  onNavigate:(handler:any)=>{routeHandler=handler;},
  initialSection:()=>{const target=navigationTarget;navigationTarget='';initialPerson='';const route=history.state?.crmMobile;if(!target&&route&&current?.owner&&route.owner===current.owner){navigating=true;initialPerson=route.person||'';return route.section;}return target;},
  initialPerson:()=>{navigating=false;const person=initialPerson;initialPerson='';return person;},
  state:async()=>{expireSession();return {owner:current?.owner||'',context:current?.context||null,version:'3.3.0-mobile.1',product:'Взыскание',offline:offline||!navigator.onLine,
    connection:{mode:current?.synthetic?'synthetic':current?'mobile':'unconfigured',role:current?.context?.role||'employee',server:location.origin,shared:!current?.synthetic},existingLogins:[],activity:activity.state()};},
  api,command,registryEdit:(body:any,owner?:string)=>enqueue('registry-edit',body,owner),queue,
  draft:async(name:string,value?:any,expected?:string)=>{
    if(!current?.owner&&value===undefined)return {ok:true,data:null};
    const owner=authority(expected),key='draft:'+name;
    if(value!==undefined){
      await vault.put(owner,key,{draft_version:2,value});
      return {ok:true,data:value};
    }
    const saved=await vault.get(owner,key);
    if(saved?.draft_version===2)return {ok:true,data:saved.value};
    if(saved!==null)return {ok:true,data:saved};
    const moved=await vault.get(owner,'work-transfer');
    return {ok:true,data:moved?.source?await vault.get(moved.source,key):null};
  },
  onChange:(fn:(event:any)=>void)=>{listeners.add(fn);return()=>listeners.delete(fn);},
  telemetry:async(event:any)=>current?api('POST','telemetry',event):{ok:true},
  connect:async(input:any)=>{
    if(input.action==='account-registration-options')return request('registration');
    if(input.action==='refresh'){
      const before=current?.owner,scope=grantScope();
      const response=await initialize();connectionResponse=response;
      if(before!==current?.owner||scope!==grantScope()){
        navigationTarget=current?defaultSection():'connection';
        emit({type:'identity',owner:current?.owner},true);
      }
      return response;
    }
    if(input.action==='offline'){offline=!!input.value;if(!offline)background(flush());return {ok:true};}
    const installation=await vault.get(deviceScope,'installation');
    const device_id=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(installation)))).map(v=>v.toString(16).padStart(2,'0')).join('');
    if(input.action==='account-register')return request('registration',{...input,device_id,device_label:navigator.userAgent.slice(0,120)});
    if(input.action==='login'||input.action==='logout')legacyTried=true;
    const response=input.action==='grant'?await request('api',{method:'POST',action:'grant',body:{grant_id:input.grant_id}}):await request(input.action==='logout'?'logout':input.action==='password'?'password':'login',{...input,device_id,device_label:navigator.userAgent.slice(0,120)});
    connectionResponse=response;
    if(response.ok){await initialize();navigationTarget=current?defaultSection():'connection';emit({type:'identity',owner:current?.owner},true);background(flush());}else renderGrantPicker();return response;
  },workbook,document:documentFile,visitPhoto:(contract:string,file:File,expected:string)=>queueFile(contract,file,authority(expected),true),admin,win:()=>{},
  visitPhotoQueue:(form:string,expected:string)=>({
    stage:(contract:string,files:File[])=>{
      if(files.some(file=>!file.size||file.size>25*1024*1024))throw Error('Размер каждого фото должен быть от 1 байта до 25 МБ.');
      return vault.stagePhotoFiles(authority(expected),form,contract,files);
    },
    staged:()=>vault.stagedPhotoIds(authority(expected),form),
    prepare:(id:string,allowed:string[])=>prepareVisitPhoto(form,id,allowed,expected),
    release:(id:string)=>vault.releasePhoto(authority(expected),form,id),
  }),
  diagnostics:async()=>({ok:true,data:{checks:[{title:'Хранилище телефона',code:'storage_ready',ok:!!await vault.get(deviceScope,'installation')},{title:'Связь',code:navigator.onLine?'network_ready':'offline',ok:navigator.onLine}]}}),
  renderConnection:async(host:HTMLElement)=>{
    connectionHost=host;await mountConnection(host,bridge,async()=>{
      const before=current?.owner;
      const response=await initialize();connectionResponse=response;
      if(current?.owner&&before!==current.owner){
        navigationTarget=defaultSection();
        queueMicrotask(()=>emit({type:'identity',owner:current.owner},true));
      }
      return response;
    });if(connectionHost===host)renderGrantPicker();
  },
};
window.addEventListener('online',()=>{background(initialize().then(()=>flush()));});
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden)background(checkReadCache(true).then(()=>initialize()).then(()=>flush()));
});
