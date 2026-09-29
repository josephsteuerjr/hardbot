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
import secrets
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR.parent) not in sys.path:
    sys.path.insert(0, str(APP_DIR.parent))

from pult.core import api as api_client  # noqa: E402
from pult.core import devices as dv  # noqa: E402
from pult.core.settings import active_profile, load_settings  # noqa: E402

SECRET = secrets.token_urlsafe(24)
_lock = threading.RLock()
_settings = load_settings()
_jobs: dict[str, dict] = {}


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


def state() -> dict:
    with _lock:
        prof = _profile()
        return {"profiles": list(_settings.get("profiles", {})), "active": _settings.get("active"),
                "url": prof.get("url", ""), "bound": bool(prof.get("device_token")),
                "device_name": dv.device_default_name(), "key_state": dv.key_state_text(prof.get("device_token", "")),
                "settings_path": str(_settings.path)}


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
        profiles = _settings.setdefault("profiles", {})
        if act == "switch" and name in profiles:
            _settings["active"] = name
        elif act == "save":
            _profile()["url"] = str(body.get("url") or "").strip().rstrip("/")
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
        _settings.save()
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
        _settings.save()
    return state()


def unbind() -> dict:
    with _lock:
        _profile()["device_token"] = ""
        _settings.save()
    return state()


# --- долгие задачи (Excel, импорт) -------------------------------------------------------
def start_job(kind: str, fn) -> str:
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "state": "running", "log": [], "result": None, "error": None,
           "started": time.time()}
    _jobs[jid] = job

    def say(line: str) -> None:
        job["log"].append(str(line))

    def run():
        try:
            job["result"] = fn(say)
            job["state"] = "done"
        except Exception as exc:  # noqa: BLE001
            job["state"] = "failed"
            job["error"] = str(exc) or type(exc).__name__
            job["log"].append(traceback.format_exc()[-1200:])
    threading.Thread(target=run, daemon=True).start()
    return jid


def job_state(jid: str) -> dict:
    job = _jobs.get(jid)
    if not job:
        raise KeyError("нет такой задачи")
    return {k: job[k] for k in ("id", "kind", "state", "log", "result", "error")}


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
    from pult import desk_registry
    return desk_registry.route(path, body, admin=admin, start_job=start_job, settings=_settings)


def main() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"HARD-BACKEND {srv.server_address[1]} {SECRET}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
