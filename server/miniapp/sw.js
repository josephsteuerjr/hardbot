/* Service worker миниаппа выездника.
 *
 * Цель — оффлайн «с холода»: приложение должно подниматься без сети, если хоть раз
 * было открыто при интернете. Кэшируем оболочку (index.html), локальный Telegram SDK
 * и иконки. Данные (/api/*) через SW НЕ кэшируем — их оффлайн-кэш держит сам миниапп в
 * IndexedDB, а исходящие POST'ы (визит/фото/метка) идут в свою очередь. Карты грузятся
 * лениво с CDN и кэшируются оппортунистически (для заполнения визита карта не нужна).
 *
 * ВАЖНО при обновлении оболочки на сервере: поднимите версию CACHE (v1→v2), иначе у уже
 * открывавших приложение выездников останется старый index.html из кэша до смены версии.
 *
 * Страница сама стирает Cache Storage целиком при выходе и при отзыве входа (wipeDevice
 * в index.html): там лежит и персональный manifest.webmanifest?t=<токен>. Ничего чинить
 * после этого не нужно: открытие по сети кладёт оболочку обратно (network-first ниже),
 * свои ассеты — при первом запросе (caches.open создаёт кэш заново).
 */
const CACHE = "hard-shell-v79";
const PRECACHE = [
  "/",
  "/vendor/telegram-web-app.js",
  "/manifest.webmanifest",
  "/vendor/icon-192.png",
  "/vendor/icon-512.png",
  "/vendor/maplibre-gl-4.7.1.css",
  "/vendor/maplibre-gl-4.7.1.js",
  "/vendor/pmtiles-3.2.1.js",
  "/vendor/protomaps-themes-base-4.4.0.js",
  // Шрифты облика «Почерк» (~135 КБ на все): без них офлайн-старт рисовал бы
  // системным шрифтом.
  "/fonts/golos-cyr.woff2",
  "/fonts/golos-ext.woff2",
  "/fonts/golos-lat.woff2",
  "/fonts/hand-cyr.woff2",
  "/fonts/hand-lat.woff2",
  "/fonts/mono-cyr.woff2",
  "/fonts/mono-lat.woff2",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;                     // POST'ы (save/photo/marker) — мимо SW
  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  // Маленький version handshake всегда идёт в сеть и никогда не становится
  // частью shell-кэша. Иначе именно старая PWA могла бы "подтвердить" себе
  // старую версию из собственного cache-first.
  if (sameOrigin && url.pathname === "/version.json") return;

  // Данные, админ-API и тайлы — всегда из сети (оффлайн-данные держит IndexedDB миниаппа).
  if (sameOrigin && (url.pathname.startsWith("/api/") ||
                     url.pathname.startsWith("/admin/") ||
                     url.pathname.startsWith("/tiles/"))) return;

  // Открытие приложения — NETWORK-FIRST: онлайн всегда берём свежую оболочку с сервера
  // (ведёт себя как без SW — надёжно в webview Telegram), кэш только фолбэк при офлайне.
  // Так service worker не может «залипнуть» на старой/сломанной закэшированной странице.
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req, {cache: "no-store"}).then((resp) => {
        if (resp && resp.ok) { const copy = resp.clone(); caches.open(CACHE).then((c) => c.put("/", copy)); }
        return resp;
      }).catch(() => caches.match("/"))
    );
    return;
  }

  if (sameOrigin) {
    // Свои ассеты (SDK, иконки, манифест) — cache-first с фоновым обновлением.
    e.respondWith(
      caches.match(req).then((hit) => {
        const net = fetch(req).then((resp) => {
          if (resp && resp.ok) caches.open(CACHE).then((c) => c.put(req, resp.clone()));
          return resp;
        }).catch(() => hit);
        return hit || net;
      })
    );
    return;
  }

  // Кросс-ориджин (карты/шрифты с CDN) — из кэша, иначе сеть; удачный ответ кладём в кэш
  // на будущее (в т.ч. opaque-ответы no-cors — тогда и карта заработает оффлайн повторно).
  e.respondWith(
    caches.match(req).then((hit) =>
      hit || fetch(req).then((resp) => {
        caches.open(CACHE).then((c) => c.put(req, resp.clone())).catch(() => {});
        return resp;
      }).catch(() => hit)
    )
  );
});
