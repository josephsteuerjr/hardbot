"""Source-only update rehearsal. Copies update.sh into a synthetic unit; no Docker daemon.

A mocked _lib/_actions and fake backup.sh exercise the *actual* update script in
Git Bash/Unix bash. Nothing in the production deploy directory is executed.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


UPDATE = Path(__file__).resolve().parents[1] / "deploy/units/core/update.sh"
OLD_ID = "sha256:" + "a" * 64
BASH = shutil.which("bash")
if os.name == "nt" and (not BASH or "system32" in BASH.lower()):
    git = shutil.which("git")
    candidates = ([Path(git).resolve().parents[1] / "bin/bash.exe"] if git else [])
    candidates += [Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe")),
                   Path("C:/Program Files/Git/bin/bash.exe")]
    BASH = next((str(p) for p in candidates if p.is_file()), None)


@pytest.fixture
def unit(tmp_path):
    core = tmp_path / "deploy/units/core"
    core.mkdir(parents=True)
    shutil.copy2(UPDATE, core / "update.sh")
    (tmp_path / "deploy/_lib.sh").write_text(
        """set -euo pipefail
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
cd "$UNIT_DIR"
say() { echo "$*"; }
warn() { echo "$*" >&2; }
die() { echo "REFUSED: $*" >&2; exit 1; }
check_docker() { echo check >> operations; }
require_env() { test -f .env; }
require_sources() { :; }
check_profiles() { :; }
container_id() { if [ -f changed ]; then echo new-container; else echo old-container; fi; }
health_of() { echo healthy; }
compose() {
  case "$1" in
    config) echo 'services: {app: {image: hard-app}}' ;;
    ps) echo 'app healthy' ;;
    *) echo "compose $*" >> operations ;;
  esac
}
docker() {
  echo "docker $*" >> operations
  case "${1:-} ${2:-} ${3:-}" in
    'inspect -f {{.Image}}') echo 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;
    'image inspect -f') echo 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;
    'image inspect '*) : ;;
    'image tag '*) : ;;
    'inspect old-container '*) echo '[{"Config":{"Env":["SECRET=synthetic"]}}]' ;;
    *) echo "unexpected docker call: $*" >&2; return 17 ;;
  esac
}
""", encoding="utf-8",
    )
    (tmp_path / "deploy/_actions.sh").write_text(
        """banner() { :; }
step() { :; }
build_images() { echo build >> operations; [ "${CASE:-}" != build_fail ]; }
start_unit() { echo start >> operations; [ "${CASE:-}" != start_fail ]; }
wait_unit() { echo wait >> operations; [ "${CASE:-}" != health_fail ]; }
app_port() { echo 18080; }
""", encoding="utf-8",
    )
    (core / "backup.sh").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
echo "backup:$*" >> operations
[ "$#" -eq 0 ] || exit 18
[ "${CASE:-}" != backup_fail ] || exit 19
[ "${CASE:-}" != changed_during_backup ] || touch changed
mkdir -p backups/20261017T120000
printf 'synthetic fixture\\n' > backups/20261017T120000/SHA256SUMS
echo 'Готово: backups/20261017T120000'
""", encoding="utf-8",
    )
    (core / "backup.sh").chmod(0o755)
    (core / ".env").write_text("APP_PORT=18080\n", encoding="utf-8")
    (core / "data").mkdir()
    (core / "data/hardbot.db").write_bytes(b"synthetic, not a real database")
    return core


def run_synthetic(unit, case):
    if not BASH:
        pytest.skip("No Git Bash / Unix bash available")
    env = {**os.environ, "CASE": case}
    # Pin all helper commands in the disposable unit. No real Docker binary is
    # reachable: docker is a shell function defined above.
    return subprocess.run(
        [BASH, "update.sh"], cwd=unit, env=env, text=True,
        encoding="utf-8", errors="replace", capture_output=True, timeout=25,
    )


@pytest.mark.parametrize("case", ["build_fail", "start_fail", "health_fail", "success"])
def test_prior_image_retained_across_update_outcomes(unit, case):
    result = run_synthetic(unit, case)
    assert (result.returncode == 0) == (case == "success"), result.stdout + result.stderr
    operations = (unit / "operations").read_text(encoding="utf-8")
    assert "backup:" in operations and "backup:--db-only" not in operations
    assert operations.index("backup:") < operations.index("docker image tag ") < operations.index("build")
    assert "image prune" not in operations and "docker compose down" not in operations
    receipt, = (unit / "backups").glob("update-*/RECOVERY.txt")
    text = receipt.read_text(encoding="utf-8")
    assert OLD_ID in text and "pre-update-data" in text
    assert "candidate.env" in text and "prior-container.json" in text
    assert "./restore.sh" in text and "внешний HTTPS" in text
    assert (receipt.parent / "prior-container.json").is_file()
    assert (receipt.parent / "prior-image-id").read_text(encoding="utf-8").strip() == OLD_ID
    assert (receipt.parent / "pre-update-data/SHA256SUMS").is_file()
    assert not (unit / "backups/20261017T120000").exists(), "snapshot must evade future rotation"
    if case != "success":
        assert "ручной порядок" in result.stderr


def test_backup_failure_never_builds_or_retags(unit):
    result = run_synthetic(unit, "backup_fail")
    assert result.returncode != 0
    operations = (unit / "operations").read_text(encoding="utf-8")
    assert "backup:" in operations
    assert "build" not in operations and "docker image tag" not in operations
    assert not list((unit / "backups").glob("update-*"))
    assert "полная копия не завершилась" in result.stderr


def test_other_operator_replaced_app_during_backup_refuses_before_build(unit):
    result = run_synthetic(unit, "changed_during_backup")
    assert result.returncode != 0
    operations = (unit / "operations").read_text(encoding="utf-8")
    assert "backup:" in operations
    assert "build" not in operations and "docker image tag" not in operations
    assert "app изменился во время копирования" in result.stderr
    assert (unit / "backups/20261017T120000/SHA256SUMS").is_file()
