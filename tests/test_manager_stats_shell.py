"""Экран статистики в оболочке: инварианты, которые дешевле сломать, чем заметить."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "server" / "miniapp" / "index.html").read_text("utf-8")


def _section():
    return INDEX[INDEX.index("let DG={"):INDEX.index("const PHOTOBLOBS")]


def test_landing_is_stats_and_talks_to_the_new_endpoints():
    body = _section()
    # v62: «Сводка» и «Сотрудники» входят через общий dgEnter, окно — то же.
    assert 'return dgEnter("stats")' in body and "DG.view=view" in body
    for path in ("/api/manager/stats", "/api/manager/stats_employee", "/api/manager/stats.xlsx"):
        assert path in body, path


def test_client_never_computes_dates():
    """Окно периода считает сервер: клиент шлёт пресет и сдвиг либо строки из
    полей «с/по». Ни одной даты на клиенте — контейнер в UTC, телефон в +3/+5."""
    body = _section()
    assert "toISOString" not in body
    i = body.index("function dgRangeQ(")
    q = body[i:body.index("\n}", i)]
    assert "new Date" not in q and 'p.set("preset"' in q and 'p.set("shift"' in q
    for m in re.finditer(r"DG\.(from|to|date)=([^;]+);", body):
        assert "new Date" not in m.group(2), m.group(0)


def test_presets_and_custom_dates_are_present():
    body = _section()
    for key in ("today", "yesterday", "7d", "30d", "month", "custom"):
        assert f'["{key}",' in body, key
    assert 'type="date" id="dgfrom"' in body and 'type="date" id="dgto"' in body
    assert "dgShift(-1)" in body and "dgShift(1)" in body


def test_onclick_carries_only_indices_or_literals():
    body = _section()
    for m in re.finditer(r'onclick="([^"]*)"', body):
        call = m.group(1)
        for bad in ("${r.employee", "${c.client", "${c.rep", "${p.client", "${it."):
            assert bad not in call, call


def test_excel_goes_through_headers_not_query_token():
    i = INDEX.index("async function dgExcel(")
    body = INDEX[i:INDEX.index("\n}", i)]
    assert "authHeaders()" in body and "token" not in body.lower()
    assert "URL.createObjectURL" in body


def test_table_is_sortable_and_filterable_without_losing_focus():
    body = _section()
    assert "function dgSort(" in body and "function dgCountry(" in body
    # поле поиска живёт вне перерисовываемой таблицы: иначе фокус терялся бы на каждой букве
    # v62: таблица — на вкладке «Сотрудники» (dgTeam), поле поиска там же, вне таблицы.
    stats = body[body.index("function dgTeam("):body.index("function dgCountryTotals(")]
    assert 'oninput="dgSearch(this.value)"' in stats and '<div id="dgtable">' in stats
    draw = body[body.index("function dgDrawTable("):body.index("function dgEmp(")]
    assert "oninput" not in draw


def test_wide_container_only_on_the_manager_tab():
    assert ".wrap.wide{max-width:920px}" in INDEX
    assert '$("app").classList.toggle("wide", active==="digest"||active==="team")' in INDEX


def test_no_auto_polling():
    for m in re.finditer(r"setInterval\(([^,]+),", INDEX):
        assert "dgLoad" not in m.group(1) and "renderDigest" not in m.group(1)
