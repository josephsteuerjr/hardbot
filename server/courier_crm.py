"""Existing PWA authentication/queue adapter for the isolated canonical CRM."""
import asyncio
import json
from pathlib import Path
import aiohttp
from aiohttp import web

UPSTREAM = 'http://crm-pilot-api:8080'
NAMESPACE = 'crm-shared-pilot-v1'


def install(app, host, *, transport=None):
    client = None

    @web.middleware
    async def legacy_guard(request,handler):
        business=('/api/save','/api/office/','/api/workspace/action','/api/note','/api/marker','/api/photo','/api/receipt')
        if request.method=='POST' and request.path.startswith(business) and (request.headers.get('X-App-Token') or not request.headers.get('X-Pult-Token')):
            uid,_=await asyncio.to_thread(host._auth,request)
            if await asyncio.to_thread(host.db.get_kind,uid)=='courier':
                raise host._json_error(web.HTTPForbidden,'courier_form_only')
        return await handler(request)
    app.middlewares.append(legacy_guard)

    async def lifecycle(app):
        nonlocal client
        if transport is not None:
            yield;return
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=8)) as session:
            client=session;yield
    app.cleanup_ctx.append(lifecycle)

    async def upstream(method, path, headers, body=None):
        if transport is not None:
            return await transport(method,path,headers,body)
        async with client.request(method,UPSTREAM+path,headers=headers,json=body,
                                  allow_redirects=False) as response:
            if response.headers.get('X-CRM-Namespace') != NAMESPACE:
                raise ValueError('Unconfirmed CRM namespace')
            raw=await response.content.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise ValueError('CRM response too large')
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError('Invalid CRM response')
            return response.status,value

    async def handler(request):
        try:
            token=request.headers.get('X-App-Token','').strip()
            if not token:raise host._json_error(web.HTTPUnauthorized,'crm_device_required')
            await asyncio.to_thread(host._auth,request)
            status,session=await upstream('POST','/auth/exchange',{'X-App-Token':token})
            if status!=200:return web.json_response(session,status=status)
            auth={'Authorization':'Bearer '+session['token']}
            status,context=await upstream('GET','/api/crm/context',auth)
            if status!=200:return web.json_response(context,status=status)
            if context.get('role')!='courier':
                return web.json_response(dict(error='courier_required',text='Для этого входа не назначена работа курьера.'),status=403)
            action=request.match_info['action']
            if action=='context' and request.method=='GET':
                status,result=await upstream('GET','/api/crm/contracts?work=delivery&limit=500',auth)
                if status==200:result={**result,'employee_uuid':context['employee_uuid'],'role':'courier','fields':context.get('fields',[])}
            elif action=='contracts' and request.method=='GET':
                from urllib.parse import urlencode
                search=request.query.get('search','')
                if len(search)>200:raise host._json_error(web.HTTPBadRequest,'courier_search_invalid')
                query={'search':search,'limit':100} if search else {'work':'delivery','limit':500}
                status,result=await upstream('GET','/api/crm/contracts?'+urlencode(query),auth)
            elif action=='command' and request.method=='POST':
                if request.content_length is not None and request.content_length>65536:
                    raise web.HTTPRequestEntityTooLarge(max_size=65536,actual_size=request.content_length)
                data=await request.content.read(65537)
                if len(data)>65536:
                    raise web.HTTPRequestEntityTooLarge(max_size=65536,actual_size=len(data))
                try:raw=json.loads(data)
                except (ValueError,UnicodeError):raise host._json_error(web.HTTPBadRequest,'courier_command_invalid')
                if not isinstance(raw,dict) or raw.get('kind')!='delivery':
                    raise host._json_error(web.HTTPBadRequest,'courier_action_required')
                status,result=await upstream('POST','/api/crm/command',auth,raw)
            else:raise web.HTTPNotFound()
            return web.json_response(result,status=status,headers={'Cache-Control':'no-store'})
        except (aiohttp.ClientError,asyncio.TimeoutError):
            return web.json_response(dict(error='crm_unavailable',text='Нет связи с CRM. Результат остаётся в очереди.'),status=503)
        except (ValueError,KeyError,TypeError):
            return web.json_response(dict(error='crm_invalid',text='Не удалось подтвердить данные CRM. Результат сохранён на устройстве.'),status=502)

    app.router.add_get('/api/courier/{action}',handler)
    app.router.add_post('/api/courier/{action}',handler)
    assets=Path(__file__).parent/'miniapp/courier'
    if assets.is_dir():app.router.add_static('/crm-courier/assets/',assets,show_index=False)
