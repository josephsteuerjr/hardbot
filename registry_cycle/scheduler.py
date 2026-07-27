"""Windows Task Scheduler: XML, установка и статус."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from .config import ConfigBundle
from .errors import RegistryCycleError

TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
DAY_NAMES = {
    "MON": "Monday",
    "TUE": "Tuesday",
    "WED": "Wednesday",
    "THU": "Thursday",
    "FRI": "Friday",
    "SAT": "Saturday",
    "SUN": "Sunday",
}


@dataclass(frozen=True, slots=True)
class SchedulerStatus:
    installed: bool
    task_name: str
    detail: str = ""


def _windows_user() -> str:
    domain = os.environ.get("USERDOMAIN", "").strip()
    username = os.environ.get("USERNAME", "").strip() or getpass.getuser()
    return f"{domain}\\{username}" if domain else username


def _sub(parent, name: str, text: str | None = None):
    node = ET.SubElement(parent, f"{{{TASK_NS}}}{name}")
    if text is not None:
        node.text = text
    return node


def build_task_xml(
    bundle: ConfigBundle,
    *,
    python_executable: str | Path | None = None,
    user_id: str | None = None,
    now: datetime | None = None,
) -> str:
    config = bundle.app.scheduler
    hour, minute = map(int, str(config.get("time", "23:40")).split(":"))
    current = now or datetime.now()
    start = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if start <= current:
        start += timedelta(days=1)

    ET.register_namespace("", TASK_NS)
    task = ET.Element(f"{{{TASK_NS}}}Task", {"version": "1.4"})
    registration = _sub(task, "RegistrationInfo")
    _sub(registration, "Description", "Безопасный цикл реестров СНГ")

    triggers = _sub(task, "Triggers")
    calendar = _sub(triggers, "CalendarTrigger")
    _sub(calendar, "StartBoundary", start.isoformat())
    _sub(calendar, "Enabled", "true")
    weekly = _sub(calendar, "ScheduleByWeek")
    days_node = _sub(weekly, "DaysOfWeek")
    for day in config.get("days", []):
        key = str(day).upper()
        if key not in DAY_NAMES:
            raise RegistryCycleError(f"неизвестный день планировщика: {day}")
        _sub(days_node, DAY_NAMES[key])
    _sub(weekly, "WeeksInterval", "1")

    principals = _sub(task, "Principals")
    principal = _sub(principals, "Principal")
    principal.set("id", "Author")
    _sub(principal, "UserId", user_id or _windows_user())
    _sub(principal, "LogonType", "InteractiveToken")
    _sub(principal, "RunLevel", "LeastPrivilege")

    settings = _sub(task, "Settings")
    _sub(settings, "MultipleInstancesPolicy", "IgnoreNew")
    _sub(settings, "DisallowStartIfOnBatteries", "false")
    _sub(settings, "StopIfGoingOnBatteries", "false")
    _sub(
        settings,
        "StartWhenAvailable",
        str(bool(config.get("start_when_available", True))).lower(),
    )
    _sub(settings, "RunOnlyIfNetworkAvailable", "true")
    _sub(settings, "AllowHardTerminate", "true")
    _sub(settings, "ExecutionTimeLimit", "PT2H")
    _sub(settings, "Enabled", "true")

    actions = _sub(task, "Actions")
    actions.set("Context", "Author")
    execute = _sub(actions, "Exec")
    _sub(execute, "Command", str(python_executable or sys.executable))
    _sub(execute, "Arguments", "-m registry_cycle execute --publish --scheduled")
    _sub(execute, "WorkingDirectory", str(bundle.project_root))
    return ET.tostring(task, encoding="unicode")


def task_status(bundle: ConfigBundle) -> SchedulerStatus:
    name = str(bundle.app.scheduler["task_name"])
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return SchedulerStatus(
        installed=result.returncode == 0,
        task_name=name,
        detail=(result.stdout or result.stderr).strip(),
    )


def install_task(bundle: ConfigBundle) -> SchedulerStatus:
    name = str(bundle.app.scheduler["task_name"])
    scheduler_dir = bundle.workspace_path() / "scheduler"
    scheduler_dir.mkdir(parents=True, exist_ok=True)
    xml_path = scheduler_dir / "task.xml"
    xml_path.write_text(build_task_xml(bundle), encoding="utf-8")
    result = subprocess.run(
        ["schtasks", "/Create", "/TN", name, "/XML", str(xml_path), "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RegistryCycleError(
            "не удалось установить задачу: "
            + (result.stderr or result.stdout).strip()
        )
    return task_status(bundle)


def remove_task(bundle: ConfigBundle) -> SchedulerStatus:
    name = str(bundle.app.scheduler["task_name"])
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", name, "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RegistryCycleError(
            "не удалось удалить задачу: "
            + (result.stderr or result.stdout).strip()
        )
    return SchedulerStatus(False, name, (result.stdout or "").strip())
