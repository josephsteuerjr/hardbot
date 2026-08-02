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
import functools
import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from aiohttp.abc import AbstractAccessLogger

import addr_cis                       # разбор произвольного адреса для навигации
import db
import debt
import geoindex                       # офлайн-справочник координат (без похода в сеть)
from config import ADMIN_ID, approval_required, lang_of
from exchange_contract import (
    ExchangeContractError,
    FORM_DROPDOWN_KEYS,
    FORM_PROTOCOL_VERSION,
    FORM_TEXT_KEYS,
    OFFICE_RESULT_SNAPSHOT_KEY,
    contract_binding,
    validate_contract_binding,
)
from fields import BY_KEY, DROPDOWNS
from i18n import FIELD_TITLES, LANGUAGES, t, tf, tv
import maplink
import routing_client
import stt_client
from keyboards import approve_bind_kb
from photostore import PHOTO_DIR, day_photos

# CORS на статике/тайлах: по умолчанию НЕ ставим «*» (миниапп и тайлы — same-origin
# через свой aiohttp/Caddy). Кросс-ориджин — только если явно задан CORS_ALLOW_ORIGIN.
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "").strip()

MINIAPP_DIR = Path(__file__).parent / "miniapp"
# Свой вектор-тайлсет (Protomaps PMTiles). Если файла нет — карта на растровом OSM.
TILES_FILE = Path(os.getenv("HARDBOT_DATA", Path(__file__).parent)) / "basemap.pmtiles"
# Маршрутизаторы UZ/TJ проверяются по расстоянию привязки к дорожному графу: соседний
# граф тоже может вернуть Ok, но «притянуть» точку на десятки километров к границе.
# OSRM_URL оставлен как совместимый одиночный фолбэк.
OSRM_URL = (os.getenv("OSRM_URL", "") or "").strip()
OSRM_URL_UZ = (os.getenv("OSRM_URL_UZ", "") or "").strip()
OSRM_URL_TJ = (os.getenv("OSRM_URL_TJ", "") or "").strip()
VROOM_URL = (os.getenv("VROOM_URL", "") or "").strip()
# Голос распознаёт уже прогретая модель Praxis через authenticated Unix socket.
# Local fallback намеренно отсутствует: в лимит hardbot2 вторая модель не помещается
# и пять раз приводила к cgroup OOM. Некорректный/пустой backend = фича выключена.
VOICE_BACKEND = (os.getenv("VOICE_BACKEND", "off") or "off").strip().casefold()
PRAXIS_STT_SOCKET = (
    os.getenv("PRAXIS_STT_SOCKET", "/run/praxis-stt/stt.sock") or ""
).strip()
PRAXIS_STT_TOKEN_FILE = (
    os.getenv(
        "PRAXIS_STT_TOKEN_FILE",
        "/run/secrets/praxis-stt-client.token",
    )
    or ""
).strip()
try:
    VOICE_TIMEOUT_SECONDS = max(
        5.0,
        min(float(os.getenv("VOICE_TIMEOUT_SECONDS", "60")), 70.0),
    )
except (TypeError, ValueError):
    VOICE_TIMEOUT_SECONDS = 60.0
DEV = (os.getenv("HARDBOT_DEV", "") or "").lower() not in ("", "0", "false", "no")
_MAX_AGE = 24 * 3600  # initData не старше суток

# Фото с iPhone после сна PWA раньше повторно уходило как File из IndexedDB через
# FormData. WebKit мог выставить multipart boundary в заголовке, но не записать сам
# multipart-body. Новая оболочка материализует байты и шлёт raw-body; 25 МБ с запасом
# покрывают реальные снимки (на live максимум был <9 МБ) и не дают занять весь
# client_max_size одним файлом.
PHOTO_MAX_BYTES = 25 * 1024 * 1024
_PHOTO_CHUNK = 64 * 1024
_PHOTO_MAGIC_BYTES = 256
_PHOTO_EXTENSIONS = {
    "jpeg": {".jpg", ".jpeg", ".jpe", ".jfif"},
    "png": {".png"},
    "gif": {".gif"},
    "webp": {".webp"},
    "heif": {".heic", ".heif"},
    "avif": {".avif"},
}
_PHOTO_CANONICAL_EXT = {
    "jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp",
    "heif": ".heic", "avif": ".avif",
}
_PHOTO_SUFFIXES = frozenset().union(*_PHOTO_EXTENSIONS.values())

# Поля формы и её протокол берутся из того же version+SHA-locked контракта, что
# bot snapshot, Excel enrichment и clearance. Смена списка в одном компоненте
# теперь блокирует контур вместо частично совместимой выгрузки.
FORM_DROPDOWNS = list(FORM_DROPDOWN_KEYS)
# Комментарий выездника живёт в visit_comment («Комментарий текущего месяца
# выезда»). До 18.07.2026 форма ошибочно писала его в call_comment («комментарий
# звонка») — в выгрузке он сидел не в той колонке; легаси-сохранения ремапятся
# в _save, старые данные перенесены миграцией на проде.
FORM_TEXT = list(FORM_TEXT_KEYS)
# Что показываем во вкладке «Клиент» (плотные строки).
INFO_KEYS = ["birth_date", "phone", "phone_main", "city", "address",
             "address_customer", "extra_address", "responsible", "tips"]
# Вкладка «Долг»: статьи разбивки считает debt.breakdown (набор зависит от
# контрагента), подписи — локализованные, суммы — числа. Раньше здесь лежал
# плоский DEBT_KEYS, из-за которого миниапп рисовал ключи БД («camera_fine»).
PROMISE_KEYS = ["agreed_payment", "promise_date", "last_payment_sum",
                "last_payment_date", "forecast"]
ALLOWED_SAVE = set(FORM_DROPDOWNS) | set(FORM_TEXT)
DATA_EXCHANGE_CONTRACT = contract_binding()


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
    """telegram_id запросившего: из подписанного initData (внутри Телеги), из токена
    автономного PWA (вне Телеги) либо из дев-обхода."""
    data = validate_init_data(request.headers.get("X-Init-Data") or "",
                              os.getenv("BOT_TOKEN", ""))
    if data and isinstance(data.get("user"), dict):
        return int(data["user"].get("id") or 0) or None
    # Автономное приложение (PWA вне Телеги): авторизация по выданному ботом токену.
    # ТОЛЬКО из заголовка — в query токен нельзя (осел бы в access-логах/Referer/истории).
    tok = (request.headers.get("X-App-Token") or "").strip()
    if tok:
        uid = db.uid_for_app_token(tok)
        if uid:
            db.touch_app_token(tok)   # скользящие 30 дней: пользуются — не протухает
            return uid
    if DEV:
        raw = request.headers.get("X-Dev-Uid") or request.query.get("dev_uid")
        try:
            return int(raw) if raw else None
        except ValueError:
            return None
    return None


def _manager_scope(uid: int) -> list[str]:
    """Сотрудники, чьи портфели вправе открыть этот руководитель (или владелец)."""
    return db.manager_employees(uid, is_owner=(uid == ADMIN_ID))


def _employee(uid: int) -> str | None:
    """Кем пользователь работает в приложении.

    У выездника это его привязка. У РУКОВОДИТЕЛЯ привязки нет и быть не должно —
    у него своя роль, — поэтому он «действует от имени» одного из своих сотрудников
    и переключается между ними. Без этого руководитель со ссылкой входа упирался
    в 403 «not bound»: ссылка выдавалась, а приложение не пускало."""
    emp = db.get_binding(uid)
    if emp:
        return emp
    acting = db.acting_employee(uid, _manager_scope(uid))
    if acting:
        return acting
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
        "formProtocolVersion": FORM_PROTOCOL_VERSION,
        "dataExchangeContract": DATA_EXCHANGE_CONTRACT,
        "vectorTiles": "/tiles/basemap.pmtiles" if TILES_FILE.exists() else None,
        # Микрофон показываем только при полностью заданном UDS-контракте.
        "voice": _voice_configured(),
    }


def _person_brief(p: dict) -> dict:
    visited = any(db.bot_visit_snapshot(c) is not None for c in p["contracts"])
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
    visit = next((v for r in rows if (v := db.bot_visit_snapshot(r)) is not None), None)
    visited = visit is not None
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
    form = []
    for k in FORM_DROPDOWNS:
        # До первого доказанного визита форма всегда пустая. Значения HARD-полей
        # из master допустимы только как read-only контекст в карточке: иначе
        # простое открытие и сохранение формы превращало старый импорт в
        # bot_visit_data. Пользовательский черновик восстанавливает клиент из
        # отдельного localStorage и присылает только явно затронутые поля.
        current = visit.get(k, "") if visit is not None else ""
        if current and current not in DROPDOWNS[k]:
            current = ""
        form.append({"key": k, "title": _title(k, lang), "current": current})
    return {
        "rep": rep, "client": main["client"], "birth_date": main["birth_date"],
        "visited": visited,
        "status": tv((visit or {}).get(OFFICE_RESULT_SNAPSHOT_KEY)
                     or main["overall_result"]
                     or main["call_status"], lang),
        "assign_date": main["assign_date"],
        "visit_date": (visit or {}).get("visit_date"),
        "address": _row_get(rows, "address"), "phone": _row_get(rows, "phone"),
        "geo": {"lat": main["geo_lat"], "lon": main["geo_lon"], "src": main["geo_src"]},
        "total": sum(debt.contract_debt(r) for r in rows),
        "unit": debt.unit(sorted({r["partner"] for r in rows if r["partner"]})),
        "totals": debt.totals_by_unit(rows),
        "photos": sum(r["photos_count"] or 0 for r in rows),
        "contracts": contracts, "info": info, "promise": promise,
        "form": form,
        "visit_comment": (visit or {}).get("visit_comment") or "",
        # call_comment оставлен для чтения доказанных legacy snapshot старой
        # оболочкой; сырой master-комментарий форму не префиллит.
        "call_comment": (visit or {}).get("call_comment") or "",
        "tips": (visit or {}).get("tips") or "",
    }


