"""Explicit portable configuration shared by the observer and Python writers."""
from __future__ import annotations
import json,os
from datetime import datetime
from pathlib import Path
from .manual_edits import Store

def state_directory():
    return Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Hard/manual-edits'

def load_config(path=None):
    current=Path(path or os.environ.get('HARD_MANUAL_DATES_CONFIG') or state_directory()/'config.json')
    seen=set()
    for _ in range(4):
        key=Store.key(current)
        if key in seen:raise ValueError('Циклическая ссылка конфигурации дат')
        seen.add(key)
        if not current.is_file():return {}
        config=json.loads(current.read_text('utf-8-sig'))
        if 'manifest' not in config:return config
        current=Path(config['manifest'])
    raise ValueError('Слишком много ссылок конфигурации дат')

def preferred_path(path,config=None):
    path=Path(path);config=load_config() if config is None else config
    matches=[Path(p) for p in config.get('preferred_books',[]) if Store.key(Path(p).parent)==Store.key(path.parent)]
    if len(matches)>1:raise ValueError('Несколько выбранных активных книг в одной папке')
    return matches[0] if matches else path

def department_folders(root,config=None,*,extra_active_names=()):
    """Bounded employee folders; legacy nested Hard books need an active Soft peer.

    This is the same current-portfolio boundary as portfolio_paths, with the
    explicitly preferred dated Soft filename also recognized. Split may supply
    current master Soft owners before generating their first personal books.
    """
    root=Path(root);config=load_config() if config is None else config
    if not root.is_dir():return []
    folders=[p for p in root.iterdir() if p.is_dir() and not p.name.startswith(('!','_','~')) and not p.name.casefold().startswith('архив')]
    if root.name.casefold() not in ('софт','софты'):return folders
    name_key=lambda name:' '.join(str(name).strip().casefold().split())
    requested={name_key(name) for name in extra_active_names};active=set()
    country=root.parent.parent
    for department in country.iterdir():
        if not department.is_dir() or department.name.casefold()!='soft':continue
        for folder in department.iterdir():
            if folder.is_dir() and (name_key(folder.name) in requested or preferred_path(folder/(folder.name+'.xlsb'),config).is_file()):active.add(name_key(folder.name))
    return [folder for folder in folders if name_key(folder.name) in active]

def personal_paths(roots,config=None):
    config=load_config() if config is None else config
    paths=[]
    for root in map(Path,roots):
        if not root.is_dir():continue
        for folder in department_folders(root,config):
            path=preferred_path(folder/(folder.name+'.xlsb'),config)
            if path.is_file() and not path.name.startswith('~'):paths.append(path)
    return sorted(set(paths),key=lambda p:Store.key(p))

def current_pattern_paths(config):
    months=('ЯНВАРЬ','ФЕВРАЛЬ','МАРТ','АПРЕЛЬ','МАЙ','ИЮНЬ','ИЮЛЬ','АВГУСТ','СЕНТЯБРЬ','ОКТЯБРЬ','НОЯБРЬ','ДЕКАБРЬ')
    now=datetime.now()
    return [Path(p.format(month_ru=months[now.month-1],month=f'{now.month:02d}',year=now.year)) for p in config.get('book_patterns',[])]

def book_paths(config):
    paths=[preferred_path(p,config) for p in config.get('books',[])]
    paths.extend(current_pattern_paths(config))
    paths.extend(personal_paths(config.get('personal_roots',[]),config))
    return sorted(set(paths),key=lambda p:Store.key(p))

def is_configured(path,config):
    key=Store.key(path)
    if key in {Store.key(p) for p in current_pattern_paths(config)}:return True
    if key in {Store.key(preferred_path(p,config)) for p in config.get('books',[])}:return True
    path=Path(path)
    if key!=Store.key(preferred_path(path,config)):return False
    if path.name!=path.parent.name+'.xlsb' and key not in {Store.key(p) for p in config.get('preferred_books',[])}:return False
    # Known writers must acknowledge the first Hard projection even when the
    # paired Soft workbook is generated later in that same split. Observation
    # and collection use the narrower department_folders/personal_paths scope.
    return Store.key(path.parent.parent) in {Store.key(p) for p in config.get('personal_roots',[])}
