from datetime import date, datetime
import json

import pytest

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


def test_legacy_daily_cycle_write_is_fail_closed(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(daily_cycle.LEGACY_WRITE_ENV, raising=False)
    state = tmp_path / "state"
    monkeypatch.setattr(daily_cycle, "STATE_DIR", state)
    monkeypatch.setattr(daily_cycle, "LOCK_PATH", state / "cycle.lock")
    monkeypatch.setattr(
        daily_cycle,
        "load_settings",
        lambda: pytest.fail("guard должен сработать до чтения настроек"),
    )

    code = daily_cycle.main(["export"])

    assert code == daily_cycle.LEGACY_WRITE_BLOCKED_EXIT
    assert not state.exists()
    assert daily_cycle.LEGACY_WRITE_ENV in capsys.readouterr().err


def test_legacy_daily_cycle_direct_steps_are_guarded(monkeypatch):
    monkeypatch.delenv(daily_cycle.LEGACY_WRITE_ENV, raising=False)

    with pytest.raises(daily_cycle.LegacyWriteBlocked):
        daily_cycle.step_export({}, None, False, lambda _message: None)
    with pytest.raises(daily_cycle.LegacyWriteBlocked):
        daily_cycle.step_import({}, None, False, lambda _message: None)


def test_daily_cycle_dry_run_remains_available(monkeypatch, tmp_path):
    monkeypatch.delenv(daily_cycle.LEGACY_WRITE_ENV, raising=False)
    state = tmp_path / "state"
    monkeypatch.setattr(daily_cycle, "STATE_DIR", state)
    monkeypatch.setattr(daily_cycle, "LOCK_PATH", state / "cycle.lock")
    monkeypatch.setattr(
        daily_cycle,
        "load_settings",
        lambda: {"url": "https://invalid.test", "scatterBase": str(tmp_path)},
    )
    called = []

    def fake_export(_settings, _base, dry, _log):
        called.append(dry)
        return "dry-run"

    monkeypatch.setattr(daily_cycle, "step_export", fake_export)

    assert daily_cycle.main(["export", "--dry-run", "--no-report"]) == 0
    assert called == [True]


def test_daily_cycle_emergency_flag_requires_exact_one(monkeypatch):
    for value in ("", "0", "true", "yes", " 1"):
        monkeypatch.setenv(daily_cycle.LEGACY_WRITE_ENV, value)
        assert not daily_cycle.legacy_write_enabled()
    monkeypatch.setenv(daily_cycle.LEGACY_WRITE_ENV, "1")
    assert daily_cycle.legacy_write_enabled()
