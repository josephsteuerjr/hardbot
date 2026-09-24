"""Поток импорта: предпросмотр → применение, плюс fail-closed заглушки.

`ImportFlow` — примесь без Qt. Она смотрит на «вид» через небольшой протокол
(так же, как исторически смотрел Tk-пульт, и так же, как его подменяют
тесты-пробники):

* атрибуты состояния: `imp_book`, `imp_selected`, `db_employees`,
  `exp_selected`, `_imp_preview_body`, `_imp_preview_signature`,
  `_imp_preview_meta`, `_proof_path`;
* переключатели с `.get()`: `imp_full_var`, `imp_restore_manual_var`,
  `imp_replace_current_comments_var`; поля с `.get()`: `exp_since`, `exp_until`;
* виджеты вывода: `imp_kvs.set_pairs(pairs)`, `imp_warn.configure(text=…)`,
  `imp_apply_btn.configure(text=…, state=…)`, `reset_out.configure(text=…)`;
* методы окна: `toast(text, err=False)`, `bg_task(fn, done, fail)`,
  `api(method, path, body=None, timeout=20)`, `refresh()`.

Qt-виджеты пульта (`ui/widgets.py`) реализуют `configure`/`get`/`set_pairs`
ровно ради этого протокола.
"""

from __future__ import annotations

from pathlib import Path

from . import registry_import as ri
from .constants import (
    LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE,
    LIMITED_TEST_MASTER_IMPORT_UI_ENABLED,
    LIMITED_TEST_RESET_DISABLED_MESSAGE,
    LIMITED_TEST_RESET_UI_ENABLED,
)
from .export import export_request
from .text import fmt_n


def _toggle(view, name: str) -> bool:
    """Галочка вида, которой может и не быть (старые пробники её не строят)."""
    return bool(getattr(view, name).get()) if hasattr(view, name) else False


