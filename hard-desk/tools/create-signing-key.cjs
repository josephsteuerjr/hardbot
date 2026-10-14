const { generateKeyPairSync, createPublicKey } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const key = path.resolve(__dirname, '../../_local/release-signing.private.pem');
if (!fs.existsSync(key)) {
  const pair = generateKeyPairSync('ed25519');
  fs.writeFileSync(key, pair.privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600 });
}
const publicKey = createPublicKey(fs.readFileSync(key)).export({ type: 'spki', format: 'pem' });
fs.writeFileSync(path.resolve(__dirname, '../src/main/release-public-key.json'), JSON.stringify({ pem: publicKey }, null, 2));
console.log('Signing key ready. Private key remains in _local.');
