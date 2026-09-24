"""
Книги руководителя: статистика за окно и вечерняя сводка — с НАСТОЯЩИМИ типами.

Писатели переехали сюда из digest_data 24.09.2026 вместе с переходом на
xlsxwriter (book.py). Считает по-прежнему digest_data — здесь только раскладка
по листам, поэтому книга и экран приложения не могут разойтись в цифрах.
Помощники зовутся через модуль (`dd.<имя>`), а не импортом имени: тесты
подменяют их monkeypatch'ем, и подмена обязана доезжать до книги.

Что поменялось против openpyxl-версии — только ТИПЫ ячеек, раскладка прежняя
(листы, порядок и названия колонок, ширины, закрепление, фильтры):
  * даты выезда, обещания, платежа, первого выезда и листа «По дням» — даты;
  * «Сохранён в приложении» и «Принят сервером» — дата-время;
  * время на карточке и у адреса (средние) — длительность «[h]:mm:ss»;
  * «У адреса с/до» — время суток;
  * счётчики пишут НОЛЬ нулём (раньше — пустой строкой, и пустота значила
    то «ноль», то «нет данных»); в сетке исходов ноль спрятан форматом;
  * «% плана» — доля без округления.

Первым листом книги за окно идёт «Сводка» (закреплено / за период / всего):
на контуре компании Телеграма нет, вечерняя книга никому не приходит, и
руководитель берёт её сам кнопкой «Excel» — значит, всё должно быть в ней.
"""

from __future__ import annotations

import book
import country_scope
import db
import digest_data as dd
import evidence
import funnel
from util import num as _num

FILL_SUCCESS, FILL_FAIL, FILL_OTHER = "#E3F2E1", "#FAE3E0", "#EFEFEF"
FILL_METRIC = "#E4ECF6"
FILLS = {"success": FILL_SUCCESS, "fail": FILL_FAIL, "other": FILL_OTHER}
NOTE_COLOR = "#666666"


def _money(units, text: str = ""):
    """Деньги ячейкой: (число, валюта) при одной валюте, текст при смешанных,
    None — денег нет вовсе."""
    num, unit = dd._units_num(units)
    if num is None:
        return text or None
    return num, unit


def _grid_money(units, text: str = ""):
    """То же для сетки исходов: «денег нет» там — это ноль, а не пустота, и
    ячейка остаётся числом (ноль прячет формат)."""
    if not units:
        return 0, ""
    return _money(units, text)


# --- Листы «Сотрудники», «Республики», «Офисы» ---------------------------------
BASE_KINDS = ("text", "text", "text", "int", "int", "pct0",
              "int", "int", "int", "int", "int", "int",
              "int", "int", "int", "int", "int",
              "duration", "duration", "money", "money")


def _line(label: str, country: str, stats: dict, by_outcome: dict,
          layout: list[dict], plan: int, office: str = ""):
    """Строка листа: метрики, план, затем исходы блоками. Возвращает
    (значения, виды, колонки со спрятанным нулём). Раскладка — ровно та же,
    что у dd._book_line (экранный вариант с текстом), типы — нативные."""
    assert len(BASE_KINDS) == len(dd.BOOK_BASE_HEAD)
    values = [label, country, office, stats["visits"], plan or None,
              # Доля без округления: 5 из 20 — это 0.25, а «25%» рисует формат.
              (stats["visits"] or 0) / plan if plan else None,
              stats["persons"], stats["contracts"], stats["overdue"],
              stats["with_photo"], stats.get("late") or 0, stats.get("no_geo") or 0,
              stats.get("confirmed") or 0, stats.get("far") or 0,
              stats.get("unverified") or 0, stats.get("series") or 0,
              stats.get("saved_away") or 0,
              stats.get("near_avg"), stats.get("fill_avg"),
              _money(stats.get("money"), stats.get("money_text") or "") or 0,
              _money(stats.get("money_paid"), stats.get("money_paid_text") or "") or 0]
    kinds = list(BASE_KINDS)
    hidden: set[int] = set()
    by_outcome = by_outcome or {}
    for block in layout:
        total_n, units = 0, []
        for status in block["statuses"]:
            item = by_outcome.get(status) or {}
            total_n += item.get("n") or 0
            units.append(item.get("money") or [])
        merged = dd._merge_units(units)
        hidden.update((len(values), len(values) + 1))
        values += [total_n, _grid_money(merged, dd._units_text(merged))]
        kinds += ["int", "money"]
        for status in block["statuses"]:
            item = by_outcome.get(status) or {}
            hidden.update((len(values), len(values) + 1))
            values += [item.get("n") or 0,
                       _grid_money(item.get("money"), item.get("money_text") or "")]
            kinds += ["int", "money"]
        if block["code"] == "other":
            values.append(" · ".join(f"{st} {by_outcome[st]['n']}"
                                     for st in block["statuses"] if by_outcome.get(st)))
            kinds.append("text")
    return values, kinds, hidden


