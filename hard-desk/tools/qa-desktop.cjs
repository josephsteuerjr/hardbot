const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const out = process.env.HARD_QA_OUT || path.resolve(__dirname, '../../_local/redesign-2026-10-09/renderer');
fs.mkdirSync(out, { recursive: true });
app.setPath('userData', path.join(out, 'electron-test-data'));
app.disableHardwareAcceleration();
app.on('window-all-closed', () => {});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
app.whenReady().then(async () => {
  const results = [];
  try {
    for (const role of ['manager', 'full', 'security']) {
      process.env.HARD_QA_ROLE = role;
      for (const theme of ['light', 'dark']) {
        process.env.HARD_QA_THEME = theme;
        const w = new BrowserWindow({ width: 1440, height: 950, show: false,
          webPreferences: { partition: `qa-${role}-${theme}-${Date.now()}`, preload: path.join(__dirname, 'qa-desktop-preload.cjs'), contextIsolation: false, sandbox: false, backgroundThrottling: false } });
        const js = code => w.webContents.executeJavaScript(code);
        await w.loadFile(path.join(__dirname, '../out/ui/index.html'));
        await sleep(1000);
        await js('(()=>{const s=document.createElement("style");s.textContent="*,*::before,*::after{transition:none!important;animation:none!important}#app{--rail-track:var(--rail-w)!important;--panel-track:0px!important}#app.with-panel{--panel-track:var(--panel-w)!important}";document.head.append(s)})()');
        assert.equal(await js('window.__errors.length'), 0, await js('JSON.stringify(window.__errors)'));
        const rails = await js('[...document.querySelectorAll("[data-go]")].map(b=>b.dataset.go)');
        assert.equal(rails.includes('control'), role !== 'manager');
        assert.equal(rails.includes('links'), role !== 'security');
        if (role === 'security') await js('document.querySelector("[data-go=workspace]").click()');
        await sleep(200);
        assert.equal(await js('document.querySelectorAll("#crm-table tbody tr").length'), 3);
        const layout = await js('(()=>{const a=document.getElementById("app"),r=document.getElementById("rail");return {app: a.className,track:getComputedStyle(a).getPropertyValue("--rail-track"),railWidth:getComputedStyle(r).width,x:r.getBoundingClientRect().x,grid:getComputedStyle(a).gridTemplateColumns}})()');
        fs.writeFileSync(path.join(out, `${role}-${theme}-layout.json`), JSON.stringify(layout));
        await w.webContents.capturePage(); await sleep(100);
        fs.writeFileSync(path.join(out, `${role}-${theme}-1440.png`), (await w.webContents.capturePage()).toPNG());
        await js('document.querySelector("[data-office]").value="north";document.querySelector("[data-office]").dispatchEvent(new Event("change"))');
        await sleep(150);
        assert.equal(await js('document.querySelectorAll("#crm-table tbody tr").length'), 2);
        assert.equal(await js('document.querySelector(".crm-kpi b").textContent'), '20');
        await js('document.querySelector("#crm-table tbody tr").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');
        await sleep(150);
        assert.equal(await js('document.getElementById("app").classList.contains("with-panel")'), true);
        fs.writeFileSync(path.join(out, `${role}-${theme}-detail.png`), (await w.webContents.capturePage()).toPNG());
        await js('document.querySelector("#panel-close").click();document.querySelector("[data-profile=soft]").click()');
        await sleep(150);
        assert.equal(await js('document.querySelectorAll("#crm-table tbody tr").length'), 1);
        assert.equal(await js('document.querySelector(".crm-kpi b").textContent'), '32');
        await js('document.querySelector("#crm-table").dispatchEvent(new MouseEvent("contextmenu",{bubbles:true,clientX:300,clientY:300}))');
        assert.equal(await js('!!document.querySelector(".brand-menu")'), true);
        await js('document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape"}))');
        assert.equal(await js('!!document.querySelector(".brand-menu")'), false);
        await js('document.querySelector(".update-button").click()'); await sleep(50);
        assert.equal(await js('!!document.querySelector(".update-pop")'), true);
        await js('document.querySelector("[data-dismiss]").click()');
        w.setSize(1024, 768); w.webContents.setZoomFactor(1.25); await sleep(150);
        fs.writeFileSync(path.join(out, `${role}-${theme}-125pct.png`), (await w.webContents.capturePage()).toPNG());
        assert.equal(await js('window.__errors.length'), 0, await js('JSON.stringify(window.__errors)'));
        results.push({ role, theme, filters: true, detail: true, keyboard: true, menus: true });
        w.destroy();
      }
    }
    fs.writeFileSync(path.join(out, 'RESULTS.json'), JSON.stringify(results, null, 2));
    console.log('Renderer QA passed: manager/full/security, hard/soft slices, office KPI scope, details, keyboard, custom menus, light/dark and 125% zoom.');
    app.exit(0);
  } catch (error) { console.error(error); app.exit(1); }
});
