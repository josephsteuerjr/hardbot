import copy, uuid
import pytest
from test_crm_core import crm_world, call_body
from crm import exchange, store
from pult.core import crm_workbook as books


@pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="Native Excel integration"
)
def test_native_workbook_export_manual_edit_three_way_import(crm_world, tmp_path):
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
    with books.excel() as excel:
        book=excel.Workbooks.Open(str(first))
        sheet=book.Worksheets.Add();sheet.Name='Witness'
        sheet.Range('A1').Formula='=SUM(2,3)'
        sheet.Range('B2').Value='Unknown column data'
        book.Save();book.Close(False)
    original = books.sha(first)
    second = tmp_path / "second.xlsx"
    books.write(snapshot, second, first)
    assert books.sha(first) == original
    with books.excel() as excel:
        book=excel.Workbooks.Open(str(second))
        assert book.Worksheets('Witness').Range('A1').Formula=='=SUM(2,3)'
        assert book.Worksheets('Witness').Range('B2').Value=='Unknown column data'
        sheet=book.Worksheets(books.SHEET)
        headers=list(sheet.Range(sheet.Cells(2,1),sheet.Cells(2,sheet.UsedRange.Columns.Count)).Value[0])
        col=headers.index(next(f['title'] for f in snapshot['fields'] if f['key']=='soft_comment'))+1
        sheet.Cells(4,col).Value='Manual native workbook'
        book.Save();book.Close(False)
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
    w = crm_world
    store.command(w["member"], call_body(w))
    snapshot = exchange.export(
        w["owner"],
        dict(operation_id=str(uuid.uuid4()), period="2026-11", target_id="general"),
    )
    first = tmp_path / "origin.xlsx"
    books.write(snapshot, first)
    template = tmp_path / ("template" + suffix)
    with books.excel() as excel:
        book = excel.Workbooks.Open(str(first))
        book.Worksheets.Add().Name = "Witness"
        book.Worksheets("Witness").Range("A1").Formula = "=SUM(2,3)"
        book.SaveAs(str(template), FileFormat=format_id)
        book.Close(False)
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
