import * as vault from './store';

type Session = {owner:string;context:{role:string;actor_uid:number;employee_uuid:string;timezone:string;staff?:any[]}};
type Summary = {id:string;rep:string;time:number;photo:boolean};
type Request = (method:string,action:string,body?:any,query?:any)=>Promise<any>;
const first = Date.parse('2026-11-19T20:48:00Z');
const through = Date.parse('2026-11-20T16:00:00Z');
const fields = new Set('call_status visit_date visit_comment call_comment contact_with agreed_payment who_at_address client_location purpose_rf tips extra_address'.split(' '));
const fail:(message:string)=>never = message=>{throw Error(message);};
const keyOK = (id:unknown)=>typeof id==='string' && /^[a-zA-Z0-9_-]{12,80}$/.test(id);
function mine(row:any,session:Session){
  const uid=session.context.actor_uid;
  if(!Number.isSafeInteger(uid)||!uid||String(row?._owner?.uid)!==String(uid))return false;
  const name=session.context.staff?.find(s=>s.employee_uuid===session.context.employee_uuid)?.name;
  return !name || !row._owner.employee || row._owner.employee===name;
}
async function oldQueue<T>(read:(store:IDBObjectStore,done:(value:T)=>void)=>void,absent:T):Promise<T>{
  return new Promise((resolve,reject)=>{
    const open=indexedDB.open('hardbot');let missing=false;
    open.onupgradeneeded=()=>{missing=true;open.transaction!.abort();};
    open.onerror=()=>missing?resolve(absent):reject(Error('Не удалось открыть прежнюю очередь на телефоне.'));
    open.onblocked=()=>reject(Error('Закройте второе окно прежней программы и повторите.'));
    open.onsuccess=()=>{
      const db=open.result;
      if(!db.objectStoreNames.contains('queue')){db.close();resolve(absent);return;}
      const tx=db.transaction('queue','readonly');let value=absent;
      tx.oncomplete=()=>{db.close();resolve(value);};
      tx.onabort=tx.onerror=()=>{db.close();reject(Error('Не удалось прочитать прежнюю очередь.'));};
      read(tx.objectStore('queue'),v=>{value=v;});
    };
  });
}
function list(session:Session){
  return oldQueue<Summary[]>((store,done)=>{
    const rows:Summary[]=[];const cursor=store.openCursor();
    cursor.onsuccess=()=>{
      const item=cursor.result;if(!item){done(rows.sort((a,b)=>a.time-b.time));return;}
      const r=item.value;
      if(mine(r,session)&&((r.kind==='save'&&!r.save_kind)||r.kind==='photo')&&keyOK(r.op_id)){
        rows.push({id:r.op_id,rep:String(r.rep),time:r.occurred_at||r.at,photo:r.kind==='photo'});
      }
      item.continue();
    };
  },[]);
}
function readOne(summary:Summary,session:Session){
  return oldQueue<any>((store,done)=>{const get=store.get(summary.id);get.onsuccess=()=>done(get.result);},null).then(r=>{
    if(!r||!mine(r,session)||String(r.rep)!==summary.rep||r.op_id!==summary.id||
       (summary.photo?r.kind!=='photo':r.kind!=='save'||r.save_kind)||
       (r.occurred_at||r.at)!==summary.time)fail('Запись на телефоне изменилась. Обновите список.');
    return r;
  });
}
function eligible(summary:Summary,row:any){
  if(!Number.isSafeInteger(summary.time)||summary.time<first||summary.time>through||row.rejected||
     (row.failed&&row.failed!=='server-owner')||row.replaces_op_id)
    fail('Эта запись требует отдельной сверки. Оригинал сохранён на телефоне.');
}
function visitPayload(row:any){
  if(row.form_version!==2||!Array.isArray(row.input_keys)||!row.input_keys.includes('call_status')||!row.values?.call_status)
    fail('В прежней форме не подтверждён результат выезда.');
  const values:Record<string,any>={};const clear=row.cleared_keys||[];
  if(!Array.isArray(clear)||clear.some((k:string)=>!row.input_keys.includes(k)))fail('Проверьте очищенные поля прежней формы.');
  for(const k of row.input_keys){
    if(!clear.includes(k)&&(!Object.prototype.hasOwnProperty.call(row.values,k)||String(row.values[k]??'').trim()===''))continue;
    if(!fields.has(k))fail('В форме есть поля, которые требуют ручной сверки.');
    values[k]=clear.includes(k)?null:row.values[k];
  }
  const evidence:any={};
  if(row.geo){
    const {lat,lon}=row.geo;
    if(!Number.isFinite(lat)||Math.abs(lat)>90||!Number.isFinite(lon)||Math.abs(lon)>180)fail('Координаты прежнего выезда требуют сверки.');
    evidence.point={lat,lon};
  }
  return {values,clear,field_visit:evidence};
}
async function picture(row:any){
  const raw=row.bytes||row.blob;
  const blob=raw instanceof Blob?raw:raw instanceof ArrayBuffer||ArrayBuffer.isView(raw)?new Blob([raw as BlobPart]):null;
  if(!blob||!blob.size||blob.size>10*1024*1024)return fail('Размер или формат фотографии требует ручной сверки.');
  const data=new Uint8Array(await blob.arrayBuffer());
  const png=[137,80,78,71,13,10,26,10].every((v,i)=>data[i]===v);
  if(!png && !(data[0]===255&&data[1]===216&&data[2]===255))return fail('Этот формат фото требует ручной сверки.');
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',data)),n=>n.toString(16).padStart(2,'0')).join('');
  const chunks=[];for(let i=0;i<data.length;i+=16384)chunks.push(String.fromCharCode(...data.subarray(i,i+16384)));
  return {filename:'Фото-'+row.op_id+(png?'.png':'.jpg'),media_type:png?'image/png':'image/jpeg',sha256:hash,
    bytes:data.length,content_base64:btoa(chunks.join(''))};
}
const owners=new WeakMap<HTMLElement,string>();
export function legacyVisits(host:HTMLElement,session:Session,active:()=>Session|null,api:Request,previous?:HTMLElement|null){
  if(session?.context.role!=='employee')return;
  if(previous&&owners.get(previous)===session.owner){host.prepend(previous);return;}
  const box=document.createElement('section');box.className='record';host.prepend(box);
  box.dataset.legacyVisitPanel='';owners.set(box,session.owner);
  const title=document.createElement('h3');title.textContent='Прежние выезды и фото';
  const scan=document.createElement('button');scan.textContent='Проверить на этом телефоне';
  const output=document.createElement('div');output.setAttribute('aria-live','polite');box.append(title,scan,output);
  const scope=()=>{if(!box.isConnected||active()?.owner!==session.owner)fail('Вход изменился. Откройте отправки заново.');};
  const call=async(method:string,action:string,body?:any,query?:any)=>{
    scope();const response=await api(method,action,body,query);scope();
    if(!response.ok||response.stale||response.status===202)fail(response.text||'Ответ сервера не подтверждён. Повторите при связи.');
    return response.data;
  };
  async function locate(s:Summary,row:any){
    const portfolio=await call('GET','work-portfolio',undefined,{work:'all',employee_uuid:session.context.employee_uuid,search:s.rep,offset:0,limit:100});
    const match=portfolio.contracts.filter((c:any)=>String(c.external_id)===s.rep);
    if(match.length!==1||portfolio.has_more||portfolio.total_contracts>100)fail('Не удалось однозначно найти договор в вашем портфеле.');
    const card=await call('GET','card',undefined,{person_uuid:match[0].person_uuid});
    const c=card.contracts.find((v:any)=>v.contract_uuid===match[0].contract_uuid&&String(v.legacy_id)===s.rep);
    if(!c||!c.allowed_actions.includes(s.photo?'document':'visit'))fail('Для этого договора требуется сверка назначения.');
    if(row._owner.employee&&!c.assignments.some((a:any)=>a.employee_uuid===session.context.employee_uuid&&a.name===row._owner.employee))
      fail('Для прежней записи требуется сверка имени и назначения сотрудника.');
    if(!s.photo&&card.contracts.filter((v:any)=>v.allowed_actions.includes('visit')).length!==1)fail('У клиента несколько выездных договоров. Требуется сверка.');
    return {cid:c.contract_uuid,history:card.history};
  }
  async function upload(row:any,cid:string,history:any[]){
    const p=await picture(row);
    if(history.some(e=>e.payload?.sha256===p.sha256&&e.payload.document_id!==row.op_id))
      fail('В карточке уже есть такой файл с другим номером. Требуется сверка.');
    const {sha256,bytes,...data}=p;
    const receipt=await call('POST','document',{...data,document_id:row.op_id,contract_uuid:cid});
    if(receipt.document_id!==row.op_id||receipt.contract_uuid!==cid||receipt.sha256!==sha256||receipt.bytes!==bytes)
      fail('Сервер не подтвердил содержимое фотографии.');
    return {document_id:row.op_id,filename:p.filename,sha256};
  }
  async function restore(s:Summary,selected:Summary[]){
    const row=await readOne(s,session);eligible(s,row);const {cid,history}=await locate(s,row);
    const kind=s.photo?'document':'visit',id=s.photo?'legacy-photo-'+s.id:s.id;
    const existing=history.find((e:any)=>String(e.actor_uid)===String(session.context.actor_uid)&&e.operation_id===id);
    if(history.length>=500&&!existing)fail('История карточки слишком велика для однозначной сверки.');
    const ownEvent=(e:any)=>String(e.actor_uid)===String(session.context.actor_uid)&&e.contract_ids.includes(cid);
    if(!s.photo&&!existing&&history.some((e:any)=>ownEvent(e)&&e.kind==='visit'&&Math.abs(Date.parse(e.occurred_at)-s.time)<86400000))
      fail('В истории уже есть выезд сотрудника рядом с этим временем. Требуется сверка.');
    let payload:any;
    if(s.photo){
      const linked=history.find((e:any)=>e.payload?.field_visit?.photos?.includes(s.id));
      payload=await upload(row,cid,history);
      if(linked){if(!ownEvent(linked))fail('Для связи фотографии с выездом требуется сверка.');return 'Фото уже принято в составе выезда.';}
    }else{
      payload=visitPayload(row);
      const choices=selected.map(x=>x.id).sort();
      // Pin the explicit association before the first upload, also across tabs and lost replies.
      const name='legacy-visit-photos:'+id;
      if(choices.length>10)fail('К одному выезду можно прикрепить до десяти фотографий.');
      scope();const pinned:string[]=await vault.get(session.owner,name)??choices;
      const photos=[];
      const queue=pinned.length?await list(session):[];
      for(const photoId of pinned){
        const photo=queue.find(x=>x.id===photoId&&x.photo&&x.rep===s.rep);
        if(!photo)fail('Исходное фото для этого выезда отсутствует в очереди.');
        if(history.some((e:any)=>e.operation_id!==id&&e.payload?.field_visit?.photos?.includes(photoId)))fail('Фото уже связано с другим выездом.');
        const photoRow=await readOne(photo,session);
        eligible(photo,photoRow);
        if(photoRow._owner.employee!==row._owner.employee)fail('Для фотографии требуется сверка автора.');
        await upload(photoRow,cid,history);photos.push(photoId);
      }
      scope();const fixed=await vault.update(session.owner,name,(old:string[]|null)=>old??pinned);
      if(JSON.stringify(fixed)!==JSON.stringify(pinned))fail('В другом окне уже выбран состав фотографий. Обновите список.');
      if(photos.length)payload.field_visit.photos=photos;
      if(!Object.keys(payload.field_visit).length)delete payload.field_visit;
    }
    const receipt=await call('POST','command',{schema_version:1,operation_id:id,kind,contract_ids:[cid],
      occurred_at:new Date(s.time).toISOString(),base_revisions:{},payload});
    if(receipt.status!=='accepted'||receipt.operation_id!==id||!receipt.event_id)fail('Квитанция операции не подтверждена.');
    const checked=await locate(s,row);
    if(!checked.history.some((e:any)=>ownEvent(e)&&e.operation_id===id&&e.event_id===receipt.event_id&&e.kind===kind))
      fail('Операция отправлена. Повторите проверку истории.');
    return s.photo?'Фото подтверждено в карточке.':receipt.projection_status==='needs_review'?'Выезд подтверждён в истории. Текущие поля карточки требуют сверки.':'Выезд подтверждён в истории.';
  }
  scan.onclick=async()=>{
    scan.disabled=true;output.replaceChildren();
    try{
      scope();const rows=await list(session);scope();
      if(!rows.length){output.textContent='На этом телефоне прежних выездов и фото вашей учётной записи нет.';return;}
      for(const s of rows){
        scope();
        const record=document.createElement('div');record.className='record';
        const label=document.createElement('p');label.textContent=(s.photo?'Фото':'Выезд')+' · '+s.rep+' · '+new Date(s.time).toLocaleString('ru')+(s.photo?' · '+s.id.slice(-6):'');record.append(label);
        const choices:{photo:Summary;input:HTMLInputElement}[]=[];
        if(!s.photo){
          const details=document.createElement('details'),heading=document.createElement('summary'),body=document.createElement('p');
          heading.textContent='Результат и комментарий';details.append(heading,body);record.append(details);
          details.ontoggle=async()=>{if(!details.open)return;try{const r=await readOne(s,session);scope();
            body.textContent=[r.values?.call_status,r.values?.visit_comment,r.values?.call_comment].filter(Boolean).join(' · ')||'В записи нет результата или комментария.';
          }catch{body.textContent='Не удалось прочитать исходную запись.';}};
          const pinned=await vault.get(session.owner,'legacy-visit-photos:'+s.id);scope();
          for(const p of rows.filter(p=>p.photo&&p.rep===s.rep)){
            const l=document.createElement('label'),input=document.createElement('input');input.type='checkbox';
            if(pinned){input.disabled=true;input.checked=pinned.includes(p.id);}
            l.append(input,document.createTextNode(' Прикрепить фото от '+new Date(p.time).toLocaleString('ru')+' · '+p.id.slice(-6)));
            record.append(l);choices.push({photo:p,input});
          }
        }else{
          const look=document.createElement('button');look.textContent='Посмотреть фото';
          look.onclick=async()=>{
            try{const photo=await picture(await readOne(s,session));scope();
              const img=document.createElement('img');img.alt='Фото из прежней очереди';img.style.maxWidth='100%';
              img.src='data:'+photo.media_type+';base64,'+photo.content_base64;look.replaceWith(img);
            }catch{look.textContent='Предпросмотр недоступен';}
          };record.append(look);
        }
        const submit=document.createElement('button'),status=document.createElement('p');
        submit.textContent=s.photo?'Сверить и сохранить фото в карточке':'Сверить и отправить выезд';
        submit.onclick=async()=>{
          submit.disabled=true;choices.forEach(c=>c.input.disabled=true);
          try{
            if(!navigator.locks)fail('Для восстановления откройте этот адрес в обновлённом браузере на исходном телефоне.');
            const selected=choices.filter(c=>c.input.checked).map(c=>c.photo);
            const result=await navigator.locks.request('crm-legacy-recovery:'+session.owner,()=>{scope();return restore(s,selected);});
            scope();status.textContent=result;
          }
          catch(error){if(box.isConnected&&active()?.owner===session.owner){status.textContent=error instanceof Error?error.message:'Не удалось сверить отправку.';submit.disabled=false;
            const fixed=await vault.get(session.owner,'legacy-visit-photos:'+s.id);choices.forEach(c=>{c.input.disabled=!!fixed;if(fixed)c.input.checked=fixed.includes(c.photo.id);});}}
        };
        record.append(submit,status);output.append(record);
      }
    }catch{if(box.isConnected&&active()?.owner===session.owner)output.textContent='Не удалось прочитать прежние отправки. Повторите проверку на этом телефоне.';}
    finally{scan.disabled=false;}
  };
}
