"""Read-only CRM identity bridge through existing account/device gates."""
import asyncio
import os
from aiohttp import web
import db
import accounts


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
    if device.get('account_id'):
        import crm_registration
        crm_registration.project_verified_account(uid)
    return {"ok": True, "subject": {"type": role, "actor_uid": uid},
            "device": {"id": int(device["id"]), "account_bound": bool(device.get('account_id'))},
            "me": {"role": role, "name": name}}


def install(app, host):
    async def handler(request):
        result = await asyncio.to_thread(identity, request, host)
        return web.json_response(result, headers={"Cache-Control": "no-store"})
    app.router.add_get("/api/crm-identity", handler)

    def approved_phone(request):
        entry = host._account_device_of_request(request)
        if entry:
            token_id, account_id, token_table = entry
            gate, _ = accounts.device_gate_by_token(token_table, token_id)
            if gate == 'ok':
                with db.connect() as conn:
                    phone = conn.execute("""
                        SELECT a.username
                        FROM account_token_sessions s JOIN account_devices d ON d.id=s.device_id
                        JOIN accounts a ON a.id=d.account_id
                        WHERE s.token_table=? AND s.token_id=? AND a.id=?
                          AND d.kind='phone' AND d.status='active' AND a.status='active'
                    """, (token_table, token_id, account_id)).fetchone()
                if phone:
                    return {**identity(request, host), 'username': phone['username']}
        raise host._json_error(web.HTTPForbidden, 'crm_approved_phone_required')

    async def mobile_session(request):
        if os.getenv('HARD_CRM_MOBILE') != '1':
            raise host._json_error(web.HTTPForbidden, 'crm_mobile_disabled')
        if (request.headers.get('X-Device-Token') or
                (request.headers.get('X-App-Token') and request.headers.get('X-Pult-Token'))):
            raise host._json_error(web.HTTPUnauthorized, 'crm_identity_required')
        return web.json_response(await asyncio.to_thread(approved_phone, request),
                                 headers={'Cache-Control': 'no-store'})

    app.router.add_get("/api/crm-mobile-session", mobile_session)
