// Actual Electron renderer journey; network and credentials replaced by synthetic preload.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
// Parent test runner owns the synthetic profile and removes it only after
// Electron exits: Windows holds Chromium handles until process teardown.
if (!process.env.HARD_MANAGER_QA_TEMP) throw new Error('Use test-manager-account-ui-run.cjs');
app.setPath('userData', process.env.HARD_MANAGER_QA_TEMP);
app.disableHardwareAcceleration();
app.on('window-all-closed', () => {});
const nap = ms => new Promise(resolve => setTimeout(resolve, ms));
app.whenReady().then(async () => {
  let w;
  try {
    w = new BrowserWindow({ width: 1100, height: 780, show: false,
      webPreferences: { preload: path.join(__dirname, 'test-manager-account-preload.cjs'),
        contextIsolation: false, sandbox: false, backgroundThrottling: false, offscreen: true } });
    await w.loadFile(path.join(__dirname, '../out/ui/index.html'));
    const js = code => w.webContents.executeJavaScript(code).catch(error=>{console.error(code);throw error;});
    const until = async (condition) => {
      for (let i = 0; i < 80; i++) { if (await js(condition)) return; await nap(50); }
      throw new Error('UI condition timed out: ' + condition + ' ' + await js('JSON.stringify(window.__errors)'));
    };
    if(process.env.HARD_MANAGER_QA_LEGACY==='1'){
      await until('!!document.querySelector("[data-go=links]")');
      await js('document.querySelector("[data-go=links]").click()');
      await until('!!document.querySelector("#entered-tbl [data-tsearch]")');
      assert.equal(await js('document.querySelectorAll("[data-device]").length'),0);
      assert.equal(await js('window.__errors.length'),0);
      console.log('Manager UI: legacy session cannot approve account/device/GPS');app.exit(0);return;
    }
    await until('!!document.querySelector("#manager-go")');
    assert.equal(await js('!!document.querySelector("#manager-pane-link")'),false);
    await js('document.querySelector("#manager-login").value="synthetic";document.querySelector("#manager-pass").value="Oldpass123";document.querySelector("#manager-go").click()');
    await until('!!document.querySelector("#pw-go")');
    await js('document.querySelector("#pw-new").value="Newpass123";document.querySelector("#pw-repeat").value="Newpass123";document.querySelector("#pw-go").click()');
    await until('!!document.querySelector("#pending-manager-check")');
    assert.equal(await js('!!document.querySelector("[data-go=crm]")'),false);
    assert.equal(await js('window.__account.requests[1].path'),'/api/account/password');
    await js('window.__account.stage="approved";document.querySelector("#pending-manager-check").click()');
    await until('!!document.querySelector("[data-go=crm]")');
    await js('document.querySelector("[data-go=links]").click()');
    await until('!![...document.querySelectorAll("[data-device]")].find(x=>x.dataset.device==="91")');
    assert.equal(await js('!![...document.querySelectorAll("[data-device]")].find(x=>x.dataset.device==="92")'),false);
    assert.equal(await js('!![...document.querySelectorAll("[data-device]")].find(x=>x.dataset.device==="93")'),false);
    await js('[...document.querySelectorAll("[data-device]")].find(x=>x.dataset.device==="91").click()');
    await until('!!document.querySelector("#ask [data-yes]")');
    assert.match(await js('document.querySelector("#ask").textContent'),/лично видите.*ФИО/);
    await js('document.querySelector("#ask [data-no]").click()');
    assert.equal(await js('window.__account.requests.some(r=>r.path==="/api/manager/account_approve")'),false);
    await js('[...document.querySelectorAll("[data-device]")].find(x=>x.dataset.device==="91").click()');
    await until('!!document.querySelector("#ask [data-yes]")');
    await js('document.querySelector("#ask [data-yes]").click()');
    await until('![...document.querySelectorAll("[data-device]")].find(x=>x.dataset.device==="91")');
    assert.equal(await js('window.__account.requests.at(-1).body.observed_pc'),true);
    await js('window.prompt=()=>{throw new Error("Phone approval must not ask for GPS reason or duration")};[...document.querySelectorAll("[data-device]")].find(x=>x.dataset.device==="94").click()');
    await until('!!document.querySelector("#ask [data-yes]")');
    await js('document.querySelector("#ask [data-yes]").click()');
    await until('![...document.querySelectorAll("[data-device]")].find(x=>x.dataset.device==="94")');
    assert.equal(await js('"gps_exception_minutes" in window.__account.requests.at(-1).body'),false);
    assert.equal(await js('window.__account.requests.some(r=>r.path==="/api/manager/account_phone_request")'),false);
    assert.equal(await js('window.__errors.length'),0,await js('JSON.stringify(window.__errors)'));
    console.log('Manager Electron UI: login/password -> pending -> observed PC cancel/approve -> own phone approval without GPS PASS');
    app.exit(0);
  } catch (error) { console.error(error); app.exit(1); }
  finally { if (w && !w.isDestroyed()) w.destroy(); }
}).catch(error => { console.error(error); app.exit(1); });
