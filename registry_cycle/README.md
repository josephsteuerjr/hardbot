# Контур · Реестры

Единый безопасный цикл для четырёх городов HARD и контуров SOFT. Старые скрипты
сборки/разборки не меняются и автоматически не запускаются.

## Что гарантирует цикл

- живые книги сначала копируются в `_local`; чтение выполняет `python-calamine`;
- Excel COM (`xlwings`) пишет только staging-копии;
- 21 жёлтое поле принадлежит сотрудникам: собирается вверх и не
  перезаписывается вниз из общего у уже существующей строки;
- пустое жёлтое значение сотрудника не очищает мастер и не заполняется обратно
  старым значением мастера;
- `Телефон` и `Город` сохраняются из личного файла, если мастер пуст;
- `Дата выезда` переносится буквально без массовой нормализации;
- аналитический блок месячных оплат не попадает в личные книги;
- на границе месяца `Текущие платежи` общего архивируются в
  `Оплаты за MM.YY`, а у сотрудников очищаются только жёлтые
  `Сумма последнего платежа` и `Дата последнего платежа`;
- прошлый успешный baseline даёт трёхсторонний merge;
- независимые разные правки одного жёлтого поля блокируют весь цикл;
- правка жёлтого поля только в мастере возвращается к версии сотрудника;
- строка снимается со старого сотрудника только при совпадении с `пред отв`;
- при доказанном перераспределении жёлтая история передаётся новому владельцу;
- перед публикацией проверяется каждая ячейка мастера, каждая проекция и сигнатуры
  нетронутых листов;
- публикация выполняется через sibling-temp, optimistic-lock и rollback по SHA-256;
- занятые файлы поддерживают явные режимы `block`, `sidecar` и делегированный
  `force_close`;
- общий реестр заменяется последним;
- технический итог уходит только владельцу через `/admin/notify` → `ADMIN_ID`.

## Запуск

Двойной клик по `Контур — Реестры.bat` или:

```powershell
python -m registry_cycle gui
```

Полезные команды:

```powershell
python -m registry_cycle discover
python -m registry_cycle plan
python -m registry_cycle stage --snapshot <run>\snapshot.json
python -m registry_cycle validate --snapshot <run>\snapshot.json --stage <run>\staging\stage.json
python -m registry_cycle publish --snapshot <run>\snapshot.json --stage <run>\staging\stage.json --confirm-digest <sha256>
python -m registry_cycle execute
python -m registry_cycle rehearse-first-baseline --snapshot <run>\snapshot.json --summary
python -m registry_cycle promote-first-baseline --snapshot <run>\snapshot.json --stage <candidate>\staging\stage.json --candidate-baseline <candidate>\candidate-baseline.json.gz --confirm-digest <sha256>
python -m registry_cycle scheduler status
python -m registry_cycle scheduler install
```

`execute --publish --scheduled` предназначен только для Планировщика Windows.
Задача создаётся с `InteractiveToken`: пользователь должен быть вошедшим в Windows,
иначе Excel COM не запускается.

Закрытие месяца автоматически включается только в последний календарный день.
Задача поэтому настроена на все семь дней недели. Для контролируемого пропущенного
запуска есть явный параметр `--rollover-month YYYY-MM`; после начала нового месяца
он не применяется автоматически, чтобы не принять новые оплаты за старые.

## Документы приёмки

- [Отчёт для руководителей](REPORT_FOR_MANAGEMENT_2026-07-28.md)
- [Технический приёмочный отчёт](REHEARSAL_REPORT_2026-07-28.md)
- [Подробное описание логики](SCRIPT_GUIDE_2026-07-28.md)
- [Решения встречи](MEETING_NOTES_2026-07-28.md)
- [Техническое задание](MODERNIZATION_TZ_2026-07-28.md)

## Аддитивный ночной разброс

Пока первый baseline полного merge не очищен, новые закрепы раскладываются отдельным
режимом. Общий реестр в нём только читается, существующие строки личных файлов не
перезаписываются, а КД, исчезнувшие из общего, не удаляются:

```powershell
python -m registry_cycle.distribution prepare --assignment-baseline <master-before-assignments>.xlsb
python -m registry_cycle.distribution plan --snapshot <run>\snapshot.json --assignment-baseline <master-before-assignments>.xlsb
python -m registry_cycle.distribution stage --snapshot <run>\snapshot.json --plan <run>\distribution\plan.json
python -m registry_cycle.distribution validate --plan <run>\distribution\plan.json --stage <run>\distribution\staging-*\stage.json
python -m registry_cycle.distribution preflight --plan <run>\distribution\plan.json --stage <run>\distribution\staging-*\stage.json
python -m registry_cycle.distribution publish --plan <run>\distribution\plan.json --stage <run>\distribution\staging-*\stage.json --confirm-digest <sha256>
```

