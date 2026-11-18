"""Read the courier pool and save a filtered snapshot through Excel."""

from datetime import date, datetime, timedelta
from pathlib import Path

from python_calamine import CalamineWorkbook

from .native_exports import _sha, _write

FIELDS = {
    'partner': {'партнер'},
    'number': {'id/номер к+i+a2:k305', 'номер кд / id', 'номер кд', 'номер договора', 'id/номер кд'},
    'courier': {'курьер'},
    'transferred_on': {'дата передачи курьеру'},
    'phone': {'телефон'},
}


def string(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return '' if value is None else str(value).strip()


def date_string(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and 36526 <= value <= 73415:
        return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
    raw = string(value)
    try:
        return datetime.strptime(raw, '%d.%m.%Y').date().isoformat()
    except ValueError:
        return raw


def header_columns(row):
    headers = [' '.join(string(v).casefold().replace('ё', 'е').split()) for v in row]
    positions = {key: [i for i, value in enumerate(headers) if value in aliases]
                 for key, aliases in FIELDS.items()}
    if any(len(indices) != 1 for indices in positions.values()):
        return None
    return {key: indices[0] for key, indices in positions.items()}


def read(path):
    path = Path(path)
    if path.suffix.lower() not in {'.xlsx', '.xlsb', '.xlsm'} or not path.is_file():
        raise ValueError('Выберите существующий курьерский реестр Excel.')
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Выберите книгу меньше 32 МБ.')
    fingerprint = _sha(path)
    with CalamineWorkbook.from_path(str(path)) as workbook:
        sheet = workbook.get_sheet_by_name('Sheet1') if 'Sheet1' in workbook.sheet_names else workbook.get_sheet_by_index(0)
        if sheet.end is None or sheet.end[0] > 10000 or sheet.end[1] > 150:
            raise ValueError('Проверьте используемый диапазон курьерского реестра.')
        rows = sheet.to_python(skip_empty_area=False)
        name = sheet.name
    if _sha(path) != fingerprint:
        raise ValueError('Книга изменилась при чтении. Откройте её повторно.')
    for index, row in enumerate(rows[:12]):
        columns = header_columns(row)
        if columns is not None:
            break
    else:
        raise ValueError('Не найдены однозначные заголовки договора, партнёра, курьера, даты и телефона.')
    data = []
    for line, row in enumerate(rows[index+1:], index+2):
        values = {k: row[i] if i < len(row) else '' for k, i in columns.items()}
        if not any(values[k] not in (None, '') for k in ('number', 'partner', 'courier')):
            continue
        item = {k: string(v) for k, v in values.items()}
        item['transferred_on'] = date_string(values['transferred_on'])
        data.append(dict(line=line, **item))
    if not 1 <= len(data) <= 5000:
        raise ValueError('В реестре должно быть от 1 до 5 000 строк.')
    return dict(workbook_sha256=fingerprint, sheet=name, rows=data)


def write(path, snapshot):
    path = Path(path)
    if path.suffix.lower() != '.xlsx' or path.exists():
        raise ValueError('Выберите новое имя файла XLSX.')
    headers, rows = snapshot['headers'], snapshot['rows']
    _write(path, 'Курьерские пулы', headers, rows, [19, 19, 31, 24, 18, 22, 24, 37, 26, 21, 37, 28, 21])
    return dict(rows=len(rows), sha256=_sha(path))
