import json
from tools.legacy_pipeline.config_choice import select


def put(path,config):
    path.write_text(json.dumps(config),'utf-8');return path


def test_disabled_package_does_not_replace_active_same_journal(tmp_path):
    put(tmp_path/'rbr-manual-dates.json',dict(enabled=False,journal='same'))
    active=put(tmp_path/'active.json',dict(enabled=True,journal='same'))
    put(tmp_path/'config.json',dict(manifest=str(active)))
    assert select(tmp_path,tmp_path)==(active,False)


def test_other_journal_is_not_reconfigured(tmp_path):
    own=put(tmp_path/'rbr-manual-dates.json',dict(enabled=False,journal='ours'))
    put(tmp_path/'config.json',dict(enabled=True,journal='another'))
    assert select(tmp_path,tmp_path)==(own,False)


def test_explicit_config_is_honoured(tmp_path):
    own=put(tmp_path/'manual.json',dict(enabled=False))
    put(tmp_path/'config.json',dict(enabled=True))
    assert select(tmp_path,tmp_path,str(own))==(own,False)


def test_direct_active_config_never_becomes_self_referencing_pointer(tmp_path):
    put(tmp_path/'rbr-manual-dates.json',dict(enabled=False,journal='same'))
    pointer=put(tmp_path/'config.json',dict(enabled=True,journal='same'))
    assert select(tmp_path,tmp_path)==(pointer,False)
