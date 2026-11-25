// In-memory TypeScript unit checks; synthetic DOM and operations only.
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const esbuild = require('esbuild');

function load(name) {
  const code = esbuild.buildSync({
    entryPoints: [path.join(__dirname, '../ui/kit', name + '.ts')],
    bundle: true, platform: 'node', format: 'cjs', write: false,
  }).outputFiles[0].text;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module, exports: module.exports, AbortController, URLSearchParams,
    setTimeout, clearTimeout, console,
  });
  return module.exports;
}

const { ViewScope } = load('view-scope');
const { ActionRunner, ActionTargetLockedError } = load('async-action');
const { DialogManager } = load('dialog');
const { serializeListQuery, listQuerySearchParams } = load('list-query');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const results = [];
async function test(name, body) {
  try { await body(); results.push(true); console.log('PASS ' + name); }
  catch (error) { results.push(false); console.error('FAIL ' + name + ': ' + error.stack); }
}

class FakeElement {
  constructor(document, { focusable = false, dialog = false } = {}) {
    this.ownerDocument = document;
    this.focusable = focusable;
    this.isDialog = dialog;
    this.children = [];
    this.parentElement = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this.hidden = false;
    this.open = false;
    this.tabIndex = focusable ? 0 : -1;
    this.isConnected = true;
    this.focusCount = 0;
  }
  append(...children) {
    for (const child of children) { child.parentElement = this; this.children.push(child); }
  }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  emit(type, event = {}) {
    event.type = type;
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    for (const listener of this.listeners.get(type) || []) listener(event);
    return event;
  }
  setAttribute(key, value) { this.attributes.set(key, String(value)); }
  getAttribute(key) { return this.attributes.has(key) ? this.attributes.get(key) : null; }
  hasAttribute(key) { return this.attributes.has(key); }
  removeAttribute(key) { this.attributes.delete(key); }
  showModal() { assert.equal(this.isDialog, true); this.open = true; }
  close() { this.open = false; this.emit('close'); }
  focus() { this.ownerDocument.activeElement = this; this.focusCount += 1; }
  contains(element) {
    for (let current = element; current; current = current.parentElement) if (current === this) return true;
    return false;
  }
  closest(selector) {
    if (selector !== '[hidden], [inert]') return null;
    for (let current = this; current; current = current.parentElement) {
      if (current.hidden || current.hasAttribute('inert')) return current;
    }
    return null;
  }
  querySelectorAll(selector) {
    assert.equal(selector.includes('button'), true);
    return this.children.filter(child => child.focusable);
  }
  querySelector(selector) {
    if (selector === '[autofocus]') return this.children.find(child => child.hasAttribute('autofocus')) || null;
    if (selector.startsWith('#')) return this.children.find(child => child.getAttribute('id') === selector.slice(1)) || null;
    return null;
  }
}

function fakeDialog() {
  const document = { activeElement: null };
  const opener = new FakeElement(document, { focusable: true });
  const dialog = new FakeElement(document, { dialog: true });
  const first = new FakeElement(document, { focusable: true });
  const second = new FakeElement(document, { focusable: true });
  dialog.append(first, second);
  document.activeElement = opener;
  return { document, opener, dialog, first, second };
}

