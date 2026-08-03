"""enrich.py — планировщик обогащения реестра (чистая логика, без Excel).

Инвариант, ради которого всё затевалось: план трогает ТОЛЬКО кейсы с
завершённым визитом бота (visit_date), и ТОЛЬКО столбцы, которые реально
пишет миниапп. Никаких записей в чужие строки, чужие колонки, пустыми
значениями, системными дефолтами («Не обработан») — и никакой пересборки файла.
"""
import gzip
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import enrich  # noqa: E402
import db  # noqa: E402


# --- фикстуры: мини-реестр формата «Хард» ------------------------------------
FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID", "block": "Инфо", "source": "сх",
     "date": False, "num": False},
    {"key": "client", "title": "Клиент", "block": "Инфо", "source": "сх",
     "date": False, "num": False},
    {"key": "debt_total", "title": "долг всего", "block": "Долг", "source": "сх",
     "date": False, "num": True},
    {"key": "visit_date", "title": "Дата выезда", "block": "Выезд", "source": "хард",
     "date": True, "num": False},
    {"key": "call_status", "title": "Статус звонка", "block": "Выезд", "source": "хард",
     "date": False, "num": False},
    {"key": "call_comment", "title": "комментарий звонка", "block": "Выезд",
     "source": "хард", "date": False, "num": False},
    {"key": "overall_result", "title": "Общий результат", "block": "Выезд",
     "source": "сх", "date": False, "num": False},
    {"key": "extra_address", "title": "Доп. Адрес/Доп.Номер", "block": "Выезд",
     "source": "сх", "date": False, "num": False},
    {"key": "visit_comment", "title": "Комментарий текущего месяца выезда",
     "block": "Выезд", "source": "хард", "date": False, "num": False},
]

# Заголовок «Cтатус звонка» — с ЛАТИНСКОЙ C, как в реальном Хард.xlsb
HEADERS = ["Номер КД / ID", "Клиент", "долг всего", "Дата выезда",
           "Cтатус звонка", "комментарий звонка", "Общий результат",
           "Доп. Адрес/Доп.Номер", "Комментарий текущего месяца выезда"]


def make_grid(data_rows):
    return [
        ["Инфо", None, None, "Выезд", None, None, None, None, None],  # блоки
        list(HEADERS),                                      # заголовки
        ["сх", "сх", "сх", "хард", "хард", "хард", "сх", "сх", "хард"],
        *data_rows,
    ]


def srv_row(cid, **kw):
    row = {f["key"]: None for f in FIELDS}
    row["contract_id"] = cid
    row.update(kw)
    return row


def bot_row(cid, **kw):
    """Строка нового безопасного API: provenance-маркер + данные визита."""
    return srv_row(cid, _bot_completed=True, **kw)


# --- шапка --------------------------------------------------------------------
def test_find_header_and_homoglyph():
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    h, cols = enrich.find_header(grid, FIELDS)
    assert h == 1
    assert cols["contract_id"] == 0
    assert cols["call_status"] == 4          # латинская C в файле не мешает


def test_no_key_column_is_loud():
    grid = [["Что-то", "Другое"], ["1", "2"]]
    try:
        enrich.find_header(grid, FIELDS)
        raise AssertionError("ожидали ValueError")
    except ValueError as e:
        assert "Номер КД" in str(e)


def test_duplicate_recognized_header_is_loud():
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    grid[1].append("Общий результат:")
    with pytest.raises(ValueError, match="повторные заголовки"):
        enrich.find_header(grid, FIELDS)


def test_sheet_picker_has_no_fallback():
    class _Sheet:
        def __init__(self, name):
            self.name = name

    class _Book:
        sheets = [_Sheet("Данные"), _Sheet("Похожий лист")]

    with pytest.raises(ValueError, match="fallback"):
        enrich._pick_sheet(_Book(), FIELDS)


