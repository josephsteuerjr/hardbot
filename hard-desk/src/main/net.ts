// Связь с сервером «Хард». Вся сеть — здесь, в главном процессе: токен компьютера
// в страницу не попадает никогда. Мимо системного прокси (на машинах офиса бывает
// HTTPS_PROXY на заокеанский прокси, который не работает), сертификат проверяет
// Chromium по хранилищу Windows — повтора «без проверки» нет.
import { app, session, type Session } from "electron";
import { readFileSync } from "node:fs";
import { hostname, userInfo } from "node:os";
import { createHash, randomBytes, sign } from "node:crypto";
import { preparedEnroll, readDraft, writeDraft, pcIdentity } from "./store";
import { cacheGeneration, cacheGet, cachePut, cacheRemove, clearCache, readFieldToken, readSettings, readToken, wipe, wipeField, writeFieldToken, writeSettings, writeToken } from "./store";
import { clearCallQueue, readCallQueue, writeCallQueue, type CallQueue } from "./store";
import { serverOrigin, splitLink } from "./link";
import { fetchBuffered } from "./transport";
import { PRODUCT_ID } from './product';
export { splitLink } from "./link";

export const OFFLINE = "Нет связи с сервером «Хард». Как только интернет появится, пульт догонит сам.";
export const CERT_BAD = "Сертификат сервера не прошёл проверку — эта сеть подменяет соединение. " +
  "Пульт ничего не отправил. Подключитесь к другой сети.";
export const REVOKED = "Доступ этого компьютера отозван. Войдите в личный профиль и запросите подтверждение устройства.";

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

function request(url: string, init: RequestInit, timeoutMs = 30000): Promise<Response> {
  const query = new URLSearchParams(url.split("?")[1] || "");
  let product = query.get("mode") || query.get("profile");
  // Durable office/call operations keep their own product when the employee
  // changes the visible portfolio or restarts before a queue is sent.
  if (typeof init.body === "string") {
    try {
      const kind = JSON.parse(init.body).kind;
      if (kind === "office") product = "hard";
      else if (kind === "call" || kind === "payment") product = "soft";
    } catch { /* A non-JSON request keeps its explicit/default product. */ }
  }
  const workProduct = product === "hard" || product === "soft" ? product : PRODUCT_ID==='crm'?'hard':PRODUCT_ID || "hard";
  const headers = { ...(init.headers as Record<string,string> || {}), "X-Client-Version": app.getVersion?.() || "3.2.0", 'X-Work-Product': workProduct };
  init = { ...init, headers };
  return fetchBuffered((target, options) => net().fetch(target, options), url, init, timeoutMs);
}

function cacheIdentity(server: string, token: string): string {
  return createHash("sha256").update(server + "\n" + token).digest("hex");
}

function acceptScope(data: any, identity: string): void {
  if (!data?.scope_version) return;
  const settings = readSettings();
  const versions = (settings.accessVersions || {}) as Record<string, string>;
  if (versions[identity] && versions[identity] !== data.scope_version) clearCache();
  if (versions[identity] !== data.scope_version) writeSettings({ accessVersions: { ...versions, [identity]: data.scope_version } });
}

export function deviceName(): string {
  let user = "";
  try { user = userInfo().username; } catch { /* */ }
  return [hostname(), user].filter(Boolean).join(" · ").slice(0, 80);
}

/** Ссылка входа → (адрес сервера, код). Вставляют что придётся: ссылку целиком,
 *  ссылку с решёткой из старой рассылки или один код. */
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
    try { const data = JSON.parse(t) as { message?: string; error?: string }; return String(data.message || data.error || t); } catch { return t; }
  } catch {
    return "";
  }
}

type Revoked = () => void;
let onRevoked: Revoked = () => {};
export function setRevokedHandler(fn: Revoked): void { onRevoked = fn; }

const changedAuthority = (): ApiResult => ({ ok: false, status: 409, kind: "nolink",
  text: "Вход или права изменились. Откройте данные заново." });

function localPreferences(method: string, query: Record<string, unknown> | undefined, body: unknown, owner: string): ApiResult {
  try {
    const params = (method === "POST" ? body : query) as { profile?: string; id?: string; value?: unknown };
    if (!params || !["hard", "soft"].includes(params.profile || "") || !/^[a-z0-9_-]{1,80}$/.test(params.id || ""))
      throw new Error("Некорректные настройки");
    const key = owner + "\npreferences\n" + params.profile + "\n" + params.id;
    if (method === "POST") writeDraft(key, params.value ?? null);
    else if (method !== "GET") throw new Error("method");
    return { ok: true, status: 200, data: { value: readDraft(key) } };
  } catch (error) { return { ok: false, status: 0, kind: "server", text: String(error) }; }
}

