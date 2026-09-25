"""Вход по короткой ссылке: открыл — и внутри.

21.09.2026. До этой работы вход был двухступенчатым даже на вид: человек получал
адрес с «решёткой» (`…/#link=<код>`), страница сама дёргала POST /api/redeem, и
обменов у ссылки было десять — то есть пересланная ссылка неделю пускала кого
угодно. Теперь ссылка короткая и путевая (`…/i/<код>`), обменивает её САМ сервер
редиректом на `/#t=<токен>`, а обменов у неё два: браузер и отдельное хранилище
ярлыка «На экран Домой» на iOS.

Здесь проверяется то, на чём завтра держится живой тест на выездниках:
* по ссылке действительно пускает, и токен принадлежит тому, кому выдали;
* двойки хватает, а айфону сервер сам добавляет третий обмен по факту открытия;
* повторное открытие своей же ссылки обмен НЕ тратит (иначе двойка сгорала бы
  на перезагрузке страницы и на второй попытке вставить ссылку руками);
* исчерпанная ссылка — не тупик, а `/#link_dead=1`;
* робот предпросмотра мессенджера не съедает обмен раньше человека;
* старый вид ссылки `#link=` продолжает работать — на проде он в ходу.
"""

import asyncio
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from aiohttp.test_utils import TestClient, TestServer

import db
import web

INDEX = (Path(__file__).resolve().parents[1] / "server" / "miniapp"
         / "index.html").read_text("utf-8")

KEY = {"X-Admin-Key": "master"}
URL = "https://hard.example.org"

IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 13; SM-A536E) AppleWebKit/537.36 (KHTML, like "
           "Gecko) Chrome/119.0.0.0 Mobile Safari/537.36")
DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
           "Gecko) Chrome/120.0.0.0 Safari/537.36")


@pytest.fixture()
def fresh(monkeypatch):
    """Чистые ссылки и токены на каждый тест плюс фиктивный публичный адрес."""
    monkeypatch.setenv("ADMIN_API_KEY", "master")
    monkeypatch.setenv("MINIAPP_URL", URL)
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM app_links")
        conn.execute("DELETE FROM app_tokens")
        conn.execute("DELETE FROM agent_bind")
        conn.execute("DELETE FROM users WHERE telegram_id >= ?", (db.LOCAL_UID_BASE,))
        conn.commit()
    web._ADMIN_FAILS.clear()


class Serv:
    """Сервер на время одного сценария: короткие обёртки вокруг нужных ручек."""

    def __init__(self, client):
        self.c = client

    async def issue(self, employee, **extra):
        body = {"employee": employee}
        body.update(extra)
        r = await self.c.post("/admin/app_link.json", headers=KEY, json=body)
        return r.status, await r.json()

    async def enter(self, code, ua=IPHONE, same_device=False, **kw):
        """GET /i/<код> без перехода по редиректу: нам нужен сам Location.

        По умолчанию каждый вызов — НОВОЕ устройство: подсказка «этот браузер уже
        входил по ссылке» (кука hard_open) стирается. Иначе обменов не сосчитать —
        один и тот же клиент тыкал бы ссылку сколько угодно и бесплатно, а это
        ровно то поведение, ради которого кука и заведена (см. соседний тест).
        """
        if not same_device:
            self.c.session.cookie_jar.clear()
        r = await self.c.get(f"/i/{code}", headers={"User-Agent": ua},
                             allow_redirects=False, **kw)
        return r.status, r.headers.get("Location", ""), r

    async def links(self):
        r = await self.c.get("/admin/app_links.json", headers=KEY)
        return await r.json()


def run(scenario):
    async def main():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            return await scenario(Serv(client))
        finally:
            await client.close()

    return asyncio.run(main())


def _token_of(location):
    """Токен из «/#t=<токен>&fresh=1»: метку «выдан только что» отбрасываем."""
    assert "#t=" in location, location
    return parse_qs(location.split("#", 1)[1])["t"][0]


