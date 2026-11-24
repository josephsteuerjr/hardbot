import asyncio
import base64
import uuid
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from crm import http
from test_crm_core import crm_world


def test_mobile_visit_with_scoped_photo(crm_world):
    async def run():
        w = crm_world
        actor = w['member']
        async def authenticate(request):
            return actor
        app = web.Application()
        http.install(app, authenticate=authenticate)
        cid, other = w['contracts']
        doc = str(uuid.uuid4())
        content = base64.b64encode(b'synthetic-image').decode()
        blob = dict(operation_id=doc, kind='attachment', document_id=doc, contract_uuid=cid,
                    filename='visit.png', media_type='image/png', content_base64=content)
        visit = dict(schema_version=1, kind='visit', operation_id=str(uuid.uuid4()), contract_ids=[cid],
                     payload=dict(values=dict(visit_comment='Visit'), field_visit=dict(phone='+100',
                                  point=dict(lat=4, lon=5), photos=[doc])))
        async with TestClient(TestServer(app)) as client:
            url='/api/crm/'
            assert (await client.post(url+'document', json=blob)).status == 200
            assert (await client.post(url+'command', json={**visit,'contract_ids':[other]})).status == 409
            bad = dict(visit['payload'], field_visit=dict(point=dict(lat=90.1,lon=0)))
            assert (await client.post(url+'command', json={**visit,'payload':bad})).status == 400
            assert (await client.post(url+'command', json=visit)).status == 200
            assert (await client.post(url+'command', json=visit)).status == 200
            history=(await (await client.get(url+'card',params=dict(person_uuid=w['person']))).json())['history']
            matching=[r for r in history if r['kind']=='visit']
            assert len(matching)==1 and matching[0]['payload']['field_visit']==visit['payload']['field_visit']
            result=await (await client.get(url+'document-read',params=dict(document_id=doc))).json()
            assert result['content_base64']==content
            actor=w['stranger']
            assert (await client.get(url+'document-read',params=dict(document_id=doc))).status in (403,404)
            assert (await client.post(url+'command',json={**visit,'operation_id':str(uuid.uuid4())})).status==403
    asyncio.run(run())
