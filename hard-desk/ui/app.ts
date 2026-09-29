// Хард — страница окна. Режим «руководитель»: вход один раз по ключу, дальше пульт
// открывается сразу. Разделы видны по возможностям, которые считает сервер (caps):
// безопасник видит «Контроль», руководитель — «Вход команды».
import { api, hard, type Shell } from "./kit/api";
import { $, esc } from "./kit/dom";
import { bare, go, mountShell, refresh, setHead, setSections, setWho, type Section } from "./kit/shell";
import { activity } from "./views/activity";
import { control } from "./views/control";
import { device, links, promises, report } from "./views/misc";
import { team } from "./views/team";

let shell: Shell;

const ROLE: Record<string, string> = { manager: "руководитель", security: "безопасность", full: "руководитель · контроль" };

function enrollScreen(note = "") {
  bare(true);
  setSections([]);
  const host = $("view-inner");
  $("head").hidden = true;
  host.innerHTML = `<div class="page enroll">
    <img class="enroll-logo" src="icon.svg" alt="" onerror="this.remove()">
    <h1>Пульт руководителя</h1>
    <p>Вставьте ссылку входа — ту, что прислал аналитик. Это нужно один раз: дальше пульт на этом компьютере открывается сразу.</p>
    <div class="row"><input class="input" id="link" placeholder="https://…/i/…" autocomplete="off" spellcheck="false">
      <button class="btn primary" id="go">Войти</button></div>
    <div class="err" id="err">${esc(note)}</div>
    <div class="hint">Ссылка персональная — другим её не пересылайте. Если компьютер потеряли или сменили, аналитик отзовёт вход: пульт на старом компьютере сотрёт у себя всё при первом же выходе на связь.</div>
  </div>`;
  const input = $<HTMLInputElement>("link");
  const btn = $<HTMLButtonElement>("go");
  const submit = async () => {
    const v = input.value.trim();
    if (!v) { input.focus(); return; }
    btn.disabled = true;
    $("err").textContent = "";
    const r = await hard.enroll(v);
    btn.disabled = false;
    if (!r.ok) { $("err").textContent = r.text || "Не получилось"; return; }
    await start();
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") void submit(); });
  input.focus();
}

async function start() {
  shell = await hard.state();
  if (shell.theme === "light" || shell.theme === "dark") document.documentElement.dataset.theme = shell.theme;
  if (!shell.enrolled) { enrollScreen(); return; }
  bare(false);
  setHead("…", "");
  const me = await api("/api/manager/device/me");
  if (!me.ok && me.kind === "revoked") { enrollScreen(me.text); return; }
  const info = me.ok ? me.data : null;
  const caps = info?.caps || { team_links: true, control: false };
  const who = info?.me;
  setWho(who ? `<b>${esc(who.name || "Руководитель")}</b>${esc(ROLE[who.role] || "")}${who.scope ? " · " + esc(who.scope) : ""}` : "");
  const list: Section[] = [activity, team, promises];
  if (caps.control) list.push(control);
  if (caps.team_links) list.push(links);
  list.push(report(!!caps.control));
  list.push(device(shell, who, () => enrollScreen()));
  setSections(list);
  let first = "activity";
  try { first = localStorage.getItem("hard.section") || first; } catch { /* */ }
  if (!list.some((s) => s.id === first)) first = "activity";
  await go(first);
}

mountShell(`<b>Хард</b> · пульт руководителя`);
hard.on("revoked", () => enrollScreen("Доступ этого компьютера отозван аналитиком. Всё сохранённое стёрто. Попросите новую ссылку входа."));
document.addEventListener("keydown", (e) => { if (e.key === "F5") { e.preventDefault(); void refresh(); } });
void start();
