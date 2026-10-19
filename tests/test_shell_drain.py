"""Оболочка на старом адресе (v64): экран выгрузки вместо работы.

Сервер старого адреса отвечает на /api/bootstrap 410 drain_only. Оболочка
не показывает портфель, досылает очередь телефона штатным flushQueue и
пишет статус словами; без входа — просит ссылку и после неё досылает;
«Выйти» (стёр бы очередь) не показывает.
"""
from pathlib import Path
import re

from shell_node import run_shell

A = "TOK-A-aaaaaaaaaaaaaaaaaaaa"
B = "TOK-B-bbbbbbbbbbbbbbbbbbbb"
HOST = "https://old.test/"

PRELUDE = f'const A = "{A}", B = "{B}", HOST = "{HOST}";\n' + r"""
const PERSONS = [{rep: "R1", client: "ИВАНОВ И.", visited: false, n: 1,
                  totals: [{sum: 100, unit: "сум"}], ids: ["R1"], tels: []}];
const CARDS = {R1: {rep: "R1", client: "ИВАНОВ И."}};
const ME = {uid: 1, employee: "Первый П.", persons: PERSONS, cards: CARDS};
const Q = {op_id: "op-1", kind: "save", rep: "R1", values: {call_status: "x"},
           input_keys: ["call_status"], at: 1, form_version: 2,
           _owner: {uid: 1, employee: "Первый П."}};
const QF = {op_id: "op-2", kind: "save", rep: "R2", values: {}, input_keys: [], at: 2,
            form_version: 2, failed: "server-404", _owner: {uid: 1, employee: "Первый П."}};
const phone = (ls) => newWorld({ls, idb: {cards: CARDS, queue: [Q, QF]}});
const drainOnly = () => ({status: 410, body: {error: "drain_only", newUrl: "https://new.test"}});
"""


def _scenario(body: str) -> dict:
    return run_shell(PRELUDE + body)


def test_old_address_sends_the_queue_and_shows_status():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}, routes: {"/api/bootstrap": drainOnly}});
      const w = phone({app_token: A});
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      await settle(w);
      out.app = p.html("app"); out.langs = p.html("langs"); out.dump = w.dump();
      out.saves = p.fetchLog.filter(r => r.url === "/api/save").map(r => r.token);
    """)
    assert "Выгрузка на сервер" in out["app"]
    assert out["dump"]["queue"] == ["op-2"], "принятое ушло, отклонённое сервером держим"
    assert "ИВАНОВ" not in out["app"] or "Сервер не принял" in out["app"]
    assert "Сервер не принял" in out["app"], "отклонённое показано словами"
    assert A in out["saves"]
    assert "askLogout" not in out["langs"], "«Выйти» стёр бы очередь — на экране выгрузки его нет"
    assert "plist" not in out["app"], "портфеля на старом адресе нет"


def test_revoked_login_erases_old_origin_queue_before_any_send():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: 401}, routes: {"/api/bootstrap": drainOnly}});
      const w = phone({app_token: A});
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      await settle(w);
      out.dump=w.dump();out.requests=p.fetchLog;out.bar=p.html("offbar");
    """)
    assert "app_token" not in out["dump"]["ls"]
    assert out["dump"]["queue"] == []
    assert not any(r["url"] == "/api/save" for r in out["requests"])
    assert "Всё синхронизировано" not in out["bar"]


def test_texts_exist_in_all_three_languages():
    index = (Path(__file__).resolve().parent.parent / "server" / "miniapp" / "index.html").read_text("utf-8")
    for key in ("drainTitle:", "drainWhy:", "drainAuth:", "drainLogin:", "drainFailed:",
                "drainDrafts:", "drainOpen:", "drainNet:", "drainBadLink:", "drainOwner:"):
        assert len(re.findall(r"(?<![.\w])" + re.escape(key), index)) == 3, key


def test_unowned_legacy_queue_is_visible_but_never_sent_under_current_login():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}, routes: {"/api/bootstrap": drainOnly}});
      const w = newWorld({ls: {app_token: A}, idb: {cards: CARDS,
        queue: [{...Q, _owner: undefined}, QF]}});
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted(); await settle(w);
      out.app = p.html("app"); out.dump = w.dump();
      out.saves = p.fetchLog.filter(r => r.url === "/api/save").map(r => r.token);
    """)
    assert out["dump"]["queue"] == ["op-1", "op-2"]
    assert out["saves"] == [], "an unlabelled old operation cannot inherit a new user's bearer"
    assert "владелец не подтверждён" in out["app"]
    assert "Открыть новое приложение" not in out["app"]
