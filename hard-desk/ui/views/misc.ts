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
// компьютере; подтверждает его только владелец. Ячейка устройства — название
// и время ввода, пусто — ещё не вводил.
function enteredCell(x: any, fallback: string): string {
  if (!x) return '<span class="muted">—</span>';
  return `<span class="who">${esc(x.label || fallback)}</span>${x.at ? `<span class="sub">${esc(ago(x.at))}</span>` : ""}`;
}

const ENTERED_STATUS = (x: any) => (x.status === "active" ? "активен" : "ждёт подтверждения");

async function drawEntered(root: HTMLElement): Promise<void> {
  const host = root.querySelector<HTMLElement>("#entered-tbl");
  const bar = root.querySelector<HTMLElement>("#entered-bulk");
  if (!host || !bar || !root.isConnected) return;
  host.innerHTML = '<div class="skeleton"></div>';
  const r = await api("/api/manager/entered");
  if (!host.isConnected) return;
  if (!r.ok) {
    host.innerHTML = `${problem(r.text || "Не загрузилось")}<div class="toolbar" style="margin-top:10px"><button class="btn small" data-retry>Повторить</button></div>`;
    host.querySelector("[data-retry]")?.addEventListener("click", () => void drawEntered(root));
    return;
  }
  const entered = ((r.data?.entered || []) as any[]);
  new DataTable<any>(host, {
    id: "manager-entered", exportName: "Кто ввёл логин-пароль", key: (x) => `${x.username || ""}|${x.employee || ""}`,
    empty: "Никто ещё не вводил логин-пароль.",
    cols: [
      { key: "emp", title: "ФИО", value: (x) => x.employee,
        html: (x) => `<span class="who">${esc(x.employee)}</span>${x.username ? `<span class="sub mono">${esc(x.username)}</span>` : ""}` },
      { key: "office", title: "Офис", value: (x) => x.office || "" },
      { key: "phone", title: "Телефон", value: (x) => x.phone?.label || "", html: (x) => enteredCell(x.phone, "телефон") },
      { key: "desktop", title: "Компьютер", value: (x) => x.desktop?.label || "", html: (x) => enteredCell(x.desktop, "компьютер") },
      { key: "ready", title: "Готов", value: (x) => (x.ready ? "готов" : "нет"),
        html: (x) => (x.ready ? '<span class="tag ok">готов</span>' : '<span class="tag">нет</span>') },
      { key: "status", title: "Статус", value: ENTERED_STATUS,
        html: (x) => `<span class="tag ${x.status === "active" ? "ok" : "warn"}">${esc(ENTERED_STATUS(x))}</span>` },
    ],
  }).setRows(entered);
  const gen = String(r.data?.generated_at || "").replace("T", " ").slice(0, 16);
  bar.innerHTML = `<span class="who">Ввели и на телефоне, и на компьютере: <b>${n(entered.filter((x) => x.ready).length)} из ${n(entered.length)}</b></span>
    <button class="btn small" data-b="copy" ${entered.length ? "" : "disabled"}>Скопировать список</button>
    <span class="spacer"></span>${gen ? `<span class="sub">по состоянию на ${esc(gen)}</span>` : ""}
    <button class="ticon" data-b="reload" title="Обновить список"><svg viewBox="0 0 18 18" width="15" height="15"><path d="M14.5 8.5a5.5 5.5 0 1 1-1.6-3.9M14.5 2.5v3.6h-3.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>`;
  bar.querySelector('[data-b="copy"]')?.addEventListener("click", async () => {
    // Формат согласован с владельцем: «ФИО — офис — тел ✓/— — ПК ✓/— — готов/нет».
    await hard.copy(entered.map((x) => `${x.employee} — ${x.office || "—"} — тел ${x.phone ? "✓" : "—"} — ПК ${x.desktop ? "✓" : "—"} — ${x.ready ? "готов" : "нет"}`).join("\n"));
    toast(`Скопировано: ${n(entered.length)}`);
  });
  bar.querySelector('[data-b="reload"]')?.addEventListener("click", () => void drawEntered(root));
}

