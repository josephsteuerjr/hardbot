/** Origin-local encrypted records. CAS keeps concurrent tabs from losing work. */
type RecordValue={revision:number;iv:Uint8Array;cipher:ArrayBuffer};
const READ_CACHE_ACTIVITY='read-cache-last-use';
const READ_CACHE_MS=48*60*60*1000;
const readCacheDeadline=(last:number,now:number)=>Number.isFinite(last)&&last>0&&last<=now&&now-last<READ_CACHE_MS
  ? last+READ_CACHE_MS : 0;
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
  if(name.startsWith('read:')){
    const last=await result(db.transaction('keys').objectStore('keys').get(READ_CACHE_ACTIVITY));
    if(!readCacheDeadline(last,Date.now())){
      await maintainReadCache(false);
      return null;
    }
  }
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
function trackWrite<T>(work:Promise<T>){
  writes.add(work);
  void work.then(()=>writes.delete(work),()=>{writes.delete(work);failedWrite=true;});
  return work;
}
export function update(owner:string,name:string,change:(value:any)=>any){
  return trackWrite(updateRecord(owner,name,change));
}
async function updateRecord(owner:string,name:string,change:(value:any)=>any){
  const id=await address(owner,name),legacy=owner+'\n'+name,db=await database();
  const cache=name.startsWith('read:');
  for(let attempt=0;attempt<20;attempt++){
    const before=await result(db.transaction('vault').objectStore('vault').get(id)) as RecordValue|undefined;
    const old=before?null:await result(db.transaction('vault').objectStore('vault').get(legacy));
    const value=change(await decode(before||old,before?id:legacy)),iv=crypto.getRandomValues(new Uint8Array(12));
    const cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:new TextEncoder().encode(location.origin+'\n'+id)},await key(),new TextEncoder().encode(JSON.stringify(value)));
    const tx=db.transaction(cache?['vault','keys']:['vault'],'readwrite'),done=complete(tx);let conflict=false;
    const records=tx.objectStore('vault');
    const write=()=>{
      const read=records.get(id);
      read.onsuccess=()=>{
        if((read.result?.revision||0)!==(before?.revision||0)){conflict=true;tx.abort();return;}
        records.put({revision:(before?.revision||0)+1,iv,cipher},id);
        if(old)records.delete(legacy);
      };
    };
    if(cache){
      const activity=tx.objectStore('keys').get(READ_CACHE_ACTIVITY);
      activity.onsuccess=()=>{if(readCacheDeadline(activity.result,Date.now()))write();};
    }else write();
    try{await done;return value;}catch(error){if(!conflict)throw error;}
  }
  throw Error('Данные меняются в другой вкладке. Повторите сохранение.');
}
export const put=(owner:string,name:string,value:any)=>update(owner,name,()=>value);

/** The phone keeps unsent work and its login when re-downloadable reads age out. */
export async function maintainReadCache(touch:boolean){
  const db=await database(),tx=db.transaction(['keys','vault'],'readwrite'),done=complete(tx);
  const keys=tx.objectStore('keys'),records=tx.objectStore('vault');
  const activity=keys.get(READ_CACHE_ACTIVITY);
  let deadline=0,cleared=false;
  activity.onsuccess=()=>{
    const last=activity.result,now=Date.now();
    deadline=readCacheDeadline(last,now);
    cleared=!deadline;
    if(!deadline&&last!==0){
      const scan=records.openKeyCursor();
      scan.onsuccess=()=>{
        const entry=scan.result;
        if(!entry)return;
        if(String(entry.key).split('\n',2)[1]?.startsWith('read:'))records.delete(entry.key);
        entry.continue();
      };
      if(!touch)keys.put(0,READ_CACHE_ACTIVITY);
    }
    if(touch){
      keys.put(now,READ_CACHE_ACTIVITY);
      deadline=now+READ_CACHE_MS;
    }
  };
  await done;
  return {deadline,cleared};
}

const photoPrefix=async(owner:string,form:string)=>(await address(owner,'visit-photos:'+form))+':';
export function stagePhotoFiles(owner:string,form:string,contract:string,files:File[]){
  return trackWrite((async()=>{
    const prefix=await photoPrefix(owner,form),db=await database(),secret=await key();
    for(const file of files){
      const id=prefix+crypto.randomUUID(),iv=crypto.getRandomValues(new Uint8Array(12));
      const meta=new TextEncoder().encode(JSON.stringify({contract,name:file.name,type:file.type}));
      const data=new Uint8Array(4+meta.length+file.size);
      new DataView(data.buffer).setUint32(0,meta.length);
      data.set(meta,4);data.set(new Uint8Array(await file.arrayBuffer()),4+meta.length);
      const cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv,
        additionalData:new TextEncoder().encode(location.origin+'\n'+id)},secret,data);
      const tx=db.transaction('vault','readwrite'),done=complete(tx);
      tx.objectStore('vault').add({iv,cipher},id);
      await done;
    }
  })());
}
export async function stagedPhotoIds(owner:string,form:string){
  const prefix=await photoPrefix(owner,form),db=await database();
  const ids=await result(db.transaction('vault').objectStore('vault').getAllKeys(IDBKeyRange.bound(prefix,prefix+'\uffff')));
  return ids.map(id=>String(id).slice(prefix.length));
}
export async function stagedPhoto(owner:string,form:string,photo:string){
  const id=(await photoPrefix(owner,form))+photo,db=await database();
  const row=await result(db.transaction('vault').objectStore('vault').get(id));
  if(!row)return null;
  const data=await crypto.subtle.decrypt({name:'AES-GCM',iv:new Uint8Array(row.iv),
    additionalData:new TextEncoder().encode(location.origin+'\n'+id)},await key(),row.cipher);
  const end=4+new DataView(data).getUint32(0);
  const meta=JSON.parse(new TextDecoder().decode(data.slice(4,end)));
  return {contract:meta.contract as string,file:new File([data.slice(end)],meta.name,{type:meta.type})};
}
export function releasePhoto(owner:string,form:string,photo:string){
  return trackWrite((async()=>{
    const id=(await photoPrefix(owner,form))+photo,db=await database(),tx=db.transaction('vault','readwrite'),done=complete(tx);
    tx.objectStore('vault').delete(id);
    await done;
  })());
}

export async function clearReads(owner:string){
  const db=await database(),tx=db.transaction('vault','readwrite'),done=complete(tx);
  const cursor=tx.objectStore('vault').openKeyCursor();cursor.onsuccess=()=>{const row=cursor.result;if(!row)return;if(String(row.key).startsWith(owner+'\nread:'))tx.objectStore('vault').delete(row.key);row.continue();};await done;
}
