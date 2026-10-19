// Пульт аналитика: питон-сторона — дочерний процесс (pult/desk_backend.py), как у
// Hélène своя питон-часть рядом с окном. Живой Excel, контуры, токен устройства и
// все формулировки экранов остаются в проверенном коде пульта; окно только рисует.
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { app } from "electron";
import { fetchBuffered, localPath, retryLocalRead } from "./transport";

let child: ChildProcess | null = null;
let port = 0;
let secret = "";
let ready: Promise<void> | null = null;
let lastError = "";

/** Корень комплекта (там, где лежит папка pult/). */
export function kitRoot(): string {
  if (process.env.HARD_ROOT) return process.env.HARD_ROOT;
  if (app.isPackaged && existsSync(join(process.resourcesPath, "backend", "hard-backend.exe"))) return join(process.resourcesPath, "backend");
  const starts = [dirname(process.execPath), join(__dirname, "..", ".."), process.cwd()];
  for (const s of starts) {
    let d = s;
    for (let i = 0; i < 4; i++) {
      if (existsSync(join(d, "pult", "desk_backend.py"))) return d;
      d = dirname(d);
    }
  }
  return join(__dirname, "..", "..");
}

function python(): string[] {
  const bundled = join(process.resourcesPath, "backend", "hard-backend.exe");
  if (app.isPackaged && existsSync(bundled)) return [bundled];
  if (process.env.HARD_PYTHON) return [process.env.HARD_PYTHON];
  return process.platform === "win32" ? ["python", "py"] : ["python3"];
}

export function startBackend(log: (s: string) => void): Promise<void> {
  if (ready) return ready;
  ready = new Promise((resolve, reject) => {
    const root = kitRoot();
    const tries = python();
    const attempt = (i: number) => {
      if (i >= tries.length) {
        lastError = "Не найден Python. Пульту аналитика нужен Python 3.12+ (python.org, галочка «Add to PATH»).";
        reject(new Error(lastError));
        return;
      }
      const exe = tries[i];
      const bundled = exe.endsWith("hard-backend.exe");
      const args = bundled ? [] : exe === "py" ? ["-3", "-X", "utf8", "-m", "pult.desk_backend"] : ["-X", "utf8", "-m", "pult.desk_backend"];
      const p = spawn(exe, args, { cwd: root, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8",
        ...(bundled ? { HARD_PULT_SETTINGS: join(app.getPath("userData"), "backend-settings.json"), HARD_PULT_PROTECT_TOKENS: "1" } : {}) } });
      child = p; // quitting during startup must not leave a mail/backend process behind
      let buf = "";
      let err = "";
      let settled = false;
      const startupTimer = setTimeout(() => {
        if (settled) return;
        settled = true;
        p.kill();
        if (child === p) child = null;
        lastError = "Питон-сторона не запустилась за 45 секунд. Проверьте её журнал и повторите подключение.";
        reject(new Error(lastError));
      }, 45000);
      startupTimer.unref();
      p.on("error", () => { if (!settled) { settled = true; clearTimeout(startupTimer); if (child === p) child = null; attempt(i + 1); } });
      p.stderr?.on("data", (d) => { err += String(d); if (err.length > 8000) err = err.slice(-8000); });
      p.stdout?.on("data", (d) => {
        buf += String(d);
        const m = /HARD-BACKEND (\d+) (\S+)/.exec(buf);
        if (m && !settled) {
          settled = true;
          clearTimeout(startupTimer);
          port = Number(m[1]);
          secret = m[2];
          child = p;
          log(`питон-сторона пульта: порт ${port}, корень ${root}`);
          resolve();
        }
      });
      p.on("exit", (code) => {
        clearTimeout(startupTimer);
        if (!settled) {
          settled = true;
          if (child === p) child = null;
          lastError = `Питон-сторона пульта не запустилась (код ${code}).\n${err.trim().split("\n").slice(-6).join("\n")}`;
          reject(new Error(lastError));
        } else if (child === p) {
          log(`питон-сторона пульта завершилась (код ${code}): ${err.slice(-400)}`);
          child = null;
          ready = null;
        }
      });
    };
    attempt(0);
  });
  ready = ready.catch(error => { ready = null; throw error; });
  return ready;
}

export async function local(path: string, body: unknown = {}): Promise<{ ok: boolean; data?: unknown; error?: string }> {
  if (!localPath(path)) return { ok: false, error: "Недопустимый путь локального запроса" };
  try {
    await startBackend(() => {});
  } catch (e) {
    return { ok: false, error: String((e as Error).message || e) };
  }
  let last: unknown = null;
  const attempts = retryLocalRead(path, body) ? 3 : 1;
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetchBuffered(fetch, `http://127.0.0.1:${port}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Local-Secret": secret },
        body: JSON.stringify(body ?? {}),
      }, path === "/admin" || path === '/account/root_bootstrap' ? 300000 : 30000);
      return (await res.json()) as { ok: boolean; data?: unknown; error?: string };
    } catch (e) {
      last = e;
      if (i + 1 < attempts) await new Promise((r) => setTimeout(r, 120 * (i + 1)));
    }
  }
  return { ok: false, error: attempts === 1
    ? "Ответ на операцию потерян. Она могла выполниться; проверьте результат перед повтором. " + String((last as Error)?.message || last)
    : "Питон-сторона пульта не отвечает: " + String((last as Error)?.message || last) };
}

export function stopBackend(): void {
  try { child?.kill(); } catch { /* */ }
  child = null;
  ready = null;
  port = 0;
  secret = "";
}

app.on("will-quit", stopBackend);
