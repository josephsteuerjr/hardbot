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
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR.parent) not in sys.path:
    sys.path.insert(0, str(APP_DIR.parent))

# The independent CRM candidate must never load an installed Analyst profile.
if '--crm-pilot' in sys.argv:
    sys.path.insert(0,str(APP_DIR.parent/'server'))
    from crm.pilot_server import main as _crm_pilot_main
    _crm_pilot_main()
    raise SystemExit(0)

from pult.core import api as api_client  # noqa: E402
from pult.core import devices as dv  # noqa: E402
from pult.core.settings import Settings, active_profile, load_settings  # noqa: E402

SECRET = secrets.token_urlsafe(24)
_lock = threading.RLock()
_settings = load_settings()
_jobs: dict[str, dict] = {}
_journal = None
_access_busy = False
_background_busy = False


@contextmanager
def account_operation():
    global _access_busy
    with _lock:
        if _access_busy or _background_busy or _mail_busy.locked() or any(j.get('state') == 'running' for j in _jobs.values()):
            raise RuntimeError('Дождитесь завершения текущей операции перед сменой доступа.')
        _access_busy = True
    try:
        yield
    finally:
        with _lock:
            _access_busy = False


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
                "settings_path": str(_settings.path),"account_session":str(prof.get('device_token') or '').startswith('account:')}


def import_settings(path: str) -> dict:
    """Перенос старого файла без перепривязки устройства и потери контуров."""
    with _lock:
        if _access_busy:
            raise RuntimeError('Дождитесь завершения входа.')
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
    try:
        return api_client.request(prof["url"], method, path, body, key=key, device_token=token,
                                  timeout=timeout)
    except api_client.AccessRevoked:
        with _lock:
            if prof.get('device_token') == token:
                prof['device_token'] = ''
                _persist_settings()
        raise


def profile_action(body: dict) -> dict:
    act = body.get("action")
    name = str(body.get("name") or "").strip()
    with _lock:
        if _access_busy or _mail_busy.locked() or any(j.get("state") == "running" for j in _jobs.values()):
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
    if _access_busy:
        raise RuntimeError('Дождитесь завершения входа.')
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
        if _access_busy:
            raise RuntimeError('Дождитесь завершения входа.')
        previous = _profile().get("device_token", "")
        _profile()["device_token"] = ""
        try:
            _persist_settings()
        except RuntimeError:
            _profile()["device_token"] = previous
            raise
    return state()


# --- долгие задачи (Excel, импорт) -------------------------------------------------------
class JobBusyError(RuntimeError):
    """Admission refused before a durable job or worker was created."""


def start_job(kind: str, fn, *, request_id=None, fingerprint=None, before_start=None) -> str:
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
        if _access_busy or _background_busy or any(j.get("state") == "running" for j in _jobs.values()):
            raise JobBusyError("Дождитесь завершения текущей операции.")
        if before_start:before_start(jid,profile,fingerprint)
        job_journal().save(job, profile, fingerprint)
        _jobs[jid] = job

    def say(line: str) -> None:
        job["log"].append(str(line))
        job_journal().save(job, profile, fingerprint)

    def run():
        try:
            job["result"] = fn(say)
            job["state"] = completion_state(job['result']) if kind=='enr_apply' else 'done'
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
    if job.get('kind')=='enr_apply' and job.get('state')!='done' and not _background_busy:
        from pult import desk_registry
        service=desk_registry.delivery_service(_settings,admin)
        try:run=service.store.get(jid,service.owner())
        except ValueError:run=None
        if run:
            result=service.view(run)
            if result['state']=='done':
                with job_journal().connect() as conn:meta=conn.execute('SELECT fingerprint FROM jobs WHERE id=?',(jid,)).fetchone()
                job.update(state='done',result=result,error=None)
                job_journal().save(job,job_profile(),meta[0])
    return {k: job[k] for k in ("id", "kind", "state", "log", "result", "error")}


def completion_state(result):
    status=(result or {}).get('run_state','done') if isinstance(result,dict) else 'done'
    return 'attention' if status=='attention' else 'waiting' if status in ('waiting','paused') else 'done'


def enrichment_tick():
    """One serialized pass; the queue never depends on renderer polling."""
    global _background_busy
    from pult import desk_registry
    service=desk_registry.delivery_service(_settings,admin)
    run=service.current()
    if not service.ready(run):return
    with _lock:
        if _access_busy or _background_busy or _mail_busy.locked() or any(j.get('state')=='running' for j in _jobs.values()):return
        _background_busy=True
    try:
        journal=job_journal();profile=job_profile()
        with journal.connect() as conn:
            meta=conn.execute('SELECT profile,fingerprint FROM jobs WHERE id=?',(run['id'],)).fetchone()
        if not meta:
            recovered=dict(id=run['id'],kind='enr_apply',state='waiting',log=['Возобновление сохранённого переноса'],result=None,error=None,started=run['created'])
            journal.save(recovered,profile,run.get('fingerprint','enrichment'));meta=(profile,run.get('fingerprint','enrichment'))
        if meta[0]!=profile:return
        try:result=service.process(run)
        except Exception as exc:result=service.failure('Автоматический перенос остановлен: '+str(exc))
        job=journal.load(run['id'],profile)
        job.update(state=completion_state(result),result=result,error=None)
        job['log'].append(result['toast'])
        job['log']=job['log'][-300:]
        journal.save(job,profile,meta[1])
        with _lock:_jobs[run['id']]=job
        desk_registry.S['preview']=None
    finally:
        with _lock:_background_busy=False


def enrichment_loop():
    while True:
        try:enrichment_tick()
        except Exception:
            # A queue/snapshot failure must remain visibly unfinished. It is
            # never changed to a completed durable job by a daemon exception.
            pass
        time.sleep(2)


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


