# -*- coding: utf-8 -*-
"""Точечная починка ложного обогащения личных Excel-реестров.

Старый пульт считал любой непустой ``visit_date`` доказательством визита бота.
Из-за этого импортные HARD-значения (например, Excel-серийник ``531`` или
``CONSUMER``) могли повторно уехать из БД в личные реестры. Серверная причина
уже закрыта provenance-маркером; этот инструмент чинит только ранее испорченные
файлы и не требует угадывать, к какой копии откатывать весь реестр.

Контракт безопасности:

* dry-run по умолчанию; он создаёт читаемый TXT и машинный JSON с хешем плана;
* договор с подтверждённым ``_bot_completed=true`` не рассматривается вообще;
* разрешены только ``enrich.VISIT_KEYS``;
* ячейка восстанавливается лишь при видимом в цепочке копий переходе
  ``старое -> точное raw-значение сервера`` и лишь пока текущее значение всё
  ещё равно этому raw-значению;
* берётся предыдущее значение последнего такого перехода — последующая ручная
  правка тем самым важнее более старой копии;
* применение заново сверяет сервер, хеш файла и весь план;
* Excel открывает только временную копию. После проверки создаётся атомарная
  резервная копия оригинала, затем проверенная копия ставится через os.replace.

Запуск на машине, где виден тот же ``U:``/сетевой корень, что у пульта::

    python repair_false_enrichment.py --base "U:\\rbr\\Развитие СНГ"
    python repair_false_enrichment.py --apply "...\\false-enrichment-....json"

Можно проверить один файл без обхода дерева::

    python repair_false_enrichment.py --file "...\\Тошмахматов Жахонгир.xlsb"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import ssl
import sys
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import enrich
import razbros


APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "настройки пульта.json"
DEFAULT_URL = "https://hardbot2.209.222.251.112.nip.io"
FORMAT_VERSION = 1
SUPPORTED_EXTS = (".xlsb", ".xlsm", ".xlsx", ".xls")
BACKUP_MARKER = " (до обогащения "
REPAIR_MARKERS = (BACKUP_MARKER, " (до починки ", " (до починки ложного обогащения ",
                  " (до разброса ")   # копию разброса тоже не считаем живым реестром
REPORT_DIR = APP_DIR / "отчёты починки"

try:  # русские буквы в штатной консоли Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


# ---------------------------------------------------------------------------
# Общие значения, сравнение и сериализация


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def encode_value(value: Any) -> dict[str, Any]:
    """Значение calamine/Excel -> JSON без потери пустоты и базового типа."""
    if value is None:
        return {"kind": "empty", "value": None}
    if isinstance(value, datetime):
        return {"kind": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"kind": "date", "value": value.isoformat()}
    if isinstance(value, bool):
        return {"kind": "bool", "value": value}
    if isinstance(value, int):
        return {"kind": "int", "value": value}
    if isinstance(value, float):
        return {"kind": "float", "value": value}
    if isinstance(value, str):
        return {"kind": "text", "value": value}
    raise TypeError(f"неподдерживаемый тип значения ячейки: {type(value).__name__}")


def decode_value(value: dict[str, Any]) -> Any:
    kind, raw = value.get("kind"), value.get("value")
    if kind == "empty":
        return None
    if kind == "date":
        return date.fromisoformat(raw)
    if kind == "datetime":
        return datetime.fromisoformat(raw)
    if kind == "bool":
        return bool(raw)
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "text":
        return str(raw)
    raise ValueError(f"неизвестный тип значения в плане: {kind!r}")


def value_display(value: Any, limit: int = 100) -> str:
    if isinstance(value, dict) and "kind" in value:
        value = decode_value(value)
    if value is None or value == "":
        return "<пусто>"
    if isinstance(value, datetime):
        text = value.isoformat(sep=" ")
    elif isinstance(value, date):
        text = value.isoformat()
    else:
        text = str(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def excel_cell(row0: int, col0: int) -> str:
    return f"{razbros.col_letter(col0 + 1)}{row0 + 1}"


def excel_serial_date(value: Any) -> date | None:
    """Excel 1900 date system, включая совместимый с Excel leap-year offset."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not number.is_integer() or not (1 <= number <= 2_958_465):
        return None
    try:
        return (datetime(1899, 12, 30) + timedelta(days=int(number))).date()
    except (OverflowError, ValueError):
        return None


def poison_equal(key: str, cell_value: Any, raw_value: Any) -> bool:
    """Равенство с учётом автоматического преобразования Excel.

    Старый писатель отдавал COM строки. Excel мог превратить ``531`` в дату
    14.06.1901 в date-ячейке, а ISO ``2026-07-09`` — в обычную дату даже в
    ошибочной dropdown-колонке. Оба случая должны сопоставиться с raw-строкой.
    """
    if enrich.cell_text(cell_value) == enrich.cell_text(raw_value):
        return True
    cell_date = enrich.parse_date(cell_value)
    raw_date = enrich.parse_date(raw_value)
    if cell_date is not None and raw_date is not None:
        return cell_date == raw_date
    if cell_date is not None and (key == "visit_date" or isinstance(cell_value, (date, datetime))):
        return cell_date == excel_serial_date(raw_value)
    return False


def logical_equal(left: Any, right: Any) -> bool:
    """Строгое логическое равенство без склейки текста с числом/датой."""
    if left in (None, "") and right in (None, ""):
        return True
    left_is_date = isinstance(left, (date, datetime))
    right_is_date = isinstance(right, (date, datetime))
    if left_is_date or right_is_date:
        return left_is_date and right_is_date and enrich.parse_date(left) == enrich.parse_date(right)
    left_is_number = isinstance(left, (int, float)) and not isinstance(left, bool)
    right_is_number = isinstance(right, (int, float)) and not isinstance(right, bool)
    if left_is_number or right_is_number:
        return left_is_number and right_is_number and float(left) == float(right)
    return type(left) is type(right) and left == right


# ---------------------------------------------------------------------------
# Сервер: безопасный и raw-снимок одновременно


