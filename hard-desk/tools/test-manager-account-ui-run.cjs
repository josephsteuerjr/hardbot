// Run two offscreen Electron journeys against isolated synthetic profiles;
// remove each only after Chromium has fully exited on Windows.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const exe = path.join(__dirname, '../node_modules/electron/dist/electron.exe');
const script = path.join(__dirname, 'test-manager-account-ui.cjs');
async function run(legacy) {
  const temp = fs.mkdtempSync(path.join(__dirname, '../out/manager-ui-'));
  const env = { ...process.env, HARD_MANAGER_QA_TEMP: temp, HARD_MANAGER_QA_LEGACY: legacy ? '1' : '0' };
  delete env.ELECTRON_RUN_AS_NODE;
  let code;
  try {
    code = await new Promise(resolve => {
      const child = spawn(exe, [script], { windowsHide: true, stdio: 'inherit', env });
      child.on('error', error => { console.error(error); resolve(1); });
      child.on('exit', value => resolve(typeof value === 'number' ? value : 1));
    });
  } finally {
    fs.rmSync(temp, { recursive: true, force: true, maxRetries: 30, retryDelay: 200 });
  }
  return code;
}
(async () => {
  if (await run(false)) { process.exitCode = 1; return; }
  process.exitCode = await run(true);
})().catch(error => { console.error(error); process.exitCode = 1; });
