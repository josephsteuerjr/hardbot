"""Согласованный SQLite backup + неизменяемые фото + проверяемая опись.

CLI не заменяет рабочую установку: restore требует пустой новый каталог.
Копию можно передать независимому хранилищу и проверить без исходного узла.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite quick_check failed")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        result = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            rows = [json.dumps(list(r), ensure_ascii=False, default=str) for r in conn.execute("SELECT * FROM " + quoted)]
            result[table] = {"rows": len(rows), "sha256": hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()}
        return result


def private_tree(path):
    for current in [Path(path), *Path(path).rglob("*")]:
        if current.is_symlink():
            raise ValueError("Symlink is not allowed in a recovery bundle")
        os.chmod(current, 0o700 if current.is_dir() else 0o600)
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW
        owner = subprocess.run(["whoami.exe"], check=True, capture_output=True, text=True, creationflags=flags).stdout.strip()
        subprocess.run(["icacls.exe", str(path), "/reset", "/T", "/C"], check=True, capture_output=True, creationflags=flags)
        subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r", owner + ":F",
                        "*S-1-5-18:F", "/T", "/C"], check=True, capture_output=True, creationflags=flags)
        subprocess.run(["icacls.exe", str(path), "/grant:r", owner + ":(OI)(CI)F",
                        "*S-1-5-18:(OI)(CI)F"], check=True, capture_output=True, creationflags=flags)


def create(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination == source or source in destination.parents:
        raise ValueError("Backup must be outside the source data directory")
    if destination.exists():
        raise ValueError("Destination already exists")
    required = (source / "hardbot.db").stat().st_size * 2
    spool = source / "audit-spool.sqlite3"
    if spool.exists():
        required += spool.stat().st_size * 2
    for directory in ("photos", "receipts"):
        origin = source / directory
        if origin.is_symlink():
            raise ValueError("Symlink in source assets")
        if origin.exists():
            for member in origin.rglob("*"):
                if member.is_symlink():
                    raise ValueError("Symlink in source assets")
                if member.is_file():
                    required += member.stat().st_size
    if shutil.disk_usage(destination.parent).free < required:
        raise ValueError("Not enough space for a verified backup")
    destination.mkdir(mode=0o700)
    try:
        # Audit moves only spool -> primary. Snapshot spool first so a drain
        # between snapshots leaves at least one copy of every earlier event.
        # Duplicate event IDs are safe: audit_spool.drain uses INSERT OR IGNORE.
        if (source / "audit-spool.sqlite3").exists():
            with closing(sqlite3.connect((source / "audit-spool.sqlite3").as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(destination / "audit-spool.sqlite3")) as dst:
                    src.backup(dst)
                    dst.commit()
        with closing(sqlite3.connect((source / "hardbot.db").as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(destination / "hardbot.db")) as dst:
                src.backup(dst)
                dst.commit()
        for directory in ("photos", "receipts"):
            origin = source / directory
            if origin.exists():
                if origin.is_symlink() or any(p.is_symlink() for p in origin.rglob("*")):
                    raise ValueError("Symlink in source assets")
                shutil.copytree(origin, destination / directory)
        files = {p.relative_to(destination).as_posix(): {"size": p.stat().st_size, "sha256": digest(p)}
                 for p in destination.rglob("*") if p.is_file()}
        manifest = {"schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                    "tables": inventory(destination / "hardbot.db"), "files": files,
                    "activation": "isolated; external delivery disabled until recovery is reconciled"}
        (destination / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        private_tree(destination)
        verify(destination)
        return manifest
    except Exception:
        # Preserve incomplete evidence for diagnosis; never label it verified.
        (destination / "INCOMPLETE").touch()
        raise


def verify(bundle):
    bundle = Path(bundle).resolve()
    if any(p.is_symlink() for p in bundle.rglob("*")):
        raise ValueError("Symlink in recovery bundle")
    manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or (bundle / "INCOMPLETE").exists():
        raise ValueError("Incomplete or unknown recovery bundle")
    for name, expected in manifest["files"].items():
        path = (bundle / name).resolve()
        if bundle not in path.parents or path.is_symlink() or not path.is_file():
            raise ValueError("Unsafe or missing bundle member")
        if path.stat().st_size != expected["size"] or digest(path) != expected["sha256"]:
            raise ValueError("Bundle file checksum mismatch: " + name)
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file() and p.name != "MANIFEST.json"}
    if actual != set(manifest["files"]):
        raise ValueError("Unexpected recovery bundle files")
    if inventory(bundle / "hardbot.db") != manifest["tables"]:
        raise ValueError("Database inventory mismatch")
    return manifest


def restore(bundle, destination):
    manifest = verify(bundle)
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Restore requires a new empty destination")
    shutil.copytree(bundle, destination)
    private_tree(destination)
    verify(destination)
    return manifest


def retention_plan(root, *, daily=14, weekly=4):
    """Read-only plan: never deletes the sole or an unverified copy."""
    if daily < 1 or weekly < 0:
        raise ValueError("retention")
    items = []
    for directory in Path(root).iterdir():
        if directory.is_dir() and not directory.is_symlink() and (directory / "MANIFEST.json").exists():
            manifest = verify(directory)
            items.append((datetime.fromisoformat(manifest["created_at"]), directory))
    items.sort(reverse=True)
    keep = {p for _, p in items[:daily]}
    weeks = set()
    for stamp, directory in items:
        week = stamp.isocalendar()[:2]
        if week not in weeks and len(weeks) < weekly:
            weeks.add(week); keep.add(directory)
    return {"keep": [str(p) for _, p in items if p in keep],
            "eligible_after_remote_verification": [str(p) for _, p in items if p not in keep]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "verify", "restore"))
    parser.add_argument("source")
    parser.add_argument("destination", nargs="?")
    args = parser.parse_args()
    if args.action == "verify":
        result = verify(args.source)
    elif not args.destination:
        parser.error("destination is required")
    else:
        result = (create if args.action == "create" else restore)(args.source, args.destination)
    print(json.dumps({"verified": True, "created_at": result["created_at"], "tables": result["tables"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
