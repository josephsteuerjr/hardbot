import io
import json
import os
import stat
import sys
import urllib.error
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PULT_DIR = ROOT / "pult"
if str(PULT_DIR) not in sys.path:
    sys.path.insert(0, str(PULT_DIR))

import limited_test_import as operator


CONTRACT_TITLE = operator.contract.WORKBOOK_CONTRACT_TITLE
RESPONSIBLE_TITLE = operator.contract.WORKBOOK_RESPONSIBLE_TITLE


def _grid(*rows):
    return [
        [CONTRACT_TITLE, RESPONSIBLE_TITLE],
        *[list(row) for row in rows],
    ]


def _stable_payload(grid):
    return {
        "sheets": [
            {"name": operator.contract.WORKBOOK_SHEET_NAME, "grid": grid},
            {"name": "Справочник", "grid": [["служебное значение"]]},
        ],
        "file_sha256": "a" * 64,
    }


def _scope(*rows):
    return json.dumps(
        [
            {"contract_id": contract_id, "responsible": responsible}
            for contract_id, responsible in rows
        ],
        ensure_ascii=False,
    )


def _stdout(capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def _stderr(capsys):
    captured = capsys.readouterr()
    assert captured.out == ""
    return json.loads(captured.err), captured.err


def _prepare(
    monkeypatch,
    tmp_path,
    capsys,
    *,
    grid=None,
    scope=None,
    output_name="prepared",
):
    workbook = tmp_path / "subset.xlsx"
    workbook.write_bytes(b"stable workbook placeholder")
    chosen_grid = grid or _grid(("KD-1", "Agent One"))
    monkeypatch.setattr(
        operator.enrich,
        "read_stable_workbook",
        lambda path: _stable_payload(chosen_grid),
    )
    output_dir = tmp_path / output_name
    rc = operator.main(
        [
            "prepare",
            "--workbook",
            str(workbook),
            "--approval-id",
            "limited-approval-1",
            "--scope",
            scope or _scope(("KD-1", "Agent One")),
            "--output-dir",
            str(output_dir),
        ]
    )
    return rc, output_dir


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _private_settings(path, *, url="https://hardbot.example", key="secret-key"):
    path.write_text(
        json.dumps({"sec": "", "url": url, "key": key}),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def test_prepare_creates_private_exact_proof_and_request_without_row_leak(
    monkeypatch,
    tmp_path,
    capsys,
):
    rc, output_dir = _prepare(monkeypatch, tmp_path, capsys)
    assert rc == 0
    summary = _stdout(capsys)
    assert summary["ok"] is True
    assert summary["scope_count"] == summary["row_count"] == 1
    assert summary["sheet_count"] == 1
    assert "KD-1" not in json.dumps(summary)
    assert "Agent One" not in json.dumps(summary)

    proof_path = output_dir / "proof.json"
    request_path = output_dir / "request.json"
    proof = _load(proof_path)
    request = _load(request_path)
    assert request == {
        "data_exchange_contract": operator.contract.contract_binding(),
        "source_kind": operator.contract.IMPORT_SOURCE_TEST_SUBSET,
        "source_proof": proof,
        "sheets": [
            {
                "name": operator.contract.WORKBOOK_SHEET_NAME,
                "grid": _grid(("KD-1", "Agent One")),
            }
        ],
    }
    verified = operator.contract.validate_import_source_proof(
        proof,
        source_kind=operator.contract.IMPORT_SOURCE_TEST_SUBSET,
        grid=request["sheets"][0]["grid"],
    )
    assert summary["proof_digest"] == verified["digest"]
    if os.name != "nt":
        assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(proof_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(request_path.stat().st_mode) == 0o600

    before = request_path.read_bytes()
    assert operator.main(
        [
            "prepare",
            "--workbook",
            str(tmp_path / "subset.xlsx"),
            "--approval-id",
            "limited-approval-1",
            "--scope",
            _scope(("KD-1", "Agent One")),
            "--output-dir",
            str(output_dir),
        ]
    ) == 2
    refused, _ = _stderr(capsys)
    assert refused["error"]["code"] == "output_dir_exists"
    assert request_path.read_bytes() == before


def test_prepare_rejects_scope_mismatch_and_duplicate_json_keys_without_leak(
    monkeypatch,
    tmp_path,
    capsys,
):
    secret_row = "owner-that-must-not-leak"
    rc, output_dir = _prepare(
        monkeypatch,
        tmp_path,
        capsys,
        grid=_grid(("KD-9", secret_row)),
        scope=_scope(("KD-9", "Other Owner")),
    )
    assert rc == 2
    refused, raw = _stderr(capsys)
    assert refused["error"]["code"] == "scope_mismatch"
    assert secret_row not in raw
    assert not output_dir.exists()

    duplicate_scope = (
        '[{"contract_id":"KD-9","contract_id":"KD-10",'
        '"responsible":"Other Owner"}]'
    )
    rc, output_dir = _prepare(
        monkeypatch,
        tmp_path,
        capsys,
        scope=duplicate_scope,
        output_name="duplicate-scope",
    )
    assert rc == 2
    refused, _ = _stderr(capsys)
    assert refused["error"]["code"] == "json_duplicate_key"
    assert not output_dir.exists()


def test_prepare_rejects_symlink_and_duplicate_contract_sheet(
    monkeypatch,
    tmp_path,
    capsys,
):
    target = tmp_path / "real.xlsx"
    link = tmp_path / "linked.xlsx"
    target.write_bytes(b"workbook")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    called = False

    def forbidden(_path):
        nonlocal called
        called = True
        return _stable_payload(_grid(("KD-1", "Agent One")))

    monkeypatch.setattr(operator.enrich, "read_stable_workbook", forbidden)
    assert operator.main(
        [
            "prepare",
            "--workbook",
            str(link),
            "--approval-id",
            "approval",
            "--scope",
            _scope(("KD-1", "Agent One")),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    ) == 2
    refused, _ = _stderr(capsys)
    assert refused["error"]["code"] == "path_not_regular"
    assert called is False

    duplicate = _stable_payload(_grid(("KD-1", "Agent One")))
    duplicate["sheets"].insert(
        1,
        {
            "name": operator.contract.WORKBOOK_SHEET_NAME,
            "grid": _grid(("KD-1", "Agent One")),
        },
    )
    monkeypatch.setattr(
        operator.enrich,
        "read_stable_workbook",
        lambda _path: duplicate,
    )
    assert operator.main(
        [
            "prepare",
            "--workbook",
            str(target),
            "--approval-id",
            "approval",
            "--scope",
            _scope(("KD-1", "Agent One")),
            "--output-dir",
            str(tmp_path / "duplicate-out"),
        ]
    ) == 2
    refused, _ = _stderr(capsys)
    assert refused["error"]["code"] == "workbook_invalid"


class _Response:
    def __init__(self, payload, status=200):
        self._raw = json.dumps(payload).encode("utf-8")
        self.status = status
        self._stream = io.BytesIO(self._raw)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self._stream.read(size)

    def getcode(self):
        return self.status


def test_apply_revalidates_digest_scope_contract_and_posts_exact_request(
    monkeypatch,
    tmp_path,
    capsys,
):
    rc, output_dir = _prepare(monkeypatch, tmp_path, capsys)
    assert rc == 0
    prepared = _stdout(capsys)
    request_path = output_dir / "request.json"
    settings_path = tmp_path / "settings.json"
    _private_settings(settings_path)
    secret = "secret-key"
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["key"] = request.get_header("X-admin-key")
        seen["body"] = json.loads(request.data)
        return _Response(
            {
                "blocked": False,
                "source_proof_digest": prepared["proof_digest"],
                "total_rows": 1,
                "inserted": 1,
                "updated": 0,
            }
        )

    monkeypatch.setattr(operator, "_urlopen_no_redirect", fake_urlopen)
    assert operator.main(
        [
            "apply",
            "--request",
            str(request_path),
            "--settings",
            str(settings_path),
            "--confirm-proof-digest",
            prepared["proof_digest"],
        ]
    ) == 0
    summary = _stdout(capsys)
    assert summary["ok"] is True
    assert summary["proof_digest"] == prepared["proof_digest"]
    assert summary["inserted"] == 1
    assert seen == {
        "url": "https://hardbot.example/admin/import.json",
        "timeout": 120,
        "key": secret,
        "body": _load(request_path),
    }
    assert secret not in json.dumps(summary)
    assert "KD-1" not in json.dumps(summary)
    assert "Agent One" not in json.dumps(summary)


def test_apply_confirmation_mismatch_does_not_read_settings_or_call_network(
    monkeypatch,
    tmp_path,
    capsys,
):
    rc, output_dir = _prepare(monkeypatch, tmp_path, capsys)
    assert rc == 0
    _stdout(capsys)
    settings_path = tmp_path / "missing-settings.json"
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    monkeypatch.setattr(operator, "_urlopen_no_redirect", forbidden)
    assert operator.main(
        [
            "apply",
            "--request",
            str(output_dir / "request.json"),
            "--settings",
            str(settings_path),
            "--confirm-proof-digest",
            "0" * 64,
        ]
    ) == 2
    refused, _ = _stderr(capsys)
    assert refused["error"]["code"] == "confirmation_mismatch"
    assert called is False


def test_apply_unregistered_409_fails_without_secret_or_body_leak(
    monkeypatch,
    tmp_path,
    capsys,
):
    rc, output_dir = _prepare(monkeypatch, tmp_path, capsys)
    assert rc == 0
    prepared = _stdout(capsys)
    settings_path = tmp_path / "settings.json"
    secret = "admin-secret-never-print"
    _private_settings(settings_path, key=secret)
    server_body_secret = "server-body-never-print"

    def rejected(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "blocked": True,
                        "error": server_body_secret,
                    }
                ).encode("utf-8")
            ),
        )

    monkeypatch.setattr(operator, "_urlopen_no_redirect", rejected)
    assert operator.main(
        [
            "apply",
            "--request",
            str(output_dir / "request.json"),
            "--settings",
            str(settings_path),
            "--confirm-proof-digest",
            prepared["proof_digest"],
        ]
    ) == 2
    refused, raw = _stderr(capsys)
    assert refused["error"]["code"] == "authorization_not_registered"
    assert secret not in raw
    assert server_body_secret not in raw
    assert "KD-1" not in raw


def test_redirect_handler_never_forwards_admin_request():
    request = operator.urllib.request.Request(
        "https://hardbot.example/admin/import.json",
        data=b"{}",
        headers={"X-Admin-Key": "must-not-follow"},
        method="POST",
    )
    redirected = operator._NoRedirectHandler().redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://attacker.example/collect",
    )
    assert redirected is None


