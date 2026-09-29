// Пульт аналитика: питон-сторона — дочерний процесс (pult/desk_backend.py), как у
// Hélène своя питон-часть рядом с окном. Живой Excel, контуры, токен устройства и
// все формулировки экранов остаются в проверенном коде пульта; окно только рисует.
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { app } from "electron";

let child: ChildProcess | null = null;
let port = 0;
let secret = "";
let ready: Promise<void> | null = null;
let lastError = "";

/** Корень комплекта (там, где лежит папка pult/). */
export function kitRoot(): string {
  if (process.env.HARD_ROOT) return process.env.HARD_ROOT;
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
      const args = exe === "py" ? ["-3", "-X", "utf8", "-m", "pult.desk_backend"] : ["-X", "utf8", "-m", "pult.desk_backend"];
      const p = spawn(exe, args, { cwd: root, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
      let buf = "";
      let err = "";
      let settled = false;
      p.on("error", () => { if (!settled) { settled = true; attempt(i + 1); } });
      p.stderr?.on("data", (d) => { err += String(d); if (err.length > 8000) err = err.slice(-8000); });
      p.stdout?.on("data", (d) => {
        buf += String(d);
        const m = /HARD-BACKEND (\d+) (\S+)/.exec(buf);
        if (m && !settled) {
          settled = true;
          port = Number(m[1]);
          secret = m[2];
          child = p;
          log(`питон-сторона пульта: порт ${port}, корень ${root}`);
          resolve();
        }
      });
      p.on("exit", (code) => {
        if (!settled) {
          settled = true;
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
  return ready;
}

export async function local(path: string, body: unknown = {}): Promise<{ ok: boolean; data?: unknown; error?: string }> {
  try {
    await startBackend(() => {});
  } catch (e) {
    return { ok: false, error: String((e as Error).message || e) };
  }
  try {
    const res = await fetch(`http://127.0.0.1:${port}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Local-Secret": secret },
      body: JSON.stringify(body ?? {}),
    });
    return (await res.json()) as { ok: boolean; data?: unknown; error?: string };
  } catch (e) {
    return { ok: false, error: "Питон-сторона пульта не отвечает: " + String((e as Error).message || e) };
  }
}

export function stopBackend(): void {
  try { child?.kill(); } catch { /* */ }
  child = null;
}

app.on("will-quit", stopBackend);
