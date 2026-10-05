"""Both analyst clients can select a pure Soft portfolio for export."""

from urllib.parse import parse_qs, urlsplit

from pult import desk_registry
from pult.core import registry_import as ri
from pult.core.import_flow import ImportFlow


STATS = {'employees': [{'employee': 'Hard person', 'contracts': 2},
                       {'employee': 'Shared person', 'contracts': 1}],
         'soft_employees': [{'employee': 'Soft person', 'contracts': 3},
                            {'employee': 'Shared person', 'contracts': 1}]}


def test_desktop_export_list_includes_soft_and_deduplicates_names(monkeypatch):
    monkeypatch.setattr(desk_registry, 'S', {})
    response = desk_registry.exp_employees({}, admin=lambda method, path: STATS)
    names = {r['name']: r['count'] for r in response['employees']}
    # The UI labels counts as assignments: one contract can have both roles.
    assert names == {'Hard person': 2, 'Soft person': 3, 'Shared person': 2}
    desk_registry.exp_prepare({'selected': ['soft person'], 'since': '', 'until': ''},
                              admin=lambda *args, **kwargs: None,
                              start_job=lambda kind, work: kind)
    assert desk_registry.S['exp_db'] == STATS['employees'] + STATS['soft_employees']


def test_qt_export_request_accepts_soft_selection():
    class Date:
        def get(self):
            return ''

    class View:
        db_employees = STATS['employees']
        db_soft_employees = STATS['soft_employees']
        exp_selected = {'soft person'}
        exp_since = exp_until = Date()
        _merge_import_employees = staticmethod(ri.merge_import_employees)

    request = ImportFlow._export_request(View())
    assert parse_qs(urlsplit(request['path']).query)['employee'] == ['Soft person']
    assert not request['all_employees']
