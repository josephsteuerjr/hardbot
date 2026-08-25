"""Single calm GUI with two independent manual registry operations."""

from __future__ import annotations

import copy
import os
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

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


def _short(value: Any, limit: int) -> str:
    text = "—" if value is None or str(value) == "" else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _card(parent: tk.Misc, colors: dict[str, str], padding: int) -> tk.Frame:
    return tk.Frame(
        parent,
        bg=colors["card"],
        highlightbackground=colors["border"],
        highlightthickness=1,
        padx=padding,
        pady=padding,
    )


class _ScrollablePanel(tk.Frame):
    def __init__(self, parent: tk.Misc, colors: dict[str, str]) -> None:
        super().__init__(parent, bg=colors["canvas"])
        self.canvas = tk.Canvas(
            self,
            bg=colors["canvas"],
            highlightthickness=0,
            bd=0,
        )
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(self.canvas, bg=colors["canvas"])
        self.window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self.window, width=event.width),
        )
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _event: tk.Event[tk.Misc]) -> None:
        self.canvas.bind_all(
            "<MouseWheel>",
            lambda event: self.canvas.yview_scroll(int(-event.delta / 120), "units"),
        )

    def _unbind_wheel(self, _event: tk.Event[tk.Misc]) -> None:
        self.canvas.unbind_all("<MouseWheel>")


@dataclass
class _RootControls:
    frame: tk.Frame
    country: tk.StringVar
    path: tk.StringVar


