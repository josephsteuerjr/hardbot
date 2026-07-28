"""Локальные JSON/TXT-отчёты плана."""

from __future__ import annotations

import json
from pathlib import Path

from .models import SyncPlan


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def plan_text(plan: SyncPlan) -> str:
    errors = [issue for issue in plan.issues if issue.severity == "error"]
    warnings = [issue for issue in plan.issues if issue.severity == "warning"]
    info = [issue for issue in plan.issues if issue.severity == "info"]
    preserved = sum(item.preserved_cells for item in plan.projections)
    employee_preserved = sum(
        item.employee_owned_preserved_cells
        for item in plan.projections
    )
    handoff = sum(item.handoff_cells for item in plan.projections)
    rollover_cleared = sum(
        item.rollover_cleared_cells
        for item in plan.projections
    )
    retained = sum(item.retained_rows for item in plan.projections)
    removed = sum(item.removed_rows for item in plan.projections)
    lines = [
        "КОНТУР · РЕЕСТРЫ — ПЛАН ЦИКЛА",
        "",
        f"Статус: {'ЗАБЛОКИРОВАН' if plan.blocked else 'ГОТОВ К STAGING'}",
        f"SHA-256 плана: {plan.digest}",
        f"Общий реестр: {plan.master_path}",
        f"Изменений общего реестра: {len(plan.changes)}",
        f"Личных портфелей: {len(plan.projections)}",
        f"Сохранено нежёлтых значений при пустом мастере: {preserved}",
        f"Сохранено жёлтых значений текущих владельцев: {employee_preserved}",
        f"Передано жёлтых значений новому владельцу: {handoff}",
        f"Очищено жёлтых платёжных ячеек при закрытии месяца: {rollover_cleared}",
        f"Сохранено read-only строк вне текущего владельца: {retained}",
        f"Удалено строк по доказанному «пред отв»: {removed}",
        f"Ошибок: {len(errors)} · предупреждений: {len(warnings)} · инфо: {len(info)}",
        "",
    ]
    if plan.month_rollover is not None:
        rollover = plan.month_rollover
        lines.extend(
            [
                "ЗАКРЫТИЕ МЕСЯЦА",
                f"Месяц: {rollover.closing_month}",
                f"История: {rollover.history_title}",
                f"Режим: {rollover.mode}",
                f"Архивируется текущих оплат: {len(rollover.archive_cells)}",
                "",
            ]
        )
    if plan.issues:
        lines.append("СИГНАЛЫ")
        for issue in plan.issues:
            lines.append(f"[{issue.severity.upper()}] {issue.code}: {issue.message}")
        lines.append("")
    if plan.changes:
        lines.append("ИЗМЕНЕНИЯ ОБЩЕГО РЕЕСТРА")
        for change in plan.changes:
            lines.append(
                f"{change.contract_id} · {change.title} · "
                f"строка {change.row + 1} · источник {change.source_path.name}"
            )
        lines.append("")
    return "\n".join(lines)


def write_plan_reports(run_dir: Path, plan: SyncPlan) -> tuple[Path, Path]:
    json_path = run_dir / "plan.json"
    text_path = run_dir / "plan.txt"
    _atomic_text(
        json_path,
        json.dumps(plan.as_dict(), ensure_ascii=False, indent=2),
    )
    _atomic_text(text_path, plan_text(plan))
    return json_path, text_path
