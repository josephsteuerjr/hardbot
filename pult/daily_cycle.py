"""Суточный цикл «бот ↔ реестры» без пульта — под планировщик Windows.

Зачем: ежедневная работа сводилась к десятку кликов в пульте (папка → файл → галочки →
кнопка → отчёт), и делал их человек. Регламент СНГ (раздел 8.2) описывает этот цикл как
автоматический, здесь он и реализован.

Порядок суток и почему он именно такой:

    17:00      руководители сдают заявки на закрепление
    ~17:30     ВЫГРУЗКА (export): обогащаем личные реестры результатами выездов за день
    ~23:00     ЧУЖОЙ ночной скрипт на xlwings вносит закрепления в общий реестр
    после него ЗАГРУЗКА (import): полные портфели из личных реестров едут в бота

Ночной скрипт НЕ наш: он работает с теми же файлами на сетевом диске, и запускать
загрузку «по часам» опасно — сдвинулся на полчаса, и мы затираем друг друга. Поэтому
загрузка ждёт не время, а ФАКТ: общий реестр изменился и перестал меняться N минут
(``--after-registry`` + ``--stable-min``). Тогда чужой скрипт остаётся хозяином ночи,
а мы идём следом.

Excel через COM требует живой интерактивной сессии: в планировщике задача должна стоять
«выполнять только при входе пользователя в систему», иначе xlwings не поднимется.

Примеры:
    python daily_cycle.py export
    python daily_cycle.py import --after-registry "U:\\rbr\\...\\ОБЩИЙ РЕЕСТР ИЮЛЬ.xlsb"
    python daily_cycle.py export --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import enrich          # noqa: E402  — рядом, тот же пакет пульта
import razbros         # noqa: E402

SETTINGS_PATH = APP_DIR / "настройки пульта.json"
STATE_DIR = APP_DIR.parent / "_local" / "daily_cycle"
LOCK_PATH = STATE_DIR / "cycle.lock"
LOCK_STALE_SEC = 6 * 3600      # зависший прогон не блокирует навсегда


# --- журнал -----------------------------------------------------------------
class Journal:
    """Пишет в файл и на экран разом: планировщик экран не покажет, а файл останется."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(path, "a", encoding="utf-8")
        self.lines: list[str] = []

    def __call__(self, msg: str) -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        self.lines.append(line)
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self) -> None:
        try:
            self.fh.close()
        except Exception:  # noqa: BLE001
            pass


# --- сервер -----------------------------------------------------------------
def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        raise SystemExit(f"Нет файла настроек: {SETTINGS_PATH}. Запусти пульт и сохрани "
                         "адрес сервера с ключом — цикл берёт их оттуда.")
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def api(settings: dict, method: str, path: str, body: dict | None = None, timeout=180):
    url = settings["url"].rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"X-Admin-Key": settings.get("key", "")})
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def notify(settings: dict, text: str, log) -> None:
    """Итог суток — в личку владельцу бота. Молчание планировщика хуже ошибки:
    незамеченный сбой цикла означает день без обмена данными."""
    try:
        api(settings, "POST", "/admin/notify", {"text": text}, timeout=30)
        log("Отчёт отправлен в Телеграм")
    except urllib.error.HTTPError as e:
        log(f"[!] Отчёт не отправлен (HTTP {e.code}) — старый сервер без /admin/notify?")
    except Exception as e:  # noqa: BLE001 — отчёт не должен ронять цикл
        log(f"[!] Отчёт не отправлен: {e}")


# --- блокировка -------------------------------------------------------------
class Lock:
    """Два прогона одновременно = две сессии Excel на одних файлах. Не допускаем."""

    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if LOCK_PATH.exists():
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age < LOCK_STALE_SEC:
                raise SystemExit(f"Цикл уже выполняется (замок {LOCK_PATH}, "
                                 f"{int(age // 60)} мин). Если это не так — удали файл.")
            LOCK_PATH.unlink(missing_ok=True)
        LOCK_PATH.write_text(f"{os.getpid()} {datetime.now():%Y-%m-%d %H:%M:%S}",
                             encoding="utf-8")
        return self

    def __exit__(self, *_exc):
        LOCK_PATH.unlink(missing_ok=True)
        return False


