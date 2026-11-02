"""Окно дат, журнал вытесненного, копии в отдельном корне и смерть Excel.

05.08.2026. Разбор прогона 04.08 показал пять дыр, и все они об одном: писатель
считал базу единственным авторитетом над колонками, которые правит и офис, а
следов от себя не оставлял.

1. Не было фильтра по дате — каждый прогон переутверждал базу поверх ВСЕЙ
   истории визитов. Так в книгу Уткирова вернулись значения бота поверх
   офисных правок за 03.08, сделанных уже после предыдущего обогащения.
2. Вытесненное значение не сохранялось нигде: чтобы узнать, что затёрли,
   пришлось бы сравнивать книгу с резервной копией целиком.
3. Резервные копии ложились рядом с книгой. Сорить в папках сотрудников
   запрещено, а перенос копий руками ПОСЛЕ прогона ослеплял инструменты
   восстановления — они ищут копии только рядом с живым файлом.
4. Имя копии не содержало года, и он достраивался из времени файла, которое
   сбрасывается при переносе на другой том.
5. Смерть Excel на выходе из сессии уносила весь результат прогона, включая
   пути копий; сохранённая книга становилась неотличима от необработанной.
"""

import json
import sys

import pytest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import enrich  # noqa: E402
import repair_false_enrichment  # noqa: E402


FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID"},
    {"key": "visit_date", "title": "Дата выезда", "date": True},
    {"key": "call_status", "title": "Статус звонка"},
    {"key": "overall_result", "title": "Общий результат"},
    {"key": "visit_comment", "title": "Комментарий текущего месяца выезда"},
    {"key": "extra_address", "title": "Доп. Адрес/Доп.Номер"},
    {"key": "contact_with", "title": "С кем был контакт?"},
    {"key": "agreed_payment", "title": "Договорились на оплату?"},
    {"key": "who_at_address", "title": "Кто был на адресе?"},
    {"key": "client_location", "title": "Местоположение клиента"},
    {"key": "purpose_rf", "title": "Цель приезда в РФ"},
]
TITLES = [f["title"] for f in FIELDS]


def _grid(rows):
    """Лист книги: шапка + строки {заголовок: значение}."""
    out = [TITLES]
    for r in rows:
        out.append([r.get(t, None) for t in TITLES])
    return out


def _srv(cid, visit_date, **vals):
    row = {"contract_id": cid, "visit_date": visit_date,
           enrich.FILLED_MARKER: True}
    row.update(vals)
    return row


# --- 1. Окно дат ------------------------------------------------------------

