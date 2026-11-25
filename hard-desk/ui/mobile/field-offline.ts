type Bridge = {
  state():Promise<{owner:string}>;
  api(method:string,action:string,body?:unknown,query?:unknown):Promise<any>;
};

type Progress = {
  done:number;
  total:number;
  stage:'idle'|'reading'|'saving'|'ready'|'cancelled'|'error';
  cancelled:boolean;
  update?:()=>void;
};
const jobs=new Map<string,Progress>();

async function prepare(crm:Bridge,owner:string,employee:string,job:Progress){
  const active=async()=>!job.cancelled&&(await crm.state()).owner===owner;
  const read=async(action:string,query:unknown)=>{
    const answer=await crm.api('GET',action,undefined,query);
    if(!answer.ok||answer.stale)throw Error('Свежие данные не получены.');
    return answer.data;
  };
  try{
    const people=new Set<string>();
    let offset=0,more=true;
    while(more&&await active()){
      const page=await read('work-portfolio',{search:'',work:'visit',employee_uuid:employee,offset,limit:100});
      for(const row of page.contracts)people.add(row.person_uuid);
      more=page.has_more;
      if(more&&!page.contracts.length)throw Error('Не удалось прочитать все страницы.');
      offset+=100;
    }
    job.total=people.size;job.stage='saving';job.update?.();
    for(const person_uuid of people){
      if(!await active())break;
      await read('card',{person_uuid});job.done++;job.update?.();
    }
    job.stage=await active()?'ready':'cancelled';
  }catch{
    job.stage=await active()?'error':'cancelled';
  }finally{job.update?.();}
}

export function mountFieldOffline(host:HTMLElement,crm:Bridge,owner:string,employee:string){
  const button=document.createElement('button');
  button.textContent='Загрузить выезды офлайн';
  const stop=document.createElement('button');
  stop.textContent='Остановить';
  const status=document.createElement('span');
  status.setAttribute('role','status');
  host.replaceChildren(button,stop,status);
  const job=jobs.get(owner)||{done:0,total:0,stage:'idle',cancelled:false} as Progress;
  jobs.set(owner,job);
  const paint=()=>{
    if(!host.isConnected)return;
    const busy=job.stage==='reading'||job.stage==='saving';
    button.disabled=busy;
    stop.hidden=!busy;
    stop.disabled=job.cancelled;
    status.textContent=({
      idle:'',reading:'Читаю список выездов…',saving:`Сохранено карточек ${job.done} из ${job.total}`,
      ready:job.total?`Готово для офлайна · карточек ${job.total}`:'Назначенных выездов нет.',
      cancelled:`Загрузка остановлена · сохранено карточек ${job.done}`,
      error:`Загрузка прервалась · сохранено карточек ${job.done}. Повторите при связи.`,
    })[job.stage];
  };
  job.update=paint;
  button.onclick=async()=>{
    if((await crm.state()).owner!==owner||job.stage==='reading'||job.stage==='saving')return;
    Object.assign(job,{stage:'reading',done:0,total:0,cancelled:false});paint();
    void prepare(crm,owner,employee,job);
  };
  stop.onclick=()=>{job.cancelled=true;stop.disabled=true;};
  paint();
}
