import { api, hard } from "../kit/api";
import { esc, n, toast } from "../kit/dom";
import { openPanel, setHead, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";

export type Profile = "hard" | "soft";
export function lockCard(panel:HTMLElement,on:boolean):void {
  if(on){panel.dataset.saving="1";panel.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement>("input,select,textarea,button").forEach(el=>{el.dataset.wasDisabled=el.disabled?"1":"0";el.disabled=true})}
  else {delete panel.dataset.saving;panel.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement>("[data-was-disabled]").forEach(el=>{el.disabled=el.dataset.wasDisabled==="1";delete el.dataset.wasDisabled})}
}
export const money = (v: unknown): string => v === "" || v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 2 });
export const debtText = (p: any): string => p.debt_known===false?"Сумма не указана":(p.totals?.length ? p.totals : [{ sum: p.total, unit: p.unit }])
  .map((x: any) => money(x.sum) + " " + (x.unit || "валюта не указана")).join(" · ");
export const amountsText = (items: any[]): string => (items || []).map(x => money(x.sum) + " " + (x.unit || "валюта не указана")).join(" · ") || "—";
export const promiseStates: Record<string,string> = { pending: "Ожидаем", overdue: "Просрочено", partial: "Частичная оплата", kept: "Оплачено по реестру", incomplete: "Не хватает данных" };
export function promiseText(items: any[]): string {
  return (items || []).map(p => `${p.due || "без срока"}: ${money(p.amount)} ${p.unit || ""} · ${promiseStates[p.state] || p.state}`).join("; ");
}
export function personListHtml(p: any): string {
  return `<span class="person-name">${esc(p.client || p.rep)}</span><span class="person-debt">${esc(debtText(p))}</span>
    <span class="sub mono">${esc((p.ids || [p.rep]).join(" · "))} · ${esc((p.tels || []).join(" · ") || "нет телефона")}</span>
    <span class="sub">${esc(p.address || "нет адреса")} · ${esc((p.partners || []).join(", "))}</span>
    <span class="sub">${esc(p.sc || p.office?.last?.title || "Ещё не обработан")}${p.scd || p.office?.next_date ? " · Следующее действие: " + esc(p.scd || p.office.next_date) : ""}</span>
    ${(p.promises || p.office?.promises)?.length ? `<span class="sub">${esc(promiseText(p.promises || p.office.promises))}</span>` : ""}`;
}

export function cardNavigation(panel: HTMLElement, rep: string, table: DataTable<any> | undefined, open: (rep: string) => void): void {
  const navigation = document.createElement("div");
  navigation.className = "card-navigation row";
  navigation.innerHTML = `<button class="btn small" data-card-prev>← Предыдущий</button><span class="grow mono">${esc(rep)}</span><button class="btn small" data-card-next>Следующий →</button>`;
  panel.prepend(navigation);
  for (const [selector, delta] of [["[data-card-prev]", -1], ["[data-card-next]", 1]] as const) {
    const button = navigation.querySelector<HTMLButtonElement>(selector)!;
    const next = table?.navigate(rep, delta);
    button.disabled = !next;
    button.addEventListener("click", () => { if (next) open(next.rep); });
  }
  panel.querySelector<HTMLElement>("#o-action,#c-status")?.focus({preventScroll:true});
}

export function contextHtml(d: any): string {
  return `<details class="section office-context"><summary>Договоры, долг и реестровые оплаты (${n((d.contracts || []).length)})</summary>
    ${(d.contracts || []).map((c: any) => `<div class="list-row"><div class="grow"><b class="mono">${esc(c.contract_id)}</b><span class="sub">${esc(c.partner || "Контрагент не указан")}</span></div><span>${esc(money(c.total))} ${esc(c.unit || "")}</span></div>
      ${(c.debt || []).map((x:any)=>`<div class="kv-line"><span>${esc(x.title)}</span><span>${esc(money(x.value))} ${esc(c.unit || "")}</span></div>`).join("")}`).join("")}
    <h4>Оплаты из реестра</h4>${(d.payments || []).map((p:any)=>`<div class="list-row"><div class="grow"><b class="mono">${esc(p.contract_id)}</b><span class="sub">${esc(p.date || "дата не указана")}</span></div><span>${esc(money(p.amount))} ${esc(p.unit || "")}</span></div>`).join("") || '<p class="muted">Сведений пока нет</p>'}
    ${(d.info || []).map((p:any)=>`<div class="kv-line"><span>${esc(p.title)}</span><span>${esc(p.value)}</span></div>`).join("")}
    ${(d.extra || []).map((p:any)=>`<div class="kv-line"><span>${esc(p.title)}</span><span>${esc(p.value)}</span></div>`).join("")}
    <p class="hint">Реквизиты и суммы из реестра обновляются импортом.</p></details>`;
}

