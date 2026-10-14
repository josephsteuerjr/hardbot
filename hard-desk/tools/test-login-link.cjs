const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const esbuild = require('esbuild');
const code = esbuild.buildSync({entryPoints:[path.join(__dirname,'../src/main/link.ts')],bundle:true,platform:'node',format:'cjs',write:false}).outputFiles[0].text;
const moduleLink = {exports:{}};
vm.runInNewContext(code,{module:moduleLink,exports:moduleLink.exports,require});
const {splitLink} = moduleLink.exports;
const expected = {base:'https://synthetic.invalid',code:'test_code123'};
for (const text of ['https://synthetic.invalid/i/test_code123','Synthetic staff — https://synthetic.invalid/i/test_code123','<https://synthetic.invalid/i/test_code123>','https://synthetic.invalid/#link=test_code123']) {
  assert.deepEqual(JSON.parse(JSON.stringify(splitLink(text))), expected);
}
assert.equal(splitLink('Staff A — https://synthetic.invalid/i/test_code123\nStaff B — https://synthetic.invalid/i/other_code'),null);
assert.equal(splitLink('https://user:pass@synthetic.invalid/i/test_code123'),null);
assert.equal(splitLink('https://synthetic.invalid/i/test_code123/other'),null);
assert.equal(splitLink('junk /i/test_code123'),null);
assert.equal(splitLink('http://localhost:8080/i/test_code123').base,'http://localhost:8080');
assert.equal(splitLink('http://localhost.evil.invalid/i/test_code123').base,'https://localhost.evil.invalid');
assert.equal(splitLink('http://10.110.44.25:8080/i/test_code123').base,'http://10.110.44.25:8080');
assert.equal(splitLink('http://192.168.1.5/i/test_code123').base,'http://192.168.1.5');
assert.equal(splitLink('http://172.20.10.3/i/test_code123').base,'http://172.20.10.3');
assert.equal(splitLink('http://[fd00::1]:8080/i/test_code123').base,'http://[fd00::1]:8080');
assert.equal(splitLink('http://172.32.0.1/i/test_code123').base,'https://172.32.0.1');
assert.equal(splitLink('http://8.8.8.8/i/test_code123').base,'https://8.8.8.8');
assert.equal(splitLink('http://hard.corp.local/i/test_code123').base,'https://hard.corp.local');
assert.equal(splitLink('http://[fe80::1]/i/test_code123').base,'https://[fe80::1]');
console.log('Login link parser: labelled line, legacy URL, credentials, ambiguous input and TLS policy (private-range plain http) passed');
