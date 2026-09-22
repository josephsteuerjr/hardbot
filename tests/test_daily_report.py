"""Сводный отчёт руководителям одной таблицей: закреплено / сегодня / всего.

Просьба руководителей 08.09.2026 (первый день массового теста): «общей
таблицей по всем сотрудникам — закреплено, отработано сегодня, отработано
всего по нарастающей; Узбекистан и Таджикистан вместе». Длинный текст
дайджеста заменён книгой: интерактивный разбор живёт во вкладке приложения.
"""
import asyncio
import importlib.util
import io
from datetime import date, timedelta
from pathlib import Path

import db
import digest
import digest_data as dd
import web

# server/app.py нельзя импортировать как «app»: это имя занято пакетом
# engine/app (см. test_push_delivery) — грузим по пути под своим именем.
_spec = importlib.util.spec_from_file_location(
    "hardbot_app_report", Path(web.__file__).with_name("app.py"))
botapp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(botapp)

MGR = 559001
EMP_A, EMP_B, EMP_IDLE = "Отапиев А.", "Отбпиев Б.", "Отспящий С."
TODAY = date.today()
T = TODAY.isoformat()
D30 = (TODAY - timedelta(days=30)).isoformat()


def _visit(cid, day, status):
    with db.connect() as conn:
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES (?,?,?,?)",
                     (cid, 1, day,
                      f"Выезд (миниапп). Статус: {status}; Договорённость: ; "))
        conn.commit()


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'RP-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'RP-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    rows = [("RP-0", "Клиент Ноль", EMP_A, "1990-01-01"),
            ("RP-1", "Клиент Ноль", EMP_A, "1990-01-01"),
            ("RP-2", "Клиент Два", EMP_A, "1991-01-01"),
            ("RP-3", "Клиент Три", EMP_B, "1992-01-01"),
            ("RP-4", "Клиент Спящий", EMP_IDLE, "1993-01-01")]
    with db.connect() as conn:
        for cid, client, emp, dob in rows:
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "partner, debt_today, overall_result, country) VALUES (?,?,?,?,?,?,?,?)",
                (cid, client, emp, dob, "ЗК", "100 000", "Не обработан", "Таджикистан"))
        conn.commit()
    _visit("RP-0", D30, "Нет контакта")          # только в «всего»
    _visit("RP-0", T, "Оплачен")
    _visit("RP-2", T, "Обещает оплатить")
    db.record_bot_visit("RP-0", {"call_status": "Оплачен"}, T)
    db.record_bot_visit("RP-2", {"call_status": "Обещает оплатить"}, T)
    _visit("RP-3", T, "Рассрочка")
    db.record_bot_visit("RP-3", {"call_status": "Рассрочка"}, T)
    db.set_manager(MGR, "Рук", [], [EMP_A, EMP_B, EMP_IDLE])
    db.reset_role_caches()


def _cleanup():
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'RP-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'RP-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    db.reset_role_caches()


def test_rows_carry_assigned_today_and_running_total():
    _seed()
    try:
        data = dd.daily_report_data(db.get_manager(MGR), T,
                                    employees=[EMP_A, EMP_B, EMP_IDLE])
        by = {r["employee"]: r for r in data["rows"]}
        a = by[EMP_A]
        assert (a["assigned_persons"], a["assigned_contracts"]) == (2, 3)
        assert (a["today_persons"], a["today_visits"]) == (2, 2)
        assert a["total_persons"] == 2 and a["total_visits"] == 3, "нарастающий итог"
        assert a["today_paid"] == 1 and a["today_promised"] == 1
        assert a["first_day"] == D30
        assert a["country_name"] == "Таджикистан"
        idle = by[EMP_IDLE]
        assert idle["today_persons"] == 0 and idle["total_persons"] == 0
        assert idle["assigned_persons"] == 1
        assert data["rows"][-1]["employee"] == EMP_IDLE, "молчащие — внизу"
        t = data["totals"]
        assert (t["employees"], t["active"]) == (3, 2)
        assert t["today_persons"] == 3 and t["total_visits"] == 4
        assert t["assigned_contracts"] == 5
    finally:
        _cleanup()


