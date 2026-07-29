# Политика обмена данными: бот, личные реестры и общий реестр

Статус: норматив ограниченного тестирования.
Дата фиксации: 29.07.2026.
Область: `hardbot2`, Tk-пульт и `registry_cycle` для реестров СНГ.

Эта политика задаёт единственный разрешённый порядок движения данных. Если
поведение скрипта, кнопки или устной инструкции расходится с документом,
операция останавливается до отдельного разбора. Частичный успех не считается
успехом пакета.

## 1. Текущее подтверждённое состояние

Первый live baseline опубликован 29.07.2026 в 04:05:57:

- run ID: `first-live-20260729-0340-01-candidate`;
- подтверждённый digest:
  `0bc77e620a98f9b66139e20a35c31395a9c4c8a06bdaec74f9e4cd08622ba6c5`;
- опубликовано 59 из 59 файлов: общий реестр и 58 личных книг;
- создано 59 соседних backup-копий;
- live SHA совпал со staging для 59 из 59 файлов;
- backup SHA совпал с состоянием live до публикации для 59 из 59 файлов;
- sidecar, ошибок публикации и rollback — 0;
- активный baseline побайтно совпал с проверенным `baseline.next.json.gz`;
- в общий реестр собрано 11 084 изменения из 21 жёлтого поля;
- свежий post-live цикл `post-live-20260729-0410-01` дал 0 изменений
  общего реестра и 0 ошибок validation.

Источник проверки:
`_local/registry_cycle_v2/runs/first-live-20260729-0340-01/`
`NIGHT_LIVE_AUDIT_2026-07-29.md`.

Планировщик и автопубликация остаются отключёнными:
`scheduler.enabled=false`, `scheduler.publish=false`, задача Windows Scheduler
нового `registry_cycle` не установлена, `force_close` выключен. Наличие старых
legacy-задач проверяется отдельно; перед пилотом они должны быть отключены.
До отдельного решения владельца разрешён только ручной цикл в режиме `block`.

## 2. Матрица источников истины

| Объект данных | Источник истины | Разрешённое направление | Запрещено |
|---|---|---|---|
| `Номер КД / ID`, текущий `ответственный Сотрудник`, `пред отв`, профиль, долг, исходный адрес и прочие нежёлтые поля | опубликованный общий реестр | общий реестр → личная книга; опубликованный общий реестр → БД | выводить владельца из имени файла, старой личной копии или состояния БД |
| 21 жёлтое поле | живая личная книга канонического текущего сотрудника | личная книга → общий реестр | перезаписывать существующую личную ячейку старым значением общего |
| Пустая жёлтая ячейка существующей личной строки | личная книга | остаётся пустой локально; при сборке не очищает непустой master | трактовать пустоту как команду очистить master или заполнять её старым master-значением |
| Нежёлтые `Телефон` и `Город`, когда master пуст | непротиворечивое значение живой личной книги | сохранить локально | очищать только из-за пустого master |
| Канонический владелец при передаче кейса | текущий `responsible` общего реестра; прежний владелец доказывается точным `пред отв` | удалить строку у прежнего только при доказательстве; жёлтую историю передать новому как fallback | удалять спорную копию или брать из неё данные без доказательства |
| Завершённый визит приложения | служебный `bot_visit_data` и `_bot_completed`, созданные фактическим `POST /api/save` | safe export → точечное обогащение личной книги → общий реестр | считать raw `visit_date` или старый импорт доказательством визита |
| Выбранный выездником результат | доказанный bot snapshot, внутренний ключ `call_status` | в Excel только → `Общий результат` | писать в `Cтатус звонка` или `комментарий звонка` |
| Наводки `tips` | доказанный bot snapshot | preserving/idempotent merge → `Доп. Адрес/Доп.Номер` | брать raw `extra_address` как результат бота или стирать ручной текст |
| Комментарий выезда | доказанный bot snapshot плюс офисная правка, дошедшая через опубликованный master | preserving/idempotent merge → `Комментарий текущего месяца выезда` | переносить в call-comment или очищать существующий ручной текст |
| HARD/SOFT call-поля | офисная/личная книга соответствующего контура | личная книга → общий реестр как жёлтые поля | изменять обычным bot enrichment; выездники не звонят |
| Офисная правка после bot-визита | личная книга канонического владельца | personal → `registry_cycle` → опубликованный master → explicit master import → anchored office overlay → следующий safe export | любой personal → bot import, скрытый двусторонний импорт внутри enrichment |
| Историческое восстановление | отобранные архивы личной книги того же владельца | только `registry_cycle recovery`, только в пустую ячейку, после master-audit | использовать raw `mode=full` БД как универсальный архив или восстанавливать целую строку/файл |
| `Текущие платежи` и `Оплаты за MM.YY` | общий реестр | остаются master-only; rollover общего выполняется отдельно | раздавать аналитический блок в личные книги |
| Два жёлтых поля последнего платежа сотрудника | личная книга до закрытия месяца | собираются вверх; в последний календарный день очищаются в личных книгах | смешивать их с архивом аналитических `Текущих платежей` |
| SQLite hardbot | операционный слой приложения, не первичный реестр | загрузка из опубликованного master или утверждённой test-выборки | считать raw `clients` без provenance окончательным источником для Excel |
| Фото и геометки | `HARDBOT_DATA/photos` плюс `photo_geo` | сохранять и очищать как единый объект | очищать только счётчик `photos_count`, оставляя файлы или геометки |

