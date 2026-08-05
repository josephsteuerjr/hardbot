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
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Один JSON-контракт живёт в build-context hardbot и теми же байтами читается
# операторским пультом. Жёстко проверяем origin модуля, чтобы окружение Python
# не подсунуло одноимённый пакет раньше checkout.
_HARDBOT_DIR = Path(__file__).resolve().parents[1] / "hardbot"
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
        orphan_cids   — первые ORPHAN_SAMPLE номеров КД таких визитов.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.orphan_rows = 0
        self.orphan_filled = 0
        self.orphan_cids: list[str] = []


def split_by_employee(rows: list[dict]) -> EmployeeSplit:
    """Строки сервера → по ответственным: {ключ: {employee, rows, filled}}.
    filled — сколько кейсов сотрудника заполнено ботом (кандидаты на обогащение).

    Строки без ответственного не принадлежат никому: личного файла у них нет,
    поэтому в обогащение они не попадают. Раньше они отбрасывались МОЛЧА — так
    из выгрузки выпали 173 завершённых визита за 09-17.07, и жалоба Ильи о
    пропавших комментариях неделю висела без объяснения. Теперь они считаются."""
    out = EmployeeSplit()
    for r in rows:
        emp = str(r.get("responsible") or "").strip()
        if not emp:
            out.orphan_rows += 1
            if is_filled(r):
                out.orphan_filled += 1
                if len(out.orphan_cids) < ORPHAN_SAMPLE:
                    out.orphan_cids.append(norm_cid(r.get(CONTRACT_KEY)))
            continue
        e = out.setdefault(norm_name(emp), {"employee": emp, "rows": [], "filled": 0})
        e["rows"].append(r)
        if is_filled(r):
            e["filled"] += 1
    return out


def _is_source_row(row: list) -> bool:
    """Строка-пометка «сх/хард» под заголовками — не данные."""
    nonempty = [str(v).strip().lower() for v in row if v not in (None, "")]
    return bool(nonempty) and all(v in ("сх", "хард") for v in nonempty)


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
    """Границы окна обогащения как даты. Пустая граница — открытая сторона."""
    return parse_date(since) if since else None, parse_date(until) if until else None


def build_plan(grid: list[list], fields: list[dict], rows: list[dict],
               since=None, until=None) -> dict:
    """План обогащения. grid — лист файла как список строк (значения как есть),
    fields — схема сервера [{key,title,date,...}], rows — строки клиентов сервера.

    since/until — ОКНО ДАТ ВЫЕЗДА, которое задаёт оператор. Без него прогон
    переутверждает базу поверх всей истории визитов, а не только новых: так
    04.08.2026 обогащение вернуло в книгу Уткирова офисные правки за 03.08,
    сделанные уже после предыдущего прогона. Границы включительные, пустая
    граница означает открытую сторону.

    Возвращает {sheet_writes: [(r, c, value, is_date)], ...статистика}. Индексы
    0-based относительно grid. Пишутся ТОЛЬКО ячейки визита заполненных кейсов,
    и только когда значение отличается от файла."""
    header_row, col_map = find_header(grid, fields)
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

    writes: list[tuple[int, int, object, bool]] = []
    filled_cases = matched_cases = changed_cases = 0
    cells_already = cells_over_nonempty = 0
    out_of_window = technical_overwrites = 0
    missing: list[str] = []
    filled_contract_ids: list[str] = []
    matched_contract_ids: list[str] = []
    # Что именно вытесняем. Раньше старое значение не сохранялось нигде, и
    # единственным следом оставалась резервная копия книги: чтобы узнать, что
    # затёрли, приходилось сравнивать файлы целиком.
    displaced: list[dict] = []

    for srv in rows:
        cid = norm_cid(srv.get(CONTRACT_KEY))
        if not cid or not is_filled(srv):
            continue     # без завершённого визита кейс не трогаем вообще
        vdate = parse_date(srv.get("visit_date"))
        if (since_d and vdate < since_d) or (until_d and vdate > until_d):
            out_of_window += 1
            continue
        filled_contract_ids.append(cid)
        values = {k: srv.get(k) for k in VISIT_KEYS
                  if cell_text(srv.get(k)) != "" and k in col_map}
        filled_cases += 1
        targets = file_rows.get(cid)
        if not targets:
            missing.append(cid)
            continue
        matched_cases += 1
        matched_contract_ids.append(cid)
        case_changed = False
        for key, val in values.items():
            c = col_map[key]
            is_date = is_date_key.get(key, False)
            for r in targets:
                cur = grid[r][c] if c < len(grid[r]) else None
                merge_mode = MERGE_MODES[key]
                if merge_mode == "unique_lines":
                    out_val = merge_unique_lines(cur, val)
                elif merge_mode == "preserve_text":
                    out_val = merge_preserving_text(cur, val)
                elif merge_mode == "replace":
                    out_val = parse_date(val) if is_date and parse_date(val) \
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
        # Обратная совместимость отчётов старого пульта. Обычное обогащение
        # больше никогда не очищает комментарии; точечная починка — отдельный
        # dry-run/apply инструмент с собственным бэкапом.
        "poison_cleared": 0,
        "missing": missing,                # заполнены ботом, но строки в файле нет
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
        return xw.App(visible=False)
    except AttributeError as e:
        if "gen_py" not in repr(e) and "CLSIDToPackageMap" not in repr(e):
            raise
        log("Чиню кэш COM-обёрток Excel…")
        clear_gen_py()
        return xw.App(visible=False)


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


