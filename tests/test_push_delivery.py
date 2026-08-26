"""Доставка уведомлений: адресация (направленцы, нормализация ФИО), честная
защёлка с ретраем, журнал push_log, оракул альбома и единый гео-вердикт.

Дыра web.py:1697 (направленцы без визит-пуша) была зелёной именно потому, что
тесты пушей знали только руководителя с явным списком — этот файл её пинит.
"""
import asyncio
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import importlib.util

import pytest
from aiogram.exceptions import TelegramForbiddenError

import db
import notify
import photostore
import web

# hardbot/app.py нельзя импортировать как «app»: это имя занято пакетом
# engine/app (test_engine_app_imports). Грузим по пути под своим именем.
_spec = importlib.util.spec_from_file_location(
    "hardbot_app", Path(web.__file__).with_name("app.py"))
botapp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(botapp)

TODAY = date.today().isoformat()
EMP = "Доставкин Сотрудник"
CID = "PD-1"


class FakeBot:
    """Телеграм с настраиваемым сценарием отказов по chat_id."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}      # chat_id -> Exception

    def _maybe_fail(self, chat_id):
        exc = self.fail.get(chat_id)
        if exc:
            raise exc

    async def send_message(self, chat_id, text, reply_markup=None):
        self._maybe_fail(chat_id)
        self.calls.append(("msg", chat_id, text))

    async def send_media_group(self, chat_id, media):
        self._maybe_fail(chat_id)
        self.calls.append(("album", chat_id, media))
        return [SimpleNamespace(photo=[SimpleNamespace(file_id=f"fid{i}")])
                for i in range(len(media))]

    async def send_photo(self, chat_id, photo, caption=None):
        self._maybe_fail(chat_id)
        self.calls.append(("photo", chat_id, photo))
        return SimpleNamespace(photo=[SimpleNamespace(file_id="fid0")])

    async def send_document(self, chat_id, doc, caption=None):
        self._maybe_fail(chat_id)
        self.calls.append(("doc", chat_id, doc))


def _forbidden():
    # aiogram требует method/message; собираем минимально честный экземпляр
    try:
        return TelegramForbiddenError(
            method=SimpleNamespace(), message="Forbidden: bot can't initiate conversation")
    except TypeError:
        return TelegramForbiddenError("Forbidden: bot can't initiate conversation")


def _clear_push_log():
    with db.connect() as conn:
        conn.execute("DELETE FROM push_log")
        conn.commit()
    botapp._SETTLED_MEM.clear()
    botapp._HOLD_UNTIL.clear()
    for k in ("digest_retry_day", "digest_retry_n", "promise_retry_day",
              "promise_retry_n", "digest_last_run", "promise_last_run"):
        db.set_setting(k, "")


def _rows(kind=None, status=None):
    q, args = "SELECT * FROM push_log", []
    conds = []
    if kind:
        conds.append("kind=?")
        args.append(kind)
    if status:
        conds.append("status=?")
        args.append(status)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(q, args)]


def _seed_client(resp=EMP, country=None):
    db.init_db()
    row = {"contract_id": CID, "client": "Пушев П.", "responsible": resp,
           "call_status": "В работе", "agreed_payment": "Обещает оплатить клиент"}
    if country:
        row["country"] = country
    with db.connect() as conn:
        keys = ", ".join(f'"{k}"' for k in row)
        conn.execute(f"INSERT OR REPLACE INTO clients ({keys}) VALUES "
                     f"({', '.join('?' * len(row))})", tuple(row.values()))
        conn.commit()
    db.set_setting("visit_push", "1")
    d = photostore.PHOTO_DIR / CID
    if d.is_dir():
        for f in d.iterdir():
            f.unlink()


def _push(bot=None, row=None, **kw):
    bot = bot or FakeBot()
    req = SimpleNamespace(app={"bot": bot})
    asyncio.run(web._notify_managers(req, row or db.get_client(CID), **kw))
    return bot


# --- адресация ----------------------------------------------------------------
def test_direction_manager_gets_visit_push():
    _seed_client(country="Узбекистан")
    db.set_manager(9101, "Направленец", [], [], direction="uz")
    _clear_push_log()
    bot = _push()
    assert any(c[1] == 9101 for c in bot.calls)   # дыра web.py:1697 закрыта
    db.delete_manager(9101)


def test_visit_push_matches_name_by_normalized_key(caplog):
    _seed_client()
    db.set_manager(9102, "Опечаткин", [], ["доставкин  сотрудник"])
    _clear_push_log()
    import logging as _l
    with caplog.at_level(_l.INFO):
        bot = _push()
    assert any(c[1] == 9102 for c in bot.calls)
    row = _rows(kind=db.PUSH_VISIT, status=db.PUSH_SENT)[0]
    assert (row["note"] or "").startswith("name-key:")
    assert any("нормализованному ключу" in r.message for r in caplog.records)
    db.delete_manager(9102)


def test_matched_as_does_not_leak_between_employees():
    _seed_client()
    db.set_manager(9103, "Двое", [], [EMP, "Второй Сотрудник"])
    _clear_push_log()
    _push()
    row = _rows(kind=db.PUSH_VISIT, status=db.PUSH_SENT)[0]
    assert row["note"] is None       # написание совпало — «matched_as» не чужой
    db.delete_manager(9103)


def test_manager_employees_keeps_role_spelling():
    """Выдача прав НЕ канонизирует написание: строка уезжает клиенту как
    d.employee, и её смена стирала бы оффлайн-очередь выездника."""
    _seed_client()
    db.set_manager(9104, "Роль", [], ["доставкин  сотрудник"])
    allowed = db.manager_employees(9104)
    assert allowed == ["доставкин  сотрудник"]
    db.delete_manager(9104)


def test_registry_name_index_picks_the_dominant_spelling():
    _seed_client()
    with db.connect() as conn:
        for i in range(3):
            conn.execute(
                "INSERT OR REPLACE INTO clients (contract_id, client, responsible) "
                "VALUES (?,?,?)", (f"PD-D{i}", f"К{i}", "Индексов Доминант"))
        conn.execute(
            "INSERT OR REPLACE INTO clients (contract_id, client, responsible) "
            "VALUES (?,?,?)", ("PD-D9", "К9", "индексов доминант"))
        conn.commit()
    db.reset_role_caches()
    idx = db.registry_name_index()
    assert idx[db._name_key("Индексов Доминант")] == "Индексов Доминант"


# --- ранние return и рубильник -------------------------------------------------
def test_every_early_return_leaves_a_row():
    _seed_client()
    _clear_push_log()
    # 1) нет бота
    asyncio.run(web._notify_managers(SimpleNamespace(app={}), db.get_client(CID)))
    assert _rows(status=db.PUSH_SKIP_NO_BOT)
    # 2) выключен рубильник
    db.set_setting("visit_push", "0")
    _push()
    assert _rows(status=db.PUSH_SKIP_DISABLED)
    db.set_setting("visit_push", "1")
    # 3) пустой responsible
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible='' WHERE contract_id=?", (CID,))
        conn.commit()
    _push()
    assert _rows(status=db.PUSH_SKIP_NO_EMPLOYEE)
    # 4) нет руководителей
    _seed_client()
    db.reset_role_caches()
    _push()
    assert _rows(status=db.PUSH_SKIP_NO_TARGETS)


def test_visit_push_switch_understands_no():
    _seed_client()
    db.set_manager(9105, "В", [], [EMP])
    db.set_setting("visit_push", "no")   # раньше НЕ выключал (web разбирал 3 значения)
    bot = _push()
    assert bot.calls == []
    db.set_setting("visit_push", "1")
    db.delete_manager(9105)


# --- гео-вердикт и actor --------------------------------------------------------
def test_geo_verdict_comes_from_the_saved_flag():
    _seed_client()
    db.set_manager(9106, "Гео", [], [EMP])
    bot = _push(dist_m=1500, geo_far=False)
    assert "На адресе" in bot.calls[0][2] and "⚠️" not in bot.calls[0][2]
    bot = _push(dist_m=1500, geo_far=None)   # старые записи — прежний порог
    assert "⚠️" in bot.calls[0][2]
    db.delete_manager(9106)


def test_actor_is_visible_in_manager_push_and_group(monkeypatch):
    _seed_client()
    db.set_manager(9107, "Актёрский", [], [EMP])
    bot = _push(actor="руководитель Василий")
    assert "закрыл: руководитель Василий" in bot.calls[0][2]
    # и в посте в группу
    db.set_setting("group_chat_id", "777")
    gbot = FakeBot()
    req = SimpleNamespace(app={"bot": gbot})
    asyncio.run(web._notify_group(req, db.get_client(CID), 1,
                                  actor="руководитель Василий"))
    assert any("закрыл: руководитель Василий" in c[2] for c in gbot.calls)
    db.set_setting("group_chat_id", "")
    db.delete_manager(9107)


def test_group_notify_never_escapes_to_save(monkeypatch):
    _seed_client()
    _clear_push_log()

    def boom():
        raise RuntimeError("db locked")
    monkeypatch.setattr(notify, "group_chat_id", boom)
    req = SimpleNamespace(app={"bot": FakeBot()})
    asyncio.run(web._notify_group(req, db.get_client(CID), 1))   # не бросает


# --- защёлка и ретрай -----------------------------------------------------------
def _daily(bot, kind=db.PUSH_DIGEST):
    push = botapp._push_digests if kind == db.PUSH_DIGEST else botapp._push_promises
    return asyncio.run(botapp._daily_once(
        kind, TODAY, lambda attempt: push(bot, TODAY, attempt)))


def test_latch_waits_for_a_successful_delivery(monkeypatch):
    _seed_client()
    _clear_push_log()
    monkeypatch.setattr(botapp, "ADMIN_ID", 0)
    db.set_manager(9108, "Сетевой", [], [EMP])
    bot = FakeBot(fail={9108: ConnectionError("net down")})
    assert _daily(bot) is False                     # защёлки нет
    assert _rows(kind=db.PUSH_DIGEST, status=db.PUSH_FAILED)
    for _ in range(botapp.PUSH_RETRY_LIMIT):        # потолок замолкает
        latched = _daily(bot)
    assert latched is True
    db.delete_manager(9108)


def test_latch_survives_send_that_always_raises(monkeypatch):
    _seed_client()
    _clear_push_log()
    monkeypatch.setattr(botapp, "ADMIN_ID", 0)

    async def bad_send(attempt):
        raise RuntimeError("db down")
    latched = False
    for _ in range(botapp.PUSH_RETRY_LIMIT):
        latched = asyncio.run(botapp._daily_once(db.PUSH_DIGEST, TODAY, bad_send))
    assert latched is True                          # потолок сработал и на исключении


def test_retry_does_not_resend_to_those_who_already_got_it(monkeypatch):
    _seed_client()
    _clear_push_log()
    monkeypatch.setattr(botapp, "ADMIN_ID", 0)
    db.set_manager(9109, "Успешный", [], [EMP])
    db.set_manager(9110, "Сбойный", [], [EMP])
    bot = FakeBot(fail={9110: ConnectionError("net")})
    assert _daily(bot) is False
    first = [c for c in bot.calls if c[1] == 9109]
    assert len(first) == 1
    bot.fail = {}
    assert _daily(bot) is True
    assert len([c for c in bot.calls if c[1] == 9109]) == 1   # без дубля
    assert len([c for c in bot.calls if c[1] == 9110]) == 1   # добрали
    db.delete_manager(9109)
    db.delete_manager(9110)


def test_blocked_manager_does_not_hold_the_latch(monkeypatch):
    _seed_client()
    _clear_push_log()
    monkeypatch.setattr(botapp, "ADMIN_ID", 0)
    db.set_manager(9111, "Заблокированный", [], [EMP])
    bot = FakeBot(fail={9111: _forbidden()})
    assert _daily(bot) is True                     # Forbidden чинит человек
    assert _rows(kind=db.PUSH_DIGEST, status=db.PUSH_BLOCKED)
    assert db.get_manager(9111)["can_dm"] == 0
    db.delete_manager(9111)


def test_network_failure_does_not_clear_can_dm(monkeypatch):
    _seed_client()
    _clear_push_log()
    db.set_manager(9112, "Сеть", [], [EMP])
    db.set_can_dm(9112, True)
    bot = FakeBot(fail={9112: ConnectionError("net")})
    _push(bot=bot)
    assert db.get_manager(9112)["can_dm"] == 1     # сбой сети ≠ нет лички
    db.delete_manager(9112)


def test_quiet_day_latches_immediately(monkeypatch):
    _seed_client()
    _clear_push_log()
    monkeypatch.setattr(botapp, "ADMIN_ID", 0)
    for m in db.list_managers():
        db.delete_manager(m["telegram_id"])
    assert _daily(FakeBot()) is True
    assert len(_rows(kind=db.PUSH_DIGEST, status=db.PUSH_SKIP_NO_TARGETS)) == 1


def test_owner_gets_digest_without_a_managers_row(monkeypatch):
    _seed_client()
    _clear_push_log()
    monkeypatch.setattr(botapp, "ADMIN_ID", 424242)
    bot = FakeBot()
    assert _daily(bot) is True
    assert any(c[1] == 424242 for c in bot.calls)
    assert db.get_manager(424242) is None          # строка в БД не появилась


def test_owner_switches_are_personal_and_separate(monkeypatch):
    """Личные рубильники владельца (26.08.2026: «дайджест только, отдельно»).

    Общий digest_enabled выключает рассылку ВСЕМ руководителям сразу, поэтому
    «мне обещания не нужны» раньше стоило бы всей команде её утренней сводки.
    Ключи владельца работают и когда он заведён руководителем руками: иначе
    право отписаться зависело бы от наличия строки в managers.
    """
    _seed_client()
    monkeypatch.setattr(botapp, "ADMIN_ID", 424242)
    for key in ("owner_digest", "owner_promise"):
        db.set_setting(key, "")
    ids = lambda kind: [m["telegram_id"] for m in botapp._digest_targets(kind)]

    assert 424242 in ids(db.PUSH_DIGEST)        # дайджест — по умолчанию да
    assert 424242 not in ids(db.PUSH_PROMISE)   # обещания — по умолчанию нет
    db.set_setting("owner_digest", "0")
    db.set_setting("owner_promise", "1")
    assert 424242 not in ids(db.PUSH_DIGEST)
    assert 424242 in ids(db.PUSH_PROMISE)
    # рубильник владельца не трогает остальных
    db.set_manager(707070, "Сосед Руководитель", [], [EMP])
    try:
        assert 707070 in ids(db.PUSH_DIGEST)
    finally:
        db.delete_manager(707070)
    db.set_setting("owner_digest", "1")
    db.set_setting("owner_promise", "0")


def test_owner_switch_works_for_a_manager_row_too(monkeypatch):
    _seed_client()
    monkeypatch.setattr(botapp, "ADMIN_ID", 515151)
    db.set_manager(515151, "Владелец Строкой", [], [EMP])
    db.set_setting("owner_digest", "0")
    try:
        assert 515151 not in [m["telegram_id"]
                              for m in botapp._digest_targets(db.PUSH_DIGEST)]
    finally:
        db.set_setting("owner_digest", "1")
        db.delete_manager(515151)


# --- оракул альбома и file_id ---------------------------------------------------
def test_album_failure_is_not_reported_as_sent():
    from aiogram.exceptions import TelegramAPIError

    class HalfBot(FakeBot):
        async def send_media_group(self, chat_id, media):
            try:
                raise TelegramAPIError(method=SimpleNamespace(), message="bad media")
            except TypeError:
                raise RuntimeError("bad media")

        async def send_photo(self, chat_id, photo, caption=None):
            raise TelegramAPIError(method=SimpleNamespace(), message="bad photo")

        async def send_document(self, chat_id, doc, caption=None):
            raise TelegramAPIError(method=SimpleNamespace(), message="bad doc")

    bot = HalfBot()
    res = asyncio.run(notify.send_album(bot, 5, ["a.jpg", "b.jpg"], "текст визита"))
    # альбом не ушёл — но текст визита дошёл отдельным сообщением
    assert res["ok"] is True and res["media_ids"] == []
    assert any(c[0] == "msg" and c[2] == "текст визита" for c in bot.calls)


def test_second_manager_reuses_file_id():
    _seed_client()
    d = photostore.PHOTO_DIR / CID
    d.mkdir(parents=True, exist_ok=True)
    (d / "1.jpg").write_bytes(b"x")
    (d / "2.jpg").write_bytes(b"x")
    db.set_manager(9113, "Первый", [], [EMP])
    db.set_manager(9114, "Второй", [], [EMP])
    bot = _push()
    albums = [c for c in bot.calls if c[0] == "album"]
    assert len(albums) == 2
    second = albums[1][2]
    # второй адресат получил file_id, а не повторную загрузку файлов
    assert all(isinstance(m.media, str) and m.media.startswith("fid")
               for m in second)
    for f in d.iterdir():
        f.unlink()
    db.delete_manager(9113)
    db.delete_manager(9114)


# --- push_test и сводка ----------------------------------------------------------
def test_push_test_does_not_mute_the_evening_digest(monkeypatch):
    _seed_client()
    _clear_push_log()
    monkeypatch.setattr(botapp, "ADMIN_ID", 0)
    db.set_manager(9115, "Проверяемый", [], [EMP])
    db.log_push(db.PUSH_TEST, db.PUSH_SENT, target_kind="manager",
                target_id=9115, note="push_test:digest")
    bot = FakeBot()
    assert _daily(bot) is True
    assert any(c[1] == 9115 for c in bot.calls)   # вечерняя рассылка не заглушена
    db.delete_manager(9115)


def test_blocked_list_names_the_recipient():
    _seed_client()
    _clear_push_log()
    db.set_manager(9116, "Именованный", [], [EMP])
    db.log_push(db.PUSH_VISIT, db.PUSH_BLOCKED, target_kind="manager",
                target_id=9116, employee=EMP)
    s = db.push_summary(TODAY)
    b = next(x for x in s["blocked"] if x["target_id"] == 9116)
    assert b["target_name"] == "Именованный"       # имя ПОЛУЧАТЕЛЯ, не выездника
    assert b["employee"] == EMP                    # выездник — отдельным полем
    db.delete_manager(9116)


# --- статические инварианты ------------------------------------------------------
WEB_SRC = Path(web.__file__).read_text(encoding="utf-8")
APP_SRC = Path(botapp.__file__).read_text(encoding="utf-8")


def test_no_escaper_lambda_in_web():
    assert "lambda v: _html.escape" not in WEB_SRC
    assert "def _esc(" in WEB_SRC


def test_push_statuses_are_constants_only():
    for src in (WEB_SRC, APP_SRC):
        for m in re.finditer(r"db\.log_push\(\s*[\w.]+,\s*([^,)\s]+)", src):
            arg = m.group(1)
            assert arg.startswith("db.PUSH_") or arg == "status", m.group(0)


def test_push_log_is_in_the_reset_matrix():
    import limited_test_reset as reset
    assert "push_log" in reset.RESET_TABLES
    assert "push_log" in reset.DELETE_ORDER
    assert "push_log" not in reset.RETAINED_TABLES


def test_dedup_payload_has_no_promise_sum():
    """Сумма обещания в ТЕКСТЕ пуша не должна вернуть дубли отбивок: дедуп
    сравнивает фиксированный шаблон из трёх значений, и ни сумма, ни дата
    обещания в него не входят."""
    m = re.search(r"payload = \((.*?)\)", WEB_SRC, re.S)
    assert m and "forecast_sum" not in m.group(1)
    assert "promise_date" not in m.group(1)
