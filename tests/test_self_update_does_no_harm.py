"""Самообновление не должно стоить человеку работы — четыре разобранных случая.

04.08.2026, состязательная проверка перед выкаткой нашла в 1fefa8d четыре
дефекта. Ни один не виден на столе разработчика, все четыре бьют в поле:

1. Полоса «Вышло обновление» была НЕДОСТИЖИМА. Ранний выход updateOffBar
   («онлайн, очередь пуста — показывать нечего») стоял ВЫШЕ ветки обновления, а
   это и есть обычное состояние работающего человека. Занятый выездник кнопку
   «Обновить» не увидел бы никогда.
2. Когда полоса всё-таки показывалась, она ПОДМЕНЯЛА собой очередь: пропадали
   счётчик неотправленного, «N фото нужно переснять [422]» и кнопка
   «Переснять» — ровно то, что добавляли накануне.
3. Оффлайн-старт кормил решение об обновлении снимком из IndexedDB. Без сети
   перезагрузка берёт из кэша ТУ ЖЕ старую оболочку — и цикл замыкается:
   перезагрузка, снова «сервер впереди», снова перезагрузка. Телефон
   становится непригоден именно там, где связи нет.
4. Возврат из системной камеры сам поднимает visibilitychange → refreshData →
   setMeta. PHOTO_PREPARING к этому моменту ещё пуст (он заполняется ПОСЛЕ
   чтения и сжатия), значит «человек свободен» → перезагрузка через 600 мс →
   снимок исчезает, не успев попасть в очередь.
"""

import pathlib

INDEX = (pathlib.Path(__file__).resolve().parent.parent
         / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")


def _fn(name: str, until: str) -> str:
    """Тело функции по ГРАНИЦЕ, а не по числу символов.

    Срез фиксированной длины ломается от любого добавленного комментария и
    сообщает «инвариант нарушен» там, где нарушен только счёт байтов.
    """
    i = INDEX.index(f"function {name}(")
    return INDEX[i:INDEX.index(until, i)]


def test_update_decision_uses_only_a_live_server_answer():
    body = _fn("noteShellVersion", "function reloadForUpdate(")
    assert "if(!live) return;" in body, "решение снова принимается по кэшу — цикл перезагрузок"
    assert "function noteShellVersion(v, live)" in INDEX


def test_offline_boot_does_not_claim_the_version_is_live():
    """Снимок поднимают без флага, живые ответы — с флагом."""
    assert "setMeta(snap.meta);" in INDEX, "оффлайн-снимок не должен объявляться живым"
    assert INDEX.count("setMeta(d.meta, true);") == 2, "живые ответы должны быть помечены"


def test_open_form_counts_as_busy_even_when_the_draft_is_already_synced():
    """draftDirty — «есть неотправленные правки», а не «человек работает».

    pushDraft гасит его каждые 20 секунд и при уходе в фон, поэтому открытая
    заполненная форма почти всё время выглядит «свободен». Человек посреди
    разговора с должником вылетел бы в список клиентов: текст черновика уцелел
    бы, а место — нет.
    """
    body = _fn("noteShellVersion", "function reloadForUpdate(")
    # The new policy never auto-reloads, even when the form appears idle.
    assert "location.reload" not in body and "UPDATE_READY=true" in body
    reload = _fn("reloadForUpdate", "// Проверка версии")
    assert "saveDraft()===false" in reload


def test_busy_covers_the_gap_between_the_camera_and_the_queue():
    body = _fn("reloadForUpdate", "// Проверка версии")
    assert "PHOTO_BUSY>0" in body, "промежуток «камера закрылась, фото ещё в памяти» не покрыт"
    assert "let PHOTO_BUSY=0;" in INDEX


def test_photo_busy_is_held_until_the_whole_batch_is_queued():
    """Пока идёт сеть по первому снимку, остальные живут только в памяти."""
    i = INDEX.index("async function upPhoto(")
    body = INDEX[i:INDEX.index("async function sendPhoto(")]
    assert "PHOTO_BUSY++;" in body
    send = body.index("await sendPhoto(")
    release = body.index("PHOTO_BUSY--;")
    assert send < release, "«занят» снимается до конца отправки пачки"


def test_update_bar_is_reachable_in_the_normal_state():
    body = _fn("updateOffBar", "function retakeFailedPhoto(")
    early = body.index('e.className="offbar"; e.innerHTML=""; return;')
    guard = body.rindex("!UPDATE_READY", 0, early)
    assert guard < early, "ранний выход снова закрывает собой полосу обновления"


def test_update_bar_adds_to_the_queue_line_instead_of_replacing_it():
    i = INDEX.index("function updateOffBar(")
    body = INDEX[i:INDEX.index("function retakeFailedPhoto(", i)]
    assert 'e.innerHTML=`<span>${esc(L().updready)}</span>`' not in body, \
        "ветка обновления снова подменяет полосу целиком"
    # Финальная отрисовка одна на всех: и очередь, и пересъёмка, и обновление.
    tail = body[body.index("if(UPDATE_READY) msg="):]
    assert "retakeFailedPhoto()" in tail, "кнопка «Переснять» обязана уцелеть"
    assert "reloadForUpdate()" in tail
    assert "t.photoretake" in body
    # A concurrent profile wipe must prevent a stale message being rendered.
    assert "if(generation!==WIPE_GEN)return;" in tail


def test_retake_link_is_made_only_by_an_explicit_tap():
    """Обычное добавление снимка не должно стирать чужую неудачную запись."""
    assert "firstFailedPhoto" not in INDEX, "привязка снова по «самому старому failed» КД"
    assert "RETAKE_FOR={op_id:it.op_id, rep:it.rep};" in INDEX
    assert "entry.replaces_op_id=retakeOf;" in INDEX


def test_retake_intent_does_not_leak_to_another_case():
    i = INDEX.index("async function upPhoto(")
    body = INDEX[i:INDEX.index("async function sendPhoto(")]
    assert "RETAKE_FOR.rep===rep" in body
    assert "RETAKE_FOR=null;" in body


def test_leaving_the_card_mid_batch_does_not_break_the_loop():
    i = INDEX.index("async function sendPhoto(")
    body = INDEX[i:INDEX.index("\n}", i)]
    assert "if(STATE.cur&&STATE.cur.rep===rep){" in body, \
        "STATE.cur снова трогают без проверки — TypeError оборвёт отправку пачки"
