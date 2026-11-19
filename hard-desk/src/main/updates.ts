import { app, net } from "electron";
import { NsisUpdater } from "electron-updater";
import { createHash } from "node:crypto";
import { createReadStream, statSync, unlinkSync } from "node:fs";
import { PRODUCT_ID, RELEASE_URL } from "./product";
import publicKey from "./release-public-key.json";
import { matchUpdate, verifyRelease, type SignedRelease } from "./update-policy";

export interface UpdateState { phase: "disabled" | "idle" | "checking" | "downloading" | "ready" | "error"; version?: string; percent?: number; text: string }
const currentVersion = "У вас установлена последняя версия приложения.";
let state: UpdateState = { phase: "disabled", text: "Обновления доступны в установленной программе" };
let updater: NsisUpdater | null = null;
let signed: SignedRelease | null = null;
let downloaded = "";
let busy = false;
let installing = false;
let lastCheckAt = 0;
let notify = (_s: UpdateState) => {};
function tell(s: UpdateState) { state = s; notify({ ...s }); }
export function updateState(): UpdateState { return { ...state }; }

export function startUpdates(onState: (s: UpdateState) => void, log: (s: string) => void): void {
  notify = onState;
  if (!app.isPackaged || !PRODUCT_ID || PRODUCT_ID === "crm") return;
  updater = new NsisUpdater({ provider: "generic", url: RELEASE_URL, channel: PRODUCT_ID, useMultipleRangeRequest: false });
  updater.autoDownload = false;
  updater.autoInstallOnAppQuit = false;
  updater.allowDowngrade = false;
  updater.allowPrerelease = false;
  updater.disableDifferentialDownload = true;
  updater.logger = { info: () => {}, warn: () => {}, error: () => {}, debug: () => {} };
  updater.on("error", () => tell({ phase: "error", text: "Не удалось проверить или загрузить обновление. Текущая версия продолжает работать; повторите позже." }));
  updater.on("download-progress", p => tell({ phase: "downloading", version: signed?.version, percent: Math.round(p.percent), text: "Загрузка обновления" }));
  updater.on("update-downloaded", e => {
    downloaded = e.downloadedFile;
    tell({ phase: "ready", version: e.version, text: "Обновление скачано. Перезагрузите приложение, чтобы установить его." });
  });
  tell({ phase: "idle", text: "Обновления проверяются автоматически" });
  const check = () => {
    if (Date.now() - lastCheckAt >= 60 * 1000) {
      void checkUpdates().catch(() => log("Обновление: проверка не удалась; текущая версия сохранена"));
    }
  };
  setTimeout(check, 30 * 1000).unref();
  setInterval(check, 15 * 60 * 1000).unref();
  app.on("browser-window-focus", check);
}

export async function checkUpdates(): Promise<UpdateState> {
  if (!updater || !PRODUCT_ID || busy || state.phase === "ready") return updateState();
  busy = true;
  lastCheckAt = Date.now();
  try {
    tell({ phase: "checking", text: "Проверка новой версии" });
    const res = await net.fetch(RELEASE_URL + PRODUCT_ID + "-signed.json", { signal: AbortSignal.timeout(20000) });
    if (res.status === 404) { tell({ phase: "idle", text: "Доступных обновлений нет." }); return updateState(); }
    if (!res.ok) throw new Error("Release feed unavailable");
    signed = verifyRelease(await res.json(), publicKey.pem, PRODUCT_ID);
    const r = await updater.checkForUpdates();
    if (!r) throw new Error("Update metadata unavailable");
    matchUpdate(r.updateInfo, signed);
    // Сравнение версий остаётся у updater; signed metadata не разрешает откат.
    if (!r.isUpdateAvailable) { tell({ phase: "idle", text: currentVersion }); return updateState(); }
    tell({ phase: "downloading", version: signed.version, percent: 0, text: "Загрузка обновления" });
    await updater.downloadUpdate();
  } catch {
    tell({ phase: "error", text: "Обновление не загрузилось или не прошло проверку подписи. Текущая версия сохранена; повторите позже." });
  } finally { busy = false; }
  return updateState();
}

export async function installUpdate(canClose: () => Promise<boolean>, beforeInstall: () => void): Promise<{ ok: boolean; text?: string }> {
  if (!updater || state.phase !== "ready" || !signed || !downloaded || installing) return { ok: false, text: "Обновление ещё не готово" };
  installing = true;
  try {
    // canClose может БРОСИТЬ (повреждённая очередь/черновик): это «не сейчас»,
    // а не повод удалять уже скачанный установщик (раньше исключение уходило
    // в общий catch и unlinkSync съедал готовое обновление).
    let busyWork = false;
    try { busyWork = !(await canClose()); } catch { busyWork = true; }
    if (busyWork) return { ok: false, text: "Не удалось проверить незавершённые операции (возможно, повреждена локальная очередь). Повторите после проверки очереди; скачанное обновление сохранено." };
    if (statSync(downloaded).size !== signed.size) throw new Error("Size mismatch");
    const hash = createHash("sha512");
    for await (const chunk of createReadStream(downloaded)) hash.update(chunk);
    if (hash.digest("base64") !== signed.sha512) throw new Error("Checksum mismatch");
    beforeInstall();
    updater.quitAndInstall(true, true);
    if (updateState().phase === "error") return { ok: false, text: updateState().text };
    return { ok: true };
  } catch {
    // electron-updater reuses an in-memory download without hashing it again.
    // Remove a rejected payload so the next check cannot offer that file forever.
    if (downloaded) { try { unlinkSync(downloaded); } catch {} }
    downloaded = "";
    tell({ phase: "error", text: "Установку не удалось начать. Повторите проверку обновления; ваши данные сохранены." });
    return { ok: false, text: state.text };
  } finally { installing = false; }
}
