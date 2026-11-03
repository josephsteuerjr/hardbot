import copy, uuid
import pytest
from test_crm_core import crm_world, call_body
from crm import exchange, store
from pult.core import crm_workbook as books


@pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="Native Excel integration"
)
def test_native_workbook_export_manual_edit_three_way_import(crm_world, tmp_path):
    from openpyxl import load_workbook

    w = crm_world
    store.command(w["member"], call_body(w))
    snapshot = exchange.export(
        w["owner"],
        dict(operation_id=str(uuid.uuid4()), period="2026-11", target_id="general"),
    )
    for row in snapshot["rows"]:
        row["values"].update(
            {f["key"]: row["values"].get(f["key"]) for f in snapshot["fields"]}
        )
    first = tmp_path / "first.xlsx"
    written = books.write(snapshot, first)
    assert written["sha256"] == books.sha(first)
    book = load_workbook(first)
    book.create_sheet("Witness")["A1"] = "=SUM(2,3)"
    book["Witness"]["B2"] = "Unknown column data"
    book.save(first)
    original = books.sha(first)
    second = tmp_path / "second.xlsx"
    books.write(snapshot, second, first)
    assert books.sha(first) == original
    book = load_workbook(second)
    assert (
        book["Witness"]["A1"].value == "=SUM(2,3)"
        and book["Witness"]["B2"].value == "Unknown column data"
    )
    headers = [c.value for c in book[books.SHEET][2]]
    col = (
        headers.index(
            next(f["title"] for f in snapshot["fields"] if f["key"] == "soft_comment")
        )
        + 1
    )
    book[books.SHEET].cell(4, col, "Manual native workbook")
    book.save(second)
    parsed = books.read(second)
    preview = exchange.preview(w["owner"], parsed)
    assert len(preview["changes"]) == 1 and not preview["conflicts"]
    result = exchange.apply(
        w["owner"],
        dict(
            operation_id=str(uuid.uuid4()),
            preview_id=preview["preview_id"],
            file_sha256=parsed["file_sha256"],
            resolutions={},
        ),
    )
    assert result["changed_rows"] == 1
    history = store.card(w["owner"], w["person"])["history"]
    assert len(history) == 2 and history[0]["origin"] == "excel"


@pytest.mark.parametrize("suffix,format_id", [(".xlsb", 50), (".xlsm", 52)])
def test_native_binary_and_macro_container_copy_recovery(
    crm_world, tmp_path, suffix, format_id
):
    import pythoncom, win32com.client, gc

    w = crm_world
    store.command(w["member"], call_body(w))
    snapshot = exchange.export(
        w["owner"],
        dict(operation_id=str(uuid.uuid4()), period="2026-11", target_id="general"),
    )
    first = tmp_path / "origin.xlsx"
    books.write(snapshot, first)
    template = tmp_path / ("template" + suffix)
    pythoncom.CoInitialize()
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    book = None
    try:
        book = excel.Workbooks.Open(str(first))
        book.Worksheets.Add().Name = "Witness"
        book.Worksheets("Witness").Range("A1").Formula = "=SUM(2,3)"
        book.SaveAs(str(template), FileFormat=format_id)
        book.Close(False)
        book = None
    finally:
        if book is not None:
            book.Close(False)
        excel.Quit()
        excel = None
        gc.collect()
        pythoncom.CoUninitialize()
    before = books.sha(template)
    target = tmp_path / ("result" + suffix)
    written = books.write(snapshot, target, template)
    assert (
        books.sha(template) == before
        and books.resume(snapshot, target)["sha256"] == written["sha256"]
    )
    with pytest.raises(ValueError, match="новое имя"):
        books.write(snapshot, target, template)
    changed = copy.deepcopy(snapshot)
    changed["rows"][0]["values"]["soft_comment"] = "Not the same snapshot"
    with pytest.raises(ValueError, match="изменён"):
        books.resume(changed, target)


def test_recovery_refuses_other_export_identity(crm_world, tmp_path):
    w = crm_world
    snapshot = exchange.export(
        w["owner"],
        dict(operation_id=str(uuid.uuid4()), period="2026-11", target_id="general"),
    )
    path = tmp_path / "original.xlsx"
    books.write(snapshot, path)
    other = copy.deepcopy(snapshot)
    other["manifest"]["export_id"] = str(uuid.uuid4())
    with pytest.raises(ValueError, match="другой версии"):
        books.resume(other, path)
