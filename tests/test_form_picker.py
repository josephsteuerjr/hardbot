"""Свой список вариантов вместо системного пикера.

04.08.2026: у выездника не открывался системный `<select>` — он полтора часа не
мог выбрать статус визита, форма при этом выглядела рабочей, а автосохранение
пять раз отправило пустоту.

Ключевое требование к фиксу: `<select>` ОСТАЁТСЯ в DOM и остаётся источником
истины. По нему читают форму (readForm), в него восстанавливают черновик
(restoreDraftIntoForm), на его `change` подписан автосейв. Наша кнопка только
меняет способ выбрать — если она сломается, данные не поедут мимо.
"""

import pathlib

INDEX = (pathlib.Path(__file__).resolve().parent.parent
         / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")


def test_native_select_is_still_in_the_dom():
    """Убрать <select> — значит сломать чтение формы, черновик и автосейв разом."""
    assert '<select data-k="${f.key}" class="nativesel">' in INDEX
    assert '.nativesel{' in INDEX, "нативный select должен прятаться стилем, а не удаляться"


def test_readers_still_look_at_the_select():
    """Читатели формы не должны знать про кнопку."""
    assert '#panel select[data-k]' in INDEX          # readForm
    assert "document.querySelector('#panel select[data-k=\"'+k+'\"]')" in INDEX


def test_choice_dispatches_change_event():
    """Автосейв и черновик подписаны на change — без него выбор не сохранится."""
    i = INDEX.index("function choosePick(")
    body = INDEX[i:i + 900]
    assert 'sel.value=val' in body
    assert 'dispatchEvent(new Event("change"' in body


def test_labels_resync_after_draft_restore():
    """Черновик проставляет значения мимо кнопок — подписи обязаны догнать."""
    setup = INDEX.index("function setupFormDraft(")
    body = INDEX[setup:setup + 700]
    assert "restoreDraftIntoForm();" in body
    assert "syncPickLabels();" in body
    assert body.index("restoreDraftIntoForm();") < body.index("syncPickLabels();")


def test_empty_dropdown_shows_a_warning_not_a_dead_control():
    """Пустой справочник обязан быть виден человеку, а не притворяться полем."""
    i = INDEX.index("function formField(")
    body = INDEX[i:i + 1200]
    assert 'class="warnfld"' in body
    assert "if(!list.length) return" in body


def test_picker_refuses_to_open_on_an_empty_list():
    i = INDEX.index("function openPick(")
    body = INDEX[i:i + 700]
    assert "if(!list.length)" in body


def test_warning_text_exists_in_all_three_languages():
    assert INDEX.count("nolist:") == 3, "подсказка должна быть на ru/uz/tg"
