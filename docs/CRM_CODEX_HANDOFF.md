# CRM СНГ — передача Кодексу: grants, workspaces и UI kit

Дата: 25.11.2026. Ветка: `feat/crm-grants-codex-20261125`.

Это не новое ТЗ и не просьба спланировать работу. Нужно продолжить существующую реализацию, закрыть подтверждённые дефекты и довести её до изолированной приёмки. Не начинать архитектуру заново.

## 0. Статус после выполнения — `cbef398`

P0, P1 и P2 из этого документа реализованы. Дополнительно закрыты найденные приёмкой проблемы: явный render после `grant_required`, legacy-сессии без capabilities и activation-only Apply. Исходный observer workflow восстановлен без ослабления территории.

Подтверждено на изолированных данных:

- TypeScript, role editor, UI kit 18/18, native capabilities и grant queue PASS;
- полный Windows workflow, native workspace, legacy pilot, analyst, sealed-session restart и явный выбор grant через 409 PASS;
- mobile grant composition, offline queue/revocation и cache в Chromium и WebKit PASS;
- полный `tests/test_crm_*.py`: остаются только два ранее задокументированных baseline failure `test_two_manager_sessions_share_data_preserve_authors_and_obey_revocation` и `test_server_overview_stays_outside_normal_manager_scope` про немедленный отзыв вопреки 15-секундному authority cache; остальные проверки прошли;
- actor lock перед revision читается и проверен регрессией, миграция 9→10 исправлена в fixture. Реальный PostgreSQL на машине отсутствует, поэтому PostgreSQL concurrent apply и настоящая миграция не выполнялись и в зелёный результат не включены;
- native Excel в полном наборе продолжает выдавать Windows RPC warnings окружающей среды; изменённые grant/identity/transport проверки это не затрагивает.

Production, рабочие БД и каналы обновлений не менялись. Эта ветка по-прежнему кандидат на отдельную изолированную приёмку, а не production-выкладка.

## 0.1. Scoped admin proxy — `f2ca06f` и PostgreSQL

Следующий этап после `cbef398` реализован:

- Manager proxy фильтрует состав команды по `employee_scope`; reset/revoke/approve/board/plan проверяют целевого сотрудника, аккаунт и устройство перед записью.
- Для model 2 доступны безопасные `/state`, `/busy` и список аккаунтов с фильтром территории. Прежние широкие admin-записи остаются запрещены; Excel и exchange используют canonical grant-aware пути, а не полный legacy token.
- Model 1 и глобальный owner сохраняют прежнее поведение.
- Найден и исправлен отдельный PostgreSQL-дефект чтения `crm_meta` в `grant_admin._environment`.

Проверка на сервере в disposable schemas: **33 passed, 2 skipped**. Подтверждены миграция 9→10, concurrent apply одной revision, actor lock и assignment/runtime инварианты. Полная выдача всех старых admin-операций ограниченным ролям не объявляется: им нужны отдельные scoped адаптеры каждой операции либо canonical аналоги.

## 1. Стартовая точка

- Production-hotfix находится в `main` и включён в эту ветку merge-коммитом `41c42a6`.
- Исходный hotfix: `e2cc41c` (`Fix CRM startup and Electron runtime packaging`).
- Наработки роя: `c3f75c6` и `9f06a98`.
- Ветка содержит 52 изменённых файла относительно прежнего `main`: grants, grant admin, workspace host/registry, role editor, UI kit, native/mobile adapters и synthetic-тесты.
- Опубликованные стабильные пакеты: Руководитель `3.3.15`, Hard/Soft `3.4.4`. Эта feature-ветка в эти пакеты и production не входит.

Сохранить исправления hotfix, не потерять их при дальнейших изменениях:

- `hard-desk/src/main/crm-pilot.ts`: `currentWindow()` и защита destroyed BrowserWindow; чтение UI через `readFileSync`, а не `net.fetch(file://...)`.
- `hard-desk/tools/installers.mjs`: `restoreElectron()` после `afterSign` для приложения и мастера. Изменённый `resedit` executable Electron 44 ломал даже загрузку `data:`.
- `hard-desk/tools/audit-installers.mjs`: сравнение упакованного executable с pristine pinned Electron.
- `hard-desk/tools/test-crm-window-lifecycle.cjs`: регрессии destroyed window и startup race.

