// Офисное рабочее место Харда: портфель, карточка и отдельный журнал офиса.
// Реальные выезды сохраняют прежний телефонный процесс и доказательства.
import { api, hard } from "../kit/api";
import { esc, n, toast } from "../kit/dom";
import { openPanel, setHead, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { openHardCard } from "./hard-card";
import { employeeSections, personListHtml, promiseText, queueNotice } from "./employee";
import { mountModeSwitchIfKit } from "../kit/modeswitch";

const state = { persons: [] as any[], rep: "", table: undefined as DataTable<any> | undefined, today: "", showToday: false, partner: "" };
export const hardEmployeeSections = employeeSections("hard", rep => void openCard(rep));

function money(v: unknown): string {
  const num = Number(v);
    return v != null && v !== "" && Number.isFinite(num) ? num.toLocaleString("ru-RU", {maximumFractionDigits:2}) : "—";
}

function debtText(p: any): string {
  if(p.debt_known===false)return "Сумма не указана";
  return (p.totals?.length ? p.totals : [{ sum: p.total, unit: p.unit }])
    .map((x: any) => money(x.sum) + " " + (x.unit || "ед. не указана")).join(" · ");
}

export const portfolio: Section = {
  id: "portfolio",
  title: "Портфель",
  icon: "team",
  kicker: "ваши должники: долги, визиты, обещания",
  async render(root) {
    setHead("Портфель", "должники вашего закрепления");
    const [r,prefs] = await Promise.all([api("/api/bootstrap"),api("/api/local/preferences",{profile:"hard",id:"portfolio-scope"})]);
    if (!root.isConnected) return;
    if (!r.ok) { root.innerHTML = '<div class="empty"><b>Не загрузилось</b>' + esc(r.text || "") + "</div>"; return; }
    const d: any = r.data;
    state.persons = (d.persons || []) as any[];
    state.today=d.today || new Date(Date.now()+5*3600000).toISOString().slice(0,10);
    if(prefs.data?.value){state.showToday=!!prefs.data.value.showToday;state.partner=prefs.data.value.partner || ""}
    const partners=[...new Set<string>(state.persons.flatMap(p=>p.partners || []))].sort();
    if(!partners.includes(state.partner))state.partner="";
    const today=state.persons.filter(p=>(!p.office?.last&&!p.visited)||(p.office?.next_date&&p.office.next_date<=state.today)||(p.office?.promises || []).some((x:any)=>x.state!=="kept"&&x.due<=state.today));
    const totals = new Map<string, number>();
    for (const person of state.persons) for (const x of person.totals?.length ? person.totals : [{ sum: person.total, unit: person.unit }])
      totals.set(x.unit || "ед. не указана", (totals.get(x.unit || "ед. не указана") || 0) + (Number(x.sum) || 0));
    const debtSum = [...totals].map(([unit, sum]) => `${money(sum)} ${unit}`).join(" · ");
    root.innerHTML = `
      ${r.stale?`<div class="notice">Данные из локального кэша на ${esc(r.stale)}. Новые записи будут ждать сети.</div>`:""}
      <div class="kpis"><div class="kpi accent"><b>${n(state.persons.length)}</b><span>должников</span></div>
        <div class="kpi money"><b>${esc(debtSum)}</b><span>долг портфеля по валютам</span></div></div>
      <div class="toolbar employee-scope"><button class="btn small" data-scope="all">Весь портфель</button><button class="btn small" data-scope="today">На сегодня (${n(today.length)})</button><label>Контрагент <select class="input" id="f-partner"><option value="">Все</option>${partners.map(p=>`<option value="${esc(p)}" ${p===state.partner?"selected":""}>${esc(p)}</option>`).join("")}</select></label></div>
      <div data-delivery></div>
      <div class="section" id="f-tbl"></div>`;
    const units=[...totals.keys()];
    const table = new DataTable<any>(root.querySelector("#f-tbl")!, {
      id: "field-portfolio", profile:"hard", listHtml:personListHtml, pageSize:100, exportName: "Портфель", key: (x) => x.rep,
      searchPlaceholder:"ФИО, КД, телефон, адрес", empty:"Должников в вашем закреплении нет. Проверьте портфель у руководителя.",
      defaultHidden:["visited","kd","city","partners",...units.map((_,i)=>"unit-"+i)],
      cols: [
        { key: "client", title: "Должник", width:"240px", value: (x) => x.client,
          html: (x) => `<span class="who">${esc(x.client || "")}</span><span class="sub mono">${esc(x.rep || "")}</span>` },
        { key: "debt", title: "Долг по валютам", width:"180px", value: debtText },
        { key: "next", title: "Следующее действие", value:x=>x.office?.next_date || "", width:"160px" },
        { key: "last", title: "Офисный результат", value:x=>x.office?.last?.title || "", width:"180px" },
        { key: "promise", title: "Офисные обещания", value:x=>promiseText(x.office?.promises), width:"200px" },
        { key: "phones", title: "Телефоны / контакты", value:x=>[...(x.tels || []),...(x.office?.contacts || [])].join(" · "), width:"180px" },
        { key: "contracts", title:"Номера КД", value:x=>(x.ids || [x.rep]).join(", ") },
        { key: "address", title: "Адрес", value: (x) => x.address || "" },
        { key: "visited", title: "Был выезд", value: (x) => (x.visited ? "да" : "нет") },
        { key: "kd", title: "Договоров", num: true, value: (x) => x.n },
        { key: "city", title:"Город", value:x=>x.city || "" },
        { key: "partners", title:"Контрагент", value:x=>(x.partners || []).join(", ") },
        ...units.map((unit,i)=>({key:"unit-"+i,title:"Долг, "+unit,num:true,value:(x:any)=>(x.totals || []).find((a:any)=>a.unit===unit)?.sum ?? null})),
      ],
      onRow: (x) => { void openCard(x.rep); },
    });
    state.table=table;await table.restore();if(!root.isConnected)return;
    const show=()=>{table.setRows((state.showToday?today:state.persons).filter(p=>!state.partner||(p.partners || []).includes(state.partner)));root.querySelectorAll<HTMLButtonElement>("[data-scope]").forEach(button=>button.classList.toggle("primary",(button.dataset.scope==="today")===state.showToday));void api("/api/local/preferences",undefined,"POST",{profile:"hard",id:"portfolio-scope",value:{showToday:state.showToday,partner:state.partner}})};
    root.querySelectorAll<HTMLButtonElement>("[data-scope]").forEach(button=>button.addEventListener("click",()=>{state.showToday=button.dataset.scope==="today";show()}));
    root.querySelector<HTMLSelectElement>("#f-partner")?.addEventListener("change",event=>{state.partner=(event.target as HTMLSelectElement).value;show()});
    show();void queueNotice(root,"hard",rep=>void openCard(rep));
  },
};

/** Одна карточка для обоих представлений; таблица и список только читают. */
async function openCard(rep: string) {
  return openHardCard(rep,state.table,next=>void openCard(next));
}

export const fieldHelp: Section = {
  id: "fieldhelp",
  title: "Это устройство",
  icon: "control",
  kicker: "вход, выход, что здесь можно",
  async render(root) {
    setHead("Это устройство", "приложение выездника на компьютере");
    root.innerHTML = `<div class="section"><div class="grid2">
      <div class="list-row"><div class="grow"><div class="nm">Вход — по вашей ссылке</div>
        <div class="sub">Руководитель выдаёт её в «Входе команды». Телефон продолжает работать: вход на ПК его не заменяет.</div></div></div>
      <div class="list-row"><div class="grow"><div class="nm">Визит закрывается в телефоне</div>
        <div class="sub">На ПК записывайте офисные контакты, попытки связи, информацию, обещания и сообщённые оплаты в карточке. Офисная запись не создаёт выезд или GPS.</div></div></div>
      <div class="list-row"><div class="grow"><div class="nm">Чеки — для сверки оплат</div>
        <div class="sub">Сообщённая оплата и чек требуют реестровой сверки. Свои записи, доставку и отчёты смотрите в «Истории работы» и «Моих результатах». Черновики защищены на этом ПК; без сети чтение доступно до 8 часов.</div></div></div>
    </div>
    <div class="section" id="modesec" style="display:none"><div class="section-head"><h3 class="section-title">Режимы этого exe</h3></div>
      <div id="modesw"></div></div>
    <div class="row" style="margin-top:12px"><button class="btn" id="f-logout">Выйти с этого компьютера</button></div></div>`;
  void mountModeSwitchIfKit(root.querySelector("#modesw")!, "field").then(() => {
    const sec = root.querySelector("#modesec") as HTMLElement | null;
    const sw = root.querySelector("#modesw") as HTMLElement | null;
    if (sec && sw && sw.innerHTML.trim()) sec.style.display = "";
  });
    root.querySelector("#f-logout")?.addEventListener("click", async () => {
      if (!confirm("Выйти? Вход и кэш закроются. Неотправленные записи и черновики сохранятся для того же сотрудника.")) return;
      await hard.logout();
      location.reload();
    });
  },
};