def _plan_workbook(app, path: Path, fields: list[dict], rows: list[dict],
                   since=None, until=None) -> dict:
    """Reopen a saved workbook and recompute the enrichment plan read-only."""

    wb = app.books.open(str(path), update_links=False, read_only=True)
    try:
        _ws, grid = _workbook_grid(wb, fields)
        return build_plan(grid, fields, rows, since, until)
    finally:
        wb.close()


# Excel считает формулой всё, что начинается с =, +, - или @. Комментарий
# «=перезвонить» превращается в #ИМЯ?, «5.08» — в число, а повторная сверка плана
# перестаёт сходиться и роняет ВЕСЬ прогон обогащения — уже после того, как файлы
# переписаны. То есть один комментарий одного выездника оставляет офис без
# квитанции и с испорченными реестрами у всех. Ведущий апостроф Excel не
# показывает, но хранит значение текстом.
_EXCEL_FORMULA_START = ("=", "+", "-", "@")
_EXCEL_CELL_LIMIT = 32767


def _excel_safe(value):
    if not isinstance(value, str):
        return value
    v = value[:_EXCEL_CELL_LIMIT]
    if v[:1] in _EXCEL_FORMULA_START:
        return "'" + v
    return v


def backup_copy(path: Path, root: str | Path | None = None) -> Path:
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
    shutil.copy2(path, dst)
    return dst


def _co_init():
    """Пульт зовёт Excel из фонового потока — COM в нём инициализируем явно."""
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except ImportError:
        pass


def _enrich_workbook(app, path: Path, fields: list[dict], rows: list[dict],
                     log=lambda s: None, since=None, until=None,
                     backup_root=None) -> dict:
    """Один файл в уже открытом Excel: бэкап → лист → план → точечная запись →
    сохранить тем же форматом. Возвращает план + backup/sheet/file.

    Осознанно ОДНОсторонний поток (бот → файл): идея «синхронизировать файл в БД
    перед записью» отклонена пользователем и ревью 23.07 — скрытый импорт при
    обогащении реимпортировал бы июльский яд из «комментария звонка» и вставлял
    скелетные КД прошлых сессий. Офисные правки идут только personal →
    registry_cycle → опубликованный master → explicit master import; прямого
    personal → bot gateway нет."""
    wb = app.books.open(str(path))
    try:
        # Читаем строго от A1: used_range может начинаться не с A1,
        # а индексы плана — координаты ячеек листа.
        ws, grid = _workbook_grid(wb, fields)

        plan = build_plan(grid, fields, rows, since, until)
        writes = plan["writes"]
        plan["backup"] = None
        if writes:
            # бэкап — только когда реально есть что писать (файл на диске ещё
            # не изменён: Excel не сохранял); нет записей — нет мусорных копий
            plan["backup"] = str(backup_copy(path, backup_root))
            for i, (r, c, val, _is_date) in enumerate(writes, 1):
                ws.range((r + 1, c + 1)).value = _excel_safe(val)
                if i % 200 == 0:
                    log(f"{path.name}: пишу {i} / {len(writes)}…")
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
    }


