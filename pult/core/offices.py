"""Офис сотрудника по папкам портфелей.

Офис из данных реестра не выводится: город там — это где живёт ДОЛЖНИК. Но
офис сам раскладывает портфели по папкам `<база>/<республика>/<subpath>/<Город>/<ФИО>/`,
и 16.09.2026 проверка показала: из 29 закреплений, сделанных руками, 27 нашлись
в папках, и ни одного расхождения. Поэтому пульт дозаполняет офис тем, у кого
его нет, — иначе каждый новый человек из реестра неделями висит в сводке в
«Без офиса», пока кто-нибудь не заметит (Калонов и Рустамов, 16.09.2026).

Правила осторожные: заполняем только пустое (решение человека сильнее папки),
«Soft» — не офис, человек в двух разных городах — не угадываем, а показываем.
"""

from __future__ import annotations

# Папки на одном уровне с городами, которые городом не являются. Решение
# владельца 16.09.2026: курьеры и ГПХ-агенты — ташкентские, отдельных
# псевдо-офисов не заводим. Конфиг путей может дополнить карту
# («office_aliases» в «конфигурация путей.json»).
DEFAULT_ALIASES = {
    "курьеры": "Ташкент",
    "гпх агент выезд": "Ташкент",
}


def name_key(name) -> str:
    """Ключ сравнения ФИО — тот же, что у сервера (db._name_key): регистр,
    лишние пробелы и ё/е значения не имеют."""
    return " ".join(str(name or "").replace("ё", "е").replace("Ё", "Е")
                    .split()).casefold()


def read_path_config() -> dict:
    """«конфигурация путей.json» целиком — там же, где её ищет разброс."""
    import json
    from pathlib import Path

    import razbros
    here = Path(razbros.__file__).resolve().parent
    for p in (here / razbros.CONFIG_NAME, here.parent / razbros.CONFIG_NAME):
        try:
            return json.loads(razbros._strip_jsonc(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return {}


def load_aliases(config: dict | None = None) -> dict[str, str]:
    """Карта «папка → офис»: встроенная плюс из конфига путей."""
    out = dict(DEFAULT_ALIASES)
    for folder, office in ((config or {}).get("office_aliases") or {}).items():
        if str(folder).strip() and str(office).strip():
            out[name_key(folder)] = str(office).strip()
    return out


def folder_offices(folder_map: dict, aliases: dict[str, str] | None = None
                   ) -> dict[str, set[str]]:
    """{ключ ФИО: офисы, где у него лежит портфель} из карты razbros.build_folder_map.
    Папки Soft (колл-центр) в офис не превращаются."""
    aliases = DEFAULT_ALIASES if aliases is None else aliases
    out: dict[str, set[str]] = {}
    for key, entries in (folder_map or {}).items():
        for e in entries or ():
            if e.get("kind") == "soft":
                continue
            city = str(e.get("city") or "").strip()
            if not city:
                continue
            out.setdefault(name_key(key), set()).add(aliases.get(name_key(city), city))
    return out


def suggest(employees, current: dict[str, str], by_folder: dict[str, set[str]]
            ) -> dict:
    """Что делать с офисами.

    fill — {ФИО: офис}: офиса нет, папка ровно в одном городе — записать;
    conflicts — [(ФИО, стоит, в папке)]: руками поставлено одно, папка в
    другом — не трогаем, показываем; ambiguous — [ФИО]: папки в двух городах;
    nofolder — [ФИО]: офиса нет и папки не нашлось."""
    fill: dict[str, str] = {}
    conflicts, ambiguous, nofolder = [], [], []
    for emp in employees or ():
        have = str((current or {}).get(emp) or "").strip()
        found = by_folder.get(name_key(emp)) or set()
        if len(found) > 1:
            if not have or have not in found:
                ambiguous.append(emp)
            continue
        city = next(iter(found), "")
        if not have:
            if city:
                fill[emp] = city
            else:
                nofolder.append(emp)
        elif city and name_key(city) != name_key(have):
            conflicts.append((emp, have, city))
    return {"fill": fill, "conflicts": conflicts,
            "ambiguous": ambiguous, "nofolder": nofolder}


def summary_text(result: dict) -> str:
    """Одна строка для уведомления пульта."""
    parts = []
    fill = result.get("fill") or {}
    if fill:
        names = ", ".join(f"{e} → {c}" for e, c in sorted(fill.items())[:6])
        more = f" и ещё {len(fill) - 6}" if len(fill) > 6 else ""
        parts.append(f"Офис по папкам портфелей: {names}{more}")
    if result.get("conflicts"):
        parts.append(f"расходятся с папками: {len(result['conflicts'])}")
    if result.get("ambiguous"):
        parts.append(f"папки в двух городах: {len(result['ambiguous'])}")
    if result.get("nofolder"):
        parts.append(f"без офиса и без папки: {len(result['nofolder'])}")
    return " · ".join(parts)
