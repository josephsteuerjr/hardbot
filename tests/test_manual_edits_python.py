from datetime import date

import pytest

from pult.core.manual_edits import Store,view


def grid(hard='old hard',soft='old soft'):
    return [[],['Номер КД / ID','Дата ручной правки','Дата выезда','комментарий звонка','Дата ручной правки','статус звонка софт','Историчный комментарий Софт'],
            ['сх','хард','хард','хард','софт','софт','софт'],
            ['T-1',None,'2026-10-20',hard,None,soft,'history']]


def seed(tmp_path):
    store=Store(tmp_path/'state.sqlite3')
    book=tmp_path/'portfolio.xlsx'
    p=store.observe(book,'before',view(grid()),saved_on=date(2026,10,30),continuously_observed=True)
    assert p.calibrated and not p.writes
    store.commit(p,post_sha256='before',operation_id='seed',origin='calibration')
    return store,book


@pytest.mark.parametrize('hard,soft,expected',[
    ('new','old soft',(1,)),('old hard','new',(4,)),('new','new',(1,4)),
    ('','old soft',(1,)),('old hard','',(4,)),('old hard','old soft',()),
])
def test_python_diff_per_block_and_clear(tmp_path,hard,soft,expected):
    store,book=seed(tmp_path)
    p=store.observe(book,'after',view(grid(hard,soft)),saved_on=date(2026,10,30),continuously_observed=True)
    assert tuple(w[1] for w in p.writes)==expected
    assert all(w[2]==date(2026,10,30) for w in p.writes)


def test_initial_state_does_not_invent_history(tmp_path):
    p=Store(tmp_path/'s.db').observe(tmp_path/'a.xlsx','s',view(grid('existing','existing')),saved_on=date(2026,10,30),continuously_observed=True)
    assert p.calibrated and p.writes==() and not p.changed_blocks


def test_recalculation_is_not_manual(tmp_path):
    store=Store(tmp_path/'state.db');book=tmp_path/'a.xlsx'
    first=view(grid('=SUM(A1:A2)','old soft'),formula_cells={(3,3)})
    p=store.observe(book,'before',first,saved_on=None,continuously_observed=False)
    store.commit(p,post_sha256='before',operation_id='seed',origin='calibration')
    p=store.observe(book,'after',first,saved_on=date(2026,10,30),continuously_observed=True)
    assert not p.writes
    p=store.observe(book,'after',view(grid('=SUM(A1:A3)','old soft'),formula_cells={(3,3)}),saved_on=date(2026,10,30),continuously_observed=True)
    assert tuple(w[1] for w in p.writes)==(1,)


def test_known_automatic_write_is_not_manual(tmp_path):
    store,book=seed(tmp_path)
    p=store.observe(book,'after',view(grid('automatic','manual')),saved_on=date(2026,10,30),continuously_observed=True,
                    known_automatic=[('T-1','excel_visit_edited_on','комментарий звонка','automatic')])
    assert tuple(w[1] for w in p.writes)==(4,)


def test_observation_gap_never_fabricates_day(tmp_path):
    store,book=seed(tmp_path)
    p=store.observe(book,'after',view(grid('manual','old soft')),saved_on=date(2026,10,30),continuously_observed=False)
    assert not p.writes and p.review_blocks
    with pytest.raises(ValueError,match='день'):
        store.commit(p,post_sha256='after',operation_id='gap',origin='manual_observation')


def test_automatic_post_state_acknowledgement(tmp_path):
    store,book=seed(tmp_path)
    store.automatic(book,'after',view(grid('automatic','old soft')),pre_sha256='before',operation_id='auto')
    p=store.observe(book,'same',view(grid('automatic','old soft')),saved_on=date(2026,10,30),continuously_observed=True)
    assert not p.writes


def test_idempotent_receipt_and_stale_baseline(tmp_path):
    store,book=seed(tmp_path)
    p=store.observe(book,'after',view(grid('manual','old soft')),saved_on=date(2026,10,30),continuously_observed=True)
    store.commit(p,post_sha256='written',operation_id='manual',origin='manual_observation')
    store.commit(p,post_sha256='written',operation_id='manual',origin='manual_observation')
    with pytest.raises(ValueError,match='снимок'):
        store.commit(p,post_sha256='written2',operation_id='different',origin='manual_observation')
    with pytest.raises(ValueError,match='operation_id'):
        store.commit(p,post_sha256='other',operation_id='manual',origin='manual_observation')


def test_new_rows_have_unknown_history(tmp_path):
    store,book=seed(tmp_path)
    g=grid();g.append(['NEW',None,'2026-10-30','new',None,'new',None])
    p=store.observe(book,'after',view(g),saved_on=date(2026,10,30),continuously_observed=True)
    assert not p.writes


def test_duplicate_contract_is_rejected():
    g=grid();g.append(g[-1].copy())
    with pytest.raises(ValueError,match='Повторяющийся'):
        view(g)


def test_same_contract_in_different_partner_client_rows_and_sorting(tmp_path):
    g=grid();g[1].extend(['Партнер','Клиент']);g[2].extend(['сх','сх']);g[3].extend(['A','Client A'])
    second=g[3].copy();second[-2:]=['B','Client B'];g.append(second)
    store=Store(tmp_path/'state.db');book=tmp_path/'a.xlsx'
    p=store.observe(book,'before',view(g),saved_on=None,continuously_observed=False)
    store.commit(p,post_sha256='before',operation_id='seed',origin='calibration')
    g[3],g[4]=g[4],g[3]
    p=store.observe(book,'sort',view(g),saved_on=date(2026,10,30),continuously_observed=True)
    assert not p.writes
    g[4][3]='edit A'
    p=store.observe(book,'edit',view(g),saved_on=date(2026,10,30),continuously_observed=True)
    assert tuple((w[0],w[1]) for w in p.writes)==((4,1),)


def test_pending_date_survives_open_workbook_and_restart(tmp_path):
    store,book=seed(tmp_path)
    p=store.observe(book,'manual',view(grid('manual','old soft')),saved_on=date(2026,10,30),continuously_observed=True)
    store.commit(p,post_sha256='manual',operation_id='observe',origin='manual_observation')
    restarted=Store(store.path)
    assert restarted.pending(book)==(('T-1','excel_visit_edited_on','2026-10-30'),)
    g=grid('manual','old soft');g[3][1]='2026-10-30'
    restarted.automatic(book,'date-written',view(g),pre_sha256='manual',operation_id='write')
    assert not restarted.pending(book)


def test_gap_review_is_persisted_without_inventing_date(tmp_path):
    store,book=seed(tmp_path)
    p=store.observe(book,'manual',view(grid('manual','old soft')),saved_on=date(2026,10,30),continuously_observed=False)
    store.commit(p,post_sha256='manual',operation_id='gap',origin='manual_observation',retain_unknown_day_for_review=True)
    assert store.reviews(book) and not store.pending(book)
    assert store.baseline(book)[0]=='manual'


def test_automatic_writer_cannot_hide_unobserved_manual_state(tmp_path):
    store,book=seed(tmp_path)
    with pytest.raises(ValueError,match='не учтены'):
        store.automatic(book,'after',view(grid('automatic','old soft')),pre_sha256='different',operation_id='auto')


def test_excel_com_artificial_timezone_is_not_a_data_edit():
    from datetime import datetime,timezone
    from pult.core.manual_edits import comparable
    assert comparable(datetime(2026,10,30,18,30,tzinfo=timezone.utc))==comparable(datetime(2026,10,30,18,30))
