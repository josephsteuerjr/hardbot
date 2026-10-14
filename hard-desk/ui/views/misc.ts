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
      <div class="bulk" id="bulk"></div><div id="tbl"></div>`;
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
