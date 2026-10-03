// Остальные разделы руководителя: просроченные обещания, вход команды, отчёт и
// «это устройство».
import { api, hard, type Shell } from "../kit/api";
import { ago, ask, esc, icon, n, plural, shortDate, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { mountModeSwitchIfKit } from "../kit/modeswitch";
import { showIssued } from "../kit/issued";
import { openPerson, problem, rangeChips, rangeQuery, staleNote, wireRange, type Range } from "./common";

export const promises: Section = {
  id: "promises",
  title: "Обещания",
  icon: "promises",
  kicker: "просроченные обещания оплаты",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div>';
    const r = await api("/api/manager/promises");
    if (!r.ok) { root.innerHTML = problem(r.text || "Не загрузилось"); return; }
    const d = r.data;
    const t = d.totals || {};
    // Одна таблица на всех: сотрудник — столбец, по нему и фильтр, и сортировка.
    const rows = ((d.employees || []) as any[]).flatMap((e) => (e.items as any[]).map((i) => ({ ...i, employee: e.employee })));
    const after = (i: any) => (i.visited_after_promise ? "ездил" : i.last_visit ? "ездил до обещания" : "не ездил");
    root.innerHTML = `${staleNote(r.stale)}
      <div class="kpis"><div class="kpi accent"><b>${n(t.persons)}</b><span>просрочили обещание</span></div>
        <div class="kpi"><b>${n(t.worked)}</b><span>после обещания уже ездили</span></div>
        <div class="kpi"><b>${n(t.untouched)}</b><span>не трогали</span></div></div>
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
          html: (i) => (i.visited_after_promise ? '<span class="tag ok">ездил</span>' : i.last_visit ? '<span class="tag warn">ездил до обещания</span>' : '<span class="tag">не ездил</span>') },
      ],
      onRow: (i) => void openPerson(i.employee, i.rep),
    }).setRows(rows);
  },
};

const ENTRY = (x: any) => (x.entered ? "вошёл" : x.alive ? "ссылка выдана" : x.code ? "ссылка истекла" : "не выдавали");

export const links: Section = {
  id: "links",
  title: "Вход команды",
  icon: "links",
  kicker: "ссылки входа в приложение для выездников",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div>';
    const r = await api("/api/manager/app_links");
    if (!r.ok) { root.innerHTML = problem(r.text || "Не загрузилось"); return; }
    const d = r.data;
    const rows = [d.me, ...(d.links as any[])].filter(Boolean);
    root.innerHTML = `<p class="muted" style="max-width:760px;margin:0 0 6px">Ссылка персональная: перешлите её человеку в личку. Он открывает её на телефоне и сразу попадает в свой портфель. Отозвать — если телефон потерян или человек ушёл.</p>
      <div class="bulk" id="bulk"></div><div id="tbl"></div>`;
    const table = new DataTable<any>(root.querySelector("#tbl")!, {
      id: "manager-links", exportName: "Вход команды", key: (x) => x.employee, select: true,
      cols: [
        { key: "who", title: "Кто", value: (x) => x.label || x.employee, html: (x) => `<span class="who">${esc(x.label || x.employee)}</span>` },
        { key: "entry", title: "Вход", value: ENTRY,
          html: (x) => (x.entered ? `<span class="tag ok">вошёл · ${n(x.devices || 1)} ${plural(x.devices || 1, "устройство", "устройства", "устройств")}</span>`
            : x.alive ? '<span class="tag warn">ссылка выдана</span>' : x.code ? '<span class="tag">ссылка истекла</span>' : '<span class="tag">не выдавали</span>') },
        { key: "seen", title: "Последний вход", value: (x) => String(x.last_use_at || "").replace("T", " ").slice(0, 16),
          html: (x) => (x.last_use_at ? esc(ago(x.last_use_at)) : '<span class="muted">не было</span>') },
        { key: "url", title: "Ссылка", filter: false, value: (x) => (x.alive ? x.url || "" : ""), html: (x) => (x.alive && x.url ? `<span class="url">${esc(x.url)}</span>` : "") },
      ],
      onSelect: () => drawBulk(),
    });
    const drawBulk = () => {
      const bar = root.querySelector<HTMLElement>("#bulk")!;
      const sc = table.scope();
      const ppl = sc.rows;
      const lacking = ppl.filter((x) => !x.alive && !x.entered);
      const live = ppl.filter((x) => x.alive && x.url);
      const canRevoke = ppl.filter((x) => (x.entered || x.code) && x.employee !== "!me");
      bar.innerHTML = `<span class="who">Действия — <b>${esc(sc.label)}</b></span>
        <button class="btn small primary" data-b="issue" ${lacking.length ? "" : "disabled"}>Выдать тем, у кого нет (${lacking.length})</button>
        <button class="btn small" data-b="reissue" ${ppl.length ? "" : "disabled"}>Выдать заново</button>
        <button class="btn small" data-b="copy" ${live.length ? "" : "disabled"}>Скопировать живые ссылки (${live.length})</button>
        <button class="btn small danger" data-b="revoke" ${canRevoke.length ? "" : "disabled"}>Сбросить доступ (${canRevoke.length})</button>
        <span class="spacer"></span>${sc.picked ? '<button class="link" data-b="clear">снять отметки</button>' : ""}`;
      const on = (k: string, f: () => Promise<void>) => bar.querySelector(`[data-b="${k}"]`)?.addEventListener("click", () => void f());
      on("clear", async () => table.clearSelection());
      const issue = async (list: any[], title: string) => {
        if (!list.length) return;
        if (!(await ask(title, `Выдать ссылку ${list.length} чел.? У кого ссылку выдавали, но не открыли, — старая перестанет работать. Кто уже вошёл, из приложения не вылетит.`, "Выдать"))) return;
        toast("Выдаю ссылки…");
        const out: any[] = [];
        for (const x of list) {
          const res = await api("/api/manager/app_link", undefined, "POST", { employee: x.employee });
          out.push({ employee: x.label || x.employee, who: "", url: res.ok ? res.data.url : "", uses: res.ok ? res.data.uses : null,
            expires: res.ok ? String(res.data.expires_at || "").slice(0, 10) : "", error: res.ok ? "" : res.text || "не выдано" });
        }
        await links.render(root, {});
        await showIssued(`Ссылки входа · ${out.length} чел.`, out);
      };
      on("issue", () => issue(lacking, "Выдать вход"));
      on("reissue", () => issue(ppl, "Выдать заново"));
      on("copy", async () => { await hard.copy(live.map((x) => `${x.label || x.employee} — ${x.url}`).join("\n")); toast(`Скопировано ссылок: ${live.length}`); });
      on("revoke", async () => {
        if (!(await ask("Сбросить доступ?", `${canRevoke.length} чел. выйдут из приложения на телефонах, ссылки перестанут работать. Вернуть вход — новой ссылкой.`, "Сбросить", true))) return;
        let done = 0;
        for (const x of canRevoke) { const res = await api("/api/manager/app_revoke", undefined, "POST", { employee: x.employee }); if (res.ok) done++; }
        toast(`Доступ сброшен: ${done} чел.`);
        await links.render(root, {});
      });
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
    render(root) {
      const draw = () => {
        root.innerHTML = `<div class="toolbar">${rangeChips(rep.range, null)}</div>
          <div class="grid2" style="max-width:900px">
            <div class="list-row"><div class="grow"><div class="nm">Статистика команды</div><div class="sub">сводка, люди, выезды, воронка — по окну выше</div></div>
              <button class="btn primary" data-dl="stats">${icon("download")}Скачать</button></div>
            ${control ? `<div class="list-row"><div class="grow"><div class="nm">Контроль</div><div class="sub">риск по людям и итоги обзвона</div></div>
              <button class="btn" data-dl="control">${icon("download")}Скачать</button></div>` : ""}
          </div>`;
        wireRange(root, rep.range, draw);
        root.querySelectorAll<HTMLButtonElement>("[data-dl]").forEach((b) => b.addEventListener("click", async () => {
          const isC = b.dataset.dl === "control";
          const res = await hard.download(isC ? "/api/control/export.xlsx" : "/api/manager/stats.xlsx", rangeQuery(rep.range),
            isC ? "Контроль.xlsx" : "Статистика.xlsx");
          if (res.ok && res.file) { toast("Книга сохранена"); void hard.open(res.file); } else if (res.text) toast(res.text);
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
