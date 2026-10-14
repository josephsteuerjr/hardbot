"""Отзыв входа и «Выйти с этого телефона» стирают портфель с телефона.

Ревью 25.09.2026, #5: после отзыва доступа на телефоне оставались токен, снимок
портфеля kv:bootstrap и прогретые карточки, и в авиарежиме boot поднимал весь
портфель из снимка. Теперь сервер ответил 401/403 вне Телеграма — wipeDevice
стирает всё, кроме исходящей очереди (работа человека уйдёт после нового входа);
кнопка «Выйти» стирает и очередь, назвав сперва, сколько неотправленного
пропадёт. Нет сети или сервер лежит (5xx) — не стирается ничего: работа из
снимка без связи штатна.

Проверяется НАСТОЯЩАЯ оболочка под node (tests/shell_harness.js).
"""
from pathlib import Path

from shell_node import run_shell

INDEX = (Path(__file__).resolve().parent.parent / "server" / "miniapp" / "index.html").read_text("utf-8")

A = "TOK-A-aaaaaaaaaaaaaaaaaaaa"
HOST = "https://hard.test/"

PRELUDE = f'const A = "{A}", HOST = "{HOST}";\n' + r"""
const PERSONS = [{rep: "R1", client: "ИВАНОВ И.", visited: false, n: 1,
                  totals: [{sum: 100, unit: "сум"}], ids: ["R1"], tels: []}];
const CARDS = {R1: {rep: "R1", client: "ИВАНОВ И."}};
const ME = {employee: "Первый П.", persons: PERSONS, cards: CARDS};
const SNAP = {lang: "ru", meta: {}, persons: PERSONS, kind: "agent", employee: "Первый П.",
              roles: null, at: Date.now()};
const Q = {op_id: "op-1", kind: "save", rep: "R1", values: {call_status: "x"},
           input_keys: ["call_status"], at: 1, form_version: 3};
// Телефон, на котором уже поработали: вход, черновик, трек, следы, отметка
// прогрева, снимок портфеля, карточка, неотправленный выезд, кэш оболочки и карт.
const fullWorld = () => newWorld({
  ls: {app_token: A, hb_warm_at: "1",
       "draft:anon:R1": JSON.stringify({v: {visit_comment: "живёт у брата"}, keys: ["visit_comment"], at: 1}),
       hb_track_queue: JSON.stringify([{bid: "b1", points: []}]),
       "hb_presence:anon:R1": JSON.stringify({session: "s1", buf: []})},
  idb: {kv: {bootstrap: SNAP}, cards: CARDS, queue: [Q]},
  caches: {"hard-shell-v58": [], "maplibre-tiles": []},
});
"""


def _scenario(body: str) -> dict:
    return run_shell(PRELUDE + body)


def _assert_portfolio_gone(dump):
    assert "app_token" not in dump["ls"], "токен остался на телефоне"
    assert dump["kv"] == [], "снимок портфеля kv:bootstrap остался"
    assert dump["cards"] == [], "карточки остались"
    assert dump["caches"] == [], "Cache Storage не очищен"
    assert dump["ss"] == {}
    leftovers = set(dump["ls"]) - {"hb_out"}
    assert not leftovers, f"в localStorage остались: {sorted(leftovers)}"


def test_rejected_login_wipes_the_portfolio_but_keeps_the_queue():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: 401}});
      const w = fullWorld();
      const p = await openPage(w, {url: HOST + "#t=" + A, server: srv});
      await p.booted();
      out.dump = w.dump(); out.app = p.html("app"); out.langs = p.html("langs"); out.url = p.url();
    """)
    _assert_portfolio_gone(out["dump"])
    assert out["dump"]["queue"] == ["op-1"], "неотправленный выезд стёрт"
    assert "Срок доступа истёк" in out["app"], "человек не узнал, что вход истёк"
    assert 'id="codein"' in out["app"], "поле ручной вставки ссылки пропало"
    assert "askLogout" not in out["langs"]
    assert "#t=" not in out["url"], "мёртвый токен остался в адресе"


def test_handle_auth_gone_wipes_everything_but_the_queue():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}, saveStatus: 500});
      const w = fullWorld();
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      out.before = w.dump();
      await p.run("handleAuthGone()");
      await settle(w);
      out.after = w.dump(); out.app = p.html("app");
    """)
    before, after = out["before"], out["after"]
    assert before["ls"]["app_token"] == A
    assert before["kv"] == ["bootstrap"] and before["cards"] == ["R1"]
    assert before["queue"] == ["op-1"]
    _assert_portfolio_gone(after)
    assert after["queue"] == ["op-1"]
    assert "Срок доступа истёк" in out["app"]   # expired вычислен ДО очистки


