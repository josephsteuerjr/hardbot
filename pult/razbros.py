"""
Legacy-разброс кейсов по личным реестрам сотрудников.

Режим записи отключён 29.07.2026. Он сохраняется только для read-only
предпросмотра папок и как исторический код миграции. Полный рабочий цикл теперь
выполняет `registry_cycle`: snapshot → plan → staging → validation → publish.

Что делает: берёт ОДИН файл-источник (экспорт кейсов, формат «Хард») и раскладывает
его строки по личным .xlsb сотрудников — каждому достаются только его строки (по колонке
«ответственный Сотрудник»). Личный файл сотрудника при этом актуализируется: портфель
перезаписывается свежими строками, а его собственный лист «Статистика» (формулы) —
переносится из старой версии. Формат .xlsb, лист «Статусы» (выпадающие списки) и
косметика строк сохраняются, потому что раскладку делает живой Excel (xlwings/COM).

Отличия от старой «Разборки»:
  * НЕТ мастер-реестра и деления колонок на хард/софт — оба реестра формата «Хард»,
    поэтому шаблон = сам источник (все колонки целиком, ничего не режем);
  * папки сотрудников ищем ПО ИЗВЕСТНОЙ МАСКЕ, а не сканируя всё дерево сетевого диска:
        <база>/<Республика>/<subpath из json>/<Город|Soft>/<ФИО>/<ФИО>.xlsb
    — только два известных уровня под путём из конфига (`!Архив`/`_tech` игнорируются);
  * список республик берётся из json (или встроенный дефолт) — без жёсткого ограничения
    на их число/названия; можно выбрать базовую папку, в которой лежат обе республики;
  * можно раскладывать НЕ всем, а поимённо выбранным сотрудникам (`--only`, копится список).

Тяжёлые импорты (pandas/calamine — чтение источника; xlwings — раскладка) ленивые:
карту папок и превью совпадений можно считать без Excel и без pandas (и в тестах).

CLI (вывод — JSON в stdout, диагностика — в stderr):
  python razbros.py --dry-run --base "<папка с республиками>" --source "<экспорт.xlsb>"
  python razbros.py --apply   --base "<...>" --source "<...>" --only "Иванов Иван" --only "Петров Пётр"
  python razbros.py --dry-run --root "<...прямой путь к портфелям>"   # без базы/конфига
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SHEET_NAME = "Портфели"
ALLOWED_SHEETS = {"Портфели", "Статусы", "Статистика"}
HEADER_ROW_NAMES = 2          # строка с названиями колонок (1-based)
DATA_START_ROW = 4            # первая строка данных (после блоков/заголовков/типов)
TARGET_COLUMN_NAME = "ответственный Сотрудник"
# Офис вправе переименовать колонку в общем реестре и в сентябре 2026 так и
# сделал: «ответственный Сотрудник» → «ответственный Сотрудник ХАРД» (парная
# колонка СОФТ — это колл-центр, не наш). Ищем колонку по всем известным
# написаниям, чтобы разбор файла не останавливался на переименовании.
TARGET_COLUMN_ALIASES = ("ответственный Сотрудник ХАРД",)


def _target_column_index(headers: list) -> int:
    """Индекс колонки ответственного среди уже нормализованных заголовков."""
    for title in (TARGET_COLUMN_NAME, *TARGET_COLUMN_ALIASES):
        want = _norm(title)
        if want in headers:
            return headers.index(want)
    return -1
LEGACY_SCATTER_ENABLED = False
LEGACY_SCATTER_DISABLED_MESSAGE = (
    "Legacy-разброс отключён: он пересобирает личные книги из полного экспорта "
    "и может стереть локальные данные. Используйте «Контур · Реестры»."
)


class LegacyScatterDisabled(RuntimeError):
    """Попытка запустить опасный до-baseline путь записи."""

# Республики по умолчанию (маска Республика/subpath). Это ДЕФОЛТ, а не ограничение:
# json-конфиг его полностью заменяет; число/названия республик не зашиты.
DEFAULT_REPUBLICS = [
    {"republic": "Таджикистан", "subpath": "Портфели отдела взыскания РТ"},
    {"republic": "Узбекистан", "subpath": "Портфели сотрудников УЗБ"},
]
CONFIG_NAME = "конфигурация путей.json"

# Служебные папки — НИКОГДА не город и не сотрудник (`!Архив` — уволенные/неактивные).
SKIP_DIR_NAMES = {"_tech"}


def _is_skip_dir(name: str) -> bool:
    n = name.strip().lower()
    return n in SKIP_DIR_NAMES or n.startswith("!")   # !Архив, !что-угодно


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def _norm(s: str) -> str:
    """Нормализация для сравнения заголовков/имён: неразрывники→пробел, схлоп пробелов, lower."""
    return re.sub(r"\s+", " ", str(s).replace(" ", " ").strip()).lower()


_BACKUP_RE = re.compile(r"\s\(до\s[^)]*\)\s*$")


def is_backup_copy(name: str) -> bool:
    """Страховочная копия рядом с живым реестром: «ФИО (до обогащения 13.07 14-05).xlsb»,
    «(до разброса …)», «(до починки …)».

    Такие файлы нельзя принимать за личный реестр сотрудника: обогатить или
    импортировать СТАРУЮ копию вместо живого файла — значит вернуть в работу данные
    недельной давности. Раньше отсеивались только времянки Excel («~…»), а копии
    подходили под маску «первый .xlsb в папке»."""
    stem = str(name or "")
    if stem.lower().endswith(".xlsb") or stem.lower().endswith(".xlsx"):
        stem = stem.rsplit(".", 1)[0]
    return bool(_BACKUP_RE.search(stem))


KEEP_BACKUPS = 5      # сколько копий «(до разброса …)» держать на сотрудника


def _rotate_backups(folder, stem: str, log=lambda *a: None, keep: int = KEEP_BACKUPS):
    """Оставить только последние ``keep`` копий «(до разброса …)» этого сотрудника.

    Личный реестр весит мегабайты и лежит на сетевом диске: копия за каждый прогон и
    ежедневный разброс — это гигабайты в год в чужой папке. Удаляем только то, чьё имя
    полностью разобралось как наша копия; при любом сомнении файл остаётся."""
    import re as _re
    pat = _re.compile(r"^" + _re.escape(stem) + r" \(до разброса "
                      r"(\d{2})\.(\d{2})(?:\.(\d{2}))? (\d{2})-(\d{2})"
                      r"(?:, (\d+))?\)\.xlsb$")
    found = []
    try:
        names = sorted(p for p in folder.glob("*.xlsb"))
    except OSError:
        return
    for p in names:
        m = pat.match(p.name)
        if m:
            dd, mm, yy, hh, mi, dup = m.groups()
            # Сортируем по РАЗОБРАННОЙ дате: сравнение строк «ДД.ММ» на стыке месяцев
            # ставило 31.12 выше 01.01 и удаляло самые свежие копии.
            key = (int(yy or 0), int(mm), int(dd), int(hh), int(mi), int(dup or 1))
            found.append((key, p))
    if len(found) <= keep:
        return
    found.sort(key=lambda x: x[0])             # по разобранному штампу, не по mtime
    for _key, p in found[:-keep]:
        try:
            os.remove(p)
            log(f"[i] Старая копия удалена: {p.name}")
        except OSError as e:
            log(f"[!] Не удалить старую копию {p.name}: {e}")


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", str(name).strip())


def col_letter(col_idx: int) -> str:
    """Буквенное имя столбца Excel по 1-based индексу."""
    out = ""
    while col_idx > 0:
        col_idx, rem = divmod(col_idx - 1, 26)
        out = chr(65 + rem) + out
    return out


# --- Конфиг республик (лениво-терпимый к комментариям/легаси-формату) --------
def _strip_jsonc(text: str) -> str:
    """Убрать // и /* */ комментарии и висячие запятые — Егор правит json «на глаз»."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n\r]*", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def load_config(config_path: str | None) -> list[dict]:
    """Список {republic, subpath}. Порядок: явный --config → рядом со скриптом → дефолт.
    Понимает и новый формат {"republics":[{republic,subpath}]}, и легаси
    {"republicRoots":[{republic,path}]} (subpath = хвост path после республики)."""
    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    here = Path(__file__).resolve().parent
    candidates += [here / CONFIG_NAME, here.parent / CONFIG_NAME]

    for p in candidates:
        try:
            if not p or not p.exists():
                continue
            data = json.loads(_strip_jsonc(p.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            log(f"[!] Конфиг {p} не прочитан ({e}) — пробую следующий")
            continue
        reps = _config_to_republics(data)
        if reps:
            log(f"[i] Республики из {p}: {[r['republic'] for r in reps]}")
            return reps
    log("[i] Конфиг не найден — встроенный дефолт республик")
    return list(DEFAULT_REPUBLICS)


def _config_to_republics(data: dict) -> list[dict]:
    out = []
    if isinstance(data, dict) and isinstance(data.get("republics"), list):
        for r in data["republics"]:
            rep = str(r.get("republic", "")).strip()
            sub = str(r.get("subpath", "")).strip().strip("\\/")
            if rep and sub:
                out.append({"republic": rep, "subpath": sub})
    elif isinstance(data, dict) and isinstance(data.get("republicRoots"), list):
        for r in data["republicRoots"]:               # легаси: republic + полный path
            rep = str(r.get("republic", "")).strip()
            path = str(r.get("path", "")).replace("\\", "/").strip().strip("/")
            sub = ""
            if rep and rep in path:                    # хвост после названия республики
                sub = path.split(rep, 1)[1].strip("/")
            elif path:
                sub = path.rsplit("/", 1)[-1]
            if rep and sub:
                out.append({"republic": rep, "subpath": sub})
    return out


# --- Разрешение корневых папок портфелей по маске ---------------------------
def resolve_roots(base: str | None, republics: list[dict],
                  explicit_roots: list[str]) -> list[tuple[str, Path]]:
    """(республика, путь_к_портфелям). Из базы — по маске base/Республика/subpath;
    если ни один не нашёлся, но база задана — считаем саму базу прямым корнем портфелей.
    Плюс любые --root как есть (без метки республики). Всё дерево НЕ сканируем."""
    roots: list[tuple[str, Path]] = []
    if base:
        b = Path(base)
        derived = [(rep["republic"], b / rep["republic"] / rep["subpath"]) for rep in republics]
        existing = [(name, p) for name, p in derived if p.exists()]
        if existing:
            roots += existing
        else:
            log(f"[i] Под базой нет папок республик — беру саму базу как корень портфелей: {b}")
            roots.append(("", b))
    for r in explicit_roots:
        roots.append(("", Path(r)))
    return roots


# --- Карта папок сотрудников (чистый Python, без Excel/pandas) --------------
def build_folder_map(roots: list[tuple[str, Path]]) -> dict[str, list[dict]]:
    """{ФИО.lower: [ {path, republic, city, kind} ]} по маске root/<Город|Soft>/<ФИО>.
    Ровно два уровня вниз — это и есть маска, а не обход дерева. `!Архив`/`_tech` — мимо.
    Одно ФИО может встретиться в нескольких местах (город + Soft + разные республики)."""
    from registry_cycle.portfolio_paths import SECONDARY_CONTAINERS, secondary_folders
    folder_map: dict[str, list[dict]] = {}
    for republic, root in roots:
        if not root.exists():
            log(f"[!] Корень недоступен, пропускаю: {root}")
            continue
        for city in root.iterdir():                       # уровень 1: город / Soft / служебное
            if not city.is_dir() or _is_skip_dir(city.name):
                continue
            is_soft = city.name.strip().lower() == "soft"
            for emp in city.iterdir():                     # уровень 2: папка сотрудника
                if not emp.is_dir() or _is_skip_dir(emp.name):
                    continue
                if not is_soft and emp.name.casefold() in SECONDARY_CONTAINERS:
                    for person in secondary_folders(root, emp):
                        folder_map.setdefault(person.name.strip().lower(), []).append({
                            "path": person, "republic": republic,
                            "city": city.name, "kind": "hard",
                        })
                    continue
                folder_map.setdefault(emp.name.strip().lower(), []).append({
                    "path": emp, "republic": republic,
                    "city": city.name, "kind": "soft" if is_soft else "hard",
                })
    return folder_map


# --- Чтение источника (ленивый pandas+calamine): только список сотрудников ---
def read_source_employees(source: Path) -> list[str]:
    """Уникальные непустые «ответственный Сотрудник» из файла-экспорта (формат Хард).
    Лист «Портфели»; если его нет — первый лист. Матч заголовка нормализованный."""
    import pandas as pd  # ленивый импорт: карта папок/превью работают без pandas

    def _read(sheet):
        try:
            return pd.read_excel(source, sheet_name=sheet, header=None, engine="calamine")
        except ImportError:   # python-calamine не установлен — штатный движок pandas
            return pd.read_excel(source, sheet_name=sheet, header=None)

    try:
        df = _read(SHEET_NAME)
    except Exception:                                     # нет листа «Портфели» — берём первый
        df = _read(0)

    if len(df) < HEADER_ROW_NAMES:
        raise ValueError("В источнике нет строки заголовков")
    headers = [_norm(v) if pd.notna(v) else "" for v in df.iloc[HEADER_ROW_NAMES - 1]]
    tgt = _target_column_index(headers)
    if tgt < 0:
        raise ValueError(f"В источнике (строка {HEADER_ROW_NAMES}) нет колонки «{TARGET_COLUMN_NAME}»")
    seen, employees = set(), []
    for v in df.iloc[DATA_START_ROW - 1:, tgt]:
        name = str(v).strip()
        if not name or name.lower() in ("nan", "none"):
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            employees.append(name)
    return sorted(employees)


# --- Отчёт-превью «кто нашёлся / кто нет» -----------------------------------
def make_report(folder_map: dict[str, list[dict]], employees: list[str]) -> dict:
    matched, unmatched = [], []
    for emp in employees:
        entries = folder_map.get(emp.strip().lower())
        if entries:
            matched.append({
                "employee": emp,
                "folders": [str(e["path"]) for e in entries],
                "republics": sorted({e["republic"] for e in entries if e["republic"]}),
                "cities": sorted({e["city"] for e in entries}),
                "kind": entries[0]["kind"],
            })
        else:
            unmatched.append(emp)                         # есть в источнике, но папки нет
    used = {emp.strip().lower() for emp in employees}
    extra = sorted({k for k in folder_map if k not in used})   # папка есть, в источнике нет
    return {
        "ok": True,
        "employees_total": len(employees),
        "matched": matched,
        "unmatched": unmatched,
        "extra_folders": [folder_map[k][0]["path"].name for k in extra],
        "counts": {"matched": len(matched), "unmatched": len(unmatched), "extra": len(extra)},
    }


# --- Реальная раскладка через Excel (ленивый xlwings/COM) -------------------
def apply_scatter(source: Path, folder_map: dict[str, list[dict]],
                  employees: list[str], only: set[str] | None) -> dict:
    """Один шаблон = сам источник (все колонки, только лишние листы отрезаны) → копии в
    папки сотрудников → фильтр строк по «ответственный Сотрудник» + перенос личного листа
    «Статистика» из старого файла (формулы + починка ссылок). Требует Excel."""
    if not LEGACY_SCATTER_ENABLED:
        raise LegacyScatterDisabled(LEGACY_SCATTER_DISABLED_MESSAGE)
    import shutil
    import time
    import xlwings as xw

    def open_excel():
        """Битый кэш COM-обёрток pywin32 (gen_py) валит запуск Excel
        («…no attribute 'CLSIDToPackageMap'») — чистим и повторяем."""
        try:
            return xw.App(visible=False)
        except AttributeError as e:
            if "gen_py" not in repr(e) and "CLSIDToPackageMap" not in repr(e):
                raise
            import sys
            import tempfile
            try:
                import win32com
                cache = Path(win32com.__gen_path__)
            except Exception:  # noqa: BLE001
                cache = Path(tempfile.gettempdir()) / "gen_py" / \
                    f"{sys.version_info.major}.{sys.version_info.minor}"
            shutil.rmtree(cache, ignore_errors=True)
            for m in [m for m in sys.modules if "gen_py" in m]:
                sys.modules.pop(m, None)
            log("[i] Кэш COM-обёрток Excel сброшен — повторяю запуск")
            return xw.App(visible=False)

    tmp_tpl = source.parent / "~temp_razbros.xlsb"

    def build_template(app):
        shutil.copy2(source, tmp_tpl)
        wb = app.books.open(str(tmp_tpl))
        for sh in list(wb.sheets):
            if sh.name not in ALLOWED_SHEETS:
                sh.delete()
        wb.save(); wb.close()

    written, skipped = [], []
    with open_excel() as app:
        app.screen_updating = False
        app.calculation = "manual"
        app.display_alerts = False
        build_template(app)

        jobs = []
        for emp in employees:
            key = str(emp).strip().lower()
            if only is not None and key not in only:
                continue                                  # поимённый режим — только выбранные
            if key not in folder_map:
                skipped.append(emp)                       # нет папки — в отчёт, не молча
                continue
            safe = sanitize_filename(emp)
            for entry in folder_map[key]:
                folder = entry["path"]
                out = folder / f"{safe}.xlsb"
                old = None
                if out.exists():
                    old = folder / f"~old_{safe}.xlsb"
                    if old.exists():
                        os.remove(old)
                    os.rename(out, old)
                shutil.copy2(tmp_tpl, out)
                jobs.append((out, str(emp).strip(), old))

        for out, emp_name, old in jobs:
            _transfer_stats_and_filter(app, out, emp_name, old)
            written.append(str(out))
            time.sleep(0.05)

    if tmp_tpl.exists():
        os.remove(tmp_tpl)
    return {"ok": True, "written": written, "written_count": len(written), "skipped": skipped}


def _transfer_stats_and_filter(app, file_path: Path, emp_name: str, old_path):
    """Перенос личной «Статистики» из старого файла (формулы + починка ссылок) и фильтр
    строк портфеля по emp_name. Логика 1:1 с исходной «Разборкой»."""
    if old_path and Path(old_path).exists():
        try:
            wb_old = app.books.open(str(old_path))
            if "Статистика" in [s.name for s in wb_old.sheets]:
                wb_new = app.books.open(str(file_path))
                so, sn = wb_old.sheets["Статистика"], wb_new.sheets["Статистика"]
                rows = so.api.UsedRange.Rows.Count
                cols = so.api.UsedRange.Columns.Count
                if rows > 0 and cols > 0:
                    addr = f"A1:{col_letter(cols)}{rows}"
                    formulas = so.range(addr).api.FormulaLocal
                    sn.clear_contents()
                    sn.range(addr).api.FormulaLocal = formulas
                    name = Path(old_path).name
                    sn.api.Cells.Replace(What=f"[{name}]", Replacement="", LookAt=2, SearchOrder=1, MatchCase=False)
                    sn.api.Cells.Replace(What=f"{name}!", Replacement="", LookAt=2, SearchOrder=1, MatchCase=False)
                wb_new.save(); wb_new.close()
            wb_old.close()
        except Exception as e:  # noqa: BLE001
            log(f"[!] Статистика не перенесена для {emp_name}: {e}")
            try:
                wb_old.close()
            except Exception:
                pass
        finally:
            import time
            time.sleep(0.1)
            # Прежний личный файл НЕ удаляем: разброс пересобирает портфель из экспорта,
            # и всё, что сотрудник вписал руками и чего нет в источнике, живёт только
            # здесь. Раньше эта копия стиралась — и восстанавливать пропавшие
            # комментарии/доп. номера приходилось из теневых копий сети.
            if Path(old_path).exists():
                from datetime import datetime as _dt
                stamp = _dt.now().strftime("%d.%m.%y %H-%M")
                base = Path(old_path).name[len("~old_"):-len(".xlsb")]
                folder = Path(old_path).parent
                # Счётчик коллизий — ВНУТРИ скобок: «(до разброса 27.07 14-05, 2)».
                # Снаружи он ломал бы распознавание копии (is_backup_copy смотрит на
                # хвост имени), и такой файл потом приняли бы за живой реестр.
                keep = folder / f"{base} (до разброса {stamp}).xlsb"
                n = 1
                while keep.exists():
                    n += 1
                    keep = folder / f"{base} (до разброса {stamp}, {n}).xlsb"
                try:
                    os.replace(old_path, keep)
                    log(f"[i] Прежний файл сохранён: {keep.name}")
                    _rotate_backups(folder, base, log)
                except OSError as e:
                    log(f"[!] Не сохранить прежний {Path(old_path).name}: {e}")

    wb = app.books.open(str(file_path))
    ws = wb.sheets[SHEET_NAME]
    headers = ws.range(f"{HEADER_ROW_NAMES}:{HEADER_ROW_NAMES}").value
    normalized = [_norm(v) if v is not None else "" for v in headers]
    found = _target_column_index(normalized)
    if found < 0:
        wb.save(); wb.close(); return
    tgt = found + 1
    last_row = ws.api.UsedRange.Rows.Count
    max_col = ws.api.UsedRange.Columns.Count
    if last_row >= DATA_START_ROW:
        max_letter = col_letter(max_col)
        vals = ws.range(f"A{DATA_START_ROW}:{max_letter}{last_row}").value
        if not isinstance(vals, list):
            vals = [[vals]]
        keep = [r for r in vals if len(r) >= tgt and str(r[tgt - 1]).strip() == emp_name]
        ws.range(f"A{DATA_START_ROW}:{max_letter}{last_row}").clear_contents()
        if keep:
            ws.range(f"A{DATA_START_ROW}").value = keep
            first_empty = DATA_START_ROW + len(keep)
            if last_row >= first_empty:
                ws.range(f"{first_empty}:{last_row}").api.Delete()
        else:
            ws.range(f"{DATA_START_ROW}:{last_row}").api.Delete()
            first_empty = DATA_START_ROW
        _ = ws.api.UsedRange                              # освежить кэш UsedRange
        ws.range(f"{DATA_START_ROW}:{first_empty}").api.WrapText = False
        ws.range(f"{DATA_START_ROW}:{first_empty}").row_height = 15
    wb.save(); wb.close()


# --- CLI --------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Разброс кейсов по личным реестрам сотрудников")
    ap.add_argument("--base", help="базовая папка (в ней папки республик)")
    ap.add_argument("--root", action="append", default=[], dest="roots",
                    help="прямой путь к папке портфелей (в обход базы/конфига; можно несколько)")
    ap.add_argument("--config", help="путь к json с республиками (по умолч. рядом со скриптом)")
    ap.add_argument("--source", help="файл-источник (экспорт кейсов, формат Хард)")
    ap.add_argument("--only", action="append", default=[], dest="only",
                    help="раскладывать только этому ФИО (можно несколько; пусто = всем)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только отчёт-превью, без записи")
    mode.add_argument("--apply", action="store_true", help="раскладка через Excel")
    args = ap.parse_args(argv)

    republics = load_config(args.config)
    roots = resolve_roots(args.base, republics, args.roots)
    if not roots:
        print(json.dumps({"ok": False, "error": "не задана база (--base) или корень (--root)"},
                         ensure_ascii=False))
        return 2
    folder_map = build_folder_map(roots)

    if not args.source:                                   # без источника — только карта папок
        report = {"ok": True, "source": None,
                  "folders_indexed": sum(len(v) for v in folder_map.values()),
                  "employees_with_folder": sorted(folder_map.keys()),
                  "republics": [r["republic"] for r in republics]}
        print(json.dumps(report, ensure_ascii=False))
        return 0

    source = Path(args.source)
    if not source.exists():
        print(json.dumps({"ok": False, "error": f"источник не найден: {source}"}, ensure_ascii=False))
        return 2

    try:
        employees = read_source_employees(source)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"не прочитал источник: {e}"}, ensure_ascii=False))
        return 2

    only = {o.strip().lower() for o in args.only} or None
    if args.apply:
        result = apply_scatter(source, folder_map, employees, only)
    else:
        result = make_report(folder_map, employees)       # dry-run по умолчанию
        result["source"] = str(source)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
