export {};
type State = {
  name: string;
  version: string;
  removing: boolean;
  target: string;
  preview: boolean;
};
const api = (window as any).setup;
const element = (id: string) => document.getElementById(id)!;
const escape = (value: string) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
let state: State;
function bind(id: string, handler: () => void) {
  element(id)?.addEventListener("click", handler);
}
function draw(done = false, error = "") {
  element("brand").textContent = "CRM СНГ";
  element("version").textContent = state.version;
  element("steps").textContent = state.removing
    ? "Удаление"
    : done
      ? "Установка завершена"
      : "Отдельная установка\nПроверочный сценарий";
  const title = done
    ? state.removing
      ? "Удаление завершено"
      : "Установка завершена"
    : state.removing
      ? "Удаление CRM СНГ"
      : "Установка CRM СНГ";
  const description = done
    ? state.removing
      ? "Приложение удалено. Настройки и очередь сохранены."
      : "Приложение готово. При первом запуске откроется отдельная синтетическая база."
    : state.removing
      ? "Будут удалены программа и её ярлыки. Настройки, история проверочного сценария и неотправленные действия сохранятся."
      : "Программа устанавливается для текущего пользователя. После установки войдите в CRM с рабочим логином и паролем.";
  element("main").innerHTML =
    `<h1>${title}</h1><p>${description}</p><div class="path">${escape(state.target)}</div>${error ? `<p class="error">${escape(error)}</p>` : ""}<div class="actions">${done ? `${state.removing ? "" : '<button class="primary" id="launch">Открыть</button>'}<button id="finish">Закрыть</button>` : `<button class="primary" id="apply">${state.removing ? "Удалить" : "Установить"}</button><button id="cancel">Отмена</button>`}</div>`;
  bind("apply", () => void apply());
  bind("cancel", () => api.window("close"));
  bind("launch", () => api.finish(true));
  bind("finish", () => api.finish(false));
}
async function apply() {
  element("main").innerHTML =
    `<h1>${state.removing ? "Удаление" : "Установка"}</h1><p id="progress">Проверка файлов…</p>`;
  try {
    const result = await api.apply("");
    draw(
      !!result.ok,
      result.ok ? "" : result.message || "Операция не завершена.",
    );
  } catch {
    draw(false, "Операция не подтверждена. Повторите проверку.");
  }
}
bind("min", () => api.window("minimize"));
bind("close", () => api.window("close"));
api.progress((text: string) => {
  const target = document.getElementById("progress");
  if (target) target.textContent = text;
});
void api.state().then((value: State) => {
  state = value;
  draw();
});
