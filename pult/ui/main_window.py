"""Главное окно «Хард · Пульт»: рейка, шапка, стопка разделов, тост.

`Pult` собирает разделы-примеси и держит общее: настройки, учётные данные,
клиент API, фоновые задачи, навигацию. Имена методов и атрибутов состояния
сохранены с прежнего пульта — на них смотрят `core/import_flow.py` и тесты.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..core import api as api_client
from ..core.bootstrap import APP_DIR
from ..core.constants import DEFAULT_KEY, DEFAULT_URL
from ..core.import_flow import ImportFlow
from ..core.settings import Settings
from . import dialogs
from .activity import ActivitySection
from .connection import ConnectionSection
from .dashboard import DashboardSection
from .managers import ManagersSection
from .nav import SECTIONS, TITLES, NavRail
from .people import PeopleSection
from .priority import PrioritySection
from .registry import RegistrySection
from .tasks import TaskRunner
from .widgets import Button, Note, Toast, label

ICON_PATH = APP_DIR / "icon.png"
LAZY = {"prio": "load_priority", "activity": "load_activity",
        "people": "load_roles", "mgrs": "load_managers"}


class Pult(QMainWindow, ImportFlow, DashboardSection, RegistrySection, PrioritySection,
           PeopleSection, ManagersSection, ActivitySection, ConnectionSection):
    invoke = Signal(object)

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Хард · Пульт")
        if ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.settings = settings if settings is not None else Settings.load()
        self.url = self.settings.get("url", DEFAULT_URL)
        self.key = self.settings.get("key", DEFAULT_KEY)
        self.device_token = self.settings.get("device_token", "")
        self.imp_book: dict = {}          # выбранная книга: путь, грид, вид источника
        self.imp_selected: set[str] = set()   # кого из сотрудников грузим
        self.db_employees: list[dict] = []    # фактические портфели последнего /stats
        self.exp_selected: set[str] = set()
        self._exp_vars: dict = {}
        self._exp_initialized = False
        self._imp_vars: dict = {}
        self._imp_preview_body: dict | None = None
        self._imp_preview_signature: tuple | None = None
        self._imp_preview_meta: dict = {}
        self.connected = False
        self.guests: list[dict] = []
        self.mgrs: list[dict] = []
        self.mgr_cities: list[dict] = []
        self.mgr_emps: list[dict] = []
        self.mgr_city_emps: dict = {}
        self.mgr_form = {"tid": "", "cities": set(), "employees": set()}
        self.scatter_matched: list[dict] = []
        self.scatter_selected: set[str] = set()
        self.scatter_roots: list[str] = []
        self.enr_matched: list[dict] = []
        self.enr_selected: set[str] = set()
        self._enr_fields: list[dict] | None = None
        self._loaded: set[str] = set()

        self.tasks = TaskRunner(self)
        self.invoke.connect(lambda fn: fn())
        self._build()
        self._place()
        has_credentials = bool(self.key or self.device_token)
        sec = self.settings.get("sec", "dash") if has_credentials else "conn"
        self.nav(sec if sec in self.pages else "dash")
        if has_credentials:
            QTimer.singleShot(150, self.refresh)

    # ── каркас ──────────────────────────────────────────────────────────
    def _place(self) -> None:
        self.resize(1180, 800)
        self.setMinimumSize(980, 660)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.x() + (area.width() - 1180) // 2, area.x())
            y = max(area.y() + (area.height() - 800) // 3, area.y())
            self.move(x, y)

    def _build(self) -> None:
        root = QWidget()
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.rail = NavRail()
        self.rail.selected.connect(self.nav)
        lay.addWidget(self.rail)

        main = QWidget()
        col = QVBoxLayout(main)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(26, 14, 26, 10)
        self.sec_title = label("Сводка", "h1")
        bar.addWidget(self.sec_title)
        bar.addStretch(1)
        self.upd_lbl = Note("", "tiny", wrap=False)
        bar.addWidget(self.upd_lbl)
        bar.addSpacing(10)
        bar.addWidget(Button("Обновить", self.refresh))
        col.addLayout(bar)

        self.stack = QStackedWidget()
        col.addWidget(self.stack, 1)
        lay.addWidget(main, 1)
        self.setCentralWidget(root)

        self.pages: dict[str, QWidget] = {}
        for sec, _ in SECTIONS:
            page = getattr(self, f"build_{sec}")()
            self.pages[sec] = page
            self.stack.addWidget(page)

        self.toast_lbl = Toast(self)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt
        super().resizeEvent(event)
        self.toast_lbl.reposition()

    def nav(self, sec: str) -> None:
        self.rail.set_active(sec)
        page = self.pages[sec]
        self.stack.setCurrentWidget(page)
        self.sec_title.setText(TITLES.get(sec, ""))
        self.settings["sec"] = sec
        page.scroll_top()
        if self.connected and sec in LAZY and sec not in self._loaded:
            getattr(self, LAZY[sec])()

    # ── связь / фоновые задачи ──────────────────────────────────────────
    def _set_conn(self, ok: bool, text: str) -> None:
        self.connected = ok
        self.rail.set_connection(ok, text, api_client.host_of(self.url))

    def toast(self, text: str, err: bool = False) -> None:
        self.toast_lbl.show_text(text, err=err)

    def bg_task(self, fn, done=None, fail=None) -> None:
        """fn — в потоке; done(result)/fail(exc) — в UI-потоке."""
        self.tasks.run(fn, done, fail or self._fail)

    def ui_call(self, fn) -> None:
        """Выполнить `fn` в UI-потоке (из фонового — через очередь сигналов)."""
        self.invoke.emit(fn)

    def _fail(self, e: Exception) -> None:
        self.toast(str(e), err=True)

    def ask_yes_no(self, title: str, text: str) -> bool:
        return dialogs.askyesno(title, text, parent=self)

    def api(self, method: str, path: str, body: dict | None = None, timeout=20):
        """Запрос к admin-API текущими учётными данными. Дружелюбные ошибки."""
        return api_client.request(self.url, method, path, body, key=self.key,
                                  device_token=self.device_token, timeout=timeout)

    # ── обновление ──────────────────────────────────────────────────────
    def refresh(self) -> None:
        self.settings["url"], self.settings["key"] = self.url, self.key
        self._save_settings()

        def done(d):
            self._set_conn(True, "подключено")
            self.db_employees = list(d.get("employees") or [])
            self._render_export_employees()
            self.render_stats(d)
            self._show_period(d.get("period") or {})
            self._render_binds(d.get("bindings") or [])
            self.upd_lbl.setText("обновлено " + datetime.now().strftime("%H:%M"))
            self._loaded.clear()
            cur = self.settings.get("sec", "dash")
            if cur in LAZY:
                self.nav(cur)

        def fail(e):
            self._set_conn(False, "нет связи")
            self.toast(str(e), err=True)
        self.bg_task(lambda: self.api("GET", "/admin/stats"), done, fail)

    # ── настройки ───────────────────────────────────────────────────────
    def _load_settings(self) -> dict:
        return Settings.load()

    def _save_settings(self) -> None:
        self.settings.save()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt
        self.settings["url"] = self.url
        # Мастер-ключ при выходе НЕ сохраняем никогда. Он нужен ровно на одну
        # операцию — привязать или отозвать устройство — и оставаться на диске
        # ему незачем: для повседневной работы есть токен устройства.
        if self.device_token:
            self.settings["device_token"] = self.device_token
        self._save_settings()
        super().closeEvent(event)


__all__ = ["Pult", "ICON_PATH", "Path"]
