"""Local candidate review environment. Canonical services, synthetic data only."""

from pathlib import Path
from datetime import datetime, timedelta, timezone
import base64, json, os, secrets, sys, threading, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import db
from fields import DROPDOWNS
from . import (
    schema,
    store,
    access,
    exchange,
    analytics,
    telemetry,
    shifts,
    documents,
    scripts,
)
from .contract import CRMError, canonical, digest, fields, identifier, now

DEMO_EMPLOYEE = identifier("staff", "demo-employee")
DEMO_MANAGER = identifier("staff", "demo-manager")
DEMO_OTHER = identifier("staff", "demo-other")
CAPS = {
    "call",
    "visit",
    "note",
    "contact",
    "promise",
    "task",
    "reported_payment",
    "document",
    "correct",
}


def principals():
    return {
        "employee": access.Principal(
            91001,
            DEMO_EMPLOYEE,
            "demo-team",
            "employee",
            frozenset(CAPS),
            frozenset([DEMO_EMPLOYEE]),
        ),
        "manager": access.Principal(
            91002,
            DEMO_MANAGER,
            "demo-team",
            "manager",
            frozenset(CAPS | {"analytics", "exchange", "shift_correct", "accounting"}),
            frozenset([DEMO_EMPLOYEE, DEMO_MANAGER, DEMO_OTHER]),
        ),
        "observer": access.Principal(
            91003,
            DEMO_OTHER,
            "other-team",
            "observer",
            frozenset(["analytics"]),
            frozenset([DEMO_OTHER]),
        ),
    }


