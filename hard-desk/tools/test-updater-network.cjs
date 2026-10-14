// Exercise the real downloader and its persisted cache; never execute a payload.
const { app } = require('electron');
const { NsisUpdater } = require('electron-updater');
const { ElectronHttpExecutor } = require('electron-updater/out/electronHttpExecutor');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const dir = path.resolve(__dirname, '../../_local/redesign-2026-10-09/updater-network');
fs.mkdirSync(dir, { recursive: true });
app.setPath('userData', path.join(dir, 'electron'));
app.whenReady().then(async () => {
  let corrupted = false, downloads = 0;
  const bytes = Buffer.from('MZ synthetic update bytes; deliberately never executed');
  const checksum = crypto.createHash('sha512').update(bytes).digest('base64');
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/soft.yml')) {
      res.setHeader('Content-Type', 'text/yaml'); res.end(`version: 3.0.1\nfiles:\n  - url: soft-3.0.1-setup.exe\n    sha512: ${checksum}\n    size: ${bytes.length}\npath: soft-3.0.1-setup.exe\nsha512: ${checksum}\n`);
    } else if (req.url.startsWith('/soft-3.0.1-setup.exe')) { downloads++; res.end(corrupted ? Buffer.from('MZ damaged file') : bytes); }
    else { res.statusCode = 404; res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  const config = path.join(dir, 'app-update.yml');
  fs.writeFileSync(config, `provider: generic\nurl: ${url}\nchannel: soft\nupdaterCacheDirName: network-qa-${Date.now()}\n`);
  const adapter = { version: '3.0.0', name: 'hard-soft-qa', isPackaged: true, appUpdateConfigPath: config,
    userDataPath: dir, baseCachePath: dir, whenReady: async () => {}, onQuit: () => {}, quit: () => {}, relaunch: () => {} };
  function updater(channel = 'soft') {
    const u = new NsisUpdater(null, adapter);
    u.httpExecutor = new ElectronHttpExecutor(); u.setFeedURL({ provider: 'generic', url, channel, useMultipleRangeRequest: false });
    u.autoDownload = false; u.autoInstallOnAppQuit = false;
    u.disableDifferentialDownload = true; u.logger = { info() {}, debug() {}, warn() {}, error() {} }; u.on('error', () => {});
    return u;
  }
  try {
    const first = updater(); const result = await first.checkForUpdates(); assert.equal(result.isUpdateAvailable, true);
    const files = await first.downloadUpdate(); assert.deepEqual(fs.readFileSync(files[0]), bytes);
    assert.equal(downloads, 1);
    const restarted = updater(); await restarted.checkForUpdates(); await restarted.downloadUpdate(); assert.equal(downloads, 1);
    fs.writeFileSync(files[0], 'broken cached payload');
    const recover = updater(); await recover.checkForUpdates(); await recover.downloadUpdate(); assert.equal(downloads, 2);
    assert.deepEqual(fs.readFileSync(files[0]), bytes);
    fs.writeFileSync(files[0], 'broken cached payload'); corrupted = true;
    const bad = updater(); await bad.checkForUpdates(); await assert.rejects(() => bad.downloadUpdate());
    assert.equal(fs.existsSync(files[0]), false);
    await assert.rejects(() => updater('manager').checkForUpdates());
    console.log('Real updater passed: product channel, download checksum, restart/cache reuse, corrupted-cache recovery, damaged-package rejection, missing channel. No installer executed.');
    fs.writeFileSync(path.join(dir, 'RESULTS.json'), JSON.stringify({ downloads, checksum: true, cacheRestart: true, cacheRecovery: true, corruptRejected: true, wrongChannelRejected: true }));
    server.close(); app.exit(0);
  } catch (e) { console.error(e); server.close(); app.exit(1); }
});
