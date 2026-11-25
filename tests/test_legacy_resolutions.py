from tools.legacy_pipeline.collection import plan
from tools.legacy_pipeline.resolutions import resolve
from test_legacy_collection_plan import row,book,FIELDS


def case():
    rows=[row()];result=plan(rows,[book(row(value='Fresh'))],{},FIELDS)
    return rows,result,dict(master_sha256='master',source_hashes={'soft.xlsb':'source'},decisions=[dict(conflict=x,choice='file') for x in result.conflicts])


def test_approved_snapshot_resolves_exact_values():
    rows,p,a=case()
    assert resolve(p,rows,a,master_sha256='master',source_hashes={'soft.xlsb':'source'})==2
    assert not p.blocked and any(x['after']=='Fresh' for x in p.changes)


def test_changed_master_or_source_invalidates_every_choice():
    for master,source in [('changed','source'),('master','changed')]:
        rows,p,a=case()
        assert resolve(p,rows,a,master_sha256=master,source_hashes={'soft.xlsb':source})==0
        assert p.blocked


def test_choice_never_applies_to_another_conflict_value():
    rows,p,a=case()
    a['decisions']=[dict(conflict={**x['conflict'],'incoming':'different'},choice='file') for x in a['decisions']]
    assert resolve(p,rows,a,master_sha256='master',source_hashes={'soft.xlsb':'source'})==0
    assert p.blocked