def _enrich_with_evidence(
    app,
    path: Path,
    fields: list[dict],
    rows: list[dict],
    log=lambda s: None,
    since=None,
    until=None,
    backup_root=None,
) -> dict:
    """Run the existing writer, then reopen and prove a zero second plan."""

    raw_path = Path(path)
    if raw_path.is_symlink() or not raw_path.is_file():
        raise FileNotFoundError(f"нужен обычный Excel-файл: {raw_path}")
    path = raw_path.resolve()
    pre_sha = _sha256_file(path)
    plan = _enrich_workbook(app, path, fields, rows, log, since, until, backup_root)
    post_sha = _sha256_file(path)
    backup_raw = plan.get("backup")
    backup_path = Path(backup_raw).resolve() if backup_raw else None
    backup_sha = _sha256_file(backup_path) if backup_path is not None else None
    if plan["cells_written"]:
        if backup_path is None or backup_sha != pre_sha or pre_sha == post_sha:
            raise RuntimeError(f"{path.name}: backup/pre/post contract нарушен")
    elif backup_path is not None or pre_sha != post_sha:
        raise RuntimeError(f"{path.name}: zero-write contract нарушен")

    try:
        post_plan = _plan_workbook(app, path, fields, rows, since, until)
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
            displaced=plan.get("displaced") or [], reason=str(e)) from e
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


def enrich_file(path: str | Path, fields: list[dict], rows: list[dict],
                log=lambda s: None, since=None, until=None,
                backup_root=None) -> dict:
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
                                     since, until, backup_root)


def enrich_many(jobs: list[dict], fields: list[dict], log=lambda s: None,
                since=None, until=None, backup_root=None,
                receipt_dir=None) -> list[dict]:
    """Обогатить личные реестры пачкой, одной сессией Excel.
    jobs: [{employee, path, rows}] — каждому файлу свои строки (его кейсы).
    Ошибка одного файла не валит остальные: в результат кладётся {..., error}.

    since/until — окно дат выезда, которое задал оператор.

    Результат СОБИРАЕТСЯ СНАРУЖИ блока Excel. Раньше он жил внутри, и смерть
    сессии на выходе (`quit()` по мёртвому COM) уносила его целиком: книги на
    диске сохранены, а чем они кончились — неизвестно, включая пути резервных
    копий. Теперь смерть Excel гасится, а всё, что успели, доезжает до отчёта.
    """
    _co_init()
    import xlwings as xw

    results: list[dict] = []
    fatal = None
    try:
        with open_excel(xw, log) as app:
            app.display_alerts = False
            app.screen_updating = False
            for i, job in enumerate(jobs, 1):
                path = Path(job["path"])
                log(f"[{i}/{len(jobs)}] {job['employee']} — {path.name}…")
                try:
                    plan = _enrich_with_evidence(
                        app, path, fields, job["rows"], log,
                        since, until, backup_root,
                    )
                except WrittenButUnverified as e:
                    # Записано, но не проверено. НЕ откатывать.
                    plan = {**e.info, "file": str(path), "written_unverified": True}
                    log(f"{path.name}: записано, но проверить не удалось — "
                        f"откатывать эту книгу нельзя")
                except Exception as e:  # noqa: BLE001 — занят/битый: дальше по списку
                    plan = {"file": str(path), "error": str(e)}
                plan["employee"] = job["employee"]
                results.append(plan)
    except Exception as e:  # noqa: BLE001 — смерть сессии Excel не уносит результат
        fatal = str(e)
        log(f"Сессия Excel оборвалась: {e}")

    done = {r.get("file") for r in results}
    for job in jobs:
        if str(Path(job["path"])) not in done:
            results.append({"file": str(Path(job["path"])),
                            "employee": job["employee"],
                            "not_attempted": True,
                            "error": fatal or "не дошла очередь"})
    if fatal:
        for r in results:
            r.setdefault("session_error", fatal)
    if receipt_dir:
        try:
            path = write_batch_receipt(results, receipt_dir,
                                       since=since, until=until, fatal=fatal)
            log(f"Квитанция: {path}")
        except Exception as e:  # noqa: BLE001 — квитанция не важнее данных
            log(f"Квитанцию записать не удалось: {e}")
    return results


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
    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "window": {"since": str(since or ""), "until": str(until or "")},
        "session_error": fatal or "",
        "files": [
            {k: r.get(k) for k in (
                "employee", "file", "path", "backup_path", "backup_sha256",
                "pre_sha256", "post_sha256", "cells_written",
                "cells_over_nonempty", "changed_cases", "out_of_window",
                "error", "written_unverified", "not_attempted")}
            for r in results
        ],
        "displaced": [
            {**d, "employee": r.get("employee")}
            for r in results for d in (r.get("displaced") or [])
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