export async function call(method: string, path: string, query?: Record<string, unknown>, body?: unknown,
                           opts: { binary?: boolean; cache?: boolean; revalidated?: boolean } = {}): Promise<ApiResult> {
  const server = readSettings().server;
  const token = readToken();
  if (!server || !token) return { ok: false, status: 0, kind: "nolink", text: "Пульт ещё не знает, кто вы." };
  if (path === "/api/local/preferences") {
    return localPreferences(method, query, body, "manager\n" + cacheIdentity(server, token));
  }
  if (path === "/api/manager/account_phone_request") {
    // A legacy link is still a valid manager bearer in compatibility mode;
    // never offer this one-time code request from that local session. Server
    // independently checks account-linked, active principal and team scope.
    const device = readSettings().device;
    const id = (body as { device_id?: unknown } | null)?.device_id;
    if (method !== "POST" || !device?.username || !["manager", "full"].includes(device.role || "") ||
        !Number.isSafeInteger(id) || Number(id) <= 0 || !body || Object.keys(body).length !== 1)
      return { ok: false, status: 403, kind: "forbidden", text: "Нужен подтверждённый вход руководителя по учётке и ожидающий телефон своей команды." };
  }
  // Only self-service account paths are admitted; /admin remains a separate device authority.
  const accountPath = (method === "POST" && path === "/api/account/password") ||
    (method === "GET" && path === "/api/account/sessions");
  if (!accountPath && !/^\/api\/(manager|control)\//.test(path)) return { ok: false, status: 0, kind: "server", text: "запрещённый путь" };
  const url = server + path + qs(query);
  const key = cacheIdentity(server, token) + " " + method + " " + path + qs(query);
  let generation = cacheGeneration();
  const samePrincipal = () => readSettings().server === server && readToken() === token;
  const current = () => samePrincipal() && generation === cacheGeneration();
  const invalidated = (): Promise<ApiResult> | ApiResult => method === "GET" && samePrincipal() && !opts.revalidated
    ? call(method, path, query, body, { ...opts, cache: false, revalidated: true }) : changedAuthority();
  let res: Response;
  try {
    res = await request(url, {
      method,
      headers: { "X-Pult-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      bypassCustomProtocolHandlers: true,
    } as RequestInit, opts.binary ? 120000 : 30000);
  } catch (err) {
    if (!current()) return invalidated();
    const t = troubleOf(err);
    if (method === "GET" && opts.cache !== false && !opts.binary) {
      const hit = cacheGet(key);
      if (hit) return { ...t, ok: true, status: 200, data: hit.data, stale: hit.at };
    }
    return t;
  }
  if (!current()) return invalidated();
  if (res.status === 401) {
    const code = await errorCode(res);
    if (!current()) return invalidated();
    if (accountPath) return { ok: false, status: 401, kind: "forbidden", text: "У этого входа нет подтверждённой учётки или сеанс истёк." };
    if (code === "device_revoked" || code === "device_unknown") {
      wipe();
      onRevoked();
      return { ok: false, status: 401, kind: "revoked", text: REVOKED };
    }
    return { ok: false, status: 401, kind: "revoked", text: REVOKED };
  }
  if (res.status === 403) {
    const code = await errorCode(res);
    if (!current()) return invalidated();
    clearCache(); // a known permission denial cannot become an offline grant
    if (code === "account_pending" || code === "password_change_required")
      return { ok: false, status: 403, kind: "forbidden", text: code };
    const text = code === "read_only" ? "У безопасника доступ только на чтение."
      : code === "no_control" ? "Раздел «Контроль» — только для безопасности."
      : code && code !== "not a manager" && !code.startsWith("<") ? code
      : "Роль руководителя снята. Если это ошибка — напишите аналитику.";
    return { ok: false, status: 403, kind: "forbidden", text };
  }
  if (res.status === 404) { cacheRemove(key); return { ok: false, status: 404, kind: "notfound", text: "Не найдено — возможно, человека перезакрепили." }; }
  if (!res.ok) {
    const code = await errorCode(res);
    if (!current()) return invalidated();
    return { ok: false, status: res.status, kind: "server", text: `Сервер ответил ошибкой ${res.status}${code ? ": " + code.slice(0, 200) : ""}` };
  }
  if (opts.binary) {
    const buf = Buffer.from(await res.arrayBuffer());
    if (!current()) return invalidated();
    return { ok: true, status: res.status, data: { type: res.headers.get("content-type") || "", buf } };
  }
  const data = await res.json().catch(() => null);
  if (!current()) return invalidated();
  if (generation === cacheGeneration()) {
    acceptScope(data, cacheIdentity(server, token));
    generation = cacheGeneration();
  }
  if (method === "GET" && path === "/api/manager/device/me") {
    const previous = cacheGet(key);
    const authority = (d: any) => JSON.stringify([d?.me?.role, d?.me?.scope, d?.scope_version, d?.caps || {}]);
    if (previous && generation === cacheGeneration() && authority(previous.data) !== authority(data)) {
      clearCache();
      generation = cacheGeneration();
    }
  }
  if (method === "GET" && opts.cache !== false) cachePut(key, data, generation);
  return { ok: true, status: res.status, data };
}

// Отказ по ссылке — словами и с путём (30.09.2026): руководители вставляли
// ссылки от 24.09, потраченные телефоном и ярлыком, и видели только «устарела».
const NEW_LINK = "Новую возьмите в приложении на телефоне: Сводка → «Вход команды» → «Выдать себе» — или попросите у аналитика.";
const NEW_EMPLOYEE_LINK = "Попросите руководителя выдать новую ссылку на ваше ФИО в «Вход команды» или обратитесь к аналитику.";
export function deadLinkText(reason: string, employee = false): string {
  const next = employee ? NEW_EMPLOYEE_LINK : NEW_LINK;
  if (reason === "used_up") return "По этой ссылке уже вошли столько раз, сколько она позволяет — телефон и ярлык на экране тоже считаются. " + next;
  if (reason === "expired") return "Ссылке больше недели — она уже не действует. " + next;
  return "Эта ссылка не действует — возможно, её уже заменили новой. " + next;
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
    res = await request(base + "/api/manager/device/enroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: link.code, device: deviceName(), keep_token: base === readSettings().server ? readToken() : undefined,
        enroll_token: preparedEnroll("manager\n" + base + "\n" + link.code, randomBytes(32).toString("base64url")) }),
    });
  } catch (err) {
    return troubleOf(err);
  }
  if (res.status === 410) return { ok: false, status: 410, kind: "nolink", text: deadLinkText(await deadReason(res)) };
  if (res.status === 403) return { ok: false, status: 403, kind: "forbidden", text: "Это личная ссылка сотрудника. Переключаю приложение на вход сотрудника." };
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
      await request(server + "/api/manager/device/logout", { method: "POST", headers: { "X-Pult-Token": token } }, 5000);
    } catch { /* нет связи — токен всё равно стираем у себя, аналитик отзовёт */ }
  }
  if (readSettings().server === server && readToken() === token) wipe();
}