`prepare` выполняет весь безопасный dry-run до точки подтверждения: discovery,
свежий snapshot, план, Excel staging, validation и live-preflight.
Каждая новая строка должна иметь пустого ответственного в assignment-baseline и
конкретного ответственного в текущем общем реестре. После staging проверяются все
старые и новые ячейки, листы и фильтры. Публикация возможна только при совпадении
digest, неизменных общем и личных live-файлах и отсутствии Excel lock; перед
заменой создаётся соседняя копия `(... до ночного разброса ...)`. Успешная
публикация сохраняет отдельный provenance-manifest, поэтому добавленные из общего
строки не считаются последующими правками сотрудников.

Этот режим пока не устанавливается в Планировщик автоматически. Для регулярного
запуска ему нужны отдельный жизненный цикл assignment-baseline и очередь закрепов,
для которых ещё нет активного личного файла. Существующая задача полного
`execute --publish --scheduled` не должна подменять этот режим.

## Аддитивное восстановление из архивов

Восстановление отделено от обычной сборки/разборки и по умолчанию работает только
с полями из `config/recovery.json`:

```powershell
python -m registry_cycle.recovery prepare --snapshot <run>\snapshot.json
python -m registry_cycle.recovery stage --snapshot <run>\snapshot.json --plan <run>\recovery\plan.json
python -m registry_cycle.recovery validate --plan <run>\recovery\plan.json --stage <run>\recovery\staging-*\stage.json
python -m registry_cycle.recovery publish --plan <run>\recovery\plan.json --stage <run>\recovery\staging-*\stage.json --confirm-digest <sha256>
```

Контур восстанавливает значение только в пустую ячейку того же КД и того же
ответственного. Расходящаяся история пропускается. Комментарии текущего месяца
не берутся из точек раньше 6 июля, чтобы не вернуть июньский перенос.

Перед staging архивные значения автоматически сверяются с копией общего реестра:
допустимы только пустота или точное совпадение. Отличающееся непустое значение,
чужой ответственный или отсутствующий КД блокируют пакет. Изменения больше
300 ячеек или 25% строк одного файла также требуют отдельного доказательного
разбора.

Публикация не открывает живые книги через Excel. Она проверяет lock-файл и SHA,
создаёт соседнюю копию `(... до восстановления из архивов ...)`, заменяет только
неизменившийся файл и при любой ошибке откатывает уже заменённые книги.

## Конфигурация

- `config/app.json` — пути, города, безопасность, расписание и owner-only уведомления;
- `config/fields.json` — HARD/SOFT/shared, типы, aliases и политика конфликтов;
- `config/distribution.json` — aliases закрепов и лимиты аддитивного ночного разброса;
- `config/recovery.json` — архивные точки, VSS, разрешённые поля и лимиты восстановления;
- `config/theme.json` — Graphite UI.

В конфиге явно активны Душанбе, Худжанд, Ташкент, Самарканд и SOFT обеих стран.
Андижан известен, но отключён. Файлы с префиксом `СОФТ ` получают профиль SOFT
даже внутри папки HARD-сотрудника.

## Первый запуск

Baseline появляется только после успешно проверенной публикации. До этого:

- пустой мастер может быть заполнен из личного файла;
- одинаковые значения считаются безопасными;
- дополнение старого комментария может быть принято как superset;
- разные непустые значения блокируются и требуют осознанного решения.

Нельзя создавать baseline «для галочки» поверх неразобранных различий — это уничтожит
информацию о том, с какой стороны возникла правка. Первая репетиция остаётся
непубликуемой; применить её можно только отдельным
`promote-first-baseline`, который заново строит план, повторяет validation,
требует полный digest и проверяет неизменность live-файлов.

## Регламент перед первой публикацией

1. Дождаться закрытия всех рабочих книг.
2. Построить **свежий** snapshot, а не использовать дневную копию.
3. Разобрать отсутствующие КД, неверные типы и конфликты первого baseline.
4. Повторить план до состояния без ошибок.
5. Собрать staging и получить зелёный validation.
6. Сверить полный digest.
7. Для самого первого запуска выполнить `promote-first-baseline` в режиме
   занятости `block`; для следующих запусков применяется штатный цикл.

Тестовый snapshot `dev-full-001` от 27.07.2026 не является разрешением на live:
он нужен для воспроизводимого тестирования и уже устарел относительно работы сотрудников.
