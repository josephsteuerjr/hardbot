"""Graphite GUI: один операторский интерфейс над тем же безопасным ядром."""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable

from .config import ConfigBundle, load_config_bundle
from .errors import RegistryCycleError
from .force_close import DelegatedForceCloser
from .notifications import OwnerNotifier
from .publishing import publish_validated
from .scheduler import install_task, remove_task, task_status
from .service import CycleService, PreparedRun
from .staging import StageSet, build_staging
from .validation import ValidationReport, validate_staging


@dataclass(frozen=True, slots=True)
class _NavItem:
    key: str
    label: str
    eyebrow: str


class RegistryCycleApp(tk.Tk):
    NAV = (
        _NavItem("dashboard", "Обзор", "01"),
        _NavItem("analysis", "Изменения", "02"),
        _NavItem("contours", "Контуры", "03"),
        _NavItem("schedule", "Расписание", "04"),
        _NavItem("logs", "Журнал", "05"),
    )

    def __init__(self, bundle: ConfigBundle):
        super().__init__()
        self.bundle = bundle
        self.service = CycleService(bundle)
        self.prepared: PreparedRun | None = None
        self.stage: StageSet | None = None
        self.validation: ValidationReport | None = None
        self.busy = False
        self.current_page = "dashboard"
        self._events: queue.Queue[tuple[str, Any, Any]] = queue.Queue()
        self._nav_buttons: dict[str, tk.Button] = {}
        self._status_var = tk.StringVar(value="Готов к dry-run")
        self._progress_var = tk.StringVar(value="")
        self.busy_mode = tk.StringVar(
            value=str(
                bundle.app.safety.get("busy_file_policy", "block")
            )
        )
        self._build_window()
        self._build_styles()
        self._build_shell()
        self.show_page("dashboard")
        self.after(120, self._poll_events)
        self.bind("<F5>", lambda _event: self.prepare_plan())
        self.bind("<Control-l>", lambda _event: self.show_page("logs"))

    @property
    def colors(self) -> dict[str, str]:
        return self.bundle.theme["colors"]

    @property
    def typography(self) -> dict[str, Any]:
        return self.bundle.theme["typography"]

    def font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        family = str(
            self.typography.get("family")
            or self.typography.get("fallback")
            or "Segoe UI"
        )
        return family, size, weight

    def _build_window(self) -> None:
        layout = self.bundle.theme["layout"]
        self.title(f"{self.bundle.app.product_name} · {self.bundle.app.environment_name}")
        self.geometry(f"{layout['window_width']}x{layout['window_height']}")
        self.minsize(layout["minimum_width"], layout["minimum_height"])
        self.configure(bg=self.colors["canvas"])

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Graphite.Treeview",
            background=self.colors["surface"],
            fieldbackground=self.colors["surface"],
            foreground=self.colors["text"],
            borderwidth=0,
            rowheight=34,
            font=self.font(self.typography["body"]),
        )
        style.map(
            "Graphite.Treeview",
            background=[("selected", self.colors["surface_alt"])],
            foreground=[("selected", self.colors["text"])],
        )
        style.configure(
            "Graphite.Treeview.Heading",
            background=self.colors["surface_alt"],
            foreground=self.colors["text_muted"],
            borderwidth=0,
            relief="flat",
            font=self.font(self.typography["small"], "bold"),
            padding=(10, 10),
        )
        style.map("Graphite.Treeview.Heading", background=[("active", self.colors["border"])])
        style.configure(
            "Graphite.Vertical.TScrollbar",
            background=self.colors["surface_alt"],
            troughcolor=self.colors["surface"],
            bordercolor=self.colors["surface"],
            arrowcolor=self.colors["text_muted"],
        )

    def _build_shell(self) -> None:
        sidebar_width = self.bundle.theme["layout"]["sidebar_width"]
        self.sidebar = tk.Frame(
            self,
            bg=self.colors["sidebar"],
            width=sidebar_width,
            highlightbackground=self.colors["border_soft"],
            highlightthickness=1,
        )
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=self.colors["sidebar"])
        brand.pack(fill="x", padx=24, pady=(28, 34))
        tk.Label(
            brand,
            text="КОНТУР",
            bg=self.colors["sidebar"],
            fg=self.colors["accent"],
            font=self.font(10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="Реестры",
            bg=self.colors["sidebar"],
            fg=self.colors["text"],
            font=self.font(21, "bold"),
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            brand,
            text="СНГ · безопасный цикл",
            bg=self.colors["sidebar"],
            fg=self.colors["text_dim"],
            font=self.font(9),
        ).pack(anchor="w", pady=(4, 0))

        nav = tk.Frame(self.sidebar, bg=self.colors["sidebar"])
        nav.pack(fill="x", padx=12)
        for item in self.NAV:
            button = tk.Button(
                nav,
                text=f"{item.eyebrow}   {item.label}",
                anchor="w",
                command=lambda key=item.key: self.show_page(key),
                bg=self.colors["sidebar"],
                fg=self.colors["text_muted"],
                activebackground=self.colors["surface_alt"],
                activeforeground=self.colors["text"],
                relief="flat",
                borderwidth=0,
                padx=14,
                pady=12,
                cursor="hand2",
                font=self.font(10),
            )
            button.pack(fill="x", pady=2)
            self._nav_buttons[item.key] = button

        footer = tk.Frame(self.sidebar, bg=self.colors["sidebar"])
        footer.pack(side="bottom", fill="x", padx=24, pady=24)
        tk.Label(
            footer,
            text="TECH DELIVERY",
            bg=self.colors["sidebar"],
            fg=self.colors["text_dim"],
            font=self.font(8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            footer,
            text="Только владельцу",
            bg=self.colors["sidebar"],
            fg=self.colors["success"],
            font=self.font(9),
        ).pack(anchor="w", pady=(4, 0))

        right = tk.Frame(self, bg=self.colors["canvas"])
        right.pack(side="left", fill="both", expand=True)
        self.page_host = tk.Frame(right, bg=self.colors["canvas"])
        self.page_host.pack(fill="both", expand=True)
        status = tk.Frame(
            right,
            bg=self.colors["sidebar"],
            height=38,
            highlightbackground=self.colors["border_soft"],
            highlightthickness=1,
        )
        status.pack(fill="x", side="bottom")
        tk.Label(
            status,
            textvariable=self._status_var,
            bg=self.colors["sidebar"],
            fg=self.colors["text_muted"],
            font=self.font(9),
        ).pack(side="left", padx=20, pady=9)
        tk.Label(
            status,
            textvariable=self._progress_var,
            bg=self.colors["sidebar"],
            fg=self.colors["accent"],
            font=self.font(9, "bold"),
        ).pack(side="right", padx=20, pady=9)

    def _clear_page(self) -> tk.Frame:
        for child in self.page_host.winfo_children():
            child.destroy()
        padding = self.bundle.theme["layout"]["page_padding"]
        page = tk.Frame(self.page_host, bg=self.colors["canvas"])
        page.pack(fill="both", expand=True, padx=padding, pady=padding)
        return page

    def _heading(self, parent: tk.Widget, title: str, subtitle: str) -> tk.Frame:
        frame = tk.Frame(parent, bg=self.colors["canvas"])
        frame.pack(fill="x", pady=(0, 24))
        tk.Label(
            frame,
            text=title,
            bg=self.colors["canvas"],
            fg=self.colors["text"],
            font=self.font(self.typography["title"], "bold"),
        ).pack(anchor="w")
        tk.Label(
            frame,
            text=subtitle,
            bg=self.colors["canvas"],
            fg=self.colors["text_muted"],
            font=self.font(10),
        ).pack(anchor="w", pady=(5, 0))
        return frame

    def _card(self, parent: tk.Widget, *, padx: int = 20, pady: int = 18) -> tk.Frame:
        outer = tk.Frame(
            parent,
            bg=self.colors["surface"],
            highlightbackground=self.colors["border_soft"],
            highlightthickness=1,
        )
        inner = tk.Frame(outer, bg=self.colors["surface"])
        inner.pack(fill="both", expand=True, padx=padx, pady=pady)
        outer.inner = inner  # type: ignore[attr-defined]
        return outer

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        *,
        primary: bool = False,
        danger: bool = False,
        width: int | None = None,
    ) -> tk.Button:
        background = (
            self.colors["danger"]
            if danger
            else self.colors["accent"]
            if primary
            else self.colors["surface_alt"]
        )
        foreground = (
            self.colors["accent_text"]
            if primary and not danger
            else self.colors["canvas"]
            if danger
            else self.colors["text"]
        )
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=(
                self.colors["accent_hover"]
                if primary
                else self.colors["border"]
            ),
            activeforeground=foreground,
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=10,
            width=width,
            cursor="hand2",
            font=self.font(9, "bold"),
        )

    def _pill(self, parent: tk.Widget, text: str, tone: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text.upper(),
            bg=self.colors["surface_alt"],
            fg=self.colors[tone],
            padx=10,
            pady=5,
            font=self.font(8, "bold"),
        )

    def show_page(self, key: str) -> None:
        self.current_page = key
        for item_key, button in self._nav_buttons.items():
            active = item_key == key
            button.configure(
                bg=self.colors["surface_alt"] if active else self.colors["sidebar"],
                fg=self.colors["text"] if active else self.colors["text_muted"],
            )
        {
            "dashboard": self._page_dashboard,
            "analysis": self._page_analysis,
            "contours": self._page_contours,
            "schedule": self._page_schedule,
            "logs": self._page_logs,
        }[key]()

    def _metric(
        self,
        parent: tk.Widget,
        label: str,
        value: str,
        note: str,
        tone: str = "text",
    ) -> None:
        card = self._card(parent)
        card.pack(side="left", fill="both", expand=True, padx=(0, 10))
        tk.Label(
            card.inner,
            text=label.upper(),
            bg=self.colors["surface"],
            fg=self.colors["text_dim"],
            font=self.font(8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            card.inner,
            text=value,
            bg=self.colors["surface"],
            fg=self.colors[tone],
            font=self.font(self.typography["metric"], "bold"),
        ).pack(anchor="w", pady=(8, 3))
        tk.Label(
            card.inner,
            text=note,
            bg=self.colors["surface"],
            fg=self.colors["text_muted"],
            font=self.font(8),
        ).pack(anchor="w")

    def _page_dashboard(self) -> None:
        page = self._clear_page()
        header = self._heading(
            page,
            "Цикл реестров",
            "Snapshot → diff → staging → validation → атомарная публикация",
        )
        actions = tk.Frame(header, bg=self.colors["canvas"])
        actions.pack(side="right", anchor="e")
        self._button(
            actions,
            "Снять snapshot и проверить",
            self.prepare_plan,
            primary=True,
        ).pack(side="left", padx=(0, 8))
        self._button(
            actions,
            "Manifest",
            self.scan_manifest,
        ).pack(side="left")

        plan = self.prepared.plan if self.prepared else None
        metrics = tk.Frame(page, bg=self.colors["canvas"])
        metrics.pack(fill="x", pady=(0, 18))
        self._metric(
            metrics,
            "Статус",
            "BLOCK" if plan and plan.blocked else "READY" if plan else "—",
            "fail-closed до validation",
            "danger" if plan and plan.blocked else "success",
        )
        self._metric(
            metrics,
            "Diff мастера",
            f"{len(plan.changes):,}".replace(",", " ") if plan else "—",
            "только разрешённые поля",
            "accent",
        )
        self._metric(
            metrics,
            "Портфели",
            str(len(plan.projections)) if plan else "—",
            "HARD + SOFT",
        )
        error_count = (
            sum(issue.severity == "error" for issue in plan.issues)
            if plan
            else 0
        )
        self._metric(
            metrics,
            "Блокеры",
            str(error_count) if plan else "—",
            "требуют решения",
            "danger" if error_count else "success",
        )

        flow = self._card(page)
        flow.pack(fill="x", pady=(0, 18))
        top = tk.Frame(flow.inner, bg=self.colors["surface"])
        top.pack(fill="x")
        tk.Label(
            top,
            text="Шлюзы безопасности",
            bg=self.colors["surface"],
            fg=self.colors["text"],
            font=self.font(14, "bold"),
        ).pack(side="left")
        self._pill(
            top,
            "read-only live",
            "info",
        ).pack(side="right")
        stages = (
            ("01", "Стабильные копии", self.prepared is not None),
            ("02", "План и baseline", plan is not None and not plan.blocked),
            ("03", "Excel staging", self.stage is not None),
            (
                "04",
                "Нулевой forbidden diff",
                self.validation is not None and not self.validation.blocked,
            ),
        )
        row = tk.Frame(flow.inner, bg=self.colors["surface"])
        row.pack(fill="x", pady=(22, 4))
        for number, label, done in stages:
            cell = tk.Frame(row, bg=self.colors["surface"])
            cell.pack(side="left", expand=True, fill="x")
            tk.Label(
                cell,
                text=number,
                bg=self.colors["surface"],
                fg=self.colors["success"] if done else self.colors["text_dim"],
                font=self.font(9, "bold"),
            ).pack(anchor="w")
            tk.Label(
                cell,
                text=label,
                bg=self.colors["surface"],
                fg=self.colors["text"] if done else self.colors["text_muted"],
                font=self.font(9),
            ).pack(anchor="w", pady=(4, 0))

        controls = self._card(page)
        controls.pack(fill="x")
        tk.Label(
            controls.inner,
            text="Операторский проход",
            bg=self.colors["surface"],
            fg=self.colors["text"],
            font=self.font(14, "bold"),
        ).pack(anchor="w")
        tk.Label(
            controls.inner,
            text=(
                "Staging и публикация доступны только для плана без ошибок. "
                "Перед replace потребуется digest текущего плана."
            ),
            wraplength=780,
            justify="left",
            bg=self.colors["surface"],
            fg=self.colors["text_muted"],
            font=self.font(9),
        ).pack(anchor="w", pady=(7, 16))
        mode_row = tk.Frame(controls.inner, bg=self.colors["surface"])
        mode_row.pack(fill="x", pady=(0, 16))
        tk.Label(
            mode_row,
            text="ЗАНЯТЫЕ ФАЙЛЫ",
            bg=self.colors["surface"],
            fg=self.colors["text_dim"],
            font=self.font(8, "bold"),
        ).pack(side="left", padx=(0, 14))
        force_reason = DelegatedForceCloser(
            self.bundle
        ).unavailable_reason
        labels = {
            "block": "BLOCK",
            "sidecar": "SIDECAR",
            "force_close": "FORCE CLOSE",
        }
        for mode, label in labels.items():
            button = tk.Radiobutton(
                mode_row,
                text=label,
                value=mode,
                variable=self.busy_mode,
                indicatoron=False,
                bg=self.colors["surface_alt"],
                fg=self.colors["text_muted"],
                selectcolor=self.colors["accent"],
                activebackground=self.colors["border"],
                activeforeground=self.colors["text"],
                disabledforeground=self.colors["text_dim"],
                relief="flat",
                borderwidth=0,
                padx=12,
                pady=7,
                font=self.font(8, "bold"),
                state=(
                    "disabled"
                    if mode == "force_close" and force_reason
                    else "normal"
                ),
            )
            button.pack(side="left", padx=(0, 6))
        self._button(
            mode_row,
            "Сохранить режим",
            self.save_busy_mode_config,
        ).pack(side="right")
        if force_reason:
            tk.Label(
                controls.inner,
                text=f"Force close недоступен: {force_reason}.",
                bg=self.colors["surface"],
                fg=self.colors["warning"],
                font=self.font(8),
            ).pack(anchor="w", pady=(0, 14))
        buttons = tk.Frame(controls.inner, bg=self.colors["surface"])
        buttons.pack(fill="x")
        self._button(buttons, "Собрать staging", self.build_stage).pack(
            side="left", padx=(0, 8)
        )
        self._button(buttons, "Валидировать", self.validate_stage).pack(
            side="left", padx=(0, 8)
        )
        self._button(
            buttons,
            "Опубликовать",
            self.publish_stage,
            danger=True,
        ).pack(side="right")

    def _tree(
        self,
        parent: tk.Widget,
        columns: tuple[str, ...],
        headings: tuple[str, ...],
        widths: tuple[int, ...],
    ) -> ttk.Treeview:
        wrap = tk.Frame(parent, bg=self.colors["surface"])
        wrap.pack(fill="both", expand=True)
        tree = ttk.Treeview(
            wrap,
            columns=columns,
            show="headings",
            style="Graphite.Treeview",
        )
        for name, heading, width in zip(columns, headings, widths, strict=True):
            tree.heading(name, text=heading)
            tree.column(name, width=width, minwidth=60, stretch=True)
        scroll = ttk.Scrollbar(
            wrap,
            command=tree.yview,
            style="Graphite.Vertical.TScrollbar",
        )
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def _page_analysis(self) -> None:
        page = self._clear_page()
        self._heading(
            page,
            "Изменения и блокеры",
            "Поклеточный diff без молчаливых разрешений конфликтов",
        )
        plan = self.prepared.plan if self.prepared else None
        split = tk.PanedWindow(
            page,
            orient="vertical",
            sashwidth=6,
            bg=self.colors["canvas"],
            bd=0,
        )
        split.pack(fill="both", expand=True)
        issues_card = self._card(split, padx=0, pady=0)
        changes_card = self._card(split, padx=0, pady=0)
        split.add(issues_card, minsize=210)
        split.add(changes_card, minsize=250)
        issues = self._tree(
            issues_card.inner,
            ("severity", "code", "message"),
            ("УРОВЕНЬ", "КОД", "СИГНАЛ"),
            (90, 220, 620),
        )
        changes = self._tree(
            changes_card.inner,
            ("contract", "field", "before", "after", "source"),
            ("КД", "ПОЛЕ", "БЫЛО", "СТАНЕТ", "ИСТОЧНИК"),
            (100, 210, 180, 180, 170),
        )
        if plan is None:
            issues.insert("", "end", values=("INFO", "no_plan", "Сначала построй dry-run"))
            return
        for issue in plan.issues:
            issues.insert(
                "",
                "end",
                values=(issue.severity.upper(), issue.code, issue.message),
                tags=(issue.severity,),
            )
        issues.tag_configure("error", foreground=self.colors["danger"])
        issues.tag_configure("warning", foreground=self.colors["warning"])
        issues.tag_configure("info", foreground=self.colors["info"])
        for change in plan.changes[:5000]:
            changes.insert(
                "",
                "end",
                values=(
                    change.contract_id,
                    change.title,
                    self._short(change.before),
                    self._short(change.after),
                    change.source_path.name,
                ),
            )

    def _page_contours(self) -> None:
        page = self._clear_page()
        self._heading(
            page,
            "Контуры",
            "Состав берётся из app.json; отключённые города видны, но не участвуют",
        )
        card = self._card(page, padx=0, pady=0)
        card.pack(fill="both", expand=True)
        tree = self._tree(
            card.inner,
            ("country", "group", "profile", "enabled", "required", "files"),
            ("СТРАНА", "ГОРОД / ГРУППА", "ПРОФИЛЬ", "АКТИВЕН", "ОБЯЗАТЕЛЕН", "ФАЙЛОВ"),
            (170, 220, 100, 90, 110, 90),
        )
        counts: dict[tuple[str, str, str], int] = {}
        if self.prepared:
            for entry in self.prepared.discovery.entries:
                key = (entry.country, entry.group, entry.profile)
                counts[key] = counts.get(key, 0) + 1
        for root in self.bundle.app.portfolio_roots:
            for group in root.groups:
                tree.insert(
                    "",
                    "end",
                    values=(
                        root.country,
                        group.name,
                        group.profile.upper(),
                        "ДА" if group.enabled else "НЕТ",
                        "ДА" if group.required else "НЕТ",
                        counts.get((root.country, group.name, group.profile), "—"),
                    ),
                    tags=("enabled" if group.enabled else "disabled",),
                )
        tree.tag_configure("enabled", foreground=self.colors["text"])
        tree.tag_configure("disabled", foreground=self.colors["text_dim"])

    def _page_schedule(self) -> None:
        page = self._clear_page()
        self._heading(
            page,
            "Расписание",
            "Excel COM запускается только при входе пользователя в Windows",
        )
        card = self._card(page)
        card.pack(fill="x")
        scheduler = self.bundle.app.scheduler
        self.schedule_enabled = tk.BooleanVar(
            value=bool(scheduler.get("enabled", True))
        )
        self.schedule_publish = tk.BooleanVar(
            value=bool(scheduler.get("publish", False))
        )
        self.notify_enabled = tk.BooleanVar(
            value=bool(self.bundle.app.notifications.get("enabled", True))
        )
        self.schedule_time = tk.StringVar(value=str(scheduler.get("time", "23:40")))
        self.schedule_days = {
            day: tk.BooleanVar(value=day in scheduler.get("days", []))
            for day in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
        }

        top = tk.Frame(card.inner, bg=self.colors["surface"])
        top.pack(fill="x")
        tk.Checkbutton(
            top,
            text="Ночной запуск включён",
            variable=self.schedule_enabled,
            bg=self.colors["surface"],
            fg=self.colors["text"],
            selectcolor=self.colors["surface_alt"],
            activebackground=self.colors["surface"],
            activeforeground=self.colors["text"],
            font=self.font(10, "bold"),
        ).pack(side="left")
        self.schedule_status = tk.Label(
            top,
            text="Статус задания не проверен",
            bg=self.colors["surface"],
            fg=self.colors["text_muted"],
            font=self.font(9),
        )
        self.schedule_status.pack(side="right")

        form = tk.Frame(card.inner, bg=self.colors["surface"])
        form.pack(fill="x", pady=(24, 18))
        tk.Label(
            form,
            text="ВРЕМЯ",
            bg=self.colors["surface"],
            fg=self.colors["text_dim"],
            font=self.font(8, "bold"),
        ).grid(row=0, column=0, sticky="w")
        entry = tk.Entry(
            form,
            textvariable=self.schedule_time,
            width=9,
            bg=self.colors["surface_alt"],
            fg=self.colors["text"],
            insertbackground=self.colors["accent"],
            relief="flat",
            font=self.font(13, "bold"),
        )
        entry.grid(row=1, column=0, sticky="w", pady=(6, 0), ipady=8, ipadx=8)
        days = tk.Frame(form, bg=self.colors["surface"])
        days.grid(row=0, column=1, rowspan=2, sticky="w", padx=(34, 0))
        labels = {
            "MON": "ПН",
            "TUE": "ВТ",
            "WED": "СР",
            "THU": "ЧТ",
            "FRI": "ПТ",
            "SAT": "СБ",
            "SUN": "ВС",
        }
        for index, day in enumerate(labels):
            tk.Checkbutton(
                days,
                text=labels[day],
                variable=self.schedule_days[day],
                indicatoron=False,
                width=4,
                bg=self.colors["surface_alt"],
                fg=self.colors["text_muted"],
                selectcolor=self.colors["accent"],
                activebackground=self.colors["border"],
                activeforeground=self.colors["text"],
                relief="flat",
                font=self.font(9, "bold"),
            ).grid(row=0, column=index, padx=3, ipady=8)

        notify = tk.Checkbutton(
            card.inner,
            text="Технический итог в Telegram · только ADMIN_ID владельца",
            variable=self.notify_enabled,
            bg=self.colors["surface"],
            fg=self.colors["text_muted"],
            selectcolor=self.colors["surface_alt"],
            activebackground=self.colors["surface"],
            activeforeground=self.colors["text"],
            font=self.font(9),
        )
        notify.pack(anchor="w", pady=(0, 10))
        tk.Checkbutton(
            card.inner,
            text=(
                "После зелёного контроля публиковать автоматически "
                "(иначе только dry-run)"
            ),
            variable=self.schedule_publish,
            bg=self.colors["surface"],
            fg=self.colors["warning"],
            selectcolor=self.colors["surface_alt"],
            activebackground=self.colors["surface"],
            activeforeground=self.colors["text"],
            font=self.font(9, "bold"),
        ).pack(anchor="w", pady=(0, 18))
        buttons = tk.Frame(card.inner, bg=self.colors["surface"])
        buttons.pack(fill="x")
        self._button(
            buttons,
            "Сохранить конфигурацию",
            self.save_schedule_config,
            primary=True,
        ).pack(side="left", padx=(0, 8))
        self._button(buttons, "Установить задание", self.install_schedule).pack(
            side="left", padx=(0, 8)
        )
        self._button(
            buttons,
            "Удалить задание",
            self.remove_schedule,
            danger=True,
        ).pack(side="left")
        self._button(buttons, "Проверить статус", self.refresh_schedule).pack(
            side="right"
        )

    def _page_logs(self) -> None:
        page = self._clear_page()
        header = self._heading(
            page,
            "Журнал",
            "Локальные артефакты, планы, validation и rollback journal",
        )
        self._button(
            header,
            "Открыть рабочую папку",
            lambda: self._open_path(self.bundle.workspace_path()),
        ).pack(side="right")
        card = self._card(page, padx=0, pady=0)
        card.pack(fill="both", expand=True)
        text = tk.Text(
            card.inner,
            bg=self.colors["surface"],
            fg=self.colors["text_muted"],
            insertbackground=self.colors["accent"],
            relief="flat",
            padx=18,
            pady=18,
            wrap="word",
            font=("Cascadia Mono", 9),
        )
        text.pack(fill="both", expand=True)
        text.insert("end", self._latest_log_text())
        text.configure(state="disabled")

    def _latest_log_text(self) -> str:
        if self.prepared:
            candidates = (
                self.prepared.snapshot.run_dir / "outcome.json",
                self.prepared.snapshot.run_dir / "plan.txt",
                self.prepared.snapshot.run_dir / "publish.journal.jsonl",
            )
            blocks = []
            for path in candidates:
                if path.is_file():
                    blocks.append(f"── {path.name} ──\n{path.read_text(encoding='utf-8')}")
            if blocks:
                return "\n\n".join(blocks)
        runs = self.bundle.workspace_path() / "runs"
        if not runs.is_dir():
            return "Запусков пока нет."
        recent = sorted(
            (path for path in runs.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:8]
        return "\n".join(
            f"{path.name}  ·  {path.stat().st_mtime:.0f}"
            for path in recent
        ) or "Запусков пока нет."

    @staticmethod
    def _short(value: Any, limit: int = 80) -> str:
        text = "" if value is None else str(value).replace("\n", " ↵ ")
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _run_async(
        self,
        label: str,
        work: Callable[[], Any],
        done: Callable[[Any], None],
    ) -> None:
        if self.busy:
            messagebox.showinfo("Контур", "Дождись завершения текущей операции.")
            return
        self.busy = True
        self._status_var.set(label)
        self._progress_var.set("РАБОТАЕТ")

        def target() -> None:
            try:
                result = work()
                self._events.put(("ok", done, result))
            except Exception as exc:  # noqa: BLE001 — показываем оператору
                self._events.put(("error", label, exc))

        threading.Thread(target=target, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, callback, payload = self._events.get_nowait()
                if kind == "progress":
                    self._status_var.set(str(callback))
                    self._progress_var.set(str(payload))
                    continue
                self.busy = False
                self._progress_var.set("")
                if kind == "ok":
                    self._status_var.set("Готово")
                    callback(payload)
                else:
                    self._status_var.set("Остановлено защитой")
                    messagebox.showerror(
                        "Контур · операция остановлена",
                        f"{type(payload).__name__}\n\n{payload}",
                    )
                    self.show_page(self.current_page)
        except queue.Empty:
            pass
        self.after(120, self._poll_events)

    def _progress(self, label: str, done: int, total: int) -> None:
        self._events.put(("progress", label, f"{done}/{total}"))

    def scan_manifest(self) -> None:
        self._run_async(
            "Сканирую manifest без открытия Excel",
            self.service.discover,
            self._manifest_ready,
        )

    def _manifest_ready(self, report) -> None:
        errors = sum(issue.severity == "error" for issue in report.issues)
        messagebox.showinfo(
            "Manifest",
            f"Файлов: {len(report.entries)}\n"
            f"Ошибок: {errors}\n"
            f"Статус: {'BLOCK' if report.blocked else 'READY'}",
        )
        self.show_page("contours")

    def prepare_plan(self) -> None:
        self.stage = None
        self.validation = None
        self._run_async(
            "Снимаю стабильные копии и строю diff",
            self.service.prepare,
            self._plan_ready,
        )

    def _plan_ready(self, prepared: PreparedRun) -> None:
        self.prepared = prepared
        self._status_var.set(
            "План заблокирован — смотри сигналы"
            if prepared.plan.blocked
            else "План готов к staging"
        )
        self.show_page("dashboard")

    def build_stage(self) -> None:
        if self.prepared is None:
            messagebox.showwarning("Контур", "Сначала построй свежий dry-run.")
            return
        if self.prepared.plan.blocked:
            messagebox.showwarning(
                "Контур",
                "План содержит блокеры. Staging не будет построен.",
            )
            self.show_page("analysis")
            return
        self._run_async(
            "Excel собирает staging-копии",
            lambda: build_staging(
                self.bundle,
                self.prepared.snapshot,
                self.prepared.plan,
                progress=self._progress,
            ),
            self._stage_ready,
        )

    def _stage_ready(self, stage: StageSet) -> None:
        self.stage = stage
        self.validation = None
        self._status_var.set("Staging собран; требуется validation")
        self.show_page("dashboard")

    def validate_stage(self) -> None:
        if self.prepared is None or self.stage is None:
            messagebox.showwarning("Контур", "Сначала собери staging.")
            return
        self._run_async(
            "Сверяю staging поклеточно",
            lambda: validate_staging(
                self.bundle,
                self.stage,
                self.prepared.plan,
                progress=self._progress,
            ),
            self._validation_ready,
        )

    def _validation_ready(self, report: ValidationReport) -> None:
        self.validation = report
        self._status_var.set(
            "Validation заблокирован"
            if report.blocked
            else "Validation пройден; разрешён шлюз публикации"
        )
        messagebox.showinfo(
            "Validation",
            f"Файлов: {report.checked_files}\n"
            f"Мастер: {report.checked_master_cells:,} ячеек\n"
            f"Портфели: {report.checked_projection_cells:,} ячеек\n"
            f"Статус: {'BLOCK' if report.blocked else 'GREEN'}",
        )
        self.show_page("dashboard")

    def publish_stage(self) -> None:
        if self.prepared is None or self.stage is None or self.validation is None:
            messagebox.showwarning("Контур", "Нужен свежий зелёный validation.")
            return
        if self.validation.blocked:
            messagebox.showwarning("Контур", "Validation содержит ошибки.")
            return
        mode = self.busy_mode.get()
        if mode == "force_close":
            reason = DelegatedForceCloser(
                self.bundle
            ).unavailable_reason
            if reason:
                messagebox.showerror(
                    "Контур",
                    f"Force close недоступен:\n\n{reason}",
                )
                return
        digest = self.prepared.plan.digest
        token = digest[:12]
        entered = simpledialog.askstring(
            "Подтверждение публикации",
            "Будут атомарно заменены общий реестр и личные портфели.\n"
            "При сбое сработает rollback.\n\n"
            f"Режим занятых файлов: {mode}\n\n"
            f"Введи digest:  {token}",
            parent=self,
        )
        if entered is None:
            return
        if entered.strip().casefold() != token.casefold():
            messagebox.showerror("Контур", "Digest не совпал. Публикация отменена.")
            return
        if not messagebox.askyesno(
            "Финальный шлюз",
            "Опубликовать проверенный staging сейчас?",
            icon="warning",
        ):
            return
        self._run_async(
            "Публикую с optimistic-lock и rollback",
            lambda: publish_validated(
                self.bundle,
                self.stage,
                self.prepared.plan,
                self.validation,
                confirmation_digest=digest,
                busy_mode=mode,
            ),
            self._publish_ready,
        )

    def _publish_ready(self, result) -> None:
        status = (
            "ОПУБЛИКОВАНО"
            if result.complete
            else "ОПУБЛИКОВАНО С SIDECAR"
        )
        OwnerNotifier(self.bundle).send(
            f"Контур · Реестры — {status}\n"
            f"Запуск: {result.run_id}\n"
            f"Live-файлов: {len(result.published)}\n"
            f"Sidecar: {len(result.sidecars)}"
        )
        self._status_var.set(
            "Публикация завершена"
            if result.complete
            else "Публикация требует сведения sidecar"
        )
        messagebox.showinfo(
            "Контур",
            f"Опубликовано файлов: {len(result.published)}\n"
            f"Sidecar-файлов: {len(result.sidecars)}\n"
            f"Соседних backup: {len(result.backups)}\n"
            "Baseline обновлён. Журнал сохранён.",
        )
        self.show_page("logs")

    def save_schedule_config(self) -> bool:
        raw_time = self.schedule_time.get().strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw_time):
            messagebox.showerror("Расписание", "Время должно быть в формате ЧЧ:ММ.")
            return False
        selected_days = [
            day
            for day, variable in self.schedule_days.items()
            if variable.get()
        ]
        if not selected_days:
            messagebox.showerror("Расписание", "Выбери хотя бы один день.")
            return False
        path = self.bundle.config_dir / "app.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["scheduler"]["enabled"] = self.schedule_enabled.get()
        payload["scheduler"]["publish"] = self.schedule_publish.get()
        payload["scheduler"]["time"] = raw_time
        payload["scheduler"]["days"] = selected_days
        payload["notifications"]["enabled"] = self.notify_enabled.get()
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        self.bundle = load_config_bundle(self.bundle.config_dir)
        self.service = CycleService(self.bundle)
        self._status_var.set("Конфигурация расписания сохранена")
        return True

    def save_busy_mode_config(self) -> None:
        mode = self.busy_mode.get()
        if mode not in {"block", "sidecar", "force_close"}:
            messagebox.showerror("Контур", "Неизвестный режим занятых файлов.")
            return
        if mode == "force_close":
            reason = DelegatedForceCloser(
                self.bundle
            ).unavailable_reason
            if reason:
                messagebox.showerror(
                    "Контур",
                    f"Force close недоступен:\n\n{reason}",
                )
                self.busy_mode.set("block")
                return
        path = self.bundle.config_dir / "app.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("safety", {})["busy_file_policy"] = mode
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        self.bundle = load_config_bundle(self.bundle.config_dir)
        self.service = CycleService(self.bundle)
        self._status_var.set(f"Режим занятых файлов сохранён: {mode}")

    def refresh_schedule(self) -> None:
        self._run_async(
            "Проверяю Планировщик Windows",
            lambda: task_status(self.bundle),
            self._schedule_status_ready,
        )

    def _schedule_status_ready(self, status) -> None:
        if hasattr(self, "schedule_status"):
            self.schedule_status.configure(
                text="Установлено" if status.installed else "Не установлено",
                fg=(
                    self.colors["success"]
                    if status.installed
                    else self.colors["warning"]
                ),
            )

    def install_schedule(self) -> None:
        if not self.save_schedule_config():
            return
        if not self.schedule_enabled.get():
            messagebox.showwarning(
                "Расписание",
                "Сначала включи ночной запуск.",
            )
            return
        if not messagebox.askyesno(
            "Планировщик Windows",
            (
                "Установить/обновить ночное задание?\n\n"
                + (
                    "Режим: АВТОПУБЛИКАЦИЯ."
                    if self.schedule_publish.get()
                    else "Режим: только dry-run."
                )
            ),
        ):
            return
        self._run_async(
            "Устанавливаю задание",
            lambda: install_task(self.bundle),
            self._schedule_status_ready,
        )

    def remove_schedule(self) -> None:
        if not messagebox.askyesno(
            "Планировщик Windows",
            "Удалить задание? Конфигурация приложения останется.",
            icon="warning",
        ):
            return
        self._run_async(
            "Удаляю задание",
            lambda: remove_task(self.bundle),
            self._schedule_status_ready,
        )

    @staticmethod
    def _open_path(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)  # type: ignore[attr-defined]


def run_gui(bundle: ConfigBundle | None = None) -> None:
    app = RegistryCycleApp(bundle or load_config_bundle())
    app.mainloop()
