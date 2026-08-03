"""
Контур · Выезды — десктоп-пульт аналитика (Python/Tkinter).

Рабочий пульт проекта: дизайн «Контур» (слоновая кость + графит), обычный
Python без отдельного desktop-runtime. High-DPI: процесс объявляет
DPI-awareness, размеры считаются от реального DPI монитора.

На период ограниченного теста обычные Tk write-paths обогащения отключены:
запись в личные реестры выполняется только доказуемым evidence-run либо
самостоятельным ночным registry_cycle. Предпросмотр соответствий остаётся
read-only. «Реестр целиком» остаётся слепком БД для сверки.

Запуск: python pult.py  (или «Пульт.bat» — pythonw, без консоли).
Зависимости: python-calamine (чтение реестров), openpyxl (экспорт xlsx),
xlwings + установленный Excel (обогащение и разброс .xlsb).
"""

from __future__ import annotations

import json
import re
import ssl
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

import enrich
import razbros

APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "настройки пульта.json"

# Актуальный инстанс — hardbot2 (свежий код сервера). Ключ — X-Admin-Key.
DEFAULT_URL = "https://hardbot2.166.88.185.90.nip.io"
DEFAULT_KEY = ""
IMPORT_SOURCE_PUBLISHED_MASTER = enrich.IMPORT_SOURCE_PUBLISHED_MASTER
IMPORT_SOURCE_TEST_SUBSET = "test_subset"
# Возвращено владельцем 03.08.2026. Запись в личные книги — это штатная работа
# пульта (и в политике обмена она названа единственным штатным способом перенести
# доказанные визиты бота вниз). На период первого live-baseline её выключали, чтобы
# ничто не писало в книги мимо registry_cycle; baseline опубликован 29.07.
# Что НЕ вернулось: сброс базы из пульта. Он обходит snapshot/export/clearance,
# и это не про удобство, а про доказуемость очистки перед тестом на серверах компании.
LIMITED_TEST_ENRICH_WRITES_ENABLED = True
LIMITED_TEST_MASTER_IMPORT_UI_ENABLED = True
LIMITED_TEST_RESET_UI_ENABLED = False
LIMITED_TEST_ENRICH_DISABLED_MESSAGE = (
    "Запись в личные реестры из Tk-пульта отключена. Разрешены только "
    "evidence-run канонического registry_cycle и самостоятельный ночной "
    "registry_cycle. Здесь доступен только предпросмотр."
)
LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE = (
    "Прямой импорт master из Tk-пульта отключён: сервер требует отдельную "
    "root-регистрацию exact proof. Используйте двухфазный "
    "limited_test_import.py: prepare-published-master → register → apply. "
    "Ночной registry_cycle пульт не запускает."
)
LIMITED_TEST_RESET_DISABLED_MESSAGE = (
    "Сброс через Tk-пульт отключён на период ограниченного теста: он обходит "
    "обязательные snapshot, export и registry clearance. Используйте только "
    "evidence-bound limited-test reset из hardbot/DEPLOY.md."
)

# --- Палитра пульта -----------------------------------------------------------
BG = "#FCFBF8"        # фон
SIDE = "#F6F4EE"      # сайдбар
CARD = "#FFFFFF"      # карточка
ALT = "#F5F3EC"       # вторичная заливка
BORDER = "#E9E5DB"    # тёплая рамка
TEXT = "#2B2825"      # чернила
MUTED = "#6E6961"
ACCENT = "#36332E"    # графит
ACCENT_H = "#4A463F"
SUCCESS = "#5B8A3C"
DANGER = "#B0472C"
WARN = "#A66B1F"

FONT = "Segoe UI"


def f(size: int, bold: bool = False) -> tuple:
    return (FONT, size, "bold") if bold else (FONT, size)


def fmt_n(n) -> str:
    """1234567 → «1 234 567» (как toLocaleString ru-RU)."""
    if n is None:
        return "—"
    try:
        return f"{int(float(n)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def parse_num(v):
    """«10 000 000» / «12 400,50» / 100900 → float | None."""
    if isinstance(v, (int, float)):
        return float(v)
    s = "".join(ch for ch in str(v or "") if not ch.isspace()).replace(",", ".")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def to_iso(s: str) -> str:
    """Дата из поля периода: ISO или дд.мм.гггг → ISO; пусто → пусто."""
    s = (s or "").strip()
    if not s:
        return ""
    d = enrich.parse_date(s)
    if not d:
        raise ValueError(f"не дата: «{s}» (нужно ГГГГ-ММ-ДД или ДД.ММ.ГГГГ)")
    return d.isoformat()


# --- Чтение книги Excel → грид листов (зеркало Rust parse_workbook) ----------
def _cell_json(v):
    """Значение calamine → JSON-значение как у Rust cell_to_json: пустые → None,
    целые float → int, даты → ISO (полночь режем до даты)."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str):
        t = v.strip()
        return t or None
    if isinstance(v, float):
        return int(v) if v.is_integer() and abs(v) < 9e15 else v
    if isinstance(v, datetime):
        s = v.isoformat(sep=" ", timespec="seconds")
        return s[:-9] if s.endswith(" 00:00:00") else s
    if isinstance(v, date):
        return v.isoformat()
    return v if isinstance(v, (int, float, bool)) else str(v)


def read_workbook_sheets(path: str) -> list[dict]:
    """Файл реестра → [{name, grid}] для POST /admin/import.json. Парсится
    локально (python-calamine: .xlsx/.xlsm/.xlsb/.xls), на сервер летит JSON."""
    from python_calamine import CalamineWorkbook

    wb = CalamineWorkbook.from_path(path)
    sheets = []
    for name in wb.sheet_names:
        rows = wb.get_sheet_by_name(name).to_python()
        grid = [[_cell_json(c) for c in row] for row in rows]
        if grid:
            sheets.append({"name": name, "grid": grid})
    if not sheets:
        raise ValueError("в файле нет читаемых листов")
    return sheets


# --- Экспорт xlsx (порт rust_xlsxwriter-логики на openpyxl) ------------------
def _col_width(key: str, title: str) -> float:
    if key in ("address", "visit_comment"):
        return 42
    if key in ("client", "address_customer", "extra_address", "point_address"):
        return 28
    if key in ("responsible", "point_name", "call_comment"):
        return 22
    if key.endswith("_date"):
        return 12
    return min(max(len(title) * 1.1, 9), 20)


def write_registry_xlsx(path: str, fields: list[dict], rows: list[dict]) -> None:
    """Слепок БД в новый xlsx: 3 строки шапки (блоки объединены), даты и суммы —
    типизированные, автофильтр, закреплённая шапка."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Портфели"

    bold = Font(bold=True)
    italic = Font(italic=True, color="8A8578")
    center = Alignment(horizontal="center")

    # строка 1 — шапки блоков (merge по колонкам блока)
    start = 0
    for i in range(1, len(fields) + 1):
        if i == len(fields) or fields[i]["block"] != fields[start]["block"]:
            c = ws.cell(row=1, column=start + 1, value=fields[start]["block"])
            c.font, c.alignment = bold, center
            if i - 1 > start:
                ws.merge_cells(start_row=1, start_column=start + 1,
                               end_row=1, end_column=i)
            start = i
    # строка 2 — заголовки, строка 3 — сх/хард, ширины
    for j, fld in enumerate(fields, start=1):
        ws.cell(row=2, column=j, value=fld["title"]).font = bold
        ws.cell(row=3, column=j, value=fld["source"]).font = italic
        ws.column_dimensions[get_column_letter(j)].width = _col_width(fld["key"], fld["title"])
    # данные со строки 4
    for r, row in enumerate(rows, start=4):
        for j, fld in enumerate(fields, start=1):
            v = row.get(fld["key"])
            if v in (None, ""):
                continue
            if fld.get("date"):
                d = enrich.parse_date(v)
                if d:
                    c = ws.cell(row=r, column=j, value=d)
                    c.number_format = "DD.MM.YYYY"
                    continue
            if fld.get("num"):
                n = parse_num(v)
                if n is not None:
                    c = ws.cell(row=r, column=j, value=n)
                    c.number_format = "#,##0"
                    continue
            ws.cell(row=r, column=j, value=str(v))
    last = max(len(rows) + 3, 4)
    ws.auto_filter.ref = f"A2:{get_column_letter(len(fields))}{last}"
    ws.freeze_panes = "A4"
    wb.save(path)


MARKER_COLS = [
    ("created_at", "Дата", 19), ("kind", "Тип", 12), ("telegram_id", "telegram_id", 14),
    ("rep", "Кейс (rep)", 16), ("client", "Клиент", 28), ("address", "Адрес", 42),
    ("label", "Метка/подпись", 30), ("lat", "Широта", 12), ("lon", "Долгота", 12),
]


def write_markers_xlsx(path: str, rows: list[dict]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Метки"
    for j, (_k, title, w) in enumerate(MARKER_COLS, start=1):
        ws.cell(row=1, column=j, value=title).font = Font(bold=True)
        ws.column_dimensions[get_column_letter(j)].width = w
    for r, row in enumerate(rows, start=2):
        for j, (k, _t, _w) in enumerate(MARKER_COLS, start=1):
            v = row.get(k)
            if v in (None, ""):
                continue
            if k == "created_at":
                try:
                    c = ws.cell(row=r, column=j,
                                value=datetime.fromisoformat(str(v).replace("T", " ")))
                    c.number_format = "DD.MM.YYYY HH:MM"
                    continue
                except ValueError:
                    pass
            elif k in ("lat", "lon") and isinstance(v, (int, float)):
                c = ws.cell(row=r, column=j, value=float(v))
                c.number_format = "0.000000"
                continue
            ws.cell(row=r, column=j, value=v if isinstance(v, (int, float)) else str(v))
    if rows:
        from openpyxl.utils import get_column_letter as gcl
        ws.auto_filter.ref = f"A1:{gcl(len(MARKER_COLS))}{len(rows) + 1}"
    ws.freeze_panes = "A2"
    wb.save(path)


# --- Мелкие виджеты в стиле «Контур» -----------------------------------------
class Card(tk.Frame):
    """Белая карточка с тёплой рамкой, заголовком и опц. описанием."""

    def __init__(self, parent, title: str, desc: str | None = None):
        super().__init__(parent, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, bd=0)
        self.inner = tk.Frame(self, bg=CARD)
        self.inner.pack(fill="both", expand=True, padx=16, pady=(12, 14))
        tk.Label(self.inner, text=title, bg=CARD, fg=TEXT,
                 font=f(10, True)).pack(anchor="w")
        if desc:
            tk.Label(self.inner, text=desc, bg=CARD, fg=MUTED, font=f(9),
                     justify="left", wraplength=760).pack(anchor="w", pady=(2, 0))
        tk.Frame(self.inner, bg=CARD, height=8).pack()


def btn(parent, text, command=None, kind="ghost", bg_=CARD):
    """accent (графит) | ghost (обводка) | danger."""
    if kind == "accent":
        bg, fg, hover, hl = ACCENT, "#FCFBF8", ACCENT_H, ACCENT
    elif kind == "danger":
        bg, fg, hover, hl = bg_, DANGER, "#F5E3DD", BORDER
    else:
        bg, fg, hover, hl = bg_, TEXT, ALT, BORDER
    b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                  activebackground=hover, activeforeground=fg, relief="flat",
                  font=f(9), cursor="hand2", padx=13, pady=6, bd=0,
                  highlightbackground=hl, highlightthickness=1)
    b.bind("<Enter>", lambda _e: b["state"] == "normal" and b.config(bg=hover))
    b.bind("<Leave>", lambda _e: b.config(bg=bg))
    return b


def entry(parent, width=None, show=None, bg_=CARD):
    e = tk.Entry(parent, bg=bg_, fg=TEXT, insertbackground=TEXT, relief="flat",
                 font=f(10), highlightbackground=BORDER, highlightcolor=ACCENT,
                 highlightthickness=1, show=show or "")
    if width:
        e.configure(width=width)
    return e


