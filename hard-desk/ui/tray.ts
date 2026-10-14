import { hard } from "./kit/api";
const bridge = hard as typeof hard & { trayAction(action: string): void };
void hard.state().then(s => {
  document.getElementById("tray-product")!.textContent = s.product;
  document.getElementById("tray-version")!.textContent = `Версия ${s.version}`;
  if (s.theme === "light" || s.theme === "dark") document.documentElement.dataset.theme = s.theme;
});
const buttons = [...document.querySelectorAll<HTMLButtonElement>("[data-action]")];
buttons.forEach(b => b.addEventListener("click", () => bridge.trayAction(b.dataset.action!)));
buttons[0].focus();
document.addEventListener("keydown", e => {
  if (e.key === "Escape") window.close();
  const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); buttons[(i + (e.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length].focus();
  }
});
