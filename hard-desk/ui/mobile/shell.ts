import {mobileUpdates} from './updates';
const paths:Record<string,string>={portfolio:'M4 6h16v14H4z M8 6V3h8v3 M4 11h16',registry:'M5 3h14v18H5z M9 8h6 M9 12h6 M9 16h4',statistics:'M4 19h16 M7 15V9 M12 15V4 M17 15v-7',team:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M15 4a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',exchange:'M4 7h15l-4-4 M20 17H5l4 4 M19 7l-4 4 M5 17l4-4',connection:'M8 5h8v14H8z M11 16h2',tasks:'M9 5h11 M9 12h11 M9 19h11 M3 5h1 M3 12h1 M3 19h1',script:'M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h4','active-time':'M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',queue:'M4 12h5l3 4 3-4h5 M6 4h12l3 8v8H3v-8z'};
paths.cabinet=paths.statistics;paths['manager-statistics']=paths.statistics;
const icon=(id:string)=>`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[id]||'M5 7h14 M5 12h14 M5 17h14'}"/></svg>`;
export function mobileShell(){
 document.body.classList.add('mobile-crm');
 const nav=document.querySelector<HTMLElement>('#navigation')!,view=document.querySelector<HTMLElement>('#view')!;
 const expandedActions=new Map<string,boolean>();
 const decorate=()=>{
  const buttons=Array.from(nav.querySelectorAll<HTMLButtonElement>('[data-section]'));
  buttons.forEach((b,i)=>{if(!b.dataset.mobileLabel){b.dataset.mobileLabel=b.textContent||'';b.innerHTML=icon(b.dataset.section!)+`<span>${b.textContent}</span>`;}b.dataset.overflow=String(i>3);});
  let more=nav.querySelector<HTMLButtonElement>('#mobile-more');
  if(buttons.length>4&&!more){more=document.createElement('button');more.id='mobile-more';more.setAttribute('aria-label','Все разделы');more.innerHTML=icon('more')+'<span>Ещё</span>';more.onclick=()=>menu(buttons);nav.append(more);}
  if(buttons.length<=4)more?.remove();more?.classList.toggle('active',buttons.slice(4).some(b=>b.classList.contains('active')));
 };
 function menu(buttons:HTMLButtonElement[]){
  const d=document.createElement('dialog');d.className='mobile-menu';const h=document.createElement('h2');h.textContent='Рабочее место';d.append(h);
  buttons.forEach(source=>{const b=document.createElement('button');b.innerHTML=icon(source.dataset.section!)+`<span>${source.dataset.mobileLabel}</span><span aria-hidden="true">↗</span>`;b.onclick=()=>{d.close();source.click();};d.append(b);});
  const close=document.createElement('button');close.textContent='Закрыть';close.className='mobile-menu-close';close.onclick=()=>d.close();d.append(close);d.onclose=()=>d.remove();document.body.append(d);d.showModal();
 }
 new MutationObserver(records=>{if(records.some(r=>(r.target as HTMLElement).id!=='mobile-more'))decorate();}).observe(nav,{childList:true,attributes:true,attributeFilter:['class'],subtree:true});
 const adapt=()=>{
  const accountSearch=view.querySelector('#account-search'),intro=accountSearch?.parentElement?.querySelector(':scope > p.muted');
  if(intro){const help=document.createElement('details');help.className='mobile-inline-help';help.innerHTML='<summary>Подтверждение доступа</summary>';intro.before(help);help.append(intro);}
  const registry=view.querySelector<HTMLElement>('.registry');
  const cardBar=view.querySelector<HTMLElement>(':scope > .toolbar:has([data-action])');
  if(cardBar&&!cardBar.dataset.mobileReady){
   cardBar.dataset.mobileReady='1';cardBar.classList.add('mobile-card-actions');
   const secondary=Array.from(cardBar.querySelectorAll<HTMLButtonElement>('[data-action]')).filter(b=>!['call','visit','delivery'].includes(b.dataset.action!));
   if(secondary.length){const key=history.state?.crmMobile?.owner+':'+history.state?.crmMobile?.person,more=document.createElement('details');more.className='mobile-card-more';more.innerHTML='<summary>Ещё действия <span aria-hidden="true">＋</span></summary><div></div>';more.open=!!expandedActions.get(key);more.addEventListener('toggle',()=>expandedActions.set(key,more.open));secondary.forEach(b=>more.lastElementChild!.append(b));cardBar.append(more);}
   const refresh=cardBar.querySelector<HTMLButtonElement>('#card-refresh');if(refresh){refresh.textContent='↻';refresh.setAttribute('aria-label','Обновить карточку');}
  }
  if(registry&&!registry.dataset.mobileReady){
   registry.dataset.mobileReady='1';const tools=document.createElement('details');tools.className='mobile-registry-tools';tools.innerHTML='<summary>Фильтры и действия<span aria-hidden="true">＋</span></summary><div class="mobile-tools-body"></div>';
   const body=tools.lastElementChild!,bars=Array.from(registry.querySelectorAll<HTMLElement>(':scope > .toolbar')),search=bars[0];search?.classList.add('mobile-registry-search');search?.after(tools);
   search?.querySelectorAll('select,label').forEach(el=>{if(!el.closest('label')||el.tagName==='LABEL')body.append(el);});
   for(const bar of bars.slice(1,-1))body.append(bar);const allocation=registry.querySelector('#registry-allocation');if(allocation)body.append(allocation);bars.at(-1)?.classList.add('mobile-registry-pagination');
   const save=registry.querySelector('#registry-save');if(save){const strip=document.createElement('div');strip.className='mobile-save-strip';strip.append(save);tools.after(strip);}
   const count=registry.querySelector('#registry-selected-count');
   if(count){
    const selection=document.createElement('div');selection.className='mobile-selection-bar';selection.append(count);
    const actions=document.createElement('button');actions.textContent='Действия с выбранными';actions.onclick=()=>{tools.open=true;requestAnimationFrame(()=>{(registry.querySelector('#registry-allocation')?.firstElementChild||tools).scrollIntoView({block:'center'});});};selection.append(actions);tools.after(selection);
   }
  }
  view.querySelectorAll<HTMLTableElement>('table:not(.registry-grid):not(.manager-report-table)').forEach(t=>{if(t.querySelector('[data-person]')){t.classList.add('mobile-portfolio-table');t.classList.toggle('mobile-manager-portfolio',!t.querySelector('[data-work-contract]'));}const headers=Array.from(t.querySelectorAll('thead th')).map(h=>h.textContent||'');t.querySelectorAll('tbody tr').forEach(r=>Array.from(r.children).forEach((c,i)=>{if(!c.hasAttribute('data-label'))c.setAttribute('data-label',headers[i]||'');}));});
  view.querySelectorAll<HTMLElement>('[data-work-contract] td:nth-child(4),[data-work-contract] td:nth-child(5)').forEach(cell=>cell.classList.toggle('mobile-empty-result',cell.textContent?.trim()==='—'));
  const refresh=view.querySelector('#refresh');if(refresh)refresh.setAttribute('aria-label','Обновить портфель');
  const reload=view.querySelector('#registry-reload');if(reload)reload.setAttribute('aria-label','Обновить реестр');
  const text=document.querySelector('#registry-status')?.textContent||'',strip=view.querySelector<HTMLElement>('.mobile-save-strip');if(strip)strip.hidden=!text||/черновике:\s*0(?:\D|$)/.test(text);
  const selection=view.querySelector<HTMLElement>('.mobile-selection-bar'),selected=view.querySelector('#registry-selected-count')?.textContent||'';if(selection)selection.hidden=!selected||/договоров:\s*0$/.test(selected);
 };
 let scheduled=false;new MutationObserver(()=>{if(!scheduled){scheduled=true;requestAnimationFrame(()=>{scheduled=false;adapt();});}}).observe(view,{childList:true,subtree:true});decorate();adapt();
 const viewport=window.visualViewport;
 const resize=()=>{
  if(!viewport)return;
  document.body.classList.toggle('mobile-keyboard',viewport.height<window.innerHeight*.72);
  document.documentElement.style.setProperty('--mobile-visible-height',viewport.height+'px');
  document.documentElement.style.setProperty('--mobile-keyboard-bottom',Math.max(0,window.innerHeight-viewport.height-viewport.offsetTop)+'px');
 };
 viewport?.addEventListener('resize',resize);viewport?.addEventListener('scroll',resize);resize();
 document.addEventListener('click',e=>{if((e.target as HTMLElement).closest('[data-section]'))document.querySelector('main')?.scrollTo(0,0);});
 mobileUpdates();
}
