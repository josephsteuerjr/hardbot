"""Раздел «Реестр»: импорт, обогащение личных реестров, экспорт, период,
отозванные договоры. Плюс методы legacy-путей без карточек (разброс, сброс,
обогащение одного файла) — они остаются fail-closed."""

from __future__ import annotations

from pathlib import Path

import enrich
import razbros
from PySide6.QtWidgets import QWidget

from ..core import enrichment as en
from ..core import registry_import as ri
from ..core.constants import (LIMITED_TEST_ENRICH_DISABLED_MESSAGE,
                              LIMITED_TEST_ENRICH_WRITES_ENABLED)
from ..core.dashboard import period_text
from ..core.export import export_confirm_text, write_markers_xlsx, write_registry_xlsx
from ..core.text import fmt_n, to_iso, today_text
from . import dialogs
from .widgets import (Button, Card, CheckList, Combo, DateField, DecisionTable, Field,
                      KVGrid, Note, Page, Rows, Table, WrapCheck, hrow, label)


class RegistrySection:
    # ── сборка ────────────────────────────────────────────────────────────
    def build_registry(self) -> Page:
        page = Page()
        page.add(self._card_import())
        page.add(self._card_enrich())
        page.add(self._card_export())
        page.add(self._card_period())
        page.add(self._card_recalled())
        # У сброса базы карточки нет (fail-closed), но у пути есть вывод.
        self.reset_out = Note("", "warn")
        self.reset_out.hide()
        self._scatter_restore()
        return page

    def _card_import(self) -> Card:
        imp = Card("Импорт реестра",
                   "Общий реестр: опубликованный registry_cycle master или "
                   "утверждённое тестовое подмножество. Личные ФИО.xlsb сюда не "
                   "загружаются — офисные правки идут personal → registry_cycle → "
                   "master. Разрешение на загрузку регистрируется отдельно; если "
                   "ручка регистратора на сервере закрыта, пульт покажет команду.")
        imp.add_row(Button("Выбрать реестр…", self.pick_registry, kind="accent"))
        self.imp_file_lbl = imp.add(Note("", "small"))
        self.imp_pick_frame = imp.add(Rows(spacing=4))
        self.imp_full_var = imp.add(WrapCheck(
            "Для выбранных сотрудников это полный срез — снять у них договоры, "
            "которых нет в выбранных строках файла",
            on_toggle=self._imp_full_toggle), spacing_before=4)
        self.imp_restore_manual_var = imp.add(WrapCheck(
            "Вернуть ранее вручную снятые договоры, если они снова есть "
            "в выбранных строках файла с тем же ответственным",
            on_toggle=self._imp_restore_manual_toggle))
        self.imp_replace_current_comments_var = imp.add(WrapCheck(
            "Начало месяца: заменить текущие комментарии значениями файла "
            "(пустая ячейка очистит комментарий)",
            on_toggle=self._imp_comment_toggle))
        self.imp_import_historical_comments_var = imp.add(WrapCheck(
            "Разово загрузить историчные комментарии из файла "
            "(пустая ячейка очистит историчный комментарий)",
            on_toggle=self._imp_comment_toggle))
        self.imp_go_btn = Button("Предпросмотр выбранных (0)", self.do_import, kind="accent")
        self.imp_apply_btn = Button("Применить импорт", self.apply_import)
        self.imp_apply_btn.setEnabled(False)
        self.imp_go_row = QWidget()
        self.imp_go_row.setLayout(hrow(self.imp_go_btn, self.imp_apply_btn))
        imp.add(self.imp_go_row, spacing_before=4)
        self.imp_go_row.hide()
        self.imp_kvs = imp.add(KVGrid(), spacing_before=4)
        self.imp_warn = imp.add(Note("", "warn"))
        return imp

    def _card_enrich(self) -> Card:
        enr = Card("Обогащение личных реестров",
                   "Точечно переносит доказанные визиты бота в личные ФИО.xlsb: "
                   "пишутся только ячейки с доказательством визита, чужие правки "
                   "не трогаются. Сначала предпросмотр, запись — отдельной кнопкой.")
        enr.add_row(Button("Папка с республиками…", self.scatter_pick_base),
                    Button("Проверить соответствия (без записи)", self.enr_preview,
                           kind="accent"))
        self.enr_base_lbl = enr.add(Note("", "small"))

        # ОКНО ДАТ ВЫЕЗДА. Без него прогон переутверждает базу поверх всей
        # истории визитов: 04.08.2026 обогащение так вернуло в книгу офисные
        # правки за 03.08, сделанные уже после предыдущего прогона. Окно
        # предзаполняется из последней квитанции, пустое — только по явному
        # подтверждению «вся история».
        self.enr_since = DateField()
        self.enr_until = DateField()
        enr.add_row(label("Выезды с", "small"), self.enr_since, label("по", "small"),
                    self.enr_until, Note("ДД.ММ.ГГГГ · пусто = без границы", "tiny", wrap=False),
                    Button("Только сегодня", self._enr_window_today),
                    Button("Из квитанции", self._enr_suggest_window),
                    spacing_before=4)
        self.enr_window_note = enr.add(Note("", "tiny"))
        self.enr_kvs = enr.add(KVGrid(), spacing_before=4)
        self.enr_warn = enr.add(Note("", "warn"))
        self.enr_pick_frame = enr.add(Rows(spacing=4))
        # Порядок работы: «Проверить конфликты» строит план без Excel и без
        # записи, конфликты решаются в таблице ниже, и только «Записать»
        # открывает живые книги. Прежняя галочка «сервер главнее для всех
        # строк без истории» заменена решениями по каждой строке.
        self.enr_check_btn = Button("Проверить конфликты (без записи)", self.enr_check,
                                    kind="accent")
        self.enr_apply_sel_btn = Button("Записать выбранных (0)",
                                        lambda: self.enr_apply("selected"))
        self.enr_apply_all_btn = Button("Записать всех", lambda: self.enr_apply("all"))
        self.enr_apply_row = QWidget()
        self.enr_apply_row.setLayout(hrow(self.enr_check_btn, self.enr_apply_sel_btn,
                                          self.enr_apply_all_btn))
        enr.add(self.enr_apply_row, spacing_before=4)
        self.enr_apply_row.hide()
        self.enr_preview_out = enr.add(Note("", "small"), spacing_before=4)
        self.enr_decisions_title = enr.add(label("Конфликты — реши, что писать", bold=True),
                                           spacing_before=4)
        self.enr_decisions_hint = enr.add(Note(en.DECISION_HINT, "tiny"))
        self.enr_decisions_all_file = Button("Везде оставить как в книге",
                                             lambda: self.enr_decisions.set_all(en.DECISION_FILE))
        self.enr_decisions_all_server = Button("Везде записать сервер",
                                               lambda: self.enr_decisions.set_all(en.DECISION_SERVER))
        self.enr_decisions_row = QWidget()
        self.enr_decisions_row.setLayout(hrow(self.enr_decisions_all_file,
                                              self.enr_decisions_all_server, stretch=False))
        enr.add(self.enr_decisions_row)
        self.enr_decisions = enr.add(DecisionTable(
            en.CONFLICT_COLUMNS, en.DECISION_LABELS, en.DECISION_ORDER, en.DECISION_DEFAULT,
            stretch=("file", "server", "delivered"), visible_rows=12))
        for w in (self.enr_decisions_title, self.enr_decisions_hint,
                  self.enr_decisions_row, self.enr_decisions):
            w.hide()
        self._enr_preview = None          # {"keys", "since", "until"} последней проверки
        self.enrich_out = enr.add(Note("", "small"), spacing_before=4)
        self.enr_ledger_err = Note("", "danger")
        self.enr_replay_btn = Button("Повторить отправку из квитанции", self.enr_replay_ledger)
        self.enr_ledger_row = QWidget()
        self.enr_ledger_row.setLayout(hrow(self.enr_ledger_err, self.enr_replay_btn,
                                           stretch=False))
        enr.add(self.enr_ledger_row)
        self.enr_ledger_row.hide()
        self.enr_conflicts_title = enr.add(label("Конфликты", bold=True), spacing_before=4)
        self.enr_conflicts = enr.add(Table(en.CONFLICT_COLUMNS,
                                           stretch=("file", "server", "delivered"),
                                           visible_rows=10))
        self.enr_office_title = enr.add(label("Правки офиса", bold=True), spacing_before=4)
        self.enr_office = enr.add(Table(en.CONFLICT_COLUMNS,
                                        stretch=("file", "server", "delivered"),
                                        visible_rows=10))
        for w in (self.enr_conflicts_title, self.enr_conflicts,
                  self.enr_office_title, self.enr_office):
            w.hide()
        self._enr_last_receipt = None
        return enr

    def _card_export(self) -> Card:
        exp = Card("Экспорт-слепок",
                   "Новый xlsx из БД сервера. Можно выгрузить весь реестр либо "
                   "только выбранных сотрудников и независимо ограничить строки "
                   "доказанными выездами за период.")
        exp.add_row(label("Сотрудники:", "small"), None,
                    Button("снять", lambda: self._exp_all(False)),
                    Button("все", lambda: self._exp_all(True)), stretch=False)
        self.exp_pick_frame = exp.add(Rows())
        self.exp_pick_frame.set_empty("Подключись к серверу, чтобы увидеть сотрудников из БД")
        self.exp_since = DateField()
        self.exp_until = DateField()
        exp.add_row(label("Выезды с", "small"), self.exp_since, label("по", "small"),
                    self.exp_until, Note("пусто = без ограничения", "tiny", wrap=False),
                    Button("Только сегодня", self._exp_today), spacing_before=4)
        exp.add_row(Button("Подготовить выгрузку…", self.do_export_full, kind="accent"),
                    Button("Метки курьеров…", self.do_export_markers), spacing_before=4)
        # Кнопки «Запустить геокодинг» здесь больше нет. Координаты сервер ставит
        # сам после каждого импорта (scoring.geocode_city_centroids в конце
        # /admin/import.json), тем же офлайн-справочником и тем же кодом.
        return exp

    def _card_period(self) -> Card:
        per = Card("Период выгрузки",
                   "По дате закрепления: выездники видят только клиентов из этого окна. "
                   "Пусто — видны все. Формат: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.")
        self.p_from = Field(width_chars=12)
        self.p_to = Field(width_chars=12)
        per.add_row(self.p_from, label("—", "small"), self.p_to,
                    Button("Применить", self.apply_period, kind="accent"),
                    Button("Сбросить", self.clear_period))
        self.period_cur = per.add(Note("—", "small"))
        return per

    def _card_recalled(self) -> Card:
        rec = Card("Отозванные договоры",
                   "Кого офис снял с работы колонками отзыва в общем реестре. "
                   "Договор остаётся в базе со всей историей, но исчезает из "
                   "приложения выездника. Вернётся только когда офис очистит "
                   "колонки отзыва и реестр загрузят снова.")
        self.rec_emp_box = Combo([""], width_chars=24)
        rec.add_row(Button("Показать отозванных", self.load_recalled, kind="accent"),
                    Button("Показать все", lambda: self.load_recalled(limit=5000)),
                    6, label("Сотрудник:", "small"), self.rec_emp_box)
        self.rec_kvs = rec.add(KVGrid(), spacing_before=4)
        self.rec_tv = rec.add(Table(ri.RECALLED_COLUMNS, numeric=("debt",),
                                    stretch=("client", "reason"), visible_rows=14))
        return rec

    # ── отозванные ─────────────────────────────────────────────────────────
    def load_recalled(self, limit: int = 500):
        emp = (self.rec_emp_box.get() or "").strip() if hasattr(self, "rec_emp_box") else ""
        q = ri.recalled_query(limit, emp)

        def done(d):
            self.rec_kvs.set_pairs(ri.recalled_pairs(d))
            by_emp = d.get("by_employee") or {}
            self.rec_emp_box.set_values([""] + [n for n in by_emp if n])
            self.rec_tv.set_rows([ri.recalled_row(it) for it in d.get("items") or []])
        self.bg_task(lambda: self.api("GET", q), done)

    # ── импорт: выбор книги и галочки ───────────────────────────────────────
    def _imp_vars_reset(self):
        for var in (self.imp_full_var, self.imp_restore_manual_var,
                    self.imp_replace_current_comments_var,
                    self.imp_import_historical_comments_var):
            var.set(False)

    def pick_registry(self):
        """Шаг 1: выбрать книгу и показать, кто в ней есть. Ничего не отправляет."""
        path = dialogs.askopenfilename(
            "Реестр", [("Excel", "*.xlsx *.xlsm *.xlsb *.xls")], parent=self)
        if not path:
            return
        self._imp_vars_reset()
        self._invalidate_import_preview(clear_report=True)
        self.imp_file_lbl.setText("Читаю " + Path(path).name + "…")

        def work():
            book = ri.read_registry(path)
            stats = self.api("GET", "/admin/stats")
            book["db_employees"] = list(stats.get("employees") or [])
            book["employees"] = ri.merge_import_employees(
                book["employees"], book["db_employees"])
            return book

        def done(book):
            head, first, c_id, c_who = ri.grid_layout(book["grid"])
            book.update(head=head, first=first, c_id=c_id, c_who=c_who)
            self.imp_book = book
            self.db_employees = list(book.get("db_employees") or [])
            self.imp_selected = ri.default_import_selection(book["employees"])
            self.imp_full_var.set(False)
            self.imp_restore_manual_var.set(False)
            self._invalidate_import_preview(clear_report=True)
            self.imp_file_lbl.setText(ri.file_caption(book))
            self._imp_render()

        def fail(exc):
            self.imp_book = {}
            self.imp_selected = set()
            self.imp_full_var.set(False)
            self.imp_restore_manual_var.set(False)
            self._invalidate_import_preview(clear_report=True)
            self._imp_render()
            self.imp_file_lbl.setText(f"Файл не разобран: {exc}")
            self.toast("Реестр не прочитан", err=True)

        self.bg_task(work, done, fail)

    def _imp_render(self):
        self.imp_pick_frame.clear()
        self.imp_go_row.hide()
        book = self.imp_book
        if not book:
            return
        self.imp_pick_frame.add_row(
            Note("Кого грузить (галочки):", "small"), None,
            Button("снять", lambda: self._imp_all(False)),
            Button("есть в БД", self._imp_existing),
            Button("все", lambda: self._imp_all(True)), stretch=False)
        self.imp_list = CheckList()
        self.imp_list.set_items(
            [{"key": emp["key"], "text": emp["name"], "note": ri.employee_note(emp),
              "checked": emp["key"] in self.imp_selected}
             for emp in book["employees"]],
            on_toggle=self._imp_toggle)
        self._imp_vars = self.imp_list.checks
        self.imp_pick_frame.add(self.imp_list)
        self.imp_go_row.show()
        self._imp_count()

    def _imp_toggle(self, name, on):
        (self.imp_selected.add if on else self.imp_selected.discard)(name)
        self._invalidate_import_preview(clear_report=True)
        self._imp_count()

    def _imp_all(self, on):
        self.imp_selected = ({e["key"] for e in self.imp_book.get("employees") or []}
                             if on else set())
        self._imp_sync_selection()

    def _imp_existing(self):
        """Отметить строки файла только для сотрудников, уже известных БД."""
        self.imp_selected = ri.existing_import_selection(
            self.imp_book.get("employees") or [])
        self._imp_sync_selection()

    def _imp_sync_selection(self):
        if hasattr(self, "imp_list"):
            self.imp_list.set_checked(self.imp_selected)
        self._invalidate_import_preview(clear_report=True)
        self._imp_count()

    def _imp_count(self):
        self.imp_go_btn.configure(
            text=ri.preview_button_text(self.imp_book or {}, self.imp_selected,
                                        self.imp_full_var.get()),
            state="normal" if self.imp_selected else "disabled")

    def _imp_restore_manual_toggle(self):
        self._invalidate_import_preview(clear_report=True)
        if self.imp_restore_manual_var.get():
            self.imp_warn.configure(
                text=("ВОЗВРАТ РУЧНЫХ СНЯТИЙ: будут возвращены только договоры, "
                      "которые снова есть в выбранных строках с тем же сотрудником. "
                      "Точное число пульт покажет до записи."))
        self._imp_count()

    def _imp_comment_toggle(self):
        self._invalidate_import_preview(clear_report=True)
        notes = []
        if self.imp_replace_current_comments_var.get():
            notes.append(
                "Текущие комментарии будут заменены значениями файла, включая пустые.")
        if self.imp_import_historical_comments_var.get():
            notes.append(
                "Историчные комментарии будут разово загружены из файла, включая пустые.")
        if notes:
            notes.append("Перед записью пульт покажет точный предпросмотр изменений.")
            self.imp_warn.configure(text="\n".join(notes))
        self._imp_count()

    def _imp_full_toggle(self):
        self._invalidate_import_preview(clear_report=True)
        if self.imp_full_var.get():
            self.imp_warn.configure(text=(
                "ПОЛНЫЙ СРЕЗ: у выбранных сотрудников будут сняты договоры, "
                "которых нет среди их выбранных строк. Остальные сотрудники "
                "не затрагиваются."))
            self.toast("Полный режим включён для выбранных сотрудников")
        self._imp_count()

    # ── обогащение по папке республик (главный путь) ─────────────────────────
    _personal_files = staticmethod(en.personal_files)

    def enr_preview(self):
        if not self.scatter_base:
            self.toast("Сначала выбери папку с республиками", err=True)
            return
        self.enr_warn.configure(text="Проверяю: данные сервера + папки сотрудников…")
        base = self.scatter_base

        def work():
            d = self.api("GET", "/admin/export.json", timeout=120)
            enrich.validate_server_export(d)
            by_emp = enrich.split_by_employee(d["rows"])
            fmap = en.folder_map(base)
            return d["fields"], by_emp, fmap

        def done(res):
            fields, by_emp, fmap = res
            self._enr_fields = fields
            matched, nofolder, nofile = en.match_employees(by_emp, fmap)
            self.enr_matched = matched
            self.enr_selected = {m["key"] for m in matched}
            self.enr_kvs.set_pairs(en.preview_pairs(by_emp, matched))
            self.enr_warn.configure(text=en.preview_warning(by_emp, matched, nofolder, nofile))
            self._render_enr_pick()

        self.bg_task(work, done, lambda e: (self.enr_warn.configure(text=f"Ошибка: {e}"),
                                            self.toast(str(e), err=True)))

    def _render_enr_pick(self):
        self.enr_pick_frame.clear()
        self.enr_apply_row.hide()
        if not self.enr_matched:
            return
        self.enr_pick_frame.add_row(
            Note("Кого обогащать (галочки):", "small"), None,
            Button("снять", lambda: self._enr_all(False)),
            Button("все", lambda: self._enr_all(True)), stretch=False)
        self.enr_list = CheckList()
        self.enr_list.set_items(
            [{"key": m["key"], "text": m["employee"], "note": en.pick_note(m),
              "checked": m["key"] in self.enr_selected} for m in self.enr_matched],
            on_toggle=self._enr_toggle)
        self._enr_vars = self.enr_list.checks
        self.enr_pick_frame.add(self.enr_list)
        if LIMITED_TEST_ENRICH_WRITES_ENABLED:
            self.enr_apply_row.show()
        self._enr_seln()

    def _enr_toggle(self, k, on):
        (self.enr_selected.add if on else self.enr_selected.discard)(k)
        self._enr_seln()

    def _enr_all(self, on):
        for m in self.enr_matched:
            (self.enr_selected.add if on else self.enr_selected.discard)(m["key"])
        if hasattr(self, "enr_list"):
            self.enr_list.set_all(on)
        self._enr_seln()

    def _enr_seln(self):
        n = sum(1 for m in self.enr_matched if m["key"] in self.enr_selected)
        self.enr_apply_sel_btn.configure(text=f"Записать выбранных ({n})",
                                         state="normal" if n else "disabled")

    def _enr_window_today(self):
        today = today_text()
        self.enr_since.set(today)
        self.enr_until.set(today)

    def _enr_window(self) -> tuple[str, str]:
        """Введённое окно как есть. Разбор дат — в enrich.build_plan."""
        return (self.enr_since.get().strip(), self.enr_until.get().strip())

    def _enr_receipt_dir(self):
        return en.receipt_dir(self.scatter_base)

    def _enr_suggest_window(self, quiet: bool = False):
        """Окно из последней квитанции. По кнопке — всегда; при старте и смене
        папки — только если поля пустые, чтобы не затирать набранное."""
        if quiet and (self.enr_since.get().strip() or self.enr_until.get().strip()):
            return
        since, until = en.suggest_window(self._enr_receipt_dir())
        if since or until:
            self.enr_since.set(since)
            self.enr_until.set(until)
            self.enr_window_note.setText(
                f"Окно подставлено из последней квитанции: с {since or '—'} по {until or '—'}.")
        else:
            self.enr_window_note.setText(
                "Квитанций прошлых прогонов не найдено — окно надо задать самому."
                if self.scatter_base else "")
            if not quiet:
                self.toast("Квитанций нет — окно не подставлено", err=True)

    def _enr_picked(self, which: str) -> list[dict]:
        return [m for m in self.enr_matched
                if which == "all" or m["key"] in self.enr_selected]

    def _ledger_for(self, employees: list[str]) -> dict:
        # Реестр доставленного: сервер помнит, что пульт сам положил в книгу;
        # без него отличие файла от сервера неотличимо от правки офиса.
        answer = self.api("GET", en.ledger_query(employees), timeout=120) or {}
        return dict(answer.get("ledger") or {})

    def enr_check(self, which: str = "all"):
        """Проверка без Excel и без записи: план по книгам, конфликты — в таблицу
        решений. Живые книги при этом не открываются."""
        picked = self._enr_picked(which)
        if not picked:
            self.toast("Никто не выбран", err=True)
            return
        jobs = en.build_jobs(picked)
        since, until = self._enr_window()
        fields = self._enr_fields
        employees = [m["employee"] for m in picked]
        keys = frozenset(m["key"] for m in picked)
        log = lambda s: self.ui_call(lambda: self.enr_preview_out.setText(s))  # noqa: E731

        def work():
            ledger = self._ledger_for(employees)
            kwargs = en.preview_kwargs(since=since, until=until, ledger=ledger)
            return enrich.preview_many(jobs, fields, log, **kwargs)

        def done(results):
            self._enr_preview = {"keys": keys, "since": since, "until": until}
            self.enr_preview_out.setText("\n".join(en.preview_lines(results)))
            conflicts, office = en.collect_conflicts(results)
            self.enr_decisions.set_conflicts(conflicts, [en.conflict_row(c) for c in conflicts])
            for w in (self.enr_decisions_title, self.enr_decisions_hint,
                      self.enr_decisions_row, self.enr_decisions):
                w.setVisible(bool(conflicts))
            self.enr_decisions_title.setText(f"Конфликты — реши, что писать ({len(conflicts)})")
            self.enr_office.set_rows([en.conflict_row(c) for c in office])
            self.enr_office_title.setText(f"Правки офиса ({len(office)})")
            self.enr_office_title.setVisible(bool(office))
            self.enr_office.setVisible(bool(office))
            self.enr_conflicts_title.hide()
            self.enr_conflicts.hide()
            self.toast(f"Проверено: конфликтов {len(conflicts)}", err=False)

        self.bg_task(work, done, lambda e: (self.enr_preview_out.setText(f"Ошибка: {e}"),
                                            self.toast(str(e), err=True)))

    def _enr_decisions_for(self, picked: list[dict], since: str, until: str) -> dict:
        """Решения из таблицы — только если проверка делалась для того же
        набора сотрудников и того же окна; иначе решений нет."""
        keys = frozenset(m["key"] for m in picked)
        pv = self._enr_preview
        if not pv or pv["keys"] != keys or (pv["since"], pv["until"]) != (since, until):
            return {}
        return self.enr_decisions.decisions()

    def _enr_backup_root(self):
        return en.backup_root(self.scatter_base)

    def enr_apply(self, which: str):
        if not LIMITED_TEST_ENRICH_WRITES_ENABLED:
            self.enr_warn.configure(text=LIMITED_TEST_ENRICH_DISABLED_MESSAGE)
            self.toast(LIMITED_TEST_ENRICH_DISABLED_MESSAGE, err=True)
            return
        picked = self._enr_picked(which)
        if not picked:
            self.toast("Никто не выбран", err=True)
            return
        jobs = en.build_jobs(picked)
        since, until = self._enr_window()
        if not (since or until) and not self.ask_yes_no(en.WHOLE_HISTORY_TITLE,
                                                        en.WHOLE_HISTORY_TEXT):
            return
        decisions = self._enr_decisions_for(picked, since, until)
        note = (en.confirm_decisions_note(decisions, 0) if decisions
                else en.NO_CHECK_NOTE)
        if not self.ask_yes_no("Обогащение реестров",
                               en.confirm_text(len(picked), len(jobs), since, until)
                               + (("\n\n" + note) if note else "")):
            return
        fields = self._enr_fields
        log = lambda s: self.ui_call(lambda: self.enrich_out.setText(s))  # noqa: E731
        backup_root = self._enr_backup_root()
        receipts = self._enr_receipt_dir()
        employees = [m["employee"] for m in picked]

        def on_delivered(payload: dict):
            return self.api("POST", "/admin/enrichment/ledger.json", payload, timeout=120)

        def work():
            ledger = self._ledger_for(employees)
            kwargs = en.enrich_many_kwargs(
                since=since, until=until, backup_root=backup_root, receipt_dir=receipts,
                ledger=ledger, on_delivered=on_delivered,
                server_authoritative=False, decisions=decisions)
            return enrich.enrich_many(jobs, fields, log, **kwargs)

        self.bg_task(work, self._enr_done,
                     lambda e: (self.enrich_out.setText(f"Ошибка: {e}"),
                                self.toast(str(e), err=True)))

    def _enr_done(self, results: list[dict]):
        self.enrich_out.setText("\n".join(en.report_lines(results)))
        self._enr_last_receipt = next((r.get("receipt") for r in results if r.get("receipt")),
                                      None)
        # Решения применены — таблица решений больше не актуальна.
        self._enr_preview = None
        self.enr_decisions.clear()
        for w in (self.enr_decisions_title, self.enr_decisions_hint,
                  self.enr_decisions_row, self.enr_decisions):
            w.hide()
        err = en.ledger_error(results)
        self.enr_ledger_err.setText(err)
        self.enr_ledger_row.setVisible(bool(err))
        conflicts, office = en.collect_conflicts(results)
        self.enr_conflicts.set_rows([en.conflict_row(c) for c in conflicts])
        self.enr_office.set_rows([en.conflict_row(c) for c in office])
        self.enr_conflicts_title.setText(f"Конфликты ({len(conflicts)})")
        self.enr_office_title.setText(f"Правки офиса ({len(office)})")
        self.enr_conflicts_title.setVisible(bool(conflicts))
        self.enr_conflicts.setVisible(bool(conflicts))
        self.enr_office_title.setVisible(bool(office))
        self.enr_office.setVisible(bool(office))
        text, err_flag = en.toast_text(results)
        self.toast(text, err=err_flag)

    def enr_replay_ledger(self):
        """Книги обогащены, а сервер не узнал — дослать реестр из квитанции."""
        receipt = self._enr_last_receipt or en.last_receipt(self._enr_receipt_dir())
        if not receipt:
            self.toast("Квитанция последнего прогона не найдена", err=True)
            return

        def on_delivered(payload: dict):
            return self.api("POST", "/admin/enrichment/ledger.json", payload, timeout=120)

        def done(d):
            self.enr_ledger_row.hide()
            self.toast(f"Реестр доставленного отправлен: записано {fmt_n(d.get('recorded'))}")

        self.toast("Отправляю реестр доставленного из квитанции…")
        self.bg_task(lambda: en.replay_receipt(receipt, on_delivered), done)

    # --- обогащение одного файла (запасной путь, кнопки в UI нет) ---
    def do_enrich(self):
        if not LIMITED_TEST_ENRICH_WRITES_ENABLED:
            self.enr_warn.configure(text=LIMITED_TEST_ENRICH_DISABLED_MESSAGE)
            self.toast(LIMITED_TEST_ENRICH_DISABLED_MESSAGE, err=True)
            return
        path = dialogs.askopenfilename(
            "Живой файл реестра для обогащения",
            [("Excel", "*.xlsb *.xlsx *.xlsm *.xls")],
            initialfile=self.settings.get("enrich_file", ""), parent=self)
        if not path:
            return
        if not self.ask_yes_no(
                "Обогатить реестр",
                f"{Path(path).name}\n\nВ файл будут дописаны ТОЛЬКО данные визитов "
                "(заполненные ботом кейсы и их столбцы). Остальные строки, столбцы, "
                "листы и форматирование не трогаются.\n\nРезервная копия будет создана "
                "рядом с файлом. Если в файле правили руками — сначала запусти импорт. "
                "Продолжить?"):
            return
        self.settings["enrich_file"] = path
        self._save_settings()
        self.enrich_out.setText("Забираю данные с сервера…")

        def work():
            d = self.api("GET", "/admin/export.json", timeout=120)
            enrich.validate_server_export(d)
            log = lambda s: self.ui_call(lambda: self.enrich_out.setText(s))  # noqa: E731
            return enrich.enrich_file(path, d["fields"], d["rows"], log=log)

        def done(p):
            self.enrich_out.setText(en.single_file_report(p))
            self.toast(f"Обогащено: {p['changed_cases']} кейсов, {p['cells_written']} ячеек")

        def fail(e):
            self.enrich_out.setText(f"Ошибка: {e}")
            self.toast(str(e), err=True)
        self.bg_task(work, done, fail)

    # ── экспорт: слепок/метки ─────────────────────────────────────────────────
    def _render_export_employees(self):
        if not hasattr(self, "exp_pick_frame"):
            return
        rows = ri.merge_import_employees([], self.db_employees)
        keys = {row["key"] for row in rows}
        old_keys = set(self._exp_vars)
        if not self._exp_initialized or (old_keys and self.exp_selected == old_keys):
            self.exp_selected = set(keys)
            self._exp_initialized = True
        else:
            self.exp_selected.intersection_update(keys)
        self.exp_pick_frame.clear()
        self._exp_vars = {}
        if not rows:
            self.exp_pick_frame.set_empty(
                "Закреплённых сотрудников в БД нет; полный экспорт всё "
                "равно включит строки без ответственного.")
            return
        self.exp_list = CheckList(columns=2)
        self.exp_list.set_items(
            [{"key": e["key"], "text": f"{e['name']} · {fmt_n(e.get('db_count') or 0)} дог.",
              "checked": e["key"] in self.exp_selected} for e in rows],
            on_toggle=self._exp_toggle)
        self._exp_vars = self.exp_list.checks
        self.exp_pick_frame.add(self.exp_list)

    def _exp_toggle(self, key: str, on: bool):
        (self.exp_selected.add if on else self.exp_selected.discard)(key)

    def _exp_all(self, on: bool):
        self.exp_selected = set(self._exp_vars) if on else set()
        if hasattr(self, "exp_list"):
            self.exp_list.set_checked(self.exp_selected)

    def _exp_today(self):
        today = today_text()
        self.exp_since.set(today)
        self.exp_until.set(today)

    def do_export_full(self):
        try:
            request = self._export_request()
        except ValueError as exc:
            self.toast(str(exc), err=True)
            return
        self.toast("Готовлю точный состав выгрузки…")

        def work():
            # Raw-слепок отделён от безопасного потока обогащения: без mode=full
            # сервер намеренно очищает все неподтверждённые поля визита.
            d = self.api("GET", request["path"], timeout=120)
            enrich.validate_server_export(d)
            return d

        def ready(data):
            if not self.ask_yes_no("Экспорт реестра",
                                   export_confirm_text(request, len(data["rows"]))):
                return
            path = dialogs.asksaveasfilename(
                "Куда сохранить слепок реестра", "registry_export.xlsx",
                [("Excel", "*.xlsx")], defaultextension=".xlsx", parent=self)
            if not path:
                return

            def save():
                write_registry_xlsx(path, data["fields"], data["rows"])
                return len(data["rows"])

            self.bg_task(save, lambda count: self.toast(
                f"Сохранено: {path} ({fmt_n(count)} строк)"))

        self.bg_task(work, ready)

    def do_export_markers(self):
        path = dialogs.asksaveasfilename(
            "Куда сохранить метки", "markers.xlsx", [("Excel", "*.xlsx")],
            defaultextension=".xlsx", parent=self)
        if not path:
            return

        def work():
            d = self.api("GET", "/admin/markers.json", timeout=120)
            write_markers_xlsx(path, d["rows"])
            return len(d["rows"])
        self.bg_task(work, lambda n: self.toast(f"Сохранено: {path} ({fmt_n(n)} меток)"))

    # ── период ──────────────────────────────────────────────────────────────
    def _show_period(self, d):
        self.p_from.set(d.get("from") or "")
        self.p_to.set(d.get("to") or "")
        t = period_text(d)
        self.period_cur.setText(t)
        self.period_dash.setText(t)

    def apply_period(self):
        try:
            body = {"from": to_iso(self.p_from.get()), "to": to_iso(self.p_to.get())}
        except ValueError as e:
            self.toast(str(e), err=True)
            return
        self.bg_task(lambda: self.api("POST", "/admin/period", body),
                     lambda d: (self._show_period(d), self.toast("Период сохранён")))

    def clear_period(self):
        self.p_from.set("")
        self.p_to.set("")
        self.apply_period()

    # ── разброс (legacy; карточки нет, запись закрыта) ───────────────────────
    def _legacy(self, name: str, factory):
        """Виджеты legacy-путей без карточки: создаются скрытыми по требованию."""
        if not hasattr(self, name):
            w = factory()
            w.hide()
            setattr(self, name, w)
        return getattr(self, name)

    def _scatter_restore(self):
        self.scatter_base = self.settings.get("scatterBase", "")
        self.scatter_source = self.settings.get("scatterSource", "")
        self._scatter_labels()
        self._enr_suggest_window(quiet=True)

    def _scatter_labels(self):
        base_txt = ("база: " + self.scatter_base) if self.scatter_base \
            else "базовая папка не выбрана"
        if hasattr(self, "enr_base_lbl"):    # общая база с карточкой обогащения
            self.enr_base_lbl.configure(text=base_txt)
        if hasattr(self, "sc_base_lbl"):
            self.sc_base_lbl.configure(text=base_txt)
            self.sc_src_lbl.configure(
                text=("экспорт: " + self.scatter_source)
                if self.scatter_source else "файл экспорта не выбран")
            self.sc_roots_lbl.configure(
                text="\n".join("прямая папка: " + r for r in self.scatter_roots))

    def scatter_pick_base(self):
        p = dialogs.askdirectory("Папка, в которой лежат папки республик",
                                 initialdir=self.scatter_base, parent=self)
        if p:
            self.scatter_base = p
            self.settings["scatterBase"] = p
            self._save_settings()
            self._scatter_labels()
            self._enr_suggest_window(quiet=True)

    def scatter_pick_source(self):
        p = dialogs.askopenfilename(
            "Экспорт кейсов (формат «Хард»)",
            [("Экспорт кейсов", "*.xlsb *.xlsx *.xlsm *.xls")], parent=self)
        if p:
            self.scatter_source = p
            self.settings["scatterSource"] = p
            self._save_settings()
            self._scatter_labels()

    def scatter_add_root(self):
        p = dialogs.askdirectory("Прямая папка портфелей (в обход маски)", parent=self)
        if p and p not in self.scatter_roots:
            self.scatter_roots.append(p)
            self._scatter_labels()

    def scatter_run(self, apply: bool, which: str = "all"):
        if apply:
            self.toast(razbros.LEGACY_SCATTER_DISABLED_MESSAGE, err=True)
            return
        if not self.scatter_base and not self.scatter_roots:
            self.toast("Выбери базовую папку (или прямую папку)", err=True)
            return
        if not self.scatter_source:
            self.toast("Выбери файл экспорта кейсов", err=True)
            return
        sc_warn = self._legacy("sc_warn", lambda: Note("", "warn"))
        sc_kvs = self._legacy("sc_kvs", KVGrid)
        sc_warn.configure(text="Проверяю совпадения…")
        base, roots, source = self.scatter_base, list(self.scatter_roots), self.scatter_source

        def work():
            reps = razbros.load_config(None)
            rts = razbros.resolve_roots(base or None, reps, roots)
            fmap = razbros.build_folder_map(rts)
            emps = razbros.read_source_employees(Path(source))
            return False, razbros.make_report(fmap, emps)

        def done(res):
            _was_apply, d = res
            sc_warn.configure(text="")
            c = d.get("counts") or {}
            self.scatter_matched = d.get("matched") or []
            sc_kvs.set_pairs([
                ("в экспорте сотрудников", d.get("employees_total", 0)),
                ("нашли папку", c.get("matched", 0)),
                ("без папки", c.get("unmatched", 0)),
                ("лишние папки", c.get("extra", 0))])
            warn = ""
            if d.get("unmatched"):
                warn = f"Без папки на диске ({len(d['unmatched'])}): " + ", ".join(d["unmatched"])
            if d.get("extra_folders"):
                warn += ("\n" if warn else "") + "Папки без строк в экспорте: " \
                        + ", ".join(d["extra_folders"])
            sc_warn.configure(text=warn)
            self._render_scatter_pick()
        self.bg_task(work, done, lambda e: (sc_warn.configure(text=f"Ошибка: {e}"),
                                            self.toast(str(e), err=True)))

    def _render_scatter_pick(self):
        self._sc_vars = {}
        self.scatter_selected = {m["employee"].lower() for m in self.scatter_matched
                                 if m["employee"].lower() in self.scatter_selected}
        self._scatter_seln()

    def _scatter_toggle(self, k, on):
        (self.scatter_selected.add if on else self.scatter_selected.discard)(k)
        self._scatter_seln()

    def _scatter_all(self, on):
        for m in self.scatter_matched:
            k = m["employee"].lower()
            (self.scatter_selected.add if on else self.scatter_selected.discard)(k)
        self._scatter_seln()

    def _scatter_seln(self):
        n = sum(1 for m in self.scatter_matched
                if m["employee"].lower() in self.scatter_selected)
        btn = self._legacy("sc_apply_sel_btn",
                           lambda: Button("Разложить выбранных (0)",
                                          lambda: self.scatter_run(True, "selected")))
        btn.configure(text=f"Разложить выбранных ({n})", state="normal" if n else "disabled")
