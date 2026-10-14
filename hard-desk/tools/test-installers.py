"""Disposable local installation checks. Never replaces an existing installation."""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import winreg
import argparse
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
lab = Path(os.environ.get('HARD_QA_ROOT', repo / '_local/redesign-2026-10-09')) / 'install-qa'
version = json.loads((repo / 'hard-desk/package.json').read_text(encoding='utf-8'))['version']
previous = Path(os.environ.get('HARD_QA_PREVIOUS_RELEASE', repo / '_local/architecture-review-2026-10-09/release/3.0.0'))
previous_version = json.loads((previous / 'ASSETS.json').read_text(encoding='utf-8'))['version'] if (previous / 'ASSETS.json').is_file() else None
if previous_version == version or previous_version is None:
    previous = None
lab.mkdir(parents=True, exist_ok=True)
products = {'hard': ('Хард', 'ru.rimborso.hard.field'), 'soft': ('Софт', 'ru.rimborso.hard.soft'),
    'manager': ('Хард — Руководитель', 'ru.rimborso.hard.manager'), 'analyst': ('Хард — Аналитик', 'ru.rimborso.hard.analyst')}
parser = argparse.ArgumentParser()
parser.add_argument('--product', choices=products)
parser.add_argument('--concurrent-only', action='store_true')
options = parser.parse_args()
namespace = uuid.UUID('50e065bc-3134-11e6-9bab-38c9862bdaf3')
results = []
def reg(root, key, name):
    try:
        with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except FileNotFoundError:
        return None