## 3. Обязательный ручной порядок

### 3.1. До обмена

1. Владелец данных утверждает окно, состав книг и тестовую выборку.
2. Оператор фиксирует пути, SHA и число входных файлов.
3. Все целевые Excel-книги закрыты; Excel lock-файлы отсутствуют.
4. Legacy `Разброс`, `daily_cycle`, `restore_from_bot --apply`,
   автопубликация и `force_close` выключены.

### 3.2. Правки офиса и результат бота

1. Если после последней выгрузки в личных книгах были ручные или офисные
   правки, сначала выполняется полный `registry_cycle`: snapshot → plan →
   staging → validation → publish. Напрямую личные книги в hardbot не
   загружаются.
2. В hardbot явно импортируется только опубликованный master с
   `source_kind=published_master` и source proof, привязанным одновременно к
   SHA файла, digest переданного grid и complete receipt/journal именно его
   `registry_cycle`-публикации. Для физически утверждённой пилотной
   подвыборки используется `source_kind=test_subset`: proof содержит exact
   approval id и полный `{КД: ответственный}` allowlist, который обязан
   совпасть с фактическими строками. Exact proof предварительно и одноразово
   регистрируется локальным root-only CLI hardbot с TTL; HTTP-регистрации нет.
   Import, consumed receipt и монотонный source head фиксируются одной
   транзакцией. Незарегистрированный, просроченный, устаревший или collision
   proof блокируется до записи; текущий exact replay возвращается как явно
   помеченный zero-write.
3. Оператор проверяет отчёт master-import:
   `unknown_columns`, `duplicates`, `protected_cells`,
   `office_cells`, `isolated_call_cells`, `no_responsible`.
4. В master-import «Общий результат» при совпавшей «Дате выезда»
   reverse-map-ится во внутренний visit-result `call_status`;
   `Cтатус звонка` и `Комментарий звонка` никогда не становятся visit overlay.
5. Затем получается только safe export с доказанными bot-визитами.
6. Выполняется evidence-bound bot → personal enrichment. Он пишет только
   разрешённые непустые поля; call-поля не меняет. Legacy Tk-кнопки записи
   на ограниченный тест закрыты fail-closed и не считаются этим этапом.
7. Ошибка хотя бы одной книги означает `STOP`; следующий цикл не запускается.

### 3.3. Общий реестр

1. После enrichment и закрытия книг снимается свежий snapshot.
2. Выполняются plan → staging → полная validation.
3. Проверяются warnings, список изменений и полный digest.
4. Публикация выполняется вручную только с тем же подтверждённым digest,
   в `busy_mode=block`.
5. Общий реестр публикуется последним.
6. После публикации проверяются backups, SHA, journal, active baseline
   и немедленный нулевой post-publish dry-run.
