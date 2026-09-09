"""Раздел «Каналы»: рассылка руководителям, диагностика доставки, беседы отчётов.

Всё это жило в «Руководителях» — шесть карточек на одном экране, где про самих
руководителей были две. Теперь «кому и как шлём» — здесь, «кто руководитель» —
там."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QVBoxLayout

from ..core import managers as mg
from .widgets import Button, Card, Check, Combo, Field, KVGrid, Note, Page, Rows, label


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
        lay.addWidget(label("Чьи визиты постить в эту беседу (плюсом к направлению):",
                            bold=True))
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


class ChannelsSection:
    def build_channels(self) -> Page:
        page = Page()

        dc = Card("Рассылка руководителям",
                  "В конце дня (по Москве) каждый руководитель получает сводку по своим "
                  "сотрудникам; утром — просроченные обещания; по каждому закрытому "
                  "визиту — карточку. Кому что слать, включается у каждого руководителя "
                  "отдельно на экране «Руководители».")
        self.dig_time = Field("16:30", width_chars=7)
        self.dig_enabled = Check("автопуш включён", True)
        dc.add_row(label("Сводка (МСК)", "small"), self.dig_time, 4, self.dig_enabled)
        self.digest_xlsx_check = Check(
            "сводку слать книгой Excel (закреплено · сегодня · всего), а не длинным текстом",
            True)
        self.digest_xlsx_var = self.digest_xlsx_check
        dc.add_row(self.digest_xlsx_check)
        self.prom_time = Field("09:30", width_chars=7)
        self.prom_enabled = Check("утренний пуш «обещание просрочено»", True)
        dc.add_row(label("Обещания (МСК)", "small"), self.prom_time, 4, self.prom_enabled)
        self.visit_push_check = Check("карточки по закрытым визитам (общий рубильник)", True)
        self.visit_push_var = self.visit_push_check
        dc.add_row(self.visit_push_check)
        # Личные рубильники владельца. Общий «автопуш включён» гасит рассылку
        # ВСЕМ руководителям сразу — отписаться самому было нечем.
        self.owner_digest_check = Check("сводка", True)
        self.owner_digest_var = self.owner_digest_check
        self.owner_promise_check = Check("обещания", False)
        self.owner_promise_var = self.owner_promise_check
        self.owner_visits_check = Check("карточки по визитам", False)
        self.owner_visits_var = self.owner_visits_check
        dc.add_row(label("Лично мне:", "small"), self.owner_digest_check,
                   self.owner_promise_check, self.owner_visits_check)
        dc.add_row(Button("Сохранить", self.save_digest, kind="accent"), spacing_before=4)
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

        rc = Card("Пересылка отчётов в беседы",
                  "Закрытый визит уходит постом в закреплённые беседы: клиент, адрес, "
                  "статусы, комментарий, доп. адрес, фото и гео-точки. Добавь бота в "
                  "группу — она появится здесь; включи её и выбери направление "
                  "(Таджикистан / Узбекистан / СНГ) — состав считается живьём. Можно "
                  "добавить отдельных сотрудников сверх направления.")
        rc.add_row(Button("Обновить список бесед", self.chats_refresh))
        self.chats_list = rc.add(Rows(spacing=4), spacing_before=4)
        self.chats_list.set_empty("Бесед пока нет — добавь бота в группу, и она появится здесь")
        self._chats_emp: list[str] = []
        page.add(rc)
        return page

    # --- загрузка ---
    def load_channels(self):
        def done(d):
            self._loaded.add("channels")
            if d:
                self.dig_time.set(d.get("time") or "16:30")
                self.dig_enabled.set(bool(d.get("enabled")))
                self.prom_time.set(d.get("promise_time") or "09:30")
                self.prom_enabled.set(bool(d.get("promise_enabled", True)))
                self._show_owner_switches(d)
                self._show_visit_push(d)
                self._show_digest_state(d)
                self.delivery_kvs.set_pairs(mg.delivery_pairs(d), cols=2)
                self.delivery_out.setText("\n".join(mg.delivery_lines(d)))
            self.chats_refresh()
        self.bg_task(lambda: self.api("GET", "/admin/digest_settings"), done)

    # --- рассылка ---
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
            owner_promise=self.owner_promise_var.get(),
            owner_visits_known=self.owner_visits_check.isEnabled(),
            owner_visits=self.owner_visits_var.get(),
            xlsx_known=self.digest_xlsx_check.isEnabled(),
            digest_xlsx=self.digest_xlsx_var.get())

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
        # Карточки по визитам появились позже остальных, поэтому свой признак
        # «сервер этот ключ знает»: на старом галочка гаснет, а не врёт.
        visits_known = "owner_visits" in d
        if visits_known:
            self.owner_visits_var.set(bool(d.get("owner_visits")))
        self.owner_visits_check.configure(
            state="normal" if visits_known else "disabled")

    def _show_visit_push(self, d: dict):
        if "visit_push" in d:
            self.visit_push_var.set(bool(d.get("visit_push")))
            self.visit_push_check.configure(state="normal")
        else:
            self.visit_push_check.configure(state="disabled")
        if "digest_xlsx" in d:
            self.digest_xlsx_var.set(bool(d.get("digest_xlsx")))
            self.digest_xlsx_check.configure(state="normal")
        else:
            self.digest_xlsx_check.configure(state="disabled")

    def _show_digest_state(self, d: dict):
        self.dig_out.setText(mg.digest_state_text(d))

    # --- доставка ---
    def load_delivery(self):
        def done(d):
            self.delivery_kvs.set_pairs(mg.delivery_pairs(d), cols=2)
            self.delivery_out.setText("\n".join(mg.delivery_lines(d)))
            self._show_owner_switches(d)
            self._show_visit_push(d)
            self._show_digest_state(d)
        self.bg_task(lambda: self.api("GET", "/admin/digest_settings"), done)

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
            combo = Combo([lbl for _code, lbl in mg.CHAT_DIRECTION_OPTIONS],
                          width_chars=24)
            combo.set_current(mg.chat_direction_index(c.get("direction") or ""))
            # activated — только выбор ЧЕЛОВЕКОМ: перерисовка списка не должна
            # сама отправлять настройку обратно на сервер.
            combo.activated.connect(
                lambda i, cid=c["chat_id"]: self._chat_direction(cid, i))
            self.chats_list.add_row(
                check, Note(mg.chat_scope_text(c), "tiny", wrap=False), None,
                combo,
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

    def _chat_direction(self, chat_id, index):
        """Направление беседы: состав считается живьём, как у руководителя."""
        code = mg.chat_direction_code(index)
        self.bg_task(
            lambda: self.api("POST", "/admin/chats.set",
                             {"chat_id": chat_id, "direction": code}),
            lambda d: (self.toast("Направление беседы сохранено"),
                       self._render_chats(d.get("chats") or [])),
            lambda e: (self.toast(f"Не сохранилось: {e}", err=True),
                       self.chats_refresh()))

    def _chat_emps_dialog(self, chat):
        ChatEmployeesDialog(self, chat, self._chats_emp or []).show()