def _schedule_due_mail(now: datetime) -> None:
    """Admit today's due jobs; only mail_send records an actual successful day.

    A refused admission (busy or journal write failure) has not called Outlook:
    leave it due for the next tick. Once admitted, the stable operation ID and
    durable journal prevent an uncertain Outlook result from being sent twice.
    """
    from pult.core import mailer
    for book in mailer.due(_mail_cfg(), now):
        identifier = "scheduled-" + hashlib.sha256((job_profile() + now.date().isoformat() + book).encode()).hexdigest()
        try:
            start_job("mail", lambda say, selected=book, oid=identifier: mail_send(selected, manual=False, say=say, operation_id=oid),
                      request_id=identifier, fingerprint=identifier)
        except JobBusyError:
            # Another operation owns the sole worker; retry after it finishes.
            return
        except Exception:  # noqa: BLE001 — failed admission or durable uncertain ID; never invent a sent day
            traceback.print_exc()


def _mail_loop() -> None:
    while True:
        time.sleep(30)
        try:
            _schedule_due_mail(datetime.now())
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
        except api_client.AccessRevoked as exc:
            self._send(200, {"ok": False, "revoked": True, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — окну нужен текст, не трейсбек
            self._send(200, {"ok": False, "error": str(exc) or type(exc).__name__})


def route(path: str, body: dict):
    fingerprint = hashlib.sha256(json.dumps([path, body], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    def durable_start(kind, fn, *, before_start=None):
        return start_job(kind, fn, request_id=body.get("request_id"), fingerprint=fingerprint,before_start=before_start)
    if path == "/settings/import":
        return import_settings(str(body.get("path") or ""))
    if path == '/account/root_probe':
        from pult.core.account_session import probe
        return probe(body.get('ip'))
    if path == '/account/root_access_check':
        from pult.core.account_session import verify_root_access
        return verify_root_access(body.get('ip'),str(body.get('host_key') or ''),str(body.get('ssh_username') or 'root'),
            str(body.get('root_password') or ''),str(body.get('sudo_password') or ''))
    if path == '/account/root_bootstrap':
        with account_operation():
            from pult.core.account_session import root_bootstrap
            import datetime
            operation=str(body.get('operation_id') or '')
            if not operation: raise ValueError('Нужен номер операции.')
            log_path=Path(_settings.path).with_name('account-operations.jsonl')
            def record(stage,details):
                with log_path.open('a',encoding='utf-8') as f:
                    f.write(json.dumps({'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        'operation_id':operation,'stage':stage,**details},ensure_ascii=False)+'\n')
            record('intent',{'action':'root owner bootstrap','server':_profile().get('url'),
                'host_key':body.get('host_key'),'backup':'online SQLite backup before account creation'})
            root_credentials={'ssh_username':str(body.get('ssh_username') or 'root'),
                              'sudo_password':str(body.get('sudo_password') or '')}
            try:
                result=root_bootstrap(_profile().get('url',''),body.get('ip'),str(body.get('root_password') or ''),
                    str(body.get('host_key') or ''),str(body.get('username') or ''),str(body.get('password') or ''),operation,
                    legacy_token=str(_profile().get('device_token') or '') if not str(_profile().get('device_token') or '').startswith('account:') else '',
                    legacy_device_id=dv.device_fingerprint(),employee=str(body.get('employee') or ''),recovery=body.get('recovery') is True,
                    **root_credentials)
                record('result',{'result':'success','receipt':result})
                return result
            except Exception:
                record('result',{'result':'unknown_or_failed','next':'inspect operation_id and account before retry'})
                raise
    if path == '/account/login':
        with account_operation():
            from pult.core.account_session import login
            result=login(_profile().get('url',''),str(body.get('username') or ''),str(body.get('password') or ''),_settings.path)
            with _lock:
                previous=_profile().get('device_token','')
                _profile()['device_token']='account:'+result['token']
                try:
                    _persist_settings()
                except Exception:
                    _profile()['device_token']=previous
                    raise
            return {**state(),'must_change_password':bool(result.get('must_change_password')),'username':result.get('username'),'pending':result.get('action')=='ok_pending'}
    if path == '/account/password':
        return api_client.request(_profile().get('url',''),'POST','/api/account/password',{
            'username':str(body.get('username') or ''),'current':str(body.get('current') or ''),'new':str(body.get('new') or '')})
    if path == "/busy":
        return {"busy": _access_busy or _mail_busy.locked() or any(j.get("state") == "running" for j in _jobs.values())}
    if path == "/state":
        return state()
    if path == "/admin":
        return admin(str(body.get("method") or "GET"), str(body.get("path") or ""),
                     body.get("body"), float(body.get("timeout") or 30))
    if path == '/admin_image':
        import base64
        import urllib.parse
        kind, file = body.get('kind'), body.get('file')
        if kind not in ('photo', 'receipt_photo') or not isinstance(file, str) or not file or len(file) > 220:
            raise ValueError('Недопустимый снимок карточки')
        suffix = Path(file).suffix.lower()
        mime = {'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png',
                '.webp':'image/webp','.gif':'image/gif','.heic':'image/heic',
                '.heif':'image/heif','.avif':'image/avif','.jfif':'image/jpeg',
                '.jpe':'image/jpeg'}.get(suffix)
        if not mime:
            raise ValueError('Недопустимый формат снимка')
        prof = _profile()
        raw = api_client.download(prof.get('url', ''), '/admin/control/'+kind+'?'+
                                  urllib.parse.urlencode({'file':file}),
                                  device_token=prof.get('device_token', ''), timeout=90)
        return {'src':'data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii')}
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
    threading.Thread(target=enrichment_loop,daemon=True,name='enrichment').start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