# --- сам вход ---------------------------------------------------------------
def test_short_link_lets_the_person_in_with_one_tap(fresh):
    """Открыл ссылку — уже внутри: сервер сам обменял код и увёл на «/#t=<токен>».
    Токен во фрагменте: на сервер он больше не уедет и в логах не осядет."""

    async def scenario(s):
        _st, body = await s.issue("Иванов И.")
        status, location, resp = await s.enter(body["code"], ua=IPHONE)
        return body, status, location, resp.headers.get("Cache-Control", "")

    body, status, location, cache = run(scenario)
    assert body["url"] == f"{URL}/i/{body['code']}"
    assert status == 302 and location.startswith("/#t=")
    assert "no-store" in cache, "редирект с токеном нельзя отдавать из кэша"
    assert db.uid_for_app_token(_token_of(location)) == body["telegram_id"]


def test_the_issued_token_is_marked_as_freshly_issued(fresh):
    """К токену в редиректе сервер добавляет метку «fresh=1» — «выдан прямо сейчас
    тому, кто открыл ссылку».

    Без метки оболочка на устройстве, где кто-то уже входил, оставляла бы ПРЕЖНИЙ
    токен: обмен новой ссылки сервер потратил, «вошёл» в пульте загорелось, а
    человек смотрел бы чужой портфель. Метка едет во фрагменте — на сервер она не
    вернётся и в логи не попадёт.
    """

    async def scenario(s):
        _st, body = await s.issue("Свежов С.")
        _st2, loc, _r = await s.enter(body["code"], ua=IPHONE)
        return body, loc

    body, loc = run(scenario)
    assert loc == f"/#t={_token_of(loc)}&fresh=1", loc
    assert db.uid_for_app_token(_token_of(loc)) == body["telegram_id"]


def test_second_tap_from_the_same_browser_does_not_burn_a_use(fresh):
    """Тап по той же ссылке с того же телефона обмен НЕ тратит.

    В мессенджере по ссылке тыкают дважды и трижды — это норма, а не ошибка. Пока
    у /i/ не было способа узнать устройство, двухразовая ссылка сгорала целиком
    во встроенном браузере, и до Safari с ярлыком человек доходил уже с мёртвой
    ссылкой. Теперь выданный токен возвращается подсказкой (кука «/i/», 15 минут),
    и сервер отдаёт ТОТ ЖЕ токен, не трогая счётчик."""

    async def scenario(s):
        _st, body = await s.issue("Иванов И.")
        first = await s.enter(body["code"], ua=IPHONE)
        second = await s.enter(body["code"], ua=IPHONE, same_device=True)
        third = await s.enter(body["code"], ua=IPHONE, same_device=True)
        return body, first, second, third, await s.links()

    body, first, second, third, state = run(scenario)
    assert first[1].startswith("/#t=") and second[1].startswith("/#t=")
    assert _token_of(first[1]) == _token_of(second[1]) == _token_of(third[1]), (
        "тот же браузер обязан получить тот же токен, а не по новому на каждый тап")
    row = next(r for r in state["links"] if r["code"] == body["code"])
    assert row["uses"] == 1, f"три тапа с одного телефона — один обмен, а не три: {row}"
    assert row["max_uses"] >= 3, "айфону всё равно полагается запасной обмен"


def test_two_uses_by_default_and_then_the_link_is_dead_but_not_a_dead_end(fresh):
    """Двухразовая ссылка: андроиду бонуса не даём, третье открытие уводит на
    «/#link_dead=1» — оболочка там сперва попробует свой токен."""

    async def scenario(s):
        _st, body = await s.issue("Петров П.")
        out = [await s.enter(body["code"], ua=ANDROID) for _ in range(3)]
        return [(st, loc) for st, loc, _r in out]

    (s1, l1), (s2, l2), (s3, l3) = run(scenario)
    assert s1 == 302 and l1.startswith("/#t=")
    assert s2 == 302 and l2.startswith("/#t=")
    assert _token_of(l1) != _token_of(l2), "каждый обмен — свой девайс-токен"
    assert s3 == 302 and l3 == "/#link_dead=1"


