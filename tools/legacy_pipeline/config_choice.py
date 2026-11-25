"""Preserve an already enabled observer; a disabled package cannot disable it."""
import json
from pathlib import Path


def read(path):
    path=Path(path);seen=set()
    for _ in range(4):
        if path in seen:
            raise ValueError('Циклическая конфигурация учёта дат')
        seen.add(path)
        config=json.loads(path.read_text('utf-8-sig'))
        if 'manifest' not in config:
            return path,config
        path=Path(config['manifest'])
    raise ValueError('Слишком много ссылок конфигурации учёта дат')


def select(directory, local_root, explicit=None):
    chosen,config=read(explicit or Path(directory)/'rbr-manual-dates.json')
    pointer=Path(local_root)/'config.json'
    if not explicit and pointer.is_file():
        prior,active=read(pointer)
        if active.get('enabled') and active.get('journal')==config.get('journal'):
            return prior,False
    return chosen,bool(config.get('enabled')) and chosen.resolve()!=pointer.resolve()
