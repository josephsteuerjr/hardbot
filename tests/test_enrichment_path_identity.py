import enrich


def test_mapped_path_uses_existing_unc_history_and_preserves_office_edit(monkeypatch):
    mapped = 'U:/portfolio/Employee.xlsb'
    unc = '//server/share/portfolio/Employee.xlsb'
    monkeypatch.setattr(enrich, 'canonical_book_key', lambda path: unc.casefold())
    value = {'value': 'Delivered', 'at': '2026-10-29T12:00:00Z'}
    ledger = {'_books': {unc.casefold(): {'A': {'call_status': value}}}}
    found = enrich.ledger_for_book(ledger, mapped)
    assert found == {'A': {'call_status': value}}
    fields = [{'key': 'contract_id', 'title': 'Номер КД / ID'},
              {'key': 'visit_date', 'title': 'Дата выезда', 'date': True},
              {'key': 'call_status', 'title': 'Статус звонка'},
              {'key': 'overall_result', 'title': 'Общий результат'}]
    grid = [['Номер КД / ID', 'Дата выезда', 'Статус звонка', 'Общий результат'],
            ['A', '29.10.2026', '', 'Office changed']]
    row = {'contract_id': 'A', 'visit_date': '2026-10-29', '_bot_completed': True, 'call_status': 'Delivered'}
    plan = enrich.build_plan(grid, fields, [row], ledger=found)
    assert not plan['conflicts'] and plan['office_edits'][0]['key'] == 'call_status'
    assert not any(c == 3 for r, c, value, date in plan['writes'])


def test_alias_histories_keep_newest_field_and_old_only_fields(monkeypatch):
    mapped, unc = 'U:/book.xlsb', '//server/share/book.xlsb'
    monkeypatch.setattr(enrich, 'canonical_book_key', lambda path: unc)
    older = dict(value='Old', at='2026-10-28T10:00:00Z')
    newer = dict(value='New', at='2026-10-29T10:00:00Z')
    books = {mapped.casefold(): {'A': {'status': older, 'legacy': older}},
             unc: {'A': {'status': newer}}}
    assert enrich.ledger_for_book({'_books': books}, mapped)['A'] == dict(status=newer, legacy=older)


def test_conflict_decision_follows_real_alias_but_not_another_workbook(monkeypatch):
    mapped, unc = 'U:/book.xlsb', '//server/share/book.xlsb'
    monkeypatch.setattr(enrich, 'canonical_book_key', lambda path: unc if str(path) == mapped else enrich.book_key(path))
    decisions = {(mapped, 'A', 'status'): 'server', ('//other/share/book.xlsb', 'B', 'status'): 'server'}
    assert enrich.decisions_for_book(decisions, unc) == {('A', 'status'): 'server'}



def test_durable_backup_for_long_original_name_keeps_a_short_unique_path(tmp_path):
    root = tmp_path / ("x" * max(1, 176 - len(str(tmp_path)) - 1))
    original = tmp_path / "city" / "Example Person With Long Name" / "Example Person With Long Name.xlsb"
    source = tmp_path / "pristine.xlsb"
    source.write_bytes(b"original bytes")

    first = enrich.backup_copy(original, root, source=source, slot=7)
    second = enrich.backup_copy(original, root, source=source, slot=7)
    assert first.parent == second.parent == root
    assert first != second and len(str(second)) < 260
    assert first.read_bytes() == second.read_bytes() == source.read_bytes()