# --- ожидание чужого ночного скрипта ----------------------------------------
def wait_for_registry(path: Path, stable_min: int, timeout_min: int, log) -> bool:
    """Ждём, пока общий реестр изменится и перестанет меняться stable_min минут.

    Триггер по факту, а не по часам: ночной скрипт чужой, его расписание может сдвинуться,
    а «стабильный mtime» означает, что он закончил писать и файл отпущен."""
    if not path.exists():
        log(f"[!] Общий реестр не найден: {path} — жду появления")
    deadline = time.time() + timeout_min * 60
    start_mtime = path.stat().st_mtime if path.exists() else None
    log(f"Жду общий реестр: {path.name}, стабильность {stable_min} мин, "
        f"предел ожидания {timeout_min} мин")
    changed = start_mtime is None
    while time.time() < deadline:
        if not path.exists():
            time.sleep(60)
            continue
        mtime = path.stat().st_mtime
        if not changed:
            if start_mtime is not None and mtime != start_mtime:
                changed = True
                log(f"Реестр обновился в {datetime.fromtimestamp(mtime):%H:%M:%S}")
            else:
                time.sleep(60)
                continue
        quiet = time.time() - mtime
        if quiet >= stable_min * 60:
            log(f"Реестр не менялся {int(quiet // 60)} мин — можно работать")
            return True
        time.sleep(60)
    log("[!] Не дождался: чужой скрипт не отработал в отведённое окно. Цикл пропущен, "
        "чтобы не столкнуться с ним на одних файлах.")
    return False


# --- общие шаги -------------------------------------------------------------
def folder_map(base: str | None):
    reps = razbros.load_config(None)
    return razbros.build_folder_map(razbros.resolve_roots(base, reps, []))


def personal_files(entries, employee: str) -> list[Path]:
    """Личные реестры сотрудника: ФИО.xlsb, иначе файл по имени папки.
    Времянки Excel (~$…) и страховочные копии «(до …)» не берём — это не реестр,
    а обогатить старую копию вместо живого файла хуже, чем не обогатить вовсе."""
    files: list[Path] = []
    for e in entries:
        folder = e["path"]
        cands = [folder / f"{razbros.sanitize_filename(employee)}.xlsb",
                 folder / f"{folder.name}.xlsb"]
        found = next((c for c in cands if c.exists()), None)
        if not found:
            found = next((p for p in sorted(folder.glob("*.xlsb"))
                          if not p.name.startswith("~")
                          and not razbros.is_backup_copy(p.name)), None)
        if found and found not in files:
            files.append(found)
    return files


def step_export(settings: dict, base: str | None, dry: bool, log) -> str:
    """ВЫГРУЗКА: результаты выездов из бота в личные реестры (обогащение)."""
    data = api(settings, "GET", "/admin/export.json", timeout=180)
    by_emp = enrich.split_by_employee(data["rows"])
    fmap = folder_map(base)

    jobs, nofolder, nofile = [], [], []
    for key, e in sorted(by_emp.items(), key=lambda kv: kv[1]["employee"].lower()):
        if not e["filled"]:
            continue                       # бот ничего не заполнял — обогащать нечего
        entries = fmap.get(key)
        if not entries:
            nofolder.append(f"{e['employee']} ({e['filled']})")
            continue
        files = personal_files(entries, e["employee"])
        if not files:
            nofile.append(e["employee"])
            continue
        for p in files:
            jobs.append({"employee": e["employee"], "path": str(p), "rows": e["rows"]})

    log(f"К обогащению: {len(jobs)} файлов, {sum(1 for e in by_emp.values() if e['filled'])} "
        f"сотрудников с визитами бота")
    for who in nofolder:
        log(f"[!] Есть данные бота, но нет папки: {who}")
    for who in nofile:
        log(f"[!] Папка есть, но нет .xlsb: {who}")
    if dry:
        return f"Сухой прогон: обогатили бы {len(jobs)} файлов"
    if not jobs:
        return "Обогащать нечего"

    results = enrich.enrich_many(jobs, data["fields"], log)
    written = sum(len(r.get("writes") or []) for r in results if not r.get("error"))
    errors = [f"{r.get('employee')}: {r['error']}" for r in results if r.get("error")]
    for e in errors:
        log(f"[!] {e}")
    out = f"Выгрузка: файлов {len(results) - len(errors)} из {len(results)}, ячеек {written}"
    if nofolder or nofile:
        out += f"; без папки/файла: {len(nofolder) + len(nofile)}"
    if errors:
        out += f"; ошибок {len(errors)}"
    return out


def read_sheets(path: str) -> list[dict]:
    """Файл реестра → [{name, grid}] для POST /admin/import.json (python-calamine).
    Тот же парсер, что в пульте: разбор локальный, на сервер летит JSON."""
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_path(path)
    sheets = []
    for name in wb.sheet_names:
        rows = wb.get_sheet_by_name(name).to_python()
        grid = [[(None if c is None or (isinstance(c, str) and not c.strip()) else c)
                 for c in row] for row in rows]
        if grid:
            sheets.append({"name": name, "grid": grid})
    if not sheets:
        raise ValueError("в файле нет читаемых листов")
    return sheets