export const links: Section = {
  id: "links",
  title: "Вход команды",
  icon: "links",
  kicker: "персональные ссылки сотрудников Софта и Харда",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div>';
    const r = await api("/api/manager/app_links");
    if (!r.ok) { root.innerHTML = problem(r.text || "Не загрузилось"); return; }
    const d = r.data;
    const rows = [d.me, ...(d.links as any[])].filter(Boolean);
    let busy = false;
    root.innerHTML = `<p class="muted" style="max-width:760px;margin:0 0 6px">Ссылка персональная. Два входа для телефона (браузер и ярлык) и отдельный вход для программы на ПК. Её можно скопировать повторно после входа сотрудника — копирование квоту не тратит. Срок ссылки — неделя; уже подключённые устройства продолжают работать.</p>
      <div class="bulk" id="bulk"></div><div id="tbl"></div>
      <div class="section" id="entered-sec">
        <div class="section-head"><h2 class="section-title">Кто ввёл логин-пароль</h2></div>
        <p class="muted" style="max-width:760px;margin:6px 0 12px">Логин и пароль вводят дважды: на телефоне и на компьютере; «готов» — ввёл и там и там. Список пересылается владельцу; подтверждение — только он.</p>
        <div class="bulk" id="entered-bulk"></div><div id="entered-tbl"></div>
      </div>`;
    const table = new DataTable<any>(root.querySelector("#tbl")!, {
      id: "manager-links", exportName: "Вход команды", key: (x) => x.employee, select: true,
      cols: [
        { key: "who", title: "Кто", value: (x) => x.label || x.employee, html: (x) => `<span class="who">${esc(x.label || x.employee)}</span>` },
        { key: "entry", title: "Вход", value: ENTRY,
          html: (x) => (x.entered ? `<span class="tag ok">вошёл · ${n(x.devices || 1)} ${plural(x.devices || 1, "устройство", "устройства", "устройств")}</span>`
            : `<span class="tag">${esc(linkStatus(x))}</span>`) },
        { key: "quota", title: "Остаток входов / срок", value: linkQuota,
          html: (x) => `${esc(linkQuota(x))}${x.expires_at ? `<span class="sub">до ${esc(shortDate(x.expires_at))} · ${esc(linkStatus(x))}</span>` : ""}` },
        { key: "seen", title: "Последний вход", value: (x) => String(x.last_use_at || "").replace("T", " ").slice(0, 16),
          html: (x) => (x.last_use_at ? esc(ago(x.last_use_at)) : '<span class="muted">не было</span>') },
        { key: "url", title: "Ссылка", filter: false, value: (x) => x.url || "", html: (x) => x.url ?
          `<span class="url">${esc(x.url)}</span><button class="btn small" data-copy-link="${esc(x.employee)}">Скопировать ссылку${usableLink(x) ? "" : " (не действует)"}</button>` : "" },
      ],
      onSelect: () => drawBulk(),
      afterRender: (host) => host.querySelectorAll<HTMLButtonElement>("[data-copy-link]").forEach(button =>
        button.addEventListener("click", () => { const row = rows.find(x => x.employee === button.dataset.copyLink); if (row?.url) void copyLink(row.url); })),
    });
    const drawBulk = () => {
      const bar = root.querySelector<HTMLElement>("#bulk")!;
      const sc = table.scope();
      const ppl = sc.rows;
      const lacking = ppl.filter((x) => !usableLink(x) && !x.entered && x.can_issue !== false);
      const issuable = ppl.filter((x) => x.can_issue !== false);
      const live = ppl.filter((x) => usableLink(x) && x.url);
      const canRevoke = ppl.filter((x) => (x.entered || x.code) && x.employee !== "!me");
      bar.innerHTML = `<span class="who">Действия — <b>${esc(sc.label)}</b></span>
        <button class="btn small primary" data-b="issue" ${lacking.length ? "" : "disabled"}>Выдать тем, у кого нет (${lacking.length})</button>
        <button class="btn small" data-b="reissue" ${issuable.length ? "" : "disabled"}>Выдать заново</button>
        <button class="btn small" data-b="copy" ${live.length ? "" : "disabled"}>Скопировать действующие ссылки (${live.length})</button>
        <button class="btn small danger" data-b="revoke" ${canRevoke.length ? "" : "disabled"}>Сбросить доступ (${canRevoke.length})</button>
        <span class="spacer"></span>${sc.picked ? '<button class="link" data-b="clear">снять отметки</button>' : ""}`;
      const on = (k: string, f: () => Promise<void>) => bar.querySelector(`[data-b="${k}"]`)?.addEventListener("click", () => void f());
      on("clear", async () => table.clearSelection());
      const issue = async (list: any[], title: string) => {
        if (!list.length || busy) return;
        busy = true;
        bar.querySelectorAll<HTMLButtonElement>("button").forEach(b => b.disabled = true);
        try {
        if (!(await ask(title, `Выдать ссылку ${list.length} чел.? У кого ссылку выдавали, но не открыли, — старая перестанет работать. Кто уже вошёл, из приложения не вылетит.`, "Выдать"))) { busy = false; drawBulk(); return; }
        toast("Выдаю ссылки…");
        const out: any[] = [];
        for (const x of list) {
          const res = await api("/api/manager/app_link", undefined, "POST", { employee: x.employee });
          out.push({ employee: x.label || x.employee, who: "", url: res.ok ? res.data.url : "", uses: res.ok ? res.data.uses : null,
            expires: res.ok ? String(res.data.expires_at || "").slice(0, 10) : "", error: res.ok ? "" : res.text || "не выдано" });
        }
        await links.render(root, {});
        await showIssued(`Ссылки входа · ${out.length} чел.`, out);
        } catch (error) { toast((error as Error).message || "Не удалось выдать ссылки"); }
        finally { busy = false; if (bar.isConnected) drawBulk(); }
      };
      on("issue", () => issue(lacking, "Выдать вход"));
      on("reissue", () => issue(issuable, "Выдать заново"));
      on("copy", async () => { await hard.copy(live.map((x) => `${x.label || x.employee} — ${x.url}`).join("\n")); toast(`Скопировано ссылок: ${live.length}`); });
      on("revoke", async () => {
        if (busy) return;
        busy = true;
        try {
        if (!(await ask("Сбросить доступ?", `${canRevoke.length} чел. выйдут из приложения на телефонах и ПК, ссылки перестанут работать. Вернуть вход — новой ссылкой.`, "Сбросить", true))) { busy = false; drawBulk(); return; }
        let done = 0;
        for (const x of canRevoke) { const res = await api("/api/manager/app_revoke", undefined, "POST", { employee: x.employee }); if (res.ok) done++; }
        toast(`Доступ сброшен: ${done} чел.`);
        await links.render(root, {});
        } catch (error) { toast((error as Error).message || "Не удалось сбросить доступ"); }
        finally { busy = false; if (bar.isConnected) drawBulk(); }
      });
      if (busy) bar.querySelectorAll<HTMLButtonElement>("button").forEach(b => b.disabled = true);
    };
    table.setRows(rows);
    // Второй блок грузим тем же механизмом: лениво при открытии раздела,
    // дальше — кнопкой обновления в его строке действий.
    void drawEntered(root);
  },
};

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
          <p class="muted" style="max-width:640px;margin:0 0 12px">Пульт забудет вход и всё сохранённое. Войти снова можно новой ссылкой от аналитика.</p>
          <button class="btn danger" data-logout>Выйти</button></div>`;
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