export async function queueNotice(root: HTMLElement, profile: Profile, open: (rep: string)=>void): Promise<void> {
  const result = await api("/api/local/call_queue");
  if (!root.isConnected) return;
  const host = root.querySelector<HTMLElement>("[data-delivery]");
  if (!host) return;
  if (!result.ok) { host.innerHTML = `<div class="notice err">${esc(result.text || "Не удалось прочитать очередь")}</div>`; return; }
  const errors: any[] = result.data?.errors || [];
  const pending = Number(result.data?.pending) || 0;
  if (!pending) { host.innerHTML = '<div class="delivery-state muted">Все отправленные записи приняты сервером. Незавершённый ввод остаётся черновиком.</div>'; return; }
  host.innerHTML = `<div class="notice"><div class="grow">Ждут отправки: ${n(pending - errors.length)} · Требуют исправления: ${n(errors.length)}.
    ${errors.map(e=>`<div class="queue-error"><b class="mono">${esc(e.rep)}</b> · ${esc(e.error)}<div class="row"><button class="btn small" data-review="${esc(e.op_id)}">Разобрать запись</button><button class="btn small" data-remove="${esc(e.op_id)}">Убрать из очереди</button></div></div>`).join("")}</div></div>`;
  host.querySelectorAll<HTMLButtonElement>("[data-remove]").forEach(button=>button.addEventListener("click",async()=>{
    if (!confirm("Убрать эту проблемную запись из очереди? Принятые сервером факты останутся в истории.")) return;
    const r=await api("/api/local/call_queue/remove",undefined,"POST",{op_id:button.dataset.remove});
    if(r.ok) void queueNotice(root,profile,open); else toast(r.text || "Запись не убрана");
  }));
  host.querySelectorAll<HTMLButtonElement>("[data-review]").forEach(button=>button.addEventListener("click",()=>{
    if(document.querySelector('#panel-inner [data-saving="1"]')){toast("Дождитесь сохранения записи");return}
    const item=errors.find(e=>e.op_id===button.dataset.review); if(!item)return;
    const panel=openPanel(`<b>Запись ${esc(item.rep)}</b>`);
    const valueRows=Object.entries(item.body.values || {}).map(([key,value])=>[key,String(value)]);
    panel.innerHTML=`<p class="notice err">${esc(item.error)}</p><dl class="kv">${valueRows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      <p class="hint">Исходная запись остаётся здесь. Откройте актуальную карточку, сверьте исходник, затем сохраните исправление отдельной записью.</p><div class="row"><button class="btn" data-source>Выгрузить исходник</button><button class="btn primary" data-fix>Исправить в карточке</button></div>`;
    panel.querySelector("[data-source]")?.addEventListener("click",()=>void hard.xlsx("Исходная запись",["Поле","Значение"],Object.entries(item.body).map(([key,value])=>[key,typeof value==="object"?JSON.stringify(value):String(value)])));
    panel.querySelector("[data-fix]")?.addEventListener("click",async()=>{
      const ownProfile=item.body.kind==="office"?"hard":"soft";
      if(ownProfile!==profile){toast("Откройте программу нужной роли");return}
      const draft=item.body.kind==="office"?{office:item.body.values,action:item.body.action,touched:Object.keys(item.body.values||{}),recovery:true}:
        {fields:Object.fromEntries(Object.entries(item.body.values||{}).map(([key,value])=>[{soft_call_status:"c-status",soft_comment:"c-comment",soft_extra_phone:"c-extra",soft_next_call_date:"c-date",promise_date:"c-pdate",forecast_sum:"c-psum"}[key as "soft_comment"] || "",{value:String(value),checked:false}]).filter(([key])=>key)),touched:Object.keys(item.body.values||{})};
      // Do not overwrite a newer card draft while recovering a failed operation.
      const existing=await api("/api/local/draft",{rep:item.rep,profile});
      if(existing.data?.draft && !confirm("В карточке уже есть черновик. Заменить его исходником проблемной записи?")) {open(item.rep);return}
      const r=await api("/api/local/draft",undefined,"POST",{rep:item.rep,profile,draft});
      if(r.ok)open(item.rep);else toast(r.text || "Не удалось восстановить ввод");
    });
  }));
}

