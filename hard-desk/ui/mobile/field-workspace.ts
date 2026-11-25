import * as vault from './store';
import {mountFieldOffline} from './field-offline';

type Point={lat:number;lon:number};
type Sample=Point & {ts:string;acc:number};
type Trip={id:string;active:boolean;pending:Sample[];received:number;last:string|null};
type Bridge={state():Promise<{owner:string}>;api(method:string,action:string,body?:unknown,query?:unknown):Promise<any>;
  onChange?(listener:(event:{type:string})=>void):()=>void};
type Stop=Point&{client:string;rep:string;person_uuid:string;km?:number};
const tripKey='field-trip';
const activeTrackers=new Map<string,()=>void>();
function geo():Promise<Point>{
  if(!navigator.geolocation)return Promise.reject(Error('Геолокация недоступна в этом окне.'));
  return new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(
    ({coords})=>resolve({lat:coords.latitude,lon:coords.longitude}),()=>reject(Error('Не удалось получить текущую точку.')),
    {enableHighAccuracy:true,timeout:12000,maximumAge:3000}));
}
function button(title:string){
  const value=document.createElement('button');value.type='button';value.textContent=title;return value;
}
function text(tag:string,content:string){
  const value=document.createElement(tag);value.textContent=content;return value;
}
function navigation(point:Point){
  const link=document.createElement('a');link.textContent='Открыть навигацию';
  link.href='https://www.google.com/maps/dir/?api=1&travelmode=driving&dir_action=navigate&destination='+encodeURIComponent(point.lat+','+point.lon);
  link.target='_blank';link.rel='noopener noreferrer';return link;
}

