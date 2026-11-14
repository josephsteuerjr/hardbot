type Cell = {row:number; col:number};
type Pending = {values:Record<string,any>; clear:string[]; base_revisions:Record<string,number>};
const escape = (v:any) => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const keyOf = (p:Cell) => `${p.row}:${p.col}`;
function typed(field:any, raw:string) {
  if(field.type==='date' && raw) {
    const parts=raw.match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
    const iso=parts?`${parts[3]}-${parts[2]}-${parts[1]}`:raw;
    const value=new Date(iso+'T00:00:00Z');
    if(!/^\d{4}-\d{2}-\d{2}$/.test(iso) || !Number.isFinite(+value) || value.toISOString().slice(0,10)!==iso)
      throw Error(`${field.title}: укажите дату ДД.ММ.ГГГГ.`);
    return iso;
  }
  if(field.type==='money' && raw) {
    const value=raw.replace(/[ \u00a0]/g,'').replace(',','.');
    if(!/^-?\d{1,15}(\.\d{1,2})?$/.test(value))throw Error(`${field.title}: сумма с двумя знаками после запятой.`);
    return value;
  }
  if(field.options?.length && raw && !field.options.includes(raw))throw Error(`${field.title}: выберите значение из списка.`);
  if(raw.length>32700)throw Error(`${field.title}: слишком длинный текст.`);
  return raw;
}

