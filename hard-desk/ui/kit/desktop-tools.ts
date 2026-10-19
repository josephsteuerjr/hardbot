import { hard } from "./api";
import { esc, toast } from "./dom";

export function mountDesktopTools(): void {
  void hard.state().then(s => { if (s.fixedProduct) document.getElementById("sign")!.textContent = s.product; });
  document.addEventListener("input", e => {
    const el = e.target as HTMLElement;
    if (el.matches("input,textarea,select") && !el.closest(".toolbar,.tbar,.crm-filters,.fpop,.enroll,.update-pop,.column-picker")) el.dataset.hardDirty = "1";
  });
  let menu: HTMLElement | null = null;
  const close = () => { menu?.remove(); menu = null; };
  document.addEventListener("contextmenu", e => {
    e.preventDefault(); close();
    const target = e.target as HTMLElement;
    const editable = !!target.closest("input,textarea,[contenteditable=true]");
    const items = editable ? [["undo", "Отменить"], ["cut", "Вырезать"], ["copy", "Копировать"], ["paste", "Вставить"], ["selectAll", "Выделить всё"]]
      : [["copy", "Копировать"], ["selectAll", "Выделить всё"]];
    menu = document.createElement("div"); menu.className = "brand-menu"; menu.setAttribute("role", "menu");
    menu.innerHTML = items.map(([action, title]) => `<button role="menuitem" data-edit="${action}">${title}</button>`).join("");
    document.body.append(menu);
    menu.style.left = Math.max(8, Math.min(e.clientX, innerWidth - menu.offsetWidth - 8)) + "px";
    menu.style.top = Math.max(8, Math.min(e.clientY, innerHeight - menu.offsetHeight - 8)) + "px";
    menu.querySelectorAll<HTMLElement>("[data-edit]").forEach(b => {
      b.addEventListener("mousedown", ev => ev.preventDefault());
      b.addEventListener("click", () => { if (editable) target.focus(); hard.edit(b.dataset.edit!); close(); });
    });
    menu.addEventListener("keydown", ev => {
      const buttons = [...menu!.querySelectorAll<HTMLButtonElement>("button")];
      const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); buttons[(i + (ev.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length].focus(); }
      if (ev.key === "Escape") { close(); target.focus(); }
    });
    if (!e.clientX && !e.clientY) menu.querySelector<HTMLButtonElement>("button")?.focus();
  });
  document.addEventListener("mousedown", e => { if (menu && !menu.contains(e.target as Node)) close(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  window.addEventListener("blur", close);

  const button = document.createElement("button"); button.className = "chrome-btn update-button"; button.title = "Обновление программы"; button.textContent = "↻";
  document.querySelector(".chrome-right")?.prepend(button);
  let pop: HTMLElement | null = null;
  let latest: any = null;
  const draw = () => {
    if (!pop) return;
    pop.innerHTML = `<b>Обновление программы</b><p>${esc(latest?.text || "Загрузка состояния…")}</p>
      ${latest?.version ? `<p>Версия ${esc(latest.version)} ${latest.phase === "downloading" ? `· ${latest.percent || 0}%` : ""}</p>` : ""}
      <div class="row"><button class="btn small" data-check ${["checking", "downloading"].includes(latest?.phase) ? "disabled" : ""}>Проверить</button>
      ${latest?.phase === "ready" ? '<button class="btn small primary" data-install>Перезагрузить для установки обновления</button>' : ""}<button class="btn small" data-dismiss>Закрыть</button></div>`;
    pop.querySelector("[data-check]")?.addEventListener("click", async () => { latest = await hard.update("check"); draw(); });
    pop.querySelector("[data-dismiss]")?.addEventListener("click", () => { pop?.remove(); pop = null; });
    pop.querySelector("[data-install]")?.addEventListener("click", async () => {
      if (document.querySelector('[data-hard-dirty="1"]')) { toast("Сохраните или закройте изменённую форму перед обновлением"); return; }
      const r = await hard.update("install"); if (!r.ok) toast(r.text || "Обновление не началось");
    });
  };
  button.addEventListener("click", async () => {
    if (pop) { pop.remove(); pop = null; return; }
    pop = document.createElement("div"); pop.className = "update-pop"; pop.setAttribute("role", "dialog"); pop.setAttribute("aria-label", "Обновление программы"); document.body.append(pop);
    latest = await hard.update("state"); draw();
  });
  const renderState = (s: any) => {
    latest = s;
    const ready = latest?.phase === "ready";
    button.classList.toggle("ready", ready);
    button.style.width = ready ? "auto" : "";
    button.style.padding = ready ? "0 12px" : "";
    button.textContent = ready ? "Перезагрузить для установки обновления" : "↻";
    button.title = ready ? latest.text : "Обновление программы";
    draw();
  };
  hard.on("update", renderState);
  void hard.update("state").then(renderState);
}
