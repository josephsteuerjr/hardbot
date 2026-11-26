/** First CRM login: a separate DPAPI record, no old profile/queue migration. */
import {app,net} from 'electron';
import {existsSync,lstatSync,readFileSync,writeFileSync,renameSync,unlinkSync} from 'node:fs';
import {dirname,join,parse} from 'node:path';
import {createHash} from 'node:crypto';
import {openCrmRecord,sealCrmRecord} from './windows-dpapi';
import {desktopFingerprint,deviceName,pcLoginProof} from './net';
import {fetchBuffered} from './transport';
import {primaryOrigin, confirmRehearsal} from './crm-endpoint';
import {CRM_REHEARSAL} from './product';

type Account={credential:string;kind:'employee'|'manager'|'analyst';role:string;username:string;uid:number;pending:boolean;password:boolean;origin?:string};
type AccountRecord=Account|{username:string;login_required:true};
let generation=0;
const file=()=>join(app.getPath('userData'),'crm-account.bin');
function record():AccountRecord{
  const value:AccountRecord=JSON.parse(openCrmRecord(readFileSync(file())));
  if(CRM_REHEARSAL && !('login_required' in value) && value.origin!==primaryOrigin())
    throw Object.assign(new Error('Вход относится к прежнему адресу.'),{code:'ENOENT'});
  return value;
}
function read():Account{
  const value=record();
  if('login_required' in value)throw new Error('Войдите в CRM заново.');
  return value;
}
function save(value:AccountRecord){writeFileSync(file()+'.tmp',sealCrmRecord(JSON.stringify(CRM_REHEARSAL ? {...value,origin:primaryOrigin()} : value)),{flush:true});renameSync(file()+'.tmp',file());}
export function adoptSoftAccount(){
  if(CRM_REHEARSAL)return;
  const home=process.env.LOCALAPPDATA;
  if(process.platform!=='win32'||!home||app.getPath('userData')!==join(home,'Hard','hard','data')||existsSync(file()))return;
  const source=join(home,'Hard','soft','data','crm-account.bin');
  if(!existsSync(source))return;
  for(let cursor=source;cursor!==parse(cursor).root;cursor=dirname(cursor)){
    if(lstatSync(cursor).isSymbolicLink())throw Error('Папка прежнего входа перенаправлена.');
  }
  const attributes=lstatSync(source);
  if(!attributes.isFile()||attributes.size>32768)throw Error('Файл прежнего входа требует сверки.');
  const value:AccountRecord=JSON.parse(openCrmRecord(readFileSync(source)));
  if('login_required' in value||value.kind!=='employee'||!Number.isSafeInteger(value.uid)||
     !/^[A-Za-z0-9_-]{20,512}$/.test(value.credential))return;
  save(value);
}
export function ownFingerprint(){return createHash('sha256').update(readFileSync(file())).digest('hex');}
export function savedAccountFingerprint(){
  try{read();return ownFingerprint();}catch{return undefined;}
}
export function markAccountLoginRequired(fingerprint:string|undefined){
  if(!fingerprint || savedAccountFingerprint()!==fingerprint)return false;
  const {username}=read();
  save({username,login_required:true});
  generation++;
  return true;
}
export function ownLogin(){
  const value=read();
  if(value.pending)throw new Error(value.password?'Измените временный пароль.':'Дождитесь подтверждения устройства.');
  if(!['employee','manager','analyst'].includes(value.kind)||!Number.isSafeInteger(value.uid)||!/^[A-Za-z0-9_-]{20,512}$/.test(value.credential))throw new Error('Сохранённый вход CRM повреждён.');
  return {credential:value.credential,kind:value.kind,fingerprint:ownFingerprint()};
}
function publicState(value:Account){return {ok:true,data:{username:value.username,role:value.role,pending:value.pending,
  must_change_password:value.password,action:value.pending?'ok_pending':'ok'}};}
