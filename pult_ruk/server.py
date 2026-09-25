"""Местный сервер пульта руководителя: страница и весь локальный API.

Браузер ходит ТОЛЬКО сюда, на 127.0.0.1. Наружу — в «Хард» — ходит этот
процесс, и только он знает токен. Так устроено не из красоты: страницу
открывают и с телефона через тоннель, а телефон с токеном в кармане — это
токен в истории браузера, в логах cloudflare и в чужих руках.

Правила ответов, на которые опирается страница:

* всё — JSON, беда — `{"error": "по-русски"}`, и текст этой строки годится,
  чтобы прочитать его вслух аналитику;
* ошибка по существу (нет связи, ссылка протухла, cloudflared не установлен)
  приезжает с кодом 200: страница показывает текст, а не «не удалось
  загрузить». Кодами остаются только «сюда нельзя» (401/403) и «такого нет»
  (404) — их страница разбирает отдельно;
* данные, полученные в прошлый раз, возвращаются и при пропавшей связи — с
  `stale:true` и временем, когда связь потерялась.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import socket
import webbrowser
from pathlib import Path

from aiohttp import web

from . import session as gate_mod
from .hard import Hard, HardTrouble, PUSH_KEYS
from .settings import Settings, load_settings
from .tunnel import Tunnel, TunnelTrouble

WEB_DIR = Path(__file__).resolve().parent / "web"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Что можно отдать из web/. Белый список, а не «всё, что лежит»: в папке рядом
# однажды окажется чей-нибудь черновик с заметками, и раздавать его наружу в
# тоннель никто не собирался.
SERVABLE = {".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".mjs": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".webmanifest": "application/manifest+json",
            ".woff2": "font/woff2"}

XLSX_TYPE = ("application/vnd.openxmlformats-officedocument"
             ".spreadsheetml.sheet")

OPEN_PATHS = {"/api/state", "/api/pin"}      # сюда пускают без кода-пропуска
CONSOLE_ONLY = {"/api/link", "/api/out", "/api/phone", "/api/server"}

NEED_PIN = "Сначала введите код-пропуск — его показывает пульт на компьютере."
ONLY_PC = ("Это делается на компьютере, где открыт пульт: с телефона доступ "
           "и ссылку входа не меняют.")

log = logging.getLogger("пульт-руководителя")
_qr_cache: dict[str, str] = {}


def _json(payload: dict, status: int = 200) -> web.Response:
    """JSON без экранирования кириллицы (вдвое короче на слабом канале) и без
    кэша: на экране персональные данные должников, им незачем оседать в кэше
    браузера телефона."""
    resp = web.json_response(
        payload, status=status,
        dumps=lambda obj: json.dumps(obj, ensure_ascii=False))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def qr_data_url(url: str) -> str | None:
    """QR адреса тоннеля картинкой в самой странице. Нет segno — нет картинки,
    и страница показывает адрес текстом: набрать руками дольше, но можно."""
    if not url:
        return None
    if url in _qr_cache:
        return _qr_cache[url]
    try:
        import io

        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=8, border=2,
                                    dark="#101418", light="#ffffff")
    data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    _qr_cache.clear()          # адрес живёт один сеанс, копить незачем
    _qr_cache[url] = data
    return data


def _date(request, name: str = "date") -> str:
    """Дата из запроса. Кривую не пересылаем «Харду»: он ответит 400, а человек
    увидит «сервер не понял запрос пульта» вместо понятного «дата не та»."""
    raw = (request.query.get(name) or "").strip()
    if not raw:
        return ""
    if not DATE_RE.match(raw):
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "Дата должна быть вида 2026-09-21."},
                            ensure_ascii=False),
            content_type="application/json")
    return raw


def _trouble_json(trouble: HardTrouble) -> web.Response:
    """Беда «Харда» → ответ странице. Именно 200: текст беды и есть содержимое
    экрана, а не признак того, что пульт сломался."""
    return _json({"error": trouble.text, "kind": trouble.kind,
                  "stale": False})


# --- сборка приложения -------------------------------------------------------
def create_app(*, settings: Settings | None = None, hard: Hard | None = None,
               tunnel=None, gate=None, port: int = 0,
               web_dir: Path = WEB_DIR) -> web.Application:
    """Приложение пульта. Всё, что ходит наружу, передаётся снаружи — так тесты
    ставят вместо «Харда» и cloudflared свои заглушки и не трогают сеть."""
    settings = settings if settings is not None else load_settings()
    hard = hard if hard is not None else Hard(settings)
    tunnel = tunnel if tunnel is not None else Tunnel(port)
    gate = gate if gate is not None else gate_mod.Gate()

    app = web.Application(middlewares=[guard_middleware])
    app["settings"] = settings
    app["hard"] = hard
    app["tunnel"] = tunnel
    app["gate"] = gate
    app["web_dir"] = Path(web_dir)
    app["port"] = port

    def on_raid(text: str) -> None:
        """Пять промахов кода — гасим тоннель. Делает это Gate, а поднять
        корутину из синхронного кода можем только мы."""
        log.warning(text)
        try:
            asyncio.get_running_loop().create_task(tunnel.stop())
        except RuntimeError:
            tunnel.stop_now()

    gate._on_raid = on_raid

    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/link", api_link)
    app.router.add_post("/api/pin", api_pin)
    app.router.add_get("/api/today", api_today)
    app.router.add_get("/api/employee", api_employee)
    app.router.add_get("/api/promises", api_promises)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/report.xlsx", api_report)
    app.router.add_get("/api/pushes", api_pushes)
    app.router.add_post("/api/pushes", api_pushes)
    app.router.add_get("/api/team_links", api_team_links)
    app.router.add_post("/api/team_link", api_team_link)
    app.router.add_post("/api/phone", api_phone)
    app.router.add_post("/api/server", api_server)
    app.router.add_post("/api/out", api_out)
    app.router.add_get("/", page)
    app.router.add_get("/{name:[^/]+}", static_file)

    app.on_cleanup.append(_shutdown)
    return app


async def _shutdown(app: web.Application) -> None:
    await app["tunnel"].stop()
    await app["hard"].close()


@web.middleware
async def guard_middleware(request: web.Request, handler):
    """Один вход для всех проверок: чужое имя, чужой сайт, чужой телефон.

    Держим это в одном месте намеренно. Проверка доступа, размазанная по
    десяти обработчикам, однажды забывается в одиннадцатом — и именно он
    отдаёт наружу портфель направления.
    """
    gate = request.app["gate"]
    path = request.path

    # 1. Имя, под которым нас открыли. Чужое — значит, кто-то навёл своё имя
    #    на наш 127.0.0.1 и открыл пульт из своей вкладки.
    if not gate.known_host(request):
        return _json({"error": "Пульт открыт не по своему адресу."}, status=403)

    # 2. Чужая страница не имеет права слать сюда POST от имени хозяина
    #    (браузер отправит его молча, ответ не покажет, но сделанное останется).
    if request.method not in ("GET", "HEAD") and not own_page(request):
        return _json({"error": "Запрос пришёл с чужой страницы."}, status=403)

    if path in OPEN_PATHS or not path.startswith("/api/"):
        return await handler(request)

    # 3. Телефон из тоннеля — только после кода-пропуска.
    if not gate.allowed(request):
        return _json({"error": NEED_PIN, "need_pin": True}, status=401)

    # 4. Токен и тоннель — дело того, кто сидит за этим компьютером.
    if path in CONSOLE_ONLY and not gate.is_console(request):
        return _json({"error": ONLY_PC}, status=403)

    return await handler(request)


def own_page(request: web.Request) -> bool:
    """Меняющий запрос пришёл со страницы самого пульта. Сомнение — «нет».

    Свой origin — адрес, под которым пульт открыт: `127.0.0.1:<порт>` у своего
    браузера или имя живого тоннеля у телефона (cloudflared отдаёт его в Host,
    а чужой Host отсёк шаг 1 — `known_host` пускает только эти два). Origin
    браузер ставит на любой POST, Sec-Fetch-Site — на любой запрос, и страница
    не может подделать ни тот, ни другой. Раньше запрос БЕЗ Origin проходил
    молча: страница, сумевшая его не поставить, перепривязывала пульт к чужому
    серверу (`/api/link`) или выводила из него (`/api/out`). Теперь нужен либо
    свой Origin, либо — когда Origin нет или он «null» — слово самого браузера
    «same-origin». Чужой Origin не спасает ничто.
    """
    origin = request.headers.get("Origin") or ""
    mine = {f"http://{request.host}", f"https://{request.host}"}
    if origin in mine:
        return True
    if origin not in ("", "null"):
        return False
    site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    return site == "same-origin"


# --- страница ----------------------------------------------------------------
async def page(request: web.Request) -> web.Response:
    return await _send(request, "index.html")


async def static_file(request: web.Request) -> web.Response:
    return await _send(request, request.match_info["name"])


async def _send(request: web.Request, name: str) -> web.Response:
    """Файл из web/. Имя берём только последней частью пути: «../» здесь
    означает попытку прочитать чужой файл через тоннель."""
    path = (request.app["web_dir"] / Path(name).name)
    kind = SERVABLE.get(path.suffix.lower())
    if kind is None or not path.is_file():
        return _json({"error": f"Файл «{Path(name).name}» пульту не найден. "
                               "Похоже, папку web скопировали не целиком."},
                     status=404)
    resp = web.FileResponse(path)
    resp.headers["Content-Type"] = kind
    # Страница меняется вместе с пультом; телефон, закэшировавший старую,
    # выглядит как сломанный пульт.
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# --- состояние ---------------------------------------------------------------
def state_payload(request) -> dict:
    hard, tunnel, gate = (request.app["hard"], request.app["tunnel"],
                          request.app["gate"])
    console = gate.is_console(request)
    if not console and not gate.has_session(request):
        # Телефону до кода-пропуска не показываем ничего: ни имени, ни адреса
        # «Харда», ни самого кода. Форму ответа сохраняем, чтобы страница
        # разбирала её одним и тем же кодом.
        return {"ready": hard.ready, "need_pin": True,
                "manager": {"name": "", "scope": ""}, "hard_url": "",
                "phone": {"on": True, "url": "", "pin": "", "qr": None}}
    phone = tunnel.state()
    # Список серверов — только экрану компьютера: телефону чужие адреса
    # знать незачем, а переключение и вообще дело того, кто за пультом.
    servers = []
    if console:
        from urllib.parse import urlsplit
        for url, entry in (request.app["settings"].get("servers") or {}).items():
            host = urlsplit(url).netloc or url
            servers.append({"url": url, "host": host,
                            "ready": bool(entry.get("token"))})
        servers.sort(key=lambda s: (s["url"] != hard.base, s["host"]))
    return {
        "ready": hard.ready,
        "need_pin": False,
        "manager": {"name": hard.manager.get("name") or "",
                    "scope": hard.manager.get("scope") or ""},
        "hard_url": hard.base,
        "lost_at": hard.lost_at,
        "servers": servers,
        "phone": {
            "on": phone["on"],
            "url": phone["url"],
            # ПИН — только на экране ПК: его для того и показывают, чтобы
            # набрать на телефоне, а на самом телефоне он уже не нужен.
            "pin": gate.pin if console else "",
            "qr": qr_data_url(phone["url"]) if console else None,
            "ready": phone["ready"],
            "error": phone["error"],
            "alarm": gate.alarm,
        },
    }


async def api_state(request: web.Request) -> web.Response:
    return _json(state_payload(request))


async def api_link(request: web.Request) -> web.Response:
    hard = request.app["hard"]
    body = await _body(request)
    raw = str(body.get("link") or body.get("code") or "").strip()
    try:
        out = await hard.redeem(raw)
    except HardTrouble as trouble:
        return _trouble_json(trouble)
    log.info("вход по ссылке принят, доступ на %s суток", out.get("days"))
    return _json({"ok": True, "days": out.get("days")})


async def api_pin(request: web.Request) -> web.Response:
    """Код-пропуск с телефона. Ответ всегда 200: страница показывает текст —
    сколько попыток осталось, — а не «ошибка 403»."""
    gate = request.app["gate"]
    if gate.is_console(request):
        return _json({"ok": True})          # своему браузеру код не нужен
    body = await _body(request)
    ok, text, key = gate.check(str(body.get("pin") or ""))
    if not ok:
        return _json({"ok": False, "error": text})
    resp = _json({"ok": True})
    gate.put_cookie(resp, key)
    return resp


async def api_server(request: web.Request) -> web.Response:
    """Переключиться на другой известный сервер (прод/контур компании).

    Новый сервер заводит только ссылка входа — здесь мы лишь выбираем из уже
    знакомых, поэтому чужой адрес подставить нельзя.
    """
    hard = request.app["hard"]
    body = await _body(request)
    url = str(body.get("url") or "").rstrip("/")
    try:
        hard.switch(url)
    except HardTrouble as trouble:
        return _trouble_json(trouble)
    log.info("переключение на сервер %s", url)
    return _json({"ok": True, "hard_url": hard.base})


async def api_out(request: web.Request) -> web.Response:
    """«Выйти из пульта»: забыть токен и погасить тоннель. Одно без другого
    было бы обманом — оставшийся тоннель ведёт на пульт, который «вышел»."""
    request.app["hard"].forget()
    await request.app["tunnel"].stop()
    request.app["gate"].close()
    resp = _json({"ok": True})
    request.app["gate"].drop_cookie(resp)
    return resp


# --- экраны ------------------------------------------------------------------
async def api_today(request: web.Request) -> web.Response:
    return _json(await request.app["hard"].call("today", {"date": _date(request)}))


async def api_employee(request: web.Request) -> web.Response:
    emp = (request.query.get("emp") or request.query.get("employee") or "").strip()
    if not emp:
        return _json({"error": "Не сказано, чью карточку открыть."}, status=400)
    return _json(await request.app["hard"].call(
        "employee", {"employee": emp[:200], "date": _date(request)}))


async def api_promises(request: web.Request) -> web.Response:
    return _json(await request.app["hard"].call("promises", {"date": _date(request)}))


async def api_stats(request: web.Request) -> web.Response:
    return _json(await request.app["hard"].call("stats", _range(request)))


def _range(request) -> dict:
    """Период для отчёта. `preset` («today», «7d», «30d», «month») пропускаем
    насквозь: «Хард» считает границы сам, по своему календарю. Клиентские даты
    в утренние часы давали пустой отчёт — контейнер живёт по UTC, а телефон
    руководителя по +5."""
    out = {"from": _date(request, "from"), "to": _date(request, "to")}
    preset = (request.query.get("preset") or "").strip()
    if preset in {"today", "yesterday", "7d", "30d", "month", "custom"}:
        out["preset"] = preset
    shift = (request.query.get("shift") or "").strip()
    if shift.lstrip("-").isdigit():
        out["shift"] = shift
    return out


async def api_report(request: web.Request) -> web.Response:
    """Книга Excel — единственный ответ пульта, который не JSON. Льём её
    кусками: за месяц по направлению это десятки мегабайт, и держать их в
    памяти рабочего ноутбука незачем."""
    try:
        upstream = await request.app["hard"].book(_range(request))
    except HardTrouble as trouble:
        # Кнопку жмут переходом по адресу — человек увидит эту строку прямо
        # на пустой вкладке. Пусть она будет по-русски.
        return _json({"error": trouble.text, "kind": trouble.kind})
    name = "отчёт.xlsx"
    disp = upstream.headers.get("Content-Disposition") or ""
    found = re.search(r"filename=([^;]+)", disp)
    if found:
        name = found.group(1).strip().strip('"')
    out = web.StreamResponse(headers={
        "Content-Type": XLSX_TYPE,
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store"})
    try:
        await out.prepare(request)
        async for chunk in upstream.content.iter_chunked(64 * 1024):
            await out.write(chunk)
        await out.write_eof()
    finally:
        upstream.release()
    return out


# --- рубильники и входы команды ----------------------------------------------
async def api_pushes(request: web.Request) -> web.Response:
    """Свои рубильники рассылок: GET — прочитать, POST {key, value} — повернуть."""
    hard = request.app["hard"]
    if request.method == "POST":
        body = await _body(request)
        key, value = str(body.get("key") or ""), body.get("value")
        if key not in PUSH_KEYS or not isinstance(value, bool):
            return _json({"error": "Неизвестный рубильник."}, status=400)
        log.info("рубильник %s → %s (кем: %s)", key, value,
                 hard.manager.get("name") or "?")
        call = hard.set_push(key, value)
    else:
        call = hard.pushes()
    try:
        return _json(await call)
    except HardTrouble as trouble:
        return _trouble_json(trouble)


async def api_team_links(request: web.Request) -> web.Response:
    try:
        return _json(await request.app["hard"].team_links())
    except HardTrouble as trouble:
        return _trouble_json(trouble)


async def api_team_link(request: web.Request) -> web.Response:
    """Выдать или отозвать вход своему сотруднику. Выдача возвращает ссылку
    и QR картинкой — руководитель пересылает её человеку сам."""
    body = await _body(request)
    employee = str(body.get("employee") or "").strip()
    action = "revoke" if body.get("action") == "revoke" else "issue"
    if not employee:
        return _json({"error": "Не сказано, кому выдать вход."}, status=400)
    try:
        out = await request.app["hard"].team_link(employee, action)
    except HardTrouble as trouble:
        return _trouble_json(trouble)
    if action == "issue" and out.get("url"):
        out["qr"] = qr_data_url(out["url"])
        log.info("выдан вход: %s (кем: %s)", employee,
                 (request.app["hard"].manager.get("name") or "?"))
    else:
        log.info("отозван вход: %s (кем: %s)", employee,
                 (request.app["hard"].manager.get("name") or "?"))
    return _json(out)


async def api_phone(request: web.Request) -> web.Response:
    """Включить или погасить тоннель. Ответ — то же, что в `/api/state`, но
    только про телефон: {on, url, pin, qr}."""
    tunnel, gate = request.app["tunnel"], request.app["gate"]
    body = await _body(request)
    want = bool(body.get("on", True))
    if not want:
        await tunnel.stop()
        gate.close()
        return _json({"on": False, "url": "", "pin": "", "qr": None,
                      "ready": tunnel.available})
    gate.reopen()
    try:
        url = await tunnel.start()
    except TunnelTrouble as trouble:
        return _json({"on": False, "url": "", "pin": "", "qr": None,
                      "ready": tunnel.available, "error": trouble.text})
    gate.tunnel_host = url.split("//", 1)[-1]
    log.info("телефон включён: %s", url)
    return _json({"on": True, "url": url, "pin": gate.pin,
                  "qr": qr_data_url(url), "ready": True, "error": ""})


async def _body(request: web.Request) -> dict:
    """Тело POST. Кривой JSON — не повод падать: это всего лишь пустое тело."""
    try:
        data = await request.json()
    except Exception:       # noqa: BLE001 — тут важен любой провал разбора
        return {}
    return data if isinstance(data, dict) else {}


# --- запуск ------------------------------------------------------------------
def bind_free_port(preferred: int = 0) -> tuple[socket.socket, int]:
    """Сокет на свободном порту 127.0.0.1. Порт нужен ДО старта сервера: его
    просит cloudflared, и на него же смотрит проверка «свой ли это адрес»."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", preferred))
    except OSError:
        sock.bind(("127.0.0.1", 0))
    return sock, sock.getsockname()[1]


async def serve(*, port: int = 0, open_browser: bool = True,
                web_dir: Path = WEB_DIR, settings: Settings | None = None,
                say=log.info) -> None:
    """Поднять пульт и держать, пока не попросят выйти."""
    sock, port = bind_free_port(port)
    app = create_app(settings=settings, port=port, web_dir=web_dir)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.SockSite(runner, sock)
    await site.start()
    url = f"http://127.0.0.1:{port}/"
    say(f"Пульт руководителя открыт: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        # Пробуждение раз в полсекунды: на Windows цикл событий замечает
        # ctrl+C только между пробуждениями, а без этого пульт нечем закрыть.
        while True:
            await asyncio.sleep(0.5)
    finally:
        say("Пульт закрывается, тоннель гаснет.")
        await runner.cleanup()
