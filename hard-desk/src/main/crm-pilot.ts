import { app, BrowserWindow, dialog, ipcMain, net, protocol } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, extname, join, resolve, sep } from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { pathToFileURL } from "node:url";
import { accountLogin, accountManagerLogin } from "./net";
import {
  readFieldToken,
  readToken,
  readSettings,
  writeSettings,
} from "./store";
import { fetchBuffered } from "./transport";
import * as queue from "./crm-queue";
import { PILOT_BASE, existingManagerStatus, existingLogins, ENTRY_SOURCES, EntrySource } from "./crm-manager-login";
import { pilotCredential } from "./crm-pilot-session";
import {ThreeHourReports} from './crm-report-scheduler';

const isolatedProfile = !!process.env.HARD_USER_DATA;
let connectionNotice = "";
const data =
  process.env.HARD_USER_DATA ||
  join(
    process.env.LOCALAPPDATA || app.getPath("appData"),
    "Hard",
    "crm-pilot",
    "data",
  );
mkdirSync(data, { recursive: true });
app.setName("Взыскание — CRM пилот");
app.setPath("userData", data);
app.setAppUserModelId("ru.rimborso.crm.pilot");
process.env.HARD_USER_DATA = data; // Separate installation identity; never inherit stable profile.
protocol.registerSchemesAsPrivileged([
  {
    scheme: "hard",
    privileges: { standard: true, secure: true, supportFetchAPI: true },
  },
]);
if (!app.requestSingleInstanceLock()) app.quit();
let win: BrowserWindow | null = null,
  backend: ChildProcessWithoutNullStreams | null = null,
  port = 0,
  secret = "",
  offline = false;
let currentContext: any = null,
  owner = "",
  clock: any = null;
const sessionId = randomUUID();
const config = () =>
  readSettings().crmConnection as
    | { mode?: string; role?: string; server?: string; auth?: string; source?: EntrySource }
    | undefined;
const shared = () => ['existing-manager', 'existing-login'].includes(config()?.auth || '') && config()?.server === PILOT_BASE;
const entrySource = (): EntrySource => config()?.source || 'manager';
const demo = () => config()?.mode === "synthetic";
const role = () => config()?.role || "employee";
const origin = () => (demo() ? "synthetic-local" : config()?.server || "");
function notifyChanged(type: 'queue' | 'identity') {
  if (win && !win.isDestroyed() && !win.webContents.isDestroyed())
    win.webContents.send('crm:changed', {type});
}
const stopQueueChanges = queue.onChange(authority => {
  if (authority === owner) notifyChanged('queue');
});
app.once('will-quit', stopQueueChanges);
function forgetAuthority(message: string) {
  owner = ""; currentContext = null; clock = null;
  connectionNotice = message;
  // A known revocation must not become an offline login after a later outage.
  // Owner-scoped pending operations remain protected in their original files.
  writeSettings({crmLast: undefined});
  notifyChanged('identity');
}

