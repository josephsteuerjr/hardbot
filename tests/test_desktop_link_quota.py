"""Phone and PC admission share identity, with one separate PC activation."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from aiohttp.test_utils import make_mocked_request
from aiohttp import web as aio_web

import db
import web

UID=801001

@pytest.fixture
def entry(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'desktop-quota.db')
    db.init_db()
    db.bind_agent(UID,'Synthetic Soft')
    web._ADMIN_FAILS.clear()
    return db.create_app_link(UID)

def info(code):
    with db.connect() as conn:
        return dict(conn.execute('SELECT * FROM app_links WHERE code=?',(code,)).fetchone())

def http(handler,code,**extra):
    request=make_mocked_request('POST','/api/agent/device/enroll')
    async def body():return {'code':code,'device':'Synthetic PC',**extra}
    request.json=body
    return json.loads(asyncio.run(handler(request)).body)

def spend_phone(code):
    return [db.redeem_app_link(code,agent='android') for _ in range(db.APP_LINK_DEFAULT_USES)]

def test_phone_exhaustion_preserves_one_pc_activation_and_phone_tokens(entry):
    phones=spend_phone(entry)
    assert all(phones) and db.redeem_app_link(entry,agent='android') is None
    assert db.app_link_status(entry)=='used_up'
    assert db.app_link_status(entry,desktop=True)=='live'
    # Fresh Electron starts in manager mode; it must discover the employee
    # class before declining an exhausted phone budget.
    with pytest.raises(aio_web.HTTPForbidden):http(web._manager_device_enroll,entry)
    pc=http(web._agent_device_enroll,entry)
    state=info(entry)
    assert pc['employee']=='Synthetic Soft'
    assert state['uses']==state['max_uses']==db.APP_LINK_DEFAULT_USES+1
    assert state['bonus_desktop']==1
    assert db.get_binding(UID)=='Synthetic Soft'
    with db.connect() as conn:
        for token in phones:
            assert conn.execute('SELECT telegram_id FROM app_tokens WHERE token=?',(db.app_token_key(token),)).fetchone()[0]==UID
    with pytest.raises(aio_web.HTTPGone):http(web._agent_device_enroll,entry)


def test_pc_only_entry_is_visible_after_link_removal_and_revocation(entry):
    pc=http(web._agent_device_enroll,entry)
    row=next(r for r in db.app_links_state() if r['telegram_id']==UID)
    assert row['entered'] and row['devices']==1 and row['last_agent']=='desk'
    with db.connect() as conn:
        conn.execute('DELETE FROM app_links WHERE telegram_id=?',(UID,))
    row=next(r for r in db.app_links_state() if r['telegram_id']==UID)
    assert row['entered'] and row['devices']==1 and row['code'] is None
    with db.connect() as conn:
        conn.execute("UPDATE agent_devices SET revoked_at='2026-10-06' WHERE telegram_id=?",(UID,))
    assert not any(r['telegram_id']==UID for r in db.app_links_state())

def test_pc_first_does_not_lose_iphone_shortcut_bonus(entry):
    assert db.enroll_agent_device(entry,'Synthetic PC')
    assert db.redeem_app_link(entry,agent='iphone')
    assert db.redeem_app_link(entry,agent='iphone-pwa')
    assert db.redeem_app_link(entry,agent='android')
    state=info(entry)
    assert state['bonus_desktop']==state['bonus_ios']==1
    assert state['max_uses']==db.APP_LINK_DEFAULT_USES+2
    assert db.redeem_app_link(entry,agent='android') is None

def test_same_pc_reopening_link_costs_no_extra_activation(entry):
    spend_phone(entry)
    first=http(web._agent_device_enroll,entry)
    before=info(entry)
    again=http(web._agent_device_enroll,entry,keep_token=first['token'])
    assert again['token']==first['token'] and again['device_id']==first['device_id']
    assert info(entry)['uses']==before['uses']
    with db.connect() as conn:
        assert conn.execute('SELECT count(*) FROM agent_devices').fetchone()[0]==1

def test_missing_binding_does_not_spend_pc_quota(entry):
    with db.connect() as conn:conn.execute('UPDATE agent_bind SET approved=0 WHERE telegram_id=?',(UID,))
    assert db.enroll_agent_device(entry,'Synthetic PC') is None
    assert info(entry)['uses']==0 and info(entry)['bonus_desktop']==0

def test_expired_link_cannot_use_pc_bonus_or_keep_token(entry):
    pc=db.enroll_agent_device(entry,'Synthetic PC')
    with db.connect() as conn:
        conn.execute('UPDATE app_links SET created_at=? WHERE code=?',((datetime.now()-timedelta(days=db.APP_LINK_DAYS+1)).isoformat(),entry))
    assert db.enroll_agent_device(entry,'Synthetic PC',keep_token=pc[3]) is None
    with pytest.raises(aio_web.HTTPGone):http(web._agent_device_enroll,entry,keep_token=pc[3])

def test_revoked_pc_token_cannot_be_reused(entry):
    spend_phone(entry)
    pc=db.enroll_agent_device(entry,'Synthetic PC')
    db.agent_device_revoke(pc[2],by='synthetic-test')
    assert db.enroll_agent_device(entry,'Synthetic PC',keep_token=pc[3]) is None

def test_concurrent_pc_bonus_is_granted_only_once(entry):
    spend_phone(entry)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda name:db.enroll_agent_device(entry,name),['PC-A','PC-B']))
    assert sum(result is not None for result in results)==1
    state=info(entry)
    assert state['uses']==state['max_uses']==db.APP_LINK_DEFAULT_USES+1

def test_migration_preserves_link_and_phone(entry):
    phone=db.redeem_app_link(entry,agent='android')
    before=info(entry)
    with db.connect() as conn:conn.execute('ALTER TABLE app_links DROP COLUMN bonus_desktop')
    db.init_db()
    after=info(entry)
    assert after['code']==before['code'] and after['uses']==before['uses'] and after['max_uses']==before['max_uses']
    assert after['bonus_desktop']==0
    with db.connect() as conn:
        assert conn.execute('SELECT telegram_id FROM app_tokens WHERE token=?',(db.app_token_key(phone),)).fetchone()[0]==UID

def test_pending_calls_cannot_switch_to_another_identity_or_spend_its_link(entry):
    db.bind_agent(UID+1,'Another Synthetic Soft')
    other=db.create_app_link(UID+1)
    with pytest.raises(aio_web.HTTPConflict):
        http(web._agent_device_enroll,other,expected_uid=UID)
    assert info(other)['uses']==0 and info(other)['bonus_desktop']==0

def test_pending_calls_can_recover_same_identity_with_a_new_link(entry):
    old=http(web._agent_device_enroll,entry)
    db.agent_device_revoke(old['device_id'],by='synthetic-test')
    fresh=db.create_app_link(UID)
    recovered=http(web._agent_device_enroll,fresh,expected_uid=UID,keep_token=old['token'])
    assert recovered['telegram_id']==UID and recovered['token']!=old['token']

def test_legacy_pending_queue_requires_token_reuse_and_never_burns_other_link(entry):
    with pytest.raises(aio_web.HTTPConflict):
        http(web._agent_device_enroll,entry,reuse_only=True,keep_token='invalid-synthetic-token')
    assert info(entry)['uses']==0 and info(entry)['bonus_desktop']==0

def test_concurrent_ios_activation_adds_only_one_bonus(entry):
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(lambda _:db.redeem_app_link(entry,agent='iphone'),range(2)))
    assert all(result)
    assert info(entry)['max_uses']==db.APP_LINK_DEFAULT_USES+1
