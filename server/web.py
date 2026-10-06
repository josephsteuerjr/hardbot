"""
Веб-сервер миниаппа выездника.

aiohttp идёт в комплекте с aiogram — новых зависимостей нет. Раздаёт статику из
./miniapp и небольшое JSON-API поверх db.py.

Личность выездника берётся из подписи Telegram WebApp (`initData`, HMAC по токену
бота) — клиенту доверять не нужно. Локальный дев-режим (`APP_DEV=1`, старое имя `HARDBOT_DEV`) разрешает
открыть миниапп в обычном браузере, подставив telegram_id (заголовок `X-Dev-Uid`
или `?dev_uid=`). В боевом режиме дев-обход выключен.
"""

from __future__ import annotations

import asyncio
import collections
import functools
import hashlib
import hmac
import html as _html
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from aiohttp.abc import AbstractAccessLogger

import addr_cis                       # разбор произвольного адреса для навигации
import db
import workday
import config
import ops_panel
import debt
import geoindex                       # офлайн-справочник координат (без похода в сеть)
from config import ADMIN_ID, approval_required, lang_of
from exchange_contract import (
    FORM_AMOUNT_KEYS,
    FORM_DATE_KEYS,
    ExchangeContractError,
    FORM_DROPDOWN_KEYS,
    FORM_PROTOCOL_VERSION,
    FORM_TEXT_KEYS,
    OFFICE_RESULT_SNAPSHOT_KEY,
    contract_binding,
    validate_contract_binding,
)
from fields import BY_KEY, DROPDOWNS, SOFT
from i18n import FIELD_TITLES, FORM_FIELD_TITLES, LANGUAGES, t, tf, tv
import addr_learn
import control
import digest_data
import evidence
import maplink
import routing_client
import workload
import stt_client
from keyboards import approve_bind_kb
import photoaudit
import photostore
from photostore import PHOTO_DIR, day_photos

# CORS на статике/тайлах: по умолчанию НЕ ставим «*» (миниапп и тайлы — same-origin
# через свой aiohttp/Caddy). Кросс-ориджин — только если явно задан CORS_ALLOW_ORIGIN.
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "").strip()

MINIAPP_DIR = Path(__file__).parent / "miniapp"
# Свой вектор-тайлсет (Protomaps PMTiles). Если файла нет — карта на растровом OSM.
TILES_FILE = Path(os.getenv("APP_DATA") or os.getenv("HARDBOT_DATA") or Path(__file__).parent) / "basemap.pmtiles"
# Маршрутизаторы UZ/TJ проверяются по расстоянию привязки к дорожному графу: соседний
# граф тоже может вернуть Ok, но «притянуть» точку на десятки километров к границе.
# OSRM_URL оставлен как совместимый одиночный фолбэк.
OSRM_URL = (os.getenv("OSRM_URL", "") or "").strip()
OSRM_URL_UZ = (os.getenv("OSRM_URL_UZ", "") or "").strip()
OSRM_URL_TJ = (os.getenv("OSRM_URL_TJ", "") or "").strip()
VROOM_URL = (os.getenv("VROOM_URL", "") or "").strip()
# Голос распознаёт отдельная служба (контейнер stt/) через Unix-сокет с токеном.
# Локального фолбэка намеренно нет: распознаватель внутри процесса не помещался
# в лимит памяти и пять раз приводил к cgroup OOM. Некорректный/пустой backend =
# фича выключена. VOICE_BACKEND=stt — штатное значение; «praxis» — прежнее имя,
# оставлено рабочим для уже настроенного прода.
VOICE_BACKEND = (os.getenv("VOICE_BACKEND", "off") or "off").strip().casefold()
VOICE_BACKENDS = {"stt", "praxis"}


def _stt_env(new: str, old: str, default: str, environ=None) -> str:
    """Нейтральный ключ STT_* главнее; старый PRAXIS_STT_* остаётся рабочим (прод)."""
    env = os.environ if environ is None else environ
    return ((env.get(new) or "").strip() or (env.get(old) or "").strip() or default)


STT_SOCKET = _stt_env("STT_SOCKET", "PRAXIS_STT_SOCKET", "/run/stt/stt.sock")
STT_TOKEN_FILE = _stt_env("STT_TOKEN_FILE", "PRAXIS_STT_TOKEN_FILE", "/run/secrets/stt.token")
try:
    VOICE_TIMEOUT_SECONDS = max(
        5.0,
        min(float(os.getenv("VOICE_TIMEOUT_SECONDS", "60")), 70.0),
    )
except (TypeError, ValueError):
    VOICE_TIMEOUT_SECONDS = 60.0
DEV = (os.getenv("APP_DEV") or os.getenv("HARDBOT_DEV") or "").lower() not in ("", "0", "false", "no")
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
# Типизированные поля формы (обещание выездника, контракт v3; на v2 пусты).
FORM_AMOUNTS = list(FORM_AMOUNT_KEYS)
FORM_DATES = list(FORM_DATE_KEYS)
# Контур «Софт»: ключи софт-формы (источник поля «софт») не показываются
# в форме ВИЗИТА выездника — у звонка своя форма и свой экран.
SOFT_FORM_KEYS = frozenset(
    k for k in (*FORM_DROPDOWNS, *FORM_TEXT, *FORM_AMOUNTS, *FORM_DATES)
    if k in BY_KEY and BY_KEY[k].source == SOFT
)
FORM_DROPDOWNS_VISIT = tuple(
    k for k in FORM_DROPDOWNS if k not in SOFT_FORM_KEYS
)
# Что показываем во вкладке «Клиент» (плотные строки).
INFO_KEYS = ["birth_date", "phone", "phone_main", "city", "address",
             "address_customer", "extra_address", "responsible", "tips"]
# Вкладка «Долг»: статьи разбивки считает debt.breakdown (набор зависит от
# контрагента), подписи — локализованные, суммы — числа. Раньше здесь лежал
# плоский DEBT_KEYS, из-за которого миниапп рисовал ключи БД («camera_fine»).
PROMISE_KEYS = ["agreed_payment", "promise_date", "last_payment_sum",
                "last_payment_date", "forecast"]
ALLOWED_SAVE = set(FORM_DROPDOWNS) | set(FORM_TEXT) | set(FORM_AMOUNTS) \
    | set(FORM_DATES)
# Потолок свободного текста формы (visit_comment, tips). Длиннее — обрезаем, а
# не 400: на любой 4xx оболочка откладывает запись очереди «на ручной разбор»,
# и на сервер не доехал бы весь выезд, а не хвост комментария. У поля ввода тот
# же maxlength, так что обрезка — страховка от старой оболочки и чужого клиента.
VISIT_TEXT_MAX = 4000
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
    # Байтами по той же причине, что и в maplink: не-ASCII в чужом hash
    # бросал TypeError и превращал отказ авторизации в 500.
    if not hmac.compare_digest(calc.encode(), (received or "").encode()):
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
    if not data and _is_drain(request):
        # Старый адрес (только выгрузка): мини-приложение старого бота досылает
        # очередь с его подписью. Токен старого бота проверяет ТОЛЬКО подпись
        # и ТОЛЬКО здесь: сам бот не запускается, основной адрес его не знает.
        data = validate_init_data(request.headers.get("X-Init-Data") or "",
                                  os.getenv("DRAIN_BOT_TOKEN", ""))
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
        # Приложение выездника на ПК (agent_devices): свой класс токена, работает
        # теми же ручками, что телефон. Привязку agent_bind не трогает — телефон
        # живёт своей жизнью, вытеснения нет.
        state, dev = db.agent_device_check(tok, request.remote)
        if state == "ok" and dev:
            request["desk_employee"] = dev["employee"]
            return int(dev["telegram_id"] or 0) or None
    if DEV:
        raw = request.headers.get("X-Dev-Uid") or request.query.get("dev_uid")
        try:
            return int(raw) if raw else None
        except ValueError:
            return None
    return None


def _remember_from_init(request: web.Request) -> None:
    """Запомнить, КТО открыл приложение: id, @ник, имя.

    `touch_guest` звали только обработчики БОТА. Кто заходил сразу в приложение —
    а это штатный путь, миниапп открывается кнопкой меню — оставался в списке
    голым числом без имени и ника. 03.08.2026 на этом сгорел час: два аккаунта
    выбрали чужие фамилии, и опознать их было нечем, кроме как спрашивать людей.

    Тихо: сбой БД не должен ломать вход в приложение — имя это удобство, а не
    условие доступа.
    """
    try:
        data = validate_init_data(request.headers.get("X-Init-Data") or "",
                                  os.getenv("BOT_TOKEN", ""))
        user = (data or {}).get("user")
        if not isinstance(user, dict):
            return
        uid = int(user.get("id") or 0)
        if not uid:
            return
        name = " ".join(part for part in (user.get("first_name"), user.get("last_name"))
                        if part).strip()
        db.touch_guest(uid, user.get("username"), name or None)
    except Exception:                                  # noqa: BLE001
        pass


def _manager_scope(uid: int) -> list[str]:
    """Сотрудники, чьи портфели вправе открыть этот руководитель (или владелец)."""
    return db.manager_employees(uid, is_owner=(uid == ADMIN_ID))


def _manager_gate(request: web.Request) -> tuple[int, dict, list[str]]:
    """uid + mgr-словарь + состав команды для вкладки руководителя. БЕЗ acting.

    _auth сюда не годится по двум причинам: он даёт 403 «not bound» руководителю
    без сотрудников в scope и скоупит данные на ОДНОГО acting-сотрудника, а ещё
    db.acting_employee по дороге ЗАПИСЫВАЕТ фолбэк в settings — открытие вкладки
    не должно менять портфель, который человек смотрит.

    Пускаем по факту РОЛИ, а не по размеру команды: у направленца пустой состав
    значит «сегодня по направлению нет ответственных», а у владельца сразу после
    сброса и до первого импорта реестр пуст — старая проверка давала бы 403 на
    собственной вкладке (этот случай уже жёг прод, см. test_manager_app_access).
    Пустая команда отдаётся пустым экраном, не отказом.

    Пульт на компьютере (Пульты 2) предъявляет X-Pult-Token — токен этого
    компьютера. Он принимается ТОЛЬКО здесь: портфель выездника им не открыть.
    Безопасник смотрит, но ничего не меняет: пишущие ручки и выдача входа для
    него закрыты (403 read_only)."""
    ptok = (request.headers.get("X-Pult-Token") or "").strip()
    if ptok:
        state, dev = db.manager_device_check(ptok, _admin_peer(request))
        if state != "ok":
            raise _json_error(web.HTTPUnauthorized, f"device_{state}")
        uid = int(dev["telegram_id"])
    else:
        uid = _uid(request)
    if not uid:
        raise web.HTTPUnauthorized(text="auth")
    mgr = digest_data.manager_scope(uid, is_owner=(uid == ADMIN_ID))
    if mgr is None:
        raise web.HTTPForbidden(text="not a manager")
    caps = db.manager_caps(mgr.get("role"), is_owner=(uid == ADMIN_ID))
    # «Только чтение» — про ручки руководителя; отметка обзвона в «Контроле» —
    # работа самого безопасника.
    if not caps["team_links"] and request.path.startswith("/api/manager/") and (
            request.method != "GET" or request.path in _LEAD_ONLY_PATHS):
        raise _json_error(web.HTTPForbidden, "read_only")
    if request.path.startswith("/api/control/") and not caps["control"]:
        raise _json_error(web.HTTPForbidden, "no_control")
    return uid, mgr, digest_data.sorted_employees(mgr)


# Чтение, которое безопаснику всё равно не положено: ссылки входа команды.
_LEAD_ONLY_PATHS = frozenset({"/api/manager/app_links"})


def _json_error(cls, code: str, **extra):
    """HTTP-ошибка с телом {"error": code}: пульт показывает её словами."""
    return cls(text=json.dumps({"error": code, **extra}), content_type="application/json")


def _day_team(mgr: dict, team: list[str], iso: str) -> list[str]:
    """Команда за день: живой состав плюс те, кто в этот день выезжал, даже
    если с них с тех пор сняли договоры (digest_data.window_team)."""
    return digest_data.window_team(mgr, team, iso, iso)


def _range_team(mgr: dict, team: list[str], rng: dict) -> list[str]:
    """Команда за окно статистики — см. _day_team."""
    return digest_data.window_team(mgr, team, rng["from"], rng["to"])


def _manager_date(request: web.Request) -> tuple[str, str, str, str | None]:
    """(date, today, prev, next). Дату «сегодня» и соседние сутки считает
    СЕРВЕР: контейнер живёт по UTC, а телефон руководителя — по +3/+5, и
    клиентская дата в утренние часы показывала бы пустой день."""
    today = digest_data.today_iso()
    raw = (request.query.get("date") or "").strip()
    d = db._as_date(raw) if raw else db._as_date(today)
    if d is None:
        raise web.HTTPBadRequest(text="date")
    iso = d.isoformat()
    prev = (d - timedelta(days=1)).isoformat()
    nxt = None if iso >= today else (d + timedelta(days=1)).isoformat()
    return iso, today, prev, nxt


def _manager_employee_q(request: web.Request, team: list[str]) -> str:
    emp = (request.query.get("employee") or "").strip()
    if emp not in team:
        # 404, не 403: «сотрудник не в вашей команде» — 403 остаётся ровно
        # одним смыслом «роли нет» (клиент по нему гасит вкладку целиком).
        raise web.HTTPNotFound(text="employee")
    return emp


def _manager_json(payload: dict) -> web.Response:
    resp = web.json_response(digest_data._public(payload))
    resp.headers["Cache-Control"] = "no-store"   # ПД не должны оседать в кэшах
    resp.enable_compression()   # кириллица = 2 байта/символ, канал самый слабый
    return resp


def _manager_envelope(mgr, team, iso, today, prev, nxt, uid) -> dict:
    frm, to = db.get_period()
    note = ""
    if (frm or "").strip() or (to or "").strip():
        note = (f"окно периода {frm or '…'}—{to or '…'} к этой вкладке не "
                "применяется: здесь все выезды за день")
    return {"ok": True, "date": iso, "today": today,
            "prev_date": prev, "next_date": nxt, "team": team,
            "period_note": note,
            "manager": {"name": mgr.get("name") or "",
                        "scope": digest_data.scope_label(mgr),
                        "countries": digest_data.scope_countries_info(mgr),
                        "isOwner": uid == ADMIN_ID,
                        # Права видит и приложение: «Вход команды» безопаснику
                        # не показывается (сервер его всё равно не пустит).
                        "caps": db.manager_caps(mgr.get("role"),
                                                is_owner=uid == ADMIN_ID)}}


def _employee(uid: int, request: web.Request | None = None) -> str | None:
    # Приложение выездника на ПК знает СВОЁ ФИО из строки устройства: оно не
    # следует за привязкой (телефон могли сменить), портфель на ПК стабилен.
    if request is not None:
        desk = request.get("desk_employee")
        if desk:
            return str(desk)
    """Кем пользователь работает в приложении.

    У выездника это его привязка. У РУКОВОДИТЕЛЯ привязки нет и быть не должно —
    у него своя роль, — поэтому он «действует от имени» одного из своих сотрудников
    и переключается между ними. Без этого руководитель со ссылкой входа упирался
    в 403 «not bound»: ссылка выдавалась, а приложение не пускало.

    РОЛЬ СИЛЬНЕЕ ПРИВЯЗКИ. Если оператор по ошибке вписал telegram_id руководителя
    на экран «Роли» как выездника, тот молча терял переключатель и запирался в
    чужом портфеле, а его собственный визит уходил в гео-контроль от имени этого
    сотрудника. Роль назначает владелец осознанно, привязка появляется самовыбором —
    при конфликте выигрывает первое.
    """
    scope = _manager_scope(uid)
    emp = db.get_binding(uid)
    if emp and not scope:
        return emp
    acting = db.acting_employee(uid, scope)
    if acting:
        return acting
    if emp:
        return emp                       # роль есть, но сотрудников в ней нет
    if DEV and uid == ADMIN_ID:  # владельцу в дев-режиме показываем первого из реестра
        emps = db.employees_in_registry()
        return emps[0]["employee"] if emps else None
    return None


def _auth(request: web.Request) -> tuple[int, str]:
    uid = _uid(request)
    if not uid:
        raise web.HTTPUnauthorized(text="auth")
    emp = _employee(uid, request)
    if not emp:
        raise web.HTTPForbidden(text="not bound")
    return uid, emp


def _require_field_write(uid: int) -> None:
    """Security may inspect employee cards, but cannot edit their facts."""
    mgr = db.get_manager(uid)
    if uid != ADMIN_ID and mgr and db.manager_role(mgr.get("role")) == "security":
        raise _json_error(web.HTTPForbidden, "read_only")


# --- Формовка данных --------------------------------------------------------
def _form_field_title(key: str, lang: str) -> str:
    d = FORM_FIELD_TITLES.get(key) or {}
    return d.get(lang) or d.get("ru") or _title(key, lang)


def _title(key: str, lang: str) -> str:
    if key in FIELD_TITLES:
        return tf(key, lang)
    return BY_KEY[key].title if key in BY_KEY else key


@functools.lru_cache(maxsize=1)
def _shell_version() -> int:
    """Номер версии оболочки из sw.js. Дублировать его в коде нельзя: разъедется."""
    try:
        text = (Path(__file__).parent / "miniapp" / "sw.js").read_text(encoding="utf-8")
        m = re.search(r"hard-shell-v(\d+)", text)
        return int(m.group(1)) if m else 0
    except OSError:
        return 0


def _meta(lang: str) -> dict:
    return {
        "dropdowns": {k: [{"value": v, "label": tv(v, lang)} for v in DROPDOWNS[k]]
                      for k in FORM_DROPDOWNS},
        "dropdownTitles": {k: _title(k, lang) for k in FORM_DROPDOWNS},
        "formProtocolVersion": FORM_PROTOCOL_VERSION,
        # Версия оболочки — из sw.js, единственного места, где она объявлена.
        # Приложение сравнивает её со своей и, если сервер ушёл вперёд,
        # предлагает обновиться САМО. 04.08.2026 обновление приходилось объяснять
        # людям словами «закройте из списка задач», и до половины оно не дошло.
        "shellVersion": _shell_version(),
        "visitGeoRequired": config.VISIT_GEO_REQUIRED,
        # Типизированные поля формы: клиент рисует по ним секцию обещания;
        # на контракте v2 список пуст и секции нет. Дату «сегодня» считает
        # СЕРВЕР: контейнер живёт по UTC, телефон — по +3/+5, и клиентская
        # дата в утренние часы врала бы барабану и киллер-фильтру.
        "typedFields": [{"key": k,
                         "kind": "date" if k in FORM_DATE_KEYS else "number",
                         "title": _form_field_title(k, lang)}
                        for k in (*FORM_DATES, *FORM_AMOUNTS)],
        "today": workday.today().isoformat(),
        # Предел обещания оплаты (дней от дня выезда). Колесо дат в приложении
        # дальше этой даты не пускает, сервер проверяет то же самое ещё раз:
        # оболочка на телефоне бывает старая, а очередь доносит визиты сутками.
        "promiseMaxDays": db.promise_ahead_days(),
        "dataExchangeContract": DATA_EXCHANGE_CONTRACT,
        "vectorTiles": "/tiles/basemap.pmtiles" if TILES_FILE.exists() else None,
        # Микрофон показываем только при полностью заданном UDS-контракте.
        "voice": _voice_configured(),
    }


# Телефон в реестре живёт как придётся: «+998 90 123-45-67» — один номер сквозь
# пробелы и дефисы, а «987064552 - раис джамаата 918436099 - хаким улицы» — два
# разных, разделённых словами. Поэтому режем по БУКВАМ и явным разделителям, а
# внутри куска цифры склеиваем: иначе один номер рассыпался бы на «998», «90»,
# «123», а два чужих слиплись бы в один несуществующий.
_TEL_SPLIT_RE = re.compile(r"[^\W\d_]+|[,;/|]+", re.UNICODE)
_DIGITS_RE = re.compile(r"\d+")


def _search_ids(rows) -> list[str]:
    """Номера договоров человека — для поиска по списку."""
    ids = {str(_cell(r, "contract_id") or "").strip() for r in rows}
    return sorted(i for i in ids if i)


def _search_tels(rows) -> list[str]:
    """Телефоны человека, одними цифрами — искать будут набором подряд."""
    tels: set[str] = set()
    for r in rows:
        for key in ("phone", "phone_main"):
            for chunk in _TEL_SPLIT_RE.split(str(_cell(r, key) or "")):
                digits = "".join(_DIGITS_RE.findall(chunk))
                # «2», «-», «0» в графе телефона — пометки оператора, а не номер.
                if len(digits) >= 5:
                    tels.add(digits)
    return sorted(tels)


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
            "visited": visited, "lat": lat, "lon": lon,
            # Поле поиска обещает «ФИО, договор, телефон», а список знал только
            # имя: номера и телефоны лежали в полных карточках, куда фильтру
            # не дотянуться. Кладём их рядом со списком — те же данные того же
            # портфеля, которые уже уехали на телефон в /api/cards.
            "ids": _search_ids(p["contracts"]),
            "tels": _search_tels(p["contracts"])}


def _row_get(rows, key):
    return next((r[key] for r in rows if r[key] not in (None, "")), None)


def _cell(row, key):
    """Поле ОДНОЙ строки клиента. dict и sqlite3.Row ведут себя по-разному, а
    отсутствующий ключ (урезанный словарь в тестах, старый снимок) — не ошибка."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _fmt_date_ru(iso: str) -> str:
    """«2026-09-15» → «15.09.2026»; всё остальное — как есть."""
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso)


def _fmt_money_ru(value) -> str:
    """Сумма → «15 000»; мусор и пустота → пустая строка."""
    canon = db.canon_amount(value)
    if canon is None:
        return str(value or "").strip()
    return f"{int(canon):,}".replace(",", " ")


def _person_full_from_rows(rows, rep: str, lang: str,
                           extras: dict[str, list[dict]] | None = None,
                           receipts: list[dict] | None = None) -> dict | None:
    """Полная карточка из уже выбранных строк одного человека.

    `/api/cards` получает все группы одним запросом. Повторный
    `db.person_contracts(rep)` на каждую карточку создавал N+1 SQLite-запросов:
    у Фирдавса 270 карточек строились сотнями отдельных соединений. Точечный
    `/api/person` по-прежнему выбирает только своего человека и делегирует сюда.

    ``extras`` — уже прочитанные дополнительные поля реестра по всему портфелю
    (та же причина: не открывать соединение на каждую карточку). None означает
    «прочитай сам для этих строк».
    """
    rows = list(rows or [])
    if not rows:
        return None
    main = rows[0]
    visit = next((v for r in rows if (v := db.bot_visit_snapshot(r)) is not None), None)
    newest_row = max(rows, key=lambda r: max(str(r["bot_call_at"] or ""),
                     str(r["bot_visit_event_at"] or r["bot_visit_at"] or "")))
    latest_common = db.common_result(newest_row)
    last_visit = db.last_visit_row([r["contract_id"] for r in rows])
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
    # Обещание выездника — из доказанного снимка: дата и сумма. Раньше сумма в
    # сводке не показывалась вовсе (её не было в PROMISE_KEYS), а дата при
    # наличии снимка убиралась «чтобы не дублировать форму» — и выездник,
    # набрав сумму, нигде её потом не видел. Пока снимка нет — офисные
    # значения read-only.
    snap_date = str(latest_common.get("promise_date") or "").strip()
    snap_sum = str(latest_common.get("forecast_sum") or "").strip()
    for x in promise:
        if x["key"] == "promise_date" and snap_date:
            x["value"] = _fmt_date_ru(snap_date)
    sum_value = _fmt_money_ru(snap_sum) if snap_sum else (
        _fmt_money_ru(_row_get(rows, "forecast_sum")) or "—")
    idx = next((i for i, x in enumerate(promise) if x["key"] == "promise_date"), 0)
    promise.insert(idx + 1, {"key": "forecast_sum",
                             "title": _form_field_title("forecast_sum", lang),
                             "value": sum_value})
    # Типизированные поля: current — ТОЛЬКО из доказанного снимка; значение
    # реестра едет отдельным ключом как read-only контекст («офис: 20.09»),
    # чтобы пустое поле не читалось как «ничего не зафиксировано».
    form_typed = {k: ((visit or {}).get(k, "") if visit is not None else "")
                  for k in (*FORM_DATES, *FORM_AMOUNTS)}
    form_typed_registry = {k: (_row_get(rows, k) or "")
                           for k in (*FORM_DATES, *FORM_AMOUNTS)
                           if k in BY_KEY}
    form = []
    for k in FORM_DROPDOWNS_VISIT:
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
        "phones": list(dict.fromkeys(str(_cell(r, k)).strip() for k in ("phone_main", "phone")
                                     for r in rows if _cell(r, k) not in (None, ""))),
        "extra_contact": "\n".join(dict.fromkeys(str(_cell(r, "extra_address")).strip()
                                                for r in rows if _cell(r, "extra_address") not in (None, ""))),
        "geo": {"lat": main["geo_lat"], "lon": main["geo_lon"], "src": main["geo_src"]},
        "total": sum(debt.contract_debt(r) for r in rows),
        "unit": debt.unit(sorted({r["partner"] for r in rows if r["partner"]})),
        "totals": debt.totals_by_unit(rows),
        "photos": sum(r["photos_count"] or 0 for r in rows),
        "contracts": contracts, "info": info, "promise": promise,
        "form": form,
        "formTyped": form_typed,
        "softFormTyped": {k: latest_common.get(k, "") for k in (*FORM_DATES, *FORM_AMOUNTS)},
        "last_visit_op": last_visit["op_id"] if last_visit else None,
        "formTypedRegistry": form_typed_registry,
        # Текущий комментарий — живой совместный текст месяца: его можно
        # продолжить до завершения визита, а доказанный snapshot остаётся
        # отдельной неизменяемой историей.
        "visit_comment": _row_get(rows, "visit_comment") or "",
        # Служебный односторонний контекст из общего реестра; в форму и POST
        # сотрудника этот ключ не входит.
        "historical_visit_comment": (
            _row_get(rows, "historical_visit_comment") or ""
        ),
        # call_comment оставлен для чтения доказанных legacy snapshot старой
        # оболочкой; сырой master-комментарий форму не префиллит.
        "call_comment": (visit or {}).get("call_comment") or "",
        "tips": (visit or {}).get("tips") or "",
        # Дополнительные колонки реестра (карта колонок пульта). Состав и
        # подписи задаёт оператор, поэтому это плоский список, а не схема.
        "extra": _person_extra(rows, extras),
        # Чеки об оплате, приложенные к карточке (контур СБ): дата платежа,
        # сумма, платил ли клиент. Читаются и выездником (видит, что уже
        # приложено), и руководителем/безопасником в витрине.
        "receipts": ([receipt for items in db.receipts_for([r["contract_id"] for r in rows]).values()
                      for receipt in items]
                     if receipts is None else receipts),
        # Контур «Софт»: состояние звонка по человеку (живая колонка месяца),
        # форма «Звонок» и журнал звонков. Читается и софтником, и выездником
        # (кто звонил и что обещали — полезно и на адресе).
        "soft": {
            "status": _row_get(rows, "soft_call_status") or "",
            "comm_date": _row_get(rows, "soft_comm_date") or "",
            "comment": _row_get(rows, "soft_comment") or "",
            "history": _row_get(rows, "soft_history") or "",
            "responsible": _row_get(rows, "responsible_soft") or "",
        },
        "softForm": [
            {"key": "soft_call_status", "title": _title("soft_call_status", lang),
             "current": _row_get(rows, "soft_call_status") or ""},
        ],
        "calls": [
            {"ts": c["created_at"], "received": c["received_at"],
             "at": (control._local_dt(c["saved_at"] or c["received_at"]).isoformat(timespec="seconds")
                    if control._local_dt(c["saved_at"] or c["received_at"]) else c["created_at"]),
             "employee": c["employee"] or "", "payload": c["payload"] or ""}
            for c in db.calls_for_person(rep)
        ],
    }


def _person_extra(rows, extras: dict[str, list[dict]] | None) -> list[dict]:
    """Дополнительные поля человека: по подписи, первое непустое значение."""
    ids = [r["contract_id"] for r in rows]
    source = (db.registry_extra_values(ids) if extras is None
              else {cid: extras.get(cid, []) for cid in ids})
    shown: dict[str, str] = {}
    for cid in ids:
        for item in source.get(cid) or []:
            if item.get("show_in_card") is False:
                continue
            shown.setdefault(item["label"], item["value"])
    return [{"title": label, "value": value} for label, value in shown.items()]


def _person_full(rep: str, lang: str, *, by_soft: bool = False) -> dict | None:
    data = _person_full_from_rows((db.person_contracts_by_soft(rep) if by_soft
                                  else db.person_contracts(rep)), rep, lang)
    if data:
        data["_profile"] = "soft" if by_soft else "hard"
    return data


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
    # Telegram WebView и установленная PWA могут жить неделями. HTTP-кэш не
    # должен подменять сетевой ответ старым index.html: оффлайн-копией управляет
    # только service worker, который знает версию оболочки.
    return web.FileResponse(MINIAPP_DIR / "index.html", headers={
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Hardbot-Shell-Version": str(_shell_version()),
    })


async def _version(request: web.Request) -> web.Response:
    """Публичная версия без ПД и авторизации для пробуждённой старой PWA."""
    payload = {
        "shellVersion": _shell_version(),
        "visitGeoRequired": config.VISIT_GEO_REQUIRED,
        "dataExchangeContract": DATA_EXCHANGE_CONTRACT,
    }
    if _is_drain(request):
        payload["drain"] = _drain_payload()
    return web.json_response(payload, headers={
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
    })


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
    Компромисс: t может осесть в access-логах внешнего прокси (свой журнал
    приложения query-строку не пишет, в БД лежит только хэш токена), no-store —
    чтобы прокси/SW не закэшировали чужой манифест."""
    raw = json.loads((MINIAPP_DIR / "manifest.webmanifest").read_text("utf-8"))
    tok = (request.query.get("t") or "").strip()
    if tok and db.uid_for_app_token(tok):
        raw["start_url"] = f"/#t={tok}"
    return web.Response(text=json.dumps(raw, ensure_ascii=False),
                        content_type="application/manifest+json",
                        headers={"Cache-Control": "no-store"})


async def _apple_touch_icon(request: web.Request) -> web.StreamResponse:
    """Иконка для ярлыка iOS и вкладки браузера — та же, что в манифесте."""
    return web.FileResponse(MINIAPP_DIR / "vendor" / "icon-192.png",
                            headers={"Cache-Control": "public, max-age=86400"})


async def _tiles(request: web.Request) -> web.StreamResponse:
    if not TILES_FILE.exists():
        raise web.HTTPNotFound()
    # FileResponse сам поддерживает Range — PMTiles тянет тайлы байт-диапазонами.
    # CORS сужен: «*» убран; заголовок ставим только при явно заданном CORS_ALLOW_ORIGIN.
    headers = {"Access-Control-Allow-Origin": CORS_ALLOW_ORIGIN} if CORS_ALLOW_ORIGIN else {}
    return web.FileResponse(TILES_FILE, headers=headers)


