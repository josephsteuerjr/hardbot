"""Республика операционной команды по настроенному scope руководителя.

В реестре нет надёжного отдельного поля страны: ``pool`` на живом hardbot2 содержит
числовые коды, а адрес/город заполнены не у каждого договора. Зато руководители в
пульте уже привязаны к городам и сотрудникам. Этот scope и есть явный источник
истины для статистики Таджикистана/Узбекистана.
"""

from __future__ import annotations


COUNTRIES = (
    {"code": "tj", "name": "Таджикистан", "short": "Тадж", "flag": "🇹🇯"},
    {"code": "uz", "name": "Узбекистан", "short": "Узб", "flag": "🇺🇿"},
)

_BY_CODE = {c["code"]: c for c in COUNTRIES}

# Scope заполняется свободным текстом. Первые токены — названия стран, остальные —
# основные города фактического контура. Совпадение по подстроке переживает варианты
# Худжанд/Хучанд и подписи вроде «Ташкентская область».
_TOKENS = {
    "tj": (
        "тадж", "худж", "хучанд", "душан", "истарав", "конибод",
        "исфар", "пенджик", "бохтар", "кулоб", "кулаб",
    ),
    "uz": (
        "узб", "ташкент", "самарканд", "андижан", "ферган", "сирдар",
        "наманган", "бухар", "навои", "карши", "нукус", "джизак",
    ),
}


def info(code: str) -> dict:
    """Копия метаданных страны для JSON/UI."""
    return dict(_BY_CODE[code])


def scope_countries(cities: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Все республики, попавшие в city-scope, в порядке ``COUNTRIES``.

    Смешанный scope — это норма для старшего руководителя: он видит и РТ, и РУ.
    Раньше такой scope молча схлопывался в ``None``, и именно тот, кто просил
    разделение по республикам, получал сводку без разбивки.
    """
    text = " ".join(str(c or "").strip().casefold() for c in (cities or []))
    found = {code for code, tokens in _TOKENS.items()
             if any(token in text for token in tokens)}
    return tuple(spec["code"] for spec in COUNTRIES if spec["code"] in found)


def scope_country(cities: list[str] | tuple[str, ...] | None) -> str | None:
    """``tj``/``uz`` для однозначного city-scope, иначе ``None``.

    Смешанный scope намеренно не угадываем: один и тот же сотрудник не должен попасть
    сразу в обе статистики. Для показа обоих блоков есть ``scope_countries``.
    """
    codes = scope_countries(cities)
    return codes[0] if len(codes) == 1 else None


def employee_countries(managers: list[dict]) -> dict[str, str]:
    """ФИО → республика по однозначным scopes руководителей.

    Несколько руководителей одной страны могут видеть одного сотрудника — это штатно.
    Если сотрудник ошибочно попал в scopes разных стран, он исключается из разбивки,
    чтобы показатели не задвоились.
    """
    assigned: dict[str, set[str]] = {}
    for manager in managers:
        code = scope_country(manager.get("cities") or [])
        if not code:
            continue
        for employee in manager.get("employees") or []:
            employee = str(employee or "").strip()
            if employee:
                assigned.setdefault(employee, set()).add(code)
    return {employee: next(iter(codes)) for employee, codes in assigned.items()
            if len(codes) == 1}


def split_employees(employees, mapping: dict[str, str]) -> list[tuple[str | None, list]]:
    """[(код республики | None, сотрудники)] в порядке ``COUNTRIES``; ``None`` —
    «без привязки». Каждый сотрудник ровно в одном блоке, поэтому сводка
    руководителя с обеими республиками не задваивается."""
    buckets: dict[str | None, list] = {}
    for employee in employees:
        buckets.setdefault(mapping.get(employee), []).append(employee)
    order = [spec["code"] for spec in COUNTRIES] + [None]
    return [(code, buckets[code]) for code in order if buckets.get(code)]

