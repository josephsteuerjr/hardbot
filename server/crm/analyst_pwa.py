"""Adapter from the deployed Analyst website to the shared mobile CRM shell."""

from contextlib import closing
import hmac
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
import sqlite3

from aiohttp import web

from crm.contract import digest, fields, now
from fields import DROPDOWNS

if __package__:
    from .analyst_registry_write import Catalog, RegistryConflict, revisions, save_rows
else:
    from analyst_registry_write import Catalog, RegistryConflict, revisions, save_rows


def identity(site, username):
    return str(site.operator["uid"] if site.can_write(username) else site.account_id)


def context(site, username):
    actor = identity(site, username)
    return dict(role="analyst", source="legacy-analyst-web", actor_uid=actor,
                employee_uuid="analyst-web-"+actor, capabilities=[], staff=[], timezone="Europe/Samara",
                fields=definitions())


def definitions():
    result = [dict(f, sortable=True, options=DROPDOWNS.get(f["key"], []))
              for f in fields() if not f.get("readonly")]
    return [*result, dict(key="responsible_soft", title="Ответственный за звонки",
                          section="Закрепления", group="accounting", type="text", sortable=True)]


def row_view(row, writable, catalog):
    key = str(row["id"])
    raw = dict(row)
    observed = {k:raw[k] for k in ("id", "contract_id", "partner", *catalog.labels)}
    presented = {k:v for k,v in raw.items() if k!="id"}
    return dict(contract_uuid=key, person_uuid=key, external_id=row["contract_id"],
                client_name=row["client"], creditor_id=row["partner"], currency="",
                fields=presented, revisions=revisions(digest(observed)), assignments=[], allowed_actions=[],
                writable_fields=list(catalog.labels) if writable else [])


def parameters(raw, default, ceiling):
    source = str(raw or "")
    return min(int(source), ceiling) if re.fullmatch(r"[0-9]{1,8}", source) else default


def registry(conn, catalog, writable, query):
    columns = definitions()
    keys = {f["key"] for f in columns}
    fields_sql = ",".join(f'"{f["key"]}"' for f in columns)
    offset = parameters(query.get("offset"), 0, 1000000)
    limit = max(1, parameters(query.get("limit"), 50, 100))
    conditions = []
    inputs = []
    phrase = str(query.get("search") or "").strip().casefold()[:100]
    if phrase:
        search = ("contract_id","client","partner","phone","city","responsible_soft","responsible")
        conditions.append("("+" OR ".join(f"instr(fold({key}),?)>0" for key in search)+")")
        inputs.extend([phrase]*len(search))
    ids_string = query.get("ids")
    if ids_string:
        ids = str(ids_string).split(",")
        if len(ids)>100 or not all(re.fullmatch(r"[1-9][0-9]{0,17}", part) for part in ids):
            raise RegistryConflict("Некорректный список договоров.", 400)
        conditions.append("id IN (" + ",".join("?" for _ in ids) + ")")
        inputs.extend(map(int, ids))
    where = (" WHERE "+" AND ".join(conditions)) if conditions else ""
    sort = query.get("sort")
    sort = sort if isinstance(sort, str) and sort in keys else "contract_id"
    direction = "DESC" if query.get("direction")=="desc" else "ASC"
    total = conn.execute("SELECT count(*) FROM clients"+where, inputs).fetchone()[0]
    rows = conn.execute(f'SELECT id,{fields_sql} FROM clients{where} ORDER BY "{sort}" {direction},id LIMIT ? OFFSET ?',
                        (*inputs,limit,offset))
    return dict(rows=[row_view(row,writable,catalog) for row in rows], columns=columns,
                total=total, has_more=offset+limit<total, version_kind="digest")


def person_card(conn, catalog, writable, query):
    key = str(query.get("person_uuid") or "")
    if not re.fullmatch(r"[1-9][0-9]{0,17}",key):
        raise RegistryConflict("Карточка не найдена.",404)
    fields_sql = ",".join(f'"{f["key"]}"' for f in definitions())
    row = conn.execute(f"SELECT id,{fields_sql} FROM clients WHERE id=?", (int(key),)).fetchone()
    if not row:
        raise RegistryConflict("Карточка не найдена.",404)
    history = []
    for event in conn.execute(
            "SELECT id,ts,actor,detail FROM audit WHERE action='analyst_web_registry_edit' AND target=? ORDER BY id DESC LIMIT 100",
            (row["contract_id"],)):
        values = json.loads(event["detail"])
        patch = values["values"] if "values" in values else {values["field"]:values["value"]}
        label = conn.execute("SELECT name FROM managers WHERE telegram_id=?", (event["actor"],)).fetchone()
        history.append(dict(event_id=str(event["id"]), kind="registry_edit", origin="app",
                            occurred_at=event["ts"], received_at=event["ts"], contract_ids=[key],
                            author=label[0] if label else "", payload=dict(values=patch), projection_status="applied"))
    return dict(person=dict(person_uuid=key,name=row["client"]),contracts=[row_view(row,writable,catalog)],
                history=history,entities=[],documents=[])


