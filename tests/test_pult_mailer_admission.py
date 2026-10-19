"""Scheduled desktop mail admission without a real Outlook, server, or recipient."""
from datetime import datetime
from pathlib import Path
import sys

# conftest also prepends pult/ for its standalone helpers; package imports need root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pult import desk_backend as backend
from pult.core import mailer


NOW = datetime(2026, 9, 29, 19, 45)


class MemoryJournal:
    def __init__(self):
        self.jobs = {}

    def load(self, jid, profile, fingerprint=None):
        row = self.jobs.get(jid)
        if row is not None:
            assert row[1] == profile and (fingerprint is None or row[2] == fingerprint)
            return row[0].copy()
        return None

    def save(self, job, profile, fingerprint):
        self.jobs[job["id"]] = (job.copy(), profile, fingerprint)


class ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target

    def start(self):
        self.target()


def _setup(monkeypatch):
    cfg = mailer.config({"enabled": True, "time": "19:30", "days": [1],
                         "recipients": [{"email": "synthetic@example.invalid"}]})
    journal = MemoryJournal()
    monkeypatch.setattr(backend, "_jobs", {})
    monkeypatch.setattr(backend, "_journal", journal)
    monkeypatch.setattr(backend, "job_profile", lambda: "synthetic-profile")
    monkeypatch.setattr(backend, "_mail_cfg", lambda: cfg.copy())

    def save(updated):
        cfg.clear()
        cfg.update(updated)

    monkeypatch.setattr(backend, "_mail_save", save)
    monkeypatch.setattr(backend.threading, "Thread", ImmediateThread)
    return cfg, journal


def test_busy_admission_leaves_mail_due_then_sends_once(monkeypatch, tmp_path):
    cfg, journal = _setup(monkeypatch)
    class Clock:
        @staticmethod
        def now():
            return NOW
    monkeypatch.setattr(backend, "datetime", Clock)
    # Hold an unrelated job; no mail job should be persisted while it is running.
    backend._jobs["other"] = {"state": "running"}
    monkeypatch.setattr(backend.api_client, "download", lambda *a, **kw: b"synthetic book")
    monkeypatch.setattr(backend, "_profile", lambda: {"url": "https://invalid.example", "device_token": "synthetic"})
    monkeypatch.setattr(mailer, "summary_from_book", lambda _: [])
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    sent = []

    def fake_send(to, subject, body, attachment, **kw):
        assert to == ["synthetic@example.invalid"]
        assert Path(attachment).read_bytes() == b"synthetic book"
        sent.append(kw["operation_id"])

    monkeypatch.setattr(mailer, "send_outlook", fake_send)
    backend._schedule_due_mail(NOW)
    assert not sent and not journal.jobs and cfg["last"] == {} and cfg["history"] == []
    assert mailer.due(cfg, NOW) == ["day"]

    backend._jobs.pop("other")
    backend._schedule_due_mail(NOW)
    backend._schedule_due_mail(NOW)
    assert len(sent) == 1 and cfg["last"] == {"day": NOW.date().isoformat()}
    assert len(cfg["history"]) == 1 and cfg["history"][0]["ok"] is True
    assert len(journal.jobs) == 1 and next(iter(journal.jobs.values()))[0]["state"] == "done"
    assert mailer.due(cfg, NOW) == []


def test_journal_save_failure_before_worker_does_not_consume_day(monkeypatch):
    cfg, journal = _setup(monkeypatch)
    failures = []
    original_save = journal.save

    def refuse_save(*args):
        failures.append(1)
        raise OSError("synthetic journal unavailable")

    monkeypatch.setattr(journal, "save", refuse_save)
    monkeypatch.setattr(backend.traceback, "print_exc", lambda: None)
    backend._schedule_due_mail(NOW)
    assert failures == [1] and cfg["last"] == {} and cfg["history"] == []
    assert mailer.due(cfg, NOW) == ["day"]

    monkeypatch.setattr(journal, "save", original_save)
    sent = []
    def synthetic_send(book, *, manual, say, operation_id):
        sent.append(operation_id)
        backend._mail_save(mailer.remember(backend._mail_cfg(), book, ok=True,
                                          when=NOW, to=["synthetic@example.invalid"], manual=manual))
    monkeypatch.setattr(backend, "mail_send", synthetic_send)
    backend._schedule_due_mail(NOW)
    backend._schedule_due_mail(NOW)
    assert len(sent) == 1 and cfg["last"] == {"day": NOW.date().isoformat()}
    assert len(cfg["history"]) == 1


def test_uncertain_admitted_send_keeps_durable_id_without_retry(monkeypatch):
    cfg, journal = _setup(monkeypatch)
    attempted = []

    def ambiguous_send(*args, **kwargs):
        attempted.append(kwargs["operation_id"])
        raise RuntimeError("synthetic Outlook outcome unknown")

    monkeypatch.setattr(backend, "mail_send", ambiguous_send)
    backend._schedule_due_mail(NOW)
    backend._schedule_due_mail(NOW)
    assert len(attempted) == 1
    assert cfg["last"] == {} and mailer.due(cfg, NOW) == ["day"]
    assert next(iter(journal.jobs.values()))[0]["state"] == "uncertain"
    assert attempted[0] == next(iter(journal.jobs))
