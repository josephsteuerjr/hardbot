"""Раздел «Подключение»: сервер и ключ, привязка устройства, токен бота."""

from __future__ import annotations

from ..core import devices as dv
from . import dialogs
from .widgets import Button, Card, Field, Note, Page, Rows, label


class ConnectionSection:
    def build_conn(self) -> Page:
        page = Page()
        sc = Card("Сервер")
        sc.add(label("Адрес сервера", "small"))
        self.url_entry = sc.add(Field(self.url))
        sc.add(label("Admin-ключ", "small"), spacing_before=4)
        self.key_entry = Field(self.key, secret=True)
        sc.add_row(self.key_entry, Button("👁", self._toggle_key, kind="icon"), stretch=False)
        sc.add_row(Button("Привязать это устройство", self.device_enroll, kind="accent"),
                   Button("Подключиться", self._connect),
                   Button("Отвязать это устройство", self.device_unbind), spacing_before=4)
        self.key_out = sc.add(Note("", "tiny"), spacing_before=4)
        self._key_state()
        page.add(sc)

        dv_card = Card("Привязанные устройства",
                       "Каждое устройство ходит своим токеном. Потерянный ноутбук "
                       "отзывается по одному — мастер-ключ менять не надо, остальные "
                       "продолжают работать. Отзыв возможен только мастер-ключом.")
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
        return page

    # --- привязка устройства -------------------------------------------------
    device_fingerprint = staticmethod(dv.device_fingerprint)
    device_default_name = staticmethod(dv.device_default_name)

    def _key_state(self):
        self.key_out.configure(text=dv.key_state_text(self.device_token))

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
        self.key, self.device_token = key, ""

        def work():
            return self.api("POST", "/admin/device/enroll.json",
                            {"device_id": fingerprint, "name": name.strip()})

        def done(d):
            token = (d or {}).get("token") or ""
            if not token:
                self.toast("Сервер не выдал токен", err=True)
                return
            self.device_token = token
            self.settings["device_token"] = token
            # Ключ НЕ сохраняем — в этом весь смысл: на диске остаётся токен,
            # который отзывается по одному и работает только с этой машины.
            self.settings.pop("key", None)
            self.key = ""
            self.key_entry.set("")
            self._save_settings()
            self._key_state()
            self.toast("Устройство привязано — ключ больше не хранится")
            self.devices_load()
            self.refresh()

        def fail(exc):
            self.key = key
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