7. `publish_started` без terminal-event и доказанного complete-result означает
   `STOP`. Новый цикл и автоматическое удаление stale lock запрещены до
   `reconcile-publish`, который сводит пакет только к проверенному all-old
   либо к all-new, если каждая live-цель уже точно равна staging.
8. Все publication/rehearsal/first-baseline/execute/distribution/reconcile
   entrypoint используют один tokenized `cycle.lock`. Stale owner атомарно
   переносится в tombstone, release возможен только своим owner token.
9. Terminal закрывает только последний `publish_started` с теми же `run_id` и
   `plan_digest`. Reconcile повторяет exact-проверку перед каждым replace,
   перед baseline и финально по всему live+baseline перед terminal.

### 3.4. Загрузка в БД и подготовка пилота

1. Источник загрузки — только опубликованный общий реестр с явным
   `source_kind=published_master`, exact contract binding и проверенным
   publication/grid/file proof; personal → bot отсутствует.
2. Перед очисткой БД обязательны:
   online SQLite backup, SHA-256, `PRAGMA quick_check`, финальный safe export
   и ограниченный raw diagnostic export для расследования.
3. Очистка выполняется по утверждённой retention-матрице. Удаление только
   `clients`, `visits`, `markers` недостаточно: отдельно проверяются
   `photo_geo`, `audit`, пользователи/токены/ссылки, bindings/roles/settings
   и физический каталог `HARDBOT_DATA/photos`.
4. После очистки загружается только физически отфильтрованная и проверенная
   test-выборка. Нельзя запускать `--allow-new` на полном master.
5. Для каждого тестировщика проверяются точные КД, `responsible`,
   `/api/bootstrap`, отсутствие чужих кейсов, старых визитов и старых фото.
6. Известный конфликт КД `35854193` исключается из пилотной выборки, пока
   владелец данных не оставит в опубликованном master ровно одну проверенную
   строку с однозначным ответственным.
7. До proven `bot_visit_data` server-form пуста для всех bot-owned полей.
   Raw master разрешён только как read-only контекст; localStorage-черновик
   хранится отдельно. Finish старой оболочки, без перечня явно затронутых
   полей или без выбранного результата блокируется до любых записей.
8. Reset удаляет application state и pending import authorizations, но
   сохраняет consumed receipts/source heads: очистка тестовых данных не должна
   разрешить повторное применение старого master.

## 4. Организационные роли

Один человек может совмещать несколько ролей, но в журнале указывается,
кто выполнил и кто проверил каждое действие.

| Роль | Ответственность |
|---|---|
| Владелец данных | утверждает матрицу, окно публикации, test-scope и исключения |
| Оператор реестров | import/enrichment, snapshot, plan, staging, validation и publish |
| Офисный оператор | подтверждает ручные правки в personal и результат последующего published-master import |
| Администратор hardbot | backup/quick-check, очистка БД и фото, загрузка test-subset |
| Координатор теста | список тестировщиков, bindings/roles и сценарии приёмки |
| Независимый проверяющий | сверяет SHA, counts, digest, post-run и отсутствие чужих данных |

## 5. STOP-критерии

Пакет немедленно останавливается, если выполняется хотя бы одно условие:

- целевой Excel-файл открыт, изменился после snapshot или недоступен;
- plan/validation содержит error либо digest не совпадает;
- есть необъяснённый duplicate КД, неоднозначный owner или конфликт baseline;
- в hardbot-import повторился любой канонический КД, включая одинаковые,
  дополняющие строки и числовые эквиваленты (`12345`/`12345.0`);
- фактическая жёлтая заливка не совпадает с конфигурацией 21 поля;
- enrichment/import завершился частично либо потерял файл/папку сотрудника;
- обнаружены unknown columns, которые могут менять смысл данных;
- отсутствует exact лист `Портфели`, обязательная колонка, либо найден
  нормализованный дубль любого заголовка;
- source proof не совпал с contract version, grid, SHA файла,
  publication receipt или exact test-scope;
- офисные правки не прошли personal → registry_cycle → published master →
  explicit master import до следующего enrichment;
- обнаружен `publish_started` без terminal-event/доказанного complete-result;
- пытаются использовать raw `visit_date` как provenance;
- форма до proven snapshot префиллит raw master либо finish не доказывает
  явный выбор результата и затронутых пользователем полей;
