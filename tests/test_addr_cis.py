"""Разбор официального адреса СНГ.

Все входные строки — РЕАЛЬНЫЕ из production-реестра (обезличенные: имена улиц и
номера домов настоящие, но без привязки к человеку). Смысл теста именно в этом:
синтетический «г. Худжанд, ул. Ленина, 5» разбирается любым парсером, а ломается
всё на трёхалфавитной каше, которая приходит от партнёров.
"""
import addr_cis as A


# --- транслитерация и ключи ---------------------------------------------------
def test_uzbek_cyrillic_to_latin():
    # В GeoNames узбекские топонимы записаны латиницей, в реестре — кириллицей.
    # Без этого преобразования не совпадает ничего.
    assert A.to_latin("Шахрихон") == "shaxrixon"
    assert A.to_latin("Пастдарғом") == "pastdarg'om"
    assert A.to_latin("Хўжаобод") == "xo'jaobod"


def test_fuzzy_collapses_spelling_variants():
    # Одно место, четыре написания — должен получиться один ключ.
    assert A.fuzzy("Шахрихон") == A.fuzzy("Shahrixon") == A.fuzzy("Shaxrixon")
    assert A.fuzzy("Пахтаобод") == A.fuzzy("Paxtaobod") == A.fuzzy("Pahtaobod")


def test_apostrophes_do_not_shred_names():
    # Апостроф U+2018 раньше резался как пунктуация: QOZIQORG‘ON → «qoziqorg».
    assert A.fuzzy("QOZIQORG‘ON") == A.fuzzy("Qoziqorgon") == A.fuzzy("Қозиқорғон")


def test_short_u_is_transliterated():
    # Ў (U+040E, узбекская) — не то же самое, что ӯ (U+04EF, таджикская);
    # в таблице транслита была только вторая, и 458 адресов не матчились.
    assert "o'" in A.to_latin("Ў")
    assert A.fuzzy("Учкўза") == A.fuzzy("Uchko‘za")


def test_izafet_affix_is_stripped():
    # «Зангибойи МФЙ» = «МФЙ села Зангибой»; в справочнике оно «Zangiboy».
    assert A.strip_affix("Зангибойи") == A.fuzzy("Зангибой")


def test_strip_markers_removes_type_words():
    assert A.strip_markers("Самаркандская область") == "Самаркандская"
    assert A.strip_markers("Pastdarg‘om Tumani") == "Pastdarg‘om"


# --- разбор: узбекский порядок (ИМЯ + тип) ------------------------------------
def test_uzbek_full_address():
    p = A.parse("ЎЗБЕКИСТОН,  САМАРҚАНД ВИЛОЯТИ,  ПАСТДАРҒОМ ТУМАНИ,  "
                "ЗАНГИБОЙИ МФЙ, ул. ЗАНГИБОЙИ ҚИШЛОҒИ")
    assert p["region"] == "САМАРҚАНД"
    assert p["district"] == "ПАСТДАРҒОМ"
    assert p["mahalla"] == "ЗАНГИБОЙИ"
    assert p["village"] == "ЗАНГИБОЙИ"


def test_uzbek_latin_without_commas():
    # Здесь между махаллёй и улицей запятой нет — разделять приходится по маркерам.
    p = A.parse("АНДИЖОН ВИЛОЯТИ, ШАХРИХОН ТУМАНИ, "
                "O‘RTA SHAXRIXON QFY ul. QOZIQORG‘ON KO‘CHASI")
    assert p["region"] == "АНДИЖОН"
    assert p["district"] == "ШАХРИХОН"
    assert p["mahalla"] == "O‘RTA SHAXRIXON"
    assert p["street"] == "QOZIQORG‘ON"


def test_house_and_flat():
    p = A.parse("ЎЗБЕКИСТОН,  САМАРҚАНД ВИЛОЯТИ,  САМАРҚАНД ШАҲРИ,  "
                "БАХТЛИ ХАЁТ МФЙ, ул. БАХТЛИ ХАЁТ КУЧАСИ, д. 5, кв. 45")
    assert p["city"] == "САМАРҚАНД"
    assert p["house"] == "5"
    assert p["flat"] == "45"


def test_garbage_house_is_dropped():
    # «uy:Р/С» — не номер дома, а мусор из выгрузки.
    p = A.parse("САМАРҚАНД ВИЛОЯТИ, НАРПАЙ ТУМАНИ, КАДИМ ҚФЙ, ТОШКУПРИК МФЙ, uy:Р/С")
    assert "house" not in p