/** Password entry for a manager/security/full account. It uses the manager
 * device store, never touches the employee token or an offline call queue.
 * The analyst /admin device is a separate credential, not this token. */
export async function accountManagerLogin(server: string, username: string, password: string): Promise<ApiResult> {
  const base = serverOrigin(String(server || "").trim() || readSettings().server || "");
  if (!base) return { ok: false, status: 0, kind: "nolink", text: "Укажите адрес сервера из инструкции." };
  const previous = readSettings(), previousToken = readToken(), generation = cacheGeneration();
  const current = () => readSettings().server === previous.server && readToken() === previousToken && cacheGeneration() === generation;
  let res: Response;
  try {
    res = await request(base + "/api/account/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, audience: "manager", entrance: "internal",
        pc_proof: await pcLoginProof(base, username, password),
        device: { kind: "desktop", fp: desktopFingerprint(), label: deviceName() + " · пульт" } }),
    });
  } catch (err) { return current() ? troubleOf(err) : changedAuthority(); }
  if (!current()) return changedAuthority();
  if (!res.ok) {
    const code = await errorCode(res);
    return current() ? { ok: false, status: res.status, kind: "forbidden",
      text: code || "Не удалось войти. Проверьте логин и пароль." } : changedAuthority();
  }
  const data = await res.json().catch(() => null) as
    { action?: string; token?: string; username?: string; role?: string; uid?: number; must_change_password?: boolean; error?: string } | null;
  if (!current()) return changedAuthority();
  if (!data || !["ok", "ok_pending"].includes(data.action || "") || !data.token ||
      !["manager", "security", "full"].includes(data.role || "") || !Number.isSafeInteger(data.uid))
    return { ok: false, status: 502, kind: "server",
      text: data?.role === "analyst" ? "Аккаунт аналитика не открывает /admin: используйте установленный вход пульта аналитика."
        : "Сервер не подтвердил роль руководителя или выдал неполный вход. Существующий вход сохранён." };
  writeToken(data.token);
  writeSettings({ server: base, device: { name: deviceName(), created_at: new Date().toISOString(),
    username: data.username || username, role: data.role, telegram_id: data.uid } });
  // Cache keys include the bearer. Old manager data cannot cross this boundary;
  // leaving the independent field cache intact preserves its offline workspace.
  return { ok: true, status: res.status, data: { action: data.action, username: data.username || username,
    role: data.role, must_change_password: !!data.must_change_password } };
}

// --- Приложение выездника/софтника на ПК: свой класс токена (agent_devices) ----
// Телефон не вытесняется: вход по ссылке выездника даёт ПК-токен, привязка
// agent_bind остаётся телефону. Белый список путей — только ручки выездника.
// Хвост «(/.*)?»: путь зовётся и как «/api/save», и как «/api/photo/xyz» —
// строгая дробь после имени не пропускала «/api/bootstrap» и «/api/save»,
// и приложение выездника на сборке 03.10 не открывало портфель вовсе.
const FIELD_PATH = /^\/api\/(bootstrap|person|cards|save|photo|receipt|markers|notes|note|lang|route_to|geocode|geo)(\/.*)?$/;
const FIELD_PATH_EXACT = new Set(["/api/person", "/api/cards", "/api/markers", "/api/notes",
                                  "/api/geocode", "/api/logout", "/api/bootstrap", "/api/identity", "/api/save",
                                  "/api/receipt", "/api/photo", "/api/note"]);
for (const path of ["/api/office/action", "/api/workspace/report", "/api/workspace/promises"])
  FIELD_PATH_EXACT.add(path);
for (const path of ["/api/workspace/portfolio", "/api/workspace/dashboard"]) FIELD_PATH_EXACT.add(path);

function fieldOk(path: string): boolean {
  return FIELD_PATH.test(path) || FIELD_PATH_EXACT.has(path);
}

