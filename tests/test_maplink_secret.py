"""Ключ подписи «Карты дня» — собственный секрет сервера (ревью 25.09, #1).

Раньше ключ выводился из токена бота: sha256("mapday:" + BOT_TOKEN). На контуре
без Телеграма токен пуст, и ключ становился константой из открытого кода —
кто угодно подписывал ссылку на любое ФИО и дату и без входа читал адреса
должников с GPS-точками выездника. Здесь проверяется, что старая подпись не
проходит ни при пустом токене, ни при заданном (запасной схемы нет), что свой
секрет устойчив и заводится один на всех, и что окружение главнее базы.
"""
import asyncio
import hashlib
import hmac
import importlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import quote

import pytest
from aiohttp.test_utils import TestClient, TestServer

import db
import maplink
import web

EMP = "Картограф Секретов"
TODAY = date.today().isoformat()


@pytest.fixture(autouse=True)
def _clean_keys(monkeypatch):
    """Ни токена бота, ни своего секрета: состояние контура компании до правки."""
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.delenv("MAPLINK_SECRET", raising=False)
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('bot_token', ?)",
                     (maplink.SECRET_SETTING,))
        conn.commit()
    yield


def _old_scheme_sig(token: str, employee: str, on_date: str, exp: int) -> str:
    """Подпись так, как её считал бы атакующий по старому открытому коду."""
    key = hashlib.sha256(("mapday:" + token).encode()).digest()
    payload = f"{employee}|{on_date}|{exp}".encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()[:32]


def _stored_secret() -> str | None:
    return db.get_setting(maplink.SECRET_SETTING)


def _get(path: str):
    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.get(path)
            return resp.status, await resp.text()
        finally:
            await client.close()

    return asyncio.run(scenario())


def test_old_constant_key_no_longer_opens_the_map():
    """Главная дыра: без Телеграма ключ был sha256(b"mapday:")."""
    exp = int(time.time()) + 3600
    forged = _old_scheme_sig("", EMP, TODAY, exp)
    assert maplink.verify(EMP, TODAY, str(exp), forged) is False

    query = f"e={quote(EMP)}&d={TODAY}&x={exp}&s={forged}"
    for path in ("/manager/map_data", "/manager/map"):
        status, _ = _get(f"{path}?{query}")
        assert status == 403, f"поддельная подпись открыла {path}"


@pytest.mark.parametrize("where", ["env", "settings"])
def test_bot_token_is_not_kept_as_a_fallback_key(monkeypatch, where):
    """Старую схему не держим и запасной: токен бота знают все, кто когда-либо
    настраивал бота, а ссылка из дайджеста им карту открывать не должна."""
    token = "123456789:" + "A" * 35
    if where == "env":
        monkeypatch.setenv("BOT_TOKEN", token)
    else:
        db.set_setting("bot_token", token)
    exp = int(time.time()) + 3600
    forged = _old_scheme_sig(token, EMP, TODAY, exp)
    assert maplink.verify(EMP, TODAY, str(exp), forged) is False


def test_signed_link_still_opens_the_map():
    qs = maplink.sign(EMP, TODAY)
    status, body = _get(f"/manager/map_data?{qs}")
    assert status == 200 and json.loads(body)["employee"] == EMP
    status, _ = _get(f"/manager/map?{qs}")
    assert status == 200


def test_secret_is_created_once_and_survives_a_restart():
    assert _stored_secret() is None
    first = maplink._key()
    stored = _stored_secret()
    assert stored and len(stored) >= 32, "секрет не заведён в settings"
    assert maplink._key() == first, "ключ меняется между вызовами"

    qs = dict(p.split("=", 1) for p in maplink.sign(EMP, TODAY).split("&"))
    importlib.reload(maplink)            # как перезапуск процесса
    assert _stored_secret() == stored
    assert maplink.verify(EMP, qs["d"], qs["x"], qs["s"]), "перезапуск сломал ссылку"


def test_parallel_first_calls_agree_on_one_secret():
    """Два первых запроса одновременно не должны завести два разных ключа —
    иначе ссылку, подписанную одним, отверг бы другой."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _i: maplink._key(), range(16)))
    assert len(set(keys)) == 1
    with db.connect() as conn:
        rows = conn.execute("SELECT value FROM settings WHERE key = ?",
                            (maplink.SECRET_SETTING,)).fetchall()
    assert len(rows) == 1


def test_blank_stored_secret_is_replaced_not_used():
    """Пустая строка вместо секрета — не ключ: иначе подпись снова свелась бы
    к константе."""
    db.set_setting(maplink.SECRET_SETTING, "  ")
    assert maplink._key(), "пустой секрет принят как ключ"
    assert (_stored_secret() or "").strip(), "пустой секрет не заменён"


def test_environment_secret_wins_over_the_database(monkeypatch):
    db_qs = dict(p.split("=", 1) for p in maplink.sign(EMP, TODAY).split("&"))

    monkeypatch.setenv("MAPLINK_SECRET", "секрет-из-окружения-" + "x" * 24)
    assert not maplink.verify(EMP, db_qs["d"], db_qs["x"], db_qs["s"]), \
        "при заданном окружении принят ключ из базы"
    env_qs = dict(p.split("=", 1) for p in maplink.sign(EMP, TODAY).split("&"))
    assert maplink.verify(EMP, env_qs["d"], env_qs["x"], env_qs["s"])

    monkeypatch.delenv("MAPLINK_SECRET")
    assert maplink.verify(EMP, db_qs["d"], db_qs["x"], db_qs["s"])


def test_empty_fields_are_refused_even_with_a_matching_signature():
    """Подпись «ни на кого» или «ни на когда» не выдаётся — и не принимается,
    даже если посчитана верным ключом."""
    exp = int(time.time()) + 3600
    assert maplink.verify("", TODAY, str(exp), maplink._sig("", TODAY, exp)) is False
    assert maplink.verify(EMP, "", str(exp), maplink._sig(EMP, "", exp)) is False
    assert maplink.verify(EMP, TODAY, str(exp), "") is False
    assert maplink.verify(EMP, TODAY, str(exp), None) is False
