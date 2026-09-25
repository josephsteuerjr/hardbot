"""Перенос портфелей нескольких сотрудников: что приехало ровно нужное.

Операцию делают ночью, на живых данных, один раз и под срок — то есть ровно в
тех условиях, в которых не проверяют ничего. Поэтому проверяем здесь, на двух
временных базах, все четыре обещания инструмента:

  * в исходную базу не пишется ни одной строки (сверяем файл побайтно);
  * приезжает только названный портфель, чужое остаётся дома;
  * повторный приём того же архива ничего не удваивает и ничего не стирает;
  * при разнице колонок переносится пересечение, и об этом сказано вслух.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tarfile
from pathlib import Path

import pytest

import db
import subset_handover as sh

ROOT = Path(__file__).resolve().parent.parent

НАШИ = ["Муминов Абдулло", "Курбонов Шохрух"]
ЧУЖОЙ = "Мадаминов Лишний"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fresh_db(path: Path, monkeypatch) -> Path:
    """Пустая база той же схемы, что на сервере: её заводит сам init_db."""
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    return path


def _fill_source(data: Path) -> None:
    """Три сотрудника: двое наших и один чужой, у всех всё одинаковое."""
    conn = sqlite3.connect(data / "hardbot.db")
    people = {"Муминов Абдулло": 101, "Курбонов Шохрух": 102, ЧУЖОЙ: 103}
    for i, (name, tg) in enumerate(people.items()):
        conn.execute("INSERT INTO agent_bind (telegram_id, employee, kind, approved,"
                     " created_at) VALUES (?,?,'agent',1,'2026-09-01')", (tg, name))
        conn.execute("INSERT INTO users (telegram_id, name, lang, created_at) "
                     "VALUES (?,?,'ru','2026-09-01')", (tg, name))
        conn.execute("INSERT INTO employee_city (employee, city, updated_at) "
                     "VALUES (?,?,'2026-09-01')", (name, "Худжанд"))
        for j in range(2):
            kd = f"KD{i}{j}"
            conn.execute("INSERT INTO clients (contract_id, client, responsible, "
                         "city, debt_total) VALUES (?,?,?,?,?)",
                         (kd, f"Должник {kd}", name, "Худжанд", "1000"))
            conn.execute("INSERT INTO client_extra (contract_id, slot, title, label,"
                         " value, updated_at) VALUES (?,'extra1','Доп','Доп',?,?)",
                         (kd, f"значение {kd}", "2026-09-01"))
            conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, "
                         "payload, op_id, session_id) VALUES (?,?,?,?,?,?)",
                         (kd, tg, "2026-09-10", "{}", f"op-{kd}", f"sess-{kd}"))
            conn.execute("INSERT INTO photo_geo (contract_id, file, telegram_id, "
                         "created_at) VALUES (?,?,?,'2026-09-10')",
                         (kd, f"{kd}/1.jpg", tg))
            conn.execute("INSERT INTO enrichment_ledger (contract_id, field_key, "
                         "value, responsible, file_path, delivered_at) "
                         "VALUES (?,'visit_comment','был',?,?,'2026-09-10')",
                         (kd, name, r"C:\Users\кто-то\Общий.xlsb"))
            conn.execute("INSERT INTO addr_candidates (contract_id, employee, lat, "
                         "lon, created_at) VALUES (?,?,40.1,69.6,'2026-09-10')",
                         (kd, name))
            conn.execute("INSERT INTO visit_presence (session_id, contract_id, "
                         "employee, telegram_id, ts, kind) VALUES (?,?,?,?,?,'open')",
                         (f"sess-{kd}", kd, name, tg, "2026-09-10T10:00"))
            conn.execute("INSERT INTO track_points (session_id, employee, "
                         "telegram_id, ts, lat, lon) VALUES (?,?,?,?,40.1,69.6)",
                         (f"sess-{kd}", name, tg, "2026-09-10T09:00"))
        # Визит времён бота: без op_id. На нём проверяется сверка «по всей
        # строке» — ключа у такой строки нет.
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload)"
                     " VALUES (?,?,?,?)", (f"KD{i}0", tg, "2026-09-09", "{}"))
        conn.execute("INSERT INTO notes (telegram_id, employee, client_rep, text, "
                     "created_at, op_id) VALUES (?,?,?,?,?,?)",
                     (tg, name, f"KD{i}0", "заметка", "2026-09-10", f"note-{i}"))
        conn.execute("INSERT INTO markers (telegram_id, kind, client_rep, lat, lon, "
                     "created_at, op_id) VALUES (?,'point',?,40.1,69.6,?,?)",
                     (tg, f"KD{i}0", "2026-09-10", f"mark-{i}"))

    # Двери и чужая история — ничего из этого ехать не должно.
    conn.execute("INSERT INTO app_links (code, telegram_id, created_at) "
                 "VALUES ('код',101,'2026-09-10')")
    conn.execute("INSERT INTO app_tokens (token, telegram_id, created_at) "
                 "VALUES (?,101,'2026-09-10')", (db.app_token_key("токен"),))
    conn.execute("INSERT INTO admin_devices (token_hash, device_id, name) "
                 "VALUES ('хэш','устройство','Ноут дома')")
    conn.execute("INSERT INTO settings (key, value) VALUES ('bot_token','секрет')")
    conn.execute("INSERT INTO audit (ts, actor, action, target) "
                 "VALUES ('2026-09-10',101,'view_card','KD00')")
    conn.execute("INSERT INTO managers (telegram_id, name) VALUES (900,'Шемяка')")
    conn.commit()
    conn.close()

    photos = data / "photos"
    for kd in ("KD00", "KD01", "KD10", "KD11", "KD20", "KD21"):
        (photos / kd).mkdir(parents=True, exist_ok=True)
        (photos / kd / "1.jpg").write_bytes(b"jpeg " + kd.encode())
        # Времянка незавершённой загрузки: не фото, в архив попасть не должна.
        (photos / kd / "~2.jpg.part").write_bytes("половина".encode("utf-8"))


@pytest.fixture
def источник(tmp_path, monkeypatch) -> Path:
    data = tmp_path / "прод"
    _fresh_db(data / "hardbot.db", monkeypatch)
    _fill_source(data)
    # Закрываем WAL до замеров: иначе «изменился файл» означало бы всего лишь
    # контрольную точку, а не запись инструмента.
    conn = sqlite3.connect(data / "hardbot.db")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return data


@pytest.fixture
def приёмник(tmp_path, monkeypatch) -> Path:
    data = tmp_path / "машина компании"
    _fresh_db(data / "hardbot.db", monkeypatch)
    (data / "photos").mkdir(parents=True, exist_ok=True)
    return data


def _take(источник: Path, tmp_path: Path, *, кто=None, out="архив.tar",
          **kwargs) -> Path:
    out = tmp_path / out
    argv = ["take", "--data", str(источник), "--work", str(tmp_path / "работа"),
            "--out", str(out), "--host", "тестовый",
            "--only-employees", ";".join(кто if кто is not None else НАШИ)]
    for key, value in kwargs.items():
        argv.append(f"--{key}")
        if value is not True:
            argv.append(str(value))
    assert sh.main(argv) == 0
    return out


def _unpack(archive: Path, where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(where)
    return where / sh.ARCHIVE_ROOT


def _count(path: Path, table: str, where: str = "") -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(
            f'SELECT COUNT(*) FROM "{table}"' + (f" WHERE {where}" if where else "")
        ).fetchone()[0]
    finally:
        conn.close()


# --- политика: каждая таблица названа ----------------------------------------

def test_каждая_таблица_названа_в_политике(tmp_path, monkeypatch):
    """Незнакомая таблица останавливает перенос целиком — и узнаётся это в
    худший момент, когда копию уже снимают под срок. Пусть узнаётся здесь."""
    path = _fresh_db(tmp_path / "новая" / "hardbot.db", monkeypatch)
    conn = sqlite3.connect(path)
    есть = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")}
    conn.close()
    assert not sorted(есть - sh.KNOWN), (
        "новые таблицы не названы в server/subset_handover.py (CARRY — едет с "
        "сотрудником, DROP_ALL — не едет никогда)")
    assert not sorted(sh.KNOWN - есть), (
        "в политике названы таблицы, которых в базе нет")


def test_незнакомая_таблица_останавливает_снятие(источник, tmp_path):
    conn = sqlite3.connect(источник / "hardbot.db")
    conn.execute("CREATE TABLE sms_gateway_keys (k TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(sh.Stop) as вышло:
        _take(источник, tmp_path)
    assert "sms_gateway_keys" in str(вышло.value)


# --- снятие -------------------------------------------------------------------

def test_в_исходную_базу_не_пишется_ни_строки(источник, tmp_path):
    было = _sha(источник / "hardbot.db")
    _take(источник, tmp_path)
    assert _sha(источник / "hardbot.db") == было
    # И портфель чужого сотрудника остался на месте — мы его не «вырезали».
    assert _count(источник / "hardbot.db", "clients") == 6


def test_приезжает_только_названный_портфель(источник, tmp_path):
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    база = архив / sh.DB_NAME

    assert _count(база, "clients") == 4
    assert _count(база, "clients", f"responsible = '{ЧУЖОЙ}'") == 0
    assert _count(база, "visits") == 6            # по три на каждого нашего
    assert _count(база, "visits", "agent_id = 103") == 0
    assert _count(база, "client_extra") == 4
    assert _count(база, "photo_geo") == 4
    assert _count(база, "agent_bind") == 2
    assert _count(база, "users") == 2
    assert _count(база, "employee_city") == 2
    assert _count(база, "enrichment_ledger") == 4
    assert _count(база, "addr_candidates") == 4
    assert _count(база, "visit_presence") == 4
    assert _count(база, "track_points") == 4
    assert _count(база, "notes") == 2
    assert _count(база, "markers") == 2

    # Двери, чужие настройки и чужая история не едут никогда.
    for таблица in sh.DROP_ALL:
        assert _count(база, таблица) == 0, таблица

    # Путь к личной книге прежнего оператора обнулён.
    assert _count(база, "enrichment_ledger", "file_path IS NOT NULL") == 0

    # Привязки приезжают ДЕЙСТВУЮЩИМИ: иначе человек откроет выданную завтра
    # ссылку и не увидит ничего (db.get_binding пускает только approved=1).
    assert _count(база, "agent_bind", "approved = 1") == 2


def test_привязки_можно_привезти_заявками(источник, tmp_path):
    архив = _unpack(_take(источник, tmp_path, **{"binds-as-requests": True}),
                    tmp_path / "распаковка")
    assert _count(архив / sh.DB_NAME, "agent_bind", "approved = 1") == 0


def test_фотографии_только_по_перенесённым_кд(источник, tmp_path):
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    файлы = sorted(p.relative_to(архив / "photos").as_posix()
                   for p in (архив / "photos").rglob("*") if p.is_file())
    assert файлы == ["KD00/1.jpg", "KD01/1.jpg", "KD10/1.jpg", "KD11/1.jpg"]
    assert not (архив / "photos" / "KD20").exists()   # чужой сотрудник


def test_архив_самодостаточен_и_проверяется_сам(источник, tmp_path, capsys):
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert (архив / sh.INVENTORY_NAME).is_file()
    assert (архив / sh.MANIFEST_NAME).is_file()
    assert (архив / sh.SUMS_NAME).is_file()
    assert sh.main(["verify", "--dir", str(архив)]) == 0
    опись = (архив / sh.INVENTORY_NAME).read_text(encoding="utf-8")
    for имя in НАШИ:
        assert имя in опись
    assert ЧУЖОЙ not in опись


def test_подмена_файла_в_архиве_видна_проверке(источник, tmp_path, capsys):
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    (архив / "photos" / "KD00" / "1.jpg").write_bytes("подменили".encode("utf-8"))
    assert sh.main(["verify", "--dir", str(архив)]) == 1
    assert "не совпала сумма" in capsys.readouterr().out


def test_опечатка_в_фио_это_отказ_а_не_пустой_архив(источник, tmp_path):
    with pytest.raises(sh.Stop) as вышло:
        _take(источник, tmp_path, кто=["Муминив Абдула"])
    assert "нет сотрудника" in str(вышло.value)


def test_фио_узнаётся_по_началу_и_без_учёта_регистра(источник, tmp_path):
    архив = _unpack(_take(источник, tmp_path, кто=["муминов  абдулло"]),
                    tmp_path / "распаковка")
    assert _count(архив / sh.DB_NAME, "clients") == 2


def test_db_only_архив_без_фотографий(источник, tmp_path):
    архив = _unpack(_take(источник, tmp_path, **{"db-only": True}),
                    tmp_path / "распаковка")
    assert not (архив / "photos").exists()
    assert sh.main(["verify", "--dir", str(архив)]) == 0


# --- приём --------------------------------------------------------------------

def _merge(приёмник: Path, архив: Path, *flags) -> int:
    return sh.main(["merge", "--data", str(приёмник), "--archive", str(архив),
                    *flags])


def test_доливка_ставит_ровно_то_что_приехало(источник, приёмник, tmp_path, capsys):
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив) == 0
    цель = приёмник / "hardbot.db"
    assert _count(цель, "clients") == 4
    assert _count(цель, "visits") == 6
    assert _count(цель, "agent_bind", "approved = 1") == 2
    assert _count(цель, "photo_geo") == 4
    assert (приёмник / "photos" / "KD00" / "1.jpg").read_bytes() == b"jpeg KD00"
    отчёт = capsys.readouterr().out
    assert "ПЕРЕНЕСЕНО" in отчёт and "фотографий" in отчёт


def test_повторный_приём_ничего_не_удваивает(источник, приёмник, tmp_path):
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив) == 0
    было = {t: _count(приёмник / "hardbot.db", t) for t in sh.CARRY}
    assert _merge(приёмник, архив) == 0
    стало = {t: _count(приёмник / "hardbot.db", t) for t in sh.CARRY}
    assert стало == было
    # В том числе визит без op_id — у него ключа нет, сверка идёт по всей строке.
    assert _count(приёмник / "hardbot.db", "visits", "op_id IS NULL") == 2


def test_повторное_снятие_довозит_только_изменившееся(источник, приёмник, tmp_path):
    """Так это и будет работать в день теста: сняли утром, к обеду люди наездили
    ещё, сняли снова. Второй архив обязан довезти новое и поправить изменённое,
    не задвоив ничего из первого."""
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив) == 0

    conn = sqlite3.connect(источник / "hardbot.db")
    conn.execute("UPDATE clients SET visit_comment='был дома, обещал 25-го' "
                 "WHERE contract_id='KD00'")
    conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload, "
                 "op_id) VALUES ('KD01',101,'2026-09-21','{}','op-новый')")
    conn.commit()
    conn.close()
    (источник / "photos" / "KD01" / "2.jpg").write_bytes(b"jpeg new")

    второй = _unpack(_take(источник, tmp_path, out="архив2.tar"),
                     tmp_path / "распаковка2")
    assert _merge(приёмник, второй) == 0
    цель = приёмник / "hardbot.db"
    assert _count(цель, "visits") == 7
    assert _count(цель, "clients") == 4
    conn = sqlite3.connect(цель)
    assert conn.execute("SELECT visit_comment FROM clients WHERE contract_id='KD00'"
                        ).fetchone()[0] == "был дома, обещал 25-го"
    conn.close()
    assert (приёмник / "photos" / "KD01" / "2.jpg").is_file()
    assert (приёмник / "photos" / "KD01" / "1.jpg").is_file()


def test_доливка_не_стирает_того_что_уже_есть(источник, приёмник, tmp_path):
    """На машине уже поработали: там чужой портфель и своя заметка."""
    conn = sqlite3.connect(приёмник / "hardbot.db")
    conn.execute("INSERT INTO clients (contract_id, client, responsible) "
                 "VALUES ('МЕСТНЫЙ','Свой должник','Местный Сотрудник')")
    conn.execute("INSERT INTO notes (telegram_id, employee, text, created_at, op_id)"
                 " VALUES (777,'Местный Сотрудник','своя заметка','2026-09-20','n-1')")
    conn.commit()
    conn.close()

    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив) == 0
    цель = приёмник / "hardbot.db"
    assert _count(цель, "clients") == 5
    assert _count(цель, "clients", "contract_id = 'МЕСТНЫЙ'") == 1
    assert _count(цель, "notes", "op_id = 'n-1'") == 1


def test_чужой_договор_с_тем_же_номером_не_затирается(источник, приёмник,
                                                      tmp_path, capsys):
    """Номер договора в проекте повторяется между республиками (47892257 был в
    двух). Доливка не имеет права стереть чужого должника."""
    conn = sqlite3.connect(приёмник / "hardbot.db")
    conn.execute("INSERT INTO clients (contract_id, client, responsible) "
                 "VALUES ('KD00','Совсем другой должник','Чужой Республиканец')")
    conn.commit()
    conn.close()

    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив) == 0
    conn = sqlite3.connect(приёмник / "hardbot.db")
    клиент, ответственный = conn.execute(
        "SELECT client, responsible FROM clients WHERE contract_id='KD00'").fetchone()
    conn.close()
    assert (клиент, ответственный) == ("Совсем другой должник", "Чужой Республиканец")
    assert "НЕ ТРОНУТО" in capsys.readouterr().out


def test_чужой_договор_не_подмешивает_доп_поля_и_снимки(источник, приёмник,
                                                        tmp_path, capsys):
    """Совпал номер — мимо карточки идёт ВСЁ, что к ней привязано.

    Защищён был только сам реестр: доп. колонки, индекс снимков и файлы
    фотографий едут по номеру договора, своего владельца не знают и ложились в
    карточку чужого должника — пока отчёт писал про этот договор «НЕ ТРОНУТО».
    """
    conn = sqlite3.connect(приёмник / "hardbot.db")
    conn.execute("INSERT INTO clients (contract_id, client, responsible) "
                 "VALUES ('KD00','Совсем другой должник','Чужой Республиканец')")
    conn.execute("INSERT INTO client_extra (contract_id, slot, title, label, value,"
                 " updated_at) VALUES ('KD00','extra1','Доп','Доп',"
                 "'ЧУЖОЕ значение','2026-09-20')")
    conn.commit()
    conn.close()
    (приёмник / "photos" / "KD00").mkdir(parents=True, exist_ok=True)
    (приёмник / "photos" / "KD00" / "чужое.jpg").write_bytes(b"jpeg alien")

    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив) == 0

    цель = приёмник / "hardbot.db"
    conn = sqlite3.connect(цель)
    assert conn.execute("SELECT value FROM client_extra WHERE contract_id='KD00'"
                        ).fetchone()[0] == "ЧУЖОЕ значение"
    conn.close()
    assert _count(цель, "client_extra", "contract_id = 'KD00'") == 1
    assert _count(цель, "photo_geo", "contract_id = 'KD00'") == 0
    assert _count(цель, "addr_candidates", "contract_id = 'KD00'") == 0
    assert _count(цель, "enrichment_ledger", "contract_id = 'KD00'") == 0
    # В каталоге снимков чужого должника — только его собственный файл.
    assert sorted(p.name for p in (приёмник / "photos" / "KD00").iterdir()) == [
        "чужое.jpg"]

    # А второй договор того же нашего сотрудника приехал целиком.
    assert _count(цель, "client_extra", "contract_id = 'KD01'") == 1
    assert (приёмник / "photos" / "KD01" / "1.jpg").is_file()
    отчёт = capsys.readouterr().out
    assert "НЕ ТРОНУТО" in отчёт and "KD00" in отчёт


def test_лишний_пробел_в_фио_не_делает_договор_чужим(источник, приёмник,
                                                     tmp_path, capsys):
    """Реестр на машине компании загрузили сами, и ФИО там с двумя пробелами —
    штатная опечатка ручного ввода, ради которой и написан _norm. Точное
    сравнение объявляло КАЖДЫЙ договор такого сотрудника чужим: свежая работа не
    приезжала, а отчёт уверял оператора, что это другой человек."""
    conn = sqlite3.connect(приёмник / "hardbot.db")
    conn.execute("INSERT INTO clients (contract_id, client, responsible) "
                 "VALUES ('KD00','Старая строка','Муминов  Абдулло')")
    conn.commit()
    conn.close()

    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив) == 0
    цель = приёмник / "hardbot.db"
    conn = sqlite3.connect(цель)
    assert conn.execute("SELECT client FROM clients WHERE contract_id='KD00'"
                        ).fetchone()[0] == "Должник KD00"
    conn.close()
    assert _count(цель, "client_extra", "contract_id = 'KD00'") == 1
    assert (приёмник / "photos" / "KD00" / "1.jpg").is_file()
    assert "НЕ ТРОНУТО" not in capsys.readouterr().out


def test_выданный_на_машине_вход_не_раздваивает_привязку(источник, приёмник,
                                                         tmp_path, capsys):
    """Порядок, вполне вероятный в день теста: аналитик сначала выдал ссылку в
    пульте (пульт завёл служебный id, человек вошёл), а архив приехал ночью.

    Двух действующих привязок на одно ФИО быть не должно: db.telegram_for_employee
    берёт первый id по порядку, поэтому «Отозвать вход» гасил бы не тот, телефон
    остался бы внутри, а статистика сотрудника распалась бы надвое."""
    uid = sh.LOCAL_UID_BASE
    conn = sqlite3.connect(приёмник / "hardbot.db")
    conn.execute("INSERT INTO agent_bind (telegram_id, employee, kind, approved,"
                 " created_at) VALUES (?,'Муминов Абдулло','agent',1,'2026-09-20')",
                 (uid,))
    conn.execute("INSERT INTO users (telegram_id, name, created_at) "
                 "VALUES (?,'Муминов Абдулло','2026-09-20')", (uid,))
    conn.commit()
    conn.close()

    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив) == 0
    цель = приёмник / "hardbot.db"
    conn = sqlite3.connect(цель)
    привязки = conn.execute("SELECT telegram_id, approved FROM agent_bind "
                            "WHERE employee='Муминов Абдулло'").fetchall()
    conn.close()
    assert привязки == [(uid, 1)]

    # Вся привезённая работа записана на здешний номер, а не на номер архива.
    assert _count(цель, "visits", "agent_id = 101") == 0
    assert _count(цель, "visits", f"agent_id = {uid}") == 3
    assert _count(цель, "notes", f"telegram_id = {uid}") == 1
    assert _count(цель, "photo_geo", f"telegram_id = {uid}") == 2
    assert _count(цель, "users", "telegram_id = 101") == 0
    # Второй сотрудник, которому здесь вход не выдавали, приехал со своим id.
    assert _count(цель, "agent_bind", "telegram_id = 102") == 1
    assert "ЛИЧНОСТИ" in capsys.readouterr().out

    # И повторный приём поверх переназначенной личности тоже ничего не двоит.
    было = {t: _count(цель, t) for t in sh.CARRY}
    assert _merge(приёмник, архив) == 0
    assert {t: _count(цель, t) for t in sh.CARRY} == было


def test_занятый_номер_не_уводит_чужую_личность(источник, приёмник, tmp_path):
    """Служебные номера раздаются на каждой машине своим счётчиком, поэтому один
    и тот же номер здесь и на проде — разные люди. Привязка не имеет права лечь
    поверх чужой и увести себе её выезды."""
    conn = sqlite3.connect(приёмник / "hardbot.db")
    conn.execute("INSERT INTO agent_bind (telegram_id, employee, kind, approved,"
                 " created_at) VALUES (101,'Местный Сотрудник','agent',1,'2026-09-20')")
    conn.execute("INSERT INTO users (telegram_id, name, created_at) "
                 "VALUES (101,'Местный Сотрудник','2026-09-20')")
    conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload,"
                 " op_id) VALUES ('МЕСТНЫЙ',101,'2026-09-20','{}','op-местный')")
    conn.commit()
    conn.close()

    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив) == 0
    цель = приёмник / "hardbot.db"
    conn = sqlite3.connect(цель)
    assert conn.execute("SELECT employee FROM agent_bind WHERE telegram_id=101"
                        ).fetchone()[0] == "Местный Сотрудник"
    assert conn.execute("SELECT name FROM users WHERE telegram_id=101"
                        ).fetchone()[0] == "Местный Сотрудник"
    новый = conn.execute("SELECT telegram_id FROM agent_bind "
                         "WHERE employee='Муминов Абдулло'").fetchone()[0]
    conn.close()
    assert новый >= sh.LOCAL_UID_BASE
    assert _count(цель, "visits", f"agent_id = {новый}") == 3
    # Выезд местного сотрудника остался при нём.
    assert _count(цель, "visits", "agent_id = 101") == 1


def test_база_служебных_номеров_совпадает_с_приложением():
    """Файл переноса подаётся в контейнер через stdin и импортировать db не
    может, поэтому константа продублирована. Расходиться им нельзя: выданный
    доливкой номер обязан попадать в тот же диапазон, что и номера пульта."""
    assert sh.LOCAL_UID_BASE == db.LOCAL_UID_BASE


def test_примерка_не_меняет_ничего(источник, приёмник, tmp_path):
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert _merge(приёмник, архив, "--dry-run") == 0
    assert _count(приёмник / "hardbot.db", "clients") == 0
    assert not (приёмник / "photos" / "KD00").exists()


# --- совместимость схем -------------------------------------------------------

def test_отсутствие_таблицы_это_отказ(источник, приёмник, tmp_path):
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    conn = sqlite3.connect(приёмник / "hardbot.db")
    conn.execute("DROP TABLE notes")
    conn.commit()
    conn.close()
    with pytest.raises(sh.Stop):
        sh.main(["compat", "--data", str(приёмник), "--archive", str(архив)])
    with pytest.raises(sh.Stop):
        _merge(приёмник, архив)
    # Отказ случился ДО первой записи: база приёмника не тронута.
    assert _count(приёмник / "hardbot.db", "clients") == 0


def test_разница_колонок_переносится_по_пересечению_и_сказана_вслух(
        источник, приёмник, tmp_path, capsys):
    conn = sqlite3.connect(приёмник / "hardbot.db")
    # На машине компании появилась колонка, которой нет на проде…
    conn.execute("ALTER TABLE clients ADD COLUMN новая_колонка TEXT")
    # …и пропала та, что на проде есть.
    conn.execute("ALTER TABLE clients DROP COLUMN tips")
    # И то же самое в таблице, которая не переносится вовсе: на вопрос «базы
    # вообще совместимы?» сверка обязана ответить целиком.
    conn.execute("ALTER TABLE app_links ADD COLUMN колонка_входа TEXT")
    conn.commit()
    conn.close()

    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert sh.main(["compat", "--data", str(приёмник),
                    "--archive", str(архив)]) == 0
    отчёт = capsys.readouterr().out
    assert "tips" in отчёт and "новая_колонка" in отчёт
    assert "ПО ПЕРЕСЕЧЕНИЮ" in отчёт
    assert "колонка_входа" in отчёт and "app_links" in отчёт

    assert _merge(приёмник, архив) == 0
    assert _count(приёмник / "hardbot.db", "clients") == 4
    conn = sqlite3.connect(приёмник / "hardbot.db")
    assert conn.execute("SELECT client FROM clients WHERE contract_id='KD00'"
                        ).fetchone()[0] == "Должник KD00"
    conn.close()


def test_совпавшие_схемы_так_и_называются(источник, приёмник, tmp_path, capsys):
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert sh.main(["compat", "--data", str(приёмник),
                    "--archive", str(архив)]) == 0
    assert "Схемы совпадают" in capsys.readouterr().out


def test_инструмент_работает_с_двумя_файлами_баз(источник, приёмник, tmp_path):
    """Без этого его нельзя ни проверить, ни применить руками — только внутри
    контейнера, то есть вслепую."""
    архив = _unpack(_take(источник, tmp_path), tmp_path / "распаковка")
    assert sh.main(["compat", "--db", str(приёмник / "hardbot.db"),
                    "--subset", str(архив / sh.DB_NAME)]) == 0
    assert sh.main(["merge", "--db", str(приёмник / "hardbot.db"),
                    "--photos", str(приёмник / "photos"),
                    "--subset", str(архив / sh.DB_NAME),
                    "--photos-from", str(архив / "photos")]) == 0
    assert _count(приёмник / "hardbot.db", "clients") == 4
    assert (приёмник / "photos" / "KD00" / "1.jpg").is_file()


def test_список_сотрудников_показывает_как_пишутся_фио(источник, capsys):
    assert sh.main(["employees", "--data", str(источник)]) == 0
    вывод = capsys.readouterr().out
    for имя in [*НАШИ, ЧУЖОЙ]:
        assert имя in вывод


# --- скрипт переезда знает про новый инструмент -------------------------------

def test_migrate_sh_умеет_новые_команды():
    """Скрипт и модуль расходятся молча: инструмент есть, а позвать его нечем.

    Проверяем ровно стык: имя файла, которое migrate.sh подаёт в контейнер, и
    три команды, которыми этот перенос делается руками."""
    текст = (ROOT / "deploy" / "units" / "core" / "migrate.sh").read_text(
        encoding="utf-8")
    assert "server/subset_handover.py" in текст
    for кусок in ("--only-employees", "compat", "--merge", "employees"):
        assert кусок in текст, кусок
    # Шапка файла — она же подсказка (usage печатает её), поэтому новые команды
    # обязаны быть описаны именно там, а не только в коде.
    шапка = текст.split("source ", 1)[0]
    assert "--only-employees" in шапка and "--merge" in шапка
