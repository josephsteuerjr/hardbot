interface Candidate { source: string; label: string; last: number; kind: string }
interface State { name: string; version: string; removing: boolean; target: string; current: boolean; candidates: Candidate[]; preview: boolean; message?: string }
declare global { interface Window { setup: { state(): Promise<State>; choose(kind: "folder" | "file"): Promise<State>; apply(source: string): Promise<{ ok: boolean; message?: string }>; finish(launch: boolean): Promise<boolean>; window(action: string): void; progress(fn: (text: string) => void): void } } }
export {};
let state: State, chosen = "", scene = "welcome";
const main = document.querySelector<HTMLElement>("#main")!;
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
function button(id: string, fn: () => void) { document.getElementById(id)?.addEventListener("click", fn); }
function draw() {
  document.getElementById("brand")!.textContent = state.name;
  document.getElementById("version")!.textContent = "Версия " + state.version;
  document.getElementById("steps")!.textContent = state.removing ? "Удаление программы" : scene === "welcome" ? "01 Начало  ·  02 Настройки  ·  03 Установка" : scene === "profile" ? "02 Настройки  ·  03 Установка" : "03 Установка";
  if (scene === "welcome" && !state.removing) {
    main.innerHTML = `<p class="eyebrow">Рабочее приложение</p><h1>${esc(state.name)}.<br>На своём месте.</h1><p class="lead">Установим программу и поможем продолжить работу с прежним входом. Можно выбрать найденную копию, папку старой программы или её ярлык.</p><div class="actions"><button class="primary" id="next">Начнём →</button><button class="quiet" id="cancel">Позже</button></div><div class="notes"><div><b>Для вашей учётной записи</b>Ярлыки появятся на рабочем столе и в меню «Пуск».</div><div><b>Работа остаётся с вами</b>При обновлении сохраняются вход, настройки и очередь.</div></div>`;
    button("next", () => { scene = "profile"; draw(); }); button("cancel", () => window.setup.window("close"));
  } else if (state.removing && scene !== "done") {
    main.innerHTML = `<p class="eyebrow">${esc(state.name)}</p><h1>Снять программу?</h1><p class="lead">Удалим приложение и его ярлыки. Вход, настройки и неотправленная работа останутся на этом компьютере — при следующей установке можно будет продолжить.</p><div class="receipt">${esc(state.name)}<div class="path">${esc(state.target)}</div></div><div class="actions"><button class="primary" id="apply">Удалить программу</button><button class="quiet" id="cancel">Оставить</button></div><p class="small">Серверные данные и закрепления сотрудников сохраняются.</p>`;
    button("apply", () => void apply()); button("cancel", () => window.setup.window("close"));
  } else if (scene === "profile") {
    main.innerHTML = `<p class="eyebrow">Настройки и вход</p><h1>${state.current ? "Ваши настройки нашлись." : "Продолжим вашу работу."}</h1><p class="lead">${state.current ? "Действующий вход и очередь уже на этом компьютере. Сохраним их при установке." : "Выберите прежнюю копию или укажите, где лежала программа. В новую установку перенесём выбранные настройки и вход."}</p>${!state.current ? `<div class="list">${state.candidates.map(c => `<label class="candidate"><input type="radio" name="source" value="${esc(c.source)}" ${chosen === c.source ? "checked" : ""}><span><b>${esc(c.label)}</b><span class="path">${esc(c.source)}</span></span></label>`).join("")}<label class="candidate"><input type="radio" name="source" value="" ${chosen ? "" : "checked"}><span>Установить без переноса</span></label></div><div class="actions"><button id="folder">Выбрать папку…</button><button id="file">Ярлык или EXE…</button></div>` : `<div class="receipt">✓ Вход, настройки и очередь сохранятся</div>`}${state.message ? `<p class="error">${esc(state.message)}</p>` : ""}<div class="actions"><button class="primary" id="apply">${chosen ? "Перенести и установить" : state.current ? "Обновить программу" : "Установить программу"}</button><button class="quiet" id="back">← Назад</button></div>`;
    document.querySelectorAll<HTMLInputElement>("input[name=source]").forEach(el => el.addEventListener("change", () => { chosen = el.value; draw(); }));
    const choose = async (kind: "folder" | "file") => { try { state = await window.setup.choose(kind); if (state.candidates.length === 1) chosen = state.candidates[0].source; draw(); } catch { state.message = "Не удалось прочитать выбранную копию. Попробуйте другую папку."; draw(); } };
    button("folder", () => void choose("folder")); button("file", () => void choose("file")); button("apply", () => void apply()); button("back", () => { scene = "welcome"; draw(); });
  } else if (scene === "done") {
    main.innerHTML = `<p class="eyebrow">Готово</p><h1>${state.removing ? "Можно вернуться позже." : "Можно работать."}</h1><p class="lead">${state.removing ? "Программа удалена. Ваши настройки и очередь остались на компьютере." : esc(state.name) + " установлен. " + (chosen ? "Выбранные настройки перенесены." : state.current ? "Ваш вход и настройки сохранены." : "Откройте программу и используйте свою ссылку входа.")}</p><div class="receipt">${esc(state.name)} · ${esc(state.version)}<div class="path">${esc(state.target)}</div></div><div class="actions">${state.removing ? "" : `<button class="primary" id="launch">Открыть ${esc(state.name)}</button>`}<button class="${state.removing ? "primary" : "quiet"}" id="finish">Готово</button></div>`;
    button("launch", () => void window.setup.finish(true)); button("finish", () => void window.setup.finish(false));
  }
}
async function apply() {
  main.innerHTML = `<p class="eyebrow">${esc(state.name)}</p><h1>${state.removing ? "Снимаем программу." : "Готовим рабочее место."}</h1><p class="lead" id="progress">Проверяем настройки…</p><div class="progress-line"></div><p class="small">Дождитесь завершения операции.</p>`;
  let result: { ok: boolean; message?: string };
  try { result = await window.setup.apply(chosen); } catch { result = { ok: false, message: "Мастер не завершил операцию. Повторите запуск." }; }
  if (result.ok) { scene = "done"; draw(); }
  else { main.innerHTML = `<p class="eyebrow">${esc(state.name)}</p><h1>Нужно ещё одно действие.</h1><p class="lead">${esc(result.message)}</p><div class="actions"><button class="primary" id="retry">Повторить</button><button id="back">Назад</button></div>`; button("retry", () => void apply()); button("back", () => draw()); }
}
button("min", () => window.setup.window("minimize")); button("close", () => window.setup.window("close"));
window.setup.progress(text => { const el = document.getElementById("progress"); if (el) el.textContent = text; });
window.setup.state().then(s => { state = s; if (s.candidates.length === 1 && !s.current) chosen = s.candidates[0].source; draw(); });
