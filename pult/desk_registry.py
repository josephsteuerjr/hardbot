"""«Реестр» пульта аналитика на движке Hélène: колонки реестра, импорт,
обогащение личных книг живым Excel, выгрузки, отозванные.

Логика — та же, что у Qt-пульта (`pult/ui/registry.py`, `core/import_flow.py`),
перенесена сюда без окна: состояние выбора живёт в этом процессе, окно зовёт
маршруты `/reg/*`, `/enr/*`, `/exp/*`. Долгое (чтение книги, предпросмотр,
импорт, Excel) идёт задачей: окно спрашивает `/job` раз в секунду и показывает
журнал по ходу.
"""

from __future__ import annotations

import threading
from pathlib import Path

from pult.core import bootstrap

bootstrap.ensure_paths()

import enrich  # noqa: E402 — модуль верхнего уровня pult/, путь выше
from pult.core import enrichment as en  # noqa: E402
from pult.core import header_map as hm  # noqa: E402
import exchange_contract as _xc  # noqa: E402 — контракт обмена (server/)
from pult.core import exchange_state as xs  # noqa: E402
from pult.core import registry_import as ri  # noqa: E402
from pult.core.constants import (  # noqa: E402
    LIMITED_TEST_ENRICH_DISABLED_MESSAGE,
    LIMITED_TEST_ENRICH_WRITES_ENABLED,
    LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE,
    LIMITED_TEST_MASTER_IMPORT_UI_ENABLED,
)
from pult.core.dashboard import period_text  # noqa: E402
from pult.core.export import (export_confirm_text, export_request,  # noqa: E402
                              write_markers_xlsx, write_registry_xlsx)
from pult.core.text import fmt_n  # noqa: E402

_lock = threading.RLock()
def _empty_state() -> dict:
    return {
    "col_path": "", "col_analysis": None,
    "book": None, "preview": None,
    "enr_fields": None, "enr_matched": [], "enr_check": None, "enr_decisions": {},
    "exp_data": None, "exp_request": None, "last_receipt": None,
    }


S: dict = _empty_state()


def reset_context() -> None:
    """Предпросмотр, решения и выгрузки относятся только к своему контуру."""
    with _lock:
        S.clear()
        S.update(_empty_state())


def route(path: str, body: dict, *, admin, start_job, settings):
    h = _ROUTES.get(path)
    if h is None:
        raise KeyError(f"нет такого маршрута: {path}")
    return h(body, admin=admin, start_job=start_job, settings=settings)


# --- колонки реестра ---------------------------------------------------------------
def _columns_view() -> dict:
    a = S["col_analysis"] or {}
    return {"caption": ri.columns_caption(S["col_path"], a) if S["col_path"] else "",
            "state": ri.columns_state_text(a) if a else "",
            "rows": hm.decisions_from_map(a.get("unknown") or []),
            "options": hm.decision_options(a.get("missing") or []),
            "summary": hm.map_summary(hm.current_map())}


def columns_reload(body, *, admin, **_):
    try:
        hm.remember(admin("GET", "/admin/registry_columns.json"))
    except RuntimeError as exc:
        return {"summary": f"Карта колонок недоступна: {exc}"}
    return _columns_view()


def columns_check(body, *, admin, start_job, **_):
    path = str(body.get("path") or "")

    def work(say):
        say("Читаю шапку " + Path(path).name + "…")
        hm.remember(admin("GET", "/admin/registry_columns.json"))
        S["col_path"] = path
        S["col_analysis"] = hm.analyze(hm.read_header_grid(path))
        return _columns_view()
    return {"job": start_job("columns", work)}


def columns_save(body, *, admin, **_):
    if not S["col_analysis"]:
        raise RuntimeError("Сначала проверь книгу")
    new_map = hm.apply_decisions(hm.current_map(), list(body.get("decisions") or []))
    answer = admin("POST", "/admin/registry_columns.json", {"map": new_map})
    if isinstance(answer, dict) and answer.get("error"):
        raise RuntimeError(answer["error"])
    hm.update_map(answer.get("map"))
    if S["col_path"]:
        S["col_analysis"] = hm.analyze(hm.read_header_grid(S["col_path"]))
    return _columns_view()


