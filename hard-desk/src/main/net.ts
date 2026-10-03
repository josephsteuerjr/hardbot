// Связь с сервером «Хард». Вся сеть — здесь, в главном процессе: токен компьютера
// в страницу не попадает никогда. Мимо системного прокси (на машинах офиса бывает
// HTTPS_PROXY на заокеанский прокси, который не работает), сертификат проверяет
// Chromium по хранилищу Windows — повтора «без проверки» нет.
import { session, type Session } from "electron";
import { readFileSync } from "node:fs";
import { hostname, userInfo } from "node:os";
import { cacheGet, cachePut, readFieldToken, readSettings, readToken, wipe, wipeField, writeFieldToken, writeSettings, writeToken } from "./store";

export const OFFLINE = "Нет связи с сервером «Хард». Как только интернет появится, пульт догонит сам.";
export const CERT_BAD = "Сертификат сервера не прошёл проверку — эта сеть подменяет соединение. " +
  "Пульт ничего не отправил. Подключитесь к другой сети.";
export const REVOKED = "Доступ этого компьютера отозван. Попросите у аналитика новую ссылку входа.";

export interface ApiResult {
  ok: boolean;
  status: number;
  data?: unknown;
  kind?: "offline" | "cert" | "revoked" | "forbidden" | "server" | "notfound" | "nolink";
  text?: string;
  stale?: string;            // данные из кэша: когда их получили
}

let ses: Session | null = null;
function net(): Session {
  if (!ses) {
    ses = session.fromPartition("persist:hard-net");
    void ses.setProxy({ mode: "direct" });
  }
  return ses;
}

export function deviceName(): string {
  let user = "";
  try { user = userInfo().username; } catch { /* */ }
  return [hostname(), user].filter(Boolean).join(" · ").slice(0, 80);
}

/** Ссылка входа → (адрес сервера, код). Вставляют что придётся: ссылку целиком,
 *  ссылку с решёткой из старой рассылки или один код. */
