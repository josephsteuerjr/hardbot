# Чек-лист переноса пульта Tkinter → PySide6

Инвентаризация `pult/pult.py` (3976 строк, Tk) перед портом. Каждая строка —
экран, кнопка, диалог, подтверждение, фоновая задача или вызов API с указанием
метода класса `Pult`, откуда взято. Отметка `[x]` — перенесено и проверено,
`[~]` — перенесено с оговоркой (см. примечание), `[ ]` — не перенесено.

Куда что уехало: логика без Qt — `pult/core/*`, виджеты — `pult/ui/*`.
Метод `Pult.<имя>` сохраняет прежнее имя и сигнатуру (на них опираются
`tests/test_pult_limited_write_boundary.py` и
`tests/test_pult_import_recall_contract.py`).

## 1. Каркас, запуск, связь

| | Что | Было (`Pult.…`) | Стало |
|---|---|---|---|
| [x] | Проверка папки `hardbot` рядом, понятное окно и выход | модульный `_fatal`, проверка `HARDBOT_DIR` | `core/bootstrap.py: ensure_hardbot`, `app.py: fatal` (QMessageBox; без PySide6 — MessageBoxW) |
| [x] | Загрузка `enrich`, `razbros` с окном при ошибке | модульный try/except | `app.py: main` (StartupError / Exception → fatal) |
| [x] | High-DPI (Per-Monitor v2) | `main()` ctypes + `_dpi_geometry` | штатный Qt 6, ничего руками |
| [x] | Окно 1180×800, minsize 980×660, центрирование | `_dpi_geometry` | `ui/main_window.py: Pult.__init__` |
| [x] | Иконка окна | `icon-src.png` | `pult/icon.png`, если есть; иначе молча без иконки |
| [x] | Заголовок окна | «Контур · Выезды» | «Выезды · Пульт» |
| [x] | Рейка: логотип, 7 разделов, «Подключение» прижато вниз | `_build` | `ui/nav.py: NavRail` (монохромные SVG-иконки в коде) |
| [x] | Индикатор связи: точка + «не подключено / подключено / нет связи» + хост | `_set_conn` | `NavRail.set_connection`, `Pult._set_conn` |
| [x] | Шапка: заголовок раздела (`TITLES`), «обновлено HH:MM», кнопка «Обновить» | `_build`, `refresh` | `Pult._build_shell` |
| [x] | Прокрутка страницы; колесо над комбобоксом/чипсами листает страницу | `_build` (`bind_class`) | `ui/widgets.py: Combo.wheelEvent`, `Page` (QScrollArea) |
| [x] | `nav(sec)`: подсветка, `settings["sec"]`, прокрутка вверх, ленивая загрузка prio/activity/people/mgrs | `nav` | `Pult.nav` |
| [~] | Старт: раздел из настроек и авто-`refresh` только при наличии ключа | `__init__` | `Pult.__init__`: то же, но «есть учётные данные» = ключ **или** токен устройства (после привязки ключ стирается, и старый пульт молча открывал «Подключение») |
| [x] | Тост 3,2 с внизу по центру, красный при ошибке | `toast`, `_fail` | `ui/widgets.py: Toast`, `Pult.toast` |
| [x] | Фоновые задачи: `bg_task(fn, done, fail)`, результат в UI-потоке | `bg_task` (threading + `after`) | `ui/tasks.py: TaskRunner` (QThreadPool/QRunnable + сигналы), `Pult.bg_task` |
| [x] | Вызов из потока в UI-поток (`after(0, …)` для лога обогащения) | лямбды `self.after` | `Pult.ui_call` (сигнал `invoke`) |
| [x] | `api(method, path, body, timeout)`: X-Device-Token иначе X-Admin-Key, JSON, TLS-fallback без проверки, 401 (два текста), 404, 409 c payload, «Сервер ответил N», «Нет связи с сервером» | `api` | `core/api.py: request`, `Pult.api` |
| [x] | Настройки: файл `настройки пульта.json`, ключи `url key device_token sec scatterBase scatterSource enrich_file`, indent=1 | `_load_settings`, `_save_settings` | `core/settings.py: Settings` |
| [x] | Закрытие: сохранить url, токен устройства; мастер-ключ никогда | `_on_close` | `Pult.closeEvent` |
| [x] | Запуск двойным кликом без консоли | `Пульт.bat` (pythonw) | `pult/Пульт.pyw` (ассоциация `.pyw` → `pyw.exe`), `pult.py` — тонкая обёртка |
| [x] | Константы ограниченного теста и сообщения STOP | `LIMITED_TEST_*`, `IMPORT_SOURCE_*` | `core/constants.py`, реэкспорт в `pult.py` |

