"""Беседы отчётов о выездах + фактическое гео фото: реестр бесед, маршрутизация
по сотрудникам, композер текста отчёта, разбор гео-параметров /api/photo."""

import pytest

import db
import notify
import web as webmod


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()


# --- реестр бесед -------------------------------------------------------------
def test_chat_lifecycle_add_configure_remove_readd():
    db.upsert_report_chat(-100_1, "RBR Tajikistan")
    chats = db.list_report_chats()
    assert len(chats) == 1
    assert chats[0]["added_at"]                       # дата видна в пульте
    assert {k: v for k, v in chats[0].items() if k != "added_at"} == {
        "chat_id": -100_1, "title": "RBR Tajikistan",
        "enabled": False, "removed": False, "employees": []}

    db.set_report_chat(-100_1, enabled=True, employees=["Алиджон Душанбе", "Второй"])
    assert db.report_chats_for("Алиджон Душанбе") == [-100_1]
    assert db.report_chats_for("Чужой") == []
    assert db.report_chats_for("") == []

    # бота выгнали — посты гаснут, настройки живы
    db.remove_report_chat(-100_1)
    assert db.report_chats_for("Алиджон Душанбе") == []
    # вернули в беседу: кик — способ группы отписаться, и ЛЮБОЙ участник может
    # вернуть бота, поэтому включённость НЕ воскресает (только пульт).
    # Сотрудники сохраняются — админу достаточно снова поставить галку.
    db.upsert_report_chat(-100_1, "RBR Tajikistan (new)")
    c = db.list_report_chats()[0]
    assert c["title"] == "RBR Tajikistan (new)"
    assert c["enabled"] is False
    assert c["employees"] == ["Алиджон Душанбе", "Второй"]
    assert db.report_chats_for("Второй") == []
    db.set_report_chat(-100_1, enabled=True)
    assert db.report_chats_for("Второй") == [-100_1]
    # обычное повторное добавление БЕЗ кика (сообщение в группе) галку не сбивает
    db.upsert_report_chat(-100_1, "RBR Tajikistan (new)")
    assert db.report_chats_for("Второй") == [-100_1]


def test_chat_disabled_or_unassigned_gets_nothing():
    db.upsert_report_chat(-100_2, "Выключенная")
    db.set_report_chat(-100_2, employees=["Сотрудник"])   # включить забыли
    assert db.report_chats_for("Сотрудник") == []
    db.set_report_chat(-100_2, enabled=True)
    assert db.report_chats_for("Сотрудник") == [-100_2]
    db.set_report_chat(-100_2, employees=[])              # открепили всех
    assert db.report_chats_for("Сотрудник") == []


def test_employee_in_two_chats():
    db.upsert_report_chat(-1, "TJ")
    db.upsert_report_chat(-2, "Общая")
    db.set_report_chat(-1, enabled=True, employees=["Иванов"])
    db.set_report_chat(-2, enabled=True, employees=["Иванов", "Петров"])
    assert sorted(db.report_chats_for("Иванов")) == [-2, -1]


# --- гео фото -----------------------------------------------------------------
def test_contract_photos_skip_upload_temporaries(tmp_path, monkeypatch):
    """Времянки атомарной загрузки (~….part) — не фото: в альбомы руководителям
    и в отчёты бесед не попадают."""
    import photostore
    monkeypatch.setattr(photostore, "PHOTO_DIR", tmp_path)
    d = tmp_path / "KD-7"
    d.mkdir()
    (d / "op1.jpg").write_bytes(b"x")
    (d / "~op2.123.jpg.part").write_bytes(b"x")
    (d / "~времянка.jpg").write_bytes(b"x")
    assert [p.name for p in photostore.contract_photos("KD-7")] == ["op1.jpg"]


def test_photo_geo_saved_once_per_file():
    db.add_photo_geo("KD-1", "KD-1/op1.jpg", 101, 40.28, 69.62, 8,
                     "2026-07-24T16:10:34")
    # повторная доставка того же фото из оффлайн-очереди — не дубль
    db.add_photo_geo("KD-1", "KD-1/op1.jpg", 101, 0.0, 0.0, 999, None)
    got = db.photo_geo_for_files(["KD-1/op1.jpg", "KD-1/нет.jpg"])
    assert set(got) == {"KD-1/op1.jpg"}
    row = got["KD-1/op1.jpg"]
    assert (row["lat"], row["lon"], row["acc_m"]) == (40.28, 69.62, 8)
    assert row["captured_at"] == "2026-07-24T16:10:34"
    assert db.photo_geo_for_files([]) == {}


