// Прогон НАСТОЯЩЕЙ оболочки (server/miniapp/index.html) под node.
//
// Головной скрипт манифеста и основной скрипт исполняются в одном vm-контексте,
// как два <script> на странице: у них общая глобальная область, и сценарий потом
// читает её тем же способом (STATE, URLT, …). Браузер заменён минимальными
// заглушками: localStorage/sessionStorage, IndexedDB (три хранилища оболочки),
// Cache Storage, fetch с ответами сценария, DOM на уровне getElementById/innerHTML.
// «Мир» (хранилища, база, кэши) живёт дольше страницы: перезагрузку сценарий
// изображает новой страницей в том же мире.
//
// Запуск: node shell_harness.js <index.html>, сценарий — на stdin: тело async-
// функции, в области видимости newWorld, openPage, fakeServer, sleep, settle,
// attrs, decodeAttr, out. В конце печатается одна строка JSON — то, что сценарий
// сложил в out. Запускатель — tests/shell_node.py.
"use strict";
const fs = require("fs");
const vm = require("vm");

const INDEX = fs.readFileSync(process.argv[2], "utf8");
const SCRIPTS = [...INDEX.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const HEAD = SCRIPTS.find(s => s.includes("MANIFEST_PERSONAL"));
const MAIN = SCRIPTS[SCRIPTS.length - 1];
if (!HEAD || !MAIN || !MAIN.includes("async function boot(")) {
  throw new Error("не нашёл скрипты оболочки");
}
// Последний вызов boot() — ручка сценария: дождаться конца первой загрузки.
const BOOT_CALL = "\nboot();";
const BOOT_AT = MAIN.lastIndexOf(BOOT_CALL);
if (BOOT_AT < 0) throw new Error("нет финального boot()");
const MAIN_RUN = MAIN.slice(0, BOOT_AT) + "\nwindow.__bootP=boot();" +
  MAIN.slice(BOOT_AT + BOOT_CALL.length);

const sleep = ms => new Promise(r => setTimeout(r, ms));
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const copy = v => (v === undefined ? undefined : structuredClone(v));

// --- хранилища -----------------------------------------------------------------
// Методы — на прототипе, записи — собственные поля: Object.keys(localStorage)
// в оболочке видит ровно записи, как в браузере.
class FakeStorage {
  getItem(k) { return own(this, k) ? this[k] : null; }
  setItem(k, v) { this[k] = String(v); }
  removeItem(k) { delete this[k]; }
  clear() { for (const k of Object.keys(this)) delete this[k]; }
  key(i) { const ks = Object.keys(this); return i < ks.length ? ks[i] : null; }
  get length() { return Object.keys(this).length; }
}

function newWorld(init) {
  init = init || {};
  const w = { ls: new FakeStorage(), ss: new FakeStorage(), idb: {}, caches: {}, busy: 0 };
  for (const [k, v] of Object.entries(init.ls || {})) w.ls.setItem(k, v);
  for (const [k, v] of Object.entries(init.ss || {})) w.ss.setItem(k, v);
  if (init.idb) {
    const rec = w.idb.hardbot = {
      stores: { kv: new Map(), cards: new Map(), queue: new Map() },
      keyPaths: { kv: null, cards: null, queue: "op_id" },
    };
    for (const [k, v] of Object.entries(init.idb.kv || {})) rec.stores.kv.set(k, copy(v));
    for (const [k, v] of Object.entries(init.idb.cards || {})) rec.stores.cards.set(k, copy(v));
    for (const v of init.idb.queue || []) rec.stores.queue.set(v.op_id, copy(v));
  }
  for (const [k, v] of Object.entries(init.caches || {})) w.caches[k] = v;
  // Снимок мира для out: только то, что проверяют сценарии.
  w.dump = () => {
    const rec = w.idb.hardbot;
    const keys = name => (rec && rec.stores[name]) ? [...rec.stores[name].keys()] : null;
    return {
      ls: Object.assign({}, w.ls), ss: Object.assign({}, w.ss),
      kv: keys("kv"), cards: keys("cards"), queue: keys("queue"),
      caches: Object.keys(w.caches),
    };
  };
  return w;
}

// Ответы IndexedDB приходят позже, как в браузере; счётчик busy нужен settle().
function makeIDB(w) {
  const later = fn => { w.busy++; setTimeout(() => { try { fn(); } finally { w.busy--; } }, 0); };
  function makeDB(rec) {
    return {
      objectStoreNames: { contains: n => own(rec.stores, n) },
      createObjectStore(n, opts) {
        rec.stores[n] = new Map(); rec.keyPaths[n] = (opts && opts.keyPath) || null; return {};
      },
      transaction(name) {
        const tx = { oncomplete: null, onabort: null, onerror: null, error: null };
        let pending = 0, done = false;
        const finish = () => {
          if (!pending && !done) { done = true; later(() => { if (tx.oncomplete) tx.oncomplete(); }); }
        };
        tx.objectStore = n => {
          const m = rec.stores[n];
          if (!m) throw new Error("нет хранилища " + n + " (" + name + ")");
          const kp = rec.keyPaths[n];
          const op = fn => {
            const r = { result: undefined, error: null, onsuccess: null, onerror: null };
            pending++;
            later(() => {
              try { r.result = fn(); } catch (e) { r.error = e; }
              pending--;
              if (r.error) { if (r.onerror) r.onerror(); } else if (r.onsuccess) r.onsuccess();
              finish();
            });
            return r;
          };
          return {
            get: k => op(() => copy(m.get(k))),
            put: (v, k) => op(() => {
              const key = k !== undefined ? k : (kp ? v[kp] : undefined);
              m.set(key, copy(v)); return key;
            }),
            delete: k => op(() => { m.delete(k); }),
            getAll: () => op(() => [...m.values()].map(copy)),
            count: () => op(() => m.size),
            clear: () => op(() => { m.clear(); }),
          };
        };
        later(finish);
        return tx;
      },
    };
  }
  return {
    open(name) {
      const req = { result: null, error: null, onsuccess: null, onerror: null, onupgradeneeded: null };
      later(() => {
        let rec = w.idb[name];
        const fresh = !rec;
        if (fresh) rec = w.idb[name] = { stores: {}, keyPaths: {} };
        req.result = makeDB(rec);
        if (fresh && req.onupgradeneeded) req.onupgradeneeded();
        if (req.onsuccess) req.onsuccess();
      });
      return req;
    },
  };
}

function makeCaches(w) {
  return {
    keys: async () => Object.keys(w.caches),
    delete: async k => { const had = own(w.caches, k); delete w.caches[k]; return had; },
    open: async k => { if (!own(w.caches, k)) w.caches[k] = []; return { put: async () => {}, match: async () => undefined }; },
    match: async () => undefined,
  };
}

// --- DOM ----------------------------------------------------------------------
// Элемент — это строка разметки. getElementById находит статичные элементы
// страницы, вставленные в body и те, чей id="…" есть в чьей-то разметке.
function makeDom(page) {
  const reg = new Map(), dyn = new Map();
  const idsIn = html => [...String(html).matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
  const present = id => {
    const needle = 'id="' + id + '"';
    for (const e of reg.values()) if (e._html.includes(needle)) return true;
    for (const e of dyn.values()) if (e.id !== id && e._html.includes(needle)) return true;
    return false;
  };
  class El {
    constructor(tag) {
      this.tagName = String(tag || "div").toUpperCase();
      this.id = ""; this.className = ""; this._html = ""; this.value = ""; this.disabled = false;
      this.style = { setProperty() {} }; this.dataset = {}; this.attrs = {};
      const cls = new Set();
      this.classList = {
        add: c => { cls.add(c); }, remove: c => { cls.delete(c); }, contains: c => cls.has(c),
        toggle: (c, on) => { const v = on === undefined ? !cls.has(c) : !!on; if (v) cls.add(c); else cls.delete(c); return v; },
      };
    }
    get innerHTML() { return this._html; }
    set innerHTML(v) { for (const id of idsIn(this._html)) dyn.delete(id); this._html = String(v); }
    get textContent() { return this._html.replace(/<[^>]*>/g, ""); }
    set textContent(v) {
      this.innerHTML = String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }
    insertAdjacentHTML(_pos, html) { this._html += String(html); }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") this.id = String(v); }
    getAttribute(k) { return own(this.attrs, k) ? this.attrs[k] : null; }
    addEventListener() {} removeEventListener() {} dispatchEvent() { return true; }
    focus() {} blur() {} click() {}
    querySelector() { return null; }
    querySelectorAll() { return []; }
    appendChild(el) { return el; }
    remove() {
      if (reg.get(this.id) === this) reg.delete(this.id);
      if (dyn.get(this.id) === this) dyn.delete(this.id);
    }
  }
  for (const id of ["langs", "offbar", "app", "boot", "bottomnav", "bottomnavInner", "navmode", "toast"]) {
    const e = new El("div"); e.id = id; reg.set(id, e);
  }
  const document = {
    hidden: false,
    documentElement: { style: { setProperty() {} } },
    head: { appendChild: el => { page.headLinks.push(el); return el; } },
    body: {
      classList: new El("body").classList,
      appendChild: el => { if (el.id) reg.set(el.id, el); return el; },
    },
    createElement: tag => new El(tag),
    getElementById(id) {
      if (reg.has(id)) return reg.get(id);
      if (present(id)) {
        if (!dyn.has(id)) { const e = new El("div"); e.id = id; dyn.set(id, e); }
        return dyn.get(id);
      }
      dyn.delete(id);
      return null;
    },
    querySelector(sel) {
      if (sel === 'link[rel="manifest"]') {
        return page.headLinks.filter(l => l.rel === "manifest").pop() || null;
      }
      return null;
    },
    querySelectorAll() { return []; },
    addEventListener(type, fn) { (page.docListeners[type] = page.docListeners[type] || []).push(fn); },
    removeEventListener() {},
  };
  return document;
}

// --- сеть ---------------------------------------------------------------------
// server(rec) → {status, body} | "neterr"; rec: url, method, token, init, body.
function makeFetch(w, page, server) {
  return async (url, opts) => {
    opts = opts || {};
    const h = opts.headers || {};
    const rec = {
      url: String(url), method: opts.method || "GET",
      token: h["X-App-Token"] || null, init: h["X-Init-Data"] || null,
      body: typeof opts.body === "string" ? opts.body : null,
    };
    page.fetchLog.push(rec);
    w.busy++;
    try {
      await sleep(0);
      const res = server ? await server(rec) : null;
      if (res === "neterr") throw new TypeError("Failed to fetch");
      const status = res ? res.status : 404;
      const body = res && res.body !== undefined ? JSON.parse(JSON.stringify(res.body)) : {};
      return {
        ok: status >= 200 && status < 300, status,
        json: async () => body, text: async () => JSON.stringify(body), blob: async () => new Blob([]),
      };
    } finally { w.busy--; }
  };
}

// Сервер сценария: кто есть кто по токену (или по initData внутри Телеграма).
// cfg.tokens: {токен: тело bootstrap | код ответа | "neterr"}; неизвестный — 401.
// cfg.routes: {путь: rec => ответ} — перекрывает всё остальное.
function fakeServer(cfg) {
  cfg = cfg || {};
  cfg.tokens = cfg.tokens || {};
  const srv = rec => {
    const path = rec.url.split("?")[0];
    if (path === "/version.json") return { status: 200, body: { shellVersion: cfg.shellVersion || 0 } };
    if (path === "/api/client-event") return { status: 200, body: { ok: true } };
    if (cfg.routes && cfg.routes[path]) return cfg.routes[path](rec);
    const key = rec.token || (rec.init ? "tg:" + rec.init : "");
    const who = own(cfg.tokens, key) ? cfg.tokens[key] : 401;
    if (typeof who === "number") return { status: who, body: {} };
    if (who === "neterr") return "neterr";
    if (path === "/api/identity") return { status: 200, body: {
      ok: true, uid: who.uid === undefined ? 1 : who.uid, employee: who.employee || "" } };
    if (path === "/api/bootstrap") {
      return {
        status: 200, body: Object.assign({
          ok: true, uid: 1, kind: "agent", lang: "ru", persons: [], roles: null,
          meta: { dropdowns: {}, formProtocolVersion: 3 },
        }, who),
      };
    }
    if (path === "/api/cards") return { status: 200, body: { cards: who.cards || {} } };
    if (path === "/api/save") return { status: cfg.saveStatus || 200, body: { ok: true, person: {} } };
    // These fixture tokens come from legacy links unless a test overrides the
    // route: the account endpoint must reject them before /api/logout revokes.
    if (path === "/api/account/logout") return { status: 401, body: {} };
    return { status: 200, body: { ok: true } };
  };
  srv.cfg = cfg;
  return srv;
}

// Ждём, пока стихнут запросы и IndexedDB (фон оболочки: прогрев, досыл).
async function settle(w, maxMs) {
  const until = Date.now() + (maxMs || 3000);
  let quiet = 0;
  while (Date.now() < until) {
    await sleep(5);
    if (w.busy === 0) { if (++quiet >= 4) return; } else quiet = 0;
  }
}

const ENT = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };
// Так браузер раскодирует значение атрибута перед тем, как отдать его JS.
const decodeAttr = v => String(v).replace(/&(amp|lt|gt|quot|#39);/g, m => ENT[m]);
const attrs = (html, name) =>
  [...String(html || "").matchAll(new RegExp("\\b" + name + '="([^"]*)"', "g"))].map(m => m[1]);

async function openPage(w, opts) {
  opts = opts || {};
  const page = { world: w, reloads: [], dialogs: [], listeners: {}, docListeners: {},
                 headLinks: [], fetchLog: [] };
  let url = new URL(opts.url || "https://hard.test/");
  const location = {
    get href() { return url.href; }, set href(v) { url = new URL(String(v), url.href); },
    get origin() { return url.origin; }, get protocol() { return url.protocol; },
    get host() { return url.host; }, get pathname() { return url.pathname; },
    get search() { return url.search; }, get hash() { return url.hash; },
    reload() { page.reloads.push(url.href); },
  };
  const history = {
    replaceState(_s, _t, u) { if (u != null) url = new URL(String(u), url.href); },
    pushState(_s, _t, u) { if (u != null) url = new URL(String(u), url.href); },
  };
  const document = makeDom(page);
  const sandbox = {
    console, URL, URLSearchParams, TextEncoder, TextDecoder, Blob, FormData, AbortController,
    Event, structuredClone, performance, crypto: globalThis.crypto,
    setTimeout: (f, ms, ...a) => setTimeout(f, ms, ...a), clearTimeout,
    setInterval: () => 0, clearInterval: () => {},
    requestAnimationFrame: f => setTimeout(f, 0),
    localStorage: w.ls, sessionStorage: w.ss,
    indexedDB: makeIDB(w), caches: makeCaches(w),
    location, history, document,
    navigator: { onLine: opts.onLine !== false, userAgent: opts.userAgent || "Mozilla/5.0 (shell harness)", standalone: false },
    Date: class extends Date {static now(){return opts.now ? opts.now() : Date.now();}},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    innerHeight: 800,
    alert: m => { page.dialogs.push(["alert", String(m)]); },
    confirm: m => { page.dialogs.push(["confirm", String(m)]); return false; },
    prompt: m => { page.dialogs.push(["prompt", String(m)]); return null; },
    addEventListener: (type, fn) => { (page.listeners[type] = page.listeners[type] || []).push(fn); },
    removeEventListener() {},
    fetch: makeFetch(w, page, opts.server),
  };
  if (opts.telegram) sandbox.Telegram = opts.telegram;
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(HEAD, ctx, { filename: "index.html#head" });
  vm.runInContext(MAIN_RUN, ctx, { filename: "index.html#main" });
  page.run = code => vm.runInContext(code, ctx);
  page.url = () => url.href;
  page.el = id => document.getElementById(id);
  page.html = id => { const e = document.getElementById(id); return e ? e.innerHTML : null; };
  page.manifestHref = () => {
    const l = page.headLinks.filter(x => x.rel === "manifest").pop();
    return l ? l.href : null;
  };
  page.booted = async () => { await sandbox.__bootP; await settle(w); };
  // Адрес сменился без перезагрузки (ссылка открылась в живой вкладке).
  page.hash = async h => {
    url.hash = h;
    for (const fn of page.listeners.hashchange || []) await fn();
    await settle(w);
  };
  page.tokensSeen = path => page.fetchLog.filter(r => r.url.split("?")[0] === path).map(r => r.token);
  return page;
}

async function main() {
  const code = fs.readFileSync(0, "utf8");
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const out = {};
  const problems = [];
  process.on("unhandledRejection", e => { problems.push("unhandledRejection: " + (e && e.stack || e)); });
  process.on("uncaughtException", e => { problems.push("uncaughtException: " + (e && e.stack || e)); });
  const fn = new AsyncFunction("newWorld", "openPage", "fakeServer", "sleep", "settle",
                               "attrs", "decodeAttr", "out", code);
  await fn(newWorld, openPage, fakeServer, sleep, settle, attrs, decodeAttr, out);
  if (problems.length) out.__problems = problems;
  process.stdout.write(JSON.stringify(out) + "\n");
  process.exit(0);
}

main().catch(e => {
  process.stdout.write(JSON.stringify({ error: String(e && e.stack || e) }) + "\n");
  process.exit(0);
});