def _outcome_sheet(bk: book.Book, title: str, caption: str, first_head: str,
                   layout: list[dict], lines, total, freeze: str) -> None:
    """Лист с блоками исходов: подпись, пустая строка, блоки, шапка, строки,
    «Итого». lines/total — (значения, виды, спрятанные) из _line."""
    block_row, head_row, widths, spans = dd._book_head(layout)
    head_row = [first_head] + list(head_row[1:])
    ws = bk.sheet(title)
    ws.title(caption)
    ws.skip()
    ws.blocks(len(head_row), [(next(b["title"] for b in layout if b["code"] == code),
                               first - 1, last - 1, FILLS[code])
                              for code, first, last in spans])
    head_fills = {c - 1: FILLS[code] for code, first, last in spans
                  for c in range(first, last + 1)}
    head_at = ws.head(head_row, fills=head_fills)
    for values, kinds, hidden in lines:
        ws.append(values, kinds, hide_zero=hidden)
    last_data = ws.row - 1
    values, kinds, hidden = total
    ws.append(values, kinds, bold=True, hide_zero=hidden)
    ws.widths(widths)
    # Фильтр — по строкам данных: «Итого» в диапазон не берём, иначе она
    # уедет вместе с фильтром по республике.
    ws.autofilter(head_at, last_data, len(head_row))
    ws.freeze(freeze)


# --- Лист «Выезды» -------------------------------------------------------------
VISIT_COLUMNS = (
    ("Дата", 12, "date"), ("Республика", 14, "text"), ("Офис", 14, "text"),
    ("Сотрудник", 28, "text"), ("Клиент", 32, "text"), ("Договоров", 10, "int"),
    ("Выезд", 14, "text"), ("Результат", 22, "text"),
    ("Договорённость", 22, "text"), ("Заполнение карточки", 18, "duration"),
    ("Сохранён в приложении", 19, "datetime"), ("Принят сервером", 18, "datetime"),
    ("Внесён", 16, "text"), ("Гео", 8, "text"), ("Закрыл", 14, "text"),
    ("Долг", 18, "money"), ("Подтверждение", 26, "text"),
    ("У адреса, мин", 12, "int"), ("У адреса с", 11, "time"),
    ("У адреса до", 11, "time"), ("Связь при сохранении", 18, "text"),
    ("Признаки серии", 24, "text"), ("Где заполнен", 14, "text"),
)


