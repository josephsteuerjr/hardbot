// Остальные разделы руководителя: просроченные обещания, вход команды, отчёт и
// «это устройство».
import { api, hard, type Shell } from "../kit/api";
import { ago, ask, esc, icon, n, plural, shortDate, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { mountModeSwitchIfKit } from "../kit/modeswitch";
import { showIssued } from "../kit/issued";
import { copyLink, linkQuota, linkStatus, usableLink } from "../kit/login-links";
import { openPerson, problem, rangeChips, rangeQuery, staleNote, wireRange, type Range } from "./common";
import { go, hasSection } from "../kit/shell";

export const promises: Section = {
  id: "promises",
  title: "Обещания",
  icon: "promises",
  kicker: "просроченные обещания оплаты",
  async render(root, params) {
    root.innerHTML = '<div class="skeleton"></div>';
    const soft = params.profile === "soft" || params.mode === "soft";
    const r = await api("/api/manager/promises", { mode: soft ? "soft" : "hard" });
    if (!r.ok) { root.innerHTML = problem(r.text || "Не загрузилось"); return; }
    const d = r.data;
    const t = d.totals || {};
    // Одна таблица на всех: сотрудник — столбец, по нему и фильтр, и сортировка.
    const selected = Array.isArray(params.employees) ? new Set(params.employees) : null;
    const rows = ((d.employees || []) as any[]).filter(e => !selected || selected.has(e.employee)).flatMap((e) => (e.items as any[]).map((i) => ({ ...i, employee: e.employee })));
    const totals = { persons: rows.length, worked: rows.filter(i => i.visited_after_promise).length, untouched: rows.filter(i => !i.visited_after_promise).length };
    const after = (i: any) => soft ? (i.visited_after_promise ? "звонил после срока" : "не звонил после срока") : (i.visited_after_promise ? "ездил" : i.last_visit ? "ездил до обещания" : "не ездил");
    root.innerHTML = `${staleNote(r.stale)}
      <div class="kpis"><div class="kpi accent"><b>${n(totals.persons)}</b><span>просрочили обещание</span></div>
        <div class="kpi"><b>${n(totals.worked)}</b><span>после срока уже ${soft ? "звонили" : "ездили"}</span></div>
        <div class="kpi"><b>${n(totals.untouched)}</b><span>не трогали</span></div></div>
      <div class="section" id="prom"></div>`;
    new DataTable<any>(root.querySelector("#prom")!, {
      id: "manager-promises", exportName: "Просроченные обещания", key: (i) => `${i.employee}|${i.rep}`,
      empty: "Все обещания в срок или оплачены.",
      cols: [
        { key: "emp", title: "Сотрудник", value: (i) => i.employee },
        { key: "client", title: "Должник", value: (i) => i.client, html: (i) => `<span class="who">${esc(i.client)}</span><span class="sub mono">${esc(i.rep)}</span>` },
        { key: "date", title: "Обещал до", value: (i) => i.promise_date || "", html: (i) => esc(shortDate(i.promise_date || "")) },
        { key: "days", title: "Просрочка, дн", num: true, value: (i) => Number(i.days_overdue) || 0 },
        { key: "sum", title: "Сумма", num: true, value: (i) => i.money_text || "" },
        { key: "after", title: "После обещания", value: after,
          html: (i) => `<span class="tag ${i.visited_after_promise ? "ok" : "warn"}">${esc(after(i))}</span>` },
      ],
      onRow: (i) => void openPerson(i.employee, i.rep, undefined, soft ? "soft" : "hard"),
    }).setRows(rows);
  },
};

const ENTRY = (x: any) => (x.entered ? "вошёл" : linkStatus(x));

// «Кто ввёл логин-пароль»: сотрудник вводит вход и на телефоне, и на
// компьютере; подтверждает руководитель своей команды. Ячейка устройства — название
// и время ввода, пусто — ещё не вводил.
function enteredCell(x: any, fallback: string): string {
  if (!x) return '<span class="muted">—</span>';
  return `<span class="who">${esc(x.label || fallback)}</span>${x.at ? `<span class="sub">${esc(ago(x.at))}</span>` : ""}`;
}

const ENTERED_STATUS = (x: any) => (x.status === "active" ? "активен" : "ждёт подтверждения");


async function drawEntered(root: HTMLElement, accountSession: boolean): Promise<void> {
  const r=await api("/api/manager/entered");
  if(!r.ok){ root.innerHTML=problem(r.text || "Список заявок не загрузился."); return; }
  const rows=(r.data?.entered || []).slice().sort((a:any,b:any)=>Number((b.devices||[]).some((d:any)=>d.status==='pending'))-Number((a.devices||[]).some((d:any)=>d.status==='pending')));
  const pending=rows.flatMap((x:any)=>(x.devices||[]).filter((d:any)=>d.status==='pending').map((d:any)=>({row:x,device:d})));
  root.innerHTML=`<div class="section"><h2>Регистрация и устройства команды</h2>
    <div class="notice"><b>Как подтвердить вход</b><p>Сотрудник создаёт профиль или входит на телефоне либо в программе на ПК. Его заявка появится здесь. Сверьте ФИО и нажмите кнопку под заявкой.</p><p>Телефон можно подтвердить без предварительного ПК. Для компьютера лично проверьте сотрудника за этим ПК. Прежнее устройство того же вида будет отключено.</p></div>
    <h3>Ждут подтверждения: ${pending.length}</h3>
    <div id="entered-pending">${pending.length?pending.map(({row,device}:any)=>`<div class="list-row"><div class="grow"><b>${esc(row.employee)}</b><div class="sub">${esc(device.kind==='phone'?'Телефон':'Компьютер')} · ${esc(device.label||'')} · ${esc(row.office||'')}</div></div>${accountSession&&row.can_approve?`<button class="btn primary" data-device="${device.id}">Подтвердить ${device.kind==='phone'?'телефон':'ПК'}</button>`:'<span class="muted">Подтверждает свой руководитель</span>'}</div>`).join(''):'<p class="muted">Новых заявок нет. После регистрации или входа сотрудника нажмите «Обновить».</p>'}</div>
    <button class="btn" id="entered-reload">Обновить</button><div id="entered-tbl"></div></div>`;
  new DataTable<any>(root.querySelector("#entered-tbl")!,{id:"team-accounts",key:x=>x.username,exportName:"Регистрация команды",
    cols:[{key:"employee",title:"Сотрудник",value:x=>x.employee},{key:"office",title:"Офис",value:x=>x.office},
      {key:"status",title:"Доступ",value:x=>x.ready ? "Доступ подтверждён" : "Ожидает подтверждения"},
      {key:"devices",title:"Устройства и заявки",value:x=>(x.devices||[]).map((d:any)=>d.label).join(", "),html:x=>(x.devices||[]).map((d:any)=>
        `<div class="list-row"><span>${esc(d.kind==="phone"?"Телефон":"Компьютер")}: ${esc(d.label)} · ${esc(d.status==="active"?"подтверждён":"ожидает")}</span>
          </div>`).join("")}
    ]}).setRows(rows);
  root.querySelector("#entered-reload")?.addEventListener("click",()=>void drawEntered(root,accountSession));
  root.querySelectorAll<HTMLButtonElement>("[data-device]").forEach(button=>button.addEventListener("click",async()=>{
    const id=Number(button.dataset.device), row=rows.find((x:any)=>(x.devices||[]).some((d:any)=>d.id===id));
    const device=row?.devices.find((d:any)=>d.id===id); if(!device)return;
    if(!await ask("Подтвердить доступ",device.kind==="desktop"
      ? `Вы лично видите ${row.employee} за указанным компьютером и сверили ФИО? Прежний ПК будет отключён.`
      : `Вы сверили личность ${row.employee} и подтверждаете его телефон? Прежний телефон будет отключён. Компьютер не требуется.`,"Подтвердить"))return;
    button.disabled=true;
    try{
      const response=await api("/api/manager/account_approve",undefined,"POST",{device_id:id,observed_pc:device.kind==="desktop"});
      if(!response.ok || !response.data?.ok){toast(response.data?.error || response.text || "Подтверждение не выполнено.");return;}
      toast("Доступ подтверждён. Решение сохранено в журнале."); await drawEntered(root,accountSession);
    }finally{if(button.isConnected)button.disabled=false;}
  }));
}

export function links(accountSession: boolean): Section {
  return {id:"links",title:"Вход команды",icon:"links",kicker:"Регистрация и подтверждение устройств",
    async render(root){root.innerHTML='<div class="skeleton"></div>';await drawEntered(root,accountSession);}};
}



const rep = { range: { preset: "month" } as Range };
export function report(control: boolean): Section {
  return {
    id: "report",
    title: "Отчёт",
    icon: "report",
    kicker: "книга Excel за любое окно",
    render(root, context?: any) {
      if(context?.range)rep.range={...context.range};
      const draw = () => {
        root.innerHTML = `${context?.returnTo?.id && hasSection(context.returnTo.id) ? '<div class="toolbar"><button class="btn small" data-report-back>← Вернуться в CRM</button></div>' : ''}<div class="toolbar">${rangeChips(rep.range, null)}</div>
          <div class="toolbar"><label class="muted">С <input class="input" type="date" data-report-from value="${esc(rep.range.from || '')}"></label><label class="muted">По <input class="input" type="date" data-report-to value="${esc(rep.range.to || '')}"></label><span class="muted">Для своего периода укажите обе даты.</span></div>
          <div class="grid2" style="max-width:900px">
            <div class="list-row"><div class="grow"><div class="nm">Отчёт команды по шаблону</div><div class="sub">сводка, Хард, «Софт · операции» и «Софт · обещания» за выбранный период</div></div>
              <button class="btn primary" data-dl="stats">${icon("download")}Сформировать Excel</button></div>
            ${control ? `<div class="list-row"><div class="grow"><div class="nm">Контроль</div><div class="sub">риск по людям и итоги обзвона</div></div>
              <button class="btn" data-dl="control">${icon("download")}Скачать</button></div>` : ""}
          </div>`;
        root.querySelector('[data-report-back]')?.addEventListener('click',()=>void go(context.returnTo.id,context.returnTo.params || {}));
        wireRange(root, rep.range, draw);
        for(const key of ['from','to'] as const)root.querySelector<HTMLInputElement>(`[data-report-${key}]`)?.addEventListener('change',event=>{
          rep.range[key]=(event.target as HTMLInputElement).value || undefined;
          if(rep.range.from || rep.range.to)root.querySelectorAll('[data-preset]').forEach(el=>el.setAttribute('aria-pressed','false'));
        });
        root.querySelectorAll<HTMLButtonElement>("[data-dl]").forEach((b) => b.addEventListener("click", async () => {
          if(b.disabled)return;
          if(!!rep.range.from!==!!rep.range.to || rep.range.from && rep.range.to && rep.range.from>rep.range.to){toast('Укажите обе даты в правильном порядке');return;}
          const label=b.innerHTML;b.disabled=true;b.textContent='Формируется…';
          const isC = b.dataset.dl === "control";
          try {
            const res = await hard.download(isC ? "/api/control/export.xlsx" : "/api/manager/stats.xlsx", rangeQuery(rep.range),
              isC ? "Контроль.xlsx" : "Отчёт команды.xlsx");
            if (res.ok && res.file) { toast("Отчёт сформирован"); void hard.open(res.file); } else if (res.text) toast(res.text);
          } finally {if(b.isConnected){b.disabled=false;b.innerHTML=label}}
        }));
      };
      draw();
    },
  };
}

export function device(shell: Shell, me: any, onLogout: () => void): Section {
  return {
    id: "device",
    title: "Это устройство",
    icon: "device",
    bottom: true,
    kicker: "вход, сервер, вид",
    render(root) {
      const theme = shell.theme;
      root.innerHTML = `<dl class="kv" style="grid-template-columns:180px 1fr;max-width:760px;font-size:15px">
          <dt>Кто</dt><dd>${esc(me?.name || "—")} · ${esc(me?.role === "security" ? "безопасность" : me?.role === "full" ? "руководитель и контроль" : "руководитель")}</dd>
          <dt>Охват</dt><dd>${esc(me?.scope || "")}${me?.direction ? " · направление " + esc(me.direction) : ""}</dd>
          <dt>Компьютер</dt><dd>${esc(shell.device?.name || shell.deviceName)}</dd>
          <dt>Вход с</dt><dd>${esc(shell.device?.created_at ? new Date(shell.device.created_at).toLocaleString("ru-RU") : "—")}</dd>
          <dt>Сервер</dt><dd class="mono">${esc(shell.server)}</dd>
          <dt>Версия</dt><dd>${esc(shell.product)} ${esc(shell.version)}</dd></dl>
        <div class="section" id="modesec" style="display:none"><div class="section-head"><h2 class="section-title">Режимы этого exe</h2></div>
          <div id="modesw"></div></div>
        <div class="section"><div class="section-head"><h2 class="section-title">Вид</h2></div>
          <div class="chips">${[["system", "Как в Windows"], ["light", "Светлый"], ["dark", "Тёмный"]].map(([k, t]) =>
            `<button class="chip" data-theme="${k}" aria-pressed="${theme === k}">${t}</button>`).join("")}</div></div>
        <div class="section"><div class="section-head"><h2 class="section-title">Выйти на этом компьютере</h2></div>
          <p class="muted" style="max-width:640px;margin:0 0 12px">Пульт забудет текущий вход и кэш. Учётка останется действующей, если администратор её не отключил; вход по прежней ссылке может потребовать выдачи новой.</p>
          ${shell.device?.username ? accountSectionHtml() : ""}
          <button class="btn danger" data-logout>Выйти</button></div>`;
      if (shell.device?.username) wireAccountSection(root, shell.device.username);
      void mountModeSwitchIfKit(root.querySelector("#modesw")!, "manager").then(() => {
        const sec = root.querySelector("#modesec") as HTMLElement | null;
        const sw = root.querySelector("#modesw") as HTMLElement | null;
        if (sec && sw && sw.innerHTML.trim()) sec.style.display = "";
      });
      root.querySelectorAll<HTMLButtonElement>("[data-theme]").forEach((b) => b.addEventListener("click", async () => {
        const t = b.dataset.theme!;
        await hard.theme(t);
        shell.theme = t as Shell["theme"];
        document.documentElement.dataset.theme = t === "system" ? "" : t;
        if (t === "system") delete document.documentElement.dataset.theme;
        root.querySelectorAll("[data-theme]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      }));
      root.querySelector("[data-logout]")?.addEventListener("click", async () => {
        if (!(await ask("Выйти на этом компьютере?", "Пульт забудет вход и всё сохранённое.", "Выйти", true))) return;
        await hard.logout();
        onLogout();
      });
    },
  };
}

// --- Аккаунт «логин-пароль» сотрудника: «Мои входы» и «Смена пароля» --------
// Блоки вставляются в раздел «Это устройство» (fieldHelp/softHelp) только для
// вошедших по аккаунту: у ссылочного входа журнала и пароля нет (сервер 401).

const LOGIN_KIND: Record<string, string> = {
  ok: "вход", ok_pending: "вход · ждёт подтверждения", bad_password: "неверный пароль",
  locked: "блокировка", disabled: "доступ закрыт", blocked: "доступ закрыт",
  no_gps: "без геолокации", passwd: "смена пароля", approved: "подтверждено", unknown: "неизвестный логин",
};

export function accountSectionHtml(): string {
  return `<div class="section"><div class="section-head"><h3 class="section-title">Мои входы</h3>
      <button class="btn small" data-sessions-reload>Обновить</button></div>
    <div data-sessions><div class="skeleton"></div></div>
    <p class="hint">Каждый вход и отказ по вашему аккаунту: телефон и компьютер, адрес входа.</p></div>
    <div class="section"><div class="section-head"><h3 class="section-title">Смена пароля</h3></div>
      <div style="max-width:420px">
        <input class="input" id="cp-current" type="password" placeholder="Текущий пароль" autocomplete="current-password" style="width:100%">
        <input class="input" id="cp-new" type="password" placeholder="Новый пароль — от 8 символов, буква и цифра" autocomplete="new-password" style="width:100%;margin-top:8px">
        <input class="input" id="cp-repeat" type="password" placeholder="Повторите новый пароль" autocomplete="new-password" style="width:100%;margin-top:8px">
        <div style="margin-top:10px"><button class="btn primary" id="cp-go">Сменить пароль</button></div>
        <p class="notice" id="cp-note" hidden style="margin-top:10px"></p>
      </div></div>`;
}

export function wireAccountSection(root: HTMLElement, username: string): void {
  const list = root.querySelector<HTMLElement>("[data-sessions]");
  const load = async () => {
    if (!list) return;
    list.innerHTML = '<div class="skeleton"></div>';
    const r = await api("/api/account/sessions");
    if (!list.isConnected) return;
    if (!r.ok) { list.innerHTML = `<div class="notice err">${esc(r.text || "Журнал не загрузился")}</div>`; return; }
    const rows: any[] = r.data?.logins || [];
    list.innerHTML = rows.length ? `<div class="list">${rows.map(x => `
      <div class="list-row"><div class="grow"><div class="nm">${esc(String(x.at || "").replace("T", " ").slice(0, 19))} · ${x.device_kind === "phone" ? "телефон" : "компьютер"}</div>
        <div class="sub">${esc(x.label || "")}</div></div>
        <div class="sub">${esc(x.entrance === "internal" ? "внутренний вход" : "публичный вход")} · ${esc(LOGIN_KIND[x.kind] || x.kind || "")}</div>
      </div>`).join("")}</div>` : '<p class="muted">Входов пока не записано.</p>';
  };
  void load();
  root.querySelector("[data-sessions-reload]")?.addEventListener("click", () => void load());
  root.querySelector<HTMLButtonElement>("#cp-go")?.addEventListener("click", async () => {
    const button = root.querySelector<HTMLButtonElement>("#cp-go")!;
    const current = root.querySelector<HTMLInputElement>("#cp-current")?.value || "";
    const next = root.querySelector<HTMLInputElement>("#cp-new")?.value || "";
    const repeat = root.querySelector<HTMLInputElement>("#cp-repeat")?.value || "";
    const note = root.querySelector<HTMLElement>("#cp-note")!;
    const say = (text: string, bad: boolean) => { note.textContent = text; note.className = "notice" + (bad ? " err" : ""); note.hidden = false; };
    note.hidden = true;
    if (!current) { root.querySelector<HTMLInputElement>("#cp-current")?.focus(); return; }
    if (next.length < 8 || !/[0-9]/.test(next) || !/[a-zA-Zа-яА-ЯёЁ]/.test(next)) { say("Пароль от 8 символов, с буквой и цифрой.", true); return; }
    if (next !== repeat) { say("Пароли не совпадают.", true); return; }
    button.disabled = true;
    const r = await api("/api/account/password", undefined, "POST", { username, current, new: next });
    button.disabled = false;
    if (!r.ok) { say(r.text || "Не удалось сменить пароль.", true); return; }
    for (const id of ["#cp-current", "#cp-new", "#cp-repeat"]) root.querySelector<HTMLInputElement>(id)!.value = "";
    say("Пароль сменён.", false);
    toast("Пароль сменён");
  });
}