class ChipFlow(tk.Text):
    """Поток «чипсов» с автопереносом (трюк: Text + window_create)."""

    def __init__(self, parent, bg_=CARD):
        super().__init__(parent, wrap="char", bd=0, bg=bg_, cursor="arrow",
                         height=1, highlightthickness=0, state="disabled",
                         spacing1=2, spacing3=2)
        self._bg = bg_
        self.bind("<Configure>", lambda _e: self._fit())

    def set_chips(self, items: list[tuple[str, bool, object]] | list[str]):
        """items: строки (пассивные чипсы) или (текст, включён, on_click)."""
        self.configure(state="normal")
        self.delete("1.0", "end")
        for it in items:
            if isinstance(it, str):
                w = tk.Label(self, text=it, bg=ALT, fg=TEXT, font=f(9), padx=9, pady=2)
            else:
                text, on, cb = it
                w = tk.Button(self, text=text, command=cb, relief="flat", bd=0,
                              bg=ACCENT if on else self._bg,
                              fg="#FCFBF8" if on else MUTED,
                              activebackground=ACCENT_H if on else ALT,
                              activeforeground="#FCFBF8" if on else TEXT,
                              font=f(9), padx=10, pady=2, cursor="hand2",
                              highlightbackground=ACCENT if on else BORDER,
                              highlightthickness=1)
            self.window_create("end", window=w, padx=2, pady=2)
        self.configure(state="disabled")
        self.after_idle(self._fit)

    def _fit(self):
        """Высота — по РЕАЛЬНЫМ пикселям контента. Text меряет height в строках
        СВОЕГО шрифта, а чипсы-кнопки почти вдвое выше строки: при нескольких
        рядах низ списка обрезался и был недостижим (выглядело как «список
        сотрудников не прокручивается» в форме руководителя)."""
        try:
            ypix = self.count("1.0", "end", "ypixels")
            ypix = ypix[0] if isinstance(ypix, tuple) else (ypix or 0)
            ls = max(tkfont.Font(font=self.cget("font")).metrics("linespace"), 1)
            self.configure(height=max(1, -(-int(ypix) // ls)))
        except tk.TclError:
            pass


def names_look_different(typed: str, real: str) -> bool:
    """Похожи ли имена «на глаз». Точное совпадение не нужно и вредно: «Гульноза»
    против «Гульноза Алишеровна» — один человек, а «Михаил» против «Алиев Озодбек» —
    разные. Считаем похожими, если совпало хотя бы одно слово от трёх букв."""
    def words(s):
        return {w for w in re.split(r"[^\w]+", str(s or "").casefold().replace("ё", "е"))
                if len(w) >= 3}
    a, b = words(typed), words(real)
    return bool(a and b and not (a & b))


class KVGrid(tk.Frame):
    """Плашки «показатель → число» (отчёт импорта/разброса).

    Больше COLS плашек в ряд в карточку не влезает, а горизонтальной прокрутки в
    пульте нет — лишние молча уезжали за правый край. Поэтому переносим на ряд ниже."""

    COLS = 6

    def __init__(self, parent, bg_=CARD):
        super().__init__(parent, bg=bg_)

    def set_pairs(self, pairs: list[tuple[str, object]], cols: int | None = None):
        for w in self.winfo_children():
            w.destroy()
        cols = cols or self.COLS
        for i, (k, v) in enumerate(pairs):
            row, col = divmod(i, cols)
            cell = tk.Frame(self, bg=ALT, padx=10, pady=6)
            cell.grid(row=row, column=col, sticky="w",
                      padx=(0 if col == 0 else 6, 0),
                      pady=(0 if row == 0 else 6, 0))
            tk.Label(cell, text=k, bg=ALT, fg=MUTED, font=f(8)).pack(anchor="w")
            tk.Label(cell, text=fmt_n(v), bg=ALT, fg=TEXT, font=f(11, True)).pack(anchor="w")


# --- Приложение ---------------------------------------------------------------
SECTIONS = [
    ("dash", "Сводка"), ("registry", "Реестр"), ("prio", "Приоритет"),
    ("people", "Люди"), ("mgrs", "Руководители"), ("activity", "Активность"),
    ("conn", "Подключение"),
]
TITLES = dict(SECTIONS) | {"prio": "Приоритет выезда"}


class Pult(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Контур · Выезды")
        self.configure(bg=BG)
        self.settings = self._load_settings()
        self.url = self.settings.get("url", DEFAULT_URL)
        self.key = self.settings.get("key", DEFAULT_KEY)
        self.device_token = self.settings.get("device_token", "")
        self.imp_book: dict = {}          # выбранная книга: путь, грид, вид источника
        self.imp_selected: set[str] = set()   # кого из сотрудников грузим
        self._imp_vars: dict = {}
        self.connected = False
        self.guests: list[dict] = []
        self.mgrs: list[dict] = []
        self.mgr_cities: list[dict] = []
        self.mgr_emps: list[dict] = []
        self.mgr_city_emps: dict = {}
        self.mgr_form = {"tid": "", "cities": set(), "employees": set()}
        self.scatter_matched: list[dict] = []
        self.scatter_selected: set[str] = set()
        self.scatter_roots: list[str] = []
        self.enr_matched: list[dict] = []
        self.enr_selected: set[str] = set()
        self._enr_fields: list[dict] | None = None
        self._loaded: set[str] = set()

        self._dpi_geometry()
        self._styles()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        sec = self.settings.get("sec", "dash") if self.key else "conn"
        self.nav(sec if sec in self.frames else "dash")
        if self.key:
            self.after(150, self.refresh)

    # ── DPI / стили ────────────────────────────────────────────────────
    def _dpi_geometry(self):
        self.update_idletasks()
        scale = self.winfo_fpixels("1i") / 96.0
        self.px = lambda n: int(n * scale + 0.5)
        w, h = self.px(1180), self.px(800)
        x = max((self.winfo_screenwidth() - w) // 2, 0)
        y = max((self.winfo_screenheight() - h) // 3, 0)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(self.px(980), self.px(660))
        try:
            self.iconphoto(True, tk.PhotoImage(file=str(APP_DIR / "icon-src.png")))
        except tk.TclError:
            pass

    def _styles(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, rowheight=self.px(28), borderwidth=0, font=f(9))
        st.map("Treeview", background=[("selected", ALT)], foreground=[("selected", TEXT)])
        st.configure("Treeview.Heading", background=CARD, foreground=MUTED,
                     font=(FONT, 8, "bold"), relief="flat", padding=(6, 6))
        st.map("Treeview.Heading", background=[("active", CARD)])
        st.configure("TCombobox", fieldbackground=CARD, background=CARD,
                     foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                     lightcolor=CARD, darkcolor=CARD, padding=4)
        st.configure("Horizontal.TScale", background=CARD, troughcolor=ALT)
        st.configure("Vertical.TScrollbar", background=SIDE, troughcolor=BG,
                     bordercolor=BG, arrowcolor=MUTED)

    # ── каркас ─────────────────────────────────────────────────────────
    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # сайдбар
        side = tk.Frame(self, bg=SIDE, width=self.px(224))
        side.grid(row=0, column=0, sticky="nsw")
        side.pack_propagate(False)
        logo = tk.Frame(side, bg=SIDE)
        logo.pack(fill="x", padx=14, pady=(16, 14))
        mark = tk.Label(logo, text="К", bg=ACCENT, fg="#FCFBF8", font=f(11, True),
                        width=2, height=1)
        mark.pack(side="left")
        tt = tk.Frame(logo, bg=SIDE)
        tt.pack(side="left", padx=(9, 0))
        tk.Label(tt, text="Контур", bg=SIDE, fg=TEXT, font=f(11, True)).pack(anchor="w")
        tk.Label(tt, text="ВЫЕЗДЫ", bg=SIDE, fg=MUTED, font=(FONT, 7, "bold")).pack(anchor="w")

        self.nav_btns: dict[str, tk.Button] = {}
        navf = tk.Frame(side, bg=SIDE)
        navf.pack(fill="both", expand=True, padx=8)
        for sec, label in SECTIONS:
            if sec == "conn":
                tk.Frame(navf, bg=SIDE).pack(fill="both", expand=True)
            b = tk.Button(navf, text=label, anchor="w", relief="flat", bd=0,
                          bg=SIDE, fg=MUTED, activebackground=ALT, activeforeground=TEXT,
                          font=f(10), padx=12, pady=7, cursor="hand2",
                          command=lambda s=sec: self.nav(s))
            b.pack(fill="x", pady=1)
            self.nav_btns[sec] = b

        foot = tk.Frame(side, bg=SIDE)
        foot.pack(fill="x", padx=14, pady=12)
        self.dot = tk.Canvas(foot, width=self.px(10), height=self.px(10),
                             bg=SIDE, highlightthickness=0)
        self.dot.pack(side="left")
        ft = tk.Frame(foot, bg=SIDE)
        ft.pack(side="left", padx=(8, 0))
        self.conn_lbl = tk.Label(ft, text="не подключено", bg=SIDE, fg=TEXT, font=f(9))
        self.conn_lbl.pack(anchor="w")
        self.host_lbl = tk.Label(ft, text="—", bg=SIDE, fg=MUTED, font=f(8))
        self.host_lbl.pack(anchor="w")
        self._set_conn(False, "не подключено")

        # шапка + прокручиваемое содержимое
        main = tk.Frame(self, bg=BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        bar = tk.Frame(main, bg=BG)
        bar.grid(row=0, column=0, sticky="ew", padx=26, pady=(14, 10))
        self.sec_title = tk.Label(bar, text="Сводка", bg=BG, fg=TEXT, font=f(14, True))
        self.sec_title.pack(side="left")
        btn(bar, "Обновить", self.refresh, bg_=BG).pack(side="right")
        self.upd_lbl = tk.Label(bar, text="", bg=BG, fg=MUTED, font=f(8))
        self.upd_lbl.pack(side="right", padx=10)

        wrap = tk.Frame(main, bg=BG)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.content = tk.Frame(self.canvas, bg=BG)
        self._cwin = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>",
                          lambda _e: self.canvas.configure(
                              scrollregion=self.canvas.bbox("all") or (0, 0, 0, 0)))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._cwin, width=e.width))
        self.bind_all("<MouseWheel>",
                      lambda e: self.canvas.yview_scroll(-e.delta // 120, "units"))
        # Колесо над комбобоксом листало его ЗНАЧЕНИЯ, а над чипсами (Text) — их
        # внутренний скролл: страница «застревала». Всегда крутим страницу и гасим
        # родное поведение виджета (break: класс-биндинг перекрывает и 'all').
        def _wheel_page(e):
            self.canvas.yview_scroll(-e.delta // 120, "units")
            return "break"
        self.bind_class("TCombobox", "<MouseWheel>", _wheel_page)
        self.bind_class("Text", "<MouseWheel>", _wheel_page)

        self.frames: dict[str, tk.Frame] = {}
        for sec, _ in SECTIONS:
            fr = tk.Frame(self.content, bg=BG)
            self.frames[sec] = fr
            getattr(self, f"_build_{sec}")(fr)

        # тост
        self.toast_lbl = tk.Label(self, text="", bg=ACCENT, fg="#FCFBF8",
                                  font=f(9), padx=18, pady=8)
        self._toast_timer = None

    def _section_inner(self, fr) -> tk.Frame:
        inner = tk.Frame(fr, bg=BG)
        inner.pack(fill="x", padx=26, pady=(4, 30))
        return inner

    def nav(self, sec: str):
        for s, b in self.nav_btns.items():
            on = s == sec
            b.configure(bg=ACCENT if on else SIDE, fg="#FCFBF8" if on else MUTED,
                        activebackground=ACCENT_H if on else ALT,
                        activeforeground="#FCFBF8" if on else TEXT)
        for s, fr in self.frames.items():
            fr.pack_forget()
        self.frames[sec].pack(fill="both", expand=True)
        self.sec_title.configure(text=TITLES.get(sec, ""))
        self.settings["sec"] = sec
        self.canvas.yview_moveto(0)
        if self.connected and sec in ("prio", "activity", "people", "mgrs") \
                and sec not in self._loaded:
            {"prio": self.load_priority, "activity": self.load_activity,
             "people": self.load_roles, "mgrs": self.load_managers}[sec]()

    # ── связь/фоновые задачи ───────────────────────────────────────────
    def _set_conn(self, ok: bool, text: str):
        self.connected = ok
        self.dot.delete("all")
        r = self.px(9)
        self.dot.create_oval(1, 1, r, r, fill=SUCCESS if ok else DANGER, outline="")
        self.conn_lbl.configure(text=text)
        try:
            self.host_lbl.configure(text=urllib.parse.urlsplit(self.url).netloc or "—")
        except ValueError:
            self.host_lbl.configure(text="—")

    def toast(self, text: str, err: bool = False):
        self.toast_lbl.configure(text=text, bg=DANGER if err else ACCENT)
        self.toast_lbl.place(relx=0.5, rely=1.0, y=-self.px(26), anchor="s")
        if self._toast_timer:
            self.after_cancel(self._toast_timer)
        self._toast_timer = self.after(3200, self.toast_lbl.place_forget)

    def bg_task(self, fn, done=None, fail=None):
        """fn — в потоке; done(result)/fail(exc) — в UI-потоке."""
        def run():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 — показываем пользователю
                # e=e обязательно: Python удаляет переменную except при выходе из
                # блока, и лямбда без захвата падала бы NameError уже в Tk-цикле —
                # все fail-обработчики молча умирали, UI застревал на «Идёт…».
                self.after(0, lambda e=e: (fail or self._fail)(e))
                return
            if done:
                self.after(0, lambda: done(res))
        threading.Thread(target=run, daemon=True).start()

    def _fail(self, e: Exception):
        self.toast(str(e), err=True)

    def api(self, method: str, path: str, body: dict | None = None, timeout=20):
        """Запрос к admin-API. Дружелюбные ошибки вместо трейсбеков."""
        url = self.url.rstrip("/") + path
        data = json.dumps(body).encode() if body is not None else None
        # Каждый день ходим ТОКЕНОМ устройства; мастер-ключ идёт только если
        # устройство ещё не привязано (или его отозвали и надо привязать заново).
        headers = {"X-Device-Token": self.device_token} if self.device_token \
            else {"X-Admin-Key": self.key}
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.load(r)
            except urllib.error.URLError as e:
                # самоподписанный/прокси-сертификат — пробуем без проверки TLS
                if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                    ctx = ssl._create_unverified_context()  # noqa: S323 — осознанно
                    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                        return json.load(r)
                raise
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError(
                    "Устройство не принято: токен отозван — привяжи заново "
                    "мастер-ключом" if self.device_token
                    else "Неверный мастер-ключ") from e
            if e.code == 404:
                raise RuntimeError(f"На сервере нет {path} — сервер старой версии?") from e
            if e.code == 409:
                # Валидационный конфликт импорта (например, один КД с разными
                # владельцами/ФИО) — сервер гарантированно ничего не записал.
                # Вернуть структурированный отчёт экрану вместо безликого HTTP 409.
                try:
                    payload = json.load(e)
                except (ValueError, TypeError):
                    payload = None
                if isinstance(payload, dict) and payload.get("error"):
                    return payload
            raise RuntimeError(f"Сервер ответил {e.code} на {path}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Нет связи с сервером ({e.reason})") from e

    # ══ СВОДКА ══════════════════════════════════════════════════════════
    def _build_dash(self, fr):
        inner = self._section_inner(fr)
        row = tk.Frame(inner, bg=BG)
        row.pack(fill="x")
        self.metrics: dict[str, tk.Label] = {}
        for i, (key, label) in enumerate([
                ("clients", "Контрагентов"), ("visited", "С визитом"),
                ("emps", "Сотрудников"), ("binds", "Выбрали себя"), ("marks", "Меток")]):
            m = tk.Frame(row, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            m.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            row.grid_columnconfigure(i, weight=1)
            tk.Label(m, text=label, bg=CARD, fg=MUTED, font=f(8)).pack(
                anchor="w", padx=13, pady=(10, 0))
            v = tk.Label(m, text="—", bg=CARD, fg=TEXT, font=f(17, True))
            v.pack(anchor="w", padx=13, pady=(0, 10))
            self.metrics[key] = v

        countries = Card(
            inner, "По республикам",
            "Отдельно по закреплённым кейсам. Республика берётся из настроенного "
            "scope руководителя, а не из числового поля «Пул».")
        countries.pack(fill="x", pady=(12, 0))
        country_grid = tk.Frame(countries.inner, bg=CARD)
        country_grid.pack(fill="x")
        self.country_kv: dict[str, KVGrid] = {}
        self.country_results: dict[str, ChipFlow] = {}
        for i, (code, title) in enumerate((("tj", "🇹🇯 Таджикистан"),
                                           ("uz", "🇺🇿 Узбекистан"))):
            pane = tk.Frame(country_grid, bg=CARD, highlightbackground=BORDER,
                            highlightthickness=1, padx=12, pady=10)
            pane.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            country_grid.grid_columnconfigure(i, weight=1)
            tk.Label(pane, text=title, bg=CARD, fg=TEXT,
                     font=f(10, True)).pack(anchor="w")
            kv = KVGrid(pane)
            kv.pack(fill="x", pady=(7, 3))
            kv.set_pairs([("договоров", 0), ("с визитом", 0), ("сотрудников", 0)])
            chips = ChipFlow(pane)
            chips.pack(fill="x", pady=(3, 0))
            chips.set_chips(["нет закреплённых кейсов"])
            self.country_kv[code] = kv
            self.country_results[code] = chips

        cov = Card(inner, "Покрытие визитами")
        cov.pack(fill="x", pady=(12, 0))
        self._cov_pc = 0
        self.cov_bar = tk.Canvas(cov.inner, height=self.px(8), bg=ALT, highlightthickness=0)
        self.cov_bar.pack(fill="x")
        self.cov_bar.bind("<Configure>", lambda _e: self._draw_cov())
        self.cov_txt = tk.Label(cov.inner, text="нет данных — подключись к серверу",
                                bg=CARD, fg=MUTED, font=f(9))
        self.cov_txt.pack(anchor="w", pady=(7, 0))

        res = Card(inner, "Исходы визитов")
        res.pack(fill="x", pady=(12, 0))
        self.res_chips = ChipFlow(res.inner)
        self.res_chips.pack(fill="x")
        self.res_chips.set_chips(["—"])

        per = Card(inner, "Период выгрузки")
        per.pack(fill="x", pady=(12, 0))
        self.period_dash = tk.Label(per.inner, text="—", bg=CARD, fg=MUTED, font=f(9))
        self.period_dash.pack(anchor="w")

    def _draw_cov(self):
        self.cov_bar.delete("all")
        w = max(self.cov_bar.winfo_width(), 10)
        self.cov_bar.create_rectangle(0, 0, w * self._cov_pc / 100, self.px(8),
                                      fill=SUCCESS, outline="")

    def refresh(self):
        self.settings["url"], self.settings["key"] = self.url, self.key
        self._save_settings()
        def done(d):
            self._set_conn(True, "подключено")
            self.metrics["clients"].configure(text=fmt_n(d.get("clients")))
            self.metrics["visited"].configure(text=fmt_n(d.get("visited")))
            self.metrics["emps"].configure(text=fmt_n(len(d.get("employees", []))))
            self.metrics["binds"].configure(text=fmt_n(len(d.get("bindings", []))))
            self.metrics["marks"].configure(text=fmt_n(d.get("markers")))
            country_rows = {c.get("code"): c for c in (d.get("countries") or [])}
            for code in ("tj", "uz"):
                c = country_rows.get(code) or {}
                clients, visited = c.get("clients") or 0, c.get("visited") or 0
                pc_country = round(visited / clients * 100) if clients else 0
                self.country_kv[code].set_pairs([
                    ("договоров", clients), ("с визитом", visited),
                    ("покрытие", f"{pc_country}%"),
                    ("сотрудников", c.get("employees") or 0),
                ])
                outcomes = c.get("by_result") or []
                self.country_results[code].set_chips(
                    [f"{name or 'без исхода'}  {fmt_n(n)}" for name, n in outcomes]
                    or ["нет закреплённых кейсов"])
            cl, vi = d.get("clients") or 0, d.get("visited") or 0
            pc = round(vi / cl * 100) if cl else 0
            self._cov_pc = pc
            self._draw_cov()
            self.cov_txt.configure(
                text=f"{fmt_n(vi)} из {fmt_n(cl)} контрагентов — {pc}%" if cl else "реестр пуст")
            br = d.get("by_result") or []
            self.res_chips.set_chips(
                [f"{r or 'без исхода'}  {fmt_n(c)}" for r, c in br] or ["визитов с исходом пока нет"])
            self._show_period(d.get("period") or {})
            self._render_binds(d.get("bindings") or [])
            self.upd_lbl.configure(
                text="обновлено " + datetime.now().strftime("%H:%M"))
            self._loaded.clear()
            cur = self.settings.get("sec", "dash")
            if cur in ("prio", "activity", "people", "mgrs"):
                self.nav(cur)
        def fail(e):
            self._set_conn(False, "нет связи")
            self.toast(str(e), err=True)
        self.bg_task(lambda: self.api("GET", "/admin/stats"), done, fail)

    # ══ РЕЕСТР ══════════════════════════════════════════════════════════
    def _build_registry(self, fr):
        inner = self._section_inner(fr)

        imp = Card(inner, "Импорт реестра",
                   "Общий реестр: опубликованный registry_cycle master или "
                   "утверждённое тестовое подмножество. Личные ФИО.xlsb сюда не "
                   "загружаются — офисные правки идут personal → registry_cycle → "
                   "master. Разрешение на загрузку регистрируется отдельно; если "
                   "ручка регистратора на сервере закрыта, пульт покажет команду.")
        imp.pack(fill="x")
        rowi = tk.Frame(imp.inner, bg=CARD)
        rowi.pack(anchor="w")
        btn(rowi, "Выбрать реестр…", self.pick_registry, kind="accent").pack(side="left")
        self.imp_file_lbl = tk.Label(imp.inner, text="", bg=CARD, fg=MUTED, font=f(9),
                                     justify="left", wraplength=760)
        self.imp_file_lbl.pack(anchor="w", pady=(8, 0))
        self.imp_pick_frame = tk.Frame(imp.inner, bg=CARD)
        self.imp_pick_frame.pack(fill="x")
        self.imp_go_row = tk.Frame(imp.inner, bg=CARD)
        self.imp_go_btn = btn(self.imp_go_row, "Загрузить выбранных (0)",
                              self.do_import, kind="accent")
        self.imp_go_btn.pack(side="left")
        self.imp_kvs = KVGrid(imp.inner)
        self.imp_kvs.pack(anchor="w", pady=(10, 0))
        self.imp_warn = tk.Label(imp.inner, text="", bg=CARD, fg=WARN, font=f(9),
                                 justify="left", wraplength=760)
        self.imp_warn.pack(anchor="w")

        enr = Card(inner, "Обогащение личных реестров",
                   "Точечно переносит доказанные визиты бота в личные ФИО.xlsb: "
                   "пишутся только ячейки с доказательством визита, чужие правки "
                   "не трогаются. Сначала предпросмотр, запись — отдельной кнопкой.")
        enr.pack(fill="x", pady=(12, 0))
        rowe = tk.Frame(enr.inner, bg=CARD)
        rowe.pack(anchor="w")
        btn(rowe, "Папка с республиками…", self.scatter_pick_base).pack(side="left")
        btn(rowe, "Проверить соответствия (без записи)", self.enr_preview,
            kind="accent").pack(
            side="left", padx=(8, 0))
        self.enr_base_lbl = tk.Label(enr.inner, text="", bg=CARD, fg=MUTED, font=f(9),
                                     wraplength=760, justify="left")
        self.enr_base_lbl.pack(anchor="w", pady=(8, 0))
        self.enr_kvs = KVGrid(enr.inner)
        self.enr_kvs.pack(anchor="w", pady=(8, 0))
        self.enr_warn = tk.Label(enr.inner, text="", bg=CARD, fg=WARN, font=f(9),
                                 justify="left", wraplength=760)
        self.enr_warn.pack(anchor="w")
        self.enr_pick_frame = tk.Frame(enr.inner, bg=CARD)
        self.enr_pick_frame.pack(fill="x")
        self.enr_apply_row = tk.Frame(enr.inner, bg=CARD)
        self.enr_apply_sel_btn = btn(self.enr_apply_row, "Обогатить выбранных (0)",
                                     lambda: self.enr_apply("selected"), kind="accent")
        self.enr_apply_sel_btn.pack(side="left")
        self.enr_apply_all_btn = btn(self.enr_apply_row, "Обогатить всех",
                                     lambda: self.enr_apply("all"))
        self.enr_apply_all_btn.pack(side="left", padx=(8, 0))
        self.enrich_out = tk.Label(enr.inner, text="", bg=CARD, fg=MUTED, font=f(9),
                                   justify="left", wraplength=760)
        self.enrich_out.pack(anchor="w", pady=(8, 0))

        exp = Card(inner, "Экспорт-слепок",
                   "«Реестр целиком» — новый xlsx из БД сервера (типизированные даты и "
                   "суммы, автофильтр). Это слепок БД, а не твой живой файл — для сверки "
                   "и «чистой» выгрузки.")
        exp.pack(fill="x", pady=(12, 0))
        rowb = tk.Frame(exp.inner, bg=CARD)
        rowb.pack(anchor="w")
        btn(rowb, "Реестр целиком…", self.do_export_full).pack(side="left")
        btn(rowb, "Метки курьеров…", self.do_export_markers).pack(side="left", padx=(8, 0))

        sc = Card(inner, "Legacy-разброс отключён",
                  "Старый режим пересобирал личные .xlsb из полного экспорта и мог "
                  "стереть ручные данные. Запись заблокирована на уровне интерфейса, "
                  "CLI и движка. Для сборки и распределения используйте отдельное "
                  "приложение «Контур · Реестры»; здесь оставлен только безопасный "
                  "предпросмотр соответствия папок.")
        sc.pack(fill="x", pady=(12, 0))
        rowsc = tk.Frame(sc.inner, bg=CARD)
        rowsc.pack(anchor="w")
        btn(rowsc, "Папка с республиками…", self.scatter_pick_base).pack(side="left")
        btn(rowsc, "Экспорт кейсов…", self.scatter_pick_source).pack(side="left", padx=(8, 0))
        btn(rowsc, "＋ прямая папка", self.scatter_add_root).pack(side="left", padx=(8, 0))
        self.sc_base_lbl = tk.Label(sc.inner, text="базовая папка не выбрана", bg=CARD,
                                    fg=MUTED, font=f(9), wraplength=760, justify="left")
        self.sc_base_lbl.pack(anchor="w", pady=(8, 0))
        self.sc_src_lbl = tk.Label(sc.inner, text="файл экспорта не выбран", bg=CARD,
                                   fg=MUTED, font=f(9), wraplength=760, justify="left")
        self.sc_src_lbl.pack(anchor="w")
        self.sc_roots_lbl = tk.Label(sc.inner, text="", bg=CARD, fg=MUTED, font=f(9),
                                     wraplength=760, justify="left")
        self.sc_roots_lbl.pack(anchor="w")
        btn(sc.inner, "Проверить совпадения", lambda: self.scatter_run(False),
            kind="accent").pack(anchor="w", pady=(10, 0))
        self.sc_kvs = KVGrid(sc.inner)
        self.sc_kvs.pack(anchor="w", pady=(10, 0))
        self.sc_warn = tk.Label(sc.inner, text="", bg=CARD, fg=WARN, font=f(9),
                                justify="left", wraplength=760)
        self.sc_warn.pack(anchor="w")
        self.sc_pick_frame = tk.Frame(sc.inner, bg=CARD)
        self.sc_pick_frame.pack(fill="x")
        self.sc_apply_row = tk.Frame(sc.inner, bg=CARD)
        self.sc_apply_all_btn = btn(self.sc_apply_row, "Разложить всех",
                                    lambda: self.scatter_run(True, "all"))
        self.sc_apply_all_btn.configure(state="disabled")
        self.sc_apply_all_btn.pack(side="left")
        self.sc_apply_sel_btn = btn(self.sc_apply_row, "Разложить выбранных (0)",
                                    lambda: self.scatter_run(True, "selected"), kind="accent")
        self.sc_apply_sel_btn.configure(state="disabled")
        self.sc_apply_sel_btn.pack(side="left", padx=(8, 0))
        self._scatter_restore()

        cl = Card(inner, "Очистка данных",
                  "«Сброс визитов» — обнуляет заполненное выездами (статусы, комментарии, "
                  "дату визита, фото), клиенты/долги/закрепление остаются. «Полная очистка» — "
                  "удаляет всех клиентов, визиты и метки под свежий импорт.")
        cl.pack(fill="x", pady=(12, 0))
        rc = tk.Frame(cl.inner, bg=CARD)
        rc.pack(anchor="w")
        reset_visits_btn = btn(
            rc, "Сбросить данные визитов", lambda: self.do_reset("visits")
        )
        reset_visits_btn.configure(state="disabled")
        reset_visits_btn.pack(side="left")
        reset_all_btn = btn(
            rc,
            "Полностью очистить реестр",
            lambda: self.do_reset("all"),
            kind="danger",
        )
        reset_all_btn.configure(state="disabled")
        reset_all_btn.pack(side="left", padx=(8, 0))
        self.reset_out = tk.Label(cl.inner, text="", bg=CARD, fg=MUTED, font=f(9))
        self.reset_out.pack(anchor="w", pady=(6, 0))

        # Кнопки «Запустить геокодинг» здесь больше нет. Координаты сервер ставит
        # сам после каждого импорта (scoring.geocode_city_centroids в конце
        # /admin/import.json), тем же офлайн-справочником и тем же кодом. Ручной
        # повтор давал ровно тот же результат и только создавал впечатление, что
        # без него точки не появятся.
        per = Card(inner, "Период выгрузки",
                   "По дате закрепления: выездники видят только клиентов из этого окна. "
                   "Пусто — видны все. Формат: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.")
        per.pack(fill="x", pady=(12, 0))
        rp = tk.Frame(per.inner, bg=CARD)
        rp.pack(anchor="w")
        self.p_from = entry(rp, width=12)
        self.p_from.pack(side="left")
        tk.Label(rp, text="—", bg=CARD, fg=MUTED).pack(side="left", padx=6)
        self.p_to = entry(rp, width=12)
        self.p_to.pack(side="left")
        btn(rp, "Применить", self.apply_period, kind="accent").pack(side="left", padx=(10, 0))
        btn(rp, "Сбросить", self.clear_period).pack(side="left", padx=(8, 0))
        self.period_cur = tk.Label(per.inner, text="—", bg=CARD, fg=MUTED, font=f(9))
        self.period_cur.pack(anchor="w", pady=(8, 0))

    # --- импорт ---
    def do_import(self):
        if not LIMITED_TEST_MASTER_IMPORT_UI_ENABLED:
            self.imp_kvs.set_pairs([])
            self.imp_warn.configure(
                text=LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE
            )
            self.toast(LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE, err=True)
            return
        book = self.imp_book
        if not book:
            self.toast("Сначала выбери реестр", err=True)
            return
        picked = sorted(self.imp_selected)
        if not picked:
            self.toast("Отметь хотя бы одного сотрудника", err=True)
            return
        self.toast(f"Импорт {Path(book['path']).name}: {len(picked)} сотр…")

        def work():
            prepared = self._prepare_import(book, picked)
            body = {
                "data_exchange_contract": enrich.DATA_EXCHANGE_CONTRACT,
                "source_kind": prepared["source_kind"],
                "source_proof": prepared["source_proof"],
                "sheets": prepared["sheets"],
            }
            # Разрешение на импорт — ОТДЕЛЬНЫЙ акт. Если ручка регистратора на
            # сервере открыта, пульт делает его тем же admin-ключом; если закрыта,
            # это не ошибка пульта, а «зарегистрируй на сервере» — команда ниже.
            registered = None
            try:
                registered = self.api(
                    "POST", "/admin/import/authorize.json",
                    {"source_kind": prepared["source_kind"],
                     "source_proof": prepared["source_proof"]},
                    timeout=60)
            except Exception:                              # noqa: BLE001
                registered = None
            result = self.api("POST", "/admin/import.json", body, timeout=180)
            if isinstance(result, dict) and result.get("error"):
                result = {**result,
                          "_proof_digest": prepared["proof_digest"],
                          "_source_kind": prepared["source_kind"],
                          "_registered": bool(registered and registered.get("ok"))}
            return result

        def done(d):
            if d.get("error"):
                self.imp_kvs.set_pairs([])
                extra = ""
                if d.get("header_sample"):
                    extra = ". В файле: " + ", ".join(d["header_sample"])
                if d.get("_proof_digest") and not d.get("_registered"):
                    extra += (
                        "\n\nРазрешение не зарегистрировано — ручка регистратора на "
                        "сервере закрыта. Либо открой её (IMPORT_REGISTRAR_HTTP=1 в "
                        "/opt/hardbot2/.env), либо зарегистрируй вручную:\n"
                        "  docker compose exec -T bot python import_authorization.py "
                        f"register --source-kind {d.get('_source_kind') or ''} "
                        "--proof /tmp/proof.json\n"
                        f"proof лежит здесь: {self._proof_path or '—'}\n"
                        f"digest: {d['_proof_digest']}\n"
                        "После регистрации нажми «Загрузить выбранных» ещё раз."
                    )
                self.imp_warn.configure(text=d["error"] + extra)
                self.toast("Импорт не прошёл", err=True)
                return
            self.imp_kvs.set_pairs(self._imp_pairs(d))
            self.imp_warn.configure(text="\n".join(self._imp_notes(d)))
            if d.get("idempotent_replay"):
                self.toast(
                    "Этот receipt уже использован; повтор ничего не записал",
                    err=True,
                )
            else:
                self.toast(f"Импорт готов: +{fmt_n(d.get('inserted'))} / обновлено {fmt_n(d.get('updated'))}")
            self.refresh()
        self.bg_task(work, done)

    # Куда положен proof последней попытки — чтобы его можно было зарегистрировать
    # руками, если HTTP-ручка регистратора на сервере выключена.
    _proof_path = None

    def pick_registry(self):
        """Шаг 1: выбрать книгу и показать, кто в ней есть. Ничего не отправляет."""
        path = filedialog.askopenfilename(
            title="Реестр",
            filetypes=[("Excel", "*.xlsx *.xlsm *.xlsb *.xls")])
        if not path:
            return
        self.imp_file_lbl.configure(text="Читаю " + Path(path).name + "…")
        self.imp_kvs.set_pairs([])
        self.imp_warn.configure(text="")

        def done(book):
            head, first, c_id, c_who = self._grid_layout(book["grid"])
            book.update(head=head, first=first, c_id=c_id, c_who=c_who)
            self.imp_book = book
            self.imp_selected = {e["key"] for e in book["employees"]}
            kind = ("опубликованный master (есть расписка цикла)"
                    if book.get("published") else
                    "утверждённое подмножество: расписки registry_cycle для этого "
                    "файла нет")
            self.imp_file_lbl.configure(
                text=f"{Path(path).name} · строк {len(book['scope'])} · "
                     f"сотрудников {len(book['employees'])}\nВид источника: {kind}")
            self._imp_render()

        def fail(exc):
            self.imp_book = {}
            self.imp_selected = set()
            self._imp_render()
            self.imp_file_lbl.configure(text=f"Файл не разобран: {exc}")
            self.toast("Реестр не прочитан", err=True)

        self.bg_task(lambda: self.read_registry(path), done, fail)

    def _imp_render(self):
        for w in self.imp_pick_frame.winfo_children():
            w.destroy()
        self.imp_go_row.pack_forget()
        book = self.imp_book
        if not book:
            return
        head = tk.Frame(self.imp_pick_frame, bg=CARD)
        head.pack(fill="x", pady=(10, 4))
        tk.Label(head, text="Кого грузить (галочки):", bg=CARD, fg=MUTED,
                 font=f(9)).pack(side="left")
        btn(head, "все", lambda: self._imp_all(True)).pack(side="right")
        btn(head, "снять", lambda: self._imp_all(False)).pack(side="right", padx=(0, 6))
        box = tk.Frame(self.imp_pick_frame, bg=CARD, highlightbackground=BORDER,
                       highlightthickness=1)
        box.pack(fill="x")
        self._imp_vars = {}
        for emp in book["employees"]:
            var = tk.BooleanVar(value=emp["key"] in self.imp_selected)
            self._imp_vars[emp["key"]] = var
            row = tk.Frame(box, bg=CARD)
            row.pack(fill="x")
            tk.Checkbutton(row, text=emp["name"], variable=var,
                           bg=CARD, fg=TEXT, activebackground=CARD, selectcolor=CARD,
                           font=f(9),
                           command=lambda k=emp["key"], v=var: self._imp_toggle(k, v.get())
                           ).pack(side="left", padx=6)
            note = (f"договоров: {emp['count']}" +
                    (f" · написаний в файле: {emp['variants']}"
                     if emp["variants"] > 1 else ""))
            tk.Label(row, text=note, bg=CARD, fg=MUTED, font=f(8)).pack(side="left")
        self.imp_go_row.pack(anchor="w", pady=(10, 0))
        self._imp_count()

    def _imp_toggle(self, name, on):
        (self.imp_selected.add if on else self.imp_selected.discard)(name)
        self._imp_count()

    def _imp_all(self, on):
        self.imp_selected = ({e["key"] for e in self.imp_book.get("employees") or []}
                             if on else set())
        for key, var in (self._imp_vars or {}).items():
            var.set(key in self.imp_selected)
        self._imp_count()

    def _imp_count(self):
        book = self.imp_book or {}
        rows = sum(e["count"] for e in book.get("employees") or []
                   if e["key"] in self.imp_selected)
        self.imp_go_btn.configure(
            text=f"Загрузить выбранных ({len(self.imp_selected)} чел · {rows} дог.)",
            state="normal" if self.imp_selected else "disabled")

    def read_registry(self, path: str) -> dict:
        """Прочитать книгу и разложить её по сотрудникам. Ничего не отправляет.

        Заодно выясняем, есть ли для этого файла publish-receipt registry_cycle.
        Receipt привязан к ТОЧНОМУ пути (`U:\\rbr\\…`) и SHA файла, поэтому копия
        мастера в «Загрузках» его не имеет — и это не поломка, а ровно то, что
        receipt и означает: «этот файл опубликован циклом вот отсюда».
        """
        import limited_test_import as lti

        stable = enrich.read_stable_workbook(path)
        grid = lti._validate_sheets(stable["sheets"])
        rows = self._scope_from_grid(grid)
        # Группируем по КАНОНИЧЕСКОМУ имени: в реестре одна и та же фамилия живёт
        # и «Егор», и «егор», и «Егор ». Бот их давно склеивает, а список галочек
        # показывал бы двух разных людей — и половина строк молча не уехала бы.
        # В подписи при этом показываем живое написание, а не служебный ключ.
        groups: dict[str, dict] = {}
        for item in rows:
            key = self._emp_key(item["responsible"])
            spelling = " ".join(str(item["responsible"] or "").split())
            g = groups.setdefault(key, {"key": key, "count": 0, "spellings": {}})
            g["count"] += 1
            g["spellings"][spelling] = g["spellings"].get(spelling, 0) + 1
        employees = []
        for g in groups.values():
            best = max(g["spellings"].items(), key=lambda kv: (kv[1], kv[0]))[0]
            employees.append({"key": g["key"], "name": best or "(без ответственного)",
                              "count": g["count"], "variants": len(g["spellings"])})
        employees.sort(key=lambda e: (-e["count"], e["name"]))
        try:
            published = enrich.prepare_published_master_import(path)
            no_receipt = ""
        except Exception as exc:                           # noqa: BLE001
            published, no_receipt = None, str(exc)
        return {"path": path, "sheets": stable["sheets"], "grid": grid,
                "file_sha256": stable["file_sha256"], "scope": rows,
                "employees": employees,
                "published": published, "no_receipt": no_receipt}

    def _prepare_import(self, book: dict, picked: list[str]) -> dict:
        """Книга + отмеченные сотрудники → (source_kind, proof, sheets).

        Весь файл целиком и с receipt'ом — это `published_master`, боевой путь.
        Как только выбраны НЕ все сотрудники, файл перестаёт быть опубликованным
        мастером: строки режутся, и грузить это можно только как утверждённое
        подмножество со своим scope. Название вида источника — не косметика: от
        него зависит, что сервер сверяет и что попадёт в аудит.
        """
        import exchange_contract as contract
        import limited_test_import as lti

        self._proof_path = None
        all_emps = {e["key"] for e in book["employees"]}
        whole = set(picked) == all_emps

        if whole and book.get("published"):
            prepared = book["published"]
            grid = lti._validate_sheets(prepared["sheets"])
            digest = contract.validate_import_source_proof(
                prepared["source_proof"],
                source_kind=IMPORT_SOURCE_PUBLISHED_MASTER,
                grid=grid)["digest"]
            return {"source_kind": IMPORT_SOURCE_PUBLISHED_MASTER,
                    "source_proof": prepared["source_proof"],
                    "sheets": prepared["sheets"], "proof_digest": digest}

        grid = self._grid_for(book, set(picked)) if not whole else book["grid"]
        scope = self._scope_from_grid(grid)
        approval = (f"pult-{Path(book['path']).stem.strip()[:60]}"
                    f"-{book['file_sha256'][:12]}-{len(scope)}")
        proof = contract.build_test_subset_source_proof(
            grid, approval_id=approval, contracts=scope)
        digest = contract.validate_import_source_proof(
            proof, source_kind=IMPORT_SOURCE_TEST_SUBSET, grid=grid)["digest"]
        try:
            out = Path(tempfile.gettempdir()) / f"hardbot-proof-{digest[:12]}.json"
            out.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True),
                           encoding="utf-8")
            self._proof_path = str(out)
        except OSError:
            self._proof_path = None
        return {"source_kind": IMPORT_SOURCE_TEST_SUBSET, "source_proof": proof,
                "sheets": [{"name": contract.WORKBOOK_SHEET_NAME, "grid": grid}],
                "proof_digest": digest}

    @staticmethod
    def _emp_key(name) -> str:
        """Ответственный для группировки: схлопнутые пробелы, регистр не важен."""
        return " ".join(str(name or "").split()).casefold()

    @staticmethod
    def _grid_for(book: dict, picked: set[str]) -> list[list]:
        """Грид только из строк отмеченных сотрудников — шапка целиком, данные срезом.

        Резать надо именно ГРИД, а не один scope: сервер сверяет утверждённый
        список с тем, что реально разобрал из книги, и scope-подмножество при
        полном гриде честно отбивается как `scope_mismatch`.
        """
        grid, head = book["grid"], book["head"]
        c_who = book["c_who"]
        out = list(grid[:book["first"]])
        for row in grid[book["first"]:]:
            if len(row) > c_who and Pult._emp_key(row[c_who]) in picked:
                out.append(row)
        # Сравниваем с `first`, а не с `head`: в шапке ТРИ строки, и «len > head»
        # было истиной даже для книги без единой строки данных.
        if len(out) <= book["first"]:
            raise ValueError("после отбора не осталось ни одной строки")
        return out

    @staticmethod
    def _grid_layout(grid: list[list]) -> tuple[int, int, int, int]:
        """(строка заголовков, первая строка данных, колонка КД, колонка ФИО).

        Строка профилей («сх»/«хард»/«софт») под заголовком — часть формы книги,
        а не клиент. Распознаём её ТОЧНО ТАК ЖЕ, как сервер: только в контрактной
        позиции и только по тегу в колонке-ключе. Наивное «первая строка после
        шапки — уже данные» давало лишний договор с ответственным «сх», а сервер
        его пропускал — импорт валился на scope_mismatch, который винит реестр.
        """
        import exchange_contract as contract

        head = None
        for i, row in enumerate(grid[:10]):
            titles = {enrich.norm_title(c) for c in row}
            if enrich.norm_title("Номер КД / ID") in titles:
                head = i
                break
        if head is None:
            raise ValueError("в книге нет колонки «Номер КД / ID»")
        cols = {enrich.norm_title(c): j for j, c in enumerate(grid[head]) if c}
        c_id = cols[enrich.norm_title("Номер КД / ID")]
        c_who = cols.get(enrich.norm_title("ответственный Сотрудник"))
        if c_who is None:
            raise ValueError("в книге нет колонки «ответственный Сотрудник»")
        first = head + 1
        type_row_index = head + contract.WORKBOOK_TYPE_ROW_OFFSET
        tags = {enrich.norm_title(t) for t in contract.WORKBOOK_TYPE_ROW_TAGS}
        if type_row_index < len(grid) and c_id < len(grid[type_row_index]) \
                and enrich.norm_title(grid[type_row_index][c_id]) in tags:
            first = type_row_index + 1
        return head, first, c_id, c_who

    @staticmethod
    def _scope_from_grid(grid: list[list]) -> list[dict[str, str]]:
        """[{contract_id, responsible}] из книги — ровно то, что разрешаем грузить.

        Пробелы и регистр не режем сами: контракт нормализует их одинаково с обеих
        сторон. Именно на этом спотыкался ручной scope — хвостовой пробел в
        «ответственный Сотрудник» валил импорт целиком с `scope_invalid`.

        Строка профилей («сх»/«хард»/«софт») под заголовком — часть формы книги,
        а не клиент. Распознаём её ТОЧНО ТАК ЖЕ, как сервер: только в контрактной
        позиции и только по тегу в колонке-ключе. Иначе она приезжает в scope
        тринадцатым «договором» с ответственным «сх», а сервер её пропускает — и
        импорт валится на `scope_mismatch`, который винит данные, а не пульт.
        """
        head, first, c_id, c_who = Pult._grid_layout(grid)
        scope, seen = [], set()
        for row in grid[first:]:
            if len(row) <= max(c_id, c_who):
                continue
            cid = enrich.norm_cid(row[c_id])
            if not cid or cid in seen:
                continue
            seen.add(cid)
            scope.append({"contract_id": cid,
                          "responsible": enrich.cell_text(row[c_who])})
        if not scope:
            raise ValueError("в книге нет ни одной строки с номером КД")
        return scope

    @staticmethod
    def _imp_pairs(d: dict) -> list[tuple[str, object]]:
        return [("строк", d.get("total_rows")), ("добавлено", d.get("inserted")),
                ("обновлено", d.get("updated")), ("без КД", d.get("skipped_no_id")),
                ("дублей", d.get("duplicates")),
                ("без ответственного", d.get("no_responsible")),
                ("офисных правок", d.get("office_cells")),
                ("call-полей изолировано", d.get("isolated_call_cells")),
                # None (старый сервер без счётчиков) KVGrid покажет как «—», а не 0
                ("отклонено защитой", d.get("protected_cells"))]

    @staticmethod
    def _imp_notes(d: dict) -> list[str]:
        notes = []
        if d.get("idempotent_replay"):
            notes.append(
                "Идемпотентный повтор: это разрешение уже было использовано, "
                "сервер не сделал ни одной записи. Для нового состояния нужен "
                "новый опубликованный master или новый утверждённый test-subset."
            )
        if d.get("no_responsible"):
            notes.append(f"Без ответственного: {d['no_responsible']} строк — колонка "
                         "«ответственный Сотрудник» пуста и в файле, и в базе. Эти "
                         "договоры в базе есть, но их не видит ни один выездник, они не "
                         "входят в отчёты руководителей, и обогащение их результат "
                         "никуда не запишет — личного файла у них нет. Проставь "
                         "ответственного в реестре и импортируй ещё раз.")
        if d.get("office_cells"):
            notes.append(f"Принято офисных правок: {d['office_cells']} ячеек по "
                         f"{d.get('office_contracts') or 0} договорам — файл видел "
                         "обогащение (дата выезда совпала), отличия учтены как правки "
                         "офиса и попадут в карточки и обогащение.")
        if d.get("protected_cells"):
            notes.append(f"Отклонено защитой: {d['protected_cells']} ячеек по "
                         f"{d.get('protected_contracts') or 0} договорам — в файле нет "
                         "свежей даты выезда бота (устаревшая копия?), результат "
                         "выездника не затёрт.")
        unk = d.get("unknown_columns") or []
        if unk:
            notes.append(
                "Незнакомые колонки требуют новой версии контракта: "
                + ", ".join(unk)
            )
        ignored = d.get("ignored_columns") or []
        if ignored:
            notes.append(
                "Служебные колонки по текущему контракту: "
                + ", ".join(ignored)
            )
        return notes

    def do_import_batch(self):
        """Личные книги никогда не импортируются в hardbot.

        Метод оставлен как fail-closed совместимость для старых горячих клавиш/
        сборок; в актуальном UI кнопки нет."""
        self.imp_warn.configure(
            text="Загрузка личных реестров в hardbot отключена: "
                 "personal ↔ master выполняет registry_cycle."
        )
        self.toast("Импорт личных реестров запрещён", err=True)

    # --- обогащение по папке республик (главный путь) ---
    def _personal_files(self, entries, emp) -> list[Path]:
        """Личные реестры сотрудника в его папках: ФИО.xlsb (имя из источника или
        папки), иначе первый непременной .xlsb в папке (~времянки Excel — мимо).

        Страховочные копии «(до обогащения …)» / «(до разброса …)» / «(до починки …)»
        тоже мимо: подхватить копию вместо живого файла — значит обогатить или
        импортировать данные недельной давности."""
        files: list[Path] = []
        for e in entries:
            folder = e["path"]
            cands = [folder / f"{razbros.sanitize_filename(emp)}.xlsb",
                     folder / f"{folder.name}.xlsb"]
            found = next((c for c in cands if c.exists()), None)
            if not found:
                xs = [p for p in sorted(folder.glob("*.xlsb"))
                      if not p.name.startswith("~")
                      and not razbros.is_backup_copy(p.name)]
                found = xs[0] if xs else None
            if found and found not in files:
                files.append(found)
        return files

    def enr_preview(self):
        if not self.scatter_base:
            self.toast("Сначала выбери папку с республиками", err=True)
            return
        self.enr_warn.configure(text="Проверяю: данные сервера + папки сотрудников…")
        base = self.scatter_base
        def work():
            d = self.api("GET", "/admin/export.json", timeout=120)
            enrich.validate_server_export(d)
            by_emp = enrich.split_by_employee(d["rows"])
            reps = razbros.load_config(None)
            fmap = razbros.build_folder_map(razbros.resolve_roots(base, reps, []))
            return d["fields"], by_emp, fmap
        def done(res):
            fields, by_emp, fmap = res
            self._enr_fields = fields
            matched, nofolder, nofile = [], [], []
            for key, e in sorted(by_emp.items(), key=lambda kv: kv[1]["employee"].lower()):
                if not e["filled"]:
                    continue        # бот ничего не заполнял — обогащать нечего
                entries = fmap.get(key)
                if not entries:
                    nofolder.append(f"{e['employee']} ({e['filled']})")
                    continue
                files = self._personal_files(entries, e["employee"])
                if not files:
                    nofile.append(e["employee"])
                    continue
                matched.append({"key": key, "employee": e["employee"],
                                "filled": e["filled"], "files": files,
                                "rows": e["rows"]})
            self.enr_matched = matched
            self.enr_selected = {m["key"] for m in matched}
            pairs = [
                ("с визитами бота", sum(1 for e in by_emp.values() if e["filled"])),
                ("готовы к обогащению", len(matched)),
                ("завершённых визитов", sum(m["filled"] for m in matched)),
                ("файлов", sum(len(m["files"]) for m in matched))]
            orphan_filled = getattr(by_emp, "orphan_filled", 0)
            if orphan_filled:
                pairs.append(("визитов без ответственного", orphan_filled))
            self.enr_kvs.set_pairs(pairs)
            warn = ""
            if orphan_filled:
                # Главная строка отчёта: эти визиты не попадут никуда, и раньше об
                # этом не было ни слова — так потерялись 173 выезда за 09-17.07.
                sample = [c for c in getattr(by_emp, "orphan_cids", []) if c][:10]
                warn = (f"⚠ Завершённых визитов без ответственного: {orphan_filled} — "
                        "они НЕ попадут ни в один реестр: личного файла у таких строк "
                        f"нет (всего строк без владельца: "
                        f"{getattr(by_emp, 'orphan_rows', 0)}). Проставь «ответственный "
                        "Сотрудник» в реестре, импортируй его — и обогащай заново."
                        + ((" КД: " + ", ".join(sample)
                            + ("…" if orphan_filled > len(sample) else ""))
                           if sample else ""))
            if nofolder:
                warn += (("\n" if warn else "")
                         + "Есть данные бота, но нет папки: " + ", ".join(nofolder))
            if nofile:
                warn += ("\n" if warn else "") + "Папка есть, но нет .xlsb: " + ", ".join(nofile)
            if not matched and not warn:
                warn = "Бот пока ничего не заполнил — обогащать нечего."
            self.enr_warn.configure(text=warn)
            self._render_enr_pick()
        self.bg_task(work, done, lambda e: (self.enr_warn.configure(text=f"Ошибка: {e}"),
                                            self.toast(str(e), err=True)))

    def _render_enr_pick(self):
        for w in self.enr_pick_frame.winfo_children():
            w.destroy()
        self.enr_apply_row.pack_forget()
        if not self.enr_matched:
            return
        head = tk.Frame(self.enr_pick_frame, bg=CARD)
        head.pack(fill="x", pady=(10, 4))
        tk.Label(head, text="Кого обогащать (галочки):", bg=CARD, fg=MUTED,
                 font=f(9)).pack(side="left")
        btn(head, "все", lambda: self._enr_all(True)).pack(side="right")
        btn(head, "снять", lambda: self._enr_all(False)).pack(side="right", padx=(0, 6))
        box = tk.Frame(self.enr_pick_frame, bg=CARD, highlightbackground=BORDER,
                       highlightthickness=1)
        box.pack(fill="x")
        self._enr_vars: dict[str, tk.BooleanVar] = {}
        for m in self.enr_matched:
            var = tk.BooleanVar(value=m["key"] in self.enr_selected)
            self._enr_vars[m["key"]] = var
            row = tk.Frame(box, bg=CARD)
            row.pack(fill="x")
            tk.Checkbutton(row, text=m["employee"], variable=var, bg=CARD, fg=TEXT,
                           activebackground=CARD, selectcolor=CARD, font=f(9),
                           command=lambda k=m["key"], v=var: self._enr_toggle(k, v.get())
                           ).pack(side="left", padx=6)
            where = " · ".join(p.parent.parent.name for p in m["files"])
            tk.Label(row, text=f"визитов: {m['filled']} · файлов: {len(m['files'])} · {where}",
                     bg=CARD, fg=MUTED, font=f(8)).pack(side="left")
        if LIMITED_TEST_ENRICH_WRITES_ENABLED:
            self.enr_apply_row.pack(anchor="w", pady=(10, 0))
        self._enr_seln()

    def _enr_toggle(self, k, on):
        (self.enr_selected.add if on else self.enr_selected.discard)(k)
        self._enr_seln()

    def _enr_all(self, on):
        for m in self.enr_matched:
            (self.enr_selected.add if on else self.enr_selected.discard)(m["key"])
            if m["key"] in self._enr_vars:
                self._enr_vars[m["key"]].set(on)
        self._enr_seln()

    def _enr_seln(self):
        n = sum(1 for m in self.enr_matched if m["key"] in self.enr_selected)
        self.enr_apply_sel_btn.configure(text=f"Обогатить выбранных ({n})",
                                         state="normal" if n else "disabled")

    def enr_apply(self, which: str):
        if not LIMITED_TEST_ENRICH_WRITES_ENABLED:
            self.enr_warn.configure(text=LIMITED_TEST_ENRICH_DISABLED_MESSAGE)
            self.toast(LIMITED_TEST_ENRICH_DISABLED_MESSAGE, err=True)
            return
        picked = [m for m in self.enr_matched
                  if which == "all" or m["key"] in self.enr_selected]
        if not picked:
            self.toast("Никто не выбран", err=True)
            return
        jobs = [{"employee": m["employee"], "path": p, "rows": m["rows"]}
                for m in picked for p in m["files"]]
        if not messagebox.askyesno(
                "Обогащение реестров",
                f"Обогатить личные реестры: сотрудников {len(picked)}, файлов {len(jobs)}?\n\n"
                "В каждый файл дописываются ТОЛЬКО кейсы его сотрудника, заполненные "
                "ботом. Рядом с каждым файлом создаётся резервная копия. Делает Excel — "
                "может занять время.\n\nЕсли в personal правили результат визита, "
                "сначала проведи registry_cycle, опубликуй master и импортируй именно "
                "его в hardbot. Прямой импорт personal запрещён."):
            return
        fields = self._enr_fields
        log = lambda s: self.after(0, lambda: self.enrich_out.configure(text=s))
        def done(results):
            ok = [r for r in results if "error" not in r]
            bad = [r for r in results if "error" in r]
            cells = sum(r["cells_written"] for r in ok)
            over = sum(r["cells_over_nonempty"] for r in ok)
            missing = sum(len(r["missing"]) for r in ok)
            lines = [f"Готово: файлов {len(ok)} из {len(results)} · дописано ячеек: {cells}"
                     + (f" (поверх старых: {over})" if over else "")
                     + (f" · кейсов не нашлось в файлах: {missing}" if missing else "")]
            for r in ok[:15]:
                lines.append(f"  {r['employee']} — {Path(r['file']).name}: "
                             f"ячеек {r['cells_written']}, кейсов {r['changed_cases']}")
            if len(ok) > 15:
                lines.append(f"  … и ещё {len(ok) - 15} файлов")
            for r in bad:
                lines.append(f"  ⚠ {r['employee']} — {Path(r['file']).name}: {r['error']}")
            n_bak = sum(1 for r in ok if r.get("backup"))
            if n_bak:
                lines.append(f"Резервные копии созданы рядом с изменёнными файлами ({n_bak}).")
            self.enrich_out.configure(text="\n".join(lines))
            self.toast(f"Обогащено файлов: {len(ok)}" + (f", ошибок: {len(bad)}" if bad else ""),
                       err=bool(bad))
        self.bg_task(lambda: enrich.enrich_many(jobs, fields, log), done,
                     lambda e: (self.enrich_out.configure(text=f"Ошибка: {e}"),
                                self.toast(str(e), err=True)))

    # --- экспорт: обогащение одного файла (запасной путь) ---
    def do_enrich(self):
        if not LIMITED_TEST_ENRICH_WRITES_ENABLED:
            self.enr_warn.configure(text=LIMITED_TEST_ENRICH_DISABLED_MESSAGE)
            self.toast(LIMITED_TEST_ENRICH_DISABLED_MESSAGE, err=True)
            return
        path = filedialog.askopenfilename(
            title="Живой файл реестра для обогащения",
            initialfile=self.settings.get("enrich_file", ""),
            filetypes=[("Excel", "*.xlsb *.xlsx *.xlsm *.xls")])
        if not path:
            return
        if not messagebox.askyesno(
                "Обогатить реестр",
                f"{Path(path).name}\n\nВ файл будут дописаны ТОЛЬКО данные визитов "
                "(заполненные ботом кейсы и их столбцы). Остальные строки, столбцы, "
                "листы и форматирование не трогаются.\n\nРезервная копия будет создана "
                "рядом с файлом. Если в файле правили руками — сначала запусти импорт. "
                "Продолжить?"):
            return
        self.settings["enrich_file"] = path
        self._save_settings()
        self.enrich_out.configure(text="Забираю данные с сервера…")
        def work():
            d = self.api("GET", "/admin/export.json", timeout=120)
            enrich.validate_server_export(d)
            log = lambda s: self.after(0, lambda: self.enrich_out.configure(text=s))
            return enrich.enrich_file(path, d["fields"], d["rows"], log=log)
        def done(p):
            parts = [
                f"Кейсов с данными бота: {p['filled_cases']}",
                f"найдено в файле: {p['matched_cases']}",
                f"дописано ячеек: {p['cells_written']}"
                + (f" (поверх старых значений: {p['cells_over_nonempty']})"
                   if p["cells_over_nonempty"] else ""),
                f"уже совпадало: {p['cells_already']}",
            ]
            text = " · ".join(parts)
            text += (f"\nРезервная копия: {p['backup']}" if p.get("backup")
                     else "\nФайл не изменился — резервная копия не создавалась.")
            if p["missing"]:
                text += f"\n⚠ Заполнены ботом, но не найдены в файле ({len(p['missing'])}): " \
                        + ", ".join(p["missing"][:20]) + ("…" if len(p["missing"]) > 20 else "")
            if p["visit_cols_absent"]:
                text += "\n⚠ В файле нет колонок: " + ", ".join(p["visit_cols_absent"])
            self.enrich_out.configure(text=text)
            self.toast(f"Обогащено: {p['changed_cases']} кейсов, {p['cells_written']} ячеек")
        def fail(e):
            self.enrich_out.configure(text=f"Ошибка: {e}")
            self.toast(str(e), err=True)
        self.bg_task(work, done, fail)

    # --- экспорт: слепок/метки ---
    def do_export_full(self):
        path = filedialog.asksaveasfilename(
            title="Куда сохранить слепок реестра", defaultextension=".xlsx",
            initialfile="registry_export.xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        def work():
            # Raw-слепок отделён от безопасного потока обогащения: без mode=full
            # сервер намеренно очищает все неподтверждённые поля визита.
            d = self.api("GET", "/admin/export.json?mode=full", timeout=120)
            enrich.validate_server_export(d)
            write_registry_xlsx(path, d["fields"], d["rows"])
            return len(d["rows"])
        self.bg_task(work, lambda n: self.toast(f"Сохранено: {path} ({fmt_n(n)} строк)"))

    def do_export_markers(self):
        path = filedialog.asksaveasfilename(
            title="Куда сохранить метки", defaultextension=".xlsx",
            initialfile="markers.xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        def work():
            d = self.api("GET", "/admin/markers.json", timeout=120)
            write_markers_xlsx(path, d["rows"])
            return len(d["rows"])
        self.bg_task(work, lambda n: self.toast(f"Сохранено: {path} ({fmt_n(n)} меток)"))

    # --- разброс ---
    def _scatter_restore(self):
        self.scatter_base = self.settings.get("scatterBase", "")
        self.scatter_source = self.settings.get("scatterSource", "")
        self._scatter_labels()

    def _scatter_labels(self):
        base_txt = ("база: " + self.scatter_base) if self.scatter_base \
            else "базовая папка не выбрана"
        if hasattr(self, "enr_base_lbl"):    # общая база с карточкой обогащения
            self.enr_base_lbl.configure(text=base_txt)
        self.sc_base_lbl.configure(text=base_txt)
        self.sc_src_lbl.configure(
            text=("экспорт: " + self.scatter_source) if self.scatter_source else "файл экспорта не выбран")
        self.sc_roots_lbl.configure(
            text="\n".join("прямая папка: " + r for r in self.scatter_roots))

    def scatter_pick_base(self):
        p = filedialog.askdirectory(title="Папка, в которой лежат папки республик")
        if p:
            self.scatter_base = p
            self.settings["scatterBase"] = p
            self._save_settings()
            self._scatter_labels()

    def scatter_pick_source(self):
        p = filedialog.askopenfilename(
            title="Экспорт кейсов (формат «Хард»)",
            filetypes=[("Экспорт кейсов", "*.xlsb *.xlsx *.xlsm *.xls")])
        if p:
            self.scatter_source = p
            self.settings["scatterSource"] = p
            self._save_settings()
            self._scatter_labels()

    def scatter_add_root(self):
        p = filedialog.askdirectory(title="Прямая папка портфелей (в обход маски)")
        if p and p not in self.scatter_roots:
            self.scatter_roots.append(p)
            self._scatter_labels()

    def scatter_run(self, apply: bool, which: str = "all"):
        if apply:
            self.toast(razbros.LEGACY_SCATTER_DISABLED_MESSAGE, err=True)
            return
        if not self.scatter_base and not self.scatter_roots:
            self.toast("Выбери базовую папку (или прямую папку)", err=True)
            return
        if not self.scatter_source:
            self.toast("Выбери файл экспорта кейсов", err=True)
            return
        only = None
        if apply and which == "selected":
            only = [m["employee"] for m in self.scatter_matched
                    if m["employee"].lower() in self.scatter_selected]
            if not only:
                self.toast("Никто не выбран", err=True)
                return
        if apply:
            who = f"выбранным ({len(only)})" if only else "ВСЕМ найденным"
            if not messagebox.askyesno(
                    "Разброс кейсов",
                    f"Разложить кейсы по личным файлам — {who}?\nЛичные .xlsb будут "
                    "перезаписаны свежими строками (личная «Статистика» переносится). "
                    "Делает Excel — может занять время."):
                return
        self.sc_warn.configure(
            text="Раскладываю через Excel…" if apply else "Проверяю совпадения…")
        base, roots, source = self.scatter_base, list(self.scatter_roots), self.scatter_source
        def work():
            if apply:   # Excel/COM в фоновом потоке требует явной инициализации
                try:
                    import pythoncom
                    pythoncom.CoInitialize()
                except ImportError:
                    pass
            reps = razbros.load_config(None)
            rts = razbros.resolve_roots(base or None, reps, roots)
            fmap = razbros.build_folder_map(rts)
            emps = razbros.read_source_employees(Path(source))
            if apply:
                sel = {o.strip().lower() for o in only} if only else None
                return True, razbros.apply_scatter(Path(source), fmap, emps, sel)
            return False, razbros.make_report(fmap, emps)
        def done(res):
            was_apply, d = res
            self.sc_warn.configure(text="")
            if was_apply:
                skipped = d.get("skipped") or []
                self.sc_kvs.set_pairs([("записано файлов", d.get("written_count", 0)),
                                       ("без папки (пропущено)", len(skipped))])
                self.sc_warn.configure(
                    text=("Без папки на диске: " + ", ".join(skipped)) if skipped else "")
                self.toast(f"Разброс готов: {d.get('written_count', 0)} файлов")
                return
            c = d.get("counts") or {}
            self.scatter_matched = d.get("matched") or []
            self.sc_kvs.set_pairs([
                ("в экспорте сотрудников", d.get("employees_total", 0)),
                ("нашли папку", c.get("matched", 0)),
                ("без папки", c.get("unmatched", 0)),
                ("лишние папки", c.get("extra", 0))])
            warn = ""
            if d.get("unmatched"):
                warn = f"Без папки на диске ({len(d['unmatched'])}): " + ", ".join(d["unmatched"])
            if d.get("extra_folders"):
                warn += ("\n" if warn else "") + "Папки без строк в экспорте: " \
                        + ", ".join(d["extra_folders"])
            self.sc_warn.configure(text=warn)
            self._render_scatter_pick()
        self.bg_task(work, done, lambda e: (self.sc_warn.configure(text=f"Ошибка: {e}"),
                                            self.toast(str(e), err=True)))

    def _render_scatter_pick(self):
        for w in self.sc_pick_frame.winfo_children():
            w.destroy()
        self.sc_apply_row.pack_forget()
        if not self.scatter_matched:
            return
        head = tk.Frame(self.sc_pick_frame, bg=CARD)
        head.pack(fill="x", pady=(10, 4))
        tk.Label(head, text="Выбор поимённо (для «Разложить выбранных»):",
                 bg=CARD, fg=MUTED, font=f(9)).pack(side="left")
        btn(head, "все", lambda: self._scatter_all(True)).pack(side="right")
        btn(head, "снять", lambda: self._scatter_all(False)).pack(side="right", padx=(0, 6))
        box = tk.Frame(self.sc_pick_frame, bg=CARD, highlightbackground=BORDER,
                       highlightthickness=1)
        box.pack(fill="x")
        self._sc_vars: dict[str, tk.BooleanVar] = {}
        for m in self.scatter_matched:
            k = m["employee"].lower()
            var = tk.BooleanVar(value=k in self.scatter_selected)
            self._sc_vars[k] = var
            where = " · ".join(list(m.get("republics") or []) + list(m.get("cities") or []))
            row = tk.Frame(box, bg=CARD)
            row.pack(fill="x")
            cb = tk.Checkbutton(row, text=m["employee"], variable=var, bg=CARD, fg=TEXT,
                                activebackground=CARD, selectcolor=CARD, font=f(9),
                                command=lambda k=k, v=var: self._scatter_toggle(k, v.get()))
            cb.pack(side="left", padx=6)
            tk.Label(row, text=where, bg=CARD, fg=MUTED, font=f(8)).pack(side="left")
        if razbros.LEGACY_SCATTER_ENABLED:
            self.sc_apply_row.pack(anchor="w", pady=(10, 0))
        self._scatter_seln()

    def _scatter_toggle(self, k, on):
        (self.scatter_selected.add if on else self.scatter_selected.discard)(k)
        self._scatter_seln()

    def _scatter_all(self, on):
        for m in self.scatter_matched:
            k = m["employee"].lower()
            (self.scatter_selected.add if on else self.scatter_selected.discard)(k)
            if k in self._sc_vars:
                self._sc_vars[k].set(on)
        self._scatter_seln()

    def _scatter_seln(self):
        n = sum(1 for m in self.scatter_matched
                if m["employee"].lower() in self.scatter_selected)
        self.sc_apply_sel_btn.configure(text=f"Разложить выбранных ({n})",
                                        state="normal" if n else "disabled")

    # --- очистка/геокодинг/период ---
    def do_reset(self, scope):
        if not LIMITED_TEST_RESET_UI_ENABLED:
            self.reset_out.configure(text=LIMITED_TEST_RESET_DISABLED_MESSAGE)
            self.toast(LIMITED_TEST_RESET_DISABLED_MESSAGE, err=True)
            return
        msg = ("ПОЛНОСТЬЮ очистить реестр?\nУдалятся ВСЕ клиенты, визиты и метки, без "
               "возможности отката (привязки сотрудников останутся)."
               if scope == "all" else
               "Сбросить данные визитов у всех клиентов?\nОбнулятся статусы, комментарии, "
               "даты визита и фото. Клиенты, долги и закрепление останутся.")
        if not messagebox.askyesno("Очистка данных", msg):
            return
        def done(d):
            txt = (f"Реестр очищен · было клиентов: {fmt_n(d.get('clients_removed'))}"
                   if scope == "all" else
                   f"Данные визитов сброшены · клиентов: {fmt_n(d.get('clients_reset'))}")
            self.reset_out.configure(text=txt)
            self.toast(txt)
            self.refresh()
        self.bg_task(lambda: self.api("POST", "/admin/reset", {"scope": scope}), done)

    def _show_period(self, d):
        self.p_from.delete(0, "end")
        self.p_from.insert(0, d.get("from") or "")
        self.p_to.delete(0, "end")
        self.p_to.insert(0, d.get("to") or "")
        t = (f"Активен: {d.get('from') or '…'} — {d.get('to') or '…'}"
             if d.get("from") or d.get("to") else "Период не задан — видны все.")
        self.period_cur.configure(text=t)
        self.period_dash.configure(text=t)

    def apply_period(self):
        try:
            body = {"from": to_iso(self.p_from.get()), "to": to_iso(self.p_to.get())}
        except ValueError as e:
            self.toast(str(e), err=True)
            return
        self.bg_task(lambda: self.api("POST", "/admin/period", body),
                     lambda d: (self._show_period(d), self.toast("Период сохранён")))

    def clear_period(self):
        self.p_from.delete(0, "end")
        self.p_to.delete(0, "end")
        self.apply_period()

    # ══ ПРИОРИТЕТ ═══════════════════════════════════════════════════════
    def _build_prio(self, fr):
        inner = self._section_inner(fr)
        wcard = Card(inner, "Веса скоринга",
                     "Порядок выезда считается по ОЖИДАЕМОМУ СБОРУ: вероятность оплаты × "
                     "сумма, по модели на реальном портфеле. Долг и свежесть теперь внутри "
                     "модели — эти ползунки на порядок не влияют. «Кластер» влияет: он про "
                     "стоимость объезда, а не про деньги.")
        wcard.pack(fill="x")
        row = tk.Frame(wcard.inner, bg=CARD)
        row.pack(fill="x")
        self.weights: dict[str, tk.DoubleVar] = {}   # DoubleVar: ttk.Scale пишет дробные
        for i, (key, label, init) in enumerate([("debt", "Долг", 50),
                                                ("cluster", "Кластер", 30),
                                                ("fresh", "Свежесть", 20)]):
            cell = tk.Frame(row, bg=CARD)
            cell.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 14, 0))
            row.grid_columnconfigure(i, weight=1)
            var = tk.DoubleVar(value=init)
            self.weights[key] = var
            lab = tk.Label(cell, text=f"{label} {init}", bg=CARD, fg=MUTED, font=f(9))
            lab.pack(anchor="w")
            s = ttk.Scale(cell, from_=0, to=100, variable=var,
                          command=lambda v, l=lab, t=label, vv=var:
                          l.configure(text=f"{t} {int(vv.get())}"))
            s.pack(fill="x")
        btn(row, "Пересчитать", self.load_priority, kind="accent").grid(
            row=0, column=3, padx=(16, 0), sticky="s")

        ccard = Card(inner, "Кластеры городов")
        ccard.pack(fill="x", pady=(12, 0))
        self.cluster_chips = ChipFlow(ccard.inner)
        self.cluster_chips.pack(fill="x")
        self.cluster_chips.set_chips(["—"])

        tcard = Card(inner, "Куда ехать — топ-40")
        tcard.pack(fill="x", pady=(12, 0))
        # «Ожидание» — главная колонка: сколько денег принесёт выезд. Score оставлен
        # как относительная шкала 0–100 внутри текущей выборки.
        cols = ("n", "client", "city", "debt", "exp", "visits", "score", "mode", "why")
        heads = ("#", "Клиент", "Город", "Долг ₽", "Ожидание ₽", "Выезд.", "Score",
                 "Режим", "Почему")
        widths = (34, 180, 100, 90, 100, 56, 56, 84, 300)
        self.prio_tv = ttk.Treeview(tcard.inner, columns=cols, show="headings", height=18)
        for c, h, w in zip(cols, heads, widths):
            self.prio_tv.heading(c, text=h)
            anchor = "e" if c in ("debt", "exp", "visits", "score", "n") else "w"
            self.prio_tv.column(c, width=self.px(w), anchor=anchor,
                                stretch=(c in ("client", "why")))
        self.prio_tv.pack(fill="x")

    def load_priority(self):
        q = (f"?w_debt={int(self.weights['debt'].get())}"
             f"&w_cluster={int(self.weights['cluster'].get())}"
             f"&w_fresh={int(self.weights['fresh'].get())}&limit=40")
        def done(d):
            self._loaded.add("prio")
            cities = d.get("cities") or []
            self.cluster_chips.set_chips(
                [f"{c['name']}  {fmt_n(c['count'])}" for c in cities]
                or ["городов нет — реестр пуст или нет координат"])
            self.prio_tv.delete(*self.prio_tv.get_children())
            for i, it in enumerate(d.get("items") or [], 1):
                self.prio_tv.insert("", "end", values=(
                    i, it.get("client", ""), it.get("city", ""), fmt_n(it.get("debt") or 0),
                    fmt_n(round(it.get("expected") or 0)), it.get("visits", 0),
                    f"{it.get('score', 0):.1f}",
                    it.get("mode", ""), it.get("reason", "")))
        self.bg_task(lambda: self.api("GET", "/admin/priority" + q), done)

    # ══ ЛЮДИ ════════════════════════════════════════════════════════════
    def _build_people(self, fr):
        inner = self._section_inner(fr)
        rc = Card(inner, "Назначение ролей",
                  "ФИО берутся из реестра (колонка «ответственный»). Впиши Telegram ID "
                  "сотруднику (или выбери из писавших боту), тип — и «Привязать»: работник "
                  "с этим ID сразу привязан к своим клиентам. Можно переназначать.")
        rc.pack(fill="x")
        self.roles_frame = tk.Frame(rc.inner, bg=CARD)
        self.roles_frame.pack(fill="x")
        tk.Label(self.roles_frame, text="Нет данных — импортируй реестр и нажми «Обновить»",
                 bg=CARD, fg=MUTED, font=f(9)).pack(anchor="w")

        bc = Card(inner, "Кто вошёл в бот",
                  "Активные привязки telegram_id → сотрудник. «Сбросить» — отвязать.")
        bc.pack(fill="x", pady=(12, 0))
        self.binds_frame = tk.Frame(bc.inner, bg=CARD)
        self.binds_frame.pack(fill="x")

        uc = Card(inner, "Убрать клиентов у сотрудника",
                  "Выбери сотрудника — появится его список. Отметь галочками лишних и "
                  "нажми «Снять выбранных»: договор остаётся в реестре (без "
                  "ответственного) и пропадает из приложения сотрудника.")
        uc.pack(fill="x", pady=(12, 0))
        urow = tk.Frame(uc.inner, bg=CARD)
        urow.pack(fill="x")
        self.un_emp = ttk.Combobox(urow, values=[], state="readonly", width=32, font=f(9))
        self.un_emp.pack(side="left")
        self.un_emp.bind("<<ComboboxSelected>>", lambda _e: self.load_emp_clients())
        self.un_btn = btn(urow, "Снять выбранных (0)", self.do_unassign, kind="danger")
        self.un_btn.configure(state="disabled")
        self.un_btn.pack(side="right")
        self.un_frame = tk.Frame(uc.inner, bg=CARD)
        self.un_frame.pack(fill="x", pady=(8, 0))
        self._un_vars: dict[str, tk.BooleanVar] = {}

    @staticmethod
    def _fmt_seen(iso):
        """«был 16.07 12:01» — отличать живой аккаунт от брошенного тёзки: у человека
        бывает два телеграма с одним именем, роль надо давать тому, с которого пишут."""
        try:
            d = datetime.fromisoformat(str(iso or ""))
        except ValueError:
            return ""
        return f" · был {d.day:02d}.{d.month:02d} {d.hour:02d}:{d.minute:02d}"

    def _guest_label(self, g):
        who = g.get("name") or (("@" + g["username"]) if g.get("username") else "без имени")
        nick = (" · @" + g["username"]) if g.get("username") else ""
        busy = ("  ⚠ уже: " + g["bound_employee"]) if g.get("bound_employee") else ""
        return f"{who}{nick} · {g['telegram_id']}{self._fmt_seen(g.get('last_seen'))}{busy}"

    def load_roles(self):
        def work():
            try:
                guests = self.api("GET", "/admin/guests").get("guests") or []
            except RuntimeError:
                guests = []
            emps = self.api("GET", "/admin/employees").get("employees") or []
            return guests, emps
        def done(res):
            self._loaded.add("people")
            self.guests, emps = res
            self.un_emp["values"] = [e["employee"] for e in emps]
            frame = self.roles_frame
            for w in frame.winfo_children():
                w.destroy()
            if not emps:
                tk.Label(frame, text="Реестр пуст — импортируй xlsx на вкладке «Реестр»",
                         bg=CARD, fg=MUTED, font=f(9)).pack(anchor="w")
                return
            head = tk.Frame(frame, bg=CARD)
            head.pack(fill="x")
            for text, w in (("СОТРУДНИК", 26), ("ДОГОВОРОВ", 10), ("TELEGRAM ID", 34),
                            ("ТИП", 13), ("СТАТУС", 12)):
                tk.Label(head, text=text, bg=CARD, fg=MUTED, font=(FONT, 8, "bold"),
                         width=w, anchor="w").pack(side="left")
            glabels = [self._guest_label(g) for g in self.guests]
            self._role_rows = []
            for e in emps:
                row = tk.Frame(frame, bg=CARD)
                row.pack(fill="x", pady=2)
                tk.Label(row, text=e["employee"], bg=CARD, fg=TEXT, font=f(9),
                         width=26, anchor="w").pack(side="left")
                tk.Label(row, text=fmt_n(e.get("contracts")), bg=CARD, fg=TEXT,
                         font=f(9), width=10, anchor="w").pack(side="left")
                idcell = tk.Frame(row, bg=CARD)
                idcell.pack(side="left")
                combo = ttk.Combobox(idcell, values=["— из писавших боту —"] + glabels,
                                     state="readonly", width=40, font=f(8))
                combo.current(0)
                combo.pack(anchor="w")
                tid = entry(idcell, width=16)
                if e.get("telegram_id"):
                    tid.insert(0, str(e["telegram_id"]))
                tid.pack(anchor="w", pady=(2, 0))
                combo.bind("<<ComboboxSelected>>",
                           lambda _e, c=combo, t=tid: self._pick_guest(c, t))
                kind = ttk.Combobox(row, values=["Выездник", "Курьер ГПХ"],
                                    state="readonly", width=11, font=f(8))
                kind.current(1 if e.get("kind") == "courier" else 0)
                kind.pack(side="left", padx=(6, 6))
                status = "привязан ✓" if e.get("telegram_id") else "не назначен"
                tk.Label(row, text=status, bg=CARD,
                         fg=SUCCESS if e.get("telegram_id") else MUTED,
                         font=f(9), width=12, anchor="w").pack(side="left")
                btn(row, "Привязать",
                    lambda emp=e["employee"], t=tid, k=kind: self.bind_role(emp, t, k),
                    kind="accent").pack(side="left")
        self.bg_task(work, done)

    def _pick_guest(self, combo, tid_entry):
        i = combo.current()
        if i > 0:
            tid_entry.delete(0, "end")
            tid_entry.insert(0, str(self.guests[i - 1]["telegram_id"]))

    def bind_role(self, employee, tid_entry, kind_combo):
        tid = tid_entry.get().strip()
        if not re.fullmatch(r"\d{3,}", tid):
            self.toast("Впиши числовой Telegram ID (или выбери гостя из списка)", err=True)
            return
        kind = "courier" if kind_combo.get() == "Курьер ГПХ" else "agent"
        body = {"telegram_id": int(tid), "employee": employee, "kind": kind}
        self.bg_task(lambda: self.api("POST", "/admin/bind", body),
                     lambda _d: (self.toast(f"Привязано: {employee} → {tid}"), self.refresh()))

    def _render_binds(self, bindings):
        frame = self.binds_frame
        for w in frame.winfo_children():
            w.destroy()
        if not bindings:
            tk.Label(frame, text="Никто ещё не привязан", bg=CARD, fg=MUTED,
                     font=f(9)).pack(anchor="w")
            return
        for b in bindings:
            row = tk.Frame(frame, bg=CARD)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=str(b["telegram_id"]), bg=CARD, fg=TEXT, font=f(9),
                     width=14, anchor="w").pack(side="left")
            tk.Label(row, text=b["employee"], bg=CARD, fg=TEXT, font=f(9),
                     width=30, anchor="w").pack(side="left")
            tk.Label(row, text="курьер" if b.get("kind") == "courier" else "выездник",
                     bg=ALT, fg=MUTED, font=f(8), padx=8).pack(side="left")
            btn(row, "сбросить",
                lambda t=b["telegram_id"]: self.unbind(t), kind="danger").pack(side="right")

    def unbind(self, tid):
        if not messagebox.askyesno("Сброс привязки", f"Сбросить выбор для {tid}?"):
            return
        self.bg_task(lambda: self.api("POST", "/admin/unbind", {"telegram_id": tid}),
                     lambda _d: (self.toast("Сброшено"), self.refresh()))

    # ── Убрать клиентов у сотрудника ─────────────────────────────────────
    def load_emp_clients(self):
        emp = self.un_emp.get().strip()
        if not emp:
            return
        path = "/admin/emp_clients?employee=" + urllib.parse.quote(emp)
        def done(d):
            frame = self.un_frame
            for w in frame.winfo_children():
                w.destroy()
            self._un_vars = {}
            clients = d.get("clients") or []
            if not clients:
                tk.Label(frame, text="За сотрудником сейчас никого нет", bg=CARD,
                         fg=MUTED, font=f(9)).pack(anchor="w")
                self._un_seln()
                return
            head = tk.Frame(frame, bg=CARD)
            head.pack(fill="x", pady=(0, 4))
            tk.Label(head, text=f"Договоров: {len(clients)}", bg=CARD, fg=MUTED,
                     font=f(9)).pack(side="left")
            btn(head, "все", lambda: self._un_all(True)).pack(side="right")
            btn(head, "ничего", lambda: self._un_all(False)).pack(side="right", padx=(0, 6))
            box = tk.Frame(frame, bg=CARD, highlightbackground=BORDER,
                           highlightthickness=1)
            box.pack(fill="x")
            for c in clients:
                var = tk.BooleanVar(value=False)
                self._un_vars[c["contract_id"]] = var
                row = tk.Frame(box, bg=CARD)
                row.pack(fill="x")
                tk.Checkbutton(row, text=f'{c.get("client") or "—"} · КД {c["contract_id"]}',
                               variable=var, bg=CARD, fg=TEXT, activebackground=CARD,
                               selectcolor=CARD, font=f(9),
                               command=self._un_seln).pack(side="left", padx=6)
                debt_s = fmt_n(c.get("debt")) + (f' {c["unit"]}' if c.get("unit") else "")
                tk.Label(row, text=debt_s, bg=CARD, fg=MUTED,
                         font=f(8)).pack(side="right", padx=8)
            self._un_seln()
        self.bg_task(lambda: self.api("GET", path), done)

    def _un_all(self, on):
        for v in self._un_vars.values():
            v.set(on)
        self._un_seln()

    def _un_seln(self):
        n = sum(1 for v in self._un_vars.values() if v.get())
        self.un_btn.configure(text=f"Снять выбранных ({n})",
                              state="normal" if n else "disabled")

    def do_unassign(self):
        emp = self.un_emp.get().strip()
        ids = [k for k, v in self._un_vars.items() if v.get()]
        if not emp or not ids:
            return
        if not messagebox.askyesno(
                "Убрать клиентов",
                f"Снять {len(ids)} договор(ов) с «{emp}»?\n\nКлиенты останутся в "
                "реестре без ответственного и пропадут из приложения сотрудника."):
            return
        def done(d):
            self.toast(f"Снято: {d.get('n', 0)}")
            self.load_emp_clients()   # список пересобирается, счётчики — через refresh
            self.refresh()
        self.bg_task(lambda: self.api("POST", "/admin/unassign",
                                      {"employee": emp, "contract_ids": ids}), done)

    # ══ РУКОВОДИТЕЛИ ═════════════════════════════════════════════════════
    # --- беседы отчётов о выездах ---
    def chats_refresh(self):
        def done(d):
            self._chats_emp = d.get("employees") or []
            self._render_chats(d.get("chats") or [])
        self.bg_task(lambda: self.api("GET", "/admin/chats"), done)

    def _render_chats(self, chats):
        for w in self.chats_list.winfo_children():
            w.destroy()
        if not chats:
            tk.Label(self.chats_list,
                     text="Бесед пока нет — добавь бота в группу, и она появится здесь",
                     bg=CARD, fg=MUTED, font=f(9)).pack(anchor="w")
            return
        # Title подделываем кто угодно (любой может создать группу-двойника и
        # добавить бота) — рядом всегда показываем chat_id и дату, дубли имён
        # подсвечиваем, чтобы админ не включил чужую беседу.
        titles = [(c.get("title") or "").strip().lower() for c in chats]
        for c in chats:
            row = tk.Frame(self.chats_list, bg=CARD)
            row.pack(fill="x", pady=1)
            var = tk.BooleanVar(value=c.get("enabled", False))
            title = c.get("title") or "(без названия)"
            dup = titles.count((c.get("title") or "").strip().lower()) > 1
            label = f"{title}  ·  id {c.get('chat_id')}"
            if c.get("added_at"):
                label += f" · с {str(c['added_at'])[:10]}"
            if dup:
                label += " · ⚠ есть тёзка!"
            if c.get("removed"):
                label += " · бот удалён из беседы"
            tk.Checkbutton(row, text=label, variable=var, bg=CARD,
                           fg=WARN if dup else TEXT,
                           activebackground=CARD, selectcolor=CARD, font=f(9),
                           command=lambda cid=c["chat_id"], v=var:
                           self._chat_toggle(cid, v.get())).pack(side="left")
            emps = c.get("employees") or []
            short = (", ".join(emps[:3]) + ("…" if len(emps) > 3 else "")) if emps \
                else "сотрудники не закреплены — посты не идут"
            tk.Label(row, text=short, bg=CARD, fg=MUTED, font=f(8)).pack(
                side="left", padx=(8, 0))
            btn(row, f"Сотрудники ({len(emps)})",
                lambda c=c: self._chat_emps_dialog(c)).pack(side="right")

    def _chat_toggle(self, chat_id, on):
        # При сбое чекбокс НЕ должен показывать выключенное при включённом
        # сервере (посты с ПД продолжались бы «невидимо») — перечитываем правду.
        self.bg_task(lambda: self.api("POST", "/admin/chats.set",
                                      {"chat_id": chat_id, "enabled": on}),
                     lambda d: self.toast("Беседа включена" if on
                                          else "Беседа выключена"),
                     lambda e: (self.toast(f"Не сохранилось: {e}", err=True),
                                self.chats_refresh()))

    def _chat_emps_dialog(self, chat):
        top = tk.Toplevel(self)
        top.title(chat.get("title") or str(chat.get("chat_id")))
        top.configure(bg=CARD)
        tk.Label(top, text="Чьи визиты постить в эту беседу:", bg=CARD, fg=TEXT,
                 font=f(10, True)).pack(anchor="w", padx=12, pady=(12, 6))
        emps = self._chats_emp or []
        if not emps:
            tk.Label(top, text="Реестр пуст — сотрудников нет", bg=CARD, fg=MUTED,
                     font=f(9)).pack(anchor="w", padx=12)
        sel = set(chat.get("employees") or [])
        grid = tk.Frame(top, bg=CARD)
        grid.pack(fill="both", expand=True, padx=12)
        rows_per_col = max(1, (len(emps) + 1) // 2)
        chk_vars: dict[str, tk.BooleanVar] = {}
        for i, e in enumerate(emps):
            v = tk.BooleanVar(value=e in sel)
            chk_vars[e] = v
            tk.Checkbutton(grid, text=e, variable=v, bg=CARD, fg=TEXT,
                           activebackground=CARD, selectcolor=CARD, font=f(9)
                           ).grid(row=i % rows_per_col, column=i // rows_per_col,
                                  sticky="w", padx=(0, 12))
        def save():
            chosen = [e for e, v in chk_vars.items() if v.get()]
            def done(d):
                self.toast("Сотрудники беседы сохранены")
                top.destroy()
                self._chats_emp = [x for x in self._chats_emp]
                self._render_chats(d.get("chats") or [])
            self.bg_task(lambda: self.api(
                "POST", "/admin/chats.set",
                {"chat_id": chat["chat_id"], "employees": chosen}), done)
        btn(top, "Сохранить", save, kind="accent").pack(anchor="e", padx=12, pady=12)

    def _build_mgrs(self, fr):
        inner = self._section_inner(fr)
        dc = Card(inner, "Автосводка руководителям",
                  "В конце дня (по Москве) каждый руководитель получает дайджест по своим "
                  "сотрудникам. Реалтайм — кнопка «Дайджест за сегодня» в боте.")
        dc.pack(fill="x")
        rd = tk.Frame(dc.inner, bg=CARD)
        rd.pack(anchor="w")
        tk.Label(rd, text="Время (МСК)", bg=CARD, fg=MUTED, font=f(9)).pack(side="left")
        self.dig_time = entry(rd, width=7)
        self.dig_time.insert(0, "16:30")
        self.dig_time.pack(side="left", padx=(8, 12))
        self.dig_enabled = tk.BooleanVar(value=True)
        tk.Checkbutton(rd, text="автопуш включён", variable=self.dig_enabled, bg=CARD,
                       fg=TEXT, activebackground=CARD, selectcolor=CARD,
                       font=f(9)).pack(side="left")
        btn(rd, "Сохранить", self.save_digest, kind="accent").pack(side="left", padx=(12, 0))
        rp = tk.Frame(dc.inner, bg=CARD)
        rp.pack(anchor="w", pady=(6, 0))
        tk.Label(rp, text="Обещания (МСК)", bg=CARD, fg=MUTED, font=f(9)).pack(side="left")
        self.prom_time = entry(rp, width=7)
        self.prom_time.insert(0, "09:30")
        self.prom_time.pack(side="left", padx=(8, 12))
        self.prom_enabled = tk.BooleanVar(value=True)
        tk.Checkbutton(rp, text="утренний пуш «обещание просрочено»",
                       variable=self.prom_enabled, bg=CARD, fg=TEXT,
                       activebackground=CARD, selectcolor=CARD, font=f(9)).pack(side="left")
        self.dig_out = tk.Label(dc.inner, text="", bg=CARD, fg=MUTED, font=f(9))
        self.dig_out.pack(anchor="w", pady=(6, 0))

        rc = Card(inner, "Пересылка отчётов в беседы",
                  "Закрытый визит уходит постом в закреплённые беседы — как сотрудники "
                  "пишут руками, только автоматически: клиент, адрес, статусы, "
                  "комментарий, фото и фактические гео-точки (закрытие визита и съёмка "
                  "фото). Добавь бота в беседу — она появится в списке; включи её и "
                  "закрепи сотрудников (ФИО из реестра = их привязанные телеграмы).")
        rc.pack(fill="x", pady=(12, 0))
        btn(rc.inner, "Обновить список бесед", self.chats_refresh).pack(anchor="w")
        self.chats_list = tk.Frame(rc.inner, bg=CARD)
        self.chats_list.pack(fill="x", pady=(8, 0))
        tk.Label(self.chats_list, text="Список загрузится при открытии вкладки…",
                 bg=CARD, fg=MUTED, font=f(9)).pack(anchor="w")
        self._chats_emp: list[str] = []

        mc = Card(inner, "Руководители",
                  "Каждый привязан к городам (подпись сводки) и своим сотрудникам "
                  "(по ним считается дайджест).")
        mc.pack(fill="x", pady=(12, 0))
        self.mgr_list = tk.Frame(mc.inner, bg=CARD)
        self.mgr_list.pack(fill="x")
        tk.Label(self.mgr_list, text="Пока никого — добавьте ниже", bg=CARD, fg=MUTED,
                 font=f(9)).pack(anchor="w")

        fc = Card(inner, "Добавить / изменить руководителя")
        fc.pack(fill="x", pady=(12, 0))
        self.mgr_form_card = fc
        tk.Label(fc.inner, text="Кто это (из писавших боту)", bg=CARD, fg=MUTED,
                 font=f(9)).pack(anchor="w")
        self.mgr_guest_combo = ttk.Combobox(fc.inner, values=["— никто ещё не писал боту —"],
                                            state="readonly", width=58, font=f(8))
        self.mgr_guest_combo.current(0)
        self.mgr_guest_combo.pack(anchor="w", pady=(2, 6))
        self.mgr_guest_combo.bind("<<ComboboxSelected>>", self._mgr_pick_guest)
        rowt = tk.Frame(fc.inner, bg=CARD)
        rowt.pack(anchor="w", fill="x")
        tk.Label(rowt, text="Telegram ID", bg=CARD, fg=MUTED, font=f(9)).pack(side="left")
        self.mgr_tid = entry(rowt, width=15)
        self.mgr_tid.pack(side="left", padx=(8, 16))
        tk.Label(rowt, text="Имя (для приветствия)", bg=CARD, fg=MUTED,
                 font=f(9)).pack(side="left")
        self.mgr_name = entry(rowt, width=26)
        self.mgr_name.pack(side="left", padx=(8, 0))

        tk.Label(fc.inner, text="ГОРОДА (ПОДПИСЬ СВОДКИ)", bg=CARD, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(anchor="w", pady=(12, 2))
        self.mgr_city_chips = ChipFlow(fc.inner)
        self.mgr_city_chips.pack(fill="x")
        rowc = tk.Frame(fc.inner, bg=CARD)
        rowc.pack(anchor="w", pady=(4, 0))
        self.mgr_city_add = entry(rowc, width=24)
        self.mgr_city_add.pack(side="left")
        self.mgr_city_add.bind("<Return>", lambda _e: self._mgr_add_city())
        btn(rowc, "＋ добавить город", self._mgr_add_city).pack(side="left", padx=(8, 0))

        rowh = tk.Frame(fc.inner, bg=CARD)
        rowh.pack(fill="x", pady=(12, 2))
        tk.Label(rowh, text="СОТРУДНИКИ (ПО НИМ СЧИТАЕТСЯ ДАЙДЖЕСТ)", bg=CARD, fg=MUTED,
                 font=(FONT, 8, "bold")).pack(side="left")
        self.mgr_emp_hint = tk.Label(rowh, text="", bg=CARD, fg=MUTED, font=f(8))
        self.mgr_emp_hint.pack(side="right")
        btn(rowh, "снять", lambda: self._mgr_emps_all(False)).pack(side="right", padx=(0, 8))
        btn(rowh, "выбрать всех", lambda: self._mgr_emps_all(True)).pack(side="right", padx=(0, 6))
        self.mgr_emp_chips = ChipFlow(fc.inner)
        self.mgr_emp_chips.pack(fill="x")

        rows = tk.Frame(fc.inner, bg=CARD)
        rows.pack(anchor="w", pady=(14, 0))
        btn(rows, "Сохранить руководителя", self.save_manager, kind="accent").pack(side="left")
        btn(rows, "Очистить форму", self._mgr_form_reset).pack(side="left", padx=(8, 0))

    def load_managers(self):
        def work():
            try:
                guests = self.api("GET", "/admin/guests").get("guests") or []
            except RuntimeError:
                guests = []
            d = self.api("GET", "/admin/managers")
            return guests, d
        def done(res):
            self._loaded.add("mgrs")
            self.guests, d = res
            self.mgrs = d.get("managers") or []
            self.mgr_cities = d.get("cities") or []
            self.mgr_emps = d.get("employees") or []
            self.chats_refresh()   # карточка бесед не должна висеть пустой
            self.mgr_city_emps = d.get("city_employees") or {}
            dig = d.get("digest") or {}
            if dig:
                self.dig_time.delete(0, "end")
                self.dig_time.insert(0, dig.get("time") or "16:30")
                self.dig_enabled.set(bool(dig.get("enabled")))
                self.prom_time.delete(0, "end")
                self.prom_time.insert(0, dig.get("promise_time") or "09:30")
                self.prom_enabled.set(bool(dig.get("promise_enabled", True)))
            self.mgr_guest_combo.configure(
                values=["— из писавших боту —"] + [self._guest_label(g) for g in self.guests])
            self.mgr_guest_combo.current(0)
            self._render_mgr_table()
            self._render_mgr_chips()
        self.bg_task(work, done)

    def _render_mgr_table(self):
        for w in self.mgr_list.winfo_children():
            w.destroy()
        if not self.mgrs:
            tk.Label(self.mgr_list, text="Пока никого — добавьте ниже", bg=CARD,
                     fg=MUTED, font=f(9)).pack(anchor="w")
            return
        for m in self.mgrs:
            row = tk.Frame(self.mgr_list, bg=CARD)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=m.get("name") or "без имени", bg=CARD, fg=TEXT,
                     font=f(9, True), width=22, anchor="w").pack(side="left")
            tk.Label(row, text=" · ".join(m.get("cities") or []) or "—", bg=CARD,
                     fg=MUTED, font=f(9), width=30, anchor="w").pack(side="left")
            tk.Label(row, text=f"сотрудников: {len(m.get('employees') or [])}", bg=CARD,
                     fg=MUTED, font=f(9), width=16, anchor="w").pack(side="left")
            tk.Label(row, text=str(m["telegram_id"]), bg=CARD, fg=MUTED, font=f(9),
                     width=13, anchor="w").pack(side="left")
            btn(row, "удалить", lambda t=m["telegram_id"]: self.mgr_delete(t),
                kind="danger").pack(side="right")
            btn(row, "изменить", lambda t=m["telegram_id"]: self.mgr_edit(t)).pack(
                side="right", padx=(0, 6))

    def _render_mgr_chips(self):
        form = self.mgr_form
        reg = [c["name"] for c in self.mgr_cities]
        cities = reg + [c for c in sorted(form["cities"]) if c not in reg]
        def city_chip(c):
            cnt = next((x.get("count") for x in self.mgr_cities if x["name"] == c), None)
            text = f"{c} {cnt}" if cnt else c
            return (text, c in form["cities"], lambda c=c: self._mgr_toggle_city(c))
        self.mgr_city_chips.set_chips([city_chip(c) for c in cities]
                                      or ["городов в реестре нет — добавьте вручную"])
        reg_e = [e["employee"] for e in self.mgr_emps]
        emps = reg_e + [e for e in sorted(form["employees"]) if e not in reg_e]
        def emp_chip(e):
            cnt = next((x.get("contracts") for x in self.mgr_emps if x["employee"] == e), None)
            text = f"{e} {cnt}" if cnt else e
            return (text, e in form["employees"], lambda e=e: self._mgr_toggle_emp(e))
        self.mgr_emp_chips.set_chips([emp_chip(e) for e in emps]
                                     or ["сотрудников в реестре нет — импортируйте реестр"])
        self.mgr_emp_hint.configure(
            text=f"выбрано: {len(form['employees'])}" if form["employees"] else "")

    def _mgr_pick_guest(self, _e):
        """Из выбранного аккаунта берём и id, И имя.

        Раньше подставлялся только id, а имя оператор печатал руками — и никакая
        опечатка в выборе не была видна: запись «Михаил» со всеми городами уехала
        на посторонний аккаунт и четыре дня получала полный дайджест."""
        i = self.mgr_guest_combo.current()
        if i <= 0:
            return
        g = self.guests[i - 1]
        self.mgr_tid.delete(0, "end")
        self.mgr_tid.insert(0, str(g["telegram_id"]))
        if not self.mgr_name.get().strip():
            self.mgr_name.delete(0, "end")
            self.mgr_name.insert(0, (g.get("name") or g.get("username") or "").strip())

    def _mgr_toggle_city(self, c):
        form = self.mgr_form
        if c in form["cities"]:
            form["cities"].discard(c)
        else:
            form["cities"].add(c)
            for e in self.mgr_city_emps.get(c) or []:
                form["employees"].add(e)
        self._render_mgr_chips()

    def _mgr_add_city(self):
        v = self.mgr_city_add.get().strip()
        if not v:
            return
        self.mgr_city_add.delete(0, "end")
        self.mgr_form["cities"].add(v)
        for e in self.mgr_city_emps.get(v) or []:
            self.mgr_form["employees"].add(e)
        self._render_mgr_chips()

    def _mgr_toggle_emp(self, e):
        form = self.mgr_form
        form["employees"].discard(e) if e in form["employees"] else form["employees"].add(e)
        self._render_mgr_chips()

    def _mgr_emps_all(self, on):
        if on:
            self.mgr_form["employees"] = {e["employee"] for e in self.mgr_emps}
        else:
            self.mgr_form["employees"] = set()
        self._render_mgr_chips()

    def mgr_edit(self, tid):
        m = next((x for x in self.mgrs if str(x["telegram_id"]) == str(tid)), None)
        if not m:
            return
        self.mgr_form = {"tid": str(tid), "cities": set(m.get("cities") or []),
                         "employees": set(m.get("employees") or [])}
        self.mgr_tid.delete(0, "end")
        self.mgr_tid.insert(0, str(tid))
        self.mgr_name.delete(0, "end")
        self.mgr_name.insert(0, m.get("name") or "")
        self._render_mgr_chips()

    def _mgr_form_reset(self):
        self.mgr_form = {"tid": "", "cities": set(), "employees": set()}
        self.mgr_tid.delete(0, "end")
        self.mgr_name.delete(0, "end")
        self.mgr_city_add.delete(0, "end")
        self.mgr_guest_combo.current(0)
        self._render_mgr_chips()

    def save_manager(self):
        tid = self.mgr_tid.get().strip()
        if not re.fullmatch(r"\d{3,}", tid):
            self.toast("Впиши Telegram ID руководителя (или выбери из списка)", err=True)
            return
        if not self.mgr_form["employees"]:
            self.toast("Отметь хотя бы одного сотрудника", err=True)
            return
        # Права получает ВЛАДЕЛЕЦ telegram_id, а не тот, чьё имя вписали в форму.
        # Если такой id боту ни разу не писал, сверить некому — спрашиваем явно.
        known = next((g for g in self.guests if str(g["telegram_id"]) == tid), None)
        if not known and not messagebox.askyesno(
                "Незнакомый аккаунт",
                f"Аккаунт {tid} боту ещё не писал, поэтому проверить, кто это, нельзя.\n\n"
                "Руководителю уходит полная сводка по его сотрудникам: ФИО должников, "
                "суммы, просроченные обещания, фото выездов.\n\nВсё равно выдать доступ?"):
            return
        body = {"telegram_id": int(tid), "name": self.mgr_name.get().strip(),
                "cities": sorted(self.mgr_form["cities"]),
                "employees": sorted(self.mgr_form["employees"])}
        def done(d):
            # Показываем НЕ то, что напечатал оператор, а кого сервер увидел за этим id.
            acc = (d or {}).get("account") or {}
            real = (acc.get("name") or acc.get("username") or "").strip()
            typed = body["name"].strip()
            if names_look_different(typed, real):
                messagebox.showwarning(
                    "Имя не совпало",
                    f"Ты вписал «{typed}», а телеграм-аккаунт {tid} принадлежит "
                    f"«{real}».\n\nДоступ выдан ВЛАДЕЛЬЦУ аккаунта. Если это не тот "
                    "человек — удали руководителя и заведи заново с верным id.")
            self.toast("Руководитель сохранён: " + (real or typed or tid))
            self._mgr_form_reset()
            self.load_managers()
        self.bg_task(lambda: self.api("POST", "/admin/manager", body), done)

    def mgr_delete(self, tid):
        if not messagebox.askyesno("Удаление",
                                   f"Удалить руководителя {tid}? Он перестанет получать дайджест."):
            return
        self.bg_task(lambda: self.api("POST", "/admin/manager_delete", {"telegram_id": tid}),
                     lambda _d: (self.toast("Удалён"), self.load_managers()))

    def save_digest(self):
        t = self.dig_time.get().strip()
        pt = self.prom_time.get().strip()
        for v in (t, pt):
            if v and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", v):
                self.toast("Время в формате ЧЧ:ММ", err=True)
                return
        body = {"time": t, "enabled": self.dig_enabled.get(),
                "promise_time": pt, "promise_enabled": self.prom_enabled.get()}
        def done(d):
            self.dig_out.configure(
                text=f"Дайджест {'вкл' if d.get('enabled') else 'выкл'} · {d.get('time')} МСК"
                     f"   ·   Обещания {'вкл' if d.get('promise_enabled') else 'выкл'}"
                     f" · {d.get('promise_time')} МСК")
            self.toast("Настройки сохранены")
        self.bg_task(lambda: self.api("POST", "/admin/digest_settings", body), done)

    # ══ АКТИВНОСТЬ ══════════════════════════════════════════════════════
    def _build_activity(self, fr):
        inner = self._section_inner(fr)
        ac = Card(inner, "Активность работников",
                  "Каждый визит и метка пишутся в БД с датой — здесь сводка. Простой — "
                  "полных дней с последней активности; самые простаивающие сверху.")
        ac.pack(fill="x")
        cols = ("emp", "role", "v7", "v30", "m7", "m30", "last", "status")
        heads = ("Сотрудник", "Роль", "Визиты 7д", "30д", "Метки 7д", "30д",
                 "Последняя активность", "Статус")
        widths = (190, 80, 70, 55, 70, 55, 150, 120)
        self.act_tv = ttk.Treeview(ac.inner, columns=cols, show="headings", height=14)
        for c, h, w in zip(cols, heads, widths):
            self.act_tv.heading(c, text=h)
            self.act_tv.column(c, width=self.px(w),
                               anchor="e" if c in ("v7", "v30", "m7", "m30") else "w",
                               stretch=(c in ("emp", "status")))
        self.act_tv.pack(fill="x")

    def load_activity(self):
        def done(d):
            self._loaded.add("activity")
            self.act_tv.delete(*self.act_tv.get_children())
            for w in d.get("workers") or []:
                idle = w.get("idle_days")
                status = ("без активности" if idle is None else
                          "активен" if idle < 2 else
                          f"пауза {idle} дн" if idle <= 7 else f"простой {idle} дн")
                # ФИО привязки не совпало ни с одним «ответственным» в реестре —
                # обычно сотрудника переименовали в офисе. Портфель у него при
                # этом пустеет молча: приложение отвечает 200 и «никто не
                # закреплён», и найти причину было негде.
                if w.get("orphaned"):
                    status = "⚠ ФИО нет в реестре · " + status
                last = w.get("last_activity") or "—"
                if len(last) > 16:
                    last = last[:10] + " " + last[11:16]
                self.act_tv.insert("", "end", values=(
                    w.get("employee", ""), "курьер" if w.get("kind") == "courier" else "выездник",
                    fmt_n(w.get("visits_7d")), fmt_n(w.get("visits_30d")),
                    fmt_n(w.get("markers_7d")), fmt_n(w.get("markers_30d")), last, status))
        self.bg_task(lambda: self.api("GET", "/admin/activity"), done)

    # ══ ПОДКЛЮЧЕНИЕ ═════════════════════════════════════════════════════
    def _build_conn(self, fr):
        inner = self._section_inner(fr)
        sc = Card(inner, "Сервер")
        sc.pack(fill="x")
        tk.Label(sc.inner, text="Адрес сервера", bg=CARD, fg=MUTED, font=f(9)).pack(anchor="w")
        self.url_entry = entry(sc.inner)
        self.url_entry.insert(0, self.url)
        self.url_entry.pack(fill="x", pady=(2, 8))
        tk.Label(sc.inner, text="Admin-ключ", bg=CARD, fg=MUTED, font=f(9)).pack(anchor="w")
        rowk = tk.Frame(sc.inner, bg=CARD)
        rowk.pack(fill="x", pady=(2, 8))
        self.key_entry = entry(rowk, show="•")
        self.key_entry.insert(0, self.key)
        self.key_entry.pack(side="left", fill="x", expand=True)
        btn(rowk, "👁", self._toggle_key).pack(side="left", padx=(6, 0))
        rowc = tk.Frame(sc.inner, bg=CARD)
        rowc.pack(anchor="w")
        btn(rowc, "Привязать это устройство", self.device_enroll,
            kind="accent").pack(side="left")
        btn(rowc, "Подключиться", self._connect).pack(side="left", padx=(8, 0))
        btn(rowc, "Отвязать это устройство", self.device_unbind).pack(
            side="left", padx=(8, 0))
        self.key_out = tk.Label(sc.inner, text="", bg=CARD, fg=MUTED, font=f(8),
                                justify="left", wraplength=760)
        self.key_out.pack(anchor="w", pady=(8, 0))
        self._key_state()

        dv = Card(inner, "Привязанные устройства",
                  "Каждое устройство ходит своим токеном. Потерянный ноутбук "
                  "отзывается по одному — мастер-ключ менять не надо, остальные "
                  "продолжают работать. Отзыв возможен только мастер-ключом.")
        dv.pack(fill="x", pady=(12, 0))
        btn(dv.inner, "Обновить список", self.devices_load).pack(anchor="w")
        self.dev_frame = tk.Frame(dv.inner, bg=CARD)
        self.dev_frame.pack(fill="x", pady=(8, 0))

        tc = Card(inner, "Токен бота",
                  "Сменить бота (токен от @BotFather). После сохранения — «Перезапустить "
                  "бота»: токен применится, миниаппа развернётся в новом боте. Токен нигде "
                  "не показывается и не выгружается.")
        tc.pack(fill="x", pady=(12, 0))
        rowt = tk.Frame(tc.inner, bg=CARD)
        rowt.pack(fill="x", pady=(0, 8))
        self.token_entry = entry(rowt, show="•")
        self.token_entry.pack(side="left", fill="x", expand=True)
        btn(rowt, "👁", self._toggle_token).pack(side="left", padx=(6, 0))
        rowb = tk.Frame(tc.inner, bg=CARD)
        rowb.pack(anchor="w")
        btn(rowb, "Сменить бота", self.change_token, kind="accent").pack(side="left")
        btn(rowb, "Перезапустить бота", self.restart_bot).pack(side="left", padx=(8, 0))
        tk.Label(rowb, text="формат: цифры : буквы/цифры (≥30 символов)", bg=CARD,
                 fg=MUTED, font=f(8)).pack(side="left", padx=(10, 0))

    # --- привязка устройства -------------------------------------------------
    @staticmethod
    def device_fingerprint() -> str:
        """Стабильный отпечаток машины. Хэш, а не имя: в БД сервера не нужны ПД.

        Берём то, что не меняется от перезагрузки к перезагрузке: имя машины,
        MAC-адрес и имя пользователя. Совпадение отпечатка означает «тот же
        пульт на той же машине» — этого достаточно, чтобы повторная привязка
        заменяла прежний токен, а не плодила записи.
        """
        import getpass
        import hashlib
        import platform
        import uuid
        raw = f"{platform.node()}|{uuid.getnode()}|{getpass.getuser()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _key_state(self):
        if self.device_token:
            self.key_out.configure(
                text="Устройство привязано: пульт ходит своим токеном, мастер-ключ "
                     "здесь не хранится. Поле ключа нужно только чтобы привязать "
                     "новое устройство или перепривязать это.")
        else:
            self.key_out.configure(
                text="Устройство НЕ привязано — пульт ходит мастер-ключом. Введи "
                     "ключ и нажми «Привязать это устройство»: ключ уйдёт на сервер "
                     "один раз, в обмен на токен, и сохранён не будет.")

    def device_enroll(self):
        key = self.key_entry.get().strip()
        if not key:
            self.toast("Введи мастер-ключ — он нужен один раз", err=True)
            return
        name = simpledialog.askstring(
            "Имя устройства", "Как назвать это устройство?\n"
            "Оно будет видно в списке привязанных.",
            initialvalue=self.device_default_name(), parent=self)
        if name is None:
            return
        fingerprint = self.device_fingerprint()
        # Привязка идёт МАСТЕР-КЛЮЧОМ явно, а не через self.api: там уже мог
        # лежать токен прошлой привязки, и перепривязка ходила бы им.
        self.key, self.device_token = key, ""

        def work():
            return self.api("POST", "/admin/device/enroll.json",
                            {"device_id": fingerprint, "name": name.strip()})

        def done(d):
            token = (d or {}).get("token") or ""
            if not token:
                self.toast("Сервер не выдал токен", err=True)
                return
            self.device_token = token
            self.settings["device_token"] = token
            # Ключ НЕ сохраняем — в этом весь смысл: на диске остаётся токен,
            # который отзывается по одному и работает только с этой машины.
            self.settings.pop("key", None)
            self.key = ""
            try:
                self.key_entry.delete(0, "end")
            except tk.TclError:
                pass
            self._save_settings()
            self._key_state()
            self.toast("Устройство привязано — ключ больше не хранится")
            self.devices_load()
            self.refresh()

        def fail(exc):
            self.key = key
            self.toast(str(exc), err=True)

        self.bg_task(work, done, fail)

    @staticmethod
    def device_default_name() -> str:
        import platform
        return f"Пульт · {platform.node()}"[:60]

    def device_unbind(self):
        """Забыть токен на этой машине. На сервере запись остаётся живой.

        Отзыв на сервере — отдельное действие (мастер-ключом, из списка ниже):
        «я больше не хочу хранить токен здесь» и «этот токен больше не должен
        работать нигде» — разные вещи, и путать их опасно.
        """
        if not self.device_token:
            self.toast("Устройство и так не привязано", err=True)
            return
        if not messagebox.askyesno(
                "Отвязать устройство",
                "Забыть токен на этой машине? Пульт снова будет спрашивать "
                "мастер-ключ.\n\nНа сервере токен останется действующим — чтобы "
                "погасить его совсем, отзови устройство в списке ниже."):
            return
        self.device_token = ""
        self.settings.pop("device_token", None)
        self._save_settings()
        self.connected = False
        self._key_state()
        self.toast("Токен забыт на этой машине")

    def devices_load(self):
        def done(d):
            for w in self.dev_frame.winfo_children():
                w.destroy()
            devices = (d or {}).get("devices") or []
            if not devices:
                tk.Label(self.dev_frame, text="Привязанных устройств нет", bg=CARD,
                         fg=MUTED, font=f(9)).pack(anchor="w")
                return
            for dev in devices:
                row = tk.Frame(self.dev_frame, bg=CARD)
                row.pack(fill="x", pady=(0, 2))
                tk.Label(row, text=dev.get("name") or "(без имени)", bg=CARD, fg=TEXT,
                         font=f(9)).pack(side="left")
                seen = dev.get("last_seen") or "ни разу"
                tk.Label(row, text=f"  ·  привязано {dev.get('created_at') or '—'}"
                                   f"  ·  на связи: {seen}",
                         bg=CARD, fg=MUTED, font=f(8)).pack(side="left")
                btn(row, "Отозвать",
                    lambda i=dev.get("id"), n=dev.get("name"): self.device_revoke(i, n)
                    ).pack(side="right")
        self.bg_task(lambda: self.api("GET", "/admin/devices.json"), done)

    def device_revoke(self, row_id, name):
        if not messagebox.askyesno(
                "Отзыв устройства",
                f"Отозвать «{name or row_id}»? Его токен перестанет работать "
                "немедленно. Для отзыва нужен мастер-ключ — введи его в поле выше, "
                "если поле пустое."):
            return
        key = self.key_entry.get().strip()
        if not key:
            self.toast("Отзыв возможен только мастер-ключом — введи его выше",
                       err=True)
            return
        saved_token = self.device_token
        self.key, self.device_token = key, ""

        def restore():
            self.device_token = saved_token
            self.key = ""

        def done(d):
            restore()
            self.toast("Устройство отозвано" if (d or {}).get("ok")
                       else "Устройство уже было отозвано", err=not (d or {}).get("ok"))
            self.devices_load()

        def fail(exc):
            restore()
            self.toast(str(exc), err=True)

        self.bg_task(lambda: self.api("POST", "/admin/device/revoke.json",
                                      {"id": row_id}), done, fail)

    def _toggle_key(self):
        self.key_entry.configure(show="" if self.key_entry["show"] else "•")

    def _toggle_token(self):
        self.token_entry.configure(show="" if self.token_entry["show"] else "•")

    def _connect(self):
        self.url = self.url_entry.get().strip().rstrip("/")
        self.key = self.key_entry.get().strip()
        self.refresh()

    def change_token(self):
        tok = self.token_entry.get().strip()
        if not re.fullmatch(r"\d+:[\w-]{30,}", tok):
            self.toast("Неверный формат токена бота", err=True)
            return
        if not messagebox.askyesno("Смена токена",
                                   "Сменить токен бота? После этого бота нужно перезапустить."):
            return
        def done(d):
            self.token_entry.delete(0, "end")
            self.toast("Токен сохранён — можно перезапустить бота")
            if d.get("need_restart") and messagebox.askyesno(
                    "Перезапуск", "Токен сохранён. Перезапустить бота сейчас?"):
                self.restart_bot(confirmed=True)
        self.bg_task(lambda: self.api("POST", "/admin/bot_token", {"token": tok}), done)

    def restart_bot(self, confirmed=False):
        if not confirmed and not messagebox.askyesno(
                "Перезапуск бота",
                "Перезапустить бота? Он будет недоступен несколько секунд, затем "
                "поднимется автоматически."):
            return
        def done(_d):
            self.toast("Перезапуск запущен — бот поднимется через несколько секунд")
        def fail(_e):
            # обрыв соединения сразу после рестарта — ожидаемое поведение
            self.toast("Перезапуск запущен (соединение прервано — это нормально)")
        self.bg_task(lambda: self.api("POST", "/admin/restart"), done, fail)

    # ── настройки ──────────────────────────────────────────────────────
    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self.settings, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _on_close(self):
        self.settings["url"] = self.url
        # Мастер-ключ при выходе НЕ сохраняем никогда. Он нужен ровно на одну
        # операцию — привязать или отозвать устройство — и оставаться на диске
        # ему незачем: для повседневной работы есть токен устройства.
        if self.device_token:
            self.settings["device_token"] = self.device_token
        self._save_settings()
        self.destroy()


def main():
    # High-DPI: объявляем осведомлённость ДО создания окна — иначе Windows
    # растягивает битмап и всё мылится на 125–200% масштабах.
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-Monitor v2
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 — не Windows / старая система
        pass
    app = Pult()
    app.mainloop()


if __name__ == "__main__":
    main()