def _visits_sheet(bk: book.Book, rows: list[dict], names: dict) -> None:
    """Выезд строкой. «Внесён», «Гео» и «Закрыл» — контроль, о котором просил
    офис: занесённый назавтра, закрытый без точки телефона или закрытый
    руководителем за сотрудника выезд виден строкой. Три момента выезда
    разведены: день выезда, нажатие в приложении и приём сервером."""
    ws = bk.sheet("Выезды")
    kinds = [c[2] for c in VISIT_COLUMNS]
    ws.ws.write_row(0, 0, [c[0] for c in VISIT_COLUMNS], bk.fmt(bold=True))
    ws.row = 1
    for r in rows:
        own = set(db.agent_tids(r["employee"]) or ())
        for e in r["_entries"]:
            p = e["person"]
            group = dd.outcome_group(e["status"])
            late = e.get("late_days")
            when = ("в день выезда" if late == 0
                    else f"позже на {late} дн" if late else "неизвестно")
            who = ("сам" if not own or e.get("agent_id") in own
                   else "руководитель")
            ev = e.get("ev") or {}
            level = e.get("evidence_level") or ""
            online = ev.get("online_save")
            fills = {6: FILLS[group]}
            if late:
                fills[12] = FILL_FAIL
            if not e.get("geo"):
                fills[13] = FILL_FAIL
            if level == "confirmed":
                fills[16] = FILL_SUCCESS
            elif level == "far":
                fills[16] = FILL_FAIL
            if evidence.is_series(e.get("series")):
                fills[21] = FILL_FAIL
            if ev.get("saved_away"):
                fills[22] = FILL_FAIL
            ws.append([
                e["date"], names.get(r["country"], ""), r.get("office") or "",
                r["employee"], p["client"], p["n"],
                dd.OUTCOME_GROUP_LABEL[group], e["status"], e["agreed"],
                e.get("fill_seconds"), e.get("saved_at"), e.get("received_at"),
                when, "есть" if e.get("geo") else "нет", who,
                _money(p.get("totals"), dd._persons_total([p])),
                evidence.LEVEL_TEXT.get(level, "") if level else "",
                ev.get("near_min"), ev.get("near_first"), ev.get("near_last"),
                "" if online is None else ("была" if online else "не было"),
                evidence.series_text(e.get("series")),
                ("" if ev.get("saved_away") is None
                 else "не на адресе" if ev.get("saved_away") else "на адресе"),
            ], kinds, fills=fills)
    ws.widths([c[1] for c in VISIT_COLUMNS])
    ws.autofilter(0, ws.row - 1, len(VISIT_COLUMNS))
    ws.freeze("A2")


def _days_sheet(bk: book.Book, data: dict, rows: list[dict]) -> None:
    ws = bk.sheet("По дням")
    ws.ws.write_row(0, 0, ["Дата", "Выездов"] + [r["employee"] for r in rows],
                    bk.fmt(bold=True))
    ws.row = 1
    kinds = ["date", "int"] + ["int"] * len(rows)
    for i, x in enumerate(data["by_day"]):
        ws.append([x["date"], x["visits"]] + [r["by_day"][i] for r in rows], kinds)
    ws.ws.set_column(0, 0, 12)


# --- Лист «Сводка»: закреплено / за период / всего ----------------------------
# Строка на сотрудника. Та же таблица, что уходила руководителям вечерней
# книгой (просьба 08.09.2026), только окно — любое: на контуре компании
# Телеграма нет, и вечернюю книгу руководитель собирает себе сам.
REPORT_COLUMNS = (
    ("employee", "Сотрудник", 28, "text"),
    ("country_name", "Республика", 14, "text"),
    ("office", "Офис", 14, "text"),
    ("assigned_persons", "Закреплено должников", 20, "int"),
    ("assigned_contracts", "Закреплено договоров", 20, "int"),
    ("persons", "Отработано за период", 18, "int"),
    ("visits", "Выездов за период", 16, "int"),
    ("paid", "Оплачен за период", 16, "int"),
    ("promised", "Обещают за период", 16, "int"),
    ("with_photo", "С фото за период", 15, "int"),
    ("total_persons", "Отработано всего", 17, "int"),
    ("total_visits", "Выездов всего", 14, "int"),
    ("overdue", "Просрочено обещаний", 19, "int"),
    ("confirmed", "На адресе подтверждено", 16, "int"),
    ("far", "У адреса не был", 14, "int"),
    ("unverified", "Нечем проверить", 14, "int"),
    ("first_day", "Первый выезд", 13, "date"),
    ("assigned_money", "Долг закреплённый", 20, "money"),
)

# Ключи окна в данных сводки → ключи вечерней сводки (там окно — «сегодня»).
_DAILY_KEYS = {"persons": "today_persons", "visits": "today_visits",
               "paid": "today_paid", "promised": "today_promised",
               "with_photo": "today_photo"}
_SUM_KEYS = ("assigned_persons", "assigned_contracts", "persons", "visits",
             "paid", "promised", "with_photo", "total_persons", "total_visits",
             "total_paid", "overdue", "confirmed", "far", "unverified", "series",
             "saved_away")


