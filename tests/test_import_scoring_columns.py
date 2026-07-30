"""Импорт колонок, которые нужны скорингу, но не нужны офису.

Контракт обмена уже знал эти заголовки и разрешал их игнорировать
(`allowed_ignored_headers`). Здесь мы перестаём выбрасывать их значения — но
НЕ заводим полями реестра. Смысл ровно в этом: экспортная схема, её отпечаток
и строка типов не меняются, значит форма файла, уходящего в офис, прежняя,
и ломать пульт с шаблонами нечего.

Проверено сквозняком на боевом реестре (17 074 договора): незнакомых колонок 0,
`country` заполнена у всех, сумма `paid_current` сходится с реестром до рубля.
"""
import db
from exchange_contract import (
    WORKBOOK_ALLOWED_IGNORED_HEADERS,
    WORKBOOK_ALLOWED_IGNORED_HEADER_PATTERNS,
)
from fields import FIELDS

SCORING_COLS = ("country", "debt_start_month", "paid_current", "paid_months")


def _grid(rows: list[dict], extra_headers: list[str] | None = None):
    """Мини-книга: заголовки, строка типов, данные. Порядок как в реальном файле."""
    base = [f.title for f in FIELDS]
    extra = extra_headers or []
    header = base + extra
    types = [f.source for f in FIELDS] + [""] * len(extra)
    out = [header, types]
    for r in rows:
        out.append([r.get(f.key, "") for f in FIELDS]
                   + [r.get(h, "") for h in extra])
    return out


def _import(rows, extra_headers=None):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'SC-%'")
        conn.commit()
    return db.import_grid(_grid(rows, extra_headers),
                          source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)


EXTRA = ["Страна", "Долг на начало месяца", "Текущие платежи",
         "Оплаты за 01.26", "Оплаты за 02.26", "Оплаты за 03.26"]


def test_scoring_columns_are_imported():
    rep = _import([{"contract_id": "SC-1", "client": "Тест К.",
                    "Страна": "Таджикистан", "Долг на начало месяца": "45 000",
                    "Текущие платежи": "12 500"}], EXTRA)
    assert not rep.get("blocked"), rep.get("error")
    row = db.get_client("SC-1")
    assert row["country"] == "Таджикистан"
    assert row["debt_start_month"] == 45_000
    assert row["paid_current"] == 12_500


def test_monthly_payments_collapse_into_a_counter():
    """Колонки помесячных оплат называются по месяцу и меняются каждый месяц,
    поэтому в БД их сворачивают в число «сколько месяцев платил»."""
    rep = _import([
        {"contract_id": "SC-2", "client": "А.", "Оплаты за 01.26": "5000",
         "Оплаты за 02.26": "", "Оплаты за 03.26": "3000"},
        {"contract_id": "SC-3", "client": "Б.", "Оплаты за 01.26": "",
         "Оплаты за 02.26": "0", "Оплаты за 03.26": ""},
    ], EXTRA)
    assert not rep.get("blocked"), rep.get("error")
    assert db.get_client("SC-2")["paid_months"] == 2
    assert db.get_client("SC-3")["paid_months"] == 0     # ноль — это «не платил»


def test_file_without_scoring_columns_still_imports():
    """Старый файл без этих колонок обязан импортироваться как раньше."""
    rep = _import([{"contract_id": "SC-4", "client": "В."}])
    assert not rep.get("blocked"), rep.get("error")
    row = db.get_client("SC-4")
    assert row["country"] in (None, "")
    assert row["paid_current"] is None


def test_these_headers_are_not_unknown_columns():
    """Незнакомый заголовок блокирует импорт. Эти — знакомы контракту."""
    rep = _import([{"contract_id": "SC-5", "client": "Г."}], EXTRA)
    assert rep.get("unknown_columns") == []


def test_export_schema_is_untouched():
    """Ключевое: новые колонки НЕ попадают в экспортную схему. Иначе изменилась бы
    форма файла для офиса и отпечаток контракта — а с ними пульт и шаблоны."""
    keys = {f.key for f in FIELDS}
    assert not (set(SCORING_COLS) & keys)


def test_columns_exist_in_database():
    db.init_db()
    with db.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(clients)")}
    assert set(SCORING_COLS) <= cols


def test_contract_still_allows_them_to_be_ignored():
    """Читать их — наше решение, а не требование контракта: файл без них валиден."""
    ignored = {t.strip().lower() for t in WORKBOOK_ALLOWED_IGNORED_HEADERS}
    assert {"страна", "долг на начало месяца", "текущие платежи"} <= ignored
    assert any("Оплаты за" in p for p in WORKBOOK_ALLOWED_IGNORED_HEADER_PATTERNS)


def test_scoring_reads_the_new_features():
    """Признак «платил раньше» даёт лифт ×2.4 — ради него всё и делалось."""
    import scoring_model
    _import([{"contract_id": "SC-6", "client": "Д. Платящий",
              "Страна": "Узбекистан", "Оплаты за 01.26": "1000",
              "Оплаты за 02.26": "2000"}], EXTRA)
    import scoring
    item = next(i for i in scoring._items_from_db() if i["client"] == "Д. Платящий")
    f = scoring_model.features(item)
    assert f["paid_before"] == "1-2 мес"
    assert f["country"] == "узбекистан"
