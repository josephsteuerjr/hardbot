"""Рассылка книг почтой из пульта аналитика (pult/core/mailer.py, 29.09.2026).

Проверяется то, что можно проверить без Outlook и сети: расписание, приём
настроек из окна, история, итоги из книги и вставка текста перед подписью."""
import io
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from pult.core import mailer

TUE_EVENING = datetime(2026, 9, 29, 19, 45)      # вторник
TUE_MORNING = datetime(2026, 9, 29, 10, 0)
SAT_EVENING = datetime(2026, 10, 3, 20, 0)
FRI_EVENING = datetime(2026, 10, 2, 19, 31)


def _cfg(**kw):
    base = {"enabled": True, "recipients": [{"name": "Гендир", "email": "ceo@example.com"}]}
    base.update(kw)
    return mailer.config(base)


def test_day_book_is_due_after_time_on_a_working_day():
    assert mailer.due(_cfg(), TUE_EVENING) == ["day"]
    assert mailer.due(_cfg(), TUE_MORNING) == []
    assert mailer.due(_cfg(), SAT_EVENING) == [], "суббота не в списке дней"


def test_nothing_is_due_when_off_or_without_recipients():
    assert mailer.due(_cfg(enabled=False), TUE_EVENING) == []
    assert mailer.due(_cfg(recipients=[]), TUE_EVENING) == []


def test_sent_today_is_not_sent_again_but_tomorrow_is():
    cfg = _cfg(last={"day": "2026-09-29"})
    assert mailer.due(cfg, TUE_EVENING) == []
    assert mailer.due(cfg, datetime(2026, 9, 30, 19, 30)) == ["day"]


def test_week_book_goes_only_on_its_weekday():
    cfg = _cfg(books={"day": False, "week": True}, week_day=4)
    assert mailer.due(cfg, TUE_EVENING) == []
    assert mailer.due(cfg, FRI_EVENING) == ["week"]


def test_next_run_is_worded_for_a_person():
    assert mailer.next_run(_cfg(), TUE_MORNING) == "сегодня в 19:30 — сводка за день"
    assert mailer.next_run(_cfg(last={"day": "2026-09-29"}), TUE_EVENING).startswith("завтра в 19:30")
    assert mailer.next_run(_cfg(enabled=False), TUE_MORNING) == ""


def test_clean_accepts_the_window_form_and_rejects_nonsense():
    got = mailer.clean({"enabled": True, "time": "9.05", "days": [0, "4", 9],
                        "recipients": [{"name": " Зам ", "email": " dep@example.com "},
                                       {"name": "", "email": ""}],
                        "books": {"day": True}, "direction": "tj"})
    assert got["time"] == "09:05" and got["days"] == [0, 4]
    assert got["recipients"] == [{"name": "Зам", "email": "dep@example.com"}]
    assert got["direction"] == "tj"
    with pytest.raises(ValueError, match="адрес"):
        mailer.clean({"recipients": [{"name": "x", "email": "не-почта"}]})
    with pytest.raises(ValueError, match="получателя"):
        mailer.clean({"enabled": True, "recipients": []})
    with pytest.raises(ValueError, match="ЧЧ:ММ"):
        mailer.clean({"time": "25:00"})


def test_history_marks_the_day_only_for_scheduled_success():
    when = TUE_EVENING
    cfg = mailer.remember(_cfg(), "day", ok=True, when=when, to=["ceo@example.com"], manual=True)
    assert cfg["last"] == {}, "ручная отправка расписание не сдвигает"
    cfg = mailer.remember(cfg, "day", ok=True, when=when, to=["ceo@example.com"])
    assert cfg["last"] == {"day": "2026-09-29"}
    cfg = mailer.remember(cfg, "week", ok=False, when=when, to=[], error="Outlook")
    rows = mailer.history_rows(cfg)
    assert rows[0]["state"] == "не ушло" and rows[0]["error"] == "Outlook"
    assert rows[2]["state"] == "отправлено вручную"


def test_summary_is_read_from_the_total_row_of_the_book():
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    ws.append(["Сводка"])
    ws.append([])
    ws.append(["Сотрудник", "Республика", "Выездов сегодня", "Отработано сегодня",
               "Оплачен сегодня", "У адреса не был"])
    ws.append(["Иванов", "", 3, 2, 1, 0])
    ws.append(["Итого", "", 30, 20, 4, 2])
    buf = io.BytesIO()
    wb.save(buf)
    assert mailer.summary_from_book(buf.getvalue()) == [
        ("Выездов", 30), ("Отработано должников", 20), ("Оплатили", 4),
        ("У адреса не был", 2)]


def test_text_goes_inside_the_body_before_the_signature():
    sig = '<html><head></head><body lang=RU style="x"><p>С уважением</p></body></html>'
    out = mailer.with_signature("<p>Сводка</p>", sig)
    assert out.index("<p>Сводка</p>") > out.index("<body")
    assert out.index("<p>Сводка</p>") < out.index("С уважением")
    assert mailer.with_signature("<p>a</p>", "") == "<p>a</p>"


def test_subject_file_and_query_name_the_period():
    cfg = _cfg(direction="uz")
    d = date(2026, 9, 29)
    assert mailer.subject("day", cfg, d) == "Выезды · Узбекистан · сводка за 29.09.2026"
    assert mailer.subject("week", cfg, d) == "Выезды · Узбекистан · неделя 23.09–29.09.2026"
    assert mailer.file_name("day", cfg, d) == "Сводка выездов Узбекистан 29.09.2026.xlsx"
    assert mailer.query("day", cfg, d) == {"direction": "uz", "kind": "daily", "date": "2026-09-29"}
    assert mailer.query("week", _cfg(), d) == {"preset": "7d"}
    body = mailer.body_html("day", cfg, d, [("Выездов", 1234)], TUE_EVENING)
    assert "Добрый вечер" in body and "1 234" in body and "Как читать" in body