def period_report_data(mgr: dict, rng: dict, employees: list[str] | None = None,
                       window: dict | None = None) -> dict:
    """Строки сводки за окно [rng.from..rng.to]: закреплено сейчас, отработано
    за окно, отработано всего с TOTALS_SINCE.

    window — уже посчитанные строки окна {ФИО: строка _employee_stats}: книга
    за период считает их для своих листов, и второй проход по выездам ради
    «Сводки» стоил бы ещё столько же секунд."""
    employees = employees if employees is not None else dd.sorted_employees(mgr)
    window = window or {}
    country = None
    rows = []
    for emp in employees:
        win = window.get(emp)
        if win is None:
            win = dd._employee_stats(emp, rng["from"], rng["to"])
        # Страна — та же, что у листов книги (stats_data кладёт её в строку);
        # без готовой строки считаем по тому же окну: сотрудник, с которого
        # сняли весь портфель, иначе остался бы без республики.
        if "country" in win:
            code = win["country"]
        else:
            if country is None:
                country = dd._employee_country_index(rng["from"], rng["to"])
            code = country.get(emp)
        total = dd._employee_stats(emp, dd.TOTALS_SINCE, rng["to"], with_photos=False)
        assigned = db._group_persons(db.contracts_of_responsible(emp))
        money_val, money_unit = dd._units_num(dd._totals_units(assigned))
        rows.append({
            "employee": emp,
            "country": code,
            "country_name": country_scope.info(code)["name"] if code else "",
            # Офис — из строки _employee_stats: там он сверяется по ключу ФИО
            # (db.office_of), а точное сравнение строк уже теряло людей.
            "office": win.get("office") or "",
            "assigned_persons": len(assigned),
            "assigned_contracts": sum(p["n"] for p in assigned),
            "assigned_money": dd._persons_total(assigned),
            "assigned_money_val": money_val, "assigned_money_unit": money_unit,
            "persons": win["persons"], "visits": win["visits"],
            "paid": win["paid"], "promised": win["promised"],
            "with_photo": win["with_photo"],
            "total_persons": total["persons"], "total_visits": total["visits"],
            "total_paid": total["paid"],
            "overdue": win["overdue"],
            "first_day": min((e["date"] for e in total["_entries"]), default=""),
            "outcomes": win["outcomes"],
            "confirmed": win.get("confirmed", 0), "far": win.get("far", 0),
            "unverified": win.get("unverified", 0), "series": win.get("series", 0),
            "saved_away": win.get("saved_away", 0),
        })
    rows.sort(key=lambda r: (-r["persons"], -r["total_persons"],
                             r["employee"].lower()))
    totals = {k: sum(r[k] for r in rows) for k in _SUM_KEYS}
    totals["employees"] = len(rows)
    totals["active"] = sum(1 for r in rows if r["persons"])
    # Числовой итог по деньгам — только когда у всех одна и та же валюта:
    # сумма, в которой ₽ склеены с сумами, хуже отсутствия суммы.
    units = {r["assigned_money_unit"] for r in rows}
    if rows and len(units) == 1 and next(iter(units), ""):
        totals["assigned_money_val"] = sum(r["assigned_money_val"] for r in rows)
        totals["assigned_money_unit"] = units.pop()
    order = [s["code"] for s in country_scope.COUNTRIES]
    return {"rows": rows, "totals": totals, "scope": dd.scope_label(mgr),
            "manager_name": mgr.get("name") or "",
            "countries": [{"code": c, **{k: country_scope.info(c)[k]
                                         for k in ("name", "short", "flag")},
                           "employees": sum(1 for r in rows if r["country"] == c)}
                          for c in sorted({r["country"] for r in rows if r["country"]},
                                          key=order.index)]}


def daily_report_data(mgr: dict, on_date: str,
                      employees: list[str] | None = None) -> dict:
    """Вечерняя сводка — та же сводка с окном в один день, ключи «today_*»:
    на них стоят подпись телеграм-сообщения и колонки вечерней книги."""
    data = period_report_data(mgr, {"from": on_date, "to": on_date}, employees)

    def rename(d: dict) -> dict:
        return {_DAILY_KEYS.get(k, k): v for k, v in d.items()}

    return {"screen": "daily_report", "date": on_date,
            **{k: v for k, v in data.items() if k not in ("rows", "totals")},
            "rows": [rename(r) for r in data["rows"]],
            "totals": rename(data["totals"])}


