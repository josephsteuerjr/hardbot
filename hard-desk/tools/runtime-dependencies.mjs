import { extractFile, listPackage } from '@electron/asar';
import { posix, sep } from 'node:path';
import semver from 'semver';

// Inspect the shipped archive, including hoisted/nested transitive packages.
// A source-equivalent main.js still cannot start with an incomplete runtime.
export function auditRuntimeDependencies(archive) {
  const paths = new Set(listPackage(archive).map(p => p.replaceAll('\\', '/').replace(/^\//, '')));
  const read = name => JSON.parse(extractFile(archive, name.split('/').join(sep)).toString('utf8'));
  const visited = new Set();
  function visit(directory) {
    if (visited.has(directory)) return;
    visited.add(directory);
    const pkg = read(posix.join(directory, 'package.json'));
    const main = posix.normalize(posix.join(directory, pkg.main || 'index.js'));
    if (main.startsWith('../') || ![main, main + '.js', main + '.json', posix.join(main, 'index.js')].some(p => paths.has(p)))
      throw new Error('Missing runtime entry point: ' + (pkg.name || 'application') + ' -> ' + main);
    for (const [name, range] of Object.entries(pkg.dependencies || {})) {
      if (Object.hasOwn(pkg.optionalDependencies || {}, name)) continue;
      let base = directory, found;
      for (;;) {
        const candidate = posix.join(base, 'node_modules', name);
        if (paths.has(posix.join(candidate, 'package.json'))) { found = candidate; break; }
        if (!base || base === '.') break;
        base = posix.dirname(base);
        if (base === '.') base = '';
      }
      if (!found) throw new Error('Missing runtime dependency: ' + name + ' required by ' + (pkg.name || 'application'));
      const actual = read(posix.join(found, 'package.json'));
      if (!semver.satisfies(actual.version, range))
        throw new Error('Runtime dependency version mismatch: ' + name + '@' + actual.version + ' does not satisfy ' + range);
      visit(found);
    }
  }
  visit('');
  return { runtimePackages: visited.size };
}
