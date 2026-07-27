"""Технические уведомления только владельцу через admin API."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigBundle
from .errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class NotificationResult:
    sent: bool
    skipped: bool = False
    detail: str = ""


class OwnerNotifier:
    def __init__(self, bundle: ConfigBundle):
        self.bundle = bundle
        config = bundle.app.notifications
        self.enabled = bool(config.get("enabled", False))
        self.audience = str(config.get("audience", ""))
        self.endpoint = str(config.get("endpoint", "/admin/notify"))
        self.settings_path = Path(
            bundle.render(str(config.get("settings_file", "")))
        )
        if self.audience != "owner_only":
            raise ConfigurationError(
                "технические уведомления разрешены только audience=owner_only"
            )
        if not self.endpoint.startswith("/"):
            raise ConfigurationError("notifications.endpoint должен начинаться с /")

    def send(self, text: str, *, timeout: int = 30) -> NotificationResult:
        if not self.enabled:
            return NotificationResult(False, skipped=True, detail="disabled")
        if not self.settings_path.is_file():
            return NotificationResult(
                False,
                detail=f"нет settings-файла: {self.settings_path}",
            )
        try:
            settings = json.loads(
                self.settings_path.read_text(encoding="utf-8")
            )
            url = str(settings["url"]).rstrip("/") + self.endpoint
            payload = json.dumps({"text": text[:3500]}).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "X-Admin-Key": str(settings.get("key", "")),
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
            return NotificationResult(True, detail="owner_only")
        except Exception as exc:  # noqa: BLE001 — уведомление не портит транзакцию
            return NotificationResult(False, detail=str(exc))
