"""LEGACY: суточный цикл «бот ↔ реестры» без пульта.

Запись по умолчанию заблокирована. Разрешён только ``--dry-run``. Аварийный
write-запуск требует отдельного согласования и точного значения
``HARDBOT_EMERGENCY_ALLOW_DAILY_CYCLE_WRITE=1`` в окружении одного процесса.
Штатный обмен выполняется через ``registry_cycle``.

Зачем: ежедневная работа сводилась к десятку кликов в пульте (папка → файл → галочки →
кнопка → отчёт), и делал их человек. Регламент СНГ (раздел 8.2) описывает этот цикл как
автоматический, здесь он и реализован.

Порядок суток и почему он именно такой:

    17:00      руководители сдают заявки на закрепление
    ~17:30     ВЫГРУЗКА (export): обогащаем личные реестры результатами выездов за день
    ~23:00     ЧУЖОЙ ночной скрипт на xlwings вносит закрепления в общий реестр
    после него ЗАГРУЗКА (import): опубликованный общий master едет в бота

Ночной скрипт НЕ наш: он работает с теми же файлами на сетевом диске, и запускать
загрузку «по часам» опасно — сдвинулся на полчаса, и мы затираем друг друга. Поэтому
загрузка ждёт не время, а ФАКТ: общий реестр изменился и перестал меняться N минут
(``--after-registry`` + ``--stable-min``). Тогда чужой скрипт остаётся хозяином ночи,
а мы идём следом.

Excel через COM требует живой интерактивной сессии: в планировщике задача должна стоять
«выполнять только при входе пользователя в систему», иначе xlwings не поднимется.

Разрешённый диагностический пример:
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
from datetime import date, datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

# Планировщик перехватывает вывод, и на русской Windows он уходит в cp1251 —
# первая же кириллическая строка роняла бы прогон UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — старый Python или подменённый поток
        pass

import enrich          # noqa: E402  — рядом, тот же пакет пульта
import razbros         # noqa: E402

SETTINGS_PATH = APP_DIR / "настройки пульта.json"
STATE_DIR = APP_DIR.parent / "_local" / "daily_cycle"
LOCK_PATH = STATE_DIR / "cycle.lock"
LOCK_STALE_SEC = 6 * 3600      # зависший прогон не блокирует навсегда
LEGACY_WRITE_ENV = "HARDBOT_EMERGENCY_ALLOW_DAILY_CYCLE_WRITE"
LEGACY_WRITE_BLOCKED_EXIT = 4


class LegacyWriteBlocked(RuntimeError):
    """Старый двусторонний цикл не вправе писать без аварийного допуска."""


def legacy_write_enabled() -> bool:
    """Аварийный допуск является fail-closed: разрешает только точное ``1``."""
    return os.environ.get(LEGACY_WRITE_ENV, "") == "1"


def require_legacy_write_enabled() -> None:
    if not legacy_write_enabled():
        raise LegacyWriteBlocked(
            "запись старого daily_cycle заблокирована; штатный обмен выполняется "
            "через registry_cycle. Только для отдельно согласованной аварийной "
            f"операции установи {LEGACY_WRITE_ENV}=1 на время одного запуска"
        )


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
    if not dry:
        require_legacy_write_enabled()
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


def _cell_json(v):
    """Значение calamine → JSON-совместимое значение.

    Общий реестр содержит настоящие Excel-даты. python-calamine возвращает их
    как ``date``/``datetime``, которые стандартный json.dumps не сериализует.
    Держим преобразование тем же, что в интерактивном пульте.
    """
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str):
        t = v.strip()
        return t or None
    if isinstance(v, float):
        return int(v) if v.is_integer() and abs(v) < 9e15 else v
    if isinstance(v, datetime):
        s = v.isoformat(sep=" ", timespec="seconds")
        return s[:-9] if s.endswith(" 00:00:00") else s
    if isinstance(v, date):
        return v.isoformat()
    return v if isinstance(v, (int, float, bool)) else str(v)


def read_sheets(path: str) -> list[dict]:
    """Файл реестра → [{name, grid}] для POST /admin/import.json (python-calamine).
    Тот же парсер, что в пульте: разбор локальный, на сервер летит JSON."""
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_path(path)
    sheets = []
    for name in wb.sheet_names:
        rows = wb.get_sheet_by_name(name).to_python()
        grid = [[_cell_json(c) for c in row] for row in rows]
        if grid:
            sheets.append({"name": name, "grid": grid})
    if not sheets:
        raise ValueError("в файле нет читаемых листов")
    return sheets


def _contract_id(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value or "").strip()


def filter_to_existing_contracts(sheets: list[dict], existing_ids: set[str]):
    """Оставить в листе реестра только КД, уже существующие в production.

    Общий реестр СНГ намного шире сознательно ограниченной базы бота. Его импорт
    нужен для обновления владельцев и полей имеющихся КД, а не для неявной
    загрузки десятков тысяч новых договоров. Исходный Excel не меняется:
    фильтруется только JSON-грид в памяти перед POST.
    """
    out = [{"name": s["name"], "grid": s["grid"]} for s in sheets]
    candidates = []
    for si, sheet in enumerate(sheets):
        for hi, row in enumerate(sheet["grid"][:10]):
            for ci, cell in enumerate(row):
                if str(cell or "").strip().lower().startswith("номер кд"):
                    candidates.append((sheet["name"] == "Портфели", si, hi, ci))
    if not candidates:
        raise ValueError("колонка «Номер КД / ID» не найдена в первых 10 строках")
    _named, si, hi, ci = max(candidates)
    grid = sheets[si]["grid"]
    prefix = list(grid[:hi + 1])
    kept, skipped = 0, 0
    for row in grid[hi + 1:]:
        nonempty = [str(v).strip().lower() for v in row if v not in (None, "")]
        if nonempty and all(v in ("сх", "хард") for v in nonempty):
            prefix.append(row)
            continue
        cid = _contract_id(row[ci] if ci < len(row) else None)
        if not cid:
            continue
        if cid in existing_ids:
            prefix.append(row)
            kept += 1
        else:
            skipped += 1
    out[si] = {"name": sheets[si]["name"], "grid": prefix}
    return out, {"kept": kept, "skipped_new": skipped}


def step_import(settings: dict, base: str | None, dry: bool, log,
                registry: str | None = None, allow_new: bool = False) -> str:
    """ЗАГРУЗКА: только опубликованный общий master в бота.

    Штатный источник — ОБЩИЙ реестр одним файлом: он и есть источник правды по
    закреплениям после ночного скрипта, а сопоставление колонок у сервера идёт по
    заголовкам. Personal ↔ master выполняет только registry_cycle; аварийного
    fallback personal → bot нет."""
    if not dry:
        require_legacy_write_enabled()
    if not registry:
        raise ValueError(
            "импорт personal → bot запрещён; укажи опубликованный master "
            "через --registry/--after-registry"
        )
    p = Path(registry)
    if not p.exists():
        raise FileNotFoundError(f"общий реестр не найден: {p}")
    files = [p]
    log(f"Источник — опубликованный общий master: {p.name}")
    existing_ids = None
    if registry and not allow_new:
        current = api(settings, "GET", "/admin/export.json", timeout=180)
        existing_ids = {_contract_id(r.get("contract_id"))
                        for r in current.get("rows") or [] if r.get("contract_id")}

    if dry:
        # Открываем файл по-настоящему: смысл сухого прогона в том, чтобы узнать о
        # неузнанной шапке ДО записи в прод, а не после.
        out = [f"Сухой прогон, файлов {len(files)} — запись не выполнялась"]
        for p in files:
            try:
                sheets = read_sheets(str(p))
            except Exception as e:  # noqa: BLE001
                out.append(f"  ✗ {p.name}: не читается — {e}")
                continue
            guard = None
            if existing_ids is not None:
                sheets, guard = filter_to_existing_contracts(sheets, existing_ids)
            best = max(sheets, key=lambda s: len(s["grid"]))
            named = next((s for s in sheets if s["name"] == "Портфели"), best)
            hdr = None
            for i, row in enumerate(named["grid"][:10]):
                if any(str(c or "").strip().lower().startswith("номер кд") for c in row):
                    hdr = i
                    break
            rows = max(len(named["grid"]) - (hdr + 1 if hdr is not None else 0), 0)
            line = (f"  {p.name}: лист «{named['name']}», "
                    + (f"шапка в строке {hdr + 1}, строк данных ~{rows}"
                       if hdr is not None
                       else "✗ колонка «Номер КД / ID» не найдена в первых 10 строках — "
                            "сервер откажет"))
            if guard is not None:
                line += (f"; защита БД: строк существующих КД {guard['kept']}, "
                         f"новых пропустит {guard['skipped_new']}")
            out.append(line)
        return "\n".join(out)

    agg = {"total_rows": 0, "inserted": 0, "updated": 0, "skipped_no_id": 0,
           "duplicates": 0, "protected_cells": 0, "office_cells": 0, "no_responsible": 0}
    errors: list[str] = []
    for i, p in enumerate(files, 1):
        log(f"[{i}/{len(files)}] {p.name}…")
        try:
            sheets = read_sheets(str(p))
            if existing_ids is not None:
                sheets, guard = filter_to_existing_contracts(sheets, existing_ids)
                log(f"Защита БД: оставлено строк существующих КД {guard['kept']}, "
                    f"новых пропущено {guard['skipped_new']}")
            source_kind = "published_master" if allow_new else "test_subset"
            d = api(
                settings,
                "POST",
                "/admin/import.json",
                {"source_kind": source_kind, "sheets": sheets},
                timeout=300,
            )
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
    ap = argparse.ArgumentParser(
        description="LEGACY суточный цикл бот ↔ реестры; запись заблокирована"
    )
    ap.add_argument("step", choices=["export", "import"],
                    help="export — выгрузка в реестры до ночного скрипта; "
                         "import — загрузка портфелей в бота после него")
    ap.add_argument("--base", help="базовая папка республик (по умолчанию из настроек пульта)")
    ap.add_argument(
        "--registry",
        help="опубликованный общий master — единственный источник загрузки",
    )
    ap.add_argument("--allow-new", action="store_true",
                    help="разрешить общему реестру создавать новые КД (по умолчанию "
                         "он обновляет только уже загруженную подвыборку)")
    ap.add_argument("--after-registry", help="ждать этот файл общего реестра перед стартом")
    ap.add_argument("--stable-min", type=int, default=20,
                    help="сколько минут файл должен не меняться (по умолчанию 20)")
    ap.add_argument("--timeout-min", type=int, default=300,
                    help="предел ожидания реестра, минут (по умолчанию 300)")
    ap.add_argument("--dry-run", action="store_true", help="ничего не писать, только показать план")
    ap.add_argument("--no-report", action="store_true", help="не слать итог в Телеграм")
    a = ap.parse_args(argv)

    if not a.dry_run:
        try:
            require_legacy_write_enabled()
        except LegacyWriteBlocked as exc:
            print(f"[BLOCKED] {exc}", file=sys.stderr, flush=True)
            return LEGACY_WRITE_BLOCKED_EXIT

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = Journal(STATE_DIR / f"{datetime.now():%Y-%m-%d}.log")
    log(f"=== {a.step} ===")
    try:
        settings = load_settings()
        base = a.base or settings.get("scatterBase") or None
        if a.step == "export" and not base:
            log("[!] Базовая папка не задана: ни --base, ни scatterBase в настройках пульта. "
                "Она нужна для обогащения personal-файлов.")
            return 2
        if a.step == "import" and not (a.registry or a.after_registry):
            log("[!] Импорт personal → bot запрещён. Укажи опубликованный master "
                "через --registry или --after-registry.")
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
                                      registry=a.registry or a.after_registry,
                                      allow_new=a.allow_new)
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