(async () => {
  await test('view-scope delivers only the latest read and aborts the old signal', async () => {
    const scope = new ViewScope(), first = deferred(), second = deferred();
    let firstSignal, applied = [];
    const one = scope.read(signal => { firstSignal = signal; return first.promise; }, value => applied.push(value));
    const two = scope.read(() => second.promise, value => applied.push(value));
    assert.equal(firstSignal.aborted, true);
    second.resolve('fresh');
    assert.equal(await two, 'fresh');
    first.resolve('stale');
    assert.equal(await one, undefined);
    assert.deepEqual(applied, ['fresh']);
    assert.equal(scope.valid, true);
  });

  await test('view-scope dispose aborts reads, suppresses late errors and prevents new work', async () => {
    const scope = new ViewScope(), pending = deferred();
    let signal, called = false;
    const read = scope.read(value => { signal = value; return pending.promise; });
    scope.dispose();
    assert.equal(scope.valid, false);
    assert.equal(signal.aborted, true);
    pending.reject(new Error('late failure'));
    assert.equal(await read, undefined);
    assert.equal(await scope.read(() => { called = true; }), undefined);
    assert.equal(called, false);
    scope.invalidate();
    scope.dispose();
  });

  await test('view-scope propagates a current read error', async () => {
    const scope = new ViewScope();
    await assert.rejects(scope.read(() => { throw new Error('read failed'); }), /read failed/);
  });

  await test('action-runner keeps one intent through all phases', async () => {
    const phases = [], seen = [];
    const intent = { operation_id: 'operation-1', payload: 42 };
    const runner = new ActionRunner(state => phases.push(state.phase));
    const result = await runner.run({
      target: 'contract:1', intent,
      validate: value => { seen.push(value); },
      persist: async value => { seen.push(value); },
      submit: value => { seen.push(value); return { phase: 'accepted', value: 'receipt' }; },
    });
    assert.equal(result.phase, 'accepted');
    assert.equal(result.value, 'receipt');
    assert.equal(result.operationId, 'operation-1');
    assert.deepEqual(phases, ['validating', 'persisting', 'submitting', 'accepted']);
    assert.equal(seen.length, 3);
    assert.ok(seen.every(value => value === intent));
  });

  await test('action-runner targeted lock deduplicates only the same operation', async () => {
    const gate = deferred();
    let submissions = 0;
    const runner = new ActionRunner();
    const request = {
      target: 'contract:1', intent: { operation_id: 'same-operation' },
      submit: async () => { submissions += 1; await gate.promise; return { phase: 'accepted', value: 1 }; },
    };
    const first = runner.run(request);
    assert.equal(runner.isBusy('contract:1'), true);
    assert.equal(runner.run(request), first);
    const locked = await runner.run({
      target: 'contract:1', intent: { operation_id: 'different-operation' },
      submit: () => { throw new Error('must not submit'); },
    });
    assert.equal(locked.phase, 'error');
    assert.ok(locked.error instanceof ActionTargetLockedError);
    const other = await runner.run({
      target: 'contract:2', intent: { operation_id: 'other-target' },
      submit: () => ({ phase: 'queued' }),
    });
    assert.equal(other.phase, 'queued');
    gate.resolve();
    assert.equal((await first).phase, 'accepted');
    assert.equal(submissions, 1);
    assert.equal(runner.isBusy('contract:1'), false);
  });

  await test('action-runner never retransmits an unknown or terminal operation', async () => {
    let submissions = 0;
    const runner = new ActionRunner();
    const request = {
      target: 'contract:unknown', intent: { operation_id: 'uncertain' },
      submit: async () => { submissions += 1; throw new Error('connection lost after send'); },
    };
    const first = await runner.run(request);
    const replay = await runner.run(request);
    assert.equal(first.phase, 'unknown');
    assert.equal(replay.phase, 'unknown');
    assert.equal(replay.error, first.error);
    assert.equal(submissions, 1);
    await runner.run({
      target: 'contract:unknown', intent: { operation_id: 'intervening-operation' },
      submit: () => ({ phase: 'accepted', value: null }),
    });
    assert.equal((await runner.run(request)).phase, 'unknown');
    assert.equal(submissions, 1);
  });

  await test('action-runner explicit retry after definitive error reuses the intent', async () => {
    let submissions = 0;
    const runner = new ActionRunner();
    const request = {
      target: 'contract:error', intent: { operation_id: 'stable-after-error' },
      submit: () => {
        submissions += 1;
        return submissions === 1 ? { phase: 'error', error: new Error('rejected') } : { phase: 'accepted', value: 'ok' };
      },
    };
    assert.equal((await runner.run(request)).phase, 'error');
    assert.equal((await runner.run(request)).phase, 'accepted');
    assert.equal((await runner.run(request)).phase, 'accepted');
    assert.equal(submissions, 2);
  });

  await test('action-runner validation and persistence failures do not submit', async () => {
    for (const stage of ['validate', 'persist']) {
      let submitted = false;
      const runner = new ActionRunner();
      const request = {
        target: 'contract:' + stage, intent: { operation_id: stage },
        validate: () => { if (stage === 'validate') throw new Error('invalid'); },
        persist: () => { if (stage === 'persist') throw new Error('not durable'); },
        submit: () => { submitted = true; return { phase: 'accepted', value: null }; },
      };
      const result = await runner.run(request);
      assert.equal(result.phase, 'error');
      assert.equal(submitted, false);
    }
  });

  await test('action-runner state observer cannot change the outcome', async () => {
    const runner = new ActionRunner(() => { throw new Error('render failed'); });
    const result = await runner.run({
      target: 'observer', intent: { operation_id: 'observer' },
      submit: () => ({ phase: 'accepted', value: 7 }),
    });
    assert.equal(result.phase, 'accepted');
    assert.equal(result.value, 7);
  });

  await test('dialog initial focus, tab trap and focus return use native dialog lifecycle', async () => {
    const { document, opener, dialog, first, second } = fakeDialog();
    second.setAttribute('autofocus', '');
    let closed = 0;
    const manager = new DialogManager(dialog, { onClose: () => { closed += 1; } });
    manager.show();
    assert.equal(dialog.open, true);
    assert.equal(document.activeElement, second);
    let event = dialog.emit('keydown', { key: 'Tab', shiftKey: false });
    assert.equal(event.defaultPrevented, true);
    assert.equal(document.activeElement, first);
    event = dialog.emit('keydown', { key: 'Tab', shiftKey: true });
    assert.equal(event.defaultPrevented, true);
    assert.equal(document.activeElement, second);
    assert.equal(await manager.requestClose(), true);
    assert.equal(dialog.open, false);
    assert.equal(document.activeElement, opener);
    assert.equal(closed, 1);
    dialog.emit('close');
    assert.equal(closed, 1);
    manager.dispose();
  });

  await test('dialog dirty close requires confirmation', async () => {
    const { dialog } = fakeDialog();
    let confirmations = 0, allow = false;
    const manager = new DialogManager(dialog, {
      confirmDiscard: () => { confirmations += 1; return allow; },
    });
    manager.show();
    manager.setDirty(true);
    assert.equal(await manager.requestClose(), false);
    assert.equal(dialog.open, true);
    allow = true;
    assert.equal(await manager.requestClose(), true);
    assert.equal(confirmations, 2);
    assert.equal(manager.dirty, false);
    manager.dispose();
  });

  await test('dialog submitting blocks button and native cancel close paths', async () => {
    const { dialog } = fakeDialog();
    let confirmations = 0;
    const manager = new DialogManager(dialog, { confirmDiscard: () => { confirmations += 1; return true; } });
    manager.show();
    manager.setDirty(true);
    manager.setSubmitting(true);
    assert.equal(dialog.getAttribute('aria-busy'), 'true');
    assert.equal(await manager.requestClose(), false);
    const cancel = dialog.emit('cancel');
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(cancel.defaultPrevented, true);
    assert.equal(dialog.open, true);
    assert.equal(confirmations, 0);
    manager.setSubmitting(false);
    assert.equal(await manager.requestClose(), true);
    manager.dispose();
  });

  await test('dialog close confirmation is single-flight and rechecks submitting', async () => {
    const { dialog } = fakeDialog();
    const confirmation = deferred();
    let prompts = 0;
    const manager = new DialogManager(dialog, {
      confirmDiscard: () => { prompts += 1; return confirmation.promise; },
    });
    manager.show();
    manager.setDirty(true);
    const first = manager.requestClose(), second = manager.requestClose();
    manager.setSubmitting(true);
    confirmation.resolve(true);
    assert.equal(await first, false);
    assert.equal(await second, false);
    assert.equal(prompts, 1);
    assert.equal(dialog.open, true);
    manager.dispose();
  });

  await test('dialog disconnected opener uses fallback and native cancel closes clean form', async () => {
    const { document, opener, dialog } = fakeDialog();
    const fallback = new FakeElement(document, { focusable: true });
    opener.isConnected = false;
    const manager = new DialogManager(dialog, { returnFocus: () => fallback });
    manager.show();
    dialog.emit('cancel');
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(dialog.open, false);
    assert.equal(document.activeElement, fallback);
    manager.dispose();
  });

  await test('dialog native method=dialog submit uses guarded close', async () => {
    const { dialog } = fakeDialog();
    let confirmations = 0;
    const manager = new DialogManager(dialog, { confirmDiscard: () => { confirmations += 1; return false; } });
    manager.show();
    manager.setDirty(true);
    const submit = dialog.emit('submit', { target: { method: 'dialog' }, submitter: null });
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(submit.defaultPrevented, true);
    assert.equal(confirmations, 1);
    assert.equal(dialog.open, true);
    manager.dispose();
  });

  const baseQuery = {
    scope: { actor: 'actor-1', role: 'manager', territory: 'TJ', revision: 3, access_model: 2 },
    search: 'Алексей',
    filters: { status: ['open', 'promised'], assigned: true, nested: { from: 2, to: null } },
    sort: [{ key: 'name', direction: 'asc' }],
    cursor: null,
    limit: 50,
  };

  await test('list-query serialization is canonical and scoped', async () => {
    const reordered = {
      limit: 50, cursor: null, sort: baseQuery.sort, filters: { nested: { to: null, from: 2 }, assigned: true, status: baseQuery.filters.status },
      search: 'Алексей', scope: { revision: 3, territory: 'TJ', role: 'manager', actor: 'actor-1', access_model: 2 },
    };
    assert.equal(serializeListQuery(baseQuery), serializeListQuery(reordered));
    assert.equal(serializeListQuery(baseQuery).includes('"actor":"actor-1"'), true);
    for (const [key, value] of [['actor', 'actor-2'], ['role', 'owner'], ['territory', 'UZ'], ['revision', 4], ['access_model', 1]]) {
      const changed = { ...baseQuery, scope: { ...baseQuery.scope, [key]: value } };
      assert.notEqual(serializeListQuery(changed), serializeListQuery(baseQuery), key + ' absent from scope key');
    }
    const reversed = { ...baseQuery, filters: { ...baseQuery.filters, status: ['promised', 'open'] } };
    assert.notEqual(serializeListQuery(reversed), serializeListQuery(baseQuery));
  });

  await test('list-query URL serialization retains scope and canonical structured values', async () => {
    const params = listQuerySearchParams(baseQuery);
    assert.equal(params.get('actor'), 'actor-1');
    assert.equal(params.get('role'), 'manager');
    assert.equal(params.get('territory'), 'TJ');
    assert.equal(params.get('revision'), '3');
    assert.equal(params.get('access_model'), '2');
    assert.equal(params.get('filters'), '{"assigned":true,"nested":{"from":2,"to":null},"status":["open","promised"]}');
    assert.equal(params.get('sort'), '[{"direction":"asc","key":"name"}]');
    assert.equal(params.has('cursor'), false);
    assert.equal(params.get('limit'), '50');
  });

  await test('list-query rejects ambiguous or invalid serialization', async () => {
    const cyclic = { ...baseQuery, filters: {} };
    cyclic.filters.self = cyclic.filters;
    assert.throws(() => serializeListQuery(cyclic), /Circular/);
    assert.throws(() => serializeListQuery({ ...baseQuery, filters: { value: NaN } }), /Non-finite/);
    assert.throws(() => serializeListQuery({ ...baseQuery, filters: { value: [undefined] } }), /Undefined/);
    assert.throws(() => serializeListQuery({ ...baseQuery, filters: { value: new Date(0) } }), /Non-plain/);
    assert.throws(() => serializeListQuery({ ...baseQuery, scope: { ...baseQuery.scope, revision: -1 } }), /revision/);
    assert.throws(() => serializeListQuery({ ...baseQuery, limit: 0 }), /limit/);
  });

  console.log(`UI kit primitives: ${results.filter(Boolean).length}/${results.length} passed`);
  if (results.some(result => !result)) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
