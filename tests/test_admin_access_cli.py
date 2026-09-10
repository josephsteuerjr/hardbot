"""Коды привязки выдаются НА МЕСТЕ У СЕРВЕРА, а не по сети.

Смысл команды: право впустить новое устройство привязано к доступу к серверу,
а не к знанию строки из `.env`. Поэтому HTTP-входа у неё нет — ровно как у
регистратора импорта.
"""

import admin_access
import db
import web


def _fresh():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM admin_devices")
        conn.execute("DELETE FROM admin_enroll_codes")
        conn.execute("DELETE FROM settings WHERE key=?", (db.ADMIN_BOOTSTRAP_KEY,))
        conn.commit()


def test_issue_prints_the_code_once_and_stores_only_its_hash(capsys):
    _fresh()
    assert admin_access.main(["issue", "--name", "Ноут дома"]) == 0
    printed = capsys.readouterr().out
    code = [ln.strip() for ln in printed.splitlines() if ln.startswith("    ")][0]
    assert len(code) > 20

    with db.connect() as conn:
        rows = conn.execute("SELECT code_hash FROM admin_enroll_codes").fetchall()
    assert len(rows) == 1
    assert code not in rows[0]["code_hash"], "код лежит в базе как есть"

    # Повторно узнать код нельзя — только выдать новый.
    assert admin_access.main(["codes"]) == 0
    assert code not in capsys.readouterr().out


def test_issued_code_works_exactly_once(capsys):
    _fresh()
    admin_access.main(["issue", "--name", "Ноут"])
    code = [ln.strip() for ln in capsys.readouterr().out.splitlines()
            if ln.startswith("    ")][0]

    made = db.redeem_enroll_code(code, "fp-1", "Ноут")
    assert made["token"]
    assert db.admin_device_by_token(made["token"], "fp-1")

    try:
        db.redeem_enroll_code(code, "fp-2")
        raise AssertionError("код обменялся второй раз")
    except ValueError as exc:
        assert "использован" in str(exc)


def test_revoke_kills_the_token(capsys):
    _fresh()
    made = db.enroll_admin_device("fp-1", "Потерянный")
    assert db.admin_device_by_token(made["token"], "fp-1")
    assert admin_access.main(["revoke", "--id", str(made["id"])]) == 0
    assert db.admin_device_by_token(made["token"], "fp-1") is None
    # Повторный отзыв — это не успех: иначе «отозвал» и «не нашёл» неразличимы.
    assert admin_access.main(["revoke", "--id", str(made["id"])]) == 1


def test_there_is_no_http_way_to_issue_a_code():
    """Выдача кода по сети сделала бы бессмысленной саму затею.

    Иначе тот, кто знает мастер-ключ, выписывал бы себе коды удалённо — и мы
    вернулись бы к «одна строка открывает всё», только длиннее.
    """
    app = web.build_app(None)
    paths = {getattr(r.resource, "canonical", "") for r in app.router.routes()}
    for path in paths:
        assert "issue" not in path, f"нашлась сетевая ручка выдачи кода: {path}"
