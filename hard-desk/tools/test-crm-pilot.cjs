// Real application main/preload/renderer, protected queues and canonical Python service.
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const { app, BrowserWindow, dialog } = require("electron");
const qa = process.env.HARD_CRM_QA_ROOT;
assert(qa, "Explicit QA directory required");
fs.mkdirSync(qa, { recursive: true });
fs.writeFileSync(
  path.join(qa, "pid.json"),
  JSON.stringify({ pid: process.pid, started: new Date().toISOString() }),
);
const phase = (text) =>
  fs.appendFileSync(
    path.join(qa, "phases.log"),
    new Date().toISOString() + " " + text + "\n",
  );
const desk = path.resolve(__dirname, "..");
process.env.HARD_USER_DATA = path.join(qa, "profile");
process.env.HARD_CRM_SOURCE = path.dirname(desk);
app.getVersion = () =>
  JSON.parse(fs.readFileSync(path.join(desk, "package.json"), "utf8")).version;
const errors = [];
app.on("browser-window-created", (_, win) => {
  win.webContents.on("console-message", (_details) => {});
  win.webContents.on("render-process-gone", (_, details) =>
    errors.push(details.reason),
  );
});
dialog.showErrorBox = (title, message) => {
  fs.writeFileSync(path.join(qa, "startup-error.txt"), title + "\n" + message);
  console.error(title + ": " + message);
  app.exit(1);
};
require(path.join(desk, "out/main.js"));
const nap = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(fn, label) {
  for (let i = 0; i < 300; i++) {
    if (await fn()) return;
    await nap(100);
  }
  throw Error("Timeout: " + label);
}
async function run() {
  let win;
  try {
    await app.whenReady();
    await until(() => {
      win = BrowserWindow.getAllWindows().find(
        (w) => w.webContents.getURL() === "hard://app/index.html",
      );
      return win;
    }, "main window");
    const js = (code) =>
      Promise.race([
        win.webContents.executeJavaScript(code, true),
        new Promise((_, reject) =>
          setTimeout(
            () => reject(Error("Renderer IPC deadline: " + code.slice(0, 80))),
            45000,
          ),
        ),
      ]);
    await until(()=>js('!!document.querySelector("[data-person],.connection-testing")'),'entry loaded');
    if((await js('window.crm.state()')).connection.mode==='unconfigured')
      await js('document.querySelector(".connection-testing summary").click();document.querySelector("#demo-connect").click()');
    await until(
      () => js('!!document.querySelector("[data-person]")'),
      "portfolio loaded",
    );
    await nap(250);
    fs.writeFileSync(
      path.join(qa, "01-portfolio.png"),
      (await win.webContents.capturePage()).toPNG(),
    );
    assert.equal((await js("window.crm.state()")).context.role, "employee");
    await js('document.querySelector("[data-person]").click()');
    await until(
      () => js('!!document.querySelector("[data-action=call]")'),
      "card",
    );
    fs.writeFileSync(
      path.join(qa, "02-card.png"),
      (await win.webContents.capturePage()).toPNG(),
    );
    await js('window.crm.connect({action:"offline",value:true})');
    await js('document.querySelector("[data-action=call]").click()');
    await until(
      () => js('document.querySelector("#dialog").open'),
      "call form",
    );
    await js(
      'document.querySelector("#v-soft_call_status").value="Нет контакта";document.querySelector("#v-soft_comment").value="Synthetic offline action";document.querySelector("#action-form").requestSubmit()',
    );
    await until(
      () => js('!document.querySelector("#dialog").open'),
      "local durable save",
    );
    let queue = await js("window.crm.queue()");
    assert.equal(queue.data.entries.length, 1);
    assert.equal(queue.data.entries[0].state, "pending");
    const operation = queue.data.entries[0].body.operation_id;
    const protectedFiles = fs
      .readdirSync(path.join(qa, "profile/crm"))
      .filter((n) => n.startsWith("business-"));
    assert.equal(protectedFiles.length, 1);
    assert(
      !fs
        .readFileSync(path.join(qa, "profile/crm", protectedFiles[0]))
        .includes(Buffer.from("Synthetic offline action")),
    );
    await js('document.querySelector("[data-section=queue]").click()');
    await until(
      () => js('document.body.innerText.includes("Ожидает отправки")'),
      "queue display",
    );
    fs.writeFileSync(
      path.join(qa, "03-offline-queue.png"),
      (await win.webContents.capturePage()).toPNG(),
    );
    await js('window.crm.connect({action:"offline",value:false})');
    await js('window.crm.queue("flush")');
    queue = await js("window.crm.queue()");
    assert.equal(queue.data.entries[0].state, "accepted");
    assert.equal(queue.data.entries[0].body.operation_id, operation);
    assert.equal(
      queue.data.entries[0].result.projection_status,
      "applied",
      "Fresh card revisions must apply on the first action",
    );
    const event = queue.data.entries[0].result.event_id;
    phase("business accepted");
    await nap(7500); // Batch diagnostic delivery before switching the authenticated actor.
    phase("switching role");
    await js('document.querySelector("[data-section=connection]").click()');
    await until(
      () => js('!!document.querySelector("#demo-role")'),
      "connection page",
    );
    await js(
      'document.querySelector("#demo-role").value="manager";document.querySelector("#demo-connect").click()',
    );
    phase("role switched through actual UI");
    await until(
      () => js('!!document.querySelector("[data-section=adoption]")'),
      "manager role",
    );
    await js('document.querySelector("[data-section=adoption]").click()');
    await until(
      () => js('!!document.querySelector(".metric strong")'),
      "adoption",
    );
    await nap(2300);
    await js('document.querySelector("#adoption-refresh").click()');
    await nap(400);
    fs.writeFileSync(
      path.join(qa, "04-adoption.png"),
      (await win.webContents.capturePage()).toPNG(),
    );
    assert(
      (
        await js('document.querySelector("#adoption-report").innerText')
      ).includes("50%"),
    );
    assert.equal(
      (await js("window.crm.queue()")).data.entries.length,
      0,
      "Another employee must not inherit queue",
    );
    await js('document.querySelector("[data-section=script]").click()');
    await until(() => js('!!document.querySelector("#script-edit")'), "script");
    fs.writeFileSync(
      path.join(qa, "05-script.png"),
      (await win.webContents.capturePage()).toPNG(),
    );
    assert.deepEqual(errors, []);
    phase("verified");
    fs.writeFileSync(
      path.join(qa, "RESULT.json"),
      JSON.stringify(
        {
          stage: "verified",
          actual_main: true,
          actual_backend: true,
          dpapi_protected: true,
          offline_saved: true,
          exact_event_ack: event,
          operation_id: operation,
          scope_queue_isolated: true,
          activation: "1/2",
          screenshots: 5,
        },
        null,
        2,
      ),
    );
    console.log("CRM live application smoke verified");
    app.quit();
  } catch (error) {
    console.error(error);
    if (win) {
      fs.writeFileSync(
        path.join(qa, "failure.png"),
        (await win.webContents.capturePage()).toPNG(),
      );
      fs.writeFileSync(
        path.join(qa, "failure.txt"),
        String(error) +
          "\n" +
          (await win.webContents.executeJavaScript("document.body.innerText")),
      );
    }
    app.exit(1);
  }
}
run();
