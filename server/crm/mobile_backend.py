"""Server-side transport for the mobile shell; no working token in JavaScript."""
import asyncio, hashlib, json, os, re, secrets, sys
from pathlib import Path
import aiohttp
from .contract import CRMError

GET_ACTIONS=frozenset('context portfolio work-portfolio tasks card contracts registry managed-portfolio assignment-staff assignment-receipt assignment-history assignment-job manager-statistics manager-report analyst-report analyst-statistics changes active-time shifts shift-report cabinet three-hour script adoption document-read'.split())
POST_ACTIONS=frozenset('registry-edit assignment-preview assignment-commit assignment-job-step assignment-job-cancel active-time anchor command shift plan cohort export import-preview import-apply receipt telemetry script document'.split())
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
            async with self.client.post(self.native_url+'/crm/'+action,headers={'X-Local-Secret':self.native_secret,'X-Demo-Role':session['role']},json=dict(method='POST',body=body,query={})) as response:
                value=await response.json()
                if not response.ok:raise CRMError(value.get('text') or 'Файл не подготовлен.',value.get('error','file'),response.status)
                return value
    async def admin(self,session,path,body):
        if session['role']!='analyst':raise CRMError('Требуется вход Аналитика.','forbidden',403)
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
            return value

class SharedBackend:
    """Pinned CRM/primary access. Excel/legacy work runs on a configured executor."""
    synthetic=False
    def __init__(self,primary,crm,executor=None):
        from urllib.parse import urlsplit
        for url in (primary,crm):
            parsed=urlsplit(url)
            if parsed.scheme!='https' or parsed.hostname!='hard.rimborso.ru' or parsed.username or parsed.password or parsed.fragment or parsed.query:raise RuntimeError('Pinned first-party HTTPS services required')
        self.primary=primary.rstrip('/');self.crm=crm.rstrip('/');self.executor=executor
    async def start(self):
        self.client=aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=90))
        if self.executor:await self.executor.start()
    async def close(self):
        if self.executor:await self.executor.close()
        await self.client.close()
    async def json(self,url,method='GET',headers=None,body=None):
        async with self.client.request(method,url,headers=headers,json=body if method=='POST' else None,allow_redirects=False) as response:
            try:value=await response.json()
            except (aiohttp.ContentTypeError,ValueError):
                raise CRMError('Служба входа отклонила запрос.' if response.status in {401,403,410} else 'Служба вернула неполный ответ. Исходная операция сохранена.','authority_denied' if response.status in {401,403,410} else 'upstream_protocol',response.status if response.status>=400 else 502) from None
            if not response.ok:raise CRMError(value.get('text') or value.get('error') or 'Служба недоступна.',value.get('error','request'),response.status)
            return value
    def headers(self,session):return {session['credential_header']:session['credential'],'X-CRM-Entry':'analyst' if session['role']=='analyst' else 'manager','X-Client-Version':'3.3.0-mobile.1','X-Work-Product':'crm','X-Client-Runtime':'crm-mobile'}
    async def login(self,body):
        value=await self.json(self.primary+'/api/account/login','POST',body=dict(username=body.get('username'),password=body.get('password'),device=dict(kind='phone',fp=body.get('device_id'),label=body.get('device_label','Мобильная CRM')),entrance='crm-mobile',audience='crm-mobile',gps=body.get('gps')))
        if value.get('action') not in {'ok','ok_pending'}:raise CRMError(value.get('error') or 'Вход не выполнен.','login',403)
        role='employee' if value['role']=='agent' else 'observer' if value['role']=='security' else 'manager' if value['role']=='full' else value['role']
        session=dict(role=role,credential_header='X-App-Token' if role=='employee' else 'X-Pult-Token',credential=value['token'],synthetic=False,username=value['username'])
        if value.get('action')!='ok' or value.get('must_change_password'):
            session['pending']=dict(status='password' if value.get('must_change_password') else 'device',username=value['username'],employee=value['employee'])
            return session
        return await self.activate(session)
    async def activate(self,session):
        identity=await self.json(self.primary+'/api/crm-identity',headers=self.headers(session))
        session['role']=identity['me']['role']
        exchanged=await self.json(self.crm+'/auth/exchange','POST',headers=self.headers(session))
        session['crm_session']=exchanged['token'];session['context']=await self.request(session,'GET','context');session.pop('pending',None);return session
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
    async def request(self,session,method,action,body=None,query=None):
        from urllib.parse import urlencode
        if action=='manager-access':
            routes={'team':'/api/manager/team','approve':'/api/manager/account_approve','reset':'/api/manager/account_reset','revoke':'/api/manager/team/device-revoke','board':'/api/manager/employee_board'}
            resource=(body if method=='POST' else query or {}).get('resource')
            if resource not in routes:raise CRMError('Неизвестное действие Команды.')
            return await self.json(self.primary+routes[resource],method,self.headers(session),(body or {}).get('payload') if method=='POST' else None)
        if action not in (GET_ACTIONS if method=='GET' else POST_ACTIONS):raise CRMError('Неизвестный запрос.')
        try:return await self.json(self.crm+'/api/crm/'+action+('?' + urlencode(query) if query else ''),method,{'Authorization':'Bearer '+session['crm_session']},body)
        except CRMError as exc:
            if exc.code!='pilot_session_expired':raise
            exchanged=await self.json(self.crm+'/auth/exchange','POST',headers=self.headers(session));session['crm_session']=exchanged['token']
            return await self.json(self.crm+'/api/crm/'+action+('?' + urlencode(query) if query else ''),method,{'Authorization':'Bearer '+session['crm_session']},body)
    async def file_action(self,session,action,body):
        if self.executor is None:raise CRMError('Исполнитель Excel недоступен. Файл и исходная операция сохранены.','executor_unavailable',503)
        return await self.executor.file_action(session,action,body)
    async def admin(self,session,path,body):
        if session['role']!='analyst':raise CRMError('Требуется вход Аналитика.','forbidden',403)
        validate_admin(path,body)
        if self.executor is None:raise CRMError('Исполнитель обмена недоступен. Исходная операция сохранена.','executor_unavailable',503)
        return await self.executor.admin(session,path,body)

