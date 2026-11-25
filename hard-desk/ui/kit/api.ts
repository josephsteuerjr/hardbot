// Обёртка над мостом окна. Страница не знает ни токена, ни адреса сервера: она
// просит главный процесс, а тот ходит в сеть и отвечает готовым результатом.

export interface ApiResult<T = any> {
  ok: boolean;
  status: number;
  data?: T;
  kind?: "offline" | "cert" | "revoked" | "forbidden" | "server" | "notfound" | "nolink";
  text?: string;
  stale?: string;
}

export interface Shell {
  platform?: string;
  mode: "manager" | "analyst" | "field";
  productId?: "hard" | "soft" | "manager" | "analyst";
  fixedProduct?: boolean;
  kit?: boolean;
  product: string;
  version: string;
  server: string;
  device: { id?: number; name: string; created_at?: string; employee?: string; username?: string; role?: string; telegram_id?: number } | null;
  enrolled: boolean;
  theme: "light" | "dark" | "system";
  deviceName: string;
  maximized: boolean;
}

type Q = Record<string, unknown>;

interface Bridge {
  state(): Promise<Shell>;
  edit(action: string): void;
  update(action: "state" | "check" | "install"): Promise<any>;
  migrate(): Promise<{ ok: boolean; text?: string }>;
  /** Один exe — все роли: сменить режим («manager»/«field»/«soft»/«analyst»). */
  setMode(mode: string): Promise<{ ok: boolean; text?: string }>;
  enroll(link: string): Promise<ApiResult>;
  /** Вход сотрудника по логину и паролю: main собирает отпечаток устройства
   *  и пишет полевой токен; рендерер пароль не хранит. */
  accountLogin(server: string, username: string, password: string): Promise<ApiResult>;
  accountStaff(server: string): Promise<ApiResult>;
  accountRegister(server: string, employee: string, username: string, password: string): Promise<ApiResult>;
  /** Privileged manager/security/full entry; never grants analyst /admin access. */
  accountManagerLogin(server: string, username: string, password: string): Promise<ApiResult>;
  logout(): Promise<boolean>;
  api(method: string, path: string, query?: Q, body?: unknown): Promise<ApiResult>;
  image(path: string, query?: Q): Promise<string | null>;
  upload(path: string, query: Q, filePath: string, ext: string): Promise<ApiResult>;
  download(path: string, query: Q | undefined, name: string): Promise<{ ok: boolean; file?: string; text?: string }>;
  open(file: string): Promise<string>;
  copy(text: string): Promise<boolean>;
  saveText(name: string, content: string): Promise<{ok: boolean; text?: string}>;
  theme(t: string): Promise<boolean>;
  shot(file: string): Promise<boolean>;
  local(path: string, body?: unknown): Promise<{ ok: boolean; data?: any; error?: string }>;
  pick(kind: "file" | "folder" | "save", opts?: { title?: string; filters?: { name: string; extensions: string[] }[]; defaultPath?: string }): Promise<string>;
  xlsx(name: string, headers: string[], rows: (string | number | null)[][]): Promise<{ ok: boolean; file?: string; text?: string }>;
  /** Запуск вместе с Windows (свёрнутым к часам). Без аргумента — узнать. */
  autostart(on?: boolean): Promise<boolean>;
  win(action: string): void;
  on(name: "revoked" | "win" | "update", cb: (arg?: unknown) => void): void;
}

const bridge: Bridge = (window as unknown as { hard: Bridge }).hard;
let employeeProfile: "hard" | "soft" | undefined;

/** Keep both portfolios in the existing employee session. */
export function setEmployeeProfile(profile: "hard" | "soft"): void { employeeProfile = profile; }

function employeeQuery(path: string, query?: Q): Q | undefined {
  if (!employeeProfile || !path.startsWith("/api/") || path.startsWith("/api/local/")) return query;
  const explicit = query?.mode || query?.profile;
  return { ...query, mode: explicit === "hard" || explicit === "soft" ? explicit : employeeProfile };
}

export const hard: Bridge = {
  ...bridge,
  api: (method, path, query, body) => bridge.api(method, path, employeeQuery(path, query), body),
  image: (path, query) => bridge.image(path, employeeQuery(path, query)),
  upload: (path, query, file, ext) => bridge.upload(path, employeeQuery(path, query) || {}, file, ext),
};

type Listener = (s: { level: "live" | "warn" | "off" | "busy"; text: string }) => void;
/** Explicit CRM host adapter; main authorizes every call independently. */
const embeddedBridges:Partial<Bridge>[]=[];
const embeddedBase=new Map<string,any>();
export function setEmbeddedBridge(overrides: Partial<Bridge>): () => void {
  const layer={...overrides};
  for(const key of Object.keys(layer))if(!embeddedBase.has(key))embeddedBase.set(key,(hard as any)[key]);
  embeddedBridges.push(layer);Object.assign(hard,layer);
  return()=>{
    const index=embeddedBridges.indexOf(layer);if(index<0)return;
    embeddedBridges.splice(index,1);
    // A slow old screen can finish after its replacement has installed a bridge.
    for(const key of Object.keys(layer)){
      const current=[...embeddedBridges].reverse().find(value=>Object.hasOwn(value,key));
      (hard as any)[key]=current?(current as any)[key]:embeddedBase.get(key);
      if(!current)embeddedBase.delete(key);
    }
  };
}

const listeners = new Set<Listener>();
export function onLink(fn: Listener): void { listeners.add(fn); }
let lostAt = "";
let busy = 0;

function tell(level: "live" | "warn" | "off" | "busy", text: string) {
  for (const fn of listeners) fn({ level, text });
}

export async function api<T = any>(path: string, query?: Q, method = "GET", body?: unknown): Promise<ApiResult<T>> {
  // A locally protected draft/settings write says nothing about server reachability.
  if (path.startsWith("/api/local/")) return hard.api(method,path,query,body);
  busy++;
  tell("busy", "Обновляю…");
  let r: ApiResult<T>;
  try {
    r = await hard.api(method, path, query, body);
  } finally {
    busy--;
  }
  if (r.kind === "offline" || r.kind === "cert") {
    if (!lostAt) lostAt = new Date().toTimeString().slice(0, 5);
    tell("off", r.kind === "cert" ? "Сеть подменяет соединение" : `Нет связи с ${lostAt}`);
  } else if (r.kind === "revoked") {
    tell("off", "Доступ отозван");
  } else {
    lostAt = "";
    if (!busy) tell("live", "На связи · " + new Date().toTimeString().slice(0, 5));
  }
  return r;
}
