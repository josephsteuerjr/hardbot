"""Regression cases from the CIS CRM review. Synthetic data only."""
from dataclasses import replace
import os
import uuid

import pytest
import db
from crm import documents, exchange, scripts, shared_server, store
from crm.contract import CRMError
from test_crm_core import crm_world, call_body


def test_replayed_export_cannot_return_revoked_contracts(crm_world):
    w = crm_world
    analyst = replace(w['member'], capabilities=w['member'].capabilities | {'exchange'})
    body = dict(operation_id=str(uuid.uuid4()), period='2026-11', target_id='review')
    first = exchange.export(analyst, body)
    assert first == exchange.export(analyst, body)
    with db.connect() as conn:
        conn.execute('DELETE FROM crm_assignments WHERE contract_uuid=?', (w['contracts'][0],))
    with pytest.raises(CRMError) as caught:
        exchange.export(analyst, body)
    assert caught.value.code == 'scope_changed'


@pytest.mark.parametrize('revocation', ['capability', 'assignment'])
def test_correction_requires_current_original_action_authority(crm_world, revocation):
    w = crm_world
    event = store.command(w['member'], call_body(w))
    member = w['member']
    if revocation == 'capability':
        member = replace(member, capabilities=member.capabilities - {'call'})
    else:
        with db.connect() as conn:
            conn.execute("DELETE FROM crm_assignments WHERE capability='call'")
    body = dict(schema_version=1, operation_id=str(uuid.uuid4()), kind='correction',
                contract_ids=w['contracts'], correction_of=event['event_id'], reason='Typo',
                payload={'target_kind':'call','values':{'soft_comment':'Corrected'}},
                base_revisions={cid:{'call':1} for cid in w['contracts']})
    with pytest.raises(CRMError) as caught:
        store.command(member, body)
    assert caught.value.status == 403
    assert len(store.card(w['owner'], w['person'])['history']) == 1


def test_document_retry_is_exact_and_media_type_cannot_change(crm_world, tmp_path, monkeypatch):
    monkeypatch.setenv('HARD_CRM_DOCUMENTS', str(tmp_path/'documents'))
    w = crm_world
    args=(w['member'], str(uuid.uuid4()), w['contracts'][0], 'synthetic.pdf', 'application/pdf', b'synthetic')
    first = documents.upload(*args)
    assert documents.upload(*args) == first
    with pytest.raises(CRMError) as caught:
        documents.upload(*args[:4], 'image/png', args[5])
    assert caught.value.code == 'operation_conflict'


def test_accounting_revocation_also_blocks_payment_correction(crm_world):
    w=crm_world
    accounting=replace(w['member'],capabilities=w['member'].capabilities|{'accounting'})
    cid=w['contracts'][0]
    event=store.command(accounting,dict(schema_version=1,operation_id=str(uuid.uuid4()),
        kind='payment_confirmed',contract_ids=[cid],payload=dict(amount='10.00',currency='RUB',source='fixture',source_id='payment-1')))
    with pytest.raises(CRMError) as caught:
        store.command(w['member'],dict(schema_version=1,operation_id=str(uuid.uuid4()),
            kind='correction',contract_ids=[cid],correction_of=event['event_id'],reason='Synthetic correction',
            payload=dict(target_kind='payment_confirmed',amount='20.00',currency='RUB',source='fixture',source_id='payment-1')))
    assert caught.value.code=='forbidden'


def test_shared_app_factory_does_not_enable_crm_in_legacy_app(monkeypatch):
    monkeypatch.delenv('HARD_CRM_PILOT', raising=False)
    class SyntheticSessions:
        pass
    app = shared_server.create_app(sessions=SyntheticSessions())
    assert any(route.resource.canonical.startswith('/api/crm/') for route in app.router.routes())
    assert os.environ.get('HARD_CRM_PILOT') is None


def publish(w, title, revision):
    saved = scripts.mutate(w['owner'], dict(operation_id=str(uuid.uuid4()), action='save',
        base_revision=revision, content={'title':title,'steps':[{'title':'Step','text':title}]}))
    return scripts.mutate(w['owner'], dict(operation_id=str(uuid.uuid4()), action='publish',base_revision=saved['revision']))


def test_call_keeps_known_script_reference_after_new_publication(crm_world):
    w=crm_world
    old=publish(w,'First',0)
    reference=old['script_ref']
    publish(w,'Second',old['revision'])
    body=call_body(w)
    body['payload']['script_ref']=reference
    accepted=store.command(w['member'],body)
    assert store.command(w['member'],body)==accepted
    assert store.card(w['member'],w['person'])['history'][0]['payload']['script_ref']==reference
    for changed in [{**reference,'team_id':'foreign'}, {**reference,'script_hash':'0'*64}, {**reference,'published_revision':999}]:
        wrong=call_body(w)
        wrong['payload']['script_ref']=changed
        with pytest.raises(CRMError):
            store.command(w['member'],wrong)
