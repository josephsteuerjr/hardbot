"""Frozen Python runtime; project modules are loaded from an explicit clean payload."""
import os
import sys
from pathlib import Path

root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'project'
# The source checkout and its schemas travel together. Resolve top-level
# imports such as exchange_contract there before PyInstaller's bytecode copy.
sys.path[:0] = [str(root), str(root / 'server'), str(root / 'pult')]
os.chdir(root)
if not os.environ.get('HARD_PULT_SETTINGS'):
    raise SystemExit('The analyst runtime requires an external settings path.')
# A direct import lets PyInstaller follow the backend's Python dependencies.
# Loading only a data copy with importlib omitted stdlib modules such as getpass.
from pult import desk_backend
desk_backend.main()