export async function enrollField(raw: string): Promise<ApiResult> {
  const link = splitLink(raw);
  if (!link) return { ok: false, status: 0, kind: "nolink", text: "Это не похоже на ссылку входа — вставьте её целиком." };
  const base = link.base || readSettings().fieldServer || readSettings().server || "";
  if (!base) return { ok: false, status: 0, kind: "nolink", text: "В ссылке нет адреса сервера — вставьте её целиком." };
  const previousSettings = readSettings(), previousToken = readFieldToken(), pending = readCallQueue();
  const hasPending = !!pending?.entries.length;
  const previousUid = previousSettings.fieldDevice?.telegram_id;
  if (hasPending && base !== previousSettings.fieldServer)
    return { ok: false, status: 409, kind: "forbidden", text: "Сначала отправьте звонки прежнего входа. Очередь сохранена на этом компьютере." };
  const previousOwner = legacyCallOwner();
  let res: Response;
  try {
    res = await request(base + "/api/agent/device/enroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: link.code, device: deviceName(),
        enroll_token: hasPending ? undefined : preparedEnroll("field\n" + base + "\n" + link.code, randomBytes(32).toString("base64url")),
        keep_token: base === previousSettings.fieldServer ? previousToken : undefined,
        expected_uid: hasPending ? previousUid : undefined, reuse_only: hasPending && !previousUid }),
    });
  } catch (err) {
    return troubleOf(err);
  }
  if (res.status === 410) return { ok: false, status: 410, kind: "nolink", text: deadLinkText(await deadReason(res), true) };
  if (res.status === 403) return { ok: false, status: 403, kind: "forbidden", text: "Это ссылка руководителя или аналитика. Для приложения выездника нужна ссылка из «Вход команды» на ваше ФИО." };
  if (res.status === 429) return { ok: false, status: 429, kind: "server", text: "Слишком много попыток. Подождите минуту." };
  if (res.status === 409) return { ok: false, status: 409, kind: "forbidden", text: "На компьютере остались звонки прежнего сотрудника. Сначала отправьте их или восстановите его вход; очередь сохранена." };
  if (!res.ok) return { ok: false, status: res.status, kind: "server", text: `Сервер ответил ошибкой ${res.status}.` };
  const data = (await res.json()) as { token: string; device_id: number; employee: string; telegram_id?: number };
  if (hasPending && (previousUid ? data.telegram_id !== previousUid : data.token !== previousToken))
    return { ok: false, status: 409, kind: "forbidden", text: "Вход не изменён: сначала отправьте сохранённые звонки прежнего сотрудника." };
  writeFieldToken(data.token);
  writeSettings({ fieldServer: base, fieldDevice: { id: data.device_id, name: deviceName(), employee: data.employee, telegram_id: data.telegram_id } });
  if (data.telegram_id && pending?.owner === previousOwner && data.token === previousToken)
    writeCallQueue({ ...pending, owner: callOwner() });
  return { ok: true, status: 200, data: { ...data, token: undefined } };
}

/** Отпечаток этого компьютера для входа по логину-паролю: sha-256 имени машины,
 *  пользователя Windows и каталога данных — стабилен, пока программа живёт на
 *  этом же месте; смена машины видна аккаунту как новое устройство. */
export function desktopFingerprint(): string {
  return pcIdentity().fingerprint;
}

export async function pcLoginProof(base: string, username: string, password: string): Promise<any> {
  const identity = pcIdentity();
  const response = await request(base + "/api/account/pc_challenge", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ public_key: identity.publicKey, fingerprint: identity.fingerprint, label: deviceName() }) });
  const c = await response.json() as any;
  if (!response.ok || !c.ok) throw new Error(c.error || "Проверка компьютера недоступна. Обновите программу и сервер.");
  const message = [c.challenge_id, c.nonce, "", username.trim().toLowerCase(), createHash("sha256").update(password).digest("hex")].join("\n");
  return { challenge_id: c.challenge_id, signature: sign(null, Buffer.from(message), identity.privateKey).toString("base64") };
}

export async function accountStaff(server: string): Promise<ApiResult> {
  const base = serverOrigin(server || readSettings().fieldServer || "");
  if (!base) return { ok: false, status: 400, text: "Проверьте адрес сервера." };
  try {
    const res = await request(base + "/api/account/staff", { method: "GET" });
    return { ok: res.ok, status: res.status, data: await res.json() };
  } catch (error) { return troubleOf(error); }
}

export async function accountRegister(server: string, employee: string, username: string, password: string): Promise<ApiResult> {
  const base = serverOrigin(server || readSettings().fieldServer || "");
  if (!base) return { ok: false, status: 400, text: "Проверьте адрес сервера." };
  if (readCallQueue()?.entries.length) return { ok: false, status: 409,
    text: "Сначала отправьте очередь прежнего профиля. Записи сохранены на компьютере." };
  try {
    const identity = pcIdentity();
    const challenge = await request(base + "/api/account/pc_challenge", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ public_key: identity.publicKey, fingerprint: identity.fingerprint, label: deviceName() }) });
    const c = await challenge.json() as any;
    if (!challenge.ok || !c.ok) return { ok: false, status: challenge.status, text: c.error || "Не удалось зарегистрировать компьютер." };
    employee = employee.trim(); username = username.trim().toLowerCase();
    const message = [c.challenge_id, c.nonce, employee, username, createHash("sha256").update(password).digest("hex")].join("\n");
    const signature = sign(null, Buffer.from(message), identity.privateKey).toString("base64");
    const response = await request(base + "/api/account/register", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ employee, username, password, pc_proof: { challenge_id: c.challenge_id, signature } }) });
    const data = await response.json() as any;
    if (!response.ok || !data.ok || !data.token) return { ok: false, status: response.status, text: data.error || "Профиль не создан." };
    if (readCallQueue()?.entries.length) return { ok: false, status: 409, text: "Профиль создан. Войдите после отправки очереди прежнего профиля." };
    clearCache();
    writeFieldToken(data.token);
    writeSettings({ fieldServer: base, fieldDevice: { name: deviceName(), employee: data.employee, username: data.username, telegram_id: data.uid } });
    return { ok: true, status: response.status, data: { ...data, token: undefined } };
  } catch (error) { return troubleOf(error); }
}

