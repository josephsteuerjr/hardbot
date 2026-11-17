/** First CRM login: a separate DPAPI record, no old profile/queue migration. */
import {app,net} from 'electron';
import {readFileSync,writeFileSync,renameSync,unlinkSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {directOpen,directSeal} from './windows-dpapi';
import {desktopFingerprint,deviceName,pcLoginProof} from './net';
import {fetchBuffered} from './transport';

const PRIMARY='https://hard.rimborso.ru';
type Account={credential:string;kind:'employee'|'manager'|'analyst';role:string;username:string;uid:number;pending:boolean;password:boolean};
let generation=0;
const file=()=>join(app.getPath('userData'),'crm-account.bin');
function read():Account{return JSON.parse(directOpen(readFileSync(file())));}
function save(value:Account){writeFileSync(file()+'.tmp',directSeal(JSON.stringify(value)),{flush:true});renameSync(file()+'.tmp',file());}
export function ownFingerprint(){return createHash('sha256').update(readFileSync(file())).digest('hex');}
export function ownLogin(){
  const value=read();
  if(value.pending)throw new Error(value.password?'Измените временный пароль.':'Дождитесь подтверждения устройства.');
  if(!['employee','manager','analyst'].includes(value.kind)||!Number.isSafeInteger(value.uid)||!/^[A-Za-z0-9_-]{20,512}$/.test(value.credential))throw new Error('Сохранённый вход CRM повреждён.');
  return {credential:value.credential,kind:value.kind,fingerprint:ownFingerprint()};
}
function publicState(value:Account){return {ok:true,data:{username:value.username,role:value.role,pending:value.pending,
  must_change_password:value.password,action:value.pending?'ok_pending':'ok'}};}
export function accountState(){
  try{return {available:true,...publicState(read()).data};}
  catch(error){return {available:false,...((error as any).code==='ENOENT'?{}:{error:'Не удалось прочитать сохранённый вход. Войдите заново.'})};}
}
async function request(path:string,body?:unknown,credential?:Account){
  const response=await fetchBuffered((url,init)=>net.fetch(url,init),PRIMARY+path,{
    method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Client-Version':app.getVersion(),
      ...(credential?{[credential.kind==='employee'?'X-App-Token':'X-Pult-Token']:credential.credential}:{} )},
    ...(body===undefined?{}:{body:JSON.stringify(body)})},20000);
  const result=await response.json();
  if(!response.ok||result.error||result.ok===false)throw Object.assign(new Error(String(result.text||result.error||'Вход не подтверждён.').slice(0,500)),{status:response.status});
  return result;
}
export async function accountAction(action:string,input:any){
  const mine=++generation;
  const current=()=>{if(mine!==generation)throw new Error('Вход изменён. Повторите действие.');};
  if(action==='account-login'){
    const username=String(input.username||'').trim(),password=String(input.password||'');
    const proof=await pcLoginProof(PRIMARY,username,password);current();
    const value=await request('/api/account/login',{username,password,audience:'crm-desktop',entrance:'internal',pc_proof:proof,
      device:{kind:'desktop',fp:desktopFingerprint(),label:deviceName()+' · CRM'}});current();
    if(!['ok','ok_pending'].includes(value.action)||!['agent','manager','security','full','analyst'].includes(value.role)||
      !Number.isSafeInteger(value.uid)||!/^[A-Za-z0-9_-]{20,512}$/.test(value.token||''))throw new Error('Сервер не подтвердил полный вход CRM.');
    const result:Account={credential:value.token,username:value.username||username,uid:value.uid,
      kind:value.role==='agent'?'employee':value.role==='analyst'?'analyst':'manager',role:value.role,
      pending:value.action!=='ok'||!!value.must_change_password,password:!!value.must_change_password};
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
