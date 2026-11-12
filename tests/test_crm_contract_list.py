from dataclasses import replace
from crm import store
from test_crm_core import crm_world


def test_common_lookup_returns_readable_clients_without_edit_permission(crm_world):
    w=crm_world
    principal=replace(w['stranger'],read_all_contracts=True)
    rows=store.contract_list(principal,'synthetic')['contracts']
    assert len(rows)==2 and all(row['allowed_actions']==[] for row in rows)
    assert all(row['client_name']=='Synthetic person' and row['fields']['debt_total']=='123.45' for row in rows)
    assert store.contract_list(principal,'absent')['contracts']==[]


def test_work_lookup_filters_and_pages_only_writable_contracts(crm_world):
    w=crm_world
    principal=replace(w['member'],role='courier',capabilities=frozenset({'delivery'}),read_all_contracts=True)
    result=store.contract_list(principal,work='delivery',limit=1)
    assert result['truncated'] and len(result['contracts'])==1
    assert result['contracts'][0]['allowed_actions']==['delivery']
    other=replace(principal,actor_uid=w['stranger'].actor_uid,employee_uuid=w['stranger'].employee_uuid,
                  employee_scope=w['stranger'].employee_scope)
    assert store.contract_list(other,work='delivery')['contracts']==[]
