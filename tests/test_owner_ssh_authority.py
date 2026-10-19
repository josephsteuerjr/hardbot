"""Synthetic SSH streams: secret handling, sudo prompts and verified UID 0."""
import json
import pytest
from pult.core import account_session as session

READY=b'HARD_ROOT_READY_v1\n'
PROMPT=b'HARD_SUDO_PASSWORD_v1'
RESULT=b'{"ok":true,"effective_uid":0}'

class Channel:
    def __init__(self,mode='nopass',denied=False):
        self.mode=mode;self.denied=denied;self.out=b'';self.err=b'';self.sent=[];self.command='';self.closed=False
    def settimeout(self,_):pass
    def exec_command(self,command):
        self.command=command
        if self.denied:return
        if self.mode=='password':self.err=PROMPT
        else:self.out=READY+RESULT
    def sendall(self,data):
        self.sent.append(data)
        if data==b'sudo-secret\n':self.out=READY+RESULT
    def recv_ready(self):return bool(self.out)
    def recv_stderr_ready(self):return bool(self.err)
    def recv(self,_):data=self.out;self.out=b'';return data
    def recv_stderr(self,_):data=self.err;self.err=b'';return data
    def exit_status_ready(self):return self.denied
    def recv_exit_status(self):return 0
    def shutdown_write(self):pass
    def close(self):self.closed=True

class Transport:
    def __init__(self,channel):self.channel=channel;self.auth=[];self.closed=False
    def auth_password(self,user,password):self.auth.append((user,password))
    def is_authenticated(self):return True
    def open_session(self,**_):return self.channel
    def close(self):self.closed=True

@pytest.mark.parametrize('user,mode,expected',[('root','nopass','root'),('synthetic-admin','nopass','sudo'),('synthetic-admin','password','sudo')])
def test_root_or_real_sudo_and_no_password_mixed_into_json(monkeypatch,user,mode,expected):
    channel=Channel(mode);transport=Transport(channel)
    monkeypatch.setattr(session,'_transport',lambda _: (transport,'synthetic-key'))
    result=session.verify_root_access('192.0.2.1','synthetic-key',user,'ssh-secret','sudo-secret')
    assert result['effective_uid']==0 and result['authority']==expected and result['accounts_changed'] is False
    assert transport.auth==[(user,'ssh-secret')] and transport.closed
    assert channel.sent==([b'sudo-secret\n'] if mode=='password' else [])
    assert '/usr/bin/id -u' in channel.command and 'exit 77' in channel.command
    assert 'ssh-secret' not in channel.command and 'sudo-secret' not in channel.command

def test_sudo_without_root_never_receives_account_payload(monkeypatch):
    channel=Channel(denied=True);transport=Transport(channel)
    monkeypatch.setattr(session,'_transport',lambda _: (transport,'synthetic-key'))
    with pytest.raises(RuntimeError,match='UID 0'):session.verify_root_access('192.0.2.1','synthetic-key','synthetic-admin','ssh-secret')
    assert channel.sent==[] and channel.closed and transport.closed

def test_changed_host_key_sends_no_password(monkeypatch):
    transport=Transport(Channel())
    monkeypatch.setattr(session,'_transport',lambda _: (transport,'changed-key'))
    with pytest.raises(RuntimeError,match='Пароль не отправлен'):session.verify_root_access('192.0.2.1','expected-key','synthetic-admin','ssh-secret')
    assert transport.auth==[] and transport.closed