async function backendStart() {
  const home = join(data, "synthetic");
  mkdirSync(home, { recursive: true });
  const exe =
    process.env.HARD_QA_BACKEND ||
    join(process.resourcesPath, "backend", "hard-backend.exe");
  const source = process.env.HARD_CRM_SOURCE;
  const args = source
    ? ["-X", "utf8", "-m", "pult.desk_backend", "--crm-pilot"]
    : ["--crm-pilot"];
  backend = spawn(
    source ? process.env.HARD_CRM_PYTHON || "python" : exe,
    args,
    {
      cwd: source || dirname(exe),
      windowsHide: true,
      env: {
        ...process.env,
        APP_DATA: home,
        HARD_CRM_DEMO_HOME: home,
        HARD_CRM_SHARED: "0",
        HARD_CRM_DATABASE_URL: "",
        HARD_CRM_DATABASE_URL_FILE: "",
        HARD_CRM_DOCUMENTS: "",
        HARD_CRM_ELECTRON_PID: String(process.pid),
        PYTHONIOENCODING: "utf-8",
        HARD_PULT_SETTINGS: join(home, "unused-settings.json"),
      },
    },
  );
  backend.stderr.on("data", (chunk: Buffer) => {
    writeFileSync(join(data, "crm-backend.log"), chunk, { flag: "a" });
  });
  await new Promise<void>((resolveReady, reject) => {
    let output = "";
    const timeout = setTimeout(
      () => reject(new Error("Локальная проверочная база не запустилась.")),
      45000,
    );
    backend!.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString();
      const match = output.match(/HARD-CRM (\d+) (\S+)/);
      if (match) {
        port = Number(match[1]);
        secret = match[2];
        clearTimeout(timeout);
        resolveReady();
      }
    });
    backend!.once("exit", () => {
      port = 0;
      secret = "";
      clearTimeout(timeout);
      reject(new Error("Проверочная база завершилась."));
    });
  });
}

async function local(
  action: string,
  body: any,
  query: any = {},
  method = "POST",
): Promise<queue.Result> {
  if (!port)
    return {
      ok: false,
      status: 0,
      kind: "offline",
      text: "Проверочная база недоступна.",
    };
  try {
    const response = await fetchBuffered(
      (url, init) => net.fetch(url, init),
      "http://127.0.0.1:" + port + "/crm/" + action,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Local-Secret": secret,
          "X-Demo-Role": role(),
          "X-Client-Version": app.getVersion(),
        },
        body: JSON.stringify({ method, body, query }),
      },
      action.startsWith("workbook-") ? 600000 : 120000,
    );
    const value = await response.json();
    return {
      ok: response.ok,
      status: response.status,
      data: value,
      text: value.text,
    };
  } catch {
    return {
      ok: false,
      status: 0,
      kind: "offline",
      text: "Нет ответа проверочной базы.",
    };
  }
}

const actions = new Set([
  "context",
  "portfolio",
  "work-portfolio",
  'tasks',
  "card",
  'contracts',
  "shifts",
  'shift-report',
  'cabinet',
  'three-hour',
  "script",
  "anchor",
  "command",
  "shift",
  "plan",
  "cohort",
  "adoption",
  "export",
  "import-preview",
  "import-apply",
  "receipt",
  "telemetry",
  "document",
]);
const send: queue.Transport = async (method, action, body, query) => {
  if (!actions.has(action) || !["GET", "POST"].includes(method))
    return { ok: false, status: 400, text: "Неизвестный запрос." };
  if (!["synthetic", "remote"].includes(config()?.mode || ""))
    return {ok: false, status: 401, text: "Выберите сохранённый вход для подключения к CRM."};
  if (offline)
    return {
      ok: false,
      status: 0,
      kind: "offline",
      text: "Проверка офлайн: отправка приостановлена.",
    };
  if (demo()) return local(action, body, query, method);
  const base = origin();
  try {
    const previous = readSettings().crmLast as any;
    const expected = currentContext?.employee_uuid || (previous?.origin === base ? previous.context?.employee_uuid : undefined);
    let token = shared() ? await pilotCredential(expected, false, entrySource()) : role() === "employee" ? readFieldToken() : readToken();
    if (!base || !token) return {ok: false, status: 401, text: "Войдите в пилотный сервер."};
    const url = new URL(shared() ? PILOT_BASE + "/api/crm/" + action : "/api/crm/" + action, base);
    for (const [key, value] of Object.entries(query || {})) if (value != null) url.searchParams.set(key, String(value));
    const request = (credential: string) => fetchBuffered((target, init) => net.fetch(target, init), url.toString(), {
      method,
      headers: {
        ...(shared() ? {Authorization: "Bearer " + credential} : {[role() === "employee" ? "X-App-Token" : "X-Pult-Token"]: credential}),
        "Content-Type": "application/json", "X-Client-Version": app.getVersion(), "X-Work-Product": "hard",
      },
      body: method === "POST" ? JSON.stringify(body || {}) : undefined,
    }, ["export", "import-preview", "import-apply"].includes(action) ? 120000 : 30000);
    let response = await request(token), value = await response.json();
    if (shared() && response.status === 401 && value.error === "pilot_session_expired") {
      token = await pilotCredential(expected, true, entrySource());
      response = await request(token); value = await response.json();
    }
    if (shared() && (response.status === 401 || (response.status === 403 &&
        ["manager_required", "identity_unmapped", "pilot_session_invalid"].includes(value.error))))
      forgetAuthority(value.text || "Сервер отозвал доступ к пилоту.");
    return {ok: response.ok, status: response.status, data: value, text: value.text || value.error};
  } catch (error) {
    const status = Number((error as any)?.status || 0);
    if (shared() && (status === 401 || status === 403)) forgetAuthority(String((error as Error).message));
    return {ok: false, status, kind: status ? "auth" : "offline", text: status ? String((error as Error).message) : "Нет подтверждённой связи с сервером CRM. Проверьте сохранённый вход и подключение к сети."};
  }
};

