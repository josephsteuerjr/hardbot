"""Сводка руководителя: план, итоги по стране, тап по плашке, фото в карточке.

Что чинится по обратной связи из Душанбе и Ташкента:
— фильтр «Тадж / Узб» переключал только таблицу, а плашки сверху продолжали
  показывать весь СНГ; руководитель направления читал чужие цифры;
— офис ставит план «10 выездов на сотрудника», и процент проработки каждый
  день считали на бумаге и присылали в чат руками;
— плашки были голыми числами: «а кто эти 29 обещаний» — перебирай сотрудников;
— фото жили на отдельном экране поиска по КД, и в карточке клиента, которого
  сам же просил сфотографировать, руководитель снимков не находил.
"""
import asyncio
import json
from datetime import date, timedelta

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import digest_data as dd
import photostore
import web

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent

MGR = 559001
EMP_TJ, EMP_UZ = "Дртаджиков Т.", "Друзбеков У."
TODAY = date.today()
T = TODAY.isoformat()
D1 = (TODAY - timedelta(days=1)).isoformat()


def _payload(status, agreed=""):
    return f"Выезд (миниапп). Статус: {status}; Договорённость: {agreed}; "


def _visit(cid, day, status, agreed=""):
    with db.connect() as conn:
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES (?,?,?,?)", (cid, 1, day, _payload(status, agreed)))
        conn.commit()


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'DR-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'DR-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM settings WHERE key='visit_plan_per_day'")
        conn.commit()
    rows = [("DR-1", "Клиент Тэ", EMP_TJ, "Таджикистан", "Душанбе"),
            ("DR-2", "Клиент Тэ Два", EMP_TJ, "Таджикистан", "Душанбе"),
            ("DR-3", "Клиент У", EMP_UZ, "Узбекистан", "Ташкент")]
    with db.connect() as conn:
        for cid, client, emp, country, city in rows:
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country, city) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (cid, client, emp, "1990-01-01", "ЗК", "100 000",
                 "Не обработан", country, city))
        conn.commit()
    _visit("DR-1", T, "Оплачен", "внёс")
    db.record_bot_visit("DR-1", {"call_status": "Оплачен"}, T)
    _visit("DR-2", T, "Обещает оплатить", "до пятницы")
    db.record_bot_visit("DR-2", {"call_status": "Обещает оплатить"}, T)
    _visit("DR-3", T, "Нет контакта")
    db.record_bot_visit("DR-3", {"call_status": "Нет контакта"}, T)
    db.set_manager(MGR, "Рук СНГ", [], [EMP_TJ, EMP_UZ])
    db.reset_role_caches()


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'DR-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'DR-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.execute("DELETE FROM settings WHERE key='visit_plan_per_day'")
        conn.commit()
    db.reset_role_caches()


def _rng(preset="today"):
    return dd.stats_range(preset, 0, today=TODAY)


def _stats(preset="today"):
    _seed()
    return dd.stats_data(db.get_manager(MGR), _rng(preset),
                         employees=[EMP_TJ, EMP_UZ])


# --- план --------------------------------------------------------------------
def test_plan_is_ten_visits_per_person_per_day_by_default():
    t = _stats()["totals"]
    assert t["plan_per_day"] == 10
    assert t["plan"] == 20, "двое сотрудников за один день"


def test_plan_follows_the_office_norm_from_settings():
    _seed()
    db.set_setting("visit_plan_per_day", "8")
    try:
        t = dd.stats_data(db.get_manager(MGR), _rng(),
                          employees=[EMP_TJ, EMP_UZ])["totals"]
        assert (t["plan_per_day"], t["plan"]) == (8, 16)
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM settings WHERE key='visit_plan_per_day'")
            conn.commit()


def test_broken_norm_falls_back_to_ten():
    _seed()
    db.set_setting("visit_plan_per_day", "ерунда")
    try:
        assert db.visit_plan_per_day() == 10
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM settings WHERE key='visit_plan_per_day'")
            conn.commit()


def test_plan_grows_with_the_window():
    """Неделя команды из двух человек — это 140 выездов, а не 20."""
    t = _stats("7d")["totals"]
    assert t["plan"] == 10 * 2 * 7


# --- итоги по стране ---------------------------------------------------------
def test_each_country_carries_its_own_totals():
    data = _stats()
    by_code = {c["code"]: c for c in data["countries"]}
    assert set(by_code) == {"tj", "uz"}
    tj, uz = by_code["tj"]["totals"], by_code["uz"]["totals"]
    assert (tj["visits"], tj["employees"], tj["paid"], tj["plan"]) == (2, 1, 1, 10)
    assert (uz["visits"], uz["employees"], uz["paid"], uz["plan"]) == (1, 1, 0, 10)
    assert data["totals"]["visits"] == tj["visits"] + uz["visits"]


