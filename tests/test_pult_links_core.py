"""Раздел «Вход»: арифметика состояний ссылок (pult/core/links.py, без Qt).

Состояние ссылки — единственное, во что оператор верит на слово: по нему он
решает, выдавать вход заново или ждать. Поэтому проверяется без экрана и
подробно: кто виден в таблице, что написано в колонке «Состояние», кого
считают чипы, как собирается короткий адрес по коду и что обещают тексты.
"""

from __future__ import annotations

from pult.core import links as lk


def emp(name, kind=None, tid=None, contracts=10):
    e = {"employee": name, "contracts": contracts}
    if kind:
        e["kind"] = kind
    if tid:
        e["telegram_id"] = tid
    return e


def link(name, tid=1, code="abc", uses=0, max_uses=2, alive=True, entered=False,
         devices=0, created="2026-09-21T10:00:00", expires="2026-09-28T10:00:00",
         last_use=None, agent=None, bonus=0, kind=None):
    return {"employee": name, "telegram_id": tid, "kind": kind, "code": code,
            "created_at": created, "expires_at": expires, "uses": uses,
            "max_uses": max_uses, "bonus_ios": bonus, "last_use_at": last_use,
            "last_agent": agent, "entered": entered, "devices": devices,
            "alive": alive}


def state_of(rows, name):
    row = next(r for r in rows if r["employee"] == name)
    return lk.link_state(row), lk.state_text(row)


# --- кто попадает в таблицу ---------------------------------------------------
def test_registry_people_are_visible_before_any_link():
    """Первый день теста: ссылок нет ни у кого, а список обязан быть непустым —
    прежний экран показывал только привязанных и был пуст."""
    rows = lk.merge_rows([emp("Иванов Иван"), emp("Петров Пётр", kind="courier")])
    assert [r["employee"] for r in rows] == ["Иванов Иван", "Петров Пётр"]
    assert state_of(rows, "Иванов Иван") == ("none", "не выдавали")
    assert lk.kind_text(rows[1]) == "курьер"
    assert all(r["in_registry"] for r in rows)


def test_person_outside_registry_is_appended_not_lost():
    """Курьер ГПХ ответственным в реестре не бывает, но вход ему выдали —
    потерять такую строку нельзя, иначе отзывать доступ будет негде."""
    rows = lk.merge_rows([emp("Иванов Иван")],
                         links=[link("Курьеров Курьер", tid=77, kind="courier")])
    assert [r["employee"] for r in rows] == ["Иванов Иван", "Курьеров Курьер"]
    extra = rows[1]
    assert extra["in_registry"] is False
    assert extra["telegram_id"] == 77 and lk.kind_text(extra) == "курьер"


# --- состояния ----------------------------------------------------------------
def test_states_read_as_words():
    rows = lk.merge_rows(
        [emp("А"), emp("Б"), emp("В"), emp("Г"), emp("Д"), emp("Е")],
        links=[link("Б", tid=2),
               link("В", tid=3, uses=1),
               link("Г", tid=4, uses=1, entered=True, devices=2,
                    last_use="2026-09-21T12:30:00", agent="iphone"),
               link("Д", tid=5, uses=0, alive=False),
               link("Е", tid=6, uses=2, alive=False)])
    assert state_of(rows, "А") == ("none", "не выдавали")
    assert state_of(rows, "Б") == ("issued", "выдана, не открыта")
    assert state_of(rows, "В") == ("opened", "открыта 1 из 2")
    assert state_of(rows, "Г") == ("entered", "вошёл · устройств 2")
    assert state_of(rows, "Д") == ("expired", "просрочена")
    assert state_of(rows, "Е") == ("expired", "открытий не осталось")


def test_entered_is_proved_by_a_live_token_not_by_an_imported_last_seen():
    """Портфели на машину компании приезжают с прода вместе с users.last_seen,
    а пишет это поле только Телеграм-ветка, которой в контуре компании нет.
    Поверь ему — и все перенесённые окажутся «вошедшими» ещё до выдачи ссылки:
    не видно ни кому выдавать, ни что стало с выданным."""
    imported = lk.merge_rows(
        [emp("Иванов Иван")],
        workers=[{"employee": "Иванов Иван", "entry": "telegram",
                  "last_seen": "2026-07-01T08:00:00"}])
    assert state_of(imported, "Иванов Иван") == ("none", "не выдавали")
    assert imported[0]["last_seen"] == ""          # и колонка не врёт чужой датой
    assert lk.filter_counts(imported)["none"] == 1

    # Живой токен приложения на ЭТОМ сервере — единственное доказательство.
    live = lk.merge_rows([emp("Иванов Иван")],
                         workers=[{"employee": "Иванов Иван", "entry": "pwa",
                                   "last_seen": "2026-07-01T08:00:00"}])
    assert state_of(live, "Иванов Иван")[0] == "entered"


