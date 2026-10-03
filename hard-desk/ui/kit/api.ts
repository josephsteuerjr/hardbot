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
  mode: "manager" | "analyst" | "field";
  kit?: boolean;
  product: string;
  version: string;
  server: string;
  device: { id: number; name: string; created_at?: string } | null;
  enrolled: boolean;
  theme: "light" | "dark" | "system";
  deviceName: string;
  maximized: boolean;
}

type Q = Record<string, unknown>;

interface Bridge {
  state(): Promise<Shell>;
  /** Один exe — все роли: сменить режим («manager»/«field»/«soft»/«analyst»). */
  setMode(mode: string): Promise<{ ok: boolean; text?: string }>;
  enroll(link: string): Promise<ApiResult>;
  logout(): Promise<boolean>;
  api(method: string, path: string, query?: Q, body?: unknown): Promise<ApiResult>;
  image(path: string, query?: Q): Promise<string | null>;
  upload(path: string, query: Q, filePath: string, ext: string): Promise<ApiResult>;
  download(path: string, query: Q | undefined, name: string): Promise<{ ok: boolean; file?: string; text?: string }>;
  open(file: string): Promise<string>;
  copy(text: string): Promise<boolean>;
  theme(t: string): Promise<boolean>;
  shot(file: string): Promise<boolean>;
  local(path: string, body?: unknown): Promise<{ ok: boolean; data?: any; error?: string }>;
  pick(kind: "file" | "folder" | "save", opts?: { title?: string; filters?: { name: string; extensions: string[] }[]; defaultPath?: string }): Promise<string>;
  xlsx(name: string, headers: string[], rows: (string | number | null)[][]): Promise<{ ok: boolean; file?: string; text?: string }>;
  /** Запуск вместе с Windows (свёрнутым к часам). Без аргумента — узнать. */
  autostart(on?: boolean): Promise<boolean>;
  win(action: string): void;
  on(name: "revoked" | "win", cb: (arg?: unknown) => void): void;
}

export const hard: Bridge = (window as unknown as { hard: Bridge }).hard;

type Listener = (s: { level: "live" | "warn" | "off" | "busy"; text: string }) => void;
const listeners = new Set<Listener>();
export function onLink(fn: Listener): void { listeners.add(fn); }
let lostAt = "";
let busy = 0;

function tell(level: "live" | "warn" | "off" | "busy", text: string) {
  for (const fn of listeners) fn({ level, text });
}

export async function api<T = any>(path: string, query?: Q, method = "GET", body?: unknown): Promise<ApiResult<T>> {
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
