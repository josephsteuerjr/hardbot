"""Питон-сторона пульта аналитика на движке Hélène (Пульты 2, 29.09.2026).

Окно пульта — Electron, как у Hélène и у пульта руководителя. Всё, что окно само
не умеет или не должно уметь, живёт здесь, в дочернем процессе:

* доступ к серверу: контуры, токен устройства с отпечатком машины, мастер-ключ
  на один раз — ровно тем же кодом, что у Qt-пульта (`pult.core.api`, `settings`);
* живой Excel через COM (обогащение личных книг) и чтение реестров;
* формулировки экранов — те же чистые функции `pult.core.*`, что покрыты тестами:
  окно зовёт их по имени (`/fn`), список имён закрыт.

Сервер слушает только 127.0.0.1 на случайном порту; первая строка вывода —
`HARD-BACKEND <порт> <секрет>`. Каждый запрос обязан нести секрет: чужая
программа на этой машине порт найдёт, а секрет — нет.

    python -X utf8 -m pult.desk_backend
"""

from __future__ import annotations

import json
import copy
import hashlib
import secrets
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR.parent) not in sys.path:
    sys.path.insert(0, str(APP_DIR.parent))

from pult.core import api as api_client  # noqa: E402
from pult.core import devices as dv  # noqa: E402
from pult.core.settings import Settings, active_profile, load_settings  # noqa: E402

SECRET = secrets.token_urlsafe(24)
_lock = threading.RLock()
_settings = load_settings()
_jobs: dict[str, dict] = {}
_journal = None


def job_journal():
    global _journal
    if _journal is None:
        from pult.core.durable_jobs import Journal
        path = getattr(_settings, "path", None)
        if path is None or Path(path).parent == APP_DIR:
            path = APP_DIR.parent / "_local" / "backend-settings.json"
        _journal = Journal(Path(path).with_name("jobs.sqlite3"))
        _journal.recover()
    return _journal


def job_profile():
    prof = _profile()
    return str(_settings.get("active", "")) + "\n" + str(prof.get("url", ""))


# --- функции экранов: закрытый список ------------------------------------------------
def _fn_table() -> dict:
    from pult.core import activity, dashboard, devices, links, managers, people, priority
    names = {
        dashboard: ("tile_values", "bars", "silent_row", "silent_note", "delivery_pairs",
                    "delivery_note", "country_outcome_chips", "country_today_pairs",
                    "registry_pairs", "registry_note", "coverage", "period_text"),
        activity: ("activity_status", "activity_row", "filter_counts", "summary_text",
                   "latest_shell", "fmt_seen", "matches"),
        links: ("merge_rows", "link_row", "link_state", "state_text", "state_role",
                "uses_text", "uses_left", "filter_counts", "summary_tiles", "link_summary",
                "fmt_when", "fmt_day", "kind_text", "agent_text", "matches", "link_url",
                "issue_note", "reissue_text", "revoke_text", "bulk_items", "bulk_rows",
                "bulk_summary", "bulk_confirm_text", "fresh_row", "base_of"),
        managers: ("manager_row", "manager_body", "pushes_text", "direction_employees_note",
                   "digest_state_text", "delivery_lines", "push_result_text",
                   "chat_scope_text", "office_note", "delete_text", "next_local_manager_id",
                   "city_chip_text", "emp_chip_text", "office_chip_text"),
        people: ("role_status", "kind_text", "link_note", "unassign_text", "guest_label"),
        priority: ("priority_query", "cluster_chips", "priority_row"),
        devices: ("device_line", "key_state_text", "revoke_text"),
    }
    table = {}
    for mod, fns in names.items():
        short = mod.__name__.rsplit(".", 1)[-1]
        for fn in fns:
            if hasattr(mod, fn):
                table[f"{short}.{fn}"] = getattr(mod, fn)
    consts = {
        "activity.COLUMNS": activity.ACTIVITY_COLUMNS, "activity.FILTERS": activity.FILTERS,
        "links.COLUMNS": links.LINK_COLUMNS, "links.FILTERS": links.LINK_FILTERS,
        "links.USES": links.USES_CHOICES, "dashboard.TILES": dashboard.TILES,
        "dashboard.SILENT_COLUMNS": dashboard.SILENT_COLUMNS,
        "dashboard.COUNTRIES": dashboard.COUNTRIES,
        "managers.DIRECTIONS": managers.DIRECTION_OPTIONS,
        "managers.PUSH_SWITCHES": managers.MANAGER_PUSH_SWITCHES,
        "priority.COLUMNS": priority.PRIORITY_COLUMNS, "priority.WEIGHTS": priority.WEIGHTS,
    }
    return table, consts


_FN = None


