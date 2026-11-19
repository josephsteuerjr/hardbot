"""Add the working CRM database and its immutable documents to a private backup."""
import argparse
from contextlib import closing
from hashlib import file_digest
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from urllib.parse import urlsplit


def pg_command(container, *args):
    return ["docker", "exec", "-i", container, "sh", "-ec",
            'IFS= read -r PGPASSWORD < /run/secrets/postgres-password || [ -n "$PGPASSWORD" ]; '
            'export PGPASSWORD; exec "$@"', "crm-backup", *args]


def files_from_dump(archive, container):
    with archive.open("rb") as dump:
        output = subprocess.check_output(
            ["docker", "exec", "-i", container, "pg_restore", "--data-only",
             "--table=crm_documents", "--file=-"], stdin=dump)
    copying = False
    seen = False
    for line in output.splitlines():
        if line.startswith(b"COPY public.crm_documents ("):
            columns = line.split(b"(", 1)[1].split(b")", 1)[0].split(b", ")
            sha_column, size_column = columns.index(b"sha256"), columns.index(b"bytes")
            copying = seen = True
        elif copying and line == b"\\.":
            copying = False
        elif copying:
            fields = line.split(b"\t")
            digest = fields[sha_column].decode("ascii")
            if not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("Invalid document digest in CRM backup")
            yield digest, int(fields[size_column])
    if not seen or copying:
        raise ValueError("Incomplete CRM document directory in database backup")


def copy_sqlite(source, target):
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as live, \
            closing(sqlite3.connect(target)) as backup:
        live.backup(backup, pages=2048)
        if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Invalid SQLite backup")


def backup(args):
    os.umask(0o077)
    database = urlsplit(args.dsn_file.read_text().strip()).path.lstrip("/")
    if not re.fullmatch(r"[a-zA-Z0-9_]+", database):
        raise ValueError("Invalid working database name")
    target = args.destination / "crm-postgres"
    target.mkdir()
    dump = target / "database.dump"
    with dump.open("xb") as output:
        subprocess.run(pg_command(args.container, "pg_dump", "-U", "postgres",
                                  "--format=custom", "--dbname", database), stdout=output, check=True)
    with dump.open("rb") as source:
        listing = subprocess.check_output(["docker", "exec", "-i", args.container,
                                           "pg_restore", "--list"], stdin=source)
    if b"crm_registry migration_receipt" not in listing or b"public crm_events" not in listing:
        raise ValueError("Backup lacks the active registry or CRM events")
    docs = target / "documents"
    docs.mkdir()
    for digest, size in set(files_from_dump(dump, args.container)):
        copy = docs / digest
        shutil.copyfile(args.documents / digest, copy)
        with copy.open("rb") as row:
            if copy.stat().st_size != size or file_digest(row, "sha256").hexdigest() != digest:
                raise ValueError("Invalid document content")
    if args.mobile_state:
        mobile = target / "mobile-state"
        mobile.mkdir()
        for source in args.mobile_state.rglob("*"):
            if source.is_symlink():
                raise ValueError("Unexpected symlink in private mobile state")
            if not source.is_file() or source.name.endswith((".lock", "-wal", "-shm", "-journal", ".tmp", ".part")):
                continue
            copy = mobile / source.relative_to(args.mobile_state)
            copy.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as f:
                is_sqlite = f.read(16) == b"SQLite format 3\x00"
            if is_sqlite:
                copy_sqlite(source, copy)
            else:
                shutil.copyfile(source, copy)
    (target / "database-name").write_text(database + "\n", encoding="ascii")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--dsn-file", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--documents", required=True, type=Path)
    parser.add_argument("--mobile-state", type=Path)
    backup(parser.parse_args())
