import json
from pathlib import Path
import pytest
from pult.core.manual_config import load_config,personal_paths,is_configured
from pult.core.manual_runtime import staged_master,prepare,publish,fingerprint,case_key
from pult.core.manual_shared import SharedEngine
from pult.core.manual_workbooks import read
from test_manual_workbooks_python import fixture
from test_manual_sync_python import edit

def setup(tmp_path,monkeypatch):
    path=tmp_path/'master.xlsx';fixture(path)
    config=tmp_path/'config.json'
    config.write_text(json.dumps(dict(enabled=True,books=[str(path)],journal=str(tmp_path/'journal'))),encoding='utf-8')
    monkeypatch.setenv('HARD_MANUAL_DATES_CONFIG',str(config));monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'profile'))
    engine=SharedEngine(tmp_path/'journal',tmp_path/'other-pc');engine.scan(path)
    return path,engine

def test_staging_preserves_manual_dates_and_acks_automatic_changes(tmp_path,monkeypatch):
    path,engine=setup(tmp_path,monkeypatch)
    edit(path,'saved by user')
    with staged_master(path) as candidate:
        assert read(candidate).view.dates['T-1','excel_visit_edited_on'][2]
        edit(candidate,'automatic merge')
    assert engine.scan(path)['writes']==0
    assert next(c for c in read(path).view.cells if c.title=='комментарий звонка').value=='automatic merge'

def test_stale_master_is_not_published(tmp_path,monkeypatch):
    path,engine=setup(tmp_path,monkeypatch)
    with pytest.raises(RuntimeError,match='изменена'):
        with staged_master(path) as candidate:
            edit(candidate,'candidate')
            edit(path,'concurrent manual edit')
    assert next(c for c in read(path).view.cells if c.title=='комментарий звонка').value=='concurrent manual edit'

def test_failed_staging_leaves_source_identical(tmp_path,monkeypatch):
    path,engine=setup(tmp_path,monkeypatch);before=fingerprint(path)
    with pytest.raises(RuntimeError):
        with staged_master(path) as candidate:
            edit(candidate,'not published');raise RuntimeError('writer failed')
    assert fingerprint(path)==before

def test_explicit_dated_active_book_replaces_canonical_choice(tmp_path,monkeypatch):
    root=tmp_path/'department';folder=root/'Employee';folder.mkdir(parents=True)
    ordinary=folder/'Employee.xlsb';dated=folder/'Employee 29.xlsb'
    ordinary.touch();dated.touch()
    config=dict(enabled=True,personal_roots=[str(root)],preferred_books=[str(dated)])
    assert personal_paths([root],config)==[dated]
    assert is_configured(dated,config) and not is_configured(ordinary,config)
    pointer=tmp_path/'pointer.json';target=tmp_path/'manifest.json'
    pointer.write_text(json.dumps(dict(manifest=str(target))))
    target.write_text(json.dumps(config))
    assert load_config(pointer)==config

def test_same_contract_different_partners_are_not_joined():
    headers=['Номер КД / ID','Партнер']
    assert case_key(['T-1','A'],headers)!=case_key(['T-1','B'],headers)

def test_nested_hard_scope_keeps_only_active_soft_peers_and_preferred_dates(tmp_path):
    from pult.core.manual_config import department_folders
    country=tmp_path/'country';nested=country/'City'/'СОФТЫ';soft=country/'Soft'
    active=nested/'Current';inactive=nested/'Former';peer=soft/'Current'
    for folder in (active,inactive,peer):folder.mkdir(parents=True)
    hard=active/'Current.xlsb';old=inactive/'Former.xlsb';dated=peer/'Current 29.xlsb'
    for path in (hard,old,dated):path.touch()
    config=dict(personal_roots=[str(nested)],preferred_books=[str(dated)])
    assert personal_paths([nested],config)==[hard]
    assert is_configured(hard,config) and is_configured(old,config)
    # A current master owner can get their first nested Hard projection in
    # the same split that creates the first Soft workbook.
    new=nested/'New';new.mkdir();(soft/'New').mkdir()
    assert new in department_folders(nested,config,extra_active_names=['New'])
    assert inactive not in department_folders(nested,config,extra_active_names=['New'])
