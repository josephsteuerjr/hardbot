import { app, BrowserWindow, dialog, ipcMain, net, protocol, powerMonitor } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync, statfsSync, unlinkSync } from "node:fs";
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
import { PILOT_BASE, existingManagerStatus, existingLogins, ENTRY_SOURCES, EntrySource,readExistingManagerSession,readExistingLogin } from "./crm-manager-login";
import {AnalystRuntime} from './crm-analyst-runtime';
import { pilotCredential } from "./crm-pilot-session";
import {ThreeHourReports} from './crm-report-scheduler';
import {ActiveTime} from './crm-active-time';
import {showStartupRecovery} from './crm-startup-recovery';
import {applyCrmWindowFrame} from './crm-window-frame';
import {CrmLive} from './crm-live';
import {PRODUCTS} from './product';
import {accountAction} from './crm-account';

const isolatedProfile = !!process.env.HARD_USER_DATA;
let connectionNotice = "";
const data =
  process.env.HARD_USER_DATA ||
  join(
    process.env.LOCALAPPDATA || app.getPath("appData"),
    "Hard",
    PRODUCTS.crm.dataName,
    "data",
  );
mkdirSync(data, { recursive: true });
app.setName(PRODUCTS.crm.name);
app.setPath("userData", data);
app.setAppUserModelId(PRODUCTS.crm.appId);
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
const diagnosticEvents:any[]=[];
function diagnostic(stage:string,code:string,ok:boolean,extra:Record<string,number>={}){
  const row={at:new Date().toISOString(),stage,code,ok,version:app.getVersion(),...extra};
  diagnosticEvents.push(row);if(diagnosticEvents.length>200)diagnosticEvents.shift();
  try{writeFileSync(join(data,'crm-diagnostics.json'),JSON.stringify(diagnosticEvents),{flush:true});}catch{/* Diagnostic failure must not destroy business work. */}
}
let helperRepair:Promise<void>|null=null,helperAttempts:number[]=[],rendererReady=false,lastConfirmedAt:string|null=null;
let screenLocked=false, sleeping=false;
const activeTime=new ActiveTime(()=>owner && clock?{
  owner,anchor_id:clock.anchor_id,installation_id:clock.installation_id,
  elapsed:Math.max(0,Math.round(performance.now()-clock.monotonic)),
  kind:win?.isVisible() && win.isFocused() && !win.isMinimized() && !screenLocked && !sleeping
    ? powerMonitor.getSystemIdleTime()<120?'active':'idle':null,
}:null);
const config = () =>
  readSettings().crmConnection as
    | { mode?: string; role?: string; server?: string; auth?: string; source?: EntrySource }
    | undefined;
const shared = () => ['existing-manager', 'existing-login'].includes(config()?.auth || '') && config()?.server === PILOT_BASE;
const entrySource = (): EntrySource => config()?.source || 'manager';
const demo = () => config()?.mode === "synthetic";
const role = () => config()?.role || "employee";
const origin = () => (demo() ? "synthetic-local" : config()?.server || "");
function notifyChanged(type: 'queue' | 'identity' | 'live',extra:any={}) {
  if (win && !win.isDestroyed() && !win.webContents.isDestroyed())
    win.webContents.send('crm:changed', {type,...extra});
}
const stopQueueChanges = queue.onChange(authority => {
  if (authority === owner) notifyChanged('queue');
});
app.once('will-quit', stopQueueChanges);
function forgetAuthority(message: string) {
  live.stop();
  activeTime.stop();
  owner = ""; currentContext = null; clock = null;
  connectionNotice = message;
  // A known revocation must not become an offline login after a later outage.
  // Owner-scoped pending operations remain protected in their original files.
  writeSettings({crmLast: undefined});
  notifyChanged('identity');
}

