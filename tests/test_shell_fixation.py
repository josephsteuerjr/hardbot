"""Чужая ссылка входа не подменяет живой вход без согласия человека.

Ревью 25.09.2026, #4: любой держатель живого токена собирал адрес
«/#t=<СВОЙ токен>&fresh=1» и присылал его в мессенджер. Метка fresh безусловно
перезаписывала localStorage.app_token, и выезды, фото и GPS жертвы уходили в
аккаунт атакующего. Теперь при ЖИВОМ входе новый токен ждёт в
sessionStorage.pending_token, загрузка идёт старым входом, а после неё человек
решает во встроенном диалоге. Мёртвый вход (401) подменяется молча — это самый
частый законный случай.

Проверяется НАСТОЯЩАЯ оболочка: головной скрипт и основной скрипт index.html
исполняются в node с заглушками браузера (tests/shell_harness.js). Образец
атаки — poc_fixation.js из ревью.
"""
from pathlib import Path

from shell_node import run_shell

INDEX = (Path(__file__).resolve().parent.parent / "server" / "miniapp" / "index.html").read_text("utf-8")

A = "TOK-A-aaaaaaaaaaaaaaaaaaaa"      # вход, который уже на телефоне
B = "TOK-B-bbbbbbbbbbbbbbbbbbbb"      # токен из присланной ссылки
DEAD = "TOK-D-dddddddddddddddddddd"   # вход, который сервер уже не пускает
HOST = "https://hard.test/"

PRELUDE = f'const A = "{A}", B = "{B}", DEAD = "{DEAD}", HOST = "{HOST}";\n' + r"""
const PERSONS = [{rep: "R1", client: "ИВАНОВ И.", visited: false, n: 1,
                  totals: [{sum: 100, unit: "сум"}], ids: ["R1"], tels: []}];
const CARDS = {R1: {rep: "R1", client: "ИВАНОВ И."}};
const ME = {employee: "Первый П.", persons: PERSONS, cards: CARDS};
const OTHER = {employee: "Чужой Ч.", persons: PERSONS, cards: CARDS};
const state = (p, w) => ({
  token: w.ls.getItem("app_token"), pending: w.ss.getItem("pending_token"),
  boot_tokens: p.tokensSeen("/api/bootstrap"), url: p.url(), manifest: p.manifestHref(),
  sheet: p.html("swapsheet"), reloads: p.reloads.slice(), dialogs: p.dialogs.slice(),
  dump: w.dump(),
});
"""


def _scenario(body: str) -> dict:
    return run_shell(PRELUDE + body)


def test_foreign_fresh_link_does_not_replace_a_live_login():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME, [B]: OTHER}});
      const w = newWorld({ls: {app_token: A}});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv});
      await p.booted();
      Object.assign(out, state(p, w));
    """)
    assert out["token"] == A, "чужой токен из ссылки занял устройство без согласия"
    assert out["pending"] == B
    assert out["boot_tokens"] == [A], "загрузка обязана идти старым входом"
    assert out["url"] == HOST + "#t=" + A, "чужой токен остался в адресе"
    # Головной скрипт собрал манифест с живым входом, а не с присланным.
    assert out["manifest"] == "/manifest.webmanifest?t=" + A
    sheet = out["sheet"]
    assert sheet and "Открыта новая ссылка входа." in sheet
    assert "Сейчас на этом телефоне вход: <b>Первый П.</b>" in sheet
    assert "нажмите «Оставить»" in sheet
    assert 'onclick="swapYes()"' in sheet and 'onclick="swapNo()"' in sheet
    assert out["dialogs"] == [], "нужен встроенный диалог, а не confirm()/prompt()"
    assert out["reloads"] == []


def test_keep_drops_the_link_for_good():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME, [B]: OTHER}});
      const w = newWorld({ls: {app_token: A}});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv});
      await p.booted();
      p.run("swapNo()");
      out.kept = state(p, w);
      out.kept.urlt = p.run("URLT");
      // Отклонённая ссылка не должна всплыть и потом: вход умер — телефон
      // очищается, а не молча переходит на то, от чего человек отказался.
      srv.cfg.tokens[A] = 401;
      await p.run("boot()");
      await settle(w);
      out.later = state(p, w);
      out.later.app = p.html("app");
    """)
    kept, later = out["kept"], out["later"]
    assert kept["token"] == A and kept["pending"] is None
    assert kept["sheet"] is None and kept["urlt"] is None
    assert later["token"] is None
    assert B not in later["boot_tokens"], "отклонённый токен всё-таки подставлен"
    assert "Срок доступа истёк" in later["app"]


