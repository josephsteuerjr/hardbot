"""Mobile CRM BFF and PWA. Separate namespace; never replaces legacy PWA."""
import argparse, asyncio, base64, hashlib, json, os, re, secrets, shutil, sqlite3, time, uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote, unquote
import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .contract import CRMError
from .mobile_backend import SyntheticBackend, SharedBackend, WindowsExecutor

PREFIX='/mobile/'
COOKIE='crm_mobile_v1'
ASSETS=Path(__file__).resolve().parents[1]/'miniapp/crm'

class Sessions:
    def __init__(self,home,key,source_roots=()):
        self.home=Path(home).resolve();self.home.mkdir(parents=True,exist_ok=True)
        self.path=self.home/'mobile-sessions.sqlite3';self.cipher=AESGCM(key)
        self.source_roots=tuple(Path(p).resolve() for p in source_roots)
        if any(p==Path(p.anchor) or p==Path.home() for p in self.source_roots):raise RuntimeError('Use explicit portfolio directories, not an entire drive or home')
        with self.connect() as conn:
            conn.executescript('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,sealed BLOB NOT NULL,expires REAL NOT NULL); CREATE TABLE IF NOT EXISTS ended_sessions(id TEXT PRIMARY KEY); CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,owner TEXT NOT NULL,path TEXT NOT NULL,name TEXT NOT NULL); CREATE TABLE IF NOT EXISTS downloads(id TEXT PRIMARY KEY,owner TEXT NOT NULL,sealed BLOB NOT NULL);')
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=15);conn.row_factory=sqlite3.Row;return conn
    def read(self,sid):
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,60}',sid or ''):return None
        with self.connect() as conn:row=conn.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
        if not row or row['expires']<time.time():return None
        value=row['sealed'];return json.loads(self.cipher.decrypt(value[:12],value[12:],sid.encode()))
    def write(self,value,sid=None):
        sid=sid or secrets.token_urlsafe(32);nonce=os.urandom(12)
        sealed=nonce+self.cipher.encrypt(nonce,json.dumps(value,ensure_ascii=False).encode(),sid.encode())
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM ended_sessions WHERE id=?',(sid,)).fetchone():raise CRMError('Вход изменился. Исходная работа сохранена.','session',401)
            conn.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)',(sid,sealed,value['expires_at']/1000))
        return sid
    def forget(self,sid):
        with self.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO ended_sessions VALUES(?)',(sid,));conn.execute('DELETE FROM sessions WHERE id=?',(sid,))
    def job(self,owner,identifier,value=None):
        with self.connect() as conn:
            old=conn.execute('SELECT * FROM downloads WHERE id=?',(identifier,)).fetchone()
            if old and old['owner']!=owner:raise CRMError('Операция принадлежит другому входу.','forbidden',403)
            if value is None:
                if not old:return None
                sealed=old['sealed'];return json.loads(self.cipher.decrypt(sealed[:12],sealed[12:],('download:'+identifier).encode()))
            nonce=os.urandom(12);sealed=nonce+self.cipher.encrypt(nonce,json.dumps(value,ensure_ascii=False).encode(),('download:'+identifier).encode())
            conn.execute('INSERT OR REPLACE INTO downloads VALUES(?,?,?)',(identifier,owner,sealed))
    def jobs(self,owner):
        with self.connect() as conn:ids=[row[0] for row in conn.execute('SELECT id FROM downloads WHERE owner=?',(owner,))]
        return [(identifier,self.job(owner,identifier)) for identifier in ids]
    def reserve(self,owner,name):
        name=Path(name.replace('\\','/')).name[:160]
        name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name) or 'Файл.xlsx'
        identifier=str(uuid.uuid4());fid='mobile-file:'+identifier+'-'+name;folder=self.home/'files'/hashlib.sha256(owner.encode()).hexdigest()[:32];folder.mkdir(parents=True,exist_ok=True)
        path=folder/(identifier.replace('-','')+'-'+Path(name).stem[:24]+Path(name).suffix)
        with self.connect() as conn:conn.execute('INSERT INTO files VALUES(?,?,?,?)',(fid,owner,str(path),name))
        return fid,path,name
    def file(self,owner,fid):
        with self.connect() as conn:row=conn.execute('SELECT * FROM files WHERE id=? AND owner=?',(fid,owner)).fetchone()
        if not row:raise CRMError('Файл не выбран этим сотрудником.','file_scope',403)
        path=Path(row['path']).resolve()
        if not self.allowed(owner,path):raise CRMError('Недопустимый путь.','file_scope',403)
        return path,row['name']
    def allowed(self,owner,path):
        digest=hashlib.sha256(owner.encode()).hexdigest()
        return any(path.is_relative_to(self.home/'files'/key) for key in (digest[:32],digest)) or any(path.is_relative_to(root) for root in self.source_roots)
    def grant(self,owner,path):
        path=Path(path).resolve()
        if not self.allowed(owner,path):raise CRMError('Источник не входит в разрешённые каталоги.','file_scope',403)
        with self.connect() as conn:
            prior=conn.execute('SELECT id FROM files WHERE owner=? AND path=?',(owner,str(path))).fetchone()
            if prior:return prior['id']
            fid='mobile-file:'+str(uuid.uuid4())+'-'+path.name
            conn.execute('INSERT INTO files VALUES(?,?,?,?)',(fid,owner,str(path),path.name))
        return fid
    def translate(self,owner,value,*,admin_route=False):
        if isinstance(value,dict):
            result={}
            for k,v in value.items():
                if admin_route and k=='path':
                    if not isinstance(v,str) or not re.match(r'^/(?:admin|ops)/[a-zA-Z0-9_./?-]',v) or '..' in v:raise CRMError('Недопустимый маршрут.','forbidden',403)
                    result[k]=v
                else:result[k]=self.translate(owner,v)
            return result
        if isinstance(value,list):return [self.translate(owner,v) for v in value]
        if isinstance(value,str) and value.startswith('mobile-file:'):return str(self.file(owner,value)[0])
        if isinstance(value,str) and (re.match(r'^[a-zA-Z]:[\\/]',value) or value.startswith('\\\\') or value.startswith('/')):raise CRMError('Выберите источник в этом рабочем месте.','file_scope',403)
        return value
    def public_files(self,owner,value):
        if isinstance(value,dict):return {k:self.public_files(owner,v) for k,v in value.items()}
        if isinstance(value,list):return [self.public_files(owner,v) for v in value]
        if isinstance(value,str) and (re.match(r'^[a-zA-Z]:[\\/]',value) or value.startswith('\\\\')):
            path=Path(value).resolve()
            if self.allowed(owner,path):return self.grant(owner,path)
        return value