def test_iphone_gets_one_extra_use_by_fact_not_by_guess(fresh):
    """Айфон различаем ПО ФАКТУ первого открытия: +1 обмен на установку ярлыка,
    у которого своё хранилище. Заранее угадать телефон аналитик не может."""

    async def scenario(s):
        _st, body = await s.issue("Сидоров С.")
        first = await s.enter(body["code"], ua=IPHONE)
        state = (await s.links())["links"][0]
        rest = [await s.enter(body["code"], ua=IPHONE) for _ in range(3)]
        return first, state, [(st, loc) for st, loc, _r in rest]

    (st1, loc1, _r), state, rest = run(scenario)
    assert st1 == 302 and loc1.startswith("/#t=")
    assert state["max_uses"] == db.APP_LINK_DEFAULT_USES + 1
    assert state["bonus_ios"] is True and state["last_agent"] == "iphone"
    assert [st for st, _l in rest] == [302, 302, 302]
    assert rest[0][1].startswith("/#t=") and rest[1][1].startswith("/#t=")
    assert rest[2][1] == "/#link_dead=1", "бонус ровно один, а не бесконечный"


def test_installed_shortcut_gets_no_bonus(fresh):
    """Ярлык на экране сообщает о себе сам (standalone) — ему второй обмен не нужен,
    и дарить его не за что: иначе ссылка тихо жила бы дольше положенного."""

    async def scenario(s):
        _st, body = await s.issue("Ярлыков Я.")
        r = await s.c.post("/api/redeem", headers={"User-Agent": IPHONE},
                           json={"code": body["code"], "standalone": True})
        return await r.json(), (await s.links())["links"][0]

    redeemed, state = run(scenario)
    assert redeemed["ok"] and redeemed["token"]
    assert state["max_uses"] == db.APP_LINK_DEFAULT_USES and state["bonus_ios"] is False
    assert state["last_agent"] == "iphone-pwa"


def test_reopening_own_link_with_a_live_token_costs_nothing(fresh):
    """Тот же телефон переоткрыл ту же ссылку (перезагрузка страницы, повторная
    вставка, ссылка прилетела в живую вкладку) — сервер возвращает ТОТ ЖЕ токен и
    обмен не тратит. Без этого двойка сгорала бы, не доведя человека до ярлыка."""

    async def scenario(s):
        _st, body = await s.issue("Повторов П.")
        _s1, loc, _r = await s.enter(body["code"], ua=ANDROID)
        token = _token_of(loc)
        again = []
        for _ in range(3):
            r = await s.c.post("/api/redeem", headers={"User-Agent": ANDROID},
                               json={"code": body["code"], "token": token})
            again.append((r.status, (await r.json())["token"]))
        return token, again, (await s.links())["links"][0]

    token, again, state = run(scenario)
    assert all(st == 200 and tok == token for st, tok in again)
    assert state["uses"] == 1, "обмен потрачен ровно один — первый"
    assert state["devices"] == 1 and state["entered"] is True


def test_someone_elses_token_does_not_save_an_exhausted_link(fresh):
    """«Свой токен не тратит обмен» — только для СВОЕГО: чужим токеном чужую
    ссылку не открыть, иначе один вошедший держал бы живыми все ссылки подряд."""

    async def scenario(s):
        _st, mine = await s.issue("Чужаков Ч.")
        _st, other = await s.issue("Хозяев Х.")
        _s1, loc, _r = await s.enter(other["code"], ua=ANDROID)
        alien = _token_of(loc)
        await s.enter(mine["code"], ua=ANDROID)
        await s.enter(mine["code"], ua=ANDROID)          # исчерпали двойку
        r = await s.c.post("/api/redeem", headers={"User-Agent": ANDROID},
                           json={"code": mine["code"], "token": alien})
        return r.status

    assert run(scenario) == 410


