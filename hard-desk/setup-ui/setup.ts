interface Candidate { source: string; label: string; last: number; kind: string }
interface State { name: string; version: string; removing: boolean; target: string; installed: boolean; current: boolean; candidates: Candidate[]; preview: boolean; automaticUpdate: boolean; message?: string }
declare global { interface Window { setup: { state(): Promise<State>; choose(kind: "folder" | "file"): Promise<State>; apply(source: string): Promise<{ ok: boolean; message?: string }>; finish(launch: boolean): Promise<boolean>; window(action: string): void; progress(fn: (text: string) => void): void } } }
export {};
let state: State, chosen = "", scene = "welcome";
const main = document.querySelector<HTMLElement>("#main")!;
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
function button(id: string, fn: () => void) { document.getElementById(id)?.addEventListener("click", fn); }
function draw() {
  document.getElementById("version")!.textContent = state.version;
  document.title = state.removing ? "Удаление CRM" : "Установка CRM";
  if (scene === "welcome" && !state.removing) {
    const action = state.installed ? "Обновить" : "Установить";
    const notes = state.installed ? "Мастер закроет запущенную программу. Вход, настройки и очередь сохранятся."
      : state.current ? "Сохранённый вход, настройки и очередь будут доступны после установки."
      : "Программа установится для вашей учётной записи Windows.";
    main.innerHTML = `<h1>${action} программу</h1><p class="product-name">${esc(state.name)}</p><p class="lead">${notes}</p><div class="receipt"><div class="path">${esc(state.target)}</div></div><div class="actions"><button class="primary" id="apply">${action}</button>${state.current ? "" : '<button class="quiet" id="next">Перенести прежние настройки</button>'}<button class="quiet" id="cancel">Отмена</button></div>`;
    button("apply", () => { chosen = ""; void apply(); });
    button("next", () => { scene = "profile"; draw(); }); button("cancel", () => window.setup.window("close"));
  } else if (state.removing && scene !== "done") {
    main.innerHTML = `<h1>Удалить программу</h1><p class="product-name">${esc(state.name)}</p><p class="lead">Мастер удалит приложение и ярлыки. Вход, настройки, черновики и очередь сохранятся для следующей установки.</p><div class="receipt"><div class="path">${esc(state.target)}</div></div><div class="actions"><button class="primary" id="apply">Удалить</button><button class="quiet" id="cancel">Отмена</button></div>`;
    button("apply", () => void apply()); button("cancel", () => window.setup.window("close"));
  } else if (scene === "profile") {
    main.innerHTML = `<h1>Перенос настроек</h1><p class="lead">Выберите прежнюю копию или укажите папку либо ярлык программы.</p><div class="list">${state.candidates.map(c => `<label class="candidate"><input type="radio" name="source" value="${esc(c.source)}" ${chosen === c.source ? "checked" : ""}><span><b>${esc(c.label)}</b><span class="path">${esc(c.source)}</span></span></label>`).join("")}</div><div class="actions"><button id="folder">Выбрать папку…</button><button id="file">Ярлык или EXE…</button></div>${state.message ? `<p class="error" role="alert">${esc(state.message)}</p>` : ""}<div class="actions"><button class="primary" id="apply" ${chosen ? "" : "disabled"}>Перенести и установить</button><button class="quiet" id="back">Назад</button></div>`;
    document.querySelectorAll<HTMLInputElement>("input[name=source]").forEach(el => el.addEventListener("change", () => { chosen = el.value; draw(); }));
    const choose = async (kind: "folder" | "file") => { try { state = await window.setup.choose(kind); if (state.candidates.length === 1) chosen = state.candidates[0].source; draw(); } catch { state.message = "Не удалось прочитать выбранную копию. Попробуйте другую папку."; draw(); } };
    button("folder", () => void choose("folder")); button("file", () => void choose("file")); button("apply", () => void apply()); button("back", () => { scene = "welcome"; draw(); });
  } else if (scene === "done") {
    main.innerHTML = `<h1>${state.removing ? "Удаление завершено" : "Установка завершена"}</h1><p class="lead">${state.removing ? "Ваши настройки и очередь остались на компьютере." : chosen ? "Выбранные настройки перенесены." : state.current ? "Ваш вход и настройки сохранены." : "Откройте программу и войдите в свой профиль."}</p><div class="actions">${state.removing ? "" : `<button class="primary" id="launch">Открыть программу</button>`}<button class="${state.removing ? "primary" : "quiet"}" id="finish">Готово</button></div>`;
    button("launch", () => void window.setup.finish(true)); button("finish", () => void window.setup.finish(false));
  }
}
async function apply() {
  const title = state.removing ? "Удаление программы" : state.installed || state.automaticUpdate ? "Обновление программы" : "Установка программы";
  main.innerHTML = `<h1>${title}</h1><p class="product-name">${esc(state.name)}</p><p class="lead" id="progress" role="status">Проверяем файлы…</p><div class="progress-line"></div>`;
  let result: { ok: boolean; message?: string };
  try { result = await window.setup.apply(chosen); } catch { result = { ok: false, message: "Мастер не завершил операцию. Повторите запуск." }; }
  if (result.ok) {
    if (state.automaticUpdate) { await window.setup.finish(false); return; }
    scene = "done"; draw();
  } else {
    main.innerHTML = `<h1>Операция не завершена</h1><p class="product-name">${esc(state.name)}</p><p class="lead" role="alert">${esc(result.message)}</p><div class="actions"><button class="primary" id="retry">Повторить</button><button class="quiet" id="back">${state.automaticUpdate ? "Закрыть" : "Назад"}</button></div>`;
    button("retry", () => void apply());
    button("back", () => state.automaticUpdate ? void window.setup.finish(false) : draw());
  }
}
button("min", () => window.setup.window("minimize")); button("close", () => window.setup.window("close"));
window.setup.progress(text => { const el = document.getElementById("progress"); if (el) el.textContent = text; });
window.setup.state().then(s => {
  state = s;
  if (s.automaticUpdate) { void apply(); return; }
  if (s.candidates.length === 1 && !s.current) chosen = s.candidates[0].source;
  draw();
});
