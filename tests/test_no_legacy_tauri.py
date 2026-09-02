"""Пульт и перенос реестров — десктоп на PySide6, без Tk, без Tauri, без bat."""

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
PULT = ROOT / "pult"
CYCLE = ROOT / "registry_cycle"


def _py_files(folder: Path):
    return [p for p in folder.rglob("*.py*") if p.suffix in (".py", ".pyw")
            and "__pycache__" not in p.parts]


def test_only_supported_qt_pult_is_shipped():
    assert (PULT / "pult.py").is_file()
    assert (PULT / "app.py").is_file()
    assert (PULT / "Пульт.pyw").is_file()
    assert not (PULT / "Пульт.bat").exists()
    assert not (PULT / "dist").exists()
    assert not (PULT / "src-tauri").exists()

    launcher = (PULT / "Пульт.pyw").read_text("utf-8")
    assert "from pult.app import main" in launcher
    assert "tauri" not in launcher.casefold()


def test_no_tkinter_in_pult_and_registry_cycle():
    pattern = re.compile(r"^\s*(import tkinter|from tkinter)", re.M)
    offenders = [str(p.relative_to(ROOT)) for folder in (PULT, CYCLE)
                 for p in _py_files(folder)
                 if pattern.search(p.read_text("utf-8", errors="ignore"))]
    assert offenders == []


def test_registry_transfer_has_pyw_entry_points():
    assert (ROOT / "Перенос реестров.pyw").is_file()
    assert (ROOT / "Хард — Реестры.pyw").is_file()
    assert not (ROOT / "Перенос реестров.bat").exists()
    assert not (ROOT / "Контур — Реестры.bat").exists()
