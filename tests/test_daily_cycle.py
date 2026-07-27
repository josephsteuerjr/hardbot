from datetime import date, datetime
import json

from pult import daily_cycle


def test_cell_json_makes_calamine_dates_serializable():
    values = [
        daily_cycle._cell_json(date(2026, 7, 27)),
        daily_cycle._cell_json(datetime(2026, 7, 27, 0, 0, 0)),
        daily_cycle._cell_json(datetime(2026, 7, 27, 6, 45, 12)),
    ]

    assert values == ["2026-07-27", "2026-07-27", "2026-07-27 06:45:12"]
    assert json.loads(json.dumps(values)) == values


def test_cell_json_matches_import_contract_for_scalars():
    assert daily_cycle._cell_json("   ") is None
    assert daily_cycle._cell_json("  КД-1  ") == "КД-1"
    assert daily_cycle._cell_json(42.0) == 42
    assert daily_cycle._cell_json(42.5) == 42.5


def test_common_registry_filter_keeps_only_existing_contracts():
    sheets = [
        {"name": "Портфели", "grid": [
            ["Блок", None],
            ["Номер КД / ID", "Клиент"],
            ["сх", "сх"],
            [1001.0, "Старый"],
            ["NEW-2", "Новый"],
            [None, "без ключа"],
            ["KEEP-3", "Старый 2"],
        ]},
        {"name": "Статусы", "grid": [["Статус"], ["В работе"]]},
    ]

    filtered, report = daily_cycle.filter_to_existing_contracts(
        sheets, {"1001", "KEEP-3"})

    assert report == {"kept": 2, "skipped_new": 1}
    assert filtered[0]["grid"] == [
        ["Блок", None],
        ["Номер КД / ID", "Клиент"],
        ["сх", "сх"],
        [1001.0, "Старый"],
        ["KEEP-3", "Старый 2"],
    ]
    assert filtered[1] == sheets[1]