# --- разбор: таджикский порядок (тип + ИМЯ) -----------------------------------
def test_tajik_prefix_order():
    # Ключевой случай: в таджикском тип-слово идёт ПЕРЕД именем, и наивный разбор
    # приписывал «Суғд» городу, а не области.
    p = A.parse("Ҷумҳурии  Тоҷикистон Вилояти Суғд Шаҳри Бӯстон Кӯчаи М.Миршакар")
    assert p["region"] == "Суғд"
    assert p["city"] == "Бӯстон"
    assert p["street"] == "М.Миршакар"


def test_tajik_jamoat_and_village():
    p = A.parse("вилояти Суғд шаҳри Истаравшан Ҷамоати Зарҳалол, деҳаи Суфи-Ориф")
    assert p["city"] == "Истаравшан"
    assert p["jamoat"] == "Зарҳалол"
    assert p["village"] == "Суфи-Ориф"


def test_tajik_short_form_bare_city():
    # Короткая форма без единого тип-слова у города: первое голое имя — город.
    p = A.parse("Конибодом,Пулотон,к/Курук 33")
    assert p["city"] == "Конибодом"
    assert p["street"].startswith("Курук")


# --- русские кальки и дубли ---------------------------------------------------
def test_russian_adjective_district():
    p = A.parse("УЗБЕКИСТАН, САМАРКАНДСКАЯ ОБЛАСТЬ, НАРПАЙСКИЙ , "
                "YANGIQURG‘ON F.Y ul. BALANDBOG d. 9")
    assert p["region"] == "САМАРКАНДСКАЯ"
    assert p["district"] == "НАРПАЙСКИЙ"


def test_duplicated_russian_block_is_collapsed():
    # Официальный блок продублирован по-русски внутри самого себя — берём первое.
    p = A.parse("ЎЗБЕКИСТОН, АНДИЖОН ВИЛОЯТИ, ПАХТАОБОД ТУМАНИ, "
                "Андижанская область, Пахтаобод тумани, Учкўза МСГ, ул. Учкуза, дом 1")
    assert p["region"] == "АНДИЖОН"
    assert p["district"] == "ПАХТАОБОД"
    assert p["mahalla"] == "Учкўза"


# --- человекочитаемый вид -----------------------------------------------------
def test_pretty_is_readable():
    p = A.parse("ЎЗБЕКИСТОН,  САМАРҚАНД ВИЛОЯТИ,  ТАЙЛОҚ ТУМАНИ,  ГУЛБА МФЙ, "
                "киш. ТАЙЛОК, ул. КАТТА КИШЛОК ҚИШЛОҒИ")
    assert A.pretty(p) == "Самарқанд обл., Тайлоқ р-н, МФЙ Гулба, кишлак Тайлок"


def test_pretty_uses_reference_region_name():
    p = A.parse("САМАРҚАНД ВИЛОЯТИ, НАРПАЙ ТУМАНИ, ТОТКЕНТ МФЙ")
    assert A.pretty(p, {"region": "Самаркандская"}).startswith("Самаркандская обл.")


# --- порядок кандидатов для геокодера ------------------------------------------
def test_candidates_go_from_precise_to_coarse():
    # Главная поломка старого геокодера: он выбирал из всех совпавших токенов тот,
    # у кого больше населения, то есть заведомо самый грубый. Адрес называл кишлак,
    # а точка вставала в центр области.
    p = A.parse("ЎЗБЕКИСТОН,  САМАРҚАНД ВИЛОЯТИ,  ПАСТДАРҒОМ ТУМАНИ,  "
                "ИЛМ МФЙ, ул. ИЛМ ҚИШЛОҒИ, д. 107")
    levels = [lvl for _name, lvl in A.geo_candidates(p)]
    assert levels.index("village") < levels.index("district") < levels.index("region")


def test_level_of_reports_best_known():
    assert A.level_of(A.parse("САМАРҚАНД ВИЛОЯТИ")) == "region"
    assert A.level_of(A.parse("САМАРҚАНД ВИЛОЯТИ, НАРПАЙ ТУМАНИ, ТОТКЕНТ МФЙ")) == "mahalla"


def test_empty_input_is_safe():
    assert A.parse("") == {"bare": [], "rest": []}
    assert A.parse(None) == {"bare": [], "rest": []}
    assert A.pretty(A.parse("")) == ""
