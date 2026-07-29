# Hardbot2: выкладка и ограниченный тест

Production-контур расположен в `/opt/hardbot2`, сервис Compose — `bot`,
контейнер — `hardbot2-bot-1`. Старый `/opt/hardbot` использует тот же Telegram
token и не должен работать одновременно с hardbot2.

Любая выкладка выполняется только локальным owner-скриптом из корня проекта:

```powershell
python _local\_deploy_hardbot2.py check
python _local\_deploy_hardbot2.py prepare-shared-stt
python _local\_deploy_hardbot2.py dry-run
python _local\_deploy_hardbot2.py deploy
```

`check` отказывает, если запущен старый poller. `dry-run` перечисляет точный
набор кода. `.env`, Compose, БД, фото и production-секреты вместе с кодом не
загружаются.

## Голос: общий Whisper Praxis

Hardbot2 не загружает собственную модель. Он обращается к уже прогретому
`large-v3-turbo` внутри Praxis по authenticated HTTP через Unix-сокет:

- host socket: `/opt/praxis-stt/run/stt.sock`;
- container socket: `/run/praxis-stt/stt.sock`;
- host token: `/opt/praxis-stt/secrets/client.token`;
- container token: `/run/secrets/praxis-stt-client.token`;
- TCP-listener отсутствует;
- автоматического local fallback нет, при недоступности остаётся ручной ввод.

Сначала разворачивается и проверяется Praxis STT, затем выполняется
`prepare-shared-stt`. Команда:

- проверяет authenticated `/healthz`;
- делает timestamp-backup production Compose и `.env`;
- удаляет mount прежней локальной модели, но не удаляет модель с диска;
- добавляет read-only mounts сокета и token-файла;
- выставляет только STT env-ключи;
- закрепляет `mem_limit: 768m` и `memswap_limit: 1024m`;
- проверяет `docker compose config`, но не перезапускает hardbot2.

Обычный `deploy` откажется без готового Praxis STT и точной конфигурации.
После пересборки он проверяет authenticated health, затем распознаёт
неперсональную синтетическую русскую запись именно через
`hardbot.stt_client.STTClient` и проверяет свежие логи.

Минимальные production env-ключи голоса:

```dotenv
VOICE_BACKEND=praxis
PRAXIS_STT_SOCKET=/run/praxis-stt/stt.sock
PRAXIS_STT_TOKEN_FILE=/run/secrets/praxis-stt-client.token
VOICE_TIMEOUT_SECONDS=60
```

## Фото

Новый клиент загружает фото как raw body после немедленного копирования bytes
в IndexedDB. Сервер проверяет лимит 25 MiB и сигнатуру JPEG/PNG/GIF/WebP или
HEIF/AVIF, записывает файл атомарно и идемпотентно связывает его с `op_id`.
Legacy multipart остаётся только для очередей старых service worker; при
повреждённой границе он отвечает retryable 503, чтобы клиент не потерял фото.

После выкладки проверить минимум:

1. PWA отдаёт shell/cache текущей версии.
2. На iPhone снимок переживает сворачивание приложения и последующую отправку.
3. При неуспехе запись остаётся видимой в очереди, доступна пересъёмка.
4. Повтор одного `op_id` не создаёт второй файл и не увеличивает счётчик.

## Обмен данными между реестрами

Нормативный документ:
`registry_cycle/DATA_EXCHANGE_POLICY_2026-07-29.md`.

Штатный цикл:

1. Явно импортировать разрешённые офисные исправления в hardbot.
2. Снять safe export из hardbot.
3. Обогатить личные реестры без очистки ручного текста.
4. Сделать snapshot, plan и staging через `registry_cycle`.
5. Полностью проверить общий и все личные файлы.
6. Вручную опубликовать единый доказанный пакет.
7. Сразу выполнить нулевой dry-run.

`pult/razbros.py`, `pult/daily_cycle.py` и
`pult/restore_from_bot.py` не являются штатными write-путями. Legacy-запись
заблокирована по умолчанию. Обычная публикация требует активный baseline;
`promote-first-baseline` предназначен только для действительно первого
baseline. Частичный пакет, конфликт дублей КД, ошибка provenance или
незавершённая проверка означают STOP.

## Очистка перед ограниченным тестом

Очистка выполняется только после успешного deploy и live-канареек:

```powershell
python _local\reset_hardbot2_for_limited_test.py plan
python _local\reset_hardbot2_for_limited_test.py apply `
  --confirm CLEAR-HARDBOT2-FOR-LIMITED-TEST
```

`apply` останавливает контейнер, создаёт финальный online-backup SQLite,
safe/raw exports, schema, хэшированный photo manifest и полную копию фото,
скачивает их локально под ограниченным ACL и независимо сверяет SHA-256,
integrity, counts и содержимое фотоархива. Только затем он очищает
точно известные application-таблицы и атомарно переносит фото в карантин.
После запуска повторно проверяются пустое operational-состояние, SQLite и
логи. Перед каждым one-shot дополнительно доказываются activated deploy receipt,
Git/release manifest, hashes reset/db/photo кода и pinned image ID. Backup и
карантин сохраняются для rollback.

Очищенную БД наполняют только согласованной тестовой выборкой. Боевые
реестры, выгрузки, БД и фото не добавляются в git.

## Переключение Telegram-бота

Один token может обслуживать только один poller. Перед запуском hardbot2
старый `/opt/hardbot` должен быть остановлен. Токен хранится в production
`.env`/durable settings и не выводится в отчёты. После смены token сервис
перезапускают только после проверки отсутствия конкурирующего poller.
