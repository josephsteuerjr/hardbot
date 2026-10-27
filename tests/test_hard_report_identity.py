"""Explicit Hard author names share reports, never inferred access or facts."""
import json
from urllib.parse import quote

import pytest

import control
import db
import digest_data as dd
import staff_roster
import stats_book
import web
import workday
from test_pc_first_accounts import world, MANAGER, http

NAME='Own Employee'
HARD='Own Employee HARD'
SOFT='Own Employee SOFT'


def setup(world):
    items=[{'employee':NAME,'office':'Synthetic office','products':['hard','soft'],
            'product_names':{'hard':HARD,'soft':SOFT},'manager_uid':MANAGER}]
    preview=staff_roster.preview(items)
    assert staff_roster.apply(items,preview['sha256'],by='synthetic')['ok']
    day=workday.today().isoformat()
    with db.connect() as c:
        for cid,client,author in [('HA','Synthetic Alpha',HARD),('HB','Synthetic Beta',HARD),
                                  ('HF','Synthetic Foreign','Foreign Employee'),
                                  ('HS','Synthetic Soft Author',SOFT),
                                  ('HG','Synthetic Guessed Suffix',NAME+' OTHER')]:
            c.execute('INSERT INTO clients(contract_id,client,responsible,country,geo_lat,geo_lon) VALUES(?,?,?,?,?,?)',
                      (cid,client,author,'Узбекистан',41.0,69.0))
    for cid,author in [('HA',NAME),('HA',HARD),('HB',HARD),('HF','Foreign Employee'),('HS',SOFT),('HG',NAME+' OTHER')]:
        db.log_visit(cid,990001,'Выезд (миниапп). Статус: Нет контакта; Договорённость: ;',on_date=day,employee=author)
        db.record_bot_visit(cid,{'call_status':'Нет контакта'},day)
    mgr=db.get_manager(MANAGER)
    return day,mgr,dd.stats_range(None,0,day,day),[NAME,HARD,SOFT]


@pytest.mark.parametrize('employee',[NAME,HARD])
def test_two_hard_authors_share_history_and_deduplicate_person_day(world,employee):
    day,mgr,rng,team=setup(world)
    with db.connect() as c:before=[tuple(r) for r in c.execute('SELECT id,employee,agent_id FROM visits ORDER BY id')]
    assert len(db.visits_for_employee(employee,day,day))==3
    report=dd._employee_stats(employee,day,day,with_photos=False)
    assert report['persons']==report['visits']==2
    assert {e['person']['rep'] for e in report['_entries']}=={'HA','HB'}
    assert {p['rep'] for p in db.persons_processed(employee,day)}=={'HA','HB'}
    assert set(db.first_visit_days(employee))=={'HA','HB'}
    with db.connect() as c:assert [tuple(r) for r in c.execute('SELECT id,employee,agent_id FROM visits ORDER BY id')]==before


def test_team_stats_daily_export_and_activity_count_one_person_once(world):
    day,mgr,rng,team=setup(world)
    data=dd.stats_data(mgr,rng,team)
    assert data['totals']['visits']==3  # two Hard clients, one independently named author
    own=[r for r in data['employees'] if r['employee'] in (NAME,HARD)]
    assert len(own)==1 and own[0]['employee']==NAME and own[0]['persons']==2
    report=stats_book.period_report_data(mgr,rng,team)
    own=[r for r in report['rows'] if r['employee'] in (NAME,HARD)]
    assert len(own)==1 and own[0]['persons']==2 and own[0]['assigned_contracts']==2
    activity=control.activity(mgr,team,rng,kinds={'visit'})
    own=[e for e in activity['events'] if e['employee'] in (NAME,HARD)]
    assert len(own)==2
    daily=dd.daily_report_data(mgr,day,team)
    assert len([r for r in daily['rows'] if r['employee'] in (NAME,HARD)])==1
    assert stats_book.stats_xlsx(mgr,rng,team).startswith(b'PK')


def test_current_assignments_do_not_reassign_historical_work(world):
    day,mgr,rng,team=setup(world)
    with db.connect() as c:c.execute("UPDATE clients SET responsible='Foreign Employee' WHERE contract_id='HB'")
    assert dd._employee_stats(NAME,day,day,with_photos=False)['persons']==2
    assert db.employee_visited(NAME,['HB']) and db.employee_visited(HARD,['HB'])
    assert not db.employee_visited(NAME,['HF'])
    assert control.visit_in_scope([NAME],db.visits_for_employee(HARD,day,day)[-1]['id'])
    assert not control.visit_in_scope(['Foreign Employee'],db.visits_for_employee(HARD,day,day)[-1]['id'])


