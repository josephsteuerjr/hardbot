"""Местный API пульта руководителя: контракт страницы, вход по ссылке и замок.

Сети здесь нет: вместо сервера «Хард» поднимается маленькая заглушка на том же
127.0.0.1, и пульт ходит в неё своим настоящим клиентом — со своими
заголовками, своим разбором ошибок и своим кэшем. Проверяется ровно то, на что
завтра смотрит руководитель:

* форма ответов ровно та, что обещана странице (`date` одним предметом);
* пропала связь — на экране остаётся вчерашняя правда и время, когда она
  потерялась, а не пустота;
* токен «Харда» не вытекает наружу ни в одном ответе;
* телефон из тоннеля без кода-пропуска не видит ничего, а пять промахов гасят
  тоннель;
* книга Excel доезжает до браузера файлом, а не строкой с ошибкой.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from pult_ruk.server import create_app                      # noqa: E402
from pult_ruk.settings import Settings, server_entry        # noqa: E402
from pult_ruk.tunnel import TunnelTrouble                   # noqa: E402

TOKEN = "ТОКЕН-ХАРДА-1"
TEAM_LINKS = {
    "ok": True, "screen": "team_links",
    "manager": {"name": "Шемякин А.", "scope": "Таджикистан"},
    "links": [
        {"employee": "Апиев А.", "telegram_id": 573001, "kind": "agent",
         "code": None, "url": "", "created_at": None, "expires_at": None,
         "uses": 0, "max_uses": 2, "bonus_ios": False, "last_use_at": None,
         "last_agent": None, "entered": False, "devices": 0, "alive": False},
        {"employee": "Бепиев Б.", "telegram_id": 573002, "kind": "agent",
         "code": "LINKCODE99", "url": "https://x/i/LINKCODE99",
         "created_at": "2026-09-23T08:00:00", "expires_at": "2026-09-30",
         "uses": 1, "max_uses": 2, "bonus_ios": False, "last_use_at": None,
         "last_agent": None, "entered": True, "devices": 1, "alive": True},
    ],
}
PUSHES = {"pushes": {"push_visits": True, "push_digest": True,
                     "push_promises": False}}
TUNNEL_URL = "https://алый-кот-77.trycloudflare.com"
TUNNEL_HOST = "алый-кот-77.trycloudflare.com"
PHONE = {"Host": TUNNEL_HOST, "X-Forwarded-For": "203.0.113.7",
         "Cf-Ray": "8b0000000000-DME"}
BOOK = "PK\x03\x04-это-книга-Excel".encode("utf-8")

SUMMARY = {
    "ok": True, "screen": "summary",
    "date": "2026-09-21", "today": "2026-09-21",
    "prev_date": "2026-09-20", "next_date": None,
    "team": ["Апиев А."],
    "manager": {"name": "Шемякин А.", "scope": "Таджикистан",
                "countries": [], "isOwner": False},
    "employees": ["Апиев А."],
    "display": [{"employee": "Апиев А.", "persons": 3, "contracts": 4,
                 "with_photo": 2}],
    "groups": [{"code": "tj", "title": "Таджикистан", "flag": "",
                "employees": [{"employee": "Апиев А.", "persons": 3,
                               "contracts": 4, "with_photo": 2}]}],
    "totals": {"employees": 1, "active": 1, "persons": 3, "contracts": 4,
               "with_photo": 2, "money": 150000, "money_text": "150 000",
               "overdue_promises": 2},
    "scope": "Таджикистан", "manager_name": "Шемякин А.",
}
PROMISES = {"ok": True, "screen": "promises", "date": "2026-09-21",
            "today": "2026-09-21", "prev_date": "2026-09-20", "next_date": None,
            "manager": {"name": "Шемякин А.", "scope": "Таджикистан"},
            "totals": {"persons": 2, "worked": 1, "untouched": 1, "deferred": 0},
            "employees": [{"employee": "Апиев А.", "persons": 2, "items": []}],
            "scope": "Таджикистан"}


# --- заглушка «Харда» --------------------------------------------------------
def hard_stub(state: dict) -> web.Application:
    """Тот кусок «Харда», который нужен пульту. Всё, что он получил, кладётся
    в `state['hits']` — по нему тесты проверяют заголовки и параметры."""

    async def redeem(request):
        body = await request.json()
        state["hits"].append(("redeem", body))
        code = state.get("redeem_code", 200)
        if code != 200:
            return web.Response(status=code, text="link")
        return web.json_response({"ok": True, "token": TOKEN, "days": 30})

    def guard(request):
        if request.headers.get("X-App-Token") != TOKEN:
            return web.Response(status=401, text="auth")
        return None

    async def summary(request):
        state["hits"].append(("summary", dict(request.query),
                              request.headers.get("X-App-Token")))
        bad = guard(request)
        if bad is not None:
            return bad
        if state.get("code"):
            return web.Response(status=state["code"], text="nope")
        return web.json_response(SUMMARY)

    async def employee(request):
        state["hits"].append(("employee", dict(request.query), None))
        bad = guard(request)
        if bad is not None:
            return bad
        if request.query.get("employee") != "Апиев А.":
            return web.Response(status=404, text="employee")
        return web.json_response({**SUMMARY, "screen": "employee"})

    async def promises(request):
        state["hits"].append(("promises", dict(request.query), None))
        bad = guard(request)
        if bad is not None:
            return bad
        return web.json_response(PROMISES)

    async def stats(request):
        state["hits"].append(("stats", dict(request.query), None))
        bad = guard(request)
        if bad is not None:
            return bad
        return web.json_response({**SUMMARY, "screen": "stats",
                                  "range": {"from": "2026-09-15",
                                            "to": "2026-09-21"}})

    async def book(request):
        state["hits"].append(("book", dict(request.query),
                              request.headers.get("X-App-Token")))
        bad = guard(request)
        if bad is not None:
            return bad
        if state.get("book_code"):
            return web.Response(status=state["book_code"], text="no")
        return web.Response(
            body=BOOK,
            content_type="application/vnd.openxmlformats-officedocument"
                         ".spreadsheetml.sheet",
            headers={"Content-Disposition":
                     'attachment; filename=stats_2026-09-15_2026-09-21.xlsx'})

    async def team_links(request):
        bad = guard(request)
        if bad is not None:
            return bad
        state["hits"].append(("team_links", None))
        return web.json_response(TEAM_LINKS)

    async def app_link(request):
        bad = guard(request)
        if bad is not None:
            return bad
        body = await request.json()
        state["hits"].append(("app_link", dict(body)))
        if body.get("employee") not in {"Апиев А.", "Бепиев Б."}:
            return web.json_response(
                {"error": "Это не ваш сотрудник."}, status=403)
        return web.json_response({
            "ok": True, "employee": body.get("employee"),
            "telegram_id": 573003, "code": "NEWCODE42",
            "url": "https://x/i/NEWCODE42", "expires_at": "2026-09-30",
            "uses": 2})

    async def app_revoke(request):
        bad = guard(request)
        if bad is not None:
            return bad
        body = await request.json()
        state["hits"].append(("app_revoke", dict(body)))
        return web.json_response({"ok": True, "employee": body.get("employee"),
                                  "revoked": 1, "scope": "all"})

    pushes_current = dict(PUSHES["pushes"])

    async def pushes(request):
        bad = guard(request)
        if bad is not None:
            return bad
        if request.method == "POST":
            body = await request.json()
            state["hits"].append(("pushes_post", dict(body)))
            for key, value in body.items():
                if key in pushes_current:
                    pushes_current[key] = bool(value)
        else:
            state["hits"].append(("pushes_get", None))
        return web.json_response({"pushes": dict(pushes_current)})

    app = web.Application()
    app.router.add_post("/api/redeem", redeem)
    app.router.add_get("/api/manager/summary", summary)
    app.router.add_get("/api/manager/employee", employee)
    app.router.add_get("/api/manager/promises", promises)
    app.router.add_get("/api/manager/stats", stats)
    app.router.add_get("/api/manager/stats.xlsx", book)
    app.router.add_get("/api/manager/app_links", team_links)
    app.router.add_post("/api/manager/app_link", app_link)
    app.router.add_post("/api/manager/app_revoke", app_revoke)
    app.router.add_route("*", "/api/manager/pushes", pushes)
    return app


class FakeTunnel:
    """cloudflared без cloudflared: тот же набор свойств, никакого процесса."""

    def __init__(self, url: str = TUNNEL_URL, available: bool = True,
                 fail: str = "") -> None:
        self.url_ = url
        self.available = available
        self.fail = fail
        self.on_ = False
        self.stops = 0
        self.error = ""

    @property
    def on(self) -> bool:
        return self.on_

    @property
    def url(self) -> str:
        return self.url_ if self.on_ else ""

    def state(self) -> dict:
        return {"on": self.on_, "url": self.url, "ready": self.available,
                "error": self.error}

    async def start(self) -> str:
        if self.fail:
            self.error = self.fail
            raise TunnelTrouble(self.fail)
        self.on_ = True
        return self.url_

    async def stop(self) -> None:
        self.on_ = False
        self.stops += 1

    def stop_now(self) -> None:
        self.on_ = False
        self.stops += 1


@asynccontextmanager
async def world(tmp_path, *, token: str = TOKEN, tunnel=None, state=None):
    """Пульт, заглушка «Харда» и клиент к пульту — всё на 127.0.0.1."""
    state = state if state is not None else {}
    state.setdefault("hits", [])
    hard_srv = TestServer(hard_stub(state))
    await hard_srv.start_server()
    settings = Settings(
        {"url": str(hard_srv.make_url("")).rstrip("/"), "token": token},
        path=tmp_path / "настройки пульта руководителя.json")
    tun = tunnel or FakeTunnel()
    app = create_app(settings=settings, tunnel=tun, port=0)
    # Клиент ведёт себя как браузер на странице самого пульта: такой браузер
    # ставит Sec-Fetch-Site: same-origin на каждый свой запрос. Запросы без
    # него (как у атакующей страницы) — `bare_post` ниже.
    client = TestClient(TestServer(app), headers={"Sec-Fetch-Site": "same-origin"})
    await client.start_server()
    try:
        yield SimpleNamespace(client=client, state=state, tunnel=tun,
                              settings=settings, app=app, hard=hard_srv,
                              gate=app["gate"])
    finally:
        await client.close()
        await hard_srv.close()


def run(coro) -> None:
    asyncio.run(coro)


async def let_phone_in(w) -> dict:
    """Поднять тоннель с пульта и провести телефон через код-пропуск.
    Возвращает заголовки телефона, которому уже можно."""
    await w.client.post("/api/phone", json={"on": True})
    resp = await w.client.post("/api/pin", json={"pin": w.gate.pin},
                               headers=PHONE)
    raw = resp.headers.get("Set-Cookie") or ""
    key = raw.split(";")[0].split("=", 1)[1]
    return {**PHONE, "Cookie": f"pult_ruk={key}"}


# --- вход --------------------------------------------------------------------
def test_state_without_link_asks_for_link(tmp_path):
    async def go():
        async with world(tmp_path, token="") as w:
            data = await (await w.client.get("/api/state")).json()
            assert data["ready"] is False
            assert data["need_pin"] is False
            assert data["manager"] == {"name": "", "scope": ""}
    run(go())


def test_link_is_exchanged_and_remembered(tmp_path):
    async def go():
        async with world(tmp_path, token="") as w:
            base = w.settings["url"]
            resp = await w.client.post("/api/link",
                                       json={"link": f"{base}/i/AbCdEf123456"})
            assert (await resp.json())["ok"] is True
            assert server_entry(w.settings)["token"] == TOKEN
            # токен лёг в файл настроек, а не остался в памяти
            again = Settings.load(w.settings.path)
            assert server_entry(again)["token"] == TOKEN
            kind, body = w.state["hits"][0]
            assert kind == "redeem" and body["code"] == "AbCdEf123456"
            assert (await (await w.client.get("/api/state")).json())["ready"]
    run(go())


def test_dead_link_speaks_russian(tmp_path):
    async def go():
        async with world(tmp_path, token="", state={"redeem_code": 410}) as w:
            base = w.settings["url"]
            data = await (await w.client.post(
                "/api/link", json={"link": f"{base}/i/AbCdEf123456"})).json()
            assert "ссылк" in data["error"].lower()
            assert "410" not in data["error"]
            assert not server_entry(w.settings).get("token")
    run(go())


def test_junk_instead_of_link_is_explained(tmp_path):
    async def go():
        async with world(tmp_path, token="") as w:
            data = await (await w.client.post(
                "/api/link", json={"link": "здравствуйте, вот ссылка"})).json()
            assert "ссылк" in data["error"].lower()
    run(go())


# --- экраны ------------------------------------------------------------------
def test_today_keeps_contract_shape(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            data = await (await w.client.get("/api/today?date=2026-09-21")).json()
            assert data["date"] == {"iso": "2026-09-21", "today": "2026-09-21",
                                    "prev": "2026-09-20", "next": ""}
            assert data["totals"]["persons"] == 3
            assert data["manager_name"] == "Шемякин А."
            assert data["display"][0]["employee"] == "Апиев А."
            assert data["stale"] is False
            _, query, token = w.state["hits"][0]
            assert query == {"date": "2026-09-21"}
            assert token == TOKEN          # токен ушёл заголовком, а не в адресе
    run(go())


def test_token_never_reaches_the_page(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            for path in ("/api/state", "/api/today", "/api/promises",
                         "/api/employee?emp=Апиев А.", "/api/stats"):
                text = await (await w.client.get(path)).text()
                assert TOKEN not in text
    run(go())


def test_employee_is_asked_by_name(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            data = await (await w.client.get(
                "/api/employee?emp=Апиев А.&date=2026-09-21")).json()
            assert data["screen"] == "employee"
            kind, query, _ = w.state["hits"][0]
            assert kind == "employee"
            assert query["employee"] == "Апиев А."
            # чужой сотрудник — не трассировка, а объяснение
            miss = await (await w.client.get("/api/employee?emp=Чужой Ч.")).json()
            assert "команд" in miss["error"]
    run(go())


def test_employee_without_name_is_refused(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            resp = await w.client.get("/api/employee")
            assert resp.status == 400
            assert "карточк" in (await resp.json())["error"]
    run(go())


def test_bad_date_is_explained_before_the_server(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            resp = await w.client.get("/api/today?date=вчера")
            assert resp.status == 400
            assert "2026-09-21" in (await resp.json())["error"]
            assert not w.state["hits"]      # «Харда» этим не беспокоили
    run(go())


def test_promises_and_stats_are_proxied(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            pro = await (await w.client.get("/api/promises")).json()
            assert pro["totals"]["persons"] == 2
            sts = await (await w.client.get(
                "/api/stats?from=2026-09-15&to=2026-09-21")).json()
            assert sts["range"]["to"] == "2026-09-21"
            kinds = [h[0] for h in w.state["hits"]]
            assert kinds == ["promises", "stats"]
            assert w.state["hits"][1][1] == {"from": "2026-09-15",
                                             "to": "2026-09-21"}
    run(go())


# --- связь пропала -----------------------------------------------------------
def test_lost_connection_keeps_yesterday_truth(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            first = await (await w.client.get("/api/today")).json()
            assert first["totals"]["persons"] == 3
            await w.hard.close()           # «Хард» пропал из сети
            again = await (await w.client.get("/api/today")).json()
            assert again["totals"]["persons"] == 3      # данные на месте
            assert again["stale"] is True
            assert len(again["lost_at"]) == 5 and ":" in again["lost_at"]
            assert "связ" in again["error"].lower()
    run(go())


def test_no_connection_and_no_memory_says_so(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            await w.hard.close()
            data = await (await w.client.get("/api/today")).json()
            assert data["stale"] is False
            assert "связ" in data["error"].lower()
            assert "totals" not in data
    run(go())


def test_expired_token_sends_back_to_the_gate(tmp_path):
    async def go():
        async with world(tmp_path, token="чужой") as w:
            data = await (await w.client.get("/api/today")).json()
            assert "ссылк" in data["error"].lower()
            state = await (await w.client.get("/api/state")).json()
            assert state["ready"] is False      # страница покажет экран входа
    run(go())


def test_broken_server_is_not_a_code(tmp_path):
    async def go():
        async with world(tmp_path, state={"code": 500}) as w:
            data = await (await w.client.get("/api/today")).json()
            assert "Сервер «Хард» сломался" in data["error"]
            assert "аналитику" in data["error"]
    run(go())


# --- книга -------------------------------------------------------------------
def test_book_reaches_the_browser_as_a_file(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            resp = await w.client.get(
                "/api/report.xlsx?from=2026-09-15&to=2026-09-21")
            assert resp.status == 200
            assert await resp.read() == BOOK
            assert "attachment" in resp.headers["Content-Disposition"]
            assert "stats_2026-09-15" in resp.headers["Content-Disposition"]
            assert resp.headers["Content-Type"].endswith("spreadsheetml.sheet")
    run(go())


def test_book_failure_is_a_russian_line(tmp_path):
    async def go():
        async with world(tmp_path, state={"book_code": 500}) as w:
            resp = await w.client.get("/api/report.xlsx")
            assert "Content-Disposition" not in resp.headers
            assert "сломался" in (await resp.json())["error"]
    run(go())


# --- телефон, код-пропуск и замок -------------------------------------------
def test_phone_is_turned_on_from_the_pc(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            data = await (await w.client.post("/api/phone",
                                              json={"on": True})).json()
            assert data["on"] is True
            assert data["url"] == TUNNEL_URL
            assert len(data["pin"]) == 4 and data["pin"].isdigit()
            assert w.gate.tunnel_host == TUNNEL_HOST
            off = await (await w.client.post("/api/phone",
                                             json={"on": False})).json()
            assert off["on"] is False and w.tunnel.stops >= 1
    run(go())


def test_missing_cloudflared_is_a_line_not_a_crash(tmp_path):
    async def go():
        tun = FakeTunnel(available=False,
                         fail="Программы cloudflared на компьютере нет.")
        async with world(tmp_path, tunnel=tun) as w:
            data = await (await w.client.post("/api/phone",
                                              json={"on": True})).json()
            assert data["on"] is False
            assert "cloudflared" in data["error"]
            assert data["ready"] is False      # кнопка остаётся, но подписана
    run(go())


def test_stranger_sees_nothing_until_the_pin(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            await w.client.post("/api/phone", json={"on": True})
            state = await (await w.client.get("/api/state",
                                              headers=PHONE)).json()
            assert state["need_pin"] is True
            assert state["manager"]["name"] == ""
            assert state["hard_url"] == ""
            assert state["phone"]["pin"] == ""
            resp = await w.client.get("/api/today", headers=PHONE)
            assert resp.status == 401
            assert (await resp.json())["need_pin"] is True
    run(go())


def test_right_pin_lets_the_phone_work(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            await w.client.post("/api/phone", json={"on": True})
            resp = await w.client.post("/api/pin", json={"pin": w.gate.pin},
                                       headers=PHONE)
            raw = resp.headers.get("Set-Cookie") or ""
            assert "HttpOnly" in raw and "Secure" in raw and "Lax" in raw
            key = raw.split(";")[0].split("=", 1)[1]
            phone = {**PHONE, "Cookie": f"pult_ruk={key}"}
            data = await (await w.client.get("/api/today", headers=phone)).json()
            assert data["totals"]["persons"] == 3
            # но код на самом телефоне уже не показываем
            state = await (await w.client.get("/api/state", headers=phone)).json()
            assert state["need_pin"] is False
            assert state["phone"]["pin"] == ""
            assert state["manager"]["name"] == "Шемякин А."
    run(go())


def test_wrong_pin_counts_down_then_kills_the_tunnel(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            await w.client.post("/api/phone", json={"on": True})
            wrong = "0000" if w.gate.pin != "0000" else "1111"
            texts = []
            for _ in range(4):
                data = await (await w.client.post(
                    "/api/pin", json={"pin": wrong}, headers=PHONE)).json()
                texts.append(data["error"])
            assert "Осталось 4" in texts[0] and "Осталось 1" in texts[3]
            last = await (await w.client.post(
                "/api/pin", json={"pin": wrong}, headers=PHONE)).json()
            assert "пять раз" in last["error"]
            await asyncio.sleep(0)          # даём погасить тоннель
            assert w.tunnel.on is False
            assert w.gate.locked is True
            # владельцу на ПК видно, что ломились
            state = await (await w.client.get("/api/state")).json()
            assert "ломились" in state["phone"]["alarm"]
    run(go())


def test_right_pin_after_lock_is_useless(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            await w.client.post("/api/phone", json={"on": True})
            w.gate.lock("проверка")
            data = await (await w.client.post(
                "/api/pin", json={"pin": w.gate.pin}, headers=PHONE)).json()
            assert data["ok"] is False
    run(go())


def test_phone_cannot_change_access(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            phone = await let_phone_in(w)
            for path, body in (("/api/link", {"link": "https://x/i/abcdef12"}),
                               ("/api/out", {}),
                               ("/api/phone", {"on": False}),
                               ("/api/server", {"url": "https://чужой"})):
                resp = await w.client.post(path, json=body, headers=phone)
                assert resp.status == 403
                assert "компьютере" in (await resp.json())["error"]
            assert server_entry(w.settings)["token"] == TOKEN
    run(go())


def test_unknown_host_is_refused(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            resp = await w.client.get("/api/state",
                                      headers={"Host": "чужой-сайт.ru"})
            assert resp.status == 403
    run(go())


def test_foreign_page_cannot_post(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            resp = await w.client.post("/api/out", json={},
                                       headers={"Origin": "https://зло.example"})
            assert resp.status == 403
            assert server_entry(w.settings)["token"] == TOKEN
    run(go())


# --- чужая страница без Origin (ревью 25.09.2026) -----------------------------
async def bare_post(w, path: str, body: dict, headers: dict | None = None):
    """POST мимо браузера пульта: без Origin и Sec-Fetch-Site, если их не дали
    явно. text/plain — «простой» запрос, который чужая страница шлёт без
    префлайта; разбор тела пульт делает всё равно."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
                w.client.make_url(path), data=json.dumps(body),
                headers={"Content-Type": "text/plain", **(headers or {})}) as resp:
            return resp.status, await resp.json()