async def _raster_tile(request: web.Request) -> web.Response:
    """Same-origin fallback for devices that cannot reach OSM directly.

    The request contains only public slippy-map coordinates, never an address,
    employee identity or live GPS. Vector PMTiles remains the primary layer;
    this bounded proxy is used only if that layer cannot be opened.
    """
    try:
        zoom = int(request.match_info["z"])
        tile_x = int(request.match_info["x"])
        tile_y = int(request.match_info["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="tile") from exc
    tile_limit = 1 << zoom if 0 <= zoom <= 19 else 0
    if not tile_limit or not (0 <= tile_x < tile_limit and 0 <= tile_y < tile_limit):
        raise web.HTTPBadRequest(text="tile")

    upstream = f"https://tile.openstreetmap.org/{zoom}/{tile_x}/{tile_y}.png"
    try:
        async with ClientSession(timeout=ClientTimeout(total=12)) as session:
            async with session.get(
                upstream,
                headers={"User-Agent": "Hardbot/2 map fallback"},
            ) as response:
                if response.status != 200:
                    raise web.HTTPBadGateway(text="tile upstream")
                body = await response.read()
                content_type = response.headers.get("Content-Type", "")
    except (ClientError, asyncio.TimeoutError) as exc:
        raise web.HTTPBadGateway(text="tile upstream") from exc
    if not body or len(body) > 2 * 1024 * 1024 or not content_type.startswith("image/"):
        raise web.HTTPBadGateway(text="tile payload")
    return web.Response(
        body=body,
        content_type=content_type.split(";", 1)[0],
        headers={"Cache-Control": "public, max-age=86400, stale-if-error=604800"},
    )


_priority_refresh_task: asyncio.Task | None = None


def _priority_index_nonblocking() -> dict:
    """Индекс приоритета, который никогда не заставляет ждать пересчёта.

    Свежий есть — отдаём его. Нет — отдаём предыдущий и заводим пересчёт в
    отдельном потоке: две секунды счёта в обработчике останавливали весь
    сервер, потому что цикл событий один на всех. Параллельных пересчётов не
    заводим: следующий запрос всё равно получит результат первого.
    """
    global _priority_refresh_task
    import scoring

    idx = scoring.priority_index(allow_stale=True)
    if not scoring.priority_index_is_fresh():
        task = _priority_refresh_task
        if task is None or task.done():
            _priority_refresh_task = asyncio.create_task(
                asyncio.to_thread(scoring.priority_index))
    return idx


async def _bootstrap(request: web.Request) -> web.Response:
    started = time.perf_counter()
    uid = _uid(request)
    if not uid:
        raise web.HTTPUnauthorized(text="auth")
    _remember_from_init(request)     # вход в приложение — тоже знакомство
    # Версия оболочки устройства (шлют v33+): «у него старая оболочка» —
    # проверяемый факт, а не догадка по отсутствию заголовка. Тихо: удобство.
    try:
        sv = int(request.headers.get("X-Shell-Version") or 0)
        if 0 < sv < 100_000:
            with db.connect() as conn:
                conn.execute("UPDATE users SET shell_version=? WHERE telegram_id=?",
                             (sv, uid))
                conn.commit()
    except Exception:                                  # noqa: BLE001
        pass
    lang = lang_of(uid)
    emp = _employee(uid, request)
    if not emp:
        pend = db.pending_binding(uid)
        if pend:   # привязка есть, но ждёт аппрува ADMIN — данные не отдаём
            return web.json_response({"ok": True, "pending": True, "uid": uid, "lang": lang,
                                      "employee": pend["employee"]})
        return web.json_response({   # ещё не выбрал себя → онбординг прямо в миниаппе
            "ok": True, "needsOnboarding": True, "uid": uid, "lang": lang,
            "employees": [
                {"employee": e["employee"], "contracts": e["contracts"]}
                for e in db.employees_in_registry()
            ] + [
                # Контур «Софт»: человек может быть только софтником — его имени
                # нет среди выездников, но выбрать себя он обязан тем же списком.
                {"employee": e["employee"], "contracts": e["contracts"],
                 "soft": True}
                for e in db.soft_employees()
            ],
            "langs": [{"code": c, "name": n} for c, n in LANGUAGES.items()]})
    # Контур «Софт»: у человека есть портфель и по второй колонке
    # ответственности. Флаг едет в roles (вместе с режимом ?mode=soft это
    # переключатель «Выездник | Софт» в оболочке).
    soft_exists = db.soft_portfolio_exists(emp)
    soft_mode = soft_exists and request.query.get("mode") == "soft"
    if soft_mode:
        grouped = db.persons_for_soft(emp)
    else:
        grouped = db.persons_for_employee(emp)
    persons = [_person_brief(p) for p in grouped]
    if soft_mode:
        # Очередь звонков: просроченная дата коммуникации наверх, затем
        # договоры без статуса, дальше по долгу. Приоритет выезда здесь не
        # применим — он про адреса, а софтник работает телефоном.
        for pb, p in zip(persons, grouped):
            status = _row_get(p["contracts"], "soft_call_status")
            comm = _row_get(p["contracts"], "soft_comm_date")
            pb["sc"] = status or ""
            pb["scd"] = comm or ""
            pb["overdue"] = 0
            if comm:
                try:
                    pb["overdue"] = max(
                        0, (workday.today() - date.fromisoformat(str(comm)[:10])).days)
                except ValueError:
                    pass
        persons.sort(key=lambda x: (
            0 if x.get("scd") and x.get("overdue", 0) > 0
            else 1 if not x.get("sc")
            else 2,
            -x.get("overdue", 0), -x.get("total") if isinstance(x.get("total"), (int, float)) else 0))
    else:
        try:
            idx = _priority_index_nonblocking()
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
    # Переключатель портфелей рисуется по этому флагу. Условие «и нет привязки»
    # запирало руководителя в чужом портфеле: стоило оператору вписать его
    # telegram_id как выездника (или самому руководителю пройти онбординг), и
    # переключатель исчезал совсем — при том, что портфель ему открывает РОЛЬ.
    # Право определяется ролью, значит и переключатель зависит только от неё.
    roles = {"manager": bool(scope), "employees": scope, "acting": emp} if scope else None
    if soft_exists:
        # Портфель софта есть — человек может работать в режиме «Софт», даже
        # если он одновременно выездник (перевод УЗБ на звонки).
        roles = roles or {}
        roles["soft"] = True
        roles["field"] = bool(db.persons_for_employee(emp))
    if roles and roles.get("manager"):
        # Подпись режима в приложении (v62): «Руководитель», «Безопасник»,
        # «Директор» или «Владелец» — какой ролью человек вошёл.
        mrec = db.get_manager(uid)
        roles["role"] = ((mrec or {}).get("role")
                         or ("owner" if uid == ADMIN_ID else "manager"))
    diagnostics = {
        "server_ms": round((time.perf_counter() - started) * 1000, 1),
        "persons": len(persons),
        "contracts": sum(int(p.get("n") or 0) for p in persons),
    }
    response = web.json_response({
        "ok": True, "uid": uid, "employee": emp,
        "kind": db.get_kind(uid), "lang": lang,
        "persons": persons, "meta": _meta(lang),
        "roles": roles, "diagnostics": diagnostics, "today": workday.today().isoformat(),
    })
    response.headers["Server-Timing"] = f"app;dur={diagnostics['server_ms']}"
    response.enable_compression()
    return response


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
    emp = _employee(uid, request)
    if not emp:
        raise web.HTTPNotFound(text="bind")
    # Битая точка старта — как её отсутствие: маршрут строится от первого адреса.
    start = _latlon(request.query.get("lat"), request.query.get("lon"))
    grouped = db.persons_for_employee(emp)
    persons = [_person_brief(p) for p in grouped]
    try:
        idx = _priority_index_nonblocking()
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
    # Именно здесь имя нужнее всего: человек выбирает себя из списка, и если он
    # промахнётся, владелец должен видеть в пульте «кто», а не голый номер.
    _remember_from_init(request)
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


def _latlon(lat, lon) -> tuple[float, float] | None:
    """Пара координат из запроса — или None, если это не точка на Земле.

    float() принимает «inf», «nan» и 1e999, а json.loads — литералы Infinity и
    NaN. Такая «точка» роняла гео-контроль посреди сохранения визита (500 уже
    после записи полей, и очередь телефона повторяла его вечно), а в ответах
    превращалась в Infinity — невалидный JSON, на котором JSON.parse роняет
    экран руководителя целиком."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


async def _marker(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    _require_field_write(uid)
    body = await request.json()
    # Метка — это и есть точка, без неё записывать нечего: отказ, а не NULL.
    # Оболочка на 4xx не повторяет метку, а оставляет её в очереди видимой
    # («не сохранилось») — зацикливания нет. С rep метка переписывает точку
    # должника во всех его договорах, поэтому мусор сюда не пускаем вовсе.
    point = _latlon(body.get("lat"), body.get("lon")) if isinstance(body, dict) else None
    if point is None:
        raise web.HTTPBadRequest(
            text="координаты метки не приняты: широта от -90 до 90, долгота от -180 до 180")
    lat, lon = point
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
    point = _latlon(request.query.get("lat"), request.query.get("lon"))
    if point is None:
        raise web.HTTPBadRequest(text="lat/lon")
    lat, lon = point
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
# Разделитель — «запятая в пробелах» ИЛИ «одни пробелы», и никак иначе. Прежний
# «\s*[, ]\s*» делил серию пробелов между тремя кусками всеми способами сразу:
# квадратичный перебор, 0,7 с цикла событий на один запрос в 8 КБ (ревью
# 25.09.2026, F-2). Этот вариант линейный; табуляция стала разделителем — вреда нет.
_LATLON_RX = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)(?:\s*,\s*|\s+)(-?\d{1,3}(?:\.\d+)?)\s*$")

# Поисковая строка навигатора длиннее адреса не бывает. Режем ДО регулярок и
# разбора: их стоимость растёт с длиной, а URL вмещает до 8 КБ.
GEO_QUERY_MAX = 200


def _geo_query(request: web.Request) -> str:
    return (request.query.get("q") or "").strip()[:GEO_QUERY_MAX]


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
    q = _geo_query(request)
    if len(q) < 2:
        return web.json_response({"items": []})
    # geoindex.load() читает дампы с диска 5–7 секунд и КЭША НА ДИСКЕ НЕ ИМЕЕТ,
    # то есть платится на каждый рестарт. В корутине это останавливало весь
    # процесс: не проходили /api/save, не грузились фото, молчал поллинг
    # Телеграма. Достаточно было одному выезднику набрать две буквы в поиске
    # первым после перезапуска. Уносим в поток.
    try:
        import geoindex
        idx = await asyncio.get_running_loop().run_in_executor(None, geoindex.load)
    except Exception:                                  # noqa: BLE001
        return web.json_response({"items": []})
    # Сам поиск — тоже в потоке. Запрос без совпадений проходит весь справочник
    # (45 тыс. ключей, 131 тыс. записей): 40–130 мс на каждую паузу в наборе
    # (оболочка ждёт 250 мс), и всё это время стоял цикл событий — сохранения,
    # фото, пульт (ревью 25.09.2026, F-2). Справочник после загрузки только читается.
    items = await asyncio.to_thread(_suggest_items, idx, q)
    return web.json_response({"items": items})


def _suggest_items(idx, q: str) -> list[dict]:
    """Подсказки справочника по началу слова — синхронно, для потока."""
    needle = addr_cis.fuzzy(q)
    if not needle:
        return []

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
    return items


async def _geocode_free(request: web.Request) -> web.Response:
    """Произвольный адрес (или пара координат) → точка. Для навигации по адресу,
    которого нет в реестре: руководителю — проверить адрес должника, владельцу —
    съездить куда угодно на тесте.

    ТОЛЬКО офлайн-справочник. Сервер-витрина принципиально не ходит в публичные
    геокодеры: туда уехали бы адреса должников (см. модуль geocode — точный
    геокодинг живёт в пульте на машине владельца, а не здесь)."""
    _auth(request)
    # Обрезка до регулярки и разбора: после неё весь путь ниже — доли миллисекунды
    # (замер 25.09.2026: разбор 8 КБ шёл 20 мс, 200 знаков — 0,5 мс).
    q = _geo_query(request)
    if not q:
        raise web.HTTPBadRequest(text="q")

    m = _LATLON_RX.match(q)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return web.json_response({"ok": True, "lat": lat, "lon": lon,
                                      "pretty": f"{lat:.5f}, {lon:.5f}",
                                      "precision": "point", "name": ""})

    # Та же защита, что и в geocode_address: справочник знает только Таджикистан,
    # Узбекистан и Самарскую область. Здесь её не было — поиск в приложении шёл
    # в indx.resolve напрямую и отдавал «Москву» как достоверную точку кишлака
    # Москва в Хатлоне.
    import geocode as _gc
    if _gc._outside_coverage(q):
        raise web.HTTPNotFound(text="geo")

    parsed = addr_cis.parse(q)
    try:                                               # см. _geo_suggest: не в корутине
        idx = await asyncio.get_running_loop().run_in_executor(None, geoindex.load)
    except Exception:                                  # noqa: BLE001
        logging.exception("gazetteer load failed")
        raise web.HTTPServiceUnavailable(text="gazetteer")
    hit = idx.resolve(parsed)
    if not hit:
        raise web.HTTPNotFound(text="geo")
    return web.json_response({"ok": True, "lat": hit["lat"], "lon": hit["lon"],
                              "pretty": addr_cis.pretty(parsed, idx.display_names(parsed)) or q,
                              "precision": hit["precision"], "name": hit.get("name") or ""})


# Пачка за раз. Оболочка (с первой версии трека) шлёт не больше 10 точек
# (TRACK_FLUSH_N), а отставшие пачки из localStorage досылает по одной, так что
# 500 — запас в пятьдесят раз, а не норма. Больше присылает только чужой
# клиент: лишнее отбрасываем с записью в журнал, а не 400 — на любую ошибку
# trackDrainQueue встаёт на этой пачке и не дошлёт ни её, ни следующие.
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
    if len(points) > TRACK_MAX_POINTS:
        logging.warning("track: пачка из %d точек, приняты первые %d (uid=%s)",
                        len(points), TRACK_MAX_POINTS, uid)
    added = db.add_track_points(session_id, emp, uid, rep, points[:TRACK_MAX_POINTS])
    return web.json_response({"ok": True, "added": added})


async def _route_to(request: web.Request) -> web.Response:
    """Маршрут до произвольной точки (без привязки к договору).
    Проверка «чей должник» здесь не нужна: цель задал сам пользователь,
    из БД ничего не читается."""
    _auth(request)
    here = _latlon(request.query.get("lat"), request.query.get("lon"))
    there = _latlon(request.query.get("dlat"), request.query.get("dlon"))
    if here is None or there is None:
        raise web.HTTPBadRequest(text="lat/lon")
    return await _route_payload(*here, *there)


async def _person(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    rep = request.query.get("rep", "")
    row = db.get_client(rep)
    # Контур «Софт»: карточку открывает хозяин ЛЮБОЙ из колонок
    # ответственности — софтник работает те же договоры, что и выездник.
    if not row or (uid != ADMIN_ID and emp != row["responsible"]
                   and emp != row["responsible_soft"]):
        raise web.HTTPNotFound(text="person")
    db.audit_access(uid, "view_card", rep)              # ИБ: доступ к ПД в журнал
    by_soft = request.query.get("mode") == "soft" or (emp != row["responsible"] and emp == row["responsible_soft"])
    if by_soft and uid != ADMIN_ID and emp != row["responsible_soft"]:
        raise web.HTTPNotFound(text="person")
    data = _person_full(rep, lang_of(uid), by_soft=by_soft)
    if not data:
        raise web.HTTPNotFound(text="person")
    return web.json_response(data)


async def _cards(request: web.Request) -> web.Response:
    """Все полные карточки портфеля сотрудника разом — прогрев оффлайн-кэша миниаппа.
    Клиент зовёт это ОНЛАЙН сразу после /api/bootstrap и складывает карточки в IndexedDB,
    чтобы в поле без сети открывать любого своего клиента и заполнять визит. Один запрос
    вместо N (по карточке на человека). ПД в журнал доступа тут не пишем — это оптовый
    предзагруз своего же портфеля, а не точечный просмотр (view_card логируется в _person)."""
    started = time.perf_counter()
    uid, emp = _auth(request)
    lang = lang_of(uid)
    cards = {}
    # Прогрев — по ОБЕИМ колонкам ответственности: софтнику кэш нужен не меньше
    # (очередь «кому звонить» открывает карточки офлайн той же механикой).
    soft_mode = request.query.get("mode") == "soft" or (
        db.soft_portfolio_exists(emp) and not db.persons_for_employee(emp))
    grouped = db.persons_for_soft(emp) if soft_mode else db.persons_for_employee(emp)
    # Дополнительные поля всего портфеля — одним запросом по той же причине,
    # что и сами договоры: карточек бывает под три сотни.
    all_extras = db.registry_extra_values(
        [r["contract_id"] for p in grouped for r in p["contracts"]])
    # Чеки всего портфеля — тем же пакетом (не открывать запрос на карточку).
    all_receipts = db.receipts_for([r["contract_id"] for p in grouped for r in p["contracts"]])
    for p in grouped:
        # persons_for_employee уже принёс все строки договоров. Не открываем
        # отдельное SQLite-соединение для каждой карточки.
        full = _person_full_from_rows(p["contracts"], p["rep"], lang, all_extras,
                                      [receipt for r in p["contracts"]
                                       for receipt in all_receipts.get(r["contract_id"], [])])
        if full:
            full["_profile"] = "soft" if soft_mode else "hard"
            cards[p["rep"]] = full
    # ИБ: точечный просмотр карточки логируется в _person (view_card). Здесь — оптовый
    # предзагруз своего портфеля в оффлайн-кэш: пишем один факт (кто/когда/сколько), без
    # строки на каждого. NB: оффлайн-открытия карточек в поле сервер уже не аудирует.
    cards_json_bytes = len(json.dumps(
        cards, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8"))
    diagnostics = {
        "server_ms": round((time.perf_counter() - started) * 1000, 1),
        "cards": len(cards),
        "contracts": sum(len(p["contracts"]) for p in grouped),
        "cards_bytes": cards_json_bytes,
    }
    db.audit_access(
        uid, "cards_prefetch", target=emp,
        detail=json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":")),
    )
    response = web.json_response({
        "ok": True, "cards": cards,
        "at": datetime.now().isoformat(timespec="seconds"),
        "diagnostics": diagnostics,
    })
    response.headers["Server-Timing"] = f"app;dur={diagnostics['server_ms']}"
    response.enable_compression()
    return response


_CLIENT_EVENTS = {
    "boot_ok", "boot_error", "warm_ok", "warm_error", "render_error",
    "shell_update",
}
_CLIENT_METRICS = {
    "persons", "cards", "contracts", "server_ms", "download_ms", "cache_ms",
    "render_ms", "cards_bytes", "shell",
}


async def _client_event(request: web.Request) -> web.Response:
    """Ограниченные метрики загрузки без имён, КД, адресов и текста ошибок."""
    uid, emp = _auth(request)
    if request.content_length is not None and request.content_length > 4096:
        raise web.HTTPRequestEntityTooLarge(max_size=4096, actual_size=request.content_length)
    try:
        raw_body = await request.read()
        if len(raw_body) > 4096:
            raise web.HTTPRequestEntityTooLarge(max_size=4096, actual_size=len(raw_body))
        body = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise web.HTTPBadRequest(text="json")
    event = body.get("event") if isinstance(body, dict) else None
    if event not in _CLIENT_EVENTS:
        raise web.HTTPBadRequest(text="event")
    raw_metrics = body.get("metrics") or {}
    if not isinstance(raw_metrics, dict):
        raise web.HTTPBadRequest(text="metrics")
    metrics = {}
    for key in _CLIENT_METRICS:
        value = raw_metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        metrics[key] = round(max(0.0, min(float(value), 1_000_000_000.0)), 1)
    detail = json.dumps(
        {"event": event, "metrics": metrics},
        ensure_ascii=False, separators=(",", ":"),
    )
    db.audit_access(uid, f"client_{event}", target=emp, detail=detail)
    return web.json_response({"ok": True})


async def _save(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    _require_field_write(uid)
    body = await request.json()
    rep = body.get("rep", "")
    row = db.get_client(rep)
    # Контур «Софт»: kind=call — звонок софтника. Владение строгое: звонок
    # закрывает хозяин второй колонки ответственности, визит — первой.
    kind = "call" if str(body.get("kind") or "").strip() == "call" else "visit"
    if not row or (uid != ADMIN_ID and emp != (
            row["responsible_soft"] if kind == "call" else row["responsible"])):
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
    # Поля формы делятся по kind ЖЁСТКО: звонок не пишет визитные поля (даже
    # легаси-ключом), визит — софт-колонки. Иначе чужая форма протаскивала бы
    # результат визита через kind:"call" и наоборот.
    if kind == "call":
        # soft_comment — маркер «сх» (колонку ведёт месячный цикл), но в форме
        # звонка поле живёт: его пишет софтник, серверу оно известно текстом.
        soft_allowed = SOFT_FORM_KEYS | {"soft_comment"} | set(FORM_AMOUNTS) | set(FORM_DATES)
        raw_values = {k: v for k, v in raw_values.items() if k in soft_allowed}
    else:
        raw_values = {k: v for k, v in raw_values.items() if k not in SOFT_FORM_KEYS}
    typed_keys = set(FORM_AMOUNTS) | set(FORM_DATES)
    if not finish:
        # pushDraft шлёт finish:false каждые 20 секунд, а update_person пишет
        # независимо от finish — недописанная дата уже вычёркивала бы должника
        # из маршрута дня. Типизированные ключи принимаются только при finish.
        raw_values = {k: v for k, v in raw_values.items()
                      if k not in typed_keys}
    cleared = set(body.get("cleared_keys") or []) if finish else set()
    rejected: dict[str, str] = {}
    truncated: list[str] = []
    values = {}
    for key, raw in raw_values.items():
        if key not in ALLOWED_SAVE:
            continue
        value = str(raw or "").strip()
        if not value:
            # Явно переданная пустота типизированного ключа — ОТВЕТ «обещания
            # нет», но только по явной кнопке (cleared_keys): «пусто после
            # касания» стирало бы офисное обещание во всех КД человека.
            if key in typed_keys and key in cleared:
                values[key] = ""
            continue
        if key in FORM_TEXT and len(value) > VISIT_TEXT_MAX:
            # Режем ЗДЕСЬ, до записи: дальше текст копируется во все договоры
            # человека, в снимок визита, в журнал значений и в visits.payload.
            logging.warning("save: %s обрезан с %d до %d знаков (uid=%s)",
                            key, len(value), VISIT_TEXT_MAX, uid)
            value = value[:VISIT_TEXT_MAX].rstrip()
            truncated.append(key)
        if key in FORM_DROPDOWNS and value not in DROPDOWNS[key]:
            raise web.HTTPBadRequest(text=f"invalid {key}")
        if key in typed_keys:
            canon = db.canon_form_value(key, value)
            if canon is None:
                # НИКАКОГО 400: клиент считает любой не-2xx обрывом связи и
                # хоронит запись очереди навсегда — пропал бы весь визит,
                # а не сумма. Отбрасываем ПОЛЕ и говорим об этом в ответе.
                rejected[key] = "format"
                continue
            value = canon
        values[key] = value
    if kind == "call":
        return _save_call(uid, emp, body, rep, row, values, rejected,
                          truncated, finish, cleared)
    # Снять обещание выездник может ЛЮБОЕ, не только «своё». Он стоит перед
    # должником, и его слово свежее любой офисной даты: запрет на чужое означал
    # бы, что должника со сорвавшимся обещанием никто не вернёт в маршрут.
    # Офисная дата при этом не пропадает: она остаётся в книге офиса и
    # вернётся, если офис подтвердит её ПОСЛЕ визита (якорный шлюз импорта).
    if finish and OFFICE_RESULT_SNAPSHOT_KEY not in values:
        raise web.HTTPBadRequest(text="visit result required")
    if finish and values.get("promise_date"):
        # Обещание дальше предела офиса НЕ роняет весь визит: клиент считает
        # не-2xx обрывом и хоронит запись очереди целиком — пропала бы вся
        # карточка, а не дата. Отбрасываем ПОЛЕ и говорим об этом в ответе,
        # тем же способом, что и про нечитаемую сумму.
        limit = db.promise_ahead_days()
        if limit:
            edge = date.fromisoformat(_occurred_on(body)) + timedelta(days=limit)
            if db._as_date(values["promise_date"]) and \
                    db._as_date(values["promise_date"]) > edge:
                rejected["promise_date"] = f"too_far:{limit}"
                values.pop("promise_date", None)
    if finish:
        # «Прогноз» по итогу выезда. Колонку вёл только аналитик, и обещания
        # выездника в неё не попадали вовсе. Считаем ЗДЕСЬ, а не в пульте:
        # значение обязано лечь в доказанный снимок — иначе его нечем защитить
        # от отката импортом и незачем везти в книги.
        prev = db.bot_visit_snapshot(row) or {}
        promise_was = str(prev.get("promise_date")
                          or row["promise_date"] or "").strip()
        promise_now = (str(values.get("promise_date") or "").strip()
                       if "promise_date" in values
                       else str(row["promise_date"] or "").strip())
        fc = db.visit_forecast(
            paid=values.get(OFFICE_RESULT_SNAPSHOT_KEY) == db.PAID_STATUS,
            promise_now=promise_now, promise_was=promise_was,
            promise_dropped="promise_date" in cleared)
        if fc:
            values[db.FORECAST_KEY] = fc
    db.audit_access(uid, "save_visit" if finish else "save_card", rep)
    if not finish:
        # Автосохранение черновика несёт точку телефона: у неё есть серверное
        # время приёма, то есть это свидетельство СВЯЗИ и места разом — с
        # телефона такое не подделать (evidence.py, следы вида draft).
        _presence_from_draft(body, uid, emp, rep)
    if finish and any(k in values for k in typed_keys):
        # Кто и когда убрал должника из маршрута — единственный след; он же
        # закрывает вопрос ИБ «кто трогал поле ПД».
        prev_snap0 = db.bot_visit_snapshot(row) or {}
        db.audit_access(uid, "promise_set", rep, json.dumps(
            {"was": prev_snap0.get("promise_date") or "",
             "now": values.get("promise_date", ""),
             "sum": values.get("forecast_sum", ""),
             "fc": values.get(db.FORECAST_KEY, "")}, ensure_ascii=False))
    if values:
        db.update_person(rep, values)  # пишется во ВСЕ договоры человека, сразу в БД
    if finish:
        completed_at = _occurred_on(body)
        # visit_date НАЗАД не двигаем: у человека это дата последнего выезда, и
        # доставка позавчерашнего визита не должна отменять вчерашний.
        prev_visit = db._as_date(row["visit_date"])
        if prev_visit is None or prev_visit <= date.fromisoformat(completed_at):
            db.update_person(rep, {"visit_date": completed_at})
        cur = db.get_client(rep)
        op_id = (str(body.get("op_id") or "")).strip() or None
        # Гео-контроль: точка телефона в момент закрытия vs известная точка должника.
        # 'city' не считаем — центроид города, честная дистанция там бессмысленна.
        vlat = vlon = dist_m = None
        geo_far = None
        g = body.get("geo")
        g = g if isinstance(g, dict) else {}
        # Неконечная или внеземная точка — как её отсутствие: визит принимаем
        # всегда, в журнал ложится NULL (см. _latlon).
        vlat, vlon = _latlon(g.get("lat"), g.get("lon")) or (None, None)
        # Грубые уровни отсекаем ЯВНО: центр района или области — это не адрес,
        # и расстояние до него ничего не проверяет, зато выглядит как нарушение.
        # Раньше судили только по geo_src, а он у всей иерархии один — «geonames».
        # Пустая точность — это «неизвестно» (строка геокодирована старым кодом),
        # и тогда судим по-прежнему, только по geo_src. Отключать гео-контроль
        # для всей накопленной базы было бы хуже, чем изредка спросить лишний раз.
        # Список «грубых» уровней ОДИН на систему. Раньше здесь был свой литерал
        # из трёх значений, а scoring размазывает джиттером ещё и махаллю, кишлак
        # и джамоат — самые обычные единицы узбекского и таджикского адреса.
        # Джиттер ±0.02° сам по себе перебивает порог 1000 м, и гео-контроль
        # обвинял выездника, стоявшего ровно там, где надо.
        precision = (cur["geo_precision"] if "geo_precision" in cur.keys() else "") or ""
        # Допуск — ПО УРОВНЮ разбора адреса, а не один порог на всех. «Улица» в
        # офлайн-справочнике собрана усреднением сегментов по километровой сетке,
        # то есть это середина улицы, а не дом; мерить от неё по 1000 м значит
        # обвинять честного выездника. None = точка не адресная, судить не по чему.
        tolerance = db.geo_tolerance_m(
            precision, cur["geo_src"] if "geo_src" in cur.keys() else "")
        # Погрешность САМОЙ точки телефона. В частном секторе, в подвале и просто
        # в помещении GPS не ловится, и браузер отдаёт сетевой фикс с точностью
        # 800–1500 м — то есть больше порога тревоги. Считать по такой точке
        # расстояние значит обвинить человека, который стоял ровно там, где надо:
        # «⚠️ закрыт в 3.9 км от адреса» уходит руководителю в личку, в беседу
        # отчётов и красной точкой на карту дня. Меряем, только если точка
        # телефона точнее порога.
        # Нулевой остров у берегов Африки — это сбой датчика, а не место визита.
        # Без отсечки такая точка превращалась в «⚠️ закрыт в 8290 км от адреса»
        # в личке руководителя, в беседе отчётов и красной точкой на карте дня.
        if vlat is not None and abs(vlat) < 0.01 and abs(vlon) < 0.01:
            vlat = vlon = None
        try:
            acc = float(g.get("acc") or 0)
        except (TypeError, ValueError, OverflowError):
            acc = 0.0
        if not math.isfinite(acc):
            acc = 0.0
        # Погрешность телефона СКЛАДЫВАЕМ с погрешностью адреса, а не сравниваем с
        # фиксированным порогом. Сетевой фикс в подъезде даёт 800-1500 м: раньше
        # такой визит либо не мерился вовсе, либо становился «нарушением» из-за
        # того, что GPS не поймал спутники, — виноват был человек, а не датчик.
        if vlat is not None and cur["geo_lat"] is not None and cur["geo_lon"] is not None \
                and tolerance is not None:
            dist_m = round(_haversine_km(vlat, vlon, cur["geo_lat"], cur["geo_lon"]) * 1000)
            geo_far = dist_m > tolerance + max(acc, 0.0)
        # Подтверждение выезда: точка закрытия — лишь один из следов. Сюда же
        # ложатся точка открытия карточки, точки за время работы с ней, точки
        # с автосохранений (у них серверное время приёма) и точки снимков.
        # Вердикт хранится на визите, как geo_far, и не пересчитывается.
        session_id = (str(body.get("session_id") or "").strip()[:64]
                      or (f"srv-{op_id}" if op_id else None))
        ev_ctx = {"opened_ms": body.get("opened_at"), "saved_ms": body.get("occurred_at"),
                  "online_open": body.get("online_at_open"),
                  "foreground_ms": body.get("foreground_ms"),
                  "tolerance": tolerance, "precision": precision}
        _presence_from_save(body, uid, emp, rep, session_id, vlat, vlon, acc)
        ev = _build_evidence(session_id, rep, cur, completed_at, ev_ctx)
        # История визита строится из явного тела этой формы. Сырые HARD-поля
        # clients/master запрещено дочитывать даже для текста журнала.
        # «Контакт» и «На адресе» приехали в журнал 15.09.2026 ради воронки
        # Collection (funnel.py): снимок визита живёт ОДИН на человека и знает
        # только последний выезд, поэтому Contact Rate и RPC за прошлую неделю
        # было не из чего считать. Поля встают ПЕРЕД комментарием: комментарий —
        # свободный текст, в нём бывает «;», и всё, что за ним, разбору не
        # поддаётся. Регулярка «Статус … Договорённость …» от вставки не
        # страдает — она нежадная и читает до первой точки с запятой.
        payload = (
            f"Выезд (миниапп). Статус: "
            f"{values.get(OFFICE_RESULT_SNAPSHOT_KEY, '')}; "
            f"Договорённость: {values.get('agreed_payment', '')}; "
            f"Контакт: {values.get('contact_with', '')}; "
            f"На адресе: {values.get('who_at_address', '')}; "
            f"{values.get('visit_comment', '')}"
        )
        # Считаем ДО log_visit, иначе своя же свежая строка себя и заглушит.
        # Дедуп отбивок — по ДНЮ ВЫЕЗДА: два визита к одному должнику в разные
        # дни, доехавшие одной пачкой, — это два разных выезда.
        same_today = db.same_visit_logged_today(rep, uid, payload,
                                                on_date=completed_at)
        is_new = db.log_visit(rep, uid, payload,
                              op_id=op_id, lat=vlat, lon=vlon, dist_m=dist_m,
                              geo_far=geo_far, on_date=completed_at,
                              fill_seconds=_fill_seconds(body),
                              saved_at=_saved_at(body),
                              session_id=session_id,
                              evidence_level=ev["level"] if ev else None,
                              evidence=evidence.dumps(ev) if ev else None,
                              employee=row["responsible"])
        # Адрес уточняется по выезду, на котором должника застали (addr_learn):
        # только у нового визита — повтор из очереди адрес не переучивает.
        if is_new:
            try:
                learned = addr_learn.consider(
                    cur, values, ev, db.presence_for_session(session_id) if session_id else [],
                    employee=emp, op_id=op_id)
                if learned and learned.get("applied"):
                    db.audit_access(uid, "addr_learned", rep,
                                    f"{learned['lat']},{learned['lon']} ±{learned['acc_m']} м: "
                                    f"{learned['reason']}")
            except Exception as exc:  # noqa: BLE001 — обучение не должно ломать визит
                logging.warning("addr_learn failed: %s", exc)
        # Снимок строится только из values этого POST, а не из сырых HARD-полей
        # clients. При ретрае после редкого падения между log_visit и этой записью
        # восстанавливаем отсутствующий снимок; старый дубль новый снимок не откатывает.
        if is_new or not db.has_bot_visit(rep):
            db.record_bot_visit(rep, values, completed_at, event_at=_saved_at(body))
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
            # Кто фактически закрыл — один раз, во все три канала: у руководителя
            # через act_as гео-точка чужая, и без подписи гео-контроль врёт.
            actor = _acting_actor_name(uid) or db.get_binding(uid)
            # Рассылка ушла из запроса выездника в фон — см. _visit_notify.
            # Она и раньше занимала львиную долю ответа (альбом фото каждому
            # руководителю грузится внутри HTTP-запроса телефона), а теперь ещё
            # и ждёт опоздавшие снимки.
            _spawn_visit_notify(_visit_notify(
                request, notify_row, uid, dist_m, geo_far, actor, vlat, vlon,
                completed_at, _photos_pending(body),
                evidence_ctx={"session_id": session_id, "op_id": op_id,
                              "rep": rep, "ev": ev, **ev_ctx}))
        else:
            # Самая частая причина «отбивка не пришла» — дедуп; без этой строки
            # она оставалась невидимой.
            db.log_push(db.PUSH_VISIT, db.PUSH_SKIP_DEDUP,
                        employee=proven_row["responsible"] or "", contract_id=rep,
                        note="повтор из очереди (op_id)" if not is_new
                        else "повторное сохранение того же результата")
    data = _person_full(rep, lang_of(uid))
    out = {"ok": True, "person": data}
    if rejected:
        out["rejected"] = rejected
    if truncated:
        # Не в rejected: оболочка показала бы «часть полей не принята», а поле
        # принято — без хвоста.
        out["truncated"] = truncated
    return web.json_response(out)


def _save_call(uid: int, emp: str, body: dict, rep: str, row, values: dict,
               rejected: dict, truncated: list, finish: bool,
               cleared: set) -> web.Response:
    """Звонок софтника (kind=call): та же механика формы, БЕЗ гео, фото,
    подтверждения и переучивания адреса — звонку они не нужны по построению.

    Правила те же, что у выезда: обязательный статус, окно обещания,
    вывод «Прогноза». Отличия: статус звонка софт вместо результата визита,
    «Общий результат» звонок НЕ трогает (его ведёт офис), дата коммуникации —
    это дата следующего звонка, а не отметка о прошлом."""
    if "append_comment" in body and type(body["append_comment"]) is not bool:
        raise web.HTTPBadRequest(text="append_comment")
    if finish and "soft_call_status" not in values:
        raise web.HTTPBadRequest(text="call result required")
    if finish and values.get("promise_date"):
        limit = db.promise_ahead_days()
        if limit:
            edge = date.fromisoformat(_occurred_on(body)) + timedelta(days=limit)
            if db._as_date(values["promise_date"]) and \
                    db._as_date(values["promise_date"]) > edge:
                rejected["promise_date"] = f"too_far:{limit}"
                values.pop("promise_date", None)
    if finish:
        promise_was = str(row["promise_date"] or "").strip()
        promise_now = (str(values.get("promise_date") or "").strip()
                       if "promise_date" in values else promise_was)
        fc = db.visit_forecast(
            paid=values.get("soft_call_status") in db.PAID_SOFT_STATUSES,
            promise_now=promise_now, promise_was=promise_was,
            promise_dropped="promise_date" in cleared)
        if fc:
            values[db.FORECAST_KEY] = fc
    db.audit_access(uid, "save_call" if finish else "save_card", rep)
    if values and not finish:
        # Группа — по СОФТ-закреплению: у человека бывают разные выездные
        # ответственные, звонок обязан лечь во все его договоры софтника.
        db.update_person(rep, values, by_soft=True)
    if finish:
        completed_at = _occurred_on(body)
        op_id = (str(body.get("op_id") or "")).strip() or None
        payload = (
            f"Звонок (приложение). Статус: "
            f"{values.get('soft_call_status', '')}; "
            f"{values.get('soft_comment', '')}"
        )
        # Дедуп повторного нажатия (зеркало same_visit_logged_today, но здесь
        # гасим и строку): тот же софтник, тот же день, то же содержимое —
        # второй РЕАЛЬНЫЙ звонок с другим статусом/комментарием пройдёт.
        try:
            result = db.save_call_result(rep, uid, row["responsible_soft"] or emp,
                       values, payload, op_id=op_id, on_date=completed_at,
                       saved_at=_saved_at(body), append_comment=body.get("append_comment") is True,
                       comment_author=_acting_actor_name(uid) or row["responsible_soft"] or emp)
        except ValueError as exc:
            raise web.HTTPConflict(text=str(exc)) from exc
    data = _person_full(rep, lang_of(uid), by_soft=True)
    out = {"ok": True, "person": data}
    if finish:
        out.update(result)
    if rejected:
        out["rejected"] = rejected
    if truncated:
        out["truncated"] = truncated
    return web.json_response(out)


# --- Подтверждение выезда: следы телефона → вердикт (evidence.py) ------------
def _now_ms() -> int:
    return int(time.time() * 1000)


def _presence_from_draft(body, uid: int, emp: str, rep: str) -> None:
    """Точка с автосохранения черновика — со временем приёма сервером."""
    session_id = str(body.get("session_id") or "").strip()[:64]
    g = body.get("geo")
    if not session_id or not isinstance(g, dict):
        return
    try:
        # Разбор тоже под защитой: точность «inf» роняла его OverflowError-ом.
        samples = evidence.normalize_samples(
            [{**g, "ts": g.get("ts") or _now_ms(), "k": "draft", "on": 1, "vis": 1}])
        if samples:
            db.add_presence(session_id, emp, uid, rep, samples,
                            received_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as exc:  # noqa: BLE001 — след не должен ломать черновик
        logging.warning("presence(draft) failed: %s", exc)


def _presence_from_save(body, uid: int, emp: str, rep: str, session_id: str | None,
                        vlat, vlon, acc: float) -> None:
    """Следы, приехавшие вместе с «Сохранить выезд»: точки за время работы с
    карточкой, точка открытия и точка закрытия. Повтор из очереди дублей не
    создаёт (UNIQUE в visit_presence)."""
    if not session_id:
        return
    raw = list(body.get("presence") or []) if isinstance(body.get("presence"), list) else []
    og = body.get("open_geo")
    if isinstance(og, dict) and og.get("lat") is not None:
        raw.append({**og, "ts": og.get("ts") or body.get("opened_at"), "k": "open"})
    if vlat is not None:
        raw.append({"lat": vlat, "lon": vlon, "acc": acc,
                    "ts": body.get("occurred_at") or _now_ms(), "k": "save"})
    try:
        # Разбор следов — под той же защитой, что и запись: точность «inf» в
        # одном следе роняла его OverflowError-ом, и визит получал 500 уже
        # после записи полей, а очередь телефона повторяла его без конца.
        samples = evidence.normalize_samples(raw)
        if samples:
            db.add_presence(session_id, emp, uid, rep, samples)
    except Exception as exc:  # noqa: BLE001 — след не должен ломать визит
        logging.warning("presence(save) failed: %s", exc)


def _photo_samples(rep: str, day: str) -> list[dict]:
    """Точки съёмки снимков этого человека за день выезда."""
    out = []
    try:
        ids = [c["contract_id"] for c in db.person_contracts(rep)] or [rep]
        for r in db.photo_index_for_dirs(ids):
            if r["lat"] is None:
                continue
            when = r["captured_at"] or r["created_at"] or ""
            if not str(when).startswith(day):
                continue
            out.append({"ts": when, "lat": r["lat"], "lon": r["lon"],
                        "acc": r["acc_m"] or 0, "k": "photo"})
    except Exception as exc:  # noqa: BLE001
        logging.warning("photo samples failed: %s", exc)
    return out


def _build_evidence(session_id: str | None, rep: str, cur, day: str,
                    ctx: dict) -> dict | None:
    """Вердикт по всем следам сессии плюс снимкам дня. Считается при
    сохранении и ещё раз после догона фото — снимок у двери подтверждает
    выезд не хуже точки телефона."""
    try:
        samples = db.presence_for_session(session_id) if session_id else []
        samples += _photo_samples(rep, day)
        samples = evidence.normalize_samples(samples)
        addr = None
        if cur["geo_lat"] is not None and cur["geo_lon"] is not None:
            addr = (float(cur["geo_lat"]), float(cur["geo_lon"]))
        return evidence.build(samples, addr, ctx.get("tolerance"),
                              precision=ctx.get("precision") or "",
                              opened_ms=ctx.get("opened_ms"), saved_ms=ctx.get("saved_ms"),
                              received_ms=_now_ms(), online_open=ctx.get("online_open"),
                              foreground_ms=ctx.get("foreground_ms"))
    except Exception as exc:  # noqa: BLE001 — вердикт не должен ломать визит
        logging.warning("evidence failed: %s", exc)
        return None


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
    игнорируем: фото важнее геометки.

    Именно «игнорируем», а не 400: на любой 4xx у фото оболочка помечает снимок
    «нужно переснять» и больше его не шлёт, то есть из-за мусорной координаты
    пропал бы сам снимок. Неконечная точка — как её отсутствие (NULL)."""
    point = _latlon(q.get("lat"), q.get("lon"))
    if point is None:
        return None
    lat, lon = point
    try:
        # «Infinity» в заголовке — это строка, и JSON её не отсеет: оболочка
        # склеивает X-Photo-Geo из чисел как есть. int(inf) бросает
        # OverflowError, а он давал 500, на который очередь фото повторяет
        # запрос вечно и держит за ним всё остальное.
        acc = max(0, min(int(float(q.get("acc") or 0)), 100_000))
    except (TypeError, ValueError, OverflowError):
        acc = 0
    captured = None
    try:
        ms = int(q.get("gat") or 0)
        if ms > 0:
            captured = datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    return lat, lon, acc, captured


_PHOTO_EXIF_HDR_MAX = 1024
_PHOTO_META_STATES = {"ok", "absent", "heic", "truncated", "error", "oversize"}
_EXIF_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
_EXIF_TZ_RE = re.compile(r"^[+-]\d{2}:\d{2}$")


def _photo_meta_from(request: web.Request) -> dict:
    """Метаданные снимка из ОПЦИОНАЛЬНЫХ заголовков (X-Photo-Shot / -Source /
    -Exif / -Shrunk).

    Ни один не обязателен: установленные v12/v13/v32 их не шлют, а legacy-ветка
    отвечает 503 и держит очередь вечно. Отсутствие X-Photo-Exif целиком =
    «оболочка не умеет» (meta_state остаётся NULL) и отличается от st='absent'
    («умеет, но в файле метаданных нет»). Мусор молча игнорируем — фото важнее
    метаданных, тот же принцип, что в _photo_geo_query.

    exif_dt_norm: DateTimeOriginal — стенные часы телефона без зоны, а сервер
    живёт в UTC; сравнивать их напрямую нельзя (у выездника в UZ/TJ разрыв
    +5 ч — любой честный снимок выглядел бы снятым в будущем). Нормализуем в
    серверную шкалу ТОЛЬКО когда есть OffsetTimeOriginal; иначе None и в
    арифметику сигналов сырое время не попадает."""
    out = {"source": None, "shot_at_client": None, "shell_version": None,
           "meta_state": None, "exif": {}, "exif_dt_norm": None, "shrunk": None}
    src = (request.headers.get("X-Photo-Source") or "").strip().lower()
    if src in ("camera", "gallery"):
        out["source"] = src
    shr = (request.headers.get("X-Photo-Shrunk") or "").strip()
    if shr in ("0", "1"):
        out["shrunk"] = int(shr)
    raw_shot = (request.headers.get("X-Photo-Shot") or "").strip()[:20]
    if raw_shot.isdigit():
        try:
            ms = int(raw_shot)
            if 0 < ms < 4_102_444_800_000:
                out["shot_at_client"] = datetime.fromtimestamp(
                    ms / 1000).isoformat(timespec="seconds")
        except (ValueError, OSError, OverflowError):
            pass
    raw = request.headers.get("X-Photo-Exif") or ""
    if not raw:
        return out
    if len(raw) > _PHOTO_EXIF_HDR_MAX:
        out["meta_state"] = "oversize"
        return out
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
    except Exception:                              # noqa: BLE001
        out["meta_state"] = "error"
        return out
    st = str(data.get("st") or "")
    out["meta_state"] = st if st in _PHOTO_META_STATES else "error"
    sv = data.get("sv")
    if isinstance(sv, int) and 0 < sv < 100_000:
        out["shell_version"] = sv
    e = {}
    dt = str(data.get("dt") or "")[:32]
    if _EXIF_DT_RE.match(dt):
        e["dt"] = dt
    tz = str(data.get("tz") or "")[:6]
    if _EXIF_TZ_RE.match(tz):
        e["tz"] = tz
    if e.get("dt") and e.get("tz"):
        try:
            sign = 1 if e["tz"][0] == "+" else -1
            hh, mm = int(e["tz"][1:3]), int(e["tz"][4:6])
            local = datetime.fromisoformat(e["dt"])
            offset = timedelta(hours=hh, minutes=mm) * sign
            # UTC-момент съёмки; сервер (naive) живёт в UTC-шкале
            out["exif_dt_norm"] = (local - offset).isoformat(timespec="seconds")
        except (ValueError, OverflowError):
            pass
    try:
        lat, lon = float(data["lat"]), float(data["lon"])
        if -90 <= lat <= 90 and -180 <= lon <= 180 and \
                (abs(lat) > 0.01 or abs(lon) > 0.01):
            e["lat"], e["lon"] = round(lat, 6), round(lon, 6)
    except (KeyError, TypeError, ValueError):
        pass
    g = str(data.get("gdt") or "")[:24]
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", g):
        e["gps_at"] = g
    for src_key, dst in (("mk", "make"), ("md", "model"), ("sw", "software")):
        v = str(data.get(src_key) or "").strip()[:64]
        v = "".join(ch for ch in v if ch.isprintable())
        if v:
            e[dst] = v
    out["exif"] = e
    return out


_StoredPhoto = collections.namedtuple("_StoredPhoto", "path published sha256 size")


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
    # Оболочка v26 слала расширение сжатого снимка БЕЗ ведущей точки («jpg»), и
    # каждое пережатое фото получало 422 «bad extension» — то есть не доезжало
    # вообще, а очередь помечала его «нужно переснять» навсегда. Оболочку
    # починили, но у людей в поле она обновится не в ту же секунду, поэтому
    # точку дописываем здесь: значение всё равно сверяется с magic-байтами ниже,
    # так что послабление не пропускает ничего нового.
    if not hint.startswith("."):
        hint = "." + hint
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
    # sha256 — в этом же цикле: файл и так проходит побайтово, лишнего прохода
    # нет. На dup-retry хэш считается от ТЕЛА retry (диск не перечитывается).
    digest = hashlib.sha256()
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
                digest.update(chunk)
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
                return _StoredPhoto(old, False, digest.hexdigest(), size)
        path = dest / f"{stem}{ext}"
        try:
            os.link(tmp, path)
            published = True
        except FileExistsError:
            published = False
        return _StoredPhoto(path, published, digest.hexdigest(), size)
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
    _require_field_write(uid)
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
    meta = _photo_meta_from(request)
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
        stored = await _store_photo(chunks, dest, op, ext_hint)
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
    # Строка индекса — для КАЖДОГО снимка и на dup-retry тоже (тот же принцип,
    # что у ensure_photos_count: файл и SQLite не одна транзакция). Прежнее
    # «if geo:» оставляло галерейные и «гео запретили» снимки вне индекса.
    # Справочные данные должника — СНИМКОМ на момент загрузки (row уже в руках).
    addr = None
    if row["geo_lat"] is not None and row["geo_lon"] is not None:
        addr = (row["geo_lat"], row["geo_lon"],
                db.geo_tolerance_m(
                    (row["geo_precision"] if "geo_precision" in row.keys() else "") or "",
                    (row["geo_src"] if "geo_src" in row.keys() else "") or ""))
    assign_d = db._as_date(row["assign_date"] if "assign_date" in row.keys() else None)
    if meta.get("source") == "gallery":
        # У галерейного снимка координаты берём ТОЛЬКО из метаданных файла
        # (решение владельца 26.08.2026): точка отправки — это где человек
        # разбирал снимки вечером, и выдавать её за место съёмки нельзя.
        # Оболочка её и не шлёт, но правило серверное: заголовок мог прийти
        # из чужого клиента или из очереди будущей версии.
        geo = None
    db.record_photo(rep, f"{rep}/{stored.path.name}", uid, geo=geo,
                    sha256=stored.sha256, size_bytes=stored.size,
                    addr=addr, assign_date_at=assign_d.isoformat() if assign_d else None,
                    **meta)
    return web.json_response(
        {"ok": True, "photos": n, **({"dup": True} if not stored.published else {})})



# --- Чек об оплате (контур СБ): фото перевода + сумма + кто платил ------------
# Чеки лежат в СВОЁМ каталоге рядом с фото (photos/../receipts/<КД>/<op>.jpg):
# счётчик контрольных снимков договора их не видит, галерея «Фото по КД» тоже.
def receipt_dir() -> Path:
    return PHOTO_DIR.parent / "receipts"


# Пояснение «кто и почему платил» короче визитного текста: это одно поле формы,
# а не свободный комментарий на 4000 знаков.
RECEIPT_NOTE_MAX = 500


def _receipt_query(request: web.Request) -> dict:
    """Поля чека из query. Ошибки ПОЛЯ не роняют чек: оффлайн-очередь хоронит
    запись на любом не-2xx (то же правило, что у нечитаемой суммы в /api/save),
    а непринятое поле возвращается в rejected — нечитаемая сумма не должна
    уничтожать само доказательство платежа."""
    rejected: dict[str, str] = {}
    amount = None
    raw = (request.query.get("amount") or "").strip().replace(",", ".")
    if raw:
        try:
            amount = round(float(raw), 2)
            if not 0 < amount <= 1e9:
                raise ValueError
        except ValueError:
            rejected["amount"] = "format"
            amount = None
    paid_at = None
    raw_date = (request.query.get("paid_at") or "").strip()
    if raw_date:
        paid_at = db._as_date(raw_date)
        if paid_at is None:
            rejected["paid_at"] = "format"
        elif paid_at > workday.today():
            # Дата из будущего — не «день платежа», а опечатка: поле отбрасываем,
            # чек остаётся (дата загрузки-то настоящая).
            rejected["paid_at"] = "future"
            paid_at = None
    payer_is_client = (request.query.get("payer") or "client") != "other"
    note = (request.query.get("note") or "").strip()[:RECEIPT_NOTE_MAX]
    if not payer_is_client and not note:
        # «Другое лицо без пояснения» — сам по себе сигнал для СБ, чек принимаем.
        rejected["payer_note"] = "required"
    return {"amount": amount, "paid_at": paid_at, "payer_is_client": payer_is_client,
            "note": note, "currency": (request.query.get("currency")
                                       or "").strip()[:8] or None,
            "rejected": rejected}


async def _receipt(request: web.Request) -> web.Response:
    uid, emp = _auth(request)
    _require_field_write(uid)
    rep = request.query.get("rep", "")
    row = db.get_client(rep)
    if not row or (row["responsible"] != emp and row["responsible_soft"] != emp and uid != ADMIN_ID):
        raise web.HTTPNotFound(text="person")
    op = re.sub(r"[^A-Za-z0-9_-]", "", request.query.get("op", ""))[:64]
    if not op:
        raise web.HTTPBadRequest(text="op")
    if request.content_type.lower() != "application/octet-stream":
        raise web.HTTPUnsupportedMediaType(text="receipt content type")
    fields = _receipt_query(request)
    visit_op = re.sub(r"[^A-Za-z0-9_-]", "",
                      request.query.get("visit") or "")[:64] or None
    if visit_op:
        ids = {r["contract_id"] for r in db.person_contracts(rep)}
        with db.connect() as conn:
            associated = conn.execute("SELECT contract_id,agent_id FROM visits WHERE op_id=?",
                                      (visit_op,)).fetchone()
        if not associated or associated["contract_id"] not in ids or associated["agent_id"] != uid:
            raise web.HTTPBadRequest(text="Чек нельзя связать с этим выездом")
    fields["currency"] = fields["currency"] or debt.unit([row["partner"]]) or None
    try:
        stored = await _store_photo(
            request.content.iter_chunked(_PHOTO_CHUNK), receipt_dir() / rep, op,
            request.headers.get("X-Receipt-Ext"))
    except _PhotoBodyError as e:
        if e.too_large:
            raise web.HTTPRequestEntityTooLarge(
                max_size=PHOTO_MAX_BYTES, actual_size=e.actual_size)
        raise web.HTTPUnprocessableEntity(text=e.reason)
    db.audit_access(uid, "receipt", rep)
    rid = db.record_receipt(
        op, rep, emp, uid, visit_op=visit_op,
        paid_at=fields["paid_at"].isoformat() if fields["paid_at"] else None,
        amount=fields["amount"], currency=fields["currency"],
        payer_is_client=fields["payer_is_client"],
        payer_note=fields["note"] or None,
        file=f"{rep}/{stored.path.name}",
        sha256=stored.sha256, size_bytes=stored.size, source="app")
    body = {"ok": True, "id": rid}
    if fields["rejected"]:
        body["rejected"] = fields["rejected"]
    return web.json_response(body)


async def _manager_receipt_file(request: web.Request) -> web.Response:
    """Байты чека — руководителю и безопаснику своей команды. Право то же, что
    у снимков: каталог договора целиком (_photo_scope_ok); файл лежит в другом
    корне, но ключ тот же формат «КД/имя»."""
    uid, _mgr, scope = _manager_gate(request)
    key = (request.query.get("file") or "")[:220]
    path = photostore.photo_path(key, root=receipt_dir())
    if path is None or path.suffix.lower() not in _PHOTO_SUFFIXES or not path.is_file():
        raise web.HTTPNotFound(text="receipt")
    contract_dir = key.rsplit("/", 1)[0]
    ctx = db.photo_contract_dirs(contract_dir)
    if contract_dir not in ctx["dirs"]:
        db.audit_access(uid, "receipt_view_denied", key)
        raise web.HTTPNotFound(text="receipt")
    rows = db.photo_index_for_dirs(ctx["dirs"])
    soft_owner = any(c["contract_id"] == ctx["cid"] and (c["responsible_soft"] or "") in scope
                     for c in ctx["contracts"])
    with db.connect() as conn:
        authors = [r[0] for r in conn.execute("SELECT employee FROM payment_evidence WHERE file=?", (key,))]
    receipt_author = any(name in scope for name in authors)
    if not (_photo_scope_ok(uid, scope, ctx["contracts"], rows) or soft_owner or receipt_author):
        db.audit_access(uid, "receipt_view_denied", key)
        raise web.HTTPNotFound(text="receipt")
    db.audit_access(uid, "receipt_view", key)   # ИБ: каждый просмотр ПД — в журнал
    resp = web.FileResponse(path)
    resp.content_type = _PHOTO_MIME.get(path.suffix.lower(),
                                        "application/octet-stream")
    resp.headers["Cache-Control"] = "no-store, private"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Disposition"] = "inline"
    return resp


# --- Поиск фото по КД для руководителя (первый HTTP-выход снимков наружу) ----
_PHOTO_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg",
               ".jfif": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
               ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
               ".avif": "image/avif"}


def _photo_scope_ok(uid: int, scope: list[str], contracts, rows) -> bool:
    """Руководитель видит фото своих сотрудников И фото по договорам своих
    сотрудников, включая ОТКРЕПЛЁННЫЕ (по unassigned_from), а также архив по
    следу «кто загружал» (audit — у бэкфилленных строк telegram_id пуст, а
    после законного переназначения импорт стирает метку открепления).
    Владелец — без ограничений."""
    if uid == ADMIN_ID:
        return True
    s = set(scope)
    if not s:
        return False
    dirs = sorted({(c["contract_id"] or "") for c in contracts if c["contract_id"]})
    for c in contracts:
        resp = (c["responsible"] or "").strip()
        if resp and resp in s:
            return True
        una = (c["unassigned_from"] if "unassigned_from" in c.keys() else "") or ""
        if not resp and una.strip() in s:
            return True
    seen: dict[int, str | None] = {}
    for r in rows:
        tg = r["telegram_id"]
        if not tg:
            continue
        if tg not in seen:
            seen[tg] = db.get_binding(tg)
        if seen[tg] and seen[tg] in s:
            return True
    for tg in db.photo_uploaders_for_dirs(dirs):
        if tg not in seen:
            seen[tg] = db.get_binding(tg)
        if seen[tg] and seen[tg] in s:
            return True
    return False


async def _manager_photos(request: web.Request) -> web.Response:
    """Поиск фото по номеру договора: список с датами и сигналами к разбору."""
    uid, _mgr, scope = _manager_gate(request)
    raw = (request.query.get("contract_id") or "").strip()[:64]
    if not raw:
        raise web.HTTPBadRequest(text="contract_id")
    ctx = db.photo_contract_dirs(raw)
    rows = db.photo_index_for_dirs(ctx["dirs"])
    files = {}
    for d in ctx["dirs"]:
        for pth in photostore.contract_photos(d, root=PHOTO_DIR):
            files[photostore.photo_key(pth, root=PHOTO_DIR)] = pth
    if not _photo_scope_ok(uid, scope, ctx["contracts"], rows):
        # Отказ — тоже след: незалогированный перебор чужих КД — первое
        # замечание ИБ на новой поверхности ПД.
        db.audit_access(uid, "photo_search_denied", ctx["cid"])
        raise web.HTTPNotFound(text="photos")   # 404: не подтверждаем существование КД
    payload = photoaudit.build(
        ctx, rows, files,
        visits=db.visits_for_contracts(
            [c["contract_id"] for c in ctx["contracts"]] or [ctx["cid"]]),
        thresholds=photoaudit.thresholds(db.get_setting),
        date_from=(request.query.get("from") or "")[:10],
        date_to=(request.query.get("to") or "")[:10],
        dup_rows=db.photo_rows_by_sha([r["sha256"] for r in rows if r["sha256"]]),
    )
    # Дедуп-сигналы могут называть ЧУЖОЙ договор: без права на тот альбом
    # номер маскируется, раскрытие — отдельной строкой в журнал.
    for item in payload["items"]:
        for s in item["signals"]:
            if s["code"] != "same_file_other_contract":
                continue
            other = s.get("other_contract") or ""
            octx = db.photo_contract_dirs(other)
            orows = db.photo_index_for_dirs(octx["dirs"])
            if _photo_scope_ok(uid, scope, octx["contracts"], orows):
                db.audit_access(uid, "photo_dup_reveal", other)
            else:
                s["other_contract"] = None
                s["foreign"] = True
    db.audit_access(uid, "photo_search", ctx["cid"], f"n={len(payload['items'])}")
    return web.json_response({"ok": True, **payload})


async def _manager_photo_file(request: web.Request) -> web.Response:
    """Байты снимка. <img src> невозможен: токен в query запрещён, поэтому
    оболочка тянет это fetch'ем с authHeaders() и делает blob:-URL."""
    uid, _mgr, scope = _manager_gate(request)
    key = (request.query.get("file") or "")[:220]
    path = photostore.photo_path(key, root=PHOTO_DIR)
    if path is None or path.suffix.lower() not in _PHOTO_SUFFIXES             or not path.is_file():
        raise web.HTTPNotFound(text="photo")
    # Ключ — «КД/имя», и КД бывает со слэшем (вложенный каталог). Право
    # считаем по каталогу договора ЦЕЛИКОМ — всему до последнего «/», — а не
    # по первому сегменту: иначе «A/B/имя» проверялся как договор «A» и
    # отдавал снимок чужого договора «A/B» (ревью 25.09, F-3). Каталог обязан
    # быть одним из каталогов этого договора — ровно так их перечисляет список
    # (_manager_photos), поэтому законный просмотр не меняется.
    contract_dir = key.rsplit("/", 1)[0]
    ctx = db.photo_contract_dirs(contract_dir)
    if contract_dir not in ctx["dirs"]:
        db.audit_access(uid, "photo_view_denied", key)
        raise web.HTTPNotFound(text="photo")
    rows = db.photo_index_for_dirs(ctx["dirs"])
    if not _photo_scope_ok(uid, scope, ctx["contracts"], rows):
        db.audit_access(uid, "photo_view_denied", key)
        raise web.HTTPNotFound(text="photo")
    db.audit_access(uid, "photo_view", key)   # ИБ: каждый просмотр ПД — в журнал
    resp = web.FileResponse(path)
    resp.content_type = _PHOTO_MIME.get(path.suffix.lower(),
                                        "application/octet-stream")
    resp.headers["Cache-Control"] = "no-store, private"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Disposition"] = "inline"
    return resp


# Группы выработки, которые назвал офис: 8, 10 и 12 выездов в операционный день.
# Список — подсказка для кнопок, любое другое число тоже принимается.
VISIT_PLAN_GROUPS = (8, 10, 12)


# --- Голосовой комментарий: служба распознавания речи по Unix-сокету --------
_STT = None
# Одно распознавание за раз и БЕЗ очереди. Прежний asyncio.Lock копил ждущих
# без предела: фраза — около 17 с на обоих ядрах VM, и один токен, шлющий
# записи подряд, держал голос занятым для всех (ревью 25.09.2026, #15). Занято —
# сразу 503: оболочка на 429/503 показывает «голос недоступен» и сама не повторяет.
_VOICE_SEM = asyncio.Semaphore(1)
VOICE_BUSY_TEXT = "распознавание занято, повторите через минуту"
# Для таджикского служба пока использует auto.
_VOICE_LANG = {"ru": "ru", "uz": "uz"}


def _voice_busy() -> web.HTTPServiceUnavailable:
    return web.HTTPServiceUnavailable(
        text=json.dumps({"error": VOICE_BUSY_TEXT}, ensure_ascii=False),
        content_type="application/json",
        headers={"Retry-After": "60"},
    )


def _voice_configured() -> bool:
    return (
        VOICE_BACKEND in VOICE_BACKENDS
        and bool(STT_SOCKET)
        and bool(STT_TOKEN_FILE)
    )


def _stt() -> stt_client.STTClient:
    global _STT
    if _STT is None:
        _STT = stt_client.STTClient(
            unix_socket=STT_SOCKET,
            token_file=STT_TOKEN_FILE,
            timeout_seconds=VOICE_TIMEOUT_SECONDS,
        )
    return _STT


async def _voice(request: web.Request) -> web.Response:
    """Голосовое из миниаппа → служба распознавания речи → текст.

    На границу службы уходят только байты и ``ru|uz|auto``: uid/ФИО/КД,
    исходное имя файла и текст формы не передаются.
    """
    uid, _emp = _auth(request)
    if not _voice_configured():
        raise web.HTTPServiceUnavailable(text="voice off")
    if _VOICE_SEM.locked():
        raise _voice_busy()        # не принимаем запись, которую некому распознать
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
        # Пока запись ехала, службу мог занять другой: снова отказ, не очередь.
        # Между проверкой и захватом нет await — гонки в одном цикле нет.
        if _VOICE_SEM.locked():
            raise _voice_busy()
        async with _VOICE_SEM:
            text = str(await _stt().transcribe(tmp, lang) or "").strip()
        if not text:
            raise web.HTTPUnprocessableEntity(text="empty transcript")
    except stt_client.STTError as exc:
        logging.warning(
            "служба распознавания речи: сбой status=%s retryable=%s",
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
        logging.exception("служба распознавания речи: неожиданный сбой клиента")
        raise web.HTTPServiceUnavailable(text="voice temporarily unavailable")
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return web.json_response({"ok": True, "text": text})


# Классы устройств для ссылок входа. Короткое слово, а не сырой User-Agent: в
# аудите и в пульте нужно «с чего зашёл», а полная строка UA — это и мусор на
# экране, и лишний след. Суффикс «-pwa» значит «уже установленный ярлык» — такой
# контекст сообщает о себе сам клиент (navigator.standalone), из UA его не видно.
_AGENT_WEBVIEW = ("; wv)", "fb_iab", "fbav", "fban", "instagram", "line/",
                  "micromessenger", "gsa/", "yandexsearch")
_AGENT_DESKTOP = ("windows nt", "macintosh", "x11", "linux", "cros")


def _agent_class(ua: str, standalone: bool = False) -> str:
    """iphone / ipad / android / desktop / inapp / other (+ «-pwa» у ярлыка).

    iOS определяем ПЕРВЫМ и не смотрим на встроенный браузер: именно открытому во
    встроенном браузере айфону нужен запасной обмен на установку ярлыка, и класс
    «inapp» отнял бы у него бонус.
    """
    u = (ua or "").lower()
    if not u:
        return "other"
    if "ipad" in u:
        base = "ipad"
    elif "iphone" in u or "ipod" in u:
        base = "iphone"
    elif "android" in u:
        base = "inapp" if any(m in u for m in _AGENT_WEBVIEW) else "android"
    elif any(m in u for m in _AGENT_DESKTOP):
        base = "desktop"
    else:
        base = "other"
    return base + "-pwa" if standalone else base


# Роботы предпросмотра ссылок. До этой работы обмен шёл только POST-ом, и «съесть»
# ссылку предпросмотром было нельзя; у GET /i/<код> такая возможность появляется —
# мессенджер, почта или антивирус открывают ссылку РАНЬШЕ человека и сжигают обмен.
# Им отдаём страницу-заглушку, не трогая счётчик.
_AGENT_PREFETCH = ("telegrambot", "whatsapp/", "facebookexternalhit", "twitterbot",
                   "slackbot", "discordbot", "skypeuripreview", "vkshare", "viber",
                   "linkedinbot", "googlebot", "bingbot", "bingpreview", "yandexbot",
                   "applebot", "duckduckbot", "petalbot", "ahrefsbot", "semrushbot",
                   "crawler", "spider", "headlesschrome", "preview")

# Подсказка «этот браузер только что входил по ссылке»: имя и срок жизни куки,
# которой /i/<код> узнаёт повторный тап. Пятнадцати минут хватает на весь путь
# «открыл в мессенджере → переоткрыл в Safari → поставил ярлык», и они же не
# дают подсказке зажиться на общем телефоне.
_REOPEN_COOKIE = "hard_open"
_REOPEN_TTL_S = 900

# Заглушка для робота предпросмотра. Кода в ней нет — только человеку понятная
# подпись и кнопка «войти» на тот же адрес с ?go=1: если живой браузер вдруг
# опознан как робот, человек всё равно войдёт в одно касание.
_LINK_PREVIEW_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Хард — вход</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:40px 16px">
<h1 style="font-size:20px">Хард</h1>
<p>Ссылка входа. Откройте её на рабочем телефоне.</p>
<p><a href="?go=1" style="display:inline-block;padding:12px 20px;border-radius:12px;
background:#1f6feb;color:#fff;text-decoration:none">Войти</a></p>
</body></html>"""


def _is_link_prefetch(ua: str) -> bool:
    # Пустой User-Agent и всё, что не представилось браузером, — не человек:
    # живые браузеры поголовно начинают строку с «Mozilla/5.0», а curl, wget,
    # okhttp, Go-http-client и почтовые сканеры — нет. Дешевле одной проверкой
    # отсечь всех разом, чем гнаться за списком роботов.
    u = (ua or "").strip().lower()
    return not u or "mozilla" not in u or any(m in u for m in _AGENT_PREFETCH)


async def _app_login(request: web.Request) -> web.StreamResponse:
    """GET /i/<код> — короткая ссылка входа: открыл и уже внутри.

    Сервер сам меняет код на токен и уводит на «/#t=<токен>». Токен едет во
    ФРАГМЕНТЕ: на сервер он при следующем запросе не попадёт, в access-логи и
    Referer — тоже, а оболочка его уже понимает (absorbTokenFromUrl). Ни ввода
    кода, ни бота, ни объяснений человеку — ровно то, ради чего это делалось.

    Исчерпанная или чужая ссылка — не тупик: уводим на «/#link_dead=1», и оболочка
    сперва пробует свой сохранённый токен (человек чаще всего уже внутри и просто
    ткнул старый QR) и только потом показывает отказ.
    """
    # Перебор кодов считаем тем же счётчиком неудач, что у админ-ручек: /i/ —
    # такая же дверь без пароля, и оставлять её без счёта нельзя.
    _admin_too_many_fails(request)
    code = (request.match_info.get("code") or "").strip()
    ua = request.headers.get("User-Agent") or ""
    no_store = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
    if _is_link_prefetch(ua) and request.query.get("go") != "1":
        return web.Response(text=_LINK_PREVIEW_HTML, content_type="text/html",
                            headers=no_store)
    agent = _agent_class(ua)
    # Повторный тап ТОЙ ЖЕ ссылки в том же браузере не должен жечь обмен: в
    # мессенджере по ссылке тыкают дважды чаще, чем кажется, а двухразовой
    # ссылке это стоило бы обеих попыток ещё во встроенном браузере — до того,
    # как человек вообще доберётся до Safari и ярлыка. У GET заголовка
    # X-App-Token нет и быть не может, поэтому выданный токен кладётся в
    # короткоживущую HttpOnly-куку с областью «/i/» и на повторе подаётся как
    # keep_token: db.redeem_app_link вернёт его же, если он жив И принадлежит
    # владельцу ЭТОЙ ссылки. Кука — только подсказка: ни одна ручка API по ней
    # не пускает, доступ по-прежнему даёт исключительно заголовок.
    token = db.redeem_app_link(code, agent=agent,
                               keep_token=request.cookies.get(_REOPEN_COOKIE))
    if not token:
        _admin_auth_failed(request, scope="app")
        raise web.HTTPFound("/#link_dead=1", headers=no_store)
    _admin_auth_ok(request, scope="app")
    uid = db.uid_for_app_token(token) or 0
    db.audit_access(uid, "app_login", detail=agent)
    # Открытая ссылка входа — это и есть подтверждение привязки. Ссылку выдал тот,
    # у кого на это право (аналитик, руководитель своей команде, держатель логина
    # сервера), и достаётся она ровно тому, кому переслана. При переезде базы
    # привязки приезжают заявками (approved=0), а владельца для аппрува в контуре
    # компании нет: без этого человек входил по ссылке и упирался в «ожидайте
    # подтверждения» навсегда.
    if uid and db.get_binding(uid) is None and db.pending_binding(uid):
        employee = db.approve_binding(uid)
        db.audit_access(uid, "bind_approved_by_link", target=employee)
    # «fresh=1» — метка «токен выдан ПРЯМО СЕЙЧАС тому, кто открыл эту ссылку».
    # Без неё оболочка на устройстве, где кто-то уже входил, оставляла бы прежний
    # токен: обмен ссылки сервер потратил, а человек оказывался в ЧУЖОМ портфеле.
    # Метка едет во фрагменте (на сервер не вернётся) и живёт до первой удачной
    # загрузки — stampTokenUrl переписывает адрес на чистое «#t=<токен>».
    out = web.HTTPFound(f"/#t={token}&fresh=1", headers=no_store)
    # Secure ставим только по настоящему https: в рабочем контуре впереди Caddy
    # (X-Forwarded-Proto), а на голом http кука с Secure просто не сохранится, и
    # повторный тап снова начал бы жечь обмены.
    https = (request.headers.get("X-Forwarded-Proto") or request.scheme) == "https"
    out.set_cookie(_REOPEN_COOKIE, token, max_age=_REOPEN_TTL_S, path="/i/",
                   httponly=True, samesite="Lax", secure=https)
    raise out


async def _redeem(request: web.Request) -> web.Response:
    """Обмен кода из ссылки на девайс-токен автономного PWA (старый путь «#link=»,
    ручная вставка ссылки и ссылка, прилетевшая в уже открытую вкладку).

    Тело: ``{code, token?, standalone?}``. `token` — то, что лежит на устройстве
    сейчас: если он жив и принадлежит владельцу ссылки, сервер вернёт его же и
    обмен НЕ потратит. Без этого двухразовая ссылка сгорала бы на перезагрузке
    страницы и на второй попытке вставить ссылку руками.

    Код невалиден (исчерпан/просрочен/чужой) → 410: клиент решает сам — если на
    устройстве уже есть живой токен, продолжает тихо, иначе показывает отказ.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    code = str(body.get("code") or "").strip()
    keep = (str(body.get("token") or "").strip()
            or (request.headers.get("X-App-Token") or "").strip())
    agent = _agent_class(request.headers.get("User-Agent") or "",
                         bool(body.get("standalone")))
    _admin_too_many_fails(request)
    token = db.redeem_app_link(code, agent=agent, keep_token=keep or None)
    if not token:
        _admin_auth_failed(request, scope="app")
        raise web.HTTPGone(text="link")
    _admin_auth_ok(request, scope="app")
    uid = db.uid_for_app_token(token)
    db.audit_access(uid or 0, "app_login", detail=agent)
    return web.json_response({"ok": True, "token": token, "days": db.APP_TOKEN_DAYS})


async def _logout(request: web.Request) -> web.Response:
    """«Выйти с этого телефона» и замена входа новой ссылкой: гасим ИМЕННО
    предъявленный токен (заголовок X-App-Token). Оболочка стирает телефон и
    сама, но у ярлыка iOS хранилище своё, а токен вшит в его start_url — без
    серверного отзыва ярлык продолжал бы входить. Погасить можно только то, чем
    и так владеешь, поэтому другой проверки не нужно; неизвестный токен — тот
    же ответ, чтобы ручка не служила проверкой «жив ли чужой токен»."""
    tok = (request.headers.get("X-App-Token") or "").strip()
    uid = db.drop_app_token(tok) if tok else None
    if uid:
        db.audit_access(uid, "app_logout")
    return web.json_response({"ok": True})


async def _admin_app_revoke(request: web.Request) -> web.Response:
    """Отзыв доступа автономного приложения из пульта: ``{telegram_id, scope}``.

    `scope="all"` (по умолчанию, как было) — и токены, и ссылки: человек выходит
    везде. `scope="links"` — гасим только ссылки: выданный не тому QR умирает, а
    телефон, который уже работает, не разлогинивается.
    """
    _admin(request)
    body = await request.json()
    try:
        tid = int(body["telegram_id"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="telegram_id")
    scope = "links" if str(body.get("scope") or "all") == "links" else "all"
    n = db.revoke_app_access(tid, scope=scope)
    db.audit_access(tid, "app_revoke", detail=f"{scope}:{n}")
    return web.json_response({"ok": True, "revoked": n, "scope": scope})


async def _admin_stats_book(request: web.Request) -> web.Response:
    """GET /admin/stats_book.xlsx — книга статистики на всю команду для пульта
    аналитика. Нужна рассылке (29.09.2026): пульт в трее забирает книгу и
    отправляет её гендиректору и заму почтой владельца через Outlook.

    Окно — как у руководителя (preset/shift/from/to); kind=daily — вечерняя
    книга за день date=; direction=tj|uz — одна республика."""
    _admin(request)
    q = request.query
    direction = (q.get("direction") or "").strip()
    if direction not in ("", "all", "tj", "uz"):
        raise web.HTTPBadRequest(text="direction")
    mgr = digest_data.manager_scope(0, is_owner=True)
    # Республика — живым составом направления. Добор «кто выезжал в окне»
    # (window_team) здесь не нужен: он охватил бы и чужую республику.
    team = (db.employees_by_direction(direction) if direction in ("tj", "uz")
            else None)
    if q.get("kind") == "daily":
        day = (q.get("date") or "").strip() or digest_data.today_iso()
        if db._as_date(day) is None:
            raise web.HTTPBadRequest(text="date")
        team = team if team is not None else digest_data.sorted_employees(mgr)
        body = await asyncio.to_thread(digest_data.daily_report_xlsx, mgr, day, team)
        name = f"daily_{day}"
    else:
        rng = _stats_range_q(request)
        if team is None:
            team = _range_team(mgr, digest_data.sorted_employees(mgr), rng)
        body = await asyncio.to_thread(
            functools.partial(digest_data.stats_xlsx, mgr, rng, employees=team))
        name = f"stats_{rng['from']}_{rng['to']}"
    db.audit_access(0, "digest_export",
                    detail=f"admin {name} dir={direction or 'all'} team={len(team)}")
    return web.Response(
        body=body,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}.xlsx",
                 "Cache-Control": "no-store"})


async def _admin_app_revoke_bulk(request: web.Request) -> web.Response:
    """Сбросить доступ пачкой: ``{telegram_ids: [...], scope: "all"|"links"}``.

    То же, что /admin/app_revoke, но одним запросом: пульт ходит на сервер через
    туннель, и по запросу на человека сброс команды из шестидесяти шёл минуту.
    `all` гасит и ссылки, и телефоны, и компьютеры с пультом руководителя."""
    _admin(request)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "тело не JSON"}, status=400)
    ids = body.get("telegram_ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or len(ids) > APP_LINKS_BULK_MAX:
        return web.json_response({"error": "нужен список telegram_ids"}, status=400)
    scope = "links" if str(body.get("scope") or "all") == "links" else "all"
    revoked = people = 0
    for raw in ids:
        try:
            tid = int(raw)
        except (TypeError, ValueError):
            continue
        revoked += db.revoke_app_access(tid, scope=scope)
        people += 1
        db.audit_access(tid, "app_revoke", detail=f"{scope}:bulk")
    return web.json_response({"ok": True, "people": people, "revoked": revoked, "scope": scope})


async def _admin_app_links(request: web.Request) -> web.Response:
    """Состояние входов для раздела «Вход» в пульте: строка на человека.

    Пульт сводит этот список со своим списком сотрудников реестра: кого здесь нет —
    тому ссылку ещё не выдавали. Поля — см. db.app_links_state, плюс готовый `url`.

    Адрес собирает сервер, а не пульт: пульт ходит на сервер по служебному адресу
    (бывает и по IP), а телефон открывает публичный — собрав ссылку из своего
    адреса, пульт нарисовал бы QR, который никуда не ведёт. Без MINIAPP_URL адрес
    пустой, и это честнее выдуманного.
    """
    _admin(request)
    base = os.getenv("MINIAPP_URL", "").strip().rstrip("/")
    links = db.app_links_state()
    for row in links:
        row["url"] = f"{base}/i/{row['code']}" if base and row.get("code") else ""
    return web.json_response({"links": links})


def _admin_issue_link(body: dict, base: str) -> tuple[int, dict]:
    """Одна выдача ссылки аналитиком: общая для одиночной и массовой ручек.

    Возвращает (HTTP-статус, ответ). Правила одни на обе ручки: руководителю и
    владельцу — по id без привязки к ФИО, привязанному — его же id,
    непривязанному ФИО — служебная личность. Число открытий проверяется ДО
    того, как заводится личность: иначе опечатка в `uses` оставляла бы в базе
    лишнюю служебную учётку.
    """
    employee = str(body.get("employee") or "").strip()
    kind = "courier" if body.get("kind") == "courier" else "agent"
    raw_uses = body.get("uses")
    if raw_uses in (None, ""):
        uses = db.APP_LINK_DEFAULT_USES
    else:
        try:
            uses = int(raw_uses)
        except (TypeError, ValueError):
            return 400, {"error": "uses"}
        if not 1 <= uses <= db.APP_LINK_MAX_USES:
            return 400, {"error": f"uses: от 1 до {db.APP_LINK_MAX_USES}"}
    created = False
    raw_tid = body.get("telegram_id")
    if raw_tid not in (None, ""):
        try:
            tid = int(raw_tid)
        except (TypeError, ValueError):
            return 400, {"error": "telegram_id"}
        bound = db.get_binding(tid)
        if not bound and not employee and (tid == ADMIN_ID or db.is_manager(tid)):
            # Руководитель (или владелец) входит по ссылке БЕЗ привязки к ФИО:
            # у него своя роль, а привязка сделала бы его выездником. Раньше эта
            # ручка отвечала ему «никому не привязан», и руководитель, который
            # по контракту раздаёт входы команде, сам войти не мог вовсе.
            bound = (db.get_manager(tid) or {}).get("name") or f"руководитель {tid}"
        elif not bound and employee:
            db.set_bind(tid, employee, kind)
            bound = employee
        if not bound:
            return 409, {"error": f"id {tid} никому не привязан: укажи сотрудника"}
        employee = bound
    else:
        if not employee:
            return 400, {"error": "employee required"}
        tid, created = db.local_identity_for(employee, kind)
    code = db.create_app_link(tid, max_uses=uses)
    db.audit_access(tid, "app_link_issued_admin", detail=employee)
    return 200, {
        "ok": True,
        "telegram_id": tid,
        "employee": employee,
        "code": code,
        "url": f"{base}/i/{code}",
        "uses": 0,
        "max_uses": uses,
        "days": db.APP_LINK_DAYS,
        "expires_at": db.app_link_expires_at(
            datetime.now().isoformat(timespec="seconds")),
        "local_identity": db.is_local_uid(tid),
        "created": created,
    }


def _public_base() -> tuple[str, web.Response | None]:
    """Публичный адрес приложения или готовый отказ 409, если его нет."""
    base = os.getenv("MINIAPP_URL", "").strip().rstrip("/")
    if base:
        return base, None
    return "", web.json_response(
        {"error": "MINIAPP_URL не задан на сервере: без публичного адреса "
                  "ссылку входа не собрать"},
        status=409,
    )


async def _admin_app_link(request: web.Request) -> web.Response:
    """Ссылка входа в приложение из пульта — без Telegram.

    Тело: ``{"employee": ФИО, "kind": "agent"|"courier", "uses": N}`` либо
    ``{"telegram_id": id}``. У сотрудника без привязки создаётся служебная
    личность (db.local_identity_for), у привязанного — используется его id.
    `uses` — сколько раз ссылку можно обменять (1..APP_LINK_MAX_USES, по умолчанию
    два: браузер и отдельное хранилище ярлыка iOS). Сколько обменов останется у
    конкретного телефона, сервер решит сам: айфону на первом открытии добавится +1.

    Адрес короткий и путевой — ``<MINIAPP_URL>/i/<код>``: его видно целиком на
    экране, он читается с QR и его можно продиктовать. Открыл — и уже внутри.
    """
    _admin(request)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "тело не JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "тело не объект"}, status=400)
    base, refused = _public_base()
    if refused is not None:
        return refused
    status, payload = _admin_issue_link(body, base)
    return web.json_response(payload, status=status)


# Больше, чем людей в реестре и руководителей вместе, с запасом: защита от
# случайного «выдать миллион», а не рабочее ограничение.
APP_LINKS_BULK_MAX = 500


async def _admin_app_links_bulk(request: web.Request) -> web.Response:
    """Массовая выдача ссылок одним запросом — старт пробы в один клик.

    Тело: ``{"items": [{"employee": ФИО, "telegram_id": id?, "kind": …}, …],
    "uses": N}``. Каждая строка выдаётся по тем же правилам, что одиночная
    (`_admin_issue_link`), и ошибка одной строки не валит остальные: в ответе у
    неё будет `error`, у удачных — ссылка. По одной выдаче на запрос пульт через
    транзит тратил бы секунду на человека.
    """
    _admin(request)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "тело не JSON"}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        return web.json_response({"error": "нужен список items"}, status=400)
    items = body["items"]
    if len(items) > APP_LINKS_BULK_MAX:
        return web.json_response(
            {"error": f"за раз не больше {APP_LINKS_BULK_MAX} человек"}, status=400)
    base, refused = _public_base()
    if refused is not None:
        return refused
    results = []
    for item in items:
        if not isinstance(item, dict):
            results.append({"employee": "", "error": "строка не объект"})
            continue
        one = dict(item)
        if "uses" not in one and "uses" in body:
            one["uses"] = body["uses"]
        status, payload = _admin_issue_link(one, base)
        if status != 200:
            payload = {"employee": str(item.get("employee") or ""),
                       "telegram_id": item.get("telegram_id"),
                       "error": str(payload.get("error") or f"HTTP {status}")}
        results.append(payload)
    issued = sum(1 for r in results if r.get("url"))
    db.audit_access(0, "app_links_bulk", detail=f"выдано {issued} из {len(items)}")
    return web.json_response({"ok": True, "issued": issued, "results": results})


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