## 2. Сводка

| | Что | Было | Стало |
|---|---|---|---|
| [x] | 5 метрик: Контрагентов, С визитом, Сотрудников, Выбрали себя, Меток | `_build_dash`, `refresh` | `ui/dashboard.py: DashboardSection` |
| [x] | «По республикам»: панели tj/uz, KV (договоров, с визитом, покрытие %, сотрудников), чипсы исходов, «нет закреплённых кейсов» | `_build_dash`, `refresh` | `DashboardSection`, `core/dashboard.py: country_pairs, outcome_chips` |
| [x] | «Покрытие визитами»: полоса + «X из Y контрагентов — N%» / «реестр пуст» / «нет данных — подключись к серверу» | `_draw_cov`, `refresh` | `ui/widgets.py: Meter`, `core/dashboard.py: coverage` |
| [x] | «Исходы визитов»: чипсы / «визитов с исходом пока нет» | `refresh` | `DashboardSection` |
| [x] | «Период выгрузки»: текст периода на сводке | `_show_period` | `RegistrySection._show_period` (пишет в обе подписи) |
| [x] | `refresh`: сохраняет url/key, GET `/admin/stats`, db_employees → список экспорта, метрики, привязки, «обновлено», очистка `_loaded` и перезагрузка открытого раздела; fail → «нет связи» + тост | `refresh` | `Pult.refresh` + `DashboardSection.render_stats` |

## 3. Реестр — импорт

