# 4. Threat model (STRIDE)

Метод: STRIDE по каждой границе доверия из [DFD](02-data-flows-dfd.md). Статус: ✅ закрыто
в коде · 🟡 частично/операционно · 🔴 остаётся к go-live.

## 4.1. Spoofing (подмена личности)

| Угроза | Митигатор | Статус |
|---|---|---|
| Подмена выездника / подделка `initData` | HMAC-SHA256 подпись Telegram WebApp, constant-time `compare_digest`, свежесть 24 ч (`validate_init_data`) | ✅ (есть тесты) |
| **Самопривязка любым «Сотрудником»** (видеть чужих должников) | **Аппрув ADMIN на привязку** (`approved=0`→одобрение); пока не одобрено — доступа нет | ✅ O3 |
| Подмена запроса к движку | `ENGINE_API_TOKEN` (constant-time); реальная БД без токена не отдаётся | ✅ O3 |
| Подмена админа пульта | `ADMIN_API_KEY` (`X-Admin-Key`) | 🟡 ключ; +MFA на ОС-уровне рекомендуется |
| Кража telegram-сессии устройства | вне периметра; remote-wipe MDM на телефоне выездника | 🔴 операционно |

## 4.2. Tampering (искажение данных)

| Угроза | Митигатор | Статус |
|---|---|---|
| Клиент шлёт произвольные поля в `/api/save` | Whitelist `ALLOWED_SAVE` (только разрешённые ключи); запись `update_fields` фильтрует по схеме | ✅ |
| Подмена чужого договора в save/marker | Проверка `responsible==employee or ADMIN` на каждом запросе | ✅ |
| Искажение журнала | `audit` — append-only из кода (только INSERT) | 🟡 каркас; tamper-evident (хеш-цепочка/WORM) — к go-live |
| Порча БД при сбое | SQLite WAL + `busy_timeout`, пошаговая запись | ✅ |

## 4.3. Repudiation (отказ от действий)

| Угроза | Митигатор | Статус |
|---|---|---|
| «Я не смотрел эту карточку» | **Аудит-лог** `audit_access` (view_card/save_visit/photo/marker/approve_bind) — actor+ts+target | ✅ каркас O3 |
| Спорный исход выезда | `visits` (история, payload), сводка в беседу | ✅ |
| Целостность журнала | хеш-цепочка/экспорт в SIEM | 🔴 к go-live |

## 4.4. Information Disclosure (утечка)

| Угроза | Митигатор | Статус |
|---|---|---|
| Выездник видит чужих должников | **RBAC**: только `responsible==employee` (`clients_for_employee`) | ✅ |
| API движка отдаёт ПД без авторизации | `ENGINE_API_TOKEN` + порт только `127.0.0.1` + Caddy TLS | ✅ O3 |
| Адрес должника уходит во внешний геокодер | Свой Nominatim/Photon (env) / офлайн-газеттир | 🟡 настроить на PROD |
| Открытый CORS (`*`) | Сужен: ACAO только при `CORS_ALLOW_ORIGIN`; тайлы same-origin | ✅ O3 |
| Утечка БД/`.env` через облако-синк | `.gitignore`/`.dockerignore`; **папка не в OneDrive/Dropbox** | 🟡 проверить на ПК |
| Перехват в транзите | TLS (Caddy/Let's Encrypt); Telegram TLS | ✅ |
| Шифрование БД на диске (at-rest) | шифрование тома/диска (LUKS/BitLocker) или SQLCipher | 🔴 к go-live |

## 4.5. Denial of Service

| Угроза | Митигатор | Статус |
|---|---|---|
| Два поллера на один токен (409) | Один экземпляр; демо-VPS остановлен | ✅ операционно |
| Перегрузка движка/OSRM (1 vCPU) | Фолбэки VROOM→k-means, OSRM→гаверсинус; `_sane` отбраковка | ✅ |
| Флуд миниаппа | rate-limit на Caddy/reverse-proxy | 🔴 к go-live |

## 4.6. Elevation of Privilege

| Угроза | Митигатор | Статус |
|---|---|---|
| Выездник → доступ ADMIN | `is_trusted`=ADMIN или approved-привязка; ADMIN-эндпоинты под `X-Admin-Key` | ✅ |
| Дев-обход в проде | `HARDBOT_DEV` выключен в бою; `X-Dev-Uid` только в DEV | ✅ |
| SQL-инъекция | Параметризованные запросы; ключи полей — whitelist по `fields`/`BOOKKEEPING` | ✅ |
| Path traversal в фото/тайлах | фиксированные пути `PHOTO_DIR/<rep>`, `TILES_FILE` | 🟡 валидировать `rep` |

## 4.7. Топ-риски к go-live (приоритет)
1. 🔴 **Шифрование БД at-rest** (LUKS/BitLocker том или SQLCipher).
2. 🔴 **Независимый пентест** (см. §14).
3. 🔴 **Tamper-evident аудит** (хеш-цепочка + экспорт в SIEM).
4. 🟡 На PROD: свой геокодер-инстанс; проверка, что папка с ПД не синкается в облако; rate-limit.