# --- совместимость ----------------------------------------------------------
def test_old_hash_link_still_works(fresh):
    """Старый вид `…/#link=<код>` остаётся рабочим: на проде такие ссылки уже
    выданы, и ломать их выкладкой нельзя."""

    async def scenario(s):
        _st, body = await s.issue("Староверов С.")
        r = await s.c.post("/api/redeem", json={"code": body["code"]})
        return r.status, await r.json()

    status, body = run(scenario)
    assert status == 200 and body["token"] and body["days"] == db.APP_TOKEN_DAYS


def test_admin_link_answer_keeps_the_fields_the_pult_already_reads(fresh):
    """Контракт ответа: старые ключи на месте (их читает пульт), новые добавлены."""

    async def scenario(s):
        return await s.issue("Контрактов К.", kind="courier", uses=5)

    status, body = run(scenario)
    assert status == 200
    for key in ("ok", "telegram_id", "employee", "url", "days", "local_identity",
                "created", "code", "uses", "max_uses", "expires_at"):
        assert key in body, key
    assert body["max_uses"] == 5 and body["uses"] == 0
    assert body["days"] == db.APP_LINK_DAYS


def test_uses_is_chosen_at_issue_and_validated(fresh):
    """Число обменов выбирают при выдаче: 1 (показать QR с экрана), 2 (обычно),
    5 (перешлю в мессенджер). Потолок нужен, чтобы «поставлю 1000» не завёл
    бессрочный пропуск."""

    async def scenario(s):
        one = await s.issue("Одиночкин О.", uses=1)
        bad_hi = await s.issue("Наглов Н.", uses=db.APP_LINK_MAX_USES + 1)
        bad_lo = await s.issue("Нулёв Н.", uses=0)
        bad_txt = await s.issue("Буквин Б.", uses="много")
        code = one[1]["code"]
        first = await s.enter(code, ua=ANDROID)
        second = await s.enter(code, ua=ANDROID)
        return one, bad_hi, bad_lo, bad_txt, first[:2], second[:2]

    one, bad_hi, bad_lo, bad_txt, first, second = run(scenario)
    assert one[0] == 200 and one[1]["max_uses"] == 1
    assert bad_hi[0] == 400 and bad_lo[0] == 400 and bad_txt[0] == 400
    assert first == (302, first[1]) and first[1].startswith("/#t=")
    assert second[1] == "/#link_dead=1"


# --- «выдача API» для пульта -------------------------------------------------
def test_app_links_json_is_the_screen_behind_the_pult(fresh):
    """Одна строка на человека: последняя выданная ссылка + все его живые
    устройства. Перевыпуск ссылки не должен превращать вошедшего в «ждёт»."""

    async def scenario(s):
        _st, body = await s.issue("Иванов И.")
        await s.enter(body["code"], ua=ANDROID)
        _st, again = await s.issue("Иванов И.")            # перевыпуск
        return body, again, await s.links()

    body, again, state = run(scenario)
    rows = state["links"]
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"employee", "telegram_id", "kind", "code", "created_at",
                        "expires_at", "uses", "max_uses", "bonus_ios", "last_use_at",
                        "last_agent", "entered", "devices", "alive", "url"}
    # адрес собирает сервер: пульт знает служебный адрес, а телефону нужен публичный
    assert row["url"] == f"{URL}/i/{row['code']}"
    assert row["employee"] == "Иванов И." and row["kind"] == "agent"
    assert row["code"] == again["code"], "показываем последнюю выданную ссылку"
    assert row["uses"] == 0 and row["alive"] is True
    assert row["entered"] is True and row["devices"] == 1
    assert row["telegram_id"] == body["telegram_id"]


def test_app_links_json_needs_admin(fresh):
    async def scenario(s):
        r = await s.c.get("/admin/app_links.json")
        return r.status

    assert run(scenario) == 401


