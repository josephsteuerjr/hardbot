"""
Веб-сервер миниаппа выездника.

aiohttp идёт в комплекте с aiogram — новых зависимостей нет. Раздаёт статику из
./miniapp и небольшое JSON-API поверх db.py.

Личность выездника берётся из подписи Telegram WebApp (`initData`, HMAC по токену
бота) — клиенту доверять не нужно. Локальный дев-режим (`HARDBOT_DEV=1`) разрешает
открыть миниапп в обычном браузере, подставив telegram_id (заголовок `X-Dev-Uid`
или `?dev_uid=`). В боевом режиме дев-обход выключен.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import tempfile
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

import db
import debt
from config import ADMIN_ID, approval_required, lang_of
from fields import BY_KEY, DROPDOWNS
from i18n import FIELD_TITLES, LANGUAGES, t, tf, tv
from keyboards import approve_bind_kb

# CORS на статике/тайлах: по умолчанию НЕ ставим «*» (миниапп и тайлы — same-origin
# через свой aiohttp/Caddy). Кросс-ориджин — только если явно задан CORS_ALLOW_ORIGIN.
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "").strip()

MINIAPP_DIR = Path(__file__).parent / "miniapp"
PHOTO_DIR = Path(os.getenv("HARDBOT_DATA", Path(__file__).parent)) / "photos"
# Свой вектор-тайлсет (Protomaps PMTiles). Если файла нет — карта на растровом OSM.
TILES_FILE = Path(os.getenv("HARDBOT_DATA", Path(__file__).parent)) / "basemap.pmtiles"
# Свой маршрутизатор (OSRM) для геометрии «я → должник» в навигации миниаппа.
# Пусто — навигация рисует честную прямую (haversine), это не баг, а заявленный фолбэк.
OSRM_URL = (os.getenv("OSRM_URL", "") or "").strip()
DEV = (os.getenv("HARDBOT_DEV", "") or "").lower() not in ("", "0", "false", "no")
_MAX_AGE = 24 * 3600  # initData не старше суток

# Поля выезда, которые заполняет выездник (выпадающие списки + свободный текст).
FORM_DROPDOWNS = ["call_status", "contact_with", "who_at_address",
                  "client_location", "purpose_rf", "agreed_payment"]
FORM_TEXT = ["call_comment", "tips"]
# Что показываем во вкладке «Клиент» (плотные строки).
INFO_KEYS = ["birth_date", "phone", "phone_main", "city", "address",
             "address_customer", "extra_address", "responsible", "tips"]
# Вкладка «Долг»: статьи разбивки считает debt.breakdown (набор зависит от
# контрагента), подписи — локализованные, суммы — числа. Раньше здесь лежал
# плоский DEBT_KEYS, из-за которого миниапп рисовал ключи БД («camera_fine»).
PROMISE_KEYS = ["agreed_payment", "promise_date", "last_payment_sum",
                "last_payment_date", "forecast"]
ALLOWED_SAVE = set(FORM_DROPDOWNS) | set(FORM_TEXT)


# --- Проверка подписи Telegram WebApp --------------------------------------
def validate_init_data(init_data: str, token: str, max_age: int = _MAX_AGE) -> dict | None:
    """Разобранные поля initData, если подпись верна и свежа; иначе None."""
    if not init_data or not token:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        return None
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    try:
        if max_age and time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
    except (ValueError, TypeError):
        return None
    if "user" in pairs:
        try:
            pairs["user"] = json.loads(pairs["user"])
        except json.JSONDecodeError:
            pairs["user"] = {}
    return pairs


def _uid(request: web.Request) -> int | None:
    """telegram_id запросившего: из подписанного initData либо из дев-обхода."""
    data = validate_init_data(request.headers.get("X-Init-Data") or "",
                              os.getenv("BOT_TOKEN", ""))
    if data and isinstance(data.get("user"), dict):
        return int(data["user"].get("id") or 0) or None
    if DEV:
        raw = request.headers.get("X-Dev-Uid") or request.query.get("dev_uid")
        try:
            return int(raw) if raw else None
        except ValueError:
            return None
    return None


def _employee(uid: int) -> str | None:
    emp = db.get_binding(uid)
    if emp:
        return emp
    if DEV and uid == ADMIN_ID:  # владельцу в дев-режиме показываем первого из реестра
        emps = db.employees_in_registry()
        return emps[0]["employee"] if emps else None
    return None


def _auth(request: web.Request) -> tuple[int, str]:
    uid = _uid(request)
    if not uid:
        raise web.HTTPUnauthorized(text="auth")
    emp = _employee(uid)
    if not emp:
        raise web.HTTPForbidden(text="not bound")
    return uid, emp


# --- Формовка данных --------------------------------------------------------
def _title(key: str, lang: str) -> str:
    if key in FIELD_TITLES:
        return tf(key, lang)
    return BY_KEY[key].title if key in BY_KEY else key


def _meta(lang: str) -> dict:
    return {
        "dropdowns": {k: [{"value": v, "label": tv(v, lang)} for v in DROPDOWNS[k]]
                      for k in FORM_DROPDOWNS},
        "dropdownTitles": {k: _title(k, lang) for k in FORM_DROPDOWNS},
        "vectorTiles": "/tiles/basemap.pmtiles" if TILES_FILE.exists() else None,
    }


def _person_brief(p: dict) -> dict:
    visited = any(c["visit_date"] for c in p["contracts"])
    lat = lon = None
    for c in p["contracts"]:
        if c["geo_lat"] is not None:
            lat, lon = c["geo_lat"], c["geo_lon"]
            break
    return {"rep": p["rep"], "client": p["client"], "n": p["n"],
            "total": p["total"], "unit": debt.unit(p["partners"]),
            "totals": p["totals"],   # ЗК + Делимобиль → «11 550 000 сум · 132 900 ₽»
            "visited": visited, "lat": lat, "lon": lon}


def _row_get(rows, key):
    return next((r[key] for r in rows if r[key] not in (None, "")), None)


def _person_full(rep: str, lang: str) -> dict | None:
    rows = db.person_contracts(rep)
    if not rows:
        return None
    main = rows[0]
    visited = any(r["visit_date"] for r in rows)
    contracts = [{
        "contract_id": r["contract_id"], "partner": r["partner"],
        "unit": debt.unit([r["partner"]] if r["partner"] else []),
        "total": debt.contract_debt(r),
        "debt": [{"key": k, "title": _title(k, lang), "value": v}
                 for k, v in debt.breakdown(r)],
    } for r in rows]
    info = [{"key": k, "title": _title(k, lang), "value": _row_get(rows, k) or "—"}
            for k in INFO_KEYS]
    promise = [{"key": k, "title": _title(k, lang),
                "value": tv(_row_get(rows, k), lang) if k == "agreed_payment"
                else (_row_get(rows, k) or "—")}
               for k in PROMISE_KEYS]
    form = [{"key": k, "title": _title(k, lang), "current": _row_get(rows, k) or ""}
            for k in FORM_DROPDOWNS]
    return {
        "rep": rep, "client": main["client"], "birth_date": main["birth_date"],
        "visited": visited,
        "status": tv(main["overall_result"] or main["call_status"], lang),
        "assign_date": main["assign_date"], "visit_date": main["visit_date"],
        "address": _row_get(rows, "address"), "phone": _row_get(rows, "phone"),
        "geo": {"lat": main["geo_lat"], "lon": main["geo_lon"], "src": main["geo_src"]},
        "total": sum(debt.contract_debt(r) for r in rows),
        "unit": debt.unit(sorted({r["partner"] for r in rows if r["partner"]})),
        "totals": debt.totals_by_unit(rows),
        "photos": sum(r["photos_count"] or 0 for r in rows),
        "contracts": contracts, "info": info, "promise": promise,
        "form": form,
        "call_comment": _row_get(rows, "call_comment") or "",
        "tips": _row_get(rows, "tips") or "",
    }


# --- Маршрут «я → должник» (навигация в миниаппе) ---------------------------
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _osrm_route(lat1: float, lon1: float, lat2: float, lon2: float, base: str):
    """Геометрия по дорогам через OSRM /route. None при любой ошибке (сеть/таймаут/
    граф не покрывает точку) — вызывающий код тогда честно падает на прямую линию."""
    try:
        url = (f"{base.rstrip('/')}/route/v1/driving/"
               f"{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson")
        req = urllib.request.Request(url, headers={"User-Agent": "hardbot-miniapp/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
        route = data["routes"][0]
        return route["geometry"]["coordinates"], route["distance"] / 1000.0, route["duration"] / 60.0
    except Exception:  # noqa: BLE001
        return None


# --- Хендлеры ---------------------------------------------------------------
async def _index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(MINIAPP_DIR / "index.html")


async def _tiles(request: web.Request) -> web.StreamResponse:
    if not TILES_FILE.exists():
        raise web.HTTPNotFound()
    # FileResponse сам поддерживает Range — PMTiles тянет тайлы байт-диапазонами.
    # CORS сужен: «*» убран; заголовок ставим только при явно заданном CORS_ALLOW_ORIGIN.
    headers = {"Access-Control-Allow-Origin": CORS_ALLOW_ORIGIN} if CORS_ALLOW_ORIGIN else {}
    return web.FileResponse(TILES_FILE, headers=headers)


async def _bootstrap(request: web.Request) -> web.Response:
    uid = _uid(request)
    if not uid:
        raise web.HTTPUnauthorized(text="auth")
    lang = lang_of(uid)
    emp = _employee(uid)
    if not emp:
        pend = db.pending_binding(uid)
        if pend:   # привязка есть, но ждёт аппрува ADMIN — данные не отдаём
            return web.json_response({"ok": True, "pending": True, "uid": uid, "lang": lang,
                                      "employee": pend["employee"]})
        return web.json_response({   # ещё не выбрал себя → онбординг прямо в миниаппе
            "ok": True, "needsOnboarding": True, "uid": uid, "lang": lang,
            "employees": [{"employee": e["employee"], "contracts": e["contracts"]}
                          for e in db.employees_in_registry()],
            "langs": [{"code": c, "name": n} for c, n in LANGUAGES.items()]})
    grouped = db.persons_for_employee(emp)
    persons = [_person_brief(p) for p in grouped]
    try:
        import scoring
        idx = scoring.priority_index()
        for pb, p in zip(persons, grouped):
            s = idx.get(f"{p['client'] or ''}|{p['birth_date'] or ''}")
            if s:
                pb["score"], pb["reason"], pb["city"] = s["score"], s["reason"], s["city"]
        persons.sort(key=lambda x: x.get("score", -1), reverse=True)   # приоритет наверху
    except Exception as e:  # noqa: BLE001
        logging.warning("priority_index: %s", e)
    return web.json_response({"ok": True, "uid": uid, "employee": emp,
                              "kind": db.get_kind(uid), "lang": lang,
                              "persons": persons, "meta": _meta(lang)})


async def _bind(request: web.Request) -> web.Response:
    uid = _uid(request)
    if not uid:
        raise web.HTTPUnauthorized(text="auth")
    body = await request.json()
    kind = "courier" if body.get("kind") == "courier" else "agent"
    employee = (body.get("employee") or "").strip()
    lang = body.get("lang")
    if lang in ("ru", "uz", "tg"):
        db.set_lang(uid, lang)
    if not employee:
        raise web.HTTPBadRequest(text="employee required")
    if approval_required():
        # ИБ: привязка ждёт аппрува ADMIN; доступа к данным пока нет.
        created = db.bind_agent(uid, employee, kind, approved=False)
        db.audit_access(uid, "bind_request", employee)
        if created:                                # шлём заявку ADMIN только при первом создании
            bot = request.app.get("bot")
            if bot and ADMIN_ID:
                try:
                    await bot.send_message(
                        ADMIN_ID, t("bind_request_admin", lang_of(ADMIN_ID), id=uid, emp=employee),
                        reply_markup=approve_bind_kb(uid, lang_of(ADMIN_ID)))
                except Exception:  # noqa: BLE001
                    pass
        return web.json_response({"ok": True, "pending": True, "employee": None,
                                  "kind": db.get_kind(uid)})
    ok = db.bind_agent(uid, employee, kind)   # одноразово, сразу активна
    return web.json_response({"ok": ok, "employee": db.get_binding(uid), "kind": db.get_kind(uid)})


async def _marker(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    body = await request.json()
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="lat/lon")
    rep = (body.get("rep") or "").strip() or None
    if rep:   # метка на кейс — проверим, что кейс наш (или владелец)
        row = db.get_client(rep)
        if not row or (row["responsible"] != emp and uid != ADMIN_ID):
            raise web.HTTPNotFound(text="person")
        db.audit_access(uid, "marker", rep)
    label = (body.get("label") or "").strip() or None
    mid = db.add_marker(uid, db.get_kind(uid), lat, lon, client_rep=rep, label=label)
    out = {"ok": True, "id": mid}
    if rep:
        out["person"] = _person_full(rep, lang_of(uid))
    return web.json_response(out)


async def _markers(request: web.Request) -> web.Response:
    uid, _ = _auth(request)
    rows = db.markers_for_telegram(uid)
    return web.json_response({"ok": True, "markers": [
        {"id": r["id"], "lat": r["lat"], "lon": r["lon"], "label": r["label"],
         "rep": r["client_rep"], "at": r["created_at"]} for r in rows]})


async def _route(request: web.Request) -> web.Response:
    """Геометрия маршрута от живой точки выездника до должника — для полноэкранной
    навигации в миниаппе. OSRM, если поднят (`OSRM_URL`); иначе честная прямая —
    фронт в обоих случаях получает линию + км + минуты и рисует её на карте."""
    uid, emp = _auth(request)
    rep = request.query.get("rep", "")
    try:
        lat = float(request.query.get("lat", ""))
        lon = float(request.query.get("lon", ""))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="lat/lon")
    row = db.get_client(rep)
    if not row or (row["responsible"] != emp and uid != ADMIN_ID):
        raise web.HTTPNotFound(text="person")
    glat, glon = row["geo_lat"], row["geo_lon"]
    if glat is None or glon is None:
        raise web.HTTPNotFound(text="geo")
    glat, glon = float(glat), float(glon)
    result = None
    if OSRM_URL:
        result = await asyncio.get_event_loop().run_in_executor(
            None, _osrm_route, lat, lon, glat, glon, OSRM_URL)
    if result:
        line, km, minutes = result
        return web.json_response({"ok": True, "line": line, "km": round(km, 1),
                                  "minutes": round(minutes), "src": "osrm"})
    km = _haversine_km(lat, lon, glat, glon)
    minutes = km / 30.0 * 60.0   # тот же запас скорости 30 км/ч, что и в движке без OSRM
    return web.json_response({"ok": True, "line": [[lon, lat], [glon, glat]],
                              "km": round(km, 1), "minutes": round(minutes), "src": "straight"})


async def _person(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    rep = request.query.get("rep", "")
    row = db.get_client(rep)
    if not row or (row["responsible"] != emp and uid != ADMIN_ID):
        raise web.HTTPNotFound(text="person")
    db.audit_access(uid, "view_card", rep)              # ИБ: доступ к ПД в журнал
    data = _person_full(rep, lang_of(uid))
    if not data:
        raise web.HTTPNotFound(text="person")
    return web.json_response(data)


async def _save(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    body = await request.json()
    rep = body.get("rep", "")
    row = db.get_client(rep)
    if not row or (row["responsible"] != emp and uid != ADMIN_ID):
        raise web.HTTPNotFound(text="person")
    values = {k: v for k, v in (body.get("values") or {}).items() if k in ALLOWED_SAVE}
    db.audit_access(uid, "save_visit" if body.get("finish") else "save_card", rep)
    if values:
        db.update_person(rep, values)  # пишется во ВСЕ договоры человека, сразу в БД
    if body.get("finish"):
        db.update_person(rep, {"visit_date": date.today().isoformat()})
        cur = db.get_client(rep)
        db.log_visit(rep, uid, f"Выезд (миниапп). Статус: {cur['call_status']}; "
                     f"Договорённость: {cur['agreed_payment']}; {cur['call_comment'] or ''}")
        await _notify_group(request, cur, uid)
    data = _person_full(rep, lang_of(uid))
    return web.json_response({"ok": True, "person": data})


async def _photo(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    rep = request.query.get("rep", "")
    row = db.get_client(rep)
    if not row or (row["responsible"] != emp and uid != ADMIN_ID):
        raise web.HTTPNotFound(text="person")
    db.audit_access(uid, "photo", rep)
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "photo":
        raise web.HTTPBadRequest(text="no file")
    dest = PHOTO_DIR / rep
    dest.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(field.filename or "")[1][:8] or ".jpg"
    path = dest / f"{int(time.time()*1000)}{ext}"
    with open(path, "wb") as f:
        while chunk := await field.read_chunk():
            f.write(chunk)
    n = db.add_photo(rep)
    return web.json_response({"ok": True, "photos": n})


async def _lang(request: web.Request) -> web.Response:
    uid, _ = _auth(request)
    lang = (await request.json()).get("lang", "")
    if lang in ("ru", "uz", "tg"):
        db.set_lang(uid, lang)
    return web.json_response({"ok": True})


async def _notify_group(request: web.Request, row, uid: int) -> None:
    bot = request.app.get("bot")
    if not bot:
        return
    try:
        from notify import post_to_group
        await post_to_group(bot, text=(
            f"✅ Выезд отработан (миниапп)\n👤 {row['client']} ({row['contract_id']})\n"
            f"🧑‍💼 id {uid}\n"
            f"📝 {row['call_status'] or '—'} · {row['agreed_payment'] or '—'}\n"
            f"💬 {row['call_comment'] or '—'}"))
    except Exception:  # noqa: BLE001
        pass


# --- Admin API (для десктоп-пульта; авторизация — ключ ADMIN_API_KEY) --------
def _admin(request: web.Request) -> None:
    key = os.getenv("ADMIN_API_KEY", "")
    if not key or request.headers.get("X-Admin-Key") != key:
        raise web.HTTPUnauthorized(text="admin")


async def _admin_stats(request: web.Request) -> web.Response:
    _admin(request)
    s = db.stats_all()
    return web.json_response({
        "clients": s["total"], "visited": s["visited"], "markers": len(db.list_markers()),
        "employees": [{"employee": e["employee"], "contracts": e["contracts"]}
                      for e in db.employees_in_registry()],
        "bindings": [{"telegram_id": b["telegram_id"], "employee": b["employee"],
                      "kind": (b["kind"] if "kind" in b.keys() else "agent")}
                     for b in db.list_bindings()],
        "by_result": s["by_result"],
        "period": {"from": db.get_period()[0] or "", "to": db.get_period()[1] or ""},
    })


async def _admin_period(request: web.Request) -> web.Response:
    _admin(request)
    if request.method == "POST":
        b = await request.json()
        db.set_period(b.get("from", ""), b.get("to", ""))
    f, t = db.get_period()
    return web.json_response({"ok": True, "from": f or "", "to": t or ""})


async def _admin_import(request: web.Request) -> web.Response:
    _admin(request)
    reader = await request.multipart()
    field = await reader.next()
    if field is None:
        raise web.HTTPBadRequest(text="no file")
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    with os.fdopen(fd, "wb") as f:
        while chunk := await field.read_chunk():
            f.write(chunk)
    try:
        report = db.import_xlsx(tmp)
        try:
            import scoring
            report["geocoded"] = scoring.geocode_city_centroids()
        except Exception as e:  # noqa: BLE001
            logging.warning("geocode after import: %s", e)
    finally:
        os.unlink(tmp)
    return web.json_response(report)


async def _admin_import_json(request: web.Request) -> web.Response:
    """Импорт из пульта: гриды листов уже распарсены на клиенте (calamine),
    сервер только матчит заголовки и делает upsert — openpyxl не участвует."""
    _admin(request)
    body = await request.json()
    grid = db.pick_grid(body.get("sheets") or [])
    report = db.import_grid(grid)
    try:
        import scoring
        report["geocoded"] = scoring.geocode_city_centroids()
    except Exception as e:  # noqa: BLE001
        logging.warning("geocode after import: %s", e)
    return web.json_response(report)


async def _admin_export_json(request: web.Request) -> web.Response:
    """Экспорт реестра для пульта: схема колонок + строки. xlsx собирает пульт
    (rust_xlsxwriter), сервер отдаёт чистые данные."""
    _admin(request)
    return web.json_response({"fields": db.export_schema(), "rows": db.registry_rows()})


async def _admin_markers_json(request: web.Request) -> web.Response:
    _admin(request)
    return web.json_response({"rows": db.markers_rows()})


async def _admin_export(request: web.Request) -> web.StreamResponse:
    _admin(request)
    p = os.path.join(tempfile.gettempdir(), "registry_export.xlsx")
    db.export_xlsx(p)
    return web.FileResponse(p, headers={"Content-Disposition": "attachment; filename=registry_export.xlsx"})


async def _admin_markers_xlsx(request: web.Request) -> web.StreamResponse:
    _admin(request)
    p = os.path.join(tempfile.gettempdir(), "markers.xlsx")
    db.export_markers(p)
    return web.FileResponse(p, headers={"Content-Disposition": "attachment; filename=markers.xlsx"})


async def _admin_employees(request: web.Request) -> web.Response:
    """Список ФИО из реестра с уже проставленной привязкой — для экрана «Роли»
    в пульте. Заказчик вписывает telegram_id каждому ФИО, тип (выездник/курьер)."""
    _admin(request)
    return web.json_response({"employees": db.employees_with_bindings()})


async def _admin_bind(request: web.Request) -> web.Response:
    """Предзаданная привязка из пульта: {telegram_id:int, employee:str, kind:str}.
    Создать/обновить с approved=1 — работник с этим id сразу привязан к клиентам
    (без самовыбора в боте). Upsert через db.set_bind (по employee и telegram_id)."""
    _admin(request)
    body = await request.json()
    try:
        tid = int(body["telegram_id"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="telegram_id")
    employee = (body.get("employee") or "").strip()
    if not employee:
        raise web.HTTPBadRequest(text="employee required")
    kind = "courier" if body.get("kind") == "courier" else "agent"
    db.set_bind(tid, employee, kind)
    return web.json_response({"ok": True, "telegram_id": tid,
                              "employee": employee, "kind": kind})


async def _admin_unbind(request: web.Request) -> web.Response:
    _admin(request)
    body = await request.json()
    db.unbind(int(body["telegram_id"]))
    return web.json_response({"ok": True})


async def _admin_guests(request: web.Request) -> web.Response:
    """Список гостей (кто писал боту) — для выбора telegram_id при привязке в пульте.
    {guests:[{telegram_id, username, name, last_seen, bound_employee|null}]}. Свежие сверху."""
    _admin(request)
    return web.json_response({"guests": db.guests()})


# Формат токена @BotFather: <digits>:<35+ буквенно-цифровых/подчёркивание/дефис>.
_TOKEN_RE = re.compile(r"^\d+:[\w-]{30,}$")


async def _admin_bot_token(request: web.Request) -> web.Response:
    """Сменить токен бота: {token}. Валидируем формат, пишем в .env (+ settings),
    процесс подхватит после РЕСТАРТА (env читается на старте) — сами НЕ перезапускаем.
    Токен НЕ логируем и НЕ возвращаем. Ответ: {ok, need_restart:true}."""
    _admin(request)
    body = await request.json()
    token = (body.get("token") or "").strip()
    if not _TOKEN_RE.match(token):
        raise web.HTTPBadRequest(text="bad token format")
    try:
        db.set_bot_token(token)
    except OSError as e:  # не удалось записать .env — сообщаем без утечки токена
        logging.warning("set_bot_token: write failed (%s)", type(e).__name__)
        raise web.HTTPInternalServerError(text="cannot persist token")
    logging.info("BOT_TOKEN changed via admin API (restart required to apply)")
    return web.json_response({"ok": True, "need_restart": True})


async def _admin_geocode(request: web.Request) -> web.Response:
    _admin(request)
    import geocode
    asyncio.get_event_loop().run_in_executor(None, geocode.geocode_all)
    return web.json_response({"ok": True, "started": True})


async def _admin_priority(request: web.Request) -> web.Response:
    _admin(request)
    import scoring
    q = request.rel_url.query

    def fnum(name, default):
        try:
            return float(q.get(name, default))
        except (TypeError, ValueError):
            return default

    weights = {"debt": fnum("w_debt", 50), "cluster": fnum("w_cluster", 30),
               "fresh": fnum("w_fresh", 20)}
    audience = q.get("audience", "courier")
    limit = int(fnum("limit", 60))
    res = await asyncio.get_event_loop().run_in_executor(
        None, scoring.priority_from_db, weights, audience, limit)
    return web.json_response(res)


async def _admin_activity(request: web.Request) -> web.Response:
    """Активность работников (визиты/метки за 7/30 дней, простой) — вкладка
    «Активность» в пульте. Считается на лету из visits/markers по created_at."""
    _admin(request)
    return web.json_response({"workers": db.activity_stats(),
                              "generated_at": datetime.now().isoformat(timespec="seconds")})


async def _admin_managers(request: web.Request) -> web.Response:
    """Экран «Руководители» в пульте: список руководителей + справочники для форм
    (города и сотрудники из реестра, карта город→команда) + настройки автопуша."""
    _admin(request)
    return web.json_response({
        "managers": db.list_managers(),
        "cities": db.cities_in_registry(),
        "employees": [{"employee": e["employee"], "contracts": e["contracts"]}
                      for e in db.employees_in_registry()],
        "city_employees": db.city_employees(),
        "digest": {
            "time": db.get_setting("digest_time") or "16:30",
            "enabled": (db.get_setting("digest_enabled") or "1").strip().lower()
                       not in ("0", "false", "no", "off"),
        },
    })


async def _admin_manager_save(request: web.Request) -> web.Response:
    """Создать/обновить руководителя: {telegram_id:int, name:str, cities:[str], employees:[str]}."""
    _admin(request)
    body = await request.json()
    try:
        tid = int(body["telegram_id"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="telegram_id")
    name = (body.get("name") or "").strip()
    cities = [str(c).strip() for c in (body.get("cities") or []) if str(c).strip()]
    employees = [str(e).strip() for e in (body.get("employees") or []) if str(e).strip()]
    db.set_manager(tid, name, cities, employees)
    return web.json_response({"ok": True, "manager": db.get_manager(tid)})


async def _admin_manager_delete(request: web.Request) -> web.Response:
    _admin(request)
    body = await request.json()
    try:
        tid = int(body["telegram_id"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="telegram_id")
    db.delete_manager(tid)
    return web.json_response({"ok": True})


async def _admin_digest_settings(request: web.Request) -> web.Response:
    """GET/POST настроек автопуша дайджеста: {time:"HH:MM", enabled:bool} (время по МСК)."""
    _admin(request)
    if request.method == "POST":
        b = await request.json()
        time_raw = (b.get("time") or "").strip()
        if time_raw:
            if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", time_raw):
                raise web.HTTPBadRequest(text="bad time (HH:MM)")
            db.set_setting("digest_time", time_raw)
        if "enabled" in b:
            db.set_setting("digest_enabled", "1" if b.get("enabled") else "0")
    return web.json_response({
        "ok": True,
        "time": db.get_setting("digest_time") or "16:30",
        "enabled": (db.get_setting("digest_enabled") or "1").strip().lower()
                   not in ("0", "false", "no", "off"),
    })


async def _admin_digest_preview(request: web.Request) -> web.Response:
    """Предпросмотр сводки руководителя (текст) для пульта: ?telegram_id=."""
    _admin(request)
    import digest
    try:
        tid = int(request.query.get("telegram_id", ""))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="telegram_id")
    mgr = db.get_manager(tid)
    if not mgr:
        raise web.HTTPNotFound(text="manager")
    text, _ = digest.summary_view(mgr, digest.today_iso())
    return web.json_response({"ok": True, "text": text})


async def _admin_restart(request: web.Request) -> web.Response:
    """Плановый перезапуск процесса из пульта — чтобы применить новый BOT_TOKEN
    (переключение на другой бот) и заново поставить menu-button миниаппа новому боту.
    Работает в паре с супервизором, который поднимает процесс обратно: docker-compose
    `restart: unless-stopped` (наш случай) или systemd. Ответ отдаём ДО выхода —
    os._exit через call_later, чтобы клиент получил подтверждение."""
    _admin(request)
    logging.warning("Перезапуск процесса по запросу admin-API (применение нового токена)")
    asyncio.get_event_loop().call_later(0.5, lambda: os._exit(0))
    return web.json_response({"ok": True, "restarting": True})


async def _admin_reset(request: web.Request) -> web.Response:
    """Очистка данных для пульта. {scope:"visits"} — сброс полей визитов у всех клиентов
    (новая сессия, портфель остаётся); {scope:"all"} — полное удаление реестра под свежий
    импорт (привязки сотрудников сохраняются). Оба — под ADMIN_API_KEY."""
    _admin(request)
    scope = (await request.json()).get("scope")
    if scope == "all":
        return web.json_response(db.wipe_registry())
    if scope == "visits":
        return web.json_response(db.reset_visits())
    raise web.HTTPBadRequest(text="scope: 'visits' | 'all'")


def _apply_cors(request: web.Request, resp: web.StreamResponse) -> None:
    """Навесить CORS-заголовки на ЛЮБОЙ ответ, включая ошибочный.

    КРИТИЧНО: заголовки надо ставить и на ответы-ошибки (401/404/400/500). Если хендлер
    кинул HTTPException или упал, а CORS-заголовков нет — браузер/webview пульта блокирует
    ответ и показывает «Failed to fetch» вместо настоящего статуса. Так, например, POST на
    отсутствующую (старый сервер) ручку `/admin/reset` возвращает 404, но без заголовков он
    выглядит как «нет связи» — реальную причину не видно."""
    if request.path.startswith("/admin/"):
        # /admin/* — десктоп-пульт (Tauri), по природе кросс-ориджин к серверу (грузится не
        # с этого домена). Ручки уже гейтятся секретным ключом (ADMIN_API_KEY в _admin()) —
        # открытый CORS здесь не расширяет поверхность атаки (без ключа не пройти запрос),
        # а без него браузер/webview молча блокирует preflight → "Failed to fetch" на КАЖДЫЙ
        # POST-вызов пульта (не только явно проверенный при разработке).
        resp.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN or "*"
        resp.headers["Access-Control-Allow-Headers"] = "X-Admin-Key, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    elif CORS_ALLOW_ORIGIN:
        # Остальное (миниапп /api/*) — same-origin по умолчанию (свой aiohttp за Caddy),
        # заголовок ставим только при явно заданном CORS_ALLOW_ORIGIN.
        resp.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
        resp.headers["Access-Control-Allow-Headers"] = "X-Admin-Key, X-Init-Data, X-Dev-Uid, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"


@web.middleware
async def _cors(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
        _apply_cors(request, resp)
        return resp
    try:
        resp = await handler(request)
    except web.HTTPException as exc:
        # HTTPException — это сам по себе Response: навешиваем CORS и отдаём с настоящим
        # статусом (401/404/400/…), чтобы пульт увидел код, а не «Failed to fetch».
        _apply_cors(request, exc)
        raise
    except Exception:
        # Непредвиденная ошибка (500). Тоже отдаём с CORS-заголовками — иначе в пульте вместо
        # «Ошибка 500» будет «нет связи». Трейс пишем в лог сервера.
        logging.exception("необработанная ошибка в %s %s", request.method, request.path)
        resp = web.json_response({"error": "internal"}, status=500)
        _apply_cors(request, resp)
        return resp
    _apply_cors(request, resp)
    return resp


def build_app(bot=None) -> web.Application:
    app = web.Application(client_max_size=256 * 1024 * 1024, middlewares=[_cors])
    app["bot"] = bot
    app.router.add_get("/", _index)
    app.router.add_get("/admin/stats", _admin_stats)
    app.router.add_post("/admin/import", _admin_import)
    app.router.add_post("/admin/import.json", _admin_import_json)
    app.router.add_get("/admin/export", _admin_export)
    app.router.add_get("/admin/export.json", _admin_export_json)
    app.router.add_get("/admin/markers.xlsx", _admin_markers_xlsx)
    app.router.add_get("/admin/markers.json", _admin_markers_json)
    app.router.add_get("/admin/employees", _admin_employees)
    app.router.add_get("/admin/guests", _admin_guests)
    app.router.add_post("/admin/bind", _admin_bind)
    app.router.add_post("/admin/unbind", _admin_unbind)
    app.router.add_post("/admin/bot_token", _admin_bot_token)
    app.router.add_post("/admin/geocode", _admin_geocode)
    app.router.add_get("/admin/priority", _admin_priority)
    app.router.add_get("/admin/activity", _admin_activity)
    app.router.add_post("/admin/restart", _admin_restart)
    app.router.add_post("/admin/reset", _admin_reset)
    app.router.add_get("/admin/managers", _admin_managers)
    app.router.add_post("/admin/manager", _admin_manager_save)
    app.router.add_post("/admin/manager_delete", _admin_manager_delete)
    app.router.add_get("/admin/digest_settings", _admin_digest_settings)
    app.router.add_post("/admin/digest_settings", _admin_digest_settings)
    app.router.add_get("/admin/digest_preview", _admin_digest_preview)
    app.router.add_get("/admin/period", _admin_period)
    app.router.add_post("/admin/period", _admin_period)
    app.router.add_get("/tiles/basemap.pmtiles", _tiles)
    app.router.add_get("/api/bootstrap", _bootstrap)
    app.router.add_get("/api/person", _person)
    app.router.add_get("/api/route", _route)
    app.router.add_post("/api/save", _save)
    app.router.add_post("/api/photo", _photo)
    app.router.add_post("/api/lang", _lang)
    app.router.add_post("/api/bind", _bind)
    app.router.add_post("/api/marker", _marker)
    app.router.add_get("/api/markers", _markers)
    return app


async def start_site(bot=None, host: str = "0.0.0.0", port: int = 8080):
    """Поднять веб-сервер рядом с поллингом (один asyncio-loop)."""
    runner = web.AppRunner(build_app(bot))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner


if __name__ == "__main__":
    # Автономный запуск для локального теста миниаппа без бота: HARDBOT_DEV=1 python web.py
    db.init_db()
    web.run_app(build_app(), host="127.0.0.1", port=int(os.getenv("MINIAPP_PORT", "8080")))
