import asyncio
import json
import pytest
from aiohttp import web as http
from aiohttp.test_utils import make_mocked_request
import db
import web
from test_soft_manager_stats import world


@pytest.mark.parametrize('value,expected', [(7,7), (13,13), ('25',25), (100,100), (0,0), ('',0), (None,0)])
def test_arbitrary_integer_norms(value, expected):
    assert web._visit_plan_updates({'S1':value}) == {'S1':expected}


@pytest.mark.parametrize('value', [-1,101,7.5,True,'abc','1e1','NaN'])
def test_bad_norm_is_rejected(value):
    with pytest.raises(http.HTTPBadRequest):
        web._visit_plan_updates({'S1':value})


def test_admin_numeric_norm_persists_and_bad_batch_is_not_partly_applied(world, monkeypatch):
    monkeypatch.setattr(web, '_admin', lambda request:None)
    class Request:
        async def json(self):return self.body
    req=Request();req.body={'plan':{'S1':13,'H1':7}}
    out=json.loads(asyncio.run(web._admin_visit_plan(req)).text)
    assert out['plan']=={'S1':13,'H1':7}
    assert db.visit_plan_for('S1')==13
    req.body={'plan':{'S1':25,'H1':1.5}}
    with pytest.raises(http.HTTPBadRequest):
        asyncio.run(web._admin_visit_plan(req))
    assert db.visit_plan_map()=={'S1':13,'H1':7}
    req.body={'plan':{'S1':0}}
    asyncio.run(web._admin_visit_plan(req))
    assert 'S1' not in db.visit_plan_map()
