const assert = require('node:assert/strict');
const { test } = require('node:test');
const { buildSync } = require('esbuild');

const compiled = buildSync({
  entryPoints: [require('node:path').join(__dirname, 'index.ts')],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  write: false,
}).outputFiles[0].text;
const loaded = { exports: {} };
new Function('module', 'exports', 'require', compiled)(loaded, loaded.exports, require);
const { WorkspaceHost, WorkspaceRegistry, workspaceManifestModules } = loaded.exports;

function module(id, mount = () => {}) {
  return { id, label: id, mount };
}

function host(modules, options = {}) {
  return new WorkspaceHost({
    registry: new WorkspaceRegistry().register(...modules),
    view: {},
    onError(error) { throw error; },
    ...options,
  });
}

test('registry rejects duplicate factories and manifest reuses registered mounts', () => {
  const registry = new WorkspaceRegistry().register(module('alpha'));
  assert.throws(() => registry.register(module('alpha')), /Duplicate workspace module/);
  const manifest = { modules: [{ id: 'alpha', title: 'Server title', mount() { throw Error('not allowed'); } }] };
  const [resolved] = registry.resolve(workspaceManifestModules(manifest));
  assert.equal(resolved.title, 'Server title');
  assert.equal(resolved.mount, registry.get('alpha').mount);
});

test('invalid routes fall back to a valid default without mounting unknown modules', async () => {
  const mounted = [];
  const workspace = host([
    module('alpha', () => { mounted.push('alpha'); }),
    module('beta', () => { mounted.push('beta'); }),
  ]);
  await workspace.open({ modules: ['alpha', 'unknown', 'beta'], defaultModule: 'beta' }, 'missing');
  assert.equal(workspace.currentRoute, 'beta');
  await workspace.navigate('still-missing');
  assert.equal(workspace.currentRoute, 'beta');
  await workspace.navigate('alpha');
  assert.deepEqual(mounted, ['beta', 'beta', 'alpha']);
  workspace.dispose();
});

test('navigation and dispose invalidate contexts and run all disposers once', async () => {
  const events = [];
  let alphaContext;
  const workspace = host([
    module('alpha', context => {
      alphaContext = context;
      context.onDispose(() => events.push('registered'));
      return () => events.push('returned');
    }),
    module('beta', context => {
      context.onDispose(() => events.push('beta'));
    }),
  ]);
  await workspace.open({ modules: ['alpha', 'beta'] });
  assert.equal(alphaContext.valid(), true);
  await workspace.navigate('beta');
  assert.equal(alphaContext.valid(), false);
  assert.deepEqual(events, ['returned', 'registered']);
  workspace.dispose();
  workspace.dispose();
  assert.deepEqual(events, ['returned', 'registered', 'beta']);
  await assert.rejects(workspace.navigate('alpha'), /not open/);
});

test('a stale async mount disposes its result and late registrations', async () => {
  let finish;
  let staleContext;
  const events = [];
  const workspace = host([
    module('alpha', context => {
      staleContext = context;
      return new Promise(resolve => { finish = resolve; });
    }),
    module('beta'),
  ]);
  const opening = workspace.open({ modules: ['alpha', 'beta'] });
  await Promise.resolve();
  await workspace.navigate('beta');
  finish(() => events.push('stale-returned'));
  await opening;
  assert.equal(staleContext.valid(), false);
  staleContext.onDispose(() => events.push('late'));
  assert.deepEqual(events, ['stale-returned', 'late']);
  assert.equal(workspace.currentRoute, 'beta');
  workspace.dispose();
});

test('module state is isolated by scope, survives policy epoch, and is cleared by identity epoch', async () => {
  const workspace = host([module('alpha'), module('beta')]);
  await workspace.open({ modules: ['alpha', 'beta'], identity_epoch: 'actor-a', policy_epoch: 1, scope: 'TJ' });
  workspace.currentContext.setState({ selected: 1 });
  await workspace.open({ modules: ['alpha', 'beta'], identity_epoch: 'actor-a', policy_epoch: 2, scope: 'TJ' });
  assert.deepEqual(workspace.currentContext.getState(), { selected: 1 });
  assert.equal(workspace.currentContext.policyEpoch, '2');
  await workspace.open({ modules: ['alpha', 'beta'], identity_epoch: 'actor-a', policy_epoch: 2, scope: 'UZ' });
  assert.equal(workspace.currentContext.getState(), undefined);
  workspace.currentContext.setState({ selected: 2 });
  await workspace.open({ modules: ['alpha', 'beta'], identity_epoch: 'actor-a', policy_epoch: 2, scope: 'TJ' });
  assert.deepEqual(workspace.currentContext.getState(), { selected: 1 });
  await workspace.open({ modules: ['alpha', 'beta'], identity_epoch: 'actor-b', policy_epoch: 2, scope: 'TJ' });
  assert.equal(workspace.currentContext.getState(), undefined);
  workspace.dispose();
});

test('policy epoch removes an invalid route but retains the surviving module state', async () => {
  let leaves = 0;
  const workspace = host([module('alpha'), module('beta')], { onLeave() { leaves++; } });
  await workspace.open({ modules: ['alpha', 'beta'], identity_epoch: 'actor', policy_epoch: 1, default_module: 'alpha' });
  workspace.currentContext.setState('saved');
  await workspace.navigate('beta');
  await workspace.open({ modules: ['alpha'], identity_epoch: 'actor', policy_epoch: 2, default_module: 'alpha' });
  assert.equal(workspace.currentRoute, 'alpha');
  assert.equal(workspace.currentContext.getState(), 'saved');
  assert.equal(leaves, 2);
  workspace.dispose();
});

test('server tree flattens child routes and uses revision/defaultModule epochs', async () => {
  const manifest = {
    revision: 7,
    defaultModule: 'script',
    tree: [{ id: 'script', title: 'Скрипты и задачи', children: [{ id: 'tasks', title: 'Задачи' }] }],
  };
  const flattened = workspaceManifestModules(manifest);
  assert.equal(flattened[1].hidden, true);
  assert.equal(flattened[1].active, 'script');
  const workspace = host([module('script'), module('tasks')]);
  await workspace.open(manifest, 'tasks');
  assert.equal(workspace.currentRoute, 'tasks');
  assert.equal(workspace.policyEpoch, '7');
  workspace.dispose();
});
