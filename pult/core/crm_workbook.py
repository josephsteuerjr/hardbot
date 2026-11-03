"""Unified Portfolio workbook; copy-only native template updates and readback."""

from pathlib import Path
from contextlib import contextmanager
import datetime as dt
import gc, hashlib, json, os, shutil, uuid, zipfile

SHEET = "Портфель"
META = "_CRM"
CLEAR = "[[CLEAR]]"
IDENTITY = [
    "_crm_contract_uuid",
    "_crm_person_uuid",
    "_crm_creditor_id",
    "_crm_currency",
]


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextmanager
def excel():
    import pythoncom, win32com.client

    pythoncom.CoInitialize()
    application = None
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        application.AutomationSecurity = 3
        application.AskToUpdateLinks = False
        application.EnableEvents = False
        yield application
    finally:
        if application is not None:
            try:
                application.Quit()
            except Exception as exc:
                if getattr(exc, "hresult", None) != -2147417848:
                    raise
        application = None
        gc.collect()
        pythoncom.CoUninitialize()


def macro_hash(path):
    with zipfile.ZipFile(path) as archive:
        return {
            n: hashlib.sha256(archive.read(n)).hexdigest()
            for n in archive.namelist()
            if "vba" in n.lower() and not n.endswith("/")
        }


def restore_macro_parts(source, target):
    """Excel rewrites VBA containers when adding sheets. Preserve source bytes.

    Workbook cells and CRM sheets remain from the saved copy. Macro binaries,
    their signatures and relationships remain exactly those of the source.
    The resulting package is opened read-only in Excel by the normal readback.
    """
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(source) as incoming:
        parts = {
            name: incoming.read(name)
            for name in incoming.namelist()
            if "vba" in name.lower() and not name.endswith("/")
        }
        if not parts:
            return
        source_types = ET.fromstring(incoming.read("[Content_Types].xml"))
    stage = target.with_name(target.name + ".vba-stage")
    try:
        with zipfile.ZipFile(target) as archive, zipfile.ZipFile(
            stage, "w", zipfile.ZIP_DEFLATED
        ) as output:
            names = set(archive.namelist())
            for item in archive.infolist():
                data = parts.get(item.filename)
                if data is None:
                    data = archive.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    current = ET.fromstring(data)
                    known = {x.get("PartName") for x in current}
                    for declaration in source_types:
                        name = declaration.get("PartName", "")
                        if "vba" in name.lower() and name not in known:
                            current.append(declaration)
                    data = ET.tostring(current, encoding="utf-8", xml_declaration=True)
                output.writestr(item, data)
            for name, data in parts.items():
                if name not in names:
                    output.writestr(name, data)
        os.replace(stage, target)
    finally:
        if stage.exists():
            stage.unlink()


def _meta(snapshot, columns, headers):
    return dict(
        manifest=snapshot["manifest"],
        columns=columns,
        headers=headers,
        clear_marker=CLEAR,
        rows={
            r["contract_uuid"]: dict(
                revisions=r.get("revisions", {}), provenance=r.get("provenance", {})
            )
            for r in snapshot["rows"]
        },
    )


def _values(row, keys):
    identity = dict(
        zip(
            IDENTITY,
            [
                row["contract_uuid"],
                row["person_uuid"],
                row["creditor_id"],
                row.get("currency"),
            ],
        )
    )
    return [
        identity[key] if key in identity else row["values"].get(key) for key in keys
    ]


def _write_meta_openpyxl(book, metadata):
    if META in book:
        del book[META]
    sheet = book.create_sheet(META)
    sheet.sheet_state = "veryHidden"
    raw = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    for i in range(0, len(raw), 30000):
        sheet.append([raw[i : i + 30000]])


