// Real Electron, DPAPI, server commits, Excel and process restart. Synthetic only.
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const { app, BrowserWindow, dialog, net } = require("electron");
const qa = process.env.HARD_CRM_QA_ROOT,
  phase = Number(process.env.HARD_CRM_QA_PHASE);
assert(qa && [1, 2, 3].includes(phase));
fs.mkdirSync(qa, { recursive: true });
const desk = path.resolve(__dirname, "..");
process.env.HARD_USER_DATA = path.join(qa, "profile");
if (!process.env.HARD_QA_BACKEND)
  process.env.HARD_CRM_SOURCE = path.dirname(desk);
app.getVersion = () =>
  JSON.parse(fs.readFileSync(path.join(desk, "package.json"))).version;
fs.writeFileSync(
  path.join(qa, "pid.json"),
  JSON.stringify({ pid: process.pid, phase }),
);
const nap = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (t) =>
  fs.appendFileSync(
    path.join(qa, "phases.log"),
    `${new Date().toISOString()} ${phase}: ${t}\n`,
  );
const checkpoint = (value) =>
  fs.writeFileSync(
    path.join(qa, `PHASE${phase}.json`),
    JSON.stringify(value, null, 2),
  );
let loseCommand = phase === 2,
  loseReceipt = phase === 2,
  commandRepliesLost = 0,
  receiptRepliesLost = 0;
