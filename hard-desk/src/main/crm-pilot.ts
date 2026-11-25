import { app, BrowserWindow, dialog, ipcMain, net, protocol, powerMonitor, Menu, Tray } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync, statfsSync, statSync, unlinkSync } from "node:fs";
import { basename, dirname, extname, join, resolve, sep } from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { accountLogin, accountManagerLogin } from "./net";
import {
  readFieldToken,
  readToken,
  readSettings,
  writeSettings,
} from "./store";
import { fetchBuffered } from "./transport";
import * as queue from "./crm-queue";
import { PILOT_BASE, existingManagerStatus, existingLogins, ENTRY_SOURCES, EntrySource, entryFingerprint,readExistingManagerSession,readExistingLogin } from "./crm-manager-login";
import {AnalystRuntime} from './crm-analyst-runtime';
import { pilotCredential,pilotGrantCandidates } from "./crm-pilot-session";
import {ThreeHourReports} from './crm-report-scheduler';
import {ActiveTime} from './crm-active-time';
import {showStartupRecovery} from './crm-startup-recovery';
import {applyCrmWindowFrame} from './crm-window-frame';
import {CrmLive} from './crm-live';
import {FIXED_PRODUCT,PRODUCTS,STABLE_CRM} from './product';
import {accountAction,accountState,adoptSoftAccount,markAccountLoginRequired,savedAccountFingerprint} from './crm-account';
import {checkUpdates,installUpdate,startUpdates,updateState} from './updates';
import {legacyAdministrationAllowed,nativeCapability} from './crm-native-access';

const isolatedProfile = !!process.env.HARD_USER_DATA;
const product = STABLE_CRM ? FIXED_PRODUCT! : PRODUCTS.crm;
let connectionNotice = "";
const data =
  process.env.HARD_USER_DATA ||
  join(
    process.env.LOCALAPPDATA || app.getPath("appData"),
    "Hard",
    product.dataName,
    "data",
  );
mkdirSync(data, { recursive: true });
app.setName(product.name);
app.setPath("userData", data);
app.setAppUserModelId(product.appId);
// Stable Hard and Soft keep the same Windows device key they used before CRM.
if(!STABLE_CRM)process.env.HARD_USER_DATA = data;
protocol.registerSchemesAsPrivileged([
  {
    scheme: "hard",
    privileges: { standard: true, secure: true, supportFetchAPI: true },
  },
]);
const firstInstance=app.requestSingleInstanceLock();
if(!firstInstance)app.quit();
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
let lastRecordedApiSuccess = -Infinity;
function diagnostic(stage:string,code:string,ok:boolean,extra:Record<string,number>={}){
  const row={at:new Date().toISOString(),stage,code,ok,version:app.getVersion(),...extra};
  diagnosticEvents.push(row);if(diagnosticEvents.length>200)diagnosticEvents.shift();
  if (stage === 'api') {
    const now = performance.now();
    if (ok && code === 'confirmed') {
      if (now - lastRecordedApiSuccess < 60_000) return;
      lastRecordedApiSuccess = now;
    } else {
      lastRecordedApiSuccess = -Infinity;
    }
  }
  try{writeFileSync(join(data,'crm-diagnostics.json'),JSON.stringify(diagnosticEvents),{flush:true});}catch{/* Diagnostic failure must not destroy business work. */}
}
let helperRepair:Promise<void>|null=null,helperAttempts:number[]=[],rendererReady=false,lastConfirmedAt:string|null=null;
let tray: Tray | null = null;
let quitting = false;
function currentWindow(): BrowserWindow | null {
  return win && !win.isDestroyed() && !win.webContents.isDestroyed() ? win : null;
}
function showWindow() {
  const current = currentWindow();
  if (!current) return;
  if (current.isMinimized()) current.restore();
  current.show();
  current.focus();
}
let screenLocked=false, sleeping=false;
const activeTime=new ActiveTime(()=>{
  const current=currentWindow();
  return owner && clock?{
    owner,anchor_id:clock.anchor_id,installation_id:clock.installation_id,authority:currentContext,
    elapsed:Math.max(0,Math.round(performance.now()-clock.monotonic)),
    kind:current?.isVisible() && current.isFocused() && !current.isMinimized() && !screenLocked && !sleeping
      ? powerMonitor.getSystemIdleTime()<120?'active':'idle':null,
  }:null;
});
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
  analystRuntime.stop();
  activeTime.stop();
  owner = ""; currentContext = null; clock = null;
  connectionNotice = message;
  // A known revocation must not become an offline login after a later outage.
  // Owner-scoped pending operations remain protected in their original files.
  writeSettings({crmLast: undefined});
  notifyChanged('identity');
}