export function splitLink(raw: string): { base: string; code: string } | null {
  const text = String(raw || "").trim().replace(/^[<"']+|[>"']+$/g, "");
  if (!text) return null;
  let base = "";
  let code = "";
  const m = /^(https?:\/\/[^/?#\s]+)/i.exec(text);
  if (m) base = m[1].replace(/^http:\/\/(?!127\.0\.0\.1|localhost)/i, "https://");
  const c = /\/i\/([A-Za-z0-9_-]{6,})/.exec(text) || /[#&?]link=([A-Za-z0-9_-]{6,})/.exec(text) || /^([A-Za-z0-9_-]{6,})$/.exec(text);
  if (c) code = c[1];
  if (!code) return null;
  return { base, code };
}

function qs(query?: Record<string, unknown>): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? "?" + s : "";
}

function troubleOf(err: unknown): ApiResult {
  const text = String((err as Error)?.message || err || "").toLowerCase();
  if (text.includes("cert")) return { ok: false, status: 0, kind: "cert", text: CERT_BAD };
  return { ok: false, status: 0, kind: "offline", text: OFFLINE };
}

async function errorCode(res: Response): Promise<string> {
  try {
    const t = await res.text();
    try { return String((JSON.parse(t) as { error?: string }).error || t); } catch { return t; }
  } catch {
    return "";
  }
}

type Revoked = () => void;
let onRevoked: Revoked = () => {};
export function setRevokedHandler(fn: Revoked): void { onRevoked = fn; }

export async function call(method: string, path: string, query?: Record<string, unknown>, body?: unknown,
                           opts: { binary?: boolean; cache?: boolean } = {}): Promise<ApiResult> {
  const server = readSettings().server;
  const token = readToken();
  if (!server || !token) return { ok: false, status: 0, kind: "nolink", text: "Пульт ещё не знает, кто вы." };
  if (!/^\/api\/(manager|control)\//.test(path)) return { ok: false, status: 0, kind: "server", text: "запрещённый путь" };
  const url = server + path + qs(query);
  const key = method + " " + path + qs(query);
  let res: Response;
  try {
    res = await net().fetch(url, {
      method,
      headers: { "X-Pult-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      bypassCustomProtocolHandlers: true,
    } as RequestInit);
  } catch (err) {
    const t = troubleOf(err);
    if (method === "GET" && opts.cache !== false && !opts.binary) {
      const hit = cacheGet(key);
      if (hit) return { ...t, ok: true, status: 200, data: hit.data, stale: hit.at };
    }
    return t;
  }
  if (res.status === 401) {
    const code = await errorCode(res);
    if (code === "device_revoked" || code === "device_unknown") {
      wipe();
      onRevoked();
      return { ok: false, status: 401, kind: "revoked", text: REVOKED };
    }
    return { ok: false, status: 401, kind: "revoked", text: REVOKED };
  }
  if (res.status === 403) {
    const code = await errorCode(res);
    const text = code === "read_only" ? "У безопасника доступ только на чтение."
      : code === "no_control" ? "Раздел «Контроль» — только для безопасности."
      : code && code !== "not a manager" && !code.startsWith("<") ? code
      : "Роль руководителя снята. Если это ошибка — напишите аналитику.";
    return { ok: false, status: 403, kind: "forbidden", text };
  }
  if (res.status === 404) return { ok: false, status: 404, kind: "notfound", text: "Не найдено — возможно, человека перезакрепили." };
  if (!res.ok) {
    const code = await errorCode(res);
    return { ok: false, status: res.status, kind: "server", text: `Сервер ответил ошибкой ${res.status}${code ? ": " + code.slice(0, 200) : ""}` };
  }
  if (opts.binary) {
    const buf = Buffer.from(await res.arrayBuffer());
    return { ok: true, status: res.status, data: { type: res.headers.get("content-type") || "", buf } };
  }
  const data = await res.json().catch(() => null);
  if (method === "GET" && opts.cache !== false) cachePut(key, data);
  return { ok: true, status: res.status, data };
}

// Отказ по ссылке — словами и с путём (30.09.2026): руководители вставляли
// ссылки от 24.09, потраченные телефоном и ярлыком, и видели только «устарела».
const NEW_LINK = "Новую возьмите в приложении на телефоне: Сводка → «Вход команды» → «Выдать себе» — или попросите у аналитика.";
export function deadLinkText(reason: string): string {
  if (reason === "used_up") return "По этой ссылке уже вошли столько раз, сколько она позволяет — телефон и ярлык на экране тоже считаются. " + NEW_LINK;
  if (reason === "expired") return "Ссылке больше недели — она уже не действует. " + NEW_LINK;
  return "Эта ссылка не действует — возможно, её уже заменили новой. " + NEW_LINK;
}

async function deadReason(res: Response): Promise<string> {
  try { return String(((await res.json()) as { reason?: string }).reason || ""); } catch { return ""; }
}

/** Ключ → токен компьютера. Сервер берём из ссылки; голый код — к прежнему серверу. */
export async function enroll(raw: string): Promise<ApiResult> {
  const link = splitLink(raw);
  if (!link) return { ok: false, status: 0, kind: "nolink", text: "Это не похоже на ссылку входа — вставьте её целиком." };
  const base = link.base || readSettings().server || "";
  if (!base) return { ok: false, status: 0, kind: "nolink", text: "В ссылке нет адреса сервера — вставьте её целиком." };
  let res: Response;
  try {
    res = await net().fetch(base + "/api/manager/device/enroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: link.code, device: deviceName() }),
    });
  } catch (err) {
    return troubleOf(err);
  }
  if (res.status === 410) return { ok: false, status: 410, kind: "nolink", text: deadLinkText(await deadReason(res)) };
  if (res.status === 403) return { ok: false, status: 403, kind: "forbidden", text: "Это ссылка выездника, а не руководителя. Её место — в телефоне." };
  if (res.status === 429) return { ok: false, status: 429, kind: "server", text: "Слишком много попыток. Подождите минуту." };
  if (!res.ok) return { ok: false, status: res.status, kind: "server", text: `Сервер ответил ошибкой ${res.status}.` };
  const data = (await res.json()) as { token: string; device_id: number };
  writeToken(data.token);
  writeSettings({ server: base, device: { id: data.device_id, name: deviceName(), created_at: new Date().toISOString() } });
  return { ok: true, status: 200, data: { ...data, token: undefined } };
}

export async function logout(): Promise<void> {
  const server = readSettings().server;
  const token = readToken();
  if (server && token) {
    try {
      await net().fetch(server + "/api/manager/device/logout", { method: "POST", headers: { "X-Pult-Token": token } });
    } catch { /* нет связи — токен всё равно стираем у себя, аналитик отзовёт */ }
  }
  wipe();
}

// --- Приложение выездника/софтника на ПК: свой класс токена (agent_devices) ----
// Телефон не вытесняется: вход по ссылке выездника даёт ПК-токен, привязка
// agent_bind остаётся телефону. Белый список путей — только ручки выездника.
// Хвост «(/.*)?»: путь зовётся и как «/api/save», и как «/api/photo/xyz» —
// строгая дробь после имени не пропускала «/api/bootstrap» и «/api/save»,
// и приложение выездника на сборке 03.10 не открывало портфель вовсе.
const FIELD_PATH = /^\/api\/(bootstrap|person|cards|save|photo|receipt|markers|notes|note|lang|route_to|geocode|geo)(\/.*)?$/;
const FIELD_PATH_EXACT = new Set(["/api/person", "/api/cards", "/api/markers", "/api/notes",
                                  "/api/geocode", "/api/logout", "/api/bootstrap", "/api/save",
                                  "/api/receipt", "/api/photo", "/api/note"]);

function fieldOk(path: string): boolean {
  return FIELD_PATH.test(path) || FIELD_PATH_EXACT.has(path);
}

export async function enrollField(raw: string): Promise<ApiResult> {
  const link = splitLink(raw);
  if (!link) return { ok: false, status: 0, kind: "nolink", text: "Это не похоже на ссылку входа — вставьте её целиком." };
  const base = link.base || readSettings().fieldServer || readSettings().server || "";
  if (!base) return { ok: false, status: 0, kind: "nolink", text: "В ссылке нет адреса сервера — вставьте её целиком." };
  let res: Response;
  try {
    res = await net().fetch(base + "/api/agent/device/enroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: link.code, device: deviceName() }),
    });
  } catch (err) {
    return troubleOf(err);
  }
  if (res.status === 410) return { ok: false, status: 410, kind: "nolink", text: deadLinkText(await deadReason(res)) };
  if (res.status === 403) return { ok: false, status: 403, kind: "forbidden", text: "Это ссылка руководителя или аналитика. Для приложения выездника нужна ссылка из «Вход команды» на ваше ФИО." };
  if (res.status === 429) return { ok: false, status: 429, kind: "server", text: "Слишком много попыток. Подождите минуту." };
  if (!res.ok) return { ok: false, status: res.status, kind: "server", text: `Сервер ответил ошибкой ${res.status}.` };
  const data = (await res.json()) as { token: string; device_id: number; employee: string };
  writeFieldToken(data.token);
  writeSettings({ fieldServer: base, fieldDevice: { id: data.device_id, name: deviceName(), employee: data.employee } });
  return { ok: true, status: 200, data: { ...data, token: undefined } };
}

export async function callField(method: string, path: string, query?: Record<string, unknown>, body?: unknown,
                                opts: { binary?: boolean; cache?: boolean } = {}): Promise<ApiResult> {
  const server = readSettings().fieldServer;
  const token = readFieldToken();
  if (!server || !token) return { ok: false, status: 0, kind: "nolink", text: "Приложение ещё не знает, кто вы." };
  if (!fieldOk(String(path))) return { ok: false, status: 0, kind: "server", text: "запрещённый путь" };
  const url = server + path + qs(query);
  const key = "F " + method + " " + path + qs(query);
  let res: Response;
  try {
    res = await net().fetch(url, {
      method,
      headers: { "X-App-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      bypassCustomProtocolHandlers: true,
    } as RequestInit);
  } catch (err) {
    const t = troubleOf(err);
    if (method === "GET" && opts.cache !== false && !opts.binary) {
      const hit = cacheGet(key);
      if (hit) return { ...t, ok: true, status: 200, data: hit.data, stale: hit.at };
    }
    return t;
  }
  if (res.status === 401) {
    wipeField();
    onRevoked();
    return { ok: false, status: 401, kind: "revoked", text: REVOKED };
  }
  if (res.status === 404) return { ok: false, status: 404, kind: "notfound", text: "Не найдено — возможно, договор перезакрепили." };
  if (!res.ok) {
    const code = await errorCode(res);
    return { ok: false, status: res.status, kind: "server", text: `Сервер ответил ошибкой ${res.status}${code ? ": " + code.slice(0, 200) : ""}` };
  }
  if (opts.binary) {
    const buf = Buffer.from(await res.arrayBuffer());
    return { ok: true, status: res.status, data: { type: res.headers.get("content-type") || "", buf } };
  }
  const data = await res.json().catch(() => null);
  if (method === "GET" && opts.cache !== false) cachePut(key, data);
  return { ok: true, status: res.status, data };
}

/** Загрузка файла чека: сырые байты + поля в query (как телефон, X-Receipt-Ext). */
export async function uploadField(path: string, query: Record<string, unknown>, filePath: string,
                                  ext: string): Promise<ApiResult> {
  const server = readSettings().fieldServer;
  const token = readFieldToken();
  if (!server || !token) return { ok: false, status: 0, kind: "nolink", text: "Приложение ещё не знает, кто вы." };
  if (!fieldOk(String(path))) return { ok: false, status: 0, kind: "server", text: "запрещённый путь" };
  let buf: Buffer;
  try {
    buf = readFileSync(filePath);
  } catch {
    return { ok: false, status: 0, kind: "server", text: "Файл не читается — возможно, его уже переместили." };
  }
  let res: Response;
  try {
    res = await net().fetch(server + path + qs(query), {
      method: "POST",
      headers: { "X-App-Token": token, "Content-Type": "application/octet-stream", "X-Receipt-Ext": ext || ".jpg" },
      body: buf,
      bypassCustomProtocolHandlers: true,
    } as RequestInit);
  } catch (err) {
    return troubleOf(err);
  }
  if (res.status === 401) {
    wipeField();
    onRevoked();
    return { ok: false, status: 401, kind: "revoked", text: REVOKED };
  }
  if (!res.ok) {
    const code = await errorCode(res);
    return { ok: false, status: res.status, kind: "server", text: `Чек не принят (${res.status}${code ? ": " + code.slice(0, 120) : ""}). Возьмите другой снимок и попробуйте ещё раз.` };
  }
  return { ok: true, status: res.status, data: await res.json().catch(() => ({})) };
}

export async function logoutField(): Promise<void> {
  const server = readSettings().fieldServer;
  const token = readFieldToken();
  if (server && token) {
    try {
      await net().fetch(server + "/api/agent/device/logout", { method: "POST", headers: { "X-App-Token": token } });
    } catch { /* нет связи — токен всё равно стираем, аналитик отзовёт */ }
  }
  wipeField();
}
