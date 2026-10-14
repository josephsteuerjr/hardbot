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
const ME = {employee: "Первый П.", persons: PERSONS, cards: CARDS};
const Q = {op_id: "op-1", kind: "save", rep: "R1", values: {call_status: "x"},
           input_keys: ["call_status"], at: 1, form_version: 2};
const QF = {op_id: "op-2", kind: "save", rep: "R2", values: {}, input_keys: [], at: 2,
            form_version: 2, failed: "server-404"};
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


def test_without_login_asks_for_a_link_then_sends():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[B]: ME}, routes: {
        "/api/bootstrap": drainOnly,
        "/api/redeem": () => ({status: 200, body: {ok: true, token: B}})}});
      const w = phone({app_token: A});
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      await settle(w);
      out.before = p.html("app"); out.queue_before = w.dump().queue;
      p.run(`document.getElementById("codein").value = "https://new.test/i/NEWCODE123"; drainLogin()`);
      await settle(w);
      out.after = p.html("app"); out.dump = w.dump();
      await p.run(`DRAIN.newUrl = "https://new.test"; drainDraw("done")`);
      await settle(w);
      out.final = p.html("app");
      out.redeem = JSON.parse(p.fetchLog.find(r => r.url === "/api/redeem").body);
    """)
    assert "нужен вход" in out["before"] and 'id="codein"' in out["before"]
    assert out["queue_before"] == ["op-1", "op-2"], "без входа ничего не теряется"
    assert out["redeem"]["code"] == "NEWCODE123"
    assert out["dump"]["ls"]["app_token"] == B
    assert out["dump"]["queue"] == ["op-2"]
    assert "Сервер не принял" in out["after"] and "Всё отправлено" not in out["after"]
    assert "Открыть новое приложение" in out["final"] and "https://new.test/" in out["final"]


def test_texts_exist_in_all_three_languages():
    index = (Path(__file__).resolve().parent.parent / "server" / "miniapp" / "index.html").read_text("utf-8")
    for key in ("drainTitle:", "drainWhy:", "drainAuth:", "drainLogin:", "drainFailed:",
                "drainDrafts:", "drainOpen:", "drainNet:", "drainBadLink:"):
        assert len(re.findall(r"(?<![.\w])" + re.escape(key), index)) == 3, key
