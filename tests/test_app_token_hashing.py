"""Девайс-токены приложения лежат в базе только хэшем (ревью 25.09.2026, #12).

Раньше app_tokens.token хранил сырой токен, и любая копия базы — а их на диске
десятки: выкладки, переезды, приёмки — была живым входом под любым выездником
и руководителем на 30 дней. Теперь в базе sha256 (db.app_token_key), а сырой
токен есть только на телефоне, в ответе на выдачу и в куке повторного открытия.

Здесь проверяется то, от чего зависит, что выкладка никого не разлогинит:
* старые сырые строки переезжают в хэш одним проходом под маркером, и телефон
  входит тем же токеном — и в базе, и по HTTP;
* повторный старт ничего не меняет, а оборванный проход откатывается целиком;
* хэш из копии базы входом не служит;
* новая выдача кладёт в базу хэш, а клиенту отдаёт сырой;
* повторное открытие ссылки (кука и /api/redeem) узнаёт свой токен.
"""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer

import db
import web

MARKER = "app_tokens_hashed_v1"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# Три телефона, вошедших ДО выкладки. Третий токен по виду неотличим от хэша —
# 64 шестнадцатеричных символа: переводит маркер, а не длина или вид строки.
LEGACY = "legacy-dev-token"                   # ранняя дев-схема
PHONE = "Q2hlY2stMzItY2hhcnMtdG9rZW4tdjEx"   # как token_urlsafe(24): 32 символа
HEXLIKE = "0123456789abcdef" * 4             # 64 hex: по виду — готовый хэш
RAW = {LEGACY: 910001, PHONE: 910002, HEXLIKE: 910003}
EMPLOYEE = "Хэшев Хамид"


