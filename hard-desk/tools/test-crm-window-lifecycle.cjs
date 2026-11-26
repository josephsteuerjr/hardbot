// Real Electron main process: repeated launch must tolerate a destroyed window.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { app, BrowserWindow, dialog, protocol } = require("electron");

const qa = process.env.HARD_CRM_QA_ROOT;
assert(qa, "Explicit QA directory required");
fs.mkdirSync(qa, { recursive: true });
const desk = path.resolve(__dirname, "..");
process.env.HARD_USER_DATA = path.join(qa, "profile");
process.env.HARD_CRM_SOURCE = path.dirname(desk);
app.getVersion = () => JSON.parse(fs.readFileSync(path.join(desk, "package.json"), "utf8")).version;
const errors = [];
const failure = process.env.HARD_CRM_QA_RENDERER_FAILURE;
if (failure) {
  const handle = protocol.handle.bind(protocol);
  protocol.handle = (scheme, handler) => handle(scheme, (request) => {
    if (failure === 'script' && new URL(request.url).pathname === '/app.js')
      return new Response('', { headers: { 'Content-Type': 'text/javascript' } });
    if (failure === 'document' && new URL(request.url).pathname === '/index.html')
      return Response.error();
    return handler(request);
  });
}
process.on("uncaughtException", error => errors.push(String(error.stack || error)));
dialog.showErrorBox = (title, message) => {
  errors.push(title + "\n" + message);
  fs.writeFileSync(path.join(qa, "startup-error.txt"), errors.join("\n"));
};
if (process.env.HARD_CRM_QA_DESTROY_ON_LOAD) {
  app.on("browser-window-created", (_, win) => {
    win.webContents.once("did-start-loading", () => win.destroy());
  });
}
require(path.join(desk, "out/main.js"));
app.removeAllListeners("window-all-closed");

const nap = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(check) {
  for (let i = 0; i < 300; i++) {
    const result = await check();
    if (result) return result;
    await nap(100);
  }
  throw Error('CRM window did not reach its expected state');
}
async function run() {
  try {
    await app.whenReady();
    if (process.env.HARD_CRM_QA_DESTROY_ON_LOAD) {
      await nap(1500);
      assert.deepEqual(errors, [], "Startup touched a destroyed BrowserWindow");
      fs.writeFileSync(path.join(qa, "RESULT.json"), JSON.stringify({ stage: "verified", actual_main: true, destroyed_during_startup: true }, null, 2));
      console.log("CRM destroyed-window startup regression passed");
      app.quit();
      return;
    }
    if (failure) {
      app.emit('second-instance', {}, []);
      const recovery = await waitFor(() => BrowserWindow.getAllWindows().find(w => w.isVisible() && w.webContents.getURL().startsWith('data:')));
      const reason = failure === 'script' ? 'preload_or_renderer_unavailable' : 'main_document_failed';
      assert.match(await recovery.webContents.executeJavaScript('document.body.innerText'), new RegExp(reason));
      app.emit('second-instance', {}, []);
      assert.deepEqual(BrowserWindow.getAllWindows().filter(w => w.isVisible()), [recovery]);
      assert.deepEqual(errors, []);
      fs.writeFileSync(path.join(qa, 'RESULT.json'), JSON.stringify({ visible_recovery: true, hidden_broken_window: true, reason }, null, 2));
      app.quit();
      return;
    }
    const win = await waitFor(() => BrowserWindow.getAllWindows().find(w => w.isVisible() && w.webContents.getURL() === 'hard://app/index.html'));
    assert(await win.webContents.executeJavaScript('document.querySelector("#navigation").children.length > 0'));
    win.webContents.forcefullyCrashRenderer();
    await waitFor(() => {
      const events = JSON.parse(fs.readFileSync(path.join(qa, 'profile', 'crm-diagnostics.json'), 'utf8'));
      const stopped = events.findIndex(row => row.code === 'renderer_stopped');
      return win.isVisible() && stopped >= 0 && events.slice(stopped + 1).some(row => row.code === 'renderer_ready');
    });
    win.destroy();
    app.emit("second-instance", {}, []);
    await nap(250);
    assert.deepEqual(errors, [], "Repeated launch touched a destroyed BrowserWindow");
    fs.writeFileSync(path.join(qa, "RESULT.json"), JSON.stringify({ stage: "verified", actual_main: true, restarted_renderer: true, destroyed_window: true, second_instance: true }, null, 2));
    console.log("CRM destroyed-window second-instance regression passed");
    app.quit();
  } catch (error) {
    console.error(error);
    app.exit(1);
  }
}
void run();
