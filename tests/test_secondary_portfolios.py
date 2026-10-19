from dataclasses import replace
from pathlib import Path

from registry_cycle.portfolio_paths import secondary_folders
from registry_cycle.config import PortfolioRoot, load_config_bundle
from registry_cycle.discovery import discover
from pult.core.bootstrap import ensure_paths
ensure_paths()
import razbros
from pult.core import enrichment as en
import enrich


def tree(root):
    for name in ('Mixed Worker', 'Archived Worker'):
        folder = root / 'City' / 'СОФТЫ' / name
        folder.mkdir(parents=True)
        (folder / (name + '.xlsb')).write_bytes(b'old hard')
    soft = root / 'Soft' / 'Mixed Worker';soft.mkdir(parents=True)
    (soft / 'Mixed Worker.xlsb').write_bytes(b'current soft')
    archived = root / 'Soft' / 'Archived Worker';archived.mkdir()
    (archived / 'Копия Archived Worker.xlsb').write_bytes(b'copy')
    (archived / '~$Archived Worker.xlsb').write_bytes(b'lock')
    deep = root / 'City' / 'Other' / 'History' / 'Deep Worker';deep.mkdir(parents=True)
    (deep / 'Deep Worker.xlsb').write_bytes(b'archive')


def test_only_current_paired_secondary_books_are_discovered(tmp_path):
    tree(tmp_path)
    selected = secondary_folders(tmp_path, tmp_path / 'City' / 'СОФТЫ')
    assert [p.name for p in selected] == ['Mixed Worker']
    bundle=load_config_bundle()
    bundle=replace(bundle, app=replace(bundle.app, portfolio_roots=(PortfolioRoot('Test',str(tmp_path),()),)))
    report=discover(bundle)
    assert not report.blocked
    assert {str(e.path.relative_to(tmp_path)) for e in report.entries} == {
        str(Path('City/СОФТЫ/Mixed Worker/Mixed Worker.xlsb')),
        str(Path('Soft/Mixed Worker/Mixed Worker.xlsb')),
    }
    assert len({e.entry_id for e in report.entries}) == 2


def test_enrichment_routes_hard_and_soft_by_columns(tmp_path, monkeypatch):
    tree(tmp_path)
    fmap=razbros.build_folder_map([('Test',tmp_path)])
    entries=fmap['mixed worker']
    assert {e['kind'] for e in entries}=={'hard','soft'}
    assert all(e['kind']!='hard' for e in fmap.get('archived worker',[]))
    monkeypatch.setattr(en,'workbook_profile',lambda p:'soft' if p.parent.parent.name=='Soft' else 'hard')
    by_emp={product:{'employee':'Mixed Worker','profile':product,'filled':1,'rows':[]} for product in ('hard','soft')}
    matched,nofolder,nofile=en.match_employees(by_emp,fmap)
    assert not nofolder and not nofile and len(matched)==2
    assert all(len(m['files'])==1 for m in matched)
    for m in matched:
        assert ('Soft' in m['files'][0].parts)==(m['profile']=='soft')