def _alive(days: int = 20) -> str:
    return (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")


@pytest.fixture()
def old_base(monkeypatch, tmp_path):
    """База, какой её застанет выкладка: схема текущая, прочие миграции
    пройдены, токены сырые, маркера нет."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (MARKER,))
        for tok, uid in RAW.items():
            conn.execute(
                "INSERT INTO app_tokens (token, telegram_id, created_at, expires_at, "
                "agent) VALUES (?,?,?,?,?)",
                (tok, uid, "2026-09-20T10:00:00", _alive(), "android"))
        conn.commit()
    db.set_bind(RAW[PHONE], EMPLOYEE, "agent")
    web._ADMIN_FAILS.clear()
    return tmp_path / "hardbot.db"


def _tokens() -> dict[str, int]:
    with db.connect() as conn:
        return {r["token"]: r["telegram_id"]
                for r in conn.execute("SELECT token, telegram_id FROM app_tokens")}


def _snapshot():
    with db.connect() as conn:
        rows = sorted(tuple(r) for r in conn.execute(
            "SELECT token, telegram_id, created_at, expires_at, agent FROM app_tokens"))
        marker = conn.execute("SELECT value FROM settings WHERE key = ?",
                              (MARKER,)).fetchone()
    return rows, (marker["value"] if marker else None)


def run(scenario):
    async def main():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            return await scenario(client)
        finally:
            await client.close()

    return asyncio.run(main())


# --- миграция ----------------------------------------------------------------
def test_old_raw_tokens_become_hashes_and_phones_stay_logged_in(old_base):
    db.init_db()                                   # ← ровно то, что делает выкладка

    stored = _tokens()
    assert stored == {db.app_token_key(t): uid for t, uid in RAW.items()}
    assert all(len(t) == 64 for t in stored)
    assert not set(RAW) & set(stored), "сырого токена в базе не осталось ни одного"
    _rows, marker = _snapshot()
    assert marker is not None and json.loads(marker)["tokens"] == len(RAW)
    for tok, uid in RAW.items():                  # телефоны шлют прежний сырой токен
        assert db.uid_for_app_token(tok) == uid


def test_second_start_changes_nothing(old_base):
    db.init_db()
    before = _snapshot()
    db.init_db()
    db.init_db()
    assert _snapshot() == before, "хэш от хэша разлогинил бы всех"
    for tok, uid in RAW.items():
        assert db.uid_for_app_token(tok) == uid


def test_interrupted_pass_rolls_back_as_a_whole(old_base, monkeypatch):
    """Старт оборвался посреди прохода — не остаётся ни половины хэшей, ни
    маркера: иначе следующий старт либо не довёл бы дело, либо захэшировал бы
    хэши. Следующий старт проходит заново и до конца."""
    real = db.app_token_key
    calls = []

    def flaky(token):
        calls.append(token)
        if len(calls) == 2:
            raise RuntimeError("свет погас посреди прохода")
        return real(token)

    monkeypatch.setattr(db, "app_token_key", flaky)
    with pytest.raises(RuntimeError):
        db.init_db()
    monkeypatch.setattr(db, "app_token_key", real)

    assert _tokens() == RAW, "полупрохода не бывает: всё осталось сырым"
    assert _snapshot()[1] is None, "и маркера нет"
    db.init_db()
    assert _tokens() == {real(t): uid for t, uid in RAW.items()}
    for tok, uid in RAW.items():
        assert db.uid_for_app_token(tok) == uid


def test_null_token_rows_do_not_break_the_start(old_base):
    """Строка с NULL вместо токена входом не была; два NULL дали бы один хэш и
    уронили бы старт на PRIMARY KEY — сервер не поднялся бы вовсе. Проверка
    после выкладки (length(token) != 64 → 0) при этом остаётся чистой."""
    with db.connect() as conn:
        conn.execute("INSERT INTO app_tokens (token, telegram_id) VALUES (NULL, 1)")
        conn.execute("INSERT INTO app_tokens (token, telegram_id) VALUES (NULL, 2)")
        conn.execute("INSERT INTO app_tokens (token, telegram_id) VALUES ('', 3)")
        conn.commit()
    db.init_db()
    with db.connect() as conn:
        left = conn.execute("SELECT COUNT(*) FROM app_tokens "
                            "WHERE length(token) != 64").fetchone()[0]
    assert left == 0
    for tok, uid in RAW.items():
        assert db.uid_for_app_token(tok) == uid


def test_hash_from_a_database_copy_is_not_a_login(old_base):
    db.init_db()
    for tok in RAW:
        assert db.uid_for_app_token(db.app_token_key(tok)) is None


# --- HTTP: вход, выдача, повторное открытие ------------------------------------
def test_bootstrap_by_raw_token_after_migration(old_base):
    """Телефон, вошедший до выкладки, открывает приложение тем же токеном; хэш
    из копии базы получает 401. Скользящее продление тоже идёт по хэшу."""
    db.init_db()
    bound = RAW[PHONE]

    async def scenario(c):
        seen = {}
        for tok, uid in RAW.items():
            r = await c.get("/api/bootstrap", headers={"X-App-Token": tok})
            seen[uid] = (r.status, await r.json())
        stolen = []
        for tok in RAW:
            r = await c.get("/api/bootstrap",
                            headers={"X-App-Token": db.app_token_key(tok)})
            stolen.append(r.status)
        return seen, stolen

    seen, stolen = run(scenario)
    for uid, (status, body) in seen.items():
        assert status == 200 and body["uid"] == uid
    assert seen[bound][1]["employee"] == EMPLOYEE, "вошёл в свой портфель"
    assert stolen == [401] * len(RAW), "хэш из копии базы — не вход"
    with db.connect() as conn:
        exp = conn.execute("SELECT expires_at FROM app_tokens WHERE telegram_id = ?",
                           (bound,)).fetchone()["expires_at"]
    assert exp > _alive(db.APP_TOKEN_DAYS - 1), "касание продлило срок по хэшу"


def test_new_link_stores_hash_and_gives_raw_and_reopen_cookie_works(old_base):
    """Выдача по ссылке: в базе хэш, в редиректе и в куке повторного открытия —
    сырой токен. Второй тап того же телефона получает тот же токен и обмен не
    тратит: кука сверяется по хэшу."""
    db.init_db()
    uid = RAW[PHONE]
    code = db.create_app_link(uid)

    async def scenario(c):
        c.session.cookie_jar.clear()
        first = await c.get(f"/i/{code}", headers={"User-Agent": IPHONE},
                            allow_redirects=False)
        cookie = first.cookies.get(web._REOPEN_COOKIE)
        second = await c.get(f"/i/{code}", headers={"User-Agent": IPHONE},
                             allow_redirects=False)
        return (first.status, first.headers["Location"],
                cookie.value if cookie else "", second.headers["Location"])

    st, loc, cookie, loc2 = run(scenario)
    assert st == 302 and loc.startswith("/#t=")
    raw = loc.split("#t=", 1)[1].split("&", 1)[0]
    assert cookie == raw, "в куке сырой токен — сервер сверит его по хэшу"
    assert loc2.split("#t=", 1)[1].split("&", 1)[0] == raw, "второй тап — тот же токен"
    stored = _tokens()
    assert raw not in stored and stored[db.app_token_key(raw)] == uid
    assert db.uid_for_app_token(raw) == uid
    with db.connect() as conn:
        uses = conn.execute("SELECT uses FROM app_links WHERE code = ?",
                            (code,)).fetchone()["uses"]
    assert uses == 1, "повторный тап обмен не потратил"


def test_redeem_keeps_the_phone_token_by_its_hash(old_base):
    """/api/redeem с токеном устройства: сервер узнаёт его по хэшу, возвращает
    тот же сырой, что прислал клиент, и обмен не тратит. Так входят и пульт
    руководителя, и ссылка, прилетевшая в уже открытую вкладку."""
    db.init_db()
    tok = PHONE
    code = db.create_app_link(RAW[tok])

    async def scenario(c):
        r = await c.post("/api/redeem", json={"code": code, "token": tok},
                         headers={"User-Agent": IPHONE})
        body = await r.json()
        m = await c.get("/manifest.webmanifest", params={"t": tok})
        mh = await c.get("/manifest.webmanifest", params={"t": db.app_token_key(tok)})
        return (r.status, body, (await m.json(content_type=None))["start_url"],
                (await mh.json(content_type=None))["start_url"])

    st, body, start_url, start_url_hash = run(scenario)
    assert st == 200 and body["token"] == tok
    with db.connect() as conn:
        uses = conn.execute("SELECT uses FROM app_links WHERE code = ?",
                            (code,)).fetchone()["uses"]
    assert uses == 0, "свой живой токен обмена не тратит"
    assert start_url == f"/#t={tok}", "персональный манифест — по сырому токену"
    assert "#t=" not in start_url_hash, "по хэшу из копии манифест не персонализируется"
