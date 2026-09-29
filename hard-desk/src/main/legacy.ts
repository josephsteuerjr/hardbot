// Прежний пульт руководителя (hard-pult на Rust, до 29.09.2026) закрытием вкладки
// браузера не выключался: сервер на 127.0.0.1 жил дальше, каждый двойной щелчок
// поднимал ещё один, а сам exe нельзя было удалить — файл занят. Люди его ещё и
// переименовывали (тот же «Хард.exe»), поэтому узнаём его не по имени, а по
// соседу: файл «настройки пульта руководителя.json» рядом с exe — его и только его.
// Новый пульт при старте гасит такие процессы, чтобы старый можно было удалить.
import { execFile } from "node:child_process";

export function stopLegacyPult(log: (s: string) => void): void {
  if (process.platform !== "win32") return;
  const me = process.execPath.replace(/'/g, "''");
  const script = `
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$me = '${me}'
Get-CimInstance Win32_Process | Where-Object {
  $_.ExecutablePath -and $_.ExecutablePath -ne $me -and (
    $_.Name -eq 'hard-pult.exe' -or
    (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $_.ExecutablePath) 'настройки пульта руководителя.json')))
} | ForEach-Object {
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  $_.ExecutablePath
}`;
  const encoded = Buffer.from(script, "utf16le").toString("base64");
  execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
    { windowsHide: true, timeout: 20000, encoding: "utf8" }, (err, stdout) => {
      if (err) { log("старый пульт: проверка не удалась — " + String(err.message || err).slice(0, 200)); return; }
      const paths = [...new Set(String(stdout).split(/\r?\n/).map((s) => s.trim()).filter(Boolean))];
      if (paths.length) log(`старый пульт выключен (${paths.length}): ${paths.join(" · ")}`);
    });
}