async function backendStart() {
  if(helperRepair)return helperRepair;
  const work=async()=>{
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
        HARD_CLIENT_VERSION: app.getVersion().split('-')[0],
        HARD_CLIENT_PRODUCT: 'analyst',
        PYTHONIOENCODING: "utf-8",
        HARD_PULT_SETTINGS: join(home, "unused-settings.json"),
      },
    },
  );
  const child=backend;
  child.stderr.on('data',(chunk:Buffer)=>diagnostic('helper','helper_stderr',false,{bytes:chunk.length}));
  await new Promise<void>((resolveReady, reject) => {
    let output = "",settled=false;
    const timeout = setTimeout(
      () => {settled=true;child.kill();reject(new Error("Локальная проверочная база не запустилась."));},
      15000,
    );
    child.stdout.on("data", (chunk: Buffer) => {
      if(settled)return;
      output=(output+chunk.toString()).slice(-8192);
      const match = output.match(/HARD-CRM (\d+) (\S+)/);
      if (match) {
        settled=true;
        port = Number(match[1]);
        secret = match[2];
        clearTimeout(timeout);
        resolveReady();
      }
    });
    child.once("exit", () => {
      if(backend===child){port = 0;secret = "";}
      clearTimeout(timeout);
      reject(new Error("Проверочная база завершилась."));
    });
    child.once('error',()=>{settled=true;clearTimeout(timeout);reject(new Error('Компонент локальной базы недоступен.'));});
  });
  };
  helperRepair=work().finally(()=>{helperRepair=null;});
  return helperRepair;
}