/** Вход сотрудника по логину и паролю (аккаунт вместо персональной ссылки).
 *  Токен выдаётся того же класса, что у входа по ссылке (agent_devices), и
 *  сохраняется как обычный полевой — конструктор дальше не различает. Сервер
 *  до подтверждения владельца отвечает pending только на /api/bootstrap. */
export async function accountLogin(server: string, username: string, password: string): Promise<ApiResult> {
  const base = serverOrigin(String(server || "").trim() || readSettings().fieldServer || readSettings().server || "");
  if (!base) return { ok: false, status: 0, kind: "nolink", text: "Неизвестен адрес сервера. Войдите один раз по ссылке или укажите адрес сервера из инструкции." };
  const name = deviceName();
  const fp = desktopFingerprint();
  const previous = readSettings(), pending = readCallQueue();
  const previousToken = readFieldToken(), previousOwner = callOwner(), previousGeneration = cacheGeneration();
  const current = () => readFieldToken() === previousToken && readSettings().fieldServer === previous.fieldServer && cacheGeneration() === previousGeneration;
  const hasPending = !!pending?.entries.length;
  if (hasPending && (base !== previous.fieldServer || !previous.fieldDevice?.telegram_id ||
      pending?.owner !== previousOwner))
    return { ok: false, status: 409, kind: "forbidden", text: "Сначала отправьте звонки прежнего входа. Очередь сохранена на этом компьютере." };
  let res: Response;
  try {
    res = await request(base + "/api/account/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, entrance: "internal", audience: "employee",
        pc_proof: await pcLoginProof(base, username, password),
        expected_uid: hasPending ? previous.fieldDevice?.telegram_id : undefined,
        device: { kind: "desktop", fp, label: name + " · " + fp.slice(0, 8) } }),
    });
  } catch (err) {
    if (!current()) return changedAuthority();
    return troubleOf(err);
  }
  if (!current()) return changedAuthority();
  if (res.status === 403 || res.status === 429 || res.status === 409) {
    const data = await res.json().catch(() => null) as { error?: string } | null;
    if (!current()) return changedAuthority();
    return { ok: false, status: res.status, kind: res.status === 429 ? "server" : "forbidden",
      text: data?.error || "Не удалось войти. Проверьте логин и пароль." };
  }
  if (!res.ok) return { ok: false, status: res.status, kind: "server", text: `Сервер ответил ошибкой ${res.status}.` };
  const data = (await res.json().catch(() => null)) as
    { action?: string; token?: string; employee?: string; username?: string; uid?: number; role?: string; must_change_password?: boolean; error?: string } | null;
  if (!current()) return changedAuthority();
  if (!data?.token || (data.action !== "ok" && data.action !== "ok_pending") || data.role !== "agent")
    return { ok: false, status: 0, kind: "server", text: "Сервер не подтвердил роль сотрудника. Прежний вход сохранён." };
  // Очередь звонков прежнего сотрудника не должна молча достаться новому входу:
  // тот же человек — переносим очередь на новый токен, другой — отказ (на экране
  // входа появится кнопка «Очистить очередь прежнего сотрудника»).
  const latestQueue = readCallQueue();
  if (latestQueue?.entries.length && (base !== previous.fieldServer ||
      latestQueue.owner !== previousOwner || previous.fieldDevice?.telegram_id !== data.uid))
    return { ok: false, status: 409, kind: "forbidden", text: "На компьютере остались звонки прежнего сотрудника. Сначала отправьте их или восстановите его вход; очередь сохранена." };
  writeFieldToken(data.token);
  // uid (внутренний номер аккаунта) — тот же слот, что у входа по ссылке:
  // стабильный владелец очереди между ротациями токена и сохранённая
  // идентичность при wipeField (иначе 401 орфанил собственную очередь).
  writeSettings({ fieldServer: base, fieldDevice: { name, employee: data.employee || "", username: data.username || "", telegram_id: data.uid } });
  // The stable server+uid owner is unchanged. Keep the live queue: it may have
  // received new records or acknowledgements while the login request was pending.
  return { ok: true, status: 200, data: { action: data.action, employee: data.employee, username: data.username, must_change_password: !!data.must_change_password } };
}