- пытаются писать legacy `Разброс`, `daily_cycle` или
  `restore_from_bot --apply` без отдельного аварийного допуска;
- нет verified SQLite backup, `quick_check=ok` или утверждённой
  retention-матрицы перед очисткой;
- после очистки остались старые записи, токены/привязки вне test-scope,
  `photo_geo` или файлы фото;
- post-publish dry-run не нулевой или active baseline не подтверждён.

## 6. GO-критерии ограниченного тестирования

Допуск возможен только одновременно при всех условиях:

- active live baseline существует и относится к текущему master;
- нет незавершённого publish journal или pending sidecar;
- ручной post-publish цикл зелёный и идемпотентный;
- legacy write-paths заблокированы по умолчанию;
- scheduler и force-close выключены;
- test-subset утверждён владельцем и проверен независимо;
- БД и фото очищены по retention-матрице, затем загружен только test-subset;
- у каждого тестировщика корректны binding, bootstrap и видимые КД;
- приложены backup SHA, counts до/после, import/enrichment отчёты,
  plan digest, validation и итог независимого проверяющего.

## 7. Как безопасно менять формат или принцип обмена

Изменение не делается «по месту» в одном из скриптов. Технический источник
семантики bot ↔ Excel:

```text
hardbot/data_exchange_contract.json
```

Его одновременно читают hardbot, `pult/enrich.py`, snapshot/reset и
`registry_cycle/clearance.py`. Контракт содержит:

- версию протокола формы и перечень полей, которые вправе прислать выездник;
- exact лист, header-policy, обязательные колонки, aliases и допустимые
  служебные master-заголовки;
- обязательную immediate type-row опубликованного master и ожидаемый
  `сх`/`хард`/`софт` source-tag каждой распознанной колонки;
- marker/date и полный состав доказанного bot snapshot;
- source → target и merge-mode каждой Excel-записи;
- запрещённые call-targets;
- обратный mapping опубликованного master в anchored office overlay;
- допустимые `source_kind` загрузки в БД;
- схемы source proof для опубликованного master и утверждённого test subset;
- версии snapshot, file-map, enrichment и clearance receipts.

Версия связана с точным SHA-256 файла. Правка байтов без новой явно
поддержанной версии останавливает импорт модулей. Snapshot/evidence/clearance
также несут contract binding; старый и новый контуры нельзя случайно смешать.

### 7.1. Матрица стоимости изменений

| Изменение | Штатный способ | Что обязательно проверить |
|---|---|---|
| Переставили существующие колонки | ничего менять не нужно: поиск идёт по нормализованному заголовку | rehearsal на копии и нулевой повтор |
| Переименовали известную жёлтую колонку без смены смысла | добавить alias в `registry_cycle/config/fields.json` | old/new header fixtures, цвет, тип и профиль |
| Переименовали master-колонку, которую читает bot, без смены смысла | versioned alias в `data_exchange_contract.json` | old/new master fixtures, duplicate collision и полный import |
| Добавили заведомо служебную master-колонку | exact `allowed_ignored_headers`; только регулярная серия вроде `Оплаты за MM.YY` получает узкий pattern | positive fixture и nearby-invalid negative fixtures |
| Добавили новую группу той же геометрии | запись в `registry_cycle/config/app.json`, без Python-ветки | required/optional, profile hard/soft, manifest |
| Изменилась обязательность группы | только config + утверждение владельца | отсутствие required должно STOP; optional — warning |
| Появилась лишняя вложенность/другая геометрия | новая `app.json schema_version` и явный discovery-adapter | exact path errors, отсутствие recursive import, полный rehearsal |
| Сменился формат `.xlsb` на `.xlsx/.xlsm/.xls` | новая версия format-adapter после отдельного решения; текущий discovery намеренно STOP | type/formula/date/color fixtures и побайтовая гарантия публикации |
| Изменился набор жёлтых полей или их направление | новая версия `fields.json`, policy review и миграционный план | colour/type/profile/direction matrix, baseline compatibility |
| Переименовали bot/Excel поле либо изменили merge | новая версия `data_exchange_contract.json` и новый разрешённый SHA | форма → snapshot → personal → master → overlay → safe export |
| Изменили источник истины или порядок personal/master/bot | не runtime-флаг; отдельное архитектурное изменение | миграция, rollback, dual-version compatibility и независимая репетиция |