async function diagnostics(repair=false){
  const checks:any[]=[];
  const add=(stage:string,ok:boolean,code:string,text:string)=>{checks.push({stage,ok,code,text});diagnostic(stage,code,ok);};
  add('main',true,'main_ready','Основной процесс запущен.');
  add('renderer',rendererReady,rendererReady?'renderer_ready':'renderer_pending',rendererReady?'Интерфейс и защищённый мост отвечают.':'Ожидается интерфейс.');
  try{
    const disk=statfsSync(data);const free=Number(disk.bavail)*Number(disk.bsize);
    add('disk',free>=64*1024*1024,free>=64*1024*1024?'disk_ok':'disk_low',free>=64*1024*1024?'Место для локального сохранения доступно.':'Освободите место на локальном диске.');
    const probe=join(data,'diagnostic-'+sessionId+'.tmp');writeFileSync(probe,'crm-profile-probe',{flush:true});
    if(readFileSync(probe,'utf8')!=='crm-profile-probe')throw Error();unlinkSync(probe);
    queue.installation();
    add('profile',true,'profile_ok','Защищённое хранилище читается и записывается.');
  }catch{add('profile',false,'profile_unavailable','Локальное сохранение недоступно. Проверьте доступ к профилю и свободное место.');}
  if(!port && repair){
    helperAttempts=helperAttempts.filter(t=>performance.now()-t<300000);
    if(helperAttempts.length<2){
      helperAttempts.push(performance.now());backend?.kill();
      try{await backendStart();}catch{backend?.kill();}
    }
  }
  add('helper',!!port,port?'helper_ready':'helper_unavailable',port?'Локальный компонент отвечает.':'Локальный компонент недоступен. Рабочий API проверяется отдельно; доступен повтор запуска.');
  if(demo())add('network',!!port,port?'synthetic_ready':'synthetic_unavailable',port?'Проверочная база доступна.':'Проверочная база недоступна.');
  else{
    try{
      const base=config()?.mode==='remote'?origin():PILOT_BASE;
      const url=base===PILOT_BASE?base+'/health':new URL('/ready.json',base).toString();
      const result=await fetchBuffered((u,i)=>net.fetch(u,i),url,{method:'GET'},8000);
      const body=await result.json();
      const ok=result.ok && body.ok===true && (base!==PILOT_BASE || body.namespace==='crm-shared-pilot-v1');
      add('network',ok,ok?'api_ready':'api_unavailable',ok?'Публичная проверка API и строгий TLS прошли.':'API не подтвердил готовность или пространство CRM.');
    }catch(error){
      const message=String((error as Error).message);
      const code=/CERT|SSL|TLS/i.test(message)?'tls_failed':/NAME_NOT_RESOLVED|ENOTFOUND/i.test(message)?'dns_failed':'network_unavailable';
      add('network',false,code,code==='tls_failed'?'Сертификат сервера не прошёл проверку. Проверьте часы и доверенный сертификат.':code==='dns_failed'?'Не удалось определить адрес сервера. Проверьте VPN и DNS.':'API недоступен. Проверьте VPN, сеть и настройки прокси.');
    }
  }
  const authority=owner;
  if(config()){
    const result=await identify();
    const code=result.ok?'identity_confirmed':result.data?.error || (result.status===401?'login_required':result.status===403?'access_denied':'identity_unverified');
    add('identity',result.ok,code,result.ok?'Сервер подтвердил текущий вход и права.':result.status===401 || result.status===403?(result.text || 'Требуется действующий вход; доступ автоматически не выдаётся.'):'Вход не проверен из-за недоступности API. Локальная работа сохранена.');
  }else add('identity',false,'login_required','Выберите собственный сохранённый вход.');
  if(owner){
    try{
      const q=queue.queueState(owner);const pending=q.entries.filter(e=>e.state==='pending').length;
      const attention=q.entries.filter(e=>e.state==='attention' && !e.reviewed_at).length;
      queue.draft(owner,'registry-input');
      add('queue',!attention,attention?'queue_attention':'queue_ok',`Сохранено на устройстве: ${pending}. Требует сверки: ${attention}.`);
      if(repair && owner===authority){await queue.flush(owner,send,()=>owner);}
    }catch{add('queue',false,'protected_work_unreadable','Очередь или черновик не читается. Исходные файлы сохранены; сброс профиля не выполняется.');}
  }
  return {ok:true,data:{checks,at:new Date().toISOString()}};
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
  'registry',
  'registry-edit',
  'managed-portfolio','assignment-staff','assignment-preview','assignment-commit',
  'assignment-receipt','assignment-history','manager-statistics','changes',
  'assignment-job','assignment-job-step','assignment-job-cancel',
  'manager-access',
  'active-time',
  "shifts",
  'shift-report',
  'cabinet',
  'three-hour',
  'manager-report',
  'analyst-report','analyst-statistics',
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
const transport: queue.Transport = async (method, action, body, query) => {
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
  if(action==='manager-access')return managerAccess(method,body,query);
  if(isolatedProfile&&!app.isPackaged&&process.env.HARD_CRM_QA_SHARED_ORIGIN&&demo()){
    const endpoint=new URL(process.env.HARD_CRM_QA_SHARED_ORIGIN);
    if(endpoint.protocol!=='http:'||endpoint.hostname!=='127.0.0.1'||endpoint.pathname!=='/'||endpoint.username||endpoint.password||endpoint.search||endpoint.hash)
      return {ok:false,status:403,text:'Проверочный сервер должен быть локальным SSH-туннелем.'};
    try{
      const key=readFileSync(process.env.HARD_CRM_QA_SECRET_FILE!,'utf8').trim();
      if(!/^[a-f0-9]{64}$/.test(key))throw Error();
      const url=new URL('/api/crm/'+action,endpoint);for(const [k,v] of Object.entries(query||{}))if(v!=null)url.searchParams.set(k,String(v));
      const response=await fetchBuffered((u,i)=>net.fetch(u,i),url.toString(),{method,
        headers:{'X-QA-Secret':key,'X-QA-Actor':process.env.HARD_CRM_QA_ACTOR||'', 'Content-Type':'application/json'},
        body:method==='POST'?JSON.stringify(body||{}):undefined},action==='changes'?4000:30000);
      const value=await response.json();return {ok:response.ok,status:response.status,data:value,text:value.text||value.error};
    }catch{return {ok:false,status:0,text:'Нет подтверждённого ответа общего проверочного сервера.'};}
  }
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
    }, action==='changes'?4000:["export", "import-preview", "import-apply"].includes(action) ? 120000 : 30000);
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