# --- импорт --------------------------------------------------------------------------
def _book_view(selected=None) -> dict:
    book = S["book"] or {}
    sel = set(selected if selected is not None else book.get("_selected") or [])
    return {"caption": ri.file_caption(book) if book else "",
            "profile": book.get("import_profile", "hard"),
            "employees": [{"key": e["key"], "name": e["name"], "note": ri.employee_note(e),
                           "file_count": e.get("file_count", 0), "db_count": e.get("db_count", 0),
                           "in_db": bool(e.get("db_count"))}
                          for e in book.get("employees") or []],
            "selected": sorted(sel)}


def imp_pick(body, *, admin, start_job, settings, **_):
    path = str(body.get("path") or "")

    def work(say):
        say("Читаю " + Path(path).name + "…")
        try:
            hm.remember(admin("GET", "/admin/registry_columns.json"))
        except Exception:  # noqa: BLE001 — старый сервер: читаем по контракту
            pass
        book = ri.read_registry(path)
        stats = admin("GET", "/admin/stats")
        book["db_employees"] = list(stats.get("employees") or [])
        book["db_soft_employees"] = list(stats.get("soft_employees") or [])
        profile = ri.import_preferences(settings).get("profile", "hard")
        try:
            ri.select_profile(book, profile, book["db_employees"], book["db_soft_employees"])
        except ValueError:
            ri.select_profile(book, "hard", book["db_employees"], book["db_soft_employees"])
        head, first, c_id, c_who = ri.grid_layout(book["grid"])
        book.update(head=head, first=first, c_id=c_id, c_who=c_who)
        ri.restore_import_selection(book, settings)
        with _lock:
            S["book"], S["preview"] = book, None
        return _book_view()
    return {"job": start_job("pick", work)}


def imp_selection(body, *, settings, **_):
    """«снять / есть в БД / все» и подпись кнопки предпросмотра."""
    book = S["book"] or {}
    if body.get("profile") and body["profile"] != book.get("import_profile", "hard"):
        ri.select_profile(book, body["profile"], book.get("db_employees"), book.get("db_soft_employees"))
        ri.restore_import_selection(book, settings)
        ri.remember_import_selection(book, book["_selected"], settings)
        settings.save()
        S["preview"] = None
        return _book_view()
    mode = body.get("mode")
    if mode == "all":
        sel = {e["key"] for e in book.get("employees") or []}
    elif mode == "none":
        sel = set()
    elif mode == "existing":
        sel = ri.existing_import_selection(book.get("employees") or [])
    else:
        sel = set(body.get("selected") or [])
    ri.remember_import_selection(book, sel, settings)
    settings.save()
    S["preview"] = None
    return {"selected": sorted(sel),
            "button": ri.preview_button_text(book, sel, bool(body.get("full")))}


def _signature(book, sel, full, restore, comments, soft_edits=False):
    return (book.get("file_sha256"), book.get("import_profile", "hard"),
            tuple(sorted(sel)), bool(full), bool(restore), bool(comments), bool(soft_edits))


def imp_preview(body, *, admin, start_job, **_):
    if not LIMITED_TEST_MASTER_IMPORT_UI_ENABLED:
        raise RuntimeError(LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE)
    book = S["book"]
    if not book:
        raise RuntimeError("Сначала выбери реестр")
    picked = sorted(set(body.get("selected") or []))
    if not picked:
        raise RuntimeError("Отметь хотя бы одного сотрудника")
    full, restore, comments = bool(body.get("full")), bool(body.get("restore_manual")), bool(body.get("replace_comments"))
    soft_edits = bool(body.get("soft_edits"))
    sig = _signature(book, picked, full, restore, comments, soft_edits)

    def work(say):
        say(f"Проверяю импорт {Path(book['path']).name}: {len(picked)} сотр…")
        prepared, proof_path = ri.prepare_import(book, picked, full_registry=full,
                                                 force_test_subset=restore)
        pbody = ri.preview_body(prepared, full_registry=full, restore_manual=restore,
                                replace_current_comments=comments, allow_soft_office_edits=soft_edits)
        d = xs.post_import(admin, "/admin/import/preview.json", pbody,
                           sheet=_xc.WORKBOOK_SHEET_NAME, timeout=180, log=say)
        if d.get("error"):
            S["preview"] = None
            return {"error": ri.import_error_text(d, proof_path, registration_hint_wanted=False)}
        expected = ri.expected_from_preview(d, pbody)
        if isinstance(expected, str):
            S["preview"] = None
            return {"error": expected}
        S["preview"] = {"body": {**pbody, **expected}, "meta": prepared, "sig": sig, "proof": proof_path}
        notes = ri.import_notes(d)
        notes.insert(0, ri.preview_headline(d, full))
        return {"pairs": ri.import_pairs(d), "notes": notes, "apply": ri.apply_button_label(d),
                "unassigned": int(d.get("auto_unassigned") or 0)}
    return {"job": start_job("preview", work)}


