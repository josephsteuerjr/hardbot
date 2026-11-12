"""Read-only CRM identity bridge through existing account/device gates."""
import asyncio
from aiohttp import web
import db


def identity(request, host):
    entries = [(key, request.headers.get(key, "").strip()) for key in
               ("X-App-Token", "X-Pult-Token", "X-Device-Token")]
    entries = [(key, token) for key, token in entries if token]
    if len(entries) != 1:
        raise host._json_error(web.HTTPUnauthorized, "crm_identity_required")
    header, token = entries[0]
    if header == "X-App-Token":
        uid, employee = host._auth(request)
        state, device = db.agent_device_check(token, request.remote)
        if state != "ok" or not device or int(device["telegram_id"] or 0) != uid:
            raise host._json_error(web.HTTPUnauthorized, "crm_device_required")
        role = "courier" if db.get_kind(uid) == "courier" else "employee"
        name = employee
    elif header == "X-Pult-Token" and request.headers.get("X-CRM-Entry") != "analyst":
        uid, manager, _ = host._manager_gate(request)
        state, device = db.manager_device_check(token, host._admin_peer(request))
        if state != 'ok' or not device or int(device['telegram_id'] or 0) != uid:
            raise host._json_error(web.HTTPUnauthorized, 'crm_device_required')
        role = "observer" if db.manager_role(manager.get("role")) == "security" else "manager"
        name = manager.get("name") or ""
    else:
        host._admin(request)
        device = request.get("admin_device")
        if not device:
            raise host._json_error(web.HTTPUnauthorized, "crm_device_required")
        uid = int(device.get("telegram_id") or -int(device["id"]))
        role, name = "analyst", device.get("device") or device.get("name") or ""
    if not device or not uid:
        raise host._json_error(web.HTTPUnauthorized, "crm_identity_required")
    return {"ok": True, "subject": {"type": role, "actor_uid": uid},
            "device": {"id": int(device["id"])}, "me": {"role": role, "name": name}}


def install(app, host):
    async def handler(request):
        result = await asyncio.to_thread(identity, request, host)
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    app.router.add_get("/api/crm-identity", handler)
