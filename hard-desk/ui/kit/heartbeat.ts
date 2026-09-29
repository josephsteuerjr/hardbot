// Пульс связи: раз в 20 с тихий запрос к серверу.
//
// 30.09.2026 после сбоя провайдера пульт «не подключался», хотя сервер уже
// отвечал: индикатор в углу менялся только от запросов экрана, и на экранах
// без запросов («Рассылка») висело «Подключение…» или «Нет связи» до первого
// клика. Теперь индикатор честный сам по себе, а когда связь вернулась после
// обрыва, экран обновляется без участия человека (если он не печатает).
type Beat = "live" | "off" | "revoked";

let lostAt = "";
let timer = 0;

function show(level: "live" | "off", text: string): void {
  const pill = document.getElementById("state");
  const label = document.getElementById("state-text");
  if (pill) pill.dataset.level = level;
  if (label) label.textContent = text;
}

function now(): string {
  return new Date().toTimeString().slice(0, 5);
}

/** check — тихий запрос к серверу; onBack — что делать, когда связь вернулась
 *  после обрыва (обычно перерисовать экран). */
export function startHeartbeat(check: () => Promise<Beat>, onBack: () => unknown, everyMs = 20000): void {
  const beat = async () => {
    let s: Beat;
    try { s = await check(); } catch { s = "off"; }
    if (s === "revoked") return;                 // отзыв показывает своя ветка
    const label = document.getElementById("state-text")?.textContent || "";
    if (s === "off") {
      if (!lostAt) lostAt = now();
      show("off", `Нет связи с ${lostAt}`);
      return;
    }
    const wasLost = !!lostAt || /Нет связи/.test(label);
    lostAt = "";
    if (wasLost || /Подключение/.test(label)) show("live", "На связи · " + now());
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
    if (wasLost && !typing) void onBack();
  };
  if (timer) clearInterval(timer);
  void beat();
  timer = window.setInterval(() => void beat(), everyMs);
}
