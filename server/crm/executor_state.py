"""Private Windows executor state: recover endpoint and authority after BFF restart."""
import json
import os
from pathlib import Path
from pult.core.settings import _dpapi


def read(path):
    try:return json.loads(_dpapi(Path(path).read_bytes(),decrypt=True))
    except FileNotFoundError:return None


def write(path,value):
    path=Path(path);temporary=path.with_suffix('.tmp')
    with temporary.open('wb') as stream:
        stream.write(_dpapi(json.dumps(value,ensure_ascii=False).encode()));stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


class ServerStore:
    def __init__(self,key_file):
        from pult.core.runtime_secrets import cipher
        self.key_file=Path(key_file);cipher(self.key_file,b'crm-python-executor-v1')
    def read(self,path):
        from pult.core.runtime_secrets import open_sealed
        try:data=Path(path).read_bytes()
        except FileNotFoundError:return None
        return json.loads(open_sealed(data,self.key_file,b'crm-python-executor-v1'))
    def write(self,path,value):
        from pult.core.runtime_secrets import seal
        path=Path(path);temporary=path.with_suffix('.tmp')
        with temporary.open('wb') as stream:
            os.chmod(temporary,0o600)
            stream.write(seal(json.dumps(value,ensure_ascii=False).encode(),self.key_file,b'crm-python-executor-v1'))
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