def test_no_network_keeps_everything_and_works_from_the_snapshot():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: "neterr"}});
      const w = fullWorld();
      const before = w.dump();
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      out.before = before; out.after = w.dump(); out.list = p.html("plist");
    """)
    before, after = out["before"], out["after"]
    for key in ("kv", "cards", "queue", "caches"):
        assert after[key] == before[key], key
    assert after["ls"]["app_token"] == A
    assert "draft:anon:R1" in after["ls"] and "hb_track_queue" in after["ls"]
    assert "ИВАНОВ И." in out["list"], "оффлайн-снимок не поднялся"


def test_server_error_keeps_everything():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: 502}});
      const w = fullWorld();
      const before = w.dump();
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      out.before = before; out.after = w.dump();
    """)
    for key in ("kv", "cards", "queue", "caches"):
        assert out["after"][key] == out["before"][key], key
    assert out["after"]["ls"]["app_token"] == A


def test_logout_asks_with_the_count_and_then_wipes_the_queue_too():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}, saveStatus: 500});
      const w = fullWorld();
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      out.langs_before = p.html("langs");
      await p.run("askLogout()");
      await settle(w);
      out.sheet = p.html("outsheet");
      out.mid = w.dump();
      await p.run("logoutNow()");
      await settle(w);
      out.after = w.dump(); out.app = p.html("app"); out.langs = p.html("langs"); out.url = p.url();
      out.sheet_after = p.html("outsheet");
      out.mark = p.run("tokenMark(" + JSON.stringify(A) + ")");
      out.dialogs = p.dialogs;
      out.logout_tokens = p.tokensSeen("/api/logout");
    """)
    assert 'onclick="askLogout()"' in out["langs_before"] and "Выйти" in out["langs_before"]
    sheet = out["sheet"]
    assert "Выйти с этого телефона?" in sheet
    assert "Не отправлено операций: 1" in sheet
    assert 'onclick="logoutNow()"' in sheet
    # До ответа ничего не тронуто.
    assert out["mid"]["ls"]["app_token"] == A and out["mid"]["queue"] == ["op-1"]
    after = out["after"]
    _assert_portfolio_gone(after)
    assert after["queue"] == [], "«Выйти» обязано стереть и очередь"
    assert after["ls"] == {"hb_out": out["mark"]}
    assert "Нет доступа" in out["app"] and 'id="codein"' in out["app"]
    assert "askLogout" not in out["langs"]
    assert "#t=" not in out["url"]
    assert out["sheet_after"] is None
    assert out["dialogs"] == [], "нужен встроенный вопрос, а не confirm()"
    # Токен гасится и на сервере: у ярлыка iOS своё хранилище, отметка hb_out
    # туда не попадает, а вшитый в start_url токен иначе продолжал бы входить.
    assert out["logout_tokens"] == [A]


def test_after_logout_the_shortcut_does_not_log_back_in_by_itself():
    """У ярлыка токен вшит в start_url «/#t=<токен>»: без отметки «вышли» первый
    же запуск после «Выйти» молча вернул бы вход. Ручная вставка своей ссылки
    при этом работает."""
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}, saveStatus: 500});
      const w = fullWorld();
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      await p.run("logoutNow()");
      await settle(w);
      const p2 = await openPage(w, {url: HOST + "#t=" + A, server: srv});
      await p2.booted();
      out.relaunch = {token: w.ls.getItem("app_token"), boot_tokens: p2.tokensSeen("/api/bootstrap"),
                      app: p2.html("app")};
      p2.el("codein").value = HOST + "#t=" + A;
      await p2.run("enterCode()");
      await settle(w);
      out.pasted = {token: w.ls.getItem("app_token"), boot_tokens: p2.tokensSeen("/api/bootstrap")};
    """)
    relaunch, pasted = out["relaunch"], out["pasted"]
    assert relaunch["token"] is None, "ярлык вернул вход после «Выйти»"
    assert A not in relaunch["boot_tokens"]
    assert "Нет доступа" in relaunch["app"]
    assert pasted["token"] == A and pasted["boot_tokens"][-1] == A


