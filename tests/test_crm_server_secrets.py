import asyncio,base64,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))  # package pult, not legacy GUI module
import pytest
from cryptography.exceptions import InvalidTag
from pult.core.settings import Settings,SERVER_TOKEN_PREFIX
from crm.executor_state import ServerStore
from crm.mobile_backend import ServerExecutor
from crm.contract import CRMError


def test_server_tokens_are_encrypted_and_cannot_be_used_as_executor_state(tmp_path,monkeypatch):
    key=tmp_path/'key';key.write_bytes(os.urandom(32));monkeypatch.setenv('HARD_CRM_RUNTIME_KEY_FILE',str(key))
    path=tmp_path/'settings.json';secret='synthetic-credential-not-for-production'
    settings=Settings({'url':'https://example.invalid','device_token':secret},path)
    assert settings.save() and secret not in path.read_text()
    assert Settings.load(path)['device_token']==secret
    raw=json.loads(path.read_text())['device_token'];assert raw.startswith(SERVER_TOKEN_PREFIX)
    lease=tmp_path/'runtime.bin';lease.write_bytes(base64.b64decode(raw[len(SERVER_TOKEN_PREFIX):]))
    with pytest.raises(InvalidTag):ServerStore(key).read(lease)
    key.write_bytes(os.urandom(32))
    with pytest.raises(InvalidTag):Settings.load(path)


def test_native_commands_fail_before_starting_a_server_job(tmp_path):
    key=tmp_path/'key';key.write_bytes(os.urandom(32));home=tmp_path/'executor'
    executor=ServerExecutor(home,'https://hard.rimborso.ru',key)
    async def run():
        with pytest.raises(CRMError) as e:await executor.admin({'role':'employee'},'/mail/send',{})
        assert e.value.status==403
        with pytest.raises(CRMError) as e:await executor.admin({'role':'analyst'},'/enr/apply',{})
        assert e.value.code=='native_operation_required'
    asyncio.run(run());assert not home.exists()
