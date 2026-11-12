"""Pilot-only routes using existing device/account authentication gates."""

import asyncio, base64, contextlib, json
from aiohttp import web
import db
from . import storage
from . import (
    enabled,
    access,
    analytics,
    documents,
    exchange,
    shifts,
    store,
    telemetry,
    scripts,
    cabinet,
    three_hour,
)
from .contract import CRMError, fields


def install(app, host=None, *, authenticate=None):
    if not enabled() and authenticate is None:
        return

    def principal(request):
        if request.headers.get("X-Device-Token") or request.path.startswith(
            "/admin/crm/"
        ):
            host._admin(request)
            device = request.get("admin_device") or {}
            uid = int(device.get("telegram_id") or -int(device.get("id") or 0))
            if not uid:
                raise CRMError(
                    "Требуется подтверждённое устройство CRM.", "identity_unmapped", 403
                )
            with storage.connect() as conn:
                p = access.from_uid(conn, uid, owner=uid == host.ADMIN_ID, common_read=True)
            return p
        if request.headers.get("X-Pult-Token"):
            uid, manager, names = host._manager_gate(request)
            caps = db.manager_caps(manager.get("role"), is_owner=uid == host.ADMIN_ID)
            with storage.connect() as conn:
                return access.from_uid(
                    conn,
                    uid,
                    owner=uid == host.ADMIN_ID,
                    manager_names=names,
                    manager_write=caps["binds_edit"],
                    common_read=True,
                )
        uid, _ = host._auth(request)
        with storage.connect() as conn:
            return access.from_uid(conn, uid, owner=uid == host.ADMIN_ID, common_read=True)

    async def body(request, limit=4 * 1024 * 1024):
        if request.content_length is not None and request.content_length > limit:
            raise CRMError("Запрос слишком большой.", "size", 413)
        chunks = []
        size = 0
        async for chunk in request.content.iter_chunked(65536):
            size += len(chunk)
            if size > limit:
                raise CRMError("Запрос слишком большой.", "size", 413)
            chunks.append(chunk)
        data = b"".join(chunks)
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise CRMError("Некорректный JSON.")
        if not isinstance(value, dict):
            raise CRMError("Ожидается объект запроса.")
        return value

    def context(p):
        from fields import DROPDOWNS
        from . import tasks

        with storage.connect() as conn:
            cohorts = [
                dict(r)
                for r in conn.execute(
                    "SELECT cohort_id,name,starts_at FROM crm_cohorts"
                )
                if p.owner
                or conn.execute(
                    "SELECT 1 FROM crm_cohort_members WHERE cohort_id=? AND employee_uuid IN ("
                    + ",".join("?" for _ in p.employee_scope)
                    + ")",
                    (r["cohort_id"], *p.employee_scope),
                ).fetchone()
            ]
            staff = tasks.staff(conn, p)
        return dict(
            schema_version=1,
            employee_uuid=p.employee_uuid,
            team_id=p.team_id,
            role=p.role,
            capabilities=sorted(p.capabilities),
            owner=p.owner,
            read_all_contracts=access.reads_all(p),
            timezone=p.timezone,
            fields=fields(),
            dropdowns=DROPDOWNS,
            cohorts=cohorts,
            staff=staff,
        )

    async def handler(request):
        try:
            p = await authenticate(request) if authenticate is not None else await asyncio.to_thread(principal, request)
            action = request.match_info["action"]
            if request.method == "GET":
                if action == "context":
                    result = await asyncio.to_thread(context, p)
                elif action == "portfolio":
                    result = await asyncio.to_thread(
                        store.portfolio,
                        p,
                        request.query.get("search", ""),
                        int(request.query.get("limit", "200")),
                    )
                elif action == "card":
                    result = await asyncio.to_thread(
                        store.card, p, request.query.get("person_uuid", "")
                    )
                elif action == 'contracts':
                    result = await asyncio.to_thread(store.contract_list, p, request.query.get('search',''),
                        request.query.get('work',''), int(request.query.get('limit','100')))
                elif action == "shifts":
                    result = await asyncio.to_thread(
                        shifts.summary, p, request.query.get("employee_uuid")
                    )
                elif action == 'shift-report':
                    result = await asyncio.to_thread(shifts.period_report,p,request.query.get('since'),request.query.get('until'),request.query.get('employee_uuid'))
                elif action == 'cabinet':
                    result = await asyncio.to_thread(cabinet.dashboard,p,request.query.get('month'),request.query.get('employee_uuid'))
                elif action == 'three-hour':
                    result=await asyncio.to_thread(three_hour.report,p)
                elif action == "script":
                    result = await asyncio.to_thread(scripts.state, p)
                elif action == "document":
                    row, path = await asyncio.to_thread(
                        documents.download, p, request.query.get("document_id", "")
                    )
                    response = web.FileResponse(path)
                    response.headers["Content-Type"] = row["media_type"]
                    response.headers["Content-Disposition"] = (
                        "attachment; filename*=UTF-8''"
                        + __import__("urllib.parse", fromlist=["quote"]).quote(
                            row["filename"]
                        )
                    )
                    return response
                else:
                    raise CRMError("Маршрут не найден.", "not_found", 404)
            else:
                raw = await body(
                    request,
                    (
                        65536
                        if action == "telemetry"
                        else (
                            15 * 1024 * 1024
                            if action == "document"
                            else 128 * 1024 * 1024 if action == "import-preview" and authenticate is not None
                            else 4 * 1024 * 1024
                        )
                    ),
                )
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
                if action == "command":
                    result = await asyncio.to_thread(
                        store.command,
                        p,
                        raw,
                        client_version=request.headers.get("X-Client-Version"),
                    )
                elif action in actions:
                    result = await asyncio.to_thread(actions[action], p, raw)
                elif action == "anchor":
                    if set(raw) != {"installation_id"}:
                        raise CRMError("Неизвестные параметры времени.")
                    result = await asyncio.to_thread(
                        store.anchor, p, raw["installation_id"]
                    )
                elif action == "document":
                    if set(raw) != {
                        "document_id",
                        "contract_uuid",
                        "filename",
                        "media_type",
                        "content_base64",
                    }:
                        raise CRMError("Неизвестные поля документа.")
                    try:
                        data = base64.b64decode(raw["content_base64"], validate=True)
                    except (ValueError, TypeError):
                        raise CRMError("Некорректное содержимое документа.")
                    result = await asyncio.to_thread(
                        documents.upload,
                        p,
                        raw["document_id"],
                        raw["contract_uuid"],
                        raw["filename"],
                        raw["media_type"],
                        data,
                    )
                else:
                    raise CRMError("Маршрут не найден.", "not_found", 404)
            return web.json_response(result)
        except CRMError as exc:
            return web.json_response(
                dict(error=exc.code, text=str(exc), details=exc.details),
                status=exc.status,
            )
        except (ValueError, TypeError):
            return web.json_response(
                dict(error="invalid", text="Некорректные параметры запроса."),
                status=400,
            )

    @web.middleware
    async def cutover(request, handler):
        if request.method in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        } and request.path.startswith(("/api/", "/admin/")):
            if not request.path.startswith(
                ("/api/crm/", "/admin/crm/", "/api/account/")
            ) and request.path not in {"/api/client-event", "/api/manager/call-script"}:
                return web.json_response(
                    dict(
                        error="crm_cutover",
                        text="Для записи в пилотную базу откройте кандидат CRM. Исходная очередь сохранена.",
                    ),
                    status=409,
                )
        return await handler(request)

    async def lifecycle(app):
        async def worker():
            while True:
                try:
                    await asyncio.to_thread(analytics.aggregate)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Business commits stay durable; the next pass retries the outbox.
                    pass
                await asyncio.sleep(2)

        task = asyncio.create_task(worker())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # Serialize shared bulk requests BEFORE reading their large JSON bodies.
    # Waiting managers retain their input locally rather than multiplying
    # full-registry objects in the API process while the DB lock is held.
    bulk_lock = asyncio.Lock()

    async def post_handler(request):
        if authenticate is not None and request.match_info['action'] in {
            'export', 'import-preview', 'import-apply'
        }:
            if bulk_lock.locked():
                return web.json_response(dict(error='bulk_busy',
                    text='Выполняется обмен реестром. Повторите запрос после его завершения.'),
                    status=503, headers={'Retry-After': '5'})
            async with bulk_lock:
                return await handler(request)
        return await handler(request)

    app.middlewares.append(cutover)
    app.cleanup_ctx.append(lifecycle)
    for prefix in ("/api/crm/", "/admin/crm/"):
        app.router.add_get(prefix + "{action}", handler)
        app.router.add_post(prefix + "{action}", post_handler)