def test_revoke_can_kill_only_the_link_and_leave_the_phone_working(fresh):
    """«Выдал не тому» и «сотрудник ушёл» — разные действия. scope=links гасит
    только ссылку, scope=all (по умолчанию) — ещё и телефон."""

    async def scenario(s):
        _st, body = await s.issue("Отзывов О.")
        _s1, loc, _r = await s.enter(body["code"], ua=ANDROID)
        token = _token_of(loc)
        tid = body["telegram_id"]
        r = await s.c.post("/admin/app_revoke", headers=KEY,
                           json={"telegram_id": tid, "scope": "links"})
        only_links = await r.json()
        alive_after_links = db.uid_for_app_token(token) == tid
        r = await s.c.post("/admin/app_revoke", headers=KEY, json={"telegram_id": tid})
        everything = await r.json()
        return only_links, alive_after_links, everything, db.uid_for_app_token(token)

    only_links, alive, everything, after_all = run(scenario)
    assert only_links["revoked"] == 0 and only_links["scope"] == "links"
    assert alive is True, "телефон, который уже работает, отзыв ссылки не трогает"
    assert everything["revoked"] == 1 and everything["scope"] == "all"
    assert after_all is None


# --- защита двери ------------------------------------------------------------
def test_link_preview_robot_does_not_eat_the_use(fresh):
    """Раньше обмен шёл только POST-ом, и предпросмотр ссылки в мессенджере её не
    трогал. У GET /i/ такая возможность появляется: робот открывает ссылку раньше
    человека. Роботу — заглушка, счётчик обменов не трогаем."""

    async def scenario(s):
        _st, body = await s.issue("Превьюшкин П.")
        out = []
        for ua in ("TelegramBot (like TwitterBot)", "WhatsApp/2.23.20.0 A",
                   "facebookexternalhit/1.1", "curl/8.4.0", ""):
            st, loc, resp = await s.enter(body["code"], ua=ua)
            out.append((st, loc, (await resp.text())[:9]))
        return out, (await s.links())["links"][0]

    out, state = run(scenario)
    assert all(st == 200 and not loc for st, loc, _t in out), out
    assert all(text == "<!doctype" for _st, _l, text in out)
    assert state["uses"] == 0, "робот не потратил ни одного обмена"


def test_a_real_browser_mistaken_for_a_robot_still_has_a_way_in(fresh):
    """Заглушка не должна стать тупиком: на ней есть кнопка «Войти» (?go=1),
    которая обменивает код, даже если браузер опознали как робота."""

    async def scenario(s):
        _st, body = await s.issue("Гошин Г.")
        st, loc, _r = await s.enter(body["code"], ua="curl/8.4.0",
                                    params={"go": "1"})
        return st, loc

    status, location = run(scenario)
    assert status == 302 and location.startswith("/#t=")


def test_guessing_codes_hits_the_same_counter_as_admin_handles(fresh):
    """`/i/` — такая же дверь без пароля, как админ-ручки, и перебор по ней
    считается тем же счётчиком неудач. Удачный вход прощает промахи ПО ССЫЛКАМ:
    человек, потыкавший в старые QR, не должен запереть сам себя."""

    async def scenario(s):
        _st, body = await s.issue("Переборов П.")
        codes = []
        for i in range(web._ADMIN_FAIL_LIMIT - 1):        # 19 промахов
            st, _loc, _r = await s.enter(f"nosuchcode{i:02d}", ua=DESKTOP)
            codes.append(st)
        st_ok, loc_ok, _r = await s.enter(body["code"], ua=DESKTOP)
        left = sum(len(v) for v in web._ADMIN_FAILS.values())
        for i in range(web._ADMIN_FAIL_LIMIT):            # добираем до потолка
            await s.enter(f"nosuchmore{i:02d}", ua=DESKTOP)
        st_blocked, _loc, _r = await s.enter(body["code"], ua=DESKTOP)
        return codes, st_ok, loc_ok, left, st_blocked

    codes, ok, loc_ok, left, blocked = run(scenario)
    assert set(codes) == {302}, "мёртвая ссылка уводит на экран, а не на ошибку"
    assert ok == 302 and loc_ok.startswith("/#t=")
    assert left == 0, "свой вход снимает свои же промахи по ссылкам"
    assert blocked == 429