async function identify() {
  const result = await send("GET", "context");
  if (result.ok) {
    currentContext = result.data;
    owner = origin() + "\n" + currentContext.employee_uuid;
    queue.cache(owner, "context", currentContext);
    writeSettings({
      crmLast: {
        origin: origin(),
        role: role(),
        owner,
        context: currentContext,
      },
    });
    const anchor = await send("POST", "anchor", {
      installation_id: queue.installation(),
      reports: owner ? (()=>{const job=queue.draft(owner,'three-hour-report')||{};return {slot:job.slot,path:job.path,error:job.error,receipt:job.receipt};})() : null,
    });
    clock = anchor.ok ? { ...anchor.data, monotonic: performance.now() } : null;
  } else if (result.status === 401 || result.status === 403) {
    forgetAuthority(result.text || "Сервер не подтвердил доступ.");
  } else {
    const previous = readSettings().crmLast as any;
    if (previous?.origin === origin() && previous?.role === role()) {
      owner = previous.owner;
      currentContext = previous.context;
    }
  }
  return result;
}

// An export intent is protected before touching Excel. After a crash, re-read
// the same versioned file; only exact snapshot values authorize a receipt.
let exportRecovery = false;
async function recoverExports() {
  if (exportRecovery || !owner) return;
  exportRecovery = true;
  const authority = owner;
  try {
    const jobs = queue.draft(authority, "exports") || [];
    for (const job of jobs.filter((v: any) => !v.completed)) {
      if (owner !== authority) return;
      if (!existsSync(job.path)) {
        job.error = "Файл выгрузки не найден. Исходный запрос сохранён.";
        continue;
      }
      const result = await local("workbook-resume", {
        snapshot: job.snapshot,
        path: job.path,
      });
      if (!result.ok) {
        job.error = result.text;
        continue;
      }
      const cells = job.snapshot.rows.flatMap((row: any) =>
        Object.entries(row.provenance?._fields || {}).map(
          ([key, source]: any) => ({
            contract_uuid: row.contract_uuid,
            key,
            value: row.values[key],
            event_id: source.event_id,
          }),
        ),
      );
      if (cells.length)
        queue.enqueue(
          authority,
          {
            receipt_id: job.receipt_id,
            export_id: job.snapshot.manifest.export_id,
            manifest_hash: job.snapshot.manifest.manifest_hash,
            target_id: job.snapshot.manifest.target_id,
            file_sha256: result.data.sha256,
            cells,
          },
          "receipt",
        );
      job.completed = true;
      job.sha256 = result.data.sha256;
      // The exact receipt is already durably queued. Retain its manifest and
      // proof, without accumulating full completed registry copies locally.
      job.manifest = job.snapshot.manifest;
      delete job.snapshot;
      delete job.error;
      queue.draft(authority, "exports", jobs);
    }
    queue.draft(authority, "exports", jobs);
  } finally {
    exportRecovery = false;
  }
}