Старый каталог `_local/crm-implementation-2026-11-03/source` удалён. Работать только в корне репозитория. Прежние smoke-проверки и зелёные срезы роя не являются приёмкой всей ветки.

Стартовая проверка этой ветки после merge с hotfix:

- TypeScript PASS; 73 профильных Python-теста PASS;
- role editor: 10 групп PASS; UI kit: 18/18 PASS;
- destroyed-window lifecycle на product=manager PASS;
- `test-crm-windows-workflow.cjs` доходит до переключения на observer, но затем FAIL: показывается «Сотрудник вне доступной команды», а тест завершается `Timeout: role observer`. Нужно исправить synthetic role switching/legacy scope в grant-изменениях и довести этот workflow до PASS, не удаляя сценарий observer.

## 2. Цель владельца

1. Внутри приложения аналитик/владелец может настроить любые поддерживаемые комбинации ролей. У Руководителя доступны также manager и logistic, у владельца — все рабочие области.
2. Владелец получает одно приложение из тех же workspace-модулей, что и остальные роли. Не делать отдельный дублирующий интерфейс владельца.
3. Общий UI kit сохраняет ввод, фокус, фильтры, прокрутку, черновики и очереди при live refresh, смене роли и потере связи.
4. Модульность обязательна, но big-bang rewrite запрещён.

Базовый сценарий приёмки — синтетический аналог Мехроча: одному actor назначены совместимые manager/employee или manager/analyst grants с независимыми территориями. Проверить вход, выбор grant, работу, выход, offline queue и отзыв. Реальные учётные данные и персональные реестры не использовать.

## 3. Неснимаемые ограничения

- Production, SSH, рабочие БД, реальные реестры, каналы обновлений и публикацию пакетов не трогать.
- Primary accounts, текущие token/revoke и legacy `access_model=1` сохранить. Grants additive, opt-in через `access_model=2`.
- Grant принадлежит `actor_uid`, а не всем actor одного человека. Два actor одного `employee_uuid` не наследуют права, очередь, кэш или черновики друг друга.
- Роль, capabilities и территория проецируются только из одной выбранной grant. Никакого объединения `manager:UZ + analyst:TJ` в `manager:TJ`.
- Каждая запись и background job повторно проверяют grant. UI не является границей безопасности.
- Не использовать localStorage для авторизации. Не выдавать `owner/root/server_operator/access_manage` через обычный редактор.
- Не расширять production ACL и не выключать существующие primary/fence/проекции.
- Не исправлять unrelated ошибки и не менять навигацию ради устаревших тестов.
- Не публиковать, не выкладывать и не объявлять production-ready без отдельной приёмки владельца.

## 4. P0 — сначала закрыть безопасность и потерю работы

### 4.1. Fail-closed для старых широких адаптеров

Файлы: `hard-desk/src/main/crm-pilot.ts`, `server/crm/mobile_backend.py`, `hard-desk/src/main/crm-native-access.ts`.

`manager-access` и legacy `admin` проверяют роль/capability, но затем используют широкий legacy credential без grant territory. Для model 2 это даёт доступ и запись вне территории.

До появления настоящего scoped proxy для model 2, кроме глобального owner grant, запретить такие вызовы с 403. Не считать проверку роли достаточной. Legacy model 1 не менять. Отдельно проверить `manager-access/team`, reset/revoke/board/plan и analyst admin/exchange.

### 4.2. Привязать native identity, очередь и кэш к actor/grant

Файлы:

- `server/crm/http.py` — вернуть `access_model`, `policy_revision`, `grant_id`, `actor_uid` в context;
- `hard-desk/src/main/crm-pilot-session.ts` — хранить grant metadata и передавать `X-CRM-Grant`;
- `hard-desk/src/main/crm-pilot.ts`, `crm-queue.ts`, `crm-native-access.ts` — owner/cache/queue binding и выбор grant;
- `hard-desk/ui/*` — только необходимый выбор при `grant_required`.

Требуется:

- Обработать `409 grant_required` с кандидатами из `details.grants`; без default native должен позволить выбрать grant.
- Включить actor в native owner. Для model 2 область очереди/кэша должна различать и grant; смена default не переносит pending работу в другой grant.
- В каждую запись `command`, `registry-edit`, `receipt`, `active-time` сохранить `actor_uid/access_model/grant_id`.
- Flush пропускает записи только при совпадении binding. Непривязанные legacy entries в model 2 не отправлять и не показывать как подтверждённые текущей grant.
- Не читать offline cache другого actor/grant. Не терять pending записи при возврате исходной grant.
- Проверить смену default A→B, отзыв A, два actor одного employee, restart и повторную отправку.

