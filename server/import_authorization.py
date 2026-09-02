"""Локальный регистратор разрешений на импорт реестра.

Команда намеренно не имеет HTTP-входа и не запускает ``registry_cycle``.
Она работает только с ``db.DB_PATH`` текущего hardbot runtime:

    python import_authorization.py register \
        --source-kind published_master --proof /run/server/proof.json
    python import_authorization.py status \
        --source-kind published_master --proof-digest <sha256>
    python import_authorization.py clear-pending --confirm-clear-pending

В stdout/stderr выводятся только JSON-сводки без содержимого proof и пути к БД.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any

import db
from exchange_contract import ExchangeContractError


MAX_PROOF_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class CliBlocked(Exception):
    """Ожидаемый fail-closed отказ без утечки входного proof."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise CliBlocked(
            "usage_error",
            "invalid command arguments; use --help for the exact interface",
        )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    register = commands.add_parser(
        "register",
        help="validate and pre-register one exact proof",
    )
    register.add_argument("--source-kind", required=True)
    register.add_argument("--proof", required=True)
    register.add_argument("--ttl-hours", default="24")

    status = commands.add_parser(
        "status",
        help="read one authorization by its proof digest",
    )
    status.add_argument("--source-kind", required=True)
    status.add_argument("--proof-digest", required=True)

    clear = commands.add_parser(
        "clear-pending",
        help="invalidate every pending authorization; preserve consumed audit",
    )
    clear.add_argument(
        "--confirm-clear-pending",
        action="store_true",
        help="required destructive-action confirmation",
    )
    return parser


def _exact_source_kind(value: Any) -> str:
    if not isinstance(value, str) or value not in db.IMPORT_SOURCE_KINDS:
        allowed = ", ".join(sorted(db.IMPORT_SOURCE_KINDS))
        raise CliBlocked(
            "source_kind_invalid",
            f"source-kind must exactly equal one of: {allowed}",
        )
    return value


def _ttl_hours(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CliBlocked(
            "ttl_invalid",
            "ttl-hours must be an integer between 1 and 168",
        ) from exc
    if str(parsed) != str(value) or parsed < 1 or parsed > 168:
        raise CliBlocked(
            "ttl_invalid",
            "ttl-hours must be an integer between 1 and 168",
        )
    return parsed


def _proof_digest(value: Any) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise CliBlocked(
            "proof_digest_invalid",
            "proof-digest must be an exact lowercase SHA-256",
        )
    return value


def _is_reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _regular_file(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not _is_reparse_point(info)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _stable_metadata(info: os.stat_result) -> tuple[Any, ...]:
    return (
        stat.S_IFMT(info.st_mode),
        *_identity(info),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _read_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, MAX_PROOF_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_PROOF_BYTES:
            raise CliBlocked(
                "proof_file_too_large",
                "proof file exceeds the 1 MiB safety limit",
            )
    return b"".join(chunks)


def _read_stable_regular_file(path_value: str) -> bytes:
    """Read exact bytes twice through one descriptor and reject path races."""

    path = Path(path_value)
    fd: int | None = None
    try:
        before = os.lstat(path)
        if not _regular_file(before):
            raise CliBlocked(
                "proof_file_not_regular",
                "proof must be a regular non-symlink file",
            )
        if before.st_size > MAX_PROOF_BYTES:
            raise CliBlocked(
                "proof_file_too_large",
                "proof file exceeds the 1 MiB safety limit",
            )

        flags = os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        if (
            not _regular_file(opened)
            or _identity(opened) != _identity(before)
        ):
            raise CliBlocked(
                "proof_file_changed",
                "proof file changed while it was being opened",
            )

        first = _read_fd(fd)
        first_end = os.fstat(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        second = _read_fd(fd)
        second_end = os.fstat(fd)
        after = os.lstat(path)

        descriptor_expected = _stable_metadata(opened)
        path_expected = _stable_metadata(before)
        if (
            first != second
            or _stable_metadata(first_end) != descriptor_expected
            or _stable_metadata(second_end) != descriptor_expected
            or not _regular_file(after)
            or _stable_metadata(after) != path_expected
        ):
            raise CliBlocked(
                "proof_file_changed",
                "proof file changed while it was being read",
            )
        return first
    except CliBlocked:
        raise
    except OSError as exc:
        raise CliBlocked(
            "proof_file_unavailable",
            "proof file cannot be opened as a stable regular file",
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)


def _proof_json(path_value: str) -> dict[str, Any]:
    raw = _read_stable_regular_file(path_value)

    def exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CliBlocked(
                    "proof_json_invalid",
                    "proof JSON contains a duplicate object key",
                )
            result[key] = value
        return result

    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=exact_object)
    except CliBlocked:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliBlocked(
            "proof_json_invalid",
            "proof must contain one valid UTF-8 JSON object",
        ) from exc
    if not isinstance(payload, dict):
        raise CliBlocked(
            "proof_json_invalid",
            "proof must contain one valid UTF-8 JSON object",
        )
    return payload


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "register":
        source_kind = _exact_source_kind(args.source_kind)
        proof = _proof_json(args.proof)
        authorization = db.register_import_authorization(
            proof,
            source_kind=source_kind,
            ttl_hours=_ttl_hours(args.ttl_hours),
        )
        return {
            "ok": True,
            "command": "register",
            "authorization": authorization,
        }

    if args.command == "status":
        source_kind = _exact_source_kind(args.source_kind)
        digest = _proof_digest(args.proof_digest)
        authorization = db.import_authorization_status(digest)
        if (
            authorization is not None
            and authorization["source_kind"] != source_kind
        ):
            raise CliBlocked(
                "source_kind_mismatch",
                "stored authorization belongs to another source-kind",
            )
        return {
            "ok": True,
            "command": "status",
            "found": authorization is not None,
            "authorization": authorization,
        }

    if not args.confirm_clear_pending:
        raise CliBlocked(
            "confirmation_required",
            "clear-pending requires --confirm-clear-pending",
        )
    cleared = db.clear_pending_import_authorizations()
    return {
        "ok": True,
        "command": "clear-pending",
        "cleared": cleared,
    }


def _emit(payload: dict[str, Any], *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=stream,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        _emit(_dispatch(args))
        return 0
    except CliBlocked as exc:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            },
            error=True,
        )
        return 2
    except ExchangeContractError:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "proof_rejected",
                    "message": "proof does not satisfy the active exchange contract",
                },
            },
            error=True,
        )
        return 2
    except sqlite3.Error:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "database_error",
                    "message": "local authorization database operation failed",
                },
            },
            error=True,
        )
        return 3
    except Exception:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "authorization command failed",
                },
            },
            error=True,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
