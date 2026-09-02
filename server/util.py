"""
Единый util — общие хелперы, которые раньше дублировались по файлам.

  num / money     — надёжный разбор числа (раньше: scoring._num/_money, db._money_int,
                    engine/app/main._money — три копии одной логики).
  parse_city      — ре-экспорт единого геокодер-фасада (см. gazetteer.py).

Чистый модуль без побочных эффектов и без тяжёлых зависимостей.
"""
from __future__ import annotations

import re

from gazetteer import parse_city  # noqa: F401  — один интерфейс, ре-экспорт


def num(v) -> int:
    """Надёжный разбор числа: float-ячейки openpyxl (с хвостом …999998) округляем,
    строки '23 000'/'23,0' чистим. bool → 0. Старый «склеить цифры» ломался на дробях."""
    if v in (None, "") or isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(round(v))
    t = str(v).replace("\xa0", " ").strip().replace(" ", "").replace(",", ".")
    m = re.match(r"-?\d+(?:\.\d+)?", t)
    return int(round(float(m.group()))) if m else 0


money = num  # семантический алиас (деньги — те же целые рубли/сумы)
