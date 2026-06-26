# 7+10+8. Шифрование/ключи, секреты, аудит

## 7. Шифрование и управление ключами

### В транзите (in transit) — ✅
- Все внешние соединения по **TLS**: миниапп через Caddy (Let's Encrypt), бот — TLS к Telegram,
  деплой — SSH (ed25519-ключ `~/.ssh/kontur_deploy`).
- Внутренние сервисы (движок/OSRM/VROOM) — на `127.0.0.1`, наружу не выходят.

### Ключи/подписи в коде
| Назначение | Алгоритм | Где |
|---|---|---|
| Подпись Telegram WebApp | HMAC-SHA256, ключ `WebAppData`←`BOT_TOKEN` | `web.validate_init_data` |
| Сравнение подписей/токенов | `hmac.compare_digest` (constant-time) | web.py, engine main |
| Детерминированный джиттер точек | MD5 (НЕ безопасность — только разброс координат) | `gazetteer.jitter` |

### At rest (на диске) — 🔴 GAP к go-live
- Сейчас `hardbot.db`, фото, выгрузки лежат **в открытом виде** на диске он-прем.
- **Требование:** шифровать том/диск — **BitLocker** (Windows-ПК аналитика) или **LUKS**
  (Ubuntu-сервер филиала); опционально **SQLCipher** для самой БД.
- Бэкапы — шифровать (borgmatic даёт дедуп+шифрование), хранить в РФ (Yandex Object Storage).

## 10. Управление секретами

| Секрет | Где | Защита |
|---|---|---|
| `BOT_TOKEN` | `.env` (gitignored) | не в git, не в образе (`.dockerignore`), не в деплое |
| `ADMIN_ID` | `.env` | — |
| `ADMIN_API_KEY` | `.env` | auth admin-эндпоинтов пульта |
| `ENGINE_API_TOKEN` | env движка | auth API движка; обязателен при реальной БД |
| `YANDEX_GEOCODER_KEY` | env (опц.) | только если нужен Yandex |
| SSH deploy-ключ | `~/.ssh/kontur_deploy` | ed25519, пустая фраза — хранить локально |
| Серверные креды | `_infra/secrets.env` (gitignored) | вне репозитория |

**Реализованное (✅):** `.env` не коммитится (репозиторий бота под git, история чиста);
`.dockerignore` исключает `.env`/`*.db`; деплой не льёт `.env`; шаблон — `.env.example`.

**К go-live (🔴/🟡):**
- Ротация `BOT_TOKEN`/токенов по графику; немедленная при подозрении на компрометацию.
- Хранилище секретов (vault) при росте филиалов вместо плоского `.env`.
- **Проверить, что папка проекта на ПК аналитика НЕ синкается в OneDrive/Dropbox** (`.env` + `*.db`).

## 8. Дизайн аудита / логирования

### Реализовано (каркас O3)
- Таблица `audit(id, ts, actor, action, target, detail)` — **append-only** (только INSERT из кода).
- `db.audit_access(actor, action, target, detail)` — не валит основной поток (try/except).
- Точки логирования (кто-что-когда смотрел/менял ПД):
  - бот: `view_card` (`open_card`);
  - миниапп: `view_card` (`/api/person`), `save_card`/`save_visit` (`/api/save`), `photo`, `marker`;
  - привязки: `bind_request`, `approve_bind`, `reject_bind`.
- Просмотр: `db.audit_tail(limit)`.

### К go-live (рекомендации ИБ)
- **Tamper-evident:** хеш-цепочка записей (`hash_n = H(hash_{n-1} || record)`) или вынос в WORM.
- **Экспорт в SIEM** (syslog/файл с ротацией) для корреляции и алертов.
- **Полнота:** добавить логирование импорта/экспорта реестра и неуспешных авторизаций (401/403).
- **Хранение журналов** согласовать с retention (см. §11) и 230-ФЗ (1.5 года для записей взаимодействий).