function safeHandler(name: string, fn: (...args: any[]) => any) {
  ipcMain.handle(name, async (event, ...args) => {
    if (
      event.sender !== win?.webContents ||
      !event.senderFrame?.url.startsWith("hard://app/")
    )
      throw new Error("Недоверенное окно.");
    try {
      return await fn(...args);
    } catch (error) {
      return {
        ok: false,
        status: 0,
        text: String((error as Error).message || error),
      };
    }
  });
}

app
  .whenReady()
  .then(async () => {
    const ui = resolve(__dirname, "ui");
    protocol.handle("hard", (request) => {
      const url = new URL(request.url),
        path = resolve(ui, decodeURIComponent(url.pathname).replace(/^\//, ""));
      if (url.hostname !== "app" || !path.startsWith(ui + sep))
        return new Response("Forbidden", { status: 403 });
      return net.fetch(pathToFileURL(path).toString());
    });
    await backendStart();
    const availableLogins = existingLogins().filter(entry => entry.available);
    if (!config() && app.isPackaged && !isolatedProfile && availableLogins.length === 1) {
      try {
        const source = availableLogins[0].source;
        await pilotCredential(undefined, false, source);
        writeSettings({crmConnection: {mode: "remote", role: source === 'manager' || source === 'analyst' ? source : 'employee', source,
          server: PILOT_BASE, auth: "existing-login"}});
      } catch { connectionNotice = "CRM пока недоступна. Повторите подключение через сохранённый вход."; }
    }
    await identify();
    win = new BrowserWindow({
      width: 1320,
      height: 880,
      minWidth: 960,
      minHeight: 640,
      frame: false,
      thickFrame: false,
      backgroundColor: "#f8eee0",
      show: false,
      autoHideMenuBar: true,
      webPreferences: {
        preload: join(__dirname, "preload.js"),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
      },
    });
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.webContents.on("will-navigate", (event, url) => {
      if (!url.startsWith("hard://app/")) event.preventDefault();
    });
    safeHandler("crm:state", () => ({
      ok: true,
      version: app.getVersion(),
      connection: {
        mode: demo() ? "synthetic" : config()?.mode === "remote" ? "remote" : "unconfigured",
        role: role(),
        server: origin(),
        shared: shared(),
      },
      context: currentContext,
      existingManager: existingManagerStatus(),
      existingLogins: existingLogins(),
      connectionNotice,
      offline,
      owner,
      installation_id: queue.installation(),
    }));
    safeHandler(
      "crm:api",
      async (method: string, action: string, body: any, query: any) => {
        if (action === "command" || action === "receipt")
          throw new Error("Используйте защищённую очередь.");
        const authority = owner,
          result = await send(method, action, body, query);
        if (authority !== owner && result.ok)
          return { ok: false, status: 409, text: "Учётная запись изменилась." };
        const key = action + JSON.stringify(query || {});
        if (result.ok && method === "GET" && owner)
          queue.cache(owner, key, result.data);
        else if (method === "GET" && result.status === 0 && owner) {
          const cached = queue.cache(owner, key);
          if (cached)
            return {
              ok: true,
              status: 200,
              data: cached.data,
              stale: cached.at,
            };
        }
        return result;
      },
    );
    safeHandler("crm:command", async (body: any) => {
      if (!owner || !currentContext)
        throw new Error("Сначала подтвердите вход в CRM.");
      const authority = owner;
      const previous = queue
        .queueState(owner)
        .entries.find((e) => e.body.operation_id === body?.operation_id);
      if (previous) {
        const input = { ...previous.body };
        if (!body.clock && !body.occurred_at) {
          delete input.clock;
          delete input.occurred_at;
        }
        if (JSON.stringify(input) !== JSON.stringify(body))
          throw new Error("ID операции уже сохранён с другим содержимым.");
        void queue.flush(authority, send, () => owner);
        return {
          ok: true,
          queued: previous.state !== "accepted",
          operation_id: body.operation_id,
          result: previous.result,
        };
      }
      // Monotonic session anchor is trustworthy only in this running process.
      if (!body.clock && !body.occurred_at) {
        if (clock)
          body = {
            ...body,
            clock: {
              anchor_id: clock.anchor_id,
              installation_id: clock.installation_id,
              elapsed_ms: Math.max(
                0,
                Math.round(performance.now() - clock.monotonic),
              ),
            },
          };
        else body = { ...body, occurred_at: new Date().toISOString() };
      }
      queue.enqueue(authority, body);
      void queue.flush(authority, send, () => owner);
      return { ok: true, queued: true, operation_id: body.operation_id };
    });
    safeHandler("crm:queue", async (action: string, id: string) => {
      if (!owner) throw new Error("Нет входа.");
      if (action === "retry") queue.retry(owner, id);
      if (action === "flush") await queue.flush(owner, send, () => owner);
      return {
        ok: true,
        data: {
          ...queue.queueState(owner),
          exports: queue.draft(owner, "exports") || [],
        },
      };
    });
    safeHandler("crm:draft", (key: string, value: any) => {
      if (!owner || typeof key !== "string" || key.length > 200)
        throw new Error("Нет контекста черновика.");
      return { ok: true, data: queue.draft(owner, key, value) };
    });
    safeHandler("crm:telemetry", (event: any) => {
      if (!owner) return { ok: false };
      const allowed = new Set([
        "event_type",
        "feature",
        "operation_id",
        "error_code",
        "duration_ms",
      ]);
      if (!event || Object.keys(event).some((k) => !allowed.has(k)))
        throw new Error("Недопустимая диагностика.");
      queue.technical(owner, {
        ...event,
        schema_version: 1,
        event_id: randomUUID(),
        producer: "desktop",
        installation_id: queue.installation(),
        session_id: sessionId,
        client_version: app.getVersion(),
        occurred_at: new Date().toISOString(),
      });
      return { ok: true };
    });
    safeHandler("crm:connect", async (input: any) => {
      if (input?.action === "offline") {
        offline = !!input.value;
        return { ok: true };
      }
      if (input?.action === "existing-manager" || input?.action === 'existing-login') {
        const source: EntrySource = input.action === 'existing-manager' ? 'manager' : input.source;
        if (!ENTRY_SOURCES.includes(source)) throw new Error('Выберите сохранённый вход.');
        await pilotCredential(undefined, true, source);
        writeSettings({crmConnection: {mode: "remote", role: source === 'manager' || source === 'analyst' ? source : 'employee', source,
          server: PILOT_BASE, auth: "existing-login"}, crmLast: undefined});
        connectionNotice = "";
      } else if (input?.mode === "synthetic") {
        if (!["employee", "manager", "observer","courier","analyst"].includes(input.role))
          throw new Error("Неизвестная роль.");
        writeSettings({
          crmConnection: { mode: "synthetic", role: input.role },
        });
      } else {
        const url = new URL(input?.server);
        if (
          url.protocol !== "https:" ||
          url.username ||
          url.password ||
          url.pathname !== "/" ||
          url.search ||
          url.hash
        )
          throw new Error("Укажите HTTPS-адрес отдельного пилотного сервера.");
        if (
          url.hostname === "hard.rimborso.ru" &&
          (!url.port || url.port === "443")
        )
          throw new Error(
            "Рабочий сервер не подключён к пилоту. Укажите отдельный согласованный адрес.",
          );
        const login =
          input.role === "employee"
            ? await accountLogin(url.origin, input.username, input.password)
            : await accountManagerLogin(
                url.origin,
                input.username,
                input.password,
              );
        if (!login.ok) return login;
        writeSettings({
          crmConnection: {
            mode: "remote",
            role: input.role,
            server: url.origin,
          },
        });
      }
      owner = "";
      currentContext = null;
      clock = null;
      offline = false;
      return identify();
    });
    safeHandler("crm:workbook", async (action: string, input: any) => {
      if(action==='shift-report'){
        const authority=owner;
        if(!authority)throw new Error('Сначала подтвердите вход.');
        const report=await send('GET','shift-report',undefined,{since:input?.report?.since,until:input?.report?.until});
        if(!report.ok)return report;
        const choice=await dialog.showSaveDialog(win!,{title:'Отчёт по сменам',defaultPath:`Смены ${report.data.since} — ${report.data.until}.xlsx`,filters:[{name:'Excel',extensions:['xlsx']}]});
        if(choice.canceled||!choice.filePath)return {ok:false,cancelled:true};
        if(owner!==authority)throw new Error('Вход изменён. Повторите выгрузку.');
        return local('report-write',{report:report.data,kind:'shifts',path:choice.filePath});
      }
      if (action === "read") {
        const choice = await dialog.showOpenDialog(win!, {
          properties: ["openFile"],
          filters: [{ name: "Реестры", extensions: ["xlsx", "xlsm", "xlsb"] }],
        });
        if (choice.canceled) return { ok: false, cancelled: true };
        const result = await local("workbook-read", {
          path: choice.filePaths[0],
        });
        if (result.ok) result.data.filename = basename(choice.filePaths[0]);
        return result;
      }
      if (action !== "write" || !input?.snapshot)
        throw new Error("Нет подтверждённой выгрузки.");
      let template: string | undefined;
      if (input.use_template) {
        const choice = await dialog.showOpenDialog(win!, {
          properties: ["openFile"],
          title: "Исходная книга для копии",
          filters: [{ name: "Реестры", extensions: ["xlsx", "xlsm", "xlsb"] }],
        });
        if (choice.canceled) return { ok: false, cancelled: true };
        template = choice.filePaths[0];
      }
      const extension = template ? extname(template) : ".xlsx";
      const choice = await dialog.showSaveDialog(win!, {
        title: "Новая версионированная копия",
        defaultPath: "Реестр CRM " + input.snapshot.manifest.period + extension,
        filters: [{ name: "Реестр", extensions: [extension.slice(1)] }],
      });
      if (choice.canceled || !choice.filePath)
        return { ok: false, cancelled: true };
      if (!owner) throw new Error("Сначала подтвердите вход.");
      const authority = owner,
        jobs = queue.draft(authority, "exports") || [];
      if (existsSync(choice.filePath))
        throw new Error("Выберите новое имя: существующий файл сохраняется.");
      const job = {
        path: choice.filePath,
        snapshot: input.snapshot,
        receipt_id: randomUUID(),
        completed: false,
      };
      jobs.push(job);
      queue.draft(authority, "exports", jobs);
      const result = await local("workbook-write", {
        snapshot: input.snapshot,
        path: choice.filePath,
        template,
      });
      if (owner === authority) {
        await recoverExports();
        void queue.flush(authority, send, () => owner);
      }
      return result;
    });
    safeHandler(
      "crm:document",
      async (contract: string, documentId?: string) => {
        if (documentId) {
          let bytes: Buffer, filename: string;
          if (demo()) {
            const result = await local(
              "document-read",
              null,
              { document_id: documentId },
              "GET",
            );
            if (!result.ok) return result;
            bytes = Buffer.from(result.data.content_base64, "base64");
            filename = basename(result.data.filename);
            if (
              createHash("sha256").update(bytes).digest("hex") !==
              result.data.sha256
            )
              throw new Error("Не совпадает контрольная сумма документа.");
          } else {
            if (offline) throw new Error("Для загрузки документа нужна связь.");
            const token = shared() ? await pilotCredential(currentContext?.employee_uuid) :
              role() === "employee" ? readFieldToken() : readToken();
            const response = await fetchBuffered(
              (url, init) => net.fetch(url, init),
              origin() +
                "/api/crm/document?document_id=" +
                encodeURIComponent(documentId),
              {
                headers: {
                  ...(shared() ? {Authorization: "Bearer " + token} : {[role() === "employee" ? "X-App-Token" : "X-Pult-Token"]: token || ""}),
                },
              },
              30000,
            );
            if (!response.ok)
              throw new Error("Документ недоступен для этой учётной записи.");
            bytes = Buffer.from(await response.arrayBuffer());
            filename = basename(contract);
          }
          if (bytes.length > 10 * 1024 * 1024)
            throw new Error("Документ больше 10 МБ.");
          const choice = await dialog.showSaveDialog(win!, {
            defaultPath: filename,
          });
          if (choice.canceled || !choice.filePath)
            return { ok: false, cancelled: true };
          writeFileSync(choice.filePath, bytes, { flag: "wx", flush: true });
          return { ok: true };
        }
        const choice = await dialog.showOpenDialog(win!, {
          properties: ["openFile"],
          filters: [
            {
              name: "Документы",
              extensions: ["pdf", "png", "jpg", "jpeg", "docx"],
            },
          ],
        });
        if (choice.canceled) return { ok: false, cancelled: true };
        const path = choice.filePaths[0],
          bytes = readFileSync(path);
        if (bytes.length > 10 * 1024 * 1024)
          throw new Error("Документ больше 10 МБ.");
        const types: Record<string, string> = {
          ".pdf": "application/pdf",
          ".png": "image/png",
          ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg",
          ".docx":
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        };
        return send("POST", "document", {
          document_id: randomUUID(),
          contract_uuid: contract,
          filename: basename(path),
          media_type: types[extname(path).toLowerCase()],
          content_base64: bytes.toString("base64"),
        });
      },
    );
    ipcMain.on("crm:win", (event, action) => {
      if (event.sender !== win?.webContents) return;
      if (action === "close") win?.close();
      else if (action === "minimize") win?.minimize();
      else if (action === "maximize")
        win?.isMaximized() ? win.unmaximize() : win?.maximize();
    });
    await win.loadURL("hard://app/index.html");
    if (process.platform === "win32")
      await local("window-frame", {
        hwnd: win.getNativeWindowHandle().readBigUInt64LE().toString(),
      });
    win.show();
    void recoverExports().catch(() => {});
    const reports=new ThreeHourReports({
      identity:()=>owner&&currentContext?{owner,employee:currentContext.employee_uuid,
        allowed:!isolatedProfile&&!demo()&&!offline&&(currentContext.owner||currentContext.capabilities?.includes('analytics'))}:null,
      load:authority=>queue.draft(authority,'three-hour-report'),save:(authority,value)=>{queue.draft(authority,'three-hour-report',value);},
      source:async()=>{const result=await send('GET','three-hour');if(!result.ok)throw new Error(result.text||'Нет связи для сводки.');return result.data;},
      write:(report,path)=>local('report-write',{report,path,kind:'three-hour'}),desktop:app.getPath('desktop')
    });
    void reports.tick();
    setInterval(()=>{void reports.tick();},60000).unref();
    setInterval(() => {
      if (owner) {
        void recoverExports().catch(() => {});
        void queue.flush(owner, send, () => owner).catch(() => {});
        void queue.flushTechnical(owner, send, () => owner).catch(() => {});
      }
    }, 7000).unref();
    app.on("second-instance", () => {
      win?.show();
      win?.focus();
    });
  })
  .catch((error) => {
    dialog.showErrorBox("CRM пилот", String(error.message || error));
    app.quit();
  });
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  backend?.kill();
});