class ImportFlow:
    """Предпросмотр и применение импорта поверх протокола вида (см. модуль).

    Методы не полагаются ни на что своё: `Pult.do_import(probe)` обязан работать
    и для чужого объекта, у которого есть только атрибуты протокола."""

    # Куда положен proof последней попытки — чтобы его можно было зарегистрировать
    # руками, если HTTP-ручка регистратора на сервере выключена.
    _proof_path = None

    # --- сигнатура выбора и сброс предпросмотра --------------------------------
    def _imp_signature(self) -> tuple:
        book = self.imp_book or {}
        return (
            book.get("file_sha256"), tuple(sorted(self.imp_selected)),
            _toggle(self, "imp_full_var"), _toggle(self, "imp_restore_manual_var"),
            _toggle(self, "imp_replace_current_comments_var"),
        )

    def _invalidate_import_preview(self, *, clear_report: bool):
        self._imp_preview_body = None
        self._imp_preview_signature = None
        self._imp_preview_meta = {}
        if hasattr(self, "imp_apply_btn"):
            self.imp_apply_btn.configure(text="Применить импорт", state="disabled")
        if clear_report and hasattr(self, "imp_kvs"):
            self.imp_kvs.set_pairs([])
            self.imp_warn.configure(text="")

    # --- подготовка -------------------------------------------------------------
    def _prepare_import(self, book: dict, picked: list[str], *,
                        full_registry: bool = False,
                        force_test_subset: bool = False) -> dict:
        prepared, proof_path = ri.prepare_import(
            book, picked, full_registry=full_registry,
            force_test_subset=force_test_subset)
        self._proof_path = proof_path
        return prepared

    # --- предпросмотр -----------------------------------------------------------
    def do_import(self):
        """Построить read-only preview. Запись доступна только отдельной кнопкой."""
        if not LIMITED_TEST_MASTER_IMPORT_UI_ENABLED:
            self.imp_kvs.set_pairs([])
            self.imp_warn.configure(text=LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE)
            self.toast(LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE, err=True)
            return
        book = self.imp_book
        if not book:
            self.toast("Сначала выбери реестр", err=True)
            return
        picked = sorted(self.imp_selected)
        if not picked:
            self.toast("Отметь хотя бы одного сотрудника", err=True)
            return
        full_registry = bool(self.imp_full_var.get())
        replace_current_comments = _toggle(self, "imp_replace_current_comments_var")
        restore_manual = _toggle(self, "imp_restore_manual_var")
        signature = self._imp_signature()
        self._invalidate_import_preview(clear_report=True)
        self.toast(f"Проверяю импорт {Path(book['path']).name}: {len(picked)} сотр…")

        def work():
            prepared = self._prepare_import(
                book, picked, full_registry=full_registry,
                force_test_subset=restore_manual,
            )
            body = ri.preview_body(
                prepared, full_registry=full_registry, restore_manual=restore_manual,
                replace_current_comments=replace_current_comments,
            )
            result = self.api("POST", "/admin/import/preview.json", body, timeout=180)
            return {"report": result, "body": body, "prepared": prepared,
                    "signature": signature}

        def done(payload):
            d = payload["report"]
            if payload["signature"] != self._imp_signature():
                self._invalidate_import_preview(clear_report=True)
                self.toast("Выбор изменился во время проверки — предпросмотр отменён",
                           err=True)
                return
            if d.get("error"):
                self._show_import_error(d)
                return
            expected = self._expected_from_preview(d, payload["body"])
            if isinstance(expected, str):
                self._invalidate_import_preview(clear_report=True)
                self.toast(expected, err=True)
                return
            self._imp_preview_body = {**payload["body"], **expected}
            self._imp_preview_signature = payload["signature"]
            self._imp_preview_meta = payload["prepared"]
            self.imp_kvs.set_pairs(self._imp_pairs(d))
            notes = self._imp_notes(d)
            notes.insert(0, ri.preview_headline(d, full_registry))
            self.imp_warn.configure(text="\n".join(notes))
            self.imp_apply_btn.configure(text=ri.apply_button_label(d), state="normal")
            unassigned = int(d.get("auto_unassigned") or 0)
            self.toast("Предпросмотр готов"
                       + (f": будет снято {fmt_n(unassigned)}" if full_registry else ""))

        self.bg_task(work, done)

    # --- применение -------------------------------------------------------------
    def apply_import(self):
        """Применить проверенные preview proof/sheets/full + ожидаемый счётчик."""
        body = self._imp_preview_body
        meta = self._imp_preview_meta
        if not body or self._imp_preview_signature != self._imp_signature():
            self._invalidate_import_preview(clear_report=True)
            self.toast("Сначала сделай свежий предпросмотр импорта", err=True)
            return
        self.imp_apply_btn.configure(state="disabled")
        self.toast("Применяю проверенный импорт…")

        def work():
            # Разрешение на импорт — отдельный акт. Preview его не создаёт и не
            # consume-ит; регистрация начинается только после явного «Применить».
            registered = None
            try:
                registered = self.api(
                    "POST", "/admin/import/authorize.json",
                    {"source_kind": meta["source_kind"],
                     "source_proof": meta["source_proof"]},
                    timeout=60,
                )
            except Exception:                              # noqa: BLE001
                registered = None
            result = self.api("POST", "/admin/import.json", body, timeout=180)
            if isinstance(result, dict) and result.get("error"):
                result = {
                    **result,
                    "_proof_digest": meta["proof_digest"],
                    "_source_kind": meta["source_kind"],
                    "_registered": bool(registered and registered.get("ok")),
                }
            return result

        def done(d):
            if d.get("error"):
                self._show_import_error(d, registration_hint=True)
                if d.get("preview_stale"):
                    self._invalidate_import_preview(clear_report=False)
                else:
                    self.imp_apply_btn.configure(state="normal")
                return
            self.imp_kvs.set_pairs(self._imp_pairs(d))
            self.imp_warn.configure(text="\n".join(self._imp_notes(d)))
            self._invalidate_import_preview(clear_report=False)
            text, err = ri.apply_toast(d)
            self.toast(text, err=err)
            # Новые люди из реестра сразу получают офис по папкам портфелей —
            # иначе они неделями висят в сводке руководителя «Без офиса».
            if hasattr(self, "auto_offices"):
                self.auto_offices()
            self.refresh()

        def fail(exc):
            if self._imp_preview_signature == self._imp_signature():
                self.imp_apply_btn.configure(state="normal")
            self.toast(str(exc), err=True)

        self.bg_task(work, done, fail)

    def _show_import_error(self, d: dict, *, registration_hint: bool = False):
        self.imp_kvs.set_pairs([])
        self.imp_warn.configure(text=ri.import_error_text(
            d, self._proof_path, registration_hint_wanted=registration_hint))
        self.toast("Импорт не прошёл", err=True)

    # --- статические обёртки (имена сохранены для тестов и пробников) -------------
    _emp_key = staticmethod(ri.emp_key)
    _merge_import_employees = staticmethod(ri.merge_import_employees)
    _existing_import_selection = staticmethod(ri.existing_import_selection)
    _default_import_selection = staticmethod(ri.default_import_selection)
    _grid_for = staticmethod(ri.grid_for)
    _grid_layout = staticmethod(ri.grid_layout)
    _scope_from_grid = staticmethod(ri.scope_from_grid)
    _expected_from_preview = staticmethod(ri.expected_from_preview)
    _imp_pairs = staticmethod(ri.import_pairs)
    _imp_notes = staticmethod(ri.import_notes)
    read_registry = staticmethod(ri.read_registry)

    # --- экспорт ----------------------------------------------------------------
    def _export_request(self) -> dict:
        employees = self._merge_import_employees([], self.db_employees)
        return export_request(employees, self.exp_selected,
                              self.exp_since.get(), self.exp_until.get())

    # --- fail-closed заглушки -----------------------------------------------------
    def do_import_batch(self):
        """Личные книги никогда не импортируются в hardbot.

        Метод оставлен как fail-closed совместимость для старых горячих клавиш/
        сборок; в актуальном UI кнопки нет."""
        self.imp_warn.configure(
            text="Загрузка личных реестров в hardbot отключена: "
                 "personal ↔ master выполняет registry_cycle."
        )
        self.toast("Импорт личных реестров запрещён", err=True)

    def do_reset(self, scope):
        """Сброс базы из пульта закрыт: он обходит snapshot/export/clearance.

        Тексты подтверждений сохранены на случай, если владелец вернёт кнопку:
        «ПОЛНОСТЬЮ очистить реестр?…» для scope=all и «Сбросить данные визитов
        у всех клиентов?…» для остальных; ответ сервера — clients_removed /
        clients_reset."""
        if not LIMITED_TEST_RESET_UI_ENABLED:
            self.reset_out.configure(text=LIMITED_TEST_RESET_DISABLED_MESSAGE)
            self.toast(LIMITED_TEST_RESET_DISABLED_MESSAGE, err=True)
            return
        msg = ("ПОЛНОСТЬЮ очистить реестр?\nУдалятся ВСЕ клиенты, визиты и метки, без "
               "возможности отката (привязки сотрудников останутся)."
               if scope == "all" else
               "Сбросить данные визитов у всех клиентов?\nОбнулятся статусы, комментарии, "
               "даты визита и фото. Клиенты, долги и закрепление останутся.")
        if not self.ask_yes_no("Очистка данных", msg):
            return

        def done(d):
            txt = (f"Реестр очищен · было клиентов: {fmt_n(d.get('clients_removed'))}"
                   if scope == "all" else
                   f"Данные визитов сброшены · клиентов: {fmt_n(d.get('clients_reset'))}")
            self.reset_out.configure(text=txt)
            self.toast(txt)
            self.refresh()
        self.bg_task(lambda: self.api("POST", "/admin/reset", {"scope": scope}), done)
