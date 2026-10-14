// «Подключение» и «Обслуживание» аналитика: контуры и привязка этого компьютера,
// сервер без SSH (состояние, шесть операций, журнал, коды для новых пультов).
import { hard } from "../kit/api";
import { ask, esc, toast } from "../kit/dom";
import { refresh, type Section } from "../kit/shell";
import { adm, fn, guard, local, note, val } from "./lib";

import { mountModeSwitch } from "../kit/modeswitch";

export const conn: Section = {
  id: "conn",
  title: "Подключение",
  icon: "device",
  bottom: true,
  kicker: "контур, адрес и привязка этого компьютера",
  async render(root) {
    const s = await local("/state");
    const jobs = await local("/jobs");
    const installed = (await hard.state()).fixedProduct;
    root.innerHTML = `
      <div class="toolbar"><span class="muted" style="font-size:13.5px">Контур:</span>
        <div class="chips">${(s.profiles as string[]).map((p) => `<button class="chip" data-prof="${esc(p)}" aria-pressed="${p === s.active}">${esc(p)}</button>`).join("")}</div>
        <span class="spacer"></span><input class="input" id="c-new" placeholder="Новый контур" style="width:170px"><button class="btn small" data-add>Добавить</button></div>
      <div class="section"><div class="section-head"><h2 class="section-title">Сервер</h2></div>
        <div class="toolbar"><input class="input" id="c-url" value="${esc(s.url)}" placeholder="https://сервер" style="width:420px">
        <button class="btn" data-url>Сохранить адрес</button></div></div>
      <div class="section"><div class="section-head"><h2 class="section-title">Этот компьютер</h2>
        <span class="tag ${s.bound ? "ok" : "warn"}">${s.bound ? "привязан" : "не привязан"}</span></div>
        ${note(s.key_state)}
        <div class="toolbar" style="margin-top:12px"><input class="input" id="c-code" placeholder="Одноразовый код привязки" style="width:300px">
          <input class="input" id="c-name" value="${esc(s.device_name)}" style="width:240px">
          <button class="btn primary" data-redeem>Привязать по коду</button></div>
        <div class="toolbar"><input class="input" id="c-key" type="password" placeholder="Мастер-ключ (один раз, на диск не пишется)" style="width:300px">
          <button class="btn" data-key>Привязать мастер-ключом</button>
          ${s.bound ? '<span class="spacer"></span><button class="btn danger small" data-unbind>Забыть токен здесь</button>' : ""}</div></div>
      <div class="section"><div class="section-head"><h2 class="section-title">Устройства пульта</h2></div><div id="devs"><div class="skeleton"></div></div></div>
      <div class="section"><div class="section-head"><h2 class="section-title">Режимы этого exe</h2></div><div id="modesw"><div class="skeleton"></div></div></div>
      <div class="section"><h2 class="section-title">Журнал операций</h2><p class="hint">После потерянного ответа сверьте задачу по номеру. Для почты ищите HardOperationId в черновиках, исходящих и отправленных Outlook. Неопределённая отправка автоматически не повторяется.</p>
      ${(jobs || []).map((j: any) => `<div class="list-row"><div class="grow"><span class="mono">${esc(j.id)}</span> · ${esc(j.kind)} · ${esc(j.state)}<div class="sub">${esc(j.error || "")}</div></div></div>`).join("") || '<p class="muted">Задач в этом контуре пока нет.</p>'}</div>
      <p class="muted" style="font-size:12.5px;margin-top:24px">Настройки: <span class="mono">${esc(s.settings_path)}</span></p>`;
    if (installed && !s.bound) {
      const b = document.createElement("button"); b.className = "btn"; b.textContent = "Перенести настройки прежнего пульта";
      root.prepend(b);
      b.addEventListener("click", guard(async () => {
        const r = await hard.migrate(); if (r.ok) { toast("Контуры и вход перенесены"); await refresh(); } else toast(r.text || "Перенос отменён");
      }));
    }
  void mountModeSwitch(root.querySelector("#modesw")!, "analyst");
    root.querySelectorAll<HTMLButtonElement>("[data-prof]").forEach((b) => b.addEventListener("click", guard(async () => {
      await local("/profile", { action: "switch", name: b.dataset.prof }); toast("Контур: " + b.dataset.prof); await refresh();
    })));
    root.querySelector("[data-add]")?.addEventListener("click", guard(async () => {
      const name = val("c-new");
      if (!name) { toast("Впишите имя контура"); return; }
      await local("/profile", { action: "add", name }); await refresh();
    }));
    root.querySelector("[data-url]")?.addEventListener("click", guard(async () => {
      await local("/profile", { action: "save", url: val("c-url") }); toast("Адрес сохранён"); await refresh();
    }));
    root.querySelector("[data-redeem]")?.addEventListener("click", guard(async () => {
      if (!val("c-code")) { toast("Вставьте код"); return; }
      await local("/bind", { code: val("c-code"), name: val("c-name") }); toast("Устройство привязано по коду"); await refresh();
    }));
    root.querySelector("[data-key]")?.addEventListener("click", guard(async () => {
      if (!val("c-key")) { toast("Впишите мастер-ключ — он нужен один раз"); return; }
      await local("/bind", { key: val("c-key"), name: val("c-name") }); toast("Привязано — ключ не сохранён"); await refresh();
    }));
    root.querySelector("[data-unbind]")?.addEventListener("click", guard(async () => {
      if (!(await ask("Забыть токен?", "Пульт снова будет просить привязку. На сервере запись останется — отзывать её отдельно.", "Забыть", true))) return;
      await local("/unbind"); await refresh();
    }));
    try {
      const d = await adm("GET", "/admin/devices.json");
      const list = (d.devices || []) as any[];
      const lines = await Promise.all(list.map((x) => fn("devices.device_line", x)));
      root.querySelector("#devs")!.innerHTML = list.map((x, i) => `<div class="list-row"><div class="grow"><div class="nm">${esc(lines[i][0])}</div><div class="sub">${esc(lines[i][1])}</div></div></div>`).join("")
        || '<p class="muted">Привязанных устройств нет</p>';
    } catch (e) {
      root.querySelector("#devs")!.innerHTML = `<p class="muted">${esc((e as Error).message)}</p>`;
    }
  },
};

