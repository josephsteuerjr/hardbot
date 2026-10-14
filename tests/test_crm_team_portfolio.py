"""Full team CRM starts with assignments, not just activity; read scope is exact."""
import asyncio
import json

import pytest
from aiohttp import web as aio_web
from aiohttp.test_utils import make_mocked_request

import db
import web
import digest_data
from test_soft_manager_stats import world


def fetch(monkeypatch,profile='soft',employee='',team=None):
    monkeypatch.setattr(web,'_manager_gate',lambda req:(999,{'role':'security'},team or ['S1']))
    req=make_mocked_request('GET','/api/manager/portfolio?mode='+profile+('&employee='+employee if employee else ''))
    return json.loads(asyncio.run(web._manager_portfolio(req)).text)


def test_full_soft_portfolio_includes_unworked_cases_without_foreign_owner(world,monkeypatch):
    data=fetch(monkeypatch)
    assert data['contracts']==4
    assert {cid for row in data['items'] for cid in row['ids']}=={'A','B','D','E'}
    assert {r['employee'] for r in data['items']}=={'S1'}
    assert all(r['profile']=='soft' and not r['visited'] for r in data['items'])
    assert all('C' not in r['ids'] for r in data['items'])


def test_hard_owner_groups_do_not_merge_with_other_employee(world,monkeypatch):
    data=fetch(monkeypatch,profile='hard',team=['H1','H2'])
    assert len([p for p in data['items'] if p['client']=='Клиент один'])==2
    assert data['contracts']==5
    assert {p['employee'] for p in data['items']}=={'H1','H2'}


def test_crm_money_preserves_cents_and_known_zero(world,monkeypatch):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET debt_today='1000.25' WHERE contract_id='A'")
        conn.execute("UPDATE clients SET last_payment_sum='1 500,25 ₽' WHERE contract_id='A'")
        conn.execute("UPDATE clients SET debt_today='0' WHERE contract_id='B'")
        conn.commit()
    data=fetch(monkeypatch)
    row=next(r for r in data['items'] if 'A' in r['ids'])
    assert row['totals'][0]['sum']==1000.25
    card=digest_data.person_data({},'2026-10-06','S1','A',employees=['S1'],by_soft=True)
    contracts={r['contract_id']:r for r in card['contracts']}
    assert contracts['A']['total']==1000.25 and contracts['B']['total']==0
    assert contracts['A']['last_payment_sum']==1500.25
    assert 'C' not in contracts


def test_employee_drilldown_cannot_expand_scope(world,monkeypatch):
    with pytest.raises(aio_web.HTTPNotFound):
        fetch(monkeypatch,employee='S2')
    with pytest.raises(aio_web.HTTPBadRequest):
        fetch(monkeypatch,profile='both')


def test_manager_card_shows_payment_and_keeps_foreign_payment_author_out(world):
    with db.connect() as conn:
        conn.execute("INSERT INTO payment_evidence(op,contract_id,employee,paid_at,amount,currency,source,created_at) VALUES('p1','A','S1','2026-10-06',10,'₽','manual','2026-10-06T08:00:00')")
        conn.execute("INSERT INTO payment_evidence(op,contract_id,employee,paid_at,amount,currency,source,created_at) VALUES('p2','A','S2','2026-10-06',20,'₽','manual','2026-10-06T08:00:00')")
        conn.commit()
    card=digest_data.person_data({},'2026-10-06','S1','A',employees=['S1'],by_soft=True)
    assert [p['op'] for p in card['reported_payments']]==['p1']
    assert {r['contract_id'] for r in card['contracts']}=={'A','B'}


def test_current_crm_case_matches_selected_owner(world):
    card=digest_data.person_data({},'2026-10-06','H1','A',employees=['H1','H2'],current_only=True)
    assert {r['contract_id'] for r in card['contracts']}=={'A'}
    fields={field['key']:field for field in card['visit']}
    for key in ('promise_date','forecast_sum','forecast'):
        assert fields[key]['title'] != key
        assert fields[key]['title'] == db.BY_KEY[key].title
    soft=digest_data.person_data({},'2026-10-06','S1','A',employees=['S1'],by_soft=True,current_only=True)
    assert {r['contract_id'] for r in soft['contracts']}=={'A','B'}
    assert digest_data.person_data({},'2026-10-06','H2','A',employees=['H1','H2'],current_only=True) is None