class WindowsExecutor:
    """Actual Excel and the existing durable administrator engine on Windows."""
    def __init__(self,home,primary):
        if os.name!='nt':raise RuntimeError('The native Excel executor requires Windows')
        source=str(Path(__file__).resolve().parents[2])
        if source not in sys.path:sys.path.insert(0,source)
        self.home=Path(home).resolve();self.primary=primary;self.children={};self.locks={};self.excel_lock=asyncio.Lock()
    async def start(self):
        self.home.mkdir(parents=True,exist_ok=True);self.client=aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=300))
    async def close(self):
        await self.client.close()
        for child,_,_ in self.children.values():
            if child.returncode is None:
                child.terminate()
                try:await asyncio.wait_for(child.wait(),8)
                except asyncio.TimeoutError:child.kill();await child.wait()
    async def file_action(self,session,action,body):
        if session['role'] not in {'analyst','manager'} and not (action in {'report-write','report-resume'} and body.get('kind')=='shifts'):raise CRMError('Нет права на работу с книгами.','forbidden',403)
        def execute():
            if action in {'report-write','report-resume'}:
                from pult.core.crm_reports import write,verify,receipt
                if action=='report-resume':verify(body['path'],body['report'],body['kind']);return receipt(body['path'])
                return write(body['report'],body['path'],body['kind'])
            from pult.core import crm_workbook
            if action=='workbook-read':return crm_workbook.read(body['path'])
            if action=='workbook-write':return crm_workbook.write(body['snapshot'],body['path'],body.get('template'))
            if action=='workbook-resume':return crm_workbook.resume(body['snapshot'],body['path'])
            raise CRMError('Неизвестная операция с книгой.')
        async with self.excel_lock:return await asyncio.to_thread(execute)
    async def admin(self,session,path,body):
        if session['role']!='analyst':raise CRMError('Требуется вход Аналитика.','forbidden',403)
        validate_admin(path,body);owner=session['owner']
        binding=hashlib.sha256(session['credential'].encode()).hexdigest()
        async with self.locks.setdefault(owner,asyncio.Lock()):
            prior=self.children.get(owner)
            if prior and prior[0].returncode is None and getattr(prior[0],'mobile_binding',None)!=binding:
                async with self.client.post(prior[1]+'/busy',headers={'X-Local-Secret':prior[2]},json={}) as response:state=await response.json()
                if not state.get('ok') or state.get('data',{}).get('busy',True):raise CRMError('Завершите прежние задания перед сменой входа.','executor_busy',409)
                prior[0].terminate();await prior[0].wait()
            if not prior or prior[0].returncode is not None:
                folder=self.home/hashlib.sha256(owner.encode()).hexdigest();folder.mkdir(parents=True,exist_ok=True)
                env=dict(os.environ,PYTHONIOENCODING='utf-8',HARD_PULT_SETTINGS=str(folder/'settings.json'),HARD_PULT_PROTECT_TOKENS='1',HARD_CLIENT_VERSION='3.3.0',HARD_CLIENT_PRODUCT='analyst')
                prior=await start_runtime(folder,['--crm-analyst'],env,dict(actor=session['context']['actor_uid'],url=self.primary,token='account:'+session['credential']),'HARD-BACKEND')
                prior[0].mobile_binding=binding;self.children[owner]=prior
        async with self.client.post(prior[1]+path,headers={'X-Local-Secret':prior[2]},json=body) as response:
            result=await response.json()
            if path=='/state' and isinstance(result.get('data'),dict):result['data'].pop('settings_path',None)
            return result
