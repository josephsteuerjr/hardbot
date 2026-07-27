import json
from dataclasses import replace

from registry_cycle.config import load_config_bundle
from registry_cycle.notifications import OwnerNotifier


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b'{"ok":true}'


def test_notification_uses_owner_only_admin_endpoint(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"url": "https://example.test", "key": "secret"}),
        encoding="utf-8",
    )
    bundle = load_config_bundle()
    notifications = dict(bundle.app.notifications)
    notifications.update(
        {
            "enabled": True,
            "audience": "owner_only",
            "settings_file": str(settings),
            "endpoint": "/admin/notify",
        }
    )
    bundle = replace(
        bundle,
        app=replace(bundle.app, notifications=notifications),
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["key"] = request.headers["X-admin-key"]
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(
        "registry_cycle.notifications.urllib.request.urlopen",
        fake_urlopen,
    )

    result = OwnerNotifier(bundle).send("технический отчёт")

    assert result.sent
    assert captured == {
        "url": "https://example.test/admin/notify",
        "key": "secret",
        "body": {"text": "технический отчёт"},
        "timeout": 30,
    }
