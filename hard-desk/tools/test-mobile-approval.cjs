// Synthetic browser/HTTP contract check. Never calls a live server or real GPS.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../server/ops_panel.py'), 'utf8');
const match = source.match(/_APPROVAL_PAGE = """([\s\S]*?)"""/);
assert.ok(match, 'standalone approval page exists');
const html = match[1];
assert.match(html, /name="viewport"/);
assert.match(html, /<header><h1>Подтверждение телефона/);
assert.doesNotMatch(html, /(?:borrower|clients|visits|password|admin[_-]key|fixture-person)/i,
  'static unauthenticated HTML must not embed applicant/borrower records or credentials');
const script = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(script);
new vm.Script(script[1], {filename: 'approval_page.inline.js'});

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this._text = ''; this.value = '';
    this.hidden = false; this.disabled = false; this.checked = false;
    this.className = ''; this.listeners = {}; this.onclick = null;
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  dispatch(type) { this.listeners[type]({target: this}); }
  scrollIntoView() {}
}
const ids = [...html.matchAll(/id="([A-Za-z][A-Za-z0-9]*)"/g)].map(x => x[1]);
const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
elements.login.hidden = false;
elements.workspace.hidden = true;
elements.decision.hidden = true;
elements.exceptionWrap.hidden = true;
const storage = new Map();
const localStorage = {
  getItem: key => storage.get(key) || null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: key => storage.delete(key),
};
const fresh = new Date(Date.now() + 12 * 60 * 1000).toISOString();
let accounts = [{id: 1, username: 'fixture-user', employee: 'fixture-person', status: 'awaiting',
  created_at: '2026-10-17T11:00:00Z', registration_gps: '',
  devices: [{id: 91, kind: 'phone', label: 'fixture-device', status: 'pending', gps: '',
    first_seen: '2026-10-17T11:00:10Z'}],
  phone_requests: [{device_id: 91, manager_uid: 44, issued_at: '2026-10-17T11:01:00Z',
    expires_at: fresh, consumed_at: null}] }];
let approver = '';
let nextReply = {ok: false, status: 403, error: 'phone_request_expired_or_used'};
let gpsFailure = false;
const calls = [];
const response = (body, status = 200) => ({ok: status >= 200 && status < 300,
  status, json: async () => body});
const fetch = async (route, options = {}) => {
  calls.push({route, options});
  if(route === '/admin/device/redeem.json') return response({ok: true, role: 'operator', token: 'fake-token'});
  assert.equal(options.headers['X-Device-Token'], 'fake-token');
  assert.equal(options.headers['X-Device-Id'], 'browser-fixture');
  if(route === '/admin/accounts.json') return response({ok: true, approver, accounts});
  if(route === '/admin/account_approve.json')
    return response(nextReply.ok ? nextReply : {ok: false, error: nextReply.error}, nextReply.status || 200);
  throw Error(`unexpected route ${route}`);
};
const navigator = {geolocation: {getCurrentPosition(success, error) {
  if(gpsFailure) error({message: 'permission denied'});
  else success({coords: {latitude: 55.71, longitude: 37.61, accuracy: 9}, timestamp: Date.now()});
}}};
const document = {getElementById: id => {
  assert.ok(elements[id], `missing element ${id}`); return elements[id];
}, createElement: tag => new Element(tag), createTextNode: text => {
  const node = new Element('#text'); node.textContent = text; return node;
}};
vm.runInNewContext(script[1], {document, localStorage, fetch, navigator,
  crypto: {randomUUID: () => 'fixture'}, Date, Number, String}, {filename: 'approval_page.inline.js'});
const tick = () => new Promise(resolve => setImmediate(resolve));
const all = (node, pred) => [node, ...node.children.flatMap(c => all(c, pred))].filter(pred);
const buttons = () => all(elements.requests, el => el.tagName === 'button');

