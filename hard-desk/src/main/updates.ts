import { app, net } from "electron";
import { NsisUpdater } from "electron-updater";
import { createHash } from "node:crypto";
import { createReadStream, statSync, unlinkSync } from "node:fs";
import { PRODUCT_ID, RELEASE_URL } from "./product";
import publicKey from "./release-public-key.json";
import { matchUpdate, verifyRelease, type SignedRelease } from "./update-policy";

export interface UpdateState {
  phase: "disabled" | "idle" | "checking" | "downloading" | "ready" | "error";
  version?: string;
  transferred?: number;
  total?: number;
  text: string;
}
const currentVersion = "У вас установлена последняя версия приложения.";
const downloadIdleMs = 90_000;
let state: UpdateState = { phase: "disabled", text: "Обновления доступны в установленной программе" };
let updater: NsisUpdater | null = null;
let signed: SignedRelease | null = null;
let downloaded = "";
let busy = false;
let installing = false;
let lastCheckAt = 0;
let notify = (_s: UpdateState) => {};
let downloadAdvanced = (_bytes: number) => {};
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
  updater.on("error", () => {
    if (!busy) tell({ phase: "error", text: "Не удалось запустить установщик. Нажмите ↻, чтобы повторить." });
  });
  updater.on("download-progress", p => {
    downloadAdvanced(p.transferred);
    tell({ phase: "downloading", version: signed?.version, transferred: p.transferred, total: signed?.size, text: "Загрузка обновления" });
  });
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
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stalled = false;
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
    const cancellation = r.cancellationToken;
    if (!cancellation) throw new Error("Download token unavailable");
    tell({ phase: "downloading", version: signed.version, transferred: 0, total: signed.size, text: "Загрузка обновления" });
    let received = -1;
    downloadAdvanced = bytes => {
      if (bytes <= received) return;
      received = bytes;
      clearTimeout(timer);
      timer = setTimeout(() => {
        stalled = true;
        cancellation.cancel();
      }, downloadIdleMs);
      timer.unref();
    };
    downloadAdvanced(0);
    await updater.downloadUpdate(cancellation);
  } catch {
    const text = stalled ? "За 90 секунд не поступило новых данных. Нажмите ↻, чтобы повторить загрузку."
      : state.phase === "downloading" ? "Не удалось скачать обновление. Нажмите ↻, чтобы повторить."
        : "Не удалось проверить обновление. Нажмите ↻, чтобы повторить.";
    tell({ phase: "error", text });
  } finally {
    clearTimeout(timer);
    downloadAdvanced = () => {};
    busy = false;
  }
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
