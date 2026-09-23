"""Smoke-тест собранного Rust-бинарника пульта руководителя (hard-pult.exe).

Бинарник обязан вести себя как питон-версия: та же страница, тот же локальный
API, тот же замок. Тест поднимает заглушку «Харда» (как в тестах pult_ruk,
в отдельном потоке), кладёт exe во временную папку с чистыми настройками и
прогоняет ключевые случаи живыми HTTP-запросами. Нет exe на машине (не
собирали) — тест честно пропускается.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "hard-pult" / "hard-pult.exe"

TOKEN = "ТОКЕН-ХАРДА-RUST"
SUMMARY = {
    "ok": True, "screen": "summary",
    "date": "2026-09-22", "today": "2026-09-22",
    "prev_date": "2026-09-21", "next_date": None,
    "manager": {"name": "Шемякин А.", "scope": "СНГ"},
    "employees": ["Апиев А."],
    "totals": {"employees": 1, "persons": 3},
    "scope": "СНГ", "manager_name": "Шемякин А.",
}

pytestmark = pytest.mark.skipif(
    not EXE.is_file(), reason="hard-pult.exe не собран (cargo build --release)")


def _stub_hard(state: dict):
    from aiohttp import web

    async def redeem(request):
        body = await request.json()
        state["hits"].append(("redeem", dict(body)))
        return web.json_response({"ok": True, "token": TOKEN, "days": 30})

    def guard(request):
        if request.headers.get("X-App-Token") != TOKEN:
            return web.Response(status=401, text="auth")
        return None

    async def summary(request):
        state["hits"].append(("summary", dict(request.query)))
        bad = guard(request)
        return bad if bad is not None else web.json_response(SUMMARY)

    pushes_state = {"push_visits": True, "push_digest": True, "push_promises": False}

    async def pushes(request):
        if request.headers.get("X-App-Token") != TOKEN:
            return web.Response(status=401, text="auth")
        if request.method == "POST":
            body = await request.json()
            for key, value in body.items():
                if key in pushes_state:
                    pushes_state[key] = bool(value)
        return web.json_response({"pushes": dict(pushes_state)})

    async def app_links(request):
        if request.headers.get("X-App-Token") != TOKEN:
            return web.Response(status=401, text="auth")
        return web.json_response({
            "ok": True, "screen": "team_links",
            "manager": {"name": "Шемякин А.", "scope": "СНГ"},
            "links": [
                {"employee": "Апиев А.", "telegram_id": 573001,
                 "code": None, "url": "", "entered": False, "devices": 0,
                 "alive": False, "uses": 0, "max_uses": 2},
            ]})

    async def app_link(request):
        if request.headers.get("X-App-Token") != TOKEN:
            return web.Response(status=401, text="auth")
        body = await request.json()
        if body.get("employee") != "Апиев А.":
            return web.json_response(
                {"error": "Это не ваш сотрудник: ссылку можно выдать только своей команде."},
                status=403)
        return web.json_response({
            "ok": True, "employee": body.get("employee"), "telegram_id": 573003,
            "code": "RUSTCODE7", "url": "http://127.0.0.1:1/i/RUSTCODE7",
            "expires_at": "2026-09-30", "uses": 2})

    app = web.Application()
    app.router.add_post("/api/redeem", redeem)
    app.router.add_get("/api/manager/summary", summary)
    app.router.add_get("/api/manager/promises", summary)
    app.router.add_route("*", "/api/manager/pushes", pushes)
    app.router.add_get("/api/manager/app_links", app_links)
    app.router.add_post("/api/manager/app_link", app_link)
    return app


class _StubThread:
    """Заглушка «Харда» в своём потоке: свой цикл событий, свой порт."""

    def __init__(self, state: dict):
        self.state = state
        self.port = _free_port()
        self._started = threading.Event()
        self._loop = None
        self._runner = None
        self._task = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        import asyncio
        from aiohttp import web

        async def serve():
            self._runner = web.AppRunner(_stub_hard(self.state))
            await self._runner.setup()
            site = web.TCPSite(self._runner, "127.0.0.1", self.port)
            await site.start()
            self._started.set()
            await asyncio.Event().wait()

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(serve())
        self._loop.run_forever()

    def start(self):
        self._thread.start()
        assert self._started.wait(20), "заглушка «Харда» не поднялась"

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        if self._loop is not None:
            def _finish():
                if self._task is not None:
                    self._task.cancel()
                self._loop.stop()
            self._loop.call_soon_threadsafe(_finish)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _http(method: str, url: str, body: dict | None = None,
          headers: dict | None = None, timeout: float = 10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return e.code, {}


@pytest.fixture()
def world(tmp_path):
    stub = _StubThread({"hits": []})
    stub.start()
    exe_dir = tmp_path / "пульт"
    exe_dir.mkdir()
    shutil.copy(EXE, exe_dir / "hard-pult.exe")
    port = _free_port()
    proc = subprocess.Popen(
        [str(exe_dir / "hard-pult.exe"), "--port", str(port), "--no-browser"],
        cwd=exe_dir,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            _http("GET", base + "/api/state")
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    else:
        proc.kill()
        stub.stop()
        pytest.fail("hard-pult.exe не поднялся за 30 секунд")
    try:
        yield {"base": base, "hard": stub.base, "stub": stub,
               "exe_dir": exe_dir}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        stub.stop()


def test_page_and_state_before_login(world):
    with urllib.request.urlopen(world["base"] + "/", timeout=10) as resp:
        html = resp.read().decode("utf-8")
    assert "Пульт руководителя" in html or "пульт руководителя" in html.lower()

    status, state = _http("GET", world["base"] + "/api/state")
    assert status == 200
    assert state["ready"] is False
    assert state["manager"] == {"name": "", "scope": ""}


def test_link_redeem_and_today(world):
    status, out = _http("POST", world["base"] + "/api/link",
                        {"link": f"{world['hard']}/i/AbCdEf123456"})
    assert status == 200 and out.get("ok") is True

    # токен лёг в файл настроек рядом с exe, а не остался в памяти
    raw = json.loads((world["exe_dir"] /
                      "настройки пульта руководителя.json").read_text("utf-8"))
    assert raw["servers"][world["hard"]]["token"] == TOKEN

    status, today = _http("GET", world["base"] + "/api/today")
    assert status == 200
    assert today["totals"]["persons"] == 3
    assert today["date"]["iso"] == "2026-09-22"

    # имя руководителя пульт узнаёт из первого ответа данных — как питон-версия
    status, state = _http("GET", world["base"] + "/api/state")
    assert state["ready"] is True
    assert state["hard_url"] == world["hard"]
    assert state["manager"]["name"] == "Шемякин А."
    assert state["servers"][0]["host"].startswith("127.0.0.1")


def test_unknown_server_switch_is_refused(world):
    _http("POST", world["base"] + "/api/link",
          {"link": f"{world['hard']}/i/AbCdEf123456"})
    status, out = _http("POST", world["base"] + "/api/server",
                        {"url": "https://чужой-сервер"})
    assert status == 200 and "не знаком" in out.get("error", "")


def test_foreign_host_is_refused(world):
    # Чужое имя, ведущее на 127.0.0.1, — не наш адрес: отказ до всего остального.
    req = urllib.request.Request(world["base"] + "/api/state",
                                 headers={"Host": "evil.example"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, body = resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        status, body = e.code, json.loads(e.read().decode())
    assert status == 403
    assert "не по своему адресу" in body["error"]


def test_date_is_validated_in_russian(world):
    _http("POST", world["base"] + "/api/link",
          {"link": f"{world['hard']}/i/AbCdEf123456"})
    bad = urllib.parse.quote("вчера")
    status, out = _http("GET", world["base"] + f"/api/today?date={bad}")
    assert status == 400
    assert "2026-09-21" in out["error"]


def test_out_forgets_and_page_still_serves(world):
    _http("POST", world["base"] + "/api/link",
          {"link": f"{world['hard']}/i/AbCdEf123456"})
    status, out = _http("POST", world["base"] + "/api/out", {})
    assert status == 200 and out.get("ok") is True
    raw = json.loads((world["exe_dir"] /
                      "настройки пульта руководителя.json").read_text("utf-8"))
    assert not raw["servers"][world["hard"]].get("token")
    status, state = _http("GET", world["base"] + "/api/state")
    assert state["ready"] is False


def test_pushes_and_team_links_through_exe(world):
    _http("POST", world["base"] + "/api/link",
          {"link": f"{world['hard']}/i/AbCdEf123456"})

    status, out = _http("GET", world["base"] + "/api/pushes")
    assert status == 200 and out["pushes"]["push_promises"] is False

    status, out = _http("POST", world["base"] + "/api/pushes",
                        {"key": "push_promises", "value": True})
    assert status == 200 and out["pushes"]["push_promises"] is True

    status, out = _http("POST", world["base"] + "/api/pushes",
                        {"key": "nope", "value": True})
    assert status == 400

    status, out = _http("GET", world["base"] + "/api/team_links")
    assert status == 200 and out["links"][0]["employee"] == "Апиев А."

    status, out = _http("POST", world["base"] + "/api/team_link",
                        {"employee": "Апиев А.", "action": "issue"})
    assert status == 200 and out["url"].endswith("/i/RUSTCODE7")
    assert out["qr"], "QR не построен"

    status, out = _http("POST", world["base"] + "/api/team_link",
                        {"employee": "Чужой В.", "action": "issue"})
    assert status == 200 and "не ваш сотрудник" in out.get("error", "")