const OPS_TITLE: Record<string, string> = { doctor: "Диагностика", logs: "Журнал", backup: "Копия", restart: "Перезапуск", update: "Обновление", restore: "Восстановление" };
const CONFIRM: Record<string, [string, string]> = {
  restart: ["Перезапустить единицу?", "Контейнеры поднимутся заново, данные не трогаются."],
  update: ["Обновить единицу?", "Сначала снимется копия базы, потом пересборка и перезапуск."],
  restore: ["Восстановить из копии?", "Текущая база отложится рядом, приложение перезапустится."],
};

export const maint: Section = {
  id: "maint",
  title: "Обслуживание",
  icon: "service",
  bottom: true,
  kicker: "сервер без SSH",
  async render(root) {
    root.innerHTML = '<div class="skeleton"></div>';
    let d: any;
    try { d = await adm("GET", "/ops/state.json"); } catch (e) { root.innerHTML = `<div class="notice err">${esc((e as Error).message)}</div>`; return; }
    const agent = d.agent || {};
    const inv = d.inventory || {};
    const units = (inv.units || []) as any[];
    let unit = units[0]?.unit || "";
    const buttons = (d.buttons || []) as any[];
    const draw = () => {
      const cur = units.find((u) => u.unit === unit) || {};
      root.innerHTML = `
        <div class="notice ${agent.alive ? "" : "err"}"><span class="grow">${agent.alive ? `Помощник на хосте отвечает (на связи ${esc(agent.seen || "—")})` : `Помощник на хосте не отвечает: ${esc(agent.reason || "неизвестно")}`}</span>
          <span class="muted">свободно на диске: ${esc(inv.disk_free || "—")}</span></div>
        <div class="section"><div class="section-head"><h2 class="section-title">Единицы</h2></div>
          <div class="chips">${units.map((u) => `<button class="chip" data-unit="${esc(u.unit)}" aria-pressed="${u.unit === unit}">${esc(u.unit)} <span class="n">${(u.backups || []).length} коп.</span></button>`).join("")}</div>
          <div class="toolbar" style="margin-top:12px"><span class="muted" style="font-size:13.5px">Копия для восстановления:</span>
            <select class="input" id="o-backup" style="width:260px">${(cur.backups || []).map((b: string) => `<option>${esc(b)}</option>`).join("")}</select></div>
          <div class="toolbar">${buttons.map((b) => `<button class="btn ${b.kind === "danger" ? "danger" : ""}" data-op="${esc(b.action)}" title="${esc(b.hint || "")}">${esc(b.title || b.action)}</button>`).join("")}</div>
          ${(d.pending || []).length ? `<p class="muted">В очереди: ${esc((d.pending as any[]).map((p) => `${p.action}·${p.unit || ""}`).join(", "))}</p>` : ""}</div>
        <div class="section"><div class="section-head"><h2 class="section-title">Журнал операции</h2></div>
          <pre id="o-log" style="white-space:pre-wrap;font:13px/1.5 var(--mono);border:var(--lw) solid var(--line-ink);border-radius:14px;padding:12px 14px;background:var(--surface);max-height:360px;overflow:auto">Выберите операцию.</pre></div>
        <div class="section"><div class="section-head"><h2 class="section-title">Код для нового пульта аналитика</h2></div>
          <div class="toolbar"><input class="input" id="o-name" placeholder="Имя пульта, например «Ноут аналитика»" style="width:320px"><button class="btn primary" data-code>Выдать код</button></div>
          <div id="o-devs"></div></div>`;
      root.querySelectorAll<HTMLButtonElement>("[data-unit]").forEach((b) => b.addEventListener("click", () => { unit = b.dataset.unit!; draw(); }));
      root.querySelectorAll<HTMLButtonElement>("[data-op]").forEach((b) => b.addEventListener("click", guard(async () => {
        const action = b.dataset.op!;
        const params: Record<string, string> = { unit };
        if (action === "restore") {
          const backup = (root.querySelector("#o-backup") as HTMLSelectElement).value;
          if (!backup) { toast("Копий нет — сначала снимите копию"); return; }
          params.backup = backup;
        }
        const c = CONFIRM[action];
        if (c && !(await ask(c[0], `«${unit}»: ${c[1]}`, "Выполнить", true))) return;
        const r = await adm("POST", "/ops/run.json", { action, params });
        const log = root.querySelector("#o-log")!;
        const title = `${OPS_TITLE[action] || action} · ${unit}`;
        log.textContent = title + ": отправлена…";
        const id = r.id;
        for (let i = 0; i < 300; i++) {
          await new Promise((res) => setTimeout(res, 2000));
          if (!root.isConnected) return;
          const x = await adm("GET", `/ops/result.json?id=${encodeURIComponent(id)}`);
          if (x.done) { log.textContent = `${title} — ${x.exit === 0 ? "готово" : "код возврата " + x.exit}\n\n${x.output || "(без вывода)"}`; break; }
        }
      })));
      root.querySelector("[data-code]")?.addEventListener("click", guard(async () => {
        const name = val("o-name");
        if (!name) { toast("Назовите пульт, чтобы через месяц понять, что отзываете"); return; }
        const r = await adm("POST", "/ops/code.json", { name, role: "operator" });
        await hard.copy(String(r.code || ""));
        toast(`Код «${name}» скопирован — показывается один раз`);
      }));
      void (async () => {
        try {
          const x = await adm("GET", "/ops/devices.json");
          const list = (x.devices || []) as any[];
          const lines = await Promise.all(list.map((dv) => fn("devices.device_line", dv)));
          const box = root.querySelector("#o-devs");
          if (!box) return;
          box.innerHTML = list.map((dv, i) => `<div class="list-row"><div class="grow"><div class="nm">${esc(lines[i][0])}</div><div class="sub">${esc(lines[i][1])}</div></div>
            <button class="btn small danger" data-rev="${esc(dv.id)}" data-name="${esc(dv.name)}">Отозвать</button></div>`).join("");
          box.querySelectorAll<HTMLButtonElement>("[data-rev]").forEach((b) => b.addEventListener("click", guard(async () => {
            if (!(await ask("Отозвать устройство?", await fn("devices.revoke_text", b.dataset.name, Number(b.dataset.rev)), "Отозвать", true))) return;
            await adm("POST", "/ops/device/revoke.json", { id: Number(b.dataset.rev) }); toast("Отозвано"); await refresh();
          })));
        } catch { /* список устройств — не главное */ }
      })();
    };
    draw();
  },
};
