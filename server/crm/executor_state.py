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