const fetch = net.fetch.bind(net);
net.fetch = async (url, init) => {
  const response = await fetch(url, init);
  if (String(url).endsWith("/crm/command") && loseCommand) {
    commandRepliesLost++;
    await response.arrayBuffer();
    throw Error("QA lost reply AFTER actual server commit");
  }
  if (String(url).endsWith("/crm/receipt") && loseReceipt) {
    receiptRepliesLost++;
    await response.arrayBuffer();
    throw Error("QA lost receipt reply AFTER actual commit");
  }
  return response;
};
let savePath = path.join(qa, "general.xlsx");
dialog.showSaveDialog = async () => ({ canceled: false, filePath: savePath });
dialog.showOpenDialog = async () => ({
  canceled: false,
  filePaths: [path.join(qa, "manual.xlsx")],
});
dialog.showErrorBox = (title, message) => {
  checkpoint({ stage: "failed", title, message });
  app.exit(1);
};
require(path.join(desk, "out/main.js"));
async function run() {
  let win;
  try {
    await app.whenReady();
    async function until(fn, label) {
      for (let i = 0; i < 500; i++) {
        if (await fn()) return;
        await nap(100);
      }
      throw Error("Timeout: " + label);
    }
    await until(() => {
      win = BrowserWindow.getAllWindows().find(
        (w) => w.webContents.getURL() === "hard://app/index.html",
      );
      return win;
    }, "window");
    const js = (code) =>
      Promise.race([
        win.webContents.executeJavaScript(code, true),
        new Promise((_, reject) =>
          setTimeout(() => reject(Error("IPC deadline")), 150000),
        ),
      ]);
    await until(
      () => js('!!document.querySelector("[data-person]")'),
      "portfolio",
    );
    const screenshot = async (name) =>
      fs.writeFileSync(
        path.join(qa, name + ".png"),
        (await win.webContents.capturePage()).toPNG(),
      );
    if (phase === 1) {
      await js('document.querySelector("[data-person]").click()');
      await until(
        () => js('!!document.querySelector("[data-action=call]")'),
        "card",
      );
      await js('window.crm.connect({action:"offline",value:true})');
      await js('document.querySelector("[data-action=call]").click()');
      await until(() => js('document.querySelector("#dialog").open'), "form");
      await js(
        'document.querySelector("#v-soft_call_status").value="Нет контакта";document.querySelector("#v-soft_comment").value="Crash-safe synthetic action";document.querySelector("#action-form").requestSubmit()',
      );
      await until(() => js('!document.querySelector("#dialog").open'), "saved");
      const state = await js("window.crm.queue()");
      assert.equal(state.data.entries[0].state, "pending");
      checkpoint({
        stage: "ready_for_kill",
        operation_id: state.data.entries[0].body.operation_id,
        pid: process.pid,
      });
      log("waiting for forced termination");
      return;
    }
    const saved = JSON.parse(fs.readFileSync(path.join(qa, "PHASE1.json")));
    if (phase === 2) {
      await js('window.crm.queue("flush")');
      let queue = await js("window.crm.queue()");
      assert.equal(queue.data.entries[0].state, "pending");
      assert(commandRepliesLost > 0);
      loseCommand = false;
      await js('window.crm.queue("flush")');
      queue = await js("window.crm.queue()");
      assert.equal(queue.data.entries[0].state, "accepted");
      assert.equal(queue.data.entries[0].body.operation_id, saved.operation_id);
      const result = queue.data.entries[0].result;
      assert.equal(result.projection_status, "applied");
      await js('document.querySelector("[data-person]").click()');
      await until(
        () => js('!!document.querySelector("[data-tab=history]")'),
        "card",
      );
      await js('document.querySelector("[data-tab=history]").click()');
      assert.equal(await js('document.querySelectorAll(".record").length'), 1);
      await screenshot("06-recovered-history");
      await js('document.querySelector("[data-section=connection]").click()');
      await until(
        () => js('!!document.querySelector("#demo-role")'),
        "connection",
      );
      await js(
        'document.querySelector("#demo-role").value="manager";document.querySelector("#demo-connect").click()',
      );
      await until(
        () => js('!!document.querySelector("[data-section=exchange]")'),
        "manager",
      );
      await js('document.querySelector("[data-section=exchange]").click()');
      await until(
        () => js('!!document.querySelector("#export-new")'),
        "exchange",
      );
      await js('document.querySelector("#export-new").click()');
      await until(
        () =>
          js(
            'document.querySelector("#exchange-result").innerText.includes("повторно прочитана")',
          ),
        "native export",
      );
      await until(() => receiptRepliesLost > 0, "lost receipt reply");
      const receiptQueue = await js("window.crm.queue()");
      assert.equal(receiptQueue.data.entries[0].action, "receipt");
      assert.equal(receiptQueue.data.entries[0].state, "pending");
      await screenshot("07-excel-readback");
      checkpoint({
        stage: "ready_for_kill",
        pid: process.pid,
        operation_id: saved.operation_id,
        event_id: result.event_id,
        receipt_id: receiptQueue.data.entries[0].body.receipt_id,
      });
      log("receipt persisted; waiting for forced termination");
      return;
    }
    await js('window.crm.queue("flush")');
    let queue = await js("window.crm.queue()");
    assert.equal(queue.data.entries[0].state, "accepted");
    savePath = path.join(qa, "personal.xlsx");
    await js('document.querySelector("[data-section=exchange]").click()');
    await until(() => js('!!document.querySelector("#target")'), "exchange");
    await js(
      'document.querySelector("#target").value="personal:demo";document.querySelector("#export-new").click()',
    );
    await until(
      () =>
        js(
          'document.querySelector("#exchange-result").innerText.includes("повторно прочитана")',
        ),
      "personal export",
    );
    await js('window.crm.queue("flush")');
    await js('document.querySelector("#import-file").click()');
    await until(
      () => js('!!document.querySelector("#apply-import")'),
      "manual preview",
    );
    assert(
      (
        await js('document.querySelector("#exchange-result").innerText')
      ).includes("Изменений: 1"),
    );
    await screenshot("08-manual-preview");
    await js('document.querySelector("#apply-import").click()');
    await until(
      () =>
        js(
          'document.querySelector("#exchange-result").innerText.includes("Импорт подтверждён")',
        ),
      "import",
    );
    await js('document.querySelector("[data-section=portfolio]").click()');
    await until(
      () => js('!!document.querySelector("[data-person]")'),
      "portfolio after import",
    );
    await js('document.querySelector("[data-person]").click()');
    await until(
      () => js('!!document.querySelector("[data-tab=history]")'),
      "history",
    );
    await js('document.querySelector("[data-tab=history]").click()');
    assert.equal(await js('document.querySelectorAll(".record").length'), 2);
    assert(
      (await js('document.querySelector("#card-content").innerText')).includes(
        "Manual synthetic review",
      ),
    );
    await screenshot("09-full-cycle-history");
    await nap(2500);
    const context = (await js("window.crm.state()")).context;
    const report = await js(
      `window.crm.api('POST','adoption',{cohort_id:${JSON.stringify(context.cohorts[0].cohort_id)},since:new Date(Date.now()-28*86400000).toISOString(),until:new Date(Date.now()+86400000).toISOString()})`,
    );
    assert(report.ok);
    assert.equal(report.data.delivery.numerator, 1);
    assert.equal(report.data.delivery.denominator, 1);
    assert.equal(report.data.origins.call.counts.app, 1);
    checkpoint({
      stage: "verified",
      actual_main: true,
      actual_backend: true,
      forced_restarts: 2,
      lost_command_reply: true,
      lost_receipt_reply: true,
      root_call_facts: 1,
      delivery: "1/1 (personal + general)",
      manual_edit_events: 1,
      echo_events: 0,
    });
    log("verified");
    app.quit();
  } catch (error) {
    log(String(error));
    checkpoint({ stage: "failed", error: String(error) });
    if (win)
      fs.writeFileSync(
        path.join(qa, "failure.png"),
        (await win.webContents.capturePage()).toPNG(),
      );
    app.exit(1);
  }
}
run();