def _esc(v) -> str:
    """Экранирование для parse_mode=HTML. Локальная лямбда `e = lambda v: ...`
    рядом с `except Exception as e` — мина: Python удаляет имя после
    except-блока, и второй адресат в цикле падал бы NameError только в проде и
    только при уже сбоящей отправке. Имя модульное, никакой except его не затрёт."""
    return _html.escape(str(v or ""))


# --- День выезда, а не день досыла ------------------------------------------
# Выезд, отработанный без сети, лежит в очереди телефона до связи. Сотрудник из
# Худжанда откатал адреса во вторник 8.09.2026, уехал в горы, где глушат
# интернет, и в четверг 10.09 все его визиты подгрузились ЧЕТВЕРГОМ: сервер
# ставил дату по моменту доставки. Для руководителя это выглядит как выезды,
# которых во вторник не было, и как всплеск в четверг.
#
# Телефон знает время события (в очереди у записи есть `at`) и присылает его в
# occurred_at. Дату считаем ПО СЕРВЕРУ, как и всё остальное в системе:
# контейнер живёт по UTC, телефон — по +3/+5, и клиентская дата в утренние часы
# врала бы барабану и киллер-фильтру (см. _meta).
OCCURRED_MAX_AGE_DAYS = 14


def _late_visit_line(visit_day: str | None) -> str | None:
    """«Этот выезд был не сегодня» — строка для всех трёх карточек.

    Без неё доставленный из оффлайна визит выглядит как сегодняшний, и
    руководитель либо считает его свежим, либо не понимает, почему в сводке за
    тот день его не было."""
    if not visit_day or visit_day == workday.today().isoformat():
        return None
    try:
        pretty = date.fromisoformat(visit_day).strftime("%d.%m")
    except ValueError:
        return None
    return f"🕓 Выезд от {pretty} — доставлен из оффлайна"


