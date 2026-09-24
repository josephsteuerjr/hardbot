"""Обогащение личных реестров: подбор файлов, задания, окно дат, реестр
доставленного (ledger), отчёт прогона.

Контракт с `pult/enrich.py` (координатор):

* `enrich.suggest_window(receipt_dir, today=None) -> (since, until)` — строки
  «ДД.ММ.ГГГГ», пустые если квитанций нет;
* `enrich.enrich_many(jobs, fields, log, since, until, backup_root, receipt_dir,
  ledger=None, on_delivered=None, server_authoritative=False)`;
* `enrich.replay_ledger_receipt(receipt_path, on_delivered)`;
* `enrich.last_batch_receipt(receipt_dir) -> Path | None`.

"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

import enrich
import razbros

BACKUP_FOLDER = "!! Обогащение из БД"
RECEIPTS_FOLDER = "_квитанции"

SERVER_AUTHORITATIVE_LABEL = ("Считать сервер источником для строк без истории "
                              "доставки")
SERVER_AUTHORITATIVE_WARNING = (
    "Осторожно: для договоров, по которым пульт ещё ни разу не доставлял "
    "значение в эту книгу, версия сервера будет записана поверх того, что "
    "стоит в файле, — офисная правка в такой ячейке не распознаётся и "
    "будет заменена. Включай только для первого прогона по новой книге."
)

CONFLICT_COLUMNS = (
    ("employee", "Сотрудник", 150), ("contract_id", "КД", 100),
    ("column", "Колонка", 150), ("file", "В файле", 170),
    ("server", "На сервере", 170), ("delivered", "Доставлялось", 150),
    ("visit_date", "Выезд", 90), ("kind", "Вид", 110),
)
CONFLICT_KINDS = {"no_history": "нет истории", "both_changed": "правили обе стороны",
                  "office_edit": "правка офиса"}

# Решения по конфликтам. «Оставить как в книге» — безопасный выбор по умолчанию:
# в книгу ничего не пишется, а сервер запоминает своё значение как доставленное,
# и дальше эта ячейка считается правкой офиса, а не конфликтом.
DECISION_FILE = "file"
DECISION_SERVER = "server"
DECISION_ORDER = (DECISION_FILE, DECISION_SERVER)
DECISION_LABELS = {DECISION_FILE: "оставить как в книге",
                   DECISION_SERVER: "записать сервер"}
DECISION_DEFAULT = DECISION_FILE
DECISION_HINT = ("По каждой строке выбери, что делать: оставить значение книги "
                 "(сервер его запомнит и больше не будет спорить) или записать "
                 "значение сервера поверх (с журналом вытесненного). Кнопки "
                 "«везде…» ставят один ответ на все строки; потом нажми «Записать».")


def conflict_key(c: dict) -> tuple[str, str]:
    return (str(c.get("contract_id") or ""), str(c.get("key") or ""))


def decisions_summary(decisions: dict) -> tuple[int, int]:
    """(оставить как в книге, записать сервер)."""
    values = list((decisions or {}).values())
    return values.count(DECISION_FILE), values.count(DECISION_SERVER)


def preview_kwargs(*, since: str, until: str, ledger: dict | None,
                   decisions: dict | None = None) -> dict:
    """Именованные аргументы `enrich.preview_many`."""
    return {"since": since, "until": until, "ledger": ledger,
            "decisions": dict(decisions or {})}


def preview_lines(results: list[dict]) -> list[str]:
    """Итог проверки без записи: что будет дописано, что спорно."""
    ok = [r for r in results if "error" not in r]
    bad = [r for r in results if "error" in r]
    cells = sum(r.get("cells_written") or 0 for r in ok)
    conflicts, office = collect_conflicts(results)
    resolved_file = sum(r.get("resolved_file") or 0 for r in ok)
    resolved_server = sum(r.get("resolved_server") or 0 for r in ok)
    lines = [f"Проверено без записи: файлов {len(ok)} из {len(results)} · "
             f"будет дописано ячеек: {cells}"]
    if conflicts:
        lines.append(f"Конфликтов: {len(conflicts)} — реши их в таблице ниже, "
                     "иначе эти ячейки останутся как в книге и попадут в отчёт снова.")
    else:
        lines.append("Конфликтов нет.")
    if resolved_file or resolved_server:
        lines.append(f"Решено: оставить как в книге {resolved_file}, "
                     f"записать сервер {resolved_server}.")
    if office:
        lines.append(f"Правок офиса после доставки: {len(office)} — не трогаются.")
    lines.extend(comment_cleared_lines(ok))
    for r in results:
        if not r.get("busy"):
            continue
        owner = r.get("busy_owner") or ""
        who = f" у {owner}" if owner else ""
        lines.append(f"  ⚠ {r.get('employee', '')} — {Path(r['file']).name}: книга "
                     f"открыта{who}; запись в неё не пройдёт, пока её не закроют.")
    for r in bad:
        lines.append(f"  ⚠ {r.get('employee', '')} — {Path(r['file']).name}: {r['error']}")
    return lines


# --- подбор файлов ----------------------------------------------------------
def personal_files(entries, emp) -> list[Path]:
    """Личные реестры сотрудника в его папках: ФИО.xlsb (имя из источника или
    папки), иначе первый непременной .xlsb в папке (~времянки Excel — мимо).

    Страховочные копии «(до обогащения …)» / «(до разброса …)» / «(до починки …)»
    тоже мимо: подхватить копию вместо живого файла — значит обогатить или
    импортировать данные недельной давности."""
    files: list[Path] = []
    for e in entries:
        folder = e["path"]
        cands = [folder / f"{razbros.sanitize_filename(emp)}.xlsb",
                 folder / f"{folder.name}.xlsb"]
        found = next((c for c in cands if c.exists()), None)
        if not found:
            xs = [p for p in sorted(folder.glob("*.xlsb"))
                  if not p.name.startswith("~")
                  and not razbros.is_backup_copy(p.name)]
            found = xs[0] if xs else None
        if found and found not in files:
            files.append(found)
    return files


def folder_map(base: str, roots: list[str] | None = None) -> dict:
    reps = razbros.load_config(None)
    return razbros.build_folder_map(razbros.resolve_roots(base, reps, roots or []))


def match_employees(by_emp, fmap) -> tuple[list[dict], list[str], list[str]]:
    """Сотрудники с визитами бота → (готовы, нет папки, нет файла)."""
    matched, nofolder, nofile = [], [], []
    for key, e in sorted(by_emp.items(), key=lambda kv: kv[1]["employee"].lower()):
        if not e["filled"]:
            continue        # бот ничего не заполнял — обогащать нечего
        entries = fmap.get(key)
        if not entries:
            nofolder.append(f"{e['employee']} ({e['filled']})")
            continue
        files = personal_files(entries, e["employee"])
        if not files:
            nofile.append(e["employee"])
            continue
        matched.append({"key": key, "employee": e["employee"],
                        "filled": e["filled"], "files": files,
                        "rows": e["rows"]})
    return matched, nofolder, nofile


def preview_pairs(by_emp, matched: list[dict]) -> list[tuple[str, object]]:
    pairs = [
        ("с выездами из приложения", sum(1 for e in by_emp.values() if e["filled"])),
        ("готовы к обогащению", len(matched)),
        ("завершённых визитов", sum(m["filled"] for m in matched)),
        ("файлов", sum(len(m["files"]) for m in matched))]
    orphan_filled = getattr(by_emp, "orphan_filled", 0)
    if orphan_filled:
        pairs.append(("визитов без ответственного", orphan_filled))
    return pairs


def preview_warning(by_emp, matched: list[dict], nofolder: list[str],
                    nofile: list[str]) -> str:
    warn = ""
    orphan_filled = getattr(by_emp, "orphan_filled", 0)
    if orphan_filled:
        # Главная строка отчёта: эти визиты не попадут никуда, и раньше об
        # этом не было ни слова — так потерялись 173 выезда за 09-17.07.
        sample = [c for c in getattr(by_emp, "orphan_cids", []) if c][:10]
        # Период выездов — прямо в первой фразе: счётчик идёт по всей базе, а не
        # по окну обогащения, и без периода он читается как сегодняшняя авария.
        dates = getattr(by_emp, "orphan_dates", None)
        when = ""
        if dates:
            when = (f" (выезды {dates[0]})" if dates[0] == dates[1]
                    else f" (выезды {dates[0]} — {dates[1]}, окна обогащения это не касается)")
        warn = (f"⚠ Завершённых визитов без ответственного: {orphan_filled}{when} — "
                "они НЕ попадут ни в один реестр: личного файла у таких строк "
                f"нет (всего строк без владельца: "
                f"{getattr(by_emp, 'orphan_rows', 0)}). Проставь «ответственный "
                "Сотрудник» в реестре, импортируй его — и обогащай заново."
                + ((" КД: " + ", ".join(sample)
                    + ("…" if orphan_filled > len(sample) else ""))
                   if sample else ""))
    if nofolder:
        warn += (("\n" if warn else "")
                 + "Есть данные из приложения, но нет папки: " + ", ".join(nofolder))
    if nofile:
        warn += ("\n" if warn else "") + "Папка есть, но нет .xlsb: " + ", ".join(nofile)
    if not matched and not warn:
        warn = "В приложении пока ничего не заполнено — обогащать нечего."
    return warn


def pick_note(m: dict) -> str:
    where = " · ".join(p.parent.parent.name for p in m["files"])
    return f"визитов: {m['filled']} · файлов: {len(m['files'])} · {where}"


# --- задания и окно -----------------------------------------------------------
def build_jobs(picked: list[dict]) -> list[dict]:
    return [{"employee": m["employee"], "path": p, "rows": m["rows"]}
            for m in picked for p in m["files"]]


def backup_root(base: str | None) -> str | None:
    """Куда класть резервные копии. Сорить в папках сотрудников запрещено,
    а переносить копии руками ПОСЛЕ прогона — ослеплять инструменты
    восстановления: они ищут копии рядом с живым файлом."""
    if not base:
        return None
    return str(Path(base) / BACKUP_FOLDER)


def receipt_dir(base: str | None) -> str | None:
    root = backup_root(base)
    return str(Path(root) / RECEIPTS_FOLDER) if root else None


def window_text(since: str, until: str) -> str:
    """Строка про окно для подтверждения. Без окна прогон переутверждает базу
    поверх всей истории визитов — об этом говорим прямо."""
    if since or until:
        return f"Окно выездов: с {since or '—'} по {until or '—'}."
    return ("ОКНО НЕ ЗАДАНО: в книги пойдут выезды за ВСЮ историю, "
            "включая уже обогащённые ранее. Если после прошлого прогона "
            "результат правили в книге или в реестре, правка будет "
            "заменена версией из приложения.")


def confirm_text(n_employees: int, n_files: int, since: str, until: str) -> str:
    return (f"Обогатить личные реестры: сотрудников {n_employees}, файлов {n_files}?\n\n"
            f"{window_text(since, until)}\n\n"
            "В каждый файл дописываются ТОЛЬКО кейсы его сотрудника, заполненные "
            "в приложении. Резервная копия создаётся перед записью. Делает Excel — "
            "может занять время.")


WHOLE_HISTORY_TITLE = "Вся история"
WHOLE_HISTORY_TEXT = (
    "Окно дат пустое. Прогон перепишет в книгах результаты за ВСЮ историю "
    "визитов, включая уже обогащённые ранее и правленные после этого.\n\n"
    "Обычно нужно окно с даты прошлого прогона — оно подставляется из "
    "последней квитанции автоматически.\n\n"
    "Точно обогащать за всю историю?"
)


def suggest_window(receipts: str | None) -> tuple[str, str]:
    """Предложение окна по последней квитанции. Пустые строки, если квитанций
    нет или папка не задана — тогда пульт обязан спросить «вся история»."""
    if not receipts:
        return "", ""
    try:
        since, until = enrich.suggest_window(receipts)
    except Exception:                                  # noqa: BLE001
        return "", ""
    return str(since or ""), str(until or "")


def last_receipt(receipts: str | None):
    if not receipts:
        return None
    try:
        return enrich.last_batch_receipt(receipts)
    except Exception:                                  # noqa: BLE001
        return None


def ledger_query(employees: list[str]) -> str:
    """GET /admin/enrichment/ledger.json?employees=ФИО1;ФИО2"""
    joined = ";".join(sorted({e for e in employees if e}))
    return "/admin/enrichment/ledger.json?" + urllib.parse.urlencode({"employees": joined})


def enrich_many_kwargs(*, since: str, until: str, backup_root: str | None,
                       receipt_dir: str | None, ledger: dict | None,
                       on_delivered, server_authoritative: bool,
                       decisions: dict | None = None) -> dict:
    """Именованные аргументы `enrich.enrich_many` одним словарём: `ledger` —
    ответ сервера (`{contract_id: {key: {value, at}}}`), `on_delivered` —
    колбэк, который пульт реализует как POST реестра доставленного,
    `decisions` — решения оператора по конфликтам {(КД, поле): file|server}."""
    return {"since": since, "until": until, "backup_root": backup_root,
            "receipt_dir": receipt_dir, "ledger": ledger,
            "on_delivered": on_delivered,
            "server_authoritative": bool(server_authoritative),
            "decisions": dict(decisions or {})}


NO_CHECK_NOTE = ("Проверка конфликтов для этого набора не делалась: спорные ячейки "
                 "останутся как в книге и попадут в отчёт. Чтобы решить их сейчас, "
                 "сначала нажми «Проверить конфликты».")


def confirm_decisions_note(decisions: dict | None, pending_conflicts: int) -> str:
    """Строка подтверждения про конфликты: сколько решено и как, сколько
    останется как в книге без решения."""
    keep, write = decisions_summary(decisions or {})
    parts = []
    if keep or write:
        parts.append(f"Решения по конфликтам: оставить как в книге {keep}, "
                     f"записать сервер {write}.")
    if pending_conflicts:
        parts.append(f"Без решения останутся {pending_conflicts} конфликт(ов): в книгу "
                     "они не запишутся и попадут в отчёт.")
    return "\n".join(parts)


def replay_receipt(receipt_path, on_delivered) -> dict:
    return enrich.replay_ledger_receipt(receipt_path, on_delivered)


# --- отчёт ---------------------------------------------------------------------
def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if "error" not in r]
    unver = [r for r in results if r.get("written_unverified")]
    bad = [r for r in results if "error" in r and not r.get("written_unverified")]
    return {"ok": ok, "unverified": unver, "bad": bad, "total": len(results)}


def collect_conflicts(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """Конфликты и правки офиса по всем файлам, с именем сотрудника в строке."""
    conflicts, office = [], []
    for r in results:
        emp = r.get("employee") or ""
        for c in r.get("conflicts") or []:
            conflicts.append({"employee": emp, **c})
        for c in r.get("office_edits") or []:
            office.append({"employee": emp, **c})
    return conflicts, office


def conflict_row(c: dict) -> tuple:
    return (c.get("employee", ""), c.get("contract_id", ""), c.get("column") or c.get("key", ""),
            str(c.get("file") if c.get("file") is not None else ""),
            str(c.get("server") if c.get("server") is not None else ""),
            str(c.get("delivered") if c.get("delivered") is not None else ""),
            str(c.get("visit_date") or ""),
            CONFLICT_KINDS.get(str(c.get("kind") or ""), str(c.get("kind") or "")))


def comment_cleared_lines(results: list[dict]) -> list[str]:
    """«Комментарий есть у бота, а везти нечего» — вслух, а не молчанием.

    Обогащение возит ЖИВОЕ значение комментария, и пустое в план не попадает.
    Если живое поле очистили поверх доказанного визита (так вышло 10.09.2026
    после загрузки реестра с заменой комментариев), прогон отрабатывал «успешно»
    и просто не привозил текст. Теперь такие кейсы названы поимённо: чинятся они
    на сервере, а не в книге.
    """
    hits = [(r.get("employee") or "", cid)
            for r in results
            for cid in (r.get("comment_cleared_cids") or [])]
    total = sum(int(r.get("comment_cleared") or 0) for r in results)
    if not total:
        return []
    lines = [f"КОММЕНТАРИЙ НЕ ПОЕДЕТ ({total}): у кейса есть комментарий выезда, "
             "а живое поле сервера пусто — его очистили поверх визита. "
             "Лечится на сервере, книга тут ни при чём."]
    for emp, cid in hits[:12]:
        lines.append(f"  {emp} · КД {cid}")
    if total > len(hits[:12]):
        lines.append(f"  … и ещё {total - len(hits[:12])}")
    return lines


def report_lines(results: list[dict]) -> list[str]:
    s = summarize(results)
    ok, unver, bad = s["ok"], s["unverified"], s["bad"]
    cells = sum(r.get("cells_written") or 0 for r in ok)
    over = sum(r.get("cells_over_nonempty") or 0 for r in ok)
    missing = sum(len(r.get("missing") or []) for r in ok)
    skipped = sum(r.get("out_of_window") or 0 for r in ok)
    tech = sum(r.get("technical_overwrites") or 0 for r in ok)
    delivered_upd = sum(r.get("cells_updated_delivered") or 0 for r in ok)
    lines = [f"Готово: файлов {len(ok)} из {len(results)} · дописано ячеек: {cells}"
             + (f" (поверх старых: {over})" if over else "")
             + (f" · кейсов не нашлось в файлах: {missing}" if missing else "")
             + (f" · вне окна дат пропущено: {skipped}" if skipped else "")
             + (f" · поверх «Не обработан»: {tech}" if tech else "")
             + (f" · обновлено доставленных ранее: {delivered_upd}" if delivered_upd else "")]
    for r in ok[:15]:
        lines.append(f"  {r['employee']} — {Path(r['file']).name}: "
                     f"ячеек {r.get('cells_written')}, кейсов {r.get('changed_cases')}")
    if len(ok) > 15:
        lines.append(f"  … и ещё {len(ok) - 15} файлов")
    # Что именно вытеснили — поимённо, а не одним счётчиком.
    displaced = [(r.get("employee"), d)
                 for r in results for d in (r.get("displaced") or [])]
    if displaced:
        lines.append(f"ЗАМЕНЕНО ПОВЕРХ ЧУЖИХ ЗНАЧЕНИЙ ({len(displaced)}):")
        for emp, d in displaced[:12]:
            lines.append(f"  {emp} · КД {d['contract_id']} · {d['column']}: "
                         f"«{d['was']}» → «{d['now']}» (выезд {d['visit_date']})")
        if len(displaced) > 12:
            lines.append(f"  … и ещё {len(displaced) - 12}; полный список в квитанции")
    lines.extend(comment_cleared_lines(ok))
    conflicts, office = collect_conflicts(results)
    resolved_file = sum(r.get("resolved_file") or 0 for r in ok)
    resolved_server = sum(r.get("resolved_server") or 0 for r in ok)
    if resolved_file or resolved_server:
        lines.append(f"КОНФЛИКТЫ РЕШЕНЫ: оставлено как в книге {resolved_file}, "
                     f"записан сервер {resolved_server}.")
    if conflicts:
        lines.append(f"КОНФЛИКТЫ ({len(conflicts)}): значение в книге отличается и от "
                     "сервера, и от доставленного ранее — в книгу не записано, "
                     "разобрать руками (таблица ниже).")
    if office:
        lines.append(f"ПРАВКИ ОФИСА ({len(office)}): в книге правили после доставки, "
                     "сервер не менял — оставлено как в книге (таблица ниже).")
    for r in unver:
        lines.append(f"  ⚠ {r['employee']} — {Path(r['file']).name}: записано, "
                     f"но проверить не удалось. НЕ откатывать эту книгу.")
    for r in bad:
        lines.append(f"  ⚠ {r['employee']} — {Path(r['file']).name}: {r['error']}")
    n_bak = sum(1 for r in ok if r.get("backup"))
    receipt = next((r.get("receipt") for r in results if r.get("receipt")), None)
    rec_err = next((r.get("receipt_error") for r in results if r.get("receipt_error")), None)
    if n_bak:
        lines.append(f"Резервные копии: {n_bak}.")
    if receipt:
        lines.append(f"Квитанция: {receipt}")
    elif rec_err:
        lines.append(f"⚠ КВИТАНЦИЯ НЕ ЗАПИСАНА: {rec_err}. "
                     f"Пути и хэши копий нигде не сохранены.")
    recorded = sum(int(r.get("ledger_recorded") or 0) for r in results)
    if recorded:
        lines.append(f"Реестр доставленного на сервере: записано {recorded}.")
    return lines


def ledger_error(results: list[dict]) -> str:
    """Красная строка про недоставленный реестр — или пусто."""
    err = next((r.get("ledger_error") for r in results if r.get("ledger_error")), None)
    if not err:
        return ""
    return (f"⚠ РЕЕСТР ДОСТАВЛЕННОГО НЕ ОТПРАВЛЕН НА СЕРВЕР: {err}. Книги "
            "обогащены, но сервер не знает, что именно доставлено — следующий "
            "прогон сочтёт эти ячейки правками офиса. Нажми «Повторить отправку "
            "из квитанции».")


def toast_text(results: list[dict]) -> tuple[str, bool]:
    s = summarize(results)
    bad = s["bad"]
    return (f"Обогащено файлов: {len(s['ok'])}" + (f", ошибок: {len(bad)}" if bad else ""),
            bool(bad))


def single_file_report(p: dict) -> str:
    """Отчёт запасного пути «обогатить один файл» (`enrich.enrich_file`)."""
    parts = [
        f"Кейсов с данными из приложения: {p['filled_cases']}",
        f"найдено в файле: {p['matched_cases']}",
        f"дописано ячеек: {p['cells_written']}"
        + (f" (поверх старых значений: {p['cells_over_nonempty']})"
           if p["cells_over_nonempty"] else ""),
        f"уже совпадало: {p['cells_already']}",
    ]
    text = " · ".join(parts)
    text += (f"\nРезервная копия: {p['backup']}" if p.get("backup")
             else "\nФайл не изменился — резервная копия не создавалась.")
    if p["missing"]:
        text += f"\n⚠ Заполнены в приложении, но не найдены в файле ({len(p['missing'])}): " \
                + ", ".join(p["missing"][:20]) + ("…" if len(p["missing"]) > 20 else "")
    if p["visit_cols_absent"]:
        text += "\n⚠ В файле нет колонок: " + ", ".join(p["visit_cols_absent"])
    return text
