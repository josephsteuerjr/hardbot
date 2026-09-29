// Снимки окна для проверки глазами (только разработка): HARD_SHOTS=<папка>.
// Проходит по разделам кликами по полке, как человек, и кладёт PNG окна целиком.
// HARD_SHOT_KEY — ссылка входа дев-стенда, если компьютер ещё не привязан.
import type { BrowserWindow } from "electron";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { enroll } from "./net";
import { readToken } from "./store";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function runShots(win: BrowserWindow, dir: string): Promise<void> {
  mkdirSync(dir, { recursive: true });
  const wc = win.webContents;
  const js = (code: string) => wc.executeJavaScript(code).catch((e) => console.log("js:", String(e).slice(0, 200)));
  const shot = async (name: string, wait = 900) => {
    await sleep(wait);
    const img = await wc.capturePage();
    writeFileSync(join(dir, name + ".png"), img.toPNG());
    console.log("снимок", name);
  };
  const key = process.env.HARD_SHOT_KEY || "";
  if (!readToken()) {
    await sleep(700);
    await shot("00-enroll", 300);
    if (key) {
      await js(`document.getElementById('link').value=${JSON.stringify(key)};document.getElementById('go').click()`);
      await sleep(2500);
    }
  }
  const only = (process.env.HARD_SHOT_ONLY || "").split(",").filter(Boolean);
  await sleep(1500);
  // Разделы — как их видит человек: по кнопкам полки.
  const ids = (await js(`[...document.querySelectorAll('#rail [data-go]')].map(b => b.dataset.go)`)) as string[] || [];
  const plan: [string, string][] = ids.map((id, i) => [id, `${String(i + 1).padStart(2, "0")}-${id}`]);
  for (const [id, name] of plan) {
    if (only.length && !only.includes(name)) continue;
    const ok = await js(`(() => { const b = document.querySelector('[data-go="${id}"]'); if (b) { b.click(); return true; } return false; })()`);
    if (!ok) continue;
    await shot(name, 2200);
    if (id === "activity") {
      await js(`document.querySelector('.ev.pick')?.click()`);
      await shot("01b-activity-person", 1800);
      await js(`document.getElementById('panel-close').click()`);
    }
    if (id === "team") {
      await js(`document.querySelector('tr[data-emp]')?.click()`);
      await shot("02b-team-employee", 2000);
    }
    if (id === "control") {
      await js(`document.querySelector('[data-tab="calls"]')?.click()`);
      await shot("04b-control-calls", 2400);
      await js(`document.querySelector('[data-tab="risk"]')?.click()`);
      await sleep(1500);
      await js(`document.querySelector('tr[data-emp]')?.click()`);
      await shot("04c-control-flags", 2200);
    }
  }
}
