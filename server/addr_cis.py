"""
Разбор официального адреса СНГ (УЗ/ТДж/РФ) → структура + человекочитаемая строка.

ЗАЧЕМ. В реестре адрес выглядит так:

    ЎЗБЕКИСТОН,  САМАРҚАНД ВИЛОЯТИ,  ПАСТДАРҒОМ ТУМАНИ,  ЗАНГИБОЙИ МФЙ, ул. ЗАНГИБОЙИ ҚИШЛОҒИ

Это НЕ «кривой» адрес — он строгий и полный. Проблема в том, что одна строка написана
в трёх системах письма сразу: узбекская кириллица (ВИЛОЯТИ, МФЙ, КЎЧАСИ), узбекская
латиница (VILOYATI, MFY, KO‘CHASI) и русские кальки (ОБЛАСТЬ, ПАХТААБАДСКИЙ, МСГ).
Официальный блок вдобавок часто продублирован по-русски внутри самого себя. Ни один
геокодер такое не ест, а человек не читает.

ГЛАВНАЯ СТРУКТУРНАЯ РАЗНИЦА, из-за которой наивный разбор ломается:

    узбекский:  ИМЯ + тип     «САМАРҚАНД ВИЛОЯТИ», «ЗАНГИБОЙИ МФЙ», «БАХТЛИ ХАЁТ КЎЧАСИ»
    таджикский: тип + ИМЯ     «Вилояти Суғд», «Шаҳри Бӯстон», «Кӯчаи М.Миршакар»

Поэтому разбор потокенный: маркер типа привязывается назад (узбекский суффикс) или
вперёд (таджикский/русский префикс), а неоднозначные слова (вилояти, шаҳри, тумани —
они одинаковы в обоих языках) решаются по контексту: если незакрытый префикс уже
держит имя, значит текущий маркер начинает новый компонент, а не закрывает старый.

ТРАНСЛИТЕРАЦИЯ. В GeoNames узбекские топонимы записаны ЛАТИНИЦЕЙ (`Shahrixon`,
`Pastdarg‘om`, `Xo‘jaobod`), а в реестре — кириллицей (`Шахрихон`, `Пастдарғом`,
`Хўжаобод`). Без транслита не совпадает почти ничего: на реальных именах из реестра
было 3 попадания из 26, с транслитом и схлопыванием вариативных пар (x↔h, q↔k, g‘↔g)
стало 21. Плюс срез узбекского изафета: «Зангибойи МФЙ» = «МФЙ села Зангибой»,
в справочнике оно записано как «Zangiboy».

Модуль ЧИСТЫЙ: без сети, без файлов, без зависимостей. Справочник координат
подключается снаружи (`geocode.py`) — так разбор можно проверять отдельно.
"""
from __future__ import annotations

import re

# --- Апострофы во всех начертаниях, что живут в выгрузках, → один ASCII --------
_APO = dict.fromkeys(map(ord, "ʻʼ‘’`´′"), "'")

# --- Узбекская кириллица → узбекская латиница (офиц. соответствие) -------------
# Порядок важен: многобуквенные раньше однобуквенных.
_CYR2LAT: list[tuple[str, str]] = [
    ("ё", "yo"), ("ж", "j"), ("ч", "ch"), ("ш", "sh"), ("щ", "sh"),
    ("ю", "yu"), ("я", "ya"), ("ц", "ts"),
    ("а", "a"), ("б", "b"), ("в", "v"), ("г", "g"), ("ғ", "g'"), ("д", "d"),
    ("е", "e"), ("з", "z"), ("и", "i"), ("й", "y"), ("к", "k"), ("қ", "q"),
    ("л", "l"), ("м", "m"), ("н", "n"), ("о", "o"), ("п", "p"), ("р", "r"),
    ("с", "s"), ("т", "t"), ("у", "u"), ("ў", "o'"), ("ф", "f"), ("х", "x"),
    ("ҳ", "h"), ("ъ", "'"), ("ь", ""), ("ы", "i"), ("э", "e"),
    ("ӯ", "u"), ("ӣ", "i"), ("ҷ", "j"),          # таджикские сверх узбекского набора
]

# Одно имя гуляет между написаниями: Shahrixon/Shaxrixon/Шаҳрихон, Tayloq/Toyloq,
# Paxtaobod/Pahtaobod. Схлопываем в общий «скелет».
_FUZZY_PAIRS = [("kh", "h"), ("gh", "g"), ("ph", "f"), ("ts", "s"),
                ("dj", "j"),                 # Худжанд ↔ Xujand ↔ Khujand
                ("x", "h"), ("q", "k"), ("w", "v")]