# --- случаи по плашке --------------------------------------------------------
def _cases(metric, country=""):
    _seed()
    return dd.cases_data(db.get_manager(MGR), _rng(), metric, country,
                         employees=[EMP_TJ, EMP_UZ])


def test_visits_metric_lists_every_visit_with_its_employee():
    j = _cases("visits")
    assert j["total"] == 3
    assert {c["rep"] for c in j["cases"]} == {"DR-1", "DR-2", "DR-3"}
    assert {c["employee"] for c in j["cases"]} == {EMP_TJ, EMP_UZ}
    assert all(c["day_label"] for c in j["cases"]), "дата подписана по-человечески"


def test_paid_and_promised_metrics_match_the_tiles():
    paid = _cases("paid")
    assert [c["rep"] for c in paid["cases"]] == ["DR-1"]
    promised = _cases("promised")
    assert [c["rep"] for c in promised["cases"]] == ["DR-2"]


def test_country_filter_narrows_the_list():
    j = _cases("visits", "uz")
    assert [c["rep"] for c in j["cases"]] == ["DR-3"]


def test_unknown_metric_falls_back_to_visits():
    assert _cases("хренметрика")["metric"] == "visits"


def test_photo_metric_keeps_only_visits_with_shots(monkeypatch):
    monkeypatch.setattr(
        dd.photostore, "day_photos",
        lambda contracts, on_date: ["снимок"] if contracts[0]["contract_id"] == "DR-2" else [])
    j = _cases("with_photo")
    assert [c["rep"] for c in j["cases"]] == ["DR-2"]
    assert j["cases"][0]["photos"] == 1


# --- фото в карточке ---------------------------------------------------------
def test_person_card_carries_the_photos_of_that_client(monkeypatch, tmp_path):
    _seed()
    album = tmp_path / "DR-1"
    album.mkdir()
    for name in ("a.jpg", "b.jpg"):
        (album / name).write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(photostore, "person_photos",
                        lambda _cs: sorted(album.iterdir()))
    monkeypatch.setattr(photostore, "photo_key",
                        lambda path, root=None: f"DR-1/{path.name}")
    data = dd.person_data(db.get_manager(MGR), T, EMP_TJ, "DR-1",
                          employees=[EMP_TJ, EMP_UZ])
    files = [s["file"] for s in data["shots"]]
    assert files == ["DR-1/a.jpg", "DR-1/b.jpg"]
    assert all(s["on_visit"] for s in data["shots"]), "сняты сегодня — это выезд"


def test_person_card_without_photos_says_so(monkeypatch):
    _seed()
    monkeypatch.setattr(photostore, "person_photos", lambda _cs: [])
    data = dd.person_data(db.get_manager(MGR), T, EMP_TJ, "DR-1",
                          employees=[EMP_TJ, EMP_UZ])
    assert data["shots"] == []


# --- оболочка: снимки видно сразу --------------------------------------------
SHELL = (ROOT / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")


def test_card_loads_visit_photos_without_a_tap():
    """Кнопка «показать» читалась как «фоток нет»: на экране слово «Фото · 1»
    и больше ничего. Снимки дня выезда грузятся сами."""
    body = SHELL[SHELL.index("function dgPerson("):SHELL.index("async function dgShotsAuto(")]
    assert "dgShotsAuto();" in body, "автозагрузка снимков не вызывается"
    assert 'id="shot${i}"' in body, "нет места под снимок"
    assert "sh.on_visit" in body, "снимки дня выезда не отбираются"


def test_extra_photos_stay_behind_a_tap():
    """Альбом на десяток кадров в поле грузился бы минутами."""
    assert "const DG_SHOTS_AUTO=3;" in SHELL
    body = SHELL[SHELL.index("function dgPerson("):SHELL.index("async function dgShotsAuto(")]
    assert "rest.map" in body and "dgShot(" in body


# --- ручка -------------------------------------------------------------------
def _call(qs, monkeypatch, uid=MGR):
    monkeypatch.setattr(web, "_uid", lambda _r: uid)
    req = make_mocked_request("GET", f"/api/manager/cases{qs}")
    resp = asyncio.run(web._manager_cases(req))
    return json.loads(resp.body.decode())


def test_endpoint_answers_with_the_same_cases(monkeypatch):
    _seed()
    j = _call("?preset=today&metric=paid", monkeypatch)
    assert j["ok"] and j["screen"] == "cases" and j["metric"] == "paid"
    assert [c["rep"] for c in j["cases"]] == ["DR-1"]


def test_endpoint_is_closed_for_strangers(monkeypatch):
    _seed()
    with pytest.raises(web.web.HTTPForbidden):
        _call("?preset=today&metric=visits", monkeypatch, uid=559999)


def test_endpoint_leaves_an_audit_trail(monkeypatch):
    _seed()
    _call("?preset=today&metric=visits", monkeypatch)
    row = db.audit_tail(5)[0]
    assert row["action"] == "digest_view" and "cases visits" in (row["detail"] or "")
