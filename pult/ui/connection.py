"""Раздел «Подключение»: контуры серверов, привязка устройства, токен бота."""

from __future__ import annotations

from ..core import constants
from ..core import devices as dv
from ..core import managers as mg
from ..core.settings import active_profile
from . import dialogs
from .widgets import Button, Card, Combo, Field, Note, Page, Rows, label


class ConnectionSection:
    def build_conn(self) -> Page:
        page = Page()
        pc = Card("Контуры",
                  "Каждый сервер — свой контур: свой адрес и свой токен "
                  "устройства. Активный контур подписан в шапке каждого раздела "
                  "и в заголовке окна. Ярлык может открывать пульт сразу в "
                  "нужном контуре: «Пульт.pyw --contour Имя».")
        self.profile_combo = pc.add(Combo(width_chars=18))
        pc.add_row(self.profile_combo,
                   Button("Переключиться", self.contour_switch, kind="accent"),
                   Button("Добавить…", self.contour_add),
                   Button("Переименовать…", self.contour_rename),
                   Button("Удалить", self.contour_remove))
        page.add(pc)

        sc = Card("Сервер")
        sc.add(label("Адрес сервера", "small"))
        self.url_entry = sc.add(Field(self.url))
        sc.add(label("Admin-ключ", "small"), spacing_before=4)
        self.key_entry = Field(self.key, secret=True)
        sc.add_row(self.key_entry, Button("👁", self._toggle_key, kind="icon"), stretch=False)
        sc.add_row(Button("Привязать по коду", self.device_redeem, kind="accent"),
                   Button("Подключиться", self._connect), spacing_before=4)
        sc.add_row(Button("Привязать мастер-ключом", self.device_enroll),
                   Button("Отвязать это устройство", self.device_unbind), spacing_before=4)
        self.key_out = sc.add(Note("", "tiny"), spacing_before=4)
        self._key_state()
        page.add(sc)

        dv_card = Card("Привязанные устройства",
                       "Каждое устройство ходит своим токеном и работает только с "
                       "той машины, где его получили. Потерянный ноутбук отзывается "
                       "по одному — остальные продолжают работать. Обычный путь "
                       "привязки — одноразовый код, который выдают на сервере; "
                       "мастер-ключ нужен только когда привязывать некому.")
        dv_card.add_row(Button("Обновить список", self.devices_load))
        self.dev_frame = dv_card.add(Rows(spacing=4), spacing_before=4)
        page.add(dv_card)

        tc = Card("Токен бота",
                  "Сменить бота (токен от @BotFather). После сохранения — «Перезапустить "
                  "бота»: токен применится, миниаппа развернётся в новом боте. Токен нигде "
                  "не показывается и не выгружается.")
        self.token_entry = Field(secret=True)
        tc.add_row(self.token_entry, Button("👁", self._toggle_token, kind="icon"),
                   stretch=False)
        tc.add_row(Button("Сменить бота", self.change_token, kind="accent"),
                   Button("Перезапустить бота", self.restart_bot),
                   Note("формат: цифры : буквы/цифры (≥30 символов)", "tiny", wrap=False),
                   spacing_before=4)
        page.add(tc)
        tc.setVisible(constants.TELEGRAM_UI)

        gc = Card("Координаты фото",
                  "Точка съёмки — доказательство выезда и одновременно данные о "
                  "самом сотруднике: у снимка из галереи это чаще всего его домашний "
                  "адрес. Стереть можно точечно — по сотруднику, договору или дате "
                  "загрузки; сами фото, их даты и хэши остаются. Автоуборки по "
                  "умолчанию НЕТ: срок в днях включает её (0 = не убирать).")
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
        self.apply_profile()
        return page

    # --- контуры --------------------------------------------------------------
    def apply_profile(self) -> None:
        """Синхронизировать раздел с активным контуром — после переключения."""
        if not hasattr(self, "profile_combo"):
            return
        self.profile_combo.set_values(list(self.settings.get("profiles") or {}))
        idx = self.profile_combo.findText(self.profile)
        if idx >= 0:
            self.profile_combo.set_current(idx)
        self.url_entry.set(self.url)
        self.key_entry.set("")
        self._key_state()
        self.dev_frame.clear()
        self.dev_frame.set_empty("Устройства этого контура — «Обновить список»")

    def contour_switch(self) -> None:
        name = self.profile_combo.get()
        if name == self.profile:
            self.toast("Это и есть активный контур")
            return
        if not self.ask_yes_no(
                "Переключить контур",
                f"Переключиться на «{name}»?\n\nНезавершённые выборы (сотрудники, "
                f"книги) сбросятся — они относились к «{self.profile}»."):
            return
        self.switch_profile(name)

    def contour_add(self) -> None:
        name = dialogs.askstring("Новый контур", "Как назвать сервер?\n"
                                 "Например: Компания, Тест.", parent=self)
        if not name or not name.strip():
            return
        url = dialogs.askstring("Адрес сервера", "Адрес (https://…):", parent=self)
        if url is None:
            return
        if not self.profile_add(name.strip(), url.strip()):
            self.toast("Контур с таким именем уже есть", err=True)
            return
        self.apply_profile()
        self.toast(f"Контур «{name.strip()}» добавлен — переключись к нему в списке")

    def contour_rename(self) -> None:
        old = self.profile_combo.get()
        new = dialogs.askstring("Переименовать контур",
                                f"Новое имя для «{old}»:", initialvalue=old,
                                parent=self)
        if not new or not new.strip() or new.strip() == old:
            return
        if not self.profile_rename(old, new.strip()):
            self.toast("Контур с таким именем уже есть", err=True)
            return
        self.apply_profile()

    def contour_remove(self) -> None:
        name = self.profile_combo.get()
        if name == self.profile:
            self.toast("Сначала переключись на другой контур — активный не удаляется",
                       err=True)
            return
        if not self.ask_yes_no("Удалить контур",
                               f"Удалить контур «{name}»?\n\nТокен устройства "
                               "останется жить на сервере — при возврате контур "
                               "придётся привязать заново."):
            return
        if not self.profile_remove(name):
            self.toast("Последний контур удалить нельзя", err=True)
            return
        self.apply_profile()

    # --- координаты фото -------------------------------------------------------
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

    # --- привязка устройства -------------------------------------------------
    device_fingerprint = staticmethod(dv.device_fingerprint)
    device_default_name = staticmethod(dv.device_default_name)

    def _key_state(self):
        self.key_out.configure(text=dv.key_state_text(self.device_token))

    def device_redeem(self):
        """Обменять одноразовый код на токен этой машины.

        Обычный путь. Код выдают на сервере командой admin_access.py issue, он
        гаснет при первом обмене, и мастер-ключ для этого не нужен вовсе — в
        этом и смысл: право впустить новое устройство привязано к доступу к
        серверу, а не к знанию строки из .env.
        """
        code = dialogs.askstring(
            "Код привязки",
            "Вставь одноразовый код.\n\n"
            "Его выдают на сервере командой:\n"
            "    docker compose exec -T app python /app/admin_access.py issue --name «имя»\n\n"
            "Код срабатывает один раз и гаснет.",
            parent=self)
        if not code or not code.strip():
            return
        name = dialogs.askstring(
            "Имя устройства", "Как назвать это устройство?\n"
            "Оно будет видно в списке привязанных.",
            initialvalue=self.device_default_name(), parent=self)
        if name is None:
            return
        fingerprint = self.device_fingerprint()
        code = code.strip()

        def work():
            # Ни ключа, ни токена: код сам себе доступ.
            return self.api("POST", "/admin/device/redeem.json",
                            {"code": code, "device_id": fingerprint,
                             "name": name.strip()})

        def done(d):
            token = (d or {}).get("token") or ""
            if not token:
                self.toast("Сервер не выдал токен", err=True)
                return
            self._device_bound(token, "Устройство привязано по коду")

        def fail(exc):
            self.key, self.device_token = saved_key, saved_token
            self.toast(str(exc), err=True)

        # На время обмена ходим «никем»: коду не нужен ни ключ, ни прежний токен,
        # а прежний токен мог бы увести запрос не туда, если он уже отозван.
        saved_key, saved_token = self.key, self.device_token
        self.key, self.device_token = "", ""
        self.bg_task(work, done, fail)

    def _device_bound(self, token: str, message: str) -> None:
        """Общий хвост обеих привязок: сохранить токен и забыть ключ."""
        self.device_token = token
        active_profile(self.settings)["device_token"] = token
        # Ключ НЕ сохраняем — в этом весь смысл: на диске остаётся токен,
        # который отзывается по одному и работает только с этой машины.
        self.settings.pop("key", None)
        self.key = ""
        self.key_entry.set("")
        self._save_settings()
        self._key_state()
        self.toast(message)
        self.devices_load()
        self.refresh()

    def device_enroll(self):
        key = self.key_entry.get().strip()
        if not key:
            self.toast("Введи мастер-ключ — он нужен один раз", err=True)
            return
        name = dialogs.askstring(
            "Имя устройства", "Как назвать это устройство?\n"
            "Оно будет видно в списке привязанных.",
            initialvalue=self.device_default_name(), parent=self)
        if name is None:
            return
        fingerprint = self.device_fingerprint()
        # Привязка идёт МАСТЕР-КЛЮЧОМ явно, а не через self.api: там уже мог
        # лежать токен прошлой привязки, и перепривязка ходила бы им.
        # Прежний токен помним: при неудаче (опечатка в ключе, нет связи) он
        # возвращается, иначе ближайшее «Обновить» или закрытие окна сотрёт
        # рабочую привязку контура с диска.
        prev_token = self.device_token
        self.key, self.device_token = key, ""

        def work():
            return self.api("POST", "/admin/device/enroll.json",
                            {"device_id": fingerprint, "name": name.strip()})

        def done(d):
            token = (d or {}).get("token") or ""
            if not token:
                self.device_token = prev_token
                self.toast("Сервер не выдал токен", err=True)
                return
            self._device_bound(token, "Устройство привязано — ключ больше не хранится")

        def fail(exc):
            self.key = key
            self.device_token = prev_token
            self.toast(str(exc), err=True)

        self.bg_task(work, done, fail)

    def device_unbind(self):
        """Забыть токен на этой машине. На сервере запись остаётся живой.

        Отзыв на сервере — отдельное действие (мастер-ключом, из списка ниже):
        «я больше не хочу хранить токен здесь» и «этот токен больше не должен
        работать нигде» — разные вещи, и путать их опасно.
        """
        if not self.device_token:
            self.toast("Устройство и так не привязано", err=True)
            return
        if not self.ask_yes_no("Отвязать устройство", dv.UNBIND_TEXT):
            return
        self.device_token = ""
        active_profile(self.settings).pop("device_token", None)
        self.settings.pop("device_token", None)
        self._save_settings()
        self.connected = False
        self._key_state()
        self.toast("Токен забыт на этой машине")

    def devices_load(self):
        def done(d):
            self.dev_frame.clear()
            devices = (d or {}).get("devices") or []
            if not devices:
                self.dev_frame.set_empty("Привязанных устройств нет")
                return
            for dev in devices:
                name, note = dv.device_line(dev)
                self.dev_frame.add_row(
                    label(name), Note(note, "tiny", wrap=False), None,
                    Button("Отозвать",
                           lambda i=dev.get("id"), n=dev.get("name"): self.device_revoke(i, n)),
                    stretch=False)
        self.bg_task(lambda: self.api("GET", "/admin/devices.json"), done)

    def device_revoke(self, row_id, name):
        if not self.ask_yes_no("Отзыв устройства", dv.revoke_text(name, row_id)):
            return
        key = self.key_entry.get().strip()
        if not key:
            self.toast("Отзыв возможен только мастер-ключом — введи его выше", err=True)
            return
        saved_token = self.device_token
        self.key, self.device_token = key, ""

        def restore():
            self.device_token = saved_token
            self.key = ""

        def done(d):
            restore()
            self.toast("Устройство отозвано" if (d or {}).get("ok")
                       else "Устройство уже было отозвано", err=not (d or {}).get("ok"))
            self.devices_load()

        def fail(exc):
            restore()
            self.toast(str(exc), err=True)

        self.bg_task(lambda: self.api("POST", "/admin/device/revoke.json",
                                      {"id": row_id}), done, fail)

    def _toggle_key(self):
        self.key_entry.toggle_secret()

    def _toggle_token(self):
        self.token_entry.toggle_secret()

    def _connect(self):
        self.url = self.url_entry.get().strip().rstrip("/")
        self.key = self.key_entry.get().strip()
        self.refresh()

    def change_token(self):
        tok = self.token_entry.get().strip()
        if not dv.valid_bot_token(tok):
            self.toast("Неверный формат токена бота", err=True)
            return
        if not self.ask_yes_no("Смена токена",
                               "Сменить токен бота? После этого бота нужно перезапустить."):
            return

        def done(d):
            self.token_entry.set("")
            self.toast("Токен сохранён — можно перезапустить бота")
            if d.get("need_restart") and self.ask_yes_no(
                    "Перезапуск", "Токен сохранён. Перезапустить бота сейчас?"):
                self.restart_bot(confirmed=True)
        self.bg_task(lambda: self.api("POST", "/admin/bot_token", {"token": tok}), done)

    def restart_bot(self, confirmed=False):
        if not confirmed and not self.ask_yes_no(
                "Перезапуск бота",
                "Перезапустить бота? Он будет недоступен несколько секунд, затем "
                "поднимется автоматически."):
            return

        def done(_d):
            self.toast("Перезапуск запущен — бот поднимется через несколько секунд")

        def fail(_e):
            # обрыв соединения сразу после рестарта — ожидаемое поведение
            self.toast("Перезапуск запущен (соединение прервано — это нормально)")
        self.bg_task(lambda: self.api("POST", "/admin/restart"), done, fail)
