import {createHash,verify} from 'node:crypto';
import {createReadStream,readFileSync,statSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import yaml from 'js-yaml';
const here=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const directory=resolve(process.argv[2]||join(here,'../_local/architecture-review-2026-10-09/release/3.0.0'));
const manifest=JSON.parse(readFileSync(join(directory,'ASSETS.json'),'utf8'));
const pem=JSON.parse(readFileSync(join(here,'src/main/release-public-key.json'),'utf8')).pem;
assert.deepEqual(manifest.assets.map(x=>x.product),['hard','soft','manager','analyst']);
for(const asset of manifest.assets){
  const file=`${asset.product}-${manifest.version}-setup.exe`;
  assert.deepEqual(asset.files,[file,asset.product+'.yml',asset.product+'-signed.json']);
  const envelope=JSON.parse(readFileSync(join(directory,asset.product+'-signed.json'),'utf8'));
  const payload=Buffer.from(envelope.payload,'base64');
  assert.equal(verify(null,payload,pem,Buffer.from(envelope.signature,'base64')),true);
  const signed=JSON.parse(payload);
  assert.equal(signed.product,asset.product);assert.equal(signed.version,manifest.version);assert.equal(signed.file,file);
  assert.equal(statSync(join(directory,file)).size,signed.size);
  const hash=createHash('sha512');for await(const bytes of createReadStream(join(directory,file)))hash.update(bytes);
  assert.equal(hash.digest('base64'),signed.sha512);assert.equal(signed.sha512,asset.sha512);
  const channel=yaml.load(readFileSync(join(directory,asset.product+'.yml'),'utf8'));
  assert.equal(channel.version,signed.version);assert.equal(channel.files.length,1);
  assert.equal(channel.files[0].url,signed.file);assert.equal(channel.files[0].sha512,signed.sha512);assert.equal(channel.files[0].size,signed.size);
}
console.log('Release verified: 4 pinned Ed25519 signatures, 4 installer hashes/sizes, 4 product channels; exact 12-asset allowlist.');
