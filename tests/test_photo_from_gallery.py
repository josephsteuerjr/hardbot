"""Приложить уже снятое фото — и не соврать при этом про место съёмки.

04.08.2026: из поля попросили загрузку БЕЗ немедленного снимка. Причина
понятная: пока фото рвались на отправке, люди снимали обычной камерой телефона,
а оформляли кейс позже. Запрет на это стоял не по недосмотру — `capture` в поле
выбора файла форсировал камеру, потому что точка съёмки снимается в момент
загрузки и для камерного снимка она правдива.

Отсюда решение: путь выбирает человек кнопкой. «Добавить фото» — камера, точка
едет как раньше. «Выбрать из галереи» — снимок доезжает целиком, но текущая
точка ему НЕ приписывается: он сделан когда-то в другом месте, и подставить
сегодняшнюю координату значит показать руководителю ложное место вручения.

Почему не различать по file.lastModified: часть webview отдаёт в нём время
копирования файла в песочницу, то есть «сейчас» для любого старого снимка.
Эвристика, которая на одной платформе врёт молча, хуже явной кнопки.
"""

import pathlib
import re

INDEX = (pathlib.Path(__file__).resolve().parent.parent
         / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")


def _photo_section() -> str:
    i = INDEX.index('<div class="fsec-h">${t.photos}</div>')
    return INDEX[i:i + 1200]


def test_camera_button_still_forces_the_camera():
    """Основная кнопка — по-прежнему съёмка здесь и сейчас."""
    sec = _photo_section()
    cam = re.search(r'<input type="file"[^>]*id="photo"[^>]*>', sec).group(0)
    assert 'capture="environment"' in cam
    assert "upPhoto('photo',true)" in cam


def test_gallery_button_exists_and_does_not_force_the_camera():
    """Ровно то, что просили из поля: приложить уже снятое."""
    sec = _photo_section()
    gal = re.search(r'<input type="file"[^>]*id="photogal"[^>]*>', sec).group(0)
    assert "capture" not in gal, "галерейный вход не должен форсировать камеру"
    assert "multiple" in gal, "снимков обычно несколько — выбирать по одному мучительно"
    assert "upPhoto('photogal',false)" in gal


def test_gallery_photo_never_gets_the_current_point():
    """Главный инвариант: точка снимается ТОЛЬКО на живом пути.

    Если grabGeo когда-нибудь окажется вне `if(live)`, галерейному снимку
    припишется место, где человек стоит сейчас, — и гео-контроль начнёт врать
    руководителю ровно там, где он ему нужнее всего.
    """
    i = INDEX.index("async function sendPhoto(")
    body = INDEX[i:INDEX.index("\n}", INDEX.index("updateOffBar();", i))]
    grab = body.index("geo=await grabGeo(4000,5000);")
    guard = body.rindex("if(live){", 0, grab)
    assert guard < grab, "grabGeo вызывается вне проверки live"
    # между охраной и вызовом не должно закрыться блока
    assert "}" not in body[guard + len("if(live){"):grab].replace(" ", "")


def test_photo_still_reaches_the_server_without_a_point():
    """Отсутствие точки не должно мешать самому фото доехать."""
    assert "if(geo) h[\"X-Photo-Geo\"]" in INDEX, "заголовок ставится только при наличии точки"
    i = INDEX.index("async function sendPhoto(")
    body = INDEX[i:INDEX.index("\n}", i)]
    assert 'fetch("/api/photo?rep="' in body
    # photoHeaders получил третий (опциональный) аргумент метаданных — якорь
    # устойчив к нему, а инвариант «гео едет заголовком» проверяется отдельно
    assert "photoHeaders(geo,ext2" in body
    assert 'if(geo) h["X-Photo-Geo"]' in INDEX


def test_all_selected_files_are_read_while_the_picker_is_alive():
    """iOS-инвариант: очистка input'а — только после чтения ВСЕХ файлов.

    Очистить раньше — значит убить security-scoped файлы, и второй-третий
    снимок из выбранных потеряется молча.
    """
    i = INDEX.index("async function upPhoto(")
    body = INDEX[i:INDEX.index("async function sendPhoto(")]
    read = body.index("photoBytes(small||f)")
    clear = body.index("el.value=\"\"")
    send = body.index("await sendPhoto(rep,prepared[i],")
    assert read < clear < send


def test_a_broken_file_does_not_swallow_the_rest_of_the_selection():
    """Один нечитаемый снимок из пяти не должен отменять остальные четыре."""
    i = INDEX.index("async function upPhoto(")
    body = INDEX[i:INDEX.index("async function sendPhoto(")]
    assert "Promise.allSettled(files.map" in body
    assert 'if(result.status!=="fulfilled"){toast(L().photolost);continue;}' in body


def test_hint_and_button_exist_in_all_three_languages():
    assert INDEX.count("fromgallery:") == 3
    assert INDEX.count("galhint:") == 3


def test_server_refuses_the_send_point_for_a_gallery_photo():
    """Правило владельца 26.08.2026: «координаты только по метаданным фото, не
    отправки». До сих пор оно держалось одной строкой в оболочке — а заголовок
    может прийти из старой очереди, чужого клиента или будущей версии, где
    условие живёт уже не там. Сервер обязан снимать точку сам."""
    web_src = (pathlib.Path(__file__).resolve().parent.parent
               / "server" / "web.py").read_text(encoding="utf-8")
    i = web_src.index("async def _photo(")
    body = web_src[i:web_src.index("\n\n\n", i)]
    assert 'if meta.get("source") == "gallery":' in body
    j = body.index('if meta.get("source") == "gallery":')
    assert "geo = None" in body[j:j + 700]
    # и снимается ДО записи строки индекса, иначе точка уже в базе
    assert j < body.index("db.record_photo(")