def test_fresh_link_shows_its_own_state_even_to_someone_already_inside():
    """Перевыдача вошедшему (сменил телефон, слетел ярлык): пока факт входа
    закрывал собой состояние ссылки, оператор не видел, открыл ли человек
    новую. Плашки и чип «вошли» при этом человека не теряют."""
    rows = lk.merge_rows(
        [emp("Иванов Иван", tid=1)],
        links=[link("Иванов Иван", tid=1, uses=0, entered=True, devices=1)])
    assert state_of(rows, "Иванов Иван") == ("issued", "выдана заново, не открыта")
    assert lk.summary_tiles(rows)[2] == ("вошли", 1)
    assert lk.filter_counts(rows)["entered"] == 1
    assert "вошли 1 из 1" in lk.link_summary(rows)
    # открыл новую ссылку — снова «вошёл», зелёным
    opened = lk.merge_rows(
        [emp("Иванов Иван", tid=1)],
        links=[link("Иванов Иван", tid=1, uses=1, entered=True, devices=2)])
    assert state_of(opened, "Иванов Иван") == ("entered", "вошёл · устройств 2")


def test_revoked_is_not_the_same_as_never_issued():
    """Сервер отозванные ссылки удаляет. Без памяти об отзыве строка сказала бы
    «не выдавали», и оператор решил бы, что промахнулся кнопкой."""
    rows = lk.merge_rows([emp("Иванов Иван", tid=501)], revoked=["501"])
    assert state_of(rows, "Иванов Иван") == ("revoked", "отозвана")
    assert lk.state_role(rows[0]) == "danger"
    # выдали заново — память об отзыве больше не нужна
    again = lk.merge_rows([emp("Иванов Иван", tid=501)],
                          links=[link("Иванов Иван", tid=501)], revoked=["501"])
    assert state_of(again, "Иванов Иван") == ("issued", "выдана, не открыта")


def test_state_colors_come_from_theme_roles_only():
    assert set(lk.STATE_ROLE.values()) <= {"muted", "warn", "success", "danger"}
    assert set(lk.STATE_ROLE) == {"none", "issued", "opened", "entered",
                                  "expired", "revoked"}


def test_link_without_alive_flag_is_judged_by_date_and_opens():
    """Если сервер не прислал готовый признак «жива», считаем сами: выданная
    сегодня ссылка не должна выглядеть просроченной — за этим оператор пойдёт
    выдавать её второй раз."""
    fresh = {"code": "a", "uses": 0, "max_uses": 2, "expires_at": "2099-01-01T00:00:00"}
    assert lk.link_alive(fresh) is True
    assert lk.link_alive(dict(fresh, expires_at="2000-01-01T00:00:00")) is False
    assert lk.link_alive(dict(fresh, uses=2)) is False
    assert lk.link_alive({}) is False
    assert lk.link_alive(dict(fresh, alive=False)) is False   # сервер главнее
    rows = lk.merge_rows([emp("Иванов Иван")], links=[fresh | {"employee": "Иванов Иван"}])
    assert state_of(rows, "Иванов Иван") == ("issued", "выдана, не открыта")


def test_live_link_wins_over_fresher_dead_one():
    """У человека две ссылки: вчерашняя живая и сегодняшняя мёртвая (исчерпали
    на чужом телефоне). Показывать надо ту, которой можно пользоваться."""
    rows = lk.merge_rows(
        [emp("Иванов Иван")],
        links=[link("Иванов Иван", code="dead", alive=False, uses=2,
                    created="2026-09-21T12:00:00"),
               link("Иванов Иван", code="live", alive=True, uses=0,
                    created="2026-09-20T09:00:00")])
    assert rows[0]["code"] == "live"
    assert lk.link_state(rows[0]) == "issued"


# --- таблица, чипы, сводка ----------------------------------------------------
def test_link_row_carries_hidden_state_key():
    rows = lk.merge_rows([emp("Иванов Иван")],
                         links=[link("Иванов Иван", uses=1, agent="android",
                                     last_use="2026-09-21T12:30:00")])
    row = lk.link_row(rows[0])
    assert len(row) == lk.STATE_IN_ROW + 1 == len(lk.LINK_COLUMNS) + 1
    assert row[:4] == ("Иванов Иван", "выездник", "открыта 1 из 2", "1 из 2")
    assert row[4] == "21.09 10:00" and row[5] == "21.09 12:30"
    assert row[6] == "Android"
    assert lk.row_state(row) == "opened"
    assert lk.row_state(("а", "б")) == ""      # чужая короткая строка не роняет


def test_chips_count_everyone_and_expired_go_to_work_to_do():
    """Чип «не выдавали» отвечает на вопрос «кому выдавать», поэтому в него же
    попадают просроченные: оператору нужно действие, а не разбор состояний."""
    rows = lk.merge_rows(
        [emp("А"), emp("Б"), emp("В"), emp("Г")],
        links=[link("Б", tid=2), link("В", tid=3, uses=1),
               link("Г", tid=4, uses=0, alive=False)])
    counts = lk.filter_counts(rows)
    assert counts["all"] == 4
    assert counts["none"] == 2          # «А» без ссылки и «Г» с просроченной
    assert counts["issued"] == 1 and counts["opened"] == 1
    assert counts["entered"] == 0 and counts["revoked"] == 0
    assert [k for k, _t in lk.LINK_FILTERS][0] == "all"