async function managerAccess(method:string,body:any,query:any):Promise<queue.Result>{
  const routes:any={team:['GET','team'],revoke:['POST','team/device-revoke'],roster:['GET','employee_board'],entered:['GET','entered'],
    approve:['POST','account_approve'],reset:['POST','account_reset'],board:['POST','employee_board']};
  const resource=method==='GET'?query?.resource:body?.resource;
  const route=routes[resource];
  if(!route||route[0]!==method)return {ok:false,status:400,text:'Неизвестное действие доступа.'};
  if(demo())return local('manager-access',body,query,method);
  if(!shared()||!['manager','crm'].includes(entrySource())||currentContext?.role!=='manager')
    return {ok:false,status:403,text:'Для управления входами нужен действующий вход Руководителя.'};
  const authority=owner,actor=currentContext.actor_uid;
  try{
    await pilotCredential(currentContext.employee_uuid,false,entrySource());
    const login=readExistingLogin(entrySource());
    const headers={'X-Pult-Token':login.credential,'Content-Type':'application/json','X-Client-Version':app.getVersion()};
    const identity=await fetchBuffered((u,i)=>net.fetch(u,i),'https://hard.rimborso.ru/api/manager/device/me',{method:'GET',headers},10000);
    const subject=await identity.json();
    if(!identity.ok||subject.subject?.actor_uid!==actor||authority!==owner)
      return {ok:false,status:403,text:'Вход или полномочия изменились. Подключитесь заново.'};
    const response=await fetchBuffered((u,i)=>net.fetch(u,i),'https://hard.rimborso.ru/api/manager/'+route[1],
      {method,headers,body:method==='POST'?JSON.stringify(body?.payload||{}):undefined},15000);
    const value=await response.json();
    return {ok:response.ok&&value.ok!==false,status:response.status,data:value,text:value.message||value.text||value.error};
  }catch{return {ok:false,status:0,text:'Не удалось подтвердить результат в службе входа. Не повторяйте изменение с новым ID.'};}
}

const send:queue.Transport=async(method,action,body,query)=>{
  const result=await transport(method,action,body,query);
  if(result.ok)lastConfirmedAt=new Date().toISOString();
  const stage=['registry-edit','command','active-time'].includes(action)?'save':'api';
  diagnostic(stage,result.ok?'confirmed':result.status===401?'login_required':result.status===403?'access_denied':result.status===409?'requires_review':result.status===0?'network_unavailable':'api_error',result.ok,{status:result.status});
  return result;
};

const analystRuntime=new AnalystRuntime({home:data,window:()=>win,synthetic:demo,
  writeTable:(report,path)=>local('report-write',{report,path,kind:'table'}),
  authorize:async expected=>{
    if(!expected||expected!==owner||currentContext?.role!=='analyst')throw Error('Нужен действующий вход Аналитика.');
    const actor=currentContext.actor_uid;
    const result=await send('GET','context');
    if(!result.ok||result.data?.role!=='analyst'||expected!==owner||currentContext?.role!=='analyst'||result.data?.actor_uid!==actor)
      throw Error('Сервер не подтвердил полномочия Аналитика.');
    if(demo()){
      const endpoint=await local('analyst-admin-init',{});
      if(!endpoint.ok)throw Error(endpoint.text||'Не удалось открыть проверочный административный контур.');
      if(expected!==owner||currentContext?.role!=='analyst'||currentContext?.actor_uid!==actor)throw Error('Вход Аналитика изменился.');
      return {owner:expected,actor,...endpoint.data};
    }
    if(!shared()||!['analyst','crm'].includes(entrySource()))throw Error('Подключите вход Аналитика.');
    const login=readExistingLogin(entrySource());
    return {owner:expected,actor,url:'https://hard.rimborso.ru',
      token:(login.kind==='analyst'?'account:':'')+login.credential,fingerprint:login.fingerprint};
  }});

