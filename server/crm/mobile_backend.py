"""Server-side transport for the mobile shell; no working token in JavaScript."""
import asyncio, contextlib, hashlib, json, os, re, secrets, sys
from pathlib import Path
import aiohttp
from .contract import CRMError
from . import scoped_admin

GET_ACTIONS=frozenset('context operator-status portfolio work-portfolio tasks card contracts registry managed-portfolio assignment-staff assignment-receipt assignment-history assignment-job manager-statistics manager-report manager-report-download analyst-report analyst-statistics changes active-time shifts shift-report cabinet three-hour script adoption document-read logistics-export-download grant-catalog logistics logistics-export'.split())
POST_ACTIONS=frozenset('registry-edit assignment-preview assignment-commit assignment-job-step assignment-job-cancel active-time anchor command shift plan cohort export import-preview import-apply receipt telemetry script document logistics-call grant-admin logistics-preview logistics-apply logistics-update'.split())
ADMIN_ROUTES=frozenset(['/state','/busy','/fn','/job','/jobs','/admin','/admin_image','/mail/state','/mail/set','/mail/send'])
ROLES=frozenset(['employee','courier','manager','analyst','observer'])

async def start_runtime(home,args,env,input=None,prefix='HARD-CRM'):
    home=Path(home);home.mkdir(parents=True,exist_ok=True)
    with (home/'runtime-startup.private.log').open('ab') as errors:
        process=await asyncio.create_subprocess_exec(sys.executable,'-X','utf8','-m','pult.desk_backend',*args,
            cwd=Path(__file__).resolve().parents[2],env=env,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=errors,creationflags=0x08000000 if os.name=='nt' else 0)
    if input:process.stdin.write((json.dumps(input)+'\n').encode());await process.stdin.drain()
    process.stdin.close()
    try:
        for _ in range(20):
            line=await asyncio.wait_for(process.stdout.readline(),60)
            match=re.search(re.escape(prefix)+r' (\d+) (\S+)',line.decode(errors='replace'))
            if match:return process,'http://127.0.0.1:'+match[1],match[2]
            if not line:break
        raise RuntimeError('Mobile runtime failed to start; inspect the private startup log')
    except BaseException:
        if process.returncode is None:process.terminate();await process.wait()
        raise

def validate_admin(path,body):
    if path not in ADMIN_ROUTES and not re.fullmatch(r'/(?:imp|enr|reg|exp)/[a-z_-]+',path):raise CRMError('Действие не разрешено.','forbidden',403)
    if path=='/admin' and (body.get('method') not in {'GET','POST'} or not re.match(r'^/(?:admin|ops)/[a-zA-Z0-9_./?-]',str(body.get('path',''))) or '..' in body.get('path','') or body.get('key')):raise CRMError('Недопустимый административный запрос.','forbidden',403)


def selected_grant(session):
    context=session.get('context') or {}
    grant_id=context.get('grant_id',session.get('grant_id'))
    if context.get('access_model',session.get('access_model',1))!=2 and grant_id is None:return None
    grants=context.get('grants',session.get('grants'))
    selected=next((grant for grant in grants or () if grant.get('grant_id')==grant_id),None)
    if selected is None or str(selected.get('actor_uid'))!=str(context.get('actor_uid')):
        raise CRMError('Выбранное полномочие недоступно.','grant_revoked',403)
    return selected


def server_role(session):
    grant=selected_grant(session)
    return grant['role'] if grant else (session.get('context') or {}).get('role',session.get('role'))


def server_capabilities(session):
    context=session.get('context') or {}
    grant=selected_grant(session)
    if not grant and context.get('access_model',session.get('access_model',1))!=2 and 'capabilities' not in context and 'capabilities' not in session:
        return None
    return frozenset(grant.get('capabilities',()) if grant else context.get('capabilities',session.get('capabilities',())))


def workspace_has(session,*modules):
    context=session.get('context') or {}
    tree=(context.get('workspace') or {}).get('tree')
    if tree is None:return context.get('access_model',session.get('access_model',1))!=2
    found=set()
    def visit(nodes):
        for node in nodes:
            found.add(node.get('id'));visit(node.get('children') or ())
    visit(tree)
    return any(module in found for module in modules)