| | Что | Было | Стало |
|---|---|---|---|
| [x] | Карточка «Импорт реестра» с описанием | `_build_registry` | `ui/registry.py: RegistrySection._card_import` |
| [x] | «Выбрать реестр…» → диалог Excel; сброс галочек; «Читаю …»; чтение книги + GET `/admin/stats`, merge сотрудников; подпись «файл · строк N · сотрудников M / Вид источника: …»; fail «Файл не разобран: …» + тост «Реестр не прочитан» | `pick_registry` | `RegistrySection.pick_registry`, `core/registry_import.py: read_registry, merge_import_employees, source_kind_text` |
| [x] | Список «Кого грузить (галочки):», кнопки «все» / «есть в БД» / «снять», строка «в файле: N · сейчас в БД: M · состояние · написаний в файле: k» | `_imp_render`, `_imp_all`, `_imp_existing`, `_imp_sync_selection`, `_imp_toggle` | `RegistrySection._imp_render` и др., `core/registry_import.py: employee_note` |
| [x] | Галочка «полный срез…» + предупреждение + тост «Полный режим включён…» | `_imp_full_toggle` | `RegistrySection._imp_full_toggle` |
| [x] | Галочка «Вернуть ранее вручную снятые…» + текст «ВОЗВРАТ РУЧНЫХ СНЯТИЙ…» | `_imp_restore_manual_toggle` | `RegistrySection._imp_restore_manual_toggle` |
| [x] | Галочки «Начало месяца: заменить текущие комментарии…», «Разово загрузить историчные…» + заметки | `_imp_comment_toggle` | `RegistrySection._imp_comment_toggle` |
| [x] | Кнопка «Предпросмотр выбранных/полного среза (N чел · M дог.)», disabled без выбора | `_imp_count` | `RegistrySection._imp_count`, `core/registry_import.py: preview_button_text` |
| [x] | Кнопка «Применить импорт · снять N · комментариев N · вернуть N · отозвать N · вернуть из отзыва N», disabled до предпросмотра | `do_import` | `core/import_flow.py: ImportFlow.do_import`, `core/registry_import.py: apply_button_label` |
| [x] | `do_import`: guard `LIMITED_TEST_MASTER_IMPORT_UI_ENABLED`; «Сначала выбери реестр»; «Отметь хотя бы одного сотрудника»; сигнатура выбора; тост «Проверяю импорт …»; POST `/admin/import/preview.json` (timeout 180) `{data_exchange_contract, source_kind, source_proof, sheets, full_registry, authoritative_employees, restore_manual_unassigned, replace_current_visit_comments, import_historical_visit_comments}`; «Выбор изменился во время проверки — предпросмотр отменён»; `_expected_from_preview` fail-closed («… — нужен новый сервер»); заметки «ПОЛНЫЙ СРЕЗ ВЫБРАННЫХ…» / «ЧАСТИЧНЫЙ ИМПОРТ…»; тост «Предпросмотр готов[: будет снято N]» | `do_import` | `ImportFlow.do_import` (Qt-free, тесты `tests/test_pult_core_import_flow.py`) |
| [x] | `apply_import`: «Сначала сделай свежий предпросмотр импорта»; тост «Применяю проверенный импорт…»; POST `/admin/import/authorize.json` `{source_kind, source_proof}` (ошибка глотается); POST `/admin/import.json` (preview-body + `expected_*`, timeout 180); ошибка → `_show_import_error(registration_hint=True)`, `preview_stale`; «Этот receipt уже использован; повтор ничего не записал» / «Импорт готов: +N / обновлено M [/ снято K] [/ возвращено R]»; `refresh` | `apply_import` | `ImportFlow.apply_import` |
| [x] | `_show_import_error`: текст ошибки, `header_sample`, подсказка регистрации (IMPORT_REGISTRAR_HTTP=1, `docker compose exec … register`, путь proof, digest), тост «Импорт не прошёл» | `_show_import_error` | `ImportFlow._show_import_error`, `core/registry_import.py: registration_hint` |
| [x] | `_invalidate_import_preview(clear_report)`, `_imp_signature` | те же | `ImportFlow` |
| [x] | `_prepare_import`: published_master (весь файл + receipt, не force) / test_subset с новым approval id; proof в temp `hardbot-proof-<digest>.json` (`_proof_path`) | `_prepare_import` | `core/registry_import.py: prepare_import`, `ImportFlow._prepare_import` |
| [x] | `read_registry`: `read_stable_workbook`, `_validate_sheets`, scope, группировка по каноническому имени, `prepare_published_master_import` | `read_registry` | `core/registry_import.py: read_registry` |
| [x] | Статические: `_emp_key`, `_merge_import_employees`, `_existing_import_selection`, `_default_import_selection`, `_grid_for`, `_grid_layout`, `_scope_from_grid`, `_expected_from_preview`, `_imp_pairs`, `_imp_notes` | те же | `core/registry_import.py` (функции без подчёркивания) + статические обёртки на `Pult` |
| [x] | KV отчёта импорта (22 пары) и заметки (`_imp_notes`: колонки отзыва, office/protected по ключам, обещания, ОТЗЫВ, нераспознанные, ВОЗВРАТ ИЗ ОТЗЫВА, частичные, «не авария», ручные снятия, комментарии, идемпотентный повтор, без ответственного, офисные правки, защита, незнакомые/служебные колонки) | `_imp_pairs`, `_imp_notes` | `core/registry_import.py: import_pairs, import_notes` |
| [x] | `do_import_batch` — fail-closed заглушка (личные книги в бота не грузятся), кнопки нет | `do_import_batch` | `ImportFlow.do_import_batch` |

## 4. Реестр — обогащение личных реестров

