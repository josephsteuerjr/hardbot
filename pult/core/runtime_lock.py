"""OS-owned single runtime lease; a process crash releases it automatically."""
import os
from pathlib import Path


def acquire(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    try:
        stream.seek(0);stream.write(b'1');stream.flush();stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BaseException:
        stream.close();raise RuntimeError('Этот профиль уже выполняется другим процессом.') from None
    return stream