def test_entering_by_link_does_not_forgive_master_key_guessing(fresh):
    """Вход по ссылке НЕ обнуляет счётчик админских дверей.

    Обмен своей живой ссылки достаётся даром (со своим живым токеном он даже не
    тратит открытие), и если бы он чистил общий счётчик, любой выездник со своей
    ссылкой снимал бы единственную защиту от перебора мастер-ключа и кодов
    привязки пульта: «19 попыток ключа + одно открытие своей ссылки» — и заново.
    """

    async def scenario(s):
        _st, body = await s.issue("Ключёв К.")
        _s1, loc, _r = await s.enter(body["code"], ua=ANDROID)
        token = _token_of(loc)
        bad = []
        for i in range(web._ADMIN_FAIL_LIMIT - 1):        # 19 попыток чужим ключом
            r = await s.c.post("/admin/device/enroll.json",
                               headers={"X-Admin-Key": "не тот"},
                               json={"device_id": f"d{i}", "name": "чужой"})
            bad.append(r.status)
        r = await s.c.post("/api/redeem", headers={"User-Agent": ANDROID},
                           json={"code": body["code"], "token": token})
        free = r.status
        left = sum(len(v) for v in web._ADMIN_FAILS.values())
        more = []
        for i in range(2):                                # 20-я и 21-я попытки
            rr = await s.c.post("/admin/device/enroll.json",
                                headers={"X-Admin-Key": "не тот"},
                                json={"device_id": f"x{i}", "name": "чужой"})
            more.append(rr.status)
        # Операторская дверь /admin/* на том же счётчике, но верный ключ она не
        # запирает (за одним адресом офиса — все пульты); состояние ссылки
        # читаем из базы, чтобы не зависеть от этой двери.
        still = await s.c.get("/admin/app_links.json", headers=KEY)
        return bad, free, left, more, still.status, db.app_links_state()[0]

    bad, free, left, more, still, state = run(scenario)
    assert set(bad) == {401}
    assert free == 200, "своей ссылкой человек по-прежнему входит"
    assert state["uses"] == 1, "бесплатный обмен ничего не потратил — потому и опасен"
    assert left == web._ADMIN_FAIL_LIMIT - 1, "неудачи по ключу остались на счету"
    assert more == [401, 429], "потолок достигнут, дверь закрылась"
    assert still == 200, "верный ключ операторскую дверь по-прежнему открывает"


# --- схема базы --------------------------------------------------------------
def test_columns_migrate_on_a_working_database_with_rows(monkeypatch, tmp_path):
    """Рабочая база прода переживает эту выкладку без ручных действий: колонки
    доезжают тем же шаблоном, что и `uses`, а DEFAULT доливает двойку в СТРОКИ,
    которые уже лежат. Проверяем на базе старой схемы с данными."""
    old = tmp_path / "hardbot.db"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE app_links (code TEXT PRIMARY KEY, telegram_id INTEGER,"
                 " created_at TEXT, used_at TEXT, uses INTEGER NOT NULL DEFAULT 0)")
    conn.execute("CREATE TABLE app_tokens (token TEXT PRIMARY KEY, telegram_id INTEGER,"
                 " created_at TEXT, expires_at TEXT)")
    conn.execute("INSERT INTO app_links VALUES ('старая', 77, '2026-09-20T10:00:00',"
                 " NULL, 0)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", old)
    db.init_db()                                   # ← ровно то, что делает выкладка

    with db.connect() as c:
        row = dict(c.execute("SELECT * FROM app_links").fetchone())
        tok_cols = {r[1] for r in c.execute("PRAGMA table_info(app_tokens)")}
    assert row["max_uses"] == db.APP_LINK_DEFAULT_USES
    assert row["bonus_ios"] == 0 and row["last_use_at"] is None
    assert "agent" in tok_cols
    # и ссылка, выданная ДО выкладки, продолжает пускать ровно два раза
    assert db.redeem_app_link("старая", agent="android")
    assert db.redeem_app_link("старая", agent="android")
    assert db.redeem_app_link("старая", agent="android") is None