def imp_apply(body, *, admin, start_job, **_):
    pv = S["preview"]
    book = S["book"] or {}
    sig = _signature(book, body.get("selected") or [], body.get("full"), body.get("restore_manual"),
                     body.get("replace_comments"), body.get("soft_edits"))
    if not pv or pv["sig"] != sig:
        S["preview"] = None
        raise RuntimeError("Сначала сделай свежий предпросмотр импорта")

    def work(say):
        say("Применяю проверенный импорт…")
        meta = pv["meta"]
        registered = None
        try:
            registered = admin("POST", "/admin/import/authorize.json",
                               {"source_kind": meta["source_kind"], "source_proof": meta["source_proof"]},
                               timeout=60)
        except Exception:  # noqa: BLE001 — регистратор закрыт: подскажем команду
            registered = None
        d = xs.post_import(admin, "/admin/import.json", pv["body"],
                           sheet=_xc.WORKBOOK_SHEET_NAME, timeout=180, log=say)
        if isinstance(d, dict) and d.get("error"):
            d = {**d, "_proof_digest": meta["proof_digest"], "_source_kind": meta["source_kind"],
                 "_registered": bool(registered and registered.get("ok"))}
            if d.get("preview_stale"):
                S["preview"] = None
            return {"error": ri.import_error_text(d, pv["proof"], registration_hint_wanted=True)}
        S["preview"] = None
        text, err = ri.apply_toast(d)
        return {"pairs": ri.import_pairs(d), "notes": ri.import_notes(d), "toast": text, "toast_err": err}
    return {"job": start_job("apply", work)}


def imp_state(body, **_):
    return {**_book_view(), "has_preview": bool(S["preview"])}


# --- обогащение ------------------------------------------------------------------------
def _base(settings) -> str:
    return str(settings.get("scatterBase") or "")


def enr_state(body, *, settings, **_):
    base = _base(settings)
    since, until = en.suggest_window(en.receipt_dir(base)) if base else ("", "")
    return {"base": base, "since": since, "until": until,
            "writes": LIMITED_TEST_ENRICH_WRITES_ENABLED,
            "whole_title": en.WHOLE_HISTORY_TITLE, "whole_text": en.WHOLE_HISTORY_TEXT,
            "decision_hint": en.DECISION_HINT, "decision_labels": en.DECISION_LABELS,
            "conflict_columns": en.CONFLICT_COLUMNS}


def enr_base(body, *, settings, **_):
    settings["scatterBase"] = str(body.get("path") or "")
    settings.save()
    return enr_state({}, settings=settings)


def enr_preview(body, *, admin, start_job, settings, **_):
    base = _base(settings)
    if not base:
        raise RuntimeError("Сначала выбери папку с республиками")

    def work(say):
        say("Проверяю: данные сервера + папки сотрудников…")
        d = admin("GET", "/admin/export.json", timeout=120)
        enrich.validate_server_export(d)
        by_emp = enrich.split_by_employee(d["rows"])
        fmap = en.folder_map(base)
        matched, nofolder, nofile = en.match_employees(by_emp, fmap)
        S["enr_fields"], S["enr_matched"], S["enr_check"] = d["fields"], matched, None
        return {"pairs": en.preview_pairs(by_emp, matched),
                "warning": en.preview_warning(by_emp, matched, nofolder, nofile),
                "matched": [{"key": m["key"], "employee": m["employee"], "profile": m["profile"], "note": en.pick_note(m)}
                            for m in matched]}
    return {"job": start_job("enr_preview", work)}


