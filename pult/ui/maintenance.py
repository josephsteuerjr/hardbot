"""Раздел «Обслуживание»: сервер без SSH.

Всё, что задумывалось «гипотетическому инженеру», собрано здесь: состояние
единиц, журнал, копии, восстановление, перезапуск и обновление — тот же
закрытый контракт шести действий, что у веб-панели ``/ops/`` для ИТ-инженера.
Сервер допускает к нему устройства с ролями operator и engineer; произвольных
команд и карточек должников в контракте нет и не будет.

Здесь же живёт выдача ключей доступа: одноразовый код для нового пульта.
Код инженера панель не выдаёт — его рождает только команда на сервере.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from ..core import devices as dv
from .widgets import Button, Card, Combo, Field, Note, Page, Rows, label

# Подтверждения по степени опасности. «Безопасные» не спрашивают вовсе:
# ими нельзя ничего сломать, а лишний диалог приучает нажимать «да» не читая.
CONFIRM = {
    "restart": ("Перезапустить единицу",
                "Перезапустить «{unit}»?\n\nКонтейнеры поднимутся заново, "
                "данные не трогаются."),
    "update": ("Обновить единицу",
               "Обновить «{unit}»?\n\nСначала снимется копия базы, потом "
               "пересборка и перезапуск. В закрытом контуре пересборка "
               "пропускается."),
    "restore": ("Восстановить из копии",
                "Восстановить «{unit}» из копии {backup}?\n\nТекущая база "
                "отложится рядом, приложение перезапустится."),
}


class MaintenanceSection:
    def build_maint(self) -> Page:
        page = Page()

        st = Card("Состояние",
                  "Помощник на хосте исполняет операции; без него панель "
                  "только смотрит. Опись обновляется при входе в раздел и "
                  "после каждой операции.")
        self.ops_agent_note = st.add(Note("", "small"))
        self.ops_disk_note = st.add(Note("", "tiny"))
        self.ops_units = st.add(Rows(spacing=4), spacing_before=6)
        self.ops_pending_note = st.add(Note("", "tiny"), spacing_before=4)
        st.add_row(Button("Обновить состояние", self.maint_load, kind="accent"),
                   spacing_before=4)
        page.add(st)

        ops = Card("Операции",
                   "Шесть действий над каждой единицей установки. Журнал не "
                   "содержит адресов, координат и голоса.")
        self.ops_unit_combo = ops.add(Combo(width_chars=8))
        self.ops_backup_combo = ops.add(Combo(width_chars=22))
        self.ops_unit_combo.on_select(lambda: self._ops_fill_backups(
            getattr(self, "_ops_units_list", None) or []))
        ops.add_row(label("Единица", "small"), self.ops_unit_combo, 2,
                    label("Копия", "small"), self.ops_backup_combo)
        self.ops_actions = ops.add(Rows(spacing=4), spacing_before=6)
        self.ops_actions.set_empty("Состояние не загружено — «Обновить состояние»")
        page.add(ops)

        lg = Card("Журнал операций")
        self.ops_log = QPlainTextEdit()
        self.ops_log.setObjectName("OpsLog")
        self.ops_log.setReadOnly(True)
        self.ops_log.setMaximumHeight(280)
        self.ops_log.setPlainText("Выберите операцию.")
        lg.add(self.ops_log)
        page.add(lg)

        keys = Card("Устройства и ключи доступа",
                    "Контракт: ключи выдаёт тот, у кого есть логин от сервера. "
                    "Здесь — выдача одноразового кода для нового пульта "
                    "аналитика; код инженера рождается только командой на "
                    "сервере.")
        self.ops_new_device = Field(width_chars=26,
                                    placeholder="Имя пульта, например «Ноут аналитика»")
        keys.add_row(self.ops_new_device,
                     Button("Выдать код", self.maint_issue_code, kind="accent"))
        self.ops_code_note = keys.add(Note("", "small"), spacing_before=4)
        keys.add_row(Button("Обновить список устройств", self.maint_devices),
                     spacing_before=4)
        self.ops_devices = keys.add(Rows(spacing=4), spacing_before=4)
        page.add(keys)

        self.ops_timer = QTimer()
        self.ops_timer.setInterval(2000)
        self.ops_timer.timeout.connect(self._ops_tick)
        self._ops_poll: tuple[str, str] | None = None   # (id, заголовок)
        return page

    # --- состояние ------------------------------------------------------------
    def maint_load(self) -> None:
        def done(d):
            self._render_ops_state(d)

        def fail(exc):
            self.ops_agent_note.set_role("danger")
            self.ops_agent_note.set(str(exc))
            self.ops_units.clear()
            self.ops_actions.set_empty(
                "Обслуживание недоступно — состояние не загружено")
        self.bg_task(lambda: self.api("GET", "/ops/state.json"), done, fail)

    def _render_ops_state(self, d: dict) -> None:
        agent = d.get("agent") or {}
        if agent.get("alive"):
            self.ops_agent_note.set_role("success")
            self.ops_agent_note.set(f"Помощник на хосте отвечает (на связи "
                                    f"{agent.get('seen', '—')})")
        else:
            self.ops_agent_note.set_role("danger")
            self.ops_agent_note.set(
                f"Помощник на хосте не отвечает: {agent.get('reason', 'неизвестно')}. "
                "Операции встанут в очередь, которую некому забрать.")
        inv = d.get("inventory") or {}
        self.ops_disk_note.set(
            f"Свободно на диске: {inv.get('disk_free') or '—'}"
            + (f" · опись от {inv.get('at')}" if inv.get("at") else ""))
        units = inv.get("units") or []
        self._ops_units_list = units
        self.ops_units.clear()
        for u in units:
            backups = u.get("backups") or []
            last = f"копий: {len(backups)}" if backups else "копий нет"
            self.ops_units.add_row(
                label(str(u.get("unit", "?")), "title"),
                2,
                Note(f"установлена · {last}"
                     + (f" · свежая: {backups[0]}" if backups else ""), "tiny"),
                stretch=True)
        self.ops_unit_combo.set_values([str(u.get("unit", "")) for u in units])
        self._ops_fill_backups(units)
        pending = d.get("pending") or []
        if pending:
            names = ", ".join(f"{p.get('action')}·{(p.get('unit') or '')}"
                              for p in pending)
            self.ops_pending_note.set_role("warn")
            self.ops_pending_note.set(f"В очереди: {names}")
        else:
            self.ops_pending_note.set_role("tiny")
            self.ops_pending_note.set("")
        self._ops_render_buttons(list(d.get("buttons") or []))

    def _ops_fill_backups(self, units: list) -> None:
        unit = self.ops_unit_combo.get()
        current = next((u for u in units if str(u.get("unit")) == unit), {})
        self.ops_backup_combo.set_values(
            [str(b) for b in (current.get("backups") or [])])

    def _ops_render_buttons(self, buttons: list) -> None:
        self.ops_actions.clear()
        if not buttons:
            self.ops_actions.set_empty(
                "Сервер не рассказал об операциях — обнови состояние")
            return
        row = self.ops_actions.add_row(stretch=True)
        for b in buttons:
            action = str(b.get("action") or "")
            kind = "danger" if b.get("kind") == "danger" else "ghost"
            row.addWidget(Button(str(b.get("title") or action),
                                 lambda a=action: self.maint_run(a), kind=kind))
        row.addStretch(1)
        hints = " · ".join(f"{b.get('title')}: {b.get('hint')}"
                           for b in buttons if b.get("hint"))
        self.ops_actions.add(Note(hints, "tiny"))

    # --- операции --------------------------------------------------------------
    def maint_run(self, action: str) -> None:
        unit = self.ops_unit_combo.get() or "core"
        params: dict = {"unit": unit}
        if action == "restore":
            backup = self.ops_backup_combo.get()
            if not backup:
                self.toast("Копий нет — сначала снимите копию", err=True)
                return
            params["backup"] = backup
        title, text = CONFIRM.get(action, (None, None))
        if title and not self.ask_yes_no(
                title, text.format(unit=unit, backup=params.get("backup", ""))):
            return

        def done(d):
            self._ops_poll = (str((d or {}).get("id")), self._ops_title(action, unit))
            self.ops_log.setPlainText(
                f"{self._ops_title(action, unit)}: отправлена…")
            self.ops_timer.start()

        self.bg_task(lambda: self.api("POST", "/ops/run.json",
                                      {"action": action, "params": params}),
                     done, self._fail)

    @staticmethod
    def _ops_title(action: str, unit: str) -> str:
        names = {"doctor": "Диагностика", "logs": "Журнал", "backup": "Копия",
                 "restart": "Перезапуск", "update": "Обновление",
                 "restore": "Восстановление"}
        return f"{names.get(action, action)} · {unit}"

    def _ops_tick(self) -> None:
        if not self._ops_poll:
            self.ops_timer.stop()
            return
        request_id, title = self._ops_poll

        def done(d):
            if not (d or {}).get("done"):
                return
            self.ops_timer.stop()
            self._ops_poll = None
            head = ("готово" if d.get("exit") == 0
                    else f"код возврата {d.get('exit')}")
            self.ops_log.setPlainText(
                f"{title} — {head}\n\n{(d.get('output') or '(без вывода)')}")
            self.maint_load()

        self.bg_task(lambda: self.api(
            "GET", f"/ops/result.json?id={request_id}"), done,
            lambda e: (self.ops_timer.stop(), self._fail(e)))

    # --- устройства и ключи ----------------------------------------------------
    def maint_issue_code(self) -> None:
        name = self.ops_new_device.get().strip()
        if not name:
            self.toast("Назовите пульт, чтобы через месяц понять, что отзываете",
                       err=True)
            return

        def done(d):
            code = str((d or {}).get("code") or "")
            self.ops_code_note.set_role("title")
            self.ops_code_note.set(f"Код «{name}»: {code} — показывается один раз")
            try:
                QApplication.clipboard().setText(code)
                self.toast("Код скопирован в буфер обмена")
            except Exception:                                  # noqa: BLE001
                pass
            self.ops_new_device.set("")
            self.maint_devices()

        self.bg_task(lambda: self.api("POST", "/ops/code.json",
                                      {"name": name, "role": "operator"}),
                     done, self._fail)

    def maint_devices(self) -> None:
        def done(d):
            self.ops_devices.clear()
            devices = (d or {}).get("devices") or []
            if not devices:
                self.ops_devices.set_empty("Привязанных устройств нет")
            for dev in devices:
                name, note = dv.device_line(dev)
                self.ops_devices.add_row(
                    label(name), Note(note, "tiny", wrap=False), None,
                    Button("Отозвать", lambda i=dev.get("id"),
                           n=dev.get("name"): self.maint_revoke(i, n)),
                    stretch=False)
            waiting = (d or {}).get("codes") or []
            if waiting:
                self.ops_devices.add(
                    Note(f"Ждут обмена кодов: {len(waiting)}", "tiny"))

        self.bg_task(lambda: self.api("GET", "/ops/devices.json"), done, self._fail)

    def maint_revoke(self, row_id, name) -> None:
        if not self.ask_yes_no("Отзыв устройства", dv.revoke_text(name, row_id)):
            return

        def done(d):
            self.toast("Устройство отозвано" if (d or {}).get("ok")
                       else "Устройство уже было отозвано",
                       err=not (d or {}).get("ok"))
            self.maint_devices()

        self.bg_task(lambda: self.api("POST", "/ops/device/revoke.json",
                                      {"id": row_id}), done, self._fail)