Текущая геометрия зафиксирована как `app.json schema_version=2`:
`root/group/employee/*.xlsb`. Импорт остаётся только одноуровневым.
Рекурсивный обход используется исключительно как аудит аномалий. Активный
`.xlsb` прямо в корне республики, прямо в группе, глубже папки сотрудника или
в незарегистрированной группе даёт `STOP` с точным путём; backup/`~` и
настроенные служебные префиксы не считаются активными книгами.

Группы и республики не кодируются Python-ветками. `ГПХ Агент выезд` сейчас
описывается config: в UZ как required/profile hard, в TJ как optional/profile
hard. Добавить ту же группу в другую республику при той же геометрии — правка
config и fixtures, а не изменение discovery. Активный `.xlsx/.xlsm/.xls`
на любой рабочей глубине пока блокируется как
`unsupported_active_workbook_format` с exact path: новый формат не должен
молча выглядеть как отсутствие книг.

Фактическая master-форма проверена на сохранённом production snapshot:
лист `Портфели`, строка заголовка 2, всего 87 колонок; 52 уникальные колонки
используются hardbot, 35 явно разрешены как служебные, unknown/missing/duplicate
равны нулю. Разрешённые 35 — 29 exact названий и месячная серия
`Оплаты за 01.26`…`Оплаты за 06.26`, допускаемая только шаблоном валидного
месяца `01..12` и двухзначного года. Следующая строка master — обязательная
строка типов; она валидируется и пропускается, поэтому значение `сх` не может
стать ложным КД.

### 7.2. Обязательный change-процесс

1. Зафиксировать старую contract/config version, SHA, реальные заголовки и
   геометрию на копии live-share.
2. Классифицировать изменение как rename, add, geometry, semantic mapping
   либо source-of-truth change. Не объединять разные классы в один «малый»
   патч.
3. Повысить соответствующую версию. Для semantic mapping обновить SHA allow-list
   загрузчика; простая правка JSON без этого обязана падать.
4. Добавить positive fixture нового формата и negative fixture старого/
   неоднозначного формата. Неизвестный смысл всегда `STOP`, не best-effort.
5. Прогнать полный цикл на физических копиях: snapshot → plan → staging →
   validation → publish в изолированную цель → нулевой повтор.
6. Для bot-полей отдельно прогнать форму → proven snapshot → enrichment →
   registry cycle → published-master import → следующий safe export.
7. Зафиксировать migration/rollback: какие старые snapshots/receipts
   поддерживаются. По умолчанию unversioned и другая версия несовместимы.
8. Только после независимой сверки digest/counts/нулевого повтора менять live.

Source proof защищает от случайной подмены/устаревшего операторского артефакта
и связывает этапы одного цикла; это не замена авторизации. Злоумышленник с
действующим admin token остаётся внутри доверительной границы, поэтому token,
ACL операторских run-артефактов и независимая сверка receipt обязательны.

## 8. Аварийные исключения для legacy-кода

Read-only/dry-run старых инструментов допускается для диагностики. Запись
заблокирована по умолчанию и требует отдельного флага:

- `daily_cycle`: `HARDBOT_EMERGENCY_ALLOW_DAILY_CYCLE_WRITE=1`;
- `restore_from_bot --apply`:
  `HARDBOT_EMERGENCY_ALLOW_RESTORE_FROM_BOT_WRITE=1`.

Флаг является не штатным режимом, а аварийным обходом. Для его применения
нужны отдельное разрешение владельца, номер инцидента, backup и точный
перечень целей. Значение устанавливается только в окружении одного процесса
и удаляется сразу после него. Любое значение, кроме точного `1`, считается
запретом.

## 9. Техническая фиксация fail-closed

Локально, без деплоя, добавлены следующие уровни защиты:

- `pult/daily_cycle.py`: ранний отказ CLI до чтения настроек, сети, journal
  и lock; повторный guard внутри `step_export()` и `step_import()`;
