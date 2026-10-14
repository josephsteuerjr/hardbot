import { api, hard } from "../kit/api";
import { esc, n, toast } from "../kit/dom";
import { currentId, openPanel, refresh } from "../kit/shell";
import { type DataTable } from "../kit/table";
import { cardNavigation, contextHtml, debtText, lockCard, money, promiseText } from "./employee";
import { receiptHtml, wireReceipt } from "./receipt";

export async function openHardCard(rep:string,table:DataTable<any>|undefined,open:(rep:string)=>void):Promise<void>{
  if(document.querySelector('#panel-inner [data-saving="1"]')){toast("Дождитесь сохранения записи");return}
  const panel=openPanel(`<b class="mono">${esc(rep)}</b>`);
  const [r,draftResult]=await Promise.all([api("/api/person",{rep}),api("/api/local/draft",{rep,profile:"hard"})]);
  if(!panel.isConnected)return;
  if(!r.ok){panel.innerHTML=`<div class="empty"><b>Карточка не открылась</b>${esc(r.text || "")}</div>`;return}
  const d=r.data,office=d.office || {},saved=draftResult.data?.draft;
  panel.classList.add("employee-card");panel.dataset.hardRep=rep;
  const options=Object.entries(office.actions || {contact:"Контакт состоялся",attempt:"Попытка контакта",information:"Получена информация",note:"Заметка",promise:"Обещание оплаты",payment:"Сообщена оплата",cancel_promise:"Отмена офисного обещания"}).filter(([key])=>key!=="month_comment");
  panel.innerHTML=`<h2 class="employee-name">${esc(d.client || "")}</h2><div class="muted">${esc(d.address || "Адреса в реестре нет")}</div>
    <div class="kpis employee-kpis"><div class="kpi accent money"><b>${esc(debtText(d))}</b><span>долг по валютам</span></div><div class="kpi"><b>${n((d.contracts || []).length)}</b><span>договоров</span></div><div class="kpi"><b>${n((d.receipts || []).length)}</b><span>чеков</span></div></div>
    <div class="section office-contacts"><h3 class="section-title">Контакты</h3>
      ${(d.phones || (d.phone?[d.phone]:[])).map((phone:string,i:number)=>`<div class="row"><span class="mono grow">${esc(phone)}</span><button class="btn small" data-copy-phone="${i}">Копировать</button></div>`).join("") || '<p class="muted">Телефонов в реестре нет</p>'}
      ${d.extra_contact?`<p class="hint">Контакты и адреса из реестра: ${esc(d.extra_contact)}</p>`:""}${(office.contacts || []).map((x:string)=>`<p class="hint">Добавлено сотрудником: ${esc(x)}</p>`).join("")}${(office.addresses || []).map((x:string)=>`<p class="hint">Адрес со слов сотрудника: ${esc(x)}</p>`).join("")}</div>
    <div class="section office-form"><div class="section-head"><h3 class="section-title">Офисная работа</h3><span class="tag" id="o-state">Новая запись</span></div>
      <label for="o-action">Действие</label><select class="input" id="o-action"><option value="">Выберите действие</option>${options.map(([value,label])=>`<option value="${esc(value)}">${esc(String(label))}</option>`).join("")}</select>
      ${Object.keys(office.choices || {}).length?`<details class="office-options"><summary>Результат и дополнительный контекст</summary>${(Object.entries(office.choices) as [string,string[]][]).map(([key,choices])=>`<label>${esc(({result:"Результат офисного контакта",contact_with:"С кем общались",client_location:"Где находится клиент",agreed_payment:"О чём договорились"} as Record<string,string>)[key] || key)}<select class="input" id="o-${esc(key)}"><option value="">Не заполнять</option>${choices.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join("")}</select></label>`).join("")}</details>`:""}
      <label for="o-note">Новая заметка / результат</label><textarea class="input" id="o-note" maxlength="4000" placeholder="Что выяснили и о чём договорились"></textarea>
      <div class="office-fields"><label>Новый контакт<input class="input" id="o-contact" maxlength="4000" placeholder="Номер, имя, кем приходится"></label><label>Дополнительный адрес<input class="input" id="o-address" maxlength="4000"></label></div>
      <label>Следующее действие<input class="input" id="o-next" type="date" value="${esc(office.next_date || "")}"></label><label class="hint"><input type="checkbox" id="o-clear-next"> Убрать дату следующего действия</label>
      <div data-money-action hidden><label>Один договор<select class="input" id="o-contract">${(d.contracts || []).map((c:any)=>`<option value="${esc(c.contract_id)}">${esc(c.contract_id)} · ${esc(c.unit || "валюта не указана")}</option>`).join("")}</select></label><div class="office-fields"><label><span id="o-date-label">Дата обещания</span><input class="input" id="o-date" type="date"></label><label>Сумма<input class="input" id="o-amount" inputmode="decimal" placeholder="0,00"></label></div></div>
      <label data-cancel-action hidden>Обещание для отмены<select class="input" id="o-target">${(office.promises || []).map((p:any)=>`<option value="${esc(p.op_id)}" data-contract="${esc(p.contract_id)}">${esc(p.contract_id)} · ${esc(p.due)} · ${esc(money(p.amount))} ${esc(p.unit || "")}</option>`).join("")}</select></label>
      <p class="hint">Заметка, контакт и следующее действие — на все КД карточки. Сумма — только на выбранный КД. Сообщение об оплате требует реестровой сверки.</p>
      <div class="notice err" id="o-error" role="status" hidden></div><div class="card-save-actions row"><button class="btn primary" id="o-save">Сохранить</button><button class="btn" id="o-save-next">Сохранить и дальше →</button><button class="btn small" id="o-reload" hidden>Обновить и сверить</button></div>
      ${!office.revision?'<p class="notice err">Сервер ещё не поддерживает офисную запись. Требуется обновление сервера.</p>':""}</div>
    ${contextHtml(d)}
    <details class="section"><summary>Обещания и результаты телефона / реестра</summary><p class="hint">Реальный выезд: ${esc(d.visit_date || "не зафиксирован")} · ${esc(d.status || "")}</p>${(d.promise || []).map((p:any)=>`<div class="kv-line"><span>${esc(p.title)}</span><span>${esc(p.value)}</span></div>`).join("")}<p class="hint">Софтом записано: ${esc(d.soft?.status || "нет результата")} · ${esc(d.soft?.comm_date || "")}</p></details>
    <div class="section"><h3 class="section-title">Офисные обещания</h3><p class="hint">${esc(promiseText(office.promises)) || "Пока нет"}</p></div>
    <details class="section"><summary>Текущий комментарий месяца</summary><textarea class="input" id="f-comment">${esc(d.visit_comment || "")}</textarea><label class="hint"><input type="checkbox" id="f-clear-comment"> Явно очистить комментарий месяца</label><button class="btn" id="f-savec">Сохранить комментарий</button><p class="hint">Совместный текст месяца. Сохранение проверяет версию карточки и оставляет запись в истории.</p></details>
    <details class="section"><summary>История офисной работы (${n(office.history_count || 0)})</summary>${(office.history || []).map((e:any)=>`<div class="list-row"><div class="grow"><b>${esc(e.title)} · ${esc(e.day)}</b><span class="sub">${esc(e.employee)} · ${esc((e.contracts || []).join(", "))}</span><span class="sub office-note">${esc(Object.values(e.values || {}).join(" · "))}${e.currency?" "+esc(e.currency):""}</span></div></div>`).join("") || '<p class="muted">Офисных записей пока нет</p>'}${office.history_count>100?'<p class="hint">Последние 100 записей; полная собственная история — в «Истории работы».</p>':""}</details>
    <details class="section"><summary>Чеки об оплате (${n((d.receipts || []).length)})</summary>${receiptHtml(d,true)}</details>`;
  cardNavigation(panel,rep,table,open);
  const touched=new Set<string>(saved?.touched || []);
  let revision=saved?.revision || office.revision,busy=false,pendingOpId="";
  const ids:Record<string,string>={note:"o-note",contact:"o-contact",address:"o-address",next_date:"o-next",contract_id:"o-contract",promise_date:"o-date",amount:"o-amount",target_op:"o-target",...Object.fromEntries(Object.keys(office.choices || {}).map(key=>[key,"o-"+key]))};
  const field=(id:string)=>panel.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>("#"+id)!;
  if(saved){if(saved.action)field("o-action").value=saved.action;for(const [key,value]of Object.entries(saved.office || {})){const id=key==="payment_date"?"o-date":ids[key];if(id)field(id).value=String(value)}if(typeof saved.comment==="string")field("f-comment").value=saved.comment;
    for(const [id,value]of Object.entries(saved.fields || {})as[string,{value:string;checked:boolean}][]){const input=panel.querySelector<HTMLInputElement>("#"+id);if(input){input.value=value.value;input.checked=value.checked;if(!saved.dirty||saved.dirty.includes(id))input.dataset.hardDirty="1"}}
    panel.querySelector("#o-state")!.textContent=saved.recovery?"Восстановлен исходник":"Черновик";
  }
  const updateAction=()=>{const action=field("o-action").value;panel.querySelector<HTMLElement>("[data-money-action]")!.hidden=!["promise","payment"].includes(action);panel.querySelector<HTMLElement>("[data-cancel-action]")!.hidden=action!=="cancel_promise";panel.querySelector("#o-date-label")!.textContent=action==="payment"?"Дата сообщённой оплаты":"Дата обещания";};updateAction();
  const persist=async()=>{
    const fields:Record<string,{value:string;checked:boolean}>={};panel.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>("input[id],textarea[id],select[id]").forEach(el=>fields[el.id]={value:el.value,checked:el instanceof HTMLInputElement&&el.checked});
    const dirty=[...panel.querySelectorAll<HTMLElement>('[data-hard-dirty="1"]')].map(el=>el.id).filter(Boolean);
    const result=await api("/api/local/draft",undefined,"POST",{rep,profile:"hard",draft:dirty.length||touched.size||pendingOpId?{fields,dirty,comment:dirty.some(id=>id==="f-comment"||id==="f-clear-comment")?field("f-comment").value:undefined,action:field("o-action").value,touched:[...touched],revision,pending_op_id:pendingOpId || undefined}:null});
    if(!result.ok&&panel.isConnected)toast(result.text || "Черновик не сохранился");
  };
  panel.addEventListener("input",event=>{const target=event.target as HTMLInputElement;target.dataset.hardDirty="1";if(target.id.startsWith("o-")){for(const [key,id]of Object.entries(ids))if(id===target.id)touched.add(key);panel.querySelector("#o-state")!.textContent="Черновик"}void persist()});
  panel.addEventListener("change",event=>{const target=event.target as HTMLElement;if(target.matches("input,select,textarea"))target.dataset.hardDirty="1";if(target.id==="o-action")updateAction();void persist()});
  panel.addEventListener("hard-form-saved",()=>void persist());
  const error=panel.querySelector<HTMLElement>("#o-error")!;
  if(saved?.revision && saved.revision!==office.revision){
    error.hidden=false;error.innerHTML=`Карточка изменилась, пока вы работали с черновиком. Сверьте актуальные договоры и сведения. Текущий комментарий на сервере: ${esc(d.visit_comment || "пусто")}<br><button class="btn small" id="o-use-current">Сверил, продолжить</button>`;
    panel.querySelector("#o-state")!.textContent="Нужна сверка";
    panel.querySelector("#o-use-current")?.addEventListener("click",()=>{revision=office.revision;error.hidden=true;panel.querySelector("#o-state")!.textContent="Черновик";panel.querySelectorAll<HTMLButtonElement>("#o-save,#o-save-next,#f-savec").forEach(b=>b.disabled=!revision);void persist()});
  }
  const submit=async(action:string,values:Record<string,string>,next:boolean,cleared:string[]=[])=>{
    if(busy||!revision)return;busy=true;const destination=next?table?.navigate(rep,1):undefined;
    pendingOpId="desk-office-"+crypto.randomUUID();lockCard(panel,true);error.hidden=true;panel.querySelector("#o-state")!.textContent="Отправляем…";await persist();
    const result=await api("/api/office/action",undefined,"POST",{rep,kind:"office",finish:true,action,values,cleared_keys:cleared,revision,op_id:pendingOpId,occurred_at:Date.now()});
    busy=false;pendingOpId="";lockCard(panel,false);if(!panel.isConnected)return;const buttons=panel.querySelectorAll<HTMLButtonElement>("#o-save,#o-save-next,#f-savec");
    if(!result.ok){await persist();error.textContent=result.text || "Запись не принята; ввод сохранён";error.hidden=false;panel.querySelector("#o-state")!.textContent="Требует исправления";if(result.status===409)panel.querySelector<HTMLElement>("#o-reload")!.hidden=false;return}
    buttons.forEach(b=>b.disabled=true);
    const queued=!!result.data?.queued;panel.querySelector("#o-state")!.textContent=queued?"Ждёт отправки":"Записано на сервере";toast(queued?"Запись сохранена на компьютере и ждёт сети":"Офисная запись принята сервером");
    if(action==="month_comment"){delete field("f-comment").dataset.hardDirty;delete field("f-clear-comment").dataset.hardDirty;panel.querySelector<HTMLInputElement>("#f-clear-comment")!.checked=false;field("f-comment").value=values.note || "";}
    else {for(const id of ["o-note","o-contact","o-address","o-date","o-amount"]){field(id).value=""}panel.querySelectorAll<HTMLElement>('[id^="o-"][data-hard-dirty]').forEach(el=>delete el.dataset.hardDirty);touched.clear()}
    if(result.data?.revision)revision=result.data.revision;await persist();
    if(!queued&&currentId()==="portfolio")await refresh();
    if(next&&destination){open(destination.rep);return}
    if(queued){buttons.forEach(b=>b.disabled=true);panel.querySelector<HTMLElement>("#o-reload")!.hidden=false}else open(rep);
  };
  const saveOffice=(next:boolean)=>{
    const action=field("o-action").value,values:Record<string,string>={};if(!action){toast("Выберите действие");return}for(const key of ["note","contact","address",...Object.keys(office.choices || {})]){const value=field(ids[key]).value.trim();if(value)values[key]=value}
    const cleared:string[]=[];if(panel.querySelector<HTMLInputElement>("#o-clear-next")!.checked){values.next_date="";cleared.push("next_date")}else if(touched.has("next_date")&&field("o-next").value)values.next_date=field("o-next").value;
    if(action==="promise"||action==="payment"){values.contract_id=field("o-contract").value;values.amount=field("o-amount").value.replace(/\s/g,"").replace(",",".");values[action==="promise"?"promise_date":"payment_date"]=field("o-date").value;if(!/^\d+(?:\.\d{1,2})?$/.test(values.amount)||Number(values.amount)<=0||!values[action==="promise"?"promise_date":"payment_date"]){toast("Укажите дату и сумму с точностью до копеек");return}}
    if(action==="cancel_promise"){const option=panel.querySelector<HTMLOptionElement>("#o-target option:checked");values.target_op=option?.value || "";values.contract_id=option?.dataset.contract || "";if(!values.target_op||!values.note){toast("Выберите обещание и укажите причину отмены");return}}
    if(["note","information"].includes(action)&&![values.note,values.contact,values.address].some(Boolean)){toast("Добавьте заметку, контакт или адрес");return}void submit(action,values,next,cleared);
  };
  panel.querySelector("#o-save")?.addEventListener("click",()=>saveOffice(false));panel.querySelector("#o-save-next")?.addEventListener("click",()=>saveOffice(true));panel.querySelector("#o-reload")?.addEventListener("click",async()=>{if(!draftResult.data?.pending)await persist();open(rep)});
  panel.querySelector("#f-savec")?.addEventListener("click",()=>{const clear=panel.querySelector<HTMLInputElement>("#f-clear-comment")!.checked,text=field("f-comment").value.trim();if(!text&&!clear){toast("Введите текст или явно подтвердите очистку");return}void submit("month_comment",{note:clear?"":text},false,clear?["note"]:[])});
  panel.querySelectorAll<HTMLButtonElement>("[data-copy-phone]").forEach(button=>button.addEventListener("click",async()=>{await hard.copy((d.phones || [d.phone])[Number(button.dataset.copyPhone)]);toast("Номер скопирован")}));
  wireReceipt(panel,rep,d,()=>{if(panel.isConnected)open(rep)});
  panel.querySelectorAll<HTMLButtonElement>("#o-save,#o-save-next,#f-savec").forEach(b=>b.disabled=!revision||revision!==office.revision);panel.querySelector<HTMLButtonElement>("#o-save-next")!.disabled=!revision||revision!==office.revision||!table?.navigate(rep,1);
  if(draftResult.data?.pending&&!draftResult.data.pending.error){
    pendingOpId=draftResult.data.pending.op_id;panel.querySelector("#o-state")!.textContent="Ждёт отправки";
    panel.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement>("input,textarea,select,#f-rc-add").forEach(el=>el.disabled=true);
    panel.querySelectorAll<HTMLButtonElement>("#o-save,#o-save-next,#f-savec").forEach(b=>b.disabled=true);panel.querySelector<HTMLElement>("#o-reload")!.hidden=false;
  }
}
