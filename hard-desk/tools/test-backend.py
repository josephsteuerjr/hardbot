import json
import os
import subprocess
import sys
import urllib.request
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path(__file__).resolve().parents[2]
qa = Path(os.environ.get('HARD_QA_ROOT', root / '_local/redesign-2026-10-09')) / 'backend-qa'
qa.mkdir(parents=True, exist_ok=True)
settings = qa / 'settings.json'
settings.write_text('{}', encoding='utf-8')

seen = []
class SyntheticAdmin(BaseHTTPRequestHandler):
    def do_GET(self):
        seen.append(dict(self.headers))
        payload = b'{"devices": []}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *_):
        pass

remote = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticAdmin)
thread = threading.Thread(target=remote.serve_forever, daemon=True)
thread.start()
synthetic_url = f'http://127.0.0.1:{remote.server_port}'
env = dict(os.environ, HARD_PULT_SETTINGS=str(settings), HARD_PULT_PROTECT_TOKENS='1', PYTHONIOENCODING='utf-8')
exe = root / 'hard-desk/backend-dist/hard-backend/hard-backend.exe'
p = subprocess.Popen([str(exe)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
try:
    line = p.stdout.readline().strip()
    if not line.startswith('HARD-BACKEND '):
        raise RuntimeError(p.stderr.read())
    _, port, secret = line.split()
    def request(path, body):
        req = urllib.request.Request('http://127.0.0.1:' + port + path, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'X-Local-Secret': secret})
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    assert request('/state', {})['data']['bound'] is False
    assert request('/jobs', {}) == {'ok': True, 'data': []}
    assert request('/fn', {'name': 'dashboard.tile_values', 'args': [{}]})['ok']
    old = qa / 'old-settings.json'
    old.write_text(json.dumps({'profiles': {'Test': {'url': synthetic_url, 'device_token': 'synthetic-token'}}, 'active': 'Test'}), encoding='utf-8')
    assert request('/settings/import', {'path': str(old)})['data']['bound']
    assert json.loads(settings.read_text(encoding='utf-8'))['device_token'].startswith('dpapi-v1:')
    assert 'synthetic-token' not in settings.read_text(encoding='utf-8')
    assert request('/admin', {'method': 'GET', 'path': '/admin/devices.json'}) == {'ok': True, 'data': {'devices': []}}
    assert seen[-1]['X-Device-Token'] == 'synthetic-token'
    assert len(seen[-1]['X-Device-Id']) == 64
    assert not request('/busy', {})['data']['busy']
    sys.path.insert(0, str(root))
    from pult.core.durable_jobs import Journal
    journal = Journal(qa / 'jobs.sqlite3')
    journal.save({'id': 'QA-DURABLE-RESTART', 'kind': 'synthetic', 'state': 'running',
                  'started': 'Synthetic interrupted operation'},
                 'Test\n' + synthetic_url, 'synthetic-fingerprint')
    assert b'Synthetic interrupted operation' not in (qa / 'jobs.sqlite3').read_bytes()
    p.terminate()
    p.wait(timeout=15)
    p = subprocess.Popen([str(exe)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    line = p.stdout.readline().strip()
    assert line.startswith('HARD-BACKEND '), 'Frozen backend did not restart'
    _, port, secret = line.split()
    jobs = request('/jobs', {})
    assert jobs['ok'] and jobs['data'][0]['id'] == 'QA-DURABLE-RESTART'
    assert jobs['data'][0]['state'] == 'uncertain'
    (qa / 'RESULTS.json').write_text(json.dumps({'frozenRuntime': True, 'settingsDPAPI': True,
        'newDurableJobsModule': True, 'jobDPAPI': True, 'restartRecovery': True,
        'outboundHTTPDeviceFingerprint': True}), encoding='utf-8')
    print('Frozen analyst backend: starts/restarts without system Python, protects settings/jobs with real DPAPI and recovers an interrupted durable job.')
finally:
    p.terminate()
    p.wait(timeout=15)
    remote.shutdown()
    remote.server_close()
