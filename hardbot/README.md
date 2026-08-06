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

До первого доказанного snapshot server-form пуста для всех полей выезда,
даже если raw master содержит старые статусы, комментарии или наводки. Эти
значения разрешено показывать только read-only. PWA v15 хранит пользовательский
черновик отдельно, отправляет перечень явно затронутых полей и требует явного
результата визита; старая оболочка не может создать provenance.

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

1. `registry_cycle`: personal → master, snapshot → plan → staging → validation.
2. Ручная публикация всего Excel-пакета одним оператором.
3. Contract-bound import опубликованного master с publication/file/grid proof.
4. Safe export доказанных bot-визитов.
5. Evidence-bound enrichment личных книг без очистки ручного текста.
6. Следующий `registry_cycle` и немедленный нулевой dry-run.

Legacy `razbros.py`, `daily_cycle.py` и `restore_from_bot.py` не являются
штатными write-путями и по умолчанию работают fail-closed. Конфликт двух
непустых значений одного поля для одного КД больше не выбирается: любой
повтор канонического КД блокирует весь import до первой записи. Import без
явного `published_master`/`test_subset`, exact contract binding и source proof
также блокируется. Для `test_subset` proof обязан перечислить точные КД и
ответственных; его строки не могут стать office overlay. Одного корректного
proof недостаточно: перед HTTP-import его exact digest одноразово
регистрируется локальным root-only CLI в production SQLite. Сервер атомарно
связывает запись данных, consumed receipt и монотонный source head; старый,
просроченный, незарегистрированный или повторно подменённый источник
отклоняется.

Семантика формы, proven snapshot, bot → Excel mapping, обратного office overlay
и допустимых источников импорта находится в одном
`data_exchange_contract.json`. Контракт version+SHA-locked и вшит в snapshot,
file-map, enrichment и clearance receipts. Он также фиксирует exact лист
`Портфели`, обязательные/служебные заголовки, aliases, duplicate policy и
схемы source proof. Поэтому смена смысла поля требует новой версии и полного
rehearsal, а не локальной правки одного из скриптов.

Перестановка существующих колонок не требует кода. Переименование без смены
смысла добавляется как versioned alias; новая служебная колонка — в exact
allowlist (регулярная серия допускает только узкий pattern); неизвестная,
пропавшая или дублирующаяся колонка останавливает импорт до записи. На
сохранённом production master все 87 колонок классифицированы текущим контрактом;
число используемых и служебных выводится из самой схемы, а не служит ожидаемой
константой. Unknown/missing/duplicates — 0. Для опубликованного
master обязательна следующая сразу за заголовком строка типов
`сх`/`хард`/`софт`; она сверяется по каждой распознанной колонке и никогда не
импортируется как клиент.

Геометрия личных книг отдельно зафиксирована в `registry_cycle/config/app.json`
schema v2 как `root/group/employee/*.xlsb`; группы республик, включая
`ГПХ Агент выезд`, задаются config. Неизвестная активная ветка и пока не
поддержанный `.xlsx/.xlsm/.xls` дают STOP с точным путём.

Все entrypoint публикации, first-baseline, rehearsal, execute, distribution и
reconcile используют один tokenized `cycle.lock`. Stale owner сначала атомарно
переносится в audit-tombstone; старый owner не может удалить новый lock.
Terminal журнала обязан совпасть с последним `publish_started` по `run_id` и
`plan_digest`. Reconcile повторно проверяет каждый live-файл перед записью,
весь пакет перед baseline и live+baseline непосредственно перед terminal.

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
точно известные application tables и pending-разрешения импорта; consumed
receipts и source heads сохраняет как anti-replay audit. Фото перемещает в
rollback-карантин.
Операторский запуск описан в [`DEPLOY.md`](DEPLOY.md).
Legacy `/admin/reset` безусловно fail-closed и не может обойти этот контур.