def _report_sheet(bk: book.Book, title: str, caption: str, note: str,
                  columns, rows: list[dict], totals: dict, *,
                  title_size: int | None = None) -> None:
    """Лист «Сводка»: columns — (ключ, заголовок, ширина[, вид]); вид по
    умолчанию — счётчик. Деньги закреплённого долга — числом с валютой."""
    kinds = [(c[3] if len(c) > 3 else _REPORT_KIND.get(c[0], "int")) for c in columns]

    def cells(r: dict) -> list:
        out = []
        for key, *_rest in columns:
            if key == "assigned_money":
                val = r.get("assigned_money_val")
                out.append((val, r.get("assigned_money_unit") or "") if val is not None
                           else (r.get("assigned_money") or None))
            else:
                out.append(r.get(key, ""))
        return out

    ws = bk.sheet(title)
    ws.title(caption, size=title_size)
    if note:
        ws.title(note, italic=True, color=NOTE_COLOR)
    else:
        ws.skip()
    head_at = ws.head([c[1] for c in columns])
    for r in rows:
        ws.append(cells(r), kinds)
    last_data = ws.row - 1
    ws.append(["Итого", ""] + cells(totals)[2:], kinds, bold=True)
    ws.widths([c[2] for c in columns])
    ws.autofilter(head_at, last_data, len(columns))
    ws.freeze("C4")


_REPORT_KIND = {"employee": "text", "country_name": "text", "office": "text",
                "first_day": "date", "assigned_money": "money"}


# --- Листы воронки Collection ------------------------------------------------
# Просьба Альберта 15.09.2026. Колонка описана тройкой (заголовок, ширина, тип):
# тип «pct» — НАСТОЯЩИЙ процент Excel, а не текст «71,4%», иначе по показателю
# нельзя ни отсортировать, ни построить сводную.
def _col_kind(col, default: str = "int") -> str:
    kind = col[2] if len(col) > 2 else default
    return {"int": "int", "pct": "pct", "text": "text", "money": "money"}.get(kind, kind)


