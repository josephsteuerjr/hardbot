"""Скрипт атомарного бампа контракта v2→v3 (_local/apply_contract_v3.py).

Бамп применяется на КОПИИ файлов: результат обязан пройти валидатор контракта
(двусоставная форма) и не сдвинуть export_schema_sha256 — офисный шаблон
прежний. Сам боевой файл остаётся v2 до выкатки волны 2.

После настоящего бампа (живой контракт уже v3) модуль ПРОПУСКАЕТСЯ: переход
проверять больше не из чего, а его результат с этого момента сторожат
проснувшийся tests/test_promise_v3.py и валидатор при каждом импорте db.
Зеркальная пара: test_promise_v3 спит на v2, этот файл — на v3.
"""
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    json.loads((ROOT / "hardbot" / "data_exchange_contract.json")
               .read_text(encoding="utf-8"))["version"] != 2,
    reason="живой контракт уже не v2 — переход был проверен до бампа")

_BUMP = ROOT / "_local" / "apply_contract_v3.py"
if not _BUMP.is_file():
    # owner-скрипт лежит в _local/ — его нет ни в пакете для ИТ, ни в чистом клоне
    pytest.skip("нет _local/apply_contract_v3.py", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("apply_contract_v3", _BUMP)
bumpmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bumpmod)


def _copies(tmp_path):
    c = tmp_path / "data_exchange_contract.json"
    m = tmp_path / "exchange_contract.py"
    shutil.copy(ROOT / "hardbot" / "data_exchange_contract.json", c)
    shutil.copy(ROOT / "hardbot" / "exchange_contract.py", m)
    return c, m


def test_dry_run_touches_nothing(tmp_path):
    c, m = _copies(tmp_path)
    before = c.read_bytes(), m.read_bytes()
    res = bumpmod.bump(c, m, apply=False)
    assert res["version"] == 3 and len(res["sha256"]) == 64
    assert (c.read_bytes(), m.read_bytes()) == before


def test_apply_produces_a_valid_v3_contract(tmp_path):
    c, m = _copies(tmp_path)
    res = bumpmod.bump(c, m, apply=True)
    raw = c.read_text(encoding="utf-8")
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == res["sha256"]
    data = json.loads(raw)
    assert data["version"] == 3
    assert data["form"]["protocol_version"] == 2      # рамка владельца
    assert data["form"]["amount_keys"] == ["forecast_sum"]
    assert data["form"]["date_keys"] == ["promise_date"]
    # Все три ключа обещания проходят весь путь: снимок → обогащение → правка
    # офиса. «Прогноз» при этом НЕ поле формы: его выводит сервер.
    keys = ["promise_date", "forecast_sum", "forecast"]
    assert "forecast" not in (data["form"]["amount_keys"]
                              + data["form"]["date_keys"]
                              + data["form"]["dropdown_keys"]
                              + data["form"]["text_keys"])
    snap = data["provenance"]["snapshot_keys"]
    assert snap[-3:] == keys
    src = data["provenance"]["enrichment_source_keys"]
    assert src[-3:] == keys
    writes = data["enrichment"]["writes"]
    assert writes[-3:] == [{"source": k, "target": k, "merge": "replace"}
                           for k in keys]
    # порядок writes == порядок enrichment_source_keys (инвариант валидатора)
    assert [w["source"] for w in writes] == list(src)
    de = data["office_overlay"]["direct_edit_keys"]
    assert de[-3:] == keys
    # офисный шаблон не тронут
    import db
    assert data["workbook_shape"]["export_schema_sha256"] == \
        json.loads((ROOT / "hardbot" / "data_exchange_contract.json")
                   .read_text(encoding="utf-8"))["workbook_shape"]["export_schema_sha256"]
    # sha вписан в модуль
    assert f'3: "{res["sha256"]}"' in m.read_text(encoding="utf-8")


def test_apply_is_idempotent(tmp_path):
    c, m = _copies(tmp_path)
    bumpmod.bump(c, m, apply=True)
    res2 = bumpmod.bump(c, m, apply=True)
    assert res2["already"] is True


def test_bumped_contract_passes_the_validator(tmp_path, monkeypatch):
    """Пропускаем результат через НАСТОЯЩИЙ валидатор: свежая копия модуля
    exchange_contract читает бампнутый JSON и обязана загрузиться."""
    c, m = _copies(tmp_path)
    bumpmod.bump(c, m, apply=True)
    spec = importlib.util.spec_from_file_location("exch_v3_probe", m)
    probe = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec.loader.exec_module(probe)          # CONTRACT_PATH = рядом с модулем
    assert probe.CONTRACT["version"] == 3
    assert probe.FORM_AMOUNT_KEYS == ("forecast_sum",)
    assert probe.FORM_DATE_KEYS == ("promise_date",)
    assert probe.FORM_PROTOCOL_VERSION == 2
    assert {"promise_date", "forecast_sum", "forecast"} <= set(
        probe.OFFICE_DIRECT_EDIT_KEYS)
