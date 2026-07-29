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

1. Собрать personal → master только через `registry_cycle`.
2. Полностью проверить staging общего и всех личных файлов.
3. Вручную опубликовать единый доказанный Excel-пакет.
4. Импортировать опубликованный master только с exact contract binding и
   source proof его publication receipt, file SHA и переданного grid.
5. Снять safe export из hardbot.
6. Выполнить evidence-bound обогащение личных реестров без очистки ручного
   текста; legacy Tk write-кнопки на ограниченный тест закрыты.
7. Провести следующий `registry_cycle` и сразу выполнить нулевой dry-run.

`pult/razbros.py`, `pult/daily_cycle.py` и
`pult/restore_from_bot.py` не являются штатными write-путями. Legacy-запись
заблокирована по умолчанию. Обычная публикация требует активный baseline;
`promote-first-baseline` предназначен только для действительно первого
baseline. Частичный пакет, конфликт дублей КД, ошибка provenance или
незавершённая проверка означают STOP.

Любой `publish_started` без terminal-event/доказанного complete-result
блокирует новый publish и снятие stale lock. Оператор запускает
`python -m registry_cycle reconcile-publish --run-dir ... --mode auto`;
auto выбирает all-new только если все live-файлы уже точно равны staging,
иначе выполняет verified all-old из snapshot-backups. Personal-файлы в
hardbot напрямую не импортируются.

Admin JSON-import требует exact лист `Портфели`. Для published master
обязательны все 52 поля export-schema; для test subset — точные
`contract_id`, `responsible` и proof с полным утверждённым `{КД: владелец}`
scope. Missing, unknown и нормализованные дубли блокируются до первой записи.
Служебные колонки принимаются только из versioned allowlist/узкого pattern;
legacy multipart import отвечает 409. Следующая сразу за заголовком строка
типов опубликованного master обязательна: exact `сх`/`хард`/`софт` проверяется
по каждой распознанной колонке, а строка исключается из данных.

Source proof v2 перед отправкой одноразово регистрируется локальным
`hardbot/import_authorization.py` внутри production runtime. HTTP-endpoint для
регистрации отсутствует. Регистратор сверяет весь proof с активным контрактом,
TTL и монотонной последовательностью публикаций; import атомарно consume-ит
receipt и продвигает source head. Старый head, collision одной sequence с
другим proof, незарегистрированный или просроченный proof означают `STOP`.
Точные команды подготовки и применения опубликованного master/test-subset
описаны в `../pult/README.md`.

Root-регистрация выполняется внутри уже запущенного exact контейнера с
смонтированной production SQLite; proof заранее кладётся в приватный каталог
`/opt/hardbot2/data/backups` и виден как `/data/backups/...`:

```bash
cd /opt/hardbot2
docker compose exec -T bot python /app/import_authorization.py register \
  --source-kind published_master \
  --proof /data/backups/<private-import-dir>/proof.json
```

Для пилотной выборки используется exact `--source-kind test_subset`.
Регистратор не печатает proof, строки реестра, DB path или secret.

Все publish/rehearsal/first-baseline/execute/distribution/reconcile entrypoint
сериализуются одним `cycle.lock` с уникальным owner token. Stale reclaim
оставляет атомарный tombstone; release сверяет owner token. Terminal считается
действительным только при точном совпадении `run_id` и `plan_digest` последнего
`publish_started`. Reconcile имеет повторные exact-проверки перед каждым
replace, перед baseline и перед terminal; любое внешнее изменение оставляет
run незавершённым и требует STOP.

После выкладки PWA v15 отдельно проверить: до первого доказанного визита форма
пуста при любых старых HARD-полях master; без явного выбора результата сервер
не создаёт `visits`, `visit_date` или `bot_visit_data`. Черновик localStorage
восстанавливается отдельно и сервер принимает только перечисленные оболочкой
явно затронутые поля.

## Очистка перед ограниченным тестом

Очистка выполняется только после успешного deploy и live-канареек:

```powershell
python _local\reset_hardbot2_for_limited_test.py plan
python _local\reset_hardbot2_for_limited_test.py prepare-clearance
# затем отдельно: pult.enrich → registry_cycle publish/post-run
# → python -m registry_cycle.clearance issue/verify
python _local\reset_hardbot2_for_limited_test.py apply `
  --snapshot <snapshot> `
  --clearance <snapshot>\registry-clearance.json `
  --confirm CLEAR-HARDBOT2-FOR-LIMITED-TEST
```

`prepare-clearance` при работающем боте создаёт согласованный online-backup
SQLite, safe/raw exports, schema, хэшированный photo manifest и полную копию
фото, скачивает их локально под ограниченным ACL и независимо сверяет SHA-256,
integrity, counts и содержимое фотоархива. Затем внешний существующий
enrichment/registry_cycle завершается и проверяется через evidence-only
`registry_cycle.clearance`; этот модуль ничего не переносит и не публикует.

Старые Tk/Tauri-кнопки очистки отключены, а `/admin/reset` для обоих legacy
scope всегда отвечает `409`. Обход snapshot/clearance через admin API
отсутствует.

`apply` требует exact snapshot и digest-bound clearance receipt, повторно
проверяет все локальные evidence-файлы, останавливает контейнер и доказывает,
что live БД/фото не изменились после snapshot. Только затем он очищает точно
известные application-таблицы и pending import-authorizations, атомарно
переносит фото в карантин. Consumed import receipts и source heads не
очищаются: reset не превращает старый опубликованный master снова в новый.
После остановки controller создаёт одноразовый signed orchestration intent:
он связан с exact archive/confirm-token, SHA snapshot и clearance receipt,
случайным nonce, а также полными container/image ID. Внутренний one-shot
`apply` принимает только digest этого intent; сам по себе `--app-stopped`
очистку не разрешает.

До destructive шага сохраняется digest-bound `apply_started` receipt. Успех и
восстановление завершаются отдельными signed receipts с той же привязкой.
Registry clearance lease и hardbot ops lock остаются удержанными до
подтверждённого post-state либо полного recovery, поэтому прямой запуск
внутреннего reset-командного контейнера не является операторским путём.
После запуска повторно проверяются пустое operational-состояние, SQLite и
логи. Перед каждым one-shot дополнительно доказываются activated deploy receipt,
Git/release manifest, hashes reset/db/photo кода и pinned image ID. Backup и
карантин сохраняются для rollback.

Точный порядок и JSON-контракт enrichment evidence описаны в
`registry_cycle/CLEARANCE_FOR_LIMITED_TEST.md`.

Очищенную БД наполняют только согласованной тестовой выборкой. Боевые
реестры, выгрузки, БД и фото не добавляются в git. Production admin key,
когда-либо попадавший в tracked history, до пилота ротируется; значение не
печатается в отчётах и хранится только в production/local ignored settings.

## Переключение Telegram-бота

Один token может обслуживать только один poller. Перед запуском hardbot2
старый `/opt/hardbot` должен быть остановлен. Токен хранится в production
`.env`/durable settings и не выводится в отчёты. После смены token сервис
перезапускают только после проверки отсутствия конкурирующего poller.
