"""Чек об оплате в оболочке: секция в карточке, список приложенных, досыл
из оффлайн-очереди запросом /api/receipt с полями в query.

Проверяется НАСТОЯЩАЯ оболочка под node (tests/shell_harness.js): как и в
test_shell_wipe, сошлась бы копия, а не то, что уедет на телефон.
"""
from shell_node import run_shell

A = "TOK-A-aaaaaaaaaaaaaaaaaaaa"
HOST = "https://hard.test/"

PRELUDE = f'const A = "{A}", HOST = "{HOST}";\n' + r"""
const PERSONS = [{rep: "R1", client: "ИВАНОВ И.", visited: false, n: 1,
                  totals: [{sum: 100, unit: "сум"}], ids: ["R1"], tels: []}];
const CARD = {rep: "R1", client: "ИВАНОВ И.", visited: false, form: [],
              contracts: [], birth_date: "", photos: 0, receipts: [
                {paid_at: "2026-09-28", amount: 1500.5, payer_is_client: true},
                {paid_at: "2026-09-29", amount: 700, payer_is_client: false,
                 payer_note: "платил брат"}]};
const ME = {employee: "Первый П.", persons: PERSONS, cards: {R1: CARD}};
const RCQ = {op_id: "rc-op-1", kind: "receipt", rep: "R1",
             bytes: new Uint8Array([1, 2, 3]), ext: ".jpg", at: 5,
             amount: 2000, paid_at: "2026-09-30", payer_other: true,
             note: "сестра"};
"""


def _scenario(body: str) -> dict:
    return run_shell(PRELUDE + body)


def test_card_shows_receipt_section_and_list():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}, routes: {
        "/api/person": () => ({status: 200, body: CARD})}});
      const w = newWorld({ls: {app_token: A}, idb: {cards: {R1: CARD}}});
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted(); await settle(w);
      await p.run(`openP("R1")`);
      await settle(w);
      p.run(`setTab("form")`);   // секция чеков живёт на вкладке формы визита
      await p.run(`renderReceipts()`);   // список наполняется асинхронно — ждём явно
      out.panel = p.html("panel");
      out.rclist = p.html("rclist");
    """)
    assert "Чек об оплате" in out["panel"], "секции чеков нет в форме визита"
    assert "Приложить чек" in out["panel"]
    # приложенные чеки показываются сразу: дата, сумма, плательщик
    # (список читаем по самому rclist: DOM-заглушка строковая и заполнение
    #  дочернего узла не отражается в снимке родителя)
    assert "2026-09-28" in out["rclist"] and "1 500,5" in out["rclist"]
    assert "платил брат" in out["rclist"], "пояснение «кто платил» не показано"
    assert "Другое лицо" in out["rclist"]


def test_receipt_form_fields():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}, routes: {
        "/api/person": () => ({status: 200, body: CARD})}});
      const w = newWorld({ls: {app_token: A}, idb: {cards: {R1: CARD}}});
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted(); await settle(w);
      await p.run(`openP("R1")`); await settle(w);
      p.run(`setTab("form")`); await settle(w);
      p.run(`openReceipt()`); await settle(w);
      out.sheet = p.html("rcsheet");
      p.run(`rcSetPayer(true)`); await settle(w);
      out.other = p.html("rcsheet");
    """)
    for fragment in ("rc_amount", 'type="date"', "Платил должник", "Другое лицо",
                     "Сумма платежа", "Дата платежа"):
        assert fragment in out["sheet"], f"в форме чека нет {fragment}"
    # пояснение «кто и почему» появляется только для другого лица
    assert 'style="display:none"' in out["sheet"]
    assert 'style="display:none"' not in out["other"] or "rc_who" in out["other"]


def test_queue_sends_receipt_with_fields_in_query():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}, routes: {
        "/api/receipt": () => ({status: 200, body: {ok: true, id: 7}})}});
      const w = newWorld({ls: {app_token: A}, idb: {cards: {R1: CARD}, queue: [RCQ]}});
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted(); await settle(w);
      out.dump = w.dump();
      out.rc = p.fetchLog.find(r => r.url.indexOf("/api/receipt") === 0);
    """)
    assert out["rc"], "досыл чека не ушёл"
    url = out["rc"]["url"]
    for fragment in ("amount=2000", "paid_at=2026-09-30", "payer=other",
                     "note=%D1%81%D0%B5%D1%81%D1%82%D1%80%D0%B0", "rep=R1"):
        assert fragment in url, f"в запросе чека нет {fragment}: {url}"
    assert out["rc"].get("method") == "POST"
    assert out["dump"]["queue"] == [], "доставленный чек остался в очереди"


# --- витрина руководителя/СБ: чеки в карточке и экран «Закрепления» (статика) -----
def test_receipts_and_assignments_wired_into_manager_screens():
    from pathlib import Path
    index = (Path(__file__).resolve().parent.parent
             / "server" / "miniapp" / "index.html").read_text("utf-8")
    dg = index[index.index("let DG={"):index.index("const PHOTOBLOBS")]
    # чек в карточке должника: файл тянется авторизованным fetch'ем, токен не в query
    assert "/api/manager/receipt_photo?file=" in dg
    i = dg.index("async function dgReceipt(")
    body = dg[i:dg.index("\n}", i)]
    assert "authHeaders()" in body and "token=" not in body
    # экран закреплений: свой URL и рендер, плитка видна только роли с control
    assert '"/api/control/assignments"' in dg and "dgAssignScreen" in dg
    assert 'caps.control?tile("swap","Закрепления","dgGo(\'assign\')")' in dg
    # onclick без ФИО — только индексы (правило вкладки руководителя)
    import re
    for m in re.finditer(r'onclick="([^"]*)"', dg[dg.index("function dgAssignScreen"):
                                                dg.index("// --- ЭКРАН: вход команды")]):
        for bad in ("${r.employee", "${e.employee", "${c.rep"):
            assert bad not in m.group(1), m.group(1)