export function employeeSections(profile: Profile, open: (rep:string)=>void): Section[] {
  const promises: Section = {id:profile+"-promises",title:"Обещания",icon:"promises",kicker:"действующие договорённости и сверка оплат",async render(root){
    const r=await api("/api/workspace/promises",{profile});if(!root.isConnected)return;
    if(!r.ok){root.innerHTML=`<div class="empty"><b>Обещания не загрузились</b>${esc(r.text || "")}</div>`;return}
    const items:any[]=r.data?.items || [];
    root.innerHTML=`<div class="crm-context">По вашему текущему портфелю · на ${esc(r.data?.today || "")} · суммы на группу КД считаются один раз</div><div class="section" data-promises-table></div>`;
    const table=new DataTable<any>(root.querySelector("[data-promises-table]")!,{id:profile+"-promises",profile,key:x=>String(x.op_id||x.call_id),pageSize:100,exportName:"Мои обещания",empty:"Обещаний в вашем портфеле пока нет.",
      cols:[{key:"client",title:"Должник",value:x=>x.client},{key:"contracts",title:"Договоры",value:x=>(x.contracts||[x.contract_id]).join(", ")},{key:"due",title:"Срок",value:x=>x.due},
        {key:"amount",title:"Обещано",num:true,value:x=>x.amount},{key:"unit",title:"Валюта",value:x=>x.unit},{key:"received",title:"Оплаты реестра",num:true,value:x=>x.received},
        {key:"state",title:"Состояние",value:x=>promiseStates[x.state]||x.state},{key:"author",title:"Автор",value:x=>x.author},{key:"source",title:"Источник",value:x=>x.source}],onRow:x=>{if(x.rep)open(x.rep)}});
    await table.restore();if(root.isConnected)table.setRows(items);
  }};
  const reportSection=(history:boolean):Section=>({id:profile+(history?"-history":"-results"),title:history?"История работы":"Мои результаты",icon:history?"activity":"report",async render(root){
    setHead(history?"История работы":"Мои результаты","только ваши факты · рабочий день UTC+5");
    const prefs=await api("/api/local/preferences",{profile,id:history?"history-period":"report-period"});if(!root.isConnected)return;
    const today=new Date(Date.now()+5*3600000).toISOString().slice(0,10);
    const saved=prefs.data?.value || {};
    root.innerHTML=`<div class="toolbar employee-period"><button class="btn small" data-period="today">Сегодня</button><button class="btn small" data-period="week">7 дней</button><button class="btn small" data-period="month">Этот месяц</button><button class="btn small" data-period="all">За всё время</button>
      <label>С <input class="input" data-from type="date" value="${esc(saved.from || today)}" max="${today}"></label><label>По <input class="input" data-to type="date" value="${esc(saved.to || today)}" max="${today}"></label><button class="btn primary small" data-show>Показать</button></div>
      <div data-delivery></div><div data-report-content><div class="skeleton"></div></div>`;
    void queueNotice(root,profile,open);
    let seq=0,period=saved.period || "custom";
    const load=async()=>{
      const current=++seq,from=root.querySelector<HTMLInputElement>("[data-from]")!.value,to=root.querySelector<HTMLInputElement>("[data-to]")!.value;
      const content=root.querySelector<HTMLElement>("[data-report-content]")!;
      content.innerHTML='<div class="skeleton"></div>';
      const r=await api("/api/workspace/report",{profile,from,to,period});if(!root.isConnected||current!==seq)return;
      if(!r.ok){content.innerHTML=`<div class="notice err">${esc(r.text || "Отчёт не загрузился")}</div>`;return}
      void api("/api/local/preferences",undefined,"POST",{profile,id:history?"history-period":"report-period",value:{from,to,period}});
      const d=r.data,s=d.stats || {},soft=profile==="soft";
      const percent=(x:unknown)=>x==null?"—":(Number(x)*100).toLocaleString("ru-RU",{maximumFractionDigits:1})+"%";
      content.innerHTML=`<p class="crm-context">${esc(d.employee)} · ${esc(d.from)} — ${esc(d.to)} · собственные операции по автору; портфель — текущее закрепление</p>
        ${history?"":`<div class="kpis employee-kpis">${(soft?[[s.calls,"звонков"],[s.persons,"обработано людей"],[s.rpc,"RPC · "+percent(s.rpc_rate)],[s.ptp,"PTP · "+percent(s.ptp_rate)],[s.kp,"KP · "+percent(s.kp_rate)]]:[[s.office,"офисных действий"],[s.persons,"обработано людей"],[s.visits,"реальных выездов"],[s.promised,"офисных обещаний"]]).map(([v,label])=>`<div class="kpi"><b>${n(Number(v)||0)}</b><span>${esc(label)}</span></div>`).join("")}</div>
        <p class="hint">Текущий портфель: ${n(d.portfolio_persons)} людей / ${n(d.portfolio_contracts)} КД · ${esc(amountsText(d.money))}</p>
        ${soft?`<p class="hint">PTP: ${esc(amountsText(s.operation?.ptp_money))} · KP: ${esc(amountsText(s.operation?.kp_money))}. Частичных оплат: ${n(s.operation?.partial || 0)}; неполных обещаний: ${n(s.operation?.incomplete || 0)}. Ручная оплата и чек не дают KP.</p><details class="section"><summary>Как считаются показатели</summary><p>RPC — статусы действующего шаблона; RPC% = RPC / звонков. PTP — звонки «Обещает оплатить»; PTP% = PTP / RPC. KP — полная реестровая оплата обещанной суммы от дня обещания до срока включительно; KP% = KP / PTP. Платёж получает последнее обещание по договору, включая смену автора. Валюты разделены.</p></details>`:`<p class="hint">Офисные действия — отдельный журнал. Выезды считаются по журналу телефона. Сообщённые оплаты показаны как сведения сотрудника и требуют реестровой сверки.</p>`}`}
        <div class="section"><div class="section-head"><h3 class="section-title">${history?"История":"Детализация операций"}</h3><button class="btn small" data-export-report>Выгрузить отчёт</button></div><div data-events-table></div></div>
        ${!history&&!soft?'<div class="section"><h3 class="section-title">Реальные выезды за период</h3><div data-visits-table></div></div>':""}`;
      const events:any[]=d.events || [];
      const cols=[{key:"day",title:"День",value:(x:any)=>x.day},{key:"client",title:"Должник",value:(x:any)=>x.client || x.rep || "Прежний договор"},{key:"title",title:"Результат",value:(x:any)=>[x.title,x.values?.result].filter(Boolean).join(" · ")},
        {key:"contracts",title:"КД",value:(x:any)=>(x.contracts || [x.rep]).filter(Boolean).join(", ")},{key:"note",title:"Заметка / контакт",value:(x:any)=>[x.note,x.values?.note,x.values?.contact,x.values?.address].filter(Boolean).join(" · ")},
        {key:"date",title:"Срок / дата оплаты",value:(x:any)=>x.values?.promise_date || x.values?.payment_date || x.values?.next_date || ""},{key:"amount",title:"Сумма",value:(x:any)=>x.values?.amount || ""},{key:"currency",title:"Валюта",value:(x:any)=>x.currency || ""},{key:"source",title:"Источник",value:(x:any)=>x.source}];
      const table=new DataTable<any>(content.querySelector("[data-events-table]")!,{id:profile+(history?"-history":"-results"),profile,key:x=>String(x.op_id||x.id),pageSize:100,cols,empty:"За выбранный период ваших записей нет.",exportName:"Мои операции",onRow:x=>{if(x.rep)open(x.rep);else toast("Договор вышел из вашего закрепления; доступна ваша история в отчёте")}});
      await table.restore();if(!root.isConnected||current!==seq)return;table.setRows(events);
      if(!history&&!soft)new DataTable<any>(content.querySelector("[data-visits-table]")!,{id:"hard-real-visits",key:x=>String(x.id),pageSize:100,cols:[{key:"day",title:"День",value:x=>x.day},{key:"rep",title:"Договор",value:x=>x.rep},{key:"note",title:"Результат",value:x=>x.note}],empty:"За период реальных выездов нет.",onRow:x=>{if(x.rep)open(x.rep)}}).setRows(d.visits || []);
      content.querySelector("[data-export-report]")?.addEventListener("click",async()=>{
        const names:Record<string,string>={calls:"Звонков",office:"Офисных действий",persons:"Обработано людей",visits:"Реальных выездов",promised:"Офисных обещаний",rpc:"RPC",ptp:"PTP",kp:"KP",rpc_rate:"RPC / звонков",ptp_rate:"PTP / RPC",kp_rate:"KP / PTP"};
        const rows:(string|number|null)[][]=[["Сотрудник",d.employee],["Рабочее место",profile==="soft"?"Софт":"Хард"],["С",d.from],["По",d.to],["Текущий портфель, людей",d.portfolio_persons],["Текущий портфель, КД",d.portfolio_contracts],...Object.entries(s).filter(([,v])=>typeof v==="number").map(([k,v])=>[names[k] || k,v as number]),...d.money.map((x:any)=>["Текущий долг · "+x.unit,x.sum]),
          [],cols.map(c=>c.title),...events.map(x=>cols.map(c=>c.value(x)))];
        if(soft)for(const key of ["ptp_money","kp_money","partial_money"])for(const item of s.operation?.[key] || [])rows.push([{ptp_money:"Сумма PTP",kp_money:"Сумма KP",partial_money:"Частичные оплаты"}[key as "ptp_money"],item.unit,item.sum]);
        if(!soft){rows.push([], ["Реальные выезды: день","КД","Результат"]);for(const v of d.visits || [])rows.push([v.day,v.rep,v.note])}
        rows.push([], ["Действующие обещания на "+d.today,"Должник","КД","Срок","Сумма","Валюта","Оплата реестра","Состояние"]);
        for(const p of d.promises || [])rows.push([p.source,p.client,(p.contracts||[p.contract_id]).join(", "),p.due,p.amount,p.unit,p.received,promiseStates[p.state]||p.state]);
        const max=Math.max(...rows.map(row=>row.length));const headers=Array.from({length:max},(_,i)=>i===0?"Мои результаты":"");
        const r=await hard.xlsx(`Мои результаты ${d.from} — ${d.to}`,headers,rows);if(r.ok)toast("Отчёт сохранён");else toast(r.text || "Отчёт не сохранён");
      });
    };
    root.querySelector("[data-show]")?.addEventListener("click",()=>{period="custom";void load()});
    root.querySelectorAll<HTMLButtonElement>("[data-period]").forEach(button=>button.addEventListener("click",()=>{
      period=button.dataset.period!;const from=root.querySelector<HTMLInputElement>("[data-from]")!,to=root.querySelector<HTMLInputElement>("[data-to]")!;to.value=today;
      const start=new Date(today+"T12:00:00Z");if(period==="week")start.setUTCDate(start.getUTCDate()-6);if(period==="month")start.setUTCDate(1);from.value=start.toISOString().slice(0,10);void load();
    }));
    await load();
  }});
  return [promises,reportSection(true),reportSection(false)];
}