def call_fn(name: str, args: list, kwargs: dict):
    global _FN
    if _FN is None:
        _FN = _fn_table()
    table, consts = _FN
    if name in consts:
        return consts[name]
    fn = table.get(name)
    if fn is None:
        raise KeyError(f"нет такой функции: {name}")
    return fn(*args, **kwargs)


# --- доступ к серверу ------------------------------------------------------------------
def _profile() -> dict:
    return active_profile(_settings)


def _persist_settings() -> None:
    if not _settings.save():
        raise RuntimeError("Настройки не сохранены на диске. Не закрывайте пульт; проверьте место, права и доступность шифрования, затем сохраните настройки в «Подключении».")


def state() -> dict:
    with _lock:
        prof = _profile()
        return {"profiles": list(_settings.get("profiles", {})), "active": _settings.get("active"),
                "url": prof.get("url", ""), "bound": bool(prof.get("device_token")),
                "device_name": dv.device_default_name(), "key_state": dv.key_state_text(prof.get("device_token", "")),
                "settings_path": str(_settings.path)}


def import_settings(path: str) -> dict:
    """Перенос старого файла без перепривязки устройства и потери контуров."""
    with _lock:
        if any(p.get("device_token") for p in _settings.get("profiles", {}).values()):
            raise RuntimeError("Пульт уже привязан. Перенос поверх действующего входа запрещён.")
        if any(j.get("state") == "running" for j in _jobs.values()):
            raise RuntimeError("Дождитесь завершения текущей операции.")
        source = Path(path)
        data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not ("profiles" in data or "url" in data):
            raise ValueError("Это не файл настроек прежнего пульта.")
        incoming = Settings(data, path=_settings.path)
        if not incoming.save():
            raise RuntimeError("Не удалось сохранить перенесённые настройки.")
        _settings.clear()
        _settings.update(incoming)
        return state()


def admin(method: str, path: str, body=None, timeout: float = 30, key: str = ""):
    prof = _profile()
    token = "" if key else prof.get("device_token", "")
    if not prof.get("url"):
        raise RuntimeError(api_client.NO_URL_TEXT)
    return api_client.request(prof["url"], method, path, body, key=key, device_token=token,
                              timeout=timeout)


def profile_action(body: dict) -> dict:
    act = body.get("action")
    name = str(body.get("name") or "").strip()
    with _lock:
        if _mail_busy.locked() or any(j.get("state") == "running" for j in _jobs.values()):
            raise RuntimeError("Дождитесь завершения операции перед изменением контура.")
        profiles = _settings.setdefault("profiles", {})
        previous = copy.deepcopy(dict(_settings))
        if act == "switch" and name in profiles:
            _settings["active"] = name
        elif act == "save":
            url = str(body.get("url") or "").strip().rstrip("/")
            prof = _profile()
            if prof.get("device_token") and url != prof.get("url", "").rstrip("/"):
                raise RuntimeError("Адрес привязанного контура менять нельзя. Добавьте новый контур или сначала снимите привязку.")
            prof["url"] = url
        elif act == "add" and name and name not in profiles:
            profiles[name] = {"url": str(body.get("url") or "").strip().rstrip("/"), "device_token": ""}
            _settings["active"] = name
        elif act == "rename" and name in profiles:
            new = str(body.get("new") or "").strip()
            if new and new not in profiles:
                profiles[new] = profiles.pop(name)
                if _settings.get("active") == name:
                    _settings["active"] = new
        elif act == "remove" and name in profiles and len(profiles) > 1:
            profiles.pop(name)
            if _settings.get("active") == name:
                _settings["active"] = next(iter(profiles))
        else:
            raise ValueError("не понял, что сделать с контуром")
        try:
            _persist_settings()
        except RuntimeError:
            _settings.clear(); _settings.update(previous)
            raise
        from pult import desk_registry
        desk_registry.reset_context()
    return state()


def bind(body: dict) -> dict:
    """Привязка по одноразовому коду (обычный путь) или мастер-ключом (один раз)."""
    code = str(body.get("code") or "").strip()
    key = str(body.get("key") or "").strip()
    name = str(body.get("name") or dv.device_default_name()).strip()
    fp = dv.device_fingerprint()
    if code:
        d = api_client.request(_profile().get("url", ""), "POST", "/admin/device/redeem.json",
                               {"code": code, "device_id": fp, "name": name})
    elif key:
        d = admin("POST", "/admin/device/enroll.json", {"device_id": fp, "name": name}, key=key)
    else:
        raise ValueError("нужен код привязки или мастер-ключ")
    token = (d or {}).get("token") or ""
    if not token:
        raise RuntimeError("Сервер не выдал токен")
    with _lock:
        _profile()["device_token"] = token
        _persist_settings()
    return state()


