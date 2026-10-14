// A separate spreadsheet-style tab; opening a row enters the full CRM workspace.
import { api, hard } from "../kit/api";
import { esc, n } from "../kit/dom";
import { setHead, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { openPerson, problem, staleNote } from "./common";

const state = { profile: "hard", employee: "" };
let stateOwner = "";
const ownerStates = new Map<string, typeof state>();
let sequence = 0;
const money = (r: any) => (r.totals || []).map((a: any) => `${Number(a.sum).toLocaleString("ru-RU",{maximumFractionDigits:2})} ${a.unit}`).join(" · ");

async function render(root: HTMLElement, context?: any) {
  const seq=++sequence;
  const shell=await hard.state();
  if(!root.isConnected || seq!==sequence)return;
  const owner=JSON.stringify([shell.server,shell.device?.id]);
  if(stateOwner!==owner){
    if(stateOwner)ownerStates.set(stateOwner,{...state});
    Object.assign(state,ownerStates.get(owner) || {profile:"hard",employee:""});
    stateOwner=owner;
  }
  if(context?.profile === "soft" || context?.profile === "hard") state.profile=context.profile;
  if(context?.employee !== undefined) state.employee=context.employee;
  setHead("Таблица портфеля", "все текущие дела · колонки, фильтры и экспорт");
  root.classList.add('manager-portfolio-sheet');
  root.innerHTML='<div class="skeleton"></div><div class="skeleton"></div>';
  const response=await api("/api/manager/portfolio",{mode:state.profile,employee:state.employee});
  if(!root.isConnected || seq!==sequence)return;
  if(!response.ok){root.innerHTML=problem(response.text || "Портфель не загрузился")+'<button class="btn" data-retry>Повторить</button>';root.querySelector('[data-retry]')?.addEventListener('click',()=>void render(root));return;}
  const d=response.data;
  root.innerHTML=`${staleNote(response.stale)}<div class="toolbar"><div class="chips"><button class="chip" data-profile="hard" aria-pressed="${state.profile==='hard'}">Хард</button><button class="chip" data-profile="soft" aria-pressed="${state.profile==='soft'}">Софт</button></div><span class="spacer"></span>${state.employee?'<button class="btn small" data-all>Вся команда</button>':''}<button class="btn small" data-refresh>Обновить</button></div>
    <div class="crm-context"><span>${n(d.persons)} человек · ${n(d.contracts)} договоров</span><span>Текущие закрепления на ${esc(d.asof)} · ${state.employee ? esc(state.employee) : 'доступная команда'}</span></div><div id="team-portfolio-table"></div>`;
  const rows=d.items || [];
  const key=(r:any)=>JSON.stringify([r.employee,r.rep]);
  let table:DataTable<any>;
  const open=(row:any)=>void openPerson(row.employee,row.rep,undefined,state.profile,{
    cases:table.scope().rows.map(r=>({employee:r.employee,rep:r.rep,profile:state.profile})),
  });
  table=new DataTable(root.querySelector('#team-portfolio-table')!,{id:`team-portfolio-${state.profile}`,key,profile:state.profile as 'hard'|'soft',
    exportName:`Портфель команды · ${state.profile==='soft'?'Софт':'Хард'}`,pageSize:100,
    searchPlaceholder:'ФИО, договор, телефон, адрес, сотрудник',empty:'В этом срезе текущих дел нет.',
    cols:[{key:'client',title:'Должник',value:r=>r.client,html:r=>`<span class="who">${esc(r.client)}</span><span class="sub mono">${esc((r.ids || []).join(' · '))}</span>`},
      {key:'employee',title:'Сотрудник',value:r=>r.employee},{key:'office',title:'Офис',value:r=>r.office && r.office!=='!none' ? r.office : 'Без офиса'},
      {key:'country',title:'Страна',value:r=>r.country || '—'},{key:'status',title:'Результат',value:r=>r.status || 'Не обработан'},
      {key:'debt',title:'Долг по валютам',value:money},{key:'tels',title:'Телефоны',value:r=>(r.tels || []).join(' · ')},
      {key:'address',title:'Адрес',value:r=>r.address},{key:'promise',title:'Обещал до',value:r=>r.promise_date},
      {key:'next',title:'Следующее действие',value:r=>r.next_date},{key:'partners',title:'Контрагенты',value:r=>(r.partners || []).join(' · ')},
      {key:'ids',title:'Договоры',value:r=>(r.ids || []).join(' · ')},],
    defaultHidden:['country','partners'],onRow:open,
    footer:visible=>[`Итого · ${n(visible.length)} человек`,`${n(visible.reduce((s,r)=>s+r.n,0))} договоров`,...Array(10).fill('')],
  });
  await table.restore();if(!root.isConnected || seq!==sequence)return;table.setRows(rows);
  root.querySelectorAll<HTMLElement>('[data-profile]').forEach(b=>b.addEventListener('click',()=>{state.profile=b.dataset.profile!;state.employee='';void render(root)}));
  root.querySelector('[data-refresh]')?.addEventListener('click',()=>void render(root));
  root.querySelector('[data-all]')?.addEventListener('click',()=>{state.employee='';void render(root)});
}

export const teamPortfolio:Section={id:'team-portfolio',title:'Таблица портфеля',icon:'registry',render,leave(){sequence++}};
