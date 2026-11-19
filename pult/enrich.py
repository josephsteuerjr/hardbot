"""
Обогащение реестра результатами выездов — движок пульта.

Проблема, которую решает: старый экспорт СОБИРАЛ ФАЙЛ ЗАНОВО из БД сервера —
то есть подменял живой реестр слепком последнего импорта (плюс терял чужие
листы, формулы и колонки, которых нет в схеме). Здесь наоборот: берём ЖИВОЙ
файл реестра и точечно дописываем в него ТОЛЬКО то, что заполнили выездники
через бота — только их кейсы, только заполненные столбцы визита. Всё
остальное (строки, столбцы, листы, форматирование, выпадающие списки) не
трогаем вообще: файл открывает и сохраняет живой Excel (xlwings/COM), а пишем
мы отдельными ячейками по точному плану.

Планировка отделена от записи и не требует Excel:
    build_plan(grid, fields, rows) -> план (какие ячейки чем заполнить)
    enrich_file(path, fields, rows) -> бэкап + запись плана через xlwings

Какие столбцы пишем — VISIT_KEYS (то, что реально заполняет миниапп + дата
визита). Кейс попадает в план, только если сервер прислал явное доказательство
завершённого визита (FILLED_MARKER) и валидную дату; ячейка пишется,
только если значение непустое и отличается от того, что уже стоит в файле.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_PROJECT_DIR=Path(__file__).resolve().parents[1]
if str(_PROJECT_DIR) not in sys.path:
    sys.path.append(str(_PROJECT_DIR))
from pult.core.manual_sync import track_automatic_file

# Один JSON-контракт живёт в build-context hardbot и теми же байтами читается
# операторским пультом. Жёстко проверяем origin модуля, чтобы окружение Python
# не подсунуло одноимённый пакет раньше checkout.
_HARDBOT_DIR = Path(__file__).resolve().parents[1] / "server"
if str(_HARDBOT_DIR) not in sys.path:
    sys.path.insert(0, str(_HARDBOT_DIR))

import exchange_contract as _exchange_contract  # noqa: E402

if Path(_exchange_contract.__file__).resolve() != (
    _HARDBOT_DIR / "exchange_contract.py"
).resolve():
    raise RuntimeError("exchange_contract imported outside the hardbot checkout")

from exchange_contract import (  # noqa: E402
    CONTRACT_KEY,
    ExchangeContractError,
    ENRICHMENT_EVIDENCE_SCHEMA,
    ENRICHMENT_FILE_MAP_SCHEMA,
    ENRICHMENT_SHEET_NAME,
    ENRICHMENT_SOURCE_KEYS,
    ENRICHMENT_WRITE_RULES,
    IMPORT_SOURCE_PUBLISHED_MASTER,
    LEGACY_PUBLICATION_BOOTSTRAP,
    PUBLISHED_MASTER_SOURCE_ID,
    PROVENANCE_MARKER,
    SNAPSHOT_KIND,
    WORKBOOK_HEADER_ALIASES,
    WORKBOOK_HEADER_SEARCH_ROWS,
    build_published_master_source_proof,
    contract_binding,
    publication_sequence,
    validate_contract_binding,
    verify_export_schema,
)

SHEET_NAME = ENRICHMENT_SHEET_NAME
VISIT_KEYS = list(ENRICHMENT_SOURCE_KEYS)
WRITE_TARGETS = {
    source: target
    for source, target, _merge in ENRICHMENT_WRITE_RULES
    if source != target
}
MERGE_MODES = {
    source: merge
    for source, _target, merge in ENRICHMENT_WRITE_RULES
}
FILLED_MARKER = PROVENANCE_MARKER
# Диагностический маркер сервера (`db.VISIT_COMMENT_CLEARED_MARKER`): выездник
# комментарий написал, а живое поле пусто — его очистили поверх визита. В план
# такой комментарий не попадёт никогда (пустое значение не пишем), поэтому
# считаем такие кейсы отдельно и называем вслух. В контракт обмена маркер не
# входит: старый сервер его не отдаёт, и счётчик просто остаётся нулём.
COMMENT_CLEARED_MARKER = "_visit_comment_cleared"
EVIDENCE_SCHEMA = ENRICHMENT_EVIDENCE_SCHEMA
FILE_MAP_SCHEMA = ENRICHMENT_FILE_MAP_SCHEMA
DATA_EXCHANGE_CONTRACT = contract_binding()


def validate_server_export(payload: Any) -> None:
    """Reject stale pult/server combinations before preview or file access."""

    if not isinstance(payload, dict):
        raise ValueError("сервер вернул не JSON-объект выгрузки")
    try:
        validate_contract_binding(payload.get("data_exchange_contract"))
        verify_export_schema(payload.get("fields"))
    except ExchangeContractError as exc:
        raise ValueError(f"несовместимый контракт сервера/пульта: {exc}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _transport_cell(value: Any) -> Any:
    if isinstance(value, datetime):
        text = value.isoformat(sep=" ", timespec="seconds")
        return text[:-9] if text.endswith(" 00:00:00") else text
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def read_stable_workbook(workbook_path: str | Path) -> dict[str, Any]:
    """Parse one immutable workbook copy and return exact transport sheets."""

    from python_calamine import CalamineWorkbook

    workbook_path = Path(workbook_path)
    if workbook_path.is_symlink() or not workbook_path.is_file():
        raise ValueError(
            f"workbook не является обычным файлом: {workbook_path}"
        )
    selected_sha_before = _sha256_file(workbook_path)
    with tempfile.TemporaryDirectory(
        prefix="hardbot-master-read-",
        ignore_cleanup_errors=True,
    ) as temp_dir:
        immutable_copy = Path(temp_dir) / (
            "workbook" + workbook_path.suffix.lower()
        )
        shutil.copyfile(workbook_path, immutable_copy)
        copied_sha = _sha256_file(immutable_copy)
        if copied_sha != selected_sha_before:
            raise ValueError(
                "workbook изменился во время создания read-only копии"
            )
        workbook = CalamineWorkbook.from_path(str(immutable_copy))
        names = list(workbook.sheet_names)
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                "книга содержит повторяющиеся имена листов: "
                + ", ".join(duplicates)
            )
        sheets = []
        for name in names:
            rows = workbook.get_sheet_by_name(name).to_python()
            grid = [
                [_transport_cell(cell) for cell in row]
                for row in rows
            ]
            if grid:
                sheets.append({"name": name, "grid": grid})
    selected_sha_after = _sha256_file(workbook_path)
    if selected_sha_after != selected_sha_before:
        raise ValueError("workbook изменился во время стабильного чтения")
    if not sheets:
        raise ValueError("в workbook нет читаемых непустых листов")
    return {
        "sheets": sheets,
        "file_sha256": selected_sha_after,
    }


def prepare_published_master_import(
    master_path: str | Path,
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Parse one immutable copy and bind its grid to the latest publication."""

    master = Path(master_path)
    stable = read_stable_workbook(master)
    sheets = stable["sheets"]
    selected_sha_after = stable["file_sha256"]
    exact = [
        sheet["grid"]
        for sheet in sheets
        if sheet["name"] == SHEET_NAME
    ]
    if len(exact) != 1:
        raise ValueError(
            f"обязательный единственный лист «{SHEET_NAME}» отсутствует"
        )
    proof = _published_master_source_proof(
        master,
        exact[0],
        parsed_file_sha256=selected_sha_after,
        workspace=workspace,
    )
    return {
        "sheets": sheets,
        "source_proof": proof,
        "file_sha256": selected_sha_after,
    }


