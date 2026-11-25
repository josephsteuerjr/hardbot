// Real Electron main process: repeated launch must tolerate a destroyed window.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { app, BrowserWindow, dialog } = require("electron");

const qa = process.env.HARD_CRM_QA_ROOT;
assert(qa, "Explicit QA directory required");
fs.mkdirSync(qa, { recursive: true });
const desk = path.resolve(__dirname, "..");
process.env.HARD_USER_DATA = path.join(qa, "profile");
process.env.HARD_CRM_SOURCE = path.dirname(desk);
app.getVersion = () => JSON.parse(fs.readFileSync(path.join(desk, "package.json"), "utf8")).version;
const errors = [];
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
async function run() {
  try {
    await app.whenReady();
    if (process.env.HARD_CRM_QA_DESTROY_ON_LOAD) {
      await nap(1500);
      assert.deepEqual(errors, [], "Startup touched a destroyed BrowserWindow");
      fs.writeFileSync(path.join(qa, "RESULT.json"), JSON.stringify({ stage: "verified", actual_main: true, destroyed_during_startup: true }, null, 2));
      console.log("CRM destroyed-window startup regression passed");
      app.exit(0);
      return;
    }
    let win;
    for (let i = 0; i < 300; i++) {
      win = BrowserWindow.getAllWindows().find(w => w.webContents.getURL() === "hard://app/index.html");
      if (win) break;
      await nap(100);
    }
    assert(win, "Main window did not open");
    win.destroy();
    app.emit("second-instance", {}, []);
    await nap(250);
    assert.deepEqual(errors, [], "Repeated launch touched a destroyed BrowserWindow");
    fs.writeFileSync(path.join(qa, "RESULT.json"), JSON.stringify({ stage: "verified", actual_main: true, destroyed_window: true, second_instance: true }, null, 2));
    console.log("CRM destroyed-window second-instance regression passed");
    app.exit(0);
  } catch (error) {
    console.error(error);
    app.exit(1);
  }
}
void run();