def _create(snapshot, temp):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    # A full general registry has millions of cells. Stream new workbooks;
    # never retain all cells or recompute max_row after every appended row.
    book = Workbook(write_only=True)
    sheet = book.create_sheet(SHEET)
    keys = [f["key"] for f in snapshot["fields"]] + IDENTITY
    headers = [f["title"] for f in snapshot["fields"]] + IDENTITY
    sheet.freeze_panes = "D4"
    sheet.auto_filter.ref = f"A2:{get_column_letter(len(keys))}{max(4,len(snapshot['rows'])+3)}"
    for column in range(len(keys) - 3, len(keys) + 1):
        sheet.column_dimensions[get_column_letter(column)].hidden = True
    def text_row(values, header=False):
        cells = []
        for value in values:
            cell = WriteOnlyCell(sheet, value=None if value is None else str(value))
            # Identifiers, exact decimals and leading =,+,-,@ remain text.
            cell.data_type = "s"
            cell.number_format = "@"
            if header:
                cell.font = Font(bold=True, color="613E30")
                cell.fill = PatternFill("solid", fgColor="F3E9DD")
            cells.append(cell)
        return cells
    sheet.append(text_row(["CRM · " + snapshot["manifest"]["period"]]))
    sheet.append(text_row(headers, header=True))
    sheet.append(text_row([f["source"] for f in snapshot["fields"]] + ["CRM"] * 4))
    for row in snapshot["rows"]:
        sheet.append(text_row(_values(row, keys)))
    metadata = _meta(
        snapshot,
        {key: i + 1 for i, key in enumerate(keys)},
        {str(i + 1): title for i, title in enumerate(headers)},
    )
    _write_meta_openpyxl(book, metadata)
    book.save(temp)