class SourceFoldersDialog(tk.Toplevel):
    """Small config-backed folder picker; no naming expressions or rules."""

    def __init__(self, app: "ManualTransferApp") -> None:
        super().__init__(app)
        self.app = app
        self.bundle = app.bundle
        self.ui = app.ui
        self.colors = app.colors
        self.layout = app.layout
        self.payload = copy.deepcopy(self.bundle.pinned_config("app.json"))
        self.rows: list[_RootControls] = []
        self.title(self.ui["sections"]["rules"])
        self.geometry(f"{self.layout['dialog_width']}x{self.layout['dialog_height']}")
        self.minsize(
            int(self.layout["dialog_minimum_width"]),
            int(self.layout["dialog_minimum_height"]),
        )
        self.configure(bg=self.colors["canvas"])
        self.transient(app)
        self.grab_set()
        self._build()

    def _font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return self.app.font(size, weight)

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        primary: bool = False,
        compact: bool = False,
    ) -> tk.Button:
        background = self.colors["accent"] if primary else self.colors["card"]
        foreground = self.colors["accent_text"] if primary else self.colors["text"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=(
                self.colors["accent_hover"] if primary else self.colors["alt"]
            ),
            activeforeground=foreground,
            relief="flat",
            bd=0,
            highlightthickness=0 if primary else 1,
            highlightbackground=self.colors["border"],
            padx=10 if compact else 14,
            pady=5 if compact else 9,
            cursor="hand2",
            font=self._font(
                self.app.typography["small"] if compact else self.app.typography["body"],
                "bold" if primary else "normal",
            ),
        )

    def _entry(self, parent: tk.Misc, variable: tk.StringVar) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            bg=self.colors["card"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            highlightcolor=self.colors["focus"],
            font=self._font(self.app.typography["body"]),
        )

    def _build(self) -> None:
        padding = int(self.layout["page_padding"])
        header = tk.Frame(self, bg=self.colors["canvas"])
        header.pack(fill="x", padx=padding, pady=(padding, 12))
        tk.Label(
            header,
            text=self.ui["sections"]["rules"],
            bg=self.colors["canvas"],
            fg=self.colors["text"],
            font=self._font(self.app.typography["heading"], "bold"),
        ).pack(anchor="w")
        tk.Label(
            header,
            text=self.ui["copy"]["rules"],
            bg=self.colors["canvas"],
            fg=self.colors["text_muted"],
            justify="left",
            wraplength=int(self.layout["dialog_width"]) - 2 * padding,
            font=self._font(self.app.typography["body"]),
        ).pack(anchor="w", pady=(5, 0))

        panel = _ScrollablePanel(self, self.colors)
        panel.pack(fill="both", expand=True, padx=padding)
        self.rows_frame = tk.Frame(panel.content, bg=self.colors["canvas"])
        self.rows_frame.pack(fill="x")
        for source in self.payload["paths"]["portfolio_roots"]:
            self._add_row(source)
        self._button(
            panel.content,
            self.ui["actions"]["add_root"],
            lambda: self._add_row(None),
            compact=True,
        ).pack(anchor="w", pady=(2, 16))

        footer = tk.Frame(self, bg=self.colors["canvas"])
        footer.pack(fill="x", padx=padding, pady=padding)
        self._button(footer, self.ui["actions"]["cancel"], self.destroy).pack(side="right")
        self._button(
            footer,
            self.ui["actions"]["save"],
            self._save,
            primary=True,
        ).pack(side="right", padx=(0, 8))

    def _add_row(self, source: dict[str, Any] | None) -> None:
        source = source or {}
        frame = _card(self.rows_frame, self.colors, int(self.layout["card_padding"]))
        frame.pack(fill="x", pady=(0, 10))
        controls = _RootControls(
            frame,
            tk.StringVar(value=str(source.get("country", ""))),
            tk.StringVar(value=str(source.get("path", ""))),
        )
        self.rows.append(controls)
        top = tk.Frame(frame, bg=self.colors["card"])
        top.pack(fill="x")
        tk.Label(
            top,
            text=self.ui["sections"]["folders"],
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=self._font(self.app.typography["body"], "bold"),
        ).pack(side="left")
        self._button(
            top,
            self.ui["actions"]["remove"],
            lambda: self._remove_row(controls),
            compact=True,
        ).pack(side="right")
        form = tk.Frame(frame, bg=self.colors["card"])
        form.pack(fill="x", pady=(10, 0))
        tk.Label(
            form,
            text=self.ui["rules"]["country"],
            bg=self.colors["card"],
            fg=self.colors["text_muted"],
            font=self._font(self.app.typography["small"], "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            form,
            text=self.ui["rules"]["root_path"],
            bg=self.colors["card"],
            fg=self.colors["text_muted"],
            font=self._font(self.app.typography["small"], "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))
        self._entry(form, controls.country).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self._entry(form, controls.path).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(12, 8),
            pady=(4, 0),
        )
        self._button(
            form,
            self.ui["actions"]["browse"],
            lambda: self._browse(controls),
            compact=True,
        ).grid(row=1, column=2, pady=(4, 0))
        form.grid_columnconfigure(1, weight=1)
        tk.Label(
            frame,
            text=self.ui["rules"]["root_hint"],
            bg=self.colors["card"],
            fg=self.colors["text_muted"],
            justify="left",
            wraplength=int(self.layout["dialog_width"]) - 120,
            font=self._font(self.app.typography["small"]),
        ).pack(anchor="w", pady=(9, 0))

    def _browse(self, controls: _RootControls) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            initialdir=controls.path.get().strip() or None,
            mustexist=True,
        )
        if selected:
            controls.path.set(selected)

    def _remove_row(self, controls: _RootControls) -> None:
        if controls in self.rows:
            self.rows.remove(controls)
        controls.frame.destroy()

    def _candidate(self) -> dict[str, Any]:
        if not self.rows:
            raise ConfigurationError(self.ui["rules"]["empty_root"])
        roots = []
        for controls in self.rows:
            country = controls.country.get().strip()
            path = controls.path.get().strip()
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
            messagebox.showerror(self.ui["sections"]["rules"], str(exc), parent=self)
            return
        self.app.rules_saved(bundle)
        self.destroy()


class ManualTransferApp(tk.Tk):
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
        self.layout = bundle.theme["layout"]
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
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.action_buttons: list[tk.Button] = []
        self._build_window()
        self._build_shell()
        self._refresh_view()
        self.after(80, self._poll_events)

    @property
    def busy(self) -> bool:
        return self.busy_operation is not None

    def font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        family = str(self.typography.get("family") or self.typography["fallback"])
        return family, int(size), weight

    def _build_window(self) -> None:
        self.title(self.ui["window"]["title"])
        self.geometry(f"{self.layout['window_width']}x{self.layout['window_height']}")
        self.minsize(int(self.layout["minimum_width"]), int(self.layout["minimum_height"]))
        self.configure(bg=self.colors["canvas"])
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Fieldwork.Horizontal.TProgressbar",
            troughcolor=self.colors["alt"],
            background=self.colors["accent"],
            bordercolor=self.colors["alt"],
            lightcolor=self.colors["accent"],
            darkcolor=self.colors["accent"],
        )

    def _build_shell(self) -> None:
        padding = int(self.layout["page_padding"])
        header = tk.Frame(self, bg=self.colors["canvas"])
        header.pack(fill="x", padx=padding, pady=(padding, 14))
        title = tk.Frame(header, bg=self.colors["canvas"])
        title.pack(side="left", fill="x", expand=True)
        self.title_label = tk.Label(
            title,
            text=self.ui["window"]["title"],
            bg=self.colors["canvas"],
            fg=self.colors["text"],
            font=self.font(self.typography["title"], "bold"),
        )
        self.title_label.pack(anchor="w")
        self.subtitle_label = tk.Label(
            title,
            text=self.ui["window"]["subtitle"],
            bg=self.colors["canvas"],
            fg=self.colors["text_muted"],
            font=self.font(self.typography["body"]),
        )
        self.subtitle_label.pack(anchor="w", pady=(3, 0))
        self.rules_button = self._button(
            header,
            self.ui["actions"]["rules"],
            self.open_rules,
            compact=True,
        )
        self.rules_button.pack(side="right", padx=(14, 0), pady=(4, 0))
        self.panel = _ScrollablePanel(self, self.colors)
        self.panel.pack(fill="both", expand=True, padx=padding, pady=(0, padding))
        self.content = tk.Frame(self.panel.content, bg=self.colors["canvas"])
        self.content.pack(fill="both", expand=True)
        self.panel.content.bind("<Configure>", self._center_content)

    def _center_content(self, event: tk.Event[tk.Misc]) -> None:
        padding = max(0, (event.width - int(self.layout["content_width"])) // 2)
        self.content.pack_configure(padx=padding)

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        *,
        primary: bool = False,
        compact: bool = False,
    ) -> tk.Button:
        background = self.colors["accent"] if primary else self.colors["card"]
        foreground = self.colors["accent_text"] if primary else self.colors["text"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=(self.colors["accent_hover"] if primary else self.colors["alt"]),
            activeforeground=foreground,
            disabledforeground=self.colors["text_muted"],
            relief="flat",
            bd=0,
            highlightthickness=0 if primary else 1,
            highlightbackground=self.colors["border"],
            padx=10 if compact else 15,
            pady=5 if compact else 10,
            cursor="hand2",
            font=self.font(
                self.typography["small"] if compact else self.typography["body"],
                "bold" if primary else "normal",
            ),
        )

    def _action(self, *args: Any, **kwargs: Any) -> tk.Button:
        button = self._button(*args, **kwargs)
        self.action_buttons.append(button)
        return button

    def _heading(self, parent: tk.Misc, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=self.font(self.typography["heading"], "bold"),
        )

    def _muted(self, parent: tk.Misc, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=self.colors["card"],
            fg=self.colors["text_muted"],
            justify="left",
            anchor="w",
            wraplength=int(self.layout["content_wrap"]),
            font=self.font(self.typography["body"]),
        )

    def _refresh_view(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self.action_buttons = []
        for operation in ("upward", "downward"):
            self._render_operation(operation)
            if self.active_operation == operation and self.runs[operation] is not None:
                self._render_result(operation, self.runs[operation])
        state = "disabled" if self.busy else "normal"
        self.rules_button.configure(state=state)
        for button in self.action_buttons:
            button.configure(state=state)

    def _render_operation(self, operation: str) -> None:
        gap = int(self.layout["gap"])
        card = _card(self.content, self.colors, int(self.layout["card_padding"]))
        card.pack(fill="x", pady=(0, gap))
        self._heading(card, self.ui["sections"][operation]).pack(anchor="w")
        self._muted(card, self.ui["copy"][operation]).pack(anchor="w", pady=(6, 0))
        if operation == "upward":
            self._muted(
                card,
                self.ui["templates"]["master"].format(name=self.bundle.master_path().name),
            ).pack(anchor="w", pady=(9, 0))
        status = tk.Frame(card, bg=self.colors["card"])
        status.pack(fill="x", pady=(12, 0))
        state = self.states[operation]
        tk.Label(
            status,
            text="●",
            bg=self.colors["card"],
            fg=self.colors[self._STATE_COLORS[state]],
            font=self.font(self.typography["body"]),
        ).pack(side="left")
        tk.Label(
            status,
            text=self.ui["states"][state],
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=self.font(self.typography["body"], "bold"),
        ).pack(side="left", padx=(6, 0))
        self._muted(card, self.status_details[operation]).pack(anchor="w", pady=(4, 0))
        if self.busy_operation == operation:
            progress = ttk.Progressbar(
                card,
                style="Fieldwork.Horizontal.TProgressbar",
                mode="indeterminate",
            )
            progress.pack(fill="x", pady=(12, 0))
            progress.start(int(self.layout["progress_interval_ms"]))
        elif not self.busy:
            retry = self.runs[operation] is not None
            action_key = f"retry_{operation}" if retry else f"check_{operation}"
            self._action(
                card,
                self.ui["actions"][action_key],
                lambda selected=operation: self.start_check(selected),
                primary=True,
            ).pack(anchor="w", pady=(13, 0))

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

    def _metric(self, parent: tk.Misc, column: int, text: str) -> None:
        metric = tk.Frame(parent, bg=self.colors["alt"], padx=10, pady=9)
        metric.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 7, 0))
        tk.Label(
            metric,
            text=text,
            bg=self.colors["alt"],
            fg=self.colors["text"],
            anchor="w",
            justify="left",
            wraplength=int(self.layout["metric_wrap"]),
            font=self.font(self.typography["body"], "bold"),
        ).pack(anchor="w")
        parent.grid_columnconfigure(column, weight=1)

    def _render_result(self, operation: str, run: OperationRun) -> None:
        gap = int(self.layout["gap"])
        padding = int(self.layout["card_padding"])
        issues = self._visible_issues(run)
        summary = _card(self.content, self.colors, padding)
        summary.pack(fill="x", pady=(0, gap))
        self._heading(summary, self.ui["sections"]["summary"]).pack(anchor="w")
        metrics = tk.Frame(summary, bg=self.colors["card"])
        metrics.pack(fill="x", pady=(11, 0))
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
        for column, value in enumerate(values):
            self._metric(metrics, column, value)
        self._muted(summary, self.ui["copy"]["payment_scope"]).pack(anchor="w", pady=(11, 0))

        if issues:
            issue_card = _card(self.content, self.colors, padding)
            issue_card.pack(fill="x", pady=(0, gap))
            self._heading(issue_card, self.ui["sections"]["issues"]).pack(anchor="w")
            for issue in issues[: int(self.ui["limits"]["issue_preview"])]:
                self._issue_row(issue_card, issue)

        if operation == "upward" and run.plan.changes:
            changes = _card(self.content, self.colors, padding)
            changes.pack(fill="x", pady=(0, gap))
            self._heading(changes, self.ui["sections"]["changes"]).pack(anchor="w")
            for change in run.plan.changes[: int(self.ui["limits"]["change_preview"])]:
                self._change_row(
                    changes,
                    f"{change.contract_id} · {change.title}",
                    f"{_short(change.before, self.ui['limits']['value_preview_chars'])}  →  "
                    f"{_short(change.after, self.ui['limits']['value_preview_chars'])}",
                )
        elif operation == "downward":
            assert isinstance(run, ManualDownwardRun)
            if run.changed_deltas:
                changes = _card(self.content, self.colors, padding)
                changes.pack(fill="x", pady=(0, gap))
                self._heading(changes, self.ui["sections"]["changes"]).pack(anchor="w")
                for delta in run.changed_deltas[: int(self.ui["limits"]["change_preview"])]:
                    self._delta_row(changes, delta)

        actions = _card(self.content, self.colors, padding)
        actions.pack(fill="x", pady=(0, gap))
        can_publish = run.ready_to_publish and (
            operation == "downward" or bool(run.plan.changes)
        )
        if can_publish and not self.busy:
            self._action(
                actions,
                self.ui["actions"][f"publish_{operation}"],
                lambda selected=operation: self.ask_to_publish(selected),
                primary=True,
            ).pack(anchor="w")
        self._action(
            actions,
            self.ui["actions"]["open_report"],
            lambda selected=operation: self.open_report(selected),
        ).pack(anchor="w", pady=((8 if can_publish else 0), 0))
        self._action(
            actions,
            self.ui["actions"]["details_hide"] if self.details[operation] else self.ui["actions"]["details"],
            lambda selected=operation: self.toggle_details(selected),
        ).pack(anchor="w", pady=(8, 0))
        if self.details[operation]:
            self._render_details(run, gap, padding, operation)

    def _issue_row(self, parent: tk.Misc, issue: Issue) -> None:
        color = self.colors["danger" if issue.severity == "error" else "warning"]
        row = tk.Frame(parent, bg=self.colors["card"])
        row.pack(fill="x", pady=(9, 0))
        tk.Frame(row, bg=color, width=3).pack(side="left", fill="y")
        tk.Label(
            row,
            text=issue.message,
            bg=self.colors["card"],
            fg=self.colors["text"],
            anchor="w",
            justify="left",
            wraplength=int(self.layout["issue_wrap"]),
            font=self.font(self.typography["body"]),
        ).pack(side="left", fill="x", expand=True, padx=(9, 0))

    def _change_row(self, parent: tk.Misc, title: str, detail: str) -> None:
        row = tk.Frame(parent, bg=self.colors["card"], pady=8)
        row.pack(fill="x")
        tk.Label(
            row,
            text=title,
            bg=self.colors["card"],
            fg=self.colors["text"],
            anchor="w",
            font=self.font(self.typography["body"], "bold"),
        ).pack(anchor="w")
        tk.Label(
            row,
            text=detail,
            bg=self.colors["card"],
            fg=self.colors["text_muted"],
            anchor="w",
            font=self.font(self.typography["small"]),
        ).pack(anchor="w", pady=(2, 0))
        tk.Frame(parent, bg=self.colors["alt"], height=1).pack(fill="x")

    def _delta_row(self, parent: tk.Misc, delta: ProjectionDelta) -> None:
        self._change_row(
            parent,
            f"{delta.employee} · {delta.path.name}",
            f"Строк: {delta.rows_before} → {delta.rows_after} · ячеек: {delta.changed_cells}",
        )

    def _render_details(self, run: OperationRun, gap: int, padding: int, operation: str) -> None:
        details = _card(self.content, self.colors, padding)
        details.pack(fill="x", pady=(0, gap))
        self._heading(details, self.ui["sections"]["details"]).pack(anchor="w")
        self._muted(details, self.ui["copy"]["technical"]).pack(anchor="w", pady=(5, 8))
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
            self._muted(details, value).pack(anchor="w", pady=(3, 0))

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
        self._run_async(
            lambda: prepare(
                bundle,
                progress=lambda message: self.events.put(("progress", (operation, message))),
            ),
            lambda run: self._prepared(operation, run),
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
        copy_block = self.ui["confirmations"][operation]
        settings = (
            self.bundle.app.manual_upward
            if operation == "upward"
            else self.bundle.app.manual_downward
        )
        phrase = str(settings["confirmation_phrase"])
        dialog = tk.Toplevel(self)
        dialog.title(copy_block["title"])
        dialog.configure(bg=self.colors["canvas"])
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.geometry(f"{self.layout['modal_width']}x{self.layout['modal_height']}")
        dialog.grab_set()
        body = _card(dialog, self.colors, int(self.layout["card_padding"]))
        body.pack(
            fill="both",
            expand=True,
            padx=int(self.layout["modal_padding"]),
            pady=int(self.layout["modal_padding"]),
        )
        self._heading(body, copy_block["title"]).pack(anchor="w")
        self._muted(body, copy_block["body"]).pack(anchor="w", pady=(7, 13))
        tk.Label(
            body,
            text=copy_block["input"].format(phrase=phrase),
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=self.font(self.typography["body"], "bold"),
        ).pack(anchor="w")
        answer = tk.StringVar()
        entry = tk.Entry(
            body,
            textvariable=answer,
            bg=self.colors["card"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            highlightcolor=self.colors["focus"],
            font=self.font(self.typography["body"]),
        )
        entry.pack(fill="x", pady=(5, 0))
        error = tk.Label(
            body,
            text="",
            bg=self.colors["card"],
            fg=self.colors["danger"],
            font=self.font(self.typography["small"]),
        )
        error.pack(anchor="w", pady=(5, 0))
        actions = tk.Frame(body, bg=self.colors["card"])
        actions.pack(fill="x", pady=(15, 0))

        def confirm() -> None:
            if answer.get().strip() != phrase:
                error.configure(text=copy_block["invalid"])
                return
            dialog.destroy()
            self._publish(operation, run)

        self._button(actions, self.ui["actions"]["cancel"], dialog.destroy).pack(side="right")
        self._button(
            actions,
            self.ui["actions"][f"publish_{operation}"],
            confirm,
            primary=True,
        ).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _event: confirm())
        entry.focus_set()

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
        messagebox.showinfo(
            confirmation["success_title"],
            self.ui["copy"][f"{operation}_published"],
            parent=self,
        )

    def _run_async(self, action: Callable[[], Any], success: Callable[[Any], None]) -> None:
        def worker() -> None:
            try:
                result = action()
            except Exception as exc:  # noqa: BLE001 — shown on Tk thread
                self.events.put(("failed", exc))
            else:
                self.events.put(("finished", (success, result)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    operation, message = payload
                    self.status_details[operation] = self.ui["templates"]["progress"].format(
                        message=message
                    )
                    self._refresh_view()
                    continue
                operation = self.busy_operation
                self.busy_operation = None
                if kind == "finished":
                    success, result = payload
                    success(result)
                    continue
                if operation is not None:
                    self.states[operation] = "failed"
                    self.status_details[operation] = str(payload)
                    self.active_operation = operation
                self._refresh_view()
                messagebox.showerror(self.ui["window"]["title"], str(payload), parent=self)
        except queue.Empty:
            pass
        self.after(80, self._poll_events)

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
            messagebox.showerror(self.ui["window"]["title"], str(exc), parent=self)

    def open_rules(self) -> None:
        if not self.busy:
            SourceFoldersDialog(self)

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
        self.title(self.ui["window"]["title"])
        self.title_label.configure(text=self.ui["window"]["title"])
        self.subtitle_label.configure(text=self.ui["window"]["subtitle"])
        self.rules_button.configure(text=self.ui["actions"]["rules"])
        self._refresh_view()


RegistryCycleApp = ManualTransferApp


def run_gui(bundle: ConfigBundle | None = None) -> None:
    ManualTransferApp(bundle or load_config_bundle()).mainloop()