export function accountState(){
  try{
    const value=record();
    if('login_required' in value)return {available:false as const,username:value.username};
    return {available:true as const,...publicState(value).data};
  }
  catch(error){return {available:false as const,...((error as any).code==='ENOENT'?{}:{error:'Не удалось прочитать сохранённый вход. Войдите заново.'})};}
}
async function request(path:string,body?:unknown,credential?:Account){
  const response=await fetchBuffered((url,init)=>net.fetch(url,init),primaryOrigin()+path,{
    method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Client-Version':app.getVersion(),
      ...(credential?.kind === 'analyst' ? {'X-CRM-Entry':'analyst'} : {}),
      ...(credential?{[credential.kind==='employee'?'X-App-Token':'X-Pult-Token']:credential.credential}:{} )},
    ...(body===undefined?{}:{body:JSON.stringify(body)})},20000);
  const result=await response.json().catch(error=>{if(response.ok)throw error;return {};});
  if(!response.ok||result.error||result.ok===false)throw Object.assign(new Error(String(result.text||result.error||'Вход не подтверждён.').slice(0,500)),{status:response.status});
  return result;
}
export async function accountAction(action:string,input:any){
  if(action==='account-register'||action==='account-login')await confirmRehearsal();
  if(action==='account-registration-options')return {ok:true,data:await request('/api/account/crm-registration')};
  if(action==='account-register'){
    const username=String(input.username||'').trim().toLowerCase();
    const employee=String(input.employee||'').trim().replace(/\s+/g,' ');
    const password=String(input.password||'');
    const pc_proof=await pcLoginProof(primaryOrigin(),username,password,employee);
    const data=await request('/api/account/crm-registration',{role:input.role,employee,username,password,
      country:input.country,city:input.city,code:input.code,pc_proof,audience:'crm-desktop'});
    return {ok:true,data};
  }
  const mine=++generation;
  const current=()=>{if(mine!==generation)throw new Error('Вход изменён. Повторите действие.');};
  if(action==='account-login'){
    const username=String(input.username||'').trim(),password=String(input.password||'');
    const proof=await pcLoginProof(primaryOrigin(),username,password);current();
    const value=await request('/api/account/login',{username,password,audience:'crm-desktop',entrance:'internal',pc_proof:proof,
      device:{kind:'desktop',fp:desktopFingerprint(),label:deviceName()+' · CRM'}});current();
    if(!['ok','ok_pending'].includes(value.action)||!['agent','manager','security','full','analyst'].includes(value.role)||
      !Number.isSafeInteger(value.uid)||!/^[A-Za-z0-9_-]{20,512}$/.test(value.token||''))throw new Error('Сервер не подтвердил полный вход CRM.');
    const result:Account={credential:value.token,username:value.username||username,uid:value.uid,
      kind:value.role==='agent'?'employee':value.role==='analyst'?'analyst':'manager',role:value.role,
      pending:value.action!=='ok'||!!value.must_change_password,password:!!value.must_change_password};
    if(result.pending&&!result.password){
      try{
        const identity=await request('/api/crm-identity',undefined,result);current();
        if(identity.subject?.actor_uid===result.uid){result.pending=false;result.role=identity.me.role;}
      }catch(error){if(![401,403,410].includes((error as any).status))throw error;}
    }
    save(result);return publicState(result);
  }
  const saved=read();
  if(action==='account-password'){
    await request('/api/account/password',{username:saved.username,current:input.current,new:input.new});current();
    saved.password=false;saved.pending=true;save(saved);return publicState(saved);
  }
  if(action==='account-logout'){
    try{await request('/api/account/logout',{},saved);}catch(error){if(![401,403,410].includes((error as any).status))throw error;}
    current();unlinkSync(file());return {ok:true,data:{action:'logged_out'}};
  }
  if(action==='account-status'){
    const identity=await request('/api/crm-identity',undefined,saved);current();
    if(identity.subject?.actor_uid!==saved.uid)throw new Error('Изменилась личность входа.');
    saved.pending=false;saved.password=false;saved.role=identity.me.role;save(saved);return publicState(saved);
  }
  throw new Error('Неизвестное действие входа.');
}