def _own_origin(w) -> str:
    return str(w.client.make_url("")).rstrip("/")


def test_post_without_origin_is_refused(tmp_path):
    """Так ломали в PoC: ни Origin, ни Sec-Fetch-Site — пульт перепривязывался
    к серверу атакующего и выходил из «Харда» по чужой команде."""
    async def go():
        async with world(tmp_path) as w:
            link = f"{w.settings['url']}/i/AbCdEf123456"
            for path, body in (("/api/link", {"link": link}),
                               ("/api/out", {}),
                               ("/api/phone", {"on": True}),
                               ("/api/server", {"url": w.settings["url"]}),
                               ("/api/pushes", {"key": "push_promises", "value": True}),
                               ("/api/team_link", {"employee": "Апиев А.",
                                                   "action": "revoke"}),
                               ("/api/pin", {"pin": w.gate.pin})):
                status, data = await bare_post(w, path, body)
                assert status == 403, path
                assert "чужой страницы" in data["error"]
            assert server_entry(w.settings)["token"] == TOKEN
            assert w.tunnel.on is False
            assert w.state["hits"] == [], "запрос дошёл до «Харда»"
    run(go())


def test_foreign_or_opaque_origin_is_refused(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            for origin in ("https://зло.example",
                           "http://127.0.0.1:1",             # чужой порт той же машины
                           "https://другой-кот-78.trycloudflare.com",
                           "null"):
                status, _ = await bare_post(w, "/api/out", {},
                                            headers={"Origin": origin})
                assert status == 403, origin
            # чужой Origin не спасает и «same-origin»: верим обоим, но не порознь
            status, _ = await bare_post(
                w, "/api/out", {}, headers={"Origin": "https://зло.example",
                                            "Sec-Fetch-Site": "same-origin"})
            assert status == 403
            for site in ("cross-site", "same-site", "none"):
                status, _ = await bare_post(w, "/api/out", {},
                                            headers={"Sec-Fetch-Site": site})
                assert status == 403, site
            assert server_entry(w.settings)["token"] == TOKEN
    run(go())


def test_own_origin_alone_is_enough(tmp_path):
    """Браузер без Sec-Fetch-Site (старый Safari на iPhone) шлёт только Origin."""
    async def go():
        async with world(tmp_path, token="") as w:
            link = f"{w.settings['url']}/i/AbCdEf123456"
            status, data = await bare_post(w, "/api/link", {"link": link},
                                           headers={"Origin": _own_origin(w)})
            assert status == 200 and data["ok"] is True
            assert server_entry(w.settings)["token"] == TOKEN
    run(go())


def test_same_origin_fetch_alone_is_enough(tmp_path):
    """Origin нет или он «null» (так бывает при no-referrer) — решает слово
    браузера «same-origin», которое страница подделать не может."""
    async def go():
        async with world(tmp_path, token="") as w:
            link = f"{w.settings['url']}/i/AbCdEf123456"
            for headers in ({"Sec-Fetch-Site": "same-origin"},
                            {"Origin": "null", "Sec-Fetch-Site": "same-origin"}):
                server_entry(w.settings)["token"] = ""
                status, data = await bare_post(w, "/api/link", {"link": link},
                                               headers=headers)
                assert status == 200 and data["ok"] is True, headers
                assert server_entry(w.settings)["token"] == TOKEN
    run(go())


def test_phone_origin_is_its_tunnel(tmp_path):
    """Телефон приходит через cloudflared: Host и Origin — имя тоннеля. Чужой
    тоннель или запрос без Origin до кода-пропуска не доходят и промахом не
    считаются."""
    async def go():
        async with world(tmp_path) as w:
            await w.client.post("/api/phone", json={"on": True})
            pin = {"pin": w.gate.pin}
            for headers in ({**PHONE, "Origin": "https://другой-кот-78.trycloudflare.com"},
                            PHONE):
                status, _ = await bare_post(w, "/api/pin", pin, headers=headers)
                assert status == 403
            assert w.gate.fails == 0 and not w.gate.sessions
            status, data = await bare_post(w, "/api/pin", pin,
                                           headers={**PHONE, "Origin": TUNNEL_URL})
            assert status == 200 and data["ok"] is True
            assert len(w.gate.sessions) == 1
    run(go())


def test_out_forgets_token_and_kills_tunnel(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            await w.client.post("/api/phone", json={"on": True})
            assert (await (await w.client.post("/api/out", json={})).json())["ok"]
            assert not server_entry(w.settings).get("token")
            assert not server_entry(Settings.load(w.settings.path)).get("token")
            assert w.tunnel.on is False
            state = await (await w.client.get("/api/state")).json()
            assert state["ready"] is False
    run(go())


# --- страница ----------------------------------------------------------------
def test_page_and_style_are_served(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            page = await w.client.get("/")
            assert page.status == 200
            assert "text/html" in page.headers["Content-Type"]
            assert "пульт руководителя" in (await page.text()).lower()
            css = await w.client.get("/ui.css")
            assert css.status == 200
            assert "text/css" in css.headers["Content-Type"]
    run(go())


def test_only_page_files_are_served(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            for name in ("/settings.py", "/hard.py", "/настройки.json"):
                resp = await w.client.get(name)
                assert resp.status == 404
                assert "error" in await resp.json()
    run(go())


# --- несколько серверов (прод и контур компании) ------------------------------
def test_second_link_switches_and_keeps_the_old_token(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            first = w.settings["url"]
            second_state = {"hits": []}
            second = TestServer(hard_stub(second_state))
            await second.start_server()
            try:
                base2 = str(second.make_url("")).rstrip("/")
                resp = await w.client.post(
                    "/api/link", json={"link": f"{base2}/i/SecondCode99"})
                assert (await resp.json())["ok"] is True
                assert w.settings["url"] == base2, "ссылка не переключила сервер"
                assert set(w.settings["servers"]) == {first, base2}
                assert w.settings["servers"][first].get("token") == TOKEN, (
                    "токен первого сервера затёрт входом во второй")
                # данные идут со второго сервера
                await (await w.client.get("/api/today")).json()
                assert any(h[0] == "summary" for h in second_state["hits"])
                # переключение обратно — без новой ссылки
                resp = await w.client.post("/api/server", json={"url": first})
                body = await resp.json()
                assert body["ok"] is True and body["hard_url"] == first
                state = await (await w.client.get("/api/state")).json()
                assert state["ready"] is True
                urls = {s["url"] for s in state["servers"]}
                assert urls == {first, base2}, "список серверов неполон"
            finally:
                await second.close()
    run(go())


def test_switch_to_unknown_server_is_refused(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            data = await (await w.client.post(
                "/api/server", json={"url": "https://чужой-сервер"})).json()
            assert "не знаком" in data["error"]
            assert w.settings["url"] != "https://чужой-сервер"
    run(go())


def test_state_hides_other_servers_from_the_phone(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            phone = await let_phone_in(w)
            state = await (await w.client.get("/api/state",
                                              headers=phone)).json()
            assert state["servers"] == [], "телефон узнал адреса серверов"
    run(go())


def test_flat_old_settings_migrate_into_servers(tmp_path):
    path = tmp_path / "настройки пульта руководителя.json"
    Settings({"url": "https://old.example", "token": "t1", "days": 30,
              "manager": {"name": "А.", "scope": "РТ"}}, path).save()
    again = Settings.load(path)
    assert set(again["servers"]) == {"https://old.example"}
    assert again["servers"]["https://old.example"]["token"] == "t1"
    assert again["url"] == "https://old.example"
    assert "token" not in again, "плоский токен остался вторым источником правды"


# --- входы команды и рубильники -----------------------------------------------
def test_team_links_come_from_the_hard(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            data = await (await w.client.get("/api/team_links")).json()
            assert data["screen"] == "team_links"
            names = [r["employee"] for r in data["links"]]
            assert names == ["Апиев А.", "Бепиев Б."]
    run(go())


def test_team_link_issue_returns_url_and_qr(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            data = await (await w.client.post(
                "/api/team_link",
                json={"employee": "Апиев А.", "action": "issue"})).json()
            assert data["url"] == "https://x/i/NEWCODE42"
            assert data["qr"], "QR не построен для выданной ссылки"
            kind, body = [h for h in w.state["hits"] if h[0] == "app_link"][0]
            assert body["employee"] == "Апиев А."
    run(go())


def test_team_link_issue_refuses_stranger_by_russian_text(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            data = await (await w.client.post(
                "/api/team_link",
                json={"employee": "Чужой В.", "action": "issue"})).json()
            assert "не ваш сотрудник" in data["error"]
    run(go())


def test_team_link_requires_employee(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            resp = await w.client.post("/api/team_link", json={})
            assert resp.status == 400
    run(go())


def test_team_link_revoke_reaches_the_hard(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            data = await (await w.client.post(
                "/api/team_link",
                json={"employee": "Бепиев Б.", "action": "revoke"})).json()
            assert data["ok"] is True
            kind, body = [h for h in w.state["hits"] if h[0] == "app_revoke"][0]
            assert body["employee"] == "Бепиев Б."
    run(go())


def test_pushes_read_and_flip(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            data = await (await w.client.get("/api/pushes")).json()
            assert data["pushes"]["push_promises"] is False
            data = await (await w.client.post(
                "/api/pushes",
                json={"key": "push_promises", "value": True})).json()
            assert data["pushes"]["push_promises"] is True
            again = await (await w.client.get("/api/pushes")).json()
            assert again["pushes"]["push_promises"] is True
    run(go())


def test_pushes_reject_unknown_key(tmp_path):
    async def go():
        async with world(tmp_path) as w:
            resp = await w.client.post("/api/pushes",
                                       json={"key": "nope", "value": True})
            assert resp.status == 400
    run(go())


def test_phone_can_issue_and_flip_its_own_team(tmp_path):
    """Телефон руководителя из тоннеля — тот же руководитель: входы команды
    и свои рубильники ему доступны, это не «управление пультом»."""
    async def go():
        async with world(tmp_path) as w:
            phone = await let_phone_in(w)
            resp = await w.client.get("/api/team_links", headers=phone)
            assert resp.status == 200
            resp = await w.client.post("/api/pushes", headers=phone,
                                       json={"key": "push_digest", "value": False})
            assert resp.status == 200
            # а вот ссылку входа САМОГО пульта телефон по-прежнему не меняет
            resp = await w.client.post("/api/out", json={}, headers=phone)
            assert resp.status == 403
    run(go())