def create_app(backend,home,*,key=None,public_origin=None,source_roots=(),prefix=PREFIX):
    home=Path(home).resolve();home.mkdir(parents=True,exist_ok=True)
    if key is None:
        if not backend.synthetic:raise RuntimeError('An explicit mobile session key is required')
        path=home/'session-key.private.bin'
        if not path.exists():path.write_bytes(os.urandom(32))
        key=path.read_bytes()
    if not re.fullmatch(r'/[a-zA-Z0-9_/-]+/',prefix):raise RuntimeError('Explicit absolute mobile prefix required')
    sessions=Sessions(home,key,source_roots);app=web.Application(client_max_size=128*1024*1024)
    app['mobile_sessions']=sessions;app['mobile_backend']=backend

    @web.middleware
    async def boundary(request,handler):
        try:
            if backend.synthetic and request.host.split(':')[0] not in {'127.0.0.1','localhost'}:raise CRMError('Проверочная среда доступна только локально.','host',403)
            sid=request.cookies.get(COOKIE,'');session=sessions.read(sid)
            request['mobile_session']=session;request['mobile_sid']=sid
            if request.method=='POST':
                expected=public_origin or request.scheme+'://'+request.host
                if request.headers.get('Origin') not in (None,expected):raise CRMError('Источник запроса не совпадает.','origin',403)
                if not session or not secrets.compare_digest(request.headers.get('X-CRM-CSRF',''),session['csrf']):raise CRMError('Обновите подключение перед отправкой.','csrf',403)
            response=await handler(request)
        except CRMError as exc:response=web.json_response(dict(error=exc.code,text=str(exc),details=exc.details),status=exc.status)
        except (ValueError,TypeError,KeyError):response=web.json_response(dict(error='invalid',text='Некорректные параметры запроса.'),status=400)
        except asyncio.TimeoutError:response=web.json_response(dict(error='timeout',text='Ответ задерживается. Проверьте исходную операцию.'),status=504)
        except aiohttp.ClientError:response=web.json_response(dict(error='upstream_unavailable',text='Служба временно недоступна. Исходная операция сохранена.'),status=503)
        except OSError as exc:
            response=web.json_response(dict(error='file_path_long' if getattr(exc,'winerror',None)==206 else 'storage_unavailable',text='Папка слишком глубокая для Excel. Выберите вложенную папку отдельно.' if getattr(exc,'winerror',None)==206 else 'Хранилище недоступно. Исходные файлы сохранены.'),status=400 if getattr(exc,'winerror',None)==206 else 503)
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Cache-Control':'no-store','X-CRM-Mobile-Version':'3.3.0-mobile.1'})
        return response
    app.middlewares.append(boundary)

    def cookie(response,sid,request):response.set_cookie(COOKIE,sid,httponly=True,secure=bool(public_origin and public_origin.startswith('https:') or request.secure),samesite='Strict',path=prefix,max_age=8*3600)
    def empty():return dict(csrf=secrets.token_urlsafe(32),expires_at=int((time.time()+8*3600)*1000))
    def bind_owner(session):
        context=session['context'];namespace=str(home) if backend.synthetic else 'shared'
        session['owner']='mobile:'+hashlib.sha256((namespace+'\n'+str(context['actor_uid'])+'\n'+context['employee_uuid']+'\n'+context['role']).encode()).hexdigest()
    async def authorize(request):
        session=request['mobile_session']
        if not session or not session.get('owner'):raise CRMError('Войдите в рабочее место.','session',401)
        try:context=await backend.request(session,'GET','context')
        except CRMError as exc:
            if exc.status in {401,403,410}:
                sessions.forget(request['mobile_sid'])
                raise CRMError('Доступ к рабочему месту изменился. Исходная работа сохранена.','identity_changed',401) from None
            raise
        if str(context['actor_uid'])!=str(session['context']['actor_uid']) or context['employee_uuid']!=session['context']['employee_uuid'] or context['role']!=session['role']:
            sessions.forget(request['mobile_sid'])
            raise CRMError('Вход изменился. Исходная работа сохранена.','identity_changed',401)
        session['context']=context;sessions.write(session,request['mobile_sid']);return session
    async def boot(request):
        session=request['mobile_session'] or empty()
        if session.get('pending') and session['pending']['status']!='password':
            try:await backend.activate(session);bind_owner(session)
            except CRMError as exc:
                if exc.status not in {401,403,410}:raise
        if session.get('owner'):session=await authorize(request)
        sid=sessions.write(session,request['mobile_sid'] if request['mobile_session'] else None)
        public={k:session[k] for k in ('owner','context','synthetic','expires_at') if k in session}
        response=web.json_response(dict(csrf=session['csrf'],session=public if session.get('owner') else None,pending=session.get('pending'),synthetic=backend.synthetic))
        cookie(response,sid,request);return response
    async def login(request):
        body=await request.json();session=await backend.login(body)
        session.update(empty())
        if not session.get('pending'):bind_owner(session)
        sessions.forget(request['mobile_sid']);sid=sessions.write(session)
        response=web.json_response(dict(ok=True));cookie(response,sid,request);return response
    async def password(request):
        session=request['mobile_session']
        if backend.synthetic:raise CRMError('Смена личного пароля доступна в рабочем входе.','synthetic',403)
        result=await backend.password(session,await request.json());sessions.write(session,request['mobile_sid']);return web.json_response(result)
    async def logout(request):
        if not backend.synthetic:await backend.logout(request['mobile_session'])
        sessions.forget(request['mobile_sid']);response=web.json_response(dict(ok=True));response.del_cookie(COOKIE,path=prefix);return response
    async def api(request):
        session=await authorize(request);body=await request.json()
        method=body.get('method','GET');action=body.get('action','')
        if method not in {'GET','POST'}:raise CRMError('Недопустимый метод.')
        data=await backend.request(session,method,action,body.get('body') or {},body.get('query') or {})
        sessions.write(session,request['mobile_sid'])
        return web.json_response(data)
    async def upload(request):
        session=await authorize(request)
        if request.content_length and request.content_length>64*1024*1024:raise CRMError('Файл больше 64 МБ.','size',413)
        name=unquote(request.headers.get('X-CRM-Filename','Файл.xlsx'));fid,path,name=sessions.reserve(session['owner'],name)
        size=0
        with path.open('xb') as out:
            async for chunk in request.content.iter_chunked(1024*1024):
                size+=len(chunk)
                if size>64*1024*1024:raise CRMError('Файл больше 64 МБ.','size',413)
                out.write(chunk)
            out.flush();os.fsync(out.fileno())
        return web.json_response(dict(file=fid,name=name,bytes=size))
    async def file(request):
        session=await authorize(request);path,name=sessions.file(session['owner'],request.match_info['id'])
        if not path.is_file():raise CRMError('Файл ещё не готов.','file_missing',404)
        return web.FileResponse(path,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name),'X-CRM-Filename':quote(name)})
    async def admin(request):
        session=await authorize(request)
        if session['role']!='analyst':raise CRMError('Требуется вход Аналитика.','forbidden',403)
        value=await request.json()
        if value.get('action')=='reserve':
            fid,path,name=sessions.reserve(session['owner'],str(value['args'][0]));return web.json_response(dict(file=fid,name=name))
        if value.get('action')=='folder':
            handles=value.get('args')
            if not isinstance(handles,list) or not 1<=len(handles)<=500:raise CRMError('Выберите до 500 файлов.')
            selected=[(*sessions.file(session['owner'],item['file'] if isinstance(item,dict) else item),item.get('relative_path','') if isinstance(item,dict) else '') for item in handles]
            if sum(p.stat().st_size for p,_,_ in selected)>512*1024*1024:raise CRMError('Папка больше 512 МБ.','size',413)
            fid,path,name=sessions.reserve(session['owner'],'Портфели');path.mkdir()
            for source,name,relative in selected:
                parts=relative.replace('\\','/').split('/') if relative else [name]
                if any(part in {'','.','..'} for part in parts) or re.match(r'^[A-Za-z]:',parts[0]):raise CRMError('Недопустимое имя в папке.','file_scope',403)
                target=(path/Path(*parts)).resolve()
                if not target.is_relative_to(path):raise CRMError('Недопустимый путь в папке.','file_scope',403)
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():raise CRMError('В папке несколько файлов с одинаковым именем. Выберите отдельные подпапки.')
                await asyncio.to_thread(shutil.copy2,source,target)
            return web.json_response(dict(file=fid))
        if value.get('action')!='local' or not isinstance(value.get('args'),list) or len(value['args'])!=2:raise CRMError('Недопустимое действие Аналитика.')
        path,body=value['args'];translated=sessions.translate(session['owner'],body,admin_route=path=='/admin')
        result=await backend.admin(session,path,translated)
        await authorize(request)
        return web.json_response(sessions.public_files(session['owner'],result))
    async def sources(request):
        session=await authorize(request)
        if session['role']!='analyst':raise CRMError('Требуется вход Аналитика.','forbidden',403)
        parent=request.query.get('parent');mode=request.query.get('kind','folder')
        if not parent:
            entries=[dict(file=sessions.grant(session['owner'],p),name=p.name,directory=True) for p in sessions.source_roots if p.is_dir()]
        else:
            path,name=sessions.file(session['owner'],parent)
            if not path.is_dir():raise CRMError('Выберите папку.')
            entries=[]
            for child in sorted(path.iterdir(),key=lambda p:(not p.is_dir(),p.name.casefold())):
                if len(entries)>=200:break
                resolved=child.resolve()
                if not sessions.allowed(session['owner'],resolved) or not child.is_dir() and child.suffix.lower() not in {'.xlsx','.xlsb','.xlsm','.xls','.csv','.txt'}:continue
                if mode=='folder' and not child.is_dir():continue
                entries.append(dict(file=sessions.grant(session['owner'],resolved),name=child.name,directory=child.is_dir()))
        return web.json_response(dict(entries=entries,parent=parent))
    download_locks={}
    async def settle_receipt(session,identifier,job):
        if not job.get('receipt') or job.get('receipt_state')!='pending':return
        try:
            result=await backend.request(session,'POST','receipt',job['receipt'])
            job.update(receipt_state='accepted',receipt_result=result,receipt_error=None)
        except CRMError as exc:
            if exc.status in {400,403,409,422}:job.update(receipt_state='attention',receipt_error=str(exc),receipt_code=exc.code)
            else:job['receipt_error']='Подтверждение доставки ожидает связи.'
        except (asyncio.TimeoutError,aiohttp.ClientError):job['receipt_error']='Ответ на квитанцию не получен. Исходная квитанция сохранена.'
        sessions.job(session['owner'],identifier,job)
    async def receipts(request):
        session=await authorize(request);owner=session['owner'];input=await request.json() if request.method=='POST' else {}
        entries=[]
        for identifier,_ in sessions.jobs(owner):
            async with download_locks.setdefault(identifier,asyncio.Lock()):
                job=sessions.job(owner,identifier)
                if not job.get('receipt'):continue
                if input.get('action')=='retry' and input.get('operation_id')==identifier:job['receipt_state']='pending'
                if request.method=='POST':await settle_receipt(session,identifier,job)
                entries.append(dict(action='receipt',body={**job['receipt'],'operation_id':identifier},queued_at=job.get('queued_at',datetime.now(timezone.utc).isoformat()),state=job['receipt_state'],result=job.get('receipt_result'),error=job.get('receipt_error'),code=job.get('receipt_code'),server=True))
        return web.json_response(dict(entries=entries))
    def process_alive(pid):
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD);kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes=(wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD));kernel.GetExitCodeProcess.restype=wintypes.BOOL
            kernel.CloseHandle.argtypes=(wintypes.HANDLE,);kernel.CloseHandle.restype=wintypes.BOOL
            handle=kernel.OpenProcess(0x1000,False,int(pid))
            if not handle:return False
            code=ctypes.c_ulong()
            try:return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
            finally:kernel.CloseHandle(handle)
        try:os.kill(int(pid),0);return True
        except ProcessLookupError:return False
        except PermissionError:return True
    async def workbook(request):
        session=await authorize(request);value=await request.json();action=value['action'];data=value.get('input') or {};role=session['role']
        if role not in {'manager','analyst'} and action!='shift-report':raise CRMError('Нет права на работу с книгами.','forbidden',403)
        if action=='read':
            path,name=sessions.file(session['owner'],data['file']);result=await backend.file_action(session,'workbook-read',dict(path=str(path)));result['filename']=name;return web.json_response(result)
        identifier=str(uuid.UUID(value['operation_id']));owner=session['owner']
        expected=hashlib.sha256(json.dumps(dict(action=action,input=data),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        async with download_locks.setdefault(identifier,asyncio.Lock()):
            job=sessions.job(owner,identifier)
            if job and job['request_hash']!=expected:raise CRMError('Номер выгрузки связан с другим запросом.','operation_conflict',409)
            if not job:
                template=sessions.file(owner,data['template'])[0] if data.get('template') else None
                suffix=template.suffix.lower() if template else '.xlsx'
                fid,path,name=sessions.reserve(owner,str(data.get('name','Отчёт CRM'))+suffix)
                job=dict(request_hash=expected,file=fid,phase='planned',input=data,action=action,queued_at=datetime.now(timezone.utc).isoformat())
                if action=='write':
                    if not isinstance(data.get('snapshot'),dict):raise CRMError('Нет подтверждённой выгрузки.')
                    job.update(runtime_action='workbook-write',body=dict(snapshot=data['snapshot'],path=str(path),template=str(template) if template else None))
                else:
                    if action=='table':report=dict(headers=data['headers'],rows=data['rows']);kind='table'
                    elif action in {'manager-report','analyst-report'}:
                        month=data.get('mode')=='month';dynamics=data.get('mode')=='dynamics';report_prefix='analyst' if action=='analyst-report' else 'manager'
                        report=await backend.request(session,'GET',report_prefix+('-statistics' if month else '-report'),query=dict(month=data.get('month')) if month else dict(snapshot_id=data['snapshot_id']) if data.get('snapshot_id') and not dynamics else dict(day=data.get('day')))
                        if not month and not dynamics:report=report['report']
                        kind='manager-month' if month else 'manager-dynamics' if dynamics else 'three-hour'
                    elif action=='shift-report':report=await backend.request(session,'GET','shift-report',query={k:v for k,v in data.get('report',{}).items() if k in {'since','until'}});kind='shifts'
                    else:raise CRMError('Неизвестный вид выгрузки.')
                    job.update(runtime_action='report-write',body=dict(report=report,kind=kind,path=str(path)))
                sessions.job(owner,identifier,job)
            path,name=sessions.file(owner,job['file'])
            if job['phase']!='complete':
                if path.exists():
                    result=await backend.file_action(session,'workbook-resume' if job['runtime_action']=='workbook-write' else 'report-resume',job['body'])
                else:
                    if job['phase']=='running' and process_alive(job['executor_pid']):raise CRMError('Исходная выгрузка ещё выполняется или её результат неизвестен. Повторная запись не запущена.','download_pending',503)
                    job.update(phase='running',executor_pid=backend.native_pid if isinstance(backend,SyntheticBackend) else os.getpid());sessions.job(owner,identifier,job)
                    try:result=await backend.file_action(session,job['runtime_action'],job['body'])
                    except CRMError as exc:
                        if exc.status<500:job['phase']='failed';sessions.job(owner,identifier,job)
                        raise
                    except (ValueError,TypeError):
                        job['phase']='failed';sessions.job(owner,identifier,job)
                        raise
                job['sha256']=result['sha256'];job['phase']='file_verified';sessions.job(owner,identifier,job)
                if action=='write':
                    snapshot=job['body']['snapshot'];manifest=snapshot['manifest']
                    cells=[dict(contract_uuid=row['contract_uuid'],key=key,value=row['values'][key],event_id=source['event_id']) for row in snapshot['rows'] for key,source in row.get('provenance',{}).get('_fields',{}).items() if source.get('event_id') and key in row['values']]
                    if cells:job.update(receipt=dict(receipt_id=identifier,export_id=manifest['export_id'],manifest_hash=manifest['manifest_hash'],target_id=manifest['target_id'],file_sha256=job['sha256'],cells=cells),receipt_state='pending')
                job['phase']='complete';sessions.job(owner,identifier,job)
            await settle_receipt(session,identifier,job)
            with path.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
            if checksum!=job['sha256']:raise CRMError('Содержимое выгрузки изменилось. Исходный файл сохранён.','file_changed',409)
            return web.FileResponse(path,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name),'X-CRM-Filename':quote(name),'X-CRM-Download-Id':identifier,'X-CRM-Receipt-State':job.get('receipt_state','none')})
    async def assets(request):
        name=request.match_info.get('asset','') or 'index.html';path=(ASSETS/name).resolve()
        if not path.is_relative_to(ASSETS.resolve()) or not path.is_file():raise web.HTTPNotFound()
        response=web.FileResponse(path)
        if name=='sw.js':response.headers['Service-Worker-Allowed']=prefix
        return response
    async def lifecycle(app):
        await backend.start()
        yield
        await backend.close()
    app.cleanup_ctx.append(lifecycle)
    app.router.add_get(prefix+'boot',boot);app.router.add_post(prefix+'login',login);app.router.add_post(prefix+'logout',logout)
    app.router.add_post(prefix+'password',password)
    app.router.add_get(prefix+'receipts',receipts);app.router.add_post(prefix+'receipts',receipts)
    app.router.add_post(prefix+'api',api);app.router.add_post(prefix+'upload',upload);app.router.add_post(prefix+'admin',admin);app.router.add_post(prefix+'workbook',workbook);app.router.add_get(prefix+'sources',sources)
    app.router.add_get(prefix+'file/{id:.+}',file);app.router.add_get(prefix+'{asset:.*}',assets)
    return app