export function mountFieldWorkspace(host:HTMLElement,crm:Bridge,owner:string,employee:string,
  openPerson:(person:string)=>Promise<void>,valid:()=>boolean){
  let alive=true;
  const current=()=>alive&&valid();
  const sameOwner=async()=>(await crm.state()).owner===owner;
  const scope=async()=>{
    if(!current()||(await crm.state()).owner!==owner)throw Error('Вход изменился.');
  };
  const request=async(method:string,action:string,body?:unknown,query?:unknown)=>{
    await scope();
    const r=await crm.api(method,action,body,query);
    await scope();
    if(!r.ok)throw Error(r.text||'Сохранено на телефоне. Повторите при связи.');
    return r.data;
  };
  const status=text('p','');status.setAttribute('role','status');
  const work=(control:HTMLButtonElement,perform:()=>Promise<void>,update?:()=>Promise<unknown>)=>{
    control.onclick=async()=>{
      control.disabled=true;status.textContent='';
      try{await perform();}catch(error){if(current())status.textContent=(error as Error).message;}
      finally{control.disabled=false;if(update)await update();}
    };
  };
  const route=text('section',''),list=text('ol',''),routeStatus=text('p',''),routeGo=button('Построить маршрут');
  const offline=text('div','');offline.className='toolbar';
  route.append(text('h2','Маршрут на день'),routeGo,routeStatus,list,offline);
  mountFieldOffline(offline,crm,owner,employee);
  work(routeGo,async()=>{
    let start:Point|undefined;
    try{start=await geo();}catch{/* The existing route planner can start at the first point. */}
    const data=await request('GET','field-route',undefined,start||{});
    list.replaceChildren();
    for(const stop of data.order as Stop[]){
      const row=text('li',''),open=button(stop.client+' · '+stop.rep);
      open.onclick=()=>void openPerson(stop.person_uuid);
      row.append(open,' ',navigation(stop));list.append(row);
    }
    routeStatus.textContent=data.order.length?
      (data.km!=null?`${data.order.length} адресов · ${Number(data.km).toLocaleString('ru-RU')} км`:`${data.order.length} адресов из текущего портфеля`):
      'Назначенных адресов с координатами нет.';
  });
  const navigatorBox=text('section',''),search=document.createElement('input'),find=button('Найти точку'),destination=text('div','');
  search.type='search';search.placeholder='Адрес или координаты';search.setAttribute('aria-label','Адрес или координаты');search.maxLength=200;
  const searchBar=text('div','');searchBar.className='toolbar';searchBar.append(search,find);
  navigatorBox.append(text('h2','Навигатор'),searchBar,destination);
  work(find,async()=>{
    const q=search.value.trim();if(!q)throw Error('Введите адрес или координаты.');
    const found=await request('GET','field-geocode',undefined,{q});
    const point={lat:found.lat,lon:found.lon};
    destination.replaceChildren(text('p',found.pretty||q),navigation(point));
    const estimate=button('Рассчитать расстояние');destination.append(' ',estimate);
    work(estimate,async()=>{
      const start=await geo();
      const result=await request('GET','field-directions',undefined,{...start,dlat:point.lat,dlon:point.lon});
      if(typeof result.km==='number')estimate.replaceWith(text('p',`${result.km} км${result.min!=null?' · '+result.min+' мин':''}`));
    });
  });
  const marks=text('section',''),markName=document.createElement('input'),markList=text('ul','');
  markName.maxLength=120;markName.placeholder='Название метки';markName.setAttribute('aria-label','Название метки');
  const save=button('Сохранить текущую точку'),refresh=button('Показать метки');
  const markBar=text('div','');markBar.className='toolbar';markBar.append(markName,save,refresh);
  marks.append(text('h2','Метки'),markBar,markList);
  async function loadMarks(){
    const data=await request('GET','field-marks');
    markList.replaceChildren(...data.markers.map((mark:Point&{label:string})=>{
      const row=text('li',mark.label||`${mark.lat}, ${mark.lon}`);row.append(' ',navigation(mark));return row;
    }));
  }
  work(refresh,loadMarks);
  work(save,async()=>{
    let draft=await vault.get(owner,'field-pending-marker');
    if(!draft){
      const mark={...await geo(),label:markName.value.trim(),op_id:crypto.randomUUID()};
      await scope();draft=await vault.update(owner,'field-pending-marker',prior=>prior??mark);
    }
    const receipt=await request('POST','field-marks',draft);
    if(!receipt.ok||receipt.id==null)throw Error('Сервер не подтвердил метку.');
    await vault.update(owner,'field-pending-marker',prior=>prior?.op_id===draft.op_id?null:prior);
    markName.value='';await loadMarks();
  });

  const track=text('section',''),trackState=text('p',''),start=button('Начать трек'),stop=button('Остановить трек'),send=button('Отправить точки');
  track.append(text('h2','Трек смены'),text('p','Точки записываются, пока CRM видна на экране.'),trackState,start,' ',stop,' ',send);
  let watch:number|undefined,flushing:Promise<void>|undefined;
  function stopWatching(){if(watch!==undefined)navigator.geolocation.clearWatch(watch);watch=undefined;}
  async function state(){
    const trip:Trip|null=await vault.get(owner,tripKey);
    if(!current())return trip;
    trackState.textContent=trip?`${trip.active?'Запись включена':'Запись остановлена'} · принято ${trip.received} · ждут отправки ${trip.pending.length}`:'Запись остановлена.';
    start.disabled=!!trip?.active||!!trip?.pending.length;stop.disabled=!trip?.active;send.disabled=!trip?.pending.length;
    return trip;
  }
  const flush=()=>{
    flushing??=(async()=>{
      const transmit=async()=>{
        while(await sameOwner()){
          const trip:Trip|null=await vault.get(owner,tripKey);
          if(!trip?.pending.length)return;
          const points=trip.pending.slice(0,50);
          const response=await crm.api('POST','field-track',{session_id:trip.id,points});
          if(!await sameOwner()||!response.ok||!response.data?.ok)throw Error(response.text||'Ответ по треку не подтверждён.');
          const sent=new Set(points.map(x=>x.ts));
          await vault.update(owner,tripKey,(data:Trip)=>data?.id===trip.id?
            {...data,pending:data.pending.filter(x=>!sent.has(x.ts)),received:data.received+points.length}:data);
          await state();
        }
      };
      if(navigator.locks)await navigator.locks.request('crm-trip-send:'+owner,transmit);
      else await transmit();
    })().finally(()=>{flushing=undefined;});
    return flushing;
  };
  function watchTrip(){
    if(watch!==undefined||!navigator.geolocation)return;
    watch=navigator.geolocation.watchPosition(async position=>{
      if(document.hidden)return;
      try{
        if(!await sameOwner()){shutdown();return;}
        const stamp=new Date(position.timestamp).toISOString();
        const coords=position.coords;
        if(!Number.isFinite(coords.accuracy)||coords.accuracy>250||(!coords.latitude&&!coords.longitude))return;
        const sample={ts:stamp,lat:coords.latitude,lon:coords.longitude,acc:coords.accuracy};
        const result:Trip|null=await vault.update(owner,tripKey,(trip:Trip|null)=>{
          if(!trip?.active||trip.pending.length>=5000||(trip.last&&Date.parse(stamp)-Date.parse(trip.last)<20000))return trip;
          return {...trip,last:stamp,pending:[...trip.pending,sample]};
        });
        if(!result?.active)stopWatching();
        await state();
        if(result&&result.pending.length>=5&&navigator.onLine)void flush().catch(()=>{});
      }catch{stopWatching();if(current())status.textContent='Не удалось сохранить точку на телефоне.';}
    },()=>{if(current())status.textContent='Нет доступа к геолокации. Проверьте разрешение браузера.';},
    {enableHighAccuracy:true,maximumAge:0,timeout:25000});
  }
  const stopIdentity=crm.onChange?.(event=>{if(event.type==='identity')void sameOwner().then(matches=>{if(!matches)shutdown();});});
  const restoreConnection=()=>{if(navigator.onLine)void flush().catch(()=>{});};
  window.addEventListener('online',restoreConnection);
  function shutdown(){
    stopWatching();stopIdentity?.();window.removeEventListener('online',restoreConnection);
    if(activeTrackers.get(owner)===shutdown)activeTrackers.delete(owner);
  }
  activeTrackers.get(owner)?.();
  activeTrackers.set(owner,shutdown);
  work(start,async()=>{
    await scope();await geo();await scope();
    await vault.update(owner,tripKey,(prior:Trip|null)=>prior?.active||prior?.pending.length?prior:
      {id:crypto.randomUUID(),active:true,pending:[],received:0,last:null});
    watchTrip();await state();
  },state);
  work(stop,async()=>{
    await scope();stopWatching();
    await vault.update(owner,tripKey,(trip:Trip|null)=>trip?{...trip,active:false}:null);
    await state();if(navigator.onLine)await flush();
  },state);
  work(send,flush,state);
  host.replaceChildren(status,route,navigatorBox,marks,track);
  void state().then(trip=>{if(trip?.active&&current())watchTrip();}).catch(()=>{});
  return ()=>{alive=false;};
}