### 4.3. Разделить mobile vault и исправить выбор grant

Файлы: `server/crm/mobile_server.py`, `server/crm/mobile_backend.py`, `server/crm/shared_server.py`, `hard-desk/ui/mobile/bridge.ts`, `server/crm/shared_auth.py`.

- `bind_owner()` должен использовать origin + `actor_uid`, не только `employee_uuid`. Person-only namespace автоматически не наследовать.
- `/auth/exchange` должен возвращать `details` у `CRMError`, иначе picker не получает кандидатов при 409.
- Picker не должен предлагать grant, недоступную роли текущего credential. Либо фильтровать по текущей входной роли, либо явно делать отдельную аутентификацию другой роли; не оставлять заведомо падающий выбор.
- Тест переключения проводить через настоящий resolver, а не mock, разрешающий manager→analyst без проверки.

### 4.4. Закрыть replay и PostgreSQL-гонку

Файлы: `server/crm/store.py`, `exchange.py`, `grant_admin.py`, `grants.py`, `active_time.py`, `logistics.py`.

- `store.acknowledge()` при model 2 всегда сохраняет `grant_id` в результате.
- `store.replay()` при model 2 требует точного совпадения grant независимо от наличия поля в старой квитанции. Одинаковый `operation_id` под другой grant не должен снимать операцию из очереди.
- В `exchange.apply` выполнить `refresh_write` до replay. Уже принятая операция должна вернуть свою квитанцию, но не восстанавливать отозванные права для новой записи.
- В `grant_admin.apply` взять actor lock до `grants.load`, `store.replay` и `_build`, чтобы PostgreSQL не пропустил два разных apply с одним `base_revision`. SQLite-теста недостаточно: нужен concurrent-тест на изолированном PostgreSQL/disposable schema.
- Проверить active-time, Excel export/import, logistics-update/apply/record_call и grant-admin replay.

### 4.5. Убрать fail-open `access_manage`

Файлы: `server/crm/shared_auth.py`, `roles.py`, `grants.py`, `grant_admin.py`.

- Удалить восстановление `access_manage` из старого principal после `access.refresh_write()`.
- Переносить это actor-level полномочие из свежей membership и в legacy, и в grant projection. Не добавлять его blindly в catalog выдаваемых grant capabilities.
- Добавить тест настоящей аутентификации Аналитика вместо ручного `Principal`, а также отзыв capability между preview и apply.

## 5. P1 — восстановить целые сценарии после P0

1. **Background:** `server/crm/runtime.py`, `runtime_schema.py`, `shared_auth.py`, `manager_reports.py`. Credentials и verified ключировать `(actor_uid, grant_id)`; legacy использовать отдельный слот `grant_id=None`. Вход под B не блокирует jobs действующей A.
2. **Transport grant editor:** добавить `grant-catalog` в GET и `grant-admin` в POST native/mobile allowlists. Добавить synthetic routes в `server/crm/pilot_server.py` и demo-only `access_manage` Аналитику. Проверить list→preview→apply→replay через адаптеры, не только напрямую через HTTP.
3. **Mobile logistics:** добавить GET `logistics/logistics-export`, POST `logistics-preview/logistics-apply/logistics-update`; отдельную ветку `logistics-read` с grant guard и `pult.core.logistics_workbook.read` в `mobile_server.py`.
4. **Owner identity factories:** mobile background должен принимать owner через manager/full credential, а не требовать всегда analyst. Повторить native identity factory и grant recheck.
5. **Native analyst manager-access:** ветвить identity check по `login.kind`; analyst/analyst-device идут на `/api/crm-identity` с правильными headers, manager — на `/api/manager/device/me`. Не оставлять обязательный `X-Pult-Token` для Analyst.
6. **Assignment preview:** общую проверку `expires_at` выполнять для обеих моделей после replay, но до `_validate()`. Принятая операция возвращает квитанцию и после истечения preview.
7. **Synthetic observer:** исправить подтверждённый стартовый FAIL `test-crm-windows-workflow.cjs`: после courier переключение на observer возвращает «Сотрудник вне доступной команды». Сохранить прежний synthetic сценарий observer/read-only и не ослаблять территориальные проверки ради теста.