@pytest.mark.parametrize('kind',['inactive','conflicting','unknown'])
def test_unproven_identity_does_not_join_histories(world,kind):
    day,mgr,rng,team=setup(world)
    with db.connect() as c:
        if kind=='inactive':c.execute('UPDATE staff_roster SET active=0')
        elif kind=='conflicting':
            c.execute("UPDATE staff_roster SET product_names=? WHERE employee_id=(SELECT employee_id FROM employee_aliases WHERE name='Foreign Employee')",(json.dumps({'hard':HARD}),))
    employee=NAME if kind!='unknown' else NAME+' Unconfigured'
    assert staff_roster.product_author_names(employee)==[employee]
    assert {r['contract_id'] for r in db.visits_for_employee(employee,day,day)}==({'HA'} if kind!='unknown' else set())


def test_hard_identity_does_not_include_independent_soft_name(world):
    day,mgr,rng,team=setup(world)
    assert staff_roster.product_author_names(NAME)==[NAME,HARD]
    assert staff_roster.product_author_names(SOFT)==[SOFT]
    assert {r['contract_id'] for r in db.visits_for_employee(NAME,day,day)}=={'HA','HB'}
    assert [r['contract_id'] for r in db.contracts_of_responsible(NAME)]==[]
    assert {r['contract_id'] for r in db.hard_report_contracts(NAME)}=={'HA','HB'}


def test_real_http_stats_activity_and_card_match_without_foreign_access(world,monkeypatch):
    day,mgr,rng,team=setup(world)
    monkeypatch.setattr(web,'_manager_gate',lambda request:(MANAGER,mgr,team))
    suffix='from='+day+'&to='+day+'&employee='+quote(NAME)
    status,data=http('GET','/api/manager/stats_employee?'+suffix+'&mode=hard')
    assert status==200 and data['persons']==data['visits']==2
    status,data=http('GET','/api/manager/activity?'+suffix+'&kinds=visit')
    assert status==200 and len(data['events'])==2
    for cid in ['HA','HB']:
        assert http('GET','/api/manager/person?date='+day+'&employee='+quote(NAME)+'&rep='+cid)[0]==200
    assert http('GET','/api/manager/person?date='+day+'&employee='+quote(NAME)+'&rep=HF')[0]==404
    assert http('GET','/api/manager/stats_employee?'+suffix.replace(quote(NAME),'Foreign%20Employee'))[0]==404


def test_legacy_empty_author_fallback_still_excludes_a_known_foreign_author(world):
    day,mgr,rng,team=setup(world)
    with db.connect() as c:
        c.execute("INSERT INTO visits(contract_id,created_at,employee) VALUES('HB',?,'')",(day,))
        c.execute("UPDATE visits SET employee='Foreign Employee' WHERE contract_id='HB' AND employee<>''")
    assert len(db.visits_for_employee(NAME,day,day))==3
    assert {p['rep'] for p in db.persons_processed(NAME,day)}=={'HA','HB'}
    with db.connect() as c:c.execute("DELETE FROM visits WHERE employee='' AND contract_id='HB'")
    assert {p['rep'] for p in db.persons_processed(NAME,day)}=={'HA'}


def test_repeated_client_on_another_day_remains_a_separate_visit(world):
    from datetime import date,timedelta
    day,mgr,rng,team=setup(world)
    previous=(date.fromisoformat(day)-timedelta(days=1)).isoformat()
    db.log_visit('HA',990001,'Synthetic earlier visit',on_date=previous,employee=NAME)
    report=dd._employee_stats(NAME,previous,day,with_photos=False)
    assert report['persons']==2 and report['visits']==3


def test_map_and_own_actor_links_include_both_explicit_hard_names(world):
    day,mgr,rng,team=setup(world)
    with db.connect() as c:
        c.execute('INSERT INTO agent_bind(telegram_id,employee,approved) VALUES(990001,?,1)',(NAME,))
        c.execute('UPDATE visits SET visit_lat=41.0,visit_lon=69.0')
    assert db.agent_tids(HARD)==[990001]
    points=db.day_map_points(NAME,day)
    assert len(points['visits'])==3 and not any(p['by_other'] for p in points['visits'])