def seed():
    at = now()
    start = (datetime.fromisoformat(at) - timedelta(days=28)).isoformat()
    with db.connect() as conn:
        schema.initialize(conn)
        if conn.execute(
            "SELECT 1 FROM crm_meta WHERE key='synthetic_complete'"
        ).fetchone():
            return
        previous = conn.execute(
            "SELECT value FROM crm_meta WHERE key='synthetic_candidate'"
        ).fetchone()
        if previous:
            at = previous["value"]
            start = (datetime.fromisoformat(at) - timedelta(days=28)).isoformat()
        else:
            if (
                conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
                or conn.execute("SELECT COUNT(*) FROM crm_contracts").fetchone()[0]
            ):
                raise RuntimeError("Demo seed refuses a populated non-demo database")
            for eid, name, uid in [
                (DEMO_EMPLOYEE, "Тестовый сотрудник", 91001),
                (DEMO_MANAGER, "Тестовый руководитель", 91002),
                (DEMO_OTHER, "Участник без диагностики", 91003),
            ]:
                conn.execute(
                    "INSERT INTO crm_employees VALUES(?,?,?,?,?)",
                    (eid, "DEMO-" + str(uid), name, "synthetic", 1),
                )
                role = "manager" if uid == 91002 else "employee"
                caps = CAPS | (
                    {"analytics", "exchange", "shift_correct", "accounting"}
                    if role == "manager"
                    else set()
                )
                conn.execute(
                    "INSERT INTO crm_memberships VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        eid,
                        "demo-team",
                        role,
                        canonical(sorted(caps)),
                        "Europe/Samara",
                        start,
                        None,
                    ),
                )
                conn.execute(
                    "INSERT INTO crm_actor_links VALUES(?,?,?,?)",
                    (uid, eid, "synthetic", 1),
                )
            for number, name in [
                (1, "Клиент Альфа"),
                (2, "Клиент Бета"),
                (3, "Клиент Гамма"),
            ]:
                person = identifier("person", "demo-" + str(number))
                conn.execute(
                    "INSERT INTO crm_people VALUES(?,?,?,?)",
                    (person, name, "synthetic", at),
                )
                for n in range(2 if number == 1 else 1):
                    cid = identifier("contract", f"demo-{number}-{n}")
                    creditor = "Тестовый кредитор " + str(n + 1)
                    external = "DEMO-00" + str(number)
                    values = {f["key"]: None for f in fields()}
                    values.update(
                        contract_id=external,
                        client=name,
                        partner=creditor,
                        phone="+7 000 000-00-0" + str(number),
                        address="Тестовая улица, " + str(number),
                        debt_total=str(12000 * number) + ".00",
                        debt_today=str(10000 * number) + ".00",
                        soft_comment="",
                        visit_comment="",
                        responsible="Тестовый сотрудник",
                    )
                    conn.execute(
                        "INSERT INTO crm_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            cid,
                            person,
                            creditor,
                            external,
                            None,
                            "RUB",
                            canonical(values),
                            canonical(values),
                            "{}",
                            "{}",
                            at,
                        ),
                    )
                    for cap in ("call", "visit"):
                        conn.execute(
                            "INSERT INTO crm_assignments VALUES(?,?,?,?,?,?,?)",
                            (
                                str(uuid.uuid4()),
                                DEMO_EMPLOYEE,
                                cid,
                                cap,
                                start,
                                None,
                                "synthetic",
                            ),
                        )
            for kind in ("call", "visit", "contact", "promise"):
                conn.execute(
                    "INSERT INTO crm_route_policies VALUES(?,?,?,?,?,?)",
                    (
                        "demo-" + kind,
                        1,
                        "demo-team",
                        kind,
                        canonical(
                            [
                                {"target_id": "personal:demo", "delay_seconds": 0},
                                {"target_id": "general:demo", "delay_seconds": 0},
                            ]
                        ),
                        1,
                    ),
                )
            conn.execute("INSERT INTO crm_meta VALUES('synthetic_candidate',?)", (at,))
            conn.commit()
    manager = principals()["manager"]
    cohort = identifier("cohort", "demo")
    members = [
        dict(employee_uuid=e, access_at=start, features=["call", "visit", "task"])
        for e in (DEMO_EMPLOYEE, DEMO_OTHER)
    ]
    analytics.configure_cohort(
        manager,
        dict(
            operation_id=identifier("seed", "cohort"),
            cohort_id=cohort,
            name="Проверка CRM — синтетические данные",
            starts_at=start,
            members=members,
        ),
    )
    today = datetime.fromisoformat(at).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    for eid in (DEMO_EMPLOYEE, DEMO_OTHER):
        planned = [
            dict(
                starts_at=(today + timedelta(days=day)).isoformat(),
                ends_at=(today + timedelta(days=day, hours=23, minutes=59)).isoformat(),
                timezone="Europe/Samara",
                state="planned" if (day + 28) % 4 < 2 else "off",
                source="Синтетический график 2/2",
            )
            for day in range(-28, 8)
        ]
        # Today is planned so a reviewer can verify activation without moving time.
        planned[28]["state"] = "planned"
        shifts.set_plan(
            manager,
            dict(
                operation_id=identifier("seed", "plan-" + eid),
                employee_uuid=eid,
                shifts=planned,
            ),
        )
    draft = scripts.mutate(
        manager,
        dict(
            operation_id=identifier("seed", "script-save"),
            action="save",
            base_revision=0,
            content=dict(
                title="Тестовый сценарий звонка",
                steps=[
                    dict(
                        title="Проверка контакта",
                        text="Уточните, с кем установлена связь. Не раскрывайте сведения третьим лицам.",
                    ),
                    dict(
                        title="Результат",
                        text="Зафиксируйте фактический результат. Обещание и заявленную оплату вносите отдельно.",
                    ),
                ],
            ),
        ),
    )
    scripts.mutate(
        manager,
        dict(
            operation_id=identifier("seed", "script-publish"),
            action="publish",
            base_revision=draft["revision"],
        ),
    )

    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO crm_meta VALUES('synthetic_complete',?)", (now(),)
        )
        conn.commit()


def context(p):
    with db.connect() as conn:
        cohorts = [
            dict(r)
            for r in conn.execute("SELECT cohort_id,name,starts_at FROM crm_cohorts")
            if p.role == "manager"
        ]
    return dict(
        schema_version=1,
        employee_uuid=p.employee_uuid,
        team_id=p.team_id,
        role=p.role,
        capabilities=sorted(p.capabilities),
        owner=False,
        timezone=p.timezone,
        fields=fields(),
        dropdowns=DROPDOWNS,
        cohorts=cohorts,
        synthetic=True,
    )