def require_grant_workspace(session,modules,roles,capability=None):
    modules=(modules,) if isinstance(modules,str) else tuple(modules)
    if not workspace_has(session,*modules) or server_role(session) not in roles:
        raise CRMError('Раздел недоступен для выбранного полномочия.','forbidden',403)
    capabilities=server_capabilities(session)
    if capability and capabilities is not None and capability not in capabilities:
        raise CRMError('Действие недоступно для выбранного полномочия.','forbidden',403)


def require_grant_role(session,roles):
    selected_grant(session)
    if server_role(session) not in roles:
        raise CRMError('Действие недоступно для выбранного полномочия.','forbidden',403)


def require_file_action(session,action,body):
    if action in {'workbook-read','workbook-write','workbook-resume'}:
        require_grant_workspace(session,'exchange',{'manager','analyst','owner'},'exchange')
    elif body.get('kind')=='shifts':
        require_grant_role(session,{'employee','courier','manager','analyst','observer','owner'})
    else:
        require_grant_workspace(session,('manager-statistics','statistics'),{'manager','analyst','owner'})


class SyntheticBackend:
    synthetic=True
    def __init__(self,home):
        self.home=Path(home).resolve();self.children=[];self.admin_children={};self.admin_locks={};self.client=None
        if self.home.name!='synthetic' or self.home==Path.home():raise RuntimeError('Explicit isolated synthetic home required')
    async def child(self,args,env,input=None,prefix='HARD-CRM'):
        process,url,secret=await start_runtime(self.home,args,env,input,prefix)
        self.children.append(process)
        return url,secret
    async def start(self):
        self.home.mkdir(parents=True,exist_ok=True)
        (self.home/'manual-dates.private.json').write_text('{"enabled":false}',encoding='utf-8')
        env=dict(os.environ,HARD_CRM_DEMO_HOME=str(self.home),APP_DATA=str(self.home),HARD_CRM_SHARED='0',HARD_CRM_DATABASE_URL='',HARD_CRM_DATABASE_URL_FILE='',HARD_CRM_DOCUMENTS='',HARD_MANUAL_DATES_CONFIG=str(self.home/'manual-dates.private.json'),HARD_PULT_SETTINGS=str(self.home/'unused-settings.json'),PYTHONIOENCODING='utf-8')
        self.url,self.secret=await self.child(['--crm-pilot'],env)
        self.native_url,self.native_secret=await self.child(['--crm-pilot'],env)
        self.native_pid=self.children[-1].pid;self.native_lock=asyncio.Lock()
        self.client=aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=120))
    async def close(self):
        if self.client:await self.client.close()
        for child in reversed(self.children):
            if child.returncode is None:
                child.terminate()
                try:await asyncio.wait_for(child.wait(),8)
                except asyncio.TimeoutError:child.kill();await child.wait()
    async def rpc(self,role,action,body=None,query=None,method='POST'):
        async with self.client.post(self.url+'/crm/'+action,headers={'X-Local-Secret':self.secret,'X-Demo-Role':role},json=dict(method=method,body=body or {},query=query or {})) as response:
            data=await response.json()
            if not response.ok:raise CRMError(data.get('text','Действие отклонено.'),data.get('error','request'),response.status)
            return data
    async def login(self,body):
        role=body.get('role')
        if body.get('action')!='synthetic' or role not in ROLES:raise CRMError('Выберите проверочное рабочее место.','login',403)
        context=await self.rpc(role,'context',method='GET')
        return dict(role=role,context=context,synthetic=True)
    async def request(self,session,method,action,body=None,query=None):
        if action=='manager-access':return await self.rpc(session['role'],action,body,query,method)
        if action not in (GET_ACTIONS if method=='GET' else POST_ACTIONS):raise CRMError('Неизвестный запрос.','invalid',400)
        return await self.rpc(session['role'],action,body,query,method)
    async def file_action(self,session,action,body):
        # Native Excel must never hold the canonical demo HTTP server's thread.
        async with self.native_lock:
            endpoint='logistics-workbook-read' if action=='logistics-read' else action
            async with self.client.post(self.native_url+'/crm/'+endpoint,headers={'X-Local-Secret':self.native_secret,'X-Demo-Role':session['role']},json=dict(method='POST',body=body,query={})) as response:
                value=await response.json()
                if not response.ok:raise CRMError(value.get('text') or 'Файл не подготовлен.',value.get('error','file'),response.status)
                return value
    async def admin(self,session,path,body):
        require_grant_workspace(session,'exchange',{'analyst','owner'},'exchange')
        scoped_admin.require_safe_admin(session,path,body)
        validate_admin(path,body)
        if path=='/admin' and str(body.get('path','')).startswith('/ops/') and body.get('method')=='POST':raise CRMError('Изменения служб отключены в проверочной среде.','synthetic_boundary',403)
        async with self.admin_locks.setdefault(session['owner'],asyncio.Lock()):
            if session['owner'] not in self.admin_children:
                endpoint=await self.rpc('analyst','analyst-admin-init')
                folder=self.home/'mobile-admin'/session['owner'].split(':')[-1];folder.mkdir(parents=True,exist_ok=True)
                env=dict(os.environ,APP_DATA=str(self.home),HARD_MANUAL_DATES_CONFIG=str(self.home/'manual-dates.private.json'),PYTHONIOENCODING='utf-8',HARD_PULT_SETTINGS=str(folder/'settings.json'),HARD_PULT_PROTECT_TOKENS='1',HARD_CLIENT_VERSION='3.3.0',HARD_CLIENT_PRODUCT='analyst')
                self.admin_children[session['owner']]=await self.child(['--crm-analyst'],env,input=dict(actor=91005,url=endpoint['url'],token=endpoint['token']),prefix='HARD-BACKEND')
            url,secret=self.admin_children[session['owner']]
        async with self.client.post(url+path,headers={'X-Local-Secret':secret},json=body) as response:
            value=await response.json()
            if path=='/state' and isinstance(value.get('data'),dict):value['data'].pop('settings_path',None)
            return scoped_admin.filter_safe_admin_result(session,path,body,value)

