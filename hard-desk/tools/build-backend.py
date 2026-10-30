"""Build a self-contained analyst runtime from tracked code and public schemas only."""
import shutil
import subprocess
import sys
import json
from pathlib import Path
from backend_sources import source_manifest

repo = Path(__file__).resolve().parents[2]
desk = repo / 'hard-desk'
work = desk / 'backend-build'
stage = work / 'project'
manifest = source_manifest(repo)
if work.exists():
    if work.is_symlink() or work.resolve().parent != desk.resolve():
        raise SystemExit('Unsafe backend build directory')
    shutil.rmtree(work)
stage.mkdir(parents=True)
for source in manifest['files']:
    rel = source['path']
    target = stage / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / rel, target)
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console',
    '--name', 'hard-backend', '--distpath', str(desk / 'backend-dist'), '--workpath', str(work / 'pyinstaller'),
    '--specpath', str(work), '--paths', str(stage), '--add-data', str(stage) + ';project']
for package in ['python_calamine', 'openpyxl', 'lxml', 'tzdata', 'xlwings', 'win32com', 'pythoncom', 'pywintypes', 'segno', 'xlsxwriter', 'psutil', 'paramiko', 'cryptography']:
    command += ['--collect-all', package]
command += ['--hidden-import','zoneinfo','--hidden-import','win32file']
for package in ['PySide6', 'PyQt6', 'PyQt5', 'numpy', 'pandas', 'matplotlib', 'scipy', 'torch', 'tkinter', 'IPython', 'pytest']:
    command += ['--exclude-module', package]
command.append(str(desk / 'tools/backend-entry.py'))
subprocess.run(command, cwd=repo, check=True)
if source_manifest(repo) != manifest:
    raise SystemExit('Backend sources changed during the build; rebuild before packaging.')
(desk / 'backend-dist/hard-backend/SOURCE-MANIFEST.json').write_text(
    json.dumps(manifest, indent=2), encoding='utf-8')
print('Analyst runtime built from tracked source only; no settings, registries, or databases included.')