def test_without_a_window_the_whole_history_is_reasserted():
    """Поведение по умолчанию не изменилось: без окна берём всё."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Доп. Обогащение"},
                  {"Номер КД / ID": "B", "Общий результат": "В работе"}])
    rows = [_srv("A", "2026-08-03", call_status="Оплачен"),
            _srv("B", "2026-08-04", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["filled_cases"] == 2
    assert plan["out_of_window"] == 0


def test_window_keeps_older_visits_out_of_the_plan():
    """Ровно тот случай, что был 04.08: старое не переутверждается."""
    # У «A» за 03.08 в книге лежит офисная правка, у «B» за 04.08 колонка пуста.
    # Значит любое вытеснение в плане может прийти только от «A».
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Доп. Обогащение"},
                  {"Номер КД / ID": "B"}])
    rows = [_srv("A", "2026-08-03", call_status="Оплачен"),
            _srv("B", "2026-08-04", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows, since="2026-08-04")
    assert plan["filled_cases"] == 1
    assert plan["out_of_window"] == 1
    assert plan["displaced"] == [], "правку офиса за 03.08 всё-таки затёрли"
    assert plan["window"]["since"] == "2026-08-04"

    # Контроль: без окна та же правка вытесняется — значит окно и есть причина.
    wide = enrich.build_plan(grid, FIELDS, rows)
    assert [d["contract_id"] for d in wide["displaced"]] == ["A"]


def test_window_borders_are_inclusive():
    grid = _grid([{"Номер КД / ID": "A"}])
    rows = [_srv("A", "2026-08-04", call_status="Оплачен")]
    for since, until in (("2026-08-04", "2026-08-04"),
                         ("2026-08-01", "2026-08-04"),
                         ("2026-08-04", "2026-08-31")):
        plan = enrich.build_plan(grid, FIELDS, rows, since, until)
        assert plan["filled_cases"] == 1, f"граница {since}..{until} исключила визит"


def test_window_accepts_the_russian_date_the_operator_types():
    grid = _grid([{"Номер КД / ID": "A"}])
    rows = [_srv("A", "2026-08-04", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows, since="04.08.2026")
    assert plan["filled_cases"] == 1


def test_a_typo_in_the_date_stops_the_run_instead_of_disabling_the_window():
    """Иначе опечатка молча снимала бы единственный новый предохранитель.

    Неразобранная граница возвращала None, а None означает «открытая сторона».
    Человек думает, что обогащает за сегодня, а переутверждается вся история.
    """
    for bad in ("вчера", "32.13.2026", "04-08-2026x", "0408"):
        with pytest.raises(ValueError, match="не понимаю дату"):
            enrich.visit_window(bad, "")
        with pytest.raises(ValueError, match="не понимаю дату"):
            enrich.visit_window("", bad)


def test_reversed_window_is_refused():
    with pytest.raises(ValueError, match="позже конца"):
        enrich.visit_window("05.08.2026", "04.08.2026")


def test_empty_borders_stay_open():
    assert enrich.visit_window("", "") == (None, None)
    assert enrich.visit_window(None, None) == (None, None)


# --- 2. Журнал вытесненного -------------------------------------------------

def test_displaced_value_is_recorded_with_the_old_text():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Доп. Обогащение"}])
    rows = [_srv("A", "2026-08-03", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert len(plan["displaced"]) == 1
    d = plan["displaced"][0]
    assert d["contract_id"] == "A"
    assert d["column"] == "Общий результат"
    assert d["was"] == "Доп. Обогащение"
    assert d["now"] == "Оплачен"
    assert d["visit_date"] == "2026-08-03"


def test_writing_into_an_empty_cell_is_not_displacement():
    grid = _grid([{"Номер КД / ID": "A"}])
    rows = [_srv("A", "2026-08-03", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["cells_written"] >= 1
    assert plan["displaced"] == []


def test_technical_not_processed_is_not_displacement():
    """«Не обработан» — состояние по умолчанию, а не чьё-то решение.

    Сверка книг после прогона 04.08.2026: из 595 свежих записей 84 легли поверх
    непустого, и почти все — поверх «Не обработан». Считать их вытеснением
    значит топить настоящие потери (их было 28) в десятках пустых тревог.
    """
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Не обработан"}])
    rows = [_srv("A", "2026-08-03", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["cells_written"] >= 1, "запись вообще не произошла"
    assert plan["displaced"] == [], "«Не обработан» записан как вытеснение"
    assert plan["technical_overwrites"] == 1
    assert plan["cells_over_nonempty"] == 1, "механический счётчик должен остаться честным"


def test_case_and_spaces_do_not_hide_technical_emptiness():
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "  НЕ ОБРАБОТАН "}])
    rows = [_srv("A", "2026-08-03", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["displaced"] == []
    assert plan["technical_overwrites"] == 1


def test_a_real_office_value_is_still_displacement():
    """Контроль к двум тестам выше: настоящую правку они не должны прятать."""
    grid = _grid([{"Номер КД / ID": "A", "Общий результат": "Доп. Обогащение"}])
    rows = [_srv("A", "2026-08-03", call_status="Оплачен")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert [d["was"] for d in plan["displaced"]] == ["Доп. Обогащение"]
    assert plan["technical_overwrites"] == 0


def test_merged_columns_are_never_reported_as_displacement():
    """Слияние сохраняет старый текст внутри нового — это не потеря.

    Иначе журнал наполнился бы каждым дописанным комментарием, и настоящее
    вытеснение утонуло бы в шуме.
    """
    grid = _grid([{"Номер КД / ID": "A",
                   "Комментарий текущего месяца выезда": "старый текст",
                   "Доп. Адрес/Доп.Номер": "ул. Прежняя 1"}])
    rows = [_srv("A", "2026-08-03", visit_comment="новый текст",
                 tips="ул. Новая 2")]
    plan = enrich.build_plan(grid, FIELDS, rows)
    assert plan["cells_written"] >= 1, "слияние вообще не сработало"
    assert plan["displaced"] == [], "слияние записано как вытеснение"


# --- 3-4. Копии: отдельный корень и год в имени -----------------------------

def _book(tmp_path: Path) -> Path:
    book = tmp_path / "Узбекистан" / "Самарканд" / "Иванов Иван" / "Иванов Иван.xlsb"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"workbook")
    return book


def test_backup_goes_next_to_the_book_when_no_root_given(tmp_path):
    book = _book(tmp_path)
    dst = enrich.backup_copy(book)
    assert dst.parent == book.parent
    assert dst.read_bytes() == b"workbook"


def test_backup_root_mirrors_the_employee_folders(tmp_path):
    """Сваливать семь книг в одну папку нельзя — одноимённые столкнутся."""
    book = _book(tmp_path)
    root = tmp_path / "!! Обогащение из БД"
    dst = enrich.backup_copy(book, root)
    assert dst.parent == root / "Самарканд" / "Иванов Иван"
    assert dst.read_bytes() == b"workbook"
    assert not list(book.parent.glob("*до обогащения*")), \
        "копия всё-таки осталась в папке сотрудника"


def test_backup_name_carries_the_year(tmp_path):
    book = _book(tmp_path)
    dst = enrich.backup_copy(book)
    assert str(date.today().year) in dst.stem, "год снова не попал в имя копии"


def test_repair_tool_still_parses_both_old_and_new_backup_names(tmp_path):
    """Старые копии без года обязаны читаться как раньше."""
    book = _book(tmp_path)
    old = book.with_name(f"{book.stem} (до обогащения 03.08 21-23){book.suffix}")
    old.write_bytes(b"old")
    new = book.with_name(f"{book.stem} (до обогащения 04.08.2026 13-38){book.suffix}")
    new.write_bytes(b"new")
    found = repair_false_enrichment.discover_backups(book)
    assert {p["path"].name for p in found} == {old.name, new.name}
    stamps = {p["path"].name: p["stamp"] for p in found}
    assert stamps[new.name] == datetime(2026, 8, 4, 13, 38), \
        "год из имени не использован"


def test_repair_tool_finds_a_backup_moved_to_the_root(tmp_path):
    """Главная поломка переноса: копии переставали находиться вовсе."""
    book = _book(tmp_path)
    root = tmp_path / "!! Обогащение из БД"
    moved = enrich.backup_copy(book, root)
    assert repair_false_enrichment.discover_backups(book) == []
    found = repair_false_enrichment.discover_backups(book, roots=[root])
    assert [p["path"] for p in found] == [moved]


# --- 5. Квитанция -----------------------------------------------------------

def test_receipt_records_backups_hashes_window_and_displaced(tmp_path):
    results = [
        {"employee": "Иванов Иван", "file": "X.xlsb", "backup_path": "B.xlsb",
         "backup_sha256": "aa", "pre_sha256": "bb", "post_sha256": "cc",
         "cells_written": 3, "cells_over_nonempty": 1,
         "displaced": [{"contract_id": "A", "column": "Общий результат",
                        "was": "Доп. Обогащение", "now": "Оплачен",
                        "visit_date": "2026-08-03", "key": "call_status",
                        "row": 5}]},
        {"employee": "Петров Пётр", "file": "Y.xlsb", "error": "занят"},
    ]
    out = enrich.write_batch_receipt(results, tmp_path,
                                    since="2026-08-04", until="")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["window"]["since"] == "2026-08-04"
    assert len(data["files"]) == 2
    assert data["files"][0]["backup_sha256"] == "aa"
    assert data["displaced"][0]["employee"] == "Иванов Иван"
    assert data["displaced"][0]["was"] == "Доп. Обогащение"


def test_receipt_is_written_atomically(tmp_path):
    enrich.write_batch_receipt([], tmp_path)
    assert not list(tmp_path.glob("*.tmp")), "временный файл квитанции остался"


# --- 5. Смерть Excel --------------------------------------------------------

def test_written_but_unverified_is_a_separate_kind_of_failure():
    """Сохранённую книгу нельзя докладывать как необработанную.

    Разница практическая: необработанную прогоняют заново, а эту НЕ откатывают
    по копии — иначе потеряется уже записанная верная работа.
    """
    src = Path(enrich.__file__).read_text(encoding="utf-8")
    assert "class WrittenButUnverified" in src
    assert "except WrittenButUnverified as e:" in src
    i = src.index("def enrich_many(")
    body = src[i:src.index("def write_batch_receipt(")]
    assert '"written_unverified": True' in body
    assert "не откатывать" in body.lower()


def test_unverified_book_is_not_counted_as_success():
    """Без ключа error отчёт считал её успехом и показывал зелёное «7 из 7»."""
    src = Path(enrich.__file__).read_text(encoding="utf-8")
    i = src.index("except WrittenButUnverified as e:")
    body = src[i:i + 500]
    assert '"error": e.info.get("reason")' in body


def test_not_attempted_is_tracked_by_index_not_by_path_string():
    """Сравнение строк путей давало дубль: результат несёт resolve(), задание —
    сырой путь из настроек. На сетевом диске они расходятся, и успешная книга
    попадала в отчёт вторым разом как «не дошла очередь»."""
    src = Path(enrich.__file__).read_text(encoding="utf-8")
    i = src.index("def enrich_many(")
    body = src[i:src.index("def write_batch_receipt(")]
    assert "attempted: set[int] = set()" in body
    assert "attempted.add(i)" in body
    assert "if i not in attempted:" in body
    assert 'done = {r.get("file")' not in body, "снова сверяем строки путей"


def test_receipt_failure_reaches_the_final_report():
    """Единственным следом провала был log(), который отчёт сразу перезаписывал."""
    src = Path(enrich.__file__).read_text(encoding="utf-8")
    i = src.index("def enrich_many(")
    body = src[i:src.index("def write_batch_receipt(")]
    assert 'r["receipt_error"] = str(e)' in body
    assert 'r["receipt"] = str(path)' in body
    report = (Path(enrich.__file__).parent / "core" / "enrichment.py").read_text(
        encoding="utf-8")
    assert "КВИТАНЦИЯ НЕ ЗАПИСАНА" in report


def test_receipt_records_the_applied_window_not_the_typed_text(tmp_path):
    out = enrich.write_batch_receipt([], tmp_path, since="04.08.2026", until="")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["window"]["since"] == "2026-08-04", "записан сырой ввод, не окно"
    assert data["window_input"]["since"] == "04.08.2026"


def test_batch_result_survives_a_dead_excel_session(monkeypatch):
    """Результат собирается СНАРУЖИ блока Excel.

    Раньше он жил внутри `with open_excel(...)`, и падение на выходе из сессии
    уносило его целиком — вместе с путями резервных копий.
    """
    from contextlib import contextmanager
    from types import SimpleNamespace
    @contextmanager
    def dead_at_exit(*args):
        yield SimpleNamespace(display_alerts=True,screen_updating=True)
        raise RuntimeError('Synthetic Excel died during cleanup')
    monkeypatch.setattr(enrich,'open_excel',dead_at_exit)
    monkeypatch.setattr(enrich,'_co_init',lambda:None)
    monkeypatch.setattr(enrich,'_enrich_local',lambda *a,**k:{'file':'synthetic.xlsx','cells_written':1,'backup_path':'known-backup.xlsx','post_sha256':'verified-copy'})
    result=enrich.enrich_many([{'path':'synthetic.xlsx','employee':'Synthetic','rows':[]}],[])
    assert len(result)==1 and result[0]['cells_written']==1
    assert result[0]['backup_path']=='known-backup.xlsx' and result[0]['post_sha256']=='verified-copy'
    assert 'Synthetic Excel died during cleanup' in result[0]['session_error']