/** Own protected drafts/preferences; no Excel formatting, formulas or card navigation. */
export async function mountRegistry(host:HTMLElement, crm:any, isCurrent:()=>boolean=()=>true, options:{manager?:boolean}={}) {
  let disposed=false, generation=0, data:any, active:Cell={row:0,col:0}, anchor:Cell={row:0,col:0};
  let editing:HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement|null=null;
  let prefs=(await crm.draft('registry-view')).data || {};
  let colors:Record<string,string>=(await crm.draft('registry-colors')).data || {};
  let pending:Record<string,Pending>=(await crm.draft('registry-input')).data || {};
  let entries:any[]=[],busy=false;
  let preferenceSave:ReturnType<typeof setTimeout>|undefined;
  let resizeStop:(()=>void)|undefined;
  const snapshots=new Map<string,any>();
  const validView=options.manager?'all':['mine','all'].includes(prefs.view)?prefs.view:'mine';
  prefs={view:validView,search:String(prefs.search || ''),sort:prefs.sort || 'contract_id',direction:prefs.direction==='desc'?'desc':'asc',offset:Math.max(0,Number(prefs.offset)||0),
    left:Math.max(0,Number(prefs.left)||0),top:Math.max(0,Number(prefs.top)||0),
    active:prefs.active,anchor:prefs.anchor,
    widths:Object.fromEntries(Object.entries(prefs.widths || {}).filter(([key,width])=>key.length<160 && typeof width==='number' && Number.isFinite(width) && width>=80 && width<=640)),
    zoom:[80,90,100,110,125,150].includes(prefs.zoom)?prefs.zoom:100};
  if(disposed || !isCurrent())return ()=>{};
  host.innerHTML=`<div class="registry"><div class="toolbar"><select id="registry-scope" aria-label="Область реестра"><option value="mine">Мой реестр</option><option value="all">Общий просмотр</option></select><input id="registry-search" type="search" placeholder="Клиент, договор, телефон"><button id="registry-find">Найти</button><button id="registry-reload">Обновить</button><label>Масштаб <select id="registry-zoom">${[80,90,100,110,125,150].map(n=>`<option value="${n}">${n}%</option>`).join('')}</select></label></div>
    <div class="toolbar"><button id="registry-edit">Изменить</button><button id="registry-clear">Очистить</button><button id="registry-cancel">Отменить ввод</button><button id="registry-save" class="primary">Сохранить правки</button><button id="registry-review">Сверить отправку</button><label>Пометка строки <select id="registry-color"><option value="">Без цвета</option><option value="yellow">Жёлтый</option><option value="red">Красный</option><option value="blue">Голубой</option></select></label></div>
    <p id="registry-status" role="status"></p><div class="registry-scroll" tabindex="-1"></div><div class="toolbar"><button id="registry-prev">Назад</button><span id="registry-page"></span><button id="registry-next">Далее</button><button id="registry-reset-widths" class="link">Сбросить ширины</button></div>
    <dialog id="registry-preview"><h2>Правки реестра</h2><div class="registry-preview-values"></div><div class="actions"><button id="registry-apply" class="primary">Сохранить на компьютере и отправить</button><button id="registry-preview-cancel">Вернуться к вводу</button></div></dialog></div>`;
  const $=<T extends HTMLElement=HTMLElement>(id:string)=>host.querySelector<T>('#'+id)!;
  if(options.manager){
    host.classList.add('manager-register-data');
    $('registry-scope').innerHTML='<option value="all">Общий реестр</option>';
  }
  const scroll=host.querySelector<HTMLElement>('.registry-scroll')!;
  const protectedInput=document.createElement('div');$('registry-status').after(protectedInput);
  let revokedInput=(await crm.draft('registry-revoked-input')).data;
  function showProtectedInput(){
    protectedInput.replaceChildren();if(!revokedInput)return;
    const details=document.createElement('details'),summary=document.createElement('summary'),text=document.createElement('pre'),clear=document.createElement('button');
    summary.textContent='Ввод сохранён после изменения назначения · '+revokedInput.field;
    text.textContent=revokedInput.value;clear.textContent='Убрать сохранённый ввод';
    clear.onclick=async()=>{const r=await crm.draft('registry-revoked-input',null);if(r.ok){revokedInput=null;showProtectedInput();}};
    details.append(summary,text,clear);protectedInput.append(details);
  }
  showProtectedInput();
  const status=(text:string)=>{if(!disposed && isCurrent())$('registry-status').textContent=text;};
  const storeInput=async()=>{const r=await crm.draft('registry-input',pending);if(!r.ok)throw Error(r.text || 'Не удалось сохранить ввод на компьютере.');};
  const protect=(fn:()=>any)=>()=>{Promise.resolve().then(fn).catch(e=>status(e.message));};
  const savePreferences=async()=>{const result=await crm.draft('registry-view',prefs);if(!result.ok)throw Error(result.text || 'Не удалось сохранить настройки таблицы.');};
  const schedulePreferences=()=>{clearTimeout(preferenceSave);preferenceSave=setTimeout(()=>{if(!disposed && isCurrent())void savePreferences().catch(e=>status(e.message));},200);};
  const defaultWidth=(key:string)=>key==='$state'?140:key.includes('comment')||key.includes('history')?280:180;
  const width=(key:string)=>Object.hasOwn(prefs.widths,key)?prefs.widths[key]:defaultWidth(key);
  function layout() {
    scroll.style.setProperty('--registry-scale',String(prefs.zoom/100));
    scroll.style.setProperty('--registry-state-width',`${width('$state')*prefs.zoom/100}px`);
    const keys=['$state',...data.columns.map((f:any)=>f.key)];
    const table=scroll.querySelector<HTMLTableElement>('table');
    if(table)table.style.width=keys.reduce((n,key)=>n+width(key)*prefs.zoom/100,0)+'px';
    scroll.querySelectorAll<HTMLElement>('[data-column-width]').forEach(el=>{el.style.width=width(el.dataset.columnWidth!)*prefs.zoom/100+'px';});
    scroll.querySelectorAll<HTMLElement>('[data-resize]').forEach(el=>el.setAttribute('aria-valuenow',String(width(el.dataset.resize!))));
  }
  function rememberSelection() {
    const location=(p:Cell)=>({contract_uuid:data.rows[p.row]?.contract_uuid,field:data.columns[p.col]?.key});
    prefs.active=location(active);prefs.anchor=location(anchor);schedulePreferences();
  }
  function restoreCell(saved:any,fallback:Cell):Cell {
    const row=data.rows.findIndex((r:any)=>r.contract_uuid===saved?.contract_uuid),col=data.columns.findIndex((f:any)=>f.key===saved?.field);
    return {row:Math.max(0,Math.min(data.rows.length-1,row<0?fallback.row:row)),col:Math.max(0,Math.min(data.columns.length-1,col<0?fallback.col:col))};
  }
  const bounds=()=>({r0:Math.min(active.row,anchor.row),r1:Math.max(active.row,anchor.row),c0:Math.min(active.col,anchor.col),c1:Math.max(active.col,anchor.col)});
  const rowEntry=(id:string)=>entries.filter(e=>e.action==='registry-edit' && e.state!=='accepted' && !e.reviewed_at && e.body.rows.some((p:any)=>p.contract_uuid===id));
  const pendingCount=()=>Object.values(pending).reduce((n,p)=>n+Object.keys(p.values).length,0);
  const value=(r:any,key:string)=>{
    if(pending[r.contract_uuid] && key in pending[r.contract_uuid].values)return pending[r.contract_uuid].values[key];
    const queued=rowEntry(r.contract_uuid).at(-1)?.body.rows.find((p:any)=>p.contract_uuid===r.contract_uuid);
    return queued && key in queued.values?queued.values[key]:key==='contract_id'?r.external_id:r.fields[key];
  };
  function showValue(v:any,f:any) {
    if(v===null || v===undefined || v==='')return '—';
    if(f.type==='date' && /^\d{4}-\d{2}-\d{2}$/.test(v))return String(v).split('-').reverse().join('.');
    return String(v);
  }
  function select(cell:Cell,extend=false,focus=true,remember=true) {
    if(!data?.rows.length)return;
    active={row:Math.max(0,Math.min(data.rows.length-1,cell.row)),col:Math.max(0,Math.min(data.columns.length-1,cell.col))};
    if(!extend)anchor={...active};
    const b=bounds();
    scroll.querySelectorAll<HTMLElement>('[data-cell]').forEach(el=>{
      const [r,c]=el.dataset.cell!.split(':').map(Number);
      const selected=r>=b.r0&&r<=b.r1&&c>=b.c0&&c<=b.c1;
      el.classList.toggle('selected',selected);el.setAttribute('aria-selected',String(selected));
      el.tabIndex=r===active.row&&c===active.col?0:-1;
    });
    if(focus)scroll.querySelector<HTMLElement>(`[data-cell="${keyOf(active)}"]`)?.focus({preventScroll:true});
    $('registry-color').setAttribute('data-contract',data.rows[active.row].contract_uuid);
    $<HTMLSelectElement>('registry-color').value=colors[data.rows[active.row].contract_uuid] || '';
    if(remember)rememberSelection();
  }
  function paintStatus() {
    if(!data)return;
    for(const row of data.rows) {
      const queued=rowEntry(row.contract_uuid);
      const el=scroll.querySelector<HTMLElement>(`[data-row="${row.contract_uuid}"] .registry-row-state`);
      if(el)el.textContent=queued.some(e=>e.state==='attention')?'Требует сверки':queued.length?'Сохранено на устройстве':pending[row.contract_uuid]?'Редактируется':'Подтверждено';
    }
  }
  async function sync() {
    const result=await crm.queue();
    if(disposed || !isCurrent() || !result.ok)return;
    entries=result.data.entries || [];
    // Update only acknowledged cells. Keep active editor and staged text intact.
    for(const entry of entries.filter((e:any)=>e.action==='registry-edit' && e.state==='accepted')) {
      for(const ack of entry.result?.rows || []) {
        const row=data?.rows.find((r:any)=>r.contract_uuid===ack.contract_uuid);
        if(!row)continue;
        const newer=Object.entries(ack.revisions).some(([key,rev])=>Number(rev)>Number(row.revisions[key] || 0));
        if(newer){Object.assign(row.fields,ack.values);Object.assign(row.revisions,ack.revisions);}
      }
    }
    paintStatus();
  }
  function paint() {
    resizeStop?.();
    editing=null;
    const resizer=(key:string,title:string)=>`<span class="registry-resizer" role="separator" tabindex="0" aria-orientation="vertical" aria-label="Ширина: ${escape(title)}" aria-valuemin="80" aria-valuemax="640" data-resize="${escape(key)}" title="Потяните границу. Стрелки меняют ширину, Home сбрасывает."></span>`;
    scroll.innerHTML=`<table class="registry-grid" role="grid" aria-label="Реестр договоров"><colgroup>${['$state',...data.columns.map((f:any)=>f.key)].map(key=>`<col data-column-width="${escape(key)}">`).join('')}</colgroup><thead><tr><th class="registry-row-state">Сохранение${resizer('$state','Сохранение')}</th>${data.columns.map((f:any)=>`<th><button data-sort="${escape(f.key)}">${escape(f.title)}${prefs.sort===f.key?(prefs.direction==='asc'?' ↑':' ↓'):''}</button>${resizer(f.key,f.title)}</th>`).join('')}</tr></thead><tbody>${data.rows.map((row:any,r:number)=>`<tr data-row="${escape(row.contract_uuid)}" data-color="${colors[row.contract_uuid] || ''}"><td class="registry-row-state"></td>${data.columns.map((f:any,c:number)=>`<td role="gridcell" data-cell="${r}:${c}" data-field="${escape(f.key)}" tabindex="-1" aria-readonly="${!row.writable_fields.includes(f.key)}" class="${row.writable_fields.includes(f.key)?'writable':'readonly'}"><span>${escape(showValue(value(row,f.key),f))}</span>${f.key==='contract_id'?`<small class="registry-creditor">${escape(row.fields.partner || row.creditor_id)}</small>`:''}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    layout();
    $('registry-page').textContent=`${data.total?Number(prefs.offset)+1:0}–${Math.min(Number(prefs.offset)+data.rows.length,data.total)} из ${data.total}`;
    $<HTMLButtonElement>('registry-prev').disabled=!prefs.offset;
    $<HTMLButtonElement>('registry-next').disabled=!data.has_more;
    $<HTMLSelectElement>('registry-color').disabled=!data.rows.length;
    active=restoreCell(prefs.active,active);anchor=restoreCell(prefs.anchor,anchor);
    select(active,true,false,false);paintStatus();
    scroll.scrollLeft=prefs.left;scroll.scrollTop=prefs.top;
    scroll.querySelectorAll<HTMLElement>('[data-resize]').forEach(handle=>{
      const key=handle.dataset.resize!;
      const change=(next:number)=>{prefs.widths[key]=Math.max(80,Math.min(640,Math.round(next)));layout();};
      handle.onpointerdown=e=>{
        if(e.button!==0)return;e.preventDefault();e.stopPropagation();resizeStop?.();
        const initial=width(key),start=e.clientX,pointer=e.pointerId;
        handle.setPointerCapture(pointer);scroll.classList.add('resizing');
        handle.onpointermove=next=>{if(next.pointerId===pointer)change(initial+(next.clientX-start)/(prefs.zoom/100));};
        const finish=()=>{handle.onpointermove=null;handle.onpointerup=null;handle.onpointercancel=null;handle.onlostpointercapture=null;scroll.classList.remove('resizing');resizeStop=undefined;if(handle.hasPointerCapture(pointer))handle.releasePointerCapture(pointer);schedulePreferences();};
        handle.onpointerup=finish;handle.onpointercancel=finish;handle.onlostpointercapture=finish;resizeStop=finish;
      };
      handle.ondblclick=e=>{e.preventDefault();e.stopPropagation();delete prefs.widths[key];layout();schedulePreferences();};
      handle.onkeydown=e=>{
        if(!['ArrowLeft','ArrowRight','Home'].includes(e.key))return;
        e.preventDefault();e.stopPropagation();
        if(e.key==='Home')delete prefs.widths[key];else change(width(key)+(e.key==='ArrowRight'?1:-1)*(e.shiftKey?50:10));
        layout();schedulePreferences();
      };
    });
    scroll.querySelectorAll<HTMLElement>('[data-cell]').forEach(el=>{
      const position=()=>{const [row,col]=el.dataset.cell!.split(':').map(Number);return {row,col};};
      el.onmousedown=e=>{if(editing && el.contains(editing))return;select(position(),e.shiftKey);};
      el.ondblclick=()=>beginEdit(position());
    });
    scroll.querySelectorAll<HTMLButtonElement>('[data-sort]').forEach(b=>{
      if(data.columns.find((f:any)=>f.key===b.dataset.sort)?.sortable===false){b.disabled=true;b.tabIndex=-1;return;}
      b.onclick=protect(async()=>{
      prefs.direction=prefs.sort===b.dataset.sort&&prefs.direction==='asc'?'desc':'asc';prefs.sort=b.dataset.sort;prefs.offset=0;await load();
    });});
  }
  async function load() {
    if(editing)editing.blur();
    const n=++generation;
    await savePreferences();
    const result=await crm.api('GET','registry',undefined,{view:prefs.view,search:prefs.search,sort:prefs.sort,direction:prefs.direction,offset:prefs.offset,limit:50});
    if(disposed || !isCurrent() || n!==generation)return;
    if(!result.ok)throw Error(result.text || 'Не удалось прочитать реестр.');
    data=result.data;
    for(const row of data.rows)snapshots.set(row.contract_uuid,row);
    await sync();if(disposed || !isCurrent() || n!==generation)return;
    paint();status(result.stale?'Сохранённая копия. Правки будут проверены сервером после подключения.':`Правок в черновике: ${pendingCount()}`);
  }
  function stage(r:any,f:any,raw:any) {
    if(!r.writable_fields.includes(f.key))throw Error(`${f.title}: поле доступно только для чтения.`);
    if(rowEntry(r.contract_uuid).length)throw Error('В строке есть отправка. Дождитесь подтверждения или сверьте её в очереди.');
    const p=pending[r.contract_uuid] ||= {values:{},clear:[],base_revisions:{}};
    if(!(f.group in p.base_revisions))p.base_revisions[f.group]=r.revisions[f.group] || 0;
    p.values[f.key]=raw;
    p.clear=p.clear.filter(k=>k!==f.key);if(raw===null)p.clear.push(f.key);
  }
  function beginEdit(p:Cell=active) {
    if(busy || editing || !data?.rows.length)return;
    select(p);const row=data.rows[p.row],f=data.columns[p.col];
    if(!row.writable_fields.includes(f.key) || rowEntry(row.contract_uuid).length){status('Ячейка доступна только для чтения или ожидает сверки.');return;}
    const el=scroll.querySelector<HTMLElement>(`[data-cell="${keyOf(p)}"]`)!;
    const original=structuredClone(pending[row.contract_uuid] || null);
    const initial=value(row,f.key);
    const editor=document.createElement(f.options.length?'select':'textarea');
    if(editor instanceof HTMLSelectElement)editor.innerHTML='<option value="">Пустой текст</option>'+f.options.map((v:string)=>`<option>${escape(v)}</option>`).join('');
    editor.value=initial ?? '';editor.setAttribute('aria-label',f.title);editing=editor;el.replaceChildren(editor);editor.focus();
    if(editor instanceof HTMLTextAreaElement)editor.select();
    let cancelled=false;
    editor.oninput=()=>{try{stage(row,f,editor.value);void storeInput().catch(e=>status(e.message));paintStatus();}catch(e){status((e as Error).message);}};
    editor.onkeydown=e=>{
      if(e.key==='Escape'){
        e.preventDefault();cancelled=true;
        if(original)pending[row.contract_uuid]=original;else delete pending[row.contract_uuid];
        void storeInput().catch(e=>status(e.message));editor.blur();
      }else if(e.key==='Tab' || e.key==='Enter'&&!e.shiftKey){
        e.preventDefault();editor.blur();select({row:p.row+(e.key==='Enter'?1:0),col:p.col+(e.key==='Tab'?(e.shiftKey?-1:1):0)});
      }
    };
    editor.onblur=()=>{
      if(!cancelled && editor.value!==String(initial ?? '')){stage(row,f,editor.value);void storeInput().catch(e=>status(e.message));}
      editing=null;el.innerHTML=`<span>${escape(showValue(value(row,f.key),f))}</span>`;paintStatus();
      status(cancelled?'Незавершённый ввод отменён.':`Правок в черновике: ${pendingCount()}`);
    };
  }
  async function clearSelection() {
    if(!data?.rows.length)return;
    if(editing)editing.blur();
    const b=bounds();const batch=[];
    for(let r=b.r0;r<=b.r1;r++)for(let c=b.c0;c<=b.c1;c++){
      const row=data.rows[r],f=data.columns[c];
      if(!row.writable_fields.includes(f.key)||rowEntry(row.contract_uuid).length)throw Error('Диапазон содержит недоступные ячейки. Ничего не очищено.');
      batch.push({row,f});
    }
    for(const {row,f} of batch)stage(row,f,null);
    await storeInput();paint();status(`Явная очистка: ${batch.length} ячеек. Проверьте и сохраните правки.`);
  }
  async function preview() {
    if(editing)editing.blur();await storeInput();await sync();
    $<HTMLButtonElement>('registry-apply').textContent='Сохранить на компьютере и отправить';
    const missing=Object.keys(pending).filter(id=>!snapshots.has(id));
    if(missing.length){
      const result=await crm.api('GET','registry',undefined,{view:'all',ids:missing.join(','),limit:100});
      if(!result.ok)throw Error('Нет связи для проверки ввода с других страниц. Черновик сохранён.');
      for(const row of result.data.rows)snapshots.set(row.contract_uuid,row);
    }
    const rows=Object.entries(pending).map(([contract_uuid,p])=>{
      const row=snapshots.get(contract_uuid);
      if(!row)throw Error('Есть ввод на другой странице. Сохраните его на исходной странице или отмените ввод.');
      const values:Record<string,any>={};
      for(const [key,raw] of Object.entries(p.values)){
        const f=data.columns.find((f:any)=>f.key===key);
        if(!f || !row.writable_fields.includes(key) || rowEntry(contract_uuid).length)throw Error('Право ячейки изменилось. Ввод сохранён; откройте очередь или обновите реестр.');
        values[key]=raw===null?null:typed(f,String(raw));
      }
      return {contract_uuid,...p,values};
    });
    if(!rows.length){status('Нет новых правок.');return;}
    if(rows.length>100 || pendingCount()>1000)throw Error('Сохраните пакет до 100 строк и 1000 ячеек.');
    const dialog=$<HTMLDialogElement>('registry-preview');
    dialog.querySelector('.registry-preview-values')!.innerHTML=rows.map(p=>{
      const row=snapshots.get(p.contract_uuid);
      return `<h3>${escape(row.external_id)} · ${escape(row.fields.partner || row.creditor_id)}</h3><dl>${Object.entries(p.values).map(([key,v])=>`<dt>${escape(data.columns.find((f:any)=>f.key===key).title)}</dt><dd>${escape(showValue(row.fields[key],data.columns.find((f:any)=>f.key===key)))} → ${v===null?'Очистить':escape(v)}</dd>`).join('')}</dl>`;
    }).join('');
    $('registry-apply').onclick=protect(async()=>{
      if(busy)return;busy=true;$<HTMLButtonElement>('registry-apply').disabled=true;
      try{
        const result=await crm.registryEdit({schema_version:1,operation_id:crypto.randomUUID(),rows});
        if(!result.ok)throw Error(result.text || 'Не удалось защитить правки на компьютере.');
        for(const row of rows)delete pending[row.contract_uuid];
        await storeInput();dialog.close();await sync();paint();status('Правки сохранены на устройстве. Подтверждение и конфликты показаны в строках.');
      }finally{busy=false;$<HTMLButtonElement>('registry-apply').disabled=false;}
    });
    dialog.showModal();
  }
  async function review(){
    if(editing)editing.blur();await sync();
    const entry=rowEntry(data.rows[active.row]?.contract_uuid).find(e=>e.state==='attention');
    if(!entry)throw Error('Выберите строку с отправкой, требующей сверки.');
    const ids=entry.body.rows.map((r:any)=>r.contract_uuid);
    if(ids.some((id:string)=>pending[id]))throw Error('В строках пакета уже есть черновик. Сохраните или отмените его перед сверкой.');
    const latest=await crm.api('GET','registry',undefined,{view:'all',ids:ids.join(','),limit:100});
    if(!latest.ok || latest.stale)throw Error('Для сверки нужен текущий ответ сервера. Исходная отправка сохранена.');
    const restored:Record<string,Pending>={};
    for(const p of entry.body.rows){
      const row=latest.data.rows.find((r:any)=>r.contract_uuid===p.contract_uuid);
      if(!row || Object.keys(p.values).some(key=>!row.writable_fields.includes(key)))throw Error('Строка или поле больше недоступны для изменения. Исходная отправка сохранена.');
      restored[p.contract_uuid]={values:p.values,clear:p.clear,base_revisions:Object.fromEntries(Object.keys(p.base_revisions).map(key=>[key,row.revisions[key] || 0]))};
      snapshots.set(p.contract_uuid,row);
    }
    const dialog=$<HTMLDialogElement>('registry-preview');
    dialog.querySelector('.registry-preview-values')!.innerHTML=entry.body.rows.map((p:any)=>{
      const row=snapshots.get(p.contract_uuid);
      return `<h3>${escape(row.external_id)} · ${escape(row.fields.partner || row.creditor_id)}</h3><dl>${Object.entries(p.values).map(([key,v])=>`<dt>${escape(data.columns.find((f:any)=>f.key===key)?.title || key)}</dt><dd>Сейчас на сервере: ${escape(row.fields[key] ?? 'Пусто')}<br>Ваш ввод: ${v===null?'Очистить':escape(v)}</dd>`).join('')}</dl>`;
    }).join('');
    const apply=$<HTMLButtonElement>('registry-apply');apply.textContent='Вернуть пакет в черновик для проверки';
    apply.onclick=protect(async()=>{
      Object.assign(pending,restored);await storeInput();
      const result=await crm.queue('review-registry',entry.body.operation_id);if(!result.ok)throw Error(result.text);
      apply.textContent='Сохранить на компьютере и отправить';dialog.close();await sync();paint();
      status('Полный пакет возвращён в черновик. Проверьте значения и сохраните новой операцией. Исходная ошибка остаётся в очереди.');
    });dialog.showModal();
  }
  scroll.onkeydown=e=>{
    if(editing || (e.target as HTMLElement).closest('[data-resize]'))return;
    const moves:Record<string,Cell>={ArrowLeft:{row:0,col:-1},ArrowRight:{row:0,col:1},ArrowUp:{row:-1,col:0},ArrowDown:{row:1,col:0},Tab:{row:0,col:e.shiftKey?-1:1},Enter:{row:1,col:0}};
    if(moves[e.key]){e.preventDefault();const d=moves[e.key];select({row:active.row+d.row,col:active.col+d.col},e.shiftKey&&e.key!=='Tab');scroll.querySelector(`[data-cell="${keyOf(active)}"]`)?.scrollIntoView({block:'nearest',inline:'nearest'});}
    else if(e.key==='F2'){e.preventDefault();beginEdit();}
    else if(e.key==='Delete'){e.preventDefault();protect(clearSelection)();}
  };
  scroll.oncopy=e=>{
    if(editing || !data?.rows.length)return;
    const b=bounds(),lines=[];
    for(let r=b.r0;r<=b.r1;r++){const cells=[];for(let c=b.c0;c<=b.c1;c++)cells.push(String(value(data.rows[r],data.columns[c].key) ?? '').replace(/\t/g,' ').replace(/\r?\n/g,' '));lines.push(cells.join('\t'));}
    e.preventDefault();e.clipboardData?.setData('text/plain',lines.join('\n'));
  };
  scroll.onpaste=e=>{
    if(editing || !data?.rows.length)return;
    e.preventDefault();const raw=e.clipboardData?.getData('text/plain') || '';
    protect(async()=>{
      const lines=raw.replace(/\r/g,'').replace(/\n$/,'').split('\n').map(line=>line.split('\t'));
      if(!lines.length || !lines[0].length || lines.some(line=>line.length!==lines[0].length) || lines.length*lines[0].length>1000)throw Error('Вставьте прямоугольный диапазон до 1000 ячеек.');
      const batch=[];
      for(let r=0;r<lines.length;r++)for(let c=0;c<lines[r].length;c++){
        const row=data.rows[active.row+r],f=data.columns[active.col+c];
        if(!row || !f || !row.writable_fields.includes(f.key)||rowEntry(row.contract_uuid).length)throw Error('Диапазон выходит за страницу или содержит недоступные ячейки. Ничего не вставлено.');
        batch.push({row,f,v:typed(f,lines[r][c])});
      }
      for(const {row,f,v} of batch)stage(row,f,v);
      await storeInput();paint();await preview();
    })();
  };
  $<HTMLSelectElement>('registry-scope').value=prefs.view;
  $<HTMLInputElement>('registry-search').value=prefs.search;
  $<HTMLSelectElement>('registry-zoom').value=String(prefs.zoom);
  $('registry-scope').onchange=protect(async()=>{prefs.view=$<HTMLSelectElement>('registry-scope').value;prefs.offset=0;await load();});
  $('registry-zoom').onchange=protect(async()=>{prefs.zoom=Number($<HTMLSelectElement>('registry-zoom').value);layout();await savePreferences();});
  $('registry-reset-widths').onclick=protect(async()=>{prefs.widths={};layout();await savePreferences();status('Ширины колонок сброшены.');});
  $('registry-color').onchange=protect(async()=>{
    if(!data?.rows.length)return;
    const b=bounds(),color=$<HTMLSelectElement>('registry-color').value;
    for(let r=b.r0;r<=b.r1;r++){
      const id=data.rows[r].contract_uuid;
      if(color)colors[id]=color;else delete colors[id];
      scroll.querySelector<HTMLElement>(`[data-row="${id}"]`)!.dataset.color=color;
    }
    const result=await crm.draft('registry-colors',colors);if(!result.ok)throw Error(result.text);status('Личные пометки сохранены на компьютере.');
  });
  $('registry-find').onclick=protect(async()=>{prefs.search=$<HTMLInputElement>('registry-search').value;prefs.offset=0;await load();});
  $('registry-search').onkeydown=e=>{if(e.key==='Enter')$('registry-find').click();};
  $('registry-reload').onclick=protect(load);
  $('registry-prev').onclick=protect(async()=>{prefs.offset=Math.max(0,prefs.offset-50);await load();});
  $('registry-next').onclick=protect(async()=>{prefs.offset+=50;await load();});
  $('registry-edit').onclick=()=>beginEdit();
  $('registry-clear').onclick=protect(clearSelection);
  $('registry-save').onclick=protect(preview);
  $('registry-review').onclick=protect(review);
  $('registry-cancel').onclick=protect(async()=>{if(editing){editing.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));return;}pending={};await storeInput();paint();status('Незавершённые правки отменены.');});
  $('registry-preview-cancel').onclick=()=>$<HTMLDialogElement>('registry-preview').close();
  let liveGeneration=0;
  async function refreshLive(){
    if(!data?.rows.length)return;
    const turn=++liveGeneration,ids=data.rows.map((r:any)=>r.contract_uuid).join(',');
    const result=await crm.api('GET','registry',undefined,{view:'all',ids,limit:100});
    if(disposed||!isCurrent()||turn!==liveGeneration)return;
    if(!result.ok||result.stale){status('Нет подтверждённой актуальности. Исходный ввод сохранён.');return;}
    const latest=new Map(result.data.rows.map((r:any)=>[r.contract_uuid,r]));
    if(editing){
      const cell=editing.closest<HTMLElement>('[data-cell]');
      const [r,c]=(cell?.dataset.cell||'').split(':').map(Number),row=data.rows[r],field=data.columns[c];
      const fresh:any=row&&latest.get(row.contract_uuid);
      if(row&&field&&!fresh?.writable_fields?.includes(field.key)){
        revokedInput={contract_uuid:row.contract_uuid,field:field.key,value:editing.value,base_revisions:row.revisions};
        const saved=await crm.draft('registry-revoked-input',revokedInput);
        if(!saved.ok)throw Error('Не удалось защитить текущий ввод. Оставьте редактор открытым.');
        showProtectedInput();
      }
    }
    for(const row of data.rows){
      const fresh:any=latest.get(row.contract_uuid);
      row.writable_fields=fresh?.writable_fields||[];
      if(fresh){row.fields=fresh.fields;row.revisions=fresh.revisions;}
      scroll.querySelectorAll<HTMLElement>(`[data-row="${row.contract_uuid}"] [data-cell]`).forEach(cell=>{
        const key=cell.dataset.field!,field=data.columns.find((f:any)=>f.key===key);
        const writable=row.writable_fields.includes(key);
        cell.setAttribute('aria-readonly',String(!writable));cell.classList.toggle('readonly',!writable);cell.classList.toggle('writable',writable);
        if(!cell.contains(editing)&&!pending[row.contract_uuid]&&!rowEntry(row.contract_uuid).length&&field){const span=cell.querySelector('span');if(span)span.textContent=showValue(value(row,key),field);}
      });
    }
    await storeInput();status('Назначения и права обновлены. Незавершённый ввод сохранён.');
  }
  const stop=crm.onChange?.((event:any)=>{if(event.type==='queue')void sync().catch(e=>status(e.message));else if(event.type==='live'&&event.dirty)void refreshLive().catch(e=>status(e.message));});
  scroll.onscroll=()=>{prefs.left=scroll.scrollLeft;prefs.top=scroll.scrollTop;schedulePreferences();};
  await load();
  if(!isCurrent())stop?.();
  return ()=>{if(editing)editing.blur();resizeStop?.();clearTimeout(preferenceSave);void savePreferences().catch(()=>{});disposed=true;generation++;stop?.();};
}