class SharedBackend:
    """Pinned CRM/primary access. Excel/legacy work runs on a configured executor."""
    synthetic=False
    def __init__(self,primary,crm,executor=None):
        from urllib.parse import urlsplit
        for url in (primary,crm):
            parsed=urlsplit(url)
            if parsed.scheme!='https' or parsed.hostname!='hard.rimborso.ru' or parsed.username or parsed.password or parsed.fragment or parsed.query:raise RuntimeError('Pinned first-party HTTPS services required')
        self.primary=primary.rstrip('/');self.crm=crm.rstrip('/');self.executor=executor
        if executor is not None and getattr(executor,'crm',None) is None:executor.crm=self.crm
    async def start(self):
        self.client=aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=90))
        if self.executor:await self.executor.start()
    async def close(self):
        if self.executor:await self.executor.close()
        await self.client.close()
    async def json(self,url,method='GET',headers=None,body=None):
        mobile_headers={'X-Client-Version':'3.3.0-mobile.1','X-Work-Product':'crm','X-Client-Runtime':'crm-mobile',
                        **(headers or {})}
        async with self.client.request(method,url,headers=mobile_headers,json=body if method=='POST' else None,allow_redirects=False) as response:
            try:value=await response.json()
            except (aiohttp.ContentTypeError,ValueError):
                raise CRMError('Служба входа отклонила запрос.' if response.status in {401,403,410} else 'Служба вернула неполный ответ. Исходная операция сохранена.','authority_denied' if response.status in {401,403,410} else 'upstream_protocol',response.status if response.status>=400 else 502) from None
            if not response.ok:raise CRMError(value.get('text') or value.get('error') or 'Служба недоступна.',value.get('error','request'),response.status,value.get('details'))
            return value
    def headers(self,session):return {session['credential_header']:session['credential'],
                                      'X-CRM-Entry':'analyst' if session.get('entry_role',session['role'])=='analyst' else 'manager'}
    async def registration(self,body=None):
        if body is None:return await self.json(self.primary+'/api/account/crm-registration')
        payload={key:body.get(key) for key in ('role','employee','username','password','country','city','code')}
        payload.update(audience='crm-mobile',device=dict(kind='phone',fp=body.get('device_id'),
                                                        label=body.get('device_label','Мобильная CRM')))
        return await self.json(self.primary+'/api/account/crm-registration','POST',body=payload)
    async def login(self,body):
        value=await self.json(self.primary+'/api/account/login','POST',body=dict(username=body.get('username'),password=body.get('password'),device=dict(kind='phone',fp=body.get('device_id'),label=body.get('device_label','Мобильная CRM')),entrance='crm-mobile',audience='crm-mobile',gps=body.get('gps')))
        if value.get('action') not in {'ok','ok_pending'}:raise CRMError(value.get('error') or 'Вход не выполнен.','login',403)
        role='employee' if value['role']=='agent' else 'observer' if value['role']=='security' else 'manager' if value['role']=='full' else value['role']
        session=dict(role=role,credential_header='X-App-Token' if role=='employee' else 'X-Pult-Token',credential=value['token'],synthetic=False,username=value['username'])
        if body.get('grant_id') is not None:session['grant_id']=body['grant_id']
        if value.get('action')!='ok' or value.get('must_change_password'):
            session['pending']=dict(status='password' if value.get('must_change_password') else 'device',username=value['username'],employee=value['employee'])
            if not value.get('must_change_password'):
                try:return await self.activate(session)
                except CRMError as exc:
                    if exc.status not in {401,403,410}:raise
            return session
        return await self.activate(session)
    def context(self,session,value):
        for key in ('grant_id','grants','access_model','policy_revision'):
            if key in session:value.setdefault(key,session[key])
        if session.get('entry_role'):value.setdefault('entry_role',session['entry_role'])
        return value
    async def exchange(self,session):
        headers=self.headers(session)
        if session.get('grant_id') is not None:headers['X-CRM-Grant']=session['grant_id']
        exchanged=await self.json(self.crm+'/auth/exchange','POST',headers=headers)
        session['crm_session']=exchanged['token']
        for key in ('grant_id','grants','access_model','policy_revision'):
            if key in exchanged:session[key]=exchanged[key]
            else:session.pop(key,None)
        return exchanged
    async def activate(self,session):
        identity=await self.json(self.primary+'/api/crm-identity',headers=self.headers(session))
        session['entry_role']=identity['me']['role']
        await self.exchange(session)
        session['context']=await self.request(session,'GET','context')
        session['role']=session['context']['role']
        session.pop('pending',None)
        return session
    async def select_grant(self,session,grant_id):
        if not isinstance(grant_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',grant_id):
            raise CRMError('Некорректное полномочие.','invalid',400)
        candidate=dict(session,grant_id=grant_id)
        await self.activate(candidate)
        for key in ('entry_role','crm_session','grant_id','grants','access_model','policy_revision','context','role'):
            if key in candidate:session[key]=candidate[key]
            else:session.pop(key,None)
        session.pop('pending',None)
        return session['context']
    async def resume(self, credential, entry_role):
        if not isinstance(credential, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,512}', credential):
            raise CRMError('Сохранённый вход недоступен.', 'session', 401)
        if entry_role not in {'agent', 'manager', 'full', 'security', 'analyst'}:
            raise CRMError('Неизвестный тип сохранённого входа.', 'session', 401)
        role = 'employee' if entry_role == 'agent' else 'analyst' if entry_role == 'analyst' else 'manager'
        session = dict(role=role, credential_header='X-App-Token' if role=='employee' else 'X-Pult-Token',
                       credential=credential, synthetic=False)
        identity = await self.json(self.primary+'/api/crm-mobile-session', headers=self.headers(session))
        session['username'] = identity['username']
        return await self.activate(session)
    async def password(self,session,body):
        if not session.get('username'):raise CRMError('Сначала введите логин и пароль.','session',401)
        result=await self.json(self.primary+'/api/account/password','POST',body=dict(username=session['username'],current=body.get('current'),new=body.get('new')))
        if not result.get('ok'):raise CRMError(result.get('error') or 'Пароль не изменён.','password',403)
        if session.get('pending'):session['pending']['status']='device'
        return result
    async def logout(self,session):
        if not session.get('credential'):return
        try:await self.json(self.primary+'/api/account/logout','POST',headers=self.headers(session),body={})
        except CRMError as exc:
            if exc.status not in {401,403,410}:raise
    async def request(self,session,method,action,body=None,query=None,*,recover_legacy_receipt=False):
        from urllib.parse import urlencode
        if action in {'field-route','field-geocode','field-directions','field-marks','field-track'}:
            return await self.field_workspace(session,method,action,body or {},query or {})
        if action=='manager-access':
            routes={'team':'/api/manager/team','approve':'/api/manager/account_approve','reset':'/api/manager/account_reset','revoke':'/api/manager/team/device-revoke','board':'/api/manager/employee_board','plan':'/api/manager/plan'}
            resource=(body if method=='POST' else query or {}).get('resource')
            if resource not in routes:raise CRMError('Неизвестное действие Команды.')
            require_grant_workspace(session,'team',{'manager','analyst','owner'})
            scope=scoped_admin.scoped(session)
            payload=(body or {}).get('payload') or {}
            if resource=='plan':
                require_grant_workspace(session,'team',{'manager','owner'},'assign')
                if scope is not None:
                    if method=='POST':
                        for name in (payload.get('plan') or {}):scope.require_employee(name)
                        return await self.json(self.primary+routes[resource],method,self.headers(session),payload)
                    return scope.filter_plan(await self.json(self.primary+routes[resource],method,self.headers(session)))
            if scope is not None:
                if resource=='team':
                    return scope.filter_team(await self.json(self.primary+routes[resource],method,self.headers(session)))
                if resource=='board':scope.require_employee(payload.get('employee'))
                elif resource=='reset':
                    team=await self.json(self.primary+'/api/manager/team','GET',self.headers(session))
                    scope.require_username(scope.filter_team(team),payload.get('username'))
                elif resource in {'revoke','approve'}:
                    team=scope.filter_team(await self.json(self.primary+'/api/manager/team','GET',self.headers(session)))
                    if payload.get('device_id') is not None:scope.require_device(team,payload.get('device_id'))
                    else:scope.require_username(team,payload.get('username'))
            return await self.json(self.primary+routes[resource],method,self.headers(session),payload if method=='POST' else None)
        if action not in (GET_ACTIONS if method=='GET' else POST_ACTIONS):raise CRMError('Неизвестный запрос.')
        from .shared_auth import BOUND_ACTIONS
        context = session.get('context') or {}
        headers = {}
        if method == 'POST' and action in BOUND_ACTIONS and context.get('bound_commands'):
            headers['X-CRM-Work-Authority'] = f"{context['actor_uid']}/{context['access_model']}/{context.get('grant_id') or ''}"
            if action == 'command' and recover_legacy_receipt:
                headers['X-CRM-Recover-Legacy'] = '1'
        url = self.crm+'/api/crm/'+action+('?' + urlencode(query) if query else '')
        try:
            value=await self.json(url,method,{'Authorization':'Bearer '+session['crm_session'],**headers},body)
        except CRMError as exc:
            if exc.code not in {'pilot_session_expired','pilot_session_invalid'}:raise
            await self.exchange(session)
            value=await self.json(url,method,{'Authorization':'Bearer '+session['crm_session'],**headers},body)
        return self.context(session,value) if action=='context' else value

    async def field_workspace(self,session,method,action,body,query):
        from urllib.parse import urlencode
        require_grant_workspace(session,'field',{'employee','owner'},'visit')
        paths={
            ('GET','field-route'):('route_day',('lat','lon')),
            ('GET','field-geocode'):('geocode',('q',)),
            ('GET','field-directions'):('route_to',('lat','lon','dlat','dlon')),
            ('GET','field-marks'):('markers',()),
            ('POST','field-marks'):('marker',()),
            ('POST','field-track'):('track',()),
        }
        route=paths.get((method,action))
        if route is None:raise CRMError('Неизвестное действие навигатора.')
        route,keys=route
        selection={key:query[key] for key in keys if key in query}
        if method=='POST' and action=='field-marks':
            body={key:body[key] for key in ('lat','lon','label','op_id') if key in body}
        if method=='POST' and action=='field-track':
            body={key:body[key] for key in ('session_id','points') if key in body}
            if not isinstance(body.get('points'),list) or len(body['points'])>100:
                raise CRMError('Отправьте до 100 точек трека за раз.')
        reply=await self.json(self.primary+'/api/'+route+('?' + urlencode(selection) if selection else ''),
                              method,self.headers(session),body if method=='POST' else None)
        if action!='field-route':return reply
        current={}
        offset=0
        while True:
            page=await self.request(session,'GET','work-portfolio',query=dict(work='visit',offset=offset,limit=100))
            for row in page['contracts']:
                if not row.get('legacy_id'):continue
                key=str(row['legacy_id'])
                current[key]=row if key not in current else None
            if not page['has_more']:break
            offset+=100
        order=[]
        for stop in reply.get('order',[]):
            item=current.get(str(stop.get('rep')))
            if item:
                order.append(dict(client=item['client_name'],person_uuid=item['person_uuid'],
                                  rep=str(stop['rep']),lat=stop['lat'],lon=stop['lon'],km=stop.get('km')))
        exact=len(order)==len(reply.get('order',[]))
        if not exact:
            for stop in order:stop['km']=None
        return dict(order=order,km=reply.get('km') if exact else None,complete=exact,
                    matrix_src=reply.get('matrix_src') if exact else None)
    async def file_action(self,session,action,body):
        if self.executor is None:raise CRMError('Исполнитель Excel недоступен. Файл и исходная операция сохранены.','executor_unavailable',503)
        return await self.executor.file_action(session,action,body)
    async def admin(self,session,path,body):
        require_grant_workspace(session,'exchange',{'analyst','owner'},'exchange')
        scoped_admin.require_safe_admin(session,path,body)
        validate_admin(path,body)
        if self.executor is None:raise CRMError('Исполнитель обмена недоступен. Исходная операция сохранена.','executor_unavailable',503)
        result=await self.executor.admin(session,path,body)
        return scoped_admin.filter_safe_admin_result(session,path,body,result)

class WindowsExecutor:
    """Actual Excel and the existing durable administrator engine on Windows."""
    def __init__(self,home,primary,*,server_key_file=None):
        if os.name!='nt' and not server_key_file:raise RuntimeError('The native Excel executor requires Windows')
        source=str(Path(__file__).resolve().parents[2])
        if source not in sys.path:sys.path.insert(0,source)
        self.home=Path(home).resolve();self.primary=primary;self.crm=None;self.children={};self.locks={};self.excel_lock=asyncio.Lock()
        from . import executor_state
        self.key_file=server_key_file
        self.state=executor_state.ServerStore(server_key_file) if server_key_file else executor_state
    async def start(self):
        self.home.mkdir(parents=True,exist_ok=True);self.client=aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=300))
        self.supervisor=asyncio.create_task(self.recover(),name='crm-windows-executor')
    async def close(self):
        self.supervisor.cancel()
        with contextlib.suppress(asyncio.CancelledError):await self.supervisor
        await self.client.close()
        # The Python queue belongs to the server, not this HTTP process. Its
        # endpoint and credentials are DPAPI-sealed; a new BFF reattaches.

    async def recover(self):
        while True:
            for path in self.home.glob('*/authority.bin'):
                try:
                    session=self.state.read(path)
                    async with self.locks.setdefault(session['owner'],asyncio.Lock()):await self.ensure(session)
                except asyncio.CancelledError:raise
                except Exception:
                    # Keep the queue intact. A denied account never starts work;
                    # temporary authority failures are retried on the next pass.
                    continue
            await asyncio.sleep(10)

    async def crm_context(self,session):
        context=session['context']
        if context.get('access_model',session.get('access_model',1))!=2:return context
        if not self.crm or not session.get('crm_session'):
            raise CRMError('Полномочие фонового задания не подтверждено.','authority_denied',403)
        async def read_context():
            async with self.client.get(self.crm+'/api/crm/context',headers={'Authorization':'Bearer '+session['crm_session']},
                    allow_redirects=False,timeout=aiohttp.ClientTimeout(total=10)) as response:
                value=await response.json()
                if response.status!=200:raise CRMError(value.get('text') or 'Полномочие фонового задания отозвано.',value.get('error','authority_denied'),response.status)
                return value
        try:context=await read_context()
        except CRMError as exc:
            if exc.code not in {'pilot_session_expired','pilot_session_invalid'}:raise
            headers={session.get('credential_header','X-Pult-Token'):session['credential'],
                     'X-CRM-Entry':'analyst' if session.get('entry_role')=='analyst' else 'manager'}
            if session.get('grant_id') is not None:headers['X-CRM-Grant']=session['grant_id']
            async with self.client.post(self.crm+'/auth/exchange',headers=headers,allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=10)) as response:
                exchanged=await response.json()
                if response.status!=200:raise CRMError(exchanged.get('text') or 'Полномочие фонового задания отозвано.',exchanged.get('error','authority_denied'),response.status)
            session['crm_session']=exchanged['token']
            for key in ('grant_id','grants','access_model','policy_revision'):
                if key in exchanged:session[key]=exchanged[key]
            context=await read_context()
        for key in ('grant_id','grants','access_model','policy_revision'):
            if key in session:context.setdefault(key,session[key])
        previous=session['context']
        if (str(context.get('actor_uid'))!=str(previous.get('actor_uid')) or
                context.get('employee_uuid')!=previous.get('employee_uuid') or
                context.get('grant_id')!=previous.get('grant_id')):
            raise CRMError('Полномочие фонового задания изменилось.','authority_denied',403)
        session['context']=context;session['role']=context['role']
        require_grant_workspace(session,'exchange',{'analyst','owner'},'exchange')
        return context
    async def ensure(self,session):
        owner=session['owner'];folder=self.home/hashlib.sha256(owner.encode()).hexdigest();folder.mkdir(parents=True,exist_ok=True)
        owner_access=session['context'].get('owner') is True
        async with self.client.get(self.primary+'/api/crm-identity',headers={'X-Pult-Token':session['credential']},
                allow_redirects=False,timeout=aiohttp.ClientTimeout(total=10)) as response:
            value=await response.json()
            roles={'manager','full'} if owner_access else {'analyst'}
            if response.status!=200 or value.get('subject',{}).get('actor_uid')!=session['context']['actor_uid'] or value.get('me',{}).get('role') not in roles:
                raise CRMError('Доступ администратора отозван. Задания сохранены.','authority_denied',403)
        await self.crm_context(session)
        binding=hashlib.sha256(session['credential'].encode()).hexdigest()
        lease=self.state.read(folder/'runtime.bin');live=False
        if lease:
            if not re.fullmatch(r'http://127\.0\.0\.1:\d{1,5}',lease.get('url','')):raise CRMError('Повреждён адрес исполнителя.','executor_state',503)
            try:
                async with self.client.post(lease['url']+'/busy',headers={'X-Local-Secret':lease['secret']},json={},timeout=aiohttp.ClientTimeout(total=3)) as response:
                    state=await response.json();live=response.status==200 and state.get('ok') is True
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError):pass
            if live and lease['binding']!=binding:
                if state.get('data',{}).get('busy',True):raise CRMError('Завершите прежние задания перед сменой входа.','executor_busy',409)
                await self.stop(owner)
                live=False
        if not live:
            env=dict(os.environ,PYTHONIOENCODING='utf-8',HARD_PULT_SETTINGS=str(folder/'settings.json'),HARD_PULT_PROTECT_TOKENS='1',HARD_CLIENT_VERSION='3.3.0',HARD_CLIENT_PRODUCT='analyst')
            if self.key_file:env.update(HARD_CRM_RUNTIME_KEY_FILE=str(self.key_file),HARD_CRM_SERVER_XLSX='1')
            child,url,secret=await start_runtime(folder,['--crm-analyst'],env,dict(actor=session['context']['actor_uid'],url=self.primary,token=('' if owner_access else 'account:')+session['credential']),'HARD-BACKEND')
            self.children[owner]=child
            lease=dict(url=url,secret=secret,binding=binding,pid=child.pid)
            self.state.write(folder/'runtime.bin',lease)
        self.state.write(folder/'authority.bin',{k:session[k] for k in ('owner','role','credential','credential_header','entry_role','crm_session','grant_id','grants','access_model','policy_revision','context') if k in session})
        return lease

    async def stop(self,owner):
        path=self.home/hashlib.sha256(owner.encode()).hexdigest()/'runtime.bin';lease=self.state.read(path)
        if not lease:return
        async with self.client.post(lease['url']+'/runtime/stop',headers={'X-Local-Secret':lease['secret']},json={}) as response:
            result=await response.json()
            if not result.get('ok'):raise CRMError('Исполнитель ещё занят.','executor_busy',409)
        child=self.children.get(owner)
        if child:await asyncio.wait_for(child.wait(),10)
        else:
            for _ in range(30):
                await asyncio.sleep(0.1)
                try:
                    async with self.client.post(lease['url']+'/busy',headers={'X-Local-Secret':lease['secret']},json={},timeout=aiohttp.ClientTimeout(total=1)):pass
                except (aiohttp.ClientError,asyncio.TimeoutError):break
            else:raise CRMError('Исполнитель ещё останавливается.','executor_busy',409)
        path.unlink(missing_ok=True)
    async def file_action(self,session,action,body):
        require_file_action(session,action,body)
        def execute():
            if action in {'report-write','report-resume'}:
                from pult.core.crm_reports import write,verify,receipt
                if action=='report-resume':verify(body['path'],body['report'],body['kind']);return receipt(body['path'])
                return write(body['report'],body['path'],body['kind'])
            if action=='logistics-read':
                from pult.core import logistics_workbook
                return logistics_workbook.read(body['path'])
            from pult.core import crm_workbook
            if action=='workbook-read':return crm_workbook.read(body['path'])
            if action=='workbook-write':return crm_workbook.write(body['snapshot'],body['path'],body.get('template'))
            if action=='workbook-resume':return crm_workbook.resume(body['snapshot'],body['path'])
            raise CRMError('Неизвестная операция с книгой.')
        async with self.excel_lock:return await asyncio.to_thread(execute)
    async def admin(self,session,path,body):
        require_grant_workspace(session,'exchange',{'analyst','owner'},'exchange')
        scoped_admin.require_safe_admin(session,path,body)
        validate_admin(path,body);owner=session['owner']
        async with self.locks.setdefault(owner,asyncio.Lock()):
            lease=await self.ensure(session)
        async with self.client.post(lease['url']+path,headers={'X-Local-Secret':lease['secret']},json=body) as response:
            result=await response.json()
            if path=='/state' and isinstance(result.get('data'),dict):result['data'].pop('settings_path',None)
            return result


