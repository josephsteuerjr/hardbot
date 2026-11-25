const periods = [
  ['today', 'Сегодня'],
  ['yesterday', 'Вчера'],
  ['week', '7 дней'],
  ['month', 'Этот месяц'],
  ['custom', 'Выбрать даты'],
] as const;

type Person = {employee_uuid: string; name: string; call: number; visit: number; country?: string;
  offices?: string[]; by_day?: Record<string, number>; outcomes?: Record<string, number>;
  visit_norm?:number; personal_norm?:number; visit_plan?:number};
const countries: Record<string,string> = {uz:'Узбекистан', tj:'Таджикистан', '!none':'Без региона'};
const cityLabel = (value:string) => value === '!none' ? 'Без офиса' : value;
type Kind = 'call' | 'visit';
type Detail = {event_id:string;at: string; day: string; time_quality: string;
  contracts: {name: string; external_id?: string; person_uuid?:string}[]};
const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, char =>
  ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]!));

export function mountTeamResults(host: HTMLElement, crm: any, valid: () => boolean,
    openCard:(person:string,event:string)=>Promise<void>, report: 'team' | 'visit' = 'team') {
  const showCalls = report === 'team';
  let period = 'today', generation = 0, staff: Person[] = [], query = '', disposed = false;
  let loadedPeriod: Record<string, string> = {period};
  let region: string | undefined, city = '', since = '', through = '', exporting=false;
  let territories: Record<string,string[]> = {};
  const current = () => !disposed && valid();
  const dateText = (day: string) => day.split('-').reverse().join('.');
  const stamp = (date: string) => new Date(date).toLocaleString('ru-RU');
  host.innerHTML = `<div class="toolbar"><label>Период <select id="results-period">${periods.map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select></label><input id="results-person" type="search" aria-label="Найти сотрудника" placeholder="Найти сотрудника"><button id="results-refresh">Обновить</button></div><div id="results-dates" hidden><form class="toolbar"><label>С <input name="date_from" type="date" required></label><label>По <input name="date_to" type="date" required></label><button type="submit">Показать</button></form></div><p id="results-status" role="status"></p><div id="results-table"></div>`;
  const status = host.querySelector<HTMLElement>('#results-status')!;
  const table = host.querySelector<HTMLElement>('#results-table')!;
  const dates = host.querySelector<HTMLDivElement>('#results-dates')!;
  const dateForm = dates.querySelector('form')!;
  const filters = document.createElement('div');
  filters.className='toolbar';
  filters.innerHTML='<label>Регион <select id="results-region"></select></label><label>Город / офис <select id="results-city"></select></label><button id="results-export" disabled>Выгрузить Excel</button><button id="results-visits-export" disabled>Отчёт по выездам</button>';
  status.before(filters);
  const regionSelect=filters.querySelector<HTMLSelectElement>('#results-region')!;
  const citySelect=filters.querySelector<HTMLSelectElement>('#results-city')!;
  const exportButton=filters.querySelector<HTMLButtonElement>('#results-export')!;
  const exportVisits=filters.querySelector<HTMLButtonElement>('#results-visits-export')!;
  if(!showCalls){
    exportButton.remove();
    exportVisits.textContent='Выгрузить Excel по выездам';
  }

  function showFilters(){
    const regions=Object.keys(territories).sort((a,b)=>(countries[a]||a).localeCompare(countries[b]||b,'ru'));
    if(region===undefined || (region && !regions.includes(region))) region=regions[0] || '';
    if(regions.length===1) region=regions[0];
    regionSelect.replaceChildren();
    if(regions.length!==1) regionSelect.add(new Option('Все регионы',''));
    for(const code of regions) regionSelect.add(new Option(countries[code]||code,code));
    regionSelect.value=region;
    const cities=[...new Set(region ? territories[region]||[] : Object.values(territories).flat())].sort((a,b)=>cityLabel(a).localeCompare(cityLabel(b),'ru'));
    if(!cities.includes(city)) city='';
    citySelect.replaceChildren(new Option('Все города',''));
    for(const office of cities) citySelect.add(new Option(cityLabel(office),office));
    citySelect.value=city;
  }
  regionSelect.onchange=()=>{region=regionSelect.value;city='';showFilters();paint();};
  citySelect.onchange=()=>{city=citySelect.value;paint();};
  exportVisits.onclick=async()=>{
    exporting=true;
    exportVisits.disabled=exportButton.disabled=true;
    try{
      const result=await crm.workbook('team-visits',{...loadedPeriod,country:region||'',office:city,employee_search:query});
      if(!result.ok&&!result.cancelled)throw Error(result.text||'Не удалось выгрузить выезды.');
      if(result.ok&&current())status.textContent='Отчёт по выездам сохранён.';
    }catch(error){
      if(current())status.textContent=String((error as Error).message||'Не удалось выгрузить выезды.');
    }finally{
      exporting=false;
      if(current())paint();
    }
  };

  async function request(parameters: Record<string, string | number>) {
    const response = await crm.api('GET', 'manager-statistics', undefined, parameters);
    if (!response.ok || response.stale) throw Error('Не удалось загрузить результаты команды. Повторите попытку.');
    return response.data;
  }

  function showDetails(person: Person, kind: Kind) {
    const selectedPeriod = loadedPeriod;
    const territory = {country:region || '',office:city};
    const dialog = document.createElement('dialog');
    dialog.className = 'manager-report-detail';
    dialog.innerHTML = `<h2>${escape(person.name)} · ${kind === 'call' ? 'Звонки' : 'Выезды'}</h2><p role="status"></p><div data-detail-rows></div><div class="actions"><button data-prev disabled>Назад</button><button data-next disabled>Далее</button><form method="dialog"><button>Закрыть</button></form></div>`;
    const note = dialog.querySelector('p')!;
    const content = dialog.querySelector<HTMLElement>('[data-detail-rows]')!;
    const prev = dialog.querySelector<HTMLButtonElement>('[data-prev]')!;
    const next = dialog.querySelector<HTMLButtonElement>('[data-next]')!;
    let offset = 0;
    async function page(index: number) {
      prev.disabled = next.disabled = true;
      note.textContent = 'Загрузка…';
      try {
        const data = await request({...selectedPeriod, ...territory, employee_uuid: person.employee_uuid, kind, offset: index});
        if (!current() || !dialog.open) return;
        offset = index;
        note.textContent = data.total ? `${offset + 1}–${offset + data.rows.length} из ${data.total}` : 'За этот период записей нет.';
        content.innerHTML = data.rows.length ? `<table class="team-table"><thead><tr><th>Время</th><th>Клиенты и договоры</th></tr></thead><tbody>${data.rows.map((row: Detail) => `<tr><td>${escape(row.time_quality === 'legacy_day' ? dateText(row.day) : stamp(row.at))}</td><td>${row.contracts.map(contract => {
          const label=escape([contract.name,contract.external_id].filter(Boolean).join(' · '));
          return row.event_id&&contract.person_uuid?`<button class="link" data-result-card="${escape(contract.person_uuid)}" data-result-event="${escape(row.event_id)}">${label}</button>`:label;
        }).join('<br>')}</td></tr>`).join('')}</tbody></table>` : '';
        content.querySelectorAll<HTMLButtonElement>('[data-result-card]').forEach(button=>button.onclick=()=>{
          dialog.close();
          void openCard(button.dataset.resultCard!,button.dataset.resultEvent!).catch(error=>{
            if(current())status.textContent=String(error.message||'Не удалось открыть карточку.');
          });
        });
        prev.disabled = offset === 0;
        next.disabled = !data.has_more;
      } catch {
        if (dialog.open) {
          note.textContent = 'Не удалось открыть список. Закройте его и попробуйте снова.';
          content.replaceChildren();
        }
      }
    }
    prev.onclick = () => void page(offset - 50);
    next.onclick = () => void page(offset + 50);
    dialog.addEventListener('close', () => dialog.remove());
    host.append(dialog);
    dialog.showModal();
    void page(0);
  }

  function paint() {
    const shown = staff.filter(person => (!region || person.country===region) && (!city || person.offices?.includes(city))
      && person.name.toLocaleLowerCase('ru-RU').includes(query));
    exportVisits.disabled=exportButton.disabled=exporting||!shown.length;
    if (!shown.length) {
      table.innerHTML = '<p>По выбранным условиям сотрудников нет.</p>';
      return;
    }
    const count = (person: Person, index: number, kind: Kind) => person[kind]
      ? `<button class="link" data-result-person="${index}" data-result-kind="${kind}" aria-label="${escape(person.name)} · ${kind === 'call' ? 'звонки' : 'выезды'} · ${person[kind]}">${person[kind]}</button>` : '0';
    const sum=(kind:Kind)=>shown.reduce((n,person)=>n+person[kind],0);
    const visits=sum('visit');
    const planning=shown.some(person=>person.visit_norm!=null);
    const totalPlan=shown.reduce((total,person)=>total+(person.visit_plan||0),0);
    const performance=(fact:number,plan:number|undefined)=>plan?Number((100*fact/plan).toFixed(1)):null;
    const percent=(fact:number,plan:number|undefined)=>performance(fact,plan)?.toLocaleString('ru-RU')??'—';
    table.innerHTML = `<div class="manager-report-scroll"><table class="team-table"><thead><tr><th>Сотрудник</th>${showCalls?'<th>Звонки</th>':''}<th>Выезды</th>
      ${planning?'<th>Норма в день</th><th>План</th><th>Выполнено, %</th>':''}</tr></thead><tbody>${shown.map((person, index) =>
      `<tr><th scope="row">${escape(person.name)}</th>${showCalls?`<td>${count(person,index,'call')}</td>`:''}<td>${count(person,index,'visit')}</td>
      ${planning?`<td>${person.visit_norm==null?'—':`<button class="link" data-edit-visit-norm="${index}" aria-label="Изменить норму · ${escape(person.name)}">${person.visit_norm}</button>`}</td><td>${person.visit_plan??'—'}</td><td>${percent(person.visit,person.visit_plan)}</td>`:''}</tr>`).join('')}</tbody>
      <tfoot><tr><th scope="row">Итого</th>${showCalls?`<td>${sum('call')}</td>`:''}<td>${visits}</td>${planning?`<td>—</td><td>${totalPlan}</td><td>${percent(visits,totalPlan)}</td>`:''}</tr></tfoot></table></div>`;
    const outcomes=new Map<string,number>(), daily=new Map<string,number>();
    for(const person of shown){
      for(const [key,n] of Object.entries(person.outcomes||{})) outcomes.set(key,(outcomes.get(key)||0)+n);
      for(const [day,n] of Object.entries(person.by_day||{})) daily.set(day,(daily.get(day)||0)+n);
    }
    const days:string[]=[];
    for(let day=Date.parse(since+'T00:00:00Z'),end=Date.parse(through+'T00:00:00Z');day<=end && days.length<31;day+=86400000)
      days.push(new Date(day).toISOString().slice(0,10));
    if(showCalls)exportButton.onclick=async()=>{
      exporting=true;exportVisits.disabled=exportButton.disabled=true;
      try{
        const outcomeKeys=[...outcomes.keys()].sort((a,b)=>a.localeCompare(b,'ru'));
        const headers=['Сотрудник','Страна','Город / офис','Звонки','Выезды',...(planning?['Норма в день','План','Выполнено, %']:[]),...days.map(dateText),...outcomeKeys];
        const rows=shown.map(person=>[person.name,countries[person.country||'!none']||'',
          (city?[city]:person.offices||[]).map(cityLabel).join(', '),person.call,person.visit,
          ...(planning?[person.visit_norm??null,person.visit_plan??null,performance(person.visit,person.visit_plan)]:[]),
          ...days.map(day=>person.by_day?.[day]||0),...outcomeKeys.map(key=>person.outcomes?.[key]||0)]);
        rows.push(['Итого',region?countries[region]||'':'',city?cityLabel(city):'',sum('call'),visits,
          ...(planning?[null,totalPlan,performance(visits,totalPlan)]:[]),
          ...days.map(day=>daily.get(day)||0),...outcomeKeys.map(key=>outcomes.get(key)||0)]);
        const scope=region?countries[region]:'Все регионы';
        const result=await crm.workbook('table',{name:`Результаты ${scope}${city?' '+cityLabel(city):''} ${since}–${through}.xlsx`,headers,rows});
        if(!result.ok&&!result.cancelled)throw Error(result.text||'Не удалось выгрузить Excel.');
        if(result.ok&&current())status.textContent='Excel сохранён.';
      }catch(error){
        if(current())status.textContent=String((error as Error).message||'Не удалось выгрузить Excel.');
      }finally{
        exporting=false;
        if(current())paint();
      }
    };
    const summary='<div class="summary" data-team-totals>'+[
      ['Выезды',visits],['Сотрудников с выездами',shown.filter(p=>p.visit>0).length],...(showCalls?[['Звонки',sum('call')]]:[])
    ].map(([label,n])=>`<div class="metric">${label}<strong>${n}</strong></div>`).join('')+'</div>';
    const breakdown=(title:string,entries:[string,number][])=>entries.length ? `<details><summary>${title}</summary><table class="team-table"><tbody>${entries.map(([label,n])=>`<tr><th scope="row">${escape(label)}</th><td>${n}</td></tr>`).join('')}</tbody></table></details>` : '';
    table.insertAdjacentHTML('afterbegin',summary+
      breakdown('Результаты выездов',[...outcomes].sort((a,b)=>b[1]-a[1]))+
      (days.length>1 ? breakdown('Выезды по дням',days.map(d=>[dateText(d),daily.get(d)||0])) : ''));
    table.querySelectorAll<HTMLButtonElement>('[data-result-person]').forEach(button => {
      button.onclick = () => showDetails(shown[Number(button.dataset.resultPerson)], button.dataset.resultKind as Kind);
    });
    table.querySelectorAll<HTMLButtonElement>('[data-edit-visit-norm]').forEach(button=>{
      button.onclick=()=>editNorm(shown[Number(button.dataset.editVisitNorm)]);
    });
  }

  function editNorm(person:Person){
    const dialog=document.createElement('dialog');
    dialog.className='manager-report-detail';
    dialog.innerHTML=`<h2>${escape(person.name)}</h2><form><label>Личная норма выездов в день
      <input type="number" min="1" max="100" step="1" name="norm" value="${person.personal_norm??''}" placeholder="Общая норма"></label>
      <p role="status"></p><div class="actions"><button type="button" data-norm-close>Закрыть</button><button type="submit">Сохранить</button></div></form>`;
    const form=dialog.querySelector('form')!,message=dialog.querySelector('p')!,save=form.querySelector<HTMLButtonElement>('[type=submit]')!;
    dialog.querySelector<HTMLButtonElement>('[data-norm-close]')!.onclick=()=>dialog.close();
    dialog.onclose=()=>dialog.remove();
    form.onsubmit=async event=>{
      event.preventDefault();
      const value=String(new FormData(form).get('norm')||'');
      const norm=value?Number(value):0;
      if(!form.reportValidity()||!Number.isInteger(norm)||norm>100||norm<0)return;
      save.disabled=true;message.textContent='Сохранение…';
      try{
        const response=await crm.api('POST','manager-access',{resource:'plan',payload:{plan:{[person.name]:norm}}});
        const record=response.data?.employees?.find((row:any)=>row.employee===person.name);
        if(!response.ok||!record||(norm?record.plan!==norm||!record.personal:record.personal))
          throw Error(response.text||'Сервер не подтвердил норму сотрудника.');
        if(dialog.open)dialog.close();
        await load();
      }catch(error){
        if(dialog.open)message.textContent=String((error as Error).message||'Не удалось подтвердить норму.');
      }finally{save.disabled=false;}
    };
    host.append(dialog);dialog.showModal();
  }

  async function load(clear = false, parameters = loadedPeriod) {
    const requestId = ++generation;
    if (clear) {
      staff = [];
      exportVisits.disabled=exportButton.disabled=true;
      table.replaceChildren();
    }
    status.textContent = 'Загрузка…';
    try {
      const data = await request(parameters);
      if (!current() || requestId !== generation) return;
      loadedPeriod = parameters;
      const score=(person:Person)=>person.visit+(showCalls?person.call:0);
      staff = (data.staff as Person[]).sort((a, b) => score(b)-score(a) || a.name.localeCompare(b.name, 'ru'));
      territories=data.territories || {};
      since=data.since;through=data.through;showFilters();
      status.textContent = `${dateText(data.since)}${data.since === data.through ? '' : ' — ' + dateText(data.through)} · Обновлено ${stamp(data.as_of)}`;
      paint();
    } catch (error) {
      if (current() && requestId === generation) status.textContent = (error as Error).message;
    }
  }

  host.querySelector<HTMLSelectElement>('#results-period')!.onchange = event => {
    period = (event.target as HTMLSelectElement).value;
    dates.hidden = period !== 'custom';
    if (dates.hidden) void load(true, {period});
    else {
      const from = dateForm.elements.namedItem('date_from') as HTMLInputElement;
      const to = dateForm.elements.namedItem('date_to') as HTMLInputElement;
      from.value ||= since;
      to.value ||= through;
    }
  };
  dateForm.onsubmit = event => {
    event.preventDefault();
    const data = new FormData(dateForm);
    void load(true, {period, date_from: String(data.get('date_from')), date_to: String(data.get('date_to'))});
  };
  host.querySelector<HTMLInputElement>('#results-person')!.oninput = event => {
    query = (event.target as HTMLInputElement).value.trim().toLocaleLowerCase('ru-RU');
    if (staff.length) paint();
  };
  host.querySelector<HTMLButtonElement>('#results-refresh')!.onclick = () => void load();
  const stop = crm.onChange?.((event: any) => {
    if (event.type === 'live' && event.dirty && loadedPeriod.period === period &&
        !status.textContent?.startsWith('Загрузка') && !host.querySelector('dialog[open]')) void load();
  });
  void load();
  return () => { disposed = true; stop?.(); };
}
