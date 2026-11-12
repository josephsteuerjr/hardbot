from dataclasses import replace
import copy
import json
import uuid
import pytest

from crm import storage, store
from crm.contract import CRMError
from test_crm_core import crm_world


def courier(w):
    return replace(w['member'],role='courier',capabilities=frozenset({'delivery','correct','task'}),read_all_contracts=True)


def body(w, *, delivered=True, address='Delivery address', phone='-', comment='Given to recipient'):
    return dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='delivery',
        contract_ids=[w['contracts'][0]],base_revisions={},payload=dict(delivered=delivered,
        delivery_address=address,delivery_phone=phone,delivery_comment=comment))


def test_delivery_is_one_replayable_fact_without_a_fake_visit_or_contact_edit(crm_world):
    w=crm_world;p=courier(w);command=body(w);unchanged=copy.deepcopy(command)
    first=store.command(p,command)
    assert store.command(p,command)==first and command==unchanged
    card=store.card(p,w['person'])
    assert len(card['history'])==1 and card['history'][0]['kind']=='delivery'
    assert card['history'][0]['payload']['delivery_phone']=='-'
    assert all('visit_date' not in row['fields'] and 'phone' not in row['fields'] for row in card['contracts'])
    command['payload']['delivery_comment']='Different'
    with pytest.raises(CRMError):store.command(p,command)
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]==1


def test_unsuccessful_attempt_with_missing_phone_is_preserved(crm_world):
    w=crm_world;command=body(w,delivered=False,address='',phone='—',comment='Recipient absent')
    store.command(courier(w),command)
    item=store.card(courier(w),w['person'])['history'][0]
    assert item['payload']['delivered'] is False and item['payload']['delivery_comment']=='Recipient absent'
    assert item['payload']['delivery_phone']=='-'


@pytest.mark.parametrize('change',[{'delivered':'yes'}, {'delivery_comment':''},
                                  {'delivery_address':''}, {'delivery_phone':'not-a-number'},
                                  {'values':{'phone':'forged'}}, {'amount':'100'}])
def test_delivery_fields_are_validated_without_writing_history(crm_world,change):
    command=body(crm_world);command['payload'].update(change)
    with pytest.raises(CRMError):store.command(courier(crm_world),command)
    assert store.card(courier(crm_world),crm_world['person'])['history']==[]


def test_common_courier_read_does_not_grant_delivery_on_another_assignment(crm_world):
    w=crm_world;p=replace(courier(w),actor_uid=w['stranger'].actor_uid,
        employee_uuid=w['stranger'].employee_uuid,employee_scope=w['stranger'].employee_scope)
    assert len(store.card(p,w['person'])['contracts'])==2
    with pytest.raises(CRMError):store.command(p,body(w))
