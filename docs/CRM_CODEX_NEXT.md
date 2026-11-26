# CRM СНГ — следующая передача Кодексу

Дата: 25.11.2026. Ветка: `feat/crm-grants-codex-20261125`, HEAD `ca93ddc`.

Работаем над ошибками и доведением существующей ветки, а не над новой архитектурой. Нужно реализовывать и проверять, а не только ревьюировать.

## 1. Что уже сделано

### Production hotfix отдельно от feature

`main` содержит `e2cc41c` и опубликованные стабильные версии:

- Руководитель `3.3.15`;
- Hard/Soft `3.4.4`.

Hotfix исправляет:

- `TypeError: Object has been destroyed at showWindow` через защиту `currentWindow()`;
- `ERR_FAILED` при загрузке UI из ASAR через `readFileSync`;
- повреждение Electron executable от `resedit`: `afterSign: restoreElectron()` оставляет pristine runtime;
- добавлены installer audit и lifecycle regression.

Feature включает эти исправления через merge `41c42a6`. Не удалять и не переписывать их.

### Grants, owner и UI kit

Реализовано и проверено:

- actor-scoped grants `access_model=2`, независимые role/capabilities/territory;
- legacy `access_model=1` и global owner сохранены;
- явный выбор grant при `409 grant_required`, включая grants без default;
- native/mobile owner, очередь, кэш, черновики и background jobs разделены по actor/grant;
- replay требует точного grant; старые unbound receipts fail-closed;
- `access_manage` вычисляется из свежей membership и не восстанавливается из stale principal;
- общий workspace и UI kit; владелец использует те же модули, отдельного интерфейса нет;
- редактор grants сохраняет DOM, фокус, ввод, operation_id и более новый draft при старой квитанции;
- activation-only Apply исправлен;
- PostgreSQL actor lock перед revision и реальным concurrent apply.

### Scoped admin proxy

`server/crm/scoped_admin.py`, `hard-desk/src/main/crm-native-access.ts`:

- Состав команды фильтруется по `employee_scope`.
- Reset/revoke/approve/board/plan проверяют сотрудника, аккаунт и устройство перед записью.
- Для model 2 доступны безопасные `/state`, `/busy` и список аккаунтов с фильтром территории.
- Model 1 и global owner сохраняют прежнее поведение.
- Excel/exchange идут через canonical grant-aware пути, а не широкий legacy token.

Важно: scoped proxy — это НЕ просто проверка роли/capability. Список команды фильтруется внутри самой проверки, иначе чужой аккаунт прошёл бы просто потому, что существует.

## 2. Доказательства текущего состояния

Уже PASS:

- TypeScript, role editor, UI kit 18/18, grant queue, native capabilities;
- полный Windows workflow, native workspace, analyst, sealed-session restart;
- явный native login с двумя grants;
- mobile composition и offline/cache в Chromium и WebKit;
- PostgreSQL на сервере в disposable schemas: **33 passed, 2 skipped**, включая schema 9→10, concurrent apply и actor lock;
- manager-access и admin territory mutations;
- full `tests/test_crm_*.py`, кроме двух известных baseline failures про 15-секундный authority cache:
  - `test_two_manager_sessions_share_data_preserve_authors_and_obey_revocation`;
  - `test_server_overview_stays_outside_normal_manager_scope`.

Подробности и предыдущая история — `docs/CRM_CODEX_HANDOFF.md`.

Production, рабочие БД и каналы обновлений не менялись после hotfix. Feature EXE не опубликован.

## 3. Сегодняшний кейс сотрудника — не считать доказанным новым багом

На фото снова:

```text
TypeError: Object has been destroyed
at showWindow
at EventEmitter.<anonymous>
```

Это буквально старый стек `second-instance` до hotfix. Пока не подтверждено, что установлена и запущена именно `3.4.4`.

Порядок для Кодекса:

1. Проверить версию в «Подключение» и полностью завершить прежний `hard-hard.exe` через трей/Диспетчер задач.
2. Если версия старше — установить `hard-3.4.4-setup.exe` поверх, профиль сохранить.
3. Если уже 3.4.4 и ошибка повторяется — получить `crm-diagnostics.json`, точную версию и шаги воспроизведения. Только тогда искать второй lifecycle path.
4. Не откатывать hotfix и не менять production на основании одного фото.

Текущий канал проверен: Hard/Soft `3.4.4`, Руководитель `3.3.15`.

## 4. Что делать дальше по приоритету

### P0. Оставшиеся scoped admin операции

