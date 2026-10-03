// Переключатель режимов — ТОЛЬКО в пульте аналитика (внутри комплекта).
// Сотрудникам СНГ роль определяет их ссылка входа; они о режимах не знают.
// Входы всех классов живут на компьютере одновременно и не вытесняют друг друга.
import { hard } from "./api";
import { esc } from "./dom";

/** Только на компьютерах комплекта (у аналитика): сотрудникам СНГ роль
 *  определяет ссылка, и переключателя у них нет вовсе. */
export async function mountModeSwitchIfKit(host: HTMLElement, current: string): Promise<void> {
  try {
    const st = await hard.state();
    if (!st.kit) { host.innerHTML = ""; return; }
  } catch { host.innerHTML = ""; return; }
  await mountModeSwitch(host, current);
}

export async function mountModeSwitch(host: HTMLElement, current: string): Promise<void> {
  const items: [string, string, string][] = [
    ["manager", "Пульт", "руководитель, безопасник, директор — сводка, команда, контроль"],
    ["field", "Приложение сотрудника", "выездник и софт: экран (портфель или очередь звонков) определяет сервер по портфелю"],
    ["analyst", "Аналитик", "реестры, обогащение, рассылка — только внутри комплекта"],
  ];
  host.innerHTML = `
    <p class="muted" style="max-width:640px">Окно поддержки: переключение ролей этого exe.
      Входы всех классов живут одновременно; смена режима перезагружает окно и ничего не отзывает.</p>
    <div class="list">${items.map(([m, t, s]) => `
      <div class="list-row"><div class="grow"><div class="nm">${esc(t)}
        ${m === current ? '<span class="tag ok">сейчас</span>' : ""}</div>
        <div class="sub">${esc(s)}</div></div>
        ${m === current ? "" : `<button class="btn" data-mode="${m}">Открыть</button>`}</div>`).join("")}</div>`;
  host.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      const r = await hard.setMode(b.dataset.mode!);
      if (!r.ok) { b.disabled = false; b.textContent = r.text || "Не получилось"; }
    }));
}
