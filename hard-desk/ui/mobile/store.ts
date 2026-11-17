/** Origin-local encrypted records. CAS keeps concurrent tabs from losing work. */
type RecordValue={revision:number;iv:Uint8Array;cipher:ArrayBuffer};
let opening:Promise<IDBDatabase>;
function database(){return opening??=new Promise<IDBDatabase>((resolve,reject)=>{
  const request=indexedDB.open('rimborso-crm-mobile-v1',1);
  request.onupgradeneeded=()=>{request.result.createObjectStore('vault');request.result.createObjectStore('keys');};
  request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
});}
const result=<T>(request:IDBRequest<T>)=>new Promise<T>((resolve,reject)=>{request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
const complete=(tx:IDBTransaction)=>new Promise<void>((resolve,reject)=>{tx.oncomplete=()=>resolve();tx.onabort=tx.onerror=()=>reject(tx.error||Error('Не удалось сохранить данные на телефоне.'));});
let keyPromise:Promise<CryptoKey>;
async function key(){return keyPromise??=(async()=>{
  const db=await database();let saved=await result(db.transaction('keys').objectStore('keys').get('device'));
  if(saved)return saved as CryptoKey;
  const generated=await crypto.subtle.generateKey({name:'AES-GCM',length:256},false,['encrypt','decrypt']);
  try{const tx=db.transaction('keys','readwrite'),done=complete(tx);tx.objectStore('keys').add(generated,'device');await done;return generated;}
  catch{saved=await result(db.transaction('keys').objectStore('keys').get('device'));if(!saved)throw Error('Не удалось открыть защищённое хранилище.');return saved as CryptoKey;}
})();}
const address=async(owner:string,name:string)=>owner+'\n'+(name.startsWith('read:')?'read:':'record:')+Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(name)))).map(v=>v.toString(16).padStart(2,'0')).join('');
async function decode(record:RecordValue|undefined,id:string){
  if(!record)return null;
  const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:new Uint8Array(record.iv),additionalData:new TextEncoder().encode(location.origin+'\n'+id)},await key(),record.cipher);
  return JSON.parse(new TextDecoder().decode(plain));
}
export async function get(owner:string,name:string){
  const id=await address(owner,name),db=await database();
  const record=await result(db.transaction('vault').objectStore('vault').get(id));
  if(record)return decode(record,id);
  // Preserve and atomically re-address records from the internal early candidate.
  if(await result(db.transaction('vault').objectStore('vault').get(owner+'\n'+name)))return update(owner,name,value=>value);
  return null;
}
const writes=new Set<Promise<any>>();
let failedWrite=false;
export async function settled(){
  while(writes.size)await Promise.all([...writes]);
  if(failedWrite)throw Error("Сохранение не подтверждено. Оставьте приложение открытым и повторите сохранение.");
}
export function update(owner:string,name:string,change:(value:any)=>any){
  const work=updateRecord(owner,name,change);writes.add(work);
  void work.then(()=>writes.delete(work),()=>{writes.delete(work);failedWrite=true;});
  return work;
}
async function updateRecord(owner:string,name:string,change:(value:any)=>any){
  const id=await address(owner,name),legacy=owner+'\n'+name,db=await database();
  for(let attempt=0;attempt<20;attempt++){
    const before=await result(db.transaction('vault').objectStore('vault').get(id)) as RecordValue|undefined;
    const old=before?null:await result(db.transaction('vault').objectStore('vault').get(legacy));
    const value=change(await decode(before||old,before?id:legacy)),iv=crypto.getRandomValues(new Uint8Array(12));
    const cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:new TextEncoder().encode(location.origin+'\n'+id)},await key(),new TextEncoder().encode(JSON.stringify(value)));
    const tx=db.transaction('vault','readwrite'),done=complete(tx);let conflict=false;
    const read=tx.objectStore('vault').get(id);
    read.onsuccess=()=>{if((read.result?.revision||0)!==(before?.revision||0)){conflict=true;tx.abort();}else{tx.objectStore('vault').put({revision:(before?.revision||0)+1,iv,cipher},id);if(old)tx.objectStore('vault').delete(legacy);}};
    try{await done;return value;}catch(error){if(!conflict)throw error;}
  }
  throw Error('Данные меняются в другой вкладке. Повторите сохранение.');
}
export const put=(owner:string,name:string,value:any)=>update(owner,name,()=>value);
export async function clearReads(owner:string){
  const db=await database(),tx=db.transaction('vault','readwrite'),done=complete(tx);
  const cursor=tx.objectStore('vault').openKeyCursor();cursor.onsuccess=()=>{const row=cursor.result;if(!row)return;if(String(row.key).startsWith(owner+'\nread:'))tx.objectStore('vault').delete(row.key);row.continue();};await done;
}
