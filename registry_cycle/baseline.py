"""Зафиксированное успешное состояние для трёхстороннего merge."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ConfigBundle, FieldConfig, FieldRule
from .errors import SafetyViolation
from .models import WorkbookSnapshot
from .normalize import is_blank, json_value, norm_key, norm_title
from .semantics import comparable_for_rule, write_value_for_rule


@dataclass(frozen=True, slots=True)
class CycleBaseline:
    schema_version: int
    created_at: str
    master_path: Path
    plan_digest: str
    values: dict[tuple[str, str], Any]

    def lookup(self, contract_id: Any, title: Any) -> tuple[bool, Any]:
        key = (norm_key(contract_id), norm_title(title))
        if key not in self.values:
            return False, None
        return True, self.values[key]

    def as_dict(self) -> dict[str, Any]:
        records = [
            {
                "contract_id": contract_id,
                "title": title,
                "value": json_value(value),
            }
            for (contract_id, title), value in sorted(self.values.items())
        ]
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "master_path": str(self.master_path),
            "plan_digest": self.plan_digest,
            "records": records,
        }


def _rule_map(fields: FieldConfig) -> dict[str, FieldRule]:
    return {norm_title(rule.title): rule for rule in fields.collect}


def _baseline_comparable(value: Any, rule: FieldRule) -> tuple[str, Any]:
    return comparable_for_rule(value, rule)


def build_baseline(
    master: WorkbookSnapshot,
    fields: FieldConfig,
    *,
    plan_digest: str,
    created_at: datetime | None = None,
) -> CycleBaseline:
    """Снять baseline с уже проверенного итогового мастера.

    Хранятся только непустые собираемые значения. Пустое отсутствие безопасно
    трактуется как новый ключ; противоречивые дубли КД запрещены.
    """
    key_column = master.column_index(fields.key_title)
    if key_column is None:
        raise SafetyViolation("baseline: в мастере нет колонки КД")
    rule_map = _rule_map(fields)
    columns = {
        title: master.column_index(rule.title)
        for title, rule in rule_map.items()
    }
    missing = [
        rule_map[title].title
        for title, column in columns.items()
        if column is None
    ]
    if missing:
        raise SafetyViolation(
            "baseline: в мастере нет собираемых колонок: " + ", ".join(missing)
        )

    values: dict[tuple[str, str], Any] = {}
    for row in master.rows:
        contract_id = norm_key(master.row_value(row, key_column))
        if not contract_id:
            continue
        for title, column in columns.items():
            assert column is not None
            value = master.row_value(row, column)
            if is_blank(value):
                continue
            key = (contract_id, title)
            if key in values:
                rule = rule_map[title]
                if (
                    _baseline_comparable(values[key], rule)
                    != _baseline_comparable(value, rule)
                ):
                    raise SafetyViolation(
                        "baseline: разные значения у дублей КД "
                        f"{contract_id}, поле «{rule.title}»"
                    )
                continue
            values[key] = json_value(write_value_for_rule(value, rule_map[title]))

    return CycleBaseline(
        schema_version=1,
        created_at=(created_at or datetime.now()).isoformat(timespec="seconds"),
        master_path=master.path,
        plan_digest=plan_digest,
        values=values,
    )


def baseline_path(bundle: ConfigBundle, master_path: str | Path) -> Path:
    identity = str(Path(master_path)).casefold().encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:16]
    return bundle.workspace_path() / "baselines" / f"{digest}.json.gz"


def save_baseline(path: str | Path, baseline: CycleBaseline) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    payload = json.dumps(
        baseline.as_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    with gzip.open(temporary, "wb", compresslevel=6) as stream:
        stream.write(payload)
    temporary.replace(destination)
    return destination


def load_baseline(
    path: str | Path,
    *,
    expected_master_path: str | Path | None = None,
) -> CycleBaseline:
    source = Path(path)
    try:
        with gzip.open(source, "rb") as stream:
            payload = json.loads(stream.read().decode("utf-8"))
    except FileNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001 — повреждение gzip/json
        raise SafetyViolation(f"baseline не прочитан: {source}: {exc}") from exc
    if int(payload.get("schema_version", 0)) != 1:
        raise SafetyViolation(
            f"baseline имеет неизвестную версию: {payload.get('schema_version')}"
        )
    master_path = Path(payload["master_path"])
    if (
        expected_master_path is not None
        and str(master_path).casefold()
        != str(Path(expected_master_path)).casefold()
    ):
        raise SafetyViolation(
            "baseline относится к другому общему реестру: "
            f"{master_path}"
        )
    values: dict[tuple[str, str], Any] = {}
    for record in payload.get("records", []):
        key = (
            norm_key(record.get("contract_id")),
            norm_title(record.get("title")),
        )
        if not all(key) or key in values:
            raise SafetyViolation("baseline содержит пустой или повторный ключ")
        values[key] = record.get("value")
    return CycleBaseline(
        schema_version=1,
        created_at=str(payload["created_at"]),
        master_path=master_path,
        plan_digest=str(payload["plan_digest"]),
        values=values,
    )


def load_baseline_if_present(
    bundle: ConfigBundle,
    master_path: str | Path,
) -> CycleBaseline | None:
    path = baseline_path(bundle, master_path)
    if not path.is_file():
        return None
    return load_baseline(path, expected_master_path=master_path)