# --- Маршрут «я → должник» (навигация в миниаппе) ---------------------------
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return routing_client.haversine_km((lat1, lon1), (lat2, lon2))


def _routing_backends() -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, value in (("uz", OSRM_URL_UZ), ("tj", OSRM_URL_TJ), ("default", OSRM_URL)):
        url = (value or "").strip().rstrip("/")
        if url and url not in seen:
            result.append((name, url))
            seen.add(url)
    return result


def _osrm_route(lat1: float, lon1: float, lat2: float, lon2: float, base: str):
    """Совместимая обёртка старого одиночного OSRM-вызова для внешних тестов."""
    result = routing_client.best_route(
        (lat1, lon1), (lat2, lon2), backends=[("default", base)])
    if not result:
        return None
    return result["line"], result["km"], result["minutes"]


# --- Хендлеры ---------------------------------------------------------------
async def _index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(MINIAPP_DIR / "index.html")


async def _sw(request: web.Request) -> web.StreamResponse:
    """Service worker миниаппа. Отдаётся с корня (/sw.js), чтобы его скоуп охватывал весь
    origin. Service-Worker-Allowed:/ и no-cache — чтобы обновление SW подхватывалось."""
    return web.FileResponse(MINIAPP_DIR / "sw.js", headers={
        "Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


async def _manifest(request: web.Request) -> web.StreamResponse:
    """Манифест PWA. С ?t=<живой девайс-токен> отдаём ПЕРСОНАЛЬНЫЙ манифест:
    start_url со вшитым токеном (во фрагменте — на сервер не уходит). Это
    преемственность входа для iOS-ярлыка: у «На экран Домой» отдельное хранилище,
    localStorage Safari туда не переезжает — а start_url переезжает всегда. Страница
    после входа сама переписывает <link rel=manifest> на /manifest.webmanifest?t=…,
    и ярлык рождается уже авторизованным. Чужой/протухший t молча игнорируем.
    Компромисс: t оседает в access-логах (логи — root-only на своём VPS; сами токены
    и так лежат рядом в БД), no-store — чтобы прокси/SW не закэшировали чужой манифест."""
    raw = json.loads((MINIAPP_DIR / "manifest.webmanifest").read_text("utf-8"))
    tok = (request.query.get("t") or "").strip()
    if tok and db.uid_for_app_token(tok):
        raw["start_url"] = f"/#t={tok}"
    return web.Response(text=json.dumps(raw, ensure_ascii=False),
                        content_type="application/manifest+json",
                        headers={"Cache-Control": "no-store"})


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
    # Роль руководителя: у него нет привязки, он работает от имени сотрудника и
    # может переключаться. Фронт по этому блоку рисует переключатель портфелей.
    scope = _manager_scope(uid)
    roles = {"manager": bool(scope) and not db.get_binding(uid),
             "employees": scope, "acting": emp} if scope else None
    return web.json_response({"ok": True, "uid": uid, "employee": emp,
                              "kind": db.get_kind(uid), "lang": lang,
                              "persons": persons, "meta": _meta(lang),
                              "roles": roles})


async def _act_as(request: web.Request) -> web.Response:
    """Руководитель переключается на портфель другого своего сотрудника.

    Проверка списка — на сервере: клиент присылает имя, но право открыть этот
    портфель определяется ролью в БД, а не тем, что пришло в запросе."""
    uid = _uid(request)
    if not uid:
        raise web.HTTPUnauthorized(text="auth")
    try:
        body = await request.json()
    except Exception:                                  # noqa: BLE001
        raise web.HTTPBadRequest(text="json")
    employee = str(body.get("employee") or "").strip()
    scope = _manager_scope(uid)
    if not scope:
        raise web.HTTPForbidden(text="not a manager")
    if not db.set_acting_employee(uid, employee, scope):
        raise web.HTTPForbidden(text="employee not in scope")
    db.audit_access(uid, "act_as", employee)           # ИБ: смена контекста — в журнал
    return web.json_response({"ok": True, "acting": employee})


async def _route_day(request: web.Request) -> web.Response:
    """Маршрут дня: top-12 по скорингу, дорожная OSRM-матрица и VROOM.

    Неверный соседний граф отбрасывается по snap-distance. При любой недоступности
    остаётся локальный 2-opt по той же матрице либо честный haversine-фолбэк.
    """
    uid = _uid(request)
    if not uid:
        raise web.HTTPUnauthorized(text="auth")
    emp = _employee(uid)
    if not emp:
        raise web.HTTPNotFound(text="bind")
    try:
        lat, lon = float(request.query.get("lat", "")), float(request.query.get("lon", ""))
        start = (lat, lon)
    except (TypeError, ValueError):
        start = None
    grouped = db.persons_for_employee(emp)
    persons = [_person_brief(p) for p in grouped]
    try:
        import scoring
        idx = scoring.priority_index()
        for pb, p in zip(persons, grouped):
            s = idx.get(f"{p['client'] or ''}|{p['birth_date'] or ''}")
            if s:
                pb["score"] = s["score"]
    except Exception as e:  # noqa: BLE001
        logging.warning("route_day scoring: %s", e)
    cand = [p for p in persons if not p["visited"] and p.get("lat") is not None
            and (p.get("score") is None or p["score"] > 0)]
    cand.sort(key=lambda x: x.get("score") or 0, reverse=True)
    cand = cand[:12]   # больше 12 адресов в день не объехать
    plan = await asyncio.get_event_loop().run_in_executor(
        None, routing_client.plan_day, cand, start, _routing_backends(), VROOM_URL)
    order = []
    for index, leg in zip(plan["order"], plan["legs_km"]):
        nxt = cand[index]
        order.append({"rep": nxt["rep"], "client": nxt["client"], "totals": nxt["totals"],
                      "score": nxt.get("score"), "km": leg,
                      "lat": nxt["lat"], "lon": nxt["lon"]})
    db.audit_access(uid, "route_day",
                    detail=f"{emp} {len(order)} {plan['matrix_src']} {plan['solver']}")
    return web.json_response({"ok": True, "employee": emp,
                              "order": order, "km": plan["km"],
                              "matrix_src": plan["matrix_src"], "solver": plan["solver"],
                              "max_snap_m": plan.get("max_snap_m")})


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
    # Самовыбор активируется СРАЗУ: выездник в поле не должен ждать, пока владелец
    # увидит заявку. Аппрув остаётся ровно для одного случая — на это ФИО уже сел
    # другой телефон. Один сотрудник = одно устройство, иначе активность разъезжается
    # по двум привязкам (карточка дайджеста показывала нули отработавшему), а на
    # чужое ФИО может сесть посторонний. Разрешить второе устройство может только
    # владелец, кнопкой под заявкой.
    taken_by = db.employee_taken_by(employee, exclude_telegram_id=uid)
    if taken_by:
        created = db.bind_agent(uid, employee, kind, approved=False)
        db.audit_access(uid, "bind_request_taken", employee)
        if created:                                # заявка уходит владельцу один раз
            bot = request.app.get("bot")
            if bot and ADMIN_ID:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        t("bind_request_admin", lang_of(ADMIN_ID), id=uid, emp=employee)
                        + f"\n\n⚠️ За «{employee}» уже закреплено другое устройство"
                          f" (id {taken_by}). Подтвердите, только если это тот же человек.",
                        reply_markup=approve_bind_kb(uid, lang_of(ADMIN_ID)))
                except Exception:  # noqa: BLE001
                    pass
        return web.json_response({"ok": True, "pending": True, "taken": True,
                                  "employee": None, "kind": db.get_kind(uid)})
    ok = db.bind_agent(uid, employee, kind)   # свободное ФИО — работаем сразу
    db.audit_access(uid, "bind_self", employee)
    return web.json_response({"ok": ok, "employee": db.get_binding(uid), "kind": db.get_kind(uid)})


async def _note_add(request: web.Request) -> web.Response:
    """Заметка выездника «для себя». Привязка к кейсу необязательна.

    Чужой кейс привязать нельзя — иначе заметка стала бы способом узнать, что
    договор с таким номером вообще существует.
    """
    uid, emp = _auth(request)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="text")
    rep = (body.get("rep") or "").strip() or None
    if rep:
        row = db.get_client(rep)
        if not row or (row["responsible"] != emp and uid != ADMIN_ID):
            raise web.HTTPNotFound(text="person")
    op_id = (str(body.get("op_id") or "")).strip() or None
    nid = db.add_note(uid, text, employee=emp, client_rep=rep, op_id=op_id)
    return web.json_response({"ok": True, "id": nid})


