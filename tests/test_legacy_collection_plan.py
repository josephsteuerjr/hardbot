import pytest
from tools.legacy_pipeline.collection import Book, plan, KEY, PARTNER, OWNERS, EVENT_DAYS, FORECAST


def row(*, cid='1', partner='Creditor A', day='2026-11-01', value='Old', role='soft'):
    return {KEY: cid, PARTNER: partner, OWNERS['soft']: 'Soft One', OWNERS['hard']: 'Hard One',
            EVENT_DAYS[role]: day, 'Комментарий текущего месяца Софт': value,
            **dict(zip(FORECAST, (100, 'PTP', '2026-11-05', value)))}


def book(r, role='soft'):
    return Book(role+'.xlsb', role, r[OWNERS[role]], [r])


FIELDS = set(FORECAST) | {'Комментарий текущего месяца Софт', 'Сумма последнего платежа'}


def test_newer_soft_beats_older_hard_and_keeps_forecast_atomic():
    base = row()
    soft = row(day='2026-11-03', value='Fresh')
    soft.update(dict(zip(FORECAST, (0, None, None, 'Fresh'))))
    hard = row(role='hard', day='2026-11-02', value='Middle')
    for entries in ([book(soft), book(hard,'hard')], [book(hard,'hard'), book(soft)]):
        p = plan([base], entries, {}, FIELDS)
        assert not p.blocked
        values = {x['field']:x['after'] for x in p.changes}
        assert values['Сумма прогноза'] == 0 and values['дата обещания'] is None
        assert values['Общий результат'] == 'Fresh'


def test_conflicting_same_day_blocks_whole_plan():
    p = plan([row()], [book(row(value='Unknown order'))], {}, FIELDS)
    assert p.blocked and any(x['kind']=='ambiguous_forecast_group' for x in p.conflicts)


def test_old_owner_changes_are_not_silently_discarded():
    source = row(day='2026-11-02', value='Unsaved work')
    current = row(); current[OWNERS['soft']] = 'New Owner'
    p = plan([current], [book(source)], {}, FIELDS)
    assert p.blocked and not p.changes and p.stale_assignments == 1


def test_partner_collision_and_duplicate_rows_never_merge():
    p = plan([row()], [book(row(partner='Creditor B'))], {}, FIELDS)
    assert p.blocked and not p.changes
    p = plan([row(),row()], [], {}, FIELDS)
    assert p.blocked


def test_employee_payment_copy_cannot_change_analyst_payment():
    base = row(); base['Сумма последнего платежа'] = 10
    source = row(day='2026-11-02'); source['Сумма последнего платежа'] = 999
    p = plan([base], [book(source)], {}, FIELDS)
    assert not p.blocked and not p.changes


def test_blank_and_future_dates_not_used_to_prove_order():
    for d in (None, '2099-01-01'):
        p = plan([row()], [book(row(day=d,value='Wrong'))], {}, FIELDS)
        assert p.blocked


def test_equal_values_and_older_snapshot_are_noops():
    assert not plan([row(day='2026-11-02')], [book(row())], {}, FIELDS).changes
    p = plan([row()], [book(row())], {}, FIELDS)
    assert not p.blocked and not p.changes


def test_readonly_soft_comment_in_hard_book_does_not_flow_back():
    source=row(role='hard',day='2026-11-02',value='Hard mirror');source.update({f:row()[f] for f in FORECAST})
    p=plan([row()], [book(source,'hard')], {}, FIELDS)
    assert not p.blocked and not p.changes


def test_previous_month_comment_is_not_resurrected_after_clear():
    from datetime import date
    base=row(day=None,value='');source=row(day='2026-10-30',value='Old month')
    p=plan([base],[book(source)],{}, {'Комментарий текущего месяца Софт'},today=date(2026,11,3))
    assert not p.blocked and not p.changes


def test_undated_comment_cannot_refill_a_cleared_month():
    base=row(day=None,value='');source=row(day=None,value='Unknown old text')
    p=plan([base],[book(source)],{}, {'Комментарий текущего месяца Софт'})
    assert p.blocked and not p.changes
