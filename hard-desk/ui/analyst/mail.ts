// «Рассылка»: книги Excel гендиректору и заму — письмом из Outlook владельца.
// Пульт живёт в трее, питон-сторона раз в полминуты сверяется с расписанием
// (pult/core/mailer.py). Здесь — кому, что, когда, и журнал отправок.
import { hard } from "../kit/api";
import { ask, esc, toast } from "../kit/dom";
import { refresh, type Section } from "../kit/shell";
import { DataTable } from "../kit/table";
import { guard, local, note } from "./lib";

type Person = { name: string; email: string };

async function job(body: unknown, log: (s: string) => void): Promise<any> {
  const { job: id } = await local("/mail/send", body);
  for (;;) {
    await new Promise((r) => setTimeout(r, 700));
    const j = await local("/job", { id });
    if (j.log?.length) log(j.log[j.log.length - 1]);
    if (j.state === "done") return j.result;
    if (j.state === "failed") throw new Error(j.error || "не вышло");
  }
}

export const mail: Section = {
  id: "mail",
  title: "Рассылка",
  icon: "mail",
  kicker: "книги Excel почтой — из вашего Outlook, по расписанию",
  async render(root) {
    const s = await local("/mail/state");
    const cfg = s.config;
    const auto = await hard.autostart().catch(() => false);
    const people: Person[] = (cfg.recipients as Person[]).map((p) => ({ ...p }));
    const days = new Set<number>(cfg.days as number[]);
    const books: Record<string, boolean> = { ...cfg.books };
    let direction: string = cfg.direction || "";
    const weekdays = s.weekdays as string[];

    const state = cfg.enabled
      ? `<span class="tag ok">включена</span> <span class="muted">${esc(s.next ? "следующая: " + s.next : "")}</span>`
      : '<span class="tag">выключена</span>';
    const outlook = s.outlook ? '<span class="tag ok">Outlook открыт</span>'
      : '<span class="tag warn">Outlook закрыт — письмо откроет его само</span>';

    root.innerHTML = `
      <div class="toolbar"><label class="check"><input type="checkbox" id="m-on" ${cfg.enabled ? "checked" : ""}> Рассылка включена</label>
        ${state}<span class="spacer"></span>${outlook}</div>
      <div class="section"><div class="section-head"><h2 class="section-title">Кому</h2></div>
        <div id="m-people"></div>
        <div class="toolbar"><button class="btn small" data-add>Добавить получателя</button></div></div>
      <div class="section"><div class="section-head"><h2 class="section-title">Что и когда</h2></div>
        <div class="form-grid">
          <label class="check"><input type="checkbox" data-book="day" ${books.day ? "checked" : ""}> ${esc(s.books.day)}</label>
          <div class="chips">${weekdays.map((d, i) => `<button class="chip toggle" data-day="${i}" aria-pressed="${days.has(i)}">${esc(d)}</button>`).join("")}</div>
          <label class="check"><input type="checkbox" data-book="week" ${books.week ? "checked" : ""}> ${esc(s.books.week)} — за 7 дней, по</label>
          <select class="input" id="m-wd" style="width:120px">${weekdays.map((d, i) => `<option value="${i}" ${i === cfg.week_day ? "selected" : ""}>${esc(d)}</option>`).join("")}</select>
          <span class="muted">Время отправки</span>
          <input class="input" id="m-time" value="${esc(cfg.time)}" style="width:90px" placeholder="19:30">
          <span class="muted">Кого считать</span>
          <div class="chips">${Object.entries(s.directions as Record<string, string>).map(([k, t]) => `<button class="chip" data-dir="${esc(k)}" aria-pressed="${k === direction}">${esc(t)}</button>`).join("")}</div>
          <span class="muted">Тема начинается с</span>
          <input class="input" id="m-subj" value="${esc(cfg.subject)}" style="width:280px">
          <span class="muted">Своя строка в письме</span>
          <textarea class="input" id="m-note" rows="2" style="width:520px;height:auto;padding:8px 12px" placeholder="необязательно">${esc(cfg.note)}</textarea>
        </div>
        <div class="toolbar" style="margin-top:14px"><button class="btn primary" data-save>Сохранить</button>
          <span class="spacer"></span>
          <select class="input" id="m-book" style="width:170px">${Object.entries(s.books as Record<string, string>).map(([k, t]) => `<option value="${esc(k)}">${esc(t)}</option>`).join("")}</select>
          <button class="btn" data-show>Показать письмо</button>
          <button class="btn" data-send>Отправить сейчас</button></div>
        <p class="muted" id="m-log" style="font-size:13px;min-height:18px;margin:6px 0 0"></p></div>
      <div class="section"><div class="section-head"><h2 class="section-title">Как это работает</h2></div>
        ${note("Письмо уходит из вашего Outlook — от вашего имени, с вашей подписью, копия остаётся в «Отправленных». Книгу пульт берёт с сервера в момент отправки, итоги дня — в тексте письма.\nПульт должен быть запущен: крестик сворачивает его к часам. Если компьютер был выключен в назначенное время, письмо уйдёт, когда пульт запустится в тот же день.")}
        <div class="toolbar" style="margin-top:10px"><label class="check"><input type="checkbox" id="m-auto" ${auto ? "checked" : ""}> Запускать пульт вместе с Windows (сразу к часам)</label></div></div>
      <div class="section"><div class="section-head"><h2 class="section-title">Отправки</h2></div><div id="m-hist"></div></div>`;

    const drawPeople = () => {
      const box = root.querySelector<HTMLElement>("#m-people")!;
      box.innerHTML = people.map((p, i) => `<div class="toolbar" style="margin:6px 0 0">
          <input class="input" data-pn="${i}" value="${esc(p.name)}" placeholder="Кто это (гендиректор, зам…)" style="width:240px">
          <input class="input" data-pe="${i}" value="${esc(p.email)}" placeholder="адрес@почта" style="width:300px">
          <button class="btn small" data-del="${i}" title="Убрать">×</button></div>`).join("")
        || '<p class="muted" style="margin:6px 0 0">Получателей пока нет.</p>';
      box.querySelectorAll<HTMLInputElement>("[data-pn]").forEach((el) => el.addEventListener("input", () => { people[Number(el.dataset.pn)].name = el.value; }));
      box.querySelectorAll<HTMLInputElement>("[data-pe]").forEach((el) => el.addEventListener("input", () => { people[Number(el.dataset.pe)].email = el.value.trim(); }));
      box.querySelectorAll<HTMLButtonElement>("[data-del]").forEach((b) => b.addEventListener("click", () => { people.splice(Number(b.dataset.del), 1); drawPeople(); }));
    };
    drawPeople();
    root.querySelector("[data-add]")!.addEventListener("click", () => { people.push({ name: "", email: "" }); drawPeople(); });
    root.querySelectorAll<HTMLButtonElement>("[data-day]").forEach((b) => b.addEventListener("click", () => {
      const d = Number(b.dataset.day);
      if (days.has(d)) days.delete(d); else days.add(d);
      b.setAttribute("aria-pressed", String(days.has(d)));
    }));
    root.querySelectorAll<HTMLInputElement>("[data-book]").forEach((el) => el.addEventListener("change", () => { books[el.dataset.book!] = el.checked; }));
    root.querySelectorAll<HTMLButtonElement>("[data-dir]").forEach((b) => b.addEventListener("click", () => {
      direction = b.dataset.dir || "";
      root.querySelectorAll<HTMLButtonElement>("[data-dir]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    }));

    const collect = (enabled?: boolean) => ({
      enabled: enabled ?? root.querySelector<HTMLInputElement>("#m-on")!.checked,
      recipients: people, days: [...days], books, direction,
      week_day: Number(root.querySelector<HTMLSelectElement>("#m-wd")!.value),
      time: root.querySelector<HTMLInputElement>("#m-time")!.value,
      subject: root.querySelector<HTMLInputElement>("#m-subj")!.value,
      note: root.querySelector<HTMLTextAreaElement>("#m-note")!.value,
    });
    const save = async (enabled?: boolean) => { await local("/mail/set", collect(enabled)); };
    root.querySelector("[data-save]")!.addEventListener("click", guard(async () => { await save(); toast("Рассылка сохранена"); await refresh(); }));
    root.querySelector<HTMLInputElement>("#m-on")!.addEventListener("change", guard(async (e: Event) => {
      const el = e.target as HTMLInputElement;
      try { await save(el.checked); } catch (err) { el.checked = !el.checked; throw err; }
      toast(el.checked ? "Рассылка включена" : "Рассылка выключена");
      await refresh();
    }));
    root.querySelector<HTMLInputElement>("#m-auto")!.addEventListener("change", guard(async (e: Event) => {
      const on = await hard.autostart((e.target as HTMLInputElement).checked);
      toast(on ? "Пульт будет запускаться вместе с Windows" : "Автозапуск выключен");
    }));
    const log = (t: string) => { root.querySelector("#m-log")!.textContent = t; };
    const run = (display: boolean) => guard(async () => {
      await save();
      const book = root.querySelector<HTMLSelectElement>("#m-book")!.value;
      if (!display && !(await ask("Отправить сейчас?", `Письмо с книгой «${s.books[book]}» уйдёт: ${people.map((p) => p.email).join(", ")}.`, "Отправить"))) return;
      log("…");
      const r = await job({ book, display }, log);
      log(display ? "Черновик открыт в Outlook — проверьте и закройте без отправки." : `Отправлено: ${r.to.join(", ")}`);
      if (!display) await refresh();
    });
    root.querySelector("[data-show]")!.addEventListener("click", run(true));
    root.querySelector("[data-send]")!.addEventListener("click", run(false));

    new DataTable<any>(root.querySelector("#m-hist")!, {
      id: "analyst-mail-history", exportName: "Рассылка — отправки", key: (h) => h.at + h.book,
      empty: "Писем ещё не было.",
      cols: [
        { key: "at", title: "Когда", value: (h) => h.at, html: (h) => `<span class="mono">${esc(h.at)}</span>` },
        { key: "book", title: "Книга", value: (h) => h.book },
        { key: "state", title: "Итог", value: (h) => h.state, html: (h) => `<span class="tag ${h.ok ? "ok" : "err"}">${esc(h.state)}</span>` },
        { key: "to", title: "Кому", value: (h) => h.to },
        { key: "error", title: "Ошибка", filter: false, value: (h) => h.error },
      ],
    }).setRows(s.history);
  },
};