def test_tiles_and_summary_speak_about_people_not_rows():
    rows = lk.merge_rows(
        [emp("А"), emp("Б"), emp("В")],
        links=[link("Б", tid=2), link("В", tid=3, uses=1, entered=True, devices=1)])
    assert lk.summary_tiles(rows) == [("в реестре", 3), ("ссылок живо", 2),
                                      ("вошли", 1), ("ждут", 2)]
    text = lk.link_summary(rows)
    assert "вошли 1 из 3" in text and "вход не выдан 1" in text
    assert "Реестр пуст" in lk.link_summary([])


# --- адрес ссылки -------------------------------------------------------------
def test_short_url_is_built_from_code_and_remembered_base():
    assert lk.base_of("https://x.example/i/КОД") == "https://x.example"
    assert lk.base_of("https://x.example/#link=КОД") == "https://x.example"
    assert lk.base_of("https://x.example/") == "https://x.example"
    row = {"code": "abc"}
    assert lk.link_url(row, "https://x.example/") == "https://x.example/i/abc"
    assert lk.link_url(row, "") == ""                  # база неизвестна — не врём
    assert lk.link_url({"url": "https://y/i/z"}, "https://x") == "https://y/i/z"
    assert lk.link_url({}, "https://x") == ""


def test_fresh_link_always_has_all_its_opens_left():
    """Сервер может вернуть в `uses` и «израсходовано», и эхо запроса «выдать
    на N открытий». На свежей ссылке «осталось 0» — недопустимо ни в каком."""
    for payload in ({"uses": 0, "max_uses": 2}, {"uses": 2, "max_uses": 2},
                    {"uses": 2}):
        row = lk.fresh_row(dict(payload, employee="Иванов Иван",
                                url="https://x/i/abc", code="abc"))
        assert row["uses"] == 0 and row["max_uses"] == 2
        assert lk.uses_left(row) == 2
        assert lk.link_state(row) == "issued"
    assert lk.fresh_row({}).get("max_uses") == lk.DEFAULT_USES


# --- тексты для человека ------------------------------------------------------
def test_issue_lines_tell_whom_until_when_and_what_to_do():
    row = lk.fresh_row({"employee": "Иванов Иван", "max_uses": 2,
                        "expires_at": "2026-09-28T10:00:00", "local_identity": True,
                        "url": "https://x/i/abc"})
    lines = lk.issue_lines(row)
    roles = {role for role, _t in lines}
    assert roles <= {"small", "warn", "success", "muted"}
    text = lk.issue_note(row)
    assert "Иванов Иван" in text
    assert "до 28.09" in text and "осталось открытий: 2" in text
    assert "На экран „Домой“" in text            # ярлык уносит вход с собой
    assert "служебная личность" in text          # вход без Telegram
    assert "персональная" in text


def test_shortcut_advice_never_asks_to_paste_the_link_inside_the_shortcut():
    """В открытом ярлыке нет адресной строки: «вставь ссылку ещё раз» там
    выполнить нечем, а вторая попытка в браузере сожжёт запасное открытие
    впустую. Подсказка про ярлык нужна и на последнем открытии — именно тогда
    оператор по таблице и «помогает с ярлыком»."""
    two = lk.issue_note(lk.fresh_row({"employee": "Иванов Иван", "max_uses": 2}))
    assert "вставит ссылку" not in two and "вставь" not in two
    assert "наведёт камеру на этот QR ещё раз" in two      # запас — на переустановку

    one = lk.issue_note(lk.fresh_row({"employee": "Иванов Иван", "max_uses": 1}))
    assert "На экран „Домой“" in one             # помощь с ярлыком остаётся
    assert "ещё раз" not in one                  # а второго открытия не обещаем
    assert "осталось открытий: 1" in one and "выдай вход заново" in one


def test_questions_before_dangerous_buttons_say_what_dies():
    row = {"employee": "Иванов Иван"}
    assert "НЕОТКРЫТАЯ" in lk.reissue_text(row) and "Иванов Иван" in lk.reissue_text(row)
    assert "разлогинится" in lk.revoke_text(row)
    assert "остаются в базе" in lk.revoke_text(row)


def test_uses_column_is_empty_without_a_link():
    assert lk.uses_text({}) == ""
    assert lk.uses_text({"code": "a", "uses": 1, "max_uses": 2}) == "1 из 2"
    assert lk.uses_left({"uses": 3, "max_uses": 2}) == 0
    assert lk.agent_text({"agent": "inapp"}) == "в мессенджере"
    assert lk.agent_text({}) == "—"
    assert lk.fmt_when(None) == "—" and lk.fmt_when("2026-09-21") == "21.09"
