"""Раздел «Руководители»: автосводка, диагностика доставки, координаты фото,
беседы отчётов, список руководителей и форма."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ..core import managers as mg
from ..core.devices import valid_telegram_id
from ..core.people import GUEST_PLACEHOLDER, guest_label
from ..core.text import names_look_different
from . import dialogs
from .widgets import (Button, Card, Check, ChipFlow, Combo, Field, KVGrid, Note, Page,
                      Rows, label)


class ChatEmployeesDialog(QDialog):
    """«Чьи визиты постить в эту беседу» — галочки в две колонки."""

    def __init__(self, host, chat: dict, employees: list[str]) -> None:
        super().__init__(host)
        self.host = host
        self.chat = chat
        self.setWindowTitle(chat.get("title") or str(chat.get("chat_id")))
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        lay.addWidget(label("Чьи визиты постить в эту беседу:", bold=True))
        if not employees:
            lay.addWidget(Note("Реестр пуст — сотрудников нет", "small"))
        sel = set(chat.get("employees") or [])
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        rows_per_col = max(1, (len(employees) + 1) // 2)
        self.checks: dict[str, Check] = {}
        for i, e in enumerate(employees):
            c = Check(e, e in sel)
            self.checks[e] = c
            grid.addWidget(c, i % rows_per_col, i // rows_per_col, Qt.AlignmentFlag.AlignLeft)
        lay.addLayout(grid)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(Button("Отмена", self.reject))
        buttons.addWidget(Button("Сохранить", self.save, kind="accent"))
        lay.addLayout(buttons)

    def save(self) -> None:
        chosen = [e for e, c in self.checks.items() if c.get()]

        def done(d):
            self.host.toast("Сотрудники беседы сохранены")
            self.accept()
            self.host._render_chats(d.get("chats") or [])
        self.host.bg_task(lambda: self.host.api(
            "POST", "/admin/chats.set",
            {"chat_id": self.chat["chat_id"], "employees": chosen}), done)


class ManagersSection:
    def build_mgrs(self) -> Page:
        page = Page()

        dc = Card("Автосводка руководителям",
                  "В конце дня (по Москве) каждый руководитель получает дайджест по своим "
                  "сотрудникам. Реалтайм — кнопка «Дайджест за сегодня» в боте.")
        self.dig_time = Field("16:30", width_chars=7)
        self.dig_enabled = Check("автопуш включён", True)
        dc.add_row(label("Время (МСК)", "small"), self.dig_time, 4, self.dig_enabled, 4,
                   Button("Сохранить", self.save_digest, kind="accent"))
        self.prom_time = Field("09:30", width_chars=7)
        self.prom_enabled = Check("утренний пуш «обещание просрочено»", True)
        dc.add_row(label("Обещания (МСК)", "small"), self.prom_time, 4, self.prom_enabled)
        self.visit_push_check = Check("отбивки по закрытым визитам (в личку руководителям)", True)
        self.visit_push_var = self.visit_push_check
        dc.add_row(self.visit_push_check)
        # Личные рубильники владельца. Общий «автопуш включён» гасит рассылку
        # ВСЕМ руководителям сразу — отписаться самому было нечем.
        self.owner_digest_check = Check("дайджест", True)
        self.owner_digest_var = self.owner_digest_check
        self.owner_promise_check = Check("обещания", False)
        self.owner_promise_var = self.owner_promise_check
        dc.add_row(label("Лично мне:", "small"), self.owner_digest_check,
                   self.owner_promise_check)
        self.dig_out = dc.add(Note("", "small"))
        page.add(dc)

        dl = Card("Диагностика доставки",
                  "Кому за сегодня ушли пуши, кому нет и почему. «Заблокирован» = "
                  "руководитель не нажал /start в личке боту — это чинится только "
                  "им самим.")
        dl.add_row(Button("Обновить сводку доставки", self.load_delivery, kind="accent"))
        self.delivery_kvs = dl.add(KVGrid(), spacing_before=4)
        self.delivery_out = dl.add(Note("", "small"))
        page.add(dl)

        gc = Card("Координаты фото",
                  "Точка съёмки — доказательство выезда и одновременно данные о "
                  "самом сотруднике: у снимка из галереи это чаще всего его "
                  "домашний адрес. Стереть можно точечно — по сотруднику, "
                  "договору или дате загрузки; сами фото, их даты и хэши "
                  "остаются. Автоуборки по умолчанию НЕТ: срок в днях включает "
                  "её, если однажды потребуется регламент (0 = не убирать).")
        self.geo_days = Field(width_chars=6)
        gc.add_row(label("Хранить (дней)", "small"), self.geo_days, 4,
                   Button("Сохранить срок", self.save_geo_retention, kind="accent"),
                   Button("Обновить", self.load_geo_retention))
        self.geo_emp = Field(width_chars=22)
        self.geo_cid = Field(width_chars=14)
        self.geo_before = Field(width_chars=12, placeholder="ДД.ММ.ГГГГ")
        gc.add_row(label("Стереть сейчас:", "small"), 2, label("сотрудник", "small"),
                   self.geo_emp, 2, label("КД", "small"), self.geo_cid, 2,
                   label("до даты", "small"), self.geo_before, 4,
                   Button("Стереть координаты", self.forget_geo), spacing_before=4)
        self.geo_out = gc.add(Note("", "small"))
        page.add(gc)

        rc = Card("Пересылка отчётов в беседы",
                  "Закрытый визит уходит постом в закреплённые беседы — как сотрудники "
                  "пишут руками, только автоматически: клиент, адрес, статусы, "
                  "комментарий, фото и фактические гео-точки (закрытие визита и съёмка "
                  "фото). Добавь бота в беседу — она появится в списке; включи её и "
                  "закрепи сотрудников (ФИО из реестра = их привязанные телеграмы).")
        rc.add_row(Button("Обновить список бесед", self.chats_refresh))
        self.chats_list = rc.add(Rows(spacing=4), spacing_before=4)
        self.chats_list.set_empty("Список загрузится при открытии вкладки…")
        self._chats_emp: list[str] = []
        page.add(rc)

        mc = Card("Руководители",
                  "Каждый привязан к городам (подпись сводки) и своим сотрудникам "
                  "(по ним считается дайджест).")
        self.mgr_list = mc.add(Rows(spacing=4))
        self.mgr_list.set_empty("Пока никого — добавьте ниже")
        page.add(mc)

        fc = Card("Добавить / изменить руководителя")
        self.mgr_form_card = fc
        fc.add(label("Кто это (из писавших боту)", "small"))
        self.mgr_guest_combo = Combo(["— никто ещё не писал боту —"], width_chars=58)
        self.mgr_guest_combo.on_select(self._mgr_pick_guest)
        fc.add(self.mgr_guest_combo)
        self.mgr_tid = Field(width_chars=15)
        self.mgr_name = Field(width_chars=26)
        fc.add_row(label("Telegram ID", "small"), self.mgr_tid, 8,
                   label("Имя (для приветствия)", "small"), self.mgr_name, spacing_before=4)
        fc.add(label("ГОРОДА (ПОДПИСЬ СВОДКИ)", "caps"), spacing_before=8)
        self.mgr_city_chips = fc.add(ChipFlow())
        self.mgr_city_add = Field(width_chars=24)
        self.mgr_city_add.returnPressed.connect(self._mgr_add_city)
        fc.add_row(self.mgr_city_add, Button("＋ добавить город", self._mgr_add_city))
        self.mgr_emp_hint = Note("", "tiny", wrap=False)
        fc.add_row(label("СОТРУДНИКИ (ПО НИМ СЧИТАЕТСЯ ДАЙДЖЕСТ)", "caps"), None,
                   Button("выбрать всех", lambda: self._mgr_emps_all(True)),
                   Button("снять", lambda: self._mgr_emps_all(False)), self.mgr_emp_hint,
                   spacing_before=8, stretch=False)
        self.mgr_emp_chips = fc.add(ChipFlow())
        fc.add_row(Button("Сохранить руководителя", self.save_manager, kind="accent"),
                   Button("Очистить форму", self._mgr_form_reset), spacing_before=8)
        page.add(fc)
        return page

    # --- беседы отчётов о выездах ---
    def chats_refresh(self):
        def done(d):
            self._chats_emp = d.get("employees") or []
            self._render_chats(d.get("chats") or [])
        self.bg_task(lambda: self.api("GET", "/admin/chats"), done)

    def _render_chats(self, chats):
        self.chats_list.clear()
        if not chats:
            self.chats_list.set_empty(
                "Бесед пока нет — добавь бота в группу, и она появится здесь")
            return
        dups = mg.chat_duplicates(chats)
        for c in chats:
            dup = (c.get("title") or "").strip().lower() in dups
            check = Check(mg.chat_label(c, dup), c.get("enabled", False),
                          role="warn" if dup else None)
            check.toggled.connect(lambda on, cid=c["chat_id"]: self._chat_toggle(cid, on))
            emps = c.get("employees") or []
            self.chats_list.add_row(
                check, Note(mg.chat_employees_text(emps), "tiny", wrap=False), None,
                Button(f"Сотрудники ({len(emps)})", lambda c=c: self._chat_emps_dialog(c)),
                stretch=False)

    def _chat_toggle(self, chat_id, on):
        # При сбое чекбокс НЕ должен показывать выключенное при включённом
        # сервере (посты с ПД продолжались бы «невидимо») — перечитываем правду.
        self.bg_task(lambda: self.api("POST", "/admin/chats.set",
                                      {"chat_id": chat_id, "enabled": on}),
                     lambda d: self.toast("Беседа включена" if on else "Беседа выключена"),
                     lambda e: (self.toast(f"Не сохранилось: {e}", err=True),
                                self.chats_refresh()))

    def _chat_emps_dialog(self, chat):
        ChatEmployeesDialog(self, chat, self._chats_emp or []).show()

    # --- загрузка ---
    def load_managers(self):
        def work():
            try:
                guests = self.api("GET", "/admin/guests").get("guests") or []
            except RuntimeError:
                guests = []
            d = self.api("GET", "/admin/managers")
            return guests, d

        def done(res):
            self._loaded.add("mgrs")
            self.guests, d = res
            self.mgrs = d.get("managers") or []
            self.mgr_cities = d.get("cities") or []
            self.mgr_emps = d.get("employees") or []
            self.chats_refresh()   # карточка бесед не должна висеть пустой
            self.mgr_city_emps = d.get("city_employees") or {}
            dig = d.get("digest") or {}
            if dig:
                self.dig_time.set(dig.get("time") or "16:30")
                self.dig_enabled.set(bool(dig.get("enabled")))
                self.prom_time.set(dig.get("promise_time") or "09:30")
                self.prom_enabled.set(bool(dig.get("promise_enabled", True)))
                # БЕЗ дефолта True: на старом сервере галка врала бы «включено»
                self._show_owner_switches(dig)
                self._show_visit_push(dig)
                self._show_digest_state(dig)
            self.mgr_guest_combo.set_values(
                [GUEST_PLACEHOLDER] + [guest_label(g) for g in self.guests])
            self.mgr_guest_combo.set_current(0)
            self._render_mgr_table()
            self._render_mgr_chips()
        self.bg_task(work, done)

    def _render_mgr_table(self):
        self.mgr_list.clear()
        if not self.mgrs:
            self.mgr_list.set_empty("Пока никого — добавьте ниже")
            return
        for m in self.mgrs:
            row = mg.manager_row(m)
            name = label(row["name"], bold=True)
            name.setFixedWidth(180)
            cities = Note(row["cities"], "small", wrap=False)
            cities.setFixedWidth(240)
            emps = Note(row["employees"], "small", wrap=False)
            emps.setFixedWidth(130)
            tid = Note(row["tid"], "small", wrap=False)
            items = [name, cities, emps, tid]
            if row["mismatch"]:
                items.append(Note("⚠ ФИО ≠ реестру", "danger", wrap=False))
            items += [None,
                      Button("проверить доставку",
                             lambda t=m["telegram_id"]: self.push_test(t)),
                      Button("изменить", lambda t=m["telegram_id"]: self.mgr_edit(t)),
                      Button("удалить", lambda t=m["telegram_id"]: self.mgr_delete(t),
                             kind="danger")]
            self.mgr_list.add_row(*items, stretch=False)

    def _render_mgr_chips(self):
        form = self.mgr_form
        reg = [c["name"] for c in self.mgr_cities]
        cities = reg + [c for c in sorted(form["cities"]) if c not in reg]
        self.mgr_city_chips.set_chips(
            [(mg.city_chip_text(c, self.mgr_cities), c in form["cities"],
              lambda c=c: self._mgr_toggle_city(c)) for c in cities]
            or ["городов в реестре нет — добавьте вручную"])
        reg_e = [e["employee"] for e in self.mgr_emps]
        emps = reg_e + [e for e in sorted(form["employees"]) if e not in reg_e]
        self.mgr_emp_chips.set_chips(
            [(mg.emp_chip_text(e, self.mgr_emps), e in form["employees"],
              lambda e=e: self._mgr_toggle_emp(e)) for e in emps]
            or ["сотрудников в реестре нет — импортируйте реестр"])
        self.mgr_emp_hint.setText(
            f"выбрано: {len(form['employees'])}" if form["employees"] else "")

    def _mgr_pick_guest(self, *_args):
        """Из выбранного аккаунта берём и id, И имя.

        Раньше подставлялся только id, а имя оператор печатал руками — и никакая
        опечатка в выборе не была видна: запись «Михаил» со всеми городами уехала
        на посторонний аккаунт и четыре дня получала полный дайджест."""
        i = self.mgr_guest_combo.current()
        if i <= 0:
            return
        g = self.guests[i - 1]
        self.mgr_tid.set(str(g["telegram_id"]))
        if not self.mgr_name.get().strip():
            self.mgr_name.set((g.get("name") or g.get("username") or "").strip())

    def _mgr_toggle_city(self, c):
        form = self.mgr_form
        if c in form["cities"]:
            form["cities"].discard(c)
        else:
            form["cities"].add(c)
            for e in self.mgr_city_emps.get(c) or []:
                form["employees"].add(e)
        self._render_mgr_chips()

    def _mgr_add_city(self):
        v = self.mgr_city_add.get().strip()
        if not v:
            return
        self.mgr_city_add.set("")
        self.mgr_form["cities"].add(v)
        for e in self.mgr_city_emps.get(v) or []:
            self.mgr_form["employees"].add(e)
        self._render_mgr_chips()

    def _mgr_toggle_emp(self, e):
        form = self.mgr_form
        form["employees"].discard(e) if e in form["employees"] else form["employees"].add(e)
        self._render_mgr_chips()

    def _mgr_emps_all(self, on):
        self.mgr_form["employees"] = ({e["employee"] for e in self.mgr_emps}
                                      if on else set())
        self._render_mgr_chips()

    def mgr_edit(self, tid):
        m = next((x for x in self.mgrs if str(x["telegram_id"]) == str(tid)), None)
        if not m:
            return
        self.mgr_form = {"tid": str(tid), "cities": set(m.get("cities") or []),
                         "employees": set(m.get("employees") or [])}
        self.mgr_tid.set(str(tid))
        self.mgr_name.set(m.get("name") or "")
        self._render_mgr_chips()

    def _mgr_form_reset(self):
        self.mgr_form = {"tid": "", "cities": set(), "employees": set()}
        self.mgr_tid.set("")
        self.mgr_name.set("")
        self.mgr_city_add.set("")
        self.mgr_guest_combo.set_current(0)
        self._render_mgr_chips()

    def save_manager(self):
        tid = self.mgr_tid.get().strip()
        if not valid_telegram_id(tid):
            self.toast("Впиши Telegram ID руководителя (или выбери из списка)", err=True)
            return
        if not self.mgr_form["employees"]:
            self.toast("Отметь хотя бы одного сотрудника", err=True)
            return
        # Права получает ВЛАДЕЛЕЦ telegram_id, а не тот, чьё имя вписали в форму.
        # Если такой id боту ни разу не писал, сверить некому — спрашиваем явно.
        known = next((g for g in self.guests if str(g["telegram_id"]) == tid), None)
        if not known and not self.ask_yes_no("Незнакомый аккаунт", mg.unknown_account_text(tid)):
            return
        body = mg.manager_body(tid, self.mgr_name.get(), self.mgr_form["cities"],
                               self.mgr_form["employees"])

        def done(d):
            # Показываем НЕ то, что напечатал оператор, а кого сервер увидел за этим id.
            acc = (d or {}).get("account") or {}
            real = (acc.get("name") or acc.get("username") or "").strip()
            typed = body["name"].strip()
            if names_look_different(typed, real):
                dialogs.showwarning("Имя не совпало", mg.name_mismatch_text(typed, real, tid),
                                    parent=self)
            self.toast("Руководитель сохранён: " + (real or typed or tid))
            self._mgr_form_reset()
            self.load_managers()
        self.bg_task(lambda: self.api("POST", "/admin/manager", body), done)

    def mgr_delete(self, tid):
        if not self.ask_yes_no("Удаление", mg.delete_text(tid)):
            return
        self.bg_task(lambda: self.api("POST", "/admin/manager_delete", {"telegram_id": tid}),
                     lambda _d: (self.toast("Удалён"), self.load_managers()))

    # --- дайджест ---
    def save_digest(self):
        t = self.dig_time.get().strip()
        pt = self.prom_time.get().strip()
        for v in (t, pt):
            if not mg.valid_time(v):
                self.toast("Время в формате ЧЧ:ММ", err=True)
                return
        body = mg.digest_body(
            time=t, enabled=self.dig_enabled.get(), promise_time=pt,
            promise_enabled=self.prom_enabled.get(), visit_push=self.visit_push_var.get(),
            owner_known=self.owner_digest_check.isEnabled(),
            owner_digest=self.owner_digest_var.get(),
            owner_promise=self.owner_promise_var.get())

        def done(d):
            self._show_digest_state(d)
            self.toast("Настройки сохранены")
        self.bg_task(lambda: self.api("POST", "/admin/digest_settings", body), done)

    def _show_owner_switches(self, d: dict):
        """Личные рубильники видны только на сервере, который их знает: на
        старом чекбоксы гаснут, а не показывают выдуманное состояние."""
        known = "owner_digest" in d
        for var, check, key in (
                (self.owner_digest_var, self.owner_digest_check, "owner_digest"),
                (self.owner_promise_var, self.owner_promise_check, "owner_promise")):
            if known:
                var.set(bool(d.get(key)))
            check.configure(state="normal" if known else "disabled")

    def _show_visit_push(self, d: dict):
        if "visit_push" in d:
            self.visit_push_var.set(bool(d.get("visit_push")))
            self.visit_push_check.configure(state="normal")
        else:
            self.visit_push_check.configure(state="disabled")

    def _show_digest_state(self, d: dict):
        self.dig_out.setText(mg.digest_state_text(d))

    # --- координаты фото ---
    def _show_geo_state(self, d: dict):
        self.geo_out.setText(mg.geo_state_text(d))
        days = d.get("retention_days")
        if days is not None and not self.geo_days.get().strip():
            self.geo_days.set(str(days))

    def load_geo_retention(self):
        self.bg_task(lambda: self.api("GET", "/admin/photo_geo"), self._show_geo_state)

    def save_geo_retention(self):
        raw = self.geo_days.get().strip()
        if not raw.isdigit():
            self.toast("Срок — целое число дней (0 = не убирать)", err=True)
            return

        def done(d):
            self._show_geo_state(d)
            self.toast("Срок хранения сохранён")
        self.bg_task(lambda: self.api("POST", "/admin/photo_geo",
                                      {"retention_days": int(raw)}), done)

    def forget_geo(self):
        emp = self.geo_emp.get().strip()
        cid = self.geo_cid.get().strip()
        before = self.geo_before.get().strip()
        # Спрашиваем всегда: стирание необратимо, а «все снимки» без фильтров —
        # это вся доказательная база выездов разом.
        if not self.ask_yes_no("Стереть координаты", mg.geo_confirm_text(emp, cid, before)):
            return

        def done(d):
            self._show_geo_state(d)
            self.toast(f"Стёрто координат: {d.get('cleared', 0)}")
        self.bg_task(lambda: self.api("POST", "/admin/photo_geo",
                                      {"forget": {"employee": emp, "contract": cid,
                                                  "before": before}}), done)

    # --- доставка ---
    def load_delivery(self):
        def done(d):
            self.delivery_kvs.set_pairs(mg.delivery_pairs(d))
            self.delivery_out.setText("\n".join(mg.delivery_lines(d)))
            # рубильник и подпись — из того же ответа
            self._show_owner_switches(d)
            self._show_visit_push(d)
            self._show_digest_state(d)
        self.bg_task(lambda: self.api("GET", "/admin/digest_settings"), done)

    def push_test(self, tid: int):
        """Реальная тестовая отправка безличного текста руководителю."""
        def done(d):
            if d.get("ok"):
                self.toast("Дошло")
            else:
                dialogs.showwarning("Доставка", mg.push_result_text(d), parent=self)

        def work():
            try:
                return self.api("POST", "/admin/push_test",
                                {"kind": "visit", "telegram_id": tid})
            except RuntimeError as exc:
                return {"ok": False, "status": "old_server",
                        "error": f"Старый сервер: обновите бота ({exc})"}
        self.bg_task(work, done)