def test_logout_button_lives_next_to_the_language_and_only_with_a_login():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME, "tg:tg-init": ME}});
      const w1 = newWorld({ls: {app_token: A}});
      const p1 = await openPage(w1, {url: HOST, server: srv});
      await p1.booted();
      out.with_login = p1.html("langs");
      const p2 = await openPage(newWorld({}), {url: HOST, server: srv});
      await p2.booted();
      out.no_login = p2.html("langs");
      const tg = {WebApp: {initData: "tg-init", initDataUnsafe: {user: {id: 5}}, viewportStableHeight: 700,
                           ready() {}, expand() {}, onEvent() {}}};
      const p3 = await openPage(newWorld({ls: {app_token: A}}), {url: HOST, server: srv, telegram: tg});
      await p3.booted();
      out.telegram = p3.html("langs");
    """)
    assert "setLang('ru')" in out["with_login"] and 'onclick="askLogout()"' in out["with_login"]
    assert "askLogout" not in out["no_login"]
    assert "askLogout" not in out["telegram"]
    for key in ("logout:", "logoutq:", "logoutwhy:", "logoutlost:", "cancel:"):
        assert INDEX.count(key) == 3, key


def test_manager_tab_401_wipes_but_a_revoked_role_does_not():
    out = _scenario(r"""
      const MGR = {employee: "Сотрудник С.", persons: PERSONS, cards: CARDS,
                   roles: {manager: true, employees: ["Сотрудник С."], acting: "Сотрудник С."}};
      let status = 403;
      const srv = fakeServer({tokens: {[A]: MGR}, saveStatus: 500,
                              routes: {"/api/manager/stats": () => ({status, body: {}})}});
      const w = fullWorld();
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      await p.run("renderDigest()");
      await settle(w);
      out.role_gone = {dump: w.dump(), body: p.html("dgbody")};
      status = 401;
      await p.run("renderDigest()");
      await settle(w);
      out.login_gone = {dump: w.dump(), app: p.html("app")};
    """)
    role_gone, login_gone = out["role_gone"], out["login_gone"]
    assert role_gone["dump"]["ls"]["app_token"] == A, "отняли роль — вход жив, стирать нечего"
    assert role_gone["dump"]["kv"] == ["bootstrap"]
    assert "Доступ к сводке отозван" in role_gone["body"]
    _assert_portfolio_gone(login_gone["dump"])
    assert login_gone["dump"]["queue"] == ["op-1"]
    assert "Срок доступа истёк" in login_gone["app"]


def test_background_refresh_waits_while_the_form_is_open():
    """Отзыв посреди заполнения формы не рвёт её: сохранить выезд в очередь
    человек ещё успеет. Очистит следующая проверка, когда форма закрыта."""
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}, saveStatus: 500});
      const w = fullWorld();
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      srv.cfg.tokens[A] = 401;
      p.run(`STATE.cur={rep:"R1"}; STATE.tab="form";
             document.getElementById("app").innerHTML='<textarea id="f_visit_comment"></textarea>';`);
      await p.run("refreshData()");
      await settle(w);
      out.form_open = w.dump();
      p.run(`STATE.cur=null; document.getElementById("app").innerHTML="";`);
      await p.run("refreshData()");
      await settle(w);
      out.form_closed = w.dump(); out.app = p.html("app");
    """)
    assert out["form_open"]["ls"]["app_token"] == A
    assert out["form_open"]["kv"] == ["bootstrap"]
    _assert_portfolio_gone(out["form_closed"])
    assert out["form_closed"]["queue"] == ["op-1"]
    assert "Срок доступа истёк" in out["app"]


def test_a_late_answer_does_not_bring_back_a_wiped_portfolio():
    """Ответ bootstrap, пришедший после «Выйти», не пишет снимок и не рисует
    чужой список поверх экрана входа."""
    out = _scenario(r"""
      let hold = false, release;
      const gate = new Promise(r => { release = r; });
      const base = fakeServer({tokens: {[A]: ME}, saveStatus: 500});
      const srv = async rec => {
        if (hold && rec.url === "/api/bootstrap") await gate;
        return base(rec);
      };
      const w = fullWorld();
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      hold = true;
      const late = p.run("boot()");
      await sleep(20);
      await p.run("logoutNow()");
      release();
      await late;
      await settle(w);
      out.dump = w.dump(); out.app = p.html("app"); out.list = p.html("plist");
    """)
    _assert_portfolio_gone(out["dump"])
    assert out["dump"]["queue"] == []
    assert "Нет доступа" in out["app"]
    assert out["list"] is None, "поздний ответ нарисовал список поверх экрана входа"