def step_import(settings: dict, base: str | None, dry: bool, log,
                registry: str | None = None) -> str:
    """ЗАГРУЗКА: полные портфели в бота.

    Штатный источник — ОБЩИЙ реестр одним файлом: он и есть источник правды по
    закреплениям после ночного скрипта, а сопоставление колонок у сервера идёт по
    заголовкам, поэтому общий реестр импортируется ровно так же, как личный.
    Это заодно возвращает владельца строкам, которые его потеряли.

    Личные реестры — запасной путь (тест-режим, когда общий недоступен)."""
    if registry:
        p = Path(registry)
        if not p.exists():
            raise FileNotFoundError(f"общий реестр не найден: {p}")
        files = [p]
        log(f"Источник — общий реестр: {p.name}")
    else:
        fmap = folder_map(base)
        files = []
        for entries in fmap.values():
            emp = entries[0].get("employee") or entries[0]["path"].name
            for f in personal_files(entries, emp):
                if f not in files:
                    files.append(f)
        log(f"Источник — личные реестры, найдено: {len(files)}")
    if dry:
        return f"Сухой прогон: импортировали бы {len(files)} файлов"
    if not files:
        return "Реестров не нашлось — проверь путь или базовую папку"

    agg = {"total_rows": 0, "inserted": 0, "updated": 0, "skipped_no_id": 0,
           "duplicates": 0, "protected_cells": 0, "office_cells": 0, "no_responsible": 0}
    errors: list[str] = []
    for i, p in enumerate(files, 1):
        log(f"[{i}/{len(files)}] {p.name}…")
        try:
            sheets = read_sheets(str(p))
            d = api(settings, "POST", "/admin/import.json", {"sheets": sheets}, timeout=300)
        except Exception as e:  # noqa: BLE001 — битый файл не валит пакет
            errors.append(f"{p.name}: {e}")
            continue
        if d.get("error"):
            errors.append(f"{p.name}: {d['error']}")
            continue
        for k in agg:
            agg[k] += int(d.get(k) or 0)
    for e in errors:
        log(f"[!] {e}")
    out = (f"Загрузка: файлов {len(files) - len(errors)} из {len(files)}, "
           f"строк {agg['total_rows']}, новых {agg['inserted']}, обновлено {agg['updated']}")
    if agg["no_responsible"]:
        out += (f"\n⚠️ Без ответственного: {agg['no_responsible']} строк — эти кейсы "
                "в базе есть, но выездник их не видит")
    if agg["protected_cells"]:
        out += f"\nОтклонено защитой визита: {agg['protected_cells']} ячеек"
    if errors:
        out += f"\nОшибок: {len(errors)}"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Суточный цикл бот ↔ реестры")
    ap.add_argument("step", choices=["export", "import"],
                    help="export — выгрузка в реестры до ночного скрипта; "
                         "import — загрузка портфелей в бота после него")
    ap.add_argument("--base", help="базовая папка республик (по умолчанию из настроек пульта)")
    ap.add_argument("--registry", help="общий реестр одним файлом — штатный источник загрузки")
    ap.add_argument("--after-registry", help="ждать этот файл общего реестра перед стартом")
    ap.add_argument("--stable-min", type=int, default=20,
                    help="сколько минут файл должен не меняться (по умолчанию 20)")
    ap.add_argument("--timeout-min", type=int, default=300,
                    help="предел ожидания реестра, минут (по умолчанию 300)")
    ap.add_argument("--dry-run", action="store_true", help="ничего не писать, только показать план")
    ap.add_argument("--no-report", action="store_true", help="не слать итог в Телеграм")
    a = ap.parse_args(argv)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = Journal(STATE_DIR / f"{datetime.now():%Y-%m-%d}.log")
    log(f"=== {a.step} ===")
    try:
        settings = load_settings()
        base = a.base or settings.get("scatterBase") or None
        if not base:
            log("[!] Базовая папка не задана: ни --base, ни scatterBase в настройках пульта")
            return 2

        if a.after_registry and not a.dry_run:
            if not wait_for_registry(Path(a.after_registry), a.stable_min,
                                     a.timeout_min, log):
                if not a.no_report:
                    notify(settings, "⏭ Суточный цикл пропущен: не дождался общего реестра "
                                     "после ночного скрипта.", log)
                return 3

        with Lock():
            started = time.time()
            if a.step == "export":
                summary = step_export(settings, base, a.dry_run, log)
            else:
                summary = step_import(settings, base, a.dry_run, log,
                                      registry=a.registry or a.after_registry)
            summary += f" · {int(time.time() - started) // 60} мин"
        log(summary.replace("\n", " | "))
        if not a.no_report and not a.dry_run:
            title = "📤 Выгрузка в реестры" if a.step == "export" else "📥 Загрузка портфелей"
            notify(settings, f"{title} — {datetime.now():%d.%m %H:%M}\n{summary}", log)
        return 0
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — планировщик не покажет traceback
        log(f"[!] ЦИКЛ УПАЛ: {e}")
        try:
            notify(load_settings(), f"❌ Суточный цикл ({a.step}) упал: {e}", log)
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