def test_empty_database_gets_the_columns_from_the_start(monkeypatch, tmp_path):
    """На ПУСТОЙ базе (машина компании) те же колонки заводятся сразу схемой."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()
    with db.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(app_links)")}
    assert {"max_uses", "bonus_ios", "last_use_at", "last_agent"} <= cols


def test_device_class_is_short_and_ios_wins_over_inapp():
    """Класс устройства — короткое слово для аудита и пульта. iOS определяем
    ПЕРВЫМ: айфону во встроенном браузере мессенджера бонус нужен больше всех."""
    inapp_iphone = IPHONE + " [FBAN/FBIOS]"
    assert web._agent_class(IPHONE) == "iphone"
    assert web._agent_class(inapp_iphone) == "iphone"
    assert web._agent_class(ANDROID) == "android"
    assert web._agent_class(ANDROID.replace("SM-A536E)", "SM-A536E; wv)")) == "inapp"
    assert web._agent_class(DESKTOP) == "desktop"
    assert web._agent_class("") == "other"
    assert web._agent_class(IPHONE, standalone=True) == "iphone-pwa"


# --- оболочка ----------------------------------------------------------------
def test_shell_sends_its_own_token_and_context_when_redeeming():
    """Оболочка при обмене шлёт свой текущий токен и признак ярлыка: без них
    сервер не отличит «переоткрыл своё» от «новый телефон» и потратит обмен."""
    assert "body:JSON.stringify({code, token:appToken(), standalone:isStandalone()})" in INDEX
    assert "navigator.standalone===true" in INDEX
    assert "'(display-mode: standalone)'" in INDEX


def test_shell_understands_a_dead_link_and_tries_its_own_token_first():
    """«/#link_dead=1» — не экран отказа, а повод войти своим токеном молча."""
    assert "function linkDeadFromUrl()" in INDEX
    assert "get('link_dead')==='1'" in INDEX
    assert "if(appToken()){ stampTokenUrl(); return 'have-token'; }" in INDEX


def test_shell_manual_paste_takes_the_short_link_and_a_personal_one():
    """Ручная вставка — главный путь на iPhone (внутри ярлыка ссылку не открыть).
    Понимать она обязана и короткую ссылку, и персональную «…#t=<токен>»."""
    assert r"raw.match(/\/i\/([A-Za-z0-9_-]{8,})/)" in INDEX
    assert "raw.match(/[#&]t=([A-Za-z0-9_-]{16,})/)" in INDEX
    assert 'id="codein"' in INDEX, "поле ручной вставки убирать нельзя"


def test_shell_refusal_screens_do_not_send_anyone_to_the_bot():
    """В контуре компании бота нет: «напишите боту слово ссылка» — тупик.

    Трёх известных строк мало: проверка на них проходила вхолостую мимо
    захардкоженного «Откройте через бота при интернете хотя бы раз» на экране
    «связи нет и снимка нет». Поэтому смотрим ВСЕ куски разметки, которые
    оболочка вставляет в страницу: ни один не должен поминать бота.
    """
    for dead_end in ("Напишите боту", "Botga «ссылка»", "Ба бот калимаи",
                     "Откройте через бота"):
        assert dead_end not in INDEX, dead_end
    assert "Попросите у аналитика" in INDEX
    # Ищем корень «бот»/«bot» с начала слова: «работа» и «margin-bottom» ложно
    # не срабатывают. Разметку из кусков выбрасываем — смотрим то, что ЧИТАЕТ
    # человек, а не имена классов и стилей.
    bot = re.compile(r"(?<![а-яёa-z-])(бот|bot)", re.IGNORECASE)
    seen = [re.sub(r"<[^>]*>", " ", chunk)
            for chunk in re.findall(r"innerHTML\s*=\s*`([^`]*)`", INDEX)]
    seen.append(INDEX[INDEX.index("const I18N = {"):INDEX.index("let STATE = {")])
    for text in seen:
        found = bot.search(text)
        assert not found, text[max(0, found.start() - 80):found.start() + 80]


