"""Fail-closed operator path for one contract-bound hardbot import.

This command does not register an authorization and does not invoke
``registry_cycle``.  The server-side root registrar must consume ``proof.json``
before ``apply`` can succeed.  Published-master preparation only verifies and
consumes an already completed registry-cycle publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO


PULT_DIR = Path(__file__).resolve().parent
HARDBOT_DIR = PULT_DIR.parent / "hardbot"
for _module_dir in (PULT_DIR, HARDBOT_DIR):
    if str(_module_dir) not in sys.path:
        sys.path.insert(0, str(_module_dir))

import db as _db  # noqa: E402
import enrich  # noqa: E402
import exchange_contract as contract  # noqa: E402


if Path(enrich.__file__).resolve() != (PULT_DIR / "enrich.py").resolve():
    raise RuntimeError("enrich imported outside the current checkout")
if Path(contract.__file__).resolve() != (
    HARDBOT_DIR / "exchange_contract.py"
).resolve():
    raise RuntimeError("exchange_contract imported outside the current checkout")
if Path(_db.__file__).resolve() != (HARDBOT_DIR / "db.py").resolve():
    raise RuntimeError("db imported outside the current checkout")


DEFAULT_SETTINGS_PATH = PULT_DIR / "настройки пульта.json"
IMPORT_PATH = "/admin/import.json"
MAX_SCOPE_BYTES = 1024 * 1024
MAX_SETTINGS_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PREFLIGHT_LOCK = threading.Lock()


class OperatorBlocked(RuntimeError):
    """Expected fail-closed refusal with a safe, non-sensitive message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise OperatorBlocked(
            "usage_error",
            "invalid command arguments; use --help for the exact interface",
        )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward X-Admin-Key to a redirected URL."""

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        return None


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: int):
    return urllib.request.build_opener(_NoRedirectHandler()).open(
        request,
        timeout=timeout,
    )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare",
        help="create one proof/request pair for an approved workbook subset",
    )
    prepare.add_argument("--workbook", required=True)
    prepare.add_argument("--approval-id", required=True)
    prepare.add_argument("--scope", required=True)
    prepare.add_argument("--output-dir", required=True)

    published = commands.add_parser(
        "prepare-published-master",
        help="create one proof/request pair for the latest published master",
    )
    published.add_argument("--workbook", required=True)
    published.add_argument("--workspace")
    published.add_argument("--output-dir", required=True)

    apply = commands.add_parser(
        "apply",
        help="send an already server-registered request",
    )
    apply.add_argument("--request", required=True)
    apply.add_argument(
        "--settings",
        default=str(DEFAULT_SETTINGS_PATH),
    )
    apply.add_argument("--confirm-proof-digest", required=True)
    return parser


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _regular_file(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not _is_reparse_point(info)


def _safe_absolute_path(value: str | os.PathLike[str], *, label: str) -> Path:
    text = os.fspath(value)
    if not isinstance(text, str) or not text or "\x00" in text:
        raise OperatorBlocked(
            "path_invalid",
            f"{label} path is invalid",
        )
    return Path(os.path.abspath(text))


def _reject_reparse_chain(path: Path, *, label: str) -> None:
    current = path
    while True:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            info = None
        except OSError as exc:
            raise OperatorBlocked(
                "path_unavailable",
                f"{label} path cannot be inspected safely",
            ) from exc
        if info is not None:
            if stat.S_ISLNK(info.st_mode) or _is_reparse_point(info):
                raise OperatorBlocked(
                    "path_not_regular",
                    f"{label} path contains a symlink or reparse point",
                )
            if current != path and not stat.S_ISDIR(info.st_mode):
                raise OperatorBlocked(
                    "path_not_regular",
                    f"{label} parent path is not a directory",
                )
        parent = current.parent
        if parent == current:
            break
        current = parent


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


def _read_fd(fd: int, *, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = maximum + 1 - total
        chunk = os.read(fd, min(65536, max(1, remaining)))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise OperatorBlocked(
                "input_file_too_large",
                "input JSON exceeds its safety limit",
            )


def _read_stable_regular_bytes(
    path_value: str | os.PathLike[str],
    *,
    label: str,
    maximum: int,
    require_private: bool = False,
) -> tuple[Path, bytes]:
    """Read twice through one descriptor and reject path/metadata changes."""

    path = _safe_absolute_path(path_value, label=label)
    fd: int | None = None
    try:
        _reject_reparse_chain(path, label=label)
        before = os.lstat(path)
        if not _regular_file(before):
            raise OperatorBlocked(
                "input_file_not_regular",
                f"{label} must be a regular non-symlink file",
            )
        if before.st_size > maximum:
            raise OperatorBlocked(
                "input_file_too_large",
                "input JSON exceeds its safety limit",
            )
        if (
            require_private
            and os.name != "nt"
            and stat.S_IMODE(before.st_mode) & 0o077
        ):
            raise OperatorBlocked(
                "settings_not_private",
                "settings JSON must not be readable by group or other users",
            )

        flags = os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        if not _regular_file(opened) or _identity(opened) != _identity(before):
            raise OperatorBlocked(
                "input_file_changed",
                f"{label} changed while it was being opened",
            )

        first = _read_fd(fd, maximum=maximum)
        first_end = os.fstat(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        second = _read_fd(fd, maximum=maximum)
        second_end = os.fstat(fd)
        after = os.lstat(path)
        _reject_reparse_chain(path, label=label)

        descriptor_expected = _stable_metadata(opened)
        if (
            first != second
            or _stable_metadata(first_end) != descriptor_expected
            or _stable_metadata(second_end) != descriptor_expected
            or not _regular_file(after)
            or _stable_metadata(after) != _stable_metadata(before)
        ):
            raise OperatorBlocked(
                "input_file_changed",
                f"{label} changed while it was being read",
            )
        return path, first
    except OperatorBlocked:
        raise
    except OSError as exc:
        raise OperatorBlocked(
            "input_file_unavailable",
            f"{label} cannot be opened as a stable regular file",
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OperatorBlocked(
                "json_duplicate_key",
                "JSON contains a duplicate object key",
            )
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise OperatorBlocked(
        "json_invalid",
        "JSON contains a non-finite number",
    )


def _decode_json(raw: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_exact_object,
            parse_constant=_reject_json_constant,
        )
    except OperatorBlocked:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorBlocked(
            "json_invalid",
            f"{label} must be valid UTF-8 JSON",
        ) from exc


def _read_stable_json(
    path_value: str | os.PathLike[str],
    *,
    label: str,
    maximum: int,
    require_private: bool = False,
) -> tuple[Path, Any]:
    path, raw = _read_stable_regular_bytes(
        path_value,
        label=label,
        maximum=maximum,
        require_private=require_private,
    )
    return path, _decode_json(raw, label=label)


def _approval_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 200
        or _CONTROL_RE.search(value)
    ):
        raise OperatorBlocked(
            "approval_id_invalid",
            "approval-id must be a trimmed non-control string of at most 200 characters",
        )
    return value


def _scope_json(value: str) -> list[dict[str, str]]:
    raw = value.encode("utf-8")
    if len(raw) > MAX_SCOPE_BYTES:
        raise OperatorBlocked(
            "scope_too_large",
            "scope JSON exceeds the 1 MiB safety limit",
        )
    payload = _decode_json(raw, label="scope")
    if not isinstance(payload, list) or not payload:
        raise OperatorBlocked(
            "scope_invalid",
            "scope must be a nonempty JSON list",
        )

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict) or set(item) != {
            "contract_id",
            "responsible",
        }:
            raise OperatorBlocked(
                "scope_invalid",
                "every scope item must contain exactly contract_id and responsible",
            )
        contract_id = item["contract_id"]
        responsible = item["responsible"]
        if (
            not isinstance(contract_id, str)
            or not contract_id
            or contract_id != contract_id.strip()
            or _CONTROL_RE.search(contract_id)
            or not isinstance(responsible, str)
            or not responsible
            or responsible != responsible.strip()
            or _CONTROL_RE.search(responsible)
        ):
            raise OperatorBlocked(
                "scope_invalid",
                "scope values must be nonblank trimmed strings without control characters",
            )
        if contract_id in seen:
            raise OperatorBlocked(
                "scope_invalid",
                "scope contains a duplicate contract_id",
            )
        seen.add(contract_id)
        result.append(
            {
                "contract_id": contract_id,
                "responsible": responsible,
            }
        )
    return sorted(result, key=lambda item: item["contract_id"])


def _validate_sheets(sheets: Any) -> list[list[Any]]:
    if not isinstance(sheets, list) or not sheets:
        raise OperatorBlocked(
            "workbook_invalid",
            "workbook must contain readable sheets",
        )
    names: list[str] = []
    selected: list[list[Any]] | None = None
    selected_count = 0
    for sheet in sheets:
        if not isinstance(sheet, dict) or set(sheet) != {"name", "grid"}:
            raise OperatorBlocked(
                "workbook_invalid",
                "every transport sheet must contain exactly name and grid",
            )
        name = sheet["name"]
        grid = sheet["grid"]
        if (
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or not isinstance(grid, list)
            or any(not isinstance(row, list) for row in grid)
        ):
            raise OperatorBlocked(
                "workbook_invalid",
                "transport sheet names and grids are malformed",
            )
        names.append(name)
        if name == contract.WORKBOOK_SHEET_NAME:
            selected_count += 1
            selected = grid
    if len(names) != len(set(names)):
        raise OperatorBlocked(
            "workbook_invalid",
            "workbook contains duplicate sheet names",
        )
    if selected_count != 1 or selected is None:
        raise OperatorBlocked(
            "workbook_invalid",
            "workbook must contain exactly one contract sheet",
        )
    return selected


def _stable_workbook(path_value: str) -> tuple[Path, dict[str, Any]]:
    path = _safe_absolute_path(path_value, label="workbook")
    try:
        _reject_reparse_chain(path, label="workbook")
        before = os.lstat(path)
        if not _regular_file(before):
            raise OperatorBlocked(
                "workbook_not_regular",
                "workbook must be a regular non-symlink file",
            )
        prepared = enrich.read_stable_workbook(path)
        after = os.lstat(path)
        _reject_reparse_chain(path, label="workbook")
        if (
            not _regular_file(after)
            or _stable_metadata(after) != _stable_metadata(before)
        ):
            raise OperatorBlocked(
                "workbook_changed",
                "workbook changed during stable parsing",
            )
    except OperatorBlocked:
        raise
    except (OSError, ValueError) as exc:
        raise OperatorBlocked(
            "workbook_rejected",
            "workbook failed stable contract parsing",
        ) from exc
    if not isinstance(prepared, dict) or set(prepared) != {
        "sheets",
        "file_sha256",
    }:
        raise OperatorBlocked(
            "workbook_invalid",
            "stable workbook reader returned an invalid envelope",
        )
    file_sha = prepared["file_sha256"]
    if not isinstance(file_sha, str) or _SHA256_RE.fullmatch(file_sha) is None:
        raise OperatorBlocked(
            "workbook_invalid",
            "stable workbook reader returned an invalid digest",
        )
    return path, prepared


def _authoritative_preflight(
    grid: list[list[Any]],
    proof: dict[str, Any],
    *,
    source_kind: str,
    expected_count: int | None,
) -> int:
    """Reuse the server import parser against a disposable database."""

    with _PREFLIGHT_LOCK:
        original_db_path = _db.DB_PATH
        try:
            with tempfile.TemporaryDirectory(
                prefix="hardbot-limited-import-preflight-",
                ignore_cleanup_errors=True,
            ) as temp_dir:
                _db.DB_PATH = Path(temp_dir) / "preflight.sqlite3"
                _db.init_db()
                report = _db.import_grid(
                    grid,
                    source_kind=source_kind,
                    source_proof=proof,
                    require_source_proof=False,
                )
        except (OSError, sqlite3.Error, contract.ExchangeContractError) as exc:
            raise OperatorBlocked(
                "preflight_failed",
                "local authoritative import preflight failed",
            ) from exc
        finally:
            _db.DB_PATH = original_db_path

    if not isinstance(report, dict) or report.get("blocked"):
        code = (
            "scope_mismatch"
            if isinstance(report, dict) and report.get("scope_mismatch")
            else "grid_rejected"
        )
        raise OperatorBlocked(
            code,
            "workbook grid does not satisfy the approved machine contract and scope",
        )
    total_rows = report.get("total_rows")
    if (
        isinstance(total_rows, bool)
        or not isinstance(total_rows, int)
        or total_rows < 0
        or (
            expected_count is not None
            and total_rows != expected_count
        )
    ):
        raise OperatorBlocked(
            "scope_mismatch",
            (
                "workbook row count differs from the exact approved scope"
                if expected_count is not None
                else "workbook row count is invalid"
            ),
        )
    return total_rows


def _new_private_directory(path_value: str) -> Path:
    path = _safe_absolute_path(path_value, label="output-dir")
    parent = path.parent
    _reject_reparse_chain(parent, label="output-dir")
    try:
        parent_info = os.lstat(parent)
        if not stat.S_ISDIR(parent_info.st_mode):
            raise OperatorBlocked(
                "output_parent_invalid",
                "output-dir parent must be an existing directory",
            )
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)
        _reject_reparse_chain(path, label="output-dir")
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode):
            raise OperatorBlocked(
                "output_dir_invalid",
                "new output-dir is not a directory",
            )
        if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            raise OperatorBlocked(
                "output_dir_not_private",
                "new output-dir is not private",
            )
    except OperatorBlocked:
        raise
    except FileExistsError as exc:
        raise OperatorBlocked(
            "output_dir_exists",
            "output-dir already exists; overwrite is forbidden",
        ) from exc
    except OSError as exc:
        raise OperatorBlocked(
            "output_dir_unavailable",
            "private output-dir could not be created",
        ) from exc
    return path


def _atomic_private_json(path: Path, value: Any) -> None:
    raw = _canonical_json_bytes(value) + b"\n"
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    fd: int | None = None
    linked = False
    try:
        try:
            os.lstat(path)
        except FileNotFoundError:
            pass
        else:
            raise OperatorBlocked(
                "output_exists",
                "output file already exists; overwrite is forbidden",
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.chmod(temporary, 0o600)
        # Hard-link publication is atomic and, unlike replace/rename on POSIX,
        # cannot overwrite a concurrently created destination.
        os.link(temporary, path)
        linked = True
        os.unlink(temporary)
        info = os.lstat(path)
        if not _regular_file(info):
            raise OperatorBlocked(
                "output_invalid",
                "atomic output is not a regular file",
            )
        if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            raise OperatorBlocked(
                "output_not_private",
                "atomic output is not private",
            )
    except OperatorBlocked:
        raise
    except FileExistsError as exc:
        raise OperatorBlocked(
            "output_exists",
            "output file already exists; overwrite is forbidden",
        ) from exc
    except OSError as exc:
        raise OperatorBlocked(
            "output_write_failed",
            "atomic private output could not be written",
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass
        if not linked:
            # The caller removes only its newly created, otherwise-empty
            # directory; this function never removes an existing destination.
            pass


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    workbook_path, stable = _stable_workbook(args.workbook)
    grid = _validate_sheets(stable["sheets"])
    scope = _scope_json(args.scope)
    approval_id = _approval_id(args.approval_id)
    try:
        proof = contract.build_test_subset_source_proof(
            grid,
            approval_id=approval_id,
            contracts=scope,
        )
        verified = contract.validate_import_source_proof(
            proof,
            source_kind=contract.IMPORT_SOURCE_TEST_SUBSET,
            grid=grid,
        )
    except contract.ExchangeContractError as exc:
        raise OperatorBlocked(
            "proof_rejected",
            "proof does not satisfy the active machine contract",
        ) from exc
    total_rows = _authoritative_preflight(
        grid,
        proof,
        source_kind=contract.IMPORT_SOURCE_TEST_SUBSET,
        expected_count=len(scope),
    )
    request = {
        "data_exchange_contract": contract.contract_binding(),
        "source_kind": contract.IMPORT_SOURCE_TEST_SUBSET,
        "source_proof": proof,
        # Only the contract sheet is transported. Other workbook sheets are
        # intentionally excluded from this limited-test request.
        "sheets": [
            {
                "name": contract.WORKBOOK_SHEET_NAME,
                "grid": grid,
            }
        ],
    }
    request_digest = _sha256_bytes(_canonical_json_bytes(request))

    output_dir = _new_private_directory(args.output_dir)
    proof_path = output_dir / "proof.json"
    request_path = output_dir / "request.json"
    try:
        _atomic_private_json(proof_path, proof)
        _atomic_private_json(request_path, request)
    except Exception:
        for candidate in (request_path, proof_path):
            try:
                info = os.lstat(candidate)
                if _regular_file(info):
                    candidate.unlink()
            except (FileNotFoundError, OSError):
                pass
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise

    return {
        "ok": True,
        "command": "prepare",
        "contract": contract.contract_binding(),
        "workbook_sha256": stable["file_sha256"],
        "proof_digest": verified["digest"],
        "request_digest": request_digest,
        "scope_count": len(scope),
        "row_count": total_rows,
        "sheet_count": 1,
        "workbook_path": str(workbook_path),
        "output_dir": str(output_dir),
        "proof_path": str(proof_path),
        "request_path": str(request_path),
    }


def _stable_published_master(
    path_value: str,
    *,
    workspace: str | None,
) -> tuple[Path, dict[str, Any]]:
    """Verify one immutable master and its already completed publication."""

    path = _safe_absolute_path(path_value, label="workbook")
    try:
        _reject_reparse_chain(path, label="workbook")
        before = os.lstat(path)
        if not _regular_file(before):
            raise OperatorBlocked(
                "workbook_not_regular",
                "workbook must be a regular non-symlink file",
            )
        prepared = enrich.prepare_published_master_import(
            path,
            workspace=workspace,
        )
        after = os.lstat(path)
        _reject_reparse_chain(path, label="workbook")
        if (
            not _regular_file(after)
            or _stable_metadata(after) != _stable_metadata(before)
        ):
            raise OperatorBlocked(
                "workbook_changed",
                "workbook changed during publication verification",
            )
    except OperatorBlocked:
        raise
    except (OSError, ValueError) as exc:
        raise OperatorBlocked(
            "published_master_rejected",
            "published master failed stable publication verification",
        ) from exc
    if not isinstance(prepared, dict) or set(prepared) != {
        "sheets",
        "source_proof",
        "file_sha256",
    }:
        raise OperatorBlocked(
            "published_master_invalid",
            "published-master verifier returned an invalid envelope",
        )
    file_sha = prepared["file_sha256"]
    if not isinstance(file_sha, str) or _SHA256_RE.fullmatch(file_sha) is None:
        raise OperatorBlocked(
            "published_master_invalid",
            "published-master verifier returned an invalid digest",
        )
    return path, prepared


def _prepare_published_master(args: argparse.Namespace) -> dict[str, Any]:
    workbook_path, prepared = _stable_published_master(
        args.workbook,
        workspace=args.workspace,
    )
    grid = _validate_sheets(prepared["sheets"])
    proof = prepared["source_proof"]
    try:
        verified = contract.validate_import_source_proof(
            proof,
            source_kind=contract.IMPORT_SOURCE_PUBLISHED_MASTER,
            grid=grid,
        )
    except contract.ExchangeContractError as exc:
        raise OperatorBlocked(
            "proof_rejected",
            "proof does not satisfy the active published-master contract",
        ) from exc
    total_rows = _authoritative_preflight(
        grid,
        proof,
        source_kind=contract.IMPORT_SOURCE_PUBLISHED_MASTER,
        expected_count=None,
    )
    request = {
        "data_exchange_contract": contract.contract_binding(),
        "source_kind": contract.IMPORT_SOURCE_PUBLISHED_MASTER,
        "source_proof": proof,
        "sheets": [
            {
                "name": contract.WORKBOOK_SHEET_NAME,
                "grid": grid,
            }
        ],
    }
    request_digest = _sha256_bytes(_canonical_json_bytes(request))

    output_dir = _new_private_directory(args.output_dir)
    proof_path = output_dir / "proof.json"
    request_path = output_dir / "request.json"
    try:
        _atomic_private_json(proof_path, proof)
        _atomic_private_json(request_path, request)
    except Exception:
        for candidate in (request_path, proof_path):
            try:
                info = os.lstat(candidate)
                if _regular_file(info):
                    candidate.unlink()
            except (FileNotFoundError, OSError):
                pass
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise

    publication = verified.get("proof", {}).get("publication", {})
    return {
        "ok": True,
        "command": "prepare-published-master",
        "contract": contract.contract_binding(),
        "source_kind": contract.IMPORT_SOURCE_PUBLISHED_MASTER,
        "workbook_sha256": prepared["file_sha256"],
        "proof_digest": verified["digest"],
        "request_digest": request_digest,
        "row_count": total_rows,
        "sheet_count": 1,
        "publication_run_id": publication.get("run_id"),
        "publication_sequence": verified["sequence"],
        "workbook_path": str(workbook_path),
        "output_dir": str(output_dir),
        "proof_path": str(proof_path),
        "request_path": str(request_path),
    }


def _validate_request(payload: Any) -> tuple[dict[str, Any], str, int]:
    if not isinstance(payload, dict) or set(payload) != {
        "data_exchange_contract",
        "source_kind",
        "source_proof",
        "sheets",
    }:
        raise OperatorBlocked(
            "request_invalid",
            "request must contain exactly contract binding, source kind, proof and sheets",
        )
    try:
        contract.validate_contract_binding(payload["data_exchange_contract"])
    except contract.ExchangeContractError as exc:
        raise OperatorBlocked(
            "request_contract_mismatch",
            "request is bound to another machine contract",
        ) from exc
    source_kind = payload["source_kind"]
    if source_kind not in {
        contract.IMPORT_SOURCE_TEST_SUBSET,
        contract.IMPORT_SOURCE_PUBLISHED_MASTER,
    }:
        raise OperatorBlocked(
            "request_source_invalid",
            "operator accepts only contract-bound hardbot import sources",
        )
    grid = _validate_sheets(payload["sheets"])
    try:
        verified = contract.validate_import_source_proof(
            payload["source_proof"],
            source_kind=source_kind,
            grid=grid,
        )
    except contract.ExchangeContractError as exc:
        raise OperatorBlocked(
            "request_proof_rejected",
            "request proof does not match its exact grid and contract",
        ) from exc
    expected_count: int | None = None
    if source_kind == contract.IMPORT_SOURCE_TEST_SUBSET:
        scope = verified.get("scope")
        if not isinstance(scope, dict) or not scope:
            raise OperatorBlocked(
                "request_scope_invalid",
                "request proof has no exact test scope",
            )
        expected_count = len(scope)
    elif verified.get("scope") is not None:
        raise OperatorBlocked(
            "request_scope_invalid",
            "published-master proof unexpectedly contains a test scope",
        )
    rows = _authoritative_preflight(
        grid,
        payload["source_proof"],
        source_kind=source_kind,
        expected_count=expected_count,
    )
    return payload, verified["digest"], rows


def _confirmed_digest(value: Any) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise OperatorBlocked(
            "confirmation_invalid",
            "confirm-proof-digest must be an exact lowercase SHA-256",
        )
    return value


def _settings(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise OperatorBlocked(
            "settings_invalid",
            "settings JSON must contain an object",
        )
    url = payload.get("url")
    key = payload.get("key")
    if (
        not isinstance(url, str)
        or not url
        or url != url.strip()
        or _CONTROL_RE.search(url)
        or not isinstance(key, str)
        or not key
        or key != key.strip()
        or _CONTROL_RE.search(key)
    ):
        raise OperatorBlocked(
            "settings_invalid",
            "settings JSON must contain trimmed nonblank url and key strings",
        )
    return url, key


def _base_url(value: str) -> tuple[str, str]:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise OperatorBlocked(
            "url_invalid",
            "settings URL is malformed",
        ) from exc
    if (
        parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.path
        or value != f"{parsed.scheme}://{parsed.netloc}"
        or _CONTROL_RE.search(parsed.netloc)
    ):
        raise OperatorBlocked(
            "url_invalid",
            "settings URL must be an exact origin without credentials, path, query or fragment",
        )
    del port  # Access above validates malformed port syntax.
    is_loopback = parsed.hostname.casefold() in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    allow_test_http = (
        os.environ.get("HARDBOT_LIMITED_TEST_ALLOW_LOCAL_HTTP") == "1"
    )
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and is_loopback and allow_test_http
    ):
        raise OperatorBlocked(
            "url_not_https",
            "settings URL must use HTTPS",
        )
    return value, parsed.netloc


def _read_response(stream: BinaryIO) -> Any:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(65536, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise OperatorBlocked(
                "server_response_too_large",
                "server response exceeds the safety limit",
            )
    return _decode_json(b"".join(chunks), label="server response")


def _safe_count(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OperatorBlocked(
            "server_response_invalid",
            "server response contains invalid counters",
        )
    return value


def _post_request(
    *,
    base_url: str,
    key: str,
    payload: dict[str, Any],
    proof_digest: str,
) -> dict[str, Any]:
    body = _canonical_json_bytes(payload)
    request = urllib.request.Request(
        base_url + IMPORT_PATH,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Admin-Key": key,
        },
    )
    try:
        with _urlopen_no_redirect(request, timeout=120) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if (
                isinstance(status, bool)
                or not isinstance(status, int)
                or status < 200
                or status >= 300
            ):
                raise OperatorBlocked(
                    "server_rejected",
                    "server rejected the limited-test import",
                )
            payload_out = _read_response(response)
    except OperatorBlocked:
        raise
    except urllib.error.HTTPError as exc:
        # Из тела 409 читаем ТОЛЬКО машиночитаемый ключ "code": свободный текст
        # сервера наружу не печатается (контракт CLI). Отзыв, требующий свежего
        # предпросмотра, — не поломка регистрации расписки, и отправлять
        # оператора чинить расписку было бы диагностикой в сторону.
        server_code = ""
        if exc.code == 409:
            try:
                raw_body = exc.read()
                parsed = json.loads(raw_body.decode("utf-8", "replace"))
                if isinstance(parsed, dict):
                    server_code = str(parsed.get("code") or "")
            except Exception:
                server_code = ""
        try:
            exc.close()
        except Exception:
            pass
        if exc.code == 409 and server_code == "recall_preview_required":
            raise OperatorBlocked(
                "recall_preview_required",
                "server requires a fresh recall preview for this workbook",
            ) from exc
        code = (
            "authorization_not_registered"
            if exc.code == 409
            else "server_rejected"
        )
        raise OperatorBlocked(
            code,
            "server rejected the limited-test import",
        ) from exc
    except urllib.error.URLError as exc:
        raise OperatorBlocked(
            "network_error",
            "limited-test import could not reach the configured server",
        ) from exc
    except (OSError, TimeoutError) as exc:
        raise OperatorBlocked(
            "network_error",
            "limited-test import transport failed",
        ) from exc

    if (
        not isinstance(payload_out, dict)
        or payload_out.get("blocked")
        or payload_out.get("error")
        or payload_out.get("source_proof_digest") != proof_digest
    ):
        raise OperatorBlocked(
            "server_response_invalid",
            "server did not confirm the exact proof digest",
        )
    return {
        "total_rows": _safe_count(payload_out, "total_rows"),
        "inserted": _safe_count(payload_out, "inserted"),
        "updated": _safe_count(payload_out, "updated"),
        "idempotent_replay": payload_out.get("idempotent_replay") is True,
    }


def _apply(args: argparse.Namespace) -> dict[str, Any]:
    request_path, request_payload = _read_stable_json(
        args.request,
        label="request",
        maximum=MAX_REQUEST_BYTES,
    )
    request_payload, proof_digest, row_count = _validate_request(
        request_payload
    )
    if _confirmed_digest(args.confirm_proof_digest) != proof_digest:
        raise OperatorBlocked(
            "confirmation_mismatch",
            "confirmed proof digest differs from the exact request proof",
        )

    settings_path, settings_payload = _read_stable_json(
        args.settings,
        label="settings",
        maximum=MAX_SETTINGS_BYTES,
        require_private=True,
    )
    url, key = _settings(settings_payload)
    base_url, endpoint = _base_url(url)
    result = _post_request(
        base_url=base_url,
        key=key,
        payload=request_payload,
        proof_digest=proof_digest,
    )
    return {
        "ok": True,
        "command": "apply",
        "proof_digest": proof_digest,
        "request_digest": _sha256_bytes(
            _canonical_json_bytes(request_payload)
        ),
        "row_count": row_count,
        "server": endpoint,
        "request_path": str(request_path),
        "settings_path": str(settings_path),
        **result,
    }


def _emit(payload: dict[str, Any], *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        ),
        file=stream,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "prepare":
            result = _prepare(args)
        elif args.command == "prepare-published-master":
            result = _prepare_published_master(args)
        else:
            result = _apply(args)
        _emit(result)
        return 0
    except OperatorBlocked as exc:
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
    except Exception:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "limited-test import command failed",
                },
            },
            error=True,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
