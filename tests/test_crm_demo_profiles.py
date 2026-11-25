import db
from crm import pilot_server,store,cabinet,tasks,storage


def test_all_five_review_roles_share_clients_and_keep_separate_actions(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'demo.db');db.init_db();pilot_server.seed();pilot_server.seed()
    roles=pilot_server.principals()
    assert set(roles)=={'employee','manager','observer','courier','analyst'}
    assert all(store.portfolio(role)['total_contracts']==4 for role in roles.values())
    assert len(store.contract_list(roles['courier'],work='delivery')['contracts'])==1
    assert roles['courier'].capabilities.isdisjoint({'call','visit','accounting','exchange'})
    assert cabinet.dashboard(roles['employee'],pilot_server.now()[:7])['payments']['totals'][0]['amount']=='1250.25'
    with storage.connect() as conn:
        staff=tasks.staff(conn,roles['manager'])
    assert any(row['role']=='courier' and row['work_kinds']==['delivery'] for row in staff)