def invoke(args, timeout=240):
    strings = [str(a) for a in args]
    command = subprocess.list2cmdline(strings[:-1]) + ' ' + strings[-1] if strings[-1].startswith('/D=') else strings
    completed = subprocess.run(command, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    assert completed.returncode == 0, (args[0], completed.returncode)
selected = {options.product: products[options.product]} if options.product else products
for product, (name, app_id) in (() if options.concurrent_only else selected.items()):
    guid = str(uuid.uuid5(namespace, app_id))
    install_key = 'Software\\' + guid
    uninstall_key = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\' + guid
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        if reg(root, install_key, 'InstallLocation') or reg(root, uninstall_key, 'DisplayName'):
            raise SystemExit('Existing product installation detected; disposable test stopped before changing it: ' + product)
    destination = lab / product / 'program'
    assert destination.resolve().is_relative_to(lab.resolve())
    user_data = lab / product / 'data'
    user_data.mkdir(parents=True, exist_ok=True)
    marker = user_data / 'preserved-queue-fixture.bin'
    marker.write_bytes(b'Synthetic queue fixture; no user identity or token')
    def check_data(create=False):
        fixture_env = dict(os.environ, HARD_INSTALL_QA_DATA=str(user_data), HARD_INSTALL_QA_MAKE='1' if create else '0')
        fixture_env.pop('ELECTRON_RUN_AS_NODE', None)
        completed = subprocess.run(['node', str(repo / 'hard-desk/tools/run-electron-test.cjs'),
            str(repo / 'hard-desk/tools/test-install-data.cjs'), *(['--make'] if create else [])],
            env=fixture_env, check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True, text=True, encoding='utf-8')
        print(completed.stdout.strip(), flush=True)
    check_data(True)
    protected = {p.name: p.read_bytes() for p in user_data.iterdir() if p.suffix in ('.key', '.bin')}
    # Windows shell locations are discovered through the known-folder API.
    import ctypes
    desktop_buffer = ctypes.create_unicode_buffer(32768)
    menu_buffer = ctypes.create_unicode_buffer(32768)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, desktop_buffer)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x02, None, 0, menu_buffer)
    shortcuts = [Path(desktop_buffer.value) / (name + '.lnk'), Path(menu_buffer.value) / (name + '.lnk')]
    backup = {p: p.read_bytes() if p.exists() else None for p in shortcuts}
    cache = Path(os.environ['LOCALAPPDATA']) / ('hard-' + product + '-updater') / 'installer.exe'
    cache_before = cache.read_bytes() if cache.exists() else None
    installer = repo / 'hard-desk/installers' / product / (product + '-' + version + '-setup.exe')
    process = None
    try:
        if previous:
            old = previous / (product + '-' + previous_version + '-setup.exe')
            if not old.is_file():
                raise RuntimeError('Missing previous-version test installer: ' + product)
            invoke([old, '/S', '/currentuser', '/D=' + str(destination)])
            assert reg(winreg.HKEY_CURRENT_USER, uninstall_key, 'DisplayVersion') == previous_version
        invoke([installer, '/S', '/currentuser', '/D=' + str(destination)])
        check_data()
        assert all((user_data / name).read_bytes() == content for name, content in protected.items())
        exe = destination / ('hard-' + product + '.exe')
        assert exe.exists()
        assert reg(winreg.HKEY_CURRENT_USER, uninstall_key, 'DisplayVersion') == version
        assert Path(reg(winreg.HKEY_CURRENT_USER, install_key, 'InstallLocation')).resolve() == destination.resolve()
        assert all(p.exists() for p in shortcuts)
        env = dict(os.environ, HARD_USER_DATA=str(user_data))
        env.pop('ELECTRON_RUN_AS_NODE', None)
        process = subprocess.Popen([str(exe), '--tray'], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(4)
        assert process.poll() is None
        log = (user_data / 'pult.log').read_text(encoding='utf-8')
        assert name in log and version in log
        second = subprocess.run([str(exe), '--tray'], env=env, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        assert second.returncode == 0 and process.poll() is None
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        process.wait(timeout=15)
        process = None
        invoke([installer, '/S', '/currentuser', '/D=' + str(destination)])
        assert marker.read_bytes().startswith(b'Synthetic queue')
        check_data()
        uninstaller = destination / ('Uninstall hard-' + product + '.exe')
        invoke([uninstaller, '/S', '/currentuser'])
        # NSIS exits via a temporary helper; wait for its final cleanup.
        deadline = time.monotonic() + 20
        while (exe.exists() or reg(winreg.HKEY_CURRENT_USER, uninstall_key, 'DisplayName') is not None) and time.monotonic() < deadline:
            time.sleep(.25)
        assert not exe.exists()
        assert reg(winreg.HKEY_CURRENT_USER, uninstall_key, 'DisplayName') is None
        assert marker.read_bytes().startswith(b'Synthetic queue')
        check_data()
        assert all((user_data / name).read_bytes() == content for name, content in protected.items())
        results.append({'product': product, 'install': True, 'registration': True, 'shortcuts': True,
            'singleInstance': True, 'reinstall': True, 'uninstall': True, 'dataPreserved': True,
            'previousVersionUpdate': bool(previous), 'realDPAPI': True, 'stableQueueAndDraft': True})
        print('Disposable installer QA passed:', product, flush=True)
    finally:
        if process and process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
        uninstaller = destination / ('Uninstall hard-' + product + '.exe')
        if uninstaller.exists():
            invoke([uninstaller, '/S', '/currentuser'])
        for shortcut, content in backup.items():
            if content is not None:
                shortcut.write_bytes(content)
            elif shortcut.exists():
                shortcut.unlink()
        if cache_before is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(cache_before)
        elif cache.exists():
            cache.unlink()
    (lab / 'RESULTS.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
processes = []
try:
    for product in ('soft', 'manager'):
        user_data = lab / 'concurrent' / product
        user_data.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, HARD_USER_DATA=str(user_data))
        env.pop('ELECTRON_RUN_AS_NODE', None)
        exe = repo / 'hard-desk/installers' / product / 'win-unpacked' / ('hard-' + product + '.exe')
        processes.append(subprocess.Popen([str(exe), '--tray'], env=env, creationflags=subprocess.CREATE_NO_WINDOW))
    time.sleep(4)
    assert all(p.poll() is None for p in processes)
    for product in ('soft', 'manager'):
        assert products[product][0] in (lab / 'concurrent' / product / 'pult.log').read_text(encoding='utf-8')
    print('Two products run concurrently with separate data and instance locks.', flush=True)
    (lab / 'CONCURRENT.json').write_text(json.dumps({'softAndManager': True, 'separateData': True}), encoding='utf-8')
finally:
    for p in processes:
        if p.poll() is None:
            subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True)
print('Only disposable workspace installations were tested; existing programs and original shortcuts were preserved.')
