import {setEmbeddedBridge} from './kit/api';
import {setEmbeddedShell,type Section} from './kit/shell';
import {people} from './analyst/people';
import {managers} from './analyst/managers';
import {accounts} from './analyst/accounts';
import {mail} from './analyst/mail';
import {today} from './analyst/today';
import {maint} from './analyst/system';
import {mountRegistryTools,priority} from './analyst/registry';
import {employeeBoard,staffTasks} from './views/team-work';
import {useAnalystCardTransport} from './analyst/card-access';

const esc=(v:any)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
type Options={owner:string;core:Record<string,(host:HTMLElement,valid:()=>boolean)=>Promise<void|(()=>void)>>;navigate(section:string):Promise<void>};
const groups:Record<string,[string,string][]>= {
  registry:[['table','Общий реестр'],['columns','Колонки'],['priority','Приоритет']],
  exchange:[['files','Импорт и обогащение'],['crm-books','Книги CRM'],['mail','Рассылка'],['queue','Отправки']],
  statistics:[['reports','Отчёты'],['activity','Активность'],['tasks','Задачи']],
  team:[['board','Состав'],['people','Сотрудники'],['managers','Руководители'],['accounts','Аккаунты и устройства'],['scripts','Скрипты']],
  connection:[['connection','Подключение'],['maintenance','Обслуживание']],
};
const legacy:Record<string,Section>={people,managers,accounts,mail,activity:today,priority,maintenance:maint,
  board:employeeBoard('analyst'),tasks:staffTasks('analyst')};
const routes:Record<string,[string,string]>={people:['team','people'],managers:['team','managers'],accounts:['team','accounts'],
  'employee-board':['team','board'],'staff-tasks':['statistics','tasks'],dash:['statistics','activity'],
  registry:['exchange','files'],prio:['registry','priority'],mail:['exchange','mail'],conn:['connection','connection'],maint:['connection','maintenance']};

export async function mountAnalyst(host:HTMLElement,group:string,crm:any,valid:()=>boolean,options:Options){
  let disposed=false,sequence=0,leave:(()=>void)|undefined,current='',panel:HTMLDialogElement|null=null;
  const live=()=>!disposed&&valid(),request=async(action:string,...args:any[])=>{
    if(!live())throw Error('Раздел закрыт.');const value=await crm.admin(action,args,options.owner);
    if(value?.ok===false&&value.text&&!value.error){if(action==='local')return {...value,error:value.text};throw Error(value.text);}
    return value;
  };
  const restoreBridge=setEmbeddedBridge({local:(path,body)=>request('local',path,body),
    pick:(kind,opts)=>request('pick',kind,opts),copy:text=>request('copy',text),
    saveText:(name,content)=>request('saveText',name,content),xlsx:(name,headers,rows)=>request('xlsx',name,headers,rows),
    open:file=>request('open',file),autostart:on=>request('autostart',on),
    state:()=>request('state'),win:action=>crm.win(action)});
  const closePanel=()=>{
    const dialog=panel;
    if(dialog?.querySelector('[data-saving="1"]'))return;
    dialog?.close();dialog?.remove();panel=null;
  };
  const navigate=async(id:string)=>{
    const target=routes[id];if(!target)throw Error('Раздел недоступен в рабочем месте Аналитика.');
    await crm.draft('analyst-tab-'+target[0],target[1],options.owner);
    if(target[0]===group)await show(target[1]);else await options.navigate(target[0]);
  };
  const restoreShell=setEmbeddedShell({go:navigate,refresh:()=>show(current),currentId:()=>legacy[current]?.id||current,
    setHead:(_title,_kicker,tools)=>{const target=host.querySelector('#head-tools');if(target)target.innerHTML=tools;},
    hasSection:id=>!!routes[id],panelOpen:()=>!!document.getElementById('analyst-panel'),closePanel,
    openPanel:(title,body)=>{
      closePanel();const dialog=document.createElement('dialog');panel=dialog;dialog.id='analyst-panel';dialog.className='analyst-tools analyst-panel';
      dialog.innerHTML=`<div class="analyst-panel-head"><h2>${title}</h2><button data-panel-close>Закрыть</button></div><div id="panel-inner"><div class="page">${body}</div></div>`;
      document.body.append(dialog);dialog.querySelector('[data-panel-close]')!.addEventListener('click',closePanel);
      dialog.addEventListener('cancel',e=>{if(dialog.querySelector('[data-saving="1"]'))e.preventDefault();});
      dialog.showModal();return dialog.querySelector('#panel-inner .page')!;
    }});
  useAnalystCardTransport();
  if(!document.getElementById('toast')){const toast=document.createElement('div');toast.id='toast';toast.role='status';document.body.append(toast);}
  host.innerHTML=`<div class="tabs analyst-tabs">${groups[group].map(([id,title])=>`<button data-analyst-tab="${id}">${title}</button>`).join('')}</div><div><div id="head-tools" class="toolbar analyst-tools"></div><div id="analyst-workspace"></div></div>`;
  async function show(id:string){
    if(!live())return;closePanel();leave?.();leave=undefined;
    const turn=++sequence;current=groups[group].some(([key])=>key===id)?id:groups[group][0][0];
    host.querySelector('#head-tools')!.replaceChildren();
    host.querySelectorAll<HTMLElement>('[data-analyst-tab]').forEach(b=>b.classList.toggle('active',b.dataset.analystTab===current));
    await crm.draft('analyst-tab-'+group,current,options.owner);
    if(!live()||turn!==sequence)return;
    const page=document.createElement('div');page.id='view-inner';host.querySelector('#analyst-workspace')!.replaceChildren(page);
    page.classList.toggle('analyst-tools',!options.core[current]);
    const currentView=()=>live()&&turn===sequence&&page.isConnected;
    try{
      let cleanup:void|(()=>void)=undefined;
      if(current==='files'||current==='columns')cleanup=await mountRegistryTools(page,current==='columns');
      else if(options.core[current])cleanup=await options.core[current](page,currentView);
      else await legacy[current].render(page,{});
      if(currentView())leave=typeof cleanup==='function'?cleanup:undefined;else cleanup?.();
    }catch(e){if(currentView())page.innerHTML=`<p class="notice err" role="alert">${esc((e as Error).message)}</p>`;}
  }
  host.querySelectorAll<HTMLButtonElement>('[data-analyst-tab]').forEach(b=>b.onclick=()=>void show(b.dataset.analystTab!));
  const saved=await crm.draft('analyst-tab-'+group,undefined,options.owner);
  await show(typeof saved?.data==='string'?saved.data:groups[group][0][0]);
  return()=>{disposed=true;sequence++;leave?.();closePanel();restoreShell();restoreBridge();host.classList.remove('analyst-tools');};
}