# Дольше половины суток на одной карточке — это не выезд, а забытое открытым
# приложение: такие значения не считаем вовсе, чтобы средние не врали.
FILL_SECONDS_MAX = 12 * 3600


def _fill_seconds(body) -> int | None:
    """Сколько человек пробыл на карточке: от её открытия до «Сохранить выезд».

    Офис просил видеть время на адресе (Худжанд, 11.09.2026). Честная граница
    измерения — именно карточка: приложение не знает, когда человек подошёл к
    подъезду, но знает, когда открыл карточку должника и когда закрыл выезд.
    Часы телефона могут врать, поэтому берём РАЗНИЦУ двух его же отметок.
    """
    try:
        opened = float(body.get("opened_at"))
        done = float(body.get("occurred_at") or 0) or opened
    except (TypeError, ValueError, OverflowError):
        return None
    delta = (done - opened) / 1000
    if not math.isfinite(delta):
        return None                 # int(inf) — 500 посреди сохранения визита
    seconds = int(delta)
    if seconds < 0 or seconds > FILL_SECONDS_MAX:
        return None
    return seconds


def _saved_at(body) -> str | None:
    """Когда человек нажал «Сохранить выезд», по часам его телефона.

    Отдельно от приёма сервером: в поле без связи между этими двумя моментами
    бывают сутки, и раньше — когда днём выезда был день загрузки — работа
    целого дня ложилась в чужой день.
    """
    try:
        moment = datetime.fromtimestamp(float(body.get("occurred_at")) / 1000.0, timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return moment.isoformat(timespec="seconds")


def _occurred_on(body) -> str:
    """День выезда из occurred_at (мс эпохи) — или сегодня, если его нет.

    Клиенту доверяем ограниченно: будущим днём визит не датируется никогда, а
    слишком старый (больше двух недель) — это не оффлайн, а сбитые часы или
    очередь, пролежавшая с прошлой жизни телефона."""
    today = workday.today()
    raw = body.get("occurred_at")
    if raw is None:
        return today.isoformat()
    try:
        moment = datetime.fromtimestamp(float(raw) / 1000.0, evidence.FIELD_TZ)
    except (TypeError, ValueError, OSError, OverflowError):
        return today.isoformat()
    day = moment.date()
    if day >= today:
        return today.isoformat()
    if (today - day).days > OCCURRED_MAX_AGE_DAYS:
        return today.isoformat()
    return day.isoformat()


# --- Догон фото: карточка визита ждёт снимки, которые ещё едут ---------------
# Карточка уходила МГНОВЕННО по закрытию визита, а снимок ехал своим запросом.
# По журналу за 8–10.09.2026: из 293 карточек 48 ушли с «📸 без фото», хотя фото
# появлялось на сервере в среднем через 2 секунды после отправки карточки
# (42 случая из 48 — в пределах минуты). Дело не в связи, а в гонке: /api/save —
# короткий JSON, /api/photo — сотни килобайт, и человек жмёт «Сохранить выезд»,
# пока снимок ещё грузится. Руководитель получал «без фото» по визиту, у
# которого фото есть, и переставал верить карточкам вообще.
#
# Чиним с двух сторон. Оболочка (v40) сама дожидается своих загрузок и говорит,
# сколько снимков ещё в пути (photos_pending) — это убирает гонку в источнике.
# Здесь — серверная страховка: она работает и со старой оболочкой на телефоне,
# и когда приложение выгрузили из памяти на полпути.
PHOTO_GRACE_S = 60          # потолок ожидания, настройка settings['photo_grace_s']
# Оболочка до v40 счётчик не шлёт, и «фото нет» для неё неотличимо от «фото
# ещё едет». Ждать там полное окно значит задерживать на минуту и честную
# карточку без фото («нет контакта»), поэтому вслепую ждём заметно меньше:
# по журналу прода этого хватает на 40 случаев из 48.
PHOTO_GRACE_BLIND_S = 25
_PHOTO_GRACE_POLL_S = 1.0


def _photo_grace_s() -> int:
    """Сколько ждать опоздавшее фото. 0 в настройке полностью отключает догон."""
    raw = (db.get_setting("photo_grace_s") or "").strip()
    if not raw:
        return PHOTO_GRACE_S
    try:
        return max(0, min(int(raw), 600))
    except ValueError:
        return PHOTO_GRACE_S


def _photos_pending(body) -> int | None:
    """Сколько снимков по этому договору оболочка ещё не отдала серверу.
    None — оболочка до v40 (поля нет), тогда судим по самим файлам."""
    raw = body.get("photos_pending")
    if raw is None:
        return None
    try:
        return max(0, min(int(raw), 50))
    except (TypeError, ValueError, OverflowError):
        return None


async def _await_visit_photos(cid: str, day: str, pending: int | None) -> None:
    """Подождать снимки визита, но не дольше окна догона.

    pending=0 — оболочка ЗНАЕТ, что фото не будет («нет контакта», дверь никто
    не открыл): такая карточка обязана уйти сразу, ждать нечего. pending>0 —
    ждём ровно столько снимков, сколько обещано. None (старая оболочка) — ждём
    только если фото за день нет вовсе; лишний снимок к уже имеющимся так не
    поймать, но и карточку с фото это не задержит."""
    if pending == 0:
        return
    grace = _photo_grace_s()
    if grace <= 0:
        return
    contracts = db.person_contracts(cid)
    # Листание папок (iterdir + stat на каждый снимок) — в потоке: опрос идёт
    # раз в секунду до конца окна догона (до 600 с), и в цикле событий он
    # отнимал время у всех остальных запросов (ревью 25.09.2026, F-2в).
    have = len(await asyncio.to_thread(day_photos, contracts, day))
    if pending is None:
        if have:
            return
        grace = min(grace, PHOTO_GRACE_BLIND_S)
    target = have + (pending or 1)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        await asyncio.sleep(_PHOTO_GRACE_POLL_S)
        if len(await asyncio.to_thread(day_photos, contracts, day)) >= target:
            return


# Ссылку на фоновую задачу держим сами: asyncio хранит только слабую, и
# сборщик вправе унести задачу вместе с неотправленной карточкой.
_VISIT_NOTIFY_TASKS: set[asyncio.Task] = set()


def _spawn_visit_notify(coro) -> None:
    task = asyncio.create_task(coro)
    _VISIT_NOTIFY_TASKS.add(task)
    task.add_done_callback(_VISIT_NOTIFY_TASKS.discard)


async def _visit_notify(request: web.Request, notify_row, uid: int,
                        dist_m, geo_far, actor, vlat, vlon,
                        day: str, pending: int | None,
                        evidence_ctx: dict | None = None) -> None:
    """Все три канала по закрытому визиту — в фоне, после догона фото.

    Тело запроса здесь уже прочитано, а request нужен только ради request.app
    (бот) — обращаться к нему после ответа безопасно. Своё исключение фоновая
    задача обязана съесть сама: наверх его принимать некому."""
    try:
        # Снимки офлайн-визита приезжают ВМЕСТЕ с ним, то есть сегодня: ждём их
        # по дню доставки, а не по дню выезда (day здесь — день выезда).
        await _await_visit_photos(notify_row["contract_id"],
                                  workday.today().isoformat(), pending)
    except Exception as exc:  # noqa: BLE001 — догон не должен съесть рассылку
        logging.warning("photo grace failed: %s", exc)
    ev = (evidence_ctx or {}).get("ev")
    if evidence_ctx and pending:
        # Снимки доехали после вердикта — пересчитать с ними: кадр у двери
        # подтверждает выезд не хуже точки телефона.
        try:
            cur = db.get_client(evidence_ctx["rep"])
            fresh = await asyncio.to_thread(
                _build_evidence, evidence_ctx.get("session_id"), evidence_ctx["rep"],
                cur, day, evidence_ctx)
            if fresh and evidence_ctx.get("op_id"):
                db.update_visit_evidence(evidence_ctx["op_id"], fresh["level"],
                                         evidence.dumps(fresh))
                ev = fresh
        except Exception as exc:  # noqa: BLE001
            logging.warning("evidence refresh failed: %s", exc)
    ev_line = evidence.line(ev)
    try:
        await _notify_group(request, notify_row, uid, actor=actor, visit_day=day,
                            evidence_line=ev_line)
        try:
            await _notify_managers(request, notify_row, dist_m,
                                   geo_far=geo_far, actor=actor, visit_day=day,
                                   evidence_line=ev_line)
        except Exception as e:  # noqa: BLE001 — сбой лички не отменяет беседу
            logging.warning("manager notify failed: %s", e)
        await _notify_report_chats(request, notify_row, vlat, vlon, dist_m,
                                   uid, actor=actor, visit_day=day,
                                   evidence_line=ev_line)
    except Exception as exc:  # noqa: BLE001
        logging.warning("visit notify failed: %s", exc)


async def wait_visit_notify(timeout: float = 30.0) -> None:
    """Дождаться фоновых рассылок (остановка сервера, тесты)."""
    if not _VISIT_NOTIFY_TASKS:
        return
    await asyncio.wait(set(_VISIT_NOTIFY_TASKS), timeout=timeout)


async def _notify_group(request: web.Request, row, uid: int,
                        actor: str | None = None,
                        visit_day: str | None = None,
                        evidence_line: str | None = None) -> None:
    """Пост о визите в «принятую» беседу. Внешний try — на ВСЁ тело: до отправки
    стоят db.log_push и чтение SQLite под busy_timeout 8000 мс; во время импорта
    (одна транзакция на весь файл) это database is locked, а исключение отсюда
    превращало бы сохранённый визит в HTTP 500 — и оболочка на 5xx прекращает
    разбор всей очереди."""
    try:
        emp = row["responsible"] or ""
        cid = row["contract_id"]
        bot = request.app.get("bot")
        if not bot:
            db.log_push(db.PUSH_GROUP, db.PUSH_SKIP_NO_BOT, employee=emp,
                        contract_id=cid)
            return
        from notify import classify_send_error, group_chat_id, post_to_group, promise_line
        chat_id = group_chat_id()
        if not chat_id:
            db.log_push(db.PUSH_GROUP, db.PUSH_SKIP_NO_TARGETS, employee=emp,
                        contract_id=cid, note="беседа не принята")
            return
        lines = [
            "✅ Выезд отработан (миниапп)",
            f"👤 {_esc(row['client'])} ({_esc(cid)})",
            f"🧑‍💼 {_esc(emp) or f'id {uid}'}",
            f"📝 {_esc(row['call_status']) or '—'} · {_esc(row['agreed_payment']) or '—'}",
            f"💬 {_esc(row['visit_comment'] or row['call_comment']) or '—'}",
        ]
        if _cell(row, "tips"):
            lines.append(f"📇 {_esc(_cell(row, 'tips'))}")
        # Кто фактически закрыл — тем же правилом, что в беседах отчётов,
        # иначе каналы расходятся.
        if actor and actor != emp:
            lines.insert(1, f"👤 закрыл: {_esc(actor)} (от имени сотрудника)")
        pline = promise_line(row)
        if pline:
            lines.insert(5 if actor and actor != emp else 4, pline)
        if evidence_line:
            lines.append(evidence_line)
        try:
            await post_to_group(bot, text="\n".join(lines), strict=True)
            db.log_push(db.PUSH_GROUP, db.PUSH_SENT, target_kind="group",
                        target_id=chat_id, employee=emp, contract_id=cid)
        except Exception as exc:  # noqa: BLE001
            status, err = classify_send_error(exc)
            db.log_push(db.PUSH_GROUP, status, target_kind="group",
                        target_id=chat_id, employee=emp, contract_id=cid, error=err)
            logging.warning("group notify failed: chat=%s status=%s err=%s",
                            chat_id, status, err)
    except Exception as exc:  # noqa: BLE001 — пост не должен ломать сохранение
        logging.warning("group notify failed: %s", exc)


async def _notify_report_chats(request: web.Request, row, vlat, vlon,
                               dist_m: int | None = None, uid: int = 0,
                               actor: str | None = None,
                               visit_day: str | None = None,
                               evidence_line: str | None = None) -> None:
    """Отчёт о визите в закреплённые беседы (опциональная фича, настраивается
    пультом): текст по образцу постов сотрудников + фото за день + фактические
    гео-точки (закрытие визита и съёмка фото — такое не подделаешь)."""
    bot = request.app.get("bot")
    if not bot:
        db.log_push(db.PUSH_REPORT_CHAT, db.PUSH_SKIP_NO_BOT,
                    employee=row["responsible"] or "",
                    contract_id=row["contract_id"])
        return
    try:
        chat_ids = db.report_chats_for(row["responsible"] or "")
        if not chat_ids:
            db.log_push(db.PUSH_REPORT_CHAT, db.PUSH_SKIP_NO_TARGETS,
                        employee=row["responsible"] or "",
                        contract_id=row["contract_id"])
            return
        files = day_photos(db.person_contracts(row["contract_id"]),
                           workday.today().isoformat())
        rels = [f"{p.parent.name}/{p.name}" for p in files]
        geo_map = db.photo_geo_for_files(rels)
        # photo_geo с вер. 33 — индекс ВСЕХ снимков: строка есть и без точки.
        # sqlite3.Row истинна по __len__ независимо от NULL — без явной проверки
        # lat галерейный снимок, приложенный последним, отбирал бы точку у
        # камерного и подставлял в беседу ссылку pt=None,None.
        pg = next(((g["lat"], g["lon"]) for r in rels
                   if (g := geo_map.get(r)) is not None and g["lat"] is not None),
                  None)
        from notify import post_visit_report, visit_report_text
        # Кто ФАКТИЧЕСКИ закрыл визит. У выездника это он сам (привязка), у
        # руководителя привязки нет — значит он работал от имени сотрудника,
        # и отчёт обязан это показать: иначе координаты руководителя уедут
        # в гео-контроль как координаты выездника.
        # Подписываем тем же правилом, каким открыт портфель (см. _employee):
        # роль сильнее привязки. Иначе в одном посте оказывались три разных
        # человека — кейс сотрудника A, подпись «выездник B», а на месте был
        # руководитель C.
        if not actor:
            actor = _acting_actor_name(uid) or db.get_binding(uid)
        text = visit_report_text(
            row, dist_m=dist_m,
            visit_geo=(vlat, vlon) if vlat is not None else None,
            photo_geo=pg, photos_n=len(files), actor=actor,
            late_line=_late_visit_line(visit_day),
            evidence_line=evidence_line)
        delivered: list[int] = []

        def _on_result(chat_id, status, error=None):
            db.log_push(db.PUSH_REPORT_CHAT, status, target_kind="chat",
                        target_id=chat_id, employee=row["responsible"] or "",
                        contract_id=row["contract_id"], error=error)
            if status == db.PUSH_SENT:
                delivered.append(chat_id)

        await post_visit_report(bot, chat_ids, text, files, on_result=_on_result)
        # Факт ВЫДАЧИ ПД (фото должника в беседы) — в append-only audit, одной
        # строкой на визит: push_log тримится и уносится сбросом, а на вопрос
        # ИБ «кто получал ПД за квартал» отвечает audit.
        if delivered and files:
            db.audit_access(0, "report_chat_push", row["contract_id"],
                            f"chats={','.join(str(c) for c in delivered)} "
                            f"photos={len(files)}")
    except Exception as exc:  # noqa: BLE001 — пост не должен ломать сохранение визита
        logging.warning("report chats notify failed: %s", exc)


async def _notify_managers(request: web.Request, row, dist_m: int | None = None,
                           *, geo_far: bool | None = None,
                           actor: str | None = None,
                           visit_day: str | None = None,
                           evidence_line: str | None = None) -> None:
    """Реалтайм-пуш: визит закрыт → руководителям этого сотрудника в личку уходит
    сводка + фото за день альбомом. Дедуп повторов из оффлайн-очереди — снаружи
    (is_new от log_visit). Рубильник без редеплоя: settings.visit_push = 0.

    Адресация — db.managers_for_employee (обратный индекс с направленцами и
    сравнением по _name_key): раньше сверка шла с сырым m['employees'], и
    руководители НАПРАВЛЕНИЙ не получали визит-пуш вообще."""
    emp = row["responsible"] or ""
    cid = row["contract_id"]
    bot = request.app.get("bot")
    if not bot:
        db.log_push(db.PUSH_VISIT, db.PUSH_SKIP_NO_BOT, employee=emp,
                    contract_id=cid)
        return
    if not db.push_enabled("visit_push"):
        db.log_push(db.PUSH_VISIT, db.PUSH_SKIP_DISABLED, employee=emp,
                    contract_id=cid, note="visit_push=0")
        return
    if not emp:
        # пустой responsible = открепление, это не сбой
        db.log_push(db.PUSH_VISIT, db.PUSH_SKIP_NO_EMPLOYEE, contract_id=cid)
        return
    targets = db.managers_for_employee(emp)
    # Владелец — не руководитель и в managers его может не быть вовсе, поэтому
    # карточки по визитам ему не приходили никогда и включить их было нечем.
    # Отдельный ключ (по умолчанию выключен) даёт ему тот же выбор, что и
    # руководителям, не заводя лишней роли.
    if (ADMIN_ID and db.push_enabled("owner_visits", "0")
            and all(m["telegram_id"] != ADMIN_ID for m in targets)):
        targets = [*targets, {"telegram_id": ADMIN_ID, "matched_as": emp}]
    if not targets:
        db.log_push(db.PUSH_VISIT, db.PUSH_SKIP_NO_TARGETS, employee=emp,
                    contract_id=cid)
        return
    from notify import classify_send_error, promise_line, send_album
    files = day_photos(db.person_contracts(cid), workday.today().isoformat())
    lines = [f"✅ Выезд отработан",
             f"👤 {_esc(row['client'])} ({_esc(cid)})",
             f"🧑‍💼 {_esc(emp)}",
             f"📝 {_esc(row['call_status']) or '—'} · {_esc(row['agreed_payment']) or '—'}"]
    if actor and actor != emp:
        lines.insert(1, f"👤 закрыл: {_esc(actor)} (от имени сотрудника)")
    pline = promise_line(row)
    if pline:
        lines.append(pline)
    lines.append(f"💬 {_esc(row['visit_comment'] or row['call_comment']) or '—'}")
    if _cell(row, "tips"):
        lines.append(f"📇 {_esc(_cell(row, 'tips'))}")
    lines.append(f"📸 {len(files)} фото" if files else "📸 без фото")
    late = _late_visit_line(visit_day)
    if late:
        lines.append(late)
    text = "\n".join(lines)
    if dist_m is not None:
        pretty = f"{dist_m / 1000:.1f} км" if dist_m >= 1000 else f"{dist_m} м"
        # Вердикт «далеко» — СОХРАНЁННЫЙ на момент визита (тот же источник, что
        # карточка дайджеста): иначе по одному визиту в системе два разных ответа.
        far = geo_far if geo_far is not None else dist_m > db.geo_alert_m()
        text += (f"\n⚠️ Закрыт в {pretty} от адреса!" if far
                 else f"\n📍 На адресе ({pretty} от точки)")
    # Подтверждение по ВСЕМ следам выезда (открытие карточки, точки за время
    # работы, снимки), а не по одной точке закрытия — см. evidence.py.
    if evidence_line:
        text += f"\n{evidence_line}"
    media_ids: list[str] | None = None
    delivered: list[int] = []
    for m in targets:
        tid = m["telegram_id"]
        spelled = m.get("matched_as") or emp
        note = None
        if spelled != emp:
            note = f"name-key: реестр «{emp}» ≠ роль «{spelled}»"
            logging.info("visit push: сматчено по нормализованному ключу — "
                         "реестр «%s», у руководителя %s записано «%s»",
                         emp, tid, spelled)
        try:
            if files:
                res = await send_album(bot, tid, files, text, media_ids=media_ids)
                if res["ok"]:
                    if res["media_ids"] and media_ids is None:
                        media_ids = res["media_ids"]   # второй адресат — без повторной загрузки
                    db.log_push(db.PUSH_VISIT, db.PUSH_SENT, target_kind="manager",
                                target_id=tid, employee=emp, contract_id=cid, note=note)
                    delivered.append(tid)
                else:
                    db.log_push(db.PUSH_VISIT, db.PUSH_FAILED, target_kind="manager",
                                target_id=tid, employee=emp, contract_id=cid,
                                error=res["error"], note=note)
                    continue   # can_dm не трогаем: альбом мог упасть не из-за лички
            else:
                await bot.send_message(tid, text)
                db.log_push(db.PUSH_VISIT, db.PUSH_SENT, target_kind="manager",
                            target_id=tid, employee=emp, contract_id=cid, note=note)
                delivered.append(tid)
        except Exception as exc:  # noqa: BLE001 — пуш не должен ломать сохранение визита
            status, err = classify_send_error(exc)
            db.log_push(db.PUSH_VISIT, status, target_kind="manager",
                        target_id=tid, employee=emp, contract_id=cid,
                        error=err, note=note)
            if status == db.PUSH_BLOCKED:
                try:
                    db.set_can_dm(tid, False)
                except Exception:  # noqa: BLE001
                    logging.warning("set_can_dm(%s, False) не записан", tid)
            logging.warning("manager push failed: tid=%s status=%s err=%s",
                            tid, status, err)
            continue
        # set_can_dm ВНЕ try отправки: его сбой (database is locked) иначе
        # классифицировался бы как отказ Телеграма, и по доставленному пушу в
        # журнале лежали бы и sent, и failed.
        try:
            db.set_can_dm(tid, True)
        except Exception:  # noqa: BLE001
            logging.warning("set_can_dm(%s, True) не записан", tid)
    # Факт ВЫДАЧИ ПД (фото должника в личку) — одной строкой на визит в audit:
    # push_log — телеметрия, он тримится и целиком уносится сбросом.
    if delivered and files:
        db.audit_access(0, "visit_push", cid,
                        f"tids={','.join(str(t) for t in delivered)} "
                        f"photos={len(files)}")


# --- «Карта дня» руководителя (подписанная ссылка из дайджеста) --------------
def _map_auth(request: web.Request) -> tuple[str, str]:
    q = request.query
    emp, d = q.get("e", ""), q.get("d", "")
    if not maplink.verify(emp, d, q.get("x", ""), q.get("s", "")):
        raise web.HTTPForbidden(
            text="Ссылка устарела. Откройте карту заново из дайджеста в боте.")
    return emp, d


# --- Вкладка руководителя в приложении (/api/manager/*) ----------------------
# Право проверяется на КАЖДОМ запросе (_manager_gate): спрятанная в JS кнопка
# ничего не защищает. Адресация по ФИО и rep-договору — индексы ei/ci из
# телеграм-callback наружу не выходят (у направленца состав живой, индексы
# поехали бы между запросами). ADMIN-байпас принадлежности кейса из _person
# здесь НЕ повторяется: у владельца нужный сотрудник и так есть в team.
async def _manager_summary(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    iso, today, prev, nxt = _manager_date(request)
    team = _day_team(mgr, team, iso)
    data = digest_data.summary_data(mgr, iso, employees=team)
    # 'ei' — индекс телеграм-callback, во вкладке он не нужен и провоцирует
    # клиент на него опереться.
    for r in data.get("display", []):
        r.pop("ei", None)
    for g in data.get("groups", []):
        for r in g.get("employees", []):
            r.pop("ei", None)
    db.audit_access(uid, "digest_view",
                    detail=f"summary {iso} team={len(team)} "
                           f"persons={data['totals']['persons']}")
    return _manager_json({**_manager_envelope(mgr, team, iso, today, prev, nxt, uid),
                          **data})


async def _manager_employee_view(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    iso, today, prev, nxt = _manager_date(request)
    team = _day_team(mgr, team, iso)
    emp = _manager_employee_q(request, team)
    data = digest_data.employee_data(mgr, iso, emp, employees=team)
    if data is None:
        raise web.HTTPNotFound(text="employee")
    # Аудит с СОСТАВОМ показанных должников: для ИБ «открывал экран» и «видел
    # этих людей» — разные вещи (до 50 rep, дальше усечение).
    reps = ",".join(c["rep"] for c in data["cases"][:50])
    db.audit_access(uid, "digest_view", target=emp,
                    detail=f"employee {iso} n={len(data['cases'])} reps={reps}")
    return _manager_json({**_manager_envelope(mgr, team, iso, today, prev, nxt, uid),
                          **data})


async def _manager_person(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    iso, today, prev, nxt = _manager_date(request)
    team = _day_team(mgr, team, iso)
    emp = _manager_employee_q(request, team)
    rep = (request.query.get("rep") or "").strip()
    row = db.get_client(rep)
    by_soft = bool(row and (request.query.get("mode") == "soft" or
                           row["responsible_soft"] == emp and row["responsible"] != emp))
    # Свой должник — или тот, к кому сотрудник выезжал до перезакрепления:
    # карточка по собственному выезду открываться обязана.
    if not row or (by_soft and row["responsible_soft"] != emp and not db.employee_called(emp, [rep])) or (
            not by_soft and row["responsible"] != emp and not db.employee_visited(emp, [rep])):
        raise web.HTTPNotFound(text="person")
    data = digest_data.person_data(mgr, iso, emp, rep, employees=team, by_soft=by_soft)
    if data is None:
        raise web.HTTPNotFound(text="person")
    db.audit_access(uid, "digest_view_card", target=rep, detail=f"{emp} {iso}")
    return _manager_json({**_manager_envelope(mgr, team, iso, today, prev, nxt, uid),
                          "employee": emp, **data})


async def _manager_period(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    iso, today, prev, nxt = _manager_date(request)
    try:
        days = int(request.query.get("days", "7"))
    except (TypeError, ValueError):
        days = 7
    span = days if days in (7, 30) else 7
    start = (db._as_date(iso) - timedelta(days=span - 1)).isoformat()
    team = digest_data.window_team(mgr, team, start, iso)
    data = digest_data.period_data(mgr, iso, days, employees=team)
    db.audit_access(uid, "digest_view", detail=f"period {iso} {data['days']}")
    return _manager_json({**_manager_envelope(mgr, team, iso, today, prev, nxt, uid),
                          **data})


async def _manager_promises(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    iso, today, prev, nxt = _manager_date(request)
    data = digest_data.promises_data(mgr, iso, employees=team)
    reps = ",".join(i["rep"] or "" for e in data["employees"]
                    for i in e["items"][:50])[:400]
    db.audit_access(uid, "digest_view",
                    detail=f"promises {iso} n={data['totals']['persons']} reps={reps}")
    return _manager_json({**_manager_envelope(mgr, team, iso, today, prev, nxt, uid),
                          **data})


def _stats_range_q(request: web.Request) -> dict:
    """Окно статистики из query: preset+shift либо from/to (+shift). Кривая
    дата или сдвиг — 400, как и у _manager_date: клиент тогда откатывается на
    «сегодня», а не показывает пустой экран."""
    q = request.query
    try:
        shift = int(q.get("shift") or 0)
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="shift")
    frm, to = (q.get("from") or "").strip(), (q.get("to") or "").strip()
    if (frm and db._as_date(frm) is None) or (to and db._as_date(to) is None):
        raise web.HTTPBadRequest(text="date")
    return digest_data.stats_range(q.get("preset"), shift, frm or None, to or None)


def _stats_envelope(mgr, team, rng, uid) -> dict:
    """Конверт статистики: дата/соседи — по концу окна, чтобы карточка
    человека и карта дня из этого экрана открывались теми же ручками."""
    today = rng["today"]
    return _manager_envelope(mgr, team, rng["to"], today, rng["prev"]["to"],
                             None if rng["to"] >= today else rng["next"]["to"], uid)


# Свод команды — это проход по портфелю каждого сотрудника: на 63 людях он
# занимает около шести секунд. В цикле событий это шесть секунд, на которые
# встаёт ВЕСЬ сервер: выездник в поле в этот момент не может ни открыть
# карточку, ни сохранить выезд. Та же ошибка уже была с индексом приоритета
# (9 сентября) — считаем в отдельном потоке, соединение SQLite у него своё.
async def _manager_stats(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    data = await asyncio.to_thread(
        functools.partial(digest_data.stats_data, mgr, rng, employees=team))
    db.audit_access(uid, "digest_view",
                    detail=f"stats {rng['from']}..{rng['to']} team={len(team)} "
                           f"visits={data['totals']['visits']}")
    # Счётчик висящих заявок на вход — прямо на сводке. 14.09.2026 две заявки
    # провисели день: увидеть их было негде, кроме как у владельца в пульте.
    return _manager_json({**_stats_envelope(mgr, team, rng, uid), **data,
                          "bind_requests": len(db.team_bindings(team)["pending"])})


async def _manager_stats_employee(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    emp = _manager_employee_q(request, team)
    data = await asyncio.to_thread(
        functools.partial(digest_data.stats_employee_data, mgr, rng, emp,
                          employees=team))
    if data is None:
        raise web.HTTPNotFound(text="employee")
    reps = ",".join(c["rep"] for c in data["cases"][:50])
    db.audit_access(uid, "digest_view", target=emp,
                    detail=f"stats_employee {rng['from']}..{rng['to']} "
                           f"n={len(data['cases'])} reps={reps}")
    return _manager_json({**_stats_envelope(mgr, team, rng, uid), **data})


async def _manager_pushes(request: web.Request) -> web.Response:
    """Личные рубильники рассылок: GET — состояние, POST {ключ: bool} — переключить.

    Руководитель меняет их сам из приложения/пульта — ровно те три, что приходят
    ему в телеграм. Рубильники владельца живут в другом месте (пульт аналитика,
    «Каналы») и через эту ручку не меняются: молчаливый «успех» без эффекта
    хуже честного отказа.
    """
    uid, mgr, team = _manager_gate(request)
    if uid == ADMIN_ID:
        return web.json_response(
            {"error": "Рубильники владельца меняются в пульте аналитика "
                      "(раздел «Каналы»), не здесь."}, status=400)
    if request.method == "POST":
        body = await _json_object(request)
        if body is None:
            return web.json_response({"error": "тело не JSON-объект"}, status=400)
        pushes = {k: bool(body[k]) for k in db.MANAGER_PUSH_KEYS if k in body}
        if not pushes:
            return web.json_response({"error": "нет известных рубильников"},
                                     status=400)
        db.set_manager_pushes(uid, pushes)
        who = mgr.get("name") or f"id{uid}"
        db.audit_access(uid, "manager_pushes",
                        detail="{} · {}".format(
                            who, ",".join(f"{k}={int(v)}"
                                          for k, v in pushes.items())))
    row = db.get_manager(uid) or {}
    return web.json_response(
        {"pushes": {k: bool(row.get(k, True)) for k in db.MANAGER_PUSH_KEYS}})


def _foreign_privileged(uid: int, tid: int | None) -> bool:
    """Вход другого руководителя или владельца. Руководитель, который сам возит,
    привязан к своему ФИО, и это ФИО может стоять в чужой команде (у руководителя
    направления, у «всей СНГ»). Токен такого входа несёт права того руководителя:
    его команду, выдачу ссылок, имя в аудите. Поэтому через ручку руководителя
    такой вход не выдаётся, не отзывается и не показывается его код. Выдаёт его
    аналитик в своём пульте."""
    return tid is not None and tid != uid and (tid == ADMIN_ID or db.is_manager(tid))


def _mask_code(code: str | None) -> str:
    """Живой код входа — это пропуск: в журналы пишется только начало."""
    return f"{code[:3]}…" if code else "-"


async def _json_object(request: web.Request) -> dict | None:
    """Тело запроса как JSON-объект; список, строка или мусор — None (→ 400, не 500)."""
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


# Метка «свой вход руководителя» в ручках выдачи. С ФИО не совпадёт никогда:
# ФИО не начинаются с «!» (та же договорённость, что у digest_data.NO_OFFICE).
MANAGER_SELF = "!me"
MANAGER_SELF_LABEL = "Вы — вход руководителя"
MANAGER_LINK_USES = 3


def _manager_self_row(uid: int, base: str) -> dict:
    """Строка собственного входа руководителя для «Вход команды».

    До 25.09.2026 её не было: руководитель с новым телефоном жал «Выдать вход»
    на своей же строке ВЫЕЗДНИКА (он и сам возит) и получал вход выездника —
    портфель есть, «Сводки» нет. Свой вход руководителя выдавал только
    аналитик. Эта строка выдаёт вход на ЕГО id — с ролью и «Сводкой»."""
    state = next((dict(r) for r in db.app_links_state()
                  if r["telegram_id"] == uid), None)
    row = state or {
        "telegram_id": uid, "kind": "agent", "code": None, "created_at": None,
        "expires_at": None, "uses": 0, "max_uses": db.APP_LINK_DEFAULT_USES,
        "bonus_ios": False, "last_use_at": None, "last_agent": None,
        "entered": False, "devices": 0, "alive": False}
    row["employee"] = MANAGER_SELF
    row["label"] = MANAGER_SELF_LABEL
    row["url"] = f"{base}/i/{row['code']}" if base and row.get("code") else ""
    return row


async def _manager_app_links(request: web.Request) -> web.Response:
    """Входы команды руководителя: строка на человека, как в пульте аналитика.

    Выдача ссылок теперь работа руководителя: аналитик не пересыльщик. Список —
    только своя команда, включая тех, кому ссылку ещё не выдавали («не выдавали»).
    """
    uid, mgr, team = _manager_gate(request)
    team_set = set(team)
    # Руководитель может быть и выездником: своя строка входа видна всегда,
    # даже если своего ФИО в составе команды нет.
    own = db.get_binding(uid)
    if own:
        team_set.add(own)
    base = os.getenv("MINIAPP_URL", "").strip().rstrip("/")
    rows = []
    by_employee: dict[str, dict] = {}
    for row in db.app_links_state():
        if row["employee"] in team_set:
            row = dict(row)
            if _foreign_privileged(uid, row.get("telegram_id")):
                row["code"] = None
            row["url"] = f"{base}/i/{row['code']}" if base and row.get("code") else ""
            rows.append(row)
            by_employee[row["employee"]] = row
    for name in team:
        if name in by_employee:
            continue
        rows.append({
            "employee": name, "telegram_id": None, "kind": "agent",
            "code": None, "url": "", "created_at": None, "expires_at": None,
            "uses": 0, "max_uses": db.APP_LINK_DEFAULT_USES, "bonus_ios": False,
            "last_use_at": None, "last_agent": None,
            "entered": False, "devices": 0, "alive": False,
        })
    rows.sort(key=lambda r: (not r["entered"], r["employee"]))
    return web.json_response({
        "screen": "team_links", "ok": True,
        "manager": {"name": mgr.get("name") or "", "scope": mgr.get("scope") or ""},
        "links": rows,
        # Свой вход руководителя — отдельно от команды: он выдаётся на его id
        # (роль и «Сводка»), а не на ФИО выездника.
        "me": _manager_self_row(uid, base),
    })


async def _manager_app_link(request: web.Request) -> web.Response:
    """Выдать ссылку входа сотруднику СВОЕЙ команды — руководитель пересылает сам.

    Механика та же, что у аналитика: короткая ``<MINIAPP_URL>/i/<код>``, окно и
    число обменов — серверные. Чужое ФИО не проходит: граница команды проверяется
    на сервере, а не в интерфейсе.
    """
    uid, mgr, team = _manager_gate(request)
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "тело не JSON-объект"}, status=400)
    employee = str(body.get("employee") or "").strip()
    if employee == MANAGER_SELF:
        # Свой вход руководителя — на его id, с ролью: новый телефон, второй
        # компьютер. Ссылка та же, что выдаёт аналитик в «Руководителях».
        base = os.getenv("MINIAPP_URL", "").strip().rstrip("/")
        if not base:
            return web.json_response(
                {"error": "MINIAPP_URL не задан на сервере: без публичного адреса "
                          "ссылку входа не собрать"}, status=409)
        # Три обмена: телефон, ярлык на экране iPhone (своё хранилище) и
        # компьютер с пультом — иначе двойка кончалась бы на втором устройстве.
        code = db.create_app_link(uid, max_uses=MANAGER_LINK_USES)
        who = mgr.get("name") or f"id{uid}"
        db.audit_access(uid, "mgr_app_link_self",
                        detail=f"{who} выдал себе вход: {_mask_code(code)}")
        return web.json_response({
            "ok": True, "employee": MANAGER_SELF_LABEL, "telegram_id": uid,
            "code": code, "url": f"{base}/i/{code}",
            "expires_at": db.app_link_expires_at(
                datetime.now().isoformat(timespec="seconds")),
            "uses": MANAGER_LINK_USES,
        })
    own = db.get_binding(uid)
    allowed = set(team) | ({own} if own else set())
    if not employee or employee not in allowed:
        return web.json_response(
            {"error": "Это не ваш сотрудник: ссылку можно выдать только своей "
                      "команде или себе."},
            status=403)
    if _foreign_privileged(uid, db.bound_identity(employee)):
        return web.json_response(
            {"error": "Это вход другого руководителя: его выдаёт аналитик "
                      "в своём пульте."},
            status=403)
    base = os.getenv("MINIAPP_URL", "").strip().rstrip("/")
    if not base:
        return web.json_response(
            {"error": "MINIAPP_URL не задан на сервере: без публичного адреса "
                      "ссылку входа не собрать"}, status=409)
    kind = "courier" if body.get("kind") == "courier" else "agent"
    tid, _created = db.local_identity_for(employee, kind)
    code = db.create_app_link(tid, max_uses=db.APP_LINK_DEFAULT_USES)
    who = mgr.get("name") or f"id{uid}"
    db.audit_access(uid, "mgr_app_link", target=employee,
                    detail=f"{who} выдал вход: {_mask_code(code)}")
    return web.json_response({
        "ok": True, "employee": employee, "telegram_id": tid,
        "code": code, "url": f"{base}/i/{code}",
        "expires_at": db.app_link_expires_at(datetime.now().isoformat(timespec="seconds")),
        "uses": db.APP_LINK_DEFAULT_USES,
    })


async def _manager_app_revoke(request: web.Request) -> web.Response:
    """Отозвать вход сотрудника своей команды: ссылки гаснут, телефон выходит."""
    uid, mgr, team = _manager_gate(request)
    body = await _json_object(request) or {}
    employee = str(body.get("employee") or "").strip()
    own = db.get_binding(uid)
    allowed = set(team) | ({own} if own else set())
    if not employee or employee not in allowed:
        return web.json_response(
            {"error": "Это не ваш сотрудник: отзывать можно только свою "
                      "команду или себя."},
            status=403)
    tids = [r["telegram_id"] for r in db.app_links_state()
            if r["employee"] == employee and r["telegram_id"]]
    if not tids:
        return web.json_response({"error": "У этого человека нет выданных входов."})
    if any(_foreign_privileged(uid, tid) for tid in tids):
        return web.json_response(
            {"error": "Это вход другого руководителя: отзывает его аналитик "
                      "в своём пульте."},
            status=403)
    scope = "links" if str(body.get("scope") or "all") == "links" else "all"
    revoked = sum(db.revoke_app_access(tid, scope=scope) for tid in tids)
    who = mgr.get("name") or f"id{uid}"
    db.audit_access(uid, "mgr_app_revoke", target=employee,
                    detail=f"{who} отозвал вход ({scope}): {revoked}")
    return web.json_response({"ok": True, "employee": employee,
                              "revoked": revoked, "scope": scope})


async def _manager_plan(request: web.Request) -> web.Response:
    """Нормы выездов команды: GET — список, POST {plan:{ФИО: норма}} — записать.

    Правит РУКОВОДИТЕЛЬ прямо из приложения: офис делит выездников на группы
    8/10/12 по фактической выработке, состав групп меняется чаще, чем выходят
    выкладки. Чужих людей через эту ручку не тронуть — только свою команду.
    """
    uid, mgr, team = _manager_gate(request)
    if request.method == "POST":
        body = await request.json()
        plan = body.get("plan")
        if not isinstance(plan, dict):
            raise web.HTTPBadRequest(text="plan")
        allowed = set(team)
        changed = []
        for employee, norm in plan.items():
            name = str(employee).strip()
            if name not in allowed:
                # Не 403 на весь запрос: руководитель мог сохранять список,
                # пока состав команды менялся. Пишем своих, чужих пропускаем.
                continue
            db.set_visit_plan(name, norm)
            changed.append(name)
        db.audit_access(uid, "visit_plan", detail=f"changed={len(changed)}")
    plan_map = db.visit_plan_map()
    common = db.visit_plan_per_day()
    # Норма по расчёту (workload.py) — подсказка рядом с ручной: по географии
    # портфеля и измеренному времени у адреса. Считается в потоке: дорожная
    # матрица на команду — не для цикла событий.
    rng = digest_data.stats_range("30d", 0)
    suggested = await asyncio.to_thread(
        workload.suggested_map, team, rng["from"], rng["to"],
        backends=_routing_backends(), vroom_url=VROOM_URL)
    return _manager_json({
        "ok": True, "screen": "plan", "common": common,
        "groups": VISIT_PLAN_GROUPS,
        "employees": [{"employee": e, "plan": plan_map.get(e) or common,
                       "personal": e in plan_map,
                       "suggested": (suggested.get(e) or {}).get("norm"),
                       "why": (suggested.get(e) or {}).get("why", "")} for e in team],
    })


async def _manager_workload(request: web.Request) -> web.Response:
    """Нагрузка команды: норма по расчёту против ручной, темп, разбор дней
    одного сотрудника (?employee=)."""
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    refresh = (request.query.get("refresh") or "") == "1"
    emp = (request.query.get("employee") or "").strip()
    if emp:
        if emp not in team:
            raise web.HTTPNotFound(text="employee")
        data = await asyncio.to_thread(
            functools.partial(workload.employee_data, emp, rng,
                              backends=_routing_backends(), vroom_url=VROOM_URL))
    else:
        data = await asyncio.to_thread(
            functools.partial(workload.workload_data, mgr, rng, team,
                              backends=_routing_backends(), vroom_url=VROOM_URL,
                              refresh=refresh))
    db.audit_access(uid, "digest_view",
                    detail=f"workload {rng['from']}..{rng['to']} emp={emp or '-'}")
    return _manager_json({**_stats_envelope(mgr, team, rng, uid), **data})


# --- Кто есть кто: аккаунты команды правит руководитель ----------------------
# 14.09.2026 три человека подряд зашли под чужим ФИО: самовыбор одноразовый,
# список строится из реестра, соседние фамилии похожи. До починки человек
# работает в ЧУЖОМ портфеле — его выезды ложатся на чужие договоры, — а починить
# это мог только владелец за пультом, то есть не в поле и не в тот же час.
# Чинит теперь руководитель: он единственный, кто знает своих людей в лицо.
#
# Границы жёсткие: и ФИО, и сам аккаунт обязаны принадлежать команде. Без
# второго условия со своего экрана можно было бы снять устройство человека из
# чужой команды, зная только его telegram_id. Аккаунт без строки в agent_bind
# руководителю не виден вовсе — «чужого» из воздуха не создать.
_BIND_ACTIONS = ("approve", "reassign", "unbind")


def _binds_payload(team: list[str]) -> dict:
    data = db.team_bindings(team)
    return {"ok": True, "screen": "binds",
            "employees": [{"employee": e, "accounts": data["bound"].get(e, [])}
                          for e in team],
            "pending": data["pending"]}


async def _tell_bind_changed(request: web.Request, uid: int, key: str, **kw) -> None:
    """Сказать человеку, что с его доступом произошло. Телеграм в контуре
    необязателен: без бота (и у входа без телеграма) молчим — сообщение это
    вежливость, а не условие привязки."""
    bot = request.app.get("bot")
    if not bot or not uid or db.is_local_uid(uid):
        return
    try:
        await bot.send_message(uid, t(key, lang_of(uid), **kw))
    except Exception:  # noqa: BLE001
        pass


async def _manager_binds(request: web.Request) -> web.Response:
    """GET — состав команды с аккаунтами и отдельный список заявок.
    POST {action, telegram_id[, employee]} — approve | reassign | unbind.

    approve — одобрить заявку. Это И ЕСТЬ смена устройства: правило «одно ФИО —
    одно устройство» остаётся, поэтому прежний телефон теряет и привязку, и
    токены приложения. Клиент об этом предупреждает до нажатия — он видит, занято
    ли ФИО.

    reassign — переставить аккаунт на другое ФИО (тот самый промах при
    самовыборе). Занятое ФИО отказываем НАЗЫВАЯ причину, а не вытесняем молча:
    вытеснение — осознанное действие, у него своя кнопка.

    unbind — снять привязку (и отклонить заявку: строка одна и та же). Доступ
    приложения закрывается вместе с ней — иначе девайс-токен жил бы ещё 30 дней.

    Отказ по существу («ФИО занято») отдаётся 200 + ok:false + error и свежим
    списком: руководителю нужен текст на экране, а не номер статуса. Кодами
    остаются кривой запрос (400) и чужой человек (404).
    """
    uid, mgr, team = _manager_gate(request)
    if request.method != "POST":
        db.audit_access(uid, "digest_view", detail=f"binds team={len(team)}")
        return _manager_json(_binds_payload(team))
    body = await request.json()
    action = str(body.get("action") or "").strip()
    if action not in _BIND_ACTIONS:
        raise web.HTTPBadRequest(text="action")
    try:
        tid = int(body.get("telegram_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="telegram_id")
    row = db.binding_of(tid)
    if row is None or row["employee"] not in team:
        # 404, как у _manager_employee_q: 403 остаётся одним смыслом «роли нет»
        # (по нему клиент гасит вкладку целиком).
        raise web.HTTPNotFound(text="account")
    if _foreign_privileged(uid, tid):
        # Руководитель, который сам возит, привязан к ФИО из чужой команды.
        # Снять, переставить или одобрить его вход со своего экрана значило бы
        # выключить коллегу (вместе с токенами приложения) — как и в ссылочных
        # ручках, такой вход трогает только аналитик в пульте.
        raise web.HTTPNotFound(text="employee")
    was, kind = row["employee"], (row["kind"] or "agent")
    error = None
    if action == "unbind":
        db.unbind(tid)
        db.audit_access(uid, "mgr_unbind", str(tid),
                        f"{was} pending={0 if row['approved'] else 1}")
        if not row["approved"]:
            await _tell_bind_changed(request, tid, "bind_rejected_user")
    elif action == "approve":
        if row["approved"]:
            error = "заявки нет: этот аккаунт уже работает"
        elif any(_foreign_privileged(uid, a["telegram_id"])
                 for a in db.team_bindings([was])["bound"].get(was, [])):
            # Одобрение вытесняет прежние устройства этого ФИО (set_bind +
            # unbind). Если среди них вход другого руководителя — это тот же
            # случай, что выше, только через заявку постороннего.
            error = ("под этим ФИО работает вход другого руководителя — "
                     "сменить ему устройство может аналитик в пульте")
        else:
            prev = db.employee_taken_by(was, exclude_telegram_id=tid)
            db.set_bind(tid, was, kind)
            if prev:
                # Привязку прежнего устройства set_bind уже убрал; здесь
                # закрываем ему и вход в приложение — «сменили устройство»
                # значит сменили, а не «теперь работают оба».
                db.unbind(prev)
            db.audit_access(uid, "mgr_approve_bind", str(tid),
                            f"{was} prev={prev or '—'}")
            await _tell_bind_changed(request, tid, "bind_approved_user", emp=was)
    else:
        target = (body.get("employee") or "").strip()
        if target not in team:
            raise web.HTTPNotFound(text="employee")
        taken = db.employee_taken_by(target, exclude_telegram_id=tid)
        if taken:
            error = f"за «{target}» уже закреплено устройство — сначала снимите его"
        elif target == was and row["approved"]:
            error = "этот аккаунт уже работает под этим ФИО"
        else:
            db.set_bind(tid, target, kind)
            db.audit_access(uid, "mgr_rebind", str(tid), f"{was} -> {target}")
            await _tell_bind_changed(request, tid, "bind_approved_user", emp=target)
    payload = _binds_payload(team)
    if error:
        payload["ok"], payload["error"] = False, error
    return _manager_json(payload)


async def _manager_cases(request: web.Request) -> web.Response:
    """Случаи за окно по одной плашке сводки: тап по числу — и вот из чего оно.

    employee сужает до одного человека: ступень воронки в карточке сотрудника
    открывает его выезды, а не всей команды."""
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    metric = (request.query.get("metric") or "visits").strip()
    country = (request.query.get("country") or "").strip()[:8]
    office = (request.query.get("office") or "").strip()[:64]
    employee = ((request.query.get("employee") or "").strip()
                if "employee" in request.query else "")
    if employee and employee not in team:
        raise web.HTTPNotFound(text="employee")
    data = await asyncio.to_thread(
        functools.partial(digest_data.cases_data, mgr, rng, metric, country,
                          employees=team, office=office, employee=employee))
    reps = ",".join(c["rep"] for c in data["cases"][:50])
    db.audit_access(uid, "digest_view",
                    detail=f"cases {data['metric']} {rng['from']}..{rng['to']} "
                           f"country={country or '-'} employee={employee or '-'} "
                           f"n={data['total']} reps={reps}")
    return _manager_json({**_stats_envelope(mgr, team, rng, uid), **data})


async def _manager_stats_xlsx(request: web.Request) -> web.Response:
    """Та же статистика книгой Excel. Токен — только в заголовке (как у фото):
    ссылка с токеном осела бы в истории браузера."""
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    body = await asyncio.to_thread(
        functools.partial(digest_data.stats_xlsx, mgr, rng, employees=team))
    db.audit_access(uid, "digest_export",
                    detail=f"stats.xlsx {rng['from']}..{rng['to']} team={len(team)}")
    return web.Response(
        body=body,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename=stats_{rng['from']}_{rng['to']}.xlsx",
                 "Cache-Control": "no-store"})


# --- Пульт руководителя на компьютере (Пульты 2, 29.09.2026) -------------------
# Вход ОДИН раз: ссылка входа руководителя (ключ) меняется на токен этого
# компьютера, дальше пульт открывается сразу. Токен живёт до отзыва; отозванный
# удаляется при первом же стуке, и пульт стирает у себя всё.
_LINK_CODE_RE = re.compile(r"/i/([A-Za-z0-9_-]{6,})|link=([A-Za-z0-9_-]{6,})"
                           r"|^([A-Za-z0-9_-]{6,})$")


def _link_code(raw: str) -> str:
    m = _LINK_CODE_RE.search((raw or "").strip())
    return next((g for g in (m.groups() if m else ()) if g), "")


def _device_me(uid: int, mgr: dict) -> dict:
    owner = uid == ADMIN_ID
    return {"me": {"name": mgr.get("name") or "",
                   "role": "full" if owner else db.manager_role(mgr.get("role")),
                   "direction": mgr.get("direction") or "",
                   "office_cities": mgr.get("office_cities") or [],
                   "scope": digest_data.scope_label(mgr), "isOwner": owner},
            "caps": db.manager_caps(mgr.get("role"), is_owner=owner),
            "today": digest_data.today_iso(),
            "server_time": datetime.now(evidence.FIELD_TZ).isoformat(timespec="seconds")}


async def _manager_device_enroll(request: web.Request) -> web.Response:
    """{code, device} → {token, device_id, me, caps}. Код — из ссылки входа
    руководителя (целиком или один код). Обмен тратит одно её использование."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    code = _link_code(str(body.get("code") or ""))
    device = str(body.get("device") or "").strip()[:80]
    _admin_too_many_fails(request)
    uid = db.peek_app_link(code, desktop=True) if code else None
    if not uid:
        _admin_auth_failed(request, scope="app")
        # Причина — чтобы пульт подсказал путь («Выдать себе» в приложении),
        # а не одно «устарела». Код 72-битный: знание «был/кончился» перебору
        # не помогает, а сам перебор считает общий счётчик неудач.
        raise _json_error(web.HTTPGone, "link_dead",
                          reason=db.app_link_status(code) if code else "unknown")
    mgr = digest_data.manager_scope(uid, is_owner=(uid == ADMIN_ID))
    if mgr is None:
        # Ссылка живая, но это вход выездника: не тратим её — пусть работает
        # там, для чего выдана.
        raise _json_error(web.HTTPForbidden, "not_manager")
    got = db.enroll_manager_device(code, device)
    if not got:
        raise _json_error(web.HTTPGone, "link_dead", reason=db.app_link_status(code))
    _admin_auth_ok(request, scope="app")
    uid, dev_id, token = got
    db.audit_access(uid, "pult_enroll", detail=f"#{dev_id} {device}")
    return _manager_json({"ok": True, "token": token, "device_id": dev_id,
                          **_device_me(uid, mgr)})


async def _manager_device_me(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    device = None
    ptok = (request.headers.get("X-Pult-Token") or "").strip()
    if ptok:
        _, dev = db.manager_device_check(ptok)
        if dev:
            device = {"id": dev["id"], "name": dev["device"] or "",
                      "created_at": dev["created_at"]}
    return _manager_json({"ok": True, "device": device, "team": len(team),
                          **_device_me(uid, mgr)})


async def _manager_device_logout(request: web.Request) -> web.Response:
    tok = (request.headers.get("X-Pult-Token") or "").strip()
    uid = db.drop_manager_device(tok) if tok else None
    if uid:
        db.audit_access(uid, "pult_logout")
    return web.json_response({"ok": True})


async def _manager_activity(request: web.Request) -> web.Response:
    """Лента: выезды с пометками, открытия карточек, фото, входы — за окно."""
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    q = request.query
    kinds = {k for k in (q.get("kinds") or "").split(",") if k} or None
    try:
        limit = int(q.get("limit") or 200)
    except ValueError:
        limit = 200
    data = await asyncio.to_thread(functools.partial(
        control.activity, mgr, team, rng, office=(q.get("office") or "").strip(),
        employee=(q.get("employee") or "").strip(), kinds=kinds,
        before=(q.get("before") or "").strip(), limit=limit))
    db.audit_access(uid, "digest_view",
                    detail=f"activity {rng['from']}..{rng['to']} n={len(data['events'])}")
    return _manager_json(data)


async def _control_overview(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    data = await asyncio.to_thread(functools.partial(
        control.overview, mgr, team, rng, office=(request.query.get("office") or "").strip()))
    db.audit_access(uid, "control_view", detail=f"overview {rng['from']}..{rng['to']}")
    return _manager_json(data)


async def _control_soft(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    data = await asyncio.to_thread(functools.partial(
        control.activity, mgr, team, rng, kinds={"call"},
        office=(request.query.get("office") or "").strip(),
        before=(request.query.get("before") or "").strip(), limit=200))
    db.audit_access(uid, "control_view", detail=f"soft {rng['from']}..{rng['to']}")
    return _manager_json(data)


async def _control_assignments(request: web.Request) -> web.Response:
    """Журнал закреплений: кому приехали договоры и чьи это были (контур СБ)."""
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    data = await asyncio.to_thread(control.assignments, mgr, team, rng)
    db.audit_access(uid, "control_view",
                    detail=f"assignments {rng['from']}..{rng['to']}")
    return _manager_json(data)


async def _control_access(request: web.Request) -> web.Response:
    """Лента выдачи входов: кто кому выдал/отозвал доступ (контур СБ)."""
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    data = await asyncio.to_thread(control.access_log, rng, team)
    db.audit_access(uid, "control_view", detail=f"access {rng['from']}..{rng['to']}")
    return _manager_json(data)


async def _control_day(request: web.Request) -> web.Response:
    """Гео-запись одного дня по времени: отрезки движения, выезды, SOS."""
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    emp = _manager_employee_q(request, team)
    day = (request.query.get("date") or rng["to"]).strip()
    data = await asyncio.to_thread(control.day_story, mgr, team, emp, day)
    if data is None:
        raise web.HTTPNotFound(text="employee")
    db.audit_access(uid, "control_view", target=emp, detail=f"day {day}")
    return _manager_json(data)


async def _control_geo_days(request: web.Request) -> web.Response:
    """Дни окна, по которым у сотрудника есть гео-запись или выезды."""
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    emp = _manager_employee_q(request, team)
    data = await asyncio.to_thread(control.geo_days, mgr, team, rng, emp)
    db.audit_access(uid, "control_view", target=emp,
                    detail=f"geo_days {rng['from']}..{rng['to']}")
    return _manager_json(data)


# --- «Контроль» для пульта аналитика: вся компания, вход устройства пульта --------
# Те же правила и функции, что у безопасника, но состав — ВСЕ сотрудники реестра:
# админ смотрит поверх границ команд. Право — токен устройства пульта (поимённо),
# как у остальных админ-ручек; каждый просмотр — в журнал.
def _all_employees() -> list[str]:
    with db.connect() as conn:
        return sorted({r["responsible"] for r in conn.execute(
            "SELECT DISTINCT responsible FROM clients "
            "WHERE TRIM(COALESCE(responsible,'')) <> '' UNION "
            "SELECT DISTINCT responsible_soft FROM clients "
            "WHERE TRIM(COALESCE(responsible_soft,'')) <> ''")})


async def _admin_control(request: web.Request, kind: str) -> web.Response:
    _admin(request)
    rng = _stats_range_q(request)
    if kind == "callbacks":
        iso, today, prev, nxt = _manager_date(request)
        data = await asyncio.to_thread(functools.partial(
            control.callbacks, None, _all_employees(), iso,
            office=(request.query.get("office") or "").strip()))
        db.audit_access(ADMIN_ID, "control_view", detail=f"admin callbacks {iso}")
        return web.json_response(digest_data._public(
            {**data, "prev_date": prev, "next_date": nxt, "today": today}))
    if kind == "flags":
        emp = (request.query.get("employee") or "").strip()
        data = await asyncio.to_thread(control.flags_of, None, _all_employees(), rng, emp)
        if data is None:
            raise web.HTTPNotFound(text="employee")
    elif kind == "soft":
        data = await asyncio.to_thread(functools.partial(
            control.activity, None, _all_employees(), rng, kinds={"call"},
            office=(request.query.get("office") or "").strip(),
            before=(request.query.get("before") or "").strip(), limit=200))
    elif kind == "assignments":
        data = await asyncio.to_thread(control.assignments, None, _all_employees(), rng)
    elif kind == "access":
        data = await asyncio.to_thread(control.access_log, rng)
    elif kind == "day":
        emp = (request.query.get("employee") or "").strip()
        day = (request.query.get("date") or rng["to"]).strip()
        data = await asyncio.to_thread(control.day_story, None, _all_employees(), emp, day)
        if data is None:
            raise web.HTTPNotFound(text="employee")
    elif kind == "geo_days":
        emp = (request.query.get("employee") or "").strip()
        data = await asyncio.to_thread(control.geo_days, None, _all_employees(), rng, emp)
    else:
        data = await asyncio.to_thread(functools.partial(
            control.overview, None, _all_employees(), rng,
            office=(request.query.get("office") or "").strip()))
    db.audit_access(ADMIN_ID, "control_view",
                    detail=f"admin {kind} {rng['from']}..{rng['to']}")
    resp = web.json_response(digest_data._public(data))
    resp.headers["Cache-Control"] = "no-store"
    return resp


async def _admin_control_overview(request):
    return await _admin_control(request, "overview")


async def _admin_control_soft(request):
    return await _admin_control(request, "soft")


async def _admin_control_flags(request):
    return await _admin_control(request, "flags")


async def _admin_control_callbacks(request):
    return await _admin_control(request, "callbacks")


async def _admin_control_assignments(request):
    return await _admin_control(request, "assignments")


async def _admin_control_access(request):
    return await _admin_control(request, "access")


async def _admin_control_day(request):
    return await _admin_control(request, "day")


async def _admin_control_geo_days(request):
    return await _admin_control(request, "geo_days")


async def _control_flags(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    emp = _manager_employee_q(request, team)
    data = await asyncio.to_thread(control.flags_of, mgr, team, rng, emp)
    if data is None:
        raise web.HTTPNotFound(text="employee")
    db.audit_access(uid, "control_view", target=emp,
                    detail=f"flags {rng['from']}..{rng['to']} n={len(data['visits'])}")
    return _manager_json(data)


async def _control_callbacks(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    iso, today, prev, nxt = _manager_date(request)
    team = _day_team(mgr, team, iso)
    data = await asyncio.to_thread(functools.partial(
        control.callbacks, mgr, team, iso, office=(request.query.get("office") or "").strip()))
    # Телефоны и адреса должников — выдача ПД: в журнал составом.
    reps = ",".join(x["rep"] for x in data["items"][:50])
    db.audit_access(uid, "control_callbacks",
                    detail=f"{iso} n={len(data['items'])} reps={reps}")
    return _manager_json({**data, "prev_date": prev, "next_date": nxt, "today": today})


async def _control_callback(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    try:
        body = await request.json()
        vid = int(body.get("visit_id"))
    except Exception:  # noqa: BLE001
        raise _json_error(web.HTTPBadRequest, "visit_id")
    outcome = str(body.get("outcome") or "").strip()
    if outcome not in db.CONTROL_OUTCOMES:
        raise _json_error(web.HTTPBadRequest, "outcome")
    if not control.visit_in_scope(team, vid):
        raise web.HTTPNotFound(text="visit")
    chk = db.control_record(vid, outcome, str(body.get("note") or ""), uid)
    if chk is None:
        raise _json_error(web.HTTPNotFound, "not_selected")
    db.audit_access(uid, "control_check", target=str(vid), detail=outcome)
    return _manager_json({"ok": True, "check": chk})


async def _control_export(request: web.Request) -> web.Response:
    uid, mgr, team = _manager_gate(request)
    rng = _stats_range_q(request)
    team = _range_team(mgr, team, rng)
    body = await asyncio.to_thread(control.control_xlsx, mgr, team, rng)
    db.audit_access(uid, "control_export", detail=f"{rng['from']}..{rng['to']}")
    return web.Response(
        body=body,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename=control_{rng['from']}_{rng['to']}.xlsx",
                 "Cache-Control": "no-store"})


async def _admin_manager_devices(request: web.Request) -> web.Response:
    """Компьютеры с пультом руководителя: у кого, с какого числа, когда в сети,
    отозван ли (и ещё не постучался)."""
    _admin(request)
    raw = (request.query.get("telegram_id") or "").strip()
    try:
        tid = int(raw) if raw else None
    except ValueError:
        raise web.HTTPBadRequest(text="telegram_id")
    return web.json_response({"ok": True, "devices": db.list_manager_devices(tid)})


async def _admin_manager_device_revoke(request: web.Request) -> web.Response:
    """{id} — один компьютер; {telegram_id, all: true} — все компьютеры человека."""
    _admin(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        if body.get("id") is not None:
            n = db.revoke_manager_devices(device_id=int(body["id"]), by="admin")
        elif body.get("telegram_id") is not None and body.get("all"):
            n = db.revoke_manager_devices(telegram_id=int(body["telegram_id"]), by="admin")
        else:
            raise ValueError
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="id")
    db.audit_access(ADMIN_ID, "pult_revoke",
                    detail=json.dumps(body, ensure_ascii=False)[:200])
    return web.json_response({"ok": True, "revoked": n})


# --- Приложение выездника на ПК: свой класс токена, телефон не вытесняет ---------
async def _agent_device_enroll(request: web.Request) -> web.Response:
    """{code, device} → {token, employee}: вход по ссылке ВЫЕЗДНИКА.

    Ссылка руководителя отвергается (не тратится): пульту — свой энролл. Привязку
    agent_bind не трогаем: телефон человека продолжает работать."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    code = _link_code(str(body.get("code") or ""))
    device = str(body.get("device") or "").strip()[:80]
    _admin_too_many_fails(request)
    uid = db.peek_app_link(code, desktop=True) if code else None
    keep_token = str(body.get("keep_token") or "").strip() or None
    expected_uid = body.get("expected_uid")
    if expected_uid is not None and (type(expected_uid) is not int or expected_uid <= 0):
        raise _json_error(web.HTTPBadRequest, "expected_uid")
    reuse_only = body.get("reuse_only") is True
    if uid is None and keep_token and code and db.app_link_status(code) != "expired":
        with db.connect() as conn:
            row = conn.execute("SELECT telegram_id FROM app_links WHERE code=?", (code,)).fetchone()
        uid = int(row["telegram_id"]) if row else None
    if not uid:
        _admin_auth_failed(request, scope="app")
        raise _json_error(web.HTTPGone, "link_dead",
                          reason=db.app_link_status(code, desktop=True) if code else "unknown")
    if digest_data.manager_scope(uid, is_owner=(uid == ADMIN_ID)) is not None:
        raise _json_error(web.HTTPForbidden, "not_agent")
    if expected_uid is not None and uid != expected_uid:
        raise _json_error(web.HTTPConflict, "pending_calls_other_login")
    got = db.enroll_agent_device(code, device, keep_token=keep_token,
                                 expected_uid=expected_uid, reuse_only=reuse_only)
    if not got:
        if reuse_only:
            raise _json_error(web.HTTPConflict, "pending_calls_other_login")
        raise _json_error(web.HTTPGone, "link_dead")
    employee, link_uid, dev_id, token = got
    _admin_auth_ok(request, scope="app")
    db.audit_access(link_uid, "desk_enroll", employee,
                    detail=f"#{dev_id} {device}")
    return web.json_response({"ok": True, "token": token, "device_id": dev_id,
                              "employee": employee, "telegram_id": link_uid})


async def _agent_device_logout(request: web.Request) -> web.Response:
    """Погасить токен этого компьютера (сам выход)."""
    tok = (request.headers.get("X-App-Token") or "").strip()
    state, dev = db.agent_device_check(tok, request.remote)
    if state != "ok" or not dev:
        raise web.HTTPUnauthorized(text="auth")
    db.agent_device_revoke(int(dev["id"]), by=f"self:{dev['employee']}")
    db.audit_access(int(dev["telegram_id"] or 0) or 0, "desk_logout",
                    dev["employee"])
    return web.json_response({"ok": True})


async def _admin_agent_devices(request: web.Request) -> web.Response:
    _admin(request)
    return web.json_response({"ok": True, "devices": db.agent_devices_list()})


async def _admin_agent_device_revoke(request: web.Request) -> web.Response:
    _admin(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        dev_id = int(body.get("id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="id")
    n = db.agent_device_revoke(dev_id, by="admin")
    db.audit_access(ADMIN_ID, "desk_revoke", detail=str(dev_id))
    return web.json_response({"ok": True, "revoked": int(n)})


async def _manager_app_map_data(request: web.Request) -> web.Response:
    """Карта дня ВНУТРИ приложения: авторизация сессией, подпись не нужна.
    Подписанный /manager/map_data остаётся для телеграм-кнопки."""
    uid, mgr, team = _manager_gate(request)
    iso, today, prev, nxt = _manager_date(request)
    team = _day_team(mgr, team, iso)
    emp = _manager_employee_q(request, team)
    pts = db.day_map_points(emp, iso)
    db.audit_access(uid, "digest_view", target=emp, detail=f"map {iso}")
    return _manager_json({**_manager_envelope(mgr, team, iso, today, prev, nxt, uid),
                          "employee": emp, **pts,
                          "vectorTiles": "/tiles/basemap.pmtiles"
                          if TILES_FILE.exists() else None,
                          "alert_m": db.geo_alert_m()})


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
    # Растровый фолбэк этой страницы идёт прямо на tile.openstreetmap.org, а
    # серверы OSM без Referer тайлы не отдают. Политика — умолчание браузера:
    # наружу уходит только адрес сайта, подписанная строка запроса — никогда.
    return web.Response(text=MAP_PAGE, content_type="text/html", charset="utf-8",
                        headers={"Referrer-Policy": "strict-origin-when-cross-origin"})


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
    // Вердикт «далеко» — СОХРАНЁННЫЙ (visits.geo_far), как в пуше и карточке:
    // руководитель попадает сюда кнопкой из дайджеста, и третьей версии правды
    // по одному визиту быть не должно. Порог — только для старых записей.
    const far = p.geo_far!=null ? !!p.geo_far : (p.dist_m!=null&&p.dist_m>j.alert_m);
    const d=p.dist_m==null?"":(p.dist_m>=1000?(p.dist_m/1000).toFixed(1)+" км":p.dist_m+" м")+" от адреса";
    add(p,pin(far?"#B0472C":"#219653",String(i+1)),
        (far?"⚠️ ":"✅ ")+esc(p.client)+(d?"<br>"+esc(d):""));
  });
  j.markers.forEach(p=>add(p,pin("#F2994A"),
    "📍 "+esc(p.label||"метка")+(p.at?" · "+esc(p.at):"")));
  map.fitBounds(b,{padding:70,maxZoom:15});
})();
</script></body></html>"""


# --- Admin API (для десктоп-пульта) ------------------------------------------
# Два способа войти, и это не дублирование:
#   * ADMIN_API_KEY — «мастер-ключ». Живёт в .env сервера, его знает владелец. Им
#     привязывают устройство и им же чинят ситуацию, когда все токены отозваны.
#   * токен устройства — то, чем пульт ходит каждый день. Привязан к конкретной
#     машине, отзывается по одному, виден в аудите поимённо.
# Смысл разделения: утёкший мастер-ключ перестаёт быть «загрузи что угодно
# откуда угодно», а потерянный ноут отзывается, не меняя ключ всем остальным.
def _admin(request: web.Request) -> None:
    """Обычные админ-ручки: ТОЛЬКО токен устройства, и только со своей машины.

    Мастер-ключ здесь больше не принимается. Раньше он открывал всё: знание одной
    строки из .env означало «загрузи что угодно откуда угодно», и утечка этой
    строки была неотличима от нормальной работы. Теперь мастер-ключ умеет ровно
    две вещи — привязать устройство и отозвать устройство, — а данные ходят
    только через устройство, которое видно в списке поимённо и гасится по одному.

    Отпечаток машины сверяется на КАЖДОМ запросе, а не только при выдаче токена:
    иначе скопированный файл настроек продолжал бы работать на чужой машине.

    Счётчик неудач — тот же, что у остальных дверей: без него перебор токена
    устройства и мастер-ключа до первой привязки шёл бы именно сюда. Но
    сверяется он только на ПРОМАХЕ: верный токен не запирается никогда. Все
    пульты офиса выходят в интернет одним адресом, и один забытый пульт с
    отозванным токеном, опрашивающий сервер, иначе запер бы на пять минут
    всех остальных. Перебирать здесь нечего (токен — 256 бит), счётчик
    держит лишь частоту промахов.
    """
    token = request.headers.get("X-Device-Token") or ""
    device = db.admin_device_by_token(
        token, request.headers.get("X-Device-Id") or "") if token else None
    # Роль сверяем явно: устройство инженера сюда не пускаем. Панель обслуживания
    # не должна видеть должников ни при каких обстоятельствах, а «токен есть —
    # значит свой» стёрло бы эту границу молча.
    if device and device.get("role", "operator") == "operator":
        _admin_auth_ok(request)
        return
    # До самой первой привязки ключ работает как обычный доступ: иначе первый
    # пульт неоткуда взять. После неё — никогда, даже если все устройства
    # отозвали (см. db.admin_bootstrap_done).
    if not db.admin_bootstrap_done():
        key = os.getenv("ADMIN_API_KEY", "")
        if key and hmac.compare_digest(
                (request.headers.get("X-Admin-Key") or "").encode("utf-8"),
                key.encode("utf-8")):
            _admin_auth_ok(request)
            return
    _admin_too_many_fails(request)
    _admin_auth_failed(request)
    raise web.HTTPUnauthorized(text="admin")


# Защита от перебора кодов и токенов. Считаем только НЕУДАЧНЫЕ попытки: общий
# лимит на частоту мешал бы штатной пакетной работе пульта, а перебор он всё
# равно почти не замедляет. Память процесса, без базы: перезапуск обнуляет —
# этого достаточно, чтобы подбор перестал быть дешёвым.
#
# У каждой неудачи есть ПОМЕТКА, чья это дверь: "admin" — мастер-ключ, токен
# устройства, код привязки пульта; "app" — код ссылки входа выездника. Считаются
# они вместе (одно окно на адрес), а вот обнуляются порознь, и это главное:
# удачный вход по своей ссылке — не пароль и достаётся даром (с живым токеном он
# даже не тратит обмен), поэтому обнулять им счётчик ЧУЖИХ неудач нельзя. Иначе
# цикл «19 попыток мастер-ключа + одно открытие своей ссылки» давал бы с одного
# адреса бесконечный перебор ключа и одноразовых кодов привязки.
_ADMIN_FAILS: dict[str, list[tuple[float, str]]] = {}
_ADMIN_FAIL_WINDOW = 300.0   # секунд
_ADMIN_FAIL_LIMIT = 20       # неудач за окно с одного адреса


def _admin_peer(request: web.Request) -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.remote or "?")


def _admin_auth_ok(request: web.Request, scope: str = "admin") -> None:
    """Удача снимает неудачи СВОЕЙ двери.

    `scope="admin"` — доказано владение ключом/токеном устройства, прощаем всё.
    `scope="app"` — доказано лишь то, что у человека живая ссылка входа: снимаем
    только его же промахи по ссылкам, счётчик админских дверей не трогаем.
    """
    peer = _admin_peer(request)
    if scope == "admin":
        _ADMIN_FAILS.pop(peer, None)
        return
    rest = [f for f in _ADMIN_FAILS.get(peer, []) if f[1] != scope]
    if rest:
        _ADMIN_FAILS[peer] = rest
    else:
        _ADMIN_FAILS.pop(peer, None)


def _admin_auth_failed(request: web.Request, scope: str = "admin") -> None:
    peer = _admin_peer(request)
    now = time.monotonic()
    fails = [f for f in _ADMIN_FAILS.get(peer, []) if now - f[0] < _ADMIN_FAIL_WINDOW]
    fails.append((now, scope))
    _ADMIN_FAILS[peer] = fails
    if len(_ADMIN_FAILS) > 1000:      # чтобы словарь не рос от случайного шума
        for stale in [k for k, v in _ADMIN_FAILS.items()
                      if not v or now - v[-1][0] > _ADMIN_FAIL_WINDOW]:
            _ADMIN_FAILS.pop(stale, None)


def _admin_too_many_fails(request: web.Request) -> None:
    peer = _admin_peer(request)
    now = time.monotonic()
    fails = [f for f in _ADMIN_FAILS.get(peer, []) if now - f[0] < _ADMIN_FAIL_WINDOW]
    _ADMIN_FAILS[peer] = fails
    if len(fails) >= _ADMIN_FAIL_LIMIT:
        raise web.HTTPTooManyRequests(text="too many attempts",
                                      headers={"Retry-After": "60"})


def _engineer(request: web.Request) -> dict:
    """Обслуживание: устройства с ролями «инженер» и «оператор».

    Инженер входит веб-панелью ``/ops/`` (ИТ-службе без SSH), оператор — пультом
    аналитика, куда переносится всё, что задумывалось «гипотетическому
    инженеру». Роли остаются раздельными для ОТЗЫВА: погасить пульт и погасить
    панель — разные действия. Мастер-ключ сюда не пускается вовсе: панель умеет
    перезапускать сервер, и открывать её строкой из .env значило бы вернуть
    ровно то, что мы только что убрали.
    """
    _admin_too_many_fails(request)
    token = request.headers.get("X-Device-Token") or ""
    device = db.admin_device_by_token(
        token, request.headers.get("X-Device-Id") or "") if token else None
    if device and device.get("role") in ("engineer", "operator"):
        _admin_auth_ok(request)
        return device
    _admin_auth_failed(request)
    raise web.HTTPUnauthorized(text="engineer")


def _admin_master_key_only(request: web.Request) -> None:
    """Операции над самими устройствами — только мастер-ключом.

    Иначе токен устройства мог бы привязать ещё одно устройство и отозвать
    остальные: укравший ноут заперновладельца снаружи, а сам остался бы внутри.
    """
    _admin_too_many_fails(request)
    key = os.getenv("ADMIN_API_KEY", "")
    # Сравниваем БАЙТЫ: compare_digest на str падает при не-ASCII, и заголовок
    # с кириллицей отвечал пятисоткой вместо честного «не тот ключ».
    if not key or not hmac.compare_digest(
            (request.headers.get("X-Admin-Key") or "").encode("utf-8"),
            key.encode("utf-8")):
        _admin_auth_failed(request)
        raise web.HTTPUnauthorized(text="master key required")
    _admin_auth_ok(request)


async def _admin_device_enroll(request: web.Request) -> web.Response:
    """Обменять мастер-ключ на токен этой машины. Токен показывается один раз."""
    _admin_master_key_only(request)
    body = await request.json()
    try:
        made = db.enroll_admin_device(body.get("device_id") or "",
                                      body.get("name") or "")
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, **made})


async def _admin_devices(request: web.Request) -> web.Response:
    """Список устройств. Пускаем и по токену, и по мастер-ключу.

    Мастер-ключом — потому что это единственный экран, который нужен, когда все
    токены отозваны: иначе владелец не увидит, что именно отзывать и что
    привязывать заново.
    """
    try:
        _admin(request)
    except web.HTTPUnauthorized:
        _admin_master_key_only(request)
    return web.json_response({"devices": db.list_admin_devices(
        include_revoked=request.rel_url.query.get("all") == "1")})


async def _admin_device_redeem(request: web.Request) -> web.Response:
    """Обменять ОДНОРАЗОВЫЙ КОД на токен этой машины. Токен показывается раз.

    Мастер-ключ здесь не нужен и не принимается: в этом весь смысл. Код выдаётся
    на месте у сервера (``server/admin_access.py``) и гаснет при первом обмене,
    поэтому перехваченный код либо уже мёртв, либо виден в списке невыданным.
    """
    _admin_too_many_fails(request)
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "тело не JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "тело не объект"}, status=400)
    try:
        made = db.redeem_enroll_code(str(body.get("code") or ""),
                                     str(body.get("device_id") or ""),
                                     str(body.get("name") or ""))
    except ValueError as exc:
        _admin_auth_failed(request)
        return web.json_response({"error": str(exc)}, status=400)
    _admin_auth_ok(request)
    return web.json_response({"ok": True, **made})


async def _ops_page(request: web.Request) -> web.Response:
    """Страница панели. Отдаётся БЕЗ проверки роли — и это не упущение.

    Браузер не умеет присылать заголовок с токеном на обычный переход по ссылке,
    поэтому закрытая страница означала бы, что панель нельзя открыть вовсе.
    В самой странице нет ни строчки данных: это разметка и скрипт, который сперва
    просит код привязки, обменивает его на токен устройства и только потом
    обращается к ручкам. Все ручки с данными закрыты ролью, как и были.
    """
    return web.Response(text=ops_panel.page(), content_type="text/html", charset="utf-8")


async def _ops_state(request: web.Request) -> web.Response:
    return await ops_panel.state(request, _engineer(request))


async def _ops_run(request: web.Request) -> web.Response:
    return await ops_panel.run(request, _engineer(request))


async def _ops_result(request: web.Request) -> web.Response:
    return await ops_panel.result(request, _engineer(request))


async def _ops_devices(request: web.Request) -> web.Response:
    return await ops_panel.devices(request, _engineer(request))


async def _ops_code(request: web.Request) -> web.Response:
    return await ops_panel.issue_code(request, _engineer(request))


async def _ops_revoke(request: web.Request) -> web.Response:
    return await ops_panel.revoke(request, _engineer(request))


async def _admin_key_check(request: web.Request) -> web.Response:
    """Жив ли мастер-ключ. Ничего не отдаёт, кроме «да».

    Нужна ротации ключа: она обязана убедиться, что новый ключ принят, а старый
    больше нет. Раньше она проверяла это на ``/admin/stats`` — то есть на ручке
    с персональными данными, и это была ровно та привилегия, которую мы у
    мастер-ключа отбираем.
    """
    _admin_master_key_only(request)
    return web.json_response({"ok": True})


async def _admin_device_revoke(request: web.Request) -> web.Response:
    """Отозвать устройство по id или ВСЕ устройства одной роли.

    ``{"all": true, "role": "operator"|"engineer"}`` — ответ на кражу токена.
    Оператор выдаёт коды пульта, и укравший его токен успевает завести себе
    второе устройство: отзыв украденного его не гасит. Роль обязательна —
    «отозвать вообще всё» одной опечаткой не делается.
    """
    _admin_master_key_only(request)
    body = await _json_object(request)
    if body is None:
        return web.json_response({"error": "тело не JSON-объект"}, status=400)
    if body.get("all") is True:
        role = str(body.get("role") or "").strip().lower()
        if role not in db.DEVICE_ROLES:
            return web.json_response(
                {"error": f"роль должна быть одной из {', '.join(db.DEVICE_ROLES)}"},
                status=400)
        revoked = db.revoke_admin_devices_by_role(role)
        return web.json_response({"ok": True, "role": role, "revoked": revoked})
    try:
        row_id = int(body.get("id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "id"}, status=400)
    return web.json_response({"ok": db.revoke_admin_device(row_id)})


async def _admin_stats(request: web.Request) -> web.Response:
    _admin(request)
    s = db.stats_all()
    return web.json_response({
        "clients": s["total"], "visited": s["visited"], "markers": len(db.list_markers()),
        "countries": db.stats_by_country(),
        "employees": [{"employee": e["employee"], "contracts": e["contracts"]}
                      for e in db.employees_in_registry()],
        "soft_employees": [{"employee": e["employee"], "contracts": e["contracts"]}
                           for e in db.soft_employees()],
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


# Регистратор разрешений на импорт живёт РЯДОМ С ДАННЫМИ и по умолчанию не имеет
# HTTP-входа: загрузка реестра — это отдельный акт, а не следствие знания ключа.
#
# Владелец может открыть узкую ручку на СВОЁМ стенде явным флагом окружения. Тогда
# второй фактор исчезает: admin-ключ становится достаточным условием загрузки. Это
# осознанный размен владельца (03.08.2026) — он один и оператор, и владелец, а
# терминал в рабочем цикле для него дороже второго шага. Флаг по умолчанию ВЫКЛЮЧЕН,
# и выключение возвращает исходный порядок целиком, без правки кода.
IMPORT_REGISTRAR_HTTP = os.getenv("IMPORT_REGISTRAR_HTTP", "").strip() in (
    "1", "true", "yes")


async def _admin_registry_columns(request: web.Request) -> web.Response:
    """Карта колонок реестра + всё, что нужно экрану пульта для сопоставления.

    Отдаём вместе со схемой и контрактными правилами: пульт должен показывать
    ровно тот разбор заголовков, который потом сделает импорт, а не свою
    приблизительную копию.
    """
    _admin(request)
    return web.json_response({
        "map": db.registry_column_map(),
        "schema": [{"key": f["key"], "title": f["title"], "block": f["block"],
                    "source": f["source"]} for f in db.export_schema()],
        "contract_aliases": {k: list(v) for k, v
                             in db.WORKBOOK_HEADER_ALIASES.items()},
        "contract_ignored": list(db.WORKBOOK_ALLOWED_IGNORED_HEADERS),
        "contract_ignored_patterns": list(
            db.WORKBOOK_ALLOWED_IGNORED_HEADER_PATTERNS),
        "required_keys": list(db.WORKBOOK_TEST_SUBSET_REQUIRED_KEYS),
        "limits": {"aliases": db.MAX_COLUMN_MAP_ALIASES,
                   "ignored": db.MAX_COLUMN_MAP_IGNORED,
                   "extras": db.MAX_COLUMN_MAP_EXTRAS},
    })


async def _admin_registry_columns_save(request: web.Request) -> web.Response:
    """Сохранить карту колонок. Ошибка валидации — 409 с человеческим текстом.

    Карта меняет то, как читается боевой реестр, поэтому запись логируется в
    журнал доступа: по нему видно, кто и когда переопределил колонку.
    """
    _admin(request)
    body = await request.json()
    device = db.admin_device_by_token(request.headers.get("X-Device-Token") or "")
    author = (device["name"] if device else "мастер-ключ")
    try:
        saved = db.save_registry_column_map(body.get("map") or body, author=author)
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=409)
    db.audit_access(
        0, "registry_columns_save", target=author,
        detail=json.dumps(
            {"aliases": sum(len(v) for v in saved["aliases"].values()),
             "ignored": len(saved["ignored"]), "extras": len(saved["extras"])},
            ensure_ascii=False, separators=(",", ":")),
    )
    return web.json_response({"ok": True, "map": saved})


async def _admin_import_authorize(request: web.Request) -> web.Response:
    """Зарегистрировать один exact proof (если ручка включена)."""
    _admin(request)
    if not IMPORT_REGISTRAR_HTTP:
        return web.json_response(
            {"blocked": True, "enabled": False,
             "error": "HTTP-регистрация разрешений выключена: зарегистрируйте proof "
                      "на сервере — import_authorization.py register"},
            status=403)
    body = await request.json()
    source_kind = body.get("source_kind")
    if source_kind not in db.IMPORT_SOURCE_KINDS:
        return web.json_response(
            {"blocked": True, "enabled": True,
             "error": f"неизвестный source_kind: {source_kind!r}"}, status=409)
    try:
        authorization = db.register_import_authorization(
            body.get("source_proof"), source_kind=source_kind,
            ttl_hours=int(body.get("ttl_hours") or 24))
    except (ExchangeContractError, ValueError, TypeError) as exc:
        return web.json_response(
            {"blocked": True, "error": f"proof отклонён: {exc}"}, status=409)
    db.audit_access(0, "import_authorize", authorization.get("proof_digest") or "",
                    source_kind)
    return web.json_response({"ok": True, "enabled": True,
                              "authorization": authorization})


async def _admin_import_json(request: web.Request) -> web.Response:
    """Импорт published master: гриды уже распарсены клиентом (calamine).

    ``source_kind`` обязателен. Personal-books → bot здесь не существует:
    personal ↔ master обслуживает только registry_cycle.
    """
    _admin(request)
    body = await request.json()
    refused = await asyncio.to_thread(_expand_sheets_delta, body)
    if refused:
        return web.json_response(refused, status=409)
    source_kind = body.get("source_kind")
    if source_kind not in db.IMPORT_SOURCE_KINDS:
        return web.json_response(
            db.import_grid([], source_kind=source_kind),
            status=409,
        )
    full_registry = body.get("full_registry", False)
    if type(full_registry) is not bool:
        return web.json_response(
            {"blocked": True,
             "error": "импорт заблокирован: full_registry должен быть boolean"},
            status=409,
        )
    replace_current_visit_comments = body.get(
        "replace_current_visit_comments", False
    )
    restore_manual_unassigned = body.get("restore_manual_unassigned", False)
    for option_name, option_value in (
        ("restore_manual_unassigned", restore_manual_unassigned),
        ("replace_current_visit_comments", replace_current_visit_comments),
    ):
        if type(option_value) is not bool:
            return web.json_response(
                {"blocked": True,
                 "error": f"импорт заблокирован: {option_name} должен быть boolean"},
                status=409,
            )
    authoritative_employees = body.get("authoritative_employees")
    expected_auto_unassigned = body.get("expected_auto_unassigned")
    expected_auto_unassigned_digest = body.get(
        "expected_auto_unassigned_digest"
    )
    expected_manual_restored = body.get("expected_manual_restored")
    expected_manual_restore_digest = body.get(
        "expected_manual_restore_digest"
    )
    expected_comment_effect_digest = body.get(
        "expected_comment_effect_digest"
    )
    expected_recalled = body.get("expected_recalled")
    expected_recalled_digest = body.get("expected_recalled_digest")
    expected_recall_restored = body.get("expected_recall_restored")
    expected_recall_restored_digest = body.get("expected_recall_restored_digest")
    for _nm, _cv, _dv in (
        ("recalled", expected_recalled, expected_recalled_digest),
        ("recall_restored", expected_recall_restored,
         expected_recall_restored_digest),
    ):
        if _cv is not None and (isinstance(_cv, bool)
                                or not isinstance(_cv, int) or _cv < 0):
            return web.json_response(
                {"blocked": True,
                 "error": (f"импорт заблокирован: expected_{_nm} должен "
                           "быть неотрицательным целым")},
                status=409,
            )
        if _dv is not None and (
            not isinstance(_dv, str) or len(_dv) != 64
            or any(ch not in "0123456789abcdef" for ch in _dv)
        ):
            return web.json_response(
                {"blocked": True,
                 "error": (f"импорт заблокирован: expected_{_nm}_digest "
                           "должен быть lowercase SHA-256")},
                status=409,
            )
    if (
        expected_auto_unassigned is not None
        and (
            isinstance(expected_auto_unassigned, bool)
            or not isinstance(expected_auto_unassigned, int)
            or expected_auto_unassigned < 0
        )
    ):
        return web.json_response(
            {"blocked": True,
             "error": ("импорт заблокирован: expected_auto_unassigned должен "
                       "быть неотрицательным целым")},
            status=409,
        )
    if (
        expected_manual_restored is not None
        and (
            isinstance(expected_manual_restored, bool)
            or not isinstance(expected_manual_restored, int)
            or expected_manual_restored < 0
        )
    ):
        return web.json_response(
            {"blocked": True,
             "error": ("импорт заблокирован: expected_manual_restored должен "
                       "быть неотрицательным целым")},
            status=409,
        )
    if full_registry and (
        expected_auto_unassigned is None
        or expected_auto_unassigned_digest is None
    ):
        return web.json_response(
            {"blocked": True,
             "error": ("импорт заблокирован: для полного среза обязателен "
                       "свежий предпросмотр точного множества снятий")},
            status=409,
        )
    if restore_manual_unassigned and (
        expected_manual_restored is None
        or expected_manual_restore_digest is None
    ):
        return web.json_response(
            {"blocked": True,
             "error": ("импорт заблокирован: для возврата вручную снятых "
                       "договоров обязателен свежий предпросмотр точного "
                       "множества возвратов")},
            status=409,
        )
    # Историчный комментарий сюда не входит: он грузится по наличию колонки, и
    # свежий предпросмотр для него требует сам импорт — там видно состав файла.
    if (
        replace_current_visit_comments
        and expected_comment_effect_digest is None
    ):
        return web.json_response(
            {"blocked": True,
             "error": ("импорт заблокирован: для изменения комментариев "
                       "обязателен свежий предпросмотр точного множества замен")},
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
    report = await asyncio.to_thread(
        _import_grid_serial,
        grid,
        source_kind=source_kind,
        source_proof=body.get("source_proof"),
        require_source_proof=True,
        full_registry=full_registry,
        authoritative_employees=authoritative_employees,
        import_profile=body.get("import_profile", "hard"),
        restore_manual_unassigned=restore_manual_unassigned,
        replace_current_visit_comments=replace_current_visit_comments,
        expected_auto_unassigned=expected_auto_unassigned,
        expected_auto_unassigned_digest=expected_auto_unassigned_digest,
        expected_manual_restored=expected_manual_restored,
        expected_manual_restore_digest=expected_manual_restore_digest,
        expected_comment_effect_digest=expected_comment_effect_digest,
        expected_recalled=expected_recalled,
        expected_recalled_digest=expected_recalled_digest,
        expected_recall_restored=expected_recall_restored,
        expected_recall_restored_digest=expected_recall_restored_digest,
    )
    if report.get("blocked"):
        # Машиночитаемый код: операторский CLI различает «нужен предпросмотр
        # отзыва» и «расписка не зарегистрирована», не разбирая свободный текст.
        if report.get("recall_preview_required"):
            report["code"] = "recall_preview_required"
        return web.json_response(report, status=409)
    # A consumed receipt is an exact zero-write replay. Do not run ancillary
    # mutators behind it: otherwise replaying the same import could still
    # change coordinates after the atomic import transaction.
    if report.get("idempotent_replay"):
        return web.json_response(report)
    # Геокодинг — по-прежнему здесь, в цикле, и это НАМЕРЕННО. Проход держит
    # ОДНУ транзакцию записи от первой найденной точки до конца: на свежей машине
    # это ~19 с, больше busy_timeout (8 с). В потоке каждый телефон, пишущий в
    # это время, простоял бы в цикле событий до 8 с на каждой записи и получил
    # бы 500 (а журнал доступа — дыру). Пока проход не коммитит порциями
    # (scoring.py), честнее один раз подождать всем. В ежедневном импорте он
    # стоит десятые доли секунды: точки уже есть, считать нечего.
    try:
        import scoring
        report["geocoded"] = scoring.geocode_city_centroids()
    except Exception as e:  # noqa: BLE001
        logging.warning("geocode after import: %s", e)
    # Новые люди из реестра сразу получают офис — по географии их должников
    # (db.guess_offices), ПОСЛЕ геокодинга: у новых договоров появились точки.
    # Иначе человек неделями висит у руководителя в «Без офиса», пока его не
    # заметят. Пишется только пустое; ошибка тут импорт не отменяет. В потоке:
    # короткие записи по одной, своё соединение, кэши ролей — см. ниже.
    try:
        guessed = await asyncio.to_thread(db.guess_offices, apply=True)
        report["offices_guessed"] = guessed["filled"]
        report["offices_undecided"] = guessed["undecided"]
        if guessed["filled"]:
            db.audit_access(0, "employee_cities",
                            detail=f"auto={len(guessed['filled'])}")
    except Exception as e:  # noqa: BLE001
        logging.warning("guess offices after import: %s", e)
    return web.json_response(report)


# import_grid — в потоке, а не в корутине: разбор и запись всего реестра (0,6 с
# на 18 тыс. договоров на машине разработчика, на двух ядрах VM — дольше)
# держали весь сервер (ревью 25.09.2026, F-stability-1). В поток можно:
# import_grid открывает СВОЁ соединение (db.connect: WAL, busy_timeout 8 с) и
# asyncio не трогает. Его BEGIN IMMEDIATE на реестре нашего размера длится
# секунды, меньше busy_timeout: запись телефона в это время подождёт и пройдёт.
# Общее с запросами у него — версия данных и кэши ролей: их сбрасывают целым
# присваиванием, а читатели берут глобал один раз (см. db.reset_role_caches),
# так что полусброшенного кэша не видит ни один поток.
# Импорты и предпросмотры — строго по одному. Пока цикл стоял, очередь
# получалась сама собой; в потоке второй упёрся бы в BEGIN IMMEDIATE первого и
# через 8 с получил бы «database is locked». Замок держит поток, цикл его не ждёт.
_IMPORT_SERIAL = threading.Lock()


def _import_grid_serial(grid, **options) -> dict:
    with _IMPORT_SERIAL:
        return db.import_grid(grid, **options)


# --- Перевоз реестра разницей (опыт exp/fast-exchange, 30.09.2026) -----------
# Сервер помнит последние гриды, которые к нему приезжали (по SHA-256), и
# принимает вместо «sheets» разницу к одному из них. Собранный грид обязан
# совпасть с присланным отпечатком — дальше путь импорта прежний, до буквы.
# Базы нет (перезапуск, чистка, другая машина) — 409 delta_base_unknown, и
# пульт шлёт грид целиком.
import grid_delta  # noqa: E402

IMPORT_BASES_KEEP = 3


def _import_bases_dir() -> Path:
    d = Path(db.DB_PATH).parent / "import_bases"
    d.mkdir(exist_ok=True)
    return d


def _remember_import_base(grid) -> str:
    import gzip
    raw = grid_delta.canonical(grid)
    digest = hashlib.sha256(raw).hexdigest()
    d = _import_bases_dir()
    path = d / f"{digest}.json.gz"
    if path.exists():
        os.utime(path)
    else:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(gzip.compress(raw, 6))
        os.replace(tmp, path)
    for old in sorted(d.glob("*.json.gz"), key=lambda x: x.stat().st_mtime,
                      reverse=True)[IMPORT_BASES_KEEP:]:
        old.unlink(missing_ok=True)
    return digest


def _load_import_base(digest):
    import gzip
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    path = _import_bases_dir() / f"{digest}.json.gz"
    if not path.exists():
        return None
    return json.loads(gzip.decompress(path.read_bytes()))


def _expand_sheets_delta(body: dict):
    """sheets_delta → sheets с тем же гридом; вернуть тело ответа-отказа или None."""
    delta = body.get("sheets_delta")
    if delta is None:
        return None
    if body.get("sheets") is not None:
        return {"blocked": True, "error": "delta_and_sheets",
                "detail": "прислано и sheets, и sheets_delta"}
    base = _load_import_base(delta.get("base") if isinstance(delta, dict) else None)
    if base is None:
        return {"blocked": True, "error": "delta_base_unknown"}
    try:
        grid = grid_delta.apply_delta(base, delta)
    except grid_delta.DeltaError as exc:
        return {"blocked": True, "error": "delta_mismatch", "detail": str(exc)}
    body.pop("sheets_delta", None)
    body["sheets"] = [{"name": db.WORKBOOK_SHEET_NAME, "grid": grid}]
    return None


async def _admin_import_preview_json(request: web.Request) -> web.Response:
    """Read-only preflight импорта без consume receipt и побочных mutator-ов.

    Отдельный endpoint намеренный: новый пульт, попавший на старый сервер, получит
    404 и остановится, а не отправит якобы preview в старый write-handler.
    """
    _admin(request)
    body = await request.json()
    refused = await asyncio.to_thread(_expand_sheets_delta, body)
    if refused:
        return web.json_response(refused, status=409)
    source_kind = body.get("source_kind")
    if source_kind not in db.IMPORT_SOURCE_KINDS:
        return web.json_response(
            db.import_grid([], source_kind=source_kind, preview_only=True),
            status=409,
        )
    full_registry = body.get("full_registry", False)
    if type(full_registry) is not bool:
        return web.json_response(
            {"blocked": True,
             "error": "предпросмотр заблокирован: full_registry должен быть boolean"},
            status=409,
        )
    replace_current_visit_comments = body.get(
        "replace_current_visit_comments", False
    )
    restore_manual_unassigned = body.get("restore_manual_unassigned", False)
    for option_name, option_value in (
        ("restore_manual_unassigned", restore_manual_unassigned),
        ("replace_current_visit_comments", replace_current_visit_comments),
    ):
        if type(option_value) is not bool:
            return web.json_response(
                {"blocked": True,
                 "error": (f"предпросмотр заблокирован: {option_name} "
                           "должен быть boolean")},
                status=409,
            )
    authoritative_employees = body.get("authoritative_employees")
    if not isinstance(body.get("source_proof"), dict):
        return web.json_response(
            {"blocked": True,
             "error": "предпросмотр заблокирован: source_proof обязателен"},
            status=409,
        )
    try:
        validate_contract_binding(body.get("data_exchange_contract"))
        grid = db.pick_grid(body.get("sheets") or [])
    except (ExchangeContractError, ValueError) as exc:
        return web.json_response(
            {"blocked": True, "error": f"предпросмотр заблокирован: {exc}"},
            status=409,
        )
    report = await asyncio.to_thread(
        _import_grid_serial,        # тот же BEGIN IMMEDIATE — в ту же очередь
        grid,
        source_kind=source_kind,
        source_proof=body.get("source_proof"),
        require_source_proof=False,
        full_registry=full_registry,
        authoritative_employees=authoritative_employees,
        import_profile=body.get("import_profile", "hard"),
        preview_only=True,
        restore_manual_unassigned=restore_manual_unassigned,
        replace_current_visit_comments=replace_current_visit_comments,
    )
    if report.get("blocked"):
        return web.json_response(report, status=409)
    report["delta_base"] = await asyncio.to_thread(_remember_import_base, grid)
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
    direction = body.get("direction")
    db.set_report_chat(
        chat_id,
        enabled=None if enabled is None else bool(enabled),
        employees=None if employees is None else [str(e) for e in employees],
        direction=None if direction is None else str(direction))
    return web.json_response({"ok": True, "chats": db.list_report_chats()})


async def _admin_export_json(request: web.Request) -> web.Response:
    """Данные пульта: по умолчанию безопасный поток для обогащения.

    ``?mode=full`` — отдельный raw-слепок БД для команды «Реестр целиком».
    Старый пульт без параметра тоже получает очищенный поток и потому не может
    повторно разнести импортные псевдовизиты по живым файлам.
    """
    _admin(request)
    full = request.query.get("mode") == "full"
    employees = request.query.getall("employee", [])
    visit_from = request.query.get("visit_from", "")
    visit_to = request.query.get("visit_to", "")
    if not full and (employees or visit_from or visit_to):
        return web.json_response(
            {"error": "фильтры сотрудников и дат доступны только для mode=full"},
            status=409,
        )
    try:
        rows = (
            db.registry_rows(
                employees=employees or None,
                visit_from=visit_from,
                visit_to=visit_to,
            )
            if full else db.enrichment_rows()
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response(
        {
            "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
            "fields": db.export_schema(),
            "rows": rows,
            "selection": {
                "all_employees": not bool(employees),
                "employees": employees,
                "visit_from": visit_from,
                "visit_to": visit_to,
                "rows": len(rows),
            },
        },
        headers=_NO_STORE,
    )


async def _admin_enrichment_ledger(request: web.Request) -> web.Response:
    """Реестр доставленного обогащением (см. db.enrichment_ledger).

    GET — забрать перед прогоном (``?employee=`` повторяемый или
    ``?employees=А;Б``), POST — записать после прогона
    ``{"entries": [...], "receipt_digest": "..."}``. Мусор в записях даёт 409
    без единой записи: половинчатый реестр хуже пустого.
    """
    _admin(request)
    if request.method == "GET":
        employees = list(request.query.getall("employee", []))
        employees += [
            e.strip()
            for e in (request.query.get("employees") or "").split(";")
            if e.strip()
        ]
        ledger = db.enrichment_ledger(employees or None)
        return web.json_response({
            "data_exchange_contract": DATA_EXCHANGE_CONTRACT,
            "ledger": ledger,
            **({"books": db.enrichment_book_ledger(employees or None)} if request.query.get("books") == "1" else {}),
            "count": sum(len(v) for v in ledger.values()),
        })
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "тело не JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "тело не объект"}, status=400)
    try:
        recorded = db.record_enrichment_deliveries(
            body.get("entries"), str(body.get("receipt_digest") or ""),
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response({"ok": True, "recorded": recorded})


async def _admin_markers_json(request: web.Request) -> web.Response:
    _admin(request)
    return web.json_response({"rows": db.markers_rows()}, headers=_NO_STORE)


# Выгрузки пульту несут ПД всего реестра — ни браузер, ни прокси не должны
# оставлять их у себя.
_NO_STORE = {"Cache-Control": "no-store"}
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_bytes(build) -> bytes:
    """Собрать книгу build(path) во временный файл и вернуть её байты.

    Раньше книга писалась в /tmp под постоянным именем и там оставалась: весь
    реестр с ПД лежал в слое контейнера до пересоздания, а два одновременных
    запроса писали в один и тот же файл (ревью 25.09.2026). Теперь имя
    уникальное (mkstemp, права 0600), и файл удаляется здесь же, в потоке, при
    любом исходе — ошибка сборки, обрыв клиента, отмена запроса."""
    fd, path = tempfile.mkstemp(prefix="hard-export-", suffix=".xlsx")
    os.close(fd)
    try:
        build(path)
        return Path(path).read_bytes()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _xlsx_download(build, filename: str) -> web.Response:
    # openpyxl на всём реестре — секунды (4,7 с на 18 тыс. договоров), и в
    # корутине на это время вставал весь сервер. Собираем в потоке.
    body = await asyncio.to_thread(_xlsx_bytes, build)
    return web.Response(body=body, content_type=_XLSX_MIME, headers={
        "Content-Disposition": f"attachment; filename={filename}", **_NO_STORE})


async def _admin_export(request: web.Request) -> web.Response:
    _admin(request)
    return await _xlsx_download(db.export_xlsx, "registry_export.xlsx")


async def _admin_markers_xlsx(request: web.Request) -> web.Response:
    _admin(request)
    return await _xlsx_download(db.export_markers, "markers.xlsx")


async def _admin_employees(request: web.Request) -> web.Response:
    """Список ФИО из реестра с уже проставленной привязкой — для экрана «Роли»
    в пульте. Заказчик вписывает telegram_id каждому ФИО, тип (выездник/курьер)."""
    _admin(request)
    # Норма выездов едет вместе со списком: в пульте она стоит колонкой рядом
    # с офисом, и отдельный запрос на каждую строку был бы тридцатью запросами.
    plan_map = db.visit_plan_map()
    common = db.visit_plan_per_day()
    rows = []
    for row in db.employees_with_bindings():
        item = dict(row)
        personal = plan_map.get(item.get("employee") or "")
        item["plan"] = personal or common
        item["plan_personal"] = bool(personal)
        rows.append(item)
    return web.json_response({"employees": rows, "plan_common": common,
                              "plan_groups": list(VISIT_PLAN_GROUPS)})


async def _admin_visit_plan(request: web.Request) -> web.Response:
    """Нормы выездов из пульта: POST {plan:{ФИО: норма}}; 0 — вернуть к общей."""
    _admin(request)
    body = await request.json()
    plan = body.get("plan")
    if not isinstance(plan, dict):
        raise web.HTTPBadRequest(text="plan")
    for employee, norm in plan.items():
        db.set_visit_plan(str(employee), norm)
    db.audit_access(0, "visit_plan", detail=f"changed={len(plan)}")
    common = db.visit_plan_per_day()
    plan_map = db.visit_plan_map()
    return web.json_response({"ok": True, "plan": plan_map, "common": common,
                              "groups": list(VISIT_PLAN_GROUPS)})


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


async def _admin_recalled(request: web.Request) -> web.Response:
    """Пульту: снятые с работы договоры — единственный экран, где их видно.
    ?reason=recalled|manual|absent_from_registry, ?employee=ФИО, ?limit=N."""
    _admin(request)
    reason = (request.query.get("reason") or db.UNASSIGNED_REASON_RECALLED).strip()
    if reason not in (db.UNASSIGNED_REASON_RECALLED, db.UNASSIGNED_REASON_MANUAL,
                      db.UNASSIGNED_REASON_ABSENT_FROM_REGISTRY):
        raise web.HTTPBadRequest(text="reason")
    try:
        limit = int(request.query.get("limit") or 500)
    except ValueError:
        raise web.HTTPBadRequest(text="limit")
    data = db.unassigned_overview(
        reason=reason,
        employee=(request.query.get("employee") or "").strip(),
        limit=limit)
    items = [{"contract_id": r["contract_id"], "client": r["client"],
              "employee": r["unassigned_from"] or "",
              "recall_date": r["recall_date"] or "",
              "recall_reason": r["recall_reason"] or "",
              "recall_transfer": r["recall_transfer"] or "",
              "recall_withdrawn": r["recall_withdrawn"] or "",
              "debt": debt.contract_debt(r), "unit": debt.unit([r["partner"]])}
             for r in data["rows"]]
    # Оптовая выдача ПД (до 5000 ФИО с долгами) — след в журнал обязателен.
    db.audit_access(ADMIN_ID or 0, "recalled_list", reason, str(data["shown"]))
    return web.json_response({"reason": reason, "totals": data["totals"],
                              "by_employee": data["by_employee"],
                              "shown": data["shown"], "limit": data["limit"],
                              "items": items})


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


async def _admin_today(request: web.Request) -> web.Response:
    """Экран «Сегодня» в пульте: выезды по дням, кто молчит, доставка,
    обогащение, реестр — одной ручкой, чтобы владелец не ходил по SSH."""
    _admin(request)
    data = await asyncio.get_event_loop().run_in_executor(
        None, digest_data.today_overview)
    return web.json_response(data)


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
    today = workday.today().isoformat()
    managers = db.list_managers()
    for m in managers:
        # Расхождение написаний ФИО роль/реестр: у таких портфель у руководителя
        # не открывается (доступ матчит точно), хотя отбивки приходят.
        m["spelling_mismatch"] = len(db.manager_spelling_mismatches(m))
    return web.json_response({
        "managers": managers,
        "cities": db.cities_in_registry(),
        "employees": [{"employee": e["employee"], "contracts": e["contracts"]}
                      for e in db.employees_in_registry()],
        "city_employees": db.city_employees(),
        "digest": {
            "time": db.get_setting("digest_time") or "16:30",
            "enabled": db.push_enabled("digest_enabled"),
            "promise_time": db.get_setting("promise_time") or "09:30",
            "promise_enabled": db.push_enabled("promise_enabled"),
            "visit_push": db.push_enabled("visit_push"),
            # Ключ обязан быть и здесь: экран «Руководители» читает настройки
            # из /admin/managers, а не из /admin/digest_settings, и без него
            # галочка «сводка книгой» гаснет как «сервер её не знает».
            "digest_xlsx": db.push_enabled("digest_xlsx"),
            "digest_last_run": db.get_setting("digest_last_run") or "",
            "promise_last_run": db.get_setting("promise_last_run") or "",
            "delivery": db.push_summary(today),
        },
    })


async def _admin_employee_cities(request: web.Request) -> web.Response:
    """Офисы сотрудников: GET — карта {ФИО: город} плюс сам список сотрудников,
    POST {cities:{ФИО: город}} — записать (пустая строка снимает закрепление).

    Зачем отдельный факт: города в clients — это где живёт ДОЛЖНИК, а
    душанбинский выездник ездит в Ёвон, Хисор и Вахдат. Штат офиса из данных не
    выводится, его заводит оператор — и на нём держится охват руководителя по
    городу (managers.cities)."""
    _admin(request)
    if request.method == "POST":
        body = await request.json()
        pairs = body.get("cities")
        if not isinstance(pairs, dict):
            raise web.HTTPBadRequest(text="cities")
        # «folder» — пульт проставил по папке портфеля; всё прочее — рука человека.
        source = "folder" if body.get("source") == "folder" else "manual"
        for employee, city in pairs.items():
            db.set_employee_city(str(employee), str(city or ""), source=source)
        db.audit_access(0, "employee_cities",
                        detail=f"changed={len(pairs)} source={source}")
    # Ключи карты — в написании РЕЕСТРА: пульт сличает офис со строкой
    # сотрудника точным совпадением, и запись, сделанная чуть другим
    # написанием, показывалась бы «без офиса» при живом закреплении.
    names = db.registry_name_index()
    cities = {names.get(db._name_key(emp), emp): city
              for emp, city in db.employee_cities().items()}
    sources = {names.get(db._name_key(emp), emp): src
               for emp, src in db.office_sources().items()}
    employees = [e["employee"] for e in db.employees_in_registry()]
    return web.json_response({
        "ok": True,
        "cities": cities,
        "employees": employees,
        # Кому офис ещё не проставлен — пульт дозаполняет их по папкам портфелей.
        "unassigned": [e for e in employees if not cities.get(e)],
        # Офис по догадке сервера (география при импорте): папка портфеля его
        # уточняет, решение человека — нет.
        "auto": [e for e in employees if cities.get(e) and sources.get(e) == "auto"],
        # Города, уже назначенные хоть кому-то, — подсказка для выбора в пульте.
        "known": sorted({c for c in cities.values() if c}),
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
    # Пульт про направления не знает и поля direction не шлёт. Без этой строки
    # любая правка руководителя направления в пульте молча сбрасывала бы его в
    # обычный список — и живой состав сотрудников превращался бы в замороженный.
    direction = body.get("direction")
    if direction is None:
        prev = db.get_manager(tid)
        direction = (prev or {}).get("direction") or None
    # Личные рубильники приходят только теми ключами, что прислал пульт:
    # старый пульт их не шлёт, и «Сохранить» не должно ничего переключать.
    pushes = {k: bool(body[k]) for k in db.MANAGER_PUSH_KEYS if k in body}
    # Офисы — отдельный факт. Здесь они необязательны (старый пульт про них не
    # знает и не должен затирать заведённые офисы); пустой список снимает все.
    offices = body.get("office_cities")
    if offices is not None:
        offices = [str(c).strip() for c in offices if str(c).strip()]
    # Роль (руководитель/безопасник) — тоже только если прислали: старый пульт
    # её не знает, и сохранение не должно разжаловать безопасника.
    role = body.get("role")
    if role is not None and str(role) not in db.MANAGER_ROLES:
        raise web.HTTPBadRequest(text="role")
    db.set_manager(tid, name, cities, employees, direction=direction,
                   pushes=pushes or None, office_cities=offices,
                   role=str(role) if role is not None else None)
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


async def _admin_photo_geo(request: web.Request) -> web.Response:
    """Срок хранения координат снимков и ручка стирания.

    GET  — сколько снимков с координатами, сколько уже стёрто, самая старая
           точка и текущий срок (0 = автоуборки нет, так по умолчанию).
    POST — {forget:{before,employee,contract}} стирает координаты прямо сейчас
           (сами файлы и индекс остаются); {retention_days:N} включает срок.

    Зачем ручка вообще: EXIF-точка галерейного снимка чаще всего домашний
    адрес САМОГО сотрудника. До 27.08.2026 стереть её можно было только
    полным сбросом теста — то есть вместе со всей работой.
    """
    _admin(request)
    # Админ-API ходит под мастер-ключом или токеном устройства, личности там
    # нет — в журнале это ADMIN_ID, как у остальных операторских действий.
    uid = ADMIN_ID or 0
    if request.method == "POST":
        b = await request.json()
        if "retention_days" in b:
            try:
                days = int(b.get("retention_days"))
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text="retention_days")
            if not 0 <= days <= 3650:
                raise web.HTTPBadRequest(text="retention_days range")
            db.set_setting("photo_geo_retention_days", str(days))
            db.audit_access(uid, "photo_geo_retention", "", str(days))
        forget = b.get("forget")
        if isinstance(forget, dict):
            try:
                cleared = db.photo_geo_forget(
                    before=(forget.get("before") or "").strip() or None,
                    employee=(forget.get("employee") or "").strip() or None,
                    contract_id=(forget.get("contract") or "").strip() or None)
            except ValueError as exc:
                raise web.HTTPBadRequest(text=str(exc))
            # Стирание ПД — след обязателен: это единственное доказательство,
            # что координаты убрали по решению, а не потеряли.
            db.audit_access(uid, "photo_geo_forget",
                            (forget.get("contract") or "").strip(),
                            json.dumps({**{k: forget.get(k) for k in
                                           ("before", "employee", "contract")},
                                        "cleared": cleared}, ensure_ascii=False))
            return web.json_response({"ok": True, "cleared": cleared,
                                      **db.photo_geo_stats()})
    return web.json_response({"ok": True, **db.photo_geo_stats()})


async def _admin_digest_settings(request: web.Request) -> web.Response:
    """GET/POST настроек автопуша дайджеста: {time:"HH:MM", enabled:bool} (время по МСК).
    GET дополнительно отдаёт защёлки, счётчики добора и сводку доставок за
    сегодня — «не работает» становится проверяемым фактом без захода на сервер."""
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
        # ТОЛЬКО при наличии ключа: старый пульт шлёт body без visit_push, и без
        # охраны обычное «Сохранить» молча выключало бы отбивки по всему проду.
        if "visit_push" in b:
            db.set_setting("visit_push", "1" if b.get("visit_push") else "0")
        # Сводка руководителям книгой Excel вместо длинного текста (решение
        # владельца 08.09.2026). Тот же принцип «только при наличии ключа».
        if "digest_xlsx" in b:
            db.set_setting("digest_xlsx", "1" if b.get("digest_xlsx") else "0")
        # Личные рубильники ВЛАДЕЛЬЦА (не общие): «дайджест только, отдельно».
        # Тот же принцип «только при наличии ключа» — старый пульт их не шлёт.
        for key in ("owner_digest", "owner_promise", "owner_visits"):
            if key in b:
                db.set_setting(key, "1" if b.get(key) else "0")

    today = workday.today().isoformat()
    delivery = db.push_summary(today)
    delivery["recent"] = db.push_tail(20, day=today)
    return web.json_response({
        "ok": True,
        "time": db.get_setting("digest_time") or "16:30",
        "enabled": db.push_enabled("digest_enabled"),
        "promise_time": db.get_setting("promise_time") or "09:30",
        "promise_enabled": db.push_enabled("promise_enabled"),
        "visit_push": db.push_enabled("visit_push"),
        "digest_xlsx": db.push_enabled("digest_xlsx"),
        "owner_digest": db.push_enabled("owner_digest", "1"),
        "owner_promise": db.push_enabled("owner_promise", "0"),
        "owner_visits": db.push_enabled("owner_visits", "0"),
        "digest_last_run": db.get_setting("digest_last_run") or "",
        "promise_last_run": db.get_setting("promise_last_run") or "",
        "digest_retry": {"day": db.get_setting("digest_retry_day") or "",
                         "attempts": int(db.get_setting("digest_retry_n") or 0)},
        "promise_retry": {"day": db.get_setting("promise_retry_day") or "",
                          "attempts": int(db.get_setting("promise_retry_n") or 0)},
        "today": today,
        "delivery": delivery,
    })


async def _admin_push_test(request: web.Request) -> web.Response:
    """Пробная отправка выбранного вида конкретному адресату — с ТЕКСТОМ ошибки
    Телеграма в ответе (весь смысл ручки в этом тексте, поэтому неудача — тоже
    HTTP 200). Пишется отдельным видом test: иначе диагностика выключала бы то,
    что диагностирует — push_settled_today считает улаженными sent и blocked,
    и руководитель, по которому нажали «проверить» в 11:00, не получил бы
    настоящую сводку в 16:30."""
    _admin(request)
    import digest
    from notify import classify_send_error
    body = await request.json()
    kind = str(body.get("kind") or "").strip()
    if kind not in (db.PUSH_DIGEST, db.PUSH_PROMISE, db.PUSH_VISIT):
        raise web.HTTPBadRequest(text="kind")
    try:
        tid = int(body.get("telegram_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="telegram_id")
    bot = request.app.get("bot")
    if not bot:
        # Telegram отключён — это не сбой, а состояние; отчитываемся тем же
        # JSON, что и о любой неудаче доставки, и оставляем след в push_log.
        from notify import bot_missing
        bot_missing(bot)
        db.log_push(db.PUSH_TEST, db.PUSH_SKIP_NO_BOT, target_kind="manager",
                    target_id=tid, note=f"push_test:{kind}")
        return web.json_response({"ok": False, "status": db.PUSH_SKIP_NO_BOT,
                                  "delivery": "off",
                                  "error": "Telegram отключён: BOT_TOKEN пуст"})
    if kind in (db.PUSH_DIGEST, db.PUSH_PROMISE):
        # ПД-граница: сводка содержит ФИО должников и суммы — слать её
        # произвольному id нельзя. kind=visit шлёт безличный текст.
        mgr = db.get_manager(tid)
        if not mgr and tid == ADMIN_ID:
            mgr = digest.owner_scope(tid)
        if not mgr:
            raise web.HTTPForbidden(text="not a manager")
        on_date = digest.today_iso()
        if kind == db.PUSH_DIGEST:
            text, kb = digest.summary_view(mgr, on_date)
        else:
            text, kb = digest.promises_view(mgr, on_date)
        payload = {"text": text, "reply_markup": kb}
    else:
        payload = {"text": ("🔧 Проверка доставки уведомлений. Если вы это "
                            "видите — бот может писать вам в личку."),
                   "reply_markup": None}
    try:
        await bot.send_message(tid, payload["text"],
                               reply_markup=payload["reply_markup"])
    except Exception as exc:  # noqa: BLE001
        status, err = classify_send_error(exc)
        db.log_push(db.PUSH_TEST, status, target_kind="manager", target_id=tid,
                    note=f"push_test:{kind}", error=err)
        if status == db.PUSH_BLOCKED:
            db.set_can_dm(tid, False)
        return web.json_response({"ok": False, "status": status, "error": err})
    db.log_push(db.PUSH_TEST, db.PUSH_SENT, target_kind="manager", target_id=tid,
                note=f"push_test:{kind}")
    db.set_can_dm(tid, True)
    return web.json_response({"ok": True, "status": db.PUSH_SENT})


async def _admin_digest_preview(request: web.Request) -> web.Response:
    """Предпросмотр сводки руководителя (текст) для пульта: ?telegram_id=.
    Плюс диагностика состава: почему сводка такая и дойдёт ли она."""
    _admin(request)
    import digest
    try:
        tid = int(request.query.get("telegram_id", ""))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="telegram_id")
    mgr = db.get_manager(tid)
    source = "explicit"
    if mgr and (mgr.get("direction") or "").strip():
        source = "direction"
    if not mgr and tid == ADMIN_ID:
        # Владелец без записи в managers — ровно его жалоба «у меня не работает»
        mgr = digest.owner_scope(tid)
        source = "owner_synthetic"
    if not mgr:
        raise web.HTTPNotFound(text="manager")
    today = digest.today_iso()
    text, _ = digest.summary_view(mgr, today)
    employees = digest.sorted_employees(mgr)
    known = db.registry_name_index()
    exact_names = set(known.values())
    matched_exact = sum(1 for e in employees if e in exact_names)
    matched_key = sum(1 for e in employees
                      if e not in exact_names and db._name_key(e) in known)
    unknown = [e for e in employees if db._name_key(e) not in known]
    _pw = db.promise_window()
    return web.json_response({"ok": True, "text": text, "diagnostics": {
        "manager": {"telegram_id": tid, "name": mgr.get("name") or "",
                    "direction": mgr.get("direction") or "",
                    "can_dm": (db.get_manager(tid) or {}).get("can_dm"),
                    "is_owner": tid == ADMIN_ID},
        "employees": employees,
        "employees_source": source,
        "matched_exact": matched_exact,
        "matched_by_name_key": matched_key,
        "unknown_in_registry": unknown,
        "spelling_mismatch": db.manager_spelling_mismatches(mgr),
        "processed_today": sum(len(db.persons_processed(e, today))
                               for e in employees),
        # То же окно, что в рассылке и в приложении (db.promise_window):
        # диагностика в пульте обязана показывать ровно то, что уйдёт людям.
        "overdue_promises": sum(
            len(db.overdue_promises(e, today, max_days=_pw[0], agent_only=_pw[1]))
            for e in employees),
        "report_chats": {e: db.report_chats_for(e) for e in employees},
        "last_run": {"digest": db.get_setting("digest_last_run") or "",
                     "promise": db.get_setting("promise_last_run") or ""},
    }})


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
    if not bot:
        from notify import bot_missing
        bot_missing(bot)
        return web.json_response({"ok": True, "delivery": "off"})
    if not ADMIN_ID:
        raise web.HTTPServiceUnavailable(text="не задан ADMIN_ID")
    import html as _html
    await bot.send_message(ADMIN_ID, _html.escape(text[:3500]))
    return web.json_response({"ok": True, "delivery": "sent"})


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


# --- Старый адрес: только выгрузка того, что застряло на телефонах -----------
# 30.09.2026, владелец: после переезда 23–24.09 часть выездников работала в старом
# приложении (hardbot2.…nip.io и мини-приложение старого бота), и выезды легли в
# очередь телефона. Старый адрес снова ведёт сюда — Caddy на Атланте ставит
# заголовок X-Hard-Drain: 1, — но работать через него нельзя: пропускаются только
# досылка очереди (выезды, фото, метки, заметки), вход по ссылке и сама
# оболочка. Всё остальное — 410 drain_only, и оболочка показывает экран выгрузки.
# Пишет сразу в рабочую базу: синхронизировать нечего.
DRAIN_HEADER = "X-Hard-Drain"
_DRAIN_GET = frozenset({"/", "/version.json", "/sw.js", "/manifest.webmanifest",
                        "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
                        "/favicon.ico"})
_DRAIN_GET_PREFIXES = ("/vendor/", "/fonts/")
_DRAIN_UPLOADS = frozenset({"/api/save", "/api/photo", "/api/marker", "/api/note",
                            "/api/note_delete"})
_DRAIN_POST = _DRAIN_UPLOADS | {"/api/redeem"}


def _is_drain(request: web.Request) -> bool:
    headers = getattr(request, "headers", None) or {}
    if headers.get(DRAIN_HEADER) == "1":
        return True
    # Дев-стенд целиком в режиме старого адреса — для студии и снимков.
    return DEV and os.getenv("HARD_DRAIN_ALL") == "1"


def _drain_payload() -> dict:
    return {"newUrl": (os.getenv("MINIAPP_URL") or "").rstrip("/")}


@web.middleware
async def _drain(request: web.Request, handler):
    if not _is_drain(request):
        return await handler(request)
    path, method = request.path, request.method
    allowed = (method == "OPTIONS"
               or (method in ("GET", "HEAD")
                   and (path in _DRAIN_GET or path.startswith(_DRAIN_GET_PREFIXES)))
               or (method == "POST" and path in _DRAIN_POST))
    if not allowed:
        if method in ("GET", "HEAD") and not path.startswith(("/api/", "/admin/")):
            raise web.HTTPFound("/")
        return web.json_response({"error": "drain_only", **_drain_payload()}, status=410,
                                 headers={"Cache-Control": "no-store"})
    resp = await handler(request)
    if method == "POST" and path in _DRAIN_UPLOADS and 200 <= resp.status < 300:
        try:   # журнал: кто и что досылал со старого адреса
            db.audit_access(_uid(request) or 0, "drain_upload", detail=path)
        except Exception:  # noqa: BLE001 — журнал не важнее досланного выезда
            pass
    return resp


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


# Сжатие крупных текстовых ответов на стороне приложения. Контур компании
# опубликован через обратный туннель, и самое узкое место — туннель из офиса до
# публикатора (~0,85 МБ/с). Caddy на публикаторе сжимает уже ПОСЛЕ туннеля, и
# полная выгрузка базы (46 МБ JSON) шла по туннелю как есть — 4,6 минуты на
# «Проверить соответствия» в пульте. Сжатая — 3,4 МБ. Мелочь не сжимаем (выгоды
# нет), фото и потоковые ответы — тоже: они уже сжаты или идут потоком.
_COMPRESS_MIN_BYTES = 32 * 1024
_COMPRESSIBLE_TYPES = ("application/json", "text/", "application/javascript")


@web.middleware
async def _compress(request: web.Request, handler):
    resp = await handler(request)
    if (type(resp) is web.Response
            and "gzip" in (request.headers.get("Accept-Encoding") or "").lower()
            and not resp.headers.get("Content-Encoding")
            and (resp.content_type or "").startswith(_COMPRESSIBLE_TYPES)
            and resp.body is not None
            and len(resp.body) >= _COMPRESS_MIN_BYTES):
        resp.enable_compression(web.ContentCoding.gzip)
    return resp


# Защитные заголовки. Кликджекинг: вредная страница прячет наш адрес в
# невидимый фрейм и наводит руководителя на «одобрить»/«снять»/«отозвать вход»
# — страница уже авторизована токеном устройства (ревью 25.09). Caddy на
# публикаторе заголовков не ставит, поэтому их ставит само приложение.
#
# Мини-приложение внутри web.telegram.org живёт во фрейме, поэтому запрет
# фреймов зависит от того, включён ли Телеграм. CSP — только frame-ancestors:
# страница карты грузит maplibre с CDN, а оболочка держится на встроенных
# скриптах, и полный CSP сломал бы обе.
_TELEGRAM_FRAME_ANCESTORS = ("frame-ancestors 'self' https://web.telegram.org "
                             "https://*.telegram.org")


def _telegram_on(app: web.Application) -> bool:
    return app.get("bot") is not None or bool((os.getenv("BOT_TOKEN") or "").strip())


async def _security_headers(request: web.Request, resp: web.StreamResponse) -> None:
    """Сигнал on_response_prepare, а не middleware: он срабатывает прямо перед
    отправкой заголовков у КАЖДОГО ответа — и у брошенных HTTPException
    (401/404/429), и у потоковых (FileResponse карты с Range), которые
    middleware видит уже отправленными. Поставленное хендлером не трогаем."""
    headers = resp.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "no-referrer")
    if _telegram_on(request.app):
        headers.setdefault("Content-Security-Policy", _TELEGRAM_FRAME_ANCESTORS)
    else:
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")


# Предел тела запроса. Прокси перед приложением режут тело и сами (встроенный
# Caddy — 30 МБ), но на порту приложения было 256 МБ: один запрос занимал
# память сервера целиком. 64 МБ — с запасом под загрузку реестра из пульта
# и фото (PHOTO_MAX_BYTES).
CLIENT_MAX_SIZE = 64 * 1024 * 1024


def build_app(bot=None) -> web.Application:
    app = web.Application(client_max_size=CLIENT_MAX_SIZE,
                          middlewares=[_drain, _cors, _compress])
    app.on_response_prepare.append(_security_headers)
    app["bot"] = bot
    app.router.add_get("/", _index)
    # Короткая ссылка входа. Путь из одной буквы — чтобы адрес целиком помещался
    # на экране телефона и читался вслух: «слэш и — и двенадцать знаков».
    app.router.add_get("/i/{code}", _app_login)
    app.router.add_get("/version.json", _version)
    app.router.add_get("/sw.js", _sw)
    app.router.add_get("/manifest.webmanifest", _manifest)
    # iOS при «На экран Домой» ищет apple-touch-icon; без него ярлык получает
    # скриншот страницы вместо иконки «Хард».
    app.router.add_get("/apple-touch-icon.png", _apple_touch_icon)
    app.router.add_get("/apple-touch-icon-precomposed.png", _apple_touch_icon)
    app.router.add_get("/favicon.ico", _apple_touch_icon)
    # Локально захостенные ассеты оболочки (Telegram SDK, иконки PWA) — same-origin,
    # чтобы миниапп поднимался в оффлайне без внешних CDN (их кэширует service worker).
    app.router.add_static("/vendor/", path=str(MINIAPP_DIR / "vendor"))
    # Шрифты облика «Почерк» (OFL) — свои, same-origin: service worker держит их
    # в кэше вместе с оболочкой, внешних CDN у выездника нет.
    app.router.add_static("/fonts/", path=str(MINIAPP_DIR / "fonts"))
    app.router.add_get("/admin/stats", _admin_stats)
    app.router.add_post("/admin/import", _admin_import)
    app.router.add_post("/admin/import.json", _admin_import_json)
    app.router.add_post("/admin/import/preview.json", _admin_import_preview_json)
    app.router.add_post("/admin/import/authorize.json", _admin_import_authorize)
    app.router.add_get("/admin/registry_columns.json", _admin_registry_columns)
    app.router.add_post("/admin/registry_columns.json",
                        _admin_registry_columns_save)
    app.router.add_post("/admin/device/enroll.json", _admin_device_enroll)
    app.router.add_post("/admin/device/redeem.json", _admin_device_redeem)
    app.router.add_get("/admin/key_check.json", _admin_key_check)

    # Панель обслуживания. Отдельный путь и отдельная роль: сюда не пускают ни
    # мастер-ключом, ни токеном пульта. Ни одна ручка здесь не читает должников.
    app.router.add_get("/ops/", _ops_page)
    app.router.add_get("/ops/state.json", _ops_state)
    app.router.add_post("/ops/run.json", _ops_run)
    app.router.add_get("/ops/result.json", _ops_result)
    app.router.add_get("/ops/devices.json", _ops_devices)
    app.router.add_post("/ops/code.json", _ops_code)
    app.router.add_post("/ops/device/revoke.json", _ops_revoke)
    app.router.add_get("/admin/devices.json", _admin_devices)
    app.router.add_post("/admin/device/revoke.json", _admin_device_revoke)
    app.router.add_post("/admin/push_test", _admin_push_test)
    app.router.add_get("/admin/recalled.json", _admin_recalled)
    app.router.add_get("/admin/chats", _admin_chats)
    app.router.add_post("/admin/chats.set", _admin_chats_set)
    app.router.add_get("/admin/export", _admin_export)
    app.router.add_get("/admin/export.json", _admin_export_json)
    app.router.add_get("/admin/enrichment/ledger.json", _admin_enrichment_ledger)
    app.router.add_post("/admin/enrichment/ledger.json", _admin_enrichment_ledger)
    app.router.add_get("/admin/markers.xlsx", _admin_markers_xlsx)
    app.router.add_get("/admin/markers.json", _admin_markers_json)
    app.router.add_get("/admin/employees", _admin_employees)
    app.router.add_post("/admin/visit_plan.json", _admin_visit_plan)
    app.router.add_get("/admin/emp_clients", _admin_emp_clients)
    app.router.add_post("/admin/unassign", _admin_unassign)
    app.router.add_get("/admin/guests", _admin_guests)
    app.router.add_post("/admin/bind", _admin_bind)
    app.router.add_post("/admin/unbind", _admin_unbind)
    app.router.add_post("/admin/bot_token", _admin_bot_token)
    app.router.add_post("/admin/geocode", _admin_geocode)
    app.router.add_get("/admin/priority", _admin_priority)
    app.router.add_get("/admin/activity", _admin_activity)
    app.router.add_get("/admin/today", _admin_today)
    app.router.add_post("/admin/notify", _admin_notify)
    app.router.add_post("/admin/restart", _admin_restart)
    app.router.add_post("/admin/reset", _admin_reset)
    app.router.add_get("/admin/employee_cities.json", _admin_employee_cities)
    app.router.add_post("/admin/employee_cities.json", _admin_employee_cities)
    app.router.add_get("/admin/managers", _admin_managers)
    app.router.add_post("/admin/manager", _admin_manager_save)
    app.router.add_post("/admin/manager_delete", _admin_manager_delete)
    app.router.add_get("/admin/digest_settings", _admin_digest_settings)
    app.router.add_post("/admin/digest_settings", _admin_digest_settings)
    app.router.add_get("/admin/photo_geo", _admin_photo_geo)
    app.router.add_post("/admin/photo_geo", _admin_photo_geo)
    app.router.add_get("/admin/digest_preview", _admin_digest_preview)
    app.router.add_get("/admin/period", _admin_period)
    app.router.add_post("/admin/period", _admin_period)
    app.router.add_get("/manager/map", _manager_map)
    app.router.add_get("/manager/map_data", _manager_map_data)
    app.router.add_get("/tiles/basemap.pmtiles", _tiles)
    app.router.add_get("/tiles/raster/{z}/{x}/{y}.png", _raster_tile)
    app.router.add_get("/api/manager/summary", _manager_summary)
    app.router.add_get("/api/manager/employee", _manager_employee_view)
    app.router.add_get("/api/manager/person", _manager_person)
    app.router.add_get("/api/manager/period", _manager_period)
    app.router.add_get("/api/manager/promises", _manager_promises)
    app.router.add_get("/api/manager/map_data", _manager_app_map_data)
    app.router.add_get("/api/manager/photos", _manager_photos)
    app.router.add_get("/api/manager/photo", _manager_photo_file)
    app.router.add_get("/api/manager/stats", _manager_stats)
    app.router.add_get("/api/manager/stats_employee", _manager_stats_employee)
    app.router.add_get("/api/manager/plan", _manager_plan)
    app.router.add_post("/api/manager/plan", _manager_plan)
    app.router.add_get("/api/manager/pushes", _manager_pushes)
    app.router.add_post("/api/manager/pushes", _manager_pushes)
    app.router.add_get("/api/manager/app_links", _manager_app_links)
    app.router.add_post("/api/manager/app_link", _manager_app_link)
    app.router.add_post("/api/manager/app_revoke", _manager_app_revoke)
    app.router.add_get("/api/manager/workload", _manager_workload)
    app.router.add_get("/api/manager/binds", _manager_binds)
    app.router.add_post("/api/manager/binds", _manager_binds)
    app.router.add_get("/api/manager/cases", _manager_cases)
    app.router.add_get("/api/manager/stats.xlsx", _manager_stats_xlsx)
    app.router.add_post("/api/manager/device/enroll", _manager_device_enroll)
    app.router.add_post("/api/agent/device/enroll", _agent_device_enroll)
    app.router.add_post("/api/agent/device/logout", _agent_device_logout)
    app.router.add_get("/admin/agent_devices.json", _admin_agent_devices)
    app.router.add_post("/admin/agent_device/revoke.json", _admin_agent_device_revoke)
    app.router.add_get("/api/manager/device/me", _manager_device_me)
    app.router.add_post("/api/manager/device/logout", _manager_device_logout)
    app.router.add_get("/api/manager/activity", _manager_activity)
    app.router.add_get("/api/control/overview", _control_overview)
    app.router.add_get("/api/control/soft", _control_soft)
    app.router.add_get("/api/control/assignments", _control_assignments)
    app.router.add_get("/api/control/access", _control_access)
    app.router.add_get("/api/control/day", _control_day)
    app.router.add_get("/api/control/geo_days", _control_geo_days)
    app.router.add_get("/admin/control/overview", _admin_control_overview)
    app.router.add_get("/admin/control/soft", _admin_control_soft)
    app.router.add_get("/admin/control/flags", _admin_control_flags)
    app.router.add_get("/admin/control/callbacks", _admin_control_callbacks)
    app.router.add_get("/admin/control/assignments", _admin_control_assignments)
    app.router.add_get("/admin/control/access", _admin_control_access)
    app.router.add_get("/admin/control/day", _admin_control_day)
    app.router.add_get("/admin/control/geo_days", _admin_control_geo_days)
    app.router.add_get("/api/control/flags", _control_flags)
    app.router.add_get("/api/control/callbacks", _control_callbacks)
    app.router.add_post("/api/control/callback", _control_callback)
    app.router.add_get("/api/control/export.xlsx", _control_export)
    app.router.add_get("/admin/manager_devices.json", _admin_manager_devices)
    app.router.add_post("/admin/manager_device/revoke.json", _admin_manager_device_revoke)
    app.router.add_get("/api/bootstrap", _bootstrap)
    app.router.add_get("/api/person", _person)
    app.router.add_get("/api/cards", _cards)
    app.router.add_post("/api/client-event", _client_event)
    app.router.add_get("/api/route", _route)
    app.router.add_get("/api/route_to", _route_to)
    app.router.add_get("/api/geocode", _geocode_free)
    app.router.add_get("/api/geo/suggest", _geo_suggest)
    app.router.add_post("/api/track", _track)
    app.router.add_post("/api/act_as", _act_as)
    app.router.add_get("/api/route_day", _route_day)
    app.router.add_post("/api/save", _save)
    app.router.add_post("/api/photo", _photo)
    app.router.add_post("/api/receipt", _receipt)
    app.router.add_get("/api/manager/receipt_photo", _manager_receipt_file)
    app.router.add_post("/api/voice", _voice)
    app.router.add_post("/api/lang", _lang)
    app.router.add_post("/api/redeem", _redeem)
    app.router.add_post("/api/logout", _logout)
    app.router.add_post("/admin/app_revoke", _admin_app_revoke)
    app.router.add_post("/admin/app_link.json", _admin_app_link)
    app.router.add_post("/admin/app_links_bulk.json", _admin_app_links_bulk)
    app.router.add_post("/admin/app_revoke_bulk.json", _admin_app_revoke_bulk)
    app.router.add_get("/admin/stats_book.xlsx", _admin_stats_book)
    app.router.add_get("/admin/app_links.json", _admin_app_links)
    app.router.add_post("/api/bind", _bind)
    app.router.add_post("/api/marker", _marker)
    app.router.add_get("/api/markers", _markers)
    app.router.add_post("/api/note", _note_add)
    app.router.add_get("/api/notes", _notes)
    app.router.add_post("/api/note_delete", _note_delete)
    # Карточка визита теперь может ждать опоздавшее фото до минуты. При
    # остановке (выкладка — это перезапуск контейнера) даём фоновым рассылкам
    # договорить: иначе выкладка в разгар рабочего дня молча съедала бы
    # карточки визитов, закрытых в последнюю минуту.
    app.on_cleanup.append(lambda _app: wait_visit_notify())
    return app



class _PathOnlyAccessLogger(AbstractAccessLogger):
    """Access-лог без query-строки: в ней ездят адреса должников и координаты.
    Код ссылки входа (`/i/<код>`) — живой пропуск, а журнал видит инженер в
    `/ops`: код маскируется."""

    def log(self, request, response, time):
        path = request.path
        if path.startswith("/i/"):
            path = "/i/" + _mask_code(path[3:])
        self.logger.info("%s %s %s %s %.3f",
                         request.remote or "-", request.method, path,
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
    # Автономный запуск для локального теста миниаппа без бота: APP_DEV=1 python web.py
    db.init_db()
    web.run_app(build_app(), host="127.0.0.1", port=int(os.getenv("MINIAPP_PORT", "8080")))
