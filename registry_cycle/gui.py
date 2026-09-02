"""Одно спокойное окно с двумя независимыми ручными операциями (PySide6).

Шаг 2 никогда не стартует сам после шага 1. У каждого шага своя проверка,
план, подтверждение фразой, архивная копия и квитанция. Тексты — из
`config/app.json` (`manual_ui`), цвета и размеры — из `config/theme.json`.
"""

from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLayout, QLineEdit, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import theme
from .config import ConfigBundle, load_config_bundle, save_app_config
from .errors import ConfigurationError, RegistryCycleError
from .manual_downward import (
    ManualDownwardRun,
    ProjectionDelta,
    prepare_manual_downward,
    publish_prepared_manual_downward,
)
from .manual_upward import (
    ManualUpwardRun,
    prepare_manual_upward,
    publish_prepared_manual_upward,
)
from .models import Issue

OperationRun = ManualUpwardRun | ManualDownwardRun
ICON_PATH = Path(__file__).resolve().parent / "icon.png"


def _short(value: Any, limit: int) -> str:
    text = "—" if value is None or str(value) == "" else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clear(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _card(padding: int) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(padding, padding, padding, padding)
    lay.setSpacing(0)
    return frame, lay


def _label(text: str, font, role: str | None = None, wrap: bool = True) -> QLabel:
    w = QLabel(text)
    w.setFont(font)
    w.setWordWrap(wrap)
    w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if role:
        w.setProperty("role", role)
    return w


def _message(icon, title: str, text: str, parent: QWidget | None, button: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.addButton(button, QMessageBox.ButtonRole.AcceptRole)
    box.exec()


# --- фоновые задачи ------------------------------------------------------------
class _Signals(QObject):
    progress = Signal(str, str)
    finished = Signal(object, object)
    failed = Signal(object)


class _Worker(QRunnable):
    def __init__(self, action: Callable[[], Any], success, signals: _Signals) -> None:
        super().__init__()
        self.action = action
        self.success = success
        self.signals = signals

    def run(self) -> None:
        try:
            result = self.action()
        except Exception as exc:  # noqa: BLE001 — показывается в UI-потоке
            self.signals.failed.emit(exc)
        else:
            self.signals.finished.emit(self.success, result)


@dataclass
class _RootControls:
    frame: QFrame
    country: QLineEdit
    path: QLineEdit


class _Scroll(QScrollArea):
    """Прокручиваемый холст с содержимым, центрированным по content_width."""

    def __init__(self, content_width: int) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName("Canvas")
        row = QHBoxLayout(canvas)
        row.setContentsMargins(0, 0, 0, 0)
        self.content = QWidget()
        self.content.setObjectName("Canvas")
        self.content.setMaximumWidth(content_width)
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        self.body.addStretch(1)
        row.addStretch(1)
        row.addWidget(self.content, 1)
        row.addStretch(1)
        self.setWidget(canvas)

    def add(self, widget: QWidget, gap: int = 0) -> QWidget:
        self.body.insertWidget(self.body.count() - 1, widget)
        if gap:
            self.body.insertSpacing(self.body.count() - 1, gap)
        return widget

    def clear(self) -> None:
        _clear(self.body)
        self.body.addStretch(1)


class SourceFoldersDialog(QDialog):
    """Small config-backed folder picker; no naming expressions or rules."""

    def __init__(self, app: "ManualTransferApp") -> None:
        super().__init__(app)
        self.app = app
        self.bundle = app.bundle
        self.ui = app.ui
        self.colors = app.colors
        self.layout_ = app.layout_
        self.payload = copy.deepcopy(self.bundle.pinned_config("app.json"))
        self.rows: list[_RootControls] = []
        self.setWindowTitle(self.ui["sections"]["rules"])
        self.resize(int(self.layout_["dialog_width"]), int(self.layout_["dialog_height"]))
        self.setMinimumSize(int(self.layout_["dialog_minimum_width"]),
                            int(self.layout_["dialog_minimum_height"]))
        self.setModal(True)
        self._build()

    def _font(self, size: int | str, weight: str = "normal"):
        return self.app.font(size, weight)

    def _button(self, text: str, command: Callable[[], None], *, primary: bool = False,
                compact: bool = False) -> QPushButton:
        return self.app._button(text, command, primary=primary, compact=compact)

    def _entry(self, value: str) -> QLineEdit:
        edit = QLineEdit(value)
        edit.setFont(self._font("body"))
        return edit

    def _build(self) -> None:
        padding = int(self.layout_["page_padding"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(padding, padding, padding, padding)
        lay.setSpacing(12)
        lay.addWidget(_label(self.ui["sections"]["rules"], self._font("heading", "bold")))
        lay.addWidget(_label(self.ui["copy"]["rules"], self._font("body"), "muted"))
        self.scroll = _Scroll(int(self.layout_["dialog_width"]))
        lay.addWidget(self.scroll, 1)
        for source in self.payload["paths"]["portfolio_roots"]:
            self._add_row(source)
        self.add_button = self._button(self.ui["actions"]["add_root"],
                                       lambda: self._add_row(None), compact=True)
        self.scroll.add(self.add_button, 16)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self._button(self.ui["actions"]["save"], self._save, primary=True))
        footer.addWidget(self._button(self.ui["actions"]["cancel"], self.reject))
        lay.addLayout(footer)

    def _add_row(self, source: dict[str, Any] | None) -> None:
        source = source or {}
        frame, body = _card(int(self.layout_["card_padding"]))
        controls = _RootControls(frame, self._entry(str(source.get("country", ""))),
                                 self._entry(str(source.get("path", ""))))
        self.rows.append(controls)
        top = QHBoxLayout()
        top.addWidget(_label(self.ui["sections"]["folders"], self._font("body", "bold")))
        top.addStretch(1)
        top.addWidget(self._button(self.ui["actions"]["remove"],
                                   lambda: self._remove_row(controls), compact=True))
        body.addLayout(top)
        body.addSpacing(10)
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(4)
        form.addWidget(_label(self.ui["rules"]["country"], self._font("small", "bold"),
                              "caps"), 0, 0)
        form.addWidget(_label(self.ui["rules"]["root_path"], self._font("small", "bold"),
                              "caps"), 0, 1)
        form.addWidget(controls.country, 1, 0)
        form.addWidget(controls.path, 1, 1)
        form.addWidget(self._button(self.ui["actions"]["browse"],
                                    lambda: self._browse(controls), compact=True), 1, 2)
        form.setColumnStretch(1, 1)
        body.addLayout(form)
        body.addSpacing(9)
        body.addWidget(_label(self.ui["rules"]["root_hint"], self._font("small"), "muted"))
        # новая папка встаёт перед кнопкой «Добавить корень» и растяжкой
        index = max(self.scroll.body.count() - 1, 0)
        if hasattr(self, "add_button"):
            index = self.scroll.body.indexOf(self.add_button)
        self.scroll.body.insertWidget(index, frame)
        self.scroll.body.insertSpacing(index + 1, 10)

    def _browse(self, controls: _RootControls) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, self.ui["actions"]["browse"], controls.path.text().strip() or "")
        if selected:
            controls.path.setText(selected)

    def _remove_row(self, controls: _RootControls) -> None:
        if controls in self.rows:
            self.rows.remove(controls)
        controls.frame.setParent(None)
        controls.frame.deleteLater()

    def _candidate(self) -> dict[str, Any]:
        if not self.rows:
            raise ConfigurationError(self.ui["rules"]["empty_root"])
        roots = []
        for controls in self.rows:
            country = controls.country.text().strip()
            path = controls.path.text().strip()
            if not country or not path:
                raise ConfigurationError(self.ui["rules"]["empty_root"])
            roots.append({"country": country, "path": path})
        payload = copy.deepcopy(self.payload)
        payload["paths"]["portfolio_roots"] = roots
        return payload

    def _save(self) -> None:
        try:
            bundle = save_app_config(self.bundle.config_dir, self._candidate())
        except RegistryCycleError as exc:
            _message(QMessageBox.Icon.Critical, self.ui["sections"]["rules"], str(exc), self,
                     self.ui["actions"]["close"])
            return
        self.app.rules_saved(bundle)
        self.accept()


class PublishDialog(QDialog):
    """Подтверждение фразой: пока она не совпала, ничего не меняется."""

    def __init__(self, app: "ManualTransferApp", operation: str, phrase: str) -> None:
        super().__init__(app)
        self.app = app
        self.phrase = phrase
        self.confirmed = False
        copy_block = app.ui["confirmations"][operation]
        self.setWindowTitle(copy_block["title"])
        self.setModal(True)
        self.setFixedWidth(int(app.layout_["modal_width"]))
        pad = int(app.layout_["modal_padding"])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(pad, pad, pad, pad)
        frame, body = _card(int(app.layout_["card_padding"]))
        body.setSpacing(0)
        body.addWidget(_label(copy_block["title"], app.font("heading", "bold")))
        body.addSpacing(7)
        body.addWidget(_label(copy_block["body"], app.font("body"), "muted"))
        body.addSpacing(13)
        body.addWidget(_label(copy_block["input"].format(phrase=phrase),
                              app.font("body", "bold")))
        body.addSpacing(5)
        self.entry = QLineEdit()
        self.entry.setFont(app.font("body"))
        self.entry.returnPressed.connect(self._confirm)
        body.addWidget(self.entry)
        body.addSpacing(5)
        self.error = _label("", app.font("small"), "danger")
        body.addWidget(self.error)
        body.addSpacing(15)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(app._button(app.ui["actions"][f"publish_{operation}"],
                                      self._confirm, primary=True))
        actions.addWidget(app._button(app.ui["actions"]["cancel"], self.reject))
        body.addLayout(actions)
        outer.addWidget(frame)
        self.invalid_text = copy_block["invalid"]
        self.entry.setFocus()

    def _confirm(self) -> None:
        if self.entry.text().strip() != self.phrase:
            self.error.setText(self.invalid_text)
            return
        self.confirmed = True
        self.accept()


class ManualTransferApp(QMainWindow):
    """One window; step 2 never starts automatically after step 1."""

    _STATE_COLORS = {
        "idle": "text_muted",
        "checking": "info",
        "ready": "success",
        "no_changes": "text_muted",
        "blocked": "danger",
        "publishing": "info",
        "published": "success",
        "failed": "danger",
    }

    def __init__(self, bundle: ConfigBundle):
        super().__init__()
        self.bundle = bundle
        self.ui = bundle.app.manual_ui
        self.colors = bundle.theme["colors"]
        self.layout_ = bundle.theme["layout"]
        self.typography = bundle.theme["typography"]
        self.runs: dict[str, OperationRun | None] = {"upward": None, "downward": None}
        self.states = {"upward": "idle", "downward": "idle"}
        self.details = {"upward": False, "downward": False}
        self.status_details = {
            "upward": self.ui["copy"]["upward"],
            "downward": self.ui["copy"]["downward"],
        }
        self.active_operation: str | None = None
        self.busy_operation: str | None = None
        self.action_buttons: list[QPushButton] = []
        self.pool = QThreadPool.globalInstance()
        self._signals: set[_Signals] = set()
        self._build_window()
        self._build_shell()
        self._refresh_view()

    @property
    def busy(self) -> bool:
        return self.busy_operation is not None

    def font(self, size: int | str, weight: str = "normal"):
        return theme.font(self.typography, size, weight)

    # --- каркас ---------------------------------------------------------------
    def _build_window(self) -> None:
        self.setWindowTitle(self.ui["window"]["title"])
        if ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(int(self.layout_["window_width"]), int(self.layout_["window_height"]))
        self.setMinimumSize(int(self.layout_["minimum_width"]),
                            int(self.layout_["minimum_height"]))

    def _build_shell(self) -> None:
        padding = int(self.layout_["page_padding"])
        root = QWidget()
        root.setObjectName("Canvas")
        lay = QVBoxLayout(root)
        lay.setContentsMargins(padding, padding, padding, padding)
        lay.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.title_label = _label(self.ui["window"]["title"], self.font("title", "bold"))
        self.subtitle_label = _label(self.ui["window"]["subtitle"], self.font("body"), "muted")
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        header.addLayout(titles, 1)
        self.rules_button = self._button(self.ui["actions"]["rules"], self.open_rules,
                                         compact=True)
        header.addWidget(self.rules_button, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(header)
        self.panel = _Scroll(int(self.layout_["content_width"]))
        lay.addWidget(self.panel, 1)
        self.setCentralWidget(root)

    def _button(self, text: str, command: Callable[[], None], *, primary: bool = False,
                compact: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("kind", "primary" if primary else ("compact" if compact else "ghost"))
        button.setFont(self.font("small" if compact else "body",
                                 "bold" if primary else "normal"))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.clicked.connect(lambda _checked=False: command())
        return button

    def _action(self, *args: Any, **kwargs: Any) -> QPushButton:
        button = self._button(*args, **kwargs)
        self.action_buttons.append(button)
        return button

    def _heading(self, text: str) -> QLabel:
        return _label(text, self.font("heading", "bold"))

    def _muted(self, text: str) -> QLabel:
        return _label(text, self.font("body"), "muted")

    # --- отрисовка --------------------------------------------------------------
    def _refresh_view(self) -> None:
        self.panel.clear()
        self.action_buttons = []
        for operation in ("upward", "downward"):
            self._render_operation(operation)
            if self.active_operation == operation and self.runs[operation] is not None:
                self._render_result(operation, self.runs[operation])
        enabled = not self.busy
        self.rules_button.setEnabled(enabled)
        for button in self.action_buttons:
            button.setEnabled(enabled)

    def _render_operation(self, operation: str) -> None:
        gap = int(self.layout_["gap"])
        card, body = _card(int(self.layout_["card_padding"]))
        body.addWidget(self._heading(self.ui["sections"][operation]))
        body.addSpacing(6)
        body.addWidget(self._muted(self.ui["copy"][operation]))
        if operation == "upward":
            body.addSpacing(9)
            body.addWidget(self._muted(
                self.ui["templates"]["master"].format(name=self.bundle.master_path().name)))
        body.addSpacing(12)
        state = self.states[operation]
        status = QHBoxLayout()
        status.setSpacing(6)
        dot = QLabel("●")
        dot.setObjectName("Dot")
        dot.setStyleSheet(f"color: {self.colors[self._STATE_COLORS[state]]};")
        status.addWidget(dot)
        status.addWidget(_label(self.ui["states"][state], self.font("body", "bold")))
        status.addStretch(1)
        body.addLayout(status)
        body.addSpacing(4)
        body.addWidget(self._muted(self.status_details[operation]))
        if self.busy_operation == operation:
            body.addSpacing(12)
            progress = QProgressBar()
            progress.setRange(0, 0)
            progress.setTextVisible(False)
            body.addWidget(progress)
        elif not self.busy:
            retry = self.runs[operation] is not None
            action_key = f"retry_{operation}" if retry else f"check_{operation}"
            body.addSpacing(13)
            body.addWidget(self._action(self.ui["actions"][action_key],
                                        lambda selected=operation: self.start_check(selected),
                                        primary=True), 0, Qt.AlignmentFlag.AlignLeft)
        self.panel.add(card, gap)

    def _visible_issues(self, run: OperationRun) -> list[Issue]:
        issues = list(run.plan.issues)
        if run.validation is not None:
            issues.extend(run.validation.issues)
        display = self.ui["issue_display"]
        visible = [
            issue
            for issue in issues
            if issue.severity in set(display["severities"])
            and issue.code not in set(display["hidden_codes"])
        ]
        grouped: dict[tuple[str, str, str], list[Issue]] = {}
        for issue in visible:
            grouped.setdefault((issue.severity, issue.code, issue.message), []).append(issue)
        result = []
        for (severity, code, message), repeated in grouped.items():
            if display["group_repeated"] and len(repeated) > 1:
                message = self.ui["templates"]["issue_group"].format(
                    message=message,
                    count=len(repeated),
                )
            result.append(Issue(severity, code, message, {"count": len(repeated)}))
        rank = {"error": 0, "warning": 1, "info": 2}
        return sorted(result, key=lambda item: (rank.get(item.severity, 9), item.message.casefold()))

    def _metric(self, row: QHBoxLayout, text: str) -> None:
        metric = QFrame()
        metric.setObjectName("Metric")
        lay = QVBoxLayout(metric)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.addWidget(_label(text, self.font("body", "bold")))
        row.addWidget(metric, 1)

    def _render_result(self, operation: str, run: OperationRun) -> None:
        gap = int(self.layout_["gap"])
        padding = int(self.layout_["card_padding"])
        issues = self._visible_issues(run)
        summary, body = _card(padding)
        body.addWidget(self._heading(self.ui["sections"]["summary"]))
        body.addSpacing(11)
        metrics = QHBoxLayout()
        metrics.setSpacing(7)
        if operation == "upward":
            values = (
                self.ui["templates"]["source_count"].format(count=len(run.snapshot.personals)),
                self.ui["templates"]["change_count"].format(count=len(run.plan.changes)),
                self.ui["templates"]["issue_count"].format(count=len(issues)),
            )
        else:
            assert isinstance(run, ManualDownwardRun)
            values = (
                self.ui["templates"]["portfolio_change_count"].format(count=len(run.changed_deltas)),
                self.ui["templates"]["row_count"].format(count=sum(item.rows_after for item in run.deltas)),
                self.ui["templates"]["employee_payment_count"].format(
                    count=sum(item.employee_payment_cells for item in run.deltas)
                ),
            )
        for value in values:
            self._metric(metrics, value)
        body.addLayout(metrics)
        body.addSpacing(11)
        body.addWidget(self._muted(self.ui["copy"]["payment_scope"]))
        self.panel.add(summary, gap)

        if issues:
            issue_card, ibody = _card(padding)
            ibody.addWidget(self._heading(self.ui["sections"]["issues"]))
            for issue in issues[: int(self.ui["limits"]["issue_preview"])]:
                self._issue_row(ibody, issue)
            self.panel.add(issue_card, gap)

        if operation == "upward" and run.plan.changes:
            changes, cbody = _card(padding)
            cbody.addWidget(self._heading(self.ui["sections"]["changes"]))
            for change in run.plan.changes[: int(self.ui["limits"]["change_preview"])]:
                self._change_row(
                    cbody,
                    f"{change.contract_id} · {change.title}",
                    f"{_short(change.before, self.ui['limits']['value_preview_chars'])}  →  "
                    f"{_short(change.after, self.ui['limits']['value_preview_chars'])}",
                )
            self.panel.add(changes, gap)
        elif operation == "downward":
            assert isinstance(run, ManualDownwardRun)
            if run.changed_deltas:
                changes, cbody = _card(padding)
                cbody.addWidget(self._heading(self.ui["sections"]["changes"]))
                for delta in run.changed_deltas[: int(self.ui["limits"]["change_preview"])]:
                    self._delta_row(cbody, delta)
                self.panel.add(changes, gap)

        actions, abody = _card(padding)
        abody.setSpacing(8)
        can_publish = run.ready_to_publish and (
            operation == "downward" or bool(run.plan.changes)
        )
        if can_publish and not self.busy:
            abody.addWidget(self._action(
                self.ui["actions"][f"publish_{operation}"],
                lambda selected=operation: self.ask_to_publish(selected),
                primary=True), 0, Qt.AlignmentFlag.AlignLeft)
        abody.addWidget(self._action(
            self.ui["actions"]["open_report"],
            lambda selected=operation: self.open_report(selected)), 0,
            Qt.AlignmentFlag.AlignLeft)
        abody.addWidget(self._action(
            self.ui["actions"]["details_hide"] if self.details[operation]
            else self.ui["actions"]["details"],
            lambda selected=operation: self.toggle_details(selected)), 0,
            Qt.AlignmentFlag.AlignLeft)
        self.panel.add(actions, gap)
        if self.details[operation]:
            self._render_details(run, gap, padding, operation)

    def _issue_row(self, body: QVBoxLayout, issue: Issue) -> None:
        color = self.colors["danger" if issue.severity == "error" else "warning"]
        row = QHBoxLayout()
        row.setSpacing(9)
        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background: {color}; border-radius: 1px;")
        row.addWidget(bar)
        row.addWidget(_label(issue.message, self.font("body")), 1)
        body.addSpacing(9)
        body.addLayout(row)

    def _change_row(self, body: QVBoxLayout, title: str, detail: str) -> None:
        body.addSpacing(8)
        body.addWidget(_label(title, self.font("body", "bold")))
        body.addSpacing(2)
        body.addWidget(_label(detail, self.font("small"), "muted"))
        body.addSpacing(8)
        rule = QFrame()
        rule.setObjectName("Rule")
        rule.setFixedHeight(1)
        body.addWidget(rule)

    def _delta_row(self, body: QVBoxLayout, delta: ProjectionDelta) -> None:
        self._change_row(
            body,
            f"{delta.employee} · {delta.path.name}",
            f"Строк: {delta.rows_before} → {delta.rows_after} · ячеек: {delta.changed_cells}",
        )

    def _render_details(self, run: OperationRun, gap: int, padding: int, operation: str) -> None:
        details, body = _card(padding)
        body.addWidget(self._heading(self.ui["sections"]["details"]))
        body.addSpacing(5)
        body.addWidget(self._muted(self.ui["copy"]["technical"]))
        body.addSpacing(8)
        report = run.snapshot.run_dir / ("plan.txt" if operation == "upward" else "downward.txt")
        values = [
            self.ui["templates"]["run"].format(id=run.snapshot.run_id),
            self.ui["templates"]["digest"].format(digest=run.plan.digest),
            self.ui["templates"]["report"].format(path=report),
        ]
        if run.publication is not None:
            values.append(
                self.ui["templates"]["published_files"].format(
                    count=len(run.publication.published)
                )
            )
            if run.publication.receipt:
                values.append(
                    self.ui["templates"]["receipt"].format(path=run.publication.receipt)
                )
        for value in values:
            body.addSpacing(3)
            body.addWidget(self._muted(value))
        self.panel.add(details, gap)

    # --- операции -------------------------------------------------------------
    def start_check(self, operation: str) -> None:
        if self.busy:
            return
        self.active_operation = operation
        self.runs[operation] = None
        self.details[operation] = False
        self.states[operation] = "checking"
        self.status_details[operation] = ""
        self.busy_operation = operation
        bundle = self.bundle
        prepare = prepare_manual_upward if operation == "upward" else prepare_manual_downward
        signals = self._new_signals()
        self._run_async(
            lambda: prepare(
                bundle,
                progress=lambda message: signals.progress.emit(operation, message),
            ),
            lambda run: self._prepared(operation, run),
            signals,
        )
        self._refresh_view()

    def _prepared(self, operation: str, run: OperationRun) -> None:
        self.runs[operation] = run
        blocked = run.plan.blocked or (
            run.validation is not None and run.validation.blocked
        )
        no_changes = (
            not run.plan.changes
            if operation == "upward"
            else not isinstance(run, ManualDownwardRun) or not run.changed_deltas
        )
        if blocked:
            state = "blocked"
            detail = self.ui["copy"]["blocked"]
        elif no_changes:
            state = "no_changes"
            detail = self.ui["copy"][f"no_{operation}_changes"]
        else:
            state = "ready"
            detail = self.ui["states"]["ready"]
        self.states[operation] = state
        self.status_details[operation] = detail
        self._refresh_view()

    def ask_to_publish(self, operation: str) -> None:
        run = self.runs[operation]
        if self.busy or run is None or not run.ready_to_publish:
            return
        settings = (
            self.bundle.app.manual_upward
            if operation == "upward"
            else self.bundle.app.manual_downward
        )
        dialog = PublishDialog(self, operation, str(settings["confirmation_phrase"]))
        dialog.exec()
        if dialog.confirmed:
            self._publish(operation, run)

    def _publish(self, operation: str, run: OperationRun) -> None:
        if self.busy:
            return
        self.busy_operation = operation
        self.states[operation] = "publishing"
        self.status_details[operation] = ""
        bundle = self.bundle
        publish = (
            publish_prepared_manual_upward
            if operation == "upward"
            else publish_prepared_manual_downward
        )
        self._run_async(
            lambda: publish(bundle, run, confirmation_digest=run.plan.digest),
            lambda result: self._published(operation, result),
            self._new_signals(),
        )
        self._refresh_view()

    def _published(self, operation: str, run: OperationRun) -> None:
        self.runs[operation] = run
        complete = run.publication is not None and run.publication.complete
        if not complete:
            self.states[operation] = "failed"
            self.status_details[operation] = self.ui["copy"]["blocked"]
            self._refresh_view()
            return
        self.states[operation] = "published"
        self.status_details[operation] = self.ui["copy"][f"{operation}_published"]
        if operation == "upward":
            self.runs["downward"] = None
            self.states["downward"] = "idle"
            self.status_details["downward"] = self.ui["copy"]["downward"]
        self._refresh_view()
        confirmation = self.ui["confirmations"][operation]
        _message(QMessageBox.Icon.Information, confirmation["success_title"],
                 self.ui["copy"][f"{operation}_published"], self, self.ui["actions"]["close"])

    # --- фон → UI ---------------------------------------------------------------
    def _new_signals(self) -> _Signals:
        signals = _Signals(self)
        self._signals.add(signals)
        signals.progress.connect(self._on_progress)
        signals.finished.connect(lambda success, result: self._on_finished(signals, success, result))
        signals.failed.connect(lambda exc: self._on_failed(signals, exc))
        return signals

    def _release(self, signals: _Signals) -> None:
        self._signals.discard(signals)
        signals.deleteLater()

    def _run_async(self, action: Callable[[], Any], success: Callable[[Any], None],
                   signals: _Signals | None = None) -> None:
        self.pool.start(_Worker(action, success, signals or self._new_signals()))

    def _on_progress(self, operation: str, message: str) -> None:
        self.status_details[operation] = self.ui["templates"]["progress"].format(message=message)
        self._refresh_view()

    def _on_finished(self, signals: _Signals, success, result) -> None:
        self._release(signals)
        self.busy_operation = None
        success(result)

    def _on_failed(self, signals: _Signals, exc: BaseException) -> None:
        self._release(signals)
        operation = self.busy_operation
        self.busy_operation = None
        if operation is not None:
            self.states[operation] = "failed"
            self.status_details[operation] = str(exc)
            self.active_operation = operation
        self._refresh_view()
        _message(QMessageBox.Icon.Critical, self.ui["window"]["title"], str(exc), self,
                 self.ui["actions"]["close"])

    # --- прочее -------------------------------------------------------------------
    def toggle_details(self, operation: str) -> None:
        self.details[operation] = not self.details[operation]
        self._refresh_view()

    def open_report(self, operation: str) -> None:
        run = self.runs[operation]
        if run is None:
            return
        path = run.snapshot.run_dir / ("plan.txt" if operation == "upward" else "downward.txt")
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError as exc:
            _message(QMessageBox.Icon.Critical, self.ui["window"]["title"], str(exc), self,
                     self.ui["actions"]["close"])

    def open_rules(self) -> None:
        if not self.busy:
            SourceFoldersDialog(self).exec()

    def rules_saved(self, bundle: ConfigBundle) -> None:
        self.bundle = bundle
        self.ui = bundle.app.manual_ui
        self.runs = {"upward": None, "downward": None}
        self.states = {"upward": "idle", "downward": "idle"}
        self.details = {"upward": False, "downward": False}
        self.status_details = {
            "upward": self.ui["copy"]["rules_saved"],
            "downward": self.ui["copy"]["downward"],
        }
        self.active_operation = None
        self.setWindowTitle(self.ui["window"]["title"])
        self.title_label.setText(self.ui["window"]["title"])
        self.subtitle_label.setText(self.ui["window"]["subtitle"])
        self.rules_button.setText(self.ui["actions"]["rules"])
        self._refresh_view()


RegistryCycleApp = ManualTransferApp


def run_gui(bundle: ConfigBundle | None = None) -> None:
    bundle = bundle or load_config_bundle()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(bundle.app.manual_ui["window"]["title"])
    theme.apply(app, bundle.theme)
    window = ManualTransferApp(bundle)
    window.show()
    app.exec()
