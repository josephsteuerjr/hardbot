"""Постоянный журнал задач; прерванная запись не повторяется вслепую."""
import base64
import json
import os
import sqlite3
from pathlib import Path
from .settings import _dpapi


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, profile TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)")
        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def seal(self, data):
        text = json.dumps(data, ensure_ascii=False, default=str)
        return "dpapi:" + base64.b64encode(_dpapi(text.encode())).decode() if os.name == "nt" else text

    def open(self, text):
        if text.startswith("dpapi:"):
            text = _dpapi(base64.b64decode(text[6:]), decrypt=True).decode()
        return json.loads(text)

    def save(self, job, profile, fingerprint):
        with self.connect() as conn:
            old = conn.execute("SELECT profile,fingerprint FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if old and old != (profile, fingerprint):
                raise ValueError("ID задачи уже относится к другой операции или контуру")
            conn.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?)",
                         (job["id"], profile, fingerprint, job["state"], self.seal(job)))

    def load(self, jid, profile, fingerprint=None):
        with self.connect() as conn:
            row = conn.execute("SELECT profile,fingerprint,payload FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            return None
        if row[0] != profile or (fingerprint is not None and row[1] != fingerprint):
            raise ValueError("ID задачи относится к другому контуру или запросу")
        return self.open(row[2])

    def list(self, profile):
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM jobs WHERE profile=? ORDER BY rowid DESC LIMIT 100", (profile,)).fetchall()
        return [self.open(r[0]) for r in rows]

    def recover(self):
        with self.connect() as conn:
            rows = conn.execute("SELECT id,profile,fingerprint,payload FROM jobs WHERE state='running'").fetchall()
        for jid, profile, fingerprint, text in rows:
            job = self.open(text)
            job.update(state="uncertain", error="Программа завершилась во время операции. Сверьте результат перед повтором.")
            self.save(job, profile, fingerprint)