def test_the_no_network_screen_speaks_all_three_languages():
    """Экран «связи нет, а портфель ещё не загружен» — тоже отказ, и он был
    захардкожен по-русски (узбек с таджиком читали его как сломанное приложение).
    Теперь текст берётся из словаря языков и говорит то, что человек может
    сделать сам: дойти до связи и открыть приложение ещё раз."""
    assert INDEX.count("bootnone:") == 3 and INDEX.count("bootnonewhy:") == 3
    assert "esc(t.bootnone)" in INDEX and "esc(t.bootnonewhy)" in INDEX


def test_shell_prefers_a_freshly_issued_token_over_the_one_on_the_device():
    """Короткая ссылка, открытая на устройстве, где входа нет (или он мёртв),
    впускает НОВОГО человека сразу: сервер потратил на него обмен и пометил токен
    «fresh=1». Но при ЖИВОМ входе адрес «#t=…&fresh=1» мог собрать и прислать
    кто угодно (ревью 25.09.2026, #4): новый токен не перезаписывает вход, а ждёт
    ответа человека (pending_token) — поведение целиком проверяет
    test_shell_fixation под node. Токен из start_url ярлыка (метки нет) прежний
    не подменяет — там он бывает СТАРШЕ того, что на устройстве."""
    body = INDEX[INDEX.index("function absorbTokenFromUrl("):]
    body = body[:body.index("function personalizeManifest(")]
    assert "get('fresh')==='1'" in body
    fresh_at = body.index("get('fresh')==='1'")
    keep_at = body.index("if(!appToken())")
    assert fresh_at < keep_at, "свежий токен разбирается ДО проверки «а есть ли свой»"
    fresh_branch = body[fresh_at:keep_at]
    assert "localStorage.setItem('app_token',tk)" in fresh_branch, "входа нет — новый и есть вход"
    assert "setPendingToken(tk)" in fresh_branch, "живой вход — новый ждёт согласия"
    # Манифест собирается в <head> ДО всего остального: при живом входе он
    # строится с ним, а не с токеном из адреса — иначе ярлык «На экран Домой»
    # унёс бы в start_url чужой вход.
    head = INDEX[:INDEX.index("<meta name=\"apple-mobile-web-app-capable\"")]
    assert "if(!t) t=u;" in head, "токен из адреса — только когда своего входа нет"
    assert ".test(h)) t=u" not in head, "метка fresh больше не перебивает живой вход"


def test_opening_a_link_approves_a_pending_binding(fresh):
    """При переезде базы привязки приезжают заявками (approved=0), а владельца для
    аппрува в контуре компании нет. Ссылку выдал тот, у кого есть право, — её
    открытие и есть подтверждение: человек попадает в портфель, а не в вечное
    «ожидайте подтверждения»."""
    tid = 700500001
    with db.connect() as conn:
        conn.execute("INSERT INTO agent_bind (telegram_id, employee, kind, approved, "
                     "created_at) VALUES (?,?,?,?,?)",
                     (tid, "Заявкин З.", "agent", 0, "2026-09-23"))
        conn.commit()
    assert db.get_binding(tid) is None
    code = db.create_app_link(tid)

    async def scenario(s):
        status, location, _ = await s.enter(code, ua=ANDROID)
        return status, location

    status, location = run(scenario)
    assert status == 302 and db.uid_for_app_token(_token_of(location)) == tid
    assert db.get_binding(tid) == "Заявкин З.", "заявка осталась заявкой — человек заперт"


def test_dead_link_approves_nothing(fresh):
    tid = 700500002
    with db.connect() as conn:
        conn.execute("INSERT INTO agent_bind (telegram_id, employee, kind, approved, "
                     "created_at) VALUES (?,?,?,?,?)",
                     (tid, "Чужаков Ч.", "agent", 0, "2026-09-23"))
        conn.commit()

    async def scenario(s):
        return await s.enter("nope-not-a-code", ua=ANDROID)

    status, location, _ = run(scenario)
    assert "link_dead" in location
    assert db.get_binding(tid) is None
