"""Раздел «Руководители»: список с личными рубильниками и форма.

Рассылки, диагностика доставки и беседы — в разделе «Каналы»; координаты
фото — в «Подключении»."""

from __future__ import annotations

from ..core import constants
from ..core import links as lk
from ..core import managers as mg
from ..core.devices import valid_telegram_id
from ..core.people import GUEST_PLACEHOLDER, guest_label
from ..core.text import names_look_different
from . import dialogs
from .widgets import (Button, Card, Check, ChipFlow, Combo, Field, Note, Page, Rows,
                      label)

MGR_FORM_TITLE = "Добавить / изменить руководителя"


class ManagersSection:
    def build_mgrs(self) -> Page:
        page = Page()
        # До первой загрузки карта офисов пуста, но чипы уже рисуются (сброс формы).
        self.mgr_office_staff: dict[str, list[str]] = {}

        mc = Card("Руководители",
                  "Руководитель направления видит всех сотрудников своей республики "
                  "(СНГ — всех), состав считается живьём. " + (
                      "Галочки в строке — что ему слать: карточку по каждому визиту, "
                      "сводку в конце дня, утренние обещания. Гендиру обычно хватает "
                      "одной сводки." if constants.TELEGRAM_UI else
                      "Вход — по ссылке: «выдать вход» в строке или всем сразу "
                      "кнопкой ниже. Дальше входы своей команде руководитель выдаёт "
                      "сам, из своего пульта."))
        # Старт пробы: вход всем руководителям одной кнопкой, итог — списком
        # «ФИО — ссылка» (копировать целиком или в Excel). У каждой строки есть
        # и своя кнопка «выдать вход» — для одного человека.
        mc.add_row(Button("Выдать вход всем руководителям", self.mgr_bulk_issue,
                          kind="accent"),
                   Note("руководителям без команды вход не выдаётся", "tiny", wrap=False),
                   spacing_before=2, stretch=False)
        self.mgr_list = mc.add(Rows(spacing=4))
        self.mgr_list.set_empty("Пока никого — добавьте ниже")
        page.add(mc)

        fc = Card(MGR_FORM_TITLE)
        self.mgr_form_card = fc
        guest_lbl = fc.add(label("Кто это (из писавших боту)", "small"))
        self.mgr_guest_combo = Combo(["— никто ещё не писал боту —"], width_chars=58)
        self.mgr_guest_combo.on_select(self._mgr_pick_guest)
        fc.add(self.mgr_guest_combo)
        guest_lbl.setVisible(constants.TELEGRAM_UI)
        self.mgr_guest_combo.setVisible(constants.TELEGRAM_UI)
        self.mgr_tid = Field(width_chars=15)
        self.mgr_name = Field(width_chars=26)
        tg = constants.TELEGRAM_UI
        fc.add_row(label("Telegram ID" if tg else "Номер", "small"), self.mgr_tid, 8,
                   label("Имя (для приветствия)" if tg else "Имя", "small"),
                   self.mgr_name, spacing_before=4)
        if not tg:
            fc.add(Note("Номер новому руководителю вписывать не нужно — пульт выдаст "
                        "свой. У тех, кто уже есть в списке, он стоит.", "tiny"))
        # Направление: состав сотрудников сервер считает живьём по стране
        # реестра. Раньше пульт про направления не знал и требовал отметить
        # сотрудников вручную — замороженный список устаревал с каждым разбросом.
        self.mgr_direction = Combo([lbl for _code, lbl in mg.DIRECTION_OPTIONS],
                                   width_chars=34)
        self.mgr_direction.on_select(self._mgr_direction_changed)
        fc.add_row(label("Направление", "small"), self.mgr_direction,
                   Note(mg.DIRECTION_HINT, "tiny"), spacing_before=6, stretch=False)
        # Офисы — НАСТОЯЩИЙ охват (живой состав), в отличие от городов ниже,
        # которые остаются подписью сводки. Разводим их визуально и словами:
        # иначе оператор отмечает город и ждёт, что состав подтянется сам.
        self.mgr_office_hint = Note("", "tiny", wrap=True)
        fc.add_row(label("ОФИСЫ — ЧЕЙ ШТАТ ОН ВЕДЁТ", "caps"), None,
                   Note(mg.OFFICE_HINT, "tiny"), spacing_before=8, stretch=False)
        self.mgr_office_chips = fc.add(ChipFlow())
        fc.add(self.mgr_office_hint)
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

    # --- загрузка ---
    def load_managers(self):
        def work():
            try:
                guests = self.api("GET", "/admin/guests").get("guests") or []
            except RuntimeError:
                guests = []
            d = self.api("GET", "/admin/managers")
            try:
                offices = self.api("GET", "/admin/employee_cities.json")
            except RuntimeError:
                offices = {}      # старый сервер про офисы не знает
            return guests, d, offices

        def done(res):
            self._loaded.add("mgrs")
            self.guests, d, offices = res
            staff: dict[str, list[str]] = {}
            for emp, city in (offices.get("cities") or {}).items():
                staff.setdefault(city, []).append(emp)
            self.mgr_office_staff = {c: sorted(v) for c, v in staff.items()}
            self.mgrs = d.get("managers") or []
            self.mgr_cities = d.get("cities") or []
            self.mgr_emps = d.get("employees") or []
            self.mgr_city_emps = d.get("city_employees") or {}
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
            cities.setFixedWidth(200)
            emps = Note(row["employees"], "small", wrap=False)
            emps.setFixedWidth(150)
            tid = Note(row["tid"], "small", wrap=False)
            items = [name, cities, emps, tid]
            if row["mismatch"]:
                items.append(Note("⚠ ФИО ≠ реестру", "danger", wrap=False))
            if constants.TELEGRAM_UI and m.get("can_dm") is None:
                items.append(Note("⚠ не писал боту — ничего не дойдёт", "danger", wrap=False))
            # Тумблеры прямо в строке: «гендиру не нужны 225 карточек в день»
            # решалось тем, что он уносил чат в архив. Сохраняем сразу — форму
            # ради одной галочки открывать незачем.
            for key, title, hint in (mg.MANAGER_PUSH_SWITCHES
                                     if constants.TELEGRAM_UI else ()):
                sw = Check(title, m.get(key, True))
                sw.setToolTip(hint)
                sw.toggled.connect(
                    lambda on, t=m["telegram_id"], k=key: self._mgr_push(t, k, on))
                items.append(sw)
            items += [None,
                      # Вход руководителя — ссылкой, как у всех: дальше он сам
                      # раздаёт входы своей команде из пульта руководителя.
                      Button("выдать вход",
                             lambda t=m["telegram_id"]: self.issue_link(telegram_id=t)),
                      *([Button("проверить доставку",
                                lambda t=m["telegram_id"]: self.push_test(t))]
                        if constants.TELEGRAM_UI else []),
                      Button("изменить", lambda t=m["telegram_id"]: self.mgr_edit(t)),
                      Button("удалить", lambda t=m["telegram_id"]: self.mgr_delete(t),
                             kind="danger")]
            self.mgr_list.add_row(*items, stretch=False)

    def _mgr_push(self, tid, key, on):
        """Один рубильник одного руководителя. При сбое перечитываем список:
        галочка не имеет права показывать состояние, которого нет на сервере."""
        m = next((x for x in self.mgrs if str(x["telegram_id"]) == str(tid)), None)
        if m is None:
            return
        m[key] = bool(on)
        body = mg.manager_body(str(tid), m.get("name") or "",
                               set(m.get("cities") or []),
                               set(m.get("employees") or []),
                               m.get("direction") or "", mg.manager_pushes(m))
        title = next(t for k, t, _h in mg.MANAGER_PUSH_SWITCHES if k == key)
        self.bg_task(
            lambda: self.api("POST", "/admin/manager", body),
            lambda _d: self.toast(f"{m.get('name') or tid}: {title} "
                                  f"{'включены' if on else 'выключены'}"),
            lambda e: (self.toast(f"Не сохранилось: {e}", err=True),
                       self.load_managers()))

    def _render_mgr_chips(self):
        form = self.mgr_form
        reg = [c["name"] for c in self.mgr_cities]
        cities = reg + [c for c in sorted(form["cities"]) if c not in reg]
        self.mgr_city_chips.set_chips(
            [(mg.city_chip_text(c, self.mgr_cities), c in form["cities"],
              lambda c=c: self._mgr_toggle_city(c)) for c in cities]
            or ["городов в реестре нет — добавьте вручную"])
        offices = sorted(set(self.mgr_office_staff) | form["offices"])
        self.mgr_office_chips.set_chips(
            [(mg.office_chip_text(c, self.mgr_office_staff), c in form["offices"],
              lambda c=c: self._mgr_toggle_office(c)) for c in offices]
            or ["офисы ещё не проставлены — вкладка «Сотрудники», колонка «Офис»"])
        self.mgr_office_hint.setText(
            mg.office_note(form["offices"], self.mgr_office_staff))
        reg_e = [e["employee"] for e in self.mgr_emps]
        emps = reg_e + [e for e in sorted(form["employees"]) if e not in reg_e]
        self.mgr_emp_chips.set_chips(
            [(mg.emp_chip_text(e, self.mgr_emps), e in form["employees"],
              lambda e=e: self._mgr_toggle_emp(e)) for e in emps]
            or ["сотрудников в реестре нет — импортируйте реестр"])
        # У руководителя направления состав считает сервер: галочки гаснут, а
        # подсказка про это НЕ затирается счётчиком «выбрано».
        code = self._mgr_direction()
        self.mgr_emp_chips.setEnabled(not code)
        if code:
            self.mgr_emp_hint.setText(mg.direction_employees_note(code))
        else:
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

    def _mgr_toggle_office(self, c):
        form = self.mgr_form
        form["offices"].discard(c) if c in form["offices"] else form["offices"].add(c)
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
                         "employees": set(m.get("employees") or []),
                         "offices": set(m.get("office_cities") or [])}
        self.mgr_tid.set(str(tid))
        self.mgr_name.set(m.get("name") or "")
        self.mgr_direction.set_current(mg.direction_index(m.get("direction") or ""))
        self._render_mgr_chips()
        # Форма — в самом низу длинной страницы. Без прокрутки клик по
        # «изменить» выглядит так, будто ничего не произошло.
        who = m.get("name") or str(tid)
        self.mgr_form_card.title.setText(f"Изменить руководителя · {who}")
        self.pages["mgrs"].scroll_to(self.mgr_form_card)
        self.toast(f"Форма заполнена: {who} — правь и нажми «Сохранить руководителя»")

    def _mgr_form_reset(self):
        self.mgr_form = {"tid": "", "cities": set(), "employees": set(),
                         "offices": set()}
        self.mgr_tid.set("")
        self.mgr_name.set("")
        self.mgr_city_add.set("")
        self.mgr_guest_combo.set_current(0)
        self.mgr_direction.set_current(0)
        self.mgr_form_card.title.setText(MGR_FORM_TITLE)
        self._render_mgr_chips()

    def _mgr_direction(self) -> str:
        return mg.direction_code(self.mgr_direction.current())

    def _mgr_direction_changed(self, *_args):
        self._render_mgr_chips()

    def save_manager(self):
        tid = self.mgr_tid.get().strip()
        if not tid and not constants.TELEGRAM_UI:
            tid = mg.next_local_manager_id(getattr(self, "mgrs", None) or [])
        if not valid_telegram_id(tid):
            self.toast("Впиши Telegram ID руководителя (или выбери из списка)"
                       if constants.TELEGRAM_UI else "Номер руководителя — только цифры",
                       err=True)
            return
        direction = self._mgr_direction()
        if (not direction and not self.mgr_form["employees"]
                and not self.mgr_form["offices"]):
            self.toast("Выбери направление, офис или отметь хотя бы одного сотрудника",
                       err=True)
            return
        # Права получает ВЛАДЕЛЕЦ telegram_id, а не тот, чьё имя вписали в форму.
        # Если такой id боту ни разу не писал, сверить некому — спрашиваем явно.
        known = next((g for g in self.guests if str(g["telegram_id"]) == tid), None)
        if (constants.TELEGRAM_UI and not known
                and not self.ask_yes_no("Незнакомый аккаунт", mg.unknown_account_text(tid))):
            return
        body = mg.manager_body(tid, self.mgr_name.get(), self.mgr_form["cities"],
                               self.mgr_form["employees"], direction,
                               office_cities=self.mgr_form["offices"])

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

    def mgr_bulk_issue(self):
        """Вход всем руководителям с командой — одним запросом, итог списком."""
        mgrs = getattr(self, "mgrs", None) or []
        if not mgrs:
            self.toast("Список руководителей пуст — нажмите «Обновить»", err=True)
            return
        items, skipped = lk.manager_items(mgrs)
        if not items:
            self.toast("Ни у одного руководителя нет команды — выдавать некому", err=True)
            return
        if not self.ask_yes_no("Вход руководителям",
                               lk.manager_bulk_text(len(items), skipped)):
            return
        self.issue_bulk(items, lk.DEFAULT_USES, "Ссылки входа · руководители",
                        manager_ids=[i["telegram_id"] for i in items])

    def mgr_delete(self, tid):
        if not self.ask_yes_no("Удаление", mg.delete_text(tid)):
            return
        self.bg_task(lambda: self.api("POST", "/admin/manager_delete", {"telegram_id": tid}),
                     lambda _d: (self.toast("Удалён"), self.load_managers()))

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