| | Что | Было | Стало |
|---|---|---|---|
| [x] | Карточка с описанием, «Папка с республиками…» (settings `scatterBase`), «Проверить соответствия (без записи)» | `_build_registry`, `scatter_pick_base` | `RegistrySection._card_enrich`, `scatter_pick_base` |
| [x] | Подпись базы «база: …» / «базовая папка не выбрана» | `_scatter_labels` | `RegistrySection._scatter_labels` |
| [x] | Окно дат «Выезды с … по … ДД.ММ.ГГГГ · пусто = без границы», «Только сегодня» | `_enr_window_today`, `_enr_window` | `ui/widgets.py: DateField` (кнопка «сегодня»), `RegistrySection._enr_window` |
| [x] | Новое: предзаполнение окна из `enrich.suggest_window(receipt_dir)` | — | `core/enrichment.py: suggest_window`, `RegistrySection._enr_suggest_window` (через `getattr`) |
| [x] | Новое: пустое окно требует явного подтверждения «вся история» | — | `core/enrichment.py: window_text`, `RegistrySection.enr_apply` (отдельный askyesno «Вся история») |
| [x] | Новое: галочка «Считать сервер источником…» с предупреждением, передаётся только если `enrich_many` её принимает | — | `core/enrichment.py: enrich_many_kwargs` (`inspect.signature`) |
| [x] | Новое: ledger — GET `/admin/enrichment/ledger.json?employees=…`, колбэк `on_delivered` → POST `/admin/enrichment/ledger.json` `{entries, receipt_digest}` | — | `core/enrichment.py: ledger_query, delivered_payload`, `RegistrySection.enr_apply` |
| [x] | Новое: конфликты и правки офиса — отдельные таблицы и строки отчёта | — | `core/enrichment.py: collect_conflicts, report_lines`, `RegistrySection` (две таблицы) |
| [x] | `enr_preview`: GET `/admin/export.json` (120 с), `validate_server_export`, `split_by_employee`, `razbros.load_config/resolve_roots/build_folder_map`; matched/nofolder/nofile; KV (с визитами бота, готовы, завершённых визитов, файлов, [без ответственного]); предупреждения (⚠ orphan с КД, «нет папки», «нет .xlsb», «Бот пока ничего не заполнил…»); «Ошибка: …» | `enr_preview`, `_personal_files` | `core/enrichment.py: personal_files, match_employees, preview_pairs, preview_warning`, `RegistrySection.enr_preview` |
| [x] | Список «Кого обогащать (галочки):», «все»/«снять», «визитов: N · файлов: M · где» | `_render_enr_pick`, `_enr_toggle`, `_enr_all`, `_enr_seln` | `RegistrySection` |
| [x] | «Обогатить выбранных (N)» / «Обогатить всех» только при `LIMITED_TEST_ENRICH_WRITES_ENABLED` | `_render_enr_pick` | `RegistrySection._render_enr_pick` |
| [x] | `enr_apply(which)`: guard (сообщение STOP), «Никто не выбран», jobs, текст окна / «ОКНО НЕ ЗАДАНО…», askyesno «Обогащение реестров», backup_root `<база>/!! Обогащение из БД`, receipt_dir `_квитанции`, `enrich.enrich_many`, лог в `enrich_out` | `enr_apply`, `_enr_backup_root` | `RegistrySection.enr_apply`, `core/enrichment.py: build_jobs, backup_root, confirm_text` |
| [x] | Отчёт прогона: «Готово: файлов…», ≤15 строк по файлам, «ЗАМЕНЕНО ПОВЕРХ ЧУЖИХ ЗНАЧЕНИЙ», ⚠ written_unverified, ⚠ ошибки, «Резервные копии», «Квитанция» / «⚠ КВИТАНЦИЯ НЕ ЗАПИСАНА»; тост «Обогащено файлов: N[, ошибок: M]» | `enr_apply.done` | `core/enrichment.py: report_lines, summarize`, `RegistrySection._enr_done` |
| [x] | `do_enrich` (один файл, запасной путь, кнопки нет): guard, диалог файла, askyesno «Обогатить реестр», `enrich_file`, отчёт | `do_enrich` | `RegistrySection.do_enrich` |

## 5. Реестр — экспорт, период, отозванные, legacy