def test_replace_wipes_the_old_login_and_reloads_with_the_new_one():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME, [B]: OTHER}});
      const w = newWorld({ls: {app_token: A, "draft:anon:R1": JSON.stringify({v: {visit_comment: "x"}})},
                          caches: {"hard-shell-v58": []}});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv});
      await p.booted();
      out.before = w.dump();
      await p.run("swapYes()");
      await settle(w);
      out.after = state(p, w);
      out.mark_a = p.run("tokenMark(" + JSON.stringify(A) + ")");
      out.logout_tokens = p.tokensSeen("/api/logout");
      // Перезагрузка — новая страница в том же мире.
      const p2 = await openPage(w, {url: p.reloads[0], server: srv});
      await p2.booted();
      out.next = state(p2, w);
    """)
    before, after, nxt = out["before"], out["after"], out["next"]
    assert before["kv"] == ["bootstrap"] and before["cards"] == ["R1"]
    assert after["token"] == B and after["pending"] is None
    assert after["reloads"] == [HOST + "#t=" + B]
    dump = after["dump"]
    assert dump["kv"] == [] and dump["cards"] == [] and dump["queue"] == []
    assert dump["caches"] == []
    # От прежнего входа на телефоне остался только отпечаток «из него вышли».
    assert dump["ls"] == {"app_token": B, "hb_out": out["mark_a"]}
    assert out["logout_tokens"] == [A], "прежний вход гасится и на сервере"
    assert nxt["boot_tokens"] == [B] and nxt["reloads"] == [] and nxt["sheet"] is None
    assert nxt["manifest"] == "/manifest.webmanifest?t=" + B


def test_replace_first_sends_the_old_queue_with_the_old_login():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME, [B]: OTHER}});
      const w = newWorld({ls: {app_token: A}});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv});
      await p.booted();
      // Выезд, сделанный уже после загрузки: он в очереди и ещё не ушёл.
      await p.run(`enqueue({op_id: "op-late", kind: "save", rep: "R1",
                   values: {call_status: "x"}, input_keys: ["call_status"], at: 5, form_version: 3})`);
      await p.run("swapYes()");
      await settle(w);
      out.save_tokens = p.tokensSeen("/api/save");
      Object.assign(out, state(p, w));
    """)
    assert out["save_tokens"] == [A], "очередь ушла не под тем входом"
    assert out["token"] == B and out["reloads"] == [HOST + "#t=" + B]


def test_replace_with_unsent_work_names_the_loss_and_asks_again():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME, [B]: OTHER}, saveStatus: 500});
      const Q = {op_id: "op-1", kind: "save", rep: "R1", values: {call_status: "x"},
                 input_keys: ["call_status"], at: 1, form_version: 3};
      const w = newWorld({ls: {app_token: A}, idb: {queue: [Q]}});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv});
      await p.booted();
      await p.run("swapYes()");
      await settle(w);
      out.asked = state(p, w);
      await p.run("swapYes(true)");
      await settle(w);
      out.done = state(p, w);
    """)
    asked, done = out["asked"], out["done"]
    assert "Не отправлено операций: 1" in asked["sheet"]
    assert 'onclick="swapYes(true)"' in asked["sheet"]
    assert asked["token"] == A and asked["pending"] == B, "стёрли, не дождавшись ответа"
    assert asked["dump"]["queue"] == ["op-1"] and asked["reloads"] == []
    assert done["token"] == B and done["dump"]["queue"] == []
    assert done["reloads"] == [HOST + "#t=" + B]
    assert done["dialogs"] == []


def test_dead_login_is_replaced_silently_and_the_manifest_rebuilt():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[DEAD]: 401, [B]: OTHER}});
      const w = newWorld({ls: {app_token: DEAD}});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv});
      await p.booted();
      out.first = state(p, w);
      const p2 = await openPage(w, {url: p.reloads[0], server: srv});
      await p2.booted();
      out.second = state(p2, w);
    """)
    first, second = out["first"], out["second"]
    assert first["boot_tokens"] == [DEAD]
    assert first["token"] == B and first["pending"] is None and first["sheet"] is None
    # Манифест в <head> собран с мёртвым входом — страница перезагружается один раз.
    assert first["manifest"] == "/manifest.webmanifest?t=" + DEAD
    assert first["reloads"] == [HOST + "#t=" + B]
    assert second["manifest"] == "/manifest.webmanifest?t=" + B
    assert second["boot_tokens"] == [B] and second["reloads"] == []
    assert second["sheet"] is None and second["dialogs"] == []


def test_link_opened_offline_is_asked_about_once_the_login_answers():
    """Без связи старый вход не проверить: работаем из снимка и молчим. Вопрос
    задаёт фоновая проверка, как только сервер подтвердил старый вход; уже
    открытый вопрос повторная проверка не сбивает."""
    out = _scenario(r"""
      const SNAP = {lang: "ru", meta: {}, persons: PERSONS, kind: "agent", uid: 1,
                    employee: "Первый П.", roles: null, at: Date.now(), _credential: "token:"+A};
      const srv = fakeServer({tokens: {[A]: "neterr", [B]: OTHER}});
      const w = newWorld({ls: {app_token: A}, idb: {kv: {bootstrap: SNAP}}});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv});
      await p.booted();
      out.offline = state(p, w);
      out.offline.list = p.html("plist");
      srv.cfg.tokens[A] = ME;
      await p.run("refreshData()");
      await settle(w);
      out.online = state(p, w);
      p.run('document.getElementById("swapsheet").innerHTML = "<i>second step</i>"; LAST_REFRESH = 0;');
      await p.run("refreshData()");
      await settle(w);
      out.again = p.html("swapsheet");
    """)
    offline, online = out["offline"], out["online"]
    assert offline["token"] == A and offline["pending"] == B and offline["sheet"] is None
    assert "ИВАНОВ И." in offline["list"], "без связи не поднялся снимок"
    assert online["token"] == A and online["pending"] == B
    assert "Сейчас на этом телефоне вход: <b>Первый П.</b>" in online["sheet"]
    assert out["again"] == "<i>second step</i>", "фоновая проверка сбила открытый вопрос"