function requireFreshAccountLogin(fingerprint:string|undefined,status:number) {
  if (![401,403,410].includes(status) || !markAccountLoginRequired(fingerprint))return false;
  writeSettings({crmConnection:undefined,crmLast:undefined,crmExplicitEntry:true});
  forgetAuthority('Сохранённый сеанс завершён. Войдите в CRM с текущим паролем.');
  return true;
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
      () => {settled=true;child.kill();reject(new Error("Локальный компонент CRM не запустился."));},
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
  }else add('identity',false,'login_required','Войдите с рабочим логином и паролем.');
  if(owner){
    try{
      const q=queue.queueState(owner);const pending=q.entries.filter(e=>e.state==='pending').length;
      const attention=q.entries.filter(e=>e.state==='attention' && !e.reviewed_at).length;
      queue.draft(owner,'registry-input');
      add('queue',!attention,attention?'queue_attention':'queue_ok',`Сохранено на устройстве: ${pending}. Требует сверки: ${attention}.`);
      if(repair && owner===authority){await queue.flush(owner,send,()=>owner,()=>currentContext);}
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
  "context", "operator-status",
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
  'logistics','logistics-export','logistics-export-download','logistics-preview','logistics-apply','logistics-update','logistics-call',
  'manager-access','grant-catalog','grant-admin',
  'active-time',
  "shifts",
  'shift-report',
  'cabinet',
  'three-hour',
  'manager-report',
  'manager-report-download',
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
    return {ok: false, status: 401, text: "Введите логин и пароль для подключения к CRM."};
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
  const source = shared() ? entrySource() : undefined;
  const fingerprint = source === 'crm' ? savedAccountFingerprint() : undefined;
  try {
    const previous = readSettings().crmLast as any;
    const expected = currentContext?.employee_uuid || (previous?.origin === base ? previous.context?.employee_uuid : undefined);
    let token = shared() ? await pilotCredential(expected, false, entrySource(), currentContext?.grant_id || undefined) : role() === "employee" ? readFieldToken() : readToken();
    if (!base || !token) return {ok: false, status: 401, text: "Войдите в CRM."};
    const url = new URL(shared() ? PILOT_BASE + "/api/crm/" + action : "/api/crm/" + action, base);
    for (const [key, value] of Object.entries(query || {})) if (value != null) url.searchParams.set(key, String(value));
    const request = (credential: string) => fetchBuffered((target, init) => net.fetch(target, init), url.toString(), {
      method,
      headers: {
        ...(shared() ? {Authorization: "Bearer " + credential} : {[role() === "employee" ? "X-App-Token" : "X-Pult-Token"]: credential}),
        "Content-Type": "application/json", "X-Client-Version": app.getVersion(), "X-Work-Product": "hard",
      },
      body: method === "POST" ? JSON.stringify(body || {}) : undefined,
    }, action==='changes'?12000:["export", "import-preview", "import-apply", "manager-report", "manager-statistics", "manager-report-download", "analyst-report", "analyst-statistics", "three-hour"].includes(action) ? 120000 : 30000);
    let response = await request(token), value = await response.json();
    if (source && response.status === 401 && ["pilot_session_expired","pilot_session_invalid"].includes(value.error)) {
      token = await pilotCredential(expected, true, source, currentContext?.grant_id || undefined);
      response = await request(token); value = await response.json();
    }
    if (source && (response.status === 401 || (response.status === 403 &&
        ["manager_required", "identity_unmapped", "pilot_session_invalid"].includes(value.error)))) {
      if (!requireFreshAccountLogin(fingerprint,response.status) && shared())
        forgetAuthority(value.text || "Сервер отозвал доступ к CRM.");
    }
    return {ok: response.ok, status: response.status, data: value, text: value.text || value.error};
  } catch (error) {
    const status = Number((error as any)?.status || 0);
    if (source && [401,403,410].includes(status)) {
      if(!requireFreshAccountLogin(fingerprint,status) && shared())
        forgetAuthority(String((error as Error).message));
    }
    return {ok: false, status, kind: status ? "auth" : "offline", text: status ? String((error as Error).message) : "Нет подтверждённой связи с сервером CRM. Проверьте сохранённый вход и подключение к сети."};
  }
};

async function managerAccess(method:string,body:any,query:any):Promise<queue.Result>{
  const routes:any={team:['GET','team'],revoke:['POST','team/device-revoke'],roster:['GET','employee_board'],entered:['GET','entered'],
    approve:['POST','account_approve'],reset:['POST','account_reset'],board:['POST','employee_board'],plan:['POST','plan']};
  const resource=method==='GET'?query?.resource:body?.resource;
  const route=routes[resource];
  if(!route||route[0]!==method)return {ok:false,status:400,text:'Неизвестное действие доступа.'};
  const capability=resource==='plan'?'manager-plan':'manager-access';
  if(!nativeCapability(currentContext,capability))
    return {ok:false,status:403,text:'Сервер не выдал полномочие управления входами в этом рабочем месте.'};
  const authority=owner,previous=currentContext,fresh=await send('GET','context');
  if(!fresh.ok)return fresh;
  if(authority!==owner)return {ok:false,status:409,text:'Учётная запись изменилась. Повторите действие.'};
  if(fresh.data?.employee_uuid!==previous?.employee_uuid ||
      previous?.actor_uid!==undefined&&fresh.data?.actor_uid!==previous.actor_uid ||
      !nativeCapability(fresh.data,capability))
    return {ok:false,status:403,text:'Полномочие управления входами отозвано или изменилось.'};
  currentContext=fresh.data;
  if(!legacyAdministrationAllowed(fresh.data))
    return {ok:false,status:403,text:'Для этой полномочия нужен отдельный территориально ограниченный контур управления входами.'};
  if(demo())return local('manager-access',body,query,method);
  if(!shared()||!['manager','analyst','crm'].includes(entrySource()))
    return {ok:false,status:403,text:'Для управления входами нужно подтверждённое подключение к рабочему месту.'};
  const actor=currentContext.actor_uid;
  try{
    await pilotCredential(currentContext.employee_uuid,false,entrySource(),currentContext.grant_id || undefined);
    const login=readExistingLogin(entrySource());
    const analyst=login.kind==='analyst'||login.kind==='analyst-device';
    const headers:any={...(analyst&&login.kind==='analyst-device'?{'X-Device-Token':login.credential}:{'X-Pult-Token':login.credential}),
      'Content-Type':'application/json','X-Client-Version':app.getVersion(),
      ...(login.kind==='analyst'?{'X-CRM-Entry':'analyst'}:{}),...(login.deviceFingerprint?{'X-Device-Id':login.deviceFingerprint}:{})};
    const identity=await fetchBuffered((u,i)=>net.fetch(u,i),analyst?'https://hard.rimborso.ru/api/crm-identity':'https://hard.rimborso.ru/api/manager/device/me',{method:'GET',headers},10000);
    const subject=await identity.json();
    const confirmed = subject.subject?.actor_uid;
    const confirmedKind=analyst?subject.subject?.type==='analyst'&&subject.me?.role==='analyst':subject.subject?.type==='manager';
    if(!identity.ok || !confirmedKind || !Number.isSafeInteger(confirmed) || confirmed === 0 ||
       (actor !== undefined && actor !== confirmed) || authority!==owner || login.fingerprint!==entryFingerprint(entrySource()))
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
    if(!expected||expected!==owner||!nativeCapability(currentContext,'admin'))
      throw Error('Сервер не выдал полномочие администрирования в этом рабочем месте.');
    const employee = currentContext.employee_uuid;
    const previousActor = currentContext.actor_uid;
    const source = entrySource();
    const fingerprint = demo() ? '' : entryFingerprint(source);
    const valid = () => expected === owner && nativeCapability(currentContext,'admin')
      && currentContext.employee_uuid === employee && (demo() || (entrySource() === source && entryFingerprint(source) === fingerprint));
    const result = await send('GET','context');
    if (!result.ok || !nativeCapability(result.data,'admin') || result.data.employee_uuid !== employee || !valid())
      throw Error('Сервер не подтвердил полномочия администрирования.');
    if (!legacyAdministrationAllowed(result.data))
      throw Error('Для этой полномочия нужен отдельный территориально ограниченный контур администрирования.');
    const ownerAccess = result.data.owner === true;
    let actor = result.data.actor_uid;
    if(demo()){
      const endpoint=await local('analyst-admin-init',{});
      if(!endpoint.ok || !Number.isSafeInteger(actor) || !actor || !valid())throw Error('Полномочие администрирования изменилось.');
      return {owner:expected,...endpoint.data,actor,current:valid};
    }
    if (!shared() || !(ownerAccess ? ['manager','analyst','crm'] : ['analyst','crm']).includes(source))
      throw Error('Подключите вход с подтверждённым полномочием администрирования.');
    const login = readExistingLogin(source);
    if (!(ownerAccess ? ['manager','analyst','analyst-device'] : ['analyst','analyst-device']).includes(login.kind) || login.fingerprint !== fingerprint)
      throw Error('Вход администратора изменился.');
    if (actor === undefined) {
      const response = await fetchBuffered((url, init) => net.fetch(url,init), 'https://hard.rimborso.ru/api/crm-identity', {
        method:'GET', redirect:'error', headers:{
          [login.kind === 'analyst-device' ? 'X-Device-Token' : 'X-Pult-Token']:login.credential,
          ...(login.kind === 'analyst' ? {'X-CRM-Entry':'analyst'} : {}),'X-Client-Version':app.getVersion(),
          ...(login.deviceFingerprint ? {'X-Device-Id':login.deviceFingerprint} : {})
        }},10000);
      const identity = await response.json();
      const confirmed = ownerAccess
        ? identity.subject?.type === 'manager' && ['manager','full'].includes(identity.me?.role)
        : identity.subject?.type === 'analyst' && identity.me?.role === 'analyst';
      if (!response.ok || identity.ok !== true || !confirmed)
        throw Error('Сервер не подтвердил личность администратора.');
      actor = identity.subject.actor_uid;
    }
    if (!Number.isSafeInteger(actor) || !actor || (previousActor !== undefined && previousActor !== actor) || !valid())
      throw Error('Изменилась личность администратора.');
    return {owner:expected,actor,url:'https://hard.rimborso.ru',
      token:(login.kind==='analyst'?'account:':'')+login.credential,fingerprint,
      existingProfile: STABLE_CRM && product.id === 'analyst' && source === 'analyst' && !isolatedProfile, profileUrl:login.profileUrl,current:valid};
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
    owner = origin() + "\n" + currentContext.employee_uuid +
      (currentContext.access_model === 2 ? "\n" + currentContext.actor_uid + "\n" + currentContext.grant_id : "");
    if(STABLE_CRM&&product.id==='hard'&&currentContext.role==='employee'){
      try{queue.importSoftQueue(owner);}
      catch(error){connectionNotice=(error as Error).message;}
    }
    if (STABLE_CRM && !isolatedProfile && (product.id === 'analyst' || currentContext.owner === true) &&
        nativeCapability(currentContext,'admin')) {
      void analystRuntime.resume(owner).catch(() => diagnostic('helper','analyst_start_failed',false));
    }
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
    if(config())forgetAuthority(result.text || "Сервер не подтвердил доступ.");
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
    let errorsChanged = false;
    const recordError = (job: any, message: string | undefined) => {
      if (job.error === message) return;
      job.error = message;
      errorsChanged = true;
    };
    for (const job of jobs.filter((v: any) => !v.completed)) {
      if (owner !== authority) return;
      if (!existsSync(job.path)) {
        recordError(job, "Файл выгрузки не найден. Исходный запрос сохранён.");
        continue;
      }
      const result = await local("workbook-resume", {
        snapshot: job.snapshot,
        path: job.path,
      });
      if (!result.ok) {
        recordError(job, result.text);
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
          currentContext,
        );
      job.completed = true;
      job.sha256 = result.data.sha256;
      // The exact receipt is already durably queued. Retain its manifest and
      // proof, without accumulating full completed registry copies locally.
      job.manifest = job.snapshot.manifest;
      delete job.snapshot;
      delete job.error;
      queue.draft(authority, "exports", jobs);
      errorsChanged = false;
    }
    if (errorsChanged) queue.draft(authority, "exports", jobs);
  } finally {
    exportRecovery = false;
  }
}

let activeRequests = 0, installingUpdate = false;
function safeHandler(name: string, fn: (...args: any[]) => any) {
  ipcMain.handle(name, async (event, ...args) => {
    const current = currentWindow();
    if (!current) return {ok:false,status:409,text:'Окно закрыто. Повторите действие после запуска.'};
    if (
      event.sender !== current.webContents ||
      !event.senderFrame?.url.startsWith("hard://app/")
    )
      throw new Error("Недоверенное окно.");
    const workingRequest = name !== 'crm:update';
    if(workingRequest && installingUpdate)
      return {ok:false,status:409,text:'Началась установка обновления.'};
    if(workingRequest)activeRequests++;
    try {
      return await fn(...args);
    } catch (error) {
      return {
        ok: false,
        status: Number((error as any)?.status || 0),
        text: String((error as Error).message || error),
        ...((error as any)?.code ? {error:(error as any).code} : {}),
        ...((error as any)?.details ? {details:(error as any).details} : {}),
      };
    } finally { if(workingRequest)activeRequests--; }
  });
}

function watchInstallerExit(): void {
  if (process.platform !== 'win32' || (!STABLE_CRM && product.id !== 'crm')) return;
  let checking = false;
  const installPath = createHash('sha256').update(process.execPath.toLowerCase()).digest('hex');
  const request = join(process.env.LOCALAPPDATA || app.getPath('appData'), 'Hard', 'installer-requests', installPath + '.request');
  const timer = setInterval(() => {
    if (checking || installingUpdate) return;
    let age: number;
    try { age = Date.now() - statSync(request).mtimeMs; } catch { return; }
    if (age < 0 || age > 35000) return;
    checking = true;
    void (async () => {
      try {
        if (activeRequests || exportRecovery || await analystRuntime.busy() || activeRequests || exportRecovery) return;
        app.quit();
      } catch { /* Keep running and let the installer retry. */ }
      finally { checking = false; }
    })();
  }, 350);
  timer.unref();
  app.once('will-quit', () => clearInterval(timer));
}

app
  .whenReady()
  .then(async () => {
    if(!firstInstance)return;
    watchInstallerExit();
    const ui = resolve(__dirname, "ui");
    const mime: Record<string, string> = {
      ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
      ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2", ".json": "application/json",
    };
    protocol.handle("hard", (request) => {
      const url = new URL(request.url),
        path = resolve(ui, decodeURIComponent(url.pathname).replace(/^\//, ""));
      if (url.hostname !== "app" || !path.startsWith(ui + sep))
        return new Response("Forbidden", { status: 403 });
      return new Response(readFileSync(path), { headers: { "content-type": mime[extname(path).toLowerCase()] || "application/octet-stream", "cache-control": "no-cache" } });
    });
    diagnostic('launcher','launcher_ready',true);
    try{await backendStart();diagnostic('helper','helper_ready',true);}catch{backend?.kill();diagnostic('helper','helper_unavailable',false);}
    if(quitting)return;
    if(STABLE_CRM&&product.id==='hard'&&!isolatedProfile&&!config()&&!readSettings().crmExplicitEntry){
      try{adoptSoftAccount();}
      catch(error){connectionNotice=(error as Error).message;}
    }
    const savedAccount=accountState();
    if(STABLE_CRM&&!isolatedProfile&&!config()&&savedAccount.available&&!savedAccount.pending){
      const fingerprint=savedAccountFingerprint();
      try {
        await pilotCredential(undefined,true,'crm');
        writeSettings({crmConnection:{mode:'remote',server:PILOT_BASE,source:'crm',auth:'existing-login'}});
      } catch(error) {
        if(!requireFreshAccountLogin(fingerprint,Number((error as any)?.status)))
          connectionNotice='Сервер CRM недоступен. Повторите подключение, когда появится сеть.';
      }
    }
    if(quitting)return;
    // A stable in-place upgrade uses only this product's previously approved
    // sign-in. Separate CRM installations and explicit logouts keep the entry form.
    if (STABLE_CRM && !isolatedProfile && !config() && !readSettings().crmExplicitEntry &&
        !existsSync(join(data,'crm-shared-session.bin')) && !existsSync(join(data,'crm-account.bin'))) {
      const logins=existingLogins();
      const candidates:EntrySource[]=product.id==='hard'?['hard','soft']:[product.id as EntrySource];
      const source=candidates.find(id=>logins.some(login=>login.source===id&&login.available));
      if (source) {
        try {
          await pilotCredential(undefined,true,source);
          writeSettings({crmConnection:{mode:'remote',server:PILOT_BASE,source,auth:'existing-login',
            role:source==='analyst'||source==='manager'?source:'employee'}});
        } catch { connectionNotice='Сохранённый вход не подтверждён. Проверьте подключение или войдите в CRM.'; }
      }
    }
    if(quitting)return;
    await identify();
    if(quitting)return;
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
      product: product.name,
      ok: true,
      version: app.getVersion(),
      connection: {
        mode: demo() ? "synthetic" : config()?.mode === "remote" ? "remote" : "unconfigured",
        role: role(),
        server: origin(),
        source: entrySource(),
        shared: shared(),
      },
      grantCandidates: pilotGrantCandidates(),
      context: currentContext,
      existingManager: existingManagerStatus(),
      existingLogins: existingLogins(),
      account: accountState(),
      testingAllowed: isolatedProfile,
      connectionNotice,
      offline,
      owner,
      installation_id: queue.installation(),
      activity:{clock_quality:clock?'anchored':'unconfirmed',idle_threshold_seconds:120,last_confirmed_at:lastConfirmedAt},
    }));
    safeHandler('crm:ui-ready',()=>{rendererReady=true;diagnostic('renderer','renderer_ready',true);return {ok:true};});
    safeHandler('crm:update',async(action:string)=>{
      if(action==='state')return updateState();
      if(action==='check')return checkUpdates();
      if(action!=='install')throw Error('Неизвестное действие обновления.');
      if(activeRequests||installingUpdate||exportRecovery)
        return {ok:false,text:'Дождитесь завершения текущей операции и повторите.'};
      installingUpdate=true;
      try{
        if(await analystRuntime.busy())
          return {ok:false,text:'Дождитесь завершения операции Аналитика и повторите.'};
        const result=await installUpdate(async()=>!exportRecovery,()=>{activeTime.stop();quitting=true;});
        if (!result.ok) quitting=false;
        return result;
      }finally{installingUpdate=false;}
    });
    if(STABLE_CRM&&!isolatedProfile)startUpdates(
      update=>{if(win&&!win.isDestroyed())win.webContents.send('crm:update-state',update);},
      ()=>diagnostic('update','update_check_failed',false)
    );
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
        const freshOnly=action==='operator-status'||action==='changes'||action.startsWith('assignment-')||action.startsWith('logistics')||action==='managed-portfolio'||action==='manager-statistics'||action==='manager-report'||action==='analyst-report'||action==='analyst-statistics';
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
      queue.enqueue(authority, body, 'registry-edit', currentContext);
      void queue.flush(authority, send, () => owner, () => currentContext);
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
        void queue.flush(authority, send, () => owner, () => currentContext);
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
      queue.enqueue(authority, body, undefined, currentContext);
      void queue.flush(authority, send, () => owner, () => currentContext);
      return { ok: true, queued: true, operation_id: body.operation_id };
    });
    safeHandler("crm:queue", async (action: string, id: string,expectedOwner?:string) => {
      if (!owner) throw new Error("Нет входа.");
      if(expectedOwner!==undefined && owner!==expectedOwner)throw new Error('Личность очереди изменилась.');
      if(action==='review-registry')queue.reviewRegistry(owner,id);
      if (action === "retry") queue.retry(owner, id);
      if (action === "flush") await queue.flush(owner, send, () => owner, () => currentContext);
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
      if (input?.action !== 'offline' && await analystRuntime.busy())
        throw Error('Дождитесь завершения текущей операции Аналитика перед сменой входа.');
      if (input?.action !== 'offline') analystRuntime.stop();
      if(['account-login','account-password','account-status','account-logout'].includes(input?.action)){
        const previous=accountState(),previousFingerprint=savedAccountFingerprint();
        const result=await accountAction(input.action,input).catch(error=>{
          if(input.action==='account-status'&&previous.available&&!previous.pending&&
             requireFreshAccountLogin(previousFingerprint,Number(error?.status)))
            return {ok:true,data:{pending:true}};
          throw error;
        });
        if(input.action==='account-password')return result;
        if(input.action==='account-status'&&!accountState().available)return result;
        forgetAuthority('');
        writeSettings({crmConnection:undefined,crmLast:undefined,crmExplicitEntry:true});
        if(input.action==='account-logout'||(result.data as any).pending)return result;
        const fingerprint=savedAccountFingerprint();
        try{await pilotCredential(undefined,true,'crm',input.grant_id);}
        catch(error){
          if(requireFreshAccountLogin(fingerprint,Number((error as any)?.status)))
            return {ok:true,data:{pending:true}};
          throw error;
        }
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
        await pilotCredential(undefined, true, source, input.grant_id);
        writeSettings({crmConnection: {mode: "remote", role: source === 'manager' || source === 'analyst' ? source : 'employee', source,
          server: PILOT_BASE, auth: "existing-login"}, crmLast: undefined});
        connectionNotice = "";
      } else if (input?.mode === "synthetic") {
        if(!isolatedProfile)throw new Error('Проверочная база доступна только в отдельном проверочном профиле.');
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
            "Для рабочего сервера используйте форму «Войти в CRM».",
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
      if (action === 'logistics-read' || action === 'logistics-export' || action === 'logistics-export-download') {
        const authority = owner;
        if (!authority || !nativeCapability(currentContext,'workbook-logistics'))
          throw new Error('Сервер не выдал полномочие работы с курьерскими книгами.');
        if (action === 'logistics-export-download') {
          const result = await send('GET', action, undefined, input);
          if (!result.ok) return result;
          if (owner !== authority) throw new Error('Учётная запись изменилась.');
          const {filename,content_base64,sha256}=result.data;
          const bytes=Buffer.from(content_base64,'base64');
          if(createHash('sha256').update(bytes).digest('hex')!==sha256)throw Error('Проверка файла выгрузки не прошла.');
          const choice=await dialog.showSaveDialog(win!,{title:'Выгрузить курьерский пул',defaultPath:basename(filename),filters:[{name:'Excel',extensions:['xlsx']}]});
          if(choice.canceled||!choice.filePath)return {ok:false,cancelled:true};
          if(owner!==authority)throw new Error('Учётная запись изменилась.');
          writeFileSync(choice.filePath,bytes,{flag:'wx',flush:true});
          return {ok:true,data:{filename,rows:result.data.rows}};
        }
        if (action === 'logistics-read') {
          const choice = await dialog.showOpenDialog(win!, {
            properties: ['openFile'], filters: [{name: 'Реестры', extensions: ['xlsx', 'xlsm', 'xlsb']}],
          });
          if (choice.canceled || !choice.filePaths[0]) return {ok: false, cancelled: true};
          if (owner !== authority) throw new Error('Учётная запись изменилась.');
          const result = await local('logistics-workbook-read', {path: choice.filePaths[0]});
          if (owner !== authority) throw new Error('Учётная запись изменилась.');
          if (result.ok) result.data.filename = basename(choice.filePaths[0]);
          return result;
        }
        const choice = await dialog.showSaveDialog(win!, {
          title: 'Выгрузить курьерский пул', defaultPath: 'Курьерский пул.xlsx',
          filters: [{name: 'Excel', extensions: ['xlsx']}],
        });
        if (choice.canceled || !choice.filePath) return {ok: false, cancelled: true};
        if (owner !== authority) throw new Error('Учётная запись изменилась.');
        const result = await send('GET', 'logistics-export', undefined, input);
        if (!result.ok) return result;
        if (owner !== authority) throw new Error('Учётная запись изменилась.');
        return local('logistics-workbook-write', {path: choice.filePath, snapshot: result.data});
      }
      if(action==='team-visits'||action==='report-preset'){
        const authority=owner;
        if(!authority || !nativeCapability(currentContext,'workbook-manager-report'))
          throw Error('Сервер не выдал полномочие выгрузки отчётов руководителя.');
        const result=action==='report-preset'
          ? await send('GET','manager-report-download',undefined,input)
          : await send('GET','manager-statistics',undefined,{...input,download:'visits'});
        if(!result.ok)return result;
        if(owner!==authority)throw new Error('Вход изменён. Повторите выгрузку.');
        const {filename,content_base64,sha256}=result.data;
        const bytes=Buffer.from(content_base64,'base64');
        if(createHash('sha256').update(bytes).digest('hex')!==sha256)throw Error('Проверка файла отчёта не прошла.');
        const choice=await dialog.showSaveDialog(win!,{title:'Сохранить отчёт',defaultPath:basename(filename),filters:[{name:'Excel',extensions:['xlsx']}]});
        if(choice.canceled||!choice.filePath)return {ok:false,cancelled:true};
        if(owner!==authority)throw new Error('Вход изменён. Повторите выгрузку.');
        writeFileSync(choice.filePath,bytes,{flag:'wx',flush:true});
        return {ok:true,data:{filename}};
      }
      if(action==='table'){
        const authority=owner;
        if(!authority || !nativeCapability(currentContext,'workbook-table'))
          throw Error('Сервер не выдал полномочие табличной выгрузки.');
        const clean=(value:unknown)=>typeof value==='string'&&/^[=+\-@]/.test(value)?'\''+value:value;
        const report={headers:input.headers.map(clean),rows:input.rows.map((row:unknown[])=>row.map(clean))};
        const name=String(input.name||'Результаты.xlsx').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_');
        const choice=await dialog.showSaveDialog(win!,{title:'Выгрузить Excel',defaultPath:name,filters:[{name:'Excel',extensions:['xlsx']}]});
        if(choice.canceled||!choice.filePath)return {ok:false,cancelled:true};
        if(owner!==authority)throw new Error('Вход изменён. Повторите выгрузку.');
        return local('report-write',{report,kind:'table',path:choice.filePath});
      }
      if(action==='manager-report'||action==='analyst-report'){
        const authority=owner;
        if(!authority)throw new Error('Сначала подтвердите вход.');
        const month=input?.mode==='month',dynamics=input?.mode==='dynamics';
        const selection=month?{month:input.month}:{country:input?.country||undefined,
          ...(input?.snapshot_id&&!dynamics?{snapshot_id:input.snapshot_id}:{day:input?.day})};
        const result=await send('GET',action==='analyst-report'?(month?'analyst-statistics':'analyst-report'):(month?'manager-statistics':'manager-report'),undefined,selection);
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
        if(!owner || !nativeCapability(currentContext,'workbook-exchange'))
          throw Error('Сервер не выдал полномочие обмена книгами.');
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
      if(!owner || !nativeCapability(currentContext,'workbook-exchange'))
        throw Error('Сервер не выдал полномочие обмена книгами.');
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
        void queue.flush(authority, send, () => owner, () => currentContext);
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
            const token = shared() ? await pilotCredential(currentContext?.employee_uuid, false, entrySource(), currentContext?.grant_id || undefined) :
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
      const current = currentWindow();
      if (!current || event.sender !== current.webContents) return;
      if (action === "close") current.close();
      else if (action === "minimize") current.minimize();
      else if (action === "maximize")
        current.isMaximized() ? current.unmaximize() : current.maximize();
    });
    const startupWindow = win;
    await startupWindow.loadURL("hard://app/index.html");
    if (quitting || startupWindow.isDestroyed()) return;
    let rendererRepairs=0;
    win.webContents.on('render-process-gone',()=>{
      diagnostic('renderer','renderer_stopped',false);
      if(++rendererRepairs<=2){rendererReady=false;win?.webContents.reload();}
      else void showStartupRecovery('renderer_unavailable');
    });
    setTimeout(()=>{if(!rendererReady && win && !win.isDestroyed())void showStartupRecovery('preload_or_renderer_unavailable');},10000).unref();
    const framed=await applyCrmWindowFrame(startupWindow);
    if (quitting || startupWindow.isDestroyed()) return;
    diagnostic('window',framed?'window_frame_applied':'window_frame_unavailable',framed);
    if (!framed && process.platform === "win32")
      await local("window-frame", {
        hwnd: startupWindow.getNativeWindowHandle().readBigUInt64LE().toString(),
      });
    if (quitting || startupWindow.isDestroyed()) return;
    if (process.platform === 'win32' && (STABLE_CRM || product.id === 'crm')) {
      tray = new Tray(join(__dirname, 'icons', 'tray-32.png'));
      tray.setToolTip(product.name);
      tray.on('click', showWindow);
      tray.setContextMenu(Menu.buildFromTemplate([
        {label:'Открыть',click:showWindow},
        {label:'Выход',click:async () => {
          if (activeRequests || await analystRuntime.busy()) {
            showWindow();
            const current = currentWindow();
            if (current) await dialog.showMessageBox(current,{message:'Дождитесь завершения текущей операции.'});
          } else app.quit();
        }}
      ]));
      win.on('close', event => {
        if (!quitting && !installingUpdate) { event.preventDefault(); win?.hide(); }
      });
      win.on('minimize', () => { if (!quitting) win?.hide(); });
    }
    if (!tray || !process.argv.includes('--tray')) win.show();
    win.on('blur',()=>activeTime.stop());win.on('hide',()=>activeTime.stop());
    win.on('minimize',()=>activeTime.stop());win.on('close',()=>activeTime.stop());
    powerMonitor.on('lock-screen',()=>{screenLocked=true;activeTime.stop();});
    powerMonitor.on('unlock-screen',()=>{screenLocked=false;});
    powerMonitor.on('suspend',()=>{sleeping=true;activeTime.stop();clock=null;});
    powerMonitor.on('resume',()=>{sleeping=false;void identify().catch(()=>{});});
    const activeTimeError='Не удалось сохранить активный интервал. Проверьте защищённый профиль.';
    setInterval(()=>{
      try{activeTime.tick();if(connectionNotice===activeTimeError)connectionNotice='';}
      catch{connectionNotice=activeTimeError;}
    },2000).unref();
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
        void queue.flush(owner, send, () => owner, () => currentContext).catch(() => {});
        void queue.flushTechnical(owner, send, () => owner).catch(() => {});
      }
    }, 7000).unref();

  })
  .catch((error) => {
    if(quitting || win?.isDestroyed())return;
    diagnostic('startup','startup_failed',false);
    void showStartupRecovery('startup_failed');
  });
app.on("second-instance", (_event, argv) => { if (!argv.includes('--tray')) showWindow(); });
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  quitting = true;
  try{activeTime.stop();}catch{/* The last protected checkpoint remains recoverable. */}
  backend?.kill();
});