# --- разбор гео-параметров /api/photo ----------------------------------------
def test_photo_geo_header_parse():
    """Основной путь — заголовок X-Photo-Geo «lat,lon,acc,gat»: query оседает в
    access-логах общего VPS, заголовки — нет."""
    class Req:
        def __init__(self, h):
            self.headers = h
            self.query = {}
    ok = webmod._photo_geo_from(Req({"X-Photo-Geo": "40.28, 69.62, 7.9, 1784900000000"}))
    assert ok is not None and (ok[0], ok[1], ok[2]) == (40.28, 69.62, 7)
    assert webmod._photo_geo_from(Req({"X-Photo-Geo": "мусор"})) is None
    assert webmod._photo_geo_from(Req({})) is None


def test_photo_geo_query_parse():
    ok = webmod._photo_geo_query({"lat": "40.28", "lon": "69.62", "acc": "7.9",
                                  "gat": "1784900000000"})
    assert ok is not None
    lat, lon, acc, captured = ok
    assert (lat, lon, acc) == (40.28, 69.62, 7)
    assert captured and captured.startswith("20")          # валидная ISO-дата
    # без координат/мусор/вне диапазона — фото без геометки, не ошибка
    assert webmod._photo_geo_query({}) is None
    assert webmod._photo_geo_query({"lat": "мусор", "lon": "1"}) is None
    assert webmod._photo_geo_query({"lat": "91", "lon": "0"}) is None
    lat, lon, acc, captured = webmod._photo_geo_query({"lat": "1", "lon": "2",
                                                       "acc": "мусор", "gat": "х"})
    assert (acc, captured) == (0, None)


# --- текст отчёта в беседу ----------------------------------------------------
def test_visit_report_text_full():
    row = {"responsible": "Алиджон Душанбе", "client": "Курбанов Охунчон",
           "contract_id": "KD-9", "city": "Спитаменский район",
           "address": "село Янгиобод, ул Каримов",
           "call_status": "Обещает оплатить",
           "agreed_payment": "Обещает оплатить 3-е лицо",
           "visit_comment": "Нашли отца клиента, обещал зайти в джамоат",
           "call_comment": ""}
    text = notify.visit_report_text(row, dist_m=120, visit_geo=(40.28, 69.62),
                                    photo_geo=(40.281, 69.621), photos_n=3)
    assert "Алиджон Душанбе" in text
    assert "Курбанов Охунчон (KD-9)" in text
    assert "Спитаменский район · село Янгиобод, ул Каримов" in text
    assert "Обещает оплатить ·" in text
    assert "Нашли отца клиента" in text
    assert "pt=69.62,40.28" in text                        # точка закрытия
    assert "(120 м от адреса)" in text
    assert "Фото: 3" in text
    assert "pt=69.621,40.281" in text                      # точка съёмки


def test_chat_migration_moves_settings_to_new_id():
    """Группа стала супергруппой: Telegram меняет chat_id — настройка и
    сотрудники переезжают, отчёты не умирают молча."""
    db.upsert_report_chat(-500, "Отчёты")
    db.set_report_chat(-500, enabled=True, employees=["Иванов"])
    db.migrate_report_chat(-500, -100_500)
    assert db.report_chats_for("Иванов") == [-100_500]
    chats = {c["chat_id"]: c for c in db.list_report_chats()}
    assert -500 not in chats
    assert chats[-100_500]["enabled"] is True
    db.migrate_report_chat(-999, -998)          # неизвестный старый id — no-op
    assert db.report_chats_for("Иванов") == [-100_500]


def test_report_chats_match_by_name_key():
    """Опечатка регистра при закреплении ФИО в пульте не должна глушить отчёты:
    матч по _name_key, чужое ФИО по-прежнему не матчится."""
    db.upsert_report_chat(-100_7, "RBR")
    db.set_report_chat(-100_7, enabled=True, employees=["алиджон  душанбе"])
    assert db.report_chats_for("Алиджон Душанбе") == [-100_7]
    assert db.report_chats_for("Алиджон Худжанд") == []


def test_visit_report_text_escapes_html():
    """Бот шлёт с parse_mode=HTML: «<» в комментарии не должен ронять доставку,
    а разметка из реестра — рендериться в беседе ссылкой/жирным."""
    row = {"responsible": "А", "client": "Б <тест>", "contract_id": "KD-1",
           "visit_comment": 'долг <передал соседям> & <a href="http://x">жми</a>'}
    text = notify.visit_report_text(row)
    assert "<передал" not in text and "<a href" not in text
    assert "&lt;передал соседям&gt;" in text
    assert "&amp;" in text


def test_visit_report_text_minimal_and_km():
    text = notify.visit_report_text({"responsible": "", "client": None,
                                     "contract_id": "KD-0"})
    assert "—" in text and "KD-0" in text
    assert "Точка закрытия" not in text and "Фото" not in text
    text2 = notify.visit_report_text({"contract_id": "KD-0"}, dist_m=2500,
                                     visit_geo=(1.0, 2.0))
    assert "(2.5 км от адреса)" in text2
