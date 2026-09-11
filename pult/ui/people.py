"""Раздел «Сотрудники»: активность и вход одной таблицей, роли, привязки,
снятие клиентов. Слиты бывшие «Люди» и «Активность» — это одни и те же люди."""

from __future__ import annotations

import urllib.parse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from ..core import activity as act
from ..core.devices import valid_telegram_id
from ..core.people import (GUEST_PLACEHOLDER, KIND_LABELS, client_debt, client_label,
                           guest_label, kind_from_label, kind_index, kind_text, link_note,
                           role_status, unassign_text)
from ..core.text import fmt_n
from .dialogs import show_link
from .widgets import (Button, Card, CheckList, ChipFlow, Combo, Field, Note, Page, Rows,
                      Table, label, vcol)


PLAN_COMMON = "общая"
OFFICE_NONE = "— не задан —"


class PeopleSection:
    def build_people(self) -> Page:
        page = Page()
        self._offices: dict[str, str] = {}
        self._office_known: list[str] = []

        ac = Card("Активность и вход",
                  "Каждый визит, метка, фото и вход в приложение пишутся в базу — здесь "
                  "сводка. Клик по заголовку сортирует, поле ищет по любой колонке, "
                  "чипы фильтруют. Выбери строку — кнопки ниже действуют на неё.")
        self.act_summary = ac.add(Note("", "small"))
        self.act_search = Field(width_chars=30, placeholder="поиск по ФИО, статусу…")
        self.act_search.textChanged.connect(lambda t: self.act_tv.set_filter(t))
        self.act_filters = ChipFlow()
        ac.add_row(self.act_search, 8, self.act_filters, spacing_before=4, stretch=False)
        self.act_tv = ac.add(Table(act.ACTIVITY_COLUMNS, numeric=act.NUMERIC,
                                   stretch=("emp", "status"), visible_rows=16),
                             spacing_before=4)
        self.act_tv.on_select(self._act_selected)
        self.act_pick = Note("", "tiny")
        self.act_link_btn = Button("Ссылка входа", self._act_link)
        self.act_unassign_btn = Button("Снять клиентов…", self._act_unassign)
        for b in (self.act_link_btn, self.act_unassign_btn):
            b.setEnabled(False)
        ac.add_row(self.act_link_btn, self.act_unassign_btn, self.act_pick,
                   None, Button("Обновить", self.load_people), spacing_before=4,
                   stretch=False)
        page.add(ac)
        self._act_workers: list[dict] = []
        self._act_filter = "all"
        self._act_row: tuple | None = None

        rc = Card("Назначение ролей",
                  "ФИО берутся из реестра (колонка «ответственный»). Впиши Telegram ID "
                  "сотруднику (или выбери из писавших боту), тип — и «Привязать»: работник "
                  "с этим ID сразу привязан к своим клиентам. Можно переназначать. "
                  "«Офис» — где сидит сам сотрудник (Душанбе, Худжанд, Ташкент…): "
                  "по нему считается штат руководителя города, сохраняется сразу.")
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

    # --- активность -------------------------------------------------------------
    def load_people(self):
        self.load_activity()
        self.load_roles()

    def load_activity(self):
        def done(d):
            self._act_workers = list(d.get("workers") or [])
            self._render_activity()
        self.bg_task(lambda: self.api("GET", "/admin/activity"), done)

    def _render_activity(self):
        workers = self._act_workers
        latest = act.latest_shell(workers)
        counts = act.filter_counts(workers)
        self.act_filters.set_chips(
            [(f"{title} · {counts.get(key, 0)}", key == self._act_filter,
              lambda k=key: self._act_set_filter(k)) for key, title in act.FILTERS])
        rows = [act.activity_row(w) for w in workers if act.matches(w, self._act_filter, latest)]
        self.act_tv.set_rows(rows)
        self.act_tv.set_filter(self.act_search.get())
        self.act_summary.setText(act.summary_text(workers))
        self._act_selected(None)

    def _act_set_filter(self, key):
        self._act_filter = key
        self._render_activity()

    def _act_selected(self, row):
        self._act_row = row
        on = bool(row)
        self.act_link_btn.setEnabled(on)
        self.act_unassign_btn.setEnabled(on)
        self.act_pick.setText(f"выбран: {row[0]}" if row else "")

    def _act_link(self):
        if self._act_row:
            self.issue_link(employee=self._act_row[0])

    def _act_unassign(self):
        if not self._act_row:
            return
        emp = self._act_row[0]
        values = [self.un_emp.itemText(i) for i in range(self.un_emp.count())]
        if emp in values:
            self.un_emp.set_current(values.index(emp))
            self.load_emp_clients()
            self.pages["people"].scroll_to(self.un_frame)
        else:
            self.toast(f"«{emp}» нет в реестре как ответственного", err=True)

    # --- роли -------------------------------------------------------------------
    def load_roles(self):
        def work():
            try:
                guests = self.api("GET", "/admin/guests").get("guests") or []
            except RuntimeError:
                guests = []
            emps_payload = self.api("GET", "/admin/employees")
            emps = emps_payload.get("employees") or []
            try:
                offices = self.api("GET", "/admin/employee_cities.json")
            except RuntimeError:
                offices = {}      # старый сервер про офисы не знает
            return guests, emps, offices, emps_payload

        def done(res):
            self._loaded.add("people")
            self.guests, emps, offices, emps_meta = res
            self._offices = dict(offices.get("cities") or {})
            self._office_known = list(offices.get("known") or [])
            self._plan_groups = list(emps_meta.get("plan_groups") or (8, 10, 12))
            self._plan_common = emps_meta.get("plan_common") or 10
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
            for col, text in enumerate(("СОТРУДНИК", "ДОГОВОРОВ", "ОФИС", "НОРМА",
                                        "TELEGRAM ID", "ТИП", "СТАТУС", "", "ВХОД")):
                grid.addWidget(label(text, "caps"), 0, col)
            glabels = [guest_label(g) for g in self.guests]
            self._role_rows = []
            for r, e in enumerate(emps, 1):
                grid.addWidget(label(e["employee"]), r, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(label(fmt_n(e.get("contracts"))), r, 1,
                               Qt.AlignmentFlag.AlignTop)
                # Офис. Из данных он не выводится: город в реестре — это где живёт
                # ДОЛЖНИК. На офисе держится охват руководителя по городу, поэтому
                # он правится прямо здесь и сохраняется сразу по выбору.
                office = Combo([OFFICE_NONE] + self._office_known, width_chars=13)
                office.setEditable(True)
                office.setCurrentText(self._offices.get(e["employee"]) or OFFICE_NONE)
                office.on_select(lambda emp=e["employee"], c=office: self.set_office(emp, c))
                office.lineEdit().editingFinished.connect(
                    lambda emp=e["employee"], c=office: self.set_office(emp, c))
                grid.addWidget(office, r, 2, Qt.AlignmentFlag.AlignTop)
                # Норма выездов в день. Офис делит людей на группы по фактической
                # выработке (8/10/12), и от неё считается план в сводке.
                norm = Combo([PLAN_COMMON] + [str(n) for n in self._plan_groups],
                             width_chars=9)
                norm.setEditable(True)
                norm.setCurrentText(str(e["plan"]) if e.get("plan_personal")
                                    else PLAN_COMMON)
                norm.on_select(lambda emp=e["employee"], c=norm: self.set_plan(emp, c))
                norm.lineEdit().editingFinished.connect(
                    lambda emp=e["employee"], c=norm: self.set_plan(emp, c))
                grid.addWidget(norm, r, 3, Qt.AlignmentFlag.AlignTop)
                combo = Combo([GUEST_PLACEHOLDER] + glabels, width_chars=40)
                tid = Field(width_chars=16)
                if e.get("telegram_id"):
                    tid.set(str(e["telegram_id"]))
                combo.on_select(lambda c=combo, t=tid: self._pick_guest(c, t))
                grid.addLayout(vcol(combo, tid, spacing=2), r, 4)
                kind = Combo(KIND_LABELS, width_chars=11)
                kind.set_current(kind_index(e))
                grid.addWidget(kind, r, 5, Qt.AlignmentFlag.AlignTop)
                status_text, bound = role_status(e)
                grid.addWidget(Note(status_text, "success" if bound else "small", wrap=False),
                               r, 8, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(Button("Привязать",
                                      lambda emp=e["employee"], t=tid, k=kind:
                                      self.bind_role(emp, t, k), kind="accent"),
                               r, 6, Qt.AlignmentFlag.AlignTop)
                # Ссылка входа без Telegram: сервер сам выдаст сотруднику
                # служебную личность, если привязки ещё нет.
                grid.addWidget(Button("Ссылка входа",
                                      lambda emp=e["employee"], k=kind:
                                      self.issue_link(employee=emp, kind_combo=k)),
                               r, 7, Qt.AlignmentFlag.AlignTop)
                self._role_rows.append((combo, tid, kind))
            grid.setColumnStretch(9, 1)
            frame.add(holder)
        self.bg_task(work, done)

    def set_office(self, employee: str, combo) -> None:
        """Записать офис сотрудника. Сохраняем сразу по выбору: отдельная кнопка
        на каждую из тридцати строк — это тридцать поводов забыть нажать."""
        city = (combo.get() or "").strip()
        if city == OFFICE_NONE:
            city = ""
        if city == (self._offices.get(employee) or ""):
            return                      # ничего не изменилось — молчим

        def done(d):
            self._offices = dict((d or {}).get("cities") or {})
            self._office_known = list((d or {}).get("known") or [])
            self.toast(f"{employee}: офис {city}" if city else f"{employee}: офис снят")

        self.bg_task(lambda: self.api("POST", "/admin/employee_cities.json",
                                      {"cities": {employee: city}}), done)

    def set_plan(self, employee: str, combo) -> None:
        """Записать норму выездов. Сохраняем сразу по выбору — как и офис."""
        raw = (combo.get() or "").strip()
        norm = 0 if raw in ("", PLAN_COMMON) else raw
        try:
            norm = int(norm)
        except (TypeError, ValueError):
            self.toast(f"{employee}: норма — это число (или «{PLAN_COMMON}»)")
            return

        def done(_d):
            self.toast(f"{employee}: норма {norm} в день" if norm
                       else f"{employee}: норма общая")

        self.bg_task(lambda: self.api("POST", "/admin/visit_plan.json",
                                      {"plan": {employee: norm}}), done)

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