def unbind() -> dict:
    with _lock:
        previous = _profile().get("device_token", "")
        _profile()["device_token"] = ""
        try:
            _persist_settings()
        except RuntimeError:
            _profile()["device_token"] = previous
            raise
    return state()


# --- долгие задачи (Excel, импорт) -------------------------------------------------------
def start_job(kind: str, fn, *, request_id=None, fingerprint=None) -> str:
    jid = str(request_id or uuid.uuid4().hex)
    if not 8 <= len(jid) <= 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in jid):
        raise ValueError("request_id")
    profile = job_profile()
    fingerprint = fingerprint or kind
    job = {"id": jid, "kind": kind, "state": "running", "log": [], "result": None, "error": None,
           "started": time.time()}
    with _lock:
        previous = job_journal().load(jid, profile, fingerprint)
        if previous:
            return jid
        if any(j.get("state") == "running" for j in _jobs.values()):
            raise RuntimeError("Дождитесь завершения текущей операции.")
        job_journal().save(job, profile, fingerprint)
        _jobs[jid] = job

    def say(line: str) -> None:
        job["log"].append(str(line))
        job_journal().save(job, profile, fingerprint)

    def run():
        try:
            job["result"] = fn(say)
            job["state"] = "done"
        except Exception as exc:  # noqa: BLE001
            job["state"] = "uncertain" if kind == "mail" else "failed"
            job["error"] = str(exc) or type(exc).__name__
            job["log"].append(traceback.format_exc()[-1200:])
        finally:
            try:
                job_journal().save(job, profile, fingerprint)
            except Exception as exc:
                job["state"] = "uncertain"
                job["error"] = "Не удалось записать результат операции: " + str(exc)
    threading.Thread(target=run, daemon=True).start()
    return jid


def job_state(jid: str) -> dict:
    job = job_journal().load(jid, job_profile())
    if not job:
        raise KeyError("нет такой задачи")
    return {k: job[k] for k in ("id", "kind", "state", "log", "result", "error")}


# --- рассылка книг почтой (Outlook через COM) ---------------------------------------------
# Пульт в трее — и рассылка живёт, пока он запущен. Поток просыпается раз в
# полминуты, а отправка идёт тем же порядком, что по кнопке «Отправить сейчас».
_mail_busy = threading.Lock()


def _mail_cfg() -> dict:
    from pult.core import mailer
    return mailer.config(_settings.get("mail"))


def _mail_save(cfg: dict) -> None:
    with _lock:
        _settings["mail"] = cfg
        _persist_settings()


def mail_state() -> dict:
    from pult.core import mailer
    cfg = _mail_cfg()
    now = datetime.now()
    return {"config": {k: v for k, v in cfg.items() if k not in ("history", "last")},
            "books": mailer.BOOKS, "weekdays": mailer.WEEKDAYS,
            "directions": mailer.DIRECTIONS, "next": mailer.next_run(cfg, now),
            "history": mailer.history_rows(cfg), "outlook": mailer.outlook_running()}


def mail_set(body: dict) -> dict:
    from pult.core import mailer
    with _lock:
        cfg = mailer.clean(body, _settings.get("mail"))
        _mail_save(cfg)
    return mail_state()


def mail_send(book: str, *, manual: bool, display: bool = False, say=print, operation_id=None) -> dict:
    """Одна книга одним письмом: скачать с сервера, сохранить во временную
    папку, отдать Outlook. Каждая попытка — в историю."""
    import tempfile
    import urllib.parse

    from pult.core import mailer
    if book not in mailer.BOOKS:
        raise ValueError("нет такой книги")
    with _mail_busy:
        cfg = _mail_cfg()
        to = [r["email"] for r in cfg.get("recipients") or []]
        if not to:
            raise ValueError("Нет получателей")
        now = datetime.now()
        today = now.date()
        name = mailer.file_name(book, cfg, today)
        try:
            say(f"Беру книгу «{mailer.BOOKS[book]}» с сервера…")
            prof = _profile()
            path = "/admin/stats_book.xlsx?" + urllib.parse.urlencode(mailer.query(book, cfg, today))
            blob = api_client.download(prof.get("url", ""), path,
                                       device_token=prof.get("device_token", ""))
            folder = Path(tempfile.gettempdir()) / "Хард-рассылка"
            folder.mkdir(exist_ok=True)
            file = folder / name
            file.write_bytes(blob)
            try:
                summary = mailer.summary_from_book(blob)
            except Exception:  # noqa: BLE001 — итоги в письме не главное
                summary = []
            say("Передаю письмо в Outlook…")
            mailer.send_outlook(to, mailer.subject(book, cfg, today),
                                mailer.body_html(book, cfg, today, summary, now),
                                str(file), display=display, operation_id=operation_id)
        except Exception as exc:  # noqa: BLE001
            if not display:
                _mail_save(mailer.remember(_mail_cfg(), book, ok=False, when=now, to=to,
                                           error=str(exc) or type(exc).__name__,
                                           file=name, manual=manual))
            raise
        if not display:
            _mail_save(mailer.remember(_mail_cfg(), book, ok=True, when=now, to=to,
                                       file=name, manual=manual))
        say("Открыт черновик в Outlook." if display else f"Отправлено: {', '.join(to)}")
        return {"file": name, "to": to, "shown": display}


