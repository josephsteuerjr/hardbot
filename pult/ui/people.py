"""Раздел «Люди»: роли, привязки, снятие клиентов у сотрудника."""

from __future__ import annotations

import urllib.parse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from ..core.devices import valid_telegram_id
from ..core.people import (GUEST_PLACEHOLDER, KIND_LABELS, client_debt, client_label,
                           guest_label, kind_from_label, kind_index, kind_text, link_note,
                           role_status, unassign_text)
from ..core.text import fmt_n
from .dialogs import show_link
from .widgets import Button, Card, CheckList, Combo, Field, Note, Page, Rows, label, vcol


class PeopleSection:
    def build_people(self) -> Page:
        page = Page()
        rc = Card("Назначение ролей",
                  "ФИО берутся из реестра (колонка «ответственный»). Впиши Telegram ID "
                  "сотруднику (или выбери из писавших боту), тип — и «Привязать»: работник "
                  "с этим ID сразу привязан к своим клиентам. Можно переназначать.")
        self.roles_frame = rc.add(Rows(spacing=4))
        self.roles_frame.set_empty("Нет данных — импортируй реестр и нажми «Обновить»")
        page.add(rc)

        bc = Card("Кто вошёл в бот",
                  "Активные привязки telegram_id → сотрудник. «Сбросить» — отвязать.")
        self.binds_frame = bc.add(Rows())
        page.add(bc)

        uc = Card("Убрать клиентов у сотрудника",
                  "Выбери сотрудника — появится его список. Отметь галочками лишних и "
                  "нажми «Снять выбранных»: договор остаётся в реестре (без "
                  "ответственного) и пропадает из приложения сотрудника.")
        self.un_emp = Combo([], width_chars=32)
        self.un_emp.on_select(self.load_emp_clients)
        self.un_btn = Button("Снять выбранных (0)", self.do_unassign, kind="danger")
        self.un_btn.setEnabled(False)
        uc.add_row(self.un_emp, None, self.un_btn, stretch=False)
        self.un_frame = uc.add(Rows(spacing=4), spacing_before=4)
        self._un_vars = {}
        page.add(uc)
        return page

    _guest_label = staticmethod(guest_label)

    def load_roles(self):
        def work():
            try:
                guests = self.api("GET", "/admin/guests").get("guests") or []
            except RuntimeError:
                guests = []
            emps = self.api("GET", "/admin/employees").get("employees") or []
            return guests, emps

        def done(res):
            self._loaded.add("people")
            self.guests, emps = res
            self.un_emp.set_values([e["employee"] for e in emps])
            frame = self.roles_frame
            frame.clear()
            if not emps:
                frame.set_empty("Реестр пуст — импортируй xlsx на вкладке «Реестр»")
                return
            holder = QWidget()
            grid = QGridLayout(holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            for col, text in enumerate(("СОТРУДНИК", "ДОГОВОРОВ", "TELEGRAM ID",
                                        "ТИП", "СТАТУС", "", "ВХОД")):
                grid.addWidget(label(text, "caps"), 0, col)
            glabels = [guest_label(g) for g in self.guests]
            self._role_rows = []
            for r, e in enumerate(emps, 1):
                grid.addWidget(label(e["employee"]), r, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(label(fmt_n(e.get("contracts"))), r, 1,
                               Qt.AlignmentFlag.AlignTop)
                combo = Combo([GUEST_PLACEHOLDER] + glabels, width_chars=40)
                tid = Field(width_chars=16)
                if e.get("telegram_id"):
                    tid.set(str(e["telegram_id"]))
                combo.on_select(lambda c=combo, t=tid: self._pick_guest(c, t))
                grid.addLayout(vcol(combo, tid, spacing=2), r, 2)
                kind = Combo(KIND_LABELS, width_chars=11)
                kind.set_current(kind_index(e))
                grid.addWidget(kind, r, 3, Qt.AlignmentFlag.AlignTop)
                status_text, bound = role_status(e)
                grid.addWidget(Note(status_text, "success" if bound else "small", wrap=False),
                               r, 4, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(Button("Привязать",
                                      lambda emp=e["employee"], t=tid, k=kind:
                                      self.bind_role(emp, t, k), kind="accent"),
                               r, 5, Qt.AlignmentFlag.AlignTop)
                # Ссылка входа без Telegram: сервер сам выдаст сотруднику
                # служебную личность, если привязки ещё нет.
                grid.addWidget(Button("Ссылка входа",
                                      lambda emp=e["employee"], k=kind:
                                      self.issue_link(employee=emp, kind_combo=k)),
                               r, 6, Qt.AlignmentFlag.AlignTop)
                self._role_rows.append((combo, tid, kind))
            grid.setColumnStretch(7, 1)
            frame.add(holder)
        self.bg_task(work, done)

    def _pick_guest(self, combo, tid_entry):
        i = combo.current()
        if i > 0:
            tid_entry.set(str(self.guests[i - 1]["telegram_id"]))

    def bind_role(self, employee, tid_entry, kind_combo):
        tid = tid_entry.get().strip()
        if not valid_telegram_id(tid):
            self.toast("Впиши числовой Telegram ID (или выбери гостя из списка)", err=True)
            return
        kind = kind_from_label(kind_combo.get())
        body = {"telegram_id": int(tid), "employee": employee, "kind": kind}
        self.bg_task(lambda: self.api("POST", "/admin/bind", body),
                     lambda _d: (self.toast(f"Привязано: {employee} → {tid}"), self.refresh()))

    def _render_binds(self, bindings):
        frame = self.binds_frame
        frame.clear()
        if not bindings:
            frame.set_empty("Никто ещё не привязан")
            return
        for b in bindings:
            tid = label(str(b["telegram_id"]))
            tid.setFixedWidth(110)
            name = label(b["employee"])
            name.setFixedWidth(240)
            frame.add_row(tid, name, Note(kind_text(b), "badge", wrap=False), None,
                          Button("ссылка входа",
                                 lambda t=b["telegram_id"], e=b["employee"]:
                                 self.issue_link(telegram_id=t, employee=e)),
                          Button("сбросить", lambda t=b["telegram_id"]: self.unbind(t),
                                 kind="danger"), stretch=False)

    def issue_link(self, employee=None, telegram_id=None, kind_combo=None):
        """Персональная ссылка входа — та же, что бот даёт на слово «ссылка»,
        но из пульта и без Telegram. Показывается окном с кнопкой «Скопировать»."""
        body = {}
        if telegram_id:
            body["telegram_id"] = telegram_id
        if employee:
            body["employee"] = employee
        if kind_combo is not None:
            body["kind"] = kind_from_label(kind_combo.get())

        def done(d):
            if not isinstance(d, dict) or not d.get("url"):
                self.toast(str((d or {}).get("error") or "Сервер не выдал ссылку"), err=True)
                return
            show_link("Ссылка входа", d["url"], link_note(d), parent=self)
            if d.get("created"):
                self.refresh()

        self.bg_task(lambda: self.api("POST", "/admin/app_link.json", body), done)

    def unbind(self, tid):
        if not self.ask_yes_no("Сброс привязки", f"Сбросить выбор для {tid}?"):
            return
        self.bg_task(lambda: self.api("POST", "/admin/unbind", {"telegram_id": tid}),
                     lambda _d: (self.toast("Сброшено"), self.refresh()))

    # ── Убрать клиентов у сотрудника ────────────────────────────────────────
    def load_emp_clients(self):
        emp = self.un_emp.get().strip()
        if not emp:
            return
        path = "/admin/emp_clients?employee=" + urllib.parse.quote(emp)

        def done(d):
            frame = self.un_frame
            frame.clear()
            self._un_vars = {}
            clients = d.get("clients") or []
            if not clients:
                frame.set_empty("За сотрудником сейчас никого нет")
                self._un_seln()
                return
            frame.add_row(Note(f"Договоров: {len(clients)}", "small"), None,
                          Button("ничего", lambda: self._un_all(False)),
                          Button("все", lambda: self._un_all(True)), stretch=False)
            self.un_list = CheckList()
            self.un_list.set_items(
                [{"key": c["contract_id"], "text": client_label(c), "right": client_debt(c)}
                 for c in clients],
                on_toggle=lambda _k, _on: self._un_seln())
            self._un_vars = self.un_list.checks
            frame.add(self.un_list)
            self._un_seln()
        self.bg_task(lambda: self.api("GET", path), done)

    def _un_all(self, on):
        for v in self._un_vars.values():
            v.set(on)
        self._un_seln()

    def _un_seln(self):
        n = sum(1 for v in self._un_vars.values() if v.get())
        self.un_btn.configure(text=f"Снять выбранных ({n})",
                              state="normal" if n else "disabled")

    def do_unassign(self):
        emp = self.un_emp.get().strip()
        ids = [k for k, v in self._un_vars.items() if v.get()]
        if not emp or not ids:
            return
        if not self.ask_yes_no("Убрать клиентов", unassign_text(len(ids), emp)):
            return

        def done(d):
            self.toast(f"Снято: {d.get('n', 0)}")
            self.load_emp_clients()   # список пересобирается, счётчики — через refresh
            self.refresh()
        self.bg_task(lambda: self.api("POST", "/admin/unassign",
                                      {"employee": emp, "contract_ids": ids}), done)
