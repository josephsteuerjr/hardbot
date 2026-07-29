"""Ограниченный тест: Tk-пульт не имеет права писать в личные реестры."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PULT_DIR = ROOT / "pult"
sys.path.insert(0, str(PULT_DIR))

SPEC = importlib.util.spec_from_file_location(
    "hardbot_limited_test_pult",
    PULT_DIR / "pult.py",
)
assert SPEC is not None and SPEC.loader is not None
pult_ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pult_ui)


class _Label:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, **kwargs) -> None:
        self.text = kwargs.get("text", self.text)


class _GuardProbe:
    """Ровно то, что разрешено тронуть до раннего fail-closed return."""

    def __init__(self) -> None:
        self.enr_warn = _Label()
        self.toasts: list[tuple[str, bool]] = []

    def toast(self, message: str, *, err: bool = False) -> None:
        self.toasts.append((message, err))

    def __getattr__(self, name: str):
        raise AssertionError(f"write path continued past limited-test guard: {name}")


class _Pairs:
    def __init__(self) -> None:
        self.value = None

    def set_pairs(self, value) -> None:
        self.value = value


class _ImportGuardProbe:
    def __init__(self) -> None:
        self.imp_kvs = _Pairs()
        self.imp_warn = _Label()
        self.toasts: list[tuple[str, bool]] = []

    def toast(self, message: str, *, err: bool = False) -> None:
        self.toasts.append((message, err))

    def __getattr__(self, name: str):
        raise AssertionError(f"master import continued past limited-test guard: {name}")


class _ResetGuardProbe:
    def __init__(self) -> None:
        self.reset_out = _Label()
        self.toasts: list[tuple[str, bool]] = []

    def toast(self, message: str, *, err: bool = False) -> None:
        self.toasts.append((message, err))

    def __getattr__(self, name: str):
        raise AssertionError(f"reset continued past limited-test guard: {name}")


def _must_not_run(*_args, **_kwargs):
    raise AssertionError("legacy enrichment writer was called")


def _assert_guard_message(probe: _GuardProbe) -> None:
    expected = pult_ui.LIMITED_TEST_ENRICH_DISABLED_MESSAGE
    assert probe.enr_warn.text == expected
    assert probe.toasts == [(expected, True)]
    assert "evidence-run" in expected
    assert "ночной registry_cycle" in expected


def test_folder_enrichment_cannot_call_enrich_many(monkeypatch):
    assert pult_ui.LIMITED_TEST_ENRICH_WRITES_ENABLED is False
    monkeypatch.setattr(pult_ui.enrich, "enrich_many", _must_not_run)
    monkeypatch.setattr(pult_ui.messagebox, "askyesno", _must_not_run)
    probe = _GuardProbe()

    pult_ui.Pult.enr_apply(probe, "all")

    _assert_guard_message(probe)


def test_single_file_enrichment_cannot_call_enrich_file(monkeypatch):
    assert pult_ui.LIMITED_TEST_ENRICH_WRITES_ENABLED is False
    monkeypatch.setattr(pult_ui.enrich, "enrich_file", _must_not_run)
    monkeypatch.setattr(pult_ui.filedialog, "askopenfilename", _must_not_run)
    monkeypatch.setattr(pult_ui.messagebox, "askyesno", _must_not_run)
    probe = _GuardProbe()

    pult_ui.Pult.do_enrich(probe)

    _assert_guard_message(probe)


def test_tk_master_import_stops_before_file_picker_and_api(monkeypatch):
    assert pult_ui.LIMITED_TEST_MASTER_IMPORT_UI_ENABLED is False
    monkeypatch.setattr(pult_ui.filedialog, "askopenfilename", _must_not_run)
    monkeypatch.setattr(
        pult_ui.enrich,
        "prepare_published_master_import",
        _must_not_run,
    )
    probe = _ImportGuardProbe()

    pult_ui.Pult.do_import(probe)

    expected = pult_ui.LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE
    assert probe.imp_kvs.value == []
    assert probe.imp_warn.text == expected
    assert probe.toasts == [(expected, True)]
    assert "prepare-published-master" in expected
    assert "register" in expected
    assert "apply" in expected
    assert "Ночной registry_cycle" in expected


def test_tk_reset_stops_before_confirmation_and_api(monkeypatch):
    assert pult_ui.LIMITED_TEST_RESET_UI_ENABLED is False
    monkeypatch.setattr(pult_ui.messagebox, "askyesno", _must_not_run)
    for scope in ("visits", "all"):
        probe = _ResetGuardProbe()
        pult_ui.Pult.do_reset(probe, scope)
        expected = pult_ui.LIMITED_TEST_RESET_DISABLED_MESSAGE
        assert probe.reset_out.text == expected
        assert probe.toasts == [(expected, True)]
        assert "snapshot" in expected
        assert "clearance" in expected