def serve():
    secret = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200):
            raw = canonical(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if not secrets.compare_digest(
                self.headers.get("X-Local-Secret", ""), secret
            ):
                return self.reply({"error": "forbidden"}, 403)
            size = int(self.headers.get("Content-Length") or 0)
            limit = 128 * 1024 * 1024 if self.path in {"/crm/workbook-write", "/crm/workbook-resume"} else 16 * 1024 * 1024
            if size < 0 or size > limit:
                return self.reply({"error": "size"}, 413)
            try:
                body = json.loads(self.rfile.read(size) or b"{}")
                mode = self.headers.get("X-Demo-Role", "employee")
                p = principals().get(mode)
                if p is None:
                    raise CRMError("Роль сценария не найдена.")
                action = self.path.removeprefix("/crm/")
                raw = body.get("body", {})
                query = body.get("query", {})
                method = body.get("method", "GET")
                actions = {
                    "command": store.command,
                    "shift": shifts.command,
                    "plan": shifts.set_plan,
                    "cohort": analytics.configure_cohort,
                    "adoption": analytics.report,
                    "export": exchange.export,
                    "import-preview": exchange.preview,
                    "import-apply": exchange.apply,
                    "receipt": exchange.receipt,
                    "telemetry": telemetry.ingest,
                    "script": scripts.mutate,
                }
                if action == "context":
                    result = context(p)
                elif action == "portfolio":
                    result = store.portfolio(
                        p, query.get("search", ""), query.get("limit", 200)
                    )
                elif action == "card":
                    result = store.card(p, query.get("person_uuid", ""))
                elif action == "shifts":
                    result = shifts.summary(p)
                elif action == "script" and method == "GET":
                    result = scripts.state(p)
                elif action == "anchor":
                    result = store.anchor(p, raw.get("installation_id"))
                elif action == "workbook-write":
                    from pult.core.crm_workbook import write

                    result = write(raw["snapshot"], raw["path"], raw.get("template"))
                elif action == "workbook-read":
                    from pult.core.crm_workbook import read

                    result = read(raw["path"])
                elif action == "workbook-resume":
                    from pult.core.crm_workbook import resume

                    result = resume(raw["snapshot"], raw["path"])
                elif action == "document-read":
                    row, path = documents.download(p, query["document_id"])
                    result = dict(
                        filename=row["filename"],
                        sha256=row["sha256"],
                        content_base64=base64.b64encode(path.read_bytes()).decode(),
                    )
                elif action == "document":
                    result = documents.upload(
                        p,
                        raw["document_id"],
                        raw["contract_uuid"],
                        raw["filename"],
                        raw["media_type"],
                        base64.b64decode(raw["content_base64"], validate=True),
                    )
                elif action == "window-frame":
                    import ctypes
                    from ctypes import wintypes

                    handle = wintypes.HWND(int(raw["hwnd"]))
                    pid = wintypes.DWORD()
                    ctypes.windll.user32.GetWindowThreadProcessId(
                        handle, ctypes.byref(pid)
                    )
                    if pid.value != int(os.environ.get("HARD_CRM_ELECTRON_PID", "0")):
                        raise CRMError(
                            "Окно принадлежит другому процессу.", "forbidden", 403
                        )
                    color = wintypes.DWORD(0xFFFFFFFE)
                    corners = wintypes.DWORD(1)
                    border = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        handle, 34, ctypes.byref(color), ctypes.sizeof(color)
                    )
                    corner = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        handle, 33, ctypes.byref(corners), ctypes.sizeof(corners)
                    )
                    result = dict(border_hresult=border, corners_hresult=corner)
                elif action == "command" and method == "POST":
                    result = store.command(
                        p, raw, client_version=self.headers.get("X-Client-Version")
                    )
                elif action in actions and method == "POST":
                    result = actions[action](p, raw)
                else:
                    raise CRMError("Маршрут не найден.", "not_found", 404)
                self.reply(result)
            except CRMError as exc:
                self.reply(
                    dict(error=exc.code, text=str(exc), details=exc.details), exc.status
                )
            except Exception as exc:
                self.reply(dict(error="server", text=str(exc)), 500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    def aggregate():
        while True:
            try:
                analytics.aggregate()
            except Exception:
                pass
            threading.Event().wait(2)

    threading.Thread(target=aggregate, daemon=True, name="crm-metrics").start()
    print(f"HARD-CRM {server.server_address[1]} {secret}", flush=True)
    server.serve_forever()


def main():
    home = Path(os.environ["HARD_CRM_DEMO_HOME"]).resolve()
    if not home.is_absolute() or home == Path.home() or home.name != "synthetic":
        raise RuntimeError("Explicit isolated synthetic directory required")
    home.mkdir(parents=True, exist_ok=True)
    db.DB_PATH = home / "crm-pilot.sqlite3"
    if not db.DB_PATH.exists():
        db.init_db()
    else:
        with db.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "crm_meta" not in tables or (
                not conn.execute(
                    "SELECT 1 FROM crm_meta WHERE key='synthetic_candidate'"
                ).fetchone()
                and (
                    conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
                    or conn.execute("SELECT COUNT(*) FROM crm_contracts").fetchone()[0]
                )
            ):
                raise RuntimeError("Refusing a non-demo database")
    seed()
    serve()


if __name__ == "__main__":
    main()