def _picked(keys) -> list[dict]:
    keys = set(keys or [])
    return [m for m in S["enr_matched"] if m["key"] in keys]


def _ledger(admin, employees):
    answer = admin("GET", en.ledger_query(employees) + "&books=1", timeout=120) or {}
    return {**dict(answer.get("ledger") or {}), "_books": dict(answer.get("books") or {})}


def _conf(c: list[dict]) -> list[dict]:
    return [{"contract_id": str(x.get("contract_id") or ""), "key": str(x.get("key") or ""),
             "book_path": str(x.get("book_path") or ""),
             "row": en.conflict_row(x)} for x in c]


def enr_check(body, *, admin, start_job, **_):
    picked = _picked(body.get("keys"))
    if not picked:
        raise RuntimeError("Никто не выбран")
    since, until = str(body.get("since") or ""), str(body.get("until") or "")

    def work(say):
        ledger = _ledger(admin, [m["employee"] for m in picked])
        kwargs = en.preview_kwargs(since=since, until=until, ledger=ledger)
        results = enrich.preview_many(en.build_jobs(picked), S["enr_fields"], say, **kwargs)
        conflicts, office = en.collect_conflicts(results)
        S["enr_check"] = {"keys": sorted(m["key"] for m in picked), "since": since, "until": until}
        return {"lines": en.preview_lines(results), "conflicts": _conf(conflicts), "office": _conf(office)}
    return {"job": start_job("enr_check", work)}


def enr_confirm(body, **_):
    picked = _picked(body.get("keys"))
    since, until = str(body.get("since") or ""), str(body.get("until") or "")
    decisions = _decisions(body, picked, since, until)
    note = en.confirm_decisions_note(decisions, 0) if decisions else en.NO_CHECK_NOTE
    return {"text": en.confirm_text(len(picked), len(en.build_jobs(picked)), since, until)
            + (("\n\n" + note) if note else ""), "whole": not (since or until)}


def _decisions(body, picked, since, until) -> dict:
    pv = S["enr_check"]
    if not pv or pv["keys"] != sorted(m["key"] for m in picked) or (pv["since"], pv["until"]) != (since, until):
        return {}
    return {en.conflict_key(d): str(d["decision"]) for d in body.get("decisions") or []}


def enr_apply(body, *, admin, start_job, settings, **_):
    if not LIMITED_TEST_ENRICH_WRITES_ENABLED:
        raise RuntimeError(LIMITED_TEST_ENRICH_DISABLED_MESSAGE)
    picked = _picked(body.get("keys"))
    if not picked:
        raise RuntimeError("Никто не выбран")
    since, until = str(body.get("since") or ""), str(body.get("until") or "")
    decisions = _decisions(body, picked, since, until)
    base = _base(settings)

    def on_delivered(payload: dict):
        return admin("POST", "/admin/enrichment/ledger.json", payload, timeout=120)

    def work(say):
        ledger = _ledger(admin, [m["employee"] for m in picked])
        kwargs = en.enrich_many_kwargs(since=since, until=until, backup_root=en.backup_root(base),
                                       receipt_dir=en.receipt_dir(base), ledger=ledger,
                                       on_delivered=on_delivered, server_authoritative=False,
                                       decisions=decisions)
        # Книга едет на шару только проверенной: Excel работает с локальной копией
        # (опыт 30.09–01.10.2026: те же книги, в 2–6 раз быстрее, занятую или
        # изменённую за время прогона книгу не трогает). Переключателя нет.
        results = enrich.enrich_many(en.build_jobs(picked), S["enr_fields"], say,
                                     engine="local", **kwargs)
        S["last_receipt"] = next((r.get("receipt") for r in results if r.get("receipt")), None)
        S["enr_check"] = None
        conflicts, office = en.collect_conflicts(results)
        text, err = en.toast_text(results)
        return {"lines": en.report_lines(results), "ledger_error": en.ledger_error(results),
                "conflicts": _conf(conflicts), "office": _conf(office), "toast": text, "toast_err": err}
    return {"job": start_job("enr_apply", work)}