function legacyCallOwner(): string {
  return createHash("sha256").update((readSettings().fieldServer || "") + "\n" + readFieldToken()).digest("hex");
}
function callOwner(): string {
  const settings = readSettings(), uid = settings.fieldDevice?.telegram_id;
  return uid ? createHash("sha256").update((settings.fieldServer || "") + "\nuid:" + uid).digest("hex") : legacyCallOwner();
}
function ownedCallQueue(): CallQueue {
  const owner = callOwner(), queue = readCallQueue();
  if (queue && queue.owner !== owner && queue.entries.length) throw new Error("На компьютере остались звонки другого входа. Восстановите прежний вход или явно очистите очередь при выходе.");
  return queue?.owner === owner ? queue : { owner, entries: [] };
}
let flushingCalls: Promise<Map<string, ApiResult>> | null = null;
function acknowledgeDraft(body:Record<string,unknown>,data:any):void {
  if(body.kind!=="office"&&body.kind!=="call"&&body.kind!=="payment")return;
  const profile=body.kind==="office"?"hard":"soft",key=callOwner()+"\n"+profile+"\n"+String(body.draft_rep || body.rep);
  const draft=readDraft(key) as any;
  if(!draft || draft.pending_op_id!==body.op_id)return;
  const accepted=(id:string)=>body.kind==="call"?id.startsWith("c-"):body.kind==="payment"?id.startsWith("p-"):
    body.action==="month_comment"?["f-comment","f-clear-comment"].includes(id):id.startsWith("o-");
  const fields=Object.fromEntries(Object.entries(draft.fields || {}).filter(([id])=>!accepted(id)&&(!draft.dirty||draft.dirty.includes(id))));
  const next={...draft,fields,dirty:Object.keys(fields),touched:body.kind==="office"&&body.action!=="month_comment"||body.kind==="call"?[]:draft.touched,
    pending_op_id:undefined,revision:data?.revision || data?.person?.office?.revision || draft.revision};
  if(body.kind==="office"&&body.action!=="month_comment")delete next.office;
  if(!fields["f-comment"]&&!fields["f-clear-comment"])delete next.comment;
  writeDraft(key,Object.keys(fields).length?next:null);
}
export function flushCallQueue(): Promise<Map<string, ApiResult>> {
  if (flushingCalls) return flushingCalls;
  const work = async () => {
    const results = new Map<string, ApiResult>();
    if (!readFieldToken()) return results;
    const owner = callOwner();
    if(!ownedCallQueue().entries.some(e=>!e.error))return results;
    const access=await callField("GET","/api/identity",undefined,undefined,{cache:false});
    if(!access.ok||access.stale||callOwner()!==owner)return results;
    for (;;) {
      const entry = ownedCallQueue().entries.find(e => !e.error);
      if (!entry || callOwner() !== owner) break;
      const id = String(entry.body.op_id);
      const path = entry.body.kind === "office" ? "/api/office/action" : "/api/save";
      const res = await callField("POST", path, undefined, entry.body, { sendingQueue: true });
      if (res.kind === "offline" || res.status === 408 || res.status === 429 || res.status >= 500) break;
      results.set(id, res);
      if (callOwner() !== owner || res.kind === "cert" || res.kind === "revoked" || res.kind === "nolink") break;
      const current = ownedCallQueue(); // сохранения во время отправки остаются в очереди
      const rejected = (res.data as { rejected?: Record<string, unknown> } | undefined)?.rejected;
      if (res.ok && !rejected) {
        try { acknowledgeDraft(entry.body, res.data); }
        catch { /* повреждённый черновик не должен подвешивать всю очередь */ }
        current.entries = current.entries.filter(e => e.body.op_id !== entry.body.op_id);
      }
      else {
        const pending = current.entries.find(e => e.body.op_id === entry.body.op_id);
        if (pending) pending.error = res.ok ? "Запись принята, но поля требуют проверки: " + Object.keys(rejected || {}).join(", ") : res.text || "Сервер не принял запись";
      }
      writeCallQueue(current);
    }
    return results;
  };
  flushingCalls = work().finally(() => { flushingCalls = null; });
  return flushingCalls;
}