def _text(v) -> str:
    """Любая ячейка → строка. Из xlsx приходят не только строки: номер дома
    читается как число, пустая ячейка как NaN, дата как datetime. Падать на этом
    посреди импорта реестра нельзя."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, float) and v != v:          # NaN
        return ""
    return str(v)


def to_latin(s: str | None) -> str:
    """Кириллица (уз/тдж) → узбекская латиница. Латиница проходит насквозь."""
    s = _text(s).translate(_APO).lower()
    for cyr, lat in _CYR2LAT:
        s = s.replace(cyr, lat)
    return s


def fuzzy(s: str | None) -> str:
    """Нечёткий ключ топонима: латиница → схлопывание пар → только a-z0-9."""
    s = to_latin(s).replace("'", "")
    for a, b in _FUZZY_PAIRS:
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return re.sub(r"(.)\1+", r"\1", s)


def strip_affix(s: str | None) -> str:
    """Ключ без узбекского изафета/аффикса в хвосте (Зангибойи → Зангибой)."""
    k = fuzzy(s)
    for suf in ("si", "i", "y"):
        if len(k) > 4 and k.endswith(suf):
            return k[: -len(suf)]
    return k


def keys(s: str | None) -> list[str]:
    """Ключи имени (без аффикса и с ним) — для поиска по справочнику."""
    a, b = fuzzy(s), strip_affix(s)
    return [a] if a == b else [b, a]


def strip_markers(s: str | None) -> str:
    """Имя собственное без тип-слов: «Самаркандская область» → «Самаркандская»,
    «Pastdarg‘om Tumani» → «Pastdarg‘om», «Do‘stlik Shahri» → «Do‘stlik».

    Нужно при индексации справочника: в реестре область называется «САМАРКАНДСКАЯ
    ОБЛАСТЬ», а в GeoNames — «Самаркандская область»; без среза тип-слова ключи
    не совпадают, привязка к области отваливается, и поиск кишлака теряет главную
    защиту — «искать только в своём регионе»."""
    kept = [t for t in _text(s).split() if _marker(t) is None]
    return " ".join(kept).strip(" .,;:-") or _text(s)


# ==============================================================================
#  Маркеры типов. PREF — привязывается вперёд (таджикский/русский порядок),
#  SUF — назад (узбекский), BOTH — по контексту.
# ==============================================================================
PREF, SUF, BOTH = "pref", "suf", "both"

# Маркеры пишем ОБЫЧНЫМИ словами (как в реестре), а ключи считаем тем же `fuzzy`,
# что и для топонимов. Иначе легко промахнуться руками: «дехаи» латинизируется в
# `dexai` (х→x), а не в `dehai`, и написанный вручную паттерн молча не срабатывает.
_MARKERS: list[tuple[str, str, tuple[str, ...]]] = [          # (уровень, вид, слова)
    ("country",  SUF,  ("ўзбекистон", "узбекистон", "узбекистан", "o'zbekiston",
                        "uzbekistan", "тоҷикистон", "точикистон", "таджикистан",
                        "tojikiston", "ҷумҳурии", "чумхурии", "россия")),
    # «в», «ш», «н», «ҷ» — таджикские сокращения (в.Суғд, ш.Конибодом, н.Сино,
    # ҷ.Пӯлотон). Без них у таджикских адресов область не извлекалась ВООБЩЕ:
    # там пишут не «вилояти Суғд», а «в.Суғд» либо просто «Суғд».
    ("region",   BOTH, ("вилояти", "вилоят", "viloyati", "viloyat", "область",
                        "обл", "oblast", "области", "в")),
    ("district", BOTH, ("тумани", "туман", "tumani", "tuman", "ноҳияи", "ноҳия",
                        "нохия", "нохияи", "район", "рayoni", "rayon", "р-н", "рн", "н")),
    ("city",     BOTH, ("шаҳри", "шаҳар", "шахри", "шахар", "shahri", "shahar",
                        "shaxri", "shaxar", "шаҳраки", "шахраки", "город", "г", "ш")),
    ("mahalla",  SUF,  ("мфй", "mfy", "мсг", "msg", "қфй", "qfy", "фй", "f.y",
                        "маҳалла", "махалла", "махалля", "mahalla", "маҳалласи")),
    ("mahalla",  PREF, ("маҳаллаи", "махаллаи", "мавзеи", "м")),
    # «ч», «чд», «ҷд», «ч/д», «ч/ш» — как пишут ҷамоат(и деҳотӣ) в реальных
    # таджикских адресах: «Б Гафуров Ч Овчикалъча», «н Бохтар, ҷд Мехнатобод».
    ("jamoat",   PREF, ("ҷамоати", "ҷамоат", "джамоат", "джамоати", "жамоат",
                        "jamoati", "jamoat", "чамоати", "ҷ", "ч", "чд", "ҷд",
                        "ч/д", "ч/ш", "ҷ/д")),
    ("village",  SUF,  ("қишлоғи", "кишлоғи", "кишлоги", "қишлоқ", "кишлок",
                        "qishlog'i", "qishloq", "овул", "аул")),
    ("village",  PREF, ("киш", "деҳаи", "деҳа", "деха", "дехаи", "деҳот", "дехот",
                        "посёлок", "пос", "qishlog'i")),
    ("street",   SUF,  ("кўчаси", "кучаси", "кўча", "куча", "ko'chasi", "ko'cha",
                        "kuchasi", "кўчада")),
    ("street",   PREF, ("ул", "улица", "ulitsa", "ul", "кӯчаи", "кучаи", "кӯча",
                        "хиёбони", "хиебони", "проспект", "пр", "пер", "к")),
    ("house",    PREF, ("уй", "uy", "дом", "д", "d", "хонаи", "хона")),
    ("flat",     PREF, ("кв", "квартира", "kv", "kvartira")),
]
# fuzzy-ключ → (уровень, вид). Первое вхождение выигрывает: списки идут от точного
# к грубому, поэтому «қишлоғи» останется village, а не перетрётся чем-то ещё.
_MARKER_KEYS: dict[str, tuple[str, str]] = {}
for _lvl, _kind, _words in _MARKERS:
    for _w in _words:
        _k = fuzzy(_w)
        if _k and _k not in _MARKER_KEYS:
            _MARKER_KEYS[_k] = (_lvl, _kind)

# Русское прилагательное-район без существительного: «ПАХТААБАДСКИЙ ,», «НАРПАЙСКИЙ ,»
_RU_ADJ = re.compile(r"^[a-z']+(?:skiy|skaya|skoy|skogo|skom)$")

_LEVELS_ORDER = ["country", "region", "district", "city", "jamoat",
                 "mahalla", "village", "street", "house", "flat"]

# Склейки, которые надо расцепить до токенизации: «uy:30», «к/Курук», «д.5»
_GLUE = re.compile(r"(?<=[a-zа-яёқғҳўҷӯӣ])[:/](?=[\wа-яё])", re.I)

# Таджикская запись лепит сокращение к названию через точку: «в.Суғд», «ш.Конибодом»,
# «н.Сино», «ҷ.Пӯлотон», «к.Д.Олимов». Расцепляем ТОЛЬКО одиночную букву с точкой
# в начале токена — инициалы внутри имени («Д.Олимов») при этом не трогаем.
_ABBR = re.compile(r"(?:(?<=^)|(?<=[\s,;]))([вшнҷчкгд])\.(?=[^\s.])", re.I)


def _marker(token: str) -> tuple[str, str] | None:
    """(уровень, вид) если токен — маркер типа, иначе None."""
    return _MARKER_KEYS.get(fuzzy(token))


def _is_number(token: str) -> bool:
    return bool(re.fullmatch(r"\d+[а-яa-z/\\-]*", token.strip(" .,;:"), re.I))


def _parse_chunk(chunk: str) -> list[tuple[str | None, str]]:
    """Один кусок адреса (между запятыми) → [(уровень|None, имя как в исходнике)]."""
    chunk = _ABBR.sub(r"\1 ", _GLUE.sub(" ", chunk))
    out: list[tuple[str | None, str]] = []
    buf: list[str] = []
    prefix: str | None = None

    def flush(level: str | None) -> None:
        nonlocal buf
        if buf:
            out.append((level, " ".join(buf).strip(" .,;:-")))
            buf = []

    for token in chunk.split():
        m = _marker(token)
        if m is None:
            buf.append(token)
            continue
        lvl, kind = m
        if kind == BOTH:
            # Неоднозначное слово (вилояти/шаҳри/тумани — одинаковы в уз и тдж).
            # Если незакрытый префикс уже держит имя, значит это начало НОВОГО
            # компонента (таджикский порядок), иначе — закрытие текущего (узбекский).
            kind = PREF if (prefix and buf) else SUF
        if kind == SUF and buf:
            flush(lvl)
            prefix = None
        else:
            flush(prefix)               # хвост предыдущего префикса
            prefix = lvl
    if buf:
        if prefix:
            flush(prefix)
        elif len(out) == 0 or all(l is None for l, _ in out):
            flush(None)                 # голое имя без типа (обычно город в ТДж-адресе)
        else:
            flush(None)
    return out


def parse(address: str | None, city: str | None = None) -> dict:
    """Официальный адрес → {country, region, district, city, jamoat, mahalla, village,
    street, house, flat, bare[], rest[]}. Значения — ИСХОДНЫЕ имена собственные
    (кириллица остаётся кириллицей — выездник читает адрес глазами).

    Дубли одного уровня («АНДИЖОН ВИЛОЯТИ … Андижанская область») схлопываются:
    остаётся ПЕРВОЕ непустое."""
    src = _text(address).strip()
    city = _text(city).strip()
    if city and fuzzy(city) and fuzzy(city) not in fuzzy(src):
        src = f"{city}, {src}"
    out: dict = {"bare": [], "rest": []}
    if not src:
        return out

    for chunk in re.split(r"[,;]+", src):
        chunk = chunk.strip()
        if not chunk:
            continue
        for level, name in _parse_chunk(chunk):
            if not name:
                continue
            if level is None:
                if _is_number(name):
                    out.setdefault("house", name)
                else:
                    out["bare"].append(name)
                continue
            if level in ("house", "flat") and not re.search(r"\d", name):
                # «д» без цифры — это не дом. В таджикском адресе так пишут
                # ДЕҲА (село): «н Бохтар, ҷд Мехнатобод д Зарнисор». Раньше
                # такой кусок просто выбрасывался вместе с названием села.
                if level == "house" and not out.get("village"):
                    out["village"] = name
                continue                          # «uy:Р/С» и прочий мусор в номере дома
            out.setdefault(level, name)

    # Русское прилагательное без существительного («…, НАРПАЙСКИЙ ,») — это район.
    # Проверять ОБЯЗАТЕЛЬНО до правила «первое голое имя = город», иначе район
    # съедается как город и привязка к adm2 теряется.
    for name in list(out["bare"]):
        if not _RU_ADJ.match(to_latin(name)):
            continue
        if not out.get("district"):
            out["district"] = name
            out["bare"].remove(name)
        elif not out.get("region"):
            out["region"] = name
            out["bare"].remove(name)
    # Голое имя в начале ТДж-адреса («Конибодом, Пулотон, к/Курук 33») — это город.
    if out["bare"] and not out.get("city"):
        out["city"] = out["bare"][0]
        out["rest"] = out["bare"][1:]
    else:
        out["rest"] = list(out["bare"])
    return out


# --- Человекочитаемая сборка ---------------------------------------------------
# Тип-слова по-русски (их читают и руководитель, и выездник), имена собственные —
# как в исходнике: местному «Ўрта Шахрихон» понятнее русской кальки.
_LABEL = {
    "region":   ("", " обл."),
    "district": ("", " р-н"),
    "city":     ("г. ", ""),
    "jamoat":   ("джамоат ", ""),
    "mahalla":  ("МФЙ ", ""),
    "village":  ("кишлак ", ""),
    "street":   ("ул. ", ""),
    "house":    ("д. ", ""),
    "flat":     ("кв. ", ""),
}
_PRETTY_ORDER = ["region", "district", "city", "jamoat", "mahalla", "village",
                 "street", "house", "flat"]


def _tc(s: str) -> str:
    """Аккуратный регистр: ВСЁ КАПСОМ → Как Обычно, остальное не трогаем."""
    if not s.isupper():
        return s
    return " ".join(w.capitalize() for w in s.split())


def pretty(parsed: dict, names: dict | None = None) -> str:
    """Читаемая строка. `names` — необязательная подмена на справочные написания
    (например, область по-русски из GeoNames): {'region': 'Андижанская', …}."""
    names = names or {}
    bits = []
    for level in _PRETTY_ORDER:
        val = names.get(level) or parsed.get(level)
        if not val:
            continue
        pre, post = _LABEL[level]
        bits.append(f"{pre}{_tc(val)}{post}")
    return ", ".join(bits)


def level_of(parsed: dict) -> str:
    """Самый точный опознанный уровень — им определяется, во что целиться геокодеру."""
    for level in ("street", "village", "mahalla", "jamoat", "city", "district", "region"):
        if parsed.get(level):
            return level
    return "unknown"


def geo_candidates(parsed: dict) -> list[tuple[str, str]]:
    """Кандидаты для справочника — от САМОГО ТОЧНОГО к самому грубому.

    Именно этот порядок и есть главное отличие от прежнего поведения, где из всех
    совпавших токенов брался населённый пункт с НАИБОЛЬШИМ населением, то есть
    заведомо самый грубый: адрес называл кишлак Зангибой, а точка ставилась
    в центр Самарканда за 40 км."""
    out = []
    for level in ("village", "mahalla", "jamoat", "city", "district", "region"):
        val = parsed.get(level)
        if val:
            out.append((val, level))
    return out