def enr_replay(body, *, admin, start_job, settings, **_):
    receipt = S["last_receipt"] or en.last_receipt(en.receipt_dir(_base(settings)))
    if not receipt:
        raise RuntimeError("Квитанция последнего прогона не найдена")

    def work(say):
        d = en.replay_receipt(receipt, lambda p: admin("POST", "/admin/enrichment/ledger.json", p, timeout=120))
        return {"toast": f"Реестр доставленного отправлен: записано {fmt_n(d.get('recorded'))}"}
    return {"job": start_job("enr_replay", work)}


# --- выгрузки, период, отозванные -----------------------------------------------------------
def exp_employees(body, *, admin, **_):
    stats = admin("GET", "/admin/stats")
    S["exp_db"] = [*list(stats.get("employees") or []),
                   *list(stats.get("soft_employees") or [])]
    rows = ri.merge_import_employees([], S["exp_db"])
    return {"employees": [{"key": e["key"], "name": e["name"], "count": e.get("db_count") or 0} for e in rows]}


def exp_prepare(body, *, admin, start_job, **_):
    employees = ri.merge_import_employees([], S.get("exp_db") or [])
    request = export_request(employees, set(body.get("selected") or []),
                             str(body.get("since") or ""), str(body.get("until") or ""))

    def work(say):
        say("Готовлю точный состав выгрузки…")
        d = admin("GET", request["path"], timeout=120)
        enrich.validate_server_export(d)
        S["exp_data"], S["exp_request"] = d, request
        return {"confirm": export_confirm_text(request, len(d["rows"]))}
    return {"job": start_job("exp_prepare", work)}


def exp_save(body, *, start_job, **_):
    path = str(body.get("path") or "")
    d = S["exp_data"]
    if not d or not path:
        raise RuntimeError("Сначала подготовь выгрузку")

    def work(say):
        write_registry_xlsx(path, d["fields"], d["rows"])
        return {"toast": f"Сохранено: {path} ({fmt_n(len(d['rows']))} строк)", "path": path}
    return {"job": start_job("exp_save", work)}


def exp_markers(body, *, admin, start_job, **_):
    path = str(body.get("path") or "")

    def work(say):
        d = admin("GET", "/admin/markers.json", timeout=120)
        write_markers_xlsx(path, d["rows"])
        return {"toast": f"Сохранено: {path} ({fmt_n(len(d['rows']))} меток)", "path": path}
    return {"job": start_job("exp_markers", work)}


def period(body, *, admin, **_):
    if body.get("set"):
        d = admin("POST", "/admin/period", {"from": body.get("from") or "", "to": body.get("to") or ""})
    else:
        d = admin("GET", "/admin/period")
    return {**d, "text": period_text(d)}


def recalled(body, *, admin, **_):
    d = admin("GET", ri.recalled_query(int(body.get("limit") or 500), str(body.get("employee") or "")))
    return {"pairs": ri.recalled_pairs(d), "columns": ri.RECALLED_COLUMNS,
            "rows": [ri.recalled_row(it) for it in d.get("items") or []],
            "employees": [n for n in (d.get("by_employee") or {}) if n]}


_ROUTES = {
    "/reg/columns_reload": columns_reload, "/reg/columns_check": columns_check,
    "/reg/columns_save": columns_save, "/reg/pick": imp_pick, "/reg/select": imp_selection,
    "/reg/preview": imp_preview, "/reg/apply": imp_apply, "/reg/state": imp_state,
    "/enr/state": enr_state, "/enr/base": enr_base, "/enr/preview": enr_preview,
    "/enr/check": enr_check, "/enr/confirm": enr_confirm, "/enr/apply": enr_apply,
    "/enr/replay": enr_replay, "/exp/employees": exp_employees, "/exp/prepare": exp_prepare,
    "/exp/save": exp_save, "/exp/markers": exp_markers, "/reg/period": period,
    "/reg/recalled": recalled,
}