def _mail_loop() -> None:
    from pult.core import mailer
    while True:
        time.sleep(30)
        try:
            cfg = _mail_cfg()
            for book in mailer.due(cfg, datetime.now()):
                try:
                    identifier = "scheduled-" + hashlib.sha256((job_profile() + datetime.now().date().isoformat() + book).encode()).hexdigest()
                    start_job("mail", lambda say, selected=book, oid=identifier: mail_send(selected, manual=False, say=say, operation_id=oid),
                              request_id=identifier, fingerprint=identifier)
                except Exception:  # noqa: BLE001 — ошибка уже в истории
                    # Неудачу тоже считаем попыткой дня: иначе поток бился бы в
                    # закрытый Outlook каждые полминуты до полуночи.
                    cfg2 = _mail_cfg()
                    cfg2["last"] = {**(cfg2.get("last") or {}), book: datetime.now().date().isoformat()}
                    _mail_save(cfg2)
        except Exception:  # noqa: BLE001 — поток рассылки не должен умирать
            traceback.print_exc()


# --- HTTP ----------------------------------------------------------------------------------
def _jsonable(v):
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, set):
        return sorted(_jsonable(x) for x in v)
    if isinstance(v, Path):
        return str(v)
    return v


class Handler(BaseHTTPRequestHandler):
    server_version = "HardDesk/1"
    # Постоянное соединение: окно шлёт десятки мелких запросов разом, и при
    # HTTP/1.0 Node пытался переиспользовать уже закрытый сокет («fetch failed»).
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # тишина: вывод читает Electron
        return

    def _send(self, status: int, payload) -> None:
        data = json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        if self.headers.get("X-Local-Secret") != SECRET:
            return self._send(403, {"ok": False, "error": "чужой"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return self._send(400, {"ok": False, "error": "не JSON"})
        try:
            data = route(self.path, body)
            self._send(200, {"ok": True, "data": data})
        except Exception as exc:  # noqa: BLE001 — окну нужен текст, не трейсбек
            self._send(200, {"ok": False, "error": str(exc) or type(exc).__name__})


def route(path: str, body: dict):
    fingerprint = hashlib.sha256(json.dumps([path, body], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    def durable_start(kind, fn):
        return start_job(kind, fn, request_id=body.get("request_id"), fingerprint=fingerprint)
    if path == "/settings/import":
        return import_settings(str(body.get("path") or ""))
    if path == "/busy":
        return {"busy": _mail_busy.locked() or any(j.get("state") == "running" for j in _jobs.values())}
    if path == "/state":
        return state()
    if path == "/admin":
        return admin(str(body.get("method") or "GET"), str(body.get("path") or ""),
                     body.get("body"), float(body.get("timeout") or 30))
    if path == "/fn":
        name = str(body.get("name") or "")
        if "map" in body:          # одна функция по списку — одной поездкой
            return [call_fn(name, list(a), {}) for a in body.get("map") or []]
        return call_fn(name, list(body.get("args") or []), dict(body.get("kwargs") or {}))
    if path == "/profile":
        return profile_action(body)
    if path == "/bind":
        return bind(body)
    if path == "/unbind":
        return unbind()
    if path == "/job":
        return job_state(str(body.get("id") or ""))
    if path == "/jobs":
        return [{k: j.get(k) for k in ("id", "kind", "state", "started", "error")}
                for j in job_journal().list(job_profile())]
    if path == "/mail/state":
        return mail_state()
    if path == "/mail/set":
        return mail_set(body)
    if path == "/mail/send":
        book = str(body.get("book") or "")
        display = bool(body.get("display"))
        return {"job": durable_start("mail", lambda say: mail_send(book, manual=True, display=display,
                                                              say=say, operation_id=body.get("request_id")))}
    from pult import desk_registry
    return desk_registry.route(path, body, admin=admin, start_job=durable_start, settings=_settings)


def main() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"HARD-BACKEND {srv.server_address[1]} {SECRET}", flush=True)
    threading.Thread(target=_mail_loop, daemon=True, name="mail").start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
