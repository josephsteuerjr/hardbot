"""
Книга Excel с НАСТОЯЩИМИ типами ячеек — тонкий писатель поверх xlsxwriter.

Зачем отдельный слой. Книги руководителей до 24.09.2026 собирались openpyxl
из тех же строк, что показывает экран, а экран ест текст: дата выезда лежала
строкой «2026-09-22», время на карточке — «4 мин», отметки — «11.09 14:32»,
а ноль — пустой строкой. Такую колонку Excel не сортирует по времени, не
складывает и не группирует в сводной, хотя книгу заказывали именно «для
самостоятельного анализа». Здесь каждый вид колонки пишется своим типом:
дата — датой, деньги — числом с валютой в формате, доля — долей с «%».

Слой ничего не знает про выезды и сотрудников: ему дают значение и вид
колонки, он выбирает тип ячейки и формат. Правила «что во что» живут в одном
месте и не расползаются по трём листам и двум книгам.

Виды колонки:
  text      — строка; число тоже пишется строкой (номер договора с ведущим
              нулём не должен превратиться в число и потерять ноль);
  int, num  — число; ноль пишется НУЛЁМ, пустая ячейка значит «нет данных»;
  money     — число с валютой в формате ячейки; значение — число, пара
              (число, валюта) или текст, когда валюты смешаны и числа нет;
  pct, pct0 — доля (0.25), формат «0.0%» / «0%»; значение не округляется;
  date      — дата (date, datetime или ISO-строка) с форматом «дд.мм.гггг»;
  datetime  — момент (ISO-строка «2026-09-22T18:40:00»);
  time      — время суток («14:02»);
  duration  — длительность в СЕКУНДАХ → доля суток с форматом «[h]:mm:ss».

Нечитаемое значение для даты или времени пишется как есть, строкой: потерять
его молча хуже, чем показать некрасиво.
"""

from __future__ import annotations

from datetime import date, datetime, time
from io import BytesIO

import xlsxwriter

DATE_FMT = "dd.mm.yyyy"
DATETIME_FMT = "dd.mm.yyyy hh:mm"
TIME_FMT = "hh:mm"
DURATION_FMT = "[h]:mm:ss"
# Ноль прячется ФОРМАТОМ, а не пустой ячейкой: сетка исходов — это десятки
# колонок, где у каждого человека заполнены две-три, и нули в остальных
# забивают глаз. Но ячейка остаётся числом — SUM и сводная её видят.
HIDE_ZERO_FMT = "#,##0;-#,##0;;@"

KINDS = ("text", "int", "num", "money", "pct", "pct0", "date", "datetime",
         "time", "duration")


def money_fmt(unit: str, hide_zero: bool = False) -> str:
    """Формат денег: «47 199 614 ₽» — валюта в формате, а не внутри значения
    (по тексту «1 508 299 ₽» SUM молча даёт ноль)."""
    base = f'#,##0" {unit}"' if unit else "#,##0"
    if not hide_zero:
        return base
    return f"{base};-{base};;@"


def as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text) if len(text) == 10 else None
    except ValueError:
        return None


def as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value or "").strip()
    if len(text) < 16:
        return None
    try:
        # «Z» и смещение отрезаем: книга показывает момент так, как он записан
        # в базе, а часовой пояс Excel хранить не умеет.
        return datetime.fromisoformat(text[:19].replace(" ", "T"))
    except ValueError:
        return None


def as_time(value) -> time | None:
    if isinstance(value, time):
        return value
    text = str(value or "").strip()
    try:
        h, m = text.split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


class Book:
    """Одна книга: листы, кэш форматов и байты на выходе."""

    def __init__(self) -> None:
        self._buf = BytesIO()
        # strings_to_*: False — строка остаётся строкой. Иначе «007» стало бы
        # числом 7, а «=…» в комментарии выездника — формулой.
        self.wb = xlsxwriter.Workbook(self._buf, {
            "in_memory": True,
            "strings_to_numbers": False,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        })
        self._formats: dict[tuple, object] = {}

    def fmt(self, **props):
        """Формат по набору свойств, из кэша: у xlsxwriter формат — объект
        книги, и на каждую ячейку заводить новый значило бы раздуть файл."""
        props = {k: v for k, v in props.items() if v not in (None, False, "")}
        key = tuple(sorted(props.items()))
        if key not in self._formats:
            self._formats[key] = self.wb.add_format(dict(props))
        return self._formats[key]

    def sheet(self, title: str) -> "Sheet":
        return Sheet(self, self.wb.add_worksheet(title))

    def close(self) -> bytes:
        self.wb.close()
        return self._buf.getvalue()


