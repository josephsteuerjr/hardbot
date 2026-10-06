// «Выездник» — приложение сотрудника на ПК. Excel для портфеля был плох тем,
// что не знал визитов, фото и чеков; здесь всё это видно. Ввод ВИЗИТА остаётся
// в телефоне (гео и снимки с адреса — свойства поля): на ПК готовим — смотрим
// портфель, пишем текущий комментарий месяца и прикладываем чеки об оплате.
import { api, hard } from "../kit/api";
import { esc, n, toast } from "../kit/dom";
import { openPanel, setHead, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { receiptHtml, wireReceipt } from "./receipt";
import { mountModeSwitchIfKit } from "../kit/modeswitch";

const state = { persons: [] as any[], rep: "" };

function money(v: unknown): string {
  const num = Number(v);
  return Number.isFinite(num) && num > 0 ? num.toLocaleString("ru-RU") : "—";
}

export const portfolio: Section = {
  id: "portfolio",
  title: "Портфель",
  icon: "team",
  kicker: "ваши должники: долги, визиты, обещания",
  async render(root) {
    setHead("Портфель", "должники вашего закрепления");
    const r = await api("/api/bootstrap");
    if (!r.ok) { root.innerHTML = '<div class="empty"><b>Не загрузилось</b>' + esc(r.text || "") + "</div>"; return; }
    const d: any = r.data;
    state.persons = (d.persons || []) as any[];
    const debtSum = state.persons.reduce((s, x) => s + (Number(x.total) || 0), 0);
    root.innerHTML = `
      <div class="kpis"><div class="kpi accent"><b>${n(state.persons.length)}</b><span>должников</span></div>
        <div class="kpi"><b>${money(debtSum)}</b><span>долг портфеля</span></div></div>
      <div class="section" id="f-tbl"></div>`;
    new DataTable<any>(root.querySelector("#f-tbl")!, {
      id: "field-portfolio", exportName: "Портфель", key: (x) => x.rep,
      cols: [
        { key: "client", title: "Должник", value: (x) => x.client,
          html: (x) => `<span class="who">${esc(x.client || "")}</span><span class="sub mono">${esc(x.rep || "")}</span>` },
        { key: "debt", title: "Долг", num: true, value: (x) => x.total },
        { key: "address", title: "Адрес", value: (x) => x.address || "" },
        { key: "visited", title: "Был выезд", value: (x) => (x.visited ? "да" : "нет") },
        { key: "kd", title: "Договоров", num: true, value: (x) => x.n },
      ],
      onRow: (x) => { void openCard(x.rep); },
    }).setRows(state.persons);
  },
};

/** Карточка должника: чтение + текущий комментарий месяца + чек об оплате.
 *  Комментарий уходит черновиком (finish:false) — как автосохранение телефона:
 *  живой текст месяца, а «Сохранить выезд» остаётся за полем. */
async function openCard(rep: string) {
  state.rep = rep;
  const panel = openPanel(`<b class="mono">${esc(rep)}</b>`);
  const r = await api("/api/person", { rep });
  if (!r.ok) { panel.innerHTML = '<div class="empty"><b>Не открылось</b>' + esc(r.text || "") + "</div>"; return; }
  const d: any = r.data;
  const rcs: any[] = d.receipts || [];
  panel.innerHTML = `
    <h2 style="margin:0">${esc(d.client || "")}</h2>
    <div class="muted">${esc(d.address || "адреса в реестре нет")}</div>
    <div class="kpis"><div class="kpi accent"><b>${money(d.total)}</b><span>долг всего</span></div>
      <div class="kpi"><b>${n((d.contracts || []).length)}</b><span>договоров</span></div>
      <div class="kpi"><b>${n(rcs.length)}</b><span>чеков приложено</span></div></div>
    <div class="section"><div class="section-head"><h3 class="section-title">Текущий комментарий месяца</h3></div>
      <textarea class="input" id="f-comment" style="width:100%;min-height:90px">${esc(d.visit_comment || "")}</textarea>
      <div class="row" style="margin-top:8px"><button class="btn" id="f-savec">Сохранить комментарий</button>
        <span class="muted" style="font-size:13px">это совместный текст месяца: его увидит и офис, и руководитель</span></div></div>
    ${receiptHtml(d, true)}`;
  panel.querySelector("#f-savec")?.addEventListener("click", async () => {
    const text = (panel.querySelector("#f-comment") as HTMLTextAreaElement).value;
    const res = await api("/api/save", undefined, "POST", { rep, values: { visit_comment: text }, finish: false });
    if (res.ok) toast("Комментарий сохранён"); else toast(res.text || "Не сохранилось");
  });
  wireReceipt(panel, rep, d, () => void openCard(rep));
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
        <div class="sub">Гео и снимки с адреса — свойства поля. На ПК: портфель, текущий комментарий месяца, чеки об оплате.</div></div></div>
      <div class="list-row"><div class="grow"><div class="nm">Чеки — для сверки оплат</div>
        <div class="sub">Прикладывайте снимок перевода сразу, как должник показал оплату: сумма свери́тся с реестром.</div></div></div>
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
      if (!confirm("Выйти? Вход, портфель и кэш этого компьютера сотрутся.")) return;
      await hard.logout();
      location.reload();
    });
  },
};
