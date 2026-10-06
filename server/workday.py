"""Calendar dates of work in the offices, independent of the server's UTC."""
from datetime import date, datetime
from evidence import FIELD_TZ


def today() -> date:
    return datetime.now(FIELD_TZ).date()
