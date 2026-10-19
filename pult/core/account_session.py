"""Analyst password entry and owner bootstrap over an authenticated SSH session."""
import base64
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import re
import shlex
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from . import api
from .settings import _dpapi


def _identity(settings_path):
    path=Path(settings_path).with_name('analyst-pc-key.bin')
    if not path.exists():
        key=Ed25519PrivateKey.generate()
        raw=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        path.parent.mkdir(parents=True,exist_ok=True)
        protected=_dpapi(raw)
        try:
            with path.open('xb') as f: f.write(protected)
        except FileExistsError: pass
    key=serialization.load_pem_private_key(_dpapi(path.read_bytes(),decrypt=True),password=None)
    pub=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    return key,base64.b64encode(pub).decode(),hashlib.sha256(pub).hexdigest()


def login(url,username,password,settings_path):
    key,pub,fp=_identity(settings_path)
    username=str(username or '').strip().lower()
    challenge=api.request(url,'POST','/api/account/pc_challenge',{'public_key':pub,'fingerprint':fp,'label':'Аналитик · '+socket.gethostname()})
    message='\n'.join((challenge['challenge_id'],challenge['nonce'],'',username,hashlib.sha256(password.encode()).hexdigest()))
    result=api.request(url,'POST','/api/account/login',{'username':username,'password':password,'audience':'analyst',
        'entrance':'internal','device':{'kind':'desktop','fp':fp,'label':'Аналитик · '+socket.gethostname()},
        'pc_proof':{'challenge_id':challenge['challenge_id'],'signature':base64.b64encode(key.sign(message.encode())).decode()}})
    if result.get('action') not in ('ok','ok_pending') or not result.get('token'):
        raise RuntimeError(result.get('error') or 'Вход не выполнен.')
    return result


def _transport(ip):
    import paramiko
    ip=str(ipaddress.ip_address(str(ip or '').strip()))
    transport=paramiko.Transport(socket.create_connection((ip,22),timeout=12))
    transport.banner_timeout=12;transport.auth_timeout=15
    transport.start_client(timeout=15)
    key=transport.get_remote_server_key()
    fp='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
    return transport,fp


def probe(ip):
    transport,fp=_transport(ip)
    try: return {'host_key':fp}
    finally: transport.close()


def _root_channel(transport,ssh_username,ssh_password,sudo_password,command):
    """Send JSON only after a real host UID 0 has announced readiness.

    sudo may be passwordless. Never prepend a password to the program's stdin:
    send it only in response to sudo's own prompt, before the ready marker.
    """
    import paramiko
    user=str(ssh_username or 'root').strip()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}\$?',user):raise ValueError('Проверьте имя пользователя SSH.')
    if any(c in str(sudo_password or ssh_password) for c in ('\r','\n')):raise ValueError('Пароль sudo не должен содержать перевод строки.')
    try:transport.auth_password(user,ssh_password)
    except paramiko.AuthenticationException:raise RuntimeError('Пароль SSH не принят или этот способ входа запрещён сервером.') from None
    if not transport.is_authenticated():raise RuntimeError('SSH-вход не подтверждён.')
    channel=transport.open_session(timeout=20);channel.settimeout(180)
    ready=b'HARD_ROOT_READY_v1\n';prompt=b'HARD_SUDO_PASSWORD_v1'
    script='test "$(/usr/bin/id -u)" = 0 || exit 77; printf '+shlex.quote(ready.decode())+'; exec '+command
    invoke='/bin/sh -c '+shlex.quote(script)
    if user!='root':invoke='sudo -S -p '+shlex.quote(prompt.decode())+' -- '+invoke
    channel.exec_command(invoke)
    output=b'';errors=b'';sent=False;deadline=time.monotonic()+35
    try:
        while time.monotonic()<deadline:
            if channel.recv_ready():output+=channel.recv(65536)
            if channel.recv_stderr_ready():errors+=channel.recv_stderr(65536)
            if len(output)>65536 or len(errors)>65536:raise RuntimeError('Неожиданный ответ проверки SSH/sudo.')
            if prompt in errors:
                if sent:raise RuntimeError('Пароль sudo не принят.')
                channel.sendall((str(sudo_password or ssh_password)+'\n').encode());sent=True;errors=b''
            if output.startswith(ready):
                channel._hard_root_extra=output[len(ready):]
                return channel,('root' if user=='root' else 'sudo')
            if channel.exit_status_ready():break
            time.sleep(.01)
        raise RuntimeError('SSH-вход подтверждён, но права root не получены. Нужен root или sudo с фактическим UID 0.')
    except BaseException:
        channel.close();raise


def verify_root_access(ip,host_key,ssh_username,ssh_password,sudo_password=''):
    transport,actual_key=_transport(ip)
    try:
        if not secrets.compare_digest(actual_key,str(host_key or '')):raise RuntimeError('Ключ SSH сервера изменился. Пароль не отправлен.')
        channel,method=_root_channel(transport,ssh_username,ssh_password,sudo_password,"printf '%s' '{\"ok\":true,\"effective_uid\":0}'")
        channel.shutdown_write();chunks=[getattr(channel,'_hard_root_extra',b'')]
        while True:
            part=channel.recv(65536)
            if not part:break
            chunks.append(part)
        data=json.loads(b''.join(chunks).decode())
        if channel.recv_exit_status()!=0 or data.get('effective_uid')!=0:raise RuntimeError('Права root не подтверждены.')
        return {**data,'authority':method,'host_key':actual_key,'accounts_changed':False}
    finally:transport.close()


def root_bootstrap(url,ip,root_password,host_key,username,password,operation_id,*,legacy_token='',legacy_device_id='',employee='',recovery=False,ssh_username='root',sudo_password=''):
    # Detect an old server before any SSH-side database operation.
    caps=api.request(url,'GET','/api/account/capabilities')
    if not caps.get('root_bootstrap'): raise RuntimeError('Сначала требуется новая версия сервера. Профиль не создавался.')
    nonce=api.request(url,'POST','/api/account/root_challenge',{'operation_id':operation_id})['nonce']
    transport,actual_key=_transport(ip)
    try:
        if not secrets.compare_digest(actual_key,str(host_key or '')):
            raise RuntimeError('Ключ SSH сервера изменился. Пароль не отправлен.')
        channel,authority=_root_channel(transport,ssh_username,root_password,sudo_password,'docker exec -i hard-core-app-1 python -B -m root_bootstrap')
        channel.sendall(json.dumps({'username':username,'password':password,'operation_id':operation_id,'nonce':nonce,
            'legacy_token':legacy_token,'legacy_device_id':legacy_device_id,'employee':employee,'recovery':bool(recovery)}).encode())
        channel.shutdown_write()
        chunks=[getattr(channel,'_hard_root_extra',b'')]
        while True:
            part=channel.recv(65536)
            if not part:break
            chunks.append(part)
        result=json.loads(b''.join(chunks).decode('utf-8'))
        if channel.recv_exit_status()!=0 or not result.get('ok'):raise RuntimeError(result.get('error') or 'Профиль не создан.')
        return {**result,'authority':authority}
    finally: transport.close()
