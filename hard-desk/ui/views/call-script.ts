import { api, type ApiResult } from '../kit/api';
import { esc } from '../kit/dom';
import { setHead, type Section } from '../kit/shell';
type Content = { title: string; steps: { title: string; text: string }[] };
const guideRequests = new WeakMap<HTMLElement, number>();

export function scriptHtml(content: Content): string {
  return `<h2 class="script-name">${esc(content.title)}</h2><div class="script-steps">${content.steps.map((step,i)=>
    `<details class="script-step" open><summary><span class="script-number">${i+1}</span><span>${esc(step.title)}</span></summary><div class="script-text">${esc(step.text)}</div></details>`).join('')}</div>`;
}

/** Updating guidance never replaces the call form or its draft. */
export async function showCallScript(panel: HTMLElement, force = false): Promise<void> {
  const sequence=(guideRequests.get(panel)||0)+1; guideRequests.set(panel,sequence);
  let r:ApiResult;
  try{r=await api('/api/call-script', {mode:'soft'});}
  catch{r={ok:false,status:0,text:'Не удалось связаться с сервером.'};}
  if (guideRequests.get(panel)!==sequence || !panel.isConnected || !document.querySelector('#app.with-panel')) return;
  let host = document.getElementById('soft-call-guide');
  if(!r.ok && host?.querySelector('.script-name')) {
    let error=host.querySelector<HTMLElement>('[data-script-load-error]');
    if(!error){error=document.createElement('p');error.className='notice err';error.dataset.scriptLoadError='';error.setAttribute('role','status');host.querySelector('.script-pane-body')!.prepend(error);}
    error.textContent='Скрипт не обновился. Показана прежняя версия. '+(r.text||'Проверьте связь.');
    document.getElementById('app')!.classList.add('with-call-script');return;
  }
  if (!host) { host=document.createElement('aside'); host.id='soft-call-guide'; document.getElementById('main')!.append(host); }
  const value=r.data?.script;
  if (!value && r.ok && !force) return;
  host.innerHTML=`<div class="script-pane-head"><div><h2>Скрипт звонка</h2>${value?`<p class="muted">${esc(r.data.author)} · версия ${esc(r.data.revision)}</p>`:''}</div><button class="btn" data-script-portfolio>Портфель</button></div>
    <div class="script-pane-body">${value?scriptHtml(value):`<p class="notice">${esc(r.ok?'Руководитель ещё не опубликовал скрипт.':r.text||'Не удалось загрузить скрипт. Запись звонка доступна.')}</p>`}</div>
    <div class="script-pane-foot"><span class="muted">${value?'Текст руководителя на момент открытия карточки':''}</span><button class="btn small" data-script-reload>Обновить скрипт</button></div>`;
  host.querySelector('[data-script-portfolio]')!.addEventListener('click',()=>document.getElementById('app')!.classList.remove('with-call-script'));
  host.querySelector<HTMLButtonElement>('[data-script-reload]')!.addEventListener('click',async e=>{
    const button=e.currentTarget as HTMLButtonElement; button.disabled=true;
    try { await showCallScript(panel,true); } finally { if(button.isConnected)button.disabled=false; }
  });
  document.getElementById('app')!.classList.add('with-call-script');
}

