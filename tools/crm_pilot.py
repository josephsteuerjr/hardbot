"""Offline pilot preparation. Creates a separate database; never deploys a service."""

from pathlib import Path
import argparse, hashlib, json, os, shutil, sqlite3, sys, time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT)]


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
    os.replace(temp, path)


def fingerprints(conn):
    result = {}
    for table in ("clients", "calls", "visits"):
        columns = [r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")]
        hasher = hashlib.sha256()
        count = 0
        for row in conn.execute(
            "SELECT "
            + ",".join('"' + c + '"' for c in columns)
            + " FROM "
            + table
            + " ORDER BY id"
        ):
            hasher.update(
                json.dumps(
                    list(row), ensure_ascii=False, separators=(",", ":"), default=str
                ).encode()
            )
            hasher.update(b"\n")
            count += 1
        result[table] = dict(columns=columns, count=count, sha256=hasher.hexdigest())
    return result


def verify_original(conn, before):
    for table, snapshot in before.items():
        hasher = hashlib.sha256()
        count = 0
        for row in conn.execute(
            "SELECT "
            + ",".join('"' + c + '"' for c in snapshot["columns"])
            + " FROM "
            + table
            + " ORDER BY id"
        ):
            hasher.update(
                json.dumps(
                    list(row), ensure_ascii=False, separators=(",", ":"), default=str
                ).encode()
            )
            hasher.update(b"\n")
            count += 1
        if count != snapshot["count"] or hasher.hexdigest() != snapshot["sha256"]:
            raise RuntimeError("Legacy rows changed: " + table)


def prepare(args):
    source = Path(args.source).resolve(strict=True)
    destination = Path(args.destination).resolve()
    if source == destination or destination.exists():
        raise RuntimeError("Choose a new separate destination file")
    if str(destination).startswith(("\\\\", "//")) or destination.drive.upper() == "U:":
        raise RuntimeError("Use a local preparation directory, not a corporate share")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        shutil.disk_usage(destination.parent).free
        < source.stat().st_size * 2 + 150_000_000
    ):
        raise RuntimeError(
            "Insufficient free space for an isolated database and migration"
        )
    mapping = (
        json.loads(Path(args.mapping).read_text("utf-8"))
        if args.mapping
        else {"source": "Read-only copy inspection; no staff/person links approved"}
    )
    receipt = destination.with_suffix(".preparation.json")
    original = sha(source)
    save(
        receipt,
        dict(
            stage="copy_intent",
            source=str(source),
            source_sha256=original,
            destination=str(destination),
            production_changed=False,
        ),
    )
    incoming = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    outgoing = sqlite3.connect(destination)
    try:
        before = fingerprints(incoming)
        incoming.backup(outgoing)
        assert outgoing.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        incoming.close()
        outgoing.close()
    import db
    from crm import migrate, store, analytics
    from crm.access import Principal

    db.DB_PATH = destination
    db.init_db()
    with db.connect() as conn:
        verify_original(conn, before)
    started = time.perf_counter()
    result = migrate.apply(mapping)
    with db.connect() as conn:
        verify_original(conn, before)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        counts = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT kind,COUNT(*) FROM crm_migration_issues GROUP BY kind"
            )
        }
        assert (
            conn.execute("SELECT COUNT(*) FROM crm_events").fetchone()[0]
            == before["calls"]["count"] + before["visits"]["count"]
        )
    if sha(source) != original:
        raise RuntimeError(
            "Source changed during preparation; inspect snapshot custody"
        )
    assert migrate.apply(mapping) == result
    outcome = dict(
        stage="verified",
        source_sha256=original,
        destination_sha256=sha(destination),
        legacy=before,
        migration=result,
        issue_counts=counts,
        elapsed_seconds=round(time.perf_counter() - started, 3),
        production_changed=False,
        cohort_enrolled=False,
    )
    save(receipt, outcome)
    print(
        json.dumps(
            {
                key: outcome[key]
                for key in (
                    "stage",
                    "issue_counts",
                    "elapsed_seconds",
                    "production_changed",
                    "cohort_enrolled",
                )
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare")
    create.add_argument("--source", required=True)
    create.add_argument("--destination", required=True)
    create.add_argument("--mapping")
    rebuild = commands.add_parser("rebuild")
    rebuild.add_argument("--database", required=True)
    rollback = commands.add_parser("release-legacy-guard")
    rollback.add_argument("--database", required=True)
    rollback.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        return prepare(args)
    import db
    from crm import store, analytics, legacy
    from crm.access import Principal

    db.DB_PATH = Path(args.database).resolve(strict=True)
    operator = Principal(
        0, "local-operator", "maintenance", "owner", frozenset(), frozenset()
    )
    if args.command == "rebuild":
        result = store.rebuild(operator)
        count = analytics.aggregate(rebuild=True)
        while True:
            batch = analytics.aggregate()
            count += batch
            if not batch:
                break
        print(
            json.dumps(
                {"stage": "verified", "projection": result, "metric_events": count}
            )
        )
        return
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'crm_guard_%'"
        ).fetchone()[0]
        if args.apply:
            backup = db.DB_PATH.with_name(
                db.DB_PATH.name
                + ".before-guard-release-"
                + time.strftime("%Y%m%dT%H%M%S")
            )
            target = sqlite3.connect(backup)
            try:
                conn.backup(target)
            finally:
                target.close()
            legacy.release(conn)
            store.audit(
                conn, operator, "legacy_guard_released", "crm", {"backup": backup.name}
            )
            conn.commit()
    print(
        json.dumps({"guards": count, "applied": args.apply, "history_deleted": False})
    )


if __name__ == "__main__":
    main()