class Sheet:
    """Лист с построчной записью. row — номер следующей строки (с нуля)."""

    def __init__(self, book: Book, ws) -> None:
        self.book, self.ws, self.row = book, ws, 0

    # --- строки ------------------------------------------------------------
    def title(self, text: str, *, italic: bool = False, size: int | None = None,
              color: str | None = None) -> int:
        """Строка подписи в колонке A (заголовок листа, оговорка)."""
        at = self.row
        style = {"bold": not italic, "italic": italic, "font_size": size,
                 "font_color": color}
        if text:
            self.ws.write_string(at, 0, str(text), self.book.fmt(**style))
        self.row += 1
        return at

    def skip(self, n: int = 1) -> None:
        self.row += n

    def head(self, titles, *, fills=None) -> int:
        """Строка заголовков: жирно, с переносом. fills — {колонка: цвет}."""
        at = self.row
        fills = fills or {}
        for c, text in enumerate(titles):
            f = self.book.fmt(bold=True, text_wrap=True, valign="vcenter",
                              bg_color=fills.get(c))
            if text in (None, ""):
                self.ws.write_blank(at, c, None, f)
            else:
                self.ws.write_string(at, c, str(text), f)
        self.row += 1
        return at

    def blocks(self, width: int, spans) -> int:
        """Строка блоков над колонками: spans — [(заголовок, первая, последняя,
        цвет)] с номерами колонок С НУЛЯ; блок сливается в одну ячейку."""
        at = self.row
        for title, first, last, color in spans:
            f = self.book.fmt(bold=True, align="center", bg_color=color)
            if last > first:
                self.ws.merge_range(at, first, at, last, title, f)
            else:
                self.ws.write_string(at, first, title, f)
        self.row += 1
        return at

    def append(self, values, kinds, *, bold: bool = False, fills=None,
               units=None, hide_zero=None) -> int:
        """Строка данных. kinds — вид каждой колонки (см. KINDS), units —
        {колонка: валюта} для денег, fills — {колонка: цвет}, hide_zero —
        множество колонок, где ноль прячется форматом."""
        at = self.row
        fills, units, hide_zero = fills or {}, units or {}, hide_zero or ()
        for c, value in enumerate(values):
            kind = kinds[c] if c < len(kinds) else "text"
            self.put(at, c, value, kind, bold=bold, fill=fills.get(c),
                     unit=units.get(c, ""), hide_zero=c in hide_zero)
        self.row += 1
        return at

    # --- ячейка ------------------------------------------------------------
    def put(self, r: int, c: int, value, kind: str = "text", *, bold=False,
            fill=None, unit: str = "", hide_zero: bool = False) -> None:
        ws, style = self.ws, {"bold": bold, "bg_color": fill}

        def number(num, fmt=None):
            ws.write_number(r, c, num, self.book.fmt(num_format=fmt, **style))

        def blank(fmt=None):
            f = self.book.fmt(num_format=fmt, **style)
            ws.write_blank(r, c, None, f)

        def text(val):
            ws.write_string(r, c, str(val), self.book.fmt(**style))

        if kind == "money":
            if isinstance(value, tuple):
                value, unit = value
            fmt = money_fmt(unit or "", hide_zero)
            if value is None or value == "":
                return blank(fmt)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return text(value)      # смешанные валюты: честный текст
            return number(value, fmt)
        if value is None or (isinstance(value, str) and not value.strip()):
            return blank({"pct": "0.0%", "pct0": "0%", "date": DATE_FMT,
                          "datetime": DATETIME_FMT, "time": TIME_FMT,
                          "duration": DURATION_FMT}.get(kind))
        if kind in ("int", "num"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return text(value)
            return number(value, HIDE_ZERO_FMT if hide_zero else None)
        if kind in ("pct", "pct0"):
            if not isinstance(value, (int, float)):
                return text(value)
            return number(value, "0%" if kind == "pct0" else "0.0%")
        if kind == "date":
            d = as_date(value)
            if d is None:
                return text(value)
            return ws.write_datetime(r, c, d, self.book.fmt(num_format=DATE_FMT,
                                                            **style))
        if kind == "datetime":
            d = as_datetime(value)
            if d is None:
                return text(value)
            return ws.write_datetime(r, c, d,
                                     self.book.fmt(num_format=DATETIME_FMT, **style))
        if kind == "time":
            t = as_time(value)
            if t is None:
                return text(value)
            return ws.write_datetime(r, c, t, self.book.fmt(num_format=TIME_FMT,
                                                            **style))
        if kind == "duration":
            if not isinstance(value, (int, float)) or value < 0:
                return text(value)
            return number(value / 86400, DURATION_FMT)
        return text(value)

    # --- оформление ----------------------------------------------------------
    def widths(self, widths) -> None:
        for c, w in enumerate(widths):
            self.ws.set_column(c, c, w)

    def freeze(self, cell: str) -> None:
        """«C5» — как у openpyxl: закреплено всё выше и левее ячейки."""
        self.ws.freeze_panes(cell)

    def autofilter(self, head_row: int, last_row: int, ncols: int) -> None:
        """Фильтр от строки заголовков до последней строки ДАННЫХ (с нуля).
        Строку «Итого» в диапазон не берут, иначе она уедет вместе с фильтром."""
        if last_row > head_row:
            self.ws.autofilter(head_row, 0, last_row, ncols - 1)