def test_caption_fits_telegram_and_names_both_numbers():
    _seed()
    try:
        data = dd.daily_report_data(db.get_manager(MGR), T,
                                    employees=[EMP_A, EMP_B, EMP_IDLE])
        cap = dd.daily_report_caption(data)
        assert len(cap) <= 1024, "подпись документа в Telegram ограничена"
        assert "Отработано сегодня" in cap and "Всего с начала работы" in cap
        assert "Закреплено" in cap
    finally:
        _cleanup()


def test_xlsx_has_a_row_per_employee_and_a_total():
    _seed()
    try:
        from openpyxl import load_workbook
        blob = dd.daily_report_xlsx(db.get_manager(MGR), T,
                                    employees=[EMP_A, EMP_B, EMP_IDLE])
        assert blob[:2] == b"PK"
        wb = load_workbook(io.BytesIO(blob))
        # Воронка едет и в вечерней книге (22.09.2026): на контуре компании
        # Telegram выключен, и кнопка «⬇️ Excel» в приложении — не дорога.
        assert wb.sheetnames[:2] == ["Сводка", "Исходы за день"]
        assert "Воронка" in wb.sheetnames and "Обещания" in wb.sheetnames
        ws = wb["Сводка"]
        head = [c.value for c in ws[3]]
        assert head[:5] == ["Сотрудник", "Республика", "Закреплено должников",
                            "Закреплено договоров", "Отработано сегодня"]
        names = [ws.cell(row=r, column=1).value for r in range(4, ws.max_row + 1)]
        assert names[:3] == [EMP_A, EMP_B, EMP_IDLE] and names[-1] == "Итого"
        assert dd.daily_report_filename(T).endswith(".xlsx")
    finally:
        _cleanup()


def test_digest_view_returns_caption_keyboard_and_document():
    _seed()
    try:
        built = digest.daily_report_view(db.get_manager(MGR), T)
        assert len(built) == 3
        text, kb, doc = built
        assert "Сводка за" in text and doc[0].endswith(".xlsx") and doc[1][:2] == b"PK"
        assert kb is not None
    finally:
        _cleanup()


def test_broadcast_sends_a_document_and_falls_back_to_text(monkeypatch):
    """Сбой сборки книги не имеет права оставить руководителя без сводки."""
    _seed()
    try:
        sent = []

        class _Bot:
            async def send_document(self, chat_id, document, caption=None,
                                    reply_markup=None):
                sent.append(("doc", chat_id, caption))

            async def send_message(self, chat_id, text, reply_markup=None):
                sent.append(("msg", chat_id, text))

        monkeypatch.setattr(botapp.notify, "bot_missing", lambda _b: False)
        monkeypatch.setattr(botapp, "_digest_targets",
                            lambda _k: [db.get_manager(MGR)])
        monkeypatch.setattr(botapp, "_settled", lambda *_a: set())
        monkeypatch.setattr(botapp, "_mark_settled", lambda *_a: None)
        bot = _Bot()
        assert asyncio.run(botapp._push_digests(bot, T)) == 0
        assert sent and sent[0][0] == "doc", "сводка обязана уйти книгой"

        sent.clear()
        monkeypatch.setattr(digest, "daily_report_view",
                            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("нет openpyxl")))
        assert asyncio.run(botapp._push_digests(bot, T)) == 0
        assert sent and sent[0][0] == "msg", "при сбое книги уходит прежний текст"
    finally:
        _cleanup()


def test_switch_returns_the_old_text_digest(monkeypatch):
    _seed()
    try:
        db.set_setting("digest_xlsx", "0")
        built = botapp._digest_build(db.get_manager(MGR), T)
        assert len(built) == 2, "рубильник возвращает текстовый дайджест"
    finally:
        db.set_setting("digest_xlsx", "1")
        _cleanup()
