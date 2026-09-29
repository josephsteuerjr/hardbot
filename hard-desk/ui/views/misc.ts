// Остальные разделы руководителя: просроченные обещания, вход команды, отчёт и
// «это устройство».
import { api, hard, type Shell } from "../kit/api";
import { ago, ask, esc, icon, n, plural, shortDate, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
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
    const emps = (d.employees || []) as any[];
    const t = d.totals || {};
    root.innerHTML = `${staleNote(r.stale)}
      <div class="kpis"><div class="kpi accent"><b>${n(t.persons)}</b><span>просрочили обещание</span></div>
        <div class="kpi"><b>${n(t.worked)}</b><span>после обещания уже ездили</span></div>
        <div class="kpi"><b>${n(t.untouched)}</b><span>не трогали</span></div></div>
      ${emps.map((e) => `<div class="section"><div class="section-head"><h2 class="section-title">${esc(e.employee)}</h2>
          <span class="section-note">${n(e.items.length)} ${plural(e.items.length, "человек", "человека", "человек")}</span></div>
        <table class="grid"><thead><tr><th class="l">Должник</th><th>Обещал до</th><th>Просрочка</th><th>Сумма</th><th class="l">После обещания</th></tr></thead>
        <tbody>${(e.items as any[]).map((i) => `<tr class="pick" data-emp="${esc(e.employee)}" data-rep="${esc(i.rep)}">
          <td class="l"><span class="who">${esc(i.client)}</span><span class="sub mono">${esc(i.rep)}</span></td>
          <td>${esc(shortDate(i.promise_date || ""))}</td><td>${n(i.days_overdue)} дн</td><td>${esc(i.money_text || "")}</td>
          <td class="l">${i.visited_after_promise ? '<span class="tag ok">ездил</span>' : i.last_visit ? '<span class="tag warn">ездил до обещания</span>' : '<span class="tag">не ездил</span>'}</td></tr>`).join("")}</tbody></table></div>`).join("")
        || '<div class="empty"><b>Просроченных нет</b>Все обещания в срок или оплачены.</div>'}`;
    root.querySelectorAll<HTMLElement>("tr[data-rep]").forEach((tr) => tr.addEventListener("click", () => {
      void openPerson(tr.dataset.emp!, tr.dataset.rep!);
    }));
  },
};

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
    const status = (x: any) => x.entered
      ? `<span class="tag ok">вошёл · ${n(x.devices || 1)} ${plural(x.devices || 1, "устройство", "устройства", "устройств")}</span>`
      : x.code ? '<span class="tag warn">ссылка выдана, не открыта</span>' : '<span class="tag">не выдавали</span>';
    root.innerHTML = `<p class="muted" style="max-width:760px;margin:0 0 14px">Ссылка персональная: перешлите её человеку в личку. Он открывает её на телефоне и сразу попадает в свой портфель. Отозвать — если телефон потерян или человек ушёл.</p>
      <div>${rows.map((x) => `<div class="list-row" data-emp="${esc(x.employee)}">
        <div class="grow"><div class="nm">${esc(x.label || x.employee)}</div>
          <div class="sub">${x.last_use_at ? "последний вход " + esc(ago(x.last_use_at)) : "входов не было"}${x.last_agent ? " · " + esc(x.last_agent) : ""}</div>
          ${x.url ? `<div class="url" style="margin-top:4px">${esc(x.url)}</div>` : ""}</div>
        ${status(x)}
        <button class="btn small" data-issue>${x.code ? "Новая ссылка" : "Выдать вход"}</button>
        ${x.url ? `<button class="btn small" data-copy="${esc(x.url)}">${icon("copy")}Копировать</button>` : ""}
        ${x.entered && x.employee !== "!me" ? '<button class="btn small danger" data-revoke>Отозвать</button>' : ""}
      </div>`).join("")}</div>`;
    root.querySelectorAll<HTMLElement>(".list-row").forEach((row) => {
      const emp = row.dataset.emp!;
      row.querySelector("[data-issue]")?.addEventListener("click", async () => {
        const res = await api("/api/manager/app_link", undefined, "POST", { employee: emp });
        if (!res.ok) { toast(res.text || "Не выдалось"); return; }
        await hard.copy(res.data.url);
        toast("Ссылка скопирована — перешлите её человеку");
        void links.render(root, {});
      });
      row.querySelector<HTMLButtonElement>("[data-copy]")?.addEventListener("click", async (e) => {
        await hard.copy((e.currentTarget as HTMLButtonElement).dataset.copy!); toast("Скопировано");
      });
      row.querySelector("[data-revoke]")?.addEventListener("click", async () => {
        if (!(await ask("Отозвать вход?", `«${emp}» выйдет из приложения на телефоне. Новую ссылку можно выдать в любой момент.`, "Отозвать", true))) return;
        const res = await api("/api/manager/app_revoke", undefined, "POST", { employee: emp });
        toast(res.ok ? "Вход отозван" : res.text || "Не отозвалось");
        void links.render(root, {});
      });
    });
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
        <div class="section"><div class="section-head"><h2 class="section-title">Вид</h2></div>
          <div class="chips">${[["system", "Как в Windows"], ["light", "Светлый"], ["dark", "Тёмный"]].map(([k, t]) =>
            `<button class="chip" data-theme="${k}" aria-pressed="${theme === k}">${t}</button>`).join("")}</div></div>
        <div class="section"><div class="section-head"><h2 class="section-title">Выйти на этом компьютере</h2></div>
          <p class="muted" style="max-width:640px;margin:0 0 12px">Пульт забудет вход и всё сохранённое. Войти снова можно новой ссылкой от аналитика.</p>
          <button class="btn danger" data-logout>Выйти</button></div>`;
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