if __name__=='__main__':
    parser=argparse.ArgumentParser();homes=parser.add_mutually_exclusive_group(required=True)
    homes.add_argument('--demo-home');homes.add_argument('--home');parser.add_argument('--port',type=int,default=0);parser.add_argument('--receipt')
    parser.add_argument('--primary');parser.add_argument('--crm');parser.add_argument('--session-key-file');parser.add_argument('--native-executor',action='store_true')
    parser.add_argument('--source-root',action='append',default=[]);parser.add_argument('--prefix',default=PREFIX);parser.add_argument('--public-origin')
    args=parser.parse_args();home=Path(args.demo_home or args.home).resolve();key=None
    if args.demo_home:
        if args.primary or args.crm or args.native_executor:parser.error('Synthetic mode cannot use shared services')
        backend=SyntheticBackend(home/'synthetic')
    else:
        if not args.primary or not args.crm or not args.session_key_file or args.public_origin!='https://hard.rimborso.ru':parser.error('Shared mode requires pinned services, explicit key and first-party public origin')
        key=Path(args.session_key_file).read_bytes()
        if len(key)!=32:parser.error('The mobile session key must contain 32 bytes')
        executor=WindowsExecutor(home/'executor',args.primary) if args.native_executor else None
        backend=SharedBackend(args.primary,args.crm,executor)
    async def run():
        app=create_app(backend,home,key=key,public_origin=args.public_origin,source_roots=args.source_root,prefix=args.prefix);runner=web.AppRunner(app,access_log=None);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',args.port);await site.start();url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+args.prefix
        if args.receipt:Path(args.receipt).write_text(json.dumps(dict(url=url,pid=os.getpid(),synthetic=backend.synthetic)),encoding='utf-8')
        print('MOBILE-CRM '+url,flush=True)
        try:await asyncio.Event().wait()
        finally:await runner.cleanup()
    asyncio.run(run())
