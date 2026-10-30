import pytest

from pult import desk_registry as desk


@pytest.mark.parametrize('legacy', [{}, {'full': False, 'restore_manual': False, 'soft_edits': False}])
def test_old_false_flags_cannot_disable_required_preview_or_apply(monkeypatch, legacy):
    monkeypatch.setattr(desk, 'S', desk._empty_state())
    monkeypatch.setattr(desk, 'LIMITED_TEST_MASTER_IMPORT_UI_ENABLED', True)
    desk.S['book'] = dict(path='synthetic.xlsb', file_sha256='source', import_profile='soft')
    prepared = dict(source_kind='test_subset', source_proof={'synthetic': True}, sheets=[],
                    authoritative_employees=['Synthetic'], import_profile='soft')
    preparations = []
    def prepare(book, selected, **options):
        preparations.append(options)
        return prepared, None
    monkeypatch.setattr(desk.ri, 'exclude_contracts', lambda book, excluded: (book, 0))
    monkeypatch.setattr(desk.ri, 'prepare_import', prepare)
    posted = []
    def post(admin, path, body, **kwargs):
        posted.append((path, body.copy()))
        if path.endswith('/preview.json'):
            return dict(auto_unassigned=1, auto_unassigned_digest='a'*64,
                        manual_restored=2, manual_restore_digest='b'*64,
                        soft_office_effect_digest='c'*64)
        return dict(updated=3)
    monkeypatch.setattr(desk.xs, 'post_import', post)
    result = []
    def start(name, work):
        result.append(work(lambda line: None))
        return name
    body = {**legacy, 'selected': ['synthetic'], 'replace_comments': False}
    desk.imp_preview(body, admin=lambda *a, **k: {'ok': True}, start_job=start)
    assert not result[-1].get('error')
    desk.imp_apply(body, admin=lambda *a, **k: {'ok': True}, start_job=start)
    assert len(posted) == 2
    assert preparations == [dict(full_registry=True, force_test_subset=True)]
    for path, request in posted:
        assert request['full_registry'] is True
        assert request['restore_manual_unassigned'] is True
        assert request['allow_soft_office_edits'] is True
        assert request['replace_current_visit_comments'] is False
    assert posted[1][1]['expected_auto_unassigned_digest'] == 'a'*64
    assert posted[1][1]['expected_manual_restore_digest'] == 'b'*64
    assert posted[1][1]['expected_soft_office_effect_digest'] == 'c'*64


def test_fixed_options_do_not_weaken_source_selection_or_comment_binding():
    book = dict(file_sha256='first', import_profile='hard')
    base = desk._signature(book, ['A'], False, False, False, False, ['excluded'])
    assert base == desk._signature(book, ['A'], True, True, False, True, ['excluded'])
    assert base != desk._signature(book, ['B'], True, True, False, True, ['excluded'])
    assert base != desk._signature(book, ['A'], True, True, True, True, ['excluded'])
    assert base != desk._signature(book, ['A'], True, True, False, True, [])
    assert base != desk._signature({**book, 'file_sha256': 'changed'}, ['A'], True, True, False, True, ['excluded'])