def test_without_a_login_the_link_is_the_login():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[B]: OTHER}});
      const w = newWorld({});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv});
      await p.booted();
      Object.assign(out, state(p, w));
    """)
    assert out["token"] == B and out["pending"] is None and out["sheet"] is None
    assert out["manifest"] == "/manifest.webmanifest?t=" + B
    assert out["boot_tokens"] == [B] and out["reloads"] == []


def test_reopening_own_link_asks_nothing():
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME}});
      const w = newWorld({ls: {app_token: A}});
      const p = await openPage(w, {url: HOST + "#t=" + A + "&fresh=1", server: srv});
      await p.booted();
      Object.assign(out, state(p, w));
    """)
    assert out["token"] == A and out["pending"] is None and out["sheet"] is None
    assert out["url"] == HOST + "#t=" + A


def test_hashchange_with_a_foreign_token_asks_instead_of_switching():
    """Ссылка открылась в уже живой вкладке: раньше «#t=…» без метки молча
    переключал вход — та же дыра, что и с fresh."""
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME, [B]: OTHER}});
      const w = newWorld({ls: {app_token: A}});
      const p = await openPage(w, {url: HOST, server: srv});
      await p.booted();
      await p.hash("#t=" + B);
      Object.assign(out, state(p, w));
    """)
    assert out["token"] == A and out["pending"] == B
    assert out["boot_tokens"] == [A, A], "вкладка перезагрузилась чужим входом"
    assert "Первый П." in out["sheet"]
    assert out["dialogs"] == []


def test_link_code_of_someone_else_also_waits_for_consent():
    """Старая ссылка «#link=<код>»: сервер меняет чужой код на чужой токен — это
    та же подмена личности, только через обмен кода."""
    out = _scenario(r"""
      const srv = fakeServer({tokens: {[A]: ME, [B]: OTHER},
                              routes: {"/api/redeem": () => ({status: 200, body: {ok: true, token: B}})}});
      const w = newWorld({ls: {app_token: A}});
      const p = await openPage(w, {url: HOST + "#link=CODE12345678", server: srv});
      await p.booted();
      Object.assign(out, state(p, w));
      out.redeem = JSON.parse(p.fetchLog.find(r => r.url === "/api/redeem").body);
    """)
    assert out["redeem"]["token"] == A, "обмен обязан показывать серверу свой вход"
    assert out["token"] == A and out["pending"] == B
    assert out["boot_tokens"] == [A] and out["sheet"]


def test_manager_is_named_by_role_in_the_dialog():
    out = _scenario(r"""
      const MGR = {employee: "Сотрудник С.", persons: PERSONS,
                   roles: {manager: true, employees: ["Сотрудник С."], acting: "Сотрудник С."}};
      const srv = fakeServer({tokens: {[A]: MGR, [B]: OTHER}});
      const w = newWorld({ls: {app_token: A}});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv});
      await p.booted();
      out.sheet = p.html("swapsheet");
    """)
    assert "Сейчас на этом телефоне вход: <b>руководитель · Сотрудник С.</b>" in out["sheet"]


def test_inside_telegram_account_bearer_keeps_swap_and_logout():
    """Подписанный Telegram-контекст не даёт без вопроса заменить аккаунтный
    токен, а для парольного входа кнопка выхода остаётся доступна."""
    out = _scenario(r"""
      const tg = {WebApp: {initData: "tg-init", initDataUnsafe: {user: {id: 5}}, viewportStableHeight: 700,
                           ready() {}, expand() {}, onEvent() {}}};
      const srv = fakeServer({tokens: {"tg:tg-init": ME}});
      const w = newWorld({ls: {app_token: A}});
      const p = await openPage(w, {url: HOST + "#t=" + B + "&fresh=1", server: srv, telegram: tg});
      await p.booted();
      Object.assign(out, state(p, w));
      out.langs = p.html("langs");
    """)
    assert out["token"] == B and out["pending"] is None and out["sheet"] is None
    assert "askLogout" in out["langs"]


def test_dialog_texts_exist_in_all_three_languages():
    for key in ("swaptitle:", "swapnow:", "swapwarn:", "swapyes:", "swapno:",
                "swaplost:", "swapmgr:"):
        assert INDEX.count(key) == 3, key


def test_swap_and_logout_never_use_system_dialogs():
    body = INDEX[INDEX.index("function askSheet("):INDEX.index("let START_TAB")]
    for call in ("confirm(", "prompt(", "alert("):
        assert call not in body, call