class ServerExecutor(WindowsExecutor):
    """Headless Python and XLSX on the same Linux server as the mobile BFF."""
    def __init__(self,home,primary,key_file):super().__init__(home,primary,server_key_file=key_file)
    async def admin(self,session,path,body):
        require_grant_workspace(session,'exchange',{'analyst','owner'},'exchange')
        validate_admin(path,body)
        if path in {'/enr/confirm','/enr/apply','/enr/retry','/enr/replay','/enr/reconcile-apply','/mail/send'} or path=='/mail/set' and body.get('enabled'):
            raise CRMError('Правка исходных книг и отправка через Outlook выполняются в Windows-программе. Серверные отчёты и новые выгрузки доступны здесь.','native_operation_required',409)
        return await super().admin(session,path,body)
    async def file_action(self,session,action,body):
        require_file_action(session,action,body)
        def execute():
            from pult.core import portable_xlsx,crm_reports,crm_workbook
            if action=='report-write':return portable_xlsx.write_report(body['report'],body['path'],body['kind'])
            if action=='report-resume':crm_reports.verify(body['path'],body['report'],body['kind']);return crm_reports.receipt(Path(body['path']))
            if action=='logistics-read':
                from pult.core import logistics_workbook
                return logistics_workbook.read(body['path'])
            if action=='workbook-read':return portable_xlsx.read_workbook(body['path'])
            if action=='workbook-write':return portable_xlsx.write_workbook(body['snapshot'],body['path'],body.get('template'))
            if action=='workbook-resume':return crm_workbook.resume(body['snapshot'],body['path'])
            raise CRMError('Неизвестная операция с книгой.')
        async with self.excel_lock:return await asyncio.to_thread(execute)