Сейчас для model 2 безопасны только `/state`, `/busy`, список аккаунтов и manager proxy. Широкие legacy admin-записи остаются закрыты.

Для каждой нужной операции выбрать и реализовать один из двух вариантов:

- scoped adapter с фильтром `employee_scope` и проверкой каждой цели;
- canonical grant-aware аналог вместо старого admin endpoint.

Нельзя открывать `/imp`, `/enr`, `/reg`, `/exp`, `/mail` и другие legacy write-операции одним общим token. Нельзя возвращать полный список аккаунтов, реестров или заданий без территориального фильтра.

Сначала перечислить реальные пользовательские сценарии Аналитика и закрыть их по одному, с тестами на запрет чужой территории. Не объявлять «весь admin готов», если остаются непроверенные операции.

### P0. Приёмка Мехроча и Василия

На synthetic и затем изолированном PostgreSQL:

- совместимые двойные роли с независимыми территориями;
- вход, выбор grant, работа, отзыв, выход;
- offline queue, cache, drafts, restart;
- manager-access сотрудника внутри/вне территории;
- owner со всеми workspace-модулями.

Физические запуски на их ПК — отдельная приёмка, synthetic её не заменяет.

### P1. PostgreSQL и production migration

Уже есть isolated PostgreSQL PASS. Дальше:

- проверить scoped admin mutations на том же стенде, не только pure helpers;
- провести реальную миграцию `access_model=2` на копии рабочей БД с backup/rollback;
- проверить, что `access_model=1` пользователи не изменили поведение;
- не включать production без отдельного разрешения владельца.

### P1. Устройства и реальная UX-приёмка

- Физические Android/iPhone.
- Установленные Windows-клиенты на рабочих ПК.
- Фокус, клавиатура, фильтры, прокрутка, live refresh, очередь и длинные списки.
- Не заявлять принятие по одним browser/synthetic проверкам.

### P2. Baseline и technical debt

Два baseline failure про немедленный revoke вопреки 15-секундному authority cache не относить к новой регрессии и не ломать cache ради зелёного отчёта. Исправлять только отдельным согласованным изменением поведения, если владелец его запросит.

## 5. Границы работы

- Production, SSH к рабочим сервисам, рабочие БД и каналы обновлений не трогать без отдельного разрешения.
- Не читать секреты/реальные реестры и не помещать их в Git.
- Не объединять территории разных grants и не наследовать права между actor одного человека.
- Не использовать localStorage для авторизации.
- Не переписывать primary accounts/token/revoke и legacy model 1.
- Не запускать новый рой и не делать big-bang rewrite.
- Не смешивать production hotfix с feature-коммитами.

## 6. Проверки перед каждым этапом

```bash
python -m pytest tests/test_crm_grants.py tests/test_crm_grant_admin.py tests/test_crm_grant_work.py tests/test_crm_grant_reports.py tests/test_crm_workspace.py tests/test_crm_mobile_shared.py tests/test_crm_runtime.py -q
npm --prefix hard-desk run check
HARD_PRODUCT=manager HARD_CRM_STABLE=1 npm --prefix hard-desk run build
node hard-desk/tools/test-role-editor.cjs
node hard-desk/tools/test-ui-kit-primitives.cjs
node hard-desk/tools/test-crm-queue-grants.cjs
node hard-desk/tools/test-crm-native-access.cjs
```

Для native обязательно собрать product=manager, иначе lifecycle запустит legacy shell:

```bash
HARD_CRM_QA_ROOT=/absolute/isolated/qa node hard-desk/tools/run-electron-test.cjs hard-desk/tools/test-crm-window-lifecycle.cjs
HARD_CRM_QA_ROOT=/absolute/isolated/workflow node hard-desk/tools/run-electron-test.cjs hard-desk/tools/test-crm-windows-workflow.cjs
HARD_CRM_QA_ROOT=/absolute/isolated/grant-login node hard-desk/tools/run-electron-test.cjs hard-desk/tools/test-crm-grant-login.cjs
```

PostgreSQL только disposable `crm_test_*` schemas на сервере. Production DSN не читать и не использовать.

Перед любым будущим выпуском снова обязательны installer audit и реальный packaged `renderer_ready`; один `npm run build` этого не доказывает.

## 7. Итоговый формат

Сообщать:

- что изменено по файлам и фактическим контрактам;
- какие проверки запущены и их результат;
- что не проверено;
- не затронуты ли production и старые пользователи;
- какие scoped операции остаются небезопасными или непроверенными.

Не выдавать непроверенное за готовое.