def _request_json(url: str, key: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, headers={"X-Admin-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
            ctx = ssl._create_unverified_context()  # noqa: S323 — совместимость с текущим пультом
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                return json.load(response)
        raise


def _safe_provenance_fingerprint(payload: dict) -> str:
    rows = []
    for row in payload.get("rows") or []:
        rows.append({
            "contract_id": enrich.norm_cid(row.get(enrich.CONTRACT_KEY)),
            "responsible": str(row.get("responsible") or "").strip(),
            "completed": row.get(enrich.FILLED_MARKER),
            "values": {key_name: encode_value(row.get(key_name))
                       for key_name in enrich.VISIT_KEYS},
        })
    rows.sort(key=lambda item: item["contract_id"])
    return payload_sha256({"fields": payload.get("fields") or [], "rows": rows})


def fetch_server_evidence(url: str, key: str, timeout: int = 120,
                          stable_attempts: int = 3) -> dict:
    base = url.rstrip("/") + "/admin/export.json"
    safe = raw = None
    for _attempt in range(stable_attempts):
        safe_before = _request_json(base, key, timeout)
        raw_candidate = _request_json(base + "?mode=full", key, timeout)
        safe_after = _request_json(base, key, timeout)
        if _safe_provenance_fingerprint(safe_before) == \
                _safe_provenance_fingerprint(safe_after):
            safe, raw = safe_after, raw_candidate
            break
    if safe is None or raw is None:
        raise RuntimeError("provenance менялся во время чтения сервера; повтори dry-run позже")
    fields = safe.get("fields") or []
    if not fields or fields != (raw.get("fields") or []):
        raise RuntimeError("схема safe/raw выдачи отсутствует или различается")
    safe_rows, raw_rows = safe.get("rows") or [], raw.get("rows") or []
    if raw_rows and not safe_rows:
        raise RuntimeError("safe-выдача пуста при непустом raw; починка запрещена")
    if safe_rows and not all(enrich.FILLED_MARKER in row for row in safe_rows):
        raise RuntimeError("сервер не выдаёт provenance-маркер; починка запрещена")

    def unique_rows(rows: list[dict]) -> tuple[dict[str, dict], set[str]]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            cid = enrich.norm_cid(row.get(enrich.CONTRACT_KEY))
            if cid:
                grouped[cid].append(row)
        duplicate = {cid for cid, items in grouped.items() if len(items) != 1}
        return {cid: items[0] for cid, items in grouped.items() if len(items) == 1}, duplicate

    safe_by_cid, safe_dupes = unique_rows(safe_rows)
    raw_by_cid, raw_dupes = unique_rows(raw_rows)
    ambiguous = safe_dupes | raw_dupes
    risky: list[dict] = []
    trusted: set[str] = set()
    missing_safe = 0
    unknown_marker = 0
    for cid, raw_row in raw_by_cid.items():
        if cid in ambiguous:
            continue
        safe_row = safe_by_cid.get(cid)
        if safe_row is None:
            missing_safe += 1
            continue
        marker = safe_row.get(enrich.FILLED_MARKER)
        if marker is True:
            trusted.add(cid)
            continue
        if marker is not False:
            unknown_marker += 1
            continue
        # Именно это условие использовал старый is_filled(). Пустая дата не
        # запускала ложное обогащение, даже если рядом были другие HARD-поля.
        if enrich.cell_text(raw_row.get("visit_date")):
            risky.append(raw_row)

    by_employee: dict[str, dict] = {}
    unassigned = 0
    for row in risky:
        employee = str(row.get("responsible") or "").strip()
        if not employee:
            unassigned += 1
            continue
        key_name = enrich.norm_name(employee)
        item = by_employee.setdefault(key_name, {"employee": employee, "rows": []})
        item["rows"].append(row)

    risk_fingerprint_rows = []
    for row in sorted(risky, key=lambda item: enrich.norm_cid(item.get(enrich.CONTRACT_KEY))):
        risk_fingerprint_rows.append({
            "contract_id": enrich.norm_cid(row.get(enrich.CONTRACT_KEY)),
            "responsible": str(row.get("responsible") or "").strip(),
            "values": {key_name: encode_value(row.get(key_name))
                       for key_name in enrich.VISIT_KEYS},
        })
    return {
        "fields": fields,
        "raw_by_cid": raw_by_cid,
        "trusted_ids": trusted,
        "risky_rows": risky,
        "by_employee": by_employee,
        "server_fingerprint": payload_sha256(risk_fingerprint_rows),
        "counts": {
            "safe_rows": len(safe_rows),
            "raw_rows": len(raw_rows),
            "trusted_contracts": len(trusted),
            "risky_contracts": len(risky),
            "risky_named_contracts": len(risky) - unassigned,
            "risky_unassigned_contracts": unassigned,
            "risky_employees": len(by_employee),
            "ambiguous_contract_ids": len(ambiguous),
            "missing_safe_rows": missing_safe,
            "unknown_markers": unknown_marker,
        },
    }


def risk_rows_fingerprint(rows: list[dict]) -> str:
    payload = []
    for row in sorted(rows, key=lambda item: enrich.norm_cid(item.get(enrich.CONTRACT_KEY))):
        payload.append({
            "contract_id": enrich.norm_cid(row.get(enrich.CONTRACT_KEY)),
            "responsible": str(row.get("responsible") or "").strip(),
            "values": {key_name: encode_value(row.get(key_name))
                       for key_name in enrich.VISIT_KEYS},
        })
    return payload_sha256(payload)


def employee_risk_summary(by_employee: dict[str, dict]) -> list[dict]:
    """Пояснить экспозицию: серийники дают 1901–1903, обычные даты выглядят правдоподобно."""
    result = []
    for _key, item in sorted(by_employee.items(), key=lambda pair: pair[1]["employee"].lower()):
        numeric = parsed = other = 0
        nonempty = Counter()
        for row in item["rows"]:
            visit_date = row.get("visit_date")
            if excel_serial_date(visit_date) is not None and enrich.parse_date(visit_date) is None:
                numeric += 1
            elif enrich.parse_date(visit_date) is not None:
                parsed += 1
            else:
                other += 1
            for key_name in enrich.VISIT_KEYS:
                if enrich.cell_text(row.get(key_name)):
                    nonempty[key_name] += 1
        result.append({
            "employee": item["employee"],
            "contracts": len(item["rows"]),
            "numeric_excel_serial_dates": numeric,
            "parseable_dates": parsed,
            "other_dates": other,
            "nonempty_visit_fields": dict(sorted(nonempty.items())),
        })
    return result


# ---------------------------------------------------------------------------
# Реестры и цепочки копий


def _data_start(grid: list[list], header_row: int) -> int:
    start = header_row + 1
    if start < len(grid) and enrich._is_source_row(grid[start]):
        start += 1
    return start


def snapshot_from_grid(path: str | Path, grid: list[list], fields: list[dict],
                       *, sheet: str = enrich.SHEET_NAME, file_hash: str = "",
                       sheet_names: list[str] | None = None) -> dict:
    """Построить сопоставимый снимок; чистая часть используется в тестах."""
    header_row, col_map = enrich.find_header(grid, fields)
    start = _data_start(grid, header_row)
    cid_col = col_map[enrich.CONTRACT_KEY]
    rows: dict[str, list[dict]] = defaultdict(list)
    for row_index in range(start, len(grid)):
        row = grid[row_index]
        cid = enrich.norm_cid(row[cid_col] if cid_col < len(row) else None)
        if not cid:
            continue
        values = {key_name: (row[col] if col < len(row) else None)
                  for key_name, col in col_map.items() if key_name in enrich.VISIT_KEYS}
        rows[cid].append({"row": row_index, "values": values})
    return {
        "path": str(path),
        "file_sha256": file_hash,
        "sheet": sheet,
        "sheet_names": list(sheet_names or [sheet]),
        "header_row": header_row,
        "data_start": start,
        "col_map": col_map,
        "rows": dict(rows),
        "grid": grid,
    }


def read_workbook_snapshot(path: Path, fields: list[dict]) -> dict:
    from python_calamine import CalamineWorkbook

    stat_before = path.stat()
    workbook = CalamineWorkbook.from_path(str(path))
    names = list(workbook.sheet_names)
    order = ([enrich.SHEET_NAME] if enrich.SHEET_NAME in names else []) + \
        [name for name in names if name != enrich.SHEET_NAME]
    last_error: Exception | None = None
    for name in order:
        grid = workbook.get_sheet_by_name(name).to_python()
        try:
            file_hash = sha256_file(path)
            stat_after = path.stat()
            if (stat_before.st_size, stat_before.st_mtime_ns) != \
                    (stat_after.st_size, stat_after.st_mtime_ns):
                raise RuntimeError("файл изменился во время чтения")
            snapshot = snapshot_from_grid(path, grid, fields, sheet=name,
                                          file_hash=file_hash, sheet_names=names)
            snapshot.update({"size": stat_after.st_size, "mtime_ns": stat_after.st_mtime_ns})
            return snapshot
        except ValueError as exc:
            last_error = exc
    raise ValueError(f"не найден лист с шапкой реестра: {last_error or 'нет листов'}")


def _unique_cell(snapshot: dict, cid: str, key_name: str) -> dict:
    col = snapshot["col_map"].get(key_name)
    records = snapshot["rows"].get(cid) or []
    if col is None or not records:
        return {"status": "missing"}
    cells = [{"row": record["row"], "col": col,
              "value": record["values"].get(key_name)} for record in records]
    first = cells[0]["value"]
    if any(not logical_equal(first, cell["value"]) for cell in cells[1:]):
        return {"status": "ambiguous", "cells": cells}
    return {"status": "ok", **cells[0], "occurrences": len(cells)}


def _registry_structure(snapshot: dict) -> tuple:
    """То, что enrich гарантированно не меняет: лист, шапка и позиции КД."""
    positions = []
    for cid, records in snapshot["rows"].items():
        positions.extend((record["row"], cid) for record in records)
    columns = tuple(sorted(snapshot["col_map"].items()))
    return snapshot["sheet"], columns, tuple(sorted(positions))


def _backup_regex(path: Path) -> re.Pattern[str]:
    """Имя копии. Год необязателен: старые копии его не содержат.

    Новые содержат — и это важно. Без года его приходилось достраивать из
    времени создания файла, а оно сбрасывается при переносе копии на другой том,
    и цепочка копий выстраивалась в неверном порядке.
    """
    return re.compile(
        rf"^{re.escape(path.stem)} \(до обогащения "
        r"(?P<day>\d{2})\.(?P<month>\d{2})(?:\.(?P<year>\d{4}))? "
        r"(?P<hour>\d{2})-(?P<minute>\d{2})(?:, (?P<seq>\d+))?\)$",
        flags=re.IGNORECASE,
    )


def _parsed_backup_stamp(path: Path, match: re.Match[str]) -> tuple[datetime, int]:
    day, month = int(match.group("day")), int(match.group("month"))
    hour, minute = int(match.group("hour")), int(match.group("minute"))
    sequence = int(match.group("seq") or 1)
    named_year = match.group("year")
    if named_year:
        try:
            return datetime(int(named_year), month, day, hour, minute), sequence
        except ValueError as exc:
            raise ValueError(f"некорректная дата в имени копии: {path.name}") from exc
    # Год в имени отсутствует (копия сделана до 05.08.2026) — угадываем по
    # времени файла, как раньше.
    try:
        anchor = datetime.fromtimestamp(path.stat().st_ctime)
    except OSError:
        anchor = datetime.now()
    candidates = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(datetime(year, month, day, hour, minute))
        except ValueError:
            continue
    if not candidates:
        raise ValueError(f"некорректная дата в имени копии: {path.name}")
    stamp = min(candidates, key=lambda item: abs((item - anchor).total_seconds()))
    return stamp, sequence


def backup_folders(path: Path, roots=None):
    """Рядом с книгой и в корнях для копий — см. enrich.backup_copy.

    Копия, унесённая из папки сотрудника (сорить там запрещено), раньше означала
    «доказательное восстановление невозможно»: искали только в path.parent.
    """
    yield path.parent
    for root in (roots or []):
        if not root:
            continue
        yield Path(root) / path.parent.parent.name / path.parent.name


def discover_backups(path: Path, roots=None) -> list[dict]:
    pattern = _backup_regex(path)
    found = []
    candidates = []
    for folder in backup_folders(path, roots):
        try:
            candidates += list(folder.iterdir())
        except OSError:
            continue
    for candidate in candidates:
        if not candidate.is_file() or candidate.suffix.lower() != path.suffix.lower():
            continue
        match = pattern.fullmatch(candidate.stem)
        if not match:
            continue
        stamp, sequence = _parsed_backup_stamp(candidate, match)
        found.append({"path": candidate, "stamp": stamp, "sequence": sequence})
    found.sort(key=lambda item: (item["stamp"], item["sequence"],
                                 item["path"].name.lower()))
    return found


def _is_live_workbook(path: Path) -> bool:
    name = path.stem.lower()
    return (path.is_file() and path.suffix.lower() in SUPPORTED_EXTS
            and not path.name.startswith("~$")
            and not any(marker.strip().lower() in name for marker in REPAIR_MARKERS)
            and ".__repair__" not in name)


def personal_files(entries: list[dict], employee: str) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    warnings: list[str] = []
    for entry in entries:
        folder = Path(entry["path"])
        stems = [razbros.sanitize_filename(employee), folder.name]
        exact = []
        for stem in dict.fromkeys(stems):
            exact.extend(folder / f"{stem}{ext}" for ext in SUPPORTED_EXTS
                         if (folder / f"{stem}{ext}").exists())
        exact = list(dict.fromkeys(exact))
        if len(exact) == 1:
            chosen = exact[0]
        elif len(exact) > 1:
            warnings.append(f"неоднозначно, несколько основных файлов: {folder}: "
                            + ", ".join(path.name for path in exact))
            continue
        else:
            candidates = sorted((path for path in folder.iterdir() if _is_live_workbook(path)),
                                key=lambda item: item.name.lower())
            if len(candidates) != 1:
                if candidates:
                    warnings.append(f"неоднозначно, основные файлы не названы по ФИО: {folder}: "
                                    + ", ".join(path.name for path in candidates))
                else:
                    warnings.append(f"в папке нет основного Excel-файла: {folder}")
                continue
            chosen = candidates[0]
        if chosen not in files:
            files.append(chosen)
    return files, warnings


def collect_jobs(evidence: dict, *, base: str | None = None,
                 roots: list[str] | None = None, config: str | None = None,
                 files: list[str] | None = None,
                 only: list[str] | None = None) -> tuple[list[dict], list[str], list[str]]:
    """Вернуть задания, предупреждения и разрешённые корни для отчёта."""
    selected = {enrich.norm_name(name) for name in (only or []) if name.strip()}
    warnings: list[str] = []
    root_labels: list[str] = []
    jobs: list[dict] = []
    if files:
        risk_rows = [row for row in evidence["risky_rows"]
                     if not selected or enrich.norm_name(row.get("responsible")) in selected]
        for raw_path in files:
            path = Path(raw_path)
            if not path.exists():
                warnings.append(f"явный файл недоступен: {path}")
                continue
            jobs.append({"employee": path.stem, "path": path, "rows": risk_rows,
                         "explicit_file": True})
        return jobs, warnings, root_labels

    resolved = razbros.resolve_roots(base, razbros.load_config(config), roots or [])
    root_labels = [str(path) for _republic, path in resolved]
    if not resolved:
        return [], ["не задан ни --base, ни --root, ни --file"], root_labels
    for _republic, root in resolved:
        if not root.exists():
            warnings.append(f"корень реестров недоступен: {root}")
    folder_map_raw = razbros.build_folder_map(resolved)
    folder_map: dict[str, list[dict]] = defaultdict(list)
    for folder_name, entries in folder_map_raw.items():
        folder_map[enrich.norm_name(folder_name)].extend(entries)

    for key_name, item in sorted(evidence["by_employee"].items(),
                                 key=lambda pair: pair[1]["employee"].lower()):
        if selected and key_name not in selected:
            continue
        entries = folder_map.get(key_name)
        if not entries:
            warnings.append(f"нет папки сотрудника: {item['employee']} "
                            f"(рисковых договоров: {len(item['rows'])})")
            continue
        found, file_warnings = personal_files(entries, item["employee"])
        warnings.extend(file_warnings)
        for path in found:
            jobs.append({"employee": item["employee"], "path": path,
                         "rows": item["rows"], "explicit_file": False})
    return jobs, warnings, root_labels


# ---------------------------------------------------------------------------
# Доказательный план по последовательности снимков


def _skip(skipped: Counter, examples: list[dict], reason: str, *, cid: str,
          key_name: str | None = None, detail: str = "") -> None:
    skipped[reason] += 1
    if len(examples) < 250:
        item = {"reason": reason, "contract_id": cid}
        if key_name:
            item["key"] = key_name
        if detail:
            item["detail"] = detail
        examples.append(item)


def build_chain_plan(snapshots: list[dict], fields: list[dict], risk_rows: list[dict],
                     trusted_ids: set[str] | None = None) -> dict:
    """Чистая логика: найти только доказанные ячейки для восстановления."""
    if not snapshots:
        raise ValueError("нет снимков")
    current = snapshots[-1]
    field_by_key = {field["key"]: field for field in fields}
    trusted_ids = trusted_ids or set()
    skipped: Counter = Counter()
    examples: list[dict] = []
    writes: list[dict] = []
    seen_risk: set[str] = set()

    for raw in risk_rows:
        cid = enrich.norm_cid(raw.get(enrich.CONTRACT_KEY))
        if not cid or cid in seen_risk:
            if cid:
                _skip(skipped, examples, "duplicate_server_contract", cid=cid)
            continue
        seen_risk.add(cid)
        if cid in trusted_ids or raw.get(enrich.FILLED_MARKER) is True:
            _skip(skipped, examples, "trusted_bot_visit", cid=cid)
            continue
        if not enrich.cell_text(raw.get("visit_date")):
            _skip(skipped, examples, "raw_visit_date_empty", cid=cid)
            continue
        current_records = current["rows"].get(cid) or []
        if not current_records:
            _skip(skipped, examples, "contract_missing_in_current", cid=cid)
            continue

        for key_name in enrich.VISIT_KEYS:
            raw_value = raw.get(key_name)
            if not enrich.cell_text(raw_value):
                continue  # старый enrich пустые значения не писал
            current_col = current["col_map"].get(key_name)
            if current_col is None:
                _skip(skipped, examples, "column_missing_in_current", cid=cid,
                      key_name=key_name)
                continue

            latest: dict | None = None
            for index in range(1, len(snapshots)):
                previous, following = snapshots[index - 1], snapshots[index]
                # Разброс/замена реестра, вставка строк или смена шаблона — это
                # непреодолимая граница. Старые значения до неё уже не являются
                # доказательством для новой версии файла.
                if _registry_structure(previous) != _registry_structure(following):
                    latest = None
                    continue
                before = _unique_cell(previous, cid, key_name)
                after = _unique_cell(following, cid, key_name)
                date_after = _unique_cell(following, cid, "visit_date")
                if before["status"] != "ok" or after["status"] != "ok":
                    continue
                # Отпечаток именно старого enrich: после ребра также лежит raw
                # visit_date, которым прежний is_filled() разрешил весь контракт.
                if date_after["status"] != "ok" or not poison_equal(
                        "visit_date", date_after["value"], raw.get("visit_date")):
                    continue
                if poison_equal(key_name, before["value"], raw_value):
                    continue
                if not poison_equal(key_name, after["value"], raw_value):
                    continue
                try:
                    restore = encode_value(before["value"])
                    current_raw = encode_value(after["value"])
                except TypeError:
                    continue
                latest = {
                    "restore": restore,
                    "source_snapshot": str(previous["path"]),
                    "source_sha256": previous.get("file_sha256", ""),
                    "source_sheet": previous["sheet"],
                    "source_row": before["row"],
                    "source_col": before["col"],
                    "poison_snapshot": str(following["path"]),
                    "poison_sha256": following.get("file_sha256", ""),
                    "poison_sheet": following["sheet"],
                    "poison_row": after["row"],
                    "poison_col": after["col"],
                    "edge_index": index,
                    "edge_value": current_raw,
                }

            for record in current_records:
                current_value = record["values"].get(key_name)
                if not poison_equal(key_name, current_value, raw_value):
                    _skip(skipped, examples, "current_diverged_kept", cid=cid,
                          key_name=key_name, detail=value_display(current_value))
                    continue
                if latest is None:
                    _skip(skipped, examples, "no_proven_transition", cid=cid,
                          key_name=key_name, detail=value_display(current_value))
                    continue
                try:
                    encoded_current = encode_value(current_value)
                    encoded_raw = encode_value(raw_value)
                except TypeError as exc:
                    _skip(skipped, examples, "unsupported_cell_type", cid=cid,
                          key_name=key_name, detail=str(exc))
                    continue
                field = field_by_key.get(key_name, {})
                writes.append({
                    "contract_id": cid,
                    "key": key_name,
                    "title": field.get("title", key_name),
                    "target_sheet": current["sheet"],
                    "target_row": record["row"],
                    "target_col": current_col,
                    "target_cell": excel_cell(record["row"], current_col),
                    "current": encoded_current,
                    "raw_poison": encoded_raw,
                    "restore": latest["restore"],
                    "responsible": str(raw.get("responsible") or "").strip(),
                    **{key: value for key, value in latest.items() if key != "restore"},
                })

    writes.sort(key=lambda item: (item["target_row"], item["target_col"],
                                  item["contract_id"], item["key"]))
    return {
        "writes": writes,
        "cells_to_repair": len(writes),
        "contracts_to_repair": len({item["contract_id"] for item in writes}),
        "skipped_counts": dict(sorted(skipped.items())),
        "skipped_examples": examples,
    }


def scan_file(job: dict, fields: list[dict], trusted_ids: set[str] | None = None) -> dict:
    path = Path(job["path"]).resolve()
    result = {
        "employee": job["employee"],
        "path": str(path),
        "explicit_file": bool(job.get("explicit_file")),
        "risk_contract_ids": sorted({enrich.norm_cid(row.get(enrich.CONTRACT_KEY))
                                     for row in job["rows"]
                                     if enrich.norm_cid(row.get(enrich.CONTRACT_KEY))}),
        "risk_rows_fingerprint": risk_rows_fingerprint(job["rows"]),
        "errors": [],
        "warnings": [],
        "chain": [],
        "writes": [],
        "cells_to_repair": 0,
        "contracts_to_repair": 0,
        "skipped_counts": {},
        "skipped_examples": [],
    }
    if not path.exists():
        result["errors"].append("текущий файл недоступен")
        return result
    backups = discover_backups(path)
    if not backups:
        result["warnings"].append("нет копий «до обогащения»: доказательное восстановление невозможно")
    snapshots = []
    for item in [*backups, {"path": path, "stamp": None, "sequence": 0}]:
        snapshot_path = Path(item["path"])
        try:
            snapshot = read_workbook_snapshot(snapshot_path, fields)
        except Exception as exc:  # noqa: BLE001 — любой разрыв цепи запрещает применение
            result["errors"].append(f"не прочитан снимок {snapshot_path.name}: {exc}")
            return result
        snapshots.append(snapshot)
        result["chain"].append({
            "path": str(snapshot_path),
            "name": snapshot_path.name,
            "kind": "current" if snapshot_path == path else "before_enrichment",
            "stamp": item["stamp"].isoformat() if item.get("stamp") else None,
            "sequence": item.get("sequence", 0),
            "sha256": snapshot["file_sha256"],
            "size": snapshot.get("size"),
            "mtime_ns": snapshot.get("mtime_ns"),
            "sheet": snapshot["sheet"],
            "sheet_names": snapshot["sheet_names"],
        })
    result["current_sha256"] = snapshots[-1]["file_sha256"]
    result["current_size"] = snapshots[-1].get("size")
    result["current_mtime_ns"] = snapshots[-1].get("mtime_ns")
    result["current_sheet"] = snapshots[-1]["sheet"]
    plan = build_chain_plan(snapshots, fields, job["rows"], trusted_ids)
    result.update(plan)
    return result


# ---------------------------------------------------------------------------
# Отчёт и неизменяемый двухфазный план


def _report_hash(report: dict) -> str:
    payload = {key: value for key, value in report.items() if key != "plan_sha256"}
    return payload_sha256(payload)


def seal_report(report: dict) -> dict:
    report["plan_sha256"] = _report_hash(report)
    return report


def verify_report(report: dict) -> None:
    if report.get("format_version") != FORMAT_VERSION:
        raise ValueError("неподдерживаемая версия отчёта")
    expected = report.get("plan_sha256")
    if not expected or expected != _report_hash(report):
        raise ValueError("хеш плана не совпал: отчёт изменён или повреждён")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def report_paths(requested: str | None, stamp: str) -> tuple[Path, Path]:
    if requested:
        json_path = Path(requested)
        if json_path.suffix.lower() != ".json":
            json_path = json_path.with_suffix(".json")
    else:
        json_path = REPORT_DIR / f"false-enrichment-{stamp}.json"
    return json_path, json_path.with_suffix(".txt")


def human_report(report: dict, json_path: Path, *, applied: bool = False) -> str:
    mode = "ПРИМЕНЕНИЕ" if applied else "СУХОЙ ПРОГОН — ФАЙЛЫ НЕ ИЗМЕНЯЛИСЬ"
    lines = [
        f"Починка ложного обогащения — {mode}",
        f"Создано: {report['generated_at']}",
        f"Сервер: {report['server_url']}",
        f"Хеш плана: {report.get('plan_sha256', '<будет после сохранения>')}",
        "",
    ]
    counts = report.get("server_counts") or {}
    if counts:
        lines += [
            "Серверная граница:",
            f"  подтверждённых ботом договоров: {counts.get('trusted_contracts', 0)}",
            f"  неподтверждённых с непустой raw-датой: {counts.get('risky_contracts', 0)}",
            f"  из них с сотрудником: {counts.get('risky_named_contracts', 0)}",
            f"  без ответственного: {counts.get('risky_unassigned_contracts', 0)}",
            f"  сотрудников в зоне проверки: {counts.get('risky_employees', 0)}",
            "",
        ]
    employee_counts = report.get("risk_by_employee") or []
    if employee_counts:
        lines.append("Рисковые raw-строки по сотрудникам (это ещё не доказанный ущерб Excel):")
        for item in employee_counts:
            extra = (f", Excel-серийников {item.get('numeric_excel_serial_dates', 0)}, "
                     f"обычных дат {item.get('parseable_dates', 0)}")
            lines.append(f"  {item['employee']}: {item['contracts']}{extra}")
        lines.append("")
    warnings = report.get("warnings") or []
    if warnings:
        lines.append("Предупреждения обнаружения:")
        lines.extend(f"  [!] {warning}" for warning in warnings)
        lines.append("")

    total_cells = total_contracts = 0
    for file_plan in report.get("files") or []:
        cells = file_plan.get("cells_to_repair", 0)
        contracts = file_plan.get("contracts_to_repair", 0)
        total_cells += cells
        total_contracts += contracts
        lines.append(f"{file_plan['employee']} — {file_plan['path']}")
        lines.append(f"  цепочка: копий {max(0, len(file_plan.get('chain', [])) - 1)}, "
                     f"договоров к починке {contracts}, ячеек {cells}")
        for error in file_plan.get("errors") or []:
            lines.append(f"  [ОШИБКА, ФАЙЛ ЗАПРЕЩЁН] {error}")
        for warning in file_plan.get("warnings") or []:
            lines.append(f"  [!] {warning}")
        for write in file_plan.get("writes") or []:
            source = Path(write["source_snapshot"]).name
            poisoned = Path(write["poison_snapshot"]).name
            lines.append(
                f"  ПОЧИНИТЬ КД {write['contract_id']} · {write['target_cell']} · "
                f"{write['title']}: {value_display(write['current'])} -> "
                f"{value_display(write['restore'])} "
                f"[переход {source} -> {poisoned}]"
            )
        skipped = file_plan.get("skipped_counts") or {}
        if skipped:
            lines.append("  не трогать: " + ", ".join(f"{key}={value}"
                                                       for key, value in skipped.items()))
        lines.append("")
    lines += [
        f"ИТОГО: договоров {total_contracts}, ячеек {total_cells}, файлов с изменениями "
        f"{sum(1 for item in report.get('files', []) if item.get('writes'))}.",
    ]
    if not applied:
        lines += [
            "Ни один Excel-файл этим запуском не изменён.",
            f"Применить только этот проверенный план:",
            f"  python repair_false_enrichment.py --apply \"{json_path}\"",
        ]
    else:
        lines.append("Результаты по файлам:")
        for item in report.get("results") or []:
            suffix = f"; backup={item['backup']}" if item.get("backup") else ""
            reason = f"; причина={item['reason']}" if item.get("reason") else ""
            lines.append(f"  {item.get('status')}: {item.get('path')}{suffix}{reason}")
        lines.append(f"Результат применения: {report.get('apply_summary', {})}")
    return "\n".join(lines) + "\n"


def write_report(report: dict, json_path: Path, text_path: Path, *, applied: bool = False) -> None:
    seal_report(report)
    _atomic_write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(text_path, human_report(report, json_path, applied=applied))


def plan_signature(file_plan: dict) -> str:
    relevant = {
        "path": file_plan.get("path"),
        "current_sha256": file_plan.get("current_sha256"),
        "risk_rows_fingerprint": file_plan.get("risk_rows_fingerprint"),
        "writes": file_plan.get("writes") or [],
        "errors": file_plan.get("errors") or [],
    }
    return payload_sha256(relevant)


# ---------------------------------------------------------------------------
# Атомарная установка уже проверенной Excel-копии


def repair_backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%d.%m.%Y %H-%M-%S")
    candidate = path.with_name(
        f"{path.stem} (до починки ложного обогащения {stamp}){path.suffix}")
    sequence = 2
    while candidate.exists():
        candidate = path.with_name(
            f"{path.stem} (до починки ложного обогащения {stamp}, {sequence}){path.suffix}")
        sequence += 1
    return candidate


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def install_verified_copy(original: Path, prepared: Path, expected_original_sha256: str,
                          *, backup: Path | None = None) -> Path:
    """Атомарно поставить prepared; тестируется отдельно от Excel.

    До os.replace оригинал не меняется. Его backup сначала также собирается во
    временном файле и проверяется по SHA-256, поэтому половинчатой копии не бывает.
    """
    if sha256_file(original) != expected_original_sha256:
        raise RuntimeError("оригинал изменился после dry-run; установка запрещена")
    if not prepared.exists():
        raise FileNotFoundError(f"проверенная временная копия исчезла: {prepared}")
    backup = backup or repair_backup_path(original)
    staging = original.with_name(
        f"{original.stem}.__backup__{uuid.uuid4().hex}{original.suffix}")
    try:
        shutil.copy2(original, staging)
        if sha256_file(staging) != expected_original_sha256:
            raise RuntimeError("контрольная сумма резервной копии не совпала")
        os.replace(staging, backup)
        if sha256_file(original) != expected_original_sha256:
            raise RuntimeError("оригинал изменился во время подготовки; установка запрещена")
        os.replace(prepared, original)
        return backup
    finally:
        _safe_unlink(staging)


def _grid_value(grid: list[list], row: int, col: int) -> Any:
    return grid[row][col] if row < len(grid) and col < len(grid[row]) else None


def verify_prepared_copy(before: dict, after: dict, writes: list[dict]) -> None:
    if before["sheet_names"] != after["sheet_names"]:
        raise RuntimeError("набор/порядок листов временной копии изменился")
    if before["sheet"] != after["sheet"]:
        raise RuntimeError("Excel выбрал другой лист реестра во временной копии")
    targets = {(write["target_row"], write["target_col"]): write for write in writes}
    before_grid, after_grid = before["grid"], after["grid"]
    max_rows = max(len(before_grid), len(after_grid))
    unexpected = []
    for row in range(max_rows):
        before_width = len(before_grid[row]) if row < len(before_grid) else 0
        after_width = len(after_grid[row]) if row < len(after_grid) else 0
        for col in range(max(before_width, after_width)):
            old = _grid_value(before_grid, row, col)
            new = _grid_value(after_grid, row, col)
            write = targets.get((row, col))
            if write:
                if not logical_equal(new, decode_value(write["restore"])):
                    raise RuntimeError(
                        f"проверка {write['target_cell']} не прошла: "
                        f"получено {value_display(new)}, ожидалось {value_display(write['restore'])}")
            elif not logical_equal(old, new):
                unexpected.append(f"{excel_cell(row, col)}: {value_display(old)} -> {value_display(new)}")
                if len(unexpected) >= 20:
                    break
        if len(unexpected) >= 20:
            break
    if unexpected:
        raise RuntimeError("Excel изменил посторонние ячейки; оригинал сохранён. "
                           + "; ".join(unexpected))


def prepare_excel_copy(app, file_plan: dict, fields: list[dict]) -> Path:
    original = Path(file_plan["path"])
    expected = file_plan["current_sha256"]
    if sha256_file(original) != expected:
        raise RuntimeError("файл изменился после повторной сверки")
    before = read_workbook_snapshot(original, fields)
    temp = original.with_name(
        f"{original.stem}.__repair__{uuid.uuid4().hex}{original.suffix}")
    shutil.copy2(original, temp)
    if sha256_file(temp) != expected:
        _safe_unlink(temp)
        raise RuntimeError("временная копия отличается от оригинала до записи")

    workbook = None
    try:
        workbook = app.books.open(str(temp), update_links=False, read_only=False,
                                  ignore_read_only_recommended=True)
        worksheet = enrich._pick_sheet(workbook, fields)
        if worksheet.name != file_plan["current_sheet"]:
            raise RuntimeError("Excel выбрал не тот лист")
        for write in file_plan["writes"]:
            cell = worksheet.range((write["target_row"] + 1, write["target_col"] + 1))
            actual = cell.value
            if not poison_equal(write["key"], actual, decode_value(write["raw_poison"])):
                raise RuntimeError(
                    f"{write['target_cell']} уже изменена: {value_display(actual)}")
            cell.value = decode_value(write["restore"])
        workbook.save()
        workbook.close()
        workbook = None
        after = read_workbook_snapshot(temp, fields)
        verify_prepared_copy(before, after, file_plan["writes"])
        if sha256_file(original) != expected:
            raise RuntimeError("оригинал изменился, пока проверялась временная копия")
        return temp
    except Exception:
        if workbook is not None:
            try:
                workbook.close()
            except Exception:  # noqa: BLE001
                pass
        _safe_unlink(temp)
        raise


# ---------------------------------------------------------------------------
# CLI: dry-run и применение отчёта


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def credentials(args, report: dict | None = None) -> tuple[str, str]:
    settings = load_settings()
    url = args.url or (report or {}).get("server_url") or settings.get("url") or DEFAULT_URL
    key = args.key or os.getenv("HARDBOT_ADMIN_KEY") or settings.get("key") or ""
    if not key:
        raise ValueError("нет admin key: сохрани его в настройках пульта или HARDBOT_ADMIN_KEY")
    return str(url), str(key)


def dry_run(args) -> int:
    url, key = credentials(args)
    print(f"Сервер: {url}")
    print("Забираю safe + raw снимки…")
    evidence = fetch_server_evidence(url, key, args.timeout)
    base = args.base or load_settings().get("scatterBase") or None
    jobs, warnings, roots = collect_jobs(
        evidence, base=base, roots=args.root, config=args.config,
        files=args.file, only=args.only,
    )
    print(f"Неподтверждённых raw-дат: {evidence['counts']['risky_contracts']}; "
          f"сотрудников: {evidence['counts']['risky_employees']}; файлов найдено: {len(jobs)}")
    file_plans = []
    for index, job in enumerate(jobs, 1):
        print(f"[{index}/{len(jobs)}] {job['employee']} — {Path(job['path']).name}")
        plan = scan_file(job, evidence["fields"], evidence["trusted_ids"])
        file_plans.append(plan)
        print(f"    копий: {max(0, len(plan['chain']) - 1)}, "
              f"договоров: {plan['contracts_to_repair']}, ячеек: {plan['cells_to_repair']}"
              + (f", ошибок: {len(plan['errors'])}" if plan["errors"] else ""))

    generated = datetime.now().astimezone()
    stamp = generated.strftime("%Y%m%d-%H%M%S")
    json_path, text_path = report_paths(args.report, stamp)
    report = {
        "format_version": FORMAT_VERSION,
        "kind": "false_enrichment_dry_run",
        "generated_at": generated.isoformat(timespec="seconds"),
        "server_url": url,
        "server_fingerprint": evidence["server_fingerprint"],
        "server_counts": evidence["counts"],
        "risk_by_employee": employee_risk_summary(evidence["by_employee"]),
        "requested_only": args.only or [],
        "roots": roots,
        "warnings": warnings,
        "files": file_plans,
    }
    write_report(report, json_path, text_path)
    total_cells = sum(item["cells_to_repair"] for item in file_plans)
    total_files = sum(bool(item["writes"]) for item in file_plans)
    print(f"\nСухой прогон завершён: файлов к починке {total_files}, ячеек {total_cells}.")
    print(f"TXT:  {text_path}")
    print(f"JSON: {json_path}")
    print("Excel-файлы не изменялись.")
    return 0


def _rows_for_report_file(file_plan: dict, evidence: dict) -> list[dict]:
    rows = []
    for cid in file_plan.get("risk_contract_ids") or []:
        row = evidence["raw_by_cid"].get(cid)
        # Подтверждённый после dry-run визит намеренно исчезает из нового плана.
        if row is not None and cid not in evidence["trusted_ids"]:
            rows.append(row)
    return rows


def assert_contracts_still_untrusted(url: str, key: str, file_plan: dict,
                                     timeout: int = 120) -> None:
    """Последний provenance-затвор сразу перед os.replace конкретного файла."""
    payload = _request_json(url.rstrip("/") + "/admin/export.json", key, timeout)
    rows = payload.get("rows") or []
    if rows and not all(enrich.FILLED_MARKER in row for row in rows):
        raise RuntimeError("сервер перестал выдавать provenance-маркер")
    by_cid = {enrich.norm_cid(row.get(enrich.CONTRACT_KEY)): row for row in rows
              if enrich.norm_cid(row.get(enrich.CONTRACT_KEY))}
    targets = {write["contract_id"] for write in file_plan.get("writes") or []}
    missing = sorted(cid for cid in targets if cid not in by_cid)
    trusted = sorted(cid for cid in targets
                     if by_cid.get(cid, {}).get(enrich.FILLED_MARKER) is True)
    unknown = sorted(cid for cid in targets
                     if cid in by_cid and by_cid[cid].get(enrich.FILLED_MARKER) is not False
                     and cid not in trusted)
    if missing or trusted or unknown:
        parts = []
        if trusted:
            parts.append("после dry-run завершён визит: " + ", ".join(trusted[:20]))
        if missing:
            parts.append("КД исчезли из safe-выдачи: " + ", ".join(missing[:20]))
        if unknown:
            parts.append("неясный provenance: " + ", ".join(unknown[:20]))
        raise RuntimeError("; ".join(parts))


def apply_report(args) -> int:
    source_path = Path(args.apply)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    verify_report(source)
    if source.get("kind") != "false_enrichment_dry_run":
        raise ValueError("--apply принимает только dry-run отчёт починки")
    url, key = credentials(args, source)
    print(f"План: {source_path}")
    print(f"Хеш: {source['plan_sha256']}")
    print("Повторно сверяю safe + raw сервер и каждый Excel-файл…")
    evidence = fetch_server_evidence(url, key, args.timeout)
    ready: list[dict] = []
    results: list[dict] = []
    for saved in source.get("files") or []:
        if not saved.get("writes"):
            continue
        rows = _rows_for_report_file(saved, evidence)
        current_job = {
            "employee": saved["employee"], "path": Path(saved["path"]),
            "rows": rows, "explicit_file": saved.get("explicit_file", False),
        }
        fresh = scan_file(current_job, evidence["fields"], evidence["trusted_ids"])
        if plan_signature(saved) != plan_signature(fresh):
            results.append({"path": saved["path"], "status": "skipped",
                            "reason": "сервер, файл или доказательный план изменился"})
            print(f"[НЕ ТРОНУТ] {saved['path']}: план изменился")
            continue
        ready.append(fresh)

    if not ready:
        print("Нет файлов с неизменившимся планом; Excel не запускался.")
    else:
        enrich._co_init()
        import xlwings as xw

        with enrich.open_excel(xw, lambda message: print(" ", message)) as app:
            app.display_alerts = False
            app.screen_updating = False
            previous_calculation = None
            try:
                previous_calculation = app.calculation
                app.calculation = "manual"
            except Exception:  # noqa: BLE001
                previous_calculation = None
            try:
                for index, file_plan in enumerate(ready, 1):
                    path = Path(file_plan["path"])
                    print(f"[{index}/{len(ready)}] временная копия: {path.name}")
                    prepared = None
                    installed = False
                    try:
                        prepared = prepare_excel_copy(app, file_plan, evidence["fields"])
                        prepared_hash = sha256_file(prepared)
                        # Пока Excel готовил temp, сотрудник мог завершить настоящий
                        # визит. Проверяем маркер ещё раз за мгновение до установки.
                        assert_contracts_still_untrusted(
                            url, key, file_plan, timeout=args.timeout)
                        backup = install_verified_copy(
                            path, prepared, file_plan["current_sha256"])
                        installed = True
                        prepared = None  # os.replace уже перенёс файл
                        if sha256_file(path) != prepared_hash:
                            raise RuntimeError("контрольная сумма установленного файла не совпала")
                        results.append({
                            "path": str(path), "status": "applied",
                            "backup": str(backup),
                            "cells": file_plan["cells_to_repair"],
                            "contracts": file_plan["contracts_to_repair"],
                            "installed_sha256": prepared_hash,
                        })
                        print(f"    готово: ячеек {file_plan['cells_to_repair']}; копия {backup.name}")
                    except Exception as exc:  # noqa: BLE001 — один файл не ставит под риск остальные
                        results.append({"path": str(path), "status": "error", "reason": str(exc)})
                        state = "ФАЙЛ УЖЕ УСТАНОВЛЕН, ПРОВЕРЬ BACKUP" if installed \
                            else "ОРИГИНАЛ НЕ ТРОНУТ"
                        print(f"    [ОШИБКА, {state}] {exc}")
                    finally:
                        if prepared is not None:
                            _safe_unlink(prepared)
            finally:
                if previous_calculation is not None:
                    try:
                        app.calculation = previous_calculation
                    except Exception:  # noqa: BLE001
                        pass

    applied_count = sum(item["status"] == "applied" for item in results)
    error_count = sum(item["status"] == "error" for item in results)
    skipped_count = sum(item["status"] == "skipped" for item in results)
    generated = datetime.now().astimezone()
    apply_report_data = {
        "format_version": FORMAT_VERSION,
        "kind": "false_enrichment_apply_result",
        "generated_at": generated.isoformat(timespec="seconds"),
        "server_url": url,
        "source_report": str(source_path),
        "source_plan_sha256": source["plan_sha256"],
        "server_counts": evidence["counts"],
        "warnings": [],
        "files": [],
        "results": results,
        "apply_summary": {"applied": applied_count, "errors": error_count,
                          "skipped": skipped_count},
    }
    result_json = source_path.with_name(
        f"{source_path.stem}.apply-{generated.strftime('%Y%m%d-%H%M%S')}.json")
    result_txt = result_json.with_suffix(".txt")
    write_report(apply_report_data, result_json, result_txt, applied=True)
    print(f"\nПрименено: {applied_count}; ошибок: {error_count}; пропущено: {skipped_count}.")
    print(f"Отчёт применения: {result_txt}")
    return 0 if error_count == 0 and skipped_count == 0 else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Доказательная точечная починка ложного обогащения Excel")
    parser.add_argument("--base", help="папка, внутри которой лежат папки республик")
    parser.add_argument("--root", action="append", default=[],
                        help="прямой корень портфелей; можно повторять")
    parser.add_argument("--file", action="append", default=[],
                        help="проверить один явный живой Excel-файл; можно повторять")
    parser.add_argument("--only", action="append", default=[],
                        help="ограничить dry-run сотрудником; можно повторять")
    parser.add_argument("--config", help="конфигурация путей республик")
    parser.add_argument("--report", help="куда сохранить JSON dry-run (TXT будет рядом)")
    parser.add_argument("--apply", metavar="REPORT_JSON",
                        help="применить ранее проверенный JSON-план")
    parser.add_argument("--url", help="URL hardbot2 (обычно берётся из настроек)")
    parser.add_argument("--key", help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return apply_report(args) if args.apply else dry_run(args)
    except KeyboardInterrupt:
        print("Прервано. Оригиналы, не прошедшие атомарную установку, не изменены.")
        return 130
    except Exception as exc:  # noqa: BLE001 — CLI должен завершиться понятным сообщением
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