def statistics(conn):
    rows = conn.execute("""
        SELECT coalesce(nullif(trim(partner),''),'—') AS partner, count(*) AS contracts,
            sum(fold(trim(responsible_soft)) NOT IN ('','не закреплен','не закреплён','нет','не назначен')) AS soft,
            sum(fold(trim(responsible)) NOT IN ('','не закреплен','не закреплён','нет','не назначен')) AS hard
          FROM clients GROUP BY 1 ORDER BY 2 DESC,1
    """).fetchall()
    return dict(rows=[dict(row) for row in rows])


def mount_pwa(app, site, assets, prefix, cookie):
    assets = Path(assets).resolve()
    paths = {p.relative_to(assets).as_posix():p for p in assets.rglob("*") if p.is_file()
             and p.resolve().is_relative_to(assets)}
    build = {"index.html","sw.js","app.js","app.css","manifest.webmanifest","BUILD.json"}
    if not build <= paths.keys():
        raise RuntimeError("The configured CRM web bundle is incomplete.")
    catalog = Catalog()
    csrf_key = secrets.token_bytes(32)

    def csrf(request):
        return hmac.new(csrf_key, (request.cookies.get(cookie) or "logged-out").encode(), sha256).hexdigest()

    async def asset(request):
        name = request.path.removeprefix(prefix) or "index.html"
        request["analyst_pwa_asset"] = True
        response = web.FileResponse(paths[name])
        if name=="manifest.webmanifest":
            response.content_type = "application/manifest+json"
        if name=="sw.js":
            response.headers["Service-Worker-Allowed"] = prefix
        return response

    async def boot(request):
        session = site.session(request)
        public = None
        if session:
            signed, username = session
            with site.authority() as conn:
                allowed = site.authorized(conn,username)
            with site.sessions() as conn:
                expiry = conn.execute("SELECT expires FROM sessions WHERE digest=?", (signed,)).fetchone()
            if allowed and expiry:
                public = dict(context=context(site,username),
                              owner="analyst-web:"+digest([username,identity(site,username)]),
                              expires_at=expiry[0]*1000, synthetic=False)
        return web.json_response(dict(session=public,csrf=csrf(request),synthetic=False))

    def live_user(request, conn):
        session = site.session(request)
        if not session or not site.authorized(conn,session[1]):
            raise RegistryConflict("Войдите в рабочее место.",401)
        return session[1]

    def validate_origin(request):
        site.require_origin(request)
        if not hmac.compare_digest(request.headers.get("X-CRM-CSRF",""),csrf(request)):
            raise RegistryConflict("Подключение обновляется. Повторите запрос.",403)

    async def api(request):
        try:
            validate_origin(request)
            with site.authority() as conn:
                username = live_user(request,conn)
            value = await request.json()
            method, action = value["method"],value["action"]
            query, body = value.get("query",{}),value.get("body",{})
            with site.authority() as conn:
                if not site.authorized(conn,username):
                    raise RegistryConflict("Войдите в рабочее место.",401)
                writable = site.can_write(username)
                if method=="GET":
                    if action=="context":return web.json_response(context(site,username))
                    if action=="registry":return web.json_response(registry(conn,catalog,writable,query))
                    if action=="card":return web.json_response(person_card(conn,catalog,writable,query))
                    if action=="analyst-statistics":return web.json_response(statistics(conn))
                    if action=="changes":return web.json_response(dict(dirty=False,cursor=dict(assignments=0,facts=0),as_of=now()))
                if method=="POST" and action=="telemetry":
                    return web.json_response({"ok":True})
            if method=="POST" and action=="registry-edit":
                if not writable:
                    raise RegistryConflict("Доступна только работа на чтение.",403)
                with closing(sqlite3.connect(site.primary.as_uri()+"?mode=rw",uri=True,timeout=6)) as conn, conn:
                    conn.row_factory=sqlite3.Row
                    conn.execute("BEGIN IMMEDIATE")
                    if not site.can_write(live_user(request,conn)):
                        raise RegistryConflict("Доступна только работа на чтение.",403)
                    return web.json_response(save_rows(conn,catalog,body,site.operator["uid"]))
            raise RegistryConflict("Действие недоступно в этом рабочем месте.",403)
        except RegistryConflict as exc:
            return web.json_response(dict(text=str(exc),error="csrf" if str(exc).startswith("Подключение обновляется") else "registry"),
                                     status=exc.status)
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({"text":"Пакет правок больше 20 МБ."},status=413)
        except (KeyError, AttributeError, TypeError, ValueError):
            return web.json_response({"text":"Некорректные параметры запроса."},status=400)

    async def receipts(request):
        try:
            if request.method=="POST":
                validate_origin(request)
            with site.authority() as conn:
                live_user(request,conn)
            return web.json_response({"entries":[]})
        except RegistryConflict as exc:
            return web.json_response({"text":str(exc)},status=exc.status)

    app.router.add_get(prefix,asset)
    for route in paths:
        app.router.add_get(prefix+route,asset)
    app.router.add_get(prefix+"boot",boot)
    app.router.add_post(prefix+"api",api)
    app.router.add_get(prefix+"receipts",receipts)
    app.router.add_post(prefix+"receipts",receipts)
