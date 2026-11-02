"""Conservative arbitration for the legacy collector during CRM migration.

No role priority, file-order priority or inferred intraday ordering. Callers
must validate contract identity and current assignment before adding a value.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import math


class CollectionConflict(ValueError):
    pass


def day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if math.isfinite(value) and 1 <= value <= 2958465:
            return date(1899, 12, 30) + timedelta(days=int(value))
        return None
    for pattern in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(str(value).strip(), pattern).date()
        except ValueError:
            pass
    return None


def comparable(value):
    if value is None or str(value).strip().casefold() in ('', 'nan'):
        return ('empty', '')
    if isinstance(value, (datetime, date)):
        return ('date', day(value))
    if isinstance(value, bool):
        return ('bool', value)
    text = str(value).strip()
    try:
        number = Decimal(text.replace('\u00a0', '').replace(' ', '').replace(',', '.'))
        if number.is_finite():
            return ('number', number)
    except InvalidOperation:
        pass
    return ('text', text.replace('\r\n', '\n'))


def choose(previous, incoming, previous_day, incoming_day):
    """Select a value only when equality or strict calendar order proves it.

    None is an unknown base, not an instruction to clear. Explicit clearing
    needs the versioned three-way path, which the legacy collector lacks.
    """
    if comparable(incoming)[0] == 'empty':
        return False
    if comparable(previous)[0] == 'empty' and not day(previous_day):
        return True
    if comparable(previous) == comparable(incoming):
        return bool(day(incoming_day) and (not day(previous_day) or day(incoming_day) > day(previous_day)))
    a, b = day(previous_day), day(incoming_day)
    if a is not None and b is not None and a != b:
        return b > a
    raise CollectionConflict('Разные значения без доказанного порядка правок: требуется сверка до публикации')
