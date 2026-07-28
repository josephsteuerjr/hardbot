"""Планирование безопасного закрытия платёжного месяца."""

from __future__ import annotations

import calendar
from datetime import datetime

from .config import FieldConfig, MonthRolloverConfig
from .errors import ConfigurationError
from .models import (
    Issue,
    MonthRolloverPlan,
    PaymentArchiveCell,
    WorkbookSnapshot,
)
from .normalize import comparable_value, is_blank, norm_title


def parse_rollover_month(value: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise ConfigurationError(
            "месяц закрытия должен иметь формат YYYY-MM"
        ) from exc
    return parsed.year, parsed.month


def automatic_rollover_month(
    config: MonthRolloverConfig | None,
    at: datetime,
) -> str | None:
    """Вернуть текущий месяц только в его последний календарный день."""
    if config is None or not config.enabled:
        return None
    if config.trigger != "last_calendar_day":
        raise ConfigurationError(
            f"неподдерживаемый триггер закрытия месяца: {config.trigger}"
        )
    last_day = calendar.monthrange(at.year, at.month)[1]
    if at.day != last_day:
        return None
    return f"{at.year:04d}-{at.month:02d}"


def resolve_rollover_month(
    fields: FieldConfig,
    *,
    explicit: str | None,
    at: datetime,
    automatic: bool,
) -> str | None:
    if explicit is not None:
        year, month = parse_rollover_month(explicit)
        return f"{year:04d}-{month:02d}"
    if not automatic:
        return None
    return automatic_rollover_month(fields.month_rollover, at)


def _history_title(config: MonthRolloverConfig, closing_month: str) -> str:
    year, month = parse_rollover_month(closing_month)
    return config.history_title_template.format(
        month=month,
        year=year,
        year2=year % 100,
    )


def build_month_rollover_plan(
    master: WorkbookSnapshot,
    fields: FieldConfig,
    closing_month: str | None,
) -> tuple[MonthRolloverPlan | None, list[Issue]]:
    if closing_month is None:
        return None, []
    config = fields.month_rollover
    if config is None or not config.enabled:
        return None, [
            Issue(
                "error",
                "month_rollover_disabled",
                "запрошено закрытие месяца, но оно отключено в конфигурации",
                {"closing_month": closing_month},
            )
        ]

    history_title = _history_title(config, closing_month)
    current_column = master.column_index(config.current_payments_title)
    history_column = master.column_index(history_title)
    issues: list[Issue] = []
    if current_column is None:
        return None, [
            Issue(
                "error",
                "current_payments_column_missing",
                "в общем реестре нет аналитической колонки текущих платежей",
                {
                    "title": config.current_payments_title,
                    "closing_month": closing_month,
                },
            )
        ]

    insert_history_column = history_column is None
    target_history_column = (
        current_column
        if insert_history_column
        else history_column
    )
    assert target_history_column is not None
    if (
        history_column is not None
        and history_column + 1 != current_column
    ):
        issues.append(
            Issue(
                "error",
                "payment_history_column_out_of_order",
                "историческая колонка месяца существует не перед "
                "«Текущими платежами»",
                {
                    "history_title": history_title,
                    "history_column": history_column + 1,
                    "current_column": current_column + 1,
                },
            )
        )

    current_tag = (
        master.type_tags[current_column]
        if current_column < len(master.type_tags)
        else None
    )
    if norm_title(current_tag):
        issues.append(
            Issue(
                "error",
                "current_payments_is_profile_column",
                "«Текущие платежи» помечены как профильное поле и могут "
                "ошибочно попасть сотрудникам",
                {
                    "title": config.current_payments_title,
                    "type_tag": current_tag,
                },
            )
        )
    if history_column is not None:
        history_tag = (
            master.type_tags[history_column]
            if history_column < len(master.type_tags)
            else None
        )
        if norm_title(history_tag):
            issues.append(
                Issue(
                    "error",
                    "payment_history_is_profile_column",
                    "историческая колонка платежей помечена как профильная",
                    {
                        "title": history_title,
                        "type_tag": history_tag,
                    },
                )
            )

    archive_cells: list[PaymentArchiveCell] = []
    conflicts: list[dict[str, object]] = []
    for offset, row in enumerate(master.rows):
        current = master.row_value(row, current_column)
        if is_blank(current):
            continue
        absolute_row = master.data_start + offset
        if history_column is not None:
            history = master.row_value(row, history_column)
            if (
                not is_blank(history)
                and comparable_value(history) != comparable_value(current)
            ):
                if len(conflicts) < 100:
                    conflicts.append(
                        {
                            "row": absolute_row + 1,
                            "history": history,
                            "current": current,
                        }
                    )
                continue
        archive_cells.append(
            PaymentArchiveCell(
                row=absolute_row,
                value=current,
            )
        )
    if conflicts:
        issues.append(
            Issue(
                "error",
                "payment_history_conflict",
                "историческая и текущая оплаты различаются; автоматическое "
                "закрытие месяца остановлено",
                {
                    "history_title": history_title,
                    "count": len(conflicts),
                    "samples": conflicts,
                },
            )
        )

    mode = (
        "insert"
        if insert_history_column
        else ("resume" if archive_cells else "already_complete")
    )
    return (
        MonthRolloverPlan(
            closing_month=closing_month,
            history_title=history_title,
            current_title=config.current_payments_title,
            history_column=target_history_column,
            current_column=current_column,
            insert_history_column=insert_history_column,
            mode=mode,
            archive_cells=tuple(archive_cells),
            employee_clear_titles=config.employee_clear_titles,
        ),
        issues,
    )