# --- ядро: только заполненные кейсы, только их столбцы -------------------------
def test_only_filled_cases_and_columns():
    grid = make_grid([
        ["T-1", "Иванов", "100", None, None, None, None],
        ["T-2", "Петров", "200", None, None, None, None],
    ])
    rows = [
        bot_row("T-1", call_status="Оплачен", visit_date="2026-07-10"),
        srv_row("T-2"),                                   # бот ничего не заполнял
    ]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["filled_cases"] == 1
    assert plan["matched_cases"] == 1
    assert plan["cells_written"] == 2
    # обе записи — в строку T-1 (индекс 3), в колонки визита
    assert {(r, c) for r, c, *_ in plan["writes"]} == {(3, 6), (3, 3)}
    # дата пишется настоящей датой
    dates = [v for _r, c, v, is_d in plan["writes"] if is_d]
    assert dates == [date(2026, 7, 10)]


def test_never_writes_empty_or_foreign_columns():
    """Пустые значения бота и не-визитные колонки (клиент, долг) не пишутся никогда —
    даже если в БД они отличаются от файла (это защита от «старой выгрузки»)."""
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    rows = [bot_row("T-1", visit_date="2026-07-10", client="СТАРОЕ ИМЯ",
                    debt_total="9999", call_comment="взял трубку")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    cols = {c for _r, c, *_ in plan["writes"]}
    assert cols == {3}, "только дата; клиент, долг и колонки звонков не тронуты"


def test_case_without_finished_visit_is_skipped():
    """Поля визита из СТАРОГО импорта (без visit_date) не считаются работой бота —
    иначе обогащение тащило бы старую выгрузку в живые файлы."""
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    rows = [srv_row("T-1", call_status="Нет контакта", call_comment="из импорта")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["filled_cases"] == 0
    assert plan["cells_written"] == 0


def test_imported_pseudo_visits_never_reach_excel():
    """Регрессия Тошмахматова: 531 стал 14.06.1901 в date-ячейке, а рядом
    уехали CONSUMER/«Не определен»/дата вместо dropdown. Даже ISO visit_date
    без серверного provenance не является завершённым визитом.
    """
    grid = make_grid([
        ["T-1", "Первый", "100", None, None, None, None],
        ["T-2", "Второй", "200", None, None, None, None],
    ])
    rows = [
        srv_row("T-1", visit_date="531", contact_with="CONSUMER",
                agreed_payment="Не определен", client_location="2026-07-09"),
        srv_row("T-2", visit_date="2026-07-08", contact_with="EXPRESS"),
    ]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["filled_cases"] == 0
    assert plan["cells_written"] == 0


def test_system_default_overall_result_never_written():
    """overall_result приходит из системы («Не обработан» у ВСЕХ строк импорта) —
    не колонка бота, не пишется даже у кейса с завершённым визитом."""
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    rows = [bot_row("T-1", visit_date="2026-07-10", overall_result="Не обработан")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    cols = {c for _r, c, *_ in plan["writes"]}
    assert cols == {3}, "только дата визита; общий результат — системная колонка"


def test_equal_values_not_rewritten():
    grid = make_grid([
        ["T-1", "Иванов", "100", datetime(2026, 7, 10),
         None, None, "Оплачен", None],
    ])
    rows = [bot_row("T-1", visit_date="2026-07-10", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["cells_written"] == 0
    assert plan["cells_already"] == 2
    assert plan["changed_cases"] == 0


def test_overwrite_of_nonempty_is_counted():
    grid = make_grid([
        ["T-1", "Иванов", "100", None, None, None, "В работе", None],
    ])
    rows = [bot_row("T-1", visit_date="2026-07-10", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["cells_written"] == 2          # дата + результат
    assert plan["cells_over_nonempty"] == 1    # результат лёг поверх «В работе»


def test_missing_case_reported_not_silent():
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    rows = [bot_row("T-9", visit_date="2026-07-10", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["missing"] == ["T-9"]
    assert plan["cells_written"] == 0


def test_float_contract_id_matches():
    """Excel отдаёт номер КД как 12345.0 — матчится с «12345» сервера."""
    grid = make_grid([[12345.0, "Иванов", "100", None, None, None, None]])
    rows = [bot_row("12345", visit_date="2026-07-10", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["matched_cases"] == 1
    assert plan["cells_written"] == 2


def test_duplicate_rows_in_file_all_enriched():
    """Один КД дважды в файле (бывает после ручных правок) — пишем в обе строки."""
    grid = make_grid([
        ["T-1", "Иванов", "100", None, None, None, None],
        ["T-1", "Иванов", "100", None, None, None, None],
    ])
    rows = [bot_row("T-1", call_status="Оплачен", visit_date="2026-07-10")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert {(r, c) for r, c, *_ in plan["writes"]} == \
        {(3, 3), (3, 6), (4, 3), (4, 6)}


def test_visit_result_targets_overall_result_not_call_status():
    """Выездник выбирает результат ВЫЕЗДА; колл-центр этим потоком не трогаем."""
    grid = make_grid([
        ["T-1", "Иванов", "100", datetime(2026, 7, 10),
         "Родной статус колл-центра", None, None, None],
    ])
    rows = [bot_row("T-1", visit_date="2026-07-10", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["writes"] == [(3, 6, "Оплачен", False)]
    assert all(c != 4 for _r, c, _v, _d in plan["writes"])


def test_tips_append_to_extra_address_and_are_idempotent():
    """Новые номера не затирают исходный доп. адрес и не дублируются повторно."""
    initial = "старый дополнительный адрес\n+998 93 284 00 03"
    grid = make_grid([
        ["T-1", "Иванов", "100", datetime(2026, 7, 10),
         None, None, "В работе", initial],
    ])
    rows = [bot_row(
        "T-1", visit_date="2026-07-10", call_status="В работе",
        tips="932840003\nориентир: дом у школы",
    )]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["writes"] == [
        (3, 7, initial + "\nориентир: дом у школы", False),
    ]
    assert plan["cells_over_nonempty"] == 1

    grid[3][7] = plan["writes"][0][2]
    again = enrich.build_plan(grid, FIELDS, rows)
    assert again["cells_written"] == 0
    assert again["cells_already"] == 3  # дата, результат и наводки


def test_source_row_skipped_as_data():
    """Строка «сх/хард» под заголовками — не данные (и не кейс с cid='сх')."""
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    plan = enrich.build_plan(grid, FIELDS, [bot_row("T-1", visit_date="2026-07-10")])
    assert plan["data_start"] == 3


def test_absent_visit_column_is_reported():
    headers = HEADERS[:4]   # в файле нет колонок статуса и комментария звонка
    grid = [
        ["Инфо", None, None, "Выезд"],
        headers,
        ["сх", "сх", "сх", "хард"],
        ["T-1", "Иванов", "100", None],
    ]
    rows = [bot_row("T-1", visit_date="2026-07-10", call_status="Оплачен",
                    call_comment="взял трубку")]
    with pytest.raises(ValueError, match="обязательных колонок"):
        enrich.build_plan(grid, FIELDS, rows)


# --- группировка по сотрудникам (обогащение личных реестров) -------------------
def test_split_by_employee():
    rows = [
        bot_row("T-1", visit_date="2026-07-09") | {"responsible": "Иванов Иван"},
        srv_row("T-2") | {"responsible": "иванов  иван"},        # регистр/пробелы
        bot_row("T-3", visit_date="2026-07-10") | {"responsible": "Петров Пётр"},
        srv_row("T-4") | {"responsible": ""},                     # без ответственного
    ]
    by = enrich.split_by_employee(rows)
    assert set(by) == {"иванов иван", "петров пётр"}
    assert by["иванов иван"]["filled"] == 1                       # T-2 не заполнен ботом
    assert len(by["иванов иван"]["rows"]) == 2                    # но строки — обе его
    assert by["петров пётр"]["filled"] == 1


def test_is_filled_requires_provenance_and_valid_date():
    assert not enrich.is_filled(srv_row("T-1", client="Иванов", debt_total="99"))
    # статус/комментарий из старого импорта без завершённого визита — не маркер
    assert not enrich.is_filled(srv_row("T-1", call_comment="был на адресе",
                                        call_status="В работе"))
    assert not enrich.is_filled(srv_row("T-1", overall_result="Не обработан"))
    assert not enrich.is_filled(srv_row("T-1", visit_date="2026-07-10"))
    assert not enrich.is_filled(bot_row("T-1", visit_date="531"))
    assert enrich.is_filled(bot_row("T-1", visit_date="2026-07-10"))


# --- вспомогательное ----------------------------------------------------------
def test_dates_equivalence():
    assert enrich._values_equal(datetime(2026, 7, 1), "2026-07-01", True)
    assert enrich._values_equal("01.07.2026", "2026-07-01", True)
    assert not enrich._values_equal("02.07.2026", "2026-07-01", True)


def test_cell_text():
    assert enrich.cell_text(12345.0) == "12345"
    assert enrich.cell_text(None) == ""
    assert enrich.cell_text("  x ") == "x"
    assert enrich.cell_text(datetime(2026, 7, 1)) == "2026-07-01"


# --- комментарии: обогащение не удаляет и не затирает текст --------------------
def test_call_column_never_touched_even_when_it_matches_visit_comment():
    """Колонки звонков не принадлежат выездникам и всегда остаются как в файле."""
    grid = make_grid([["T-1", "Иванов", "100", None, None,
                       "приехал, говорил с братом", None]])
    rows = [bot_row("T-1", visit_date="2026-07-10",
                    visit_comment="приехал, говорил с братом", call_comment=None)]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["poison_cleared"] == 0
    assert all(c != 5 for _r, c, _v, _d in plan["writes"])
    assert (3, 8, "приехал, говорил с братом", False) in plan["writes"]


def test_visit_comment_is_merged_without_losing_manual_text_and_is_idempotent():
    """Ручной комментарий в файле сохраняется, серверный дописывается один раз."""
    grid = make_grid([["T-1", "Иванов", "100", None, None,
                       "11.12.2024 позвонил, не берёт", None, None,
                       "офис дописал после визита"]])
    rows = [bot_row("T-1", visit_date="2026-07-10",
                    visit_comment="приехал, говорил с братом", call_comment=None)]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["poison_cleared"] == 0
    assert all(c != 5 for _r, c, _v, _d in plan["writes"])
    merged = "офис дописал после визита\nприехал, говорил с братом"
    assert (3, 8, merged, False) in plan["writes"]

    grid[3][3] = date(2026, 7, 10)
    grid[3][8] = merged
    again = enrich.build_plan(grid, FIELDS, rows)
    assert again["cells_written"] == 0


def test_enrichment_never_plans_empty_writes():
    grid = make_grid([["T-1", "Иванов", "100", None, None,
                       "не трогать", None, None, None]])
    rows = [bot_row("T-1", visit_date="2026-07-10",
                    visit_comment="комментарий выезда")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert all(value not in (None, "") for _r, _c, value, _d in plan["writes"])


def test_unknown_merge_mode_never_falls_through_to_replace(monkeypatch):
    grid = make_grid([["T-1", "Иванов", "100", None, None, None, None]])
    rows = [bot_row("T-1", visit_date="2026-07-10")]
    monkeypatch.setitem(enrich.MERGE_MODES, "visit_date", "future-mode")
    with pytest.raises(ValueError, match="неподдерживаемый merge mode"):
        enrich.build_plan(grid, FIELDS, rows)


def test_published_master_proof_requires_complete_matching_registry_cycle(tmp_path):
    from openpyxl import Workbook, load_workbook

    master = tmp_path / "ОБЩИЙ РЕЕСТР.xlsx"
    grid = make_grid(
        [["T-1", "Иванов", "100", None, None, None, None, None, None]]
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = enrich.SHEET_NAME
    for row in grid:
        sheet.append(row)
    workbook.save(master)
    master_sha = hashlib.sha256(master.read_bytes()).hexdigest()
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "run-1"
    staging = run_dir / "staging"
    staging.mkdir(parents=True)
    plan_digest = "a" * 64
    baseline_path = tmp_path / "baseline.json.gz"
    with gzip.open(baseline_path, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "plan_digest": plan_digest,
                "master_path": str(master),
            },
            stream,
        )
    stage_path = staging / "stage.json"
    stage_path.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "plan_digest": plan_digest,
                "master": {
                    "original_path": str(master),
                    "staged_fingerprint": {"sha256": master_sha},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    publish_path = run_dir / "publish.json"
    publish_path.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "plan_digest": plan_digest,
                "complete": True,
                "ok": True,
                "sidecars": [],
                "finished_at": "2026-07-29T12:00:00",
                "baseline": str(baseline_path),
                "published": [str(master)],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    journal_path = run_dir / "publish.journal.jsonl"
    journal_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "publish_started",
                        "run_id": "run-1",
                        "plan_digest": plan_digest,
                    }
                ),
                json.dumps(
                    {
                        "event": "publish_committed",
                        "run_id": "run-1",
                        "plan_digest": plan_digest,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    prepared = enrich.prepare_published_master_import(
        master,
        workspace=workspace,
    )
    proof = prepared["source_proof"]
    assert proof["file_sha256"] == master_sha
    assert proof["publication"]["run_id"] == "run-1"
    assert prepared["sheets"][0]["name"] == enrich.SHEET_NAME

    # Чужая незавершённая публикация в workspace больше не отбивает импорт
    # (03.08.2026, решение владельца): проверка смотрела на ВЕСЬ workspace, и
    # одна зависшая репетиция месячной давности запрещала грузить мастер, чья
    # собственная публикация завершилась штатно. Публикация ВЫБРАННОГО файла
    # по-прежнему обязана быть complete с terminal receipt — это ниже.
    interrupted = workspace / "runs" / "run-interrupted"
    interrupted.mkdir()
    interrupted_journal = interrupted / "publish.journal.jsonl"
    interrupted_journal.write_text(
        json.dumps(
            {
                "event": "publish_started",
                "run_id": "run-interrupted",
                "plan_digest": "f" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    still_ok = enrich.prepare_published_master_import(
        master,
        workspace=workspace,
    )
    assert still_ok["source_proof"]["publication"]["run_id"] == "run-1"
    interrupted_journal.unlink()
    interrupted.rmdir()

    # A newer complete publication on a new monthly path makes the old path
    # stale too; "latest" is global, not merely latest for the selected name.
    # Содержимое РАЗНОЕ, иначе проверять нечего: с 03.08.2026 файл опознаётся по
    # байтам, а не по пути, и побайтовая копия опубликованного мастера — это и есть
    # тот же опубликованный мастер, где бы она ни лежала. Устаревшим файл делает
    # не путь, а содержимое, которое уже перекрыто более новой публикацией.
    newer = tmp_path / "ОБЩИЙ РЕЕСТР АВГУСТ.xlsx"
    newer_book = load_workbook(master)
    newer_book[enrich.SHEET_NAME].cell(row=4, column=2, value="Петров")
    newer_book.save(newer)
    newer_sha = hashlib.sha256(newer.read_bytes()).hexdigest()
    assert newer_sha != master_sha
    newer_run = workspace / "runs" / "run-2"
    newer_staging = newer_run / "staging"
    newer_staging.mkdir(parents=True)
    newer_plan = "b" * 64
    newer_stage = newer_staging / "stage.json"
    newer_stage.write_text(
        json.dumps(
            {
                "run_id": "run-2",
                "plan_digest": newer_plan,
                "master": {
                    "original_path": str(newer),
                    "staged_fingerprint": {"sha256": newer_sha},
                },
            }
        ),
        encoding="utf-8",
    )
    newer_baseline = tmp_path / "baseline-2.json.gz"
    with gzip.open(newer_baseline, "wt", encoding="utf-8") as stream:
        json.dump(
            {"plan_digest": newer_plan, "master_path": str(newer)},
            stream,
        )
    (newer_run / "publish.json").write_text(
        json.dumps(
            {
                "run_id": "run-2",
                "plan_digest": newer_plan,
                "complete": True,
                "ok": True,
                "sidecars": [],
                "finished_at": "2026-07-29T12:01:00",
                "baseline": str(newer_baseline),
                "published": [str(newer)],
            }
        ),
        encoding="utf-8",
    )
    (newer_run / "publish.journal.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "event": event,
                    "run_id": "run-2",
                    "plan_digest": newer_plan,
                }
            )
            for event in ("publish_started", "publish_committed")
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="не совпадает с новейшей"):
        enrich.prepare_published_master_import(master, workspace=workspace)
    newest = enrich.prepare_published_master_import(newer, workspace=workspace)
    assert newest["source_proof"]["publication"]["run_id"] == "run-2"

    changed = load_workbook(newer)
    changed[enrich.SHEET_NAME].cell(row=4, column=2, value="third-state")
    changed.save(newer)
    with pytest.raises(ValueError, match="не совпадает с новейшей"):
        enrich.prepare_published_master_import(
            newer,
            workspace=workspace,
        )


def test_machine_evidence_run_uses_success_path_and_emits_signed_receipt(
    monkeypatch,
    tmp_path,
):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    row = bot_row(
        "T-1",
        visit_date="2026-07-10",
        responsible="Employee",
    )
    safe_path = snapshot / "safe-enrichment.json.gz"
    raw_path = snapshot / "raw-diagnostic.json.gz"
    schema_path = snapshot / "export-schema.json"
    with gzip.open(safe_path, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "kind": "safe_enrichment",
                "data_exchange_contract": enrich.DATA_EXCHANGE_CONTRACT,
                "rows": [row],
            },
            stream,
        )
    with gzip.open(raw_path, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "kind": "raw_diagnostic",
                "data_exchange_contract": enrich.DATA_EXCHANGE_CONTRACT,
                "rows": [row],
            },
            stream,
        )
    schema_path.write_text(json.dumps(db.export_schema()), encoding="utf-8")
    artifacts = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (safe_path, raw_path, schema_path)
    }
    manifest_path = snapshot / "snapshot_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "kind": enrich.SNAPSHOT_KIND,
                "data_exchange_contract": enrich.DATA_EXCHANGE_CONTRACT,
                "counts": {"clients": 1},
                "database_state_digest": "a" * 64,
                "artifacts": artifacts,
            }
        ),
        encoding="utf-8",
    )
    binding = {
        "manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "database_state_digest": "a" * 64,
        "safe_export_sha256": artifacts[safe_path.name],
        "raw_export_sha256": artifacts[raw_path.name],
        "data_exchange_contract_sha256": enrich.DATA_EXCHANGE_CONTRACT[
            "sha256"
        ],
    }
    personal = tmp_path / "personal.xlsx"
    personal.write_bytes(b"fixture")
    file_map = snapshot / "file-map.json"
    file_map.write_text(
        json.dumps(
            {
                "schema": enrich.FILE_MAP_SCHEMA,
                "snapshot": binding,
                "owners": [
                    {
                        "responsible": "Employee",
                        "contract_ids": ["T-1"],
                        "files": [str(personal)],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    file_sha = hashlib.sha256(personal.read_bytes()).hexdigest()
    plan = {
        "employee": "Employee",
        "path": str(personal.resolve()),
        "pre_sha256": file_sha,
        "post_sha256": file_sha,
        "backup_path": None,
        "backup_sha256": None,
        "filled_cases": 1,
        "filled_contract_ids": ["T-1"],
        "matched_cases": 1,
        "matched_contract_ids": ["T-1"],
        "changed_cases": 0,
        "cells_written": 0,
        "errors": [],
        "visit_cols_absent": [],
        "missing": [],
        "post_plan": {
            "filled_cases": 1,
            "filled_contract_ids": ["T-1"],
            "matched_cases": 1,
            "matched_contract_ids": ["T-1"],
            "changed_cases": 0,
            "cells_written": 0,
            "errors": [],
            "visit_cols_absent": [],
            "missing_contract_ids": [],
        },
    }
    monkeypatch.setattr(
        enrich,
        "enrich_many",
        lambda *_args, **_kwargs: [plan],
    )

    output = snapshot / "pult-enrichment-receipt.json"
    receipt = enrich.evidence_run(
        snapshot_dir=snapshot,
        file_map=file_map,
        output=output,
    )

    assert receipt["files"][0]["path"] == str(personal.resolve())
    assert receipt["receipt_digest"] == hashlib.sha256(
        enrich._canonical(
            {
                key: value
                for key, value in receipt.items()
                if key != "receipt_digest"
            }
        )
    ).hexdigest()
    assert output.is_file()

    def fail_if_writer_restarts(*_args, **_kwargs):
        raise AssertionError("writer must not run when receipt already exists")

    monkeypatch.setattr(enrich, "enrich_many", fail_if_writer_restarts)
    try:
        enrich.evidence_run(
            snapshot_dir=snapshot,
            file_map=file_map,
            output=output,
        )
    except RuntimeError as exc:
        assert "уже существует" in str(exc)
    else:
        raise AssertionError("existing receipt must block a second evidence run")


def test_machine_evidence_run_accepts_zero_completed_without_starting_excel(
    monkeypatch,
    tmp_path,
):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    safe_path = snapshot / "safe-enrichment.json.gz"
    raw_path = snapshot / "raw-diagnostic.json.gz"
    schema_path = snapshot / "export-schema.json"
    with gzip.open(safe_path, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "kind": "safe_enrichment",
                "data_exchange_contract": enrich.DATA_EXCHANGE_CONTRACT,
                "rows": [],
            },
            stream,
        )
    with gzip.open(raw_path, "wt", encoding="utf-8") as stream:
        json.dump(
            {
                "kind": "raw_diagnostic",
                "data_exchange_contract": enrich.DATA_EXCHANGE_CONTRACT,
                "rows": [],
            },
            stream,
        )
    schema_path.write_text(json.dumps(db.export_schema()), encoding="utf-8")
    artifacts = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (safe_path, raw_path, schema_path)
    }
    manifest_path = snapshot / "snapshot_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "kind": enrich.SNAPSHOT_KIND,
                "data_exchange_contract": enrich.DATA_EXCHANGE_CONTRACT,
                "counts": {"clients": 0},
                "database_state_digest": "b" * 64,
                "artifacts": artifacts,
            }
        ),
        encoding="utf-8",
    )
    binding = {
        "manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "database_state_digest": "b" * 64,
        "safe_export_sha256": artifacts[safe_path.name],
        "raw_export_sha256": artifacts[raw_path.name],
        "data_exchange_contract_sha256": enrich.DATA_EXCHANGE_CONTRACT[
            "sha256"
        ],
    }
    file_map = snapshot / "file-map.json"
    file_map.write_text(
        json.dumps(
            {
                "schema": enrich.FILE_MAP_SCHEMA,
                "snapshot": binding,
                "owners": [],
            }
        ),
        encoding="utf-8",
    )

    def fail_if_excel_starts(*_args, **_kwargs):
        raise AssertionError("Excel must not start for an empty completed set")

    monkeypatch.setattr(enrich, "enrich_many", fail_if_excel_starts)
    output = snapshot / "pult-enrichment-receipt.json"

    receipt = enrich.evidence_run(
        snapshot_dir=snapshot,
        file_map=file_map,
        output=output,
    )

    assert receipt["completed_contract_ids"] == []
    assert receipt["owners"] == []
    assert receipt["expected_files"] == []
    assert receipt["files"] == []
    assert output.is_file()