async def _notes(request: web.Request) -> web.Response:
    uid, _ = _auth(request)
    rows = db.notes_for(uid)
    return web.json_response({"ok": True, "notes": [
        {"id": r["id"], "rep": r["client_rep"], "text": r["text"], "at": r["created_at"]}
        for r in rows]})


async def _note_delete(request: web.Request) -> web.Response:
    uid, _ = _auth(request)
    body = await request.json()
    try:
        nid = int(body.get("id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="id")
    return web.json_response({"ok": db.delete_note(uid, nid)})


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
    op_id = (str(body.get("op_id") or "")).strip() or None
    mid = db.add_marker(uid, db.get_kind(uid), lat, lon, client_rep=rep, label=label, op_id=op_id)
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
    навигации в миниаппе. Выбираем UZ/TJ OSRM по реальной близости к графу; иначе прямая —
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
    return await _route_payload(lat, lon, float(glat), float(glon))


async def _route_payload(lat: float, lon: float,
                         glat: float, glon: float) -> web.Response:
    """Общий ответ навигации: линия по дорогам (OSRM) либо честная прямая.
    Один и тот же расчёт для маршрута к должнику и к произвольной точке."""
    result = await asyncio.get_event_loop().run_in_executor(
        None, routing_client.best_route, (lat, lon), (glat, glon), _routing_backends())
    if result:
        return web.json_response({"ok": True, "line": result["line"],
                                  "km": round(result["km"], 1),
                                  "minutes": round(result["minutes"]), "src": "osrm",
                                  "router": result["name"],
                                  "max_snap_m": round(result["max_snap_m"])})
    km = _haversine_km(lat, lon, glat, glon)
    minutes = km / 30.0 * 60.0   # тот же запас скорости 30 км/ч, что и в движке без OSRM
    return web.json_response({"ok": True, "line": [[lon, lat], [glon, glat]],
                              "km": round(km, 1), "minutes": round(minutes), "src": "straight"})


# Координаты, введённые руками: «41.31, 69.24». Десятичная точка обязательна —
# с запятой «41,31 69,24» неотличимо от пары целых, и разбор становится гаданием.
_LATLON_RX = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


_SUGGEST_LIMIT = 8


def _suggest_label(idx, rec) -> str:
    """«Конибодом · Согдийская область» — чтобы тёзки различались на глаз."""
    parts = [rec.ru or rec.name]
    try:
        adm = idx.adm1_by_code.get((rec.cc, rec.adm1)) if rec.adm1 else None
        if adm is not None and (adm.ru or adm.name) != (rec.ru or rec.name):
            parts.append(adm.ru or adm.name)
    except Exception:                                  # noqa: BLE001
        pass
    return " · ".join(p for p in parts if p)


async def _geo_suggest(request: web.Request) -> web.Response:
    """Подсказки при наборе адреса в навигаторе.

    Тот же офлайн-справочник, что и у геокодинга: наружу ничего не уходит.
    Ранжируем по населению — при одинаковом имени человек почти всегда имеет в
    виду тот населённый пункт, который крупнее, а мелкие тёзки различаются
    подписью области.
    """
    _auth(request)
    q = (request.query.get("q") or "").strip()
    if len(q) < 2:
        return web.json_response({"items": []})
    try:
        import addr_cis
        import geoindex
        idx = geoindex.load()
    except Exception:                                  # noqa: BLE001
        return web.json_response({"items": []})

    needle = addr_cis.fuzzy(q)
    if not needle:
        return web.json_response({"items": []})

    seen: set[int] = set()
    hits: list[tuple[int, int, int, object]] = []
    for key, recs in idx.by_key.items():
        if not key.startswith(needle):
            continue
        exact = 0 if key == needle else 1
        for rec in recs:
            if id(rec) in seen:
                continue
            seen.add(id(rec))
            hits.append((-int(rec.pop or 0), exact, len(rec.name or ""), rec))
        if len(hits) > 400:                            # хватит для ранжирования
            break

    # Запасной проход по обычному написанию. Нечёткий ключ схлопывает диграфы
    # («kh» → «h»), и НЕДОпечатанное слово перестаёт быть префиксом полного:
    # «Shahrik» даёт «sahrik», а «Shahrikhon» — «sahrihon». Человек набирает
    # адрес по частям, поэтому такой промах здесь — правило, а не исключение.
    if len(hits) < _SUGGEST_LIMIT:
        plain = q.lower().replace("‘", "").replace("'", "").replace("ʻ", "")
        for recs in idx.by_key.values():
            for rec in recs:
                if id(rec) in seen:
                    continue
                for nm in (rec.name, rec.ru):
                    if nm and nm.lower().replace("‘", "").replace("'", "").startswith(plain):
                        seen.add(id(rec))
                        hits.append((-int(rec.pop or 0), 1, len(rec.name or ""), rec))
                        break
            if len(hits) > 400:
                break
    # Население — ГЛАВНЫЙ критерий, а не точность совпадения. Обратный порядок
    # выносил наверх мусор: на «душан» первым шёл безымянный Dusham (0 жителей),
    # потому что его ключ совпал буква в букву, а Душанбе с полутора миллионами
    # оказывался ниже. Человек, набравший пять букв, имеет в виду крупное.
    hits.sort(key=lambda t: (t[0], t[1], t[2]))

    # Один и тот же населённый пункт лежит в справочнике по нескольку раз: запись
    # города, запись района, копия из OSM, разные написания. На экране это давало
    # «Душанбе, Душанбе, Душанбе». Схлопываем по (имя, область) — этого достаточно,
    # чтобы тёзки из РАЗНЫХ областей остались видны и различимы по подписи.
    import addr_cis as _ac
    items, place_seen, names_seen = [], set(), set()
    for _p, _e, _l, r in hits:
        nm = _ac.fuzzy(r.ru or r.name)
        # У копий из OSM области нет вовсе, поэтому по паре (имя, область) они не
        # схлопывались и «Конибодом» появлялся дважды. Безобластную запись
        # показываем, только если такого имени ещё не было.
        if not r.adm1 and nm in names_seen:
            continue
        mark = (nm, r.cc, r.adm1 or "")
        if mark in place_seen:
            continue
        place_seen.add(mark)
        names_seen.add(nm)
        items.append({"label": _suggest_label(idx, r), "lat": r.lat, "lon": r.lon,
                      "kind": r.fcode or ""})
        if len(items) >= _SUGGEST_LIMIT:
            break
    return web.json_response({"items": items})


async def _geocode_free(request: web.Request) -> web.Response:
    """Произвольный адрес (или пара координат) → точка. Для навигации по адресу,
    которого нет в реестре: руководителю — проверить адрес должника, владельцу —
    съездить куда угодно на тесте.

    ТОЛЬКО офлайн-справочник. Сервер-витрина принципиально не ходит в публичные
    геокодеры: туда уехали бы адреса должников (см. модуль geocode — точный
    геокодинг живёт в пульте на машине владельца, а не здесь)."""
    _auth(request)
    q = (request.query.get("q") or "").strip()
    if not q:
        raise web.HTTPBadRequest(text="q")

    m = _LATLON_RX.match(q)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return web.json_response({"ok": True, "lat": lat, "lon": lon,
                                      "pretty": f"{lat:.5f}, {lon:.5f}",
                                      "precision": "point", "name": ""})

    parsed = addr_cis.parse(q)
    try:
        idx = geoindex.load()
    except Exception:                                  # noqa: BLE001
        logging.exception("gazetteer load failed")
        raise web.HTTPServiceUnavailable(text="gazetteer")
    hit = idx.resolve(parsed)
    if not hit:
        raise web.HTTPNotFound(text="geo")
    return web.json_response({"ok": True, "lat": hit["lat"], "lon": hit["lon"],
                              "pretty": addr_cis.pretty(parsed, idx.display_names(parsed)) or q,
                              "precision": hit["precision"], "name": hit.get("name") or ""})


# Пачка за раз. Навигатор шлёт ~10 точек, но оффлайн-очередь после часа без сети
# может накопить сотни — режем, чтобы одна отправка не встала колом.
TRACK_MAX_POINTS = 500


async def _track(request: web.Request) -> web.Response:
    """Точки трека из навигатора — пока визит ещё НЕ закрыт.

    Раньше от всей поездки в БД оставались две точки: координата фотографии и
    место закрытия визита. Трек даёт фактическое время в пути (сейчас движок
    берёт оценку OSRM и множит на взятый с потолка коэффициент), проезжаемость
    дорог и реальную длительность визита.

    Идемпотентна по (session_id, ts): в поле сеть рвётся, и клиент переотправляет
    одну и ту же пачку — дублей это не создаёт."""
    uid, emp = _auth(request)
    try:
        body = await request.json()
    except Exception:                                  # noqa: BLE001
        raise web.HTTPBadRequest(text="json")
    session_id = str(body.get("session_id") or "").strip()[:64]
    if not session_id:
        raise web.HTTPBadRequest(text="session_id")
    points = body.get("points")
    if not isinstance(points, list):
        raise web.HTTPBadRequest(text="points")

    rep = str(body.get("rep") or "").strip() or None
    if rep:
        # rep приходит от клиента: подпись подтверждает личность, но не право на
        # ЭТОТ договор. Чужой не отклоняем — просто пишем трек без привязки,
        # иначе потеря сети превратится в потерю данных поездки.
        row = db.get_client(rep)
        if not row or (row["responsible"] != emp and uid != ADMIN_ID):
            rep = None
    added = db.add_track_points(session_id, emp, uid, rep, points[:TRACK_MAX_POINTS])
    return web.json_response({"ok": True, "added": added})


async def _route_to(request: web.Request) -> web.Response:
    """Маршрут до произвольной точки (без привязки к договору).
    Проверка «чей должник» здесь не нужна: цель задал сам пользователь,
    из БД ничего не читается."""
    _auth(request)
    try:
        lat = float(request.query.get("lat", ""))
        lon = float(request.query.get("lon", ""))
        dlat = float(request.query.get("dlat", ""))
        dlon = float(request.query.get("dlon", ""))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="lat/lon")
    return await _route_payload(lat, lon, dlat, dlon)


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


async def _cards(request: web.Request) -> web.Response:
    """Все полные карточки портфеля сотрудника разом — прогрев оффлайн-кэша миниаппа.
    Клиент зовёт это ОНЛАЙН сразу после /api/bootstrap и складывает карточки в IndexedDB,
    чтобы в поле без сети открывать любого своего клиента и заполнять визит. Один запрос
    вместо N (по карточке на человека). ПД в журнал доступа тут не пишем — это оптовый
    предзагруз своего же портфеля, а не точечный просмотр (view_card логируется в _person)."""
    uid, emp = _auth(request)
    lang = lang_of(uid)
    cards = {}
    for p in db.persons_for_employee(emp):
        full = _person_full(p["rep"], lang)
        if full:
            cards[p["rep"]] = full
    # ИБ: точечный просмотр карточки логируется в _person (view_card). Здесь — оптовый
    # предзагруз своего портфеля в оффлайн-кэш: пишем один факт (кто/когда/сколько), без
    # строки на каждого. NB: оффлайн-открытия карточек в поле сервер уже не аудирует.
    db.audit_access(uid, "cards_prefetch", detail=str(len(cards)))
    return web.json_response({"ok": True, "cards": cards,
                              "at": datetime.now().isoformat(timespec="seconds")})


async def _save(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    body = await request.json()
    rep = body.get("rep", "")
    row = db.get_client(rep)
    if not row or (row["responsible"] != emp and uid != ADMIN_ID):
        raise web.HTTPNotFound(text="person")
    raw_values = dict(body.get("values") or {})
    finish = bool(body.get("finish"))
    if finish:
        if body.get("form_version") != FORM_PROTOCOL_VERSION:
            raise web.HTTPConflict(
                text="refresh form: explicit field provenance required"
            )
        input_keys = body.get("input_keys")
        if not isinstance(input_keys, list) or not all(
            isinstance(key, str) for key in input_keys
        ):
            raise web.HTTPBadRequest(text="input_keys required")
        # Даже если старая оффлайн-карточка сохранила префилл master, новая
        # оболочка передаёт только поля, которых пользователь действительно
        # коснулся. Остальные значения тела не участвуют ни в raw update, ни в
        # журнале, ни в доказанном snapshot.
        explicit_keys = set(input_keys)
        raw_values = {
            key: value
            for key, value in raw_values.items()
            if key in explicit_keys
        }
    # Легаси-клиенты (закэшированная оболочка, оффлайн-очереди на телефонах) шлют
    # комментарий выездника старым ключом call_comment — переадресуем в visit_comment.
    if "call_comment" in raw_values and "visit_comment" not in raw_values:
        raw_values["visit_comment"] = raw_values.pop("call_comment")
    values = {}
    for key, raw in raw_values.items():
        if key not in ALLOWED_SAVE:
            continue
        value = str(raw or "").strip()
        if not value:
            continue
        if key in FORM_DROPDOWNS and value not in DROPDOWNS[key]:
            raise web.HTTPBadRequest(text=f"invalid {key}")
        values[key] = value
    if finish and OFFICE_RESULT_SNAPSHOT_KEY not in values:
        raise web.HTTPBadRequest(text="visit result required")
    db.audit_access(uid, "save_visit" if finish else "save_card", rep)
    if values:
        db.update_person(rep, values)  # пишется во ВСЕ договоры человека, сразу в БД
    if finish:
        completed_at = date.today().isoformat()
        db.update_person(rep, {"visit_date": completed_at})
        cur = db.get_client(rep)
        op_id = (str(body.get("op_id") or "")).strip() or None
        # Гео-контроль: точка телефона в момент закрытия vs известная точка должника.
        # 'city' не считаем — центроид города, честная дистанция там бессмысленна.
        vlat = vlon = dist_m = None
        g = body.get("geo") or {}
        try:
            vlat, vlon = float(g["lat"]), float(g["lon"])
        except (KeyError, TypeError, ValueError):
            vlat = vlon = None
        # Грубые уровни отсекаем ЯВНО: центр района или области — это не адрес,
        # и расстояние до него ничего не проверяет, зато выглядит как нарушение.
        # Раньше судили только по geo_src, а он у всей иерархии один — «geonames».
        # Пустая точность — это «неизвестно» (строка геокодирована старым кодом),
        # и тогда судим по-прежнему, только по geo_src. Отключать гео-контроль
        # для всей накопленной базы было бы хуже, чем изредка спросить лишний раз.
        rough = {"city", "district", "region"}
        precision = (cur["geo_precision"] if "geo_precision" in cur.keys() else "") or ""
        # Погрешность САМОЙ точки телефона. В частном секторе, в подвале и просто
        # в помещении GPS не ловится, и браузер отдаёт сетевой фикс с точностью
        # 800–1500 м — то есть больше порога тревоги. Считать по такой точке
        # расстояние значит обвинить человека, который стоял ровно там, где надо:
        # «⚠️ закрыт в 3.9 км от адреса» уходит руководителю в личку, в беседу
        # отчётов и красной точкой на карту дня. Меряем, только если точка
        # телефона точнее порога.
        try:
            acc = float(g.get("acc") or 0)
        except (TypeError, ValueError):
            acc = 0.0
        trusted_fix = acc <= 0 or acc < db.geo_alert_m()
        if vlat is not None and cur["geo_lat"] is not None and cur["geo_lon"] is not None \
                and (cur["geo_src"] or "") not in ("", "city") \
                and precision not in rough and trusted_fix:
            dist_m = round(_haversine_km(vlat, vlon, cur["geo_lat"], cur["geo_lon"]) * 1000)
        # История визита строится из явного тела этой формы. Сырые HARD-поля
        # clients/master запрещено дочитывать даже для текста журнала.
        payload = (
            f"Выезд (миниапп). Статус: "
            f"{values.get(OFFICE_RESULT_SNAPSHOT_KEY, '')}; "
            f"Договорённость: {values.get('agreed_payment', '')}; "
            f"{values.get('visit_comment', '')}"
        )
        # Считаем ДО log_visit, иначе своя же свежая строка себя и заглушит.
        same_today = db.same_visit_logged_today(rep, uid, payload)
        is_new = db.log_visit(rep, uid, payload,
                              op_id=op_id, lat=vlat, lon=vlon, dist_m=dist_m)
        # Снимок строится только из values этого POST, а не из сырых HARD-полей
        # clients. При ретрае после редкого падения между log_visit и этой записью
        # восстанавливаем отсутствующий снимок; старый дубль новый снимок не откатывает.
        if is_new or not db.has_bot_visit(rep):
            db.record_bot_visit(rep, values, completed_at)
        proven_row = db.get_client(rep)
        proven_snapshot = db.bot_visit_snapshot(proven_row) or {}
        notify_row = dict(proven_row)
        for key in db.BOT_SNAPSHOT_KEYS:
            notify_row[key] = proven_snapshot.get(key)
        # is_new гасит повтор из оффлайн-очереди (тот же op_id), same_today — повторное
        # нажатие «Сохранить выезд» (новый op_id, но выезд тот же: тот же выездник и тот
        # же результат). Без второго условия руководителю прилетало 2-3 одинаковых
        # отбивки по одному должнику. Сравниваем содержимое, а не сам факт строки:
        # второй настоящий выезд в тот же день с другим исходом отбивку получить обязан.
        # Данные при этом пишутся как раньше — глушим только рассылку.
        if is_new and not same_today:
            await _notify_group(request, notify_row, uid)
            try:
                await _notify_managers(request, notify_row, dist_m)
            except Exception as e:  # noqa: BLE001 — пуш не должен ломать сохранение:
                # иначе 500 после log_visit, а повтор из очереди уже is_new=False —
                # и пуш руководителю, и отчёт в беседу пропадут навсегда
                logging.warning("manager notify failed: %s", e)
            await _notify_report_chats(
                request,
                notify_row,
                vlat,
                vlon,
                dist_m,
                uid,
            )
    data = _person_full(rep, lang_of(uid))
    return web.json_response({"ok": True, "person": data})


def _photo_geo_from(request: web.Request) -> tuple | None:
    """Гео фото из запроса: заголовок X-Photo-Geo «lat,lon,acc,gat» (основной
    путь — заголовки не попадают в access-логи, в отличие от query на общем
    VPS) или query-параметры (запасной)."""
    h = request.headers.get("X-Photo-Geo", "")
    if h:
        parts = [p.strip() for p in h.split(",")]
        q = dict(zip(("lat", "lon", "acc", "gat"), parts))
        return _photo_geo_query(q)
    return _photo_geo_query(request.query)


def _photo_geo_query(q) -> tuple | None:
    """(lat, lon, acc_m, captured_at) — фактическая точка съёмки (миниапп
    снимает её в момент съёмки, оффлайн-очередь доносит её же). Мусор молча
    игнорируем: фото важнее геометки."""
    try:
        lat, lon = float(q["lat"]), float(q["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    try:
        acc = max(0, min(int(float(q.get("acc") or 0)), 100_000))
    except (TypeError, ValueError):
        acc = 0
    captured = None
    try:
        ms = int(q.get("gat") or 0)
        if ms > 0:
            captured = datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    return lat, lon, acc, captured


class _PhotoBodyError(ValueError):
    """Тело не является допустимым фото; до финального пути ничего не опубликовано."""

    def __init__(self, reason: str, *, actual_size: int = 0,
                 too_large: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.actual_size = actual_size
        self.too_large = too_large


def _photo_kind(head: bytes) -> str | None:
    """Определить поддержанный формат по сигнатуре, не доверяя имени/MIME клиента."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        box_size = int.from_bytes(head[:4], "big")
        end = min(len(head), box_size if box_size >= 16 else len(head))
        brands = {head[8:12].lower()}
        # ISO-BMFF: major_brand, minor_version, затем compatible_brands.
        brands.update(head[pos:pos + 4].lower()
                      for pos in range(16, end - 3, 4))
        if brands & {b"avif", b"avis"}:
            return "avif"
        if brands & {
            b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis",
            b"hevm", b"hevs", b"mif1", b"msf1",
        }:
            return "heif"
    return None


def _photo_ext(kind: str, hint: str | None) -> str:
    """Сохранить расширение старого op для идемпотентного retry, но сверить с magic."""
    hint = (hint or "").strip()
    if not hint:
        return _PHOTO_CANONICAL_EXT[kind]
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,7}", hint):
        raise _PhotoBodyError("bad extension")
    if hint.lower() not in _PHOTO_EXTENSIONS[kind]:
        raise _PhotoBodyError("extension does not match image")
    return hint


async def _part_chunks(field):
    while chunk := await field.read_chunk(size=_PHOTO_CHUNK):
        yield chunk


async def _legacy_photo_field(request: web.Request):
    """Найти photo в multipart независимо от порядка полей.

    Любая ошибка framing превращается вызывающим в 503: оболочка v12 удаляет
    очередь на 4xx, а 5xx оставляет единственную локальную копию до обновления.
    """
    reader = await request.multipart()
    while field := await reader.next():
        if field.name == "photo":
            return field
        await field.release()
    return None


async def _store_photo(chunks, dest: Path, op: str, ext_hint: str | None):
    """Полностью принять и проверить фото, затем атомарно опубликовать hard-link.

    Возвращает (финальный путь, опубликован_впервые). Уникальная времянка нужна
    в том числе двум одновременным retry одного op; hard-link с O_EXCL-семантикой
    не перезаписывает уже опубликованный файл и не даёт дважды поднять счётчик.
    """
    dest.mkdir(parents=True, exist_ok=True)
    prefix = re.sub(r"[^A-Za-z0-9_-]", "", op)[:48] or "photo"
    fd, tmp_name = tempfile.mkstemp(prefix=f"~{prefix}.", suffix=".part", dir=dest)
    tmp = Path(tmp_name)
    size = 0
    head = bytearray()
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > PHOTO_MAX_BYTES:
                    raise _PhotoBodyError(
                        "photo too large", actual_size=size, too_large=True)
                if len(head) < _PHOTO_MAGIC_BYTES:
                    head.extend(chunk[:_PHOTO_MAGIC_BYTES - len(head)])
                out.write(chunk)
        if size == 0:
            raise _PhotoBodyError("empty photo")
        kind = _photo_kind(bytes(head))
        if not kind:
            raise _PhotoBodyError("unsupported image", actual_size=size)
        ext = _photo_ext(kind, ext_hint)
        stem = op or str(time.time_ns())
        # Retry оболочки v12/v13 должен узнать уже опубликованный op даже если
        # регистр/синоним расширения отличался (.JPG/.jpeg). Тело всё равно
        # полностью прочитано и проверено выше.
        if op:
            old = next(
                (candidate for candidate in dest.iterdir()
                 if candidate.is_file() and candidate.stem == stem
                 and candidate.suffix.lower() in _PHOTO_SUFFIXES),
                None,
            )
            if old is not None:
                return old, False
        path = dest / f"{stem}{ext}"
        try:
            os.link(tmp, path)
            published = True
        except FileExistsError:
            published = False
        return path, published
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _permanent_photo_count(dest: Path) -> int:
    return sum(
        1 for path in dest.iterdir()
        if path.is_file() and path.suffix.lower() in _PHOTO_SUFFIXES
    )


async def _photo(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    rep = request.query.get("rep", "")
    row = db.get_client(rep)
    if not row or (row["responsible"] != emp and uid != ADMIN_ID):
        raise web.HTTPNotFound(text="person")
    db.audit_access(uid, "photo", rep)
    dest = PHOTO_DIR / rep
    # op — идемпотентный ключ загрузки из оффлайн-очереди: имя файла = op, повторная
    # доставка того же фото не создаёт второй файл и не накручивает счётчик photos_count.
    op = re.sub(r"[^A-Za-z0-9_-]", "", request.query.get("op", ""))[:64]
    geo = _photo_geo_from(request)
    raw = request.headers.get("X-Photo-Raw", "") == "1"
    if raw:
        if request.content_type.lower() != "application/octet-stream":
            raise web.HTTPUnprocessableEntity(text="raw photo content type")
        chunks = request.content.iter_chunked(_PHOTO_CHUNK)
        ext_hint = request.headers.get("X-Photo-Ext")
    else:
        # Совместимость с установленной v12. Malformed/empty/invalid multipart
        # намеренно получает 503, а не 4xx: старая очередь удаляет элемент на 4xx.
        if request.content_type.lower() != "multipart/form-data":
            raise web.HTTPServiceUnavailable(text="legacy photo retry")
        try:
            field = await _legacy_photo_field(request)
        except Exception:  # noqa: BLE001 - любой framing/parser сбой должен сохранить v12 queue
            raise web.HTTPServiceUnavailable(text="legacy photo retry")
        if field is None:
            raise web.HTTPServiceUnavailable(text="legacy photo retry")
        chunks = _part_chunks(field)
        ext_hint = os.path.splitext(field.filename or "")[1][:8] or None

    try:
        path, published = await _store_photo(chunks, dest, op, ext_hint)
    except _PhotoBodyError as e:
        if not raw:
            raise web.HTTPServiceUnavailable(text="legacy photo retry")
        if e.too_large:
            raise web.HTTPRequestEntityTooLarge(
                max_size=PHOTO_MAX_BYTES, actual_size=e.actual_size)
        raise web.HTTPUnprocessableEntity(text=e.reason)
    except Exception:  # noqa: BLE001 - legacy v12 удаляет фото на любом 4xx
        if not raw:
            raise web.HTTPServiceUnavailable(text="legacy photo retry")
        raise

    # Сверяем и на первой публикации, и на duplicate. Если процесс упал после
    # hard-link, но до SQLite, committed IDB-очередь повторит op и починит счётчик.
    n = db.ensure_photos_count(rep, _permanent_photo_count(dest))
    if geo:
        db.add_photo_geo(rep, f"{rep}/{path.name}", uid, *geo)
    return web.json_response(
        {"ok": True, "photos": n, **({"dup": True} if not published else {})})


# --- Голосовой комментарий: общий прогретый Whisper Praxis по UDS ------------
_STT = None
_VOICE_LOCK = asyncio.Lock()
# Для таджикского профиль Praxis пока использует auto.
_VOICE_LANG = {"ru": "ru", "uz": "uz"}


def _voice_configured() -> bool:
    return (
        VOICE_BACKEND == "praxis"
        and bool(PRAXIS_STT_SOCKET)
        and bool(PRAXIS_STT_TOKEN_FILE)
    )


def _stt() -> stt_client.STTClient:
    global _STT
    if _STT is None:
        _STT = stt_client.STTClient(
            unix_socket=PRAXIS_STT_SOCKET,
            token_file=PRAXIS_STT_TOKEN_FILE,
            timeout_seconds=VOICE_TIMEOUT_SECONDS,
        )
    return _STT


async def _voice(request: web.Request) -> web.Response:
    """Голосовое из миниаппа → общий Whisper Praxis → текст.

    На границу Praxis уходят только байты и ``ru|uz|auto``: uid/ФИО/КД,
    исходное имя файла и текст формы не передаются.
    """
    uid, _emp = _auth(request)
    if not _voice_configured():
        raise web.HTTPServiceUnavailable(text="voice off")
    try:
        reader = await request.multipart()
        field = None
        while candidate := await reader.next():
            if candidate.name == "voice":
                field = candidate
                break
    except (AssertionError, OSError, ValueError):
        raise web.HTTPBadRequest(text="bad voice multipart")
    if field is None:
        raise web.HTTPBadRequest(text="no voice")
    size = 0
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
            tmp = f.name
            while chunk := await field.read_chunk():
                size += len(chunk)
                if size > stt_client.MAX_AUDIO_BYTES:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=stt_client.MAX_AUDIO_BYTES,
                        actual_size=size,
                    )
                f.write(chunk)
        if size <= 0:
            raise web.HTTPUnprocessableEntity(text="empty voice")
        db.audit_access(uid, "voice", request.query.get("rep") or None)
        lang = _VOICE_LANG.get(lang_of(uid), "auto")
        async with _VOICE_LOCK:
            text = str(await _stt().transcribe(tmp, lang) or "").strip()
        if not text:
            raise web.HTTPUnprocessableEntity(text="empty transcript")
    except stt_client.STTError as exc:
        logging.warning(
            "praxis stt failed: status=%s retryable=%s",
            exc.status,
            exc.retryable,
        )
        headers = (
            {"Retry-After": str(exc.retry_after)}
            if exc.retry_after is not None
            else None
        )
        if exc.status == 413:
            raise web.HTTPRequestEntityTooLarge(
                max_size=stt_client.MAX_AUDIO_BYTES,
                actual_size=size,
                headers=headers,
            )
        if exc.retryable or exc.status in {401, 403, 408, 429, 503}:
            raise web.HTTPServiceUnavailable(
                text="voice temporarily unavailable",
                headers=headers,
            )
        raise web.HTTPUnprocessableEntity(
            text="transcribe failed",
            headers=headers,
        )
    except web.HTTPException:
        raise
    except (AssertionError, OSError, ValueError):
        raise web.HTTPBadRequest(text="bad voice body")
    except Exception:  # noqa: BLE001 — отсутствие UDS/неожиданный client failure
        logging.exception("praxis stt unexpected client failure")
        raise web.HTTPServiceUnavailable(text="voice temporarily unavailable")
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return web.json_response({"ok": True, "text": text})


async def _redeem(request: web.Request) -> web.Response:
    """Обмен одноразового кода из ссылки бота на девайс-токен автономного PWA.
    Код невалиден (использован/просрочен/чужой) → 410: клиент решает сам — если на
    устройстве уже есть живой токен, продолжает тихо (повторное открытие той же
    ссылки), иначе просит взять новую ссылку у бота."""
    try:
        code = str((await request.json()).get("code") or "").strip()
    except Exception:  # noqa: BLE001
        code = ""
    token = db.redeem_app_link(code)
    if not token:
        raise web.HTTPGone(text="link")
    uid = db.uid_for_app_token(token)
    db.audit_access(uid or 0, "app_login")
    return web.json_response({"ok": True, "token": token, "days": db.APP_TOKEN_DAYS})


async def _admin_app_revoke(request: web.Request) -> web.Response:
    """Отзыв доступа автономного приложения из пульта: {telegram_id}."""
    _admin(request)
    body = await request.json()
    try:
        tid = int(body["telegram_id"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="telegram_id")
    n = db.revoke_app_access(tid)
    db.audit_access(tid, "app_revoke", detail=str(n))
    return web.json_response({"ok": True, "revoked": n})


async def _lang(request: web.Request) -> web.Response:
    uid, _ = _auth(request)
    lang = (await request.json()).get("lang", "")
    if lang in ("ru", "uz", "tg"):
        db.set_lang(uid, lang)
    return web.json_response({"ok": True})



def _acting_actor_name(uid: int) -> str:
    """Человекочитаемое имя того, кто действует от имени сотрудника.
    Пусто — если это обычный выездник со своей привязкой."""
    mgr = db.get_manager(uid)
    if mgr:
        return f"руководитель {mgr.get('name') or uid}"
    if uid == ADMIN_ID:
        return "владелец"
    return ""

async def _notify_group(request: web.Request, row, uid: int) -> None:
    bot = request.app.get("bot")
    if not bot:
        return
    try:
        import html as _html
        from notify import post_to_group
        e = lambda v: _html.escape(str(v or ""))   # бот шлёт с parse_mode=HTML
        await post_to_group(bot, text=(
            f"✅ Выезд отработан (миниапп)\n👤 {e(row['client'])} ({e(row['contract_id'])})\n"
            f"🧑‍💼 id {uid}\n"
            f"📝 {e(row['call_status']) or '—'} · {e(row['agreed_payment']) or '—'}\n"
            f"💬 {e(row['visit_comment'] or row['call_comment']) or '—'}"))
    except Exception:  # noqa: BLE001
        pass


async def _notify_report_chats(request: web.Request, row, vlat, vlon,
                               dist_m: int | None = None, uid: int = 0) -> None:
    """Отчёт о визите в закреплённые беседы (опциональная фича, настраивается
    пультом): текст по образцу постов сотрудников + фото за день + фактические
    гео-точки (закрытие визита и съёмка фото — такое не подделаешь)."""
    bot = request.app.get("bot")
    if not bot:
        return
    try:
        chat_ids = db.report_chats_for(row["responsible"] or "")
        if not chat_ids:
            return
        files = day_photos(db.person_contracts(row["contract_id"]),
                           date.today().isoformat())
        rels = [f"{p.parent.name}/{p.name}" for p in files]
        geo_map = db.photo_geo_for_files(rels)
        pg = next(((g["lat"], g["lon"]) for r in rels if (g := geo_map.get(r))), None)
        from notify import post_visit_report, visit_report_text
        # Кто ФАКТИЧЕСКИ закрыл визит. У выездника это он сам (привязка), у
        # руководителя привязки нет — значит он работал от имени сотрудника,
        # и отчёт обязан это показать: иначе координаты руководителя уедут
        # в гео-контроль как координаты выездника.
        actor = db.get_binding(uid) or _acting_actor_name(uid)
        text = visit_report_text(
            row, dist_m=dist_m,
            visit_geo=(vlat, vlon) if vlat is not None else None,
            photo_geo=pg, photos_n=len(files), actor=actor)
        await post_visit_report(bot, chat_ids, text, files)
    except Exception as e:  # noqa: BLE001 — пост не должен ломать сохранение визита
        logging.warning("report chats notify failed: %s", e)


async def _notify_managers(request: web.Request, row, dist_m: int | None = None) -> None:
    """Реалтайм-пуш: визит закрыт → руководителям этого сотрудника в личку уходит
    сводка + фото за день альбомом. Дедуп повторов из оффлайн-очереди — снаружи
    (is_new от log_visit). Рубильник без редеплоя: settings.visit_push = 0."""
    bot = request.app.get("bot")
    if not bot or (db.get_setting("visit_push") or "1").strip().lower() in ("0", "false", "off"):
        return
    import html as _html
    e = lambda v: _html.escape(str(v or ""))   # бот шлёт с parse_mode=HTML
    emp = row["responsible"] or ""
    targets = [m for m in db.list_managers() if emp in (m.get("employees") or [])]
    if not targets:
        return
    files = day_photos(db.person_contracts(row["contract_id"]), date.today().isoformat())
    text = (f"✅ Выезд отработан\n👤 {e(row['client'])} ({e(row['contract_id'])})\n"
            f"🧑‍💼 {e(emp)}\n"
            f"📝 {e(row['call_status']) or '—'} · {e(row['agreed_payment']) or '—'}\n"
            f"💬 {e(row['visit_comment'] or row['call_comment']) or '—'}"
            + (f"\n📸 {len(files)} фото" if files else "\n📸 без фото"))
    if dist_m is not None:
        pretty = f"{dist_m / 1000:.1f} км" if dist_m >= 1000 else f"{dist_m} м"
        text += (f"\n⚠️ Закрыт в {pretty} от адреса!" if dist_m > db.geo_alert_m()
                 else f"\n📍 На адресе ({pretty} от точки)")
    from notify import send_album
    for m in targets:
        try:
            if files:
                await send_album(bot, m["telegram_id"], files, text)
            else:
                await bot.send_message(m["telegram_id"], text)
        except Exception as e:  # noqa: BLE001 — пуш не должен ломать сохранение визита
            logging.warning("manager push failed: tid=%s err=%s", m["telegram_id"], e)


# --- «Карта дня» руководителя (подписанная ссылка из дайджеста) --------------
def _map_auth(request: web.Request) -> tuple[str, str]:
    q = request.query
    emp, d = q.get("e", ""), q.get("d", "")
    if not maplink.verify(emp, d, q.get("x", ""), q.get("s", "")):
        raise web.HTTPForbidden(
            text="Ссылка устарела. Откройте карту заново из дайджеста в боте.")
    return emp, d


async def _manager_map_data(request: web.Request) -> web.Response:
    emp, d = _map_auth(request)
    pts = db.day_map_points(emp, d)
    return web.json_response({
        "ok": True, "employee": emp, "date": d, **pts,
        "vectorTiles": "/tiles/basemap.pmtiles" if TILES_FILE.exists() else None,
        "alert_m": db.geo_alert_m(),
    })


async def _manager_map(request: web.Request) -> web.Response:
    _map_auth(request)
    return web.Response(text=MAP_PAGE, content_type="text/html", charset="utf-8")


# Отдельная лёгкая страница (не миниапп): руководитель открывает её из Телеги по
# подписанной ссылке. Библиотеки карт — c CDN (руководитель онлайн), вектор PMTiles —
# если подложен на сервере, иначе растровый OSM (тот же фолбэк, что в миниаппе).
MAP_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Карта дня</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.min.css">
<style>
html,body{margin:0;height:100%;font-family:Inter,-apple-system,Segoe UI,Roboto,sans-serif}
#map{position:absolute;inset:0}
.hdr{position:absolute;z-index:5;top:10px;left:10px;right:10px;background:#fff;
  border:1px solid #DEE4EA;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.08);
  padding:10px 14px;font-size:13px;color:#222}
.hdr b{font-size:14px}
.leg{margin-top:6px;color:#666;font-size:12px;display:flex;gap:12px;flex-wrap:wrap}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:-1px}
.pin{width:22px;height:22px;border-radius:50%;border:2px solid #fff;
  box-shadow:0 1px 4px rgba(0,0,0,.4);color:#fff;font-size:11px;font-weight:700;
  display:flex;align-items:center;justify-content:center}
.empty{position:absolute;z-index:4;inset:0;display:flex;align-items:center;
  justify-content:center;color:#666;font-size:15px;background:#F4F6F8}
</style></head><body>
<div id="map"></div><div class="hdr" id="hdr">Загрузка…</div>
<script src="https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/pmtiles@3.2.1/dist/pmtiles.js"></script>
<script src="https://cdn.jsdelivr.net/npm/protomaps-themes-base@4.4.0/dist/protomaps-themes-base.js"></script>
<script>
const RASTER={version:8,sources:{osm:{type:"raster",
  tiles:["https://a.tile.openstreetmap.org/{z}/{x}/{y}.png","https://b.tile.openstreetmap.org/{z}/{x}/{y}.png"],
  tileSize:256,maxzoom:19,attribution:"© OpenStreetMap"}},layers:[{id:"osm",type:"raster",source:"osm"}]};
function vstyle(url){
  maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
  const ptb=window.protomaps_themes_base;
  const layers=ptb.layers?ptb.layers("protomaps","light","ru"):ptb.default("protomaps","light");
  return {version:8,glyphs:"https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
    sprite:"https://protomaps.github.io/basemaps-assets/sprites/v4/light",
    sources:{protomaps:{type:"vector",url:"pmtiles://"+location.origin+url,attribution:"© OpenStreetMap"}},layers};
}
function pin(bg,txt){const e=document.createElement("div");e.className="pin";
  e.style.background=bg;e.textContent=txt||"";return e;}
(async()=>{
  // Всё, что пришло из БД, попадает в innerHTML/setHTML — а подпись метки и ФИО
  // вводит человек. Без экранирования это хранимый XSS: выездник сохраняет метку
  // с тегом, руководитель открывает карту дня и исполняет его в своей сессии.
  const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,
    c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const r=await fetch("/manager/map_data"+location.search);
  if(!r.ok){document.getElementById("hdr").textContent="Ссылка устарела — откройте карту из дайджеста заново.";return;}
  const j=await r.json();
  const pts=[...j.targets,...j.visits,...j.markers];
  document.getElementById("hdr").innerHTML=
    "<b>🗺 "+esc(j.employee)+"</b> · "+esc(j.date)+
    "<div class='leg'><span><span class='dot' style='background:#2F80ED'></span>адрес должника ("+j.targets.length+")</span>"+
    "<span><span class='dot' style='background:#219653'></span>визит закрыт ("+j.visits.length+")</span>"+
    "<span><span class='dot' style='background:#B0472C'></span>визит далеко от адреса</span>"+
    "<span><span class='dot' style='background:#F2994A'></span>метка ("+j.markers.length+")</span></div>";
  if(!pts.length){const e=document.createElement("div");e.className="empty";
    e.textContent="За этот день гео-точек нет";document.body.appendChild(e);return;}
  let style=RASTER;
  if(j.vectorTiles&&window.pmtiles&&window.protomaps_themes_base){try{style=vstyle(j.vectorTiles);}catch(e){}}
  const map=new maplibregl.Map({container:"map",style,center:[pts[0].lon,pts[0].lat],
    zoom:12,attributionControl:false,dragRotate:false});
  let fell=false;
  map.on("error",()=>{if(!fell&&style!==RASTER){fell=true;try{map.setStyle(RASTER);}catch(e){}}});
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),"top-right");
  const b=new maplibregl.LngLatBounds();
  const add=(p,el,html)=>{
    new maplibregl.Marker({element:el,anchor:"center"}).setLngLat([p.lon,p.lat])
      .setPopup(new maplibregl.Popup({offset:14}).setHTML(html)).addTo(map);
    b.extend([p.lon,p.lat]);
  };
  j.targets.forEach(p=>add(p,pin("#2F80ED"),"🏠 "+esc(p.client)));
  j.visits.forEach((p,i)=>{
    const far=p.dist_m!=null&&p.dist_m>j.alert_m;
    const d=p.dist_m==null?"":(p.dist_m>=1000?(p.dist_m/1000).toFixed(1)+" км":p.dist_m+" м")+" от адреса";
    add(p,pin(far?"#B0472C":"#219653",String(i+1)),
        (far?"⚠️ ":"✅ ")+esc(p.client)+(d?"<br>"+esc(d):""));
  });
  j.markers.forEach(p=>add(p,pin("#F2994A"),
    "📍 "+esc(p.label||"метка")+(p.at?" · "+esc(p.at):"")));
  map.fitBounds(b,{padding:70,maxZoom:15});
})();
</script></body></html>"""


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
        "countries": db.stats_by_country(),
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
    """Legacy multipart import is intentionally disabled.

    It cannot carry the versioned publication/test-scope receipt used by the
    JSON boundary, so accepting it would reopen a stale-client bypass.
    """
    _admin(request)
    return web.json_response(
        {
            "blocked": True,
            "error": (
                "multipart import отключён: используйте contract-bound "
                "/admin/import.json с exact source proof"
            ),
        },
        status=409,
    )


async def _admin_import_json(request: web.Request) -> web.Response:
    """Импорт published master: гриды уже распарсены клиентом (calamine).

    ``source_kind`` обязателен. Personal-books → bot здесь не существует:
    personal ↔ master обслуживает только registry_cycle.
    """
    _admin(request)
    body = await request.json()
    source_kind = body.get("source_kind")
    if source_kind not in db.IMPORT_SOURCE_KINDS:
        return web.json_response(
            db.import_grid([], source_kind=source_kind),
            status=409,
        )
    try:
        validate_contract_binding(body.get("data_exchange_contract"))
        grid = db.pick_grid(body.get("sheets") or [])
    except (ExchangeContractError, ValueError) as exc:
        return web.json_response(
            {"blocked": True, "error": f"импорт заблокирован: {exc}"},
            status=409,
        )
    report = db.import_grid(
        grid,
        source_kind=source_kind,
        source_proof=body.get("source_proof"),
        require_source_proof=True,
    )
    if report.get("blocked"):
        return web.json_response(report, status=409)
    # A consumed receipt is an exact zero-write replay. Do not run ancillary
    # mutators behind it: otherwise replaying the same import could still
    # change coordinates after the atomic import transaction.
    if report.get("idempotent_replay"):
        return web.json_response(report)
    try:
        import scoring
        report["geocoded"] = scoring.geocode_city_centroids()
    except Exception as e:  # noqa: BLE001
        logging.warning("geocode after import: %s", e)
    return web.json_response(report)


async def _admin_chats(request: web.Request) -> web.Response:
    """Пульту: беседы, которые бот собрал (его добавляли в группы), + список ФИО
    из реестра для закрепления."""
    _admin(request)
    return web.json_response({
        "chats": db.list_report_chats(),
        "employees": [r["employee"] for r in db.employees_in_registry()]})


async def _admin_chats_set(request: web.Request) -> web.Response:
    """Настройка беседы из пульта: {chat_id, enabled?, employees?}."""
    _admin(request)
    body = await request.json()
    try:
        chat_id = int(body["chat_id"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="chat_id")
    enabled = body.get("enabled")
    employees = body.get("employees")
    db.set_report_chat(
        chat_id,
        enabled=None if enabled is None else bool(enabled),
        employees=None if employees is None else [str(e) for e in employees])
    return web.json_response({"ok": True, "chats": db.list_report_chats()})


async def _admin_export_json(request: web.Request) -> web.Response:
    """Данные пульта: по умолчанию безопасный поток для обогащения.

    ``?mode=full`` — отдельный raw-слепок БД для команды «Реестр целиком».
    Старый пульт без параметра тоже получает очищенный поток и потому не может
    повторно разнести импортные псевдовизиты по живым файлам.
    """
    _admin(request)
    rows = db.registry_rows() if request.query.get("mode") == "full" \
        else db.enrichment_rows()
    return web.json_response(
        {
            "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
            "fields": db.export_schema(),
            "rows": rows,
        }
    )


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


async def _admin_emp_clients(request: web.Request) -> web.Response:
    """Договоры сотрудника для «убрать клиентов» в пульте: ?employee=ФИО →
    {clients:[{contract_id, client, debt, unit}]} в порядке ФИО клиента."""
    _admin(request)
    employee = (request.query.get("employee") or "").strip()
    if not employee:
        raise web.HTTPBadRequest(text="employee")
    out = [{"contract_id": r["contract_id"], "client": r["client"],
            "debt": debt.contract_debt(r), "unit": debt.unit([r["partner"]])}
           for r in db.clients_of_employee(employee)]
    return web.json_response({"clients": out})


async def _admin_unassign(request: web.Request) -> web.Response:
    """Снять договоры с сотрудника: {employee, contract_ids:[…]}. Клиенты остаются
    в реестре без ответственного и пропадают из миниаппа сотрудника. {ok, n}."""
    _admin(request)
    body = await request.json()
    employee = (body.get("employee") or "").strip()
    ids = body.get("contract_ids") or []
    if not employee or not isinstance(ids, list) or not ids:
        raise web.HTTPBadRequest(text="employee/contract_ids")
    n = db.unassign_clients(employee, ids)
    db.audit_access(ADMIN_ID, "unassign", employee, str(n))
    return web.json_response({"ok": True, "n": n})


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


# Точные геокодеры (Nominatim/Photon/Yandex) — публичные сервисы. Пускать в них
# адреса должников С СЕРВЕРА нельзя: это трансграничная передача ПД третьей стороне,
# и запрещено это прямо (SECURITY.md, docstring geocode.py). Раньше кнопка «Запустить
# геокодинг» в пульте била сюда, а отсюда `geocode_all()` вызывался БЕЗ аргументов —
# то есть с use_precise=True по умолчанию, и каждый адрес без координат уезжал
# в nominatim.openstreetmap.org. Ни флага, ни проверки не было.
#
# Теперь сервер считает только офлайн-справочником (он же здесь и лежит — geoindex
# с дампами GeoNames и OSM). Точный геокодинг остаётся в пульте, на машине владельца.
GEOCODER_ALLOW_PUBLIC = os.getenv("GEOCODER_ALLOW_PUBLIC", "").strip() in ("1", "true", "yes")


async def _admin_geocode(request: web.Request) -> web.Response:
    _admin(request)
    import geocode
    asyncio.get_event_loop().run_in_executor(
        None, functools.partial(geocode.geocode_all, use_precise=GEOCODER_ALLOW_PUBLIC))
    return web.json_response({"ok": True, "started": True,
                              "precise": GEOCODER_ALLOW_PUBLIC})


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
    # account — КТО владеет этим telegram_id по данным бота (или null, если бот его
    # не видел). «name» — всего лишь подпись оператора, права получает владелец id;
    # пульт сверяет одно с другим и показывает расхождение сразу, а не через неделю.
    return web.json_response({"ok": True, "manager": db.get_manager(tid),
                              "account": db.guest(tid)})


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
        for key, setting in (("time", "digest_time"), ("promise_time", "promise_time")):
            raw = (b.get(key) or "").strip()
            if raw:
                if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", raw):
                    raise web.HTTPBadRequest(text=f"bad {key} (HH:MM)")
                db.set_setting(setting, raw)
        if "enabled" in b:
            db.set_setting("digest_enabled", "1" if b.get("enabled") else "0")
        if "promise_enabled" in b:
            db.set_setting("promise_enabled", "1" if b.get("promise_enabled") else "0")

    def _on(key):
        return (db.get_setting(key) or "1").strip().lower() not in ("0", "false", "no", "off")

    return web.json_response({
        "ok": True,
        "time": db.get_setting("digest_time") or "16:30",
        "enabled": _on("digest_enabled"),
        "promise_time": db.get_setting("promise_time") or "09:30",
        "promise_enabled": _on("promise_enabled"),
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


async def _admin_notify(request: web.Request) -> web.Response:
    """Сообщение владельцу бота: {text:str}. Нужно суточному циклу на машине Егора
    (pult/daily_cycle.py) — он работает по расписанию и без этого молчит. Молчание
    планировщика опаснее ошибки: незамеченный сбой = сутки без обмена данными."""
    _admin(request)
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="text required")
    bot = request.app.get("bot")
    if not bot or not ADMIN_ID:
        raise web.HTTPServiceUnavailable(text="нет бота или не задан ADMIN_ID")
    import html as _html
    await bot.send_message(ADMIN_ID, _html.escape(text[:3500]))
    return web.json_response({"ok": True})


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
    """Keep the legacy URL fail-closed during the limited-test contour.

    Both historical scopes destroy provenance outside the evidence-bound
    snapshot/clearance/reset transaction. The route remains present so stale
    clients receive an explicit refusal instead of mistaking a 404 for a
    network failure.
    """
    _admin(request)
    return web.json_response(
        {
            "blocked": True,
            "error": (
                "legacy admin reset отключён: используйте только "
                "evidence-bound limited-test reset с snapshot и clearance"
            ),
        },
        status=409,
    )


def _apply_cors(request: web.Request, resp: web.StreamResponse) -> None:
    """Навесить CORS-заголовки на ЛЮБОЙ ответ, включая ошибочный.

    КРИТИЧНО: заголовки надо ставить и на ответы-ошибки (401/404/400/500). Если хендлер
    кинул HTTPException или упал, а CORS-заголовков нет — браузер/webview пульта блокирует
    ответ и показывает «Failed to fetch» вместо настоящего статуса. Так, например, POST на
    отсутствующую (старый сервер) ручку `/admin/reset` возвращает 404, но без заголовков он
    выглядит как «нет связи» — реальную причину не видно."""
    if request.path.startswith("/admin/"):
        # /admin/* — десктоп-пульт, по природе кросс-ориджин к серверу (работает не
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
    app.router.add_get("/sw.js", _sw)
    app.router.add_get("/manifest.webmanifest", _manifest)
    # Локально захостенные ассеты оболочки (Telegram SDK, иконки PWA) — same-origin,
    # чтобы миниапп поднимался в оффлайне без внешних CDN (их кэширует service worker).
    app.router.add_static("/vendor/", path=str(MINIAPP_DIR / "vendor"))
    app.router.add_get("/admin/stats", _admin_stats)
    app.router.add_post("/admin/import", _admin_import)
    app.router.add_post("/admin/import.json", _admin_import_json)
    app.router.add_get("/admin/chats", _admin_chats)
    app.router.add_post("/admin/chats.set", _admin_chats_set)
    app.router.add_get("/admin/export", _admin_export)
    app.router.add_get("/admin/export.json", _admin_export_json)
    app.router.add_get("/admin/markers.xlsx", _admin_markers_xlsx)
    app.router.add_get("/admin/markers.json", _admin_markers_json)
    app.router.add_get("/admin/employees", _admin_employees)
    app.router.add_get("/admin/emp_clients", _admin_emp_clients)
    app.router.add_post("/admin/unassign", _admin_unassign)
    app.router.add_get("/admin/guests", _admin_guests)
    app.router.add_post("/admin/bind", _admin_bind)
    app.router.add_post("/admin/unbind", _admin_unbind)
    app.router.add_post("/admin/bot_token", _admin_bot_token)
    app.router.add_post("/admin/geocode", _admin_geocode)
    app.router.add_get("/admin/priority", _admin_priority)
    app.router.add_get("/admin/activity", _admin_activity)
    app.router.add_post("/admin/notify", _admin_notify)
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
    app.router.add_get("/manager/map", _manager_map)
    app.router.add_get("/manager/map_data", _manager_map_data)
    app.router.add_get("/tiles/basemap.pmtiles", _tiles)
    app.router.add_get("/api/bootstrap", _bootstrap)
    app.router.add_get("/api/person", _person)
    app.router.add_get("/api/cards", _cards)
    app.router.add_get("/api/route", _route)
    app.router.add_get("/api/route_to", _route_to)
    app.router.add_get("/api/geocode", _geocode_free)
    app.router.add_get("/api/geo/suggest", _geo_suggest)
    app.router.add_post("/api/track", _track)
    app.router.add_post("/api/act_as", _act_as)
    app.router.add_get("/api/route_day", _route_day)
    app.router.add_post("/api/save", _save)
    app.router.add_post("/api/photo", _photo)
    app.router.add_post("/api/voice", _voice)
    app.router.add_post("/api/lang", _lang)
    app.router.add_post("/api/redeem", _redeem)
    app.router.add_post("/admin/app_revoke", _admin_app_revoke)
    app.router.add_post("/api/bind", _bind)
    app.router.add_post("/api/marker", _marker)
    app.router.add_get("/api/markers", _markers)
    app.router.add_post("/api/note", _note_add)
    app.router.add_get("/api/notes", _notes)
    app.router.add_post("/api/note_delete", _note_delete)
    return app



class _PathOnlyAccessLogger(AbstractAccessLogger):
    """Access-лог без query-строки: в ней ездят адреса должников и координаты."""

    def log(self, request, response, time):
        self.logger.info("%s %s %s %s %.3f",
                         request.remote or "-", request.method, request.path,
                         response.status, time)

async def start_site(bot=None, host: str = "0.0.0.0", port: int = 8080):
    """Поднять веб-сервер рядом с поллингом (один asyncio-loop)."""
    # Access-лог aiohttp по умолчанию печатает ПОЛНЫЙ путь с query-строкой, а туда
    # попадают адрес должника (/api/geocode?q=…) и живые координаты выездника
    # (/api/route?lat=…&lon=…). Логи контейнера читает кто угодно с доступом
    # к серверу и уезжают в бэкапы — это хранение ПД там, где его быть не должно.
    # Свой логгер печатает метод, ПУТЬ БЕЗ ЗАПРОСА, код и размер — этого хватает
    # для диагностики.
    runner = web.AppRunner(build_app(bot), access_log_class=_PathOnlyAccessLogger)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner


if __name__ == "__main__":
    # Автономный запуск для локального теста миниаппа без бота: HARDBOT_DEV=1 python web.py
    db.init_db()
    web.run_app(build_app(), host="127.0.0.1", port=int(os.getenv("MINIAPP_PORT", "8080")))
