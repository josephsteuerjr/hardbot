"""Строки из данных в разметке и во встроенных обработчиках оболочки.

Ревью 25.09.2026, Low: esc не экранировал апостроф, а строки попадали в
onclick="f('…')": код офиса «Farg'ona» ломал кнопку фильтра, номер договора
шёл в routeDay вообще без экранирования, а enc()+decodeURIComponent не спасал —
encodeURIComponent апостроф не кодирует. Браузер раскодирует &#39; и &quot; в
атрибуте ДО разбора JS, поэтому строка в обработчике идёт через jsq()
(JSON.stringify + esc). Внешние ссылки «Доехать» — rel="noopener noreferrer",
поля комментария и доп. адреса — не длиннее 4000 (столько принимает сервер).

Обработчики проверяются так, как их исполнит браузер: значение атрибута
раскодируется и выполняется как тело функции с подменённой целью вызова.
"""
import re
from pathlib import Path

from shell_node import run_shell

INDEX = (Path(__file__).resolve().parent.parent / "server" / "miniapp" / "index.html").read_text("utf-8")

A = "TOK-A-aaaaaaaaaaaaaaaaaaaa"
NASTY = ["Farg'ona", "x'-alert(1)-'", 'a"b', "back\\slash", "</script><b>", "' onclick='x", " line"]

PRELUDE = f'const A = "{A}", HOST = "https://hard.test/";\n' + r"""
// Выполнить обработчик так, как его выполнит браузер: раскодировать атрибут и
// вызвать тело с подменённой функцией `name`; вернуть, с чем её позвали.
const call = (handler, name) => {
  const got = [];
  const fn = new Function(name, "event", decodeAttr(handler));
  fn((...a) => { got.push(...a); }, {preventDefault() {}});
  return got;
};
const onclicks = (html, name) => attrs(html, "onclick").filter(h => h.startsWith(name + "("));
"""


def _scenario(body: str) -> dict:
    return run_shell(PRELUDE + body)


def test_esc_escapes_the_apostrophe_too():
    out = _scenario(r"""
      const p = await openPage(newWorld({}), {url: HOST, server: fakeServer({})});
      await p.booted();
      out.v = p.run("esc(" + JSON.stringify("Farg'ona <b>\"x\" & y") + ")");
    """)
    assert out["v"] == "Farg&#39;ona &lt;b&gt;&quot;x&quot; &amp; y"


def test_jsq_survives_the_attribute_and_the_js_parser():
    out = _scenario(r"""
      const p = await openPage(newWorld({}), {url: HOST, server: fakeServer({})});
      await p.booted();
      out.rows = NASTY.map(s => {
        const handler = "f(" + p.run("jsq(" + JSON.stringify(s) + ")") + ")";
        return {raw_quote: handler.includes('"'), got: call(handler, "f")};
      });
    """.replace("NASTY", repr(NASTY)))
    for s, row in zip(NASTY, out["rows"]):
        assert not row["raw_quote"], f"кавычка рвёт атрибут: {s!r}"
        assert row["got"] == [s], f"строка доехала не той: {s!r} → {row['got']!r}"


def test_contract_with_an_apostrophe_opens_from_list_drafts_and_route():
    rep = "12'34-x'-alert(1)-'"
    out = _scenario(r"""
      const REP = __REP__;
      const person = {rep: REP, client: "Ne'matov", visited: false, n: 1,
                      totals: [{sum: 1, unit: "сум"}], ids: [REP], tels: []};
      const srv = fakeServer({tokens: {[A]: {employee: "Первый П.", persons: [person]}},
        routes: {"/api/route_day": () => ({status: 200, body: {ok: true, km: 1,
                 order: [{rep: REP, client: "Ne'matov", totals: [{sum: 1, unit: "сум"}], km: 1}]}})}});
      const w = newWorld({ls: {app_token: A,
        ["draft:1:" + REP]: JSON.stringify({v: {visit_comment: "x"}, keys: ["visit_comment"], at: 1})}});
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      out.list = onclicks(p.html("plist"), "openP").map(h => call(h, "openP"));
      out.drafts = onclicks(p.html("app"), "openP").map(h => call(h, "openP"));
      out.list_name = p.html("plist").includes("Ne&#39;matov");
      await p.run("routeDay()");
      await settle(w);
      out.route = onclicks(p.html("plist"), "openP").map(h => call(h, "openP"));
    """.replace("__REP__", repr(rep)))
    assert out["list"] == [[rep]], out["list"]
    assert out["drafts"] == [[rep]], out["drafts"]
    assert out["route"] == [[rep]], out["route"]
    assert out["list_name"]


