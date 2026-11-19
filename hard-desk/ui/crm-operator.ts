type Entry = {id:number; employee:string; username:string; role:string; status:string; last_login:string|null};
type Device = {account_id:number; kind:string; status:string; total:number};
type Login = {employee:string; username:string; at:string; kind:string; device_kind:string};
type Snapshot = {
  checked_at:string; schema_version:string; cutover:boolean; pending:number; migrated:number;
  scheduler:{ok:boolean; heartbeat:string|null};
  entries:Entry[]; devices:Device[]; recent:Login[];
};

const roles:Record<string,string> = {
  agent:'Сотрудник', manager:'Руководитель', full:'Руководитель',
  security:'Наблюдатель', analyst:'Аналитик',
};
const states:Record<string,string> = {
  active:'Активен', disabled:'Отключён', awaiting:'Ожидает входа',
  ok:'Вход', ok_pending:'Вход на проверке', bad_password:'Неверный пароль',
  locked:'Заблокирован', passwd:'Смена пароля', approved:'Подтверждение',
};
const date = (value:string|null) => value
  ? new Date(value).toLocaleString('ru-RU', {dateStyle:'short', timeStyle:'short'}) : '—';
const cell = (value:unknown) => {
  const td=document.createElement('td');
  td.textContent=String(value ?? '—');
  return td;
};

export async function mountOperator(host:HTMLElement, read:()=>Promise<Snapshot>, valid:()=>boolean) {
  const page=document.createElement('section');
  page.className='operator-workspace';
  page.innerHTML=`
    <div class="operator-header"><span data-checked></span><button data-refresh>Обновить</button></div>
    <div data-metrics class="operator-metrics"></div>
    <section class="operator-entries">
      <div class="operator-controls">
        <label>Раздел <select data-view><option value="entries">Учётные записи</option><option value="recent">Последние входы</option></select></label>
        <label>Поиск <input type="search" data-filter placeholder="Имя или логин"></label>
      </div>
      <p role="status" data-status></p>
      <div class="operator-table" data-rows></div>
    </section>`;
  host.replaceChildren(page);
  const checked=page.querySelector<HTMLElement>('[data-checked]')!;
  const refresh=page.querySelector<HTMLButtonElement>('[data-refresh]')!;
  const metrics=page.querySelector<HTMLElement>('[data-metrics]')!;
  const filter=page.querySelector<HTMLInputElement>('[data-filter]')!;
  const view=page.querySelector<HTMLSelectElement>('[data-view]')!;
  const rows=page.querySelector<HTMLElement>('[data-rows]')!;
  const status=page.querySelector<HTMLElement>('[data-status]')!;
  let current:Snapshot|undefined;

  function renderRows() {
    if(!current)return;
    const recent=view.value==='recent', search=filter.value.trim().toLocaleLowerCase('ru-RU');
    const headings=recent?['Сотрудник','Логин','Событие','Устройство','Время']:
      ['Сотрудник','Логин','Роль','Доступ','ПК','Телефоны','Последний вход'];
    const table=document.createElement('table');
    const header=table.createTHead().insertRow();
    for(const title of headings) {
      const th=document.createElement('th');
      th.scope='col';th.textContent=title;header.append(th);
    }
    const devices=new Map<number,Device[]>();
    for(const entry of current.devices){
      if(!devices.has(entry.account_id))devices.set(entry.account_id,[]);
      devices.get(entry.account_id)!.push(entry);
    }
    const deviceCount=(id:number,kind:string)=>{
      const counts=devices.get(id)?.filter(item=>item.kind===kind)||[];
      const active=counts.find(item=>item.status==='active')?.total||0;
      const pending=counts.find(item=>item.status==='pending')?.total||0;
      return pending?`${active} · ${pending} на проверке`:String(active);
    };
    const matches=(row:Entry|Login)=>!search||(row.employee+' '+row.username).toLocaleLowerCase('ru-RU').includes(search);
    const contents=recent
      ? current.recent.filter(matches).map(row=>[row.employee,row.username,states[row.kind]||row.kind,
        row.device_kind==='phone'?'Телефон':row.device_kind==='desktop'?'ПК':'—',date(row.at)])
      : current.entries.filter(matches).map(row=>[row.employee,row.username,roles[row.role]||row.role,
        states[row.status]||row.status,deviceCount(row.id,'desktop'),deviceCount(row.id,'phone'),date(row.last_login)]);
    const body=table.createTBody();
    for(const values of contents){
      const line=body.insertRow();
      values.forEach((value,index)=>{
        const column=cell(value);column.dataset.label=headings[index];line.append(column);
      });
    }
    rows.replaceChildren(table);
    status.textContent=contents.length?'':'Нет записей.';
  }
  const metric=(label:string,value:string,detail:string,ok?:boolean)=>{
    const block=document.createElement('article');
    block.dataset.state=ok===undefined?'':ok?'ready':'attention';
    const heading=document.createElement('h2'), fig=document.createElement('strong'), note=document.createElement('small');
    heading.textContent=label;fig.textContent=value;note.textContent=detail;
    block.append(heading,fig,note);
    return block;
  };
  async function load() {
    refresh.disabled=true;
    status.textContent='Загрузка сводки…';
    try {
      const data=await read();
      if(!valid())return;
      current=data;
      checked.textContent='Обновлено '+date(data.checked_at);
      metrics.replaceChildren(
        metric('Планировщик',data.scheduler.ok?'Работает':'Требует проверки',date(data.scheduler.heartbeat),data.scheduler.ok),
        metric('Договоры в CRM',data.migrated.toLocaleString('ru-RU'),data.cutover?'Рабочий режим':'Режим подготовки'),
        metric('Записи к переносу',data.pending.toLocaleString('ru-RU'),'Схема PostgreSQL '+(data.schema_version||'—'),data.pending===0),
      );
      renderRows();
    } catch(error) {
      if(!valid())return;
      current=undefined;
      checked.textContent='';
      rows.replaceChildren();
      metrics.replaceChildren();
      status.textContent=String((error as Error).message||'Сводка недоступна.');
    } finally {
      if(valid())refresh.disabled=false;
    }
  }
  refresh.onclick=()=>void load();
  filter.oninput=renderRows;
  view.onchange=renderRows;
  await load();
}
