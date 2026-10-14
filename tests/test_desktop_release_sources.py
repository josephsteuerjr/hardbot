"""Packaging must refuse silently missing new runtime modules."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('backend_sources',
    Path(__file__).resolve().parents[1] / 'hard-desk/tools/backend_sources.py')
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


@pytest.fixture
def source_repo(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    for rel in sources.SCHEMAS | {'pult/core/existing.py'}:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}' if rel.endswith('.json') else 'value = 1\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    return tmp_path


def test_new_runtime_module_cannot_silently_disappear(source_repo):
    new = source_repo / 'pult/core/durable_jobs.py'
    new.write_text('value = 2\n', encoding='utf-8')
    with pytest.raises(RuntimeError, match='durable_jobs.py'):
        sources.source_manifest(source_repo)
    subprocess.run(['git', '-C', str(source_repo), 'add', '-N', str(new)], check=True)
    assert new.relative_to(source_repo).as_posix() in {
        f['path'] for f in sources.source_manifest(source_repo)['files']}


def test_operational_data_never_enter_the_allowlist(source_repo):
    for rel in ('_local/private.py', 'server/data/working.db', 'pult/settings.json', 'server/real.xlsx'):
        path = source_repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private', encoding='utf-8')
    subprocess.run(['git', '-C', str(source_repo), 'add', '.'], check=True)
    manifest = sources.source_manifest(source_repo)
    assert len(manifest['files']) == len(sources.SCHEMAS) + 1
    assert 'private' not in json.dumps(manifest)


def test_dirty_source_changes_the_manifest_and_missing_file_fails(source_repo):
    before = sources.source_manifest(source_repo)
    path = source_repo / 'pult/core/existing.py'
    path.write_text('value = 3\n', encoding='utf-8')
    assert sources.source_manifest(source_repo) != before
    path.unlink()
    with pytest.raises(RuntimeError, match='existing.py'):
        sources.source_manifest(source_repo)


def test_runtime_without_public_contract_is_rejected(source_repo):
    subprocess.run(['git', '-C', str(source_repo), 'rm', '-q', '-f', 'server/data_exchange_contract.json'], check=True)
    with pytest.raises(RuntimeError, match='schemas'):
        sources.source_manifest(source_repo)
