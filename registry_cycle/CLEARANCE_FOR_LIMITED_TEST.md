# Допуск к очистке hardbot перед ограниченным тестом

Этот затвор не объединяет hardbot с ночным циклом и не повторяет его функции.

- `pult.enrich` остаётся единственным штатным способом точечно перенести
  доказанные визиты бота в личные книги.
- `registry_cycle` остаётся единственным контуром обмена personal ↔ master,
  полной validation и публикации.
- hardbot только создаёт неизменяемый pre-reset пакет и позднее проверяет
  выданный локальным затвором receipt.
- `registry_cycle.clearance` ничего не переносит и не публикует. Он только
  читает уже созданные доказательства, сверяет их и выдаёт допуск.

## Порядок

После deploy и live-канареек:

```powershell
python _local\reset_hardbot2_for_limited_test.py prepare-clearance
```

Команда не останавливает бот и не очищает данные. Она создаёт согласованный
online-backup, safe/raw exports и полный архив фото, скачивает пакет локально и
печатает точный путь `snapshot`.

Из snapshot создаётся non-authorizing file map:

```powershell
python -m registry_cycle.clearance enrichment-template `
  --snapshot-dir <snapshot> `
  --output <snapshot>\enrichment-file-map.json
```

В нём оператор заполняет только `files` для каждого уже вычисленного owner.
Поля `snapshot`, `responsible` и `contract_ids` не редактируются:

```json
{
  "schema": "hardbot.pult-enrichment-map.v2",
  "snapshot": {
    "manifest_sha256": "<sha256>",
    "database_state_digest": "<sha256>",
    "safe_export_sha256": "<sha256>",
    "raw_export_sha256": "<sha256>",
    "data_exchange_contract_sha256": "<sha256>"
  },
  "owners": [
    {
      "responsible": "Сотрудник",
      "contract_ids": ["KD-1", "KD-2"],
      "files": ["U:\\...\\Сотрудник.xlsb"]
    }
  ]
}
```

Машинный запуск существующего enrichment writer одновременно создаёт
авторизующее доказательство:

```powershell
python pult\enrich.py evidence-run `
  --snapshot-dir <snapshot> `
  --file-map <snapshot>\enrichment-file-map.json `
  --output <snapshot>\pult-enrichment-receipt.json
```

Эта команда сама берёт exact safe export, выполняет штатный точечный writer,
запоминает SHA до/после и backup, вновь открывает каждый Excel read-only и
требует нулевой повторный plan. Receipt
`hardbot.pult-enrichment.v2` формируется кодом; вручную заполнять или
пересобирать его из счётчиков нельзя.

Snapshot, file-map, enrichment receipt и итоговый clearance связаны с exact
`server/data_exchange_contract.json` (schema, version, SHA-256). Артефакт
старой/другой версии блокируется до открытия Excel и тем более до очистки БД.

После этого оператор отдельно продолжает существующий порядок:

1. штатные snapshot → plan → staging → validation → publish через
   `registry_cycle`;
2. немедленный post-publish snapshot → plan → staging → validation с нулём
   изменений;
3. выдача и повторная проверка clearance receipt.

Ночной цикл самодостаточен. Ни hardbot, ни reset, ни clearance не запускают и
не воспроизводят его transfer/publish-логику. Любой orphan, непокрытый КД,
ошибка файла, отсутствующая колонка или ненулевой повторный план означает
`STOP`.

## Выдача допуска

Для обычной публикации:

```powershell
python -m registry_cycle.clearance issue `
  --snapshot-dir <snapshot> `
  --enrichment-evidence <snapshot>\pult-enrichment-receipt.json `
  --workspace <registry-workspace> `
  --mode published `
  --registry-snapshot <published-run>\snapshot.json `
  --plan <published-run>\plan.json `
  --stage <published-run>\staging\stage.json `
  --validation <published-run>\staging\validation.json `
  --publication <published-run>\staging\publish.json `
  --post-snapshot <post-run>\snapshot.json `
  --post-plan <post-run>\plan.json `
  --post-stage <post-run>\staging\stage.json `
  --post-validation <post-run>\staging\validation.json `
  --active-baseline <active-baseline.json.gz> `
  --output <snapshot>\registry-clearance.json
```

Если первый же свежий plan уже имеет ноль изменений, используется
`--mode already_zero` без `--publication`. Остальные доказательства, включая
зелёный post-run и active baseline, обязательны.

Затвор напрямую проверяет:

- SHA snapshot manifest, safe/raw exports и логический digest SQLite;
- валидный `_bot_completed`, дату, ответственного и exact множество КД;
- pre/post/backup SHA каждого personal-файла и нулевой повторный enrichment;
- exact registry snapshot/plan/stage, полный зелёный validation и полный
  publish либо already-zero;
- нулевой post-run, его snapshot/plan/stage/validation и SHA текущего active
  baseline;
- отсутствие незавершённых `publish.journal.jsonl` во всём workspace.

Receipt можно повторно проверить без изменений:

```powershell
python -m registry_cycle.clearance verify `
  --receipt <snapshot>\registry-clearance.json `
  --snapshot-dir <snapshot>
```

## Очистка

Только после зелёной повторной проверки:

```powershell
python _local\reset_hardbot2_for_limited_test.py apply `
  --snapshot <snapshot> `
  --clearance <snapshot>\registry-clearance.json `
  --confirm CLEAR-HARDBOT2-FOR-LIMITED-TEST
```

`apply` ещё раз проверяет все локальные evidence-файлы, останавливает exact
подготовленный контейнер и до первой destructive записи доказывает, что live
SQLite и каталог фото всё ещё совпадают с bound snapshot. Любая новая запись
бота, новое фото, изменение Excel evidence, незавершённый journal или смена
runtime блокирует очистку. В этом случае контейнер запускается обратно, а
оператор создаёт новый snapshot и повторяет внешний порядок.

После остановки контроллер создаёт одноразовый signed intent, привязанный к
exact snapshot, clearance receipt, nonce и полным container/image ID. Только
его digest разрешает внутренний one-shot `apply`; одного флага
`--app-stopped` недостаточно. Durable apply/post/recovery receipts имеют ту же
привязку, а registry lease и hardbot ops lock удерживаются до подтверждённого
успеха либо восстановления.

Reset удаляет application tables и все ещё pending import-authorizations.
Уже consumed receipts и соответствующие source heads входят в логический
digest, сохраняются и повторно сверяются после apply. Поэтому очистка БД не
разрешает повторно загрузить старую публикацию как новую.