def _template(snapshot, template, temp):
    before = sha(template)
    macros = macro_hash(template)
    shutil.copy2(template, temp)
    with excel() as application:
        book = application.Workbooks.Open(str(temp), UpdateLinks=0, ReadOnly=False)
        try:
            if book.ReadOnly:
                raise ValueError("Книга занята. Закройте её и повторите.")
            try:
                sheet = book.Worksheets(SHEET)
            except Exception:
                sheet = book.Worksheets.Add()
                sheet.Name = SHEET
            last_col = int(sheet.UsedRange.Column + sheet.UsedRange.Columns.Count - 1)
            last_row = max(3, int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1))
            if last_row * last_col > 5_000_000:
                raise ValueError(
                    "Область книги слишком велика. Уберите лишнее форматирование."
                )
            # One COM read per matrix; never millions of per-cell round trips.
            used = sheet.Range(sheet.Cells(1, 1), sheet.Cells(last_row, last_col))
            original = used.Value2
            formulas = used.Formula

            def cell_value(row, column):
                return (
                    original[row - 1][column - 1]
                    if row <= len(original) and column <= len(original[0])
                    else None
                )

            header = list(original[1])
            columns = {}
            headers = {}
            definitions = snapshot["fields"]
            for field in definitions:
                hits = [
                    i + 1
                    for i, title in enumerate(header)
                    if str(title or "").strip().casefold()
                    == field["title"].strip().casefold()
                ]
                if len(hits) > 1:
                    raise ValueError("Повторяющийся заголовок: " + field["title"])
                if hits:
                    columns[field["key"]] = hits[0]
                    headers[str(hits[0])] = header[hits[0] - 1]
                else:
                    last_col += 1
                    columns[field["key"]] = last_col
                    headers[str(last_col)] = field["title"]
                    sheet.Cells(2, last_col).Value2 = field["title"]
                    sheet.Cells(3, last_col).Value2 = field["source"]
            existing_ids = {}
            if "_crm_contract_uuid" in header:
                old_col = header.index("_crm_contract_uuid") + 1
                for r in range(4, last_row + 1):
                    old_id = cell_value(r, old_col)
                    if old_id:
                        if old_id in existing_ids:
                            raise ValueError("Повтор внутреннего ID в шаблоне.")
                        existing_ids[old_id] = r
            for key in IDENTITY:
                hits = [i + 1 for i, title in enumerate(header) if title == key]
                if len(hits) > 1:
                    raise ValueError("Повтор служебной колонки CRM.")
                if hits:
                    columns[key] = hits[0]
                else:
                    last_col += 1
                    columns[key] = last_col
                headers[str(columns[key])] = key
                sheet.Cells(2, columns[key]).Value2 = key
                sheet.Columns(columns[key]).Hidden = True
                if last_row >= 4:
                    sheet.Range(
                        sheet.Cells(4, columns[key]),
                        sheet.Cells(last_row, columns[key]),
                    ).ClearContents()
            source_rows = {}
            for r in range(4, last_row + 1):
                cid = str(cell_value(r, columns["contract_id"]) or "").strip()
                partner = str(cell_value(r, columns["partner"]) or "").strip()
                if not cid:
                    continue
                pair = (cid, partner)
                source_rows.setdefault(pair, []).append(r)
            changes = {}
            preserved = {}
            for r, record in enumerate(formulas, 1):
                for c, formula in enumerate(record, 1):
                    if isinstance(formula, str) and formula.startswith("="):
                        preserved[(r, c)] = formula
            for item in snapshot["rows"]:
                pair = (
                    str(item["values"].get("contract_id") or item["external_id"]),
                    str(item["values"].get("partner") or ""),
                )
                r = existing_ids.get(item["contract_uuid"])
                if r is None:
                    matches = source_rows.get(pair, [])
                    if len(matches) > 1:
                        raise ValueError(
                            "Повтор договора и партнёра без подтверждённого внутреннего ID; нужна сверка."
                        )
                    r = matches[0] if matches else None
                if r is None:
                    last_row += 1
                    r = last_row
                keys = list(columns)
                for key, value in zip(keys, _values(item, keys)):
                    current = None if key in IDENTITY else cell_value(r, columns[key])
                    if (r, columns[key]) in preserved:
                        if str(current or "") != str(value or ""):
                            raise ValueError("Результат затрагивает формулу: " + key)
                        continue
                    if current == value:
                        continue
                    changes.setdefault(columns[key], {})[r] = (
                        None if value is None else str(value)
                    )
            for column, updates in changes.items():
                ordered = sorted(updates)
                offset = 0
                while offset < len(ordered):
                    end = offset + 1
                    while end < len(ordered) and ordered[end] == ordered[end - 1] + 1:
                        end += 1
                    block = ordered[offset:end]
                    target = sheet.Range(
                        sheet.Cells(block[0], column), sheet.Cells(block[-1], column)
                    )
                    target.NumberFormat = "@"
                    target.Value2 = tuple((updates[row],) for row in block)
                    offset = end
            try:
                meta = book.Worksheets(META)
                meta.Cells.ClearContents()
            except Exception:
                meta = book.Worksheets.Add()
                meta.Name = META
            raw = json.dumps(
                _meta(snapshot, columns, headers),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for i in range(0, len(raw), 30000):
                meta.Cells(i // 30000 + 1, 1).Value2 = raw[i : i + 30000]
            meta.Visible = 2
            sheet.Activate()
            book.Save()
            checked = sheet.Range(
                sheet.Cells(1, 1), sheet.Cells(last_row, last_col)
            ).Formula
            for (r, c), formula in preserved.items():
                if checked[r - 1][c - 1] != formula:
                    raise ValueError("Изменена формула шаблона.")
        finally:
            book.Close(SaveChanges=False)
            book = sheet = meta = target = used = application = None
    if sha(template) != before:
        raise ValueError("Источник изменился при подготовке копии.")
    if macro_hash(temp) != macros:
        restore_macro_parts(template, temp)
    if macro_hash(temp) != macros:
        raise ValueError("Источник или VBA изменились при подготовке копии.")


def read(path):
    path = Path(path).resolve()
    before = sha(path)
    if path.suffix.lower() not in {".xlsx", ".xlsm", ".xlsb"}:
        raise ValueError("Поддерживаются XLSX, XLSM и XLSB.")
    with excel() as application:
        book = application.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
        try:
            sheet = book.Worksheets(SHEET)
            try:
                meta = book.Worksheets(META)
            except Exception as exc:
                raise ValueError(
                    "Нет манифеста CRM и исходной версии. Сначала создайте версионированную копию реестра."
                ) from exc
            raw = "".join(
                str(meta.Cells(r, 1).Value2 or "")
                for r in range(1, meta.UsedRange.Rows.Count + 1)
            )
            metadata = json.loads(raw)
            if metadata["manifest"]["schema_version"] != 1:
                raise ValueError("Неизвестная версия CRM.")
            columns = metadata["columns"]
            last = max(3, int(sheet.UsedRange.Row + sheet.UsedRange.Rows.Count - 1))
            last_col = max(columns.values())
            if last * last_col > 5_000_000:
                raise ValueError("Область книги слишком велика.")
            matrix = sheet.Range(sheet.Cells(1, 1), sheet.Cells(last, last_col)).Value2
            for col, title in metadata["headers"].items():
                if matrix[1][int(col) - 1] != title:
                    raise ValueError(
                        "Изменён порядок или заголовок колонок. Нужна новая сверка сопоставления."
                    )
            rows = []
            seen = set()
            for r in range(4, last + 1):
                cid = matrix[r - 1][columns["_crm_contract_uuid"] - 1]
                if not cid:
                    continue  # Retained unrelated template rows are outside this export.
                if cid in seen:
                    raise ValueError("В книге повторён внутренний ID договора.")
                seen.add(cid)
                values = {}
                clear = []
                for key, col in columns.items():
                    if key in IDENTITY:
                        continue
                    value = matrix[r - 1][col - 1]
                    if value == CLEAR:
                        value = None
                        clear.append(key)
                    elif isinstance(value, (int, float)):
                        value = format(value, ".15g")
                    values[key] = value
                rows.append(dict(contract_uuid=cid, values=values, clear=clear))
        finally:
            book.Close(SaveChanges=False)
            book = sheet = meta = application = None
    if sha(path) != before:
        raise ValueError("Файл изменился во время чтения.")
    manifest = metadata["manifest"]
    return dict(
        export_id=manifest["export_id"],
        manifest_hash=manifest["manifest_hash"],
        schema_version=1,
        file_sha256=before,
        rows=rows,
    )


def resume(snapshot, path):
    """Recover an interrupted write only after exact native readback."""
    path = Path(path).resolve()
    verified = read(path)
    if (
        verified["export_id"] != snapshot["manifest"]["export_id"]
        or verified["manifest_hash"] != snapshot["manifest"]["manifest_hash"]
    ):
        raise ValueError("Файл принадлежит другой версии выгрузки.")
    actual = {r["contract_uuid"]: r["values"] for r in verified["rows"]}
    for row in snapshot["rows"]:
        if row["contract_uuid"] not in actual:
            raise ValueError("В книге отсутствует договор.")
        for key, value in row["values"].items():
            read_value = actual[row["contract_uuid"]].get(key)
            if value in (None, "") and read_value in (None, ""):
                continue
            if read_value != value:
                raise ValueError("Файл изменён после выгрузки: " + key)
    return dict(path=str(path), sha256=verified["file_sha256"], readback=verified)


def write(snapshot, path, template=None):
    path = Path(path).resolve()
    template = Path(template).resolve() if template else None
    if path.exists():
        raise ValueError("Выберите новое имя: существующий файл сохраняется.")
    suffix = template.suffix.lower() if template else ".xlsx"
    if path.suffix.lower() != suffix or suffix not in {".xlsx", ".xlsm", ".xlsb"}:
        raise ValueError("Формат копии должен совпадать с шаблоном.")
    if not path.parent.is_dir():
        raise ValueError("Каталог назначения не найден.")
    temp = path.with_name(path.stem + ".crm-" + str(uuid.uuid4()) + suffix)
    try:
        if template:
            _template(snapshot, template, temp)
        else:
            _create(snapshot, temp)
        verified = read(temp)
        actual = {r["contract_uuid"]: r["values"] for r in verified["rows"]}
        for row in snapshot["rows"]:
            if row["contract_uuid"] not in actual:
                raise ValueError("В записанной книге отсутствует договор.")
            for key, value in row["values"].items():
                if value in (None, "") and actual[row["contract_uuid"]].get(key) in (
                    None,
                    "",
                ):
                    continue
                if actual[row["contract_uuid"]].get(key) != value:
                    raise ValueError("Не совпадает чтение записанного поля: " + key)
        if path.exists():
            raise ValueError("Имя занято другим файлом во время записи.")
        os.rename(temp, path)
        verified["file_sha256"] = sha(path)
        return dict(path=str(path), sha256=verified["file_sha256"], readback=verified)
    finally:
        if temp.exists() and temp.parent == path.parent and ".crm-" in temp.name:
            temp.unlink()