(async () => {
  await tick();
  assert.equal(elements.login.hidden, false, 'unauthenticated is a static login, no account fetch');
  assert.equal(calls.length, 0);
  elements.enrollCode.value = 'synthetic-operator-code';
  await elements.redeem.onclick();
  assert.equal(elements.workspace.hidden, false);
  assert.equal(elements.principal.textContent.includes('нет зарегистрированного'), true);
  assert.equal(buttons().length, 0, 'missing principal has no approval selection control');
  assert.equal(calls[0].route, '/admin/device/redeem.json');
  assert.equal(JSON.parse(calls[0].options.body).code, 'synthetic-operator-code');
  assert.equal(storage.get('hard.ops.device'), 'browser-fixture');
  assert.equal(storage.get('hard.approval.token'), 'fake-token');

  approver = 'admin:fixture';
  accounts = [...accounts, {id: 2, username: 'other-fixture', employee: 'Другой сотрудник',
    status: 'awaiting', created_at: '2026-10-17T11:03:00Z', registration_gps: '',
    devices: [{id: 92, kind: 'phone', status: 'pending', gps: '', label: 'другой телефон'}],
    phone_requests: [{device_id: 92, manager_uid: 55, issued_at: '2026-10-17T11:04:00Z',
      expires_at: fresh, consumed_at: null}]}];
  await elements.reload.onclick();
  assert.equal(buttons().length, 2);
  assert.match(elements.requests.textContent, /fixture-person/);
  assert.match(elements.requests.textContent, /Нет GPS/);
  assert.match(elements.requests.textContent, /Запрос руководителя #44/);
  accounts = [...accounts, {id: 3, username: 'active-fixture', employee: 'Active staff',
    status: 'active', registration_gps: JSON.stringify({lat: 55.1, lon: 37.1}),
    devices: [{id: 93, kind: 'phone', status: 'pending', gps: '', label: 'replacement phone'}],
    phone_requests: [{device_id: 93, manager_uid: 44, issued_at: '2026-10-17T11:05:00Z',
      expires_at: fresh, consumed_at: null}]}];
  await elements.reload.onclick();
  assert.equal(buttons().length, 3, 'active employee with pending replacement phone must be visible');
  assert.match(elements.requests.textContent, /replacement phone/);
  buttons()[1].onclick();
  assert.match(elements.selection.textContent, /#92/);
  elements.requestCode.value = 'code-for-other-phone';
  buttons()[0].onclick();
  assert.match(elements.selection.textContent, /#91/);
  assert.equal(elements.requestCode.value, '', 'switching phones clears prior one-use code');
  assert.equal(elements.exceptionWrap.hidden, false);
  assert.equal(elements.approve.disabled, true);
  elements.requestCode.value = 'manager-shared-code'; elements.requestCode.dispatch('input');
  elements.reason.value = 'Звонок руководителю по известному номеру'; elements.reason.dispatch('input');
  elements.exceptionReason.value = 'GPS на телефоне пока недоступен'; elements.exceptionReason.dispatch('input');
  elements.attest.checked = true; elements.attest.dispatch('change');
  assert.equal(elements.approve.disabled, true, 'owner GPS remains mandatory');
  gpsFailure = true; elements.gpsRetry.onclick();
  assert.equal(elements.approve.disabled, true, 'denied GPS never silently passes');
  assert.match(elements.myGps.textContent, /настройках сайта/);
  gpsFailure = false; elements.gpsRetry.onclick();
  assert.equal(elements.approve.disabled, false);
  await elements.approve.onclick();
  const sent = calls.filter(x => x.route === '/admin/account_approve.json');
  assert.equal(sent.length, 1);
  const body = JSON.parse(sent[0].options.body);
  assert.deepEqual(Object.keys(body).sort(),
    ['device_id', 'gps', 'gps_exception_reason', 'reason', 'request_code', 'verified_out_of_band'].sort());
  assert.equal(body.device_id, 91);
  assert.equal(body.verified_out_of_band, true);
  assert.equal(body.gps.lat, 55.71);
  assert.match(elements.decisionStatus.textContent, /Сервер отказал/);
  assert.equal(elements.approve.disabled, false, 'rejection can be corrected, not reported as success');
  nextReply = {ok: true, status: 200, account_active: true, device_active: true,
    gps_exception_pending: true};
  await elements.approve.onclick();
  assert.match(elements.decisionStatus.textContent, /исключением без GPS/);
  assert.equal(elements.requestCode.value, '', 'one-use code wiped after server success');
  assert.equal(elements.approve.disabled, true);
  elements.forget.onclick();
  assert.equal(storage.has('hard.approval.token'), false);
  assert.equal(elements.workspace.hidden, true);
  console.log('mobile approval page: synthetic auth, principal, GPS, request selection, rejection and approval PASS');
})().catch(e => {console.error(e); process.exitCode = 1;});
