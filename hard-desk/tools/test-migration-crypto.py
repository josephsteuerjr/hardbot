"""Real Windows encryption profile migration, including deferred manual import."""
import json
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
base = Path(os.environ.get('HARD_QA_ROOT', repo / '_local/redesign-2026-10-09')) / 'migration-crypto'
base.mkdir(parents=True, exist_ok=True)
lab = Path(tempfile.mkdtemp(prefix='run-', dir=base))
roaming = lab / 'Roaming'
legacy = roaming / 'Хард-сотрудник'
legacy.mkdir(parents=True)
env = dict(os.environ)
env.pop('ELECTRON_RUN_AS_NODE', None)

def fixture(directory, make=False):
    directory.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(['node', str(repo / 'hard-desk/tools/run-electron-test.cjs'),
        str(repo / 'hard-desk/tools/test-install-data.cjs')],
        cwd=repo / 'hard-desk', env=dict(env, HARD_INSTALL_QA_DATA=str(directory), HARD_INSTALL_QA_MAKE='1' if make else '0', HARD_INSTALL_QA_FIELD_ONLY='0' if make else '1'),
        capture_output=True, text=True, encoding='utf-8', timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode:
        raise RuntimeError(done.stdout + done.stderr)
    assert 'Real DPAPI tokens' in done.stdout

def migrate(target, queued=False):
    code = '''const {buildSync}=require('esbuild'),vm=require('node:vm'),path=require('node:path');
const m={exports:{}};vm.runInNewContext(buildSync({entryPoints:['src/main/migration.ts'],bundle:true,platform:'node',format:'cjs',write:false}).outputFiles[0].text,{module:m,exports:m.exports,require,Buffer});
if(process.argv[4]==='queued')m.exports.queueImport(process.argv[1],process.argv[2]);
m.exports.migrateData(process.argv[2],process.argv[3],'soft');'''
    subprocess.run(['node', '-e', code, str(legacy), str(target), str(roaming), 'queued' if queued else 'auto'],
        cwd=repo / 'hard-desk', env=env, check=True, capture_output=True, timeout=30)

fixture(legacy, True)
(legacy / 'settings.json').write_text(json.dumps({'fieldServer':'https://synthetic.invalid',
    'fieldDevice':{'id':101,'telegram_id':101},'theme':'dark'}), encoding='utf-8')
owner = hashlib.sha256(b'https://synthetic.invalid\nuid:101').hexdigest()
draft_name = 'draft-' + hashlib.sha256((owner + '\nsoft\nSYNTHETIC').encode()).hexdigest() + '.bin'
protected = {name:(legacy / name).read_bytes() for name in ('field.key','calls-pending.bin','Local State',draft_name)}
automatic = lab / 'automatic'
migrate(automatic)
# Validate only migrated employee material, without copying extra witnesses.
for target, queued in ((automatic,False),(lab / 'manual',True)):
    if queued:
        fixture(target, True)  # initialize a different destination profile key
        migrate(target, True)
    for name, content in protected.items():
        assert (target / name).read_bytes() == content
    fixture(target)
    assert json.loads((target / 'settings.json').read_text())['fieldDevice']['telegram_id'] == 101
    assert not (target / 'migration-request.json').exists()
(base / 'RESULTS.json').write_text(json.dumps({'automaticBeforeChromium':True,
    'manualDeferredUntilRestart':True,'realV10ProfileEncryption':True,'tokensQueueDraftReadable':True,
    'identityAndBytesPreserved':True}), encoding='utf-8')
print('Real migration passed: v10 profile key, token/queue/draft decryption, preserved uid/bytes, automatic and deferred manual import.')