def funnel_sheets(bk: book.Book, data: dict, rows: list[dict], names: dict,
                  rng: dict) -> None:
    """Листы воронки: «Воронка» (сотрудники), «Воронка — республики»,
    «Воронка — офисы», «Воронка — итого», «Обещания», «Ранжирование».

    Уровни среза разнесены ПО ЛИСТАМ, а не сложены в одну таблицу с колонкой
    «Срез», как было до 22.09.2026: каждый выезд лежал в ней четырежды, и SUM
    по «Выездов» давал 36 при девяти настоящих выездах.
    """
    window = f"{rng['label']} ({rng['from']} — {rng['to']})"

    def head(sheet: str, columns, title: str, note: str = ""):
        """Шапка листа: заголовок, строка оговорок, названия колонок.
        Оговорка — во ВТОРОЙ строке, над таблицей: под таблицей её не видит
        ни фильтр, ни тот, кто дочитал до итогов."""
        ws = bk.sheet(sheet)
        ws.title(title)
        ws.title(note, italic=True, color=NOTE_COLOR)
        ws.head([c[0] for c in columns],
                fills={i: FILL_METRIC for i, c in enumerate(columns)
                       if len(c) > 2 and c[2] == "pct"})
        ws.widths([c[1] for c in columns])
        return ws

    def metric_fills(columns) -> dict:
        return {i: FILL_METRIC for i, c in enumerate(columns)
                if len(c) > 2 and c[2] == "pct"}

    # --- Воронка: по листу на уровень среза ---------------------------------
    plan_map = db.visit_plan_map()
    # Задание — на РАБОЧИЙ наступивший день (решение владельца 22.09.2026).
    work = dd._workdays(data.get("by_day"), rng.get("today") or dd.today_iso())
    fkinds = [_col_kind(c) for c in dd.FUNNEL_COLUMNS]
    ffills = metric_fills(dd.FUNNEL_COLUMNS)

    def norm(group: list[dict]) -> int:
        """Задание группы: сумма ЛИЧНЫХ норм × рабочие дни (в группах 8/10/12
        общая норма × число людей — цифра, которой никто не ставил)."""
        return sum(db.visit_plan_for(r["employee"], plan_map)
                   for r in group) * work

    def funnel_sheet(title: str, subtitle: str, lines: list[list], bold=False):
        ws = head(title, dd.FUNNEL_COLUMNS,
                  f"Воронка Collection · {subtitle} · {data['scope']} · {window}",
                  f"Один уровень среза на лист: сумма колонки «Выездов» равна числу "
                  f"выездов уровня. Задание = личная норма × {work} рабочих дн., "
                  f"уже наступивших в окне (выходные и будущие дни не в счёт).")
        head_at = ws.row - 1
        for line in lines:
            ws.append(line, fkinds, bold=bold, fills=ffills)
        ws.autofilter(head_at, ws.row - 1, len(dd.FUNNEL_COLUMNS))
        ws.freeze("C4")

    funnel_sheet("Воронка", "сотрудники", [
        dd._funnel_line("Сотрудник", r["employee"], names.get(r["country"], ""),
                        r.get("office") or "", r.get("funnel"),
                        db.visit_plan_for(r["employee"], plan_map) * work, work)
        for r in rows])

    republics = []
    for c in data["countries"]:
        mine = [r for r in rows if r["country"] == c["code"]]
        republics.append(dd._funnel_line("Республика", c["name"], c["name"], "",
                                         c["totals"].get("funnel"),
                                         norm(mine), work))
    nowhere = [r for r in rows if not r.get("country")]
    if nowhere:
        # Сотрудник без страны кейсов бывает (пустой портфель, новичок). Без
        # этой строки сумма листа не сошлась бы с числом выездов команды.
        republics.append(dd._funnel_line(
            "Республика", "Без республики", "", "",
            funnel.add_counts(*[r.get("funnel") for r in nowhere]),
            norm(nowhere), work))
    funnel_sheet("Воронка — республики", "республики", republics)

    offices = []
    for office in data.get("offices") or ():
        mine = [r for r in rows if dd._in_office(r, office["code"])]
        offices.append(dd._funnel_line("Офис", office["name"],
                                       dd._office_country(mine, names),
                                       office["name"],
                                       office["totals"].get("funnel"),
                                       norm(mine), work))
    if offices:
        funnel_sheet("Воронка — офисы", "офисы", offices)

    funnel_sheet("Воронка — итого", "итог команды", [
        dd._funnel_line("Итого", f"сотрудников {data['totals']['employees']}",
                        "", "", data["totals"].get("funnel"), norm(rows), work)],
        bold=True)

    # --- Обещания: реестр для самостоятельного разбора ----------------------
    promises = dd._promise_rows(rows, names)
    counted = [x for x in promises if x["counted"]]
    # Расхождение двух листов названо вслух и ЧИСЛАМИ: «Ранжирование» считает
    # только зачётные обещания, и молчаливая разница строк выглядела бы ошибкой.
    gap = (f"Строк здесь {len(promises)} — по одной на должника; "
           f"в зачёт KP/BP идёт {len(counted)}.")
    ws2 = head("Обещания", dd.PROMISE_COLUMNS,
               f"Обещания клиентов, взятые на выездах · {window}",
               "Одна строка — один должник: результат, сумма и дата взяты с "
               "ПОСЛЕДНЕГО выезда к нему, а сколько их было — в колонке «Выездов с "
               "обещанием». " + gap + " Валюта обещания — валюта долга должника, "
               "суммы разных валют не складываются.")
    head_at = ws2.row - 1
    pkinds = ["date", "int", "text", "text", "text", "text", "int", "text", "text",
              "text", "date", "money", "text", "text", "money", "text", "int",
              "text", "date", "money", "text", "text"]
    assert len(pkinds) == len(dd.PROMISE_COLUMNS)
    for x in promises:
        fills = {}
        if x["state"] == funnel.PROMISE_KEPT:
            fills[15] = FILL_SUCCESS
        elif x["state"] == funnel.PROMISE_BROKEN:
            fills[15] = FILL_FAIL
        ws2.append([x["date"], x["visits"], x["country"], x["office"],
                    x["employee"], x["client"], x["contracts"],
                    x["contact_with"], x["who_at_address"], x["agreed"],
                    x["promise_day"], (x["sum"] or None, x["unit"]), x["unit_text"],
                    x["bucket"],
                    ((x["debt_val"], x["debt_unit"]) if x["debt_val"] is not None
                     else x["debt"]),
                    funnel.PROMISE_STATE_TEXT.get(x["state"], ""),
                    x["days_overdue"] or None, x["overdue_bucket"],
                    x["last_payment"],
                    # Платёж — в валюте долга того же должника: другой у него нет.
                    (_num(x["last_payment_sum"]) or None, x["unit"]),
                    x["status"], "да" if x["counted"] else "нет"],
                   pkinds, fills=fills)
    ws2.autofilter(head_at, ws2.row - 1, len(dd.PROMISE_COLUMNS))
    ws2.freeze("A4")

    # --- Ранжирование по объёму обещания ------------------------------------
    # Считаем по тем же обещаниям, что попали в зачёт KP/BP, иначе строки листа
    # не сойдутся с колонками воронки. Каждая валюта — своими строками.
    ws3 = head("Ранжирование", dd.RANGE_COLUMNS, f"Исполнение обещаний по размеру · {window}",
               f"Только обещания в зачёт KP/BP (колонка «В зачёт KP/BP» = «да» на "
               f"листе «Обещания»): в зачёт идёт одно, последнее обещание должника. "
               f"{gap} Каждая валюта своими строками — суммы разных валют не "
               f"складываются, знак валюты в колонке «Валюта» и в формате ячейки.")
    head_at = ws3.row - 1
    rkinds = [_col_kind(c, "int") for c in dd.RANGE_COLUMNS]
    rkinds[0] = rkinds[1] = "text"
    rfills = metric_fills(dd.RANGE_COLUMNS)
    money_cols = {i for i, c in enumerate(dd.RANGE_COLUMNS)
                  if len(c) > 2 and c[2] == "money"}
    groups = sorted({(x["unit_text"], x["unit"]) for x in counted}) or [("", "")]

    def range_row(title: str, items: list[dict], unit_text: str, unit: str,
                  bold: bool = False) -> None:
        ws3.append(dd._range_line(title, items, unit_text), rkinds, bold=bold,
                   fills=rfills, units={i: unit for i in money_cols})

    for unit_text, unit in groups:
        mine = [x for x in counted if x["unit_text"] == unit_text]
        for title in funnel.AMOUNT_ORDER:
            range_row(title, [x for x in mine if x["bucket"] == title],
                      unit_text, unit)
    last3 = ws3.row - 1
    # Итоги — ПОД диапазоном фильтра и по одному на валюту: общий итог по всем
    # валютам сразу был бы той самой суммой, которой не существует.
    for unit_text, unit in groups:
        range_row("Итого", [x for x in counted if x["unit_text"] == unit_text],
                  unit_text, unit, bold=True)
    ws3.autofilter(head_at, last3, len(dd.RANGE_COLUMNS))
    ws3.freeze("B4")


