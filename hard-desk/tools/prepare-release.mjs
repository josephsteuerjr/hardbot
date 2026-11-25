// Produce an exact upload allowlist; publication is a separate operation.
import { createHash, createPublicKey, sign } from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';
import { execFileSync } from 'node:child_process';
import { stableProducts } from './products.mjs';
const here = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const version = JSON.parse(readFileSync(join(here, 'package.json'))).version;
// A valid signature on an old binary is still an old, unsafe release.
// Refuse stale payloads before reading the private key or preparing assets.
execFileSync(process.execPath, [join(here, 'tools/audit-installers.mjs')], {cwd:here,stdio:'inherit'});
const key = readFileSync(process.env.HARD_RELEASE_SIGNING_KEY
  ? resolve(process.env.HARD_RELEASE_SIGNING_KEY)
  : resolve(here, '../_local/release-signing.private.pem'));
const pinned = JSON.parse(readFileSync(join(here, 'src/main/release-public-key.json'))).pem;
if (createPublicKey(key).export({ type: 'spki', format: 'pem' }) !== pinned) throw new Error('Signing key differs from the key pinned in the app');
const args=process.argv.slice(2);
if(args.length && (args.length!==2 || args[0]!=='--out')) throw new Error('Usage: prepare-release.mjs [--out directory]');
const output = args.length ? resolve(here,args[1]) : resolve(here, '../_local/redesign-2026-10-09/release', version);
mkdirSync(output, { recursive: true });
const assets = [];
for (const product of stableProducts) {
  const directory = join(here, 'installers', product), file = `${product}-${version}-setup.exe`;
  const exe = join(directory, file), buffer = readFileSync(exe), sha512 = createHash('sha512').update(buffer).digest('base64'), size = statSync(exe).size;
  const channel = product + '.yml', info = yaml.load(readFileSync(join(directory, channel), 'utf8'));
  if (info.version !== version || info.files.length !== 1 || info.files[0].url !== file || info.files[0].sha512 !== sha512 || (info.files[0].size != null && info.files[0].size !== size))
    throw new Error('Updater metadata mismatch for ' + product);
  const payload = Buffer.from(JSON.stringify({ product, version, file, sha512, size }));
  const envelope = { payload: payload.toString('base64'), signature: sign(null, payload, key).toString('base64') };
  const signed = product + '-signed.json';
  info.files[0].size = size;
  copyFileSync(exe, join(output, file)); writeFileSync(join(output, channel), yaml.dump(info));
  writeFileSync(join(output, signed), JSON.stringify(envelope, null, 2));
  assets.push({ product, files: [file, channel, signed], size, sha512 });
}
writeFileSync(join(output, 'ASSETS.json'), JSON.stringify({ version, assets }, null, 2));
console.log(JSON.stringify({ output, version, assets }, null, 2));
