"""Synthetic settings migration never creates or re-enrolls a server device."""
import json

import pytest

from pult.core.settings import Settings


@pytest.fixture
def backend(tmp_path, monkeypatch):
    from pult import desk_backend
    monkeypatch.setattr(desk_backend, "_settings", Settings({}, tmp_path / "new-settings.json"))
    monkeypatch.setattr(desk_backend, "_jobs", {})
    return desk_backend


def test_import_preserves_profiles_tokens_active_and_workbook_paths(backend, tmp_path):
    source = tmp_path / "old.json"
    original = {"profiles": {
        "Первый": {"url": "https://one.synthetic.invalid", "device_token": "synthetic-token-one"},
        "Второй": {"url": "https://two.synthetic.invalid", "device_token": "synthetic-token-two", "key": "synthetic-master"},
    }, "active": "Второй", "enrich_file": "synthetic-book.xlsb", "scatterBase": "synthetic-root"}
    source.write_text(json.dumps(original), encoding="utf-8")
    data = backend.import_settings(str(source))
    assert data["bound"] and data["active"] == "Второй"
    saved = dict(Settings.load(backend._settings.path))
    assert saved["device_token"] == "synthetic-token-two"
    assert saved["profiles"]["Первый"]["device_token"] == "synthetic-token-one"
    assert "key" not in saved["profiles"]["Второй"]
    assert saved["enrich_file"] == original["enrich_file"]
    assert json.loads(source.read_text(encoding="utf-8")) == original


def test_import_rejects_existing_identity_and_running_job(backend, tmp_path):
    source = tmp_path / "old.json"
    source.write_text('{"url":"https://synthetic.invalid"}', encoding="utf-8")
    backend._settings["profiles"]["Прод"]["device_token"] = "synthetic-existing"
    with pytest.raises(RuntimeError, match="уже привязан"):
        backend.import_settings(str(source))
    backend._settings["profiles"]["Прод"]["device_token"] = ""
    backend._jobs["test"] = {"state": "running"}
    with pytest.raises(RuntimeError, match="завершения"):
        backend.import_settings(str(source))


def test_confirmed_revocation_removes_only_rejected_profile_token(backend, monkeypatch):
    from pult.core import api
    prof = backend._profile()
    prof.update(url='https://synthetic.invalid', device_token='account:synthetic-revoked')
    backend._settings['profiles']['Other'] = {'url': 'https://other.invalid', 'device_token': 'synthetic-other'}
    def rejected(*args, **kwargs):
        raise api.AccessRevoked('Synthetic revoked')
    monkeypatch.setattr(api, 'request', rejected)
    with pytest.raises(api.AccessRevoked):
        backend.admin('GET', '/admin/devices.json')
    assert not prof['device_token']
    saved = Settings.load(backend._settings.path)
    assert not saved.get('device_token')
    assert saved['profiles']['Other']['device_token'] == 'synthetic-other'


def test_failed_import_keeps_current_settings(backend, tmp_path, monkeypatch):
    source = tmp_path / "old.json"
    source.write_text('{"url":"https://synthetic.invalid","sec":"registry"}', encoding="utf-8")
    previous = dict(backend._settings)
    monkeypatch.setattr(Settings, "save", lambda self: False)
    with pytest.raises(RuntimeError, match="сохранить"):
        backend.import_settings(str(source))
    assert dict(backend._settings) == previous


def test_all_exports_use_working_api_and_trial_receipts_stay_local(backend, tmp_path, monkeypatch):
    backend._settings["scatterBase"] = str(tmp_path)
    monkeypatch.setattr(backend, "_network_profile", lambda: {"url": "https://example.invalid"})
    sent = []
    monkeypatch.setattr(backend.api_client, "request", lambda url, method, path, body, **kw: sent.append((method, path)))
    export, ledger = "/admin/export.json", "/admin/enrichment/ledger.json"
    backend.admin("GET", export)
    backend.admin("POST", ledger, {"entries": []})
    (tmp_path / ".rimborso-enrichment-control").write_text("postgresql-trial", encoding="ascii")
    backend.admin("GET", export)
    with pytest.raises(ValueError, match="рабочую папку"):
        backend.admin("POST", ledger, {"entries": []})
    assert sent == [("GET", export), ("POST", ledger), ("GET", export)]