| | Что | Было | Стало |
|---|---|---|---|
| [x] | «Экспорт-слепок»: список сотрудников из БД (2 колонки, «Имя · N дог.»), «все»/«снять», пустые состояния | `_render_export_employees`, `_exp_toggle`, `_exp_all` | `RegistrySection._render_export_employees` |
| [x] | Даты «Выезды с … по … пусто = без ограничения», «Только сегодня» | `_exp_today` | `DateField` |
| [x] | `_export_request`: mode=full, employee…, visit_from/to; «отметь хотя бы одного сотрудника для экспорта»; «начало окна выгрузки позже окончания» | `_export_request` | `core/export.py: export_request`, `ImportFlow._export_request` |
| [x] | «Подготовить выгрузку…»: GET export → askyesno «Экспорт реестра» (строк / сотрудники / период) → диалог сохранения `registry_export.xlsx` → `write_registry_xlsx` в фоне → «Сохранено: … (N строк)» | `do_export_full` | `RegistrySection.do_export_full`, `core/export.py: write_registry_xlsx, export_confirm_text` |
| [x] | «Метки курьеров…»: диалог `markers.xlsx` → GET `/admin/markers.json` → `write_markers_xlsx` → тост | `do_export_markers` | `RegistrySection.do_export_markers`, `core/export.py: write_markers_xlsx` |
| [x] | «Период выгрузки»: from/to, «Применить» (POST `/admin/period {from,to}`, ошибки формата тостом, «Период сохранён»), «Сбросить», текст «Активен: … — …» / «Период не задан — видны все.» | `apply_period`, `clear_period`, `_show_period` | `RegistrySection` |
| [x] | «Отозванные договоры»: «Показать отозванных» (500) / «Показать все» (5000), фильтр по сотруднику, KV (отозвано реестром, снято вручную, снято отсутствием строки, «показано X из Y»), таблица (КД, Клиент, Снят у, Дата отзыва, Причина, Долг); GET `/admin/recalled.json?limit&employee` | `load_recalled` | `RegistrySection.load_recalled`, `core/registry_import.py: recalled_pairs, recalled_row` |
| [x] | Legacy-разброс без карточки в UI: `_scatter_restore`, `_scatter_labels`, `scatter_pick_source`, `scatter_add_root`, `scatter_run` (запись → `LEGACY_SCATTER_DISABLED_MESSAGE` STOP), `_render_scatter_pick`, `_scatter_toggle/_all/_seln` | те же | `ui/registry.py` (методы сохранены, карточки по-прежнему нет) |
| [x] | `do_reset(scope)`: guard `LIMITED_TEST_RESET_UI_ENABLED` → `reset_out` + тост, дальше не идёт; тексты подтверждений сохранены для будущего | `do_reset` | `ImportFlow.do_reset` |
| [x] | Кнопки геокодинга нет (координаты ставит сервер) | комментарий в `_build_registry` | без изменений |

## 6. Приоритет