def _published_master_source_proof(
    master_path: str | Path,
    grid: list[list],
    *,
    parsed_file_sha256: str,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Find and verify the registry-cycle receipt for exact selected bytes.

    The registry cycle remains self-contained: this function only consumes its
    durable stage/publish/journal artifacts and never starts or repeats it.
    """

    master = Path(master_path)
    if (
        master.is_symlink()
        or not master.is_file()
        or not master.resolve().is_file()
    ):
        raise ValueError(f"master не является обычным файлом: {master}")
    root = (
        Path(workspace)
        if workspace is not None
        else _HARDBOT_DIR.parent / "_local" / "registry_cycle_v2"
    )
    if not root.is_dir() or root.is_symlink():
        raise ValueError(
            "не найден workspace registry_cycle с publish receipt; "
            "сначала выполните canonical publish"
        )
    # Never fall back to a previous committed master while a later publish is
    # unresolved: personal files may already be partly replaced even if the
    # master itself still has the old hash. Reuse registry_cycle's own journal
    # authority instead of duplicating its terminal-event rules here.
    checkout_root = _HARDBOT_DIR.parent
    if str(checkout_root) not in sys.path:
        sys.path.insert(0, str(checkout_root))
    try:
        from registry_cycle.errors import SafetyViolation
        from registry_cycle.publishing import assert_no_incomplete_publications

        assert_no_incomplete_publications(root)
    except SafetyViolation as exc:
        # ПРЕДУПРЕЖДЕНИЕ, а не отказ (03.08.2026, решение владельца). Проверка
        # смотрит на ВЕСЬ workspace: одна зависшая репетиция месячной давности
        # запрещала импорт любого мастера, включая тот, чья публикация завершилась
        # штатно. Сама публикация выбранного файла ниже проверяется отдельно и
        # по-прежнему обязана быть complete с terminal receipt.
        logging.warning(
            "в workspace registry_cycle есть незавершённая публикация (%s); "
            "выбранный master проверяется отдельно и должен быть complete", exc)
    selected_key = str(master.resolve()).casefold()
    selected_sha = _sha256_file(master)
    if selected_sha != parsed_file_sha256:
        raise ValueError(
            "master изменился после стабильного чтения workbook grid"
        )
    candidates: list[tuple[int, str, str, str, dict[str, Any]]] = []
    for stage_path in root.glob("runs/**/stage.json"):
        try:
            if stage_path.is_symlink() or not stage_path.is_file():
                continue
            stage = json.loads(stage_path.read_text(encoding="utf-8"))
            run_dir = stage_path.parent.parent
            publish_path = run_dir / "publish.json"
            journal_path = run_dir / "publish.journal.jsonl"
            if any(
                path.is_symlink() or not path.is_file()
                for path in (publish_path, journal_path)
            ):
                continue
            master_item = stage["master"]
            master_key = str(
                Path(master_item["original_path"]).resolve()
            ).casefold()
            run_id = str(stage["run_id"])
            plan_digest = str(stage["plan_digest"])
            staged_sha = str(master_item["staged_fingerprint"]["sha256"])
            publish = json.loads(publish_path.read_text(encoding="utf-8"))
            if (
                publish.get("run_id") != run_id
                or publish.get("plan_digest") != plan_digest
                or publish.get("complete") is not True
                or publish.get("ok") is not True
                or publish.get("sidecars") != []
                or not publish.get("finished_at")
                or not publish.get("baseline")
                or master_key
                not in {
                    str(Path(value).resolve()).casefold()
                    for value in publish.get("published") or []
                }
            ):
                continue
            finished_at = str(publish["finished_at"])
            sequence = publication_sequence(finished_at)
            baseline_recorded = Path(str(publish["baseline"]))
            baseline_paths = (
                baseline_recorded,
                root / "baselines" / baseline_recorded.name,
            )
            baseline_path = next(
                (
                    path
                    for path in baseline_paths
                    if path.is_file() and not path.is_symlink()
                ),
                None,
            )
            if baseline_path is None:
                continue
            baseline_sha = _sha256_file(baseline_path)
            with gzip.open(baseline_path, "rt", encoding="utf-8") as stream:
                baseline = json.load(stream)
            if (
                not isinstance(baseline, dict)
                or baseline.get("plan_digest") != plan_digest
                or str(baseline.get("master_path") or "").casefold()
                != str(master_item["original_path"]).casefold()
            ):
                continue
            records = []
            malformed = False
            for line in journal_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    malformed = True
                    break
                if not isinstance(record, dict):
                    malformed = True
                    break
                records.append(record)
            if malformed:
                continue
            starts = [
                index
                for index, record in enumerate(records)
                if record.get("event") == "publish_started"
            ]
            if not starts:
                continue
            tail = records[starts[-1]:]
            modern_terminal = any(
                record.get("event") == "publish_committed"
                and record.get("run_id") == run_id
                and record.get("plan_digest") == plan_digest
                for record in tail
            )
            publish_sha = _sha256_file(publish_path)
            stage_sha = _sha256_file(stage_path)
            journal_sha = _sha256_file(journal_path)
            legacy_identity = {
                "run_id": run_id,
                "plan_digest": plan_digest,
                "publish_receipt_sha256": publish_sha,
                "stage_receipt_sha256": stage_sha,
                "journal_sha256": journal_sha,
                "baseline_sha256": baseline_sha,
                "master_sha256": staged_sha,
            }
            legacy_terminal = (
                bool(tail)
                and tail[-1].get("event") == "publish_files_completed"
                and legacy_identity in LEGACY_PUBLICATION_BOOTSTRAP
            )
            if not modern_terminal and not legacy_terminal:
                continue
            publication = {
                "run_id": run_id,
                "source_id": PUBLISHED_MASTER_SOURCE_ID,
                "publication_seq": sequence,
                "plan_digest": plan_digest,
                "finished_at": finished_at,
                "publish_receipt_sha256": publish_sha,
                "stage_receipt_sha256": stage_sha,
                "journal_sha256": journal_sha,
                "baseline_sha256": baseline_sha,
                "master_sha256": staged_sha,
            }
            candidates.append(
                (sequence, run_id, master_key, staged_sha, publication)
            )
        except (
            KeyError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ExchangeContractError,
        ):
            continue
    if not candidates:
        raise ValueError(
            "для выбранного master не найден complete registry_cycle publish "
            "с terminal receipt и проверяемым active baseline"
        )
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    latest_sequence = candidates[0][0]
    latest = [
        item for item in candidates if item[0] == latest_sequence
    ]
    if len({(item[1], item[2]) for item in latest}) != 1:
        raise ValueError(
            "несколько публикаций master имеют один monotonic sequence"
        )
    (
        _sequence,
        _run_id,
        latest_master_key,
        latest_master_sha,
        publication,
    ) = latest[0]
    # Решает СОВПАДЕНИЕ БАЙТОВ, а не путь (03.08.2026, решение владельца).
    # Раньше сверялось и то и другое, и копия опубликованного мастера, положенная
    # рядом с пультом, переставала быть опубликованным мастером: расписка держит
    # `U:\rbr\…`, а сетевого диска на домашней машине нет. Байты те же — значит это
    # тот же файл, и подменить его нельзя: SHA сверяется с застейдженным.
    # Путь остаётся диагностикой: расхождение видно в предупреждении.
    if selected_sha != latest_master_sha:
        raise ValueError(
            "выбранный master не совпадает с новейшей complete публикацией"
        )
    if selected_key != latest_master_key:
        logging.warning(
            "published master найден по SHA, но лежит не там, где публиковался: "
            "%s вместо %s", selected_key, latest_master_key)
    return build_published_master_source_proof(
        grid,
        file_sha256=selected_sha,
        publication=publication,
    )

# --- Нормализация заголовков (зеркало db._norm_title — держать в синхроне!) --
_ZERO_WIDTH = ("​", "‌", "‍", "﻿")
_HOMOGLYPHS = str.maketrans("acekmoptxy", "асекмортху")


def norm_title(s) -> str:
    """Регистр, юникод-пробелы, невидимые символы, латиница-двойники («Cтатус
    звонка» с латинской C), хвостовая пунктуация — как в db._norm_title."""
    if s in (None, ""):
        return ""
    t = str(s)
    for zw in _ZERO_WIDTH:
        t = t.replace(zw, "")
    t = re.sub(r"\s+", " ", t.strip().lower()).translate(_HOMOGLYPHS)
    return t.rstrip(":;*.")


def norm_name(s) -> str:
    """ФИО для матчинга с папками сотрудников (как в razbros): схлоп пробелов, lower."""
    return re.sub(r"\s+", " ", str(s or "").replace(" ", " ").strip()).lower()


def norm_cid(v) -> str:
    """Номер КД для матчинга: 12345.0 (float из Excel) → «12345», пробелы прочь."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def parse_date(v):
    """ISO / дд.мм.гггг / дд.мм.гг / datetime → date, иначе None."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    core = s.split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(core, fmt).date()
        except ValueError:
            continue
    return None


def cell_text(v) -> str:
    """Значение ячейки → сравнимый текст: даты в ISO (полночь режем), целые
    float без «.0», строки со схлопнутыми краями. None/'' → ''."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _values_equal(file_val, new_val, is_date: bool) -> bool:
    if is_date:
        fd, nd = parse_date(file_val), parse_date(new_val)
        if fd or nd:
            return fd == nd
    return cell_text(file_val) == cell_text(new_val)


def _line_identity(value: str) -> tuple[str, str]:
    """Идентичность строки для идемпотентного дополнения наводок.

    Чистый телефон сравниваем по последним девяти цифрам, чтобы
    ``+998 93 ...`` не продублировался рядом с локальной записью ``93...``.
    Произвольный текст сравниваем без регистра и лишних пробелов.
    """
    text = re.sub(r"\s+", " ", value.strip())
    digits = "".join(ch for ch in text if ch.isdigit())
    if 7 <= len(digits) <= 15 and re.fullmatch(r"[\d+\s()./\-]+", text):
        return "phone", digits[-9:]
    return "text", text.casefold()


def merge_unique_lines(current, incoming) -> str:
    """Сохранить старый «Доп. Адрес/Доп.Номер» и дописать новые строки один раз."""
    cur = cell_text(current).replace("\r\n", "\n").replace("\r", "\n")
    new = cell_text(incoming).replace("\r\n", "\n").replace("\r", "\n")
    incoming_lines = [line.strip() for line in new.split("\n") if line.strip()]
    if not incoming_lines:
        return cur
    existing = {_line_identity(line) for line in cur.split("\n") if line.strip()}
    additions = []
    for line in incoming_lines:
        identity = _line_identity(line)
        if identity not in existing:
            additions.append(line)
            existing.add(identity)
    if not additions:
        return cur
    return (cur.rstrip() + ("\n" if cur.strip() else "") + "\n".join(additions))


def merge_preserving_text(current, incoming) -> str:
    """Добавить комментарий выезда, не стирая уже написанное в живом файле.

    Ручной офисный текст может быть новее серверного снимка, поэтому непустую
    ячейку обычное обогащение не заменяет. Если один текст уже целиком содержит
    другой, оставляем более полный вариант; независимые тексты соединяем с
    новой строки. Повторный запуск идемпотентен.
    """
    cur = cell_text(current).replace("\r\n", "\n").replace("\r", "\n")
    new = cell_text(incoming).replace("\r\n", "\n").replace("\r", "\n")
    if not cur:
        return new
    if not new:
        return cur

    def identity(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    cur_id, new_id = identity(cur), identity(new)
    if cur_id == new_id or new_id in cur_id:
        return cur
    if cur_id in new_id:
        return new
    return cur.rstrip() + "\n" + new


def find_header(grid: list[list], fields: list[dict]) -> tuple[int, dict]:
    """(индекс строки заголовков, {key: индекс колонки}) — как в db.import_grid:
    лучшая из первых 10 строк, обязательное условие — колонка-ключ «Номер КД / ID»."""
    title_to_key = {norm_title(f["title"]): f["key"] for f in fields}
    for alias_key, aliases in WORKBOOK_HEADER_ALIASES.items():
        for alias in aliases:
            normalized = norm_title(alias)
            previous = title_to_key.get(normalized)
            if previous is not None and previous != alias_key:
                raise ValueError(
                    f"alias заголовка {alias!r} неоднозначен: "
                    f"{previous}/{alias_key}"
                )
            title_to_key[normalized] = alias_key
    best_h, best_map, best_positions, best_n, best_has_id = -1, {}, {}, -1, False
    for i, row in enumerate(grid[:WORKBOOK_HEADER_SEARCH_ROWS]):
        col_map = {}
        positions: dict[str, list[int]] = {}
        for j, cell in enumerate(row):
            key = title_to_key.get(norm_title(cell))
            if key:
                positions.setdefault(key, []).append(j)
                col_map.setdefault(key, j)
        has_id = CONTRACT_KEY in col_map
        if (has_id, len(col_map)) > (best_has_id, best_n):
            best_has_id, best_n, best_h, best_map, best_positions = (
                has_id,
                len(col_map),
                i,
                col_map,
                positions,
            )
    if not best_has_id:
        raise ValueError(
            "в листе нет колонки-ключа «Номер КД / ID» "
            f"(первые {WORKBOOK_HEADER_SEARCH_ROWS} строк)"
        )
    duplicate = {
        key: columns
        for key, columns in best_positions.items()
        if len(columns) > 1
    }
    if duplicate:
        raise ValueError(
            "неоднозначные повторные заголовки: "
            + ", ".join(
                f"{key} ({','.join(str(column + 1) for column in columns)})"
                for key, columns in sorted(duplicate.items())
            )
        )
    schema_keys = {field["key"] for field in fields}
    required_targets = {
        CONTRACT_KEY,
        *(
            target
            for _source, target, _merge in ENRICHMENT_WRITE_RULES
            if target in schema_keys
        ),
    }
    # Личные книги являются проекциями Хард/Софт, а не полным master.
    hard_only = {"visit_date", "who_at_address", "contact_with", "purpose_rf",
                 "client_location", "agreed_payment", "visit_comment"}
    soft_only = {"soft_call_status", "soft_comm_date", "soft_comment"}
    if not (hard_only & set(best_map)) and soft_only & set(best_map):
        required_targets -= hard_only | {"overall_result", "call_status", "add_address"}
    elif hard_only & set(best_map) and not ({"soft_call_status", "soft_comm_date"} & set(best_map)):
        required_targets -= soft_only
    required_targets.discard("meeting_date")
    required_targets -= {"last_payment_sum", "last_payment_date"}
    missing = sorted(required_targets - set(best_map))
    if missing:
        raise ValueError(
            "нет обязательных колонок enrichment-контракта: "
            + ", ".join(missing)
        )
    return best_h, best_map


def is_filled(row: dict) -> bool:
    """Только явно подтверждённый сервером визит с разбираемой датой."""
    return row.get(FILLED_MARKER) is True and parse_date(row.get("visit_date")) is not None


def soft_anchor(row: dict):
    """День фактического звонка. Следующий перезвон не является датой факта."""
    return parse_date(row.get("_call_day"))


def is_soft_filled(row: dict) -> bool:
    """Результат Софта: доказанный звонок или запись оплаты с днём факта.

    Снимка визита у софт-кейса нет и не будет — доказательство здесь другое:
    живая колонка месяца + окно по дню звонка + реестр доставленного."""
    return ((row.get("_bot_call_completed") is True and
             cell_text(row.get("soft_call_status")) != "" and soft_anchor(row) is not None)
            or (row.get("_bot_payment_recorded") is True and parse_date(row.get("_payment_day")) is not None))


# Что развозим по софт-книгам: колонки звонка + общие обещание/прогноз.
SOFT_CASE_KEYS = ("soft_call_status", "soft_comm_date", "soft_comment",
                  "promise_date", "forecast_sum", "forecast", "meeting_date", "soft_extra_phone")


# Сколько номеров КД показывать в предупреждении пульта: список нужен, чтобы
# «визиты в никуда» можно было проверить руками, а не поверить на слово.
ORPHAN_SAMPLE = 20


class EmployeeSplit(dict):
    """Разбивка по ответственным + счётчики «сирот» — строк без владельца.

    Это dict, а не новый тип: все прежние вызовы (items/values/get) работают как
    работали, а потери читаются атрибутами.
        orphan_rows   — строк сервера без «ответственного Сотрудника»;
        orphan_filled — из них с завершённым визитом бота: именно эти результаты
                        не попадут ни в один личный реестр, писать их некуда;
        orphan_cids   — первые ORPHAN_SAMPLE номеров КД таких визитов;
        orphan_dates  — (самый ранний, самый поздний) выезд среди них.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.orphan_rows = 0
        self.orphan_filled = 0
        self.orphan_cids: list[str] = []
        self.orphan_dates: tuple[str, str] | None = None


def latest_payment_pair(row: dict):
    """Latest stored payment, independent of the selected activity month.

    The historical _reported ledger keys also address the same two workbook
    cells for an imported registry payment. This does not change its source in
    payment_evidence or confirm it for KP.
    """
    candidates = []
    for prefix, origin in (("last_payment", "registry"), ("_reported_payment", "reported")):
        day = parse_date(row.get(prefix + "_date"))
        value = row.get(prefix + "_sum")
        if day is None or day > datetime.now(ZoneInfo("Asia/Tashkent")).date() or value in (None, ""):
            continue
        text = re.sub(r'[^\d.,-]', '', str(value))
        if ',' in text and '.' in text:
            separator = ',' if text.rfind(',') > text.rfind('.') else '.'
            text = text.replace('.' if separator == ',' else ',', '').replace(separator, '.')
        else:
            text = text.replace(',', '.')
        try:
            number = Decimal(text)
            amount = float(number.quantize(Decimal('0.01'))) if number.is_finite() else float('nan')
        except (InvalidOperation, TypeError, ValueError):
            continue
        if math.isfinite(amount) and 0 <= amount <= 1e9 and (origin != "reported" or amount > 0):
            candidates.append((day, amount, origin))
    if not candidates:
        return None
    latest = max(p[0] for p in candidates)
    selected = [p for p in candidates if p[0] == latest]
    if len({p[1] for p in selected}) != 1:
        # The canonical imported pair wins a same-day disagreement. Employee
        # evidence remains a separate immutable record/comment, never summed
        # into the registry amount and never re-labelled bank confirmation.
        return next(p for p in selected if p[2] == "registry")
    return selected[-1]


def split_by_employee(rows: list[dict]) -> EmployeeSplit:
    """Строки сервера → по ответственным: {ключ: {employee, rows, filled}}.
    filled — сколько кейсов сотрудника заполнено ботом (кандидаты на обогащение).

    Строки без ответственного не принадлежат никому: личного файла у них нет,
    поэтому в обогащение они не попадают. Раньше они отбрасывались МОЛЧА — так
    из выгрузки выпали 173 завершённых визита за 09-17.07, и жалоба Ильи о
    пропавших комментариях неделю висела без объяснения. Теперь они считаются."""
    out = EmployeeSplit()
    # Счётчик сирот идёт по ВСЕЙ базе, а окно дат — только по обогащению. Рядом
    # с окном «сегодня» число читалось как сегодняшняя авария (08.09.2026 так и
    # вышло: 342 августовских визита выглядели свежей поломкой), поэтому вместе
    # с числом запоминаем период самих выездов.
    seen_dates: list[str] = []
    for r in rows:
        emp = str(r.get("responsible") or "").strip()
        if not emp:
            out.orphan_rows += 1
            if is_filled(r):
                out.orphan_filled += 1
                d = parse_date(r.get("visit_date"))
                if d is not None:
                    seen_dates.append(d.strftime("%d.%m.%Y"))
                if len(out.orphan_cids) < ORPHAN_SAMPLE:
                    out.orphan_cids.append(norm_cid(r.get(CONTRACT_KEY)))
            continue
        e = out.setdefault(norm_name(emp), {"employee": emp, "rows": [], "filled": 0})
        e["rows"].append(r)
        if is_filled(r) or latest_payment_pair(r) is not None or (is_soft_filled(r) and any((cell_text(r.get(k)) or k in r.get("_enrichment_clear_keys", [])) and
                (r.get("_common_days") or {}).get(k) == r.get("_call_day")
                for k in ("promise_date", "forecast_sum", "forecast"))):
            e["filled"] += 1
    for r in rows:
        emp = str(r.get("responsible_soft") or "").strip()
        if not emp:
            continue
        key = "soft:" + norm_name(emp)
        e = out.setdefault(key, {"employee": emp, "profile": "soft", "rows": [], "filled": 0})
        e["rows"].append({**r, "_profile": "soft"})
        if is_soft_filled(r) or latest_payment_pair(r) is not None:
            e["filled"] += 1
    for key, e in out.items():
        if not key.startswith("soft:"):
            e["profile"] = "hard"
            e["rows"] = [{**r, "_profile": "hard"} for r in e["rows"]]
    if seen_dates:
        order = sorted(seen_dates, key=lambda s: (s[6:], s[3:5], s[:2]))
        out.orphan_dates = (order[0], order[-1])
    return out


def _is_source_row(row: list) -> bool:
    """Строка-пометка «сх/хард» под заголовками — не данные."""
    nonempty = [str(v).strip().lower() for v in row if v not in (None, "")]
    return bool(nonempty) and all(v in ("сх", "хард", "софт") for v in nonempty)


# ТЕХНИЧЕСКАЯ ПУСТОТА. В книге «Не обработан» означает «до этого кейса ещё не
# доходили» — это не чьё-то решение, а состояние по умолчанию. Запись поверх
# него ничего не вытесняет.
#
# Разница не косметическая. Сверка книг после прогона 04.08.2026 показала: из
# 595 свежих записей 84 легли поверх непустого, и почти все — поверх «Не
# обработан». Если считать их вытеснением, человек будет видеть десятки тревог
# за прогон, среди которых утонут настоящие потери — их было 28.
TECHNICAL_EMPTY = {"не обработан"}


def _is_technical_empty(value) -> bool:
    return cell_text(value).casefold() in TECHNICAL_EMPTY


def visit_window(since=None, until=None) -> tuple:
    """Границы окна обогащения как даты. Пустая граница — открытая сторона.

    Непустая, но неразобранная граница — ОШИБКА, а не «открытая сторона».
    Иначе опечатка в дате не сужала бы прогон и не отменяла его, а молча
    снимала единственный предохранитель: человек думает, что обогащает за
    сегодня, а переутверждается вся история.
    """
    out = []
    for label, raw in (("с", since), ("по", until)):
        text = str(raw or "").strip()
        if not text:
            out.append(None)
            continue
        parsed = parse_date(text)
        if parsed is None:
            raise ValueError(f"не понимаю дату «{text}» в границе «{label}» — "
                             f"нужен формат ДД.ММ.ГГГГ")
        out.append(parsed)
    if out[0] and out[1] and out[0] > out[1]:
        raise ValueError(f"начало окна ({out[0].isoformat()}) позже конца "
                         f"({out[1].isoformat()})")
    return out[0], out[1]


# --- Реестр доставленного (ledger) -------------------------------------------
#
# До сентября 2026 писатель сравнивал значение сервера с ячейкой файла и, если
# они различались, писал. Он не помнил, что САМ положил в книгу в прошлый раз,
# поэтому любая правка офиса после прогона выглядела как «файл отстал», и
# единственным предохранителем было окно дат, которое задаёт человек. 04.08
# без окна пропали правки по 28 договорам; 28.08 уже с окном прогон затёр
# 107 статусов через шлюз офисных правок.
#
# Теперь сервер ведёт реестр доставленного: для каждой пары (КД, поле) —
# значение, которое обогащение последний раз записало в книгу. План строится
# трёхсторонне (сервер S, файл F, доставлено D):
#
#     F == S            → ничего не делаем, реестр досеивается (D := S);
#     D нет, F пусто    → пишем (обычное первое обогащение);
#     D нет, F непусто  → КОНФЛИКТ без истории: не пишем, показываем;
#                         пишем только по явной галочке server_authoritative;
#     S == D, F != D    → правка офиса после доставки: не трогаем, показываем;
#     S != D, F == D    → сервер обновился, файл нетронут: пишем;
#     S != D, F != D    → правили обе стороны: КОНФЛИКТ, не пишем, показываем.
#
# Реестр действует только на колонки с merge «replace» (результат, дата и
# прочие поля-состояния). Дописывающие режимы (наводки, комментарий) и так
# ничего не уничтожают. Окно дат остаётся фильтром для скорости и обзора, а
# не предохранителем. ``ledger=None`` — прежнее поведение без реестра
# (evidence-run и старые вызовы), ``ledger={}`` — реестр включён, но пуст.

CONFLICT_NO_HISTORY = "no_history"
CONFLICT_BOTH_CHANGED = "both_changed"
OFFICE_EDIT_AFTER_DELIVERY = "office_edit"


def _ledger_value(ledger, cid: str, key: str):
    """Что обогащение доставляло для (КД, поле); None — истории нет."""
    if not ledger:
        return None
    entry = (ledger.get(cid) or {}).get(key)
    if isinstance(entry, dict):
        value = entry.get("value")
    else:
        value = entry
    return None if value is None else cell_text(value)


def book_key(path) -> str:
    return str(path).replace("\\", "/").casefold()


def canonical_book_key(path) -> str:
    """Mapped drives and UNC spellings share the actual workbook identity."""
    try:
        return book_key(Path(path).resolve())
    except (OSError, ValueError):
        return book_key(path)


def ledger_for_book(ledger, path):
    if ledger is None or "_books" not in ledger:
        return ledger
    books = ledger["_books"]
    raw, canonical = book_key(path), canonical_book_key(path)
    if raw == canonical:
        return books.get(raw, {})
    # Older receipts can contain either spelling. Keep the newest acknowledged
    # field if both exist; never turn a known office edit into missing history.
    merged = {}
    for identity in dict.fromkeys((canonical, raw)):
        for cid, fields in books.get(identity, {}).items():
            target = merged.setdefault(cid, {})
            for key, value in fields.items():
                old = target.get(key)
                at = value.get("at") if isinstance(value, dict) else ""
                old_at = old.get("at") if isinstance(old, dict) else ""
                if old is None or str(at or "") > str(old_at or ""):
                    target[key] = value
    return merged


def decisions_for_book(decisions, path, *, identities=None):
    if not decisions:
        return {}
    out = {}
    target = {book_key(path), canonical_book_key(path)}
    identities = {} if identities is None else identities
    for key, value in (decisions or {}).items():
        if len(key) == 2:
            out[key] = value
        elif len(key) == 3:
            raw = book_key(key[0])
            if raw not in identities:
                identities[raw] = canonical_book_key(key[0])
            if raw in target or identities[raw] in target:
                out[key[1:]] = value
    return out


def _enrichment_header(grid, fields, rows):
    """Repair only a blank ID heading proven by every existing row identity."""
    try:
        header, columns = find_header(grid, fields)
        return header, columns, []
    except ValueError as original:
        if 'нет колонки-ключа' not in str(original):
            raise
        title = next((f['title'] for f in fields if f['key'] == CONTRACT_KEY), None)
        source = {}
        for row in rows:
            cid = norm_cid(row.get(CONTRACT_KEY))
            identity = (norm_name(row.get('partner')), norm_name(row.get('client')))
            if cid and all(identity):
                source.setdefault(cid, set()).add(identity)
        candidates = []
        for h, line in enumerate(grid[:WORKBOOK_HEADER_SEARCH_ROWS]):
            for c in range(1, len(line)-1):
                # This is the registered portfolio layout, not a search for
                # arbitrary columns whose contents happen to look like IDs.
                if (cell_text(line[c]) or norm_title(line[c-1]) != norm_title('Пул')
                        or norm_title(line[c+1]) != norm_title('Клиент') or not title):
                    continue
                repaired = [list(row) for row in grid]
                repaired[h][c] = title
                try:
                    head, cols = find_header(repaired, fields)
                except ValueError:
                    continue
                if head != h or cols.get(CONTRACT_KEY) != c or not {'partner','client','pool'} <= cols.keys():
                    continue
                first = h+1
                if first < len(grid) and _is_source_row(grid[first]):
                    first += 1
                identities = 0
                for row in grid[first:]:
                    if not any(cell_text(value) for value in row):
                        continue
                    cid = norm_cid(row[c] if c < len(row) else None)
                    identity = tuple(norm_name(row[cols[k]] if cols[k] < len(row) else None)
                                     for k in ('partner','client'))
                    if not cid or not all(identity) or source.get(cid) != {identity}:
                        break
                    identities += 1
                else:
                    if identities:
                        candidates.append((h, cols, [{'row': h, 'col': c, 'before': line[c],
                            'after': title, 'identities_verified': identities}]))
        if len(candidates) == 1:
            return candidates[0]
        raise original


def build_plan(grid: list[list], fields: list[dict], rows: list[dict],
               since=None, until=None, ledger=None,
               server_authoritative: bool = False, decisions=None) -> dict:
    """План обогащения. grid — лист файла как список строк (значения как есть),
    fields — схема сервера [{key,title,date,...}], rows — строки клиентов сервера.

    since/until — ОКНО ДАТ ВЫЕЗДА, которое задаёт оператор. Границы
    включительные, пустая граница означает открытую сторону. Окно — фильтр,
    а не предохранитель: от затирания чужих правок защищает реестр
    доставленного (см. комментарий выше).

    ledger — реестр доставленного с сервера {КД: {поле: {"value", ...}}};
    None — прежнее поведение без реестра. server_authoritative — по явной
    галочке оператора строки без истории доставки пишутся поверх непустых
    значений файла (с записью в журнал вытесненного).

    decisions — решения оператора по конфликтам {(КД, поле): "server"|"file"}:
    «server» — записать значение сервера (с журналом вытесненного), «file» —
    оставить как в книге и запомнить в реестре доставленного значение сервера,
    чтобы дальше эта ячейка считалась правкой офиса, а не конфликтом. Ячейки
    без решения остаются конфликтами и не пишутся.

    Возвращает {sheet_writes: [(r, c, value, is_date)], ...статистика}. Индексы
    0-based относительно grid. Пишутся ТОЛЬКО ячейки визита заполненных кейсов,
    и только когда значение отличается от файла."""
    header_row, col_map, header_repairs = _enrichment_header(grid, fields, rows)
    # Не позволяем fallback в легаси-колонку «Статус звонка»: если целевой
    # «Общий результат» отсутствует, результат должен быть явно отмечен как
    # некуда записывать. Аналогично tips требует «Доп. Адрес/Доп.Номер».
    for source_key, target_key in WRITE_TARGETS.items():
        target_col = col_map.get(target_key)
        if target_col is None:
            col_map.pop(source_key, None)
        else:
            col_map[source_key] = target_col
    is_date_key = {f["key"]: bool(f.get("date")) for f in fields}
    for source,target in WRITE_TARGETS.items():
        is_date_key[source] = is_date_key.get(target,False)

    data_start = header_row + 1
    if data_start < len(grid) and _is_source_row(grid[data_start]):
        data_start += 1

    cid_col = col_map[CONTRACT_KEY]
    file_rows: dict[str, list[int]] = {}
    for r in range(data_start, len(grid)):
        row = grid[r]
        cid = norm_cid(row[cid_col] if cid_col < len(row) else None)
        if cid:
            file_rows.setdefault(cid, []).append(r)

    visit_cols_absent = sorted(k for k in VISIT_KEYS if k not in col_map)
    title_by_key = {f["key"]: f.get("title") or f["key"] for f in fields}

    since_d, until_d = visit_window(since, until)

    writes: list[tuple[int, int, object, bool]] = [
        (p['row'], p['col'], p['after'], False) for p in header_repairs]
    filled_cases = matched_cases = changed_cases = 0
    cells_already = cells_over_nonempty = 0
    out_of_window = technical_overwrites = 0
    cells_updated_delivered = ledger_seeded = 0
    missing: list[str] = []
    filled_contract_ids: list[str] = []
    matched_contract_ids: list[str] = []
    comment_cleared = 0
    comment_cleared_cids: list[str] = []
    # Что именно вытесняем. Раньше старое значение не сохранялось нигде, и
    # единственным следом оставалась резервная копия книги: чтобы узнать, что
    # затёрли, приходилось сравнивать файлы целиком.
    displaced: list[dict] = []
    # Реестр доставленного: что НЕ пишем и почему, и что надо записать в реестр.
    ledger_mode = ledger is not None
    conflicts: list[dict] = []
    office_edits: list[dict] = []
    resolved: list[dict] = []          # конфликты, решённые оператором
    preserved_payment_pairs: list[dict] = []
    preserved_promise_pairs: list[dict] = []
    coupled_file_choices: list[dict] = []
    resolved_file = resolved_server = 0
    ledger_entries: list[dict] = []
    seeded_keys: set[tuple[str, str]] = set()
    decisions = decisions or {}

    def _note(bucket: list[dict], kind: str, cid: str, key: str, r: int,
              cur, now, delivered, vdate) -> None:
        bucket.append({
            "kind": kind,
            "contract_id": cid,
            "key": key,
            "column": title_by_key.get(WRITE_TARGETS.get(key, key), key),
            "row": r + 1,                  # как видно в Excel
            "file": cell_text(cur),
            "server": cell_text(now),
            "delivered": "" if delivered is None else delivered,
            "visit_date": vdate.isoformat() if vdate else "",
        })

    def _deliver(cid: str, key: str, value, vdate, kind: str) -> None:
        ledger_entries.append({
            "contract_id": cid,
            "key": key,
            "value": cell_text(value),
            "visit_date": vdate.isoformat() if vdate else "",
            "kind": kind,                  # written | seed
        })

    for srv in rows:
        cid = norm_cid(srv.get(CONTRACT_KEY))
        if not cid:
            continue
        payment = latest_payment_pair(srv)
        if payment and not all(k in col_map for k in ("_reported_payment_sum", "_reported_payment_date")):
            raise ValueError("Для переноса платежа нужны обе колонки: сумма и дата последнего платежа")
        hard_day = parse_date(srv.get("visit_date")) if is_filled(srv) else None
        call_day = soft_anchor(srv) if is_soft_filled(srv) else None
        common_day = parse_date(srv.get("_common_day")) or call_day or hard_day
        profile = srv.get("_profile") or (
            "soft" if "soft_call_status" in col_map and "visit_date" not in col_map else
            "hard" if "visit_date" in col_map and "soft_call_status" not in col_map else "both")
        anchors = {}
        for key in VISIT_KEYS:
            if key in ("_reported_payment_sum", "_reported_payment_date"):
                anchor = payment[0] if payment else None
            elif key == "soft_call_status" and profile != "hard":
                anchor = parse_date(srv.get("_payment_status_day")) or call_day
            elif key == "tips":
                phone_day = parse_date((srv.get("_common_days") or {}).get("tips")) if profile != "hard" else None
                dates = [d for d in (phone_day, hard_day if profile != "soft" else None) if d is not None]
                anchor = max(dates) if dates else None
            elif key == "soft_comment" and profile != "hard":
                dates = [d for d in (call_day, parse_date(srv.get("_payment_day"))
                         if srv.get("_bot_payment_recorded") is True else None) if d is not None]
                anchor = max(dates) if dates else None
            elif key in ("promise_date", "forecast_sum", "forecast", "call_status", "meeting_date"):
                anchor = parse_date((srv.get("_common_days") or {}).get(key)) or common_day
            elif key in SOFT_CASE_KEYS:
                anchor = call_day if profile != "hard" else None
            else:
                anchor = hard_day if profile != "soft" else None
            if anchor is not None:
                anchors[key] = anchor
        if not anchors:
            continue
        eligible = {k: d for k, d in anchors.items()
                    if k in ("_reported_payment_sum", "_reported_payment_date")
                    or (not since_d or d >= since_d) and (not until_d or d <= until_d)}
        if not eligible:
            out_of_window += 1
            continue
        vdate = max(eligible.values())
        filled_contract_ids.append(cid)
        payment_values = ({"_reported_payment_sum": payment[1], "_reported_payment_date": payment[0]}
                          if payment else {})
        values = {k: payment_values.get(k, srv.get(k)) for k in eligible
                  if k in col_map and (k in payment_values or cell_text(srv.get(k)) != "" or
                      k in ("soft_comm_date", "promise_date", "forecast_sum", "forecast", "meeting_date")
                      and k in srv.get("_enrichment_clear_keys", []))}
        filled_cases += 1
        targets = file_rows.get(cid)
        if not targets:
            missing.append(cid)
            continue
        matched_cases += 1
        matched_contract_ids.append(cid)
        if srv.get(COMMENT_CLEARED_MARKER):
            # Строка в книге есть, комментарий выездника есть — но везти нечего.
            comment_cleared += 1
            if len(comment_cleared_cids) < ORPHAN_SAMPLE:
                comment_cleared_cids.append(cid)
        case_changed = False
        # A payment is one dated fact. An unrelated later call/manual edit must
        # not hide it, and amount/date must never be published separately.
        pay_keys = ("_reported_payment_sum", "_reported_payment_date")
        if payment and all(k in values for k in pay_keys):
            pc, dc = (col_map[k] for k in pay_keys)
            for r in targets:
                for identity_key in ("partner", "client"):
                    ic = col_map.get(identity_key)
                    if ic is not None and ic < len(grid[r]) and srv.get(identity_key) and grid[r][ic] \
                            and norm_name(grid[r][ic]) != norm_name(srv[identity_key]):
                        raise ValueError("Платёж: не совпадает клиент/партнёр строки реестра")
                old_sum = grid[r][pc] if pc < len(grid[r]) else None
                old_date = grid[r][dc] if dc < len(grid[r]) else None
                file_day = parse_date(old_date)
                if file_day and file_day > payment[0]:
                    # Explicitly surface this protected pair; never acknowledge
                    # an older source value as if it had been written.
                    preserved_payment_pairs.append(dict(contract_id=cid,row=r+1,
                        file_date=cell_text(old_date),file_amount=cell_text(old_sum),
                        source_date=payment[0].isoformat(),source_amount=payment[1]))
                    continue
                same_sum = _values_equal(old_sum, payment[1], False)
                changed_same_day = file_day == payment[0] and not same_sum and not _is_technical_empty(old_sum) and cell_text(old_sum) != ""
                amount_delivered = _ledger_value(ledger, cid, pay_keys[0])
                known_correction = amount_delivered is not None and _values_equal(old_sum, amount_delivered, False)
                if changed_same_day and not known_correction and decisions.get((cid, pay_keys[0])) != "server":
                    if decisions.get((cid,pay_keys[0])) == 'file':
                        _note(resolved,'payment_pair_conflict',cid,pay_keys[0],r,
                              old_sum,payment[1],amount_delivered,payment[0])
                        resolved[-1]['decision']='file';resolved_file+=1
                        preserved_payment_pairs.append(dict(contract_id=cid,row=r+1,
                            file_date=cell_text(old_date),file_amount=cell_text(old_sum),
                            source_date=payment[0].isoformat(),source_amount=payment[1],reason='accepted_file_payment_pair'))
                        if ledger_mode:
                            _deliver(cid,pay_keys[0],payment[1],payment[0],'kept_file')
                            _deliver(cid,pay_keys[1],payment[0],payment[0],'kept_file')
                        continue
                    _note(conflicts, "payment_pair_conflict", cid, pay_keys[0], r,
                          old_sum, payment[1], amount_delivered, payment[0])
                    continue
                for key, c, old, value, is_date in ((pay_keys[0], pc, old_sum, payment[1], False),
                                                   (pay_keys[1], dc, old_date, payment[0], True)):
                    if _values_equal(old, value, is_date):
                        cells_already += 1
                    else:
                        writes.append((r, c, value, is_date));case_changed = True
                        if cell_text(old):
                            displaced.append(dict(contract_id=cid, key=key, column=title_by_key.get(WRITE_TARGETS.get(key,key),key), row=r+1, was=cell_text(old), now=cell_text(value),visit_date=payment[0].isoformat()))
                    if ledger_mode:
                        _deliver(cid, key, value, payment[0], "written" if not _values_equal(old,value,is_date) else "seed")
            for key in pay_keys:
                values.pop(key, None)
        # Keeping one component of a manually retained promise keeps the whole
        # dated amount. Filling its other component would create a hybrid fact.
        promise_keys = ('promise_date', 'forecast_sum')
        keep_promises = set()
        if all(k in values and k in col_map for k in promise_keys):
            pair_choices = {decisions.get((cid,k)) for k in promise_keys} - {None}
            if pair_choices == {'file','server'}:
                raise ValueError('Сумма и дата обещания должны иметь одно решение')
            if 'file' in pair_choices:
                for r in targets:
                    if any(decisions.get((cid,k)) == 'file' and not _values_equal(
                            grid[r][col_map[k]] if col_map[k] < len(grid[r]) else None,
                            values[k], is_date_key.get(k,False)) for k in promise_keys):
                        keep_promises.add(r)
                        preserved_promise_pairs.append(dict(contract_id=cid,row=r+1,
                            reason='accepted_file_promise_pair'))
        for key, val in values.items():
            vdate = eligible[key]
            c = col_map[key]
            is_date = is_date_key.get(key, False)
            for r in targets:
                cur = grid[r][c] if c < len(grid[r]) else None
                if key in promise_keys and r in keep_promises:
                    _note(coupled_file_choices, 'promise_pair', cid, key, r,
                          cur, val, _ledger_value(ledger,cid,key), vdate)
                    coupled_file_choices[-1]['decision'] = 'file'
                    if ledger_mode:
                        _deliver(cid,key,val,vdate,'kept_file')
                    continue
                merge_mode = MERGE_MODES[key]
                if merge_mode == "unique_lines":
                    out_val = merge_unique_lines(cur, val)
                elif merge_mode == "preserve_text":
                    out_val = merge_preserving_text(cur, val)
                elif merge_mode == "replace":
                    if key == '_reported_payment_sum' and (not math.isfinite(float(val)) or not 0<float(val)<=1e9):
                        raise ValueError('Некорректная сумма записанной оплаты')
                    if key == '_reported_payment_date' and parse_date(val) is None:
                        raise ValueError('Некорректная дата записанной оплаты')
                    out_val = float(val) if key == "_reported_payment_sum" else parse_date(val) if is_date and parse_date(val) \
                        else cell_text(val)
                else:
                    raise ValueError(
                        f"неподдерживаемый merge mode {merge_mode!r} для {key}"
                    )
                if (merge_mode != "replace"
                        and out_val == cell_text(cur)) or \
                        (merge_mode == "replace"
                         and _values_equal(cur, val, is_date)):
                    cells_already += 1
                    # Файл уже показывает значение сервера — досеиваем реестр,
                    # чтобы следующая правка офиса была отличима от «отстал».
                    if (ledger_mode and merge_mode == "replace"
                            and (cid, key) not in seeded_keys):
                        delivered = _ledger_value(ledger, cid, key)
                        if delivered is None or \
                                not _values_equal(delivered, val, is_date):
                            seeded_keys.add((cid, key))
                            ledger_seeded += 1
                            _deliver(cid, key, out_val, vdate, "seed")
                    continue
                if ledger_mode and merge_mode == "replace":
                    delivered = _ledger_value(ledger, cid, key)
                    cur_txt = "" if _is_technical_empty(cur) else cell_text(cur)
                    decision = decisions.get((cid, key))
                    if delivered is None:
                        if cur_txt != "" and not server_authoritative \
                                and decision != "server":
                            # Истории доставки нет, в файле что-то стоит, и оно
                            # не совпадает с сервером. Кто прав — неизвестно:
                            # это либо правка офиса, либо устаревшая копия.
                            # Решает человек, а не писатель.
                            if decision == "file":
                                _note(resolved, CONFLICT_NO_HISTORY, cid, key, r,
                                      cur, out_val, delivered, vdate)
                                resolved[-1]["decision"] = "file"
                                resolved_file += 1
                                _deliver(cid, key, out_val, vdate, "kept_file")
                            else:
                                _note(conflicts, CONFLICT_NO_HISTORY, cid, key, r,
                                      cur, out_val, delivered, vdate)
                            continue
                        if cur_txt != "" and decision == "server":
                            _note(resolved, CONFLICT_NO_HISTORY, cid, key, r,
                                  cur, out_val, delivered, vdate)
                            resolved[-1]["decision"] = "server"
                            resolved_server += 1
                    else:
                        server_changed = not _values_equal(delivered, val, is_date)
                        file_untouched = _values_equal(cur, delivered, is_date)
                        if not server_changed:
                            # Сервер по-прежнему говорит то, что мы доставили,
                            # а файл — другое. Без явного решения правка офиса
                            # сохраняется; проверенный выбор оператора работает
                            # так же, как для остальных конфликтов.
                            if decision in ("server", "file"):
                                _note(resolved, OFFICE_EDIT_AFTER_DELIVERY, cid,
                                      key, r, cur, out_val, delivered, vdate)
                                resolved[-1]["decision"] = decision
                                if decision == "file":
                                    resolved_file += 1
                                    _deliver(cid, key, out_val, vdate, "kept_file")
                                    continue
                                resolved_server += 1
                            else:
                                _note(office_edits, OFFICE_EDIT_AFTER_DELIVERY, cid,
                                      key, r, cur, out_val, delivered, vdate)
                                continue
                        elif not file_untouched and cur_txt != "":
                            if decision == "server":
                                _note(resolved, CONFLICT_BOTH_CHANGED, cid, key, r,
                                      cur, out_val, delivered, vdate)
                                resolved[-1]["decision"] = "server"
                                resolved_server += 1
                                # дальше — обычная запись с журналом вытесненного
                            elif decision == "file":
                                _note(resolved, CONFLICT_BOTH_CHANGED, cid, key, r,
                                      cur, out_val, delivered, vdate)
                                resolved[-1]["decision"] = "file"
                                resolved_file += 1
                                _deliver(cid, key, out_val, vdate, "kept_file")
                                continue
                            else:
                                _note(conflicts, CONFLICT_BOTH_CHANGED, cid, key, r,
                                      cur, out_val, delivered, vdate)
                                continue
                        if file_untouched and cur_txt != "":
                            # Заменяем СВОЮ прошлую доставку — это не вытеснение
                            # чужого значения, журнал не засоряем.
                            cells_updated_delivered += 1
                            writes.append((r, c, out_val, is_date))
                            case_changed = True
                            _deliver(cid, key, out_val, vdate, "written")
                            continue
                if cell_text(cur) != "":
                    cells_over_nonempty += 1
                    # Слияние старое значение сохраняет внутри нового, замена —
                    # нет. Вытеснением считаем только второе: иначе в журнал
                    # попадёт каждый дописанный комментарий. И «Не обработан» —
                    # не чьё-то решение, а состояние по умолчанию.
                    if _is_technical_empty(cur):
                        technical_overwrites += 1
                    elif merge_mode == "replace":
                        displaced.append({
                            "contract_id": cid,
                            "key": key,
                            "column": title_by_key.get(WRITE_TARGETS.get(key, key),
                                                       key),
                            "row": r + 1,          # как видно в Excel
                            "was": cell_text(cur),
                            "now": cell_text(out_val),
                            "visit_date": vdate.isoformat() if vdate else "",
                        })
                writes.append((r, c, out_val, is_date))
                case_changed = True
                if ledger_mode:
                    _deliver(cid, key, out_val, vdate, "written")
        if case_changed:
            changed_cases += 1

    return {
        "header_row": header_row,
        "data_start": data_start,
        "writes": writes,
        "filled_cases": filled_cases,      # кейсов с данными бота в БД
        "matched_cases": matched_cases,    # из них нашлись в файле
        "changed_cases": changed_cases,    # из них реально было что дописать
        "cells_written": len(writes),
        "cells_already": cells_already,    # уже совпадало — не трогали
        "cells_over_nonempty": cells_over_nonempty,  # обновили поверх старого значения
        "displaced": displaced,            # ЧТО именно вытеснили: было/стало
        "technical_overwrites": technical_overwrites,  # поверх «Не обработан»
        "out_of_window": out_of_window,    # отсеяно окном дат оператора
        "window": {"since": since_d.isoformat() if since_d else "",
                   "until": until_d.isoformat() if until_d else ""},
        # Реестр доставленного (см. комментарий перед build_plan).
        "ledger_mode": ledger_mode,
        "server_authoritative": bool(server_authoritative),
        "conflicts": conflicts,            # НЕ записано: решает человек
        "office_edits": office_edits,      # НЕ записано: правка офиса после доставки
        "preserved_payment_pairs": preserved_payment_pairs,
        "preserved_promise_pairs": preserved_promise_pairs,
        "coupled_file_choices": coupled_file_choices,
        "header_repairs": header_repairs,
        "resolved": resolved,              # конфликты с решением оператора
        "resolved_file": resolved_file,    # оставлено как в книге
        "resolved_server": resolved_server,  # записан сервер по решению
        "ledger_entries": ledger_entries,  # что записать в реестр доставленного
        "ledger_seeded": ledger_seeded,    # досеяно без записи (файл уже = сервер)
        "cells_updated_delivered": cells_updated_delivered,  # обновили свою доставку
        # Обратная совместимость отчётов старого пульта. Обычное обогащение
        # больше никогда не очищает комментарии; точечная починка — отдельный
        # dry-run/apply инструмент с собственным бэкапом.
        "poison_cleared": 0,
        "missing": missing,                # заполнены ботом, но строки в файле нет
        # Комментарий выездника есть в снимке, а живое поле сервера пусто:
        # обогащение такой комментарий не повезёт — лечится на сервере.
        "comment_cleared": comment_cleared,
        "comment_cleared_cids": comment_cleared_cids,
        "filled_contract_ids": filled_contract_ids,
        "matched_contract_ids": matched_contract_ids,
        "visit_cols_absent": visit_cols_absent,      # колонок визита нет в файле
    }


# --- Работа с живым файлом (xlwings/COM) -------------------------------------
def clear_gen_py():
    """Снести кэш COM-обёрток pywin32 (gen_py): он умеет протухать
    («…has no attribute 'CLSIDToPackageMap'») и валит любой запуск Excel."""
    import sys
    try:
        import win32com
        path = Path(win32com.__gen_path__)
    except Exception:  # noqa: BLE001
        import tempfile
        path = Path(tempfile.gettempdir()) / "gen_py" / \
            f"{sys.version_info.major}.{sys.version_info.minor}"
    shutil.rmtree(path, ignore_errors=True)
    for m in [m for m in sys.modules if "gen_py" in m]:
        sys.modules.pop(m, None)


def open_excel(xw, log=lambda s: None):
    """xw.App(visible=False) с самолечением битого кэша gen_py."""
    try:
        app = xw.App(visible=False, add_book=False)
        app.api.EnableEvents = False
        app.api.AutomationSecurity = 3
        return app
    except AttributeError as e:
        if "gen_py" not in repr(e) and "CLSIDToPackageMap" not in repr(e):
            raise
        log("Чиню кэш COM-обёрток Excel…")
        clear_gen_py()
        app = xw.App(visible=False, add_book=False)
        app.api.EnableEvents = False
        app.api.AutomationSecurity = 3
        return app


def _pick_sheet(wb, fields: list[dict]):
    """Only the exact contract-bound portfolio sheet may be changed."""
    names = [s.name for s in wb.sheets]
    if SHEET_NAME in names:
        return wb.sheets[SHEET_NAME]
    raise ValueError(
        f"обязательный лист «{SHEET_NAME}» отсутствует; "
        "fallback на похожий лист запрещён"
    )


def _workbook_grid(wb, fields: list[dict]) -> tuple[object, list[list]]:
    """Read one workbook without changing it, using the same sheet contract."""

    ws = _pick_sheet(wb, fields)
    last = ws.used_range.last_cell
    grid = ws.range((1, 1), (last.row, last.column)).value or []
    if grid and not isinstance(grid[0], list):
        grid = [grid if isinstance(grid, list) else [grid]]
    return ws, grid


def _payment_statistics_plan(wb, grid, fields, rows, since=None, until=None, *, warnings=None) -> list[dict]:
    """Repair only the known statistics SUMIF accidentally pointing to dates.

    Collection statistics still use the existing authoritative Current Payments
    column. Manual last-payment columns do not redefine confirmed income or KP.
    """
    lo,hi=visit_window(since,until)
    if not any(r.get('_bot_payment_recorded') is True and r.get('_profile') != 'hard'
               and (day:=parse_date(r.get('_payment_day'))) is not None
               and (lo is None or day>=lo) and (hi is None or day<=hi) for r in rows):
        return []
    sheets = {sheet.name:sheet for sheet in wb.sheets}
    sheet = sheets.get('Статистика сотрудника')
    if sheet is None or norm_title(sheet.range('C1').value) != norm_title('Взыск'):
        return []
    header,cols = find_header(grid,fields)
    if 'last_payment_date' not in cols:
        return []
    def letter(index):
        value='';index+=1
        while index:
            index,remainder=divmod(index-1,26);value=chr(65+remainder)+value
        return value
    dates=letter(cols['last_payment_date'])
    current=[i for i,title in enumerate(grid[header]) if norm_title(title)==norm_title('Текущие платежи')]
    changes=[]
    for row in range(2,7):
        cell=f'C{row}'
        formula=sheet.range(cell).formula
        expected=f"=SUMIF(Портфели!A:A,'Статистика сотрудника'!$A{row},Портфели!{dates}:{dates})"
        if formula != expected:
            continue
        if len(current)!=1:
            if warnings is not None:
                warnings.append(f'{sheet.name}!{cell}: формула суммирует даты. '
                    'Колонка «Текущие платежи» отсутствует или неоднозначна; '
                    'формула сохранена, сумма взыскания требует исправления.')
            continue
        amount=letter(current[0])
        changes.append({'sheet':sheet.name,'cell':cell,'before':formula,
                        'after':expected.rsplit(f'!{dates}:{dates}',1)[0]+f'!{amount}:{amount})'})
    return changes


def _set_payment_statistics_plan(plan, wb, grid, fields, rows, since=None, until=None):
    plan['statistics_warnings'] = []
    plan['statistics_formula_changes'] = _payment_statistics_plan(
        wb, grid, fields, rows, since, until, warnings=plan['statistics_warnings'])


def _apply_payment_statistics(wb, changes):
    for change in changes:
        cell=wb.sheets[change['sheet']].range(change['cell'])
        if cell.formula != change['before']:
            raise RuntimeError('Формула статистики изменилась после проверки')
        cell.formula=change['after']


def _plan_workbook(app, path: Path, fields: list[dict], rows: list[dict],
                   since=None, until=None, ledger=None,
                   server_authoritative: bool = False, decisions=None,preservation=None) -> dict:
    """Reopen a saved workbook and recompute the enrichment plan read-only."""

    wb = app.books.open(str(path), update_links=False, read_only=True)
    try:
        if preservation:_verify_unplanned_cells(wb,*preservation)
        _ws, grid = _workbook_grid(wb, fields)
        plan = build_plan(grid, fields, rows, since, until,
                          ledger=ledger, server_authoritative=server_authoritative,
                          decisions=decisions)
        _set_payment_statistics_plan(plan,wb,grid,fields,rows,since,until)
        return plan
    finally:
        wb.close()


def _all_sheet_cells(wb):
    # Bulk UsedRange.Formula can fail on long literal history comments.
    # Read saved literals with calamine and exact expressions only from Excel's
    # formula areas, so cached results never masquerade as changed literals.
    from python_calamine import CalamineWorkbook
    book=CalamineWorkbook.from_path(wb.fullname)
    result={}
    for sheet in wb.sheets:
        name=sheet.name;source=book.get_sheet_by_name(name);start=source.start or (0,0)
        values={(r,c):value for r,line in enumerate(source.to_python(),start[0])
                for c,value in enumerate(line,start[1]) if value is not None and value!=''}
        used=sheet.api.UsedRange
        try:areas=used.SpecialCells(-4123).Areas
        except Exception:
            if used.HasFormula is not False:raise
            areas=None
        if areas is not None:
            for index in range(1,areas.Count+1):
                area=areas.Item(index);raw=area.Formula;r0=int(area.Row)-1;c0=int(area.Column)-1
                matrix=raw if isinstance(raw,(tuple,list)) else ((raw,),)
                if matrix and not isinstance(matrix[0],(tuple,list)):matrix=(matrix,)
                for r,line in enumerate(matrix,r0):
                    for c,value in enumerate(line,c0):
                        if not isinstance(value,str) or not value.startswith('='):
                            raise ValueError('Не удалось проверить формулу Excel')
                        values[r,c]=value
        result[name]=values
    return result


def _verify_unplanned_cells(wb,before,allowed):
    after=_all_sheet_cells(wb)
    if set(before)!=set(after):raise ValueError('Изменился набор листов; книга не опубликована')
    for sheet in before:
        for cell in before[sheet].keys()|after[sheet].keys():
            if (sheet,*cell) not in allowed and before[sheet].get(cell)!=after[sheet].get(cell):
                column='';number=cell[1]+1
                while number:
                    number,remainder=divmod(number-1,26);column=chr(65+remainder)+column
                raise ValueError(f'Незапланированное изменение {sheet}!{column}{cell[0]+1}; книга не опубликована')


def _verify_vba_copy(source,candidate):
    import zipfile
    if not zipfile.is_zipfile(source):return None
    if not zipfile.is_zipfile(candidate):raise ValueError('Изменился формат книги; запись отменена')
    with zipfile.ZipFile(source) as original,zipfile.ZipFile(candidate) as current:
        for name in set(original.namelist())|set(current.namelist()):
            if name.casefold().endswith('vbaproject.bin') and original.read(name)!=current.read(name):
                raise ValueError('Изменились макросы; книга не опубликована')
    return True


# Excel считает формулой всё, что начинается с =, +, - или @. Комментарий
# «=перезвонить» превращается в #ИМЯ?, «5.08» — в число, а повторная сверка плана
# перестаёт сходиться и роняет ВЕСЬ прогон обогащения — уже после того, как файлы
# переписаны. То есть один комментарий одного выездника оставляет офис без
# квитанции и с испорченными реестрами у всех. Ведущий апостроф Excel не
# показывает, но хранит значение текстом.
# Табуляция и возврат каретки в начале — туда же: их срезают при вставке и
# импорте, и под ними оказывается та же формула. А «=HYPERLINK(…)» в книге
# аналитика — уже не порча, а вынос данных при открытии (ревью 25.09.2026, #6).
# Правило одно на всех писателей пульта: `_excel_safe` — запись через Excel
# (xlwings), `core/export._text_cell` — через openpyxl.
_EXCEL_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_EXCEL_CELL_LIMIT = 32767


def excel_formula_like(value) -> bool:
    """Строка, которую Excel при вводе принял бы за формулу."""
    return isinstance(value, str) and value[:1] in _EXCEL_FORMULA_START


def _excel_safe(value):
    """Значение для записи ЧЕРЕЗ Excel (xlwings/COM). Числа, даты и None — как есть.

    Апостроф Excel разбирает при вводе как отметку «это текст» и в значение не
    кладёт: `Range.Value` потом читает строку без него, поэтому повторная сверка
    плана сходится. Для openpyxl апостроф не годится — там он лёг бы в саму
    строку (см. `core/export._text_cell`).
    """
    if not isinstance(value, str):
        return value
    v = value[:_EXCEL_CELL_LIMIT]
    if excel_formula_like(v) or excel_digits_lossy(v):
        return "'" + v
    return v


def excel_digits_lossy(value) -> bool:
    """Строка из одних цифр, которую Excel при вводе превратил бы в число С
    ПОТЕРЕЙ: ведущие нули («0012345» → 12345) или больше 15 цифр (точность
    Excel — 15 знаков, «8600123412341234» → 8600123412341230). Опыт A/B
    30.09.2026: такой комментарий книга хранит числом, повторная сверка плана
    не сходится, а в ячейке остаётся искажённое значение."""
    t = value.strip() if isinstance(value, str) else ""
    return t.isdigit() and t.isascii() and (
        (len(t) > 1 and t[0] == "0") or len(t) > 15)


# --- Занятая книга -----------------------------------------------------------
# Книгу, которую уже держит открытой другой человек, Excel отдаёт ТОЛЬКО для
# чтения, а `wb.save()` при `display_alerts = False` в этом случае молча не
# делает ничего: диалог «сохранить как копию» подавлен, правки остаются в
# памяти и умирают вместе с книгой. Так прошло обогащение Бозорова 10.09.2026:
# книга была открыта у сотрудника с 10:56, план насчитал записи, копия легла,
# ячейки «записались» — а файл на диске остался прежним. Ловил это только
# контракт pre/post, уже после копии и записи, и сообщение не называло ни
# причину, ни виновника. Поэтому спрашиваем у Windows, свободна ли книга, ДО
# открытия и проверяем режим книги сразу после — обе проверки дешёвые, обе
# срабатывают раньше копии и первой записи.
_LOCK_ANSI_FIELD = 54          # ANSI-имя в `~$Имя` добито пробелами до 54 байт


def excel_owner_file(path: str | Path) -> Path:
    """Служебный файл Excel рядом с книгой: `~$Имя.xlsb`."""
    path = Path(path)
    return path.with_name(f"~${path.name}")


def _read_shared(path: Path, limit: int = 512) -> bytes:
    """Прочитать файл, который кто-то держит открытым (Windows).

    Excel держит `~$Имя` открытым всё время, пока открыта книга, и делится им
    только вместе с правом удаления. Обычный `open()` такого не просит, и
    Windows отвечает «Permission denied», поэтому идём через CreateFileW с
    полным share-режимом. Пусто — прочитать не удалось.
    """
    if os.name != "nt":
        return b""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        handle = kernel32.CreateFileW(
            str(path),
            0x80000000,                    # GENERIC_READ
            0x1 | 0x2 | 0x4,               # FILE_SHARE_READ | WRITE | DELETE
            None, 3, 0x80, None,           # OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL
        )
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            return b""
        try:
            buffer = ctypes.create_string_buffer(limit)
            read = wintypes.DWORD()
            if not kernel32.ReadFile(handle, buffer, limit,
                                     ctypes.byref(read), None):
                return b""
            return buffer.raw[:read.value]
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 — имя владельца не стоит падения прогона
        return b""


def parse_lock_owner(blob: bytes) -> str:
    """Имя из `~$Имя`: длина + ANSI-имя, следом длина + то же имя в UTF-16.

    Берём UTF-16-копию: ANSI-копия зависит от кодовой страницы и портит
    кириллические имена. Пусто — формат не распознан.
    """
    if not blob:
        return ""
    wide_at = 1 + _LOCK_ANSI_FIELD
    if len(blob) >= wide_at + 2:
        count = int.from_bytes(blob[wide_at:wide_at + 2], "little")
        end = wide_at + 2 + 2 * count
        if count and end <= len(blob):
            name = blob[wide_at + 2:end].decode("utf-16-le", "replace").strip()
            if name:
                return name
    ansi_len = blob[0]
    if 0 < ansi_len < len(blob):
        codec = "mbcs" if os.name == "nt" else "cp1251"
        return blob[1:1 + ansi_len].decode(codec, "replace").strip()
    return ""


def workbook_lock_owner(path: str | Path) -> str:
    """Кто держит книгу открытой. Пустая строка — узнать не удалось."""
    return parse_lock_owner(_read_shared(excel_owner_file(path)))


def workbook_busy_owner(path: str | Path) -> str | None:
    """None — книгу можно писать; строка — кто её держит («» — имя не узнать).

    Просим у Windows ровно тот доступ, который понадобится Excel при
    сохранении, и сразу отпускаем: файл при этом не меняется.
    """
    try:
        with open(path, "r+b"):
            return None
    except PermissionError:
        return workbook_lock_owner(path)
    except OSError:
        return None      # нет файла и прочее — расскажет открытие книги


def _busy_reason(path: Path, owner: str) -> str:
    if not owner and not os.access(path, os.W_OK):
        # Не занята, а помечена «только для чтения»: лечится не ожиданием.
        return (f"{path.name}: файл помечен «только для чтения» — прав на запись "
                "нет, ничего не записано")
    who = f" — её держит открытой {owner}" if owner else ""
    return (f"{path.name}: книга занята{who}. Excel отдаёт занятую книгу только "
            "для чтения и сохраняет её молча «в никуда» — ничего не записано; "
            "прогони заново, когда книгу закроют")


def assert_writable(path: str | Path) -> None:
    """Отказаться до копии и до первой записи, если книга занята."""
    path = Path(path)
    owner = workbook_busy_owner(path)
    if owner is not None:
        raise RuntimeError(_busy_reason(path, owner))


def _opened_read_only(wb) -> bool:
    """Excel отдал книгу только для чтения (занята, помечена, нет прав)."""
    return bool(getattr(getattr(wb, "api", None), "ReadOnly", False))


def open_for_write(app, path: str | Path):
    """Открыть книгу на запись — или отказаться, назвав, кто её держит.

    Единственная дверь для инструментов, которые пишут в живые книги: молчаливое
    «сохранил в никуда» неотличимо от успеха, а отказ до первой записи — нет.
    """
    path = Path(path)
    assert_writable(path)
    wb = app.books.open(str(path))
    if _opened_read_only(wb):
        # Книгу успели открыть между проверкой и открытием, либо Excel отдал её
        # только для чтения по другой причине. Дальше идти нельзя: правки уйдут
        # в память, а сохранение молча не сработает.
        reason = _busy_reason(path, workbook_lock_owner(path))
        try:
            wb.close()
        except Exception:  # noqa: BLE001 — закрыть не вышло, важна причина
            pass
        raise RuntimeError(reason)
    return wb


def backup_copy(path: Path, root: str | Path | None = None,
                source: Path | None = None) -> Path:
    """Копия «Имя (до обогащения 13.07.2026 14-05).xlsb». Откат = переименовать.

    root — корень для копий. Без него копия ложится рядом с книгой, как раньше.
    С ним — в `<root>/<город>/<ФИО>/`, повторяя маску папок сотрудников: сваливать
    семь книг в одну папку нельзя, одноимённые файлы столкнутся.

    Корень нужен потому, что сорить в папках сотрудников запрещено, а переносить
    копии руками ПОСЛЕ прогона — ослеплять инструменты восстановления: они ищут
    копии рядом с живым файлом. Пусть кладёт сразу куда надо.

    Год в имени обязателен. Без него год приходилось достраивать из времени
    файла, а оно сбрасывается при переносе на другой том — и цепочка копий
    выстраивалась в неверном порядке.
    """
    stamp = datetime.now().strftime("%d.%m.%Y %H-%M")
    name = f"{path.stem} (до обогащения {stamp}){path.suffix}"
    if root:
        folder = Path(root) / path.parent.parent.name / path.parent.name
        folder.mkdir(parents=True, exist_ok=True)
    else:
        folder = path.parent
    dst = folder / name
    n = 2
    while dst.exists():
        dst = folder / f"{path.stem} (до обогащения {stamp}, {n}){path.suffix}"
        n += 1
    # source — нетронутая локальная копия той же книги (способ «local»):
    # байты те же, что на шаре до записи, а второе чтение по сети не нужно.
    shutil.copy2(source or path, dst)
    return dst


def _co_init():
    """Пульт зовёт Excel из фонового потока — COM в нём инициализируем явно."""
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except ImportError:
        pass


def _com_value(value):
    """Значение для записи напрямую через COM — ровно то, что сделал бы
    `xw.Range.value = …`: `_excel_safe` и даты через преобразователь самого
    xlwings. Наивный datetime pywin32 считает местным временем и сдвигает на
    часовой пояс: 30.09 легло бы как 29.09 20:00 (опыт 30.09.2026)."""
    value = _excel_safe(value)
    if isinstance(value, date):
        from xlwings._xlwindows import _datetime_to_com_time
        if not isinstance(value, datetime):
            value = datetime(value.year, value.month, value.day)
        return _datetime_to_com_time(value)
    return value


def _write_cells(ws, writes, log=lambda s: None, name: str = "") -> int:
    """Записать план по ячейке, но напрямую через COM, мимо обёрток xlwings.

    Опыт 30.09.2026 на книге формата выездника (593 записи): через
    `ws.range(...).value` — 9,9 с (16,7 мс на ячейку), напрямую
    `Cells(r, c).Value` — 1,1 с. Отрезки через xlwings ещё медленнее (117 мс на
    вызов), через COM — не быстрее поштучной записи: записи в книге выездника
    редкие и короткие. Значения — те же (`_com_value`); повтор той же ячейки —
    побеждает последний, как при записи по порядку."""
    cells: dict[tuple[int, int], object] = {}
    for r, c, val, _is_date in writes:
        cells[(r, c)] = val
    api_cells = ws.api.Cells
    for i, ((r, c), val) in enumerate(cells.items(), 1):
        api_cells(r + 1, c + 1).Value = _com_value(val)
        if i % 500 == 0:
            log(f"{name}: пишу {i} / {len(cells)}…")
    return len(cells)


def _enrich_workbook(app, path: Path, fields: list[dict], rows: list[dict],
                     log=lambda s: None, since=None, until=None,
                     backup_root=None, ledger=None,
                     server_authoritative: bool = False, decisions=None,
                     batch: bool = False) -> dict:
    """Один файл в уже открытом Excel: книга свободна? → лист → план → бэкап →
    точечная запись → сохранить тем же форматом. Возвращает план +
    backup/sheet/file.

    Осознанно ОДНОсторонний поток (бот → файл): идея «синхронизировать файл в БД
    перед записью» отклонена пользователем и ревью 23.07 — скрытый импорт при
    обогащении реимпортировал бы июльский яд из «комментария звонка» и вставлял
    скелетные КД прошлых сессий. Офисные правки идут только personal →
    registry_cycle → опубликованный master → explicit master import; прямого
    personal → bot gateway нет."""
    wb = open_for_write(app, path)
    try:
        # Читаем строго от A1: used_range может начинаться не с A1,
        # а индексы плана — координаты ячеек листа.
        ws, grid = _workbook_grid(wb, fields)

        plan = build_plan(grid, fields, rows, since, until,
                          ledger=ledger, server_authoritative=server_authoritative,
                          decisions=decisions)
        _set_payment_statistics_plan(plan,wb,grid,fields,rows,since,until)
        writes = plan["writes"]
        plan["backup"] = None
        if writes or plan['statistics_formula_changes']:
            # бэкап — только когда реально есть что писать (файл на диске ещё
            # не изменён: Excel не сохранял); нет записей — нет мусорных копий
            plan["backup"] = str(backup_copy(path, backup_root))
            if batch:
                _write_cells(ws, writes, log, path.name)
            else:
                for i, (r, c, val, _is_date) in enumerate(writes, 1):
                    ws.range((r + 1, c + 1)).value = _excel_safe(val)
                    if i % 200 == 0:
                        log(f"{path.name}: пишу {i} / {len(writes)}…")
            _apply_payment_statistics(wb,plan['statistics_formula_changes'])
            wb.save()
        plan["sheet"] = ws.name
        plan["file"] = str(path)
        return plan
    finally:
        wb.close()


class WrittenButUnverified(RuntimeError):
    """Книга сохранена, но проверить результат не удалось.

    Отдельный тип нужен, чтобы отличать это от «файл не обработан». Разница
    практическая: необработанную книгу надо прогнать заново, а эту — НЕ
    откатывать по копии, иначе потеряется уже записанная верная работа.
    """

    def __init__(self, **info):
        super().__init__(info.get("reason") or "не удалось проверить запись")
        self.info = info


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_size,
        right.st_mtime_ns,
    )


@contextmanager
def _open_regular_read(path: Path, *, label: str):
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"{label} недоступен: {path}") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or _is_reparse(before)
        or path.is_symlink()
    ):
        raise RuntimeError(f"{label} должен быть обычным non-reparse файлом: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"{label} не удалось безопасно открыть: {path}") from exc
    stream = os.fdopen(descriptor, "rb")
    try:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or (before.st_dev, before.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            raise RuntimeError(f"{label} подменён при открытии: {path}")
        yield stream
        after = os.fstat(stream.fileno())
        if not _same_file_state(opened, after):
            raise RuntimeError(f"{label} изменился во время чтения: {path}")
        try:
            current = os.lstat(path)
        except OSError as exc:
            raise RuntimeError(f"{label} исчез во время чтения: {path}") from exc
        if not _same_file_state(opened, current):
            raise RuntimeError(f"{label} заменён во время чтения: {path}")
    finally:
        stream.close()


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    with _open_regular_read(path, label=label) as stream:
        return stream.read()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with _open_regular_read(path, label="файл для SHA-256") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json_regular(path: Path, *, label: str) -> Any:
    try:
        return json.loads(_read_regular_bytes(path, label=label).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} содержит некорректный JSON: {path}") from exc


def _plan_summary(plan: dict) -> dict[str, Any]:
    return {
        "filled_cases": int(plan["filled_cases"]),
        "filled_contract_ids": list(plan["filled_contract_ids"]),
        "matched_cases": int(plan["matched_cases"]),
        "matched_contract_ids": list(plan["matched_contract_ids"]),
        "changed_cases": int(plan["changed_cases"]),
        "cells_written": int(plan["cells_written"]),
        "errors": [],
        "visit_cols_absent": list(plan["visit_cols_absent"]),
        "missing_contract_ids": list(plan["missing"]),
        "statistics_formula_changes": list(plan.get("statistics_formula_changes") or []),
        "statistics_warnings": list(plan.get("statistics_warnings") or []),
    }


@track_automatic_file('path')
def _enrich_with_evidence(
    app,
    path: Path,
    fields: list[dict],
    rows: list[dict],
    log=lambda s: None,
    since=None,
    until=None,
    backup_root=None,
    ledger=None,
    server_authoritative: bool = False,
    decisions=None,
    batch: bool = False,
) -> dict:
    """Run the existing writer, then reopen and prove a zero second plan."""

    raw_path = Path(path)
    if raw_path.is_symlink() or not raw_path.is_file():
        raise FileNotFoundError(f"нужен обычный Excel-файл: {raw_path}")
    path = raw_path.resolve()
    pre_sha = _sha256_file(path)
    plan = _enrich_workbook(app, path, fields, rows, log, since, until, backup_root,
                            ledger=ledger, server_authoritative=server_authoritative,
                            decisions=decisions, batch=batch)
    post_sha = _sha256_file(path)
    backup_raw = plan.get("backup")
    backup_path = Path(backup_raw).resolve() if backup_raw else None
    backup_sha = _sha256_file(backup_path) if backup_path is not None else None
    if plan["cells_written"]:
        # Сообщение обязано называть нарушенный инвариант. 10.09.2026 глухое
        # «контракт нарушен» стоило вечера разбора: по нему неотличимы
        # «копии нет», «копия не от этого прогона» и «Excel не сохранил книгу».
        broken = []
        if backup_path is None:
            broken.append("копия до записи не создана")
        elif backup_sha != pre_sha:
            broken.append("копия не совпадает с файлом до записи")
        if pre_sha == post_sha:
            broken.append(
                f"файл на диске не изменился, хотя записано ячеек: "
                f"{plan['cells_written']} — Excel не сохранил книгу "
                "(обычно её держит открытой другой пользователь)")
        if broken:
            raise RuntimeError(f"{path.name}: backup/pre/post contract нарушен — "
                               + "; ".join(broken))
    elif backup_path is not None or pre_sha != post_sha:
        raise RuntimeError(f"{path.name}: zero-write contract нарушен")

    try:
        # Тот же реестр, что и при записи: записанные ячейки теперь совпадают с
        # сервером, конфликты и правки офиса по-прежнему не пишутся — план
        # обязан быть нулевым.
        post_plan = _plan_workbook(app, path, fields, rows, since, until,
                                   ledger=ledger,
                                   server_authoritative=server_authoritative,
                                   decisions=decisions)
    except Exception as e:  # noqa: BLE001
        # Книга к этому моменту УЖЕ сохранена и её содержимое подтверждено
        # хэшами выше. Если проверку выполнить не удалось (обычно потому, что
        # Excel умер), нельзя докладывать это как «файл не обработан»: такой
        # доклад неотличим от неоткрытой книги, и откат по копии уничтожит
        # верную работу.
        raise WrittenButUnverified(
            path=str(path), pre_sha256=pre_sha, post_sha256=post_sha,
            backup_path=str(backup_path) if backup_path is not None else None,
            backup_sha256=backup_sha, cells_written=plan["cells_written"],
            displaced=plan.get("displaced") or [],
            # Записанное — в книге, значит и в реестре доставленного должно быть.
            ledger_entries=plan.get("ledger_entries") or [],
            conflicts=plan.get("conflicts") or [],
            office_edits=plan.get("office_edits") or [],
            resolved=plan.get("resolved") or [],
            reason=str(e)) from e
    if post_plan["cells_written"] or post_plan["changed_cases"]:
        raise RuntimeError(f"{path.name}: повторный enrichment plan не нулевой")
    if (
        post_plan["filled_contract_ids"] != plan["filled_contract_ids"]
        or post_plan["matched_contract_ids"] != plan["matched_contract_ids"]
        or post_plan["missing"] != plan["missing"]
    ):
        raise RuntimeError(f"{path.name}: coverage изменился после сохранения")

    plan.update(
        {
            "path": str(path),
            "pre_sha256": pre_sha,
            "post_sha256": post_sha,
            "backup_path": str(backup_path) if backup_path is not None else None,
            "backup_sha256": backup_sha,
            "errors": [],
            "post_plan": _plan_summary(post_plan),
        }
    )
    return plan


class BookChangedDuringRun(RuntimeError):
    """Книгу на шаре изменили или открыли, пока шло обогащение: не записано."""


# Способы записи личных книг. «share» — как было до 30.09.2026: Excel открывает
# книгу прямо на шаре, пишет по ячейке, сохраняет туда же и открывает ещё раз
# для проверки. «share+batch» — то же, но запись отрезками. «local» — книга
# один раз читается с шары в локальную папку, Excel работает с копией (запись
# отрезками, сохранение, повторное открытие и нулевой второй план — всё
# локально), и только проверенный файл заменяет книгу на шаре — если за это
# время её никто не открыл и не изменил. С 01.10.2026 «local» — единственный
# способ в работе: на 36 настоящих книгах результат тот же ячейка в ячейку.
ENGINES = ("share", "share+batch", "local")
_REPLACE_RETRIES = (0.2, 0.5, 1.0)


def _replace_on_share(src: Path, dst: Path) -> None:
    """Проверенный файл — на место книги: копия рядом под служебным именем и
    переименование поверх (так сохраняет и сам Excel). Книгу держат открытой —
    Windows переименование не даст, служебную копию убираем."""
    tmp = dst.with_name(f"~hard-{os.getpid()}-{dst.stem[:40]}.tmp")
    try:
        shutil.copyfile(src, tmp)
        for i, pause in enumerate((0.0, *_REPLACE_RETRIES)):
            if pause:
                time.sleep(pause)
            try:
                os.replace(tmp, dst)
                return
            except PermissionError:
                if i == len(_REPLACE_RETRIES):
                    raise
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


@track_automatic_file('path')
def _enrich_local(
    app,
    path: Path,
    fields: list[dict],
    rows: list[dict],
    log=lambda s: None,
    since=None,
    until=None,
    backup_root=None,
    ledger=None,
    server_authoritative: bool = False,
    decisions=None,
    work_dir=None,
    before_commit=None,
    on_publication_intent=None,
) -> dict:
    """Способ «local»: тот же план, те же доказательства, но Excel — локально.

    Порядок: книга свободна → один раз прочитать байты с шары (pre_sha) →
    две локальные копии (нетронутая и рабочая) → Excel открывает рабочую,
    строит план и пишет отрезками → сохранить → открыть заново и получить
    нулевой второй план → копия «до обогащения» из нетронутой → книга на шаре
    всё ещё та же и никем не открыта → заменить → хэш на шаре = хэш рабочей.
    Всё, что ломается ДО замены, оставляет книгу на шаре нетронутой.
    """
    raw_path = Path(path)
    if raw_path.is_symlink() or not raw_path.is_file():
        raise FileNotFoundError(f"нужен обычный Excel-файл: {raw_path}")
    path = raw_path.resolve()
    t0 = time.perf_counter()
    steps: list[tuple[str, float]] = []

    def mark(name: str) -> None:
        steps.append((name, round(time.perf_counter() - t0, 2)))
    assert_writable(path)
    st0 = os.stat(path)
    blob = _read_regular_bytes(path, label="книга")
    mark("чтение с шары")
    pre_sha = hashlib.sha256(blob).hexdigest()
    folder = Path(tempfile.mkdtemp(prefix="hard-enr-", dir=work_dir))
    try:
        pristine = folder / "src" / path.name
        work = folder / path.name
        pristine.parent.mkdir()
        pristine.write_bytes(blob)
        os.utime(pristine, ns=(st0.st_atime_ns, st0.st_mtime_ns))
        work.write_bytes(blob)
        wb = app.books.open(str(work),update_links=False) if on_publication_intent else app.books.open(str(work))
        try:
            protection=_all_sheet_cells(wb) if on_publication_intent else None
            ws, grid = _workbook_grid(wb, fields)
            plan = build_plan(grid, fields, rows, since, until,
                              ledger=ledger, server_authoritative=server_authoritative,
                              decisions=decisions)
            writes = plan["writes"]
            _set_payment_statistics_plan(plan,wb,grid,fields,rows,since,until)
            allowed={(ws.name,r,c) for r,c,*rest in writes}
            for change in plan['statistics_formula_changes']:
                cell=wb.sheets[change['sheet']].range(change['cell'])
                allowed.add((change['sheet'],cell.row-1,cell.column-1))
            has_changes = bool(writes or plan['statistics_formula_changes'])
            if has_changes:
                _write_cells(ws, writes, log, path.name)
                _apply_payment_statistics(wb,plan['statistics_formula_changes'])
                wb.save()
            plan["sheet"] = ws.name
        finally:
            wb.close()
        mark("Excel: план и запись локально")
        plan["file"] = str(path)
        plan["backup"] = None
        backup_path = backup_sha = None
        if not has_changes:
            # Писать нечего — шару не трогаем. Хэш после — честный, с шары:
            # если книгу за это время изменили, zero-write контракт это покажет.
            post_sha = _sha256_file(path)
            if post_sha != pre_sha:
                raise RuntimeError(f"{path.name}: zero-write contract нарушен")
            post_plan = build_plan(grid, fields, rows, since, until, ledger=ledger,
                                   server_authoritative=server_authoritative,
                                   decisions=decisions)
        else:
            post_plan = _plan_workbook(app, work, fields, rows, since, until,
                                       ledger=ledger,
                                       server_authoritative=server_authoritative,
                                       decisions=decisions,
                                       **({'preservation':(protection,allowed)} if protection is not None else {}))
            if post_plan["cells_written"] or post_plan["changed_cases"] or post_plan.get('statistics_formula_changes'):
                raise RuntimeError(f"{path.name}: повторный enrichment plan не нулевой "
                                   "— на шару ничего не записано")
            mark("Excel: повторная проверка")
            if protection is not None:
                plan['unplanned_cells_and_formulas_preserved']=True
                plan['vba_preserved']=_verify_vba_copy(pristine,work)
            work_sha = _sha256_file(work)
            backup_path = backup_copy(path, backup_root, source=pristine).resolve()
            backup_sha = _sha256_file(backup_path)
            if backup_sha != pre_sha:
                raise RuntimeError(f"{path.name}: копия «до обогащения» не совпала с "
                                   "книгой — на шару ничего не записано")
            plan["backup"] = str(backup_path)
            mark("копия «до» на шаре")
            if before_commit:
                before_commit(path)
            owner = workbook_busy_owner(path)
            if owner is not None:
                raise BookChangedDuringRun(_busy_reason(path, owner))
            if _sha256_file(path) != pre_sha:
                raise BookChangedDuringRun(
                    f"{path.name}: книгу изменили, пока шло обогащение — ничего не "
                    "записано; прогони заново")
            mark("книга не тронута — проверено")
            if on_publication_intent:
                from pult.core.manual_sync import publication_identity
                evidence = {k:v for k,v in plan.items() if k != 'writes'}
                evidence.update(path=str(path), file=str(path), pre_sha256=pre_sha,
                                post_sha256=work_sha, backup_path=str(backup_path),
                                backup_sha256=backup_sha, post_plan=_plan_summary(post_plan),
                                provenance_operation_id=publication_identity(path))
                on_publication_intent(path, evidence)
            try:
                _replace_on_share(work, path)
            except PermissionError as e:
                raise BookChangedDuringRun(
                    f"{path.name}: книгу открыли, пока шло обогащение — ничего не "
                    "записано; прогони заново") from e
            try:
                post_sha = _sha256_file(path)
            except Exception as e:  # noqa: BLE001 — записано, но не проверено
                raise WrittenButUnverified(
                    path=str(path), pre_sha256=pre_sha, post_sha256=None,
                    backup_path=str(backup_path), backup_sha256=backup_sha,
                    cells_written=plan["cells_written"],
                    displaced=plan.get("displaced") or [],
                    ledger_entries=plan.get("ledger_entries") or [],
                    conflicts=plan.get("conflicts") or [],
                    office_edits=plan.get("office_edits") or [],
                    resolved=plan.get("resolved") or [],
                    reason=str(e)) from e
            mark("замена на шаре и хэш после")
            if post_sha != work_sha:
                raise WrittenButUnverified(
                    path=str(path), pre_sha256=pre_sha, post_sha256=post_sha,
                    backup_path=str(backup_path), backup_sha256=backup_sha,
                    cells_written=plan["cells_written"],
                    displaced=plan.get("displaced") or [],
                    ledger_entries=plan.get("ledger_entries") or [],
                    conflicts=plan.get("conflicts") or [],
                    office_edits=plan.get("office_edits") or [],
                    resolved=plan.get("resolved") or [],
                    reason="файл на шаре после замены не совпал с проверенной копией")
        if (
            post_plan["filled_contract_ids"] != plan["filled_contract_ids"]
            or post_plan["matched_contract_ids"] != plan["matched_contract_ids"]
            or post_plan["missing"] != plan["missing"]
        ):
            raise RuntimeError(f"{path.name}: coverage изменился после сохранения")
        plan.update({
            "path": str(path),
            "pre_sha256": pre_sha,
            "post_sha256": post_sha,
            "backup_path": str(backup_path) if backup_path is not None else None,
            "backup_sha256": backup_sha,
            "errors": [],
            "post_plan": _plan_summary(post_plan),
            "engine": "local",
            "steps": steps,
        })
        log(f"{path.name}: " + " · ".join(f"{n} {t} с" for n, t in steps))
        return plan
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def enrich_file(path: str | Path, fields: list[dict], rows: list[dict],
                log=lambda s: None, since=None, until=None,
                backup_root=None, ledger=None,
                server_authoritative: bool = False, decisions=None) -> dict:
    """Обогатить один живой файл реестра. Требует Excel."""
    _co_init()
    import xlwings as xw

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"файл не найден: {path}")
    log("Открываю Excel…")
    with open_excel(xw, log) as app:
        app.display_alerts = False
        app.screen_updating = False
        log(f"Обогащаю {path.name}…")
        return _enrich_with_evidence(app, path, fields, rows, log,
                                     since, until, backup_root,
                                     ledger=ledger,
                                     server_authoritative=server_authoritative,
                                     decisions=decisions)


def enrich_many(jobs: list[dict], fields: list[dict], log=lambda s: None,
                since=None, until=None, backup_root=None,
                receipt_dir=None, ledger=None, on_delivered=None,
                server_authoritative: bool = False, decisions=None,
                engine: str = "local", work_dir=None,
                before_commit=None, on_publication_intent=None, excel_app=None) -> list[dict]:
    """Обогатить личные реестры пачкой, одной сессией Excel.
    jobs: [{employee, path, rows}] — каждому файлу свои строки (его кейсы).
    Ошибка одного файла не валит остальные: в результат кладётся {..., error}.

    since/until — окно дат выезда, которое задал оператор.

    ledger — реестр доставленного с сервера (None — прежнее поведение без
    реестра). on_delivered(payload) — колбэк пульта, который отправляет на
    сервер {"entries": [...], "receipt_digest": ..., "receipt": ...}; его
    провал попадает в результат как ledger_error, а не теряется в логе.
    server_authoritative — явная галочка оператора, см. build_plan.
    decisions — решения оператора по конфликтам {(КД, поле): "server"|"file"},
    обычно собранные в пульте после preview_many.
    engine — способ записи (ENGINES). По умолчанию «local» у всех входов: оба
    пульта и командная строка; «share» остался для замеров и тестов.

    Результат СОБИРАЕТСЯ СНАРУЖИ блока Excel. Раньше он жил внутри, и смерть
    сессии на выходе (`quit()` по мёртвому COM) уносила его целиком: книги на
    диске сохранены, а чем они кончились — неизвестно, включая пути резервных
    копий. Теперь смерть Excel гасится, а всё, что успели, доезжает до отчёта.
    """
    _co_init()
    import xlwings as xw

    # Окно разбираем ДО открытия Excel: неразобранная граница должна остановить
    # прогон, а не тихо превратиться в «без границы».
    visit_window(since, until)
    if engine not in ENGINES:
        raise ValueError(f"неизвестный способ записи: {engine!r}")

    results: list[dict] = []
    attempted: set[int] = set()
    decision_identities = {}
    fatal = None
    try:
        from contextlib import nullcontext
        with (nullcontext(excel_app) if excel_app is not None else open_excel(xw, log)) as app:
            app.display_alerts = False
            app.screen_updating = False
            for i, job in enumerate(jobs, 1):
                path = Path(job["path"])
                book_ledger = ledger_for_book(ledger, path)
                book_decisions = decisions_for_book(decisions, path,identities=decision_identities)
                log(f"[{i}/{len(jobs)}] {job['employee']} — {path.name}…")
                attempted.add(i)
                try:
                    if engine == "local":
                        plan = _enrich_local(
                            app, path, fields, job["rows"], log,
                            since, until, backup_root,
                            ledger=book_ledger,
                            server_authoritative=server_authoritative,
                            decisions=book_decisions, work_dir=work_dir,
                            before_commit=before_commit,
                            on_publication_intent=on_publication_intent,
                        )
                    else:
                        # batch — только когда он выбран: прежний вызов остаётся
                        # прежним до буквы (на нём держатся заглушки тестов).
                        extra = {"batch": True} if engine == "share+batch" else {}
                        plan = _enrich_with_evidence(
                            app, path, fields, job["rows"], log,
                            since, until, backup_root,
                            ledger=book_ledger,
                            server_authoritative=server_authoritative,
                            decisions=book_decisions, **extra,
                        )
                except WrittenButUnverified as e:
                    # Записано, но не проверено. НЕ откатывать. Ключ error нужен,
                    # иначе отчёт посчитает такую книгу успехом и покажет
                    # зелёное «файлов 7 из 7».
                    plan = {**e.info, "file": str(path), "written_unverified": True,
                            "error": e.info.get("reason") or str(e)}
                    log(f"{path.name}: записано, но проверить не удалось — "
                        f"откатывать эту книгу нельзя")
                except Exception as e:  # noqa: BLE001 — занят/битый: дальше по списку
                    plan = {"file": str(path), "error": str(e)}
                plan["employee"] = job["employee"]
                results.append(plan)
    except Exception as e:  # noqa: BLE001 — смерть сессии Excel не уносит результат
        fatal = str(e)
        log(f"Сессия Excel оборвалась: {e}")

    # Недошедшие ищем ПО НОМЕРУ задания, а не сравнением строк путей: результат
    # несёт разрешённый путь (resolve), а задание — сырой из настроек пульта. На
    # сетевом диске, в UNC и при другом регистре эти строки расходятся, и
    # успешно обогащённая книга попадала в отчёт вторым разом как «не дошла
    # очередь» — с предложением прогнать её заново.
    for i, job in enumerate(jobs, 1):
        if i not in attempted:
            results.append({"file": str(Path(job["path"])),
                            "employee": job["employee"],
                            "not_attempted": True,
                            "error": fatal or "не дошла очередь"})
    if fatal:
        for r in results:
            r.setdefault("session_error", fatal)
    receipt_path = None
    if receipt_dir:
        # Провал квитанции обязан быть виден в итоговом отчёте, а не мелькнуть в
        # строке лога, которую отчёт через мгновение перезапишет.
        try:
            path = write_batch_receipt(results, receipt_dir,
                                       since=since, until=until, fatal=fatal)
            receipt_path = path
            for r in results:
                r["receipt"] = str(path)
        except Exception as e:  # noqa: BLE001 — квитанция не важнее данных
            for r in results:
                r["receipt_error"] = str(e)
    if ledger is not None:
        _record_deliveries(results, on_delivered, receipt_path)
    return results


def preview_many(jobs: list[dict], fields: list[dict], log=lambda s: None,
                 since=None, until=None, ledger=None,
                 server_authoritative: bool = False, decisions=None) -> list[dict]:
    """План обогащения по каждой книге БЕЗ Excel и БЕЗ записи.

    Книга читается стабильной копией через calamine (как при импорте), план
    строится тем же build_plan, что и при записи. Нужен, чтобы оператор увидел
    конфликты и решил их в пульте ДО того, как Excel откроет живые файлы.
    Возвращает по файлу тот же словарь, что и план, без списка writes.
    """
    visit_window(since, until)
    results: list[dict] = []
    from concurrent.futures import ThreadPoolExecutor
    decision_identities = {}
    for key in (decisions or {}):
        if len(key)==3 and book_key(key[0]) not in decision_identities:
            decision_identities[book_key(key[0])]=canonical_book_key(key[0])
    def check(job):
        path = Path(job["path"])
        try:
            book = read_stable_workbook(path)
            sheet = next((s for s in book["sheets"] if s["name"] == SHEET_NAME), None)
            if sheet is None:
                raise ValueError(f"обязательный лист «{SHEET_NAME}» отсутствует")
            plan = build_plan(sheet["grid"], fields, job["rows"], since, until,
                              ledger=ledger_for_book(ledger, path), server_authoritative=server_authoritative,
                              decisions=decisions_for_book(decisions, path,identities=decision_identities))
            plan.pop("writes", None)
            plan["file_sha256"] = book["file_sha256"]
        except Exception as e:  # noqa: BLE001 — одна книга не валит проверку
            plan = {"error": str(e)}
        plan["file"] = str(path)
        plan["path"] = str(path)
        plan["employee"] = job["employee"]
        plan["preview"] = True
        # Проверка читает стабильную копию и занятой книги не замечает — а
        # запись в неё не пройдёт. Пусть оператор узнает об этом ДО прогона,
        # а не из красной строки в отчёте после него.
        owner = workbook_busy_owner(path)
        plan["busy"] = owner is not None
        plan["busy_owner"] = owner or ""
        return plan
    log(f"Сверяю книги: {len(jobs)}…")
    with ThreadPoolExecutor(max_workers=min(4,len(jobs) or 1)) as pool:
        for i,plan in enumerate(pool.map(check,jobs),1):
            results.append(plan)
            log(f"[{i}/{len(jobs)}] проверено: {plan['employee']} — {Path(plan['file']).name}")
    return results


def ledger_entries_of(results: list[dict]) -> list[dict]:
    """Записи реестра доставленного из результатов пачки, с владельцем и файлом.

    Берём и книги «записано, но не проверено»: ячейки в них уже стоят, и
    реестр обязан это знать — иначе следующий прогон увидит «файл = сервер,
    истории нет» и досеет сам, но между прогонами правка офиса в этих ячейках
    была бы неотличима от отставания файла.
    """
    entries: list[dict] = []
    for r in results:
        if r.get("not_attempted") or (
            "error" in r and not r.get("written_unverified")
        ):
            continue
        for e in r.get("ledger_entries") or []:
            entries.append({**e, "responsible": r.get("employee") or "",
                            "file": r.get("path") or r.get("file") or ""})
    return entries


def _record_deliveries(results: list[dict], on_delivered, receipt_path) -> None:
    """Отправить реестр доставленного на сервер ПОСЛЕ квитанции.

    Порядок важен: квитанция — локальный долговечный след с теми же записями,
    и если сеть упала, оператор видит красную строку и может повторить отправку
    из квитанции (replay_ledger_receipt), а не терять историю доставки.
    """
    entries = ledger_entries_of(results)
    if not entries:
        for r in results:
            r.setdefault("ledger_recorded", 0)
        return
    if on_delivered is None:
        for r in results:
            r["ledger_error"] = "реестр доставленного некому отправить (нет on_delivered)"
        return
    payload = {
        "entries": entries,
        "receipt_digest": _sha256_file(Path(receipt_path)) if receipt_path else "",
        "receipt": str(receipt_path or ""),
    }
    try:
        outcome = on_delivered(payload)
    except Exception as e:  # noqa: BLE001 — сеть не важнее данных, но видна
        for r in results:
            r["ledger_error"] = str(e)
        return
    recorded = outcome.get("recorded") if isinstance(outcome, dict) else None
    if recorded is not None and (
        isinstance(recorded, bool) or not isinstance(recorded, int)
        or recorded != len(entries)
    ):
        message = (f"Сервер подтвердил {recorded} из {len(entries)} значений "
                   "истории доставки. Сверьте квитанцию перед повтором.")
        for r in results:
            r["ledger_error"] = message
        return
    for r in results:
        eligible = not r.get("not_attempted") and (
            "error" not in r or r.get("written_unverified")
        )
        r["ledger_recorded"] = len(r.get("ledger_entries") or []) if eligible else 0


def replay_ledger_receipt(receipt_path: str | Path, on_delivered) -> dict:
    """Повторно отправить реестр доставленного из квитанции пачки.

    Нужен, когда книги записаны, квитанция лежит, а сервер в момент прогона
    был недоступен. Идемпотентно: сервер хранит последнее значение на (КД, поле).
    """
    path = Path(receipt_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = [e for e in data.get("ledger_entries") or [] if isinstance(e, dict)]
    if not entries:
        return {"recorded": 0, "entries": 0}
    outcome = on_delivered({
        "entries": entries,
        "receipt_digest": _sha256_file(path),
        "receipt": str(path),
    })
    recorded = outcome.get("recorded") if isinstance(outcome, dict) else None
    return {"recorded": len(entries) if recorded is None else recorded,
            "entries": len(entries)}


RECEIPT_GLOB = "обогащение-*.json"
SUGGESTED_WINDOW_MARGIN_DAYS = 7


def last_batch_receipt(receipt_dir) -> Path | None:
    """Самая свежая квитанция пачки в папке (по имени: в нём метка времени)."""
    if not receipt_dir:
        return None
    folder = Path(receipt_dir)
    if not folder.is_dir():
        return None
    candidates = sorted(p for p in folder.glob(RECEIPT_GLOB) if p.is_file())
    return candidates[-1] if candidates else None


def suggest_window(receipt_dir, today: date | None = None) -> tuple[str, str]:
    """Предложить окно дат по последней квитанции: «с» = день прошлого прогона
    минус запас, «по» = сегодня. Без квитанций — пустые строки (окно не задано,
    и пульт обязан спросить подтверждение «вся история»).

    Запас в неделю нужен потому, что выездник может сохранить визит задним
    числом; от затирания правок защищает реестр доставленного, окно лишь
    сужает прогон.
    """
    today = today or date.today()
    receipt = last_batch_receipt(receipt_dir)
    if receipt is None:
        return "", ""
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
        ran_at = parse_date(str(data.get("at") or ""))
    except (OSError, ValueError, json.JSONDecodeError):
        ran_at = None
    if ran_at is None:
        return "", ""
    since = ran_at - timedelta(days=SUGGESTED_WINDOW_MARGIN_DAYS)
    return since.strftime("%d.%m.%Y"), today.strftime("%d.%m.%Y")


def write_batch_receipt(results: list[dict], receipt_dir, *,
                        since=None, until=None, fatal=None) -> Path:
    """Долговечный след пачки: пути и хэши копий, окно, вытесненные значения.

    Раньше это умел только CLI-путь `evidence_run`, а батч из пульта показывал
    один счётчик «копии созданы (N)» — без имён и без хэшей. После прогона
    04.08.2026 не осталось доказательной связки «эта копия — от этого прогона»
    ни для одной из семи книг.
    """
    folder = Path(receipt_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    # Пишем ПРИМЕНЁННОЕ окно, а не то, что набрал человек: иначе квитанция
    # утверждала бы наличие границы там, где фильтр её не применил.
    since_d, until_d = visit_window(since, until)
    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "window": {"since": since_d.isoformat() if since_d else "",
                   "until": until_d.isoformat() if until_d else ""},
        "window_input": {"since": str(since or ""), "until": str(until or "")},
        "session_error": fatal or "",
        "files": [
            {k: r.get(k) for k in (
                "employee", "file", "path", "backup_path", "backup_sha256",
                "pre_sha256", "post_sha256", "cells_written",
                "cells_over_nonempty", "changed_cases", "out_of_window",
                "ledger_mode", "server_authoritative", "ledger_seeded",
                "cells_updated_delivered", "resolved_file", "resolved_server",
                "error", "written_unverified", "not_attempted", "statistics_formula_changes", "statistics_warnings")}
            for r in results
        ],
        "displaced": [
            {**d, "employee": r.get("employee")}
            for r in results for d in (r.get("displaced") or [])
        ],
        # Реестр доставленного: что ушло/должно уйти на сервер, и что НЕ
        # записано по решению трёхстороннего сравнения. Квитанция — локальный
        # след, из которого отправку можно повторить (replay_ledger_receipt).
        "ledger_entries": ledger_entries_of(results),
        "conflicts": [
            {**c, "employee": r.get("employee")}
            for r in results for c in (r.get("conflicts") or [])
        ],
        "office_edits": [
            {**c, "employee": r.get("employee")}
            for r in results for c in (r.get("office_edits") or [])
        ],
        "resolved": [
            {**c, "employee": r.get("employee")}
            for r in results for c in (r.get("resolved") or [])
        ],
    }
    out = folder / f"обогащение-{stamp}.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(out)
    return out


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_snapshot_inputs(snapshot_raw: str | Path) -> tuple[Path, dict, list, list]:
    snapshot = Path(snapshot_raw)
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise RuntimeError(f"Некорректный snapshot: {snapshot}")
    snapshot = snapshot.resolve()
    manifest_path = snapshot / "snapshot_manifest.json"
    safe_path = snapshot / "safe-enrichment.json.gz"
    schema_path = snapshot / "export-schema.json"
    raw_path = snapshot / "raw-diagnostic.json.gz"
    for path in (manifest_path, safe_path, schema_path, raw_path):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Snapshot artifact отсутствует/не regular: {path}")
    manifest_bytes = _read_regular_bytes(
        manifest_path,
        label="snapshot manifest",
    )
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Snapshot manifest содержит некорректный JSON") from exc
    artifacts = manifest.get("artifacts") or {}
    artifact_bytes = {
        safe_path: _read_regular_bytes(safe_path, label="safe export"),
        schema_path: _read_regular_bytes(schema_path, label="export schema"),
        raw_path: _read_regular_bytes(raw_path, label="raw export"),
    }
    for path, content in artifact_bytes.items():
        expected = artifacts.get(path.name)
        if (
            not isinstance(expected, str)
            or hashlib.sha256(content).hexdigest() != expected
        ):
            raise RuntimeError(f"Snapshot SHA не совпал: {path.name}")
    try:
        safe = json.loads(gzip.decompress(artifact_bytes[safe_path]).decode("utf-8"))
        fields = json.loads(artifact_bytes[schema_path].decode("utf-8"))
        raw = json.loads(gzip.decompress(artifact_bytes[raw_path]).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Safe/raw export/schema содержит некорректный JSON") from exc
    rows = safe.get("rows") if isinstance(safe, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("kind") != SNAPSHOT_KIND
        or manifest.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT
        or not isinstance(safe, dict)
        or safe.get("kind") != "safe_enrichment"
        or safe.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT
        or not isinstance(raw, dict)
        or raw.get("kind") != "raw_diagnostic"
        or raw.get("data_exchange_contract") != DATA_EXCHANGE_CONTRACT
        or not isinstance(rows, list)
        or not isinstance(fields, list)
        or len(rows) != (manifest.get("counts") or {}).get("clients")
    ):
        raise RuntimeError(
            "Snapshot несовместим с локальным data-exchange contract"
        )
    try:
        verify_export_schema(fields)
    except ExchangeContractError as exc:
        raise RuntimeError(
            f"Snapshot export schema несовместима с контрактом: {exc}"
        ) from exc
    binding = {
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "database_state_digest": manifest.get("database_state_digest"),
        "safe_export_sha256": artifacts.get(safe_path.name),
        "raw_export_sha256": artifacts.get(raw_path.name),
        "data_exchange_contract_sha256": DATA_EXCHANGE_CONTRACT["sha256"],
    }
    if any(
        not isinstance(value, str) or len(value) != 64
        for value in binding.values()
    ):
        raise RuntimeError("Snapshot binding неполный")
    return snapshot, binding, fields, rows


def _completed_by_owner(rows: list[dict]) -> dict[str, dict[str, Any]]:
    owners: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in rows:
        if row.get(FILLED_MARKER) is not True:
            continue
        cid = norm_cid(row.get(CONTRACT_KEY))
        responsible = str(row.get("responsible") or "").strip()
        if not cid or cid in seen:
            raise RuntimeError("Safe export содержит пустой/duplicate completed КД")
        if not responsible:
            raise RuntimeError(f"Завершённый визит без ответственного: {cid}")
        seen.add(cid)
        key = norm_name(responsible)
        item = owners.setdefault(
            key,
            {"responsible": responsible, "contract_ids": [], "rows": []},
        )
        if item["responsible"] != responsible:
            raise RuntimeError(
                "Ответственные различаются только нормализацией имени: "
                f"{item['responsible']} / {responsible}"
            )
        item["contract_ids"].append(cid)
        item["rows"].append(row)
    for item in owners.values():
        order = sorted(
            range(len(item["contract_ids"])),
            key=lambda index: item["contract_ids"][index],
        )
        item["contract_ids"] = [item["contract_ids"][index] for index in order]
        item["rows"] = [item["rows"][index] for index in order]
    return owners


def _load_file_map(
    path_raw: str | Path,
    *,
    binding: dict[str, str],
    owners: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    path = Path(path_raw)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"File map отсутствует/не regular: {path}")
    payload = _load_json_regular(path, label="file map")
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != FILE_MAP_SCHEMA
        or payload.get("snapshot") != binding
        or set(payload) != {"schema", "snapshot", "owners"}
        or not isinstance(payload.get("owners"), list)
    ):
        raise RuntimeError("File map schema/snapshot binding не совпал")
    mapped: dict[str, dict[str, Any]] = {}
    all_files: set[str] = set()
    jobs: list[dict[str, Any]] = []
    for raw in payload["owners"]:
        if not isinstance(raw, dict) or set(raw) != {
            "responsible",
            "contract_ids",
            "files",
        }:
            raise RuntimeError("File map owner имеет неверную schema")
        responsible = str(raw["responsible"]).strip()
        key = norm_name(responsible)
        if key in mapped or key not in owners:
            raise RuntimeError(f"File map содержит чужого/duplicate owner: {responsible}")
        expected = owners[key]
        if (
            responsible != expected["responsible"]
            or raw["contract_ids"] != expected["contract_ids"]
            or not isinstance(raw["files"], list)
            or not raw["files"]
        ):
            raise RuntimeError(f"File map owner contract нарушен: {responsible}")
        files: list[str] = []
        for item in raw["files"]:
            candidate = Path(item)
            if candidate.is_symlink() or not candidate.is_file():
                raise RuntimeError(f"Personal-файл отсутствует/не regular: {candidate}")
            resolved = str(candidate.resolve())
            if resolved in all_files:
                raise RuntimeError(f"Personal-файл указан повторно: {resolved}")
            all_files.add(resolved)
            files.append(resolved)
            jobs.append(
                {
                    "employee": responsible,
                    "path": resolved,
                    "rows": expected["rows"],
                    "owner_contract_ids": expected["contract_ids"],
                }
            )
        mapped[key] = {"responsible": responsible, "files": files}
    if set(mapped) != set(owners):
        missing = sorted(set(owners) - set(mapped))
        raise RuntimeError(f"File map не покрывает exact owners: {missing}")
    return jobs


def _receipt_file(result: dict, expected_ids: list[str]) -> dict[str, Any]:
    if "error" in result:
        raise RuntimeError(
            f"{result.get('employee')}/{result.get('file')}: {result['error']}"
        )
    return {
        "responsible": result["employee"],
        "path": str(Path(result["path"]).resolve()),
        "pre_sha256": result["pre_sha256"],
        "post_sha256": result["post_sha256"],
        "backup_path": result["backup_path"],
        "backup_sha256": result["backup_sha256"],
        "owner_contract_ids": list(expected_ids),
        "filled_cases": result["filled_cases"],
        "filled_contract_ids": list(result["filled_contract_ids"]),
        "matched_cases": result["matched_cases"],
        "matched_contract_ids": list(result["matched_contract_ids"]),
        "changed_cases": result["changed_cases"],
        "cells_written": result["cells_written"],
        "errors": list(result["errors"]),
        "visit_cols_absent": list(result["visit_cols_absent"]),
        "missing_contract_ids": list(result["missing"]),
        "post_plan": result["post_plan"],
    }


def _write_receipt(path_raw: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path_raw)
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"Evidence receipt уже существует: {path}")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RuntimeError(f"Небезопасный parent evidence receipt: {path.parent}")
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    temporary = path.parent / (
        f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def evidence_run(
    *,
    snapshot_dir: str | Path,
    file_map: str | Path,
    output: str | Path,
    log=lambda s: None,
) -> dict[str, Any]:
    """Run the existing enrichment writer and emit a machine-only receipt."""

    snapshot, binding, fields, rows = _load_snapshot_inputs(snapshot_dir)
    output_path = Path(output)
    if output_path.parent.resolve() != snapshot:
        raise RuntimeError(
            "Evidence receipt должен быть прямым файлом внутри snapshot"
        )
    if output_path.exists() or output_path.is_symlink():
        raise RuntimeError(f"Evidence receipt уже существует: {output_path}")
    if output_path.parent.is_symlink() or not output_path.parent.is_dir():
        raise RuntimeError(
            f"Небезопасный parent evidence receipt: {output_path.parent}"
        )
    owners = _completed_by_owner(rows)
    jobs = _load_file_map(
        file_map,
        binding=binding,
        owners=owners,
    )
    results = enrich_many(jobs, fields, log) if jobs else []
    by_job = {
        (job["employee"], str(Path(job["path"]).resolve())): job
        for job in jobs
    }
    files: list[dict[str, Any]] = []
    covered: set[str] = set()
    for result in results:
        result_path = result.get("path") or result.get("file")
        if not result_path:
            raise RuntimeError("Enrichment result does not identify its file")
        key = (result["employee"], str(Path(result_path).resolve()))
        job = by_job.get(key)
        if job is None:
            raise RuntimeError("Enrichment вернул неожиданный файл/owner")
        item = _receipt_file(result, job["owner_contract_ids"])
        expected = set(job["owner_contract_ids"])
        matched = set(item["matched_contract_ids"])
        if (
            item["errors"]
            or item["visit_cols_absent"]
            or set(item["filled_contract_ids"]) != expected
            or not matched <= expected
            or covered & matched
            or item["post_plan"]["cells_written"] != 0
            or item["post_plan"]["changed_cases"] != 0
        ):
            raise RuntimeError(f"Evidence gate не пройден: {item['path']}")
        covered.update(matched)
        files.append(item)
    completed = sorted(
        cid for owner in owners.values() for cid in owner["contract_ids"]
    )
    if covered != set(completed):
        raise RuntimeError(
            "Enrichment не покрыл exact completed set: "
            f"covered={len(covered)}, expected={len(completed)}"
        )
    receipt = {
        "schema": EVIDENCE_SCHEMA,
        "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
        "created_at": datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat(),
        "snapshot": binding,
        "completed_contract_ids": completed,
        "owners": [
            {
                "responsible": item["responsible"],
                "contract_ids": item["contract_ids"],
                "files": sorted(
                    str(Path(job["path"]).resolve())
                    for job in jobs
                    if job["employee"] == item["responsible"]
                ),
            }
            for item in sorted(
                owners.values(),
                key=lambda value: value["responsible"].casefold(),
            )
        ],
        "expected_files": sorted(
            str(Path(job["path"]).resolve())
            for job in jobs
        ),
        "files": sorted(files, key=lambda item: item["path"].casefold()),
    }
    receipt["receipt_digest"] = hashlib.sha256(_canonical(receipt)).hexdigest()
    _write_receipt(output_path, receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    evidence = commands.add_parser(
        "evidence-run",
        help="обогатить exact snapshot rows и выдать machine evidence",
    )
    evidence.add_argument("--snapshot-dir", type=Path, required=True)
    evidence.add_argument("--file-map", type=Path, required=True)
    evidence.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = evidence_run(
            snapshot_dir=args.snapshot_dir,
            file_map=args.file_map,
            output=args.output,
            log=lambda message: print(message, file=sys.stderr),
        )
    except Exception as exc:  # noqa: BLE001 - owner CLI must fail closed.
        print(f"ОТКАЗ: {exc}", file=sys.stderr)
        return 4
    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "receipt_digest": payload["receipt_digest"],
                "completed_cases": len(payload["completed_contract_ids"]),
                "files": len(payload["files"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
