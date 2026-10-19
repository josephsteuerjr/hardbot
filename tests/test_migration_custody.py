"""Isolated custody regressions: no Docker, production paths or network.

The shell test executes the actual receive function with stubbed container/compose
boundaries and private temp data, never the deployed script on owner data.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/export_for_handover.py"
MIGRATE = ROOT / "deploy/units/core/migrate.sh"


def _bash():
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    git = shutil.which("git")
    candidates = ([Path(git).resolve().parents[1] / "bin/bash.exe"] if git else [])
    candidates += [Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe")),
                   Path("C:/Program Files/Git/bin/bash.exe")]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    pytest.skip("No Git Bash / Unix bash available")


def tool():
    spec = importlib.util.spec_from_file_location("export_for_handover_custody", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_tool(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                          capture_output=True, env={**os.environ, "APP_DATA": "/unused-synthetic",
                                                     "PYTHONIOENCODING": "utf-8"})


def db_at(root, *, account=False, receipt=False):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "hardbot.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE clients(contract_id TEXT)")
        conn.execute("CREATE TABLE visits(id INTEGER)")
        conn.execute("CREATE TABLE accounts(uid INTEGER)")
        conn.execute("CREATE TABLE payment_evidence(file TEXT,sha256 TEXT,size_bytes INTEGER)")
        conn.execute("CREATE TABLE settings(key TEXT,value TEXT)")
        if account:
            conn.execute("INSERT INTO accounts VALUES (31)")
        if receipt:
            file = root / "receipts/KD-1/proof.jpg"
            file.parent.mkdir(parents=True)
            file.write_bytes(b"synthetic receipt bytes")
            conn.execute("INSERT INTO payment_evidence VALUES (?,?,?)",
                         ("KD-1/proof.jpg", tool().sha256_file(file), file.stat().st_size))
    return path


def test_state_counts_accounts_without_clients_visits_or_photos(tmp_path):
    data = tmp_path / "data"
    db_at(data, account=True)
    result = run_tool("state", "--data", data)
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().startswith("занято")
    (data / "hardbot.db").write_bytes(b"not sqlite")
    result = run_tool("state", "--data", data)
    assert result.returncode != 0
    assert "пусто" not in result.stdout.decode()


def test_state_rejects_orphan_sidecar_and_receipts(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "hardbot.db-wal").write_bytes(b"orphan WAL")
    assert run_tool("state", "--data", data).stdout.decode().startswith("занято")
    (data / "hardbot.db-wal").unlink()
    (data / "receipts/KD").mkdir(parents=True)
    (data / "receipts/KD/x.jpg").write_bytes(b"proof")
    assert run_tool("state", "--data", data).stdout.decode().startswith("занято")


def test_full_archive_carries_and_verifies_payment_receipt(tmp_path):
    data, work = tmp_path / "data", tmp_path / "work"
    db_at(data, receipt=True)
    taken = run_tool("take", "--data", data, "--work", work)
    assert taken.returncode == 0, taken.stderr.decode()
    archive = tmp_path / "handover.tar"
    archive.write_bytes(taken.stdout)
    extracted = tmp_path / "extract"
    extracted.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(extracted, filter="data")
    root = extracted / "hard-handover"
    assert (root / "receipts/KD-1/proof.jpg").read_bytes() == b"synthetic receipt bytes"
    assert "receipts/KD-1/proof.jpg" in (root / "SHA256SUMS").read_text()
    good = run_tool("verify", "--dir", root)
    assert good.returncode == 0, good.stdout.decode() + good.stderr.decode()
    (root / "receipts/KD-1/proof.jpg").write_bytes(b"corrupt")
    assert run_tool("verify", "--dir", root).returncode != 0
    (root / "receipts/KD-1/proof.jpg").write_bytes(b"synthetic receipt bytes")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    del manifest["чеки"]
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    old = run_tool("verify", "--dir", root)
    assert old.returncode != 0
    assert "старый архив" in old.stdout.decode()


def test_legacy_full_archive_without_payment_evidence_still_verifies(tmp_path):
    data = tmp_path / "data"
    db_at(data)
    taken = run_tool("take", "--data", data, "--work", tmp_path / "work")
    assert taken.returncode == 0, taken.stderr.decode()
    root = tmp_path / "old/hard-handover"
    root.parent.mkdir()
    with tarfile.open(fileobj=io.BytesIO(taken.stdout)) as tar:
        tar.extractall(root.parent, filter="data")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    del manifest["чеки"]
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    checked = run_tool("verify", "--dir", root)
    assert checked.returncode == 0, checked.stdout.decode() + checked.stderr.decode()


def test_db_only_refuses_payment_evidence_and_pending_spool(tmp_path):
    data = tmp_path / "data"
    db_at(data, receipt=True)
    result = run_tool("take", "--data", data, "--work", tmp_path / "work", "--db-only")
    assert result.returncode != 0 and b"" == result.stdout
    assert "--db-only" in result.stderr.decode()
    with closing(sqlite3.connect(data / "audit-spool.sqlite3")) as conn, conn:
        conn.execute("CREATE TABLE events(id TEXT)")
        conn.execute("INSERT INTO events VALUES ('synthetic-event')")
    result = run_tool("take", "--data", data, "--work", tmp_path / "work")
    assert result.returncode != 0 and b"" == result.stdout
    assert "audit-spool" in result.stderr.decode()


def receive_harness(tmp_path, *, occupied=True, replace=False, with_receipts=False):
    """Actual receive shell body, synthetic mounted data and fake Docker calls."""
    unit = tmp_path / "unit"
    data = unit / "data"
    db_at(data, account=occupied)
    old = (data / "hardbot.db").read_bytes()
    (data / "hardbot.db-wal").write_bytes(b"synthetic wal sidecar")
    (data / "hardbot.db-shm").write_bytes(b"synthetic shm sidecar")
    if with_receipts:
        (data / "receipts/OLD").mkdir(parents=True)
        (data / "receipts/OLD/old.jpg").write_bytes(b"old synthetic receipt")
    unit.mkdir(exist_ok=True)
    incoming = tmp_path / "incoming.db"
    with closing(sqlite3.connect(incoming)) as conn, conn:
        conn.execute("CREATE TABLE clients(contract_id TEXT)")
        conn.execute("INSERT INTO clients VALUES ('new')")
    incoming_receipts = tmp_path / "incoming-receipts"
    if with_receipts:
        (incoming_receipts / "KD-NEW").mkdir(parents=True)
        (incoming_receipts / "KD-NEW/new.jpg").write_bytes(b"new synthetic receipt")
    script = MIGRATE.read_text(encoding="utf-8")
    body = "cmd_receive() {" + script.split("cmd_receive() {", 1)[1].split(
        "\n# ----------------------------------------------------------------------------", 1)[0]
    stub = r'''
set -euo pipefail
say() { :; }
ok() { :; }
warn() { :; }
die() { printf '%s\n' "$*" >&2; exit 91; }
need_tool() { :; }
need_image() { :; }
check_docker() { :; }
unpack_and_verify() {
  mkdir -p "$UNPACK_DIR/$ARCHIVE_ROOT"
  cp "$FIXTURE_DB" "$UNPACK_DIR/$ARCHIVE_ROOT/hardbot.db"
  [ -d "$FIXTURE_RECEIPTS" ] && cp -a "$FIXTURE_RECEIPTS" "$UNPACK_DIR/$ARCHIVE_ROOT/receipts"
  return 0
}
in_app() { if [ "$OCCUPIED" = 1 ]; then printf 'занято\n'; else printf 'пусто\n'; fi; }
in_app_with_archive() { :; }
compose() { printf '%s\n' "$*" >> "$COMPOSE_LOG"; }
wait_healthy() { return 0; }
health_of() { echo healthy; }
'''
    env = {**os.environ, "UNIT_DIR": str(unit), "UNPACK_DIR": str(unit / ".migrate-in"),
           "ARCHIVE_ROOT": "hard-handover", "FIXTURE_DB": str(incoming),
           "FIXTURE_RECEIPTS": str(incoming_receipts),
           "TOOL": str(TOOL), "COMPOSE_LOG": str(tmp_path / "compose.log"),
           "UNIT_WAIT_SECONDS": "1", "OCCUPIED": "1" if occupied else "0"}
    harness = tmp_path / "receive-fixture.sh"
    harness.write_text(stub + "\n" + body + "\ncmd_receive synthetic.tar --yes " +
                       ("--replace" if replace else ""), encoding="utf-8")
    bash = _bash()
    result = subprocess.run([bash, str(harness)], env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    return result, unit, old


def test_receive_refuses_accounts_only_before_stop_and_preserves_wal(tmp_path):
    result, unit, old = receive_harness(tmp_path, occupied=True)
    assert result.returncode != 0
    assert not (tmp_path / "compose.log").exists(), result.stderr
    assert (unit / "data/hardbot.db").read_bytes() == old
    assert (unit / "data/hardbot.db-wal").read_bytes() == b"synthetic wal sidecar"
    assert (unit / "data/hardbot.db-shm").read_bytes() == b"synthetic shm sidecar"


def test_receive_replace_keeps_old_db_and_sidecars(tmp_path):
    result, unit, old = receive_harness(tmp_path, occupied=True, replace=True, with_receipts=True)
    assert result.returncode == 0, result.stderr
    aside = next((unit / "backups").glob("before-migration-*"))
    assert (aside / "hardbot.db").read_bytes() == old
    assert (aside / "hardbot.db-wal").read_bytes() == b"synthetic wal sidecar"
    assert (aside / "hardbot.db-shm").read_bytes() == b"synthetic shm sidecar"
    assert (aside / "receipts/OLD/old.jpg").read_bytes() == b"old synthetic receipt"
    assert (unit / "data/receipts/KD-NEW/new.jpg").read_bytes() == b"new synthetic receipt"
    with closing(sqlite3.connect(unit / "data/hardbot.db")) as conn, conn:
        assert conn.execute("SELECT contract_id FROM clients").fetchone() == ("new",)


def test_receive_empty_initialized_destination_preserves_its_old_db(tmp_path):
    result, unit, old = receive_harness(tmp_path, occupied=False)
    assert result.returncode == 0, result.stderr
    aside = next((unit / "backups").glob("before-migration-*"))
    assert (aside / "hardbot.db").read_bytes() == old
    assert (aside / "hardbot.db-wal").read_bytes() == b"synthetic wal sidecar"


def test_hosted_backup_restore_requires_complete_hash_manifest():
    backup = (ROOT / "deploy/units/core/backup.sh").read_text(encoding="utf-8")
    restore = (ROOT / "deploy/units/core/restore.sh").read_text(encoding="utf-8")
    assert backup.index("audit-spool.sqlite3.part") < backup.index("backup_sqlite hardbot.db")
    assert 'cp -a data/receipts "$dest/receipts"' in backup
    assert backup.index('mv "$dest/.SHA256SUMS.part"') > backup.index("data/receipts")
    assert restore.index('cmp -s "$src/SHA256SUMS"') < restore.index("compose stop app")
    assert restore.index('cp "$db_file" "$stage/hardbot.db"') < restore.index("compose stop app")
    assert 'mv "data/$asset" "$aside/$asset"' in restore
    assert 'rm -f data/hardbot.db-wal' not in restore


def restore_fixture(tmp_path, *, corrupted=False, with_photos=True, fail_start=False):
    """Run restore's actual shell body with a private core and inert Compose."""
    import hashlib
    unit = tmp_path / "core"
    src = unit / "backups/snapshot"
    src.mkdir(parents=True)
    db_at(src, receipt=True)
    if with_photos:
        (src / "photos/KD-1").mkdir(parents=True)
        (src / "photos/KD-1/visit.jpg").write_bytes(b"synthetic visit")
    with closing(sqlite3.connect(src / "audit-spool.sqlite3")) as conn, conn:
        conn.execute("CREATE TABLE events(id TEXT)")
        conn.execute("INSERT INTO events VALUES ('pending-synthetic')")
    files = sorted(p for p in src.rglob("*") if p.is_file())
    binary_mark = " *" if os.name == "nt" else "  "
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}{binary_mark}./{p.relative_to(src).as_posix()}\n"
                   for p in files)
    (src / "SHA256SUMS").write_bytes(sums.encode("utf-8"))
    if corrupted:
        (src / "receipts/KD-1/proof.jpg").write_bytes(b"changed")
    data = unit / "data"
    db_at(data, account=True)
    old = (data / "hardbot.db").read_bytes()
    (data / "hardbot.db-wal").write_bytes(b"synthetic wal before restore")
    (data / "hardbot.db-shm").write_bytes(b"synthetic shm before restore")
    (data / "receipts/OLD").mkdir(parents=True)
    (data / "receipts/OLD/old.jpg").write_bytes(b"old proof")
    (data / "photos/OLD").mkdir(parents=True)
    (data / "photos/OLD/old.jpg").write_bytes(b"old visit")
    script = (ROOT / "deploy/units/core/restore.sh").read_text(encoding="utf-8")
    body = 'src="${1:-}"' + script.split('src="${1:-}"', 1)[1]
    stub = r'''
set -euo pipefail
cd "$UNIT_DIR"
say() { :; }
ok() { :; }
warn() { :; }
die() { printf '%s\n' "$*" >&2; exit 91; }
need_cmd() { :; }
check_docker() { :; }
compose() {
  printf '%s\n' "$*" >> "$COMPOSE_LOG"
  if [ "${FAIL_START:-0}" = 1 ] && [ "$1" = start ]; then return 5; fi
}
wait_healthy() { return 0; }
health_of() { echo healthy; }
human_size() { echo bytes; }
'''
    harness = tmp_path / "restore-fixture.sh"
    harness.write_text(stub + "\n" + body, encoding="utf-8")
    bash = _bash()
    result = subprocess.run([bash, str(harness), "backups/snapshot", "--yes"],
                            env={**os.environ, "UNIT_DIR": str(unit),
                                 "COMPOSE_LOG": str(tmp_path / "restore-compose.log"),
                                 "FAIL_START": "1" if fail_start else "0"},
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result, unit, old


def test_restore_rejects_bad_receipt_hash_before_stop(tmp_path):
    result, unit, old = restore_fixture(tmp_path, corrupted=True)
    assert result.returncode != 0, result.stdout
    assert not (tmp_path / "restore-compose.log").exists()
    assert (unit / "data/hardbot.db").read_bytes() == old
    assert (unit / "data/hardbot.db-wal").read_bytes() == b"synthetic wal before restore"


def test_restore_stages_receipts_and_spool_and_preserves_old_raw_bytes(tmp_path):
    result, unit, old = restore_fixture(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (unit / "data/receipts/KD-1/proof.jpg").read_bytes() == b"synthetic receipt bytes"
    assert (unit / "data/photos/KD-1/visit.jpg").read_bytes() == b"synthetic visit"
    with closing(sqlite3.connect(unit / "data/audit-spool.sqlite3")) as conn, conn:
        assert conn.execute("SELECT id FROM events").fetchall() == [("pending-synthetic",)]
    aside = next((unit / "backups").glob("before-restore-*"))
    assert (aside / "hardbot.db").read_bytes() == old
    assert (aside / "hardbot.db-wal").read_bytes() == b"synthetic wal before restore"
    assert (aside / "receipts/OLD/old.jpg").read_bytes() == b"old proof"


def test_restore_db_only_snapshot_keeps_existing_photos(tmp_path):
    result, unit, old = restore_fixture(tmp_path, with_photos=False)
    assert result.returncode == 0, result.stderr
    assert (unit / "data/photos/OLD/old.jpg").read_bytes() == b"old visit"


def test_restore_failed_start_recovers_old_db_wal_and_receipts(tmp_path):
    result, unit, old = restore_fixture(tmp_path, fail_start=True)
    assert result.returncode != 0
    assert (unit / "data/hardbot.db").read_bytes() == old
    assert (unit / "data/hardbot.db-wal").read_bytes() == b"synthetic wal before restore"
    assert (unit / "data/receipts/OLD/old.jpg").read_bytes() == b"old proof"
    aside = next((unit / "backups").glob("before-restore-*"))
    assert (aside / "failed-publish/receipts/KD-1/proof.jpg").read_bytes() == b"synthetic receipt bytes"


def test_hosted_backup_captures_receipts_spool_and_manifest(tmp_path):
    """The actual backup shell body runs only against synthetic data/fake Compose."""
    unit = tmp_path / "core"
    data = unit / "data"
    db_at(data, receipt=True)
    (data / "photos/KD-1").mkdir(parents=True)
    (data / "photos/KD-1/visit.jpg").write_bytes(b"synthetic visit")
    with closing(sqlite3.connect(data / "audit-spool.sqlite3")) as conn, conn:
        conn.execute("CREATE TABLE events(id TEXT)")
        conn.execute("INSERT INTO events VALUES ('waiting-to-drain')")
    script = (ROOT / "deploy/units/core/backup.sh").read_text(encoding="utf-8")
    body = "umask 077" + script.split("umask 077", 1)[1]
    stub = r'''
set -euo pipefail
cd "$UNIT_DIR"
say() { :; }
ok() { :; }
warn() { :; }
die() { printf '%s\n' "$*" >&2; exit 91; }
check_docker() { :; }
need_cmd() { :; }
env_get() { :; }
human_size() { echo bytes; }
health_of() { echo нет; }
docker() { :; }
compose() {
  local name="${@: -1}"
  python -c 'import sqlite3,sys,os,tempfile
src=sqlite3.connect("file:"+os.path.join(sys.argv[1],sys.argv[2])+"?mode=ro",uri=True)
fd,tmp=tempfile.mkstemp(); os.close(fd)
dst=sqlite3.connect(tmp)
with dst: src.backup(dst)
dst.close(); src.close()
with open(tmp,"rb") as fp: sys.stdout.buffer.write(fp.read())
os.unlink(tmp)' "$UNIT_DIR/data" "$name"
}
'''
    harness = tmp_path / "backup-fixture.sh"
    harness.write_text(stub + "\n" + body, encoding="utf-8")
    bash = _bash()
    result = subprocess.run([bash, str(harness)], env={**os.environ, "UNIT_DIR": str(unit)},
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    dest = next((unit / "backups").glob("[0-9]*T[0-9]*"))
    assert (dest / "SHA256SUMS").is_file()
    assert (dest / "receipts/KD-1/proof.jpg").read_bytes() == b"synthetic receipt bytes"
    assert (dest / "photos/KD-1/visit.jpg").read_bytes() == b"synthetic visit"
    with closing(sqlite3.connect(dest / "audit-spool.sqlite3")) as conn, conn:
        assert conn.execute("SELECT id FROM events").fetchall() == [("waiting-to-drain",)]
    with closing(sqlite3.connect(dest / "hardbot.db")) as conn, conn:
        assert conn.execute("SELECT file FROM payment_evidence").fetchall() == [("KD-1/proof.jpg",)]
    verify = subprocess.run([bash, "-lc", f'cd "{dest.as_posix()}" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum'],
                            capture_output=True)
    assert verify.returncode == 0
    assert verify.stdout == (dest / "SHA256SUMS").read_bytes()