const live=new CrmLive(query=>send('GET','changes',undefined,query),()=>owner,
  authority=>queue.draft(authority,'live-cursor'),
  (authority,cursor)=>{queue.draft(authority,'live-cursor',cursor);},
  event=>notifyChanged('live',event));
app.once('will-quit',()=>live.stop());

async function identify() {
  const result = await send("GET", "context");
  if (result.ok) {
    currentContext = result.data;
    owner = origin() + "\n" + currentContext.employee_uuid;
    // Older deployed pilots have no invalidation protocol. A missing feature
    // must not turn their otherwise confirmed connection into an offline state.
    if(currentContext.distribution)live.start();else live.stop();
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
    diagnostic('launcher','launcher_ready',true);
    try{await backendStart();diagnostic('helper','helper_ready',true);}catch{backend?.kill();diagnostic('helper','helper_unavailable',false);}
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
      product: PRODUCTS.crm.name,
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
      activity:{clock_quality:clock?'anchored':'unconfirmed',idle_threshold_seconds:120,last_confirmed_at:lastConfirmedAt},
    }));
    safeHandler('crm:ui-ready',()=>{rendererReady=true;diagnostic('renderer','renderer_ready',true);return {ok:true};});
    safeHandler('crm:admin',(action:string,args:any[],expected:string)=>analystRuntime.request(action,args,expected));
    safeHandler('crm:diagnostics',async(repair:boolean=false)=>{rendererReady=true;return diagnostics(repair===true);});
    safeHandler('crm:diagnostics-report',async()=>{
      const choice=await dialog.showSaveDialog(win!,{title:'Диагностика CRM',defaultPath:'CRM-диагностика-'+Date.now()+'.json',filters:[{name:'JSON',extensions:['json']}]});
      if(choice.canceled || !choice.filePath)return {ok:true,cancelled:true};
      writeFileSync(choice.filePath,JSON.stringify({schema_version:1,at:new Date().toISOString(),events:diagnosticEvents},null,2),{flag:'wx',flush:true});
      return {ok:true};
    });
    safeHandler(
      "crm:api",
      async (method: string, action: string, body: any, query: any) => {
        if (action === "command" || action === "receipt" || action === 'registry-edit' || method==='POST' && action==='active-time')
          throw new Error("Используйте защищённую очередь.");
        const authority = owner,
          result = await send(method, action, body, query);
        if (authority !== owner && result.ok)
          return { ok: false, status: 409, text: "Учётная запись изменилась." };
        const key = action + JSON.stringify(query || {});
        const freshOnly=action==='changes'||action.startsWith('assignment-')||action==='managed-portfolio'||action==='manager-statistics'||action==='manager-report'||action==='analyst-report'||action==='analyst-statistics';
        if (result.ok && method === "GET" && owner && !freshOnly)
          queue.cache(owner, key, result.data);
        else if (method === "GET" && result.status === 0 && owner && !freshOnly) {
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
    safeHandler('crm:registry-edit', async (body:any,expectedOwner?:string) => {
      if (!owner || !currentContext) throw new Error('Сначала подтвердите вход в CRM.');
      if(expectedOwner!==undefined && owner!==expectedOwner)throw new Error('Личность правки изменилась. Исходный ввод сохранён.');
      const authority=owner;
      queue.enqueue(authority, body, 'registry-edit');
      void queue.flush(authority, send, () => owner);
      return {ok:true, queued:true, operation_id:body.operation_id};
    });
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
    safeHandler("crm:queue", async (action: string, id: string,expectedOwner?:string) => {
      if (!owner) throw new Error("Нет входа.");
      if(expectedOwner!==undefined && owner!==expectedOwner)throw new Error('Личность очереди изменилась.');
      if(action==='review-registry')queue.reviewRegistry(owner,id);
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
    safeHandler("crm:draft", (key: string, value: any, expectedOwner?:string) => {
      if(expectedOwner!==undefined && owner!==expectedOwner)throw new Error('Личность черновика изменилась. Исходный ввод сохранён.');
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
      if(['account-login','account-password','account-status','account-logout'].includes(input?.action)){
        const result=await accountAction(input.action,input);
        if(input.action==='account-password')return result;
        forgetAuthority('');
        writeSettings({crmConnection:undefined,crmLast:undefined});
        if(input.action==='account-logout'||(result.data as any).pending)return result;
        await pilotCredential(undefined,true,'crm');
        writeSettings({crmConnection:{mode:'remote',source:'crm',server:PILOT_BASE,auth:'existing-login'}});
        offline=false;return identify();
      }
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
      if(action==='manager-report'||action==='analyst-report'){
        const authority=owner;
        if(!authority)throw new Error('Сначала подтвердите вход.');
        const month=input?.mode==='month',dynamics=input?.mode==='dynamics';
        const result=await send('GET',action==='analyst-report'?(month?'analyst-statistics':'analyst-report'):(month?'manager-statistics':'manager-report'),undefined,month?{month:input.month}:input?.snapshot_id&&!dynamics?{snapshot_id:input.snapshot_id}:{day:input?.day});
        if(!result.ok)return result;
        const report=month||dynamics?result.data:result.data.report;
        const choice=await dialog.showSaveDialog(win!,{title:'Отчёт руководителя',defaultPath:`Отчёт ${month?report.month:dynamics?report.day:report.today} ${new Date().toISOString().replace(/[^0-9]/g,'').slice(8,14)}.xlsx`,filters:[{name:'Excel',extensions:['xlsx']}]});
        if(choice.canceled||!choice.filePath)return {ok:false,cancelled:true};
        if(owner!==authority)throw new Error('Вход изменён. Повторите выгрузку.');
        return local('report-write',{report,kind:month?'manager-month':dynamics?'manager-dynamics':'three-hour',path:choice.filePath});
      }
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
    let rendererRepairs=0;
    win.webContents.on('render-process-gone',()=>{
      diagnostic('renderer','renderer_stopped',false);
      if(++rendererRepairs<=2){rendererReady=false;win?.webContents.reload();}
      else void showStartupRecovery('renderer_unavailable');
    });
    setTimeout(()=>{if(!rendererReady && win && !win.isDestroyed())void showStartupRecovery('preload_or_renderer_unavailable');},10000).unref();
    const framed=await applyCrmWindowFrame(win);
    diagnostic('window',framed?'window_frame_applied':'window_frame_unavailable',framed);
    if (!framed && process.platform === "win32")
      await local("window-frame", {
        hwnd: win.getNativeWindowHandle().readBigUInt64LE().toString(),
      });
    win.show();
    win.on('blur',()=>activeTime.stop());win.on('hide',()=>activeTime.stop());
    win.on('minimize',()=>activeTime.stop());win.on('close',()=>activeTime.stop());
    powerMonitor.on('lock-screen',()=>{screenLocked=true;activeTime.stop();});
    powerMonitor.on('unlock-screen',()=>{screenLocked=false;});
    powerMonitor.on('suspend',()=>{sleeping=true;activeTime.stop();clock=null;});
    powerMonitor.on('resume',()=>{sleeping=false;void identify().catch(()=>{});});
    setInterval(()=>{try{activeTime.tick();}catch{connectionNotice='Не удалось сохранить активный интервал. Проверьте защищённый профиль.';}},2000).unref();
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
    diagnostic('startup','startup_failed',false);
    void showStartupRecovery('startup_failed');
  });
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  try{activeTime.stop();}catch{/* The last protected checkpoint remains recoverable. */}
  backend?.kill();
});