# --- Книга за окно -----------------------------------------------------------
def stats_xlsx(mgr: dict, rng: dict, employees: list[str] | None = None) -> bytes:
    """Статистика книгой: «Сводка», «Сотрудники», «Республики», «Офисы»,
    «Выезды», «По дням» и листы воронки.

    Исходы разложены по столбцам — количество и деньги по каждому — под
    блоками «Успешный выезд» / «Не успешный выезд» / «Прочие исходы» (просьба
    офиса 11.09.2026): одной строкой текста их ни сложить, ни отсортировать.
    """
    data = dd.stats_data(mgr, rng, employees)
    names = {c["code"]: c["name"] for c in data["countries"]}
    rows = data["employees"]
    layout = dd._outcome_layout(rows)
    period = f"{rng['label']} ({rng['from']} — {rng['to']})"
    bk = book.Book()

    # --- Сводка: закреплено / за период / всего ------------------------------
    report = period_report_data(mgr, rng, [r["employee"] for r in rows],
                                window={r["employee"]: r for r in rows})
    _report_sheet(bk, "Сводка", f"Сводка · {data['scope']} · {period}",
                  f"«Закреплено» — портфель на сегодня; «за период» — выезды "
                  f"окна; «всего» — с {db._as_date(dd.TOTALS_SINCE):%d.%m.%Y}.",
                  REPORT_COLUMNS, report["rows"], report["totals"])

    # --- Сотрудники --------------------------------------------------------
    plan_map = db.visit_plan_map()
    days = max(1, len(data["by_day"]))
    lines = [_line(r["employee"], names.get(r["country"], ""), r,
                   r.get("by_outcome") or {}, layout,
                   db.visit_plan_for(r["employee"], plan_map) * days,
                   office=r.get("office") or "") for r in rows]
    total = _line("Итого", "", data["totals"], dd._outcome_totals(rows), layout,
                  data["totals"].get("plan") or 0)
    # ФИО и страна остаются на виду при прокрутке вправо.
    _outcome_sheet(bk, "Сотрудники",
                   f"Статистика выездов · {data['scope']} · {period}",
                   "Сотрудник", layout, lines, total, "C5")

    # --- Республики: тот же расклад, но по странам ---------------------------
    lines = []
    for c in data["countries"]:
        mine = [r for r in rows if r["country"] == c["code"]]
        lines.append(_line(c["name"], f"сотрудников {c['employees']}", c["totals"],
                           dd._outcome_totals(mine), layout,
                           c["totals"].get("plan") or 0))
    total = _line("Итого", f"сотрудников {data['totals']['employees']}",
                  data["totals"], dd._outcome_totals(rows), layout,
                  data["totals"].get("plan") or 0)
    _outcome_sheet(bk, "Республики", f"По республикам · {period}", "Республика",
                   layout, lines, total, "B5")

    # --- Офисы: та же раскладка по городам, где сидит штат -------------------
    if data.get("offices"):
        # «Без офиса» приходит последней строкой в самом data["offices"].
        lines = []
        for office in data["offices"]:
            mine = [r for r in rows if dd._in_office(r, office["code"])]
            lines.append(_line(office["name"], f"сотрудников {office['employees']}",
                               office["totals"], dd._outcome_totals(mine), layout,
                               office["totals"].get("plan") or 0))
        _outcome_sheet(bk, "Офисы", f"По офисам · {period}", "Офис", layout,
                       lines, total, "B5")

    _visits_sheet(bk, rows, names)
    _days_sheet(bk, data, rows)
    # Воронка Collection — в конце книги: показатели, реестр обещаний и
    # ранжирование по размеру обещания (просьба Альберта 15.09.2026).
    funnel_sheets(bk, data, rows, names, rng)
    return bk.close()


