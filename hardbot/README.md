# Hardbot2 — приложение выездной службы

Hardbot2 — Telegram-бот и установленная PWA для выездников, плюс
административный контур. Production расположен в `/opt/hardbot2`; БД и фото
хранятся в bind-mount `/opt/hardbot2/data`.

Актуальные документы:

- [`DEPLOY.md`](DEPLOY.md) — выкладка, общий Whisper Praxis, фото и очистка;
- [`../registry_cycle/DATA_EXCHANGE_POLICY_2026-07-29.md`](../registry_cycle/DATA_EXCHANGE_POLICY_2026-07-29.md)
  — нормативная матрица загрузки/выгрузки и организационный цикл;
- [`AGENTS.md`](AGENTS.md) — карта кода и инженерные ограничения.

## Рабочий контур

Выездник получает только клиентов, у которых
`clients.responsible == agent_bind.employee`. В PWA он фиксирует посещение,
геопозицию, фото, комментарий и выбранный результат. Завершённый визит считается
доказанным только при наличии служебных `bot_visit_data` и `_bot_completed`,
созданных фактическим `POST /api/save`.

Выездники не выполняют звонки. Поэтому:

- внутренний `call_status` завершённого визита экспортируется только в
  `overall_result` (`Общий результат`);
- `tips` экспортируется только в `extra_address`;
- обычное bot-enrichment не пишет call-status и call-comment поля;
- ручной текст личного реестра сохраняется идемпотентно.

Raw `visit_date`, старая выгрузка или копия личной книги сами по себе не являются
provenance завершённого визита.

## Голос

Hardbot2 не держит вторую модель Whisper. `hardbot/stt_client.py` обращается к
уже прогретому `large-v3-turbo` Praxis по authenticated HTTP через Unix socket.
TCP listener и автоматический local fallback отсутствуют; при недоступности
голоса остаётся ручной ввод.

## Фото

PWA немедленно копирует bytes выбранного файла в IndexedDB и затем отправляет
raw body. Это исключает зависимость от восстановленного WebKit `File`/`FormData`
после сворачивания iPhone. Сервер ограничивает тело 25 MiB, проверяет magic
JPEG/PNG/GIF/WebP/HEIF/AVIF, пишет атомарно и дедуплицирует по `op_id`.
Старая multipart-очередь поддерживается только как retryable migration path.

## Реестры

Штатный цикл один:

1. Явный import разрешённых офисных исправлений.
2. Safe export доказанных bot-визитов.
3. Точечное enrichment личных книг без очистки ручного текста.
4. `registry_cycle`: snapshot → plan → staging → полная validation.
5. Ручная публикация всего пакета одним оператором.
6. Немедленный нулевой dry-run.

Legacy `razbros.py`, `daily_cycle.py` и `restore_from_bot.py` не являются
штатными write-путями и по умолчанию работают fail-closed. Конфликт двух
непустых значений одного поля для одного КД блокирует весь import до первой
записи.

## Локальная проверка

Из корня проекта:

```powershell
python -m pytest -q
python -m compileall -q hardbot pult registry_cycle
```

Для локального запуска скопируйте `.env.example` в `.env`, заполните только
локальные значения и запустите `python hardbot/app.py`. Production `.env`, БД,
фото и выгрузки не добавляются в Git.

## Ограниченный тест

Перед очисткой обязательны успешные Praxis/hardbot deploy-canaries и финальный
полный export. `hardbot/limited_test_reset.py` создаёт online SQLite backup,
safe/raw exports, schema, photo manifest и полный фотоархив; очищает только
точно известные application tables; фото перемещает в rollback-карантин.
Операторский запуск описан в [`DEPLOY.md`](DEPLOY.md).