def test_office_and_country_chips_survive_the_apostrophe():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: {employee: "Первый П.", persons: []}}});
      const p = await openPage(newWorld({ls: {app_token: A}}), {url: HOST, server: srv});
      await p.booted();
      p.run(`dgStats({range: {preset: "today", label: "сегодня", days: 1}, totals: {employees: 2},
        employees: [], by_day: [],
        countries: [{code: "U'Z", flag: "", short: "U'Z", employees: 1}, {code: "TJ", flag: "", short: "TJ", employees: 1}],
        offices: [{code: "Farg'ona", name: "Farg'ona", employees: 1}, {code: "Toshkent", name: "Toshkent", employees: 1}]},
        document.getElementById("app"))`);
      // v62: страна и офис выбираются листом снизу; в onclick — только индекс,
      // сама строка (с апострофом) живёт в состоянии и доезжает до отбора целой.
      p.run("dgFilterPick()");
      const sheet = p.html("picksheet");
      out.idx = onclicks(sheet, "dgFilterSet").map(h => call(h, "dgFilterSet"));
      out.picked = out.idx.map(([i]) => { p.run("dgFilterPick()"); p.run("dgFilterSet(" + i + ")");
                                          return p.run("[DG.office, DG.country]"); });
      out.shown = sheet.includes("Farg&#39;ona");
    """)
    assert all(len(a) == 1 and isinstance(a[0], int) for a in out["idx"]), out["idx"]
    assert ["Farg'ona", ""] in out["picked"] and ["Toshkent", ""] in out["picked"]
    assert ["", "U'Z"] in out["picked"]
    assert out["shown"]


def test_map_header_is_escaped_exactly_once():
    out = _scenario(r"""
      const p = await openPage(newWorld({}), {url: HOST, server: fakeServer({})});
      await p.booted();
      out.head = p.run(`dgHeadHTML({date: "2026-09-25", prev_date: "2026-09-24"}, "🗺 Ne'matov")`);
      out.prev = call(onclicks(out.head, "dgDate")[0], "dgDate");
    """)
    assert "Ne&#39;matov" in out["head"] and "&amp;#39;" not in out["head"]
    assert out["prev"] == ["2026-09-24"]
    assert 'dgHeadHTML(j,"🗺 "+esc(' not in INDEX, "двойное экранирование заголовка карты"


def test_drive_links_open_without_opener_and_carry_no_inline_address():
    out = _scenario(r"""
      const p = await openPage(newWorld({}), {url: HOST, server: fakeServer({})});
      await p.booted();
      out.html = p.run("providerChips(" + JSON.stringify({address: "Farg'ona, ул. \"Мир\" 5", geo: null}) + ")");
    """)
    html = out["html"]
    anchors = re.findall(r"<a [^>]*>", html)
    assert len(anchors) == 3
    for a in anchors:
        assert 'target="_blank"' in a and 'rel="noopener noreferrer"' in a, a
        assert 'onclick="openExt(event,this.href)"' in a, a
    assert "https://2gis.ru/search/Farg&#39;ona%2C%20%D1%83%D0%BB.%20%22" in html
    # Во всей оболочке: любая ссылка в новую вкладку — без window.opener.
    for m in re.finditer(r"<a [^>]*target=\"_blank\"[^>]*>", INDEX):
        assert 'rel="noopener noreferrer"' in m.group(0), m.group(0)


def test_no_string_is_glued_into_a_quoted_handler():
    for bad in ("('${esc(", "('${enc(", "openP('${", "decodeURIComponent('${"):
        assert bad not in INDEX, bad


def test_comment_and_tips_stop_at_the_server_limit():
    assert '<textarea id="f_visit_comment" maxlength="4000">' in INDEX
    assert '<textarea id="f_tips" maxlength="4000">' in INDEX
