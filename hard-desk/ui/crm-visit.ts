type Photo = {document_id: string; filename: string; contract: string};
type Draft = {phone: string; lat: string; lon: string; photos: Photo[]};
export type VisitPhotoQueue={
  stage(contract:string,files:File[]):Promise<void>;
  staged():Promise<string[]>;
  prepare(id:string,allowed:string[]):Promise<Photo>;
  release(id:string):Promise<void>;
};

export function visitEvidence(form: HTMLFormElement, previous: Partial<Draft> | undefined,
  contracts: any[], queue:VisitPhotoQueue, persist:()=>Promise<void>) {
  const box=document.createElement('fieldset');
  box.className='field span visit-evidence';
  box.innerHTML='<legend>На месте выезда</legend><label>Номер телефона<input type="tel" maxlength="80" name="visitPhone"></label>'+
    '<button type="button" data-visit-gps>Отметить точку</button><div class="grid">'+
    '<label>Широта<input name="visitLat" inputmode="decimal"></label><label>Долгота<input name="visitLon" inputmode="decimal"></label></div>'+
    '<label>Добавить фото<input type="file" accept="image/*" name="visitPhoto" multiple></label><ul></ul><p role="status"></p>'+
    '<button type="button" data-visit-retry hidden>Повторить подготовку фото</button>'+
    '<button type="button" data-visit-remove-staged hidden>Убрать необработанные фото</button>';
  form.querySelector('.grid')!.prepend(box);
  const input=(name:string)=>box.querySelector<HTMLInputElement>('[name='+name+']')!;
  const phone=input('visitPhone'),lat=input('visitLat'),lon=input('visitLon'),picker=input('visitPhoto');
  const selected=()=>form.querySelector<HTMLInputElement>('[name=contracts]:checked')?.value || '';
  const current=()=>contracts.find(row=>row.contract_uuid===selected());
  const initialPhone=()=>String(current()?.fields?.phone_main || current()?.fields?.phone || '');
  phone.value=previous?.phone ?? initialPhone();
  lat.value=previous?.lat || '';lon.value=previous?.lon || '';
  let photos:Photo[]=[...(previous?.photos || [])],pending=false,unprepared:string[]=[];
  const status=box.querySelector<HTMLElement>('[role=status]')!;
  const gps=box.querySelector<HTMLButtonElement>('[data-visit-gps]')!;
  const retry=box.querySelector<HTMLButtonElement>('[data-visit-retry]')!;
  const removeStaged=box.querySelector<HTMLButtonElement>('[data-visit-remove-staged]')!;
  const list=box.querySelector('ul')!;
  const setBusy=(busy:boolean)=>{
    pending=busy;picker.disabled=gps.disabled=busy;
    retry.disabled=removeStaged.disabled=busy;
    form.querySelector<HTMLButtonElement>('[type=submit]')!.disabled=busy;
  };
  const display=()=>{
    list.replaceChildren();
    for(const photo of photos){
      const li=document.createElement('li'),remove=document.createElement('button');
      li.append(photo.filename+' ');remove.type='button';remove.textContent='Убрать';
      remove.onclick=async()=>{photos=photos.filter(p=>p!==photo);display();try{await persist();}catch{status.textContent='Не удалось сохранить черновик.';}};
      li.append(remove);list.append(li);
    }
    picker.disabled=pending || !current()?.allowed_actions?.includes('document');
  };
  display();
  form.querySelectorAll<HTMLInputElement>('[name=contracts]').forEach(radio=>radio.addEventListener('change',()=>{
    if(!phone.value || phone.dataset.edited!=='1')phone.value=initialPhone();
    display();form.dispatchEvent(new Event('input'));
  }));
  phone.oninput=()=>{phone.dataset.edited='1';};
  if(previous?.phone!==undefined)phone.dataset.edited='1';
  gps.onclick=()=>{
    if(!navigator.geolocation){status.textContent='Введите координаты вручную.';return;}
    setBusy(true);status.textContent='Определяем точку…';
    navigator.geolocation.getCurrentPosition(async pos=>{
      try{if(!form.isConnected)return;lat.value=pos.coords.latitude.toFixed(6);lon.value=pos.coords.longitude.toFixed(6);await persist();status.textContent='Точка добавлена.';}
      catch{status.textContent='Не удалось сохранить точку в черновике.';}
      finally{setBusy(false);display();}
    },()=>{setBusy(false);display();status.textContent='Телефон не передал точку. Разрешите геолокацию или введите координаты вручную.';},
    {enableHighAccuracy:true,timeout:12000,maximumAge:0});
  };
  async function prepare(added?:{contract:string;files:File[]}){
    setBusy(true);
    try{
      if(added){status.textContent='Сохраняем исходники на телефоне…';await queue.stage(added.contract,added.files);}
      unprepared=await queue.staged();
      if(unprepared.length)status.textContent='Подготавливаем сохранённые фото…';
      let firstError='';
      for(const id of [...unprepared]){
        try{
          if(!photos.some(photo=>photo.document_id===id)){
            const allowed=contracts.filter(row=>row.allowed_actions?.includes('document')).map(row=>row.contract_uuid);
            const photo=await queue.prepare(id,allowed);
            if(!form.isConnected)return;
            photos.push(photo);display();
          }
          await persist();
          await queue.release(id);
          unprepared=unprepared.filter(photo=>photo!==id);
        }catch(e){firstError||=(e as Error).message;}
      }
      if(firstError)status.textContent=firstError;
      else if(added)status.textContent='Фото добавлены к выезду.';
    }catch(e){
      status.textContent=(e as Error).message;
      unprepared=await queue.staged().catch(()=>unprepared);
    }finally{
      picker.value='';retry.hidden=removeStaged.hidden=!unprepared.length;setBusy(false);display();
    }
  }
  picker.onchange=async()=>{
    const id=selected(),files=Array.from(picker.files||[]);
    if(!files.length)return;
    if(!id||new Set([...photos.map(p=>p.document_id),...unprepared]).size+files.length>10){
      status.textContent='Выберите договор и до 10 фотографий.';return;
    }
    await prepare({contract:id,files});
  };
  retry.onclick=()=>void prepare();
  removeStaged.onclick=async()=>{
    setBusy(true);
    try{
      for(const id of unprepared)await queue.release(id);
      unprepared=[];retry.hidden=removeStaged.hidden=true;status.textContent='Необработанные фото убраны.';
    }catch(e){status.textContent=(e as Error).message;}
    finally{setBusy(false);display();}
  };
  queueMicrotask(()=>{if(form.isConnected)void prepare();});
  return {
    draft:():Draft=>({phone:phone.value,lat:lat.value,lon:lon.value,photos:[...photos]}),
    payload:(ids:string[])=>{
      if(pending)throw Error('Дождитесь добавления точки или фото.');
      if(unprepared.length)throw Error('Завершите подготовку фото или уберите необработанные снимки.');
      const data:{phone?:string;point?:{lat:number;lon:number};photos?:string[]}={};
      if(phone.value.trim())data.phone=phone.value.trim();
      if(lat.value.trim() || lon.value.trim()){
        const n=(s:string)=>/^[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)$/.test(s.trim())?Number(s.trim().replace(',','.')):NaN;
        const a=n(lat.value),b=n(lon.value);
        if(!Number.isFinite(a)||Math.abs(a)>90||!Number.isFinite(b)||Math.abs(b)>180)throw Error('Проверьте широту и долготу.');
        data.point={lat:a,lon:b};
      }
      if(photos.length){
        if(ids.length!==1 || photos.some(p=>p.contract!==ids[0]))throw Error('Выберите договор, к которому добавили фото, или уберите фото.');
        data.photos=photos.map(p=>p.document_id);
      }
      return data;
    },
    get busy(){return pending;}
  };
}
