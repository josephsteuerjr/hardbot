# SECURITY.md — «Контур · Выезды»

Операционная памятка по безопасному запуску. Полная модель ИБ — в
[`БЕЗОПАСНОСТЬ-1 …md`](БЕЗОПАСНОСТЬ-1%20—%20Архитектура,%20потоки%20данных,%20модель%20угроз.md)
и [`docs/security/`](docs/security/00-README.md).

## Главный принцип
**Реальные ПД должников НИКОГДА не покидают периметр компании.** Обработка — он-прем, на
машине аналитика. На публичный демо-сервер уезжает **только код**; там крутится **только
синтетика** (ПД нет никогда).

## Среды и как запускать

### DEV / DEMO — синтетика (ПД нет)
```bash
python engine/build_demo.py          # синтетика → web/dashboard.html
uvicorn app.main:app --app-dir engine --host 127.0.0.1 --port 8090
# HARDBOT_DB НЕ задан → источник синтетический, /api/* открыты (ПД нет)
```

### PROD-ПД — реальные данные (только он-прем, в юрисдикции)
```bash
# Бот + миниапп (читает .env: BOT_TOKEN, ADMIN_ID):
cd hardbot && python app.py
# Движок с реальной БД — ОБЯЗАТЕЛЬНО с токеном и только на localhost:
HARDBOT_DB=/path/to/hardbot.db ENGINE_API_TOKEN=<секрет> \
  uvicorn app.main:app --app-dir engine --host 127.0.0.1 --port 8090
# Наружу — только через Caddy (TLS) или SSH-туннель.
```

## Жёсткие правила (вшиты в код — старт упадёт при нарушении)
- **Движок с реальной БД (`HARDBOT_DB`) без `ENGINE_API_TOKEN` → отказ старта.** ПД без аутентификации не отдаём.
- **Движок с реальной БД на `0.0.0.0` → отказ старта.** Bind только `127.0.0.1`.
- `/api/*` движка требуют заголовок `X-Engine-Token` (constant-time сравнение).
- Миниапп: доступ только по подписи Telegram `initData` (HMAC), выездник видит **только своих**.
- Привязка выездника активна **только после аппрува ADMIN** (`BIND_APPROVAL=1`; для пилота можно `0`).
- Доступ к карточке должника пишется в аудит-лог (`audit`).

## Секреты (env) — где и что
| Переменная | Назначение | Хранение |
|---|---|---|
| `BOT_TOKEN` | токен Telegram-бота | `hardbot/.env` (gitignored) |
| `ADMIN_ID` | владелец/админ | `hardbot/.env` |
| `ADMIN_API_KEY` | admin-API пульта (`X-Admin-Key`) | `.env` |
| `ENGINE_API_TOKEN` | API движка (`X-Engine-Token`) | env движка |
| `CORS_ALLOW_ORIGIN` | сузить CORS (пусто = same-origin) | env |
| `BIND_APPROVAL` | аппрув привязки (1/0) | env |
| `NOMINATIM_URL`/`PHOTON_URL` | СВОЙ геокодер для ПД | env (PROD) |
| серверные креды, SSH-ключ | деплой | `_infra/secrets.env`, `~/.ssh` (gitignored) |

## Что НЕЛЬЗЯ
- ❌ Заливать `.env`, `*.db`, `Хард.xlsb`, `_infra/secrets.env` в git (закрыто `.gitignore` — проверять перед коммитом).
- ❌ Класть реальную БД/`.env` в папку, синхронизируемую с облаком (OneDrive/Dropbox).
- ❌ Геокодить реальные адреса должников публичными OSM/Yandex на PROD — поднять свой Nominatim/Photon (`NOMINATIM_URL`/`PHOTON_URL`) или офлайн-газеттир.
- ❌ Публиковать порт движка/OSRM/VROOM наружу — только `127.0.0.1` + Caddy.
- ❌ Трогать контейнер `amnezia-awg2`. Запускать второй поллер с тем же `BOT_TOKEN` (ошибка 409).

## Проверки перед go-live (см. docs/security/08, БЕЗОПАСНОСТЬ-1 §9)
Шифрование БД at-rest (BitLocker/LUKS) · MFA на привилегиях · rate-limit · tamper-evident
аудит + SIEM · независимый пентест · retention/IR-регламенты · регистрации (РКН/Госреестр УЗ).

## Прогон тестов
```bash
pytest          # из корня — скоринг, routing, initData, газеттир, безопасность, smoke-импорты
```