export async function callField(method: string, path: string, query?: Record<string, unknown>, body?: unknown,
                                opts: { binary?: boolean; cache?: boolean; sendingQueue?: boolean; revalidated?: boolean } = {}): Promise<ApiResult> {
  const server = readSettings().fieldServer;
  const token = readFieldToken();
  if (method === "POST" && path === "/api/local/call_queue/clear") {
    // Полная очистка очереди прежнего входа (запрещённый вход нового сотрудника,
    // повреждённый файл очереди). UI показывает подтверждение с числом записей.
    // Доступна и БЕЗ токена: кнопка на экране входа нужна именно тогда, когда
    // войти нельзя, а чужая очередь держит компьютер.
    try {
      let dropped = -1; // -1 = файл не читался (повреждён) — всё равно чистим
      try { dropped = ownedCallQueue().entries.length; } catch { /* повреждён */ }
      clearCallQueue();
      return { ok: true, status: 200, data: { dropped } };
    } catch (error) { return { ok: false, status: 0, kind: "server", text: String(error) }; }
  }
  if (!server || !token) return { ok: false, status: 0, kind: "nolink", text: "Приложение ещё не знает, кто вы." };
  if (path === "/api/local/preferences") return localPreferences(method, query, body, callOwner());
  if (path === "/api/local/draft") {
    try {
      const data = body as { rep?: string; draft?: unknown } | undefined;
      const rep = String(query?.rep || data?.rep || "");
      if (!rep || rep.length > 200) throw new Error("rep");
      const profile = String(query?.profile || (body as any)?.profile || "soft");
      if (!["soft", "hard"].includes(profile)) throw new Error("profile");
      const key = callOwner() + "\n" + profile + "\n" + rep;
      if (method === "POST") writeDraft(key, data?.draft ?? null);
      const draft=readDraft(key) as any;
      const pending=ownedCallQueue().entries.find(e=>e.body.op_id===draft?.pending_op_id ||
        (!e.error && String(e.body.draft_rep || e.body.rep)===rep && (e.body.kind==="office"?"hard":"soft")===profile));
      return { ok: true, status: 200, data: { draft, pending:pending?{op_id:pending.body.op_id,kind:pending.body.kind,error:pending.error}:null } };
    } catch (error) { return { ok: false, status: 0, kind: "server", text: String(error) }; }
  }
  if (method === "POST" && path === "/api/local/call_queue/remove") {
    try {
      const queue = ownedCallQueue(), id = String((body as { op_id?: unknown })?.op_id || "");
      queue.entries = queue.entries.filter(entry => entry.body.op_id !== id || !entry.error);
      writeCallQueue(queue);
      return { ok: true, status: 200, data: { pending: queue.entries.length } };
    } catch (error) { return { ok: false, status: 0, kind: "server", text: String(error) }; }
  }
  if (method === "GET" && path === "/api/local/call_queue") {
    try {
      const queue = ownedCallQueue();
      return { ok: true, status: 200, data: { pending: queue.entries.length, errors: queue.entries.filter(e => e.error).map(e => ({ rep: e.body.rep, op_id: e.body.op_id, error: e.error, body: e.body })) } };
    } catch (error) { return { ok: false, status: 0, kind: "server", text: String(error) }; }
  }
  // Руки аккаунта «логин-пароль»: журнал своих входов ходит с полевым токеном,
  // смена пароля — с текущим паролем. Тексты ошибок сервера показываем как есть
  // («Текущий пароль неверен»), а 401 здесь НЕ гасит вход: у ссылочного входа
  // аккаунта просто нет — это не отзыв устройства.
  if (path === "/api/account/sessions" || path === "/api/account/password") {
    const identity = cacheIdentity(server, token);
    const current = () => cacheIdentity(readSettings().fieldServer || "", readFieldToken()) === identity;
    const allowed = (method === "GET" && path === "/api/account/sessions") || (method === "POST" && path === "/api/account/password");
    if (!allowed) return { ok: false, status: 0, kind: "server", text: "запрещённый путь" };
    let res: Response;
    try {
      res = await request(server + path, {
        method,
        headers: { "X-App-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        bypassCustomProtocolHandlers: true,
      } as RequestInit);
    } catch (err) {
      if (!current()) return changedAuthority();
      return troubleOf(err);
    }
    if (!current()) return changedAuthority();
    if (res.status === 401) return { ok: false, status: 401, kind: "server", text: "Этот вход заведён по ссылке — журнал входов и смена пароля недоступны." };
    if (!res.ok) {
      const code = await errorCode(res);
      if (!current()) return changedAuthority();
      return { ok: false, status: res.status, kind: "server", text: code || `Сервер ответил ошибкой ${res.status}.` };
    }
    const data = await res.json().catch(() => null);
    if (!current()) return changedAuthority();
    if (!data) return { ok: false, status: 502, kind: "server", text: "Сервер ответил непонятным ответом." };
    return { ok: true, status: res.status, data };
  }
  const callBody = body as Record<string, unknown> | undefined;
  if (!opts.sendingQueue && method === "POST" &&
      ((path === "/api/save" && (callBody?.kind === "call" || callBody?.kind === "payment")) ||
       (path === "/api/office/action" && callBody?.kind === "office")) && callBody?.finish === true) {
    if (!callBody.op_id || !callBody.occurred_at) return { ok: false, status: 0, kind: "server", text: "Нет идентификатора или времени записи" };
    try {
      const queue = ownedCallQueue();
      const prior = queue.entries.find(e => e.body.op_id === callBody.op_id);
      const canonical = (value: unknown) => JSON.stringify(value, (_key, item) =>
        item && typeof item === "object" && !Array.isArray(item)
          ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
      if (prior && canonical(prior.body) !== canonical(callBody))
        return { ok: false, status: 409, kind: "server", text: "Идентификатор уже используется для другой записи. Исходная запись сохранена в очереди." };
      if (!prior && callBody.kind === "call" && typeof callBody.card_revision === "string") {
        const sameAttempt = (value: Record<string, unknown>) => {
          const { op_id, occurred_at, ...content } = value;
          return canonical(content);
        };
        const conflict = queue.entries.find(entry => entry.error &&
          /(?:ошибкой 409|Карточка изменилась)/.test(entry.error) &&
          sameAttempt(entry.body) === sameAttempt(callBody));
        if (conflict) return { ok: false, status: 409, kind: "server", text: conflict.error };
      }
      if (!prior) queue.entries.push({ body: callBody });
      writeCallQueue(queue);
      const results = await flushCallQueue();
      return results.get(String(callBody.op_id)) || { ok: true, status: 200, data: { queued: true, op_id: callBody.op_id } };
    } catch (error) { return { ok: false, status: 0, kind: "server", text: "Не удалось сохранить запись на компьютере: " + String(error) }; }
  }
  if (!fieldOk(String(path))) return { ok: false, status: 0, kind: "server", text: "запрещённый путь" };
  const url = server + path + qs(query);
  const key = "F " + cacheIdentity(server, token) + " " + method + " " + path + qs(query);
  let generation = cacheGeneration();
  const samePrincipal = () => readSettings().fieldServer === server && readFieldToken() === token;
  const current = () => samePrincipal() && generation === cacheGeneration();
  const invalidated = (): Promise<ApiResult> | ApiResult => method === "GET" && samePrincipal() && !opts.revalidated
    ? callField(method, path, query, body, { ...opts, cache: false, revalidated: true }) : changedAuthority();
  let res: Response;
  try {
    res = await request(url, {
      method,
      headers: { "X-App-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      bypassCustomProtocolHandlers: true,
    } as RequestInit, opts.binary ? 120000 : 30000);
  } catch (err) {
    if (!current()) return invalidated();
    const t = troubleOf(err);
    if (method === "GET" && opts.cache !== false && !opts.binary) {
      const hit = cacheGet(key);
      if (hit) return { ...t, ok: true, status: 200, data: hit.data, stale: hit.at };
    }
    return t;
  }
  if (!current()) return invalidated();
  if (res.status === 401) {
    wipeField({discardWork:true});
    onRevoked();
    return { ok: false, status: 401, kind: "revoked", text: REVOKED };
  }
  if (res.status === 403) {
    clearCache();
    // Руководитель/аналитик по логину-паролю до двух подтверждений: честный
    // текст ожидания вместо генерик-отказа «Нет доступа к этим данным».
    const code403 = await errorCode(res);
    if (code403 === "account_pending")
      return { ok: false, status: 403, kind: "forbidden", text: "Вход записан. Доступ появится после подтверждения администраторами (для руководителя и аналитика — двух). Проверьте связь позже." };
    return { ok: false, status: 403, kind: "forbidden", text: "Нет доступа к этим данным." };
  }
  if (res.status === 404) { clearCache(); return { ok: false, status: 404, kind: "notfound", text: "Не найдено — возможно, договор перезакрепили." }; }
  if (!res.ok) {
    const code = await errorCode(res);
    if (!current()) return invalidated();
    return { ok: false, status: res.status, kind: "server", text: `Сервер ответил ошибкой ${res.status}${code ? ": " + code.slice(0, 200) : ""}` };
  }
  if (opts.binary) {
    const buf = Buffer.from(await res.arrayBuffer());
    if (!current()) return invalidated();
    return { ok: true, status: res.status, data: { type: res.headers.get("content-type") || "", buf } };
  }
  const data = await res.json().catch(() => null);
  if (!current()) return invalidated();
  if (!data || (opts.sendingQueue && (data as { ok?: boolean }).ok !== true))
    return { ok: false, status: 502, kind: "server", text: "Сервер не подтвердил запись. Она сохранена на компьютере и будет отправлена повторно." };
  const beforeScope = cacheGeneration();
  if (beforeScope === generation) {
    acceptScope(data, cacheIdentity(server, token));
    generation = cacheGeneration();
  }
  if (method === "GET" && opts.cache !== false) cachePut(key, data, generation);
  return { ok: true, status: res.status, data };
}

/** Загрузка файла чека: сырые байты + поля в query (как телефон, X-Receipt-Ext). */
export async function uploadField(path: string, query: Record<string, unknown>, filePath: string,
                                  ext: string): Promise<ApiResult> {
  const server = readSettings().fieldServer;
  const token = readFieldToken();
  if (!server || !token) return { ok: false, status: 0, kind: "nolink", text: "Приложение ещё не знает, кто вы." };
  const generation = cacheGeneration();
  const current = () => readSettings().fieldServer === server && readFieldToken() === token && generation === cacheGeneration();
  if (!fieldOk(String(path))) return { ok: false, status: 0, kind: "server", text: "запрещённый путь" };
  let buf: Buffer;
  try {
    buf = readFileSync(filePath);
  } catch {
    return { ok: false, status: 0, kind: "server", text: "Файл не читается — возможно, его уже переместили." };
  }
  let res: Response;
  try {
    res = await request(server + path + qs(query), {
      method: "POST",
      headers: { "X-App-Token": token, "Content-Type": "application/octet-stream", "X-Receipt-Ext": ext || ".jpg" },
      body: buf,
      bypassCustomProtocolHandlers: true,
    } as RequestInit, 120000);
  } catch (err) {
    if (!current()) return changedAuthority();
    return troubleOf(err);
  }
  if (!current()) return changedAuthority();
  if (res.status === 401) {
    wipeField({discardWork:true});
    onRevoked();
    return { ok: false, status: 401, kind: "revoked", text: REVOKED };
  }
  if (!res.ok) {
    const code = await errorCode(res);
    if (!current()) return changedAuthority();
    return { ok: false, status: res.status, kind: "server", text: `Чек не принят (${res.status}${code ? ": " + code.slice(0, 120) : ""}). Возьмите другой снимок и попробуйте ещё раз.` };
  }
  const data = await res.json().catch(() => ({}));
  if (!current()) return changedAuthority();
  return { ok: true, status: res.status, data };
}

export async function logoutField(): Promise<void> {
  const server = readSettings().fieldServer;
  const token = readFieldToken();
  if (server && token) {
    try {
      await request(server + "/api/agent/device/logout", { method: "POST", headers: { "X-App-Token": token } }, 5000);
    } catch { /* нет связи — токен всё равно стираем, аналитик отзовёт */ }
  }
  if (readSettings().fieldServer === server && readFieldToken() === token) wipeField();
}
