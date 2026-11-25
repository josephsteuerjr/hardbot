import copy
import pytest
import call_scripts as scripts
import db
import staff_roster
from test_manager_account_approval import world, call, MGR
from test_manager_password_reset import activated

CONTENT={'title':'Synthetic guide','steps':[{'title':'Opening','text':'Synthetic text <b>plain</b>'}]}


@pytest.fixture
def team(world):
    db.set_manager(MGR+1,'Other Leader',[],['Foreign Employee'])
    data=[dict(employee=name,products=['soft'],manager_uid=uid) for name,uid in
          [('Own Employee',MGR),('Foreign Employee',MGR+1)]]
    preview=staff_roster.preview(data)
    assert staff_roster.apply(data,preview['sha256'],by='synthetic')['ok']
    return world


def body(action='save',revision=0,op='synthetic-save-001',content=CONTENT):
    return dict(action=action,base_revision=revision,operation_id=op,**({'content':copy.deepcopy(content)} if action=='save' else {}))


def published(uid=MGR):
    assert scripts.mutate(uid,body())['revision']==1
    assert scripts.mutate(uid,body('publish',1,'synthetic-publish-001'))['published_revision']==2


def test_draft_hidden_publish_separate_and_withdraw(team):
    assert scripts.employee_script('Own Employee')['script'] is None
    scripts.mutate(MGR,body())
    assert scripts.employee_script('Own Employee')['script'] is None
    scripts.mutate(MGR,body('publish',1,'synthetic-publish-001'))
    assert scripts.employee_script('Own Employee')['script']==CONTENT
    changed={**CONTENT,'title':'Changed draft'}
    scripts.mutate(MGR,body(revision=2,op='synthetic-change-001',content=changed))
    assert scripts.employee_script('Own Employee')['script']==CONTENT
    assert scripts.manager_state(MGR)['draft']==changed
    scripts.mutate(MGR,body('withdraw',3,'synthetic-withdraw-001'))
    assert scripts.employee_script('Own Employee')['script'] is None
    assert scripts.manager_state(MGR)['draft']==changed


def test_operation_replay_and_stale_edit_protect_existing_content(team):
    request=body()
    assert scripts.mutate(MGR,request)['revision']==1
    assert scripts.mutate(MGR,request)['replayed']
    assert scripts.manager_state(MGR)['revision']==1
    with pytest.raises(scripts.ScriptError) as e:scripts.mutate(MGR,body(op='synthetic-other-001'))
    assert e.value.status==409
    changed=body(content={**CONTENT,'title':'Other payload'})
    with pytest.raises(scripts.ScriptError) as e:scripts.mutate(MGR,changed)
    assert e.value.status==409
    assert scripts.manager_state(MGR)['draft']==CONTENT


def test_foreign_team_and_role_demotion_rechecked(team):
    published()
    assert scripts.employee_script('Foreign Employee')['script'] is None
    assert scripts.manager_state(MGR+1)['draft'] is None
    db.set_manager(MGR,'Synthetic Leader',[],[],role='security')
    assert scripts.employee_script('Own Employee')['script'] is None
    with pytest.raises(scripts.ScriptError) as e:scripts.mutate(MGR,body('withdraw',2,'synthetic-withdraw-001'))
    assert e.value.status==403


def test_roster_reassignment_and_deactivation_revoke_previous_guide(team):
    published()
    with db.connect() as c:c.execute('UPDATE staff_roster SET manager_uid=? WHERE manager_uid=?',(MGR+1,MGR))
    assert scripts.employee_script('Own Employee')['script'] is None
    with pytest.raises(scripts.ScriptError):scripts.mutate(MGR,body('publish',2,'synthetic-republish-001'))
    with db.connect() as c:c.execute('UPDATE staff_roster SET active=0')
    assert scripts.employee_script('Own Employee')['script'] is None


@pytest.mark.parametrize('content',[{}, {'title':'','steps':[]},{'title':'X','steps':[{'title':'Step','text':''}]},{'title':'X'*101,'steps':CONTENT['steps']},{'title':'X','steps':[{'title':'Step','text':'X'*6001}]},{'title':'X','steps':CONTENT['steps']*13}])
def test_invalid_content_rejected_without_writes(team,content):
    with pytest.raises(scripts.ScriptError):scripts.mutate(MGR,body(content=content))
    assert scripts.manager_state(MGR)['revision']==0


def test_http_identity_scope_and_employee_query_cannot_choose_foreign_guide(team):
    token,_=activated(team)
    headers={'X-App-Token':token}
    assert call('GET','/api/manager/call-script',headers)[0]>=400
    assert call('GET','/api/call-script')[0]==401
    assert call('POST','/api/manager/call-script',team,body())[0]==200
    assert call('GET','/api/call-script?employee=Foreign%20Employee',headers)[1]['script'] is None
    assert call('POST','/api/manager/call-script',team,body('publish',1,'synthetic-publish-001'))[0]==200
    result=call('GET','/api/call-script?employee=Foreign%20Employee&manager_uid=9999',headers)
    assert result[0]==200 and result[1]['script']==CONTENT
    assert call('POST','/api/manager/call-script',team,{**body(revision=2,op='synthetic-forged-001'),'manager_uid':MGR+1})[0]==400