| | Что | Было | Стало |
|---|---|---|---|
| [x] | «Веса скоринга»: ползунки Долг 50 / Кластер 30 / Свежесть 20 с подписью значения, «Пересчитать» | `_build_prio` | `ui/priority.py: PrioritySection` |
| [x] | «Кластеры городов»: чипсы «город N» / «городов нет — реестр пуст или нет координат» | `load_priority` | `PrioritySection.load_priority` |
| [x] | «Куда ехать — топ-40»: таблица (#, Клиент, Город, Долг ₽, Ожидание ₽, Выезд., Score, Режим, Почему), GET `/admin/priority?w_debt&w_cluster&w_fresh&limit=40` | `load_priority` | `core/priority.py: priority_query, priority_row`, `PrioritySection` |

## 7. Люди

| | Что | Было | Стало |
|---|---|---|---|
| [x] | «Назначение ролей»: GET `/admin/guests` (ошибка → пусто), GET `/admin/employees`; строки: сотрудник, договоров, комбобокс «— из писавших боту —» + поле ID, тип «Выездник/Курьер ГПХ», статус «привязан ✓/не назначен», «Привязать» | `load_roles`, `_pick_guest`, `_guest_label`, `_fmt_seen` | `ui/people.py: PeopleSection`, `core/people.py: guest_label, fmt_seen` |
| [x] | `bind_role`: `\d{3,}` («Впиши числовой Telegram ID…»), POST `/admin/bind {telegram_id, employee, kind}`, тост «Привязано: … → …», `refresh` | `bind_role` | `PeopleSection.bind_role` |
| [x] | «Кто вошёл в бот»: строки привязок (id, ФИО, бейдж курьер/выездник, «сбросить»); askyesno «Сброс привязки»; POST `/admin/unbind`; «Сброшено» | `_render_binds`, `unbind` | `PeopleSection` |
| [x] | «Убрать клиентов у сотрудника»: комбобокс → GET `/admin/emp_clients?employee=`; галочки «client · КД id» + долг/unit; «все»/«ничего»; «Снять выбранных (N)» danger; askyesno «Убрать клиентов»; POST `/admin/unassign {employee, contract_ids}`; «Снято: n», перезагрузка + `refresh` | `load_emp_clients`, `_un_all`, `_un_seln`, `do_unassign` | `PeopleSection` |
| [x] | Пустые состояния: «Нет данных — импортируй реестр…», «Реестр пуст — импортируй xlsx…», «Никто ещё не привязан», «За сотрудником сейчас никого нет» | те же | `PeopleSection` |

## 8. Руководители

| | Что | Было | Стало |
|---|---|---|---|
| [x] | «Автосводка руководителям»: время 16:30, «автопуш включён», обещания 09:30, «утренний пуш…», «отбивки по закрытым визитам…» (disabled на старом сервере), «Лично мне: дайджест / обещания» (только если сервер знает), «Сохранить», строка состояния | `_build_mgrs`, `save_digest`, `_show_owner_switches`, `_show_digest_state` | `ui/managers.py: ManagersSection`, `core/managers.py: valid_time, digest_body, digest_state_text` |
| [x] | `save_digest`: ЧЧ:ММ («Время в формате ЧЧ:ММ»), POST `/admin/digest_settings`, owner_* только если чекбоксы активны, «Настройки сохранены» | `save_digest` | `ManagersSection.save_digest` |
| [x] | «Диагностика доставки»: «Обновить сводку доставки», GET `/admin/digest_settings`, KV по видам (дайджест, обещания, визиты, группа, беседы, проверки: «ушло · заблок. · ошибок · пропущено»), «за сегодня — записей доставки нет», «Заблокированы (нужен /start…)», «Последняя ошибка: …», обновление рубильников | `load_delivery` | `core/managers.py: delivery_pairs, delivery_lines`, `ManagersSection.load_delivery` |
| [x] | «Координаты фото»: «Хранить (дней)», «Сохранить срок» (POST `/admin/photo_geo {retention_days}`, «Срок — целое число дней…»), «Обновить» (GET), «Стереть сейчас: сотрудник / КД / до даты», «Стереть координаты» (askyesno с областью или «ВСЕ снимки», POST `{forget:{employee, contract, before}}`, «Стёрто координат: N»), строка состояния | `load_geo_retention`, `save_geo_retention`, `forget_geo`, `_show_geo_state` | `core/managers.py: geo_scope_text, geo_state_text`, `ManagersSection` |
| [x] | «Пересылка отчётов в беседы»: «Обновить список бесед» (GET `/admin/chats`), чекбокс беседы «title · id · с дата · ⚠ есть тёзка! · бот удалён из беседы», подпись сотрудников / «сотрудники не закреплены — посты не идут», POST `/admin/chats.set {chat_id, enabled}` («Беседа включена/выключена», fail → «Не сохранилось» + перечитать) | `chats_refresh`, `_render_chats`, `_chat_toggle` | `core/managers.py: chat_label, chat_employees_text`, `ManagersSection` |
| [x] | Диалог «Сотрудники (N)»: «Чьи визиты постить в эту беседу:», галочки в 2 колонки, «Сохранить» → POST `/admin/chats.set {chat_id, employees}` → «Сотрудники беседы сохранены» | `_chat_emps_dialog` | `ui/managers.py: ChatEmployeesDialog` |
| [x] | «Руководители»: строки (имя, города, «сотрудников: N», id, «⚠ ФИО ≠ реестру»), «проверить доставку», «изменить», «удалить» | `_render_mgr_table`, `mgr_edit`, `mgr_delete` | `ManagersSection` |
| [x] | `push_test`: POST `/admin/push_test {kind: visit, telegram_id}`; «Дошло»; showwarning «Доставка» «Не дошло (status)…»; старый сервер → текст `old_server` | `push_test` | `ManagersSection.push_test` |
| [x] | `mgr_delete`: askyesno «Удаление» → POST `/admin/manager_delete` → «Удалён» | `mgr_delete` | `ManagersSection.mgr_delete` |
| [x] | Форма: комбобокс гостей (подставляет id **и** имя), Telegram ID, Имя, чипсы городов (город добавляет своих сотрудников), поле + «＋ добавить город» (Enter), чипсы сотрудников, «выбрать всех»/«снять», «выбрано: N», «Сохранить руководителя», «Очистить форму» | `_render_mgr_chips`, `_mgr_pick_guest`, `_mgr_toggle_city`, `_mgr_add_city`, `_mgr_toggle_emp`, `_mgr_emps_all`, `_mgr_form_reset` | `ManagersSection`, `ui/widgets.py: ChipFlow` |
| [x] | `save_manager`: `\d{3,}`, «Отметь хотя бы одного сотрудника», незнакомый аккаунт → askyesno «Незнакомый аккаунт» (полный текст), POST `/admin/manager {telegram_id, name, cities, employees}`, `names_look_different` → showwarning «Имя не совпало», тост «Руководитель сохранён: …», сброс формы, перезагрузка | `save_manager` | `ManagersSection.save_manager`, `core/text.py: names_look_different` |
| [x] | `load_managers`: GET `/admin/guests` (мягко) + GET `/admin/managers` → managers/cities/employees/city_employees/digest; `chats_refresh` | `load_managers` | `ManagersSection.load_managers` |

## 9. Активность

| | Что | Было | Стало |
|---|---|---|---|
| [x] | Таблица (Сотрудник, Роль, Визиты 7д, 30д, Метки 7д, 30д, Последняя активность, Статус); GET `/admin/activity`; статусы «без активности / активен / пауза N дн / простой N дн», «⚠ ФИО нет в реестре ·», сокращение даты | `_build_activity`, `load_activity` | `core/activity.py: activity_row`, `ui/activity.py: ActivitySection` |

## 10. Подключение

| | Что | Было | Стало |
|---|---|---|---|
| [x] | «Сервер»: адрес, Admin-ключ (скрыт, 👁), «Привязать это устройство», «Подключиться», «Отвязать это устройство», подпись состояния (два текста) | `_build_conn`, `_toggle_key`, `_connect`, `_key_state` | `ui/connection.py: ConnectionSection`, `core/devices.py: key_state_text` |
| [x] | `device_enroll`: «Введи мастер-ключ — он нужен один раз»; askstring «Имя устройства» (по умолчанию «Пульт · <host>»); отпечаток sha256(host|mac|user); привязка **ключом** явно; POST `/admin/device/enroll.json {device_id, name}`; токен → настройки, ключ стирается из настроек и поля; «Устройство привязано — ключ больше не хранится»; «Сервер не выдал токен»; fail возвращает ключ | `device_enroll`, `device_fingerprint`, `device_default_name` | `core/devices.py: device_fingerprint, device_default_name`, `ConnectionSection.device_enroll` |
| [x] | `device_unbind`: «Устройство и так не привязано»; askyesno «Отвязать устройство» (полный текст); забыть токен, `connected=False`, «Токен забыт на этой машине» | `device_unbind` | `ConnectionSection.device_unbind` |
| [x] | «Привязанные устройства»: «Обновить список» (GET `/admin/devices.json`), строки «name · привязано … · на связи: … / ни разу», «Отозвать»; «Привязанных устройств нет» | `devices_load` | `ConnectionSection.devices_load`, `core/devices.py: device_line` |
| [x] | `device_revoke`: askyesno «Отзыв устройства» (полный текст); «Отзыв возможен только мастер-ключом — введи его выше»; ключом явно с восстановлением токена; POST `/admin/device/revoke.json {id}`; «Устройство отозвано» / «Устройство уже было отозвано» | `device_revoke` | `ConnectionSection.device_revoke` |
| [x] | «Токен бота»: поле (скрыт, 👁), «Сменить бота» (формат `\d+:[\w-]{30,}`, askyesno «Смена токена», POST `/admin/bot_token {token}`, «Токен сохранён — можно перезапустить бота», need_restart → askyesno «Перезапуск» → рестарт), «Перезапустить бота» (askyesno «Перезапуск бота», POST `/admin/restart`, тосты на успех и на обрыв), подпись формата | `change_token`, `restart_bot`, `_toggle_token` | `ConnectionSection`, `core/devices.py: valid_bot_token` |

## 11. Вызовы API (метод · путь · тело) — все сохранены

| | Вызов | Откуда |
|---|---|---|
| [x] | GET `/admin/stats` | `refresh`, `pick_registry` |
| [x] | POST `/admin/import/preview.json` body см. §3 | `do_import` |
| [x] | POST `/admin/import/authorize.json` `{source_kind, source_proof}` | `apply_import` |
| [x] | POST `/admin/import.json` preview-body + `expected_*` | `apply_import` |
| [x] | GET `/admin/export.json` (обогащение, без mode) | `enr_preview`, `do_enrich` |
| [x] | GET `/admin/export.json?mode=full&employee=…&visit_from&visit_to` | `do_export_full` |
| [x] | GET `/admin/markers.json` | `do_export_markers` |
| [x] | POST `/admin/period {from, to}` | `apply_period` |
| [x] | GET `/admin/recalled.json?limit&employee` | `load_recalled` |
| [x] | POST `/admin/reset {scope}` — заглушка, не вызывается | `do_reset` |
| [x] | GET `/admin/priority?w_debt&w_cluster&w_fresh&limit=40` | `load_priority` |
| [x] | GET `/admin/guests` | `load_roles`, `load_managers` |
| [x] | GET `/admin/employees` | `load_roles` |
| [x] | POST `/admin/bind {telegram_id, employee, kind}` | `bind_role` |
| [x] | POST `/admin/unbind {telegram_id}` | `unbind` |
| [x] | GET `/admin/emp_clients?employee=` | `load_emp_clients` |
| [x] | POST `/admin/unassign {employee, contract_ids}` | `do_unassign` |
| [x] | GET `/admin/chats` | `chats_refresh` |
| [x] | POST `/admin/chats.set {chat_id, enabled}` / `{chat_id, employees}` | `_chat_toggle`, диалог |
| [x] | GET `/admin/managers` | `load_managers` |
| [x] | POST `/admin/manager {telegram_id, name, cities, employees}` | `save_manager` |
| [x] | POST `/admin/manager_delete {telegram_id}` | `mgr_delete` |
| [x] | POST `/admin/digest_settings {time, enabled, promise_time, promise_enabled, visit_push[, owner_digest, owner_promise]}` | `save_digest` |
| [x] | GET `/admin/digest_settings` | `load_delivery` |
| [x] | GET `/admin/photo_geo` | `load_geo_retention` |
| [x] | POST `/admin/photo_geo {retention_days}` / `{forget:{employee, contract, before}}` | `save_geo_retention`, `forget_geo` |
| [x] | POST `/admin/push_test {kind: visit, telegram_id}` | `push_test` |
| [x] | GET `/admin/activity` | `load_activity` |
| [x] | POST `/admin/device/enroll.json {device_id, name}` | `device_enroll` |
| [x] | GET `/admin/devices.json` | `devices_load` |
| [x] | POST `/admin/device/revoke.json {id}` | `device_revoke` |
| [x] | POST `/admin/bot_token {token}` | `change_token` |
| [x] | POST `/admin/restart` | `restart_bot` |
| [x] | Новое: GET `/admin/enrichment/ledger.json?employees=…`, POST `/admin/enrichment/ledger.json {entries, receipt_digest}` | `enr_apply` |

## 12. Модульные функции, сохранённые под прежними именами в `pult.py`

| | Имя | Стало |
|---|---|---|
| [x] | `fmt_n`, `parse_num`, `to_iso`, `names_look_different` | `core/text.py` (реэкспорт) |
| [x] | `_cell_json`, `read_workbook_sheets` | `core/workbook.py` |
| [x] | `_col_width`, `write_registry_xlsx`, `MARKER_COLS`, `write_markers_xlsx` | `core/export.py` |
| [x] | `SECTIONS`, `TITLES` | `ui/nav.py` |
| [x] | `messagebox` (для monkeypatch в тестах) | `ui/dialogs.py` |
| [x] | `Pult`, `main`, `DEFAULT_URL`, `DEFAULT_KEY`, `SETTINGS_PATH`, `APP_DIR`, `HARDBOT_DIR` | `ui/main_window.py`, `app.py`, `core/*` |

## Что не перенесено и почему

- Виджеты Tk `Card`, `btn`, `entry`, `ChipFlow`, `KVGrid` — заменены Qt-аналогами
  в `ui/widgets.py`; Tk-имена не реэкспортируются (на них никто не ссылается).
- `_dpi_geometry`/`_styles`/DPI-awareness через ctypes — не нужны, Qt 6 масштабирует сам.