- `pult/restore_from_bot.py`: ранний отказ `--apply` до чтения настроек и
  сети; повторный guard внутри `apply_plan()`;
- legacy `pult/razbros.py`: write-функция fail-closed до открытия Excel,
  apply-кнопки пульта заблокированы;
- `hardbot/db.py` и admin API: import без явного `published_master`/
  `test_subset`, exact contract binding и source proof блокируется до записи;
  legacy multipart отвечает 409; любой повтор канонического КД
  блокирует весь import, одинаковые и дополняющие строки не сливаются;
  root-only pre-registration, TTL, one-shot consume и monotonic source head
  образуют серверный anti-replay ledger;
- `hardbot/data_exchange_contract.json`: version+SHA-locked единая семантика
  формы, workbook shape/aliases/ignored columns, provenance, Excel mapping,
  office overlay, DB source kinds и source-proof schemas;
  несовместимый snapshot/file-map/evidence/clearance блокируется до записи;
- `registry_cycle/config/app.json schema_version=2`: явная геометрия
  `root/group/employee/*.xlsb`; неожиданный активный файл на другой глубине
  либо в незарегистрированной ветке блокирует discovery с exact path;
  exact config identity и payload закрепляются snapshot → plan → stage, а
  distribution/recovery не перечитывают изменяемый JSON во время исполнения;
- Tk/Tauri-пульт: normal personal-books → hardbot batch import удалён;
  один файл маркируется только как опубликованный master;
  прямой import/legacy reset на ограниченный тест закрыты до file picker/API;
  оператор использует только `prepare → root register → apply`, HTTP redirect
  запрещён;
- master-import: `overall_result` reverse-map-ится в anchored visit overlay;
  `call_status`/`call_comment` изолированы, `visit_date` остаётся только якорем,
  пустота ничего не очищает;
- `registry_cycle/publishing.py`: обычный publish запрещён без уже активного
  baseline; first baseline остаётся только за явным
  `promote-first-baseline`; незавершённый journal блокирует новый publish и
  stale-lock removal, `reconcile-publish` допускает только verified all-old
  либо exact-staged all-new с соответствующим baseline; terminal exact-match
  последнему start, а TOCTOU-barriers повторяют проверки до write/baseline/
  terminal;
- `registry_cycle/cycle_lock.py`: уникальный owner token, OS-serialized stale
  claim в audit-tombstone и compare-before-release; один lock используется
  всеми publication entrypoint;
- `hardbot/web.py` и PWA v15: до proven snapshot форма пуста, local draft
  отделён от raw, finish принимает только явно затронутые поля и обязательный
  результат; log/snapshot/notifications не дочитывают пропущенные HARD-поля;
- `hardbot/limited_test_reset.py`: отдельный операторский контур делает
  SQLite backup, safe/raw exports, export schema и хэшированный photo
  manifest, проверяет неизменность состояния и только затем по confirm-token
  очищает все application tables и pending authorizations; consumed receipts
  и source heads сохраняются, каталог фото перемещается в карантин, а не
  удаляется;
- production/local admin secrets отсутствуют во всех tracked Python/HTML/
  Compose-файлах; локальный/production env остаётся единственным источником,
  а скомпрометированное историей значение требует ротации до пилота;
- `/admin/reset` всегда fail-closed `409`; очистка возможна только через
  snapshot/clearance-bound reset, а consumed import replay не запускает
  post-import geocode или другой mutator;
- оба CLI возвращают код `4` при штатной блокировке;
- dry-run/report остаются доступны без аварийного флага;
- разрешающим считается только точное значение `1`.

Локальная проверка актуальной границы:

```text
формы/provenance + publish/reconcile/lock/rehearsal/distribution: 60 passed
полный suite фиксируется после последней правки clearance/reset-контура
python -m compileall -q hardbot pult registry_cycle: OK
```

Отдельный CLI-smoke без env-флагов подтвердил:
`daily_cycle_exit=4`, `restore_from_bot_exit=4`. Прод и live-реестры этой
проверкой не изменялись.

Fault-injection после первого и второго live `os.replace` подтверждает:
следующий publish получает `STOP`; mixed source/staged сводится в verified
all-old, полностью staged-пакет — в all-new с candidate baseline.