Если scoped proxy для legacy manager/admin не реализован, оставить P0 fail-closed. Не открывать доступ ради зелёного UI.

## 6. P2 — редактор и UX-регрессии

Файлы: `hard-desk/ui/access/role-editor.ts`, `hard-desk/ui/kit/*`, `hard-desk/ui/workspace/*`.

- `runApply`: захватить `submittedEdit = editRevision`. Если пользователь изменил форму во время apply, старая квитанция обновляет только `serverGrants`; она не очищает новый draft/operation_id и не перезаписывает ввод. Новый ввод требует отдельного preview.
- Разрешить Apply при непустом server diff, включая activation-only `access_model 1→2`, и повтор при uncertain. Сейчас `dirty=false` ошибочно блокирует активацию.
- Сохранять DOM, фокус и operation_id при 409 и live refresh.
- Проверить длинные списки, возврат из карточки, фильтры и прокрутку через существующие kit primitives; не создавать второй набор UI-компонентов.

## 7. Тесты, которые надо исправить, а не продукт

- `hard-desk/tools/test-crm-pilot.cjs` ожидает старое manager-меню «Внедрение». Не возвращать его; использовать актуальные manager-разделы либо профильный adoption API-тест.
- `hard-desk/tools/test-crm-analyst.cjs` должен учитывать «Активное время» у manager.
- `tests/test_crm_grants_postgres.py` не проверяет настоящий переход 9→10: fixture уже создаёт grant-объекты. Удалить новые объекты перед downgrade метаданных, затем проверить миграцию и повторный `initialize`.
- `tests/test_crm_shared_http.py`: два известных baseline failure ожидают немедленную смену authority вопреки 15-секундному cache. Не выдавать их за новую регрессию и не ломать cache ради формального зелёного прогона; отдельно указать в итоге, если останутся.

## 8. Проверки и Definition of Done

Работа считается выполненным этапом только когда P0 закрыт кодом и регрессиями, а целевой сценарий проходит end-to-end на synthetic/isolated данных. Недостаточно unit-тестов resolver или ручного `Principal`.

Минимальные команды по мере изменений:

```bash
python -m pytest tests/test_crm_grants.py tests/test_crm_grant_admin.py tests/test_crm_grant_work.py tests/test_crm_grant_reports.py tests/test_crm_workspace.py tests/test_crm_mobile_shared.py -q
npm --prefix hard-desk run check
npm --prefix hard-desk run build
node hard-desk/tools/test-role-editor.cjs
node hard-desk/tools/test-ui-kit-primitives.cjs
```

После изменений native (product build обязателен, иначе тест запустит legacy shell):

```bash
node hard-desk/node_modules/electron/install.js
HARD_PRODUCT=manager HARD_CRM_STABLE=1 npm --prefix hard-desk run build
HARD_CRM_QA_ROOT=/absolute/isolated/qa node hard-desk/tools/run-electron-test.cjs hard-desk/tools/test-crm-window-lifecycle.cjs
HARD_CRM_QA_ROOT=/absolute/isolated/workflow node hard-desk/tools/run-electron-test.cjs hard-desk/tools/test-crm-windows-workflow.cjs
```

Для PostgreSQL использовать только отдельный изолированный instance/schema. Проверить concurrent apply и миграцию 9→10; production DSN не читать и не использовать.

Перед любым будущем выпуском, но не в рамках этой разработки, обязательны точные installer audit и реальный packaged `renderer_ready`. Один только `npm run build` упакованную навигацию не проверяет.

Итоговая приёмка ветки:

- composite grants не смешивают территории и не наследуются между actor;
- model 2 не имеет fail-open пути через legacy manager/admin;
- offline queue/cache/drafts не пересекают actor/grant;
- revoke останавливает новые записи и background, replay не признаёт чужую grant;
- native и mobile завершают вход с несколькими grants;
- редактор применяет и повторяет операции без потери более нового ввода;
- актуальные synthetic/Windows/mobile/PostgreSQL проверки зелёные либо оставшиеся baseline проблемы явно перечислены;
- production не изменён, публикация не выполнена.

В итоговом ответе Кодекса перечислить: изменённые файлы, фактические контракты, команды и результаты проверок, что именно не проверено. Не выдавать непроверенное за готовое.
