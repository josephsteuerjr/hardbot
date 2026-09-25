"""LEGACY: возврат данных из бота в личные реестры — только в ПУСТЫЕ ячейки.

Режим отчёта остаётся доступным для диагностики. ``--apply`` по умолчанию
заблокирован; штатное восстановление выполняется через
``registry_cycle recovery``. Аварийная запись требует отдельного согласования
и точного значения ``HARDBOT_EMERGENCY_ALLOW_RESTORE_FROM_BOT_WRITE=1`` в
окружении одного процесса.

Зачем. За 20–24 июля филиалы несколько раз жаловались, что из личных реестров пропали
доп. номера, доп. адреса и комментарии. Бот к этому непричастен: цикл сборки-разброса
пересобирает личный .xlsb из файла-экспорта, и всё вписанное руками, чего нет в источнике,
исчезает. Но у бота остался слепок реестра — он видел эти значения при импортах, и в его
базе они живы: 1689 доп. адресов/номеров, 2614 комментариев выезда, 4080 «Общих
результатов», 535 телефонов. Этот скрипт возвращает их в файлы.

Чем отличается от обогащения. Обогащение (enrich.py) везёт в реестр РЕЗУЛЬТАТ ВЫЕЗДА и
делает это властно: доказанный визит перекрывает файл. Здесь наоборот — восстановление:

  * пишем ТОЛЬКО в пустую ячейку. Непустую не трогаем никогда, даже если значения
    разошлись: в файле может лежать более свежая ручная правка;
  * пишем ТОЛЬКО те колонки, которые заведомо не рождаются в приложении
    (доп. адрес/номер, телефоны, комментарий звонка, общий результат) — их источник
    всегда реестр, а бот тут архив;
  * по умолчанию НИЧЕГО не пишет: сначала отчёт, запись — отдельной командой --apply.

Порядок работы:
    python restore_from_bot.py                       # отчёт по всем сотрудникам
    python restore_from_bot.py --only "Иванов Иван"  # по одному
    # --apply — только аварийный режим по отдельному регламенту

База папок берётся из «настройки пульта.json» (scatterBase) или из --base, маска
республик — из «конфигурация путей.json», как у обогащения и разброса.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

import enrich          # noqa: E402
import razbros         # noqa: E402

SETTINGS_PATH = APP_DIR / "настройки пульта.json"
REPORT_DIR = APP_DIR / "отчёты возврата"
LEGACY_WRITE_ENV = "HARDBOT_EMERGENCY_ALLOW_RESTORE_FROM_BOT_WRITE"
LEGACY_WRITE_BLOCKED_EXIT = 4

# Колонки, которые в приложении не заполняются: их источник — реестр, бот им архив.
# Комментарий выезда и поля формы сюда НЕ входят: их возвращает обогащение, и там свои
# правила старшинства.
RESTORE_KEYS = ["extra_address", "phone", "phone_main", "call_comment", "overall_result"]
MAX_WRITES_PER_FILE = 300
MAX_WRITE_SHARE = 0.25
MAX_VALUE_LEN = 500


class VerificationError(RuntimeError):
    pass


class LegacyWriteBlocked(RuntimeError):
    """Старое восстановление не вправе писать без аварийного допуска."""


def legacy_write_enabled() -> bool:
    """Аварийный допуск является fail-closed: разрешает только точное ``1``."""
    return os.environ.get(LEGACY_WRITE_ENV, "") == "1"


def require_legacy_write_enabled() -> None:
    if not legacy_write_enabled():
        raise LegacyWriteBlocked(
            "запись restore_from_bot заблокирована; штатное восстановление "
            "выполняется через registry_cycle recovery. Только для отдельно "
            "согласованной аварийной операции установи "
            f"{LEGACY_WRITE_ENV}=1 на время одного запуска"
        )


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def fetch_rows(settings: dict, log):
    """Сырая выдача реестра из бота: нужен режим full — в безопасной выдаче системные
    колонки скрыты, а нам нужны именно они."""
    import urllib.request

    url = settings["url"].rstrip("/") + "/admin/export.json?mode=full"
# Мастер-ключ больше не открывает данные после первой привязки — ходим
# тем же, чем ходит сам пульт: токеном устройства плюс отпечаток машины.
    from core.api import auth_headers, open_secure, require_https
    require_https(settings["url"])  # не https — ключ и токен не поедут вовсе
    req = urllib.request.Request(url, headers=auth_headers(
        settings.get("key", ""), settings.get("device_token", "")))
    with open_secure(req, timeout=300) as r:
        data = json.loads(r.read().decode("utf-8"))
    log(f"Из бота получено строк: {len(data.get('rows') or [])}")
    return data["fields"], data["rows"]


def plan_file(path: Path, fields: list[dict], rows: list[dict], log) -> dict:
    """Что можно вернуть в этот файл. Excel не открываем — читаем calamine."""
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_path(str(path))
    name = "Портфели" if "Портфели" in wb.sheet_names else wb.sheet_names[0]
    grid = wb.get_sheet_by_name(name).to_python()
    hi, col_map = enrich.find_header(grid, fields)
    by_cid = {enrich.norm_cid(r.get("contract_id")): r for r in rows}

    writes, absent = [], [k for k in RESTORE_KEYS if k not in col_map]
    file_rows: dict[str, list[list]] = {}
    cid_col = col_map.get("contract_id")
    if cid_col is not None:
        for row in grid[hi + 1:]:
            cid = enrich.norm_cid(row[cid_col] if cid_col < len(row) else None)
            if cid:
                file_rows.setdefault(cid, []).append(row)

    conflict_contracts = set()
    for cid, cid_rows in file_rows.items():
        for key in RESTORE_KEYS:
            ci = col_map.get(key)
            if ci is None:
                continue
            values = {enrich.cell_text(row[ci] if ci < len(row) else None)
                      for row in cid_rows}
            values.discard("")
            if len(values) > 1:
                conflict_contracts.add(cid)
                break

    oversize = 0
    for ri in range(hi + 1, len(grid)):
        row = grid[ri]
        if cid_col is None or cid_col >= len(row):
            continue
        cid = enrich.norm_cid(row[cid_col])
        if not cid or cid not in by_cid or cid in conflict_contracts:
            continue
        srv = by_cid[cid]
        for key in RESTORE_KEYS:
            ci = col_map.get(key)
            if ci is None or ci >= len(row):
                continue
            if enrich.cell_text(row[ci]):
                continue                      # непустую ячейку не трогаем никогда
            value = enrich.cell_text(srv.get(key))
            if not value:
                continue
            if len(value) > MAX_VALUE_LEN:
                oversize += 1
                continue
            writes.append({"row": ri + 1, "col": ci + 1, "cid": cid,
                           "key": key, "value": value})
    data_rows = max(len(grid) - (hi + 1), 0)
    blocked = []
    if absent:
        blocked.append(f"нет колонок {absent}")
    if len(writes) > MAX_WRITES_PER_FILE:
        blocked.append(f"план {len(writes)} > {MAX_WRITES_PER_FILE} ячеек")
    if data_rows and len(writes) > data_rows * MAX_WRITE_SHARE:
        blocked.append(f"план {len(writes)} > четверти {data_rows} строк")
    if blocked:
        writes = []
    return {"file": str(path), "sheet": name, "header_row": hi + 1,
            "writes": writes, "absent_columns": absent, "data_rows": data_rows,
            "conflict_contracts": sorted(conflict_contracts),
            "oversize_values": oversize, "blocked": blocked}


def _read_grid(path: Path, sheet: str) -> list[list]:
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_path(str(path))
    return wb.get_sheet_by_name(sheet).to_python()


def _value_diffs(before: list[list], after: list[list]) -> set[tuple[int, int]]:
    rows = max(len(before), len(after))
    cols = max(max((len(r) for r in before), default=0),
               max((len(r) for r in after), default=0))
    diffs = set()
    for ri in range(rows):
        for ci in range(cols):
            old = before[ri][ci] if ri < len(before) and ci < len(before[ri]) else None
            new = after[ri][ci] if ri < len(after) and ci < len(after[ri]) else None
            if enrich.cell_text(old) != enrich.cell_text(new):
                diffs.add((ri + 1, ci + 1))
    return diffs


def verify_applied(path: Path, backup: Path, plan: dict, fields: list[dict]) -> None:
    """После Excel-save фактический diff обязан быть ровно планом."""
    before = _read_grid(backup, plan["sheet"])
    after = _read_grid(path, plan["sheet"])
    expected = {(w["row"], w["col"]) for w in plan["writes"]}
    actual = _value_diffs(before, after)
    if actual != expected:
        raise VerificationError(
            f"diff не совпал: ожидалось {len(expected)}, получено {len(actual)}, "
            f"лишних {len(actual - expected)}, пропущено {len(expected - actual)}")
    for w in plan["writes"]:
        ri, ci = w["row"] - 1, w["col"] - 1
        got = after[ri][ci] if ri < len(after) and ci < len(after[ri]) else None
        if enrich.cell_text(got) != enrich.cell_text(w["value"]):
            raise VerificationError(
                f"ячейка {w['row']}:{w['col']} не содержит записанное значение")

    _hi, col_map = enrich.find_header(before, fields)
    restore_cols = [col_map[k] for k in RESTORE_KEYS if k in col_map]
    def nonempty(grid):
        return sum(1 for row in grid[_hi + 1:] for ci in restore_cols
                   if ci < len(row) and enrich.cell_text(row[ci]))
    before_n, after_n = nonempty(before), nonempty(after)
    if after_n < before_n:
        raise VerificationError(
            f"непустых ячеек стало меньше: {before_n} → {after_n}")


def apply_plan(app, path: Path, plan: dict, fields: list[dict], log) -> int:
    """Записать план в файл. Копия делается ДО первой записи, как в обогащении."""
    require_legacy_write_enabled()
    # Занятость проверяем ДО копии: иначе рядом с книгой останется копия от
    # прогона, который не записал ни одной ячейки.
    enrich.assert_writable(path)
    stamp = datetime.now().strftime("%d.%m.%y %H-%M")
    keep = path.with_name(f"{path.stem} (до возврата {stamp}){path.suffix}")
    n = 1
    while keep.exists():
        n += 1
        keep = path.with_name(f"{path.stem} (до возврата {stamp}, {n}){path.suffix}")
    shutil.copy2(path, keep)
    log(f"  копия: {keep.name}")

    wb = None
    try:
        wb = app.books.open(str(path))
        ws = wb.sheets[plan["sheet"]]
        for w in plan["writes"]:
            ws.range((w["row"], w["col"])).value = w["value"]
        wb.save()
    except Exception:
        if wb is not None:
            try:
                wb.close()
            except Exception:  # noqa: BLE001
                pass
            wb = None
        shutil.copy2(keep, path)
        raise
    finally:
        if wb is not None:
            wb.close()
    try:
        verify_applied(path, keep, plan, fields)
    except Exception:
        shutil.copy2(keep, path)
        raise
    return len(plan["writes"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Возврат данных из бота в личные реестры")
    ap.add_argument("--base", help="папка с республиками (по умолчанию из настроек пульта)")
    ap.add_argument("--only", action="append", default=[], help="только этот сотрудник")
    ap.add_argument(
        "--apply",
        action="store_true",
        help=(
            "аварийная запись; требует "
            f"{LEGACY_WRITE_ENV}=1 (по умолчанию только отчёт)"
        ),
    )
    a = ap.parse_args(argv)

    if a.apply:
        try:
            require_legacy_write_enabled()
        except LegacyWriteBlocked as exc:
            print(f"[BLOCKED] {exc}", file=sys.stderr, flush=True)
            return LEGACY_WRITE_BLOCKED_EXIT

    def log(s):
        print(s, flush=True)

    settings = load_settings()
    if not settings.get("url"):
        log("Нет адреса сервера: заполни «настройки пульта.json» через пульт.")
        return 2
    base = a.base or settings.get("scatterBase")
    if not base:
        log("Не задана базовая папка: ни --base, ни scatterBase в настройках пульта.")
        return 2

    fields, rows = fetch_rows(settings, log)
    fmap = razbros.build_folder_map(
        razbros.resolve_roots(base, razbros.load_config(None), []))

    only = {enrich.norm_name(x) for x in a.only}
    plans, skipped = [], []
    for key, entries in sorted(fmap.items()):
        emp = entries[0].get("employee") or entries[0]["path"].name
        if only and enrich.norm_name(emp) not in only:
            continue
        for folder in entries:
            for p in sorted(folder["path"].glob("*.xlsb")):
                if p.name.startswith("~") or razbros.is_backup_copy(p.name):
                    continue
                try:
                    plan = plan_file(p, fields, rows, log)
                except Exception as e:  # noqa: BLE001 — битый файл не валит пакет
                    skipped.append(f"{p.name}: {e}")
                    continue
                plan["employee"] = emp
                for reason in plan["blocked"]:
                    skipped.append(f"{p.name}: {reason}")
                if plan["conflict_contracts"]:
                    skipped.append(
                        f"{p.name}: конфликтующие значения у "
                        f"{len(plan['conflict_contracts'])} КД — они пропущены")
                if plan["oversize_values"]:
                    skipped.append(
                        f"{p.name}: значений длиннее {MAX_VALUE_LEN} — "
                        f"{plan['oversize_values']}, они пропущены")
                if plan["writes"]:
                    plans.append(plan)

    total = sum(len(p["writes"]) for p in plans)
    log("")
    log(f"К возврату: {total} ячеек в {len(plans)} файлах")
    by_key = {}
    for p in plans:
        for w in p["writes"]:
            by_key[w["key"]] = by_key.get(w["key"], 0) + 1
    for k, v in sorted(by_key.items(), key=lambda kv: -kv[1]):
        log(f"    {k:16} {v}")
    for p in plans:
        if p["absent_columns"]:
            log(f"  [!] {Path(p['file']).name}: нет колонок {p['absent_columns']}")
    for s in skipped:
        log(f"  [!] пропущен {s}")

    REPORT_DIR.mkdir(exist_ok=True)
    rep = REPORT_DIR / f"возврат {datetime.now():%Y-%m-%d %H-%M}.csv"
    with open(rep, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["сотрудник", "файл", "строка", "колонка", "номер КД", "значение"])
        for p in plans:
            for x in p["writes"]:
                w.writerow([p["employee"], Path(p["file"]).name, x["row"], x["key"],
                            x["cid"], x["value"]])
    log(f"Отчёт: {rep}")

    if not a.apply:
        log("\nЭто отчёт. Проверь его и запусти ещё раз с --apply, чтобы записать.")
        return 0
    if not plans:
        return 0

    enrich._co_init()
    import xlwings as xw
    written = 0
    with enrich.open_excel(xw, log) as app:
        app.display_alerts = False
        app.screen_updating = False
        for i, p in enumerate(plans, 1):
            path = Path(p["file"])
            log(f"[{i}/{len(plans)}] {p['employee']} — {path.name}: {len(p['writes'])} ячеек")
            for attempt in range(1, 4):
                try:
                    written += apply_plan(app, path, p, fields, log)
                    log("  проверка diff: ok")
                    break
                except VerificationError as e:
                    log(f"  [!] проверка не прошла, файл восстановлен из копии: {e}")
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 3:
                        log(f"  [!] не записан после трёх попыток: {e}")
                        break
                    log(f"  [!] попытка {attempt}/3 не удалась: {e}; повтор через минуту")
                    time.sleep(60)
    log(f"\nЗаписано ячеек: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