def test_apply_rejects_request_symlink_duplicate_keys_and_non_https(
    monkeypatch,
    tmp_path,
    capsys,
):
    rc, output_dir = _prepare(monkeypatch, tmp_path, capsys)
    assert rc == 0
    prepared = _stdout(capsys)
    request_path = output_dir / "request.json"
    link = tmp_path / "request-link.json"
    try:
        os.symlink(request_path, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    settings_path = tmp_path / "settings.json"
    _private_settings(settings_path)

    assert operator.main(
        [
            "apply",
            "--request",
            str(link),
            "--settings",
            str(settings_path),
            "--confirm-proof-digest",
            prepared["proof_digest"],
        ]
    ) == 2
    refused, _ = _stderr(capsys)
    assert refused["error"]["code"] == "path_not_regular"

    duplicate_request = tmp_path / "duplicate-request.json"
    duplicate_request.write_text(
        '{"source_kind":"test_subset","source_kind":"test_subset"}',
        encoding="utf-8",
    )
    assert operator.main(
        [
            "apply",
            "--request",
            str(duplicate_request),
            "--settings",
            str(settings_path),
            "--confirm-proof-digest",
            prepared["proof_digest"],
        ]
    ) == 2
    refused, _ = _stderr(capsys)
    assert refused["error"]["code"] == "json_duplicate_key"

    _private_settings(settings_path, url="http://hardbot.example")
    assert operator.main(
        [
            "apply",
            "--request",
            str(request_path),
            "--settings",
            str(settings_path),
            "--confirm-proof-digest",
            prepared["proof_digest"],
        ]
    ) == 2
    refused, _ = _stderr(capsys)
    assert refused["error"]["code"] == "url_not_https"


def test_stable_reader_detects_path_metadata_change(monkeypatch, tmp_path):
    path = tmp_path / "request.json"
    path.write_text("{}", encoding="utf-8")
    real_lstat = operator.os.lstat
    target_calls = 0

    class ChangedInfo:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            if name == "st_mtime_ns":
                return self._wrapped.st_mtime_ns + 1
            return getattr(self._wrapped, name)

    def changed_lstat(candidate):
        nonlocal target_calls
        result = real_lstat(candidate)
        if Path(candidate) == path:
            target_calls += 1
            # chain inspection, initial metadata, then post-read metadata
            if target_calls == 3:
                return ChangedInfo(result)
        return result

    monkeypatch.setattr(operator.os, "lstat", changed_lstat)
    with pytest.raises(operator.OperatorBlocked) as error:
        operator._read_stable_regular_bytes(
            path,
            label="request",
            maximum=1024,
        )
    assert error.value.code == "input_file_changed"