# --- Вечерняя книга ----------------------------------------------------------
def daily_report_xlsx(mgr: dict, on_date: str,
                      employees: list[str] | None = None,
                      data: dict | None = None) -> bytes:
    """Вечерняя книга: «Сводка», «Исходы за день» и воронка за день отчёта.

    Воронка едет и сюда (22.09.2026): до этого её листы собирались только по
    кнопке «Excel» в приложении, а просили её именно в вечерней книге."""
    data = data if data is not None else dd.daily_report_data(mgr, on_date, employees)
    bk = book.Book()
    _report_sheet(bk, "Сводка",
                  f"Выезды · сводка на {dd.human_date(on_date)} ({on_date})"
                  + (f" · {data['scope']}" if data["scope"] else ""), "",
                  dd.DAILY_REPORT_COLUMNS, data["rows"], data["totals"],
                  title_size=13)

    ws2 = bk.sheet("Исходы за день")
    ws2.ws.write_row(0, 0, ["Сотрудник", "Исход", "Должников"], bk.fmt(bold=True))
    ws2.row = 1
    for r in data["rows"]:
        for o in r["outcomes"]:
            ws2.append([r["employee"], o["status"], o["n"]], ["text", "text", "int"])
    ws2.widths((28, 28, 12))

    day_range = dd.stats_range("today", today=db._as_date(on_date))
    day_stats = dd.stats_data(mgr, day_range, [r["employee"] for r in data["rows"]])
    funnel_sheets(bk, day_stats, day_stats["employees"],
                  {c["code"]: c["name"] for c in day_stats["countries"]}, day_range)
    return bk.close()