let localDraft: {content:Content; revision:number; uid:number} | null = null;
const blank = ():Content=>({title:'',steps:[{title:'',text:''}]});
export const callScript: Section = {
  id:'call-script', title:'Скрипт звонка', icon:'phone',
  async render(root) {
    setHead('Скрипт звонка');
    let r:ApiResult;
    try{r=await api('/api/manager/call-script');}
    catch{r={ok:false,status:0,text:'Не удалось связаться с сервером.'};}
    if(!root.isConnected)return;
    if(!r.ok){root.innerHTML=`<p class="notice err">${esc(r.text||'Скрипт не загрузился.')}</p>`;return;}
    let state=r.data;
    if(localDraft?.uid!==state.manager_uid)localDraft=null;
    let content:Content=structuredClone(localDraft?.content||state.draft||blank());
    let revision=localDraft?.revision??state.revision;
    let dirty=!!localDraft;
    let pending: {key:string; body:any}|null=null;
    const draw=()=>{
      root.innerHTML=`<div class="script-status"><div><b>${state.published?'Опубликован для команды':'Публикации нет'}</b><span class="muted">${state.published?' · версия '+esc(state.published_revision):''}</span><div class="sub">${dirty?'Есть несохранённые правки':state.draft?'Черновик сохранён':'Новый скрипт'} · Сотрудники Софта: ${state.team.length}</div></div><details><summary>Кто получит скрипт</summary><p>${state.team.map((x:any)=>esc(x.employee)).join(', ')||'Активных сотрудников Софта с назначенным руководителем нет.'}</p></details></div>
        <div class="script-editor-grid"><section class="script-edit"><label>Название<input class="input" maxlength="100" data-script-title value="${esc(content.title)}" placeholder="Например, звонок по обещанию"></label><div data-script-fields>${content.steps.map((step,i)=>`<section class="script-edit-step"><div class="row"><b>Шаг ${i+1}</b><span class="grow"></span><button class="btn small" data-remove-step="${i}" ${content.steps.length===1?'disabled':''}>Удалить</button></div><label>Заголовок<input class="input" maxlength="80" data-step-title="${i}" value="${esc(step.title)}"></label><label>Текст подсказки<textarea class="input" rows="5" maxlength="6000" data-step-text="${i}">${esc(step.text)}</textarea></label></section>`).join('')}</div><button class="btn" data-add-step ${content.steps.length>=12?'disabled':''}>Добавить шаг</button></section>
        <section class="script-preview"><div class="section-head"><h3>Вид сотрудника</h3><span class="tag">Предпросмотр</span></div><div data-script-preview>${scriptHtml(content)}</div></section></div>
        <div class="script-edit-actions"><p data-script-error class="notice err" role="status" hidden></p><div class="row"><button class="btn" data-script-save>Сохранить черновик</button><button class="btn primary" data-script-publish ${dirty||!state.draft||!state.team.length?'disabled':''}>Опубликовать для команды (${state.team.length})</button><button class="btn" data-script-withdraw ${!state.published?'disabled':''}>Снять публикацию</button><span class="grow"></span><button class="btn small" data-script-refresh>Загрузить актуальный</button></div><p class="hint">Сотрудники видят только публикацию. Правки черновика сохраняют действующий текст до следующей публикации.</p></div>`;
      const changed=()=>{
        content.title=root.querySelector<HTMLInputElement>('[data-script-title]')!.value;
        content.steps.forEach((step,i)=>{step.title=root.querySelector<HTMLInputElement>(`[data-step-title="${i}"]`)!.value;step.text=root.querySelector<HTMLTextAreaElement>(`[data-step-text="${i}"]`)!.value;});
        dirty=true; localDraft={content:structuredClone(content),revision,uid:state.manager_uid};
        root.querySelector('[data-script-preview]')!.innerHTML=scriptHtml(content);
        root.querySelector<HTMLButtonElement>('[data-script-publish]')!.disabled=true;
        root.querySelector('.script-status .sub')!.textContent=`Есть несохранённые правки · Сотрудники Софта: ${state.team.length}`;
      };
      root.querySelectorAll('input,textarea').forEach(el=>el.addEventListener('input',changed));
      root.querySelector('[data-add-step]')!.addEventListener('click',()=>{content.steps.push({title:'',text:''});dirty=true;localDraft={content:structuredClone(content),revision,uid:state.manager_uid};draw();});
      root.querySelectorAll<HTMLButtonElement>('[data-remove-step]').forEach(button=>button.addEventListener('click',()=>{content.steps.splice(Number(button.dataset.removeStep),1);dirty=true;localDraft={content:structuredClone(content),revision,uid:state.manager_uid};draw();}));
      const action=async(name:string)=>{
        const error=root.querySelector<HTMLElement>('[data-script-error]')!;
        const body:any={action:name,base_revision:revision,...(name==='save'?{content}:{} )};
        const key=JSON.stringify(body);
        if(pending?.key!==key)pending={key,body:{...body,operation_id:'script-'+crypto.randomUUID()}};
        root.querySelectorAll<HTMLButtonElement>('.script-edit-actions button').forEach(b=>b.disabled=true);
        root.querySelectorAll<HTMLInputElement|HTMLTextAreaElement|HTMLButtonElement>('.script-edit input,.script-edit textarea,.script-edit button').forEach(b=>b.disabled=true);
        try{
          const response=await api('/api/manager/call-script',undefined,'POST',pending.body);
          if(!response.ok||!response.data?.ok){error.hidden=false;error.textContent=response.data?.error||response.text||'Результат не подтверждён. Повторите ту же операцию или загрузите актуальный скрипт.';return;}
          state=response.data;revision=state.revision;
          if(name==='save'){dirty=false;localDraft=null;content=structuredClone(state.draft);}
          else if(dirty)localDraft={content:structuredClone(content),revision,uid:state.manager_uid};
          pending=null;draw();
        }catch{error.hidden=false;error.textContent='Результат не подтверждён. Повтор этой операции безопасен.';}
        finally{if(error.isConnected){root.querySelectorAll<HTMLInputElement|HTMLTextAreaElement|HTMLButtonElement>('.script-edit input,.script-edit textarea,.script-edit button').forEach(b=>b.disabled=false);root.querySelector<HTMLButtonElement>('[data-script-save]')!.disabled=false;root.querySelector<HTMLButtonElement>('[data-script-publish]')!.disabled=dirty||!state.draft||!state.team.length;root.querySelector<HTMLButtonElement>('[data-script-withdraw]')!.disabled=!state.published;root.querySelector<HTMLButtonElement>('[data-script-refresh]')!.disabled=false;}}
      };
      root.querySelector('[data-script-save]')!.addEventListener('click',()=>void action('save'));
      root.querySelector('[data-script-publish]')!.addEventListener('click',()=>void action('publish'));
      root.querySelector('[data-script-withdraw]')!.addEventListener('click',()=>void action('withdraw'));
      root.querySelector('[data-script-refresh]')!.addEventListener('click',()=>{
        if(dirty&&!confirm('Загрузить сохранённый скрипт и убрать несохранённые правки?'))return;
        localDraft=null;void callScript.render(root,{});
      });
    };
    draw();
  }
};
