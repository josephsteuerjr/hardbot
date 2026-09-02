"""Статические инварианты оболочки: вкладка руководителя и барабан даты.

Тестов на полноту переводов в проекте не было вообще, и пропуск ключа
обнаруживался только в поле; остальные пункты фиксируют инварианты, которые
дешевле сломать, чем заметить.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = (ROOT / "server" / "miniapp" / "index.html").read_text("utf-8")


def _tab_section():
    return INDEX[INDEX.index("let DG={"):INDEX.index("const PHOTOBLOBS")]


def test_tab_is_gated_by_role():
    assert "STATE.roles && STATE.roles.manager" in INDEX
    assert 'id:"digest"' in INDEX


def test_tab_labels_exist_in_all_three_languages():
    for key in ('digest:"', "dgneednet:", "dgerr:", "dggone:", "dggoneemp:"):
        assert INDEX.count(key) >= 3, key


def test_roles_survive_offline():
    assert "roles:d.roles" in INDEX          # cacheBootstrap кладёт роль в снимок
    assert "STATE.roles=snap.roles" in INDEX  # catch-ветка boot её поднимает


def test_digest_map_uses_local_vendor_only():
    body = _tab_section()
    assert "cdn.jsdelivr.net" not in body
    assert "protomaps.github.io" not in body
    assert "ensureMapLibs" in body and "RASTER_STYLE" in body


def test_digest_onclick_carries_only_numeric_indices():
    """esc/enc не экранируют апостроф (ФИО на узбекской латинице) — в onclick
    уходят ТОЛЬКО числовые индексы состояния (dgPick*), не данные из БД."""
    body = _tab_section()
    for m in re.finditer(r'onclick="([^"]*)"', body):
        call = m.group(1)
        assert "${r.employee" not in call and "${p.client" not in call, call
        assert "${it." not in call, call


def test_digest_does_not_compute_request_dates_on_client():
    body = _tab_section()
    assert "toISOString" not in body
    # DG.date присваивается только из ответа сервера или диплинка
    for m in re.finditer(r"DG\.date=([^;]+);", body):
        rhs = m.group(1)
        assert "new Date" not in rhs, rhs


def test_deep_link_flag_is_one_shot_and_query_is_cleaned():
    assert "START_TAB_USED=true" in INDEX
    assert 'u.searchParams.delete("tab")' in INDEX


def test_no_auto_polling_of_the_digest():
    for m in re.finditer(r"setInterval\(([^,]+),", INDEX):
        assert "dgLoad" not in m.group(1) and "renderDigest" not in m.group(1)


def test_dg_uses_generation_counter_not_busy_latch():
    body = _tab_section()
    assert "++DG.seq" in body and "seq!==DG.seq" in body
    assert "DG.busy" not in body


def test_keep_queue_on_identity_drift():
    """Дрейф личности руководителя (acting-фолбэк) не стирает очередь."""
    assert INDEX.count("await clearLocalData({keepQueue: left > 0})") >= 3


# --- барабан даты -------------------------------------------------------------
def test_wheel_css_uses_real_variables():
    assert "var(--bd)" not in INDEX and "var(--ink)" not in INDEX
    assert ".wheelitem.on{" in INDEX and ".wheelband{" in INDEX


def test_hidden_input_is_the_source_of_truth():
    """Как у пикера: скрытый input — истина, барабан лишь способ выбрать
    (инвариант написан кровью 04.08.2026, см. test_form_picker)."""
    assert 'input type="hidden" data-k=' in INDEX
    i = INDEX.index("function wheelSet(")
    body = INDEX[i:INDEX.index("\n}", i)]
    assert 'dispatchEvent(new Event("change"' in body


def test_wheel_base_date_is_the_later_of_server_and_local():
    i = INDEX.index("function srvToday(")
    body = INDEX[i:INDEX.index("\n}", i)]
    assert "localToday()" in body and "s>loc" in body


def test_typed_clear_is_explicit_button_only():
    assert "FORM_CLEARED" in INDEX
    assert "cleared_keys" in INDEX
    # очистка попадает в payload только из FORM_CLEARED
    i = INDEX.index("function readForm(")
    body = INDEX[i:INDEX.index("\n}", i)]
    assert "FORM_CLEARED.has(f.key)" in body


def test_promise_section_hides_without_typed_fields():
    i = INDEX.index("function promiseSection(")
    body = INDEX[i:INDEX.index("\n}", i)]
    assert 'if(!tf2.length) return ""' in body.replace("  ", "")\
        or "if(!tf2.length) return" in body
