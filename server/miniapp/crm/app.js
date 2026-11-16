"use strict";
(() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __esm = (fn2, res) => function __init() {
    return fn2 && (res = (0, fn2[__getOwnPropNames(fn2)[0]])(fn2 = 0)), res;
  };
  var __export = (target, all2) => {
    for (var name in all2)
      __defProp(target, name, { get: all2[name], enumerable: true });
  };

  // hard-desk/ui/crm-delivery.ts
  async function mountDeliveryForm(host, initial, api4) {
    let contracts = [...initial];
    const draft = async (key3, ...value) => {
      const result2 = await api4.draft(key3, ...value);
      if (result2?.ok !== true) throw new Error(result2?.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A \u043D\u0430 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0435.");
      return result2;
    };
    const key2 = "courier:delivery", saved = (await draft(key2)).data || {};
    host.innerHTML = `<form data-courier-form><div class="field"><label for="courier-contract">\u041D\u043E\u043C\u0435\u0440 \u041A\u0414</label>${api4.search ? '<input id="courier-search" type="search" placeholder="\u041D\u0430\u0439\u0442\u0438 \u041A\u0414 \u0438\u043B\u0438 \u043A\u043B\u0438\u0435\u043D\u0442\u0430">' : ""}<select id="courier-contract" required></select><output id="courier-name"></output></div><div class="field"><label for="courier-address">\u0410\u0434\u0440\u0435\u0441 \u043F\u0435\u0440\u0435\u0434\u0430\u0447\u0438 \u043A\u043E\u043D\u0432\u0435\u0440\u0442\u0430</label><input id="courier-address" list="courier-addresses" autocomplete="street-address"><datalist id="courier-addresses"></datalist><small>\u0415\u0441\u043B\u0438 \u043A\u043E\u043D\u0432\u0435\u0440\u0442 \u043D\u0435 \u043F\u0435\u0440\u0435\u0434\u0430\u043D, \u043E\u0441\u0442\u0430\u0432\u044C\u0442\u0435 \u0430\u0434\u0440\u0435\u0441 \u043F\u0443\u0441\u0442\u044B\u043C</small></div><div class="field"><label for="courier-phone">\u0422\u0435\u043B\u0435\u0444\u043E\u043D \u0438\u043B\u0438 \u043F\u0440\u043E\u0447\u0435\u0440\u043A</label><input id="courier-phone" type="tel" maxlength="100" placeholder="\u2014"></div><div class="field"><label for="courier-comment">\u041A\u043E\u043C\u0443 \u043F\u0435\u0440\u0435\u0434\u0430\u043D \u043A\u043E\u043D\u0432\u0435\u0440\u0442 \u0438\u043B\u0438 \u043F\u043E\u0447\u0435\u043C\u0443 \u043F\u0435\u0440\u0435\u0434\u0430\u0442\u044C \u043D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C</label><textarea id="courier-comment" required maxlength="4000"></textarea></div><p data-courier-status role="status"></p><div class="actions">${api4.close ? '<button type="button" data-courier-close>\u0417\u0430\u043A\u0440\u044B\u0442\u044C</button>' : ""}<button type="submit" class="primary">\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C</button></div></form>`;
    const find = (selector) => host.querySelector(selector);
    const select = find("#courier-contract"), address2 = find("#courier-address");
    const info = document.createElement("details");
    info.className = "courier-client-info";
    find("#courier-name").after(info);
    const phone = find("#courier-phone"), comment = find("#courier-comment");
    const status2 = find("[data-courier-status]"), save = find("button[type=submit]");
    const chosen = () => contracts.find((row) => row.contract_uuid === select.value);
    const capture = () => ({ contract_uuid: select.value, address: address2.value, phone: phone.value, comment: comment.value });
    let draftWrites = Promise.resolve();
    const persist = () => {
      const value = capture();
      draftWrites = draftWrites.catch(() => {
      }).then(async () => {
        if (value.contract_uuid) await draft(key2 + ":" + value.contract_uuid, value);
        await draft(key2, value);
      });
      return draftWrites;
    };
    const paint = () => {
      const selected2 = chosen();
      find("#courier-name").textContent = selected2?.client_name || selected2?.fields?.client || "";
      const fields = selected2?.fields || {};
      find("#courier-addresses").innerHTML = Array.from(new Set([fields.address, fields.address_customer, fields.extra_address].filter(Boolean))).map((value) => `<option value="${esc2(value)}"></option>`).join("");
      info.innerHTML = selected2 ? "<summary>\u0421\u0432\u0435\u0434\u0435\u043D\u0438\u044F \u043E \u043A\u043B\u0438\u0435\u043D\u0442\u0435</summary><dl>" + (api4.fields || []).filter((field) => fields[field.key] !== void 0 && fields[field.key] !== null && fields[field.key] !== "").map((field) => `<dt>${esc2(field.title)}</dt><dd>${esc2(fields[field.key])}</dd>`).join("") + "</dl>" : "";
      const writable = selected2 && Array.isArray(selected2.allowed_actions) && selected2.allowed_actions.includes("delivery");
      save.disabled = !writable;
      status2.textContent = selected2 && !writable ? "\u0414\u0430\u043D\u043D\u044B\u0435 \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B \u0434\u043B\u044F \u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440\u0430. \u041F\u0435\u0440\u0435\u0434\u0430\u0447\u0430 \u043A\u043E\u043D\u0432\u0435\u0440\u0442\u0430 \u043D\u0435 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0430." : "";
    };
    const options = (preferred) => {
      select.innerHTML = '<option value="">\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u041A\u0414</option>' + contracts.map((row) => `<option value="${esc2(row.contract_uuid)}">${esc2(row.external_id)} \xB7 ${esc2(row.creditor_id)}</option>`).join("");
      select.value = preferred || "";
      paint();
    };
    options(saved.contract_uuid);
    address2.value = saved.address || "";
    phone.value = saved.phone ?? "-";
    comment.value = saved.comment || "";
    let activeContract = select.value, selectionGeneration = 0;
    select.addEventListener("change", async () => {
      const selected2 = select.value, current3 = ++selectionGeneration;
      const previous = { contract_uuid: activeContract, address: address2.value, phone: phone.value, comment: comment.value };
      const controls = Array.from(host.querySelectorAll("input,select,textarea,button")).filter((control) => control !== save).map((control) => ({ control, disabled: control.disabled }));
      controls.forEach(({ control }) => control.disabled = true);
      save.disabled = true;
      try {
        await draftWrites.catch(() => {
        });
        if (activeContract) await draft(key2 + ":" + activeContract, previous);
        const restored = selected2 ? (await draft(key2 + ":" + selected2)).data : null;
        if (current3 !== selectionGeneration || !host.isConnected) return;
        activeContract = selected2;
        address2.value = restored?.address || "";
        comment.value = restored?.comment || "";
        phone.value = restored?.phone ?? chosen()?.fields?.phone ?? "-";
        paint();
        await persist();
      } catch (error) {
        select.value = activeContract;
        paint();
        status2.textContent = error.message;
      } finally {
        controls.forEach(({ control, disabled }) => control.disabled = disabled);
      }
    });
    for (const input of [address2, phone, comment]) input.addEventListener("input", () => {
      void persist().catch((error) => {
        status2.textContent = error.message;
      });
    });
    const close = host.querySelector("[data-courier-close]");
    if (close) close.onclick = () => {
      void persist().then(() => api4.close?.()).catch((error) => {
        status2.textContent = error.message;
      });
    };
    const search = host.querySelector("#courier-search");
    let generation = 0, timer;
    if (search) search.oninput = () => {
      const current3 = ++generation;
      clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          const rows = await api4.search(search.value);
          if (current3 !== generation || !host.isConnected) return;
          const previous = chosen();
          contracts = rows;
          if (previous && !contracts.some((row) => row.contract_uuid === previous.contract_uuid)) contracts.unshift(previous);
          options(previous?.contract_uuid);
        } catch {
          if (current3 === generation) status2.textContent = "\u041F\u043E\u0438\u0441\u043A \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u0435\u043D. \u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0435 \u041A\u0414 \u043E\u0441\u0442\u0430\u044E\u0442\u0441\u044F \u0432 \u0441\u043F\u0438\u0441\u043A\u0435.";
        }
      }, 250);
    };
    find("form").onsubmit = async (event) => {
      event.preventDefault();
      if (save.disabled) return;
      const selected2 = chosen();
      if (!selected2) return;
      save.disabled = true;
      try {
        if (!comment.value.trim()) throw new Error("\u0423\u043A\u0430\u0436\u0438\u0442\u0435 \u043F\u043E\u043B\u0443\u0447\u0430\u0442\u0435\u043B\u044F \u043A\u043E\u043D\u0432\u0435\u0440\u0442\u0430 \u0438\u043B\u0438 \u043F\u0440\u0438\u0447\u0438\u043D\u0443 \u043D\u0435\u0443\u0434\u0430\u0447\u0438.");
        const phoneText = phone.value.trim(), digits = phoneText.replace(/\D/g, "").length;
        if (phoneText && !["-", "\u2014", "\u2013"].includes(phoneText) && (!/^[+\d() .-]+$/.test(phoneText) || digits < 5 || digits > 20))
          throw new Error("\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u0442\u0435\u043B\u0435\u0444\u043E\u043D \u0438\u043B\u0438 \u043F\u0440\u043E\u0447\u0435\u0440\u043A.");
        await persist();
        const raw = {
          schema_version: 1,
          kind: "delivery",
          contract_ids: [selected2.contract_uuid],
          base_revisions: {},
          payload: {
            delivered: !!address2.value.trim(),
            delivery_address: address2.value.trim(),
            delivery_phone: phone.value.trim() || "-",
            delivery_comment: comment.value.trim()
          }
        };
        const recordKey = key2 + ":" + selected2.contract_uuid;
        const content = JSON.stringify(raw), pending = (await draft(recordKey + ":pending")).data;
        const operation_id = pending?.content === content ? pending.operation_id : crypto.randomUUID();
        await draft(recordKey + ":pending", { content, operation_id });
        const result2 = await api4.command({ ...raw, operation_id });
        if (!result2.ok) throw new Error(result2.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442.");
        await draft(recordKey, null);
        await draft(key2, null);
        await draft(recordKey + ":pending", null);
        comment.value = "";
        address2.value = "";
        phone.value = "-";
        select.value = "";
        activeContract = "";
        paint();
        status2.textContent = "\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D";
      } catch (error) {
        paint();
        status2.textContent = error.message;
      }
    };
  }
  var esc2;
  var init_crm_delivery = __esm({
    "hard-desk/ui/crm-delivery.ts"() {
      "use strict";
      esc2 = (value) => String(value ?? "").replace(
        /[&<>"']/g,
        (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]
      );
    }
  });

  // hard-desk/ui/crm-allocation.ts
  var crm_allocation_exports = {};
  __export(crm_allocation_exports, {
    mountAllocation: () => mountAllocation
  });
  async function mountAllocation(host, crm2, selection, valid, changed) {
    let disposed = false, preview, intent, action3 = "replace", busy2 = false, turn = 0, connected = true, job3 = "", timer;
    const current3 = () => !disposed && valid();
    host.innerHTML = `<div class="toolbar allocation-actions"><span>\u0420\u0430\u0441\u043F\u0440\u0435\u0434\u0435\u043B\u0435\u043D\u0438\u0435</span><button data-allocation="assign">\u041D\u0430\u0437\u043D\u0430\u0447\u0438\u0442\u044C</button><button data-allocation="replace">\u0417\u0430\u043C\u0435\u043D\u0438\u0442\u044C</button><button data-allocation="remove">\u0421\u043D\u044F\u0442\u044C</button><button id="allocation-history" class="link">\u0418\u0441\u0442\u043E\u0440\u0438\u044F</button></div><div id="allocation-job" role="status"></div><dialog id="allocation-dialog"><form><h2 id="allocation-title"></h2><div class="toolbar"><label>\u0420\u0430\u0431\u043E\u0442\u0430<select id="allocation-kind"><option value="call">\u0417\u0432\u043E\u043D\u043A\u0438</option><option value="visit">\u0412\u044B\u0435\u0437\u0434\u044B</option><option value="both">\u0417\u0432\u043E\u043D\u043A\u0438 \u0438 \u0432\u044B\u0435\u0437\u0434\u044B</option></select></label><label id="allocation-target-label">\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A<select id="allocation-target"></select></label></div><label>\u041A\u043E\u043C\u043C\u0435\u043D\u0442\u0430\u0440\u0438\u0439<input id="allocation-note" maxlength="1000"></label><div id="allocation-preview"></div><p id="allocation-status" role="status"></p><div class="actions"><button type="button" id="allocation-close">\u0417\u0430\u043A\u0440\u044B\u0442\u044C</button><button type="submit" id="allocation-submit">\u041F\u0440\u0438\u043C\u0435\u043D\u0438\u0442\u044C</button></div></form></dialog>`;
    const $3 = (id) => host.querySelector("#" + id);
    const dialog = $3("allocation-dialog");
    const request3 = async (method, action4, body, query) => {
      const r = await crm2.api(method, action4, body, query);
      if (!r.ok || r.stale) throw Object.assign(Error(r.text || "\u041D\u0443\u0436\u043D\u0430 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u0430\u044F \u0441\u0432\u044F\u0437\u044C."), { result: r });
      return r.data;
    };
    const protect = (fn2) => () => {
      void Promise.resolve().then(fn2).catch((e) => {
        if (current3()) $3("allocation-status").textContent = e.message;
      });
    };
    const staff2 = (await request3("GET", "assignment-staff")).staff;
    function targets() {
      const kind2 = $3("allocation-kind").value, prior = $3("allocation-target").value;
      $3("allocation-target").innerHTML = '<option value="">\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430</option>' + staff2.filter((s) => s.eligible && (kind2 === "both" ? ["call", "visit"].every((k) => s.work_kinds.includes(k)) : s.work_kinds.includes(kind2))).map((s) => `<option value="${esc3(s.employee_uuid)}">${esc3(s.name)} \xB7 ${s.contracts} \u041A\u0414</option>`).join("");
      $3("allocation-target").value = prior;
    }
    function lock() {
      ["allocation-kind", "allocation-target", "allocation-note"].forEach((id) => $3(id).disabled = !!intent || busy2);
      $3("allocation-submit").textContent = intent ? "\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0438\u0441\u0445\u043E\u0434 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438" : "\u041F\u0440\u0438\u043C\u0435\u043D\u0438\u0442\u044C";
    }
    async function check() {
      if (intent || busy2) return;
      const n2 = ++turn;
      preview = null;
      $3("allocation-submit").disabled = true;
      targets();
      const s = selection(), kind2 = $3("allocation-kind").value, target = $3("allocation-target").value;
      if (!s.all && !s.ids.length) {
        $3("allocation-preview").textContent = "\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u0442\u0440\u043E\u043A\u0438 \u0432 \u0420\u0435\u0435\u0441\u0442\u0440\u0435.";
        return;
      }
      if (action3 !== "remove" && !target) {
        $3("allocation-preview").textContent = "\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430.";
        return;
      }
      const result2 = await request3("POST", "assignment-preview", { schema_version: 1, action: action3, employee_uuid: action3 === "remove" ? null : target, work_kinds: kind2 === "both" ? ["call", "visit"] : [kind2], selection: s.all ? { kind: "filter", part: "filtered", filters: s.filters } : { kind: "contracts", ids: s.ids, part: "filtered", filters: s.filters }, comment: $3("allocation-note").value });
      if (!current3() || n2 !== turn || !dialog.open) return;
      preview = result2;
      $3("allocation-preview").innerHTML = `<p><strong>${result2.people}</strong> \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432 \xB7 <strong>${result2.contracts}</strong> \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432 \xB7 ${result2.slots_count} \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0439</p><p>\u0411\u0435\u0437 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u0439: ${result2.no_op}.${result2.large ? " \u0411\u0443\u0434\u0435\u0442 \u0432\u044B\u043F\u043E\u043B\u043D\u0435\u043D\u043E \u0441\u0435\u0440\u0432\u0435\u0440\u043D\u043E\u0435 \u0437\u0430\u0434\u0430\u043D\u0438\u0435. \u041C\u043E\u0436\u043D\u043E \u043F\u0435\u0440\u0435\u0439\u0442\u0438 \u0432 \u0434\u0440\u0443\u0433\u043E\u0439 \u0440\u0430\u0437\u0434\u0435\u043B." : ""}</p><details><summary>\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0441\u043E\u0441\u0442\u0430\u0432${result2.large ? " \xB7 \u043F\u0435\u0440\u0432\u044B\u0435 200 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0439" : ""}</summary>${result2.slots.map((slot) => {
        const row = s.rows.find((r) => r.contract_uuid === slot.contract_uuid);
        return `<p>${esc3(row ? row.client_name + " \xB7 " + row.external_id : slot.external_id || "\u0414\u043E\u0433\u043E\u0432\u043E\u0440 \u0438\u0437 \u0432\u0441\u0435\u0433\u043E \u043E\u0442\u0431\u043E\u0440\u0430")} \xB7 ${slot.capability === "call" ? "\u0417\u0432\u043E\u043D\u043A\u0438" : "\u0412\u044B\u0435\u0437\u0434\u044B"} \xB7 ${esc3(staff2.find((p) => p.employee_uuid === slot.employee_uuid)?.name || "\u041D\u0435 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D")}</p>`;
      }).join("")}</details>`;
      $3("allocation-status").textContent = "\u0421\u043E\u0441\u0442\u0430\u0432 \u043F\u0440\u043E\u0432\u0435\u0440\u0435\u043D \u0434\u043E " + new Date(result2.expires_at).toLocaleTimeString("ru-RU");
      $3("allocation-submit").disabled = !connected;
    }
    async function monitor() {
      clearTimeout(timer);
      if (!job3 || !current3()) return;
      try {
        const result2 = await request3("GET", "assignment-job", void 0, { job_id: job3 });
        if (!current3()) return;
        $3("allocation-job").innerHTML = `<p>${result2.state === "running" ? "\u0412\u044B\u043F\u043E\u043B\u043D\u044F\u0435\u0442\u0441\u044F \u043D\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0435" : "\u0420\u0430\u0441\u043F\u0440\u0435\u0434\u0435\u043B\u0435\u043D\u0438\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u043E"} \xB7 \u041F\u0440\u0438\u043C\u0435\u043D\u0435\u043D\u043E ${result2.applied_slots} \xB7 \u0411\u0435\u0437 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u0439 ${result2.no_op_slots} \xB7 \u041E\u0436\u0438\u0434\u0430\u0435\u0442 ${result2.pending_slots} \xB7 \u041A\u043E\u043D\u0444\u043B\u0438\u043A\u0442 ${result2.conflict_slots} \xB7 \u041E\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D\u043E ${result2.cancelled_slots}</p>${result2.state === "running" ? '<button id="allocation-stop">\u041E\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C \u043E\u0441\u0442\u0430\u0432\u0448\u0438\u0435\u0441\u044F \u043F\u0430\u043A\u0435\u0442\u044B</button>' : ""}`;
        if (result2.state === "running") {
          $3("allocation-stop").onclick = protect(async () => {
            await request3("POST", "assignment-job-cancel", { schema_version: 1, job_id: job3 });
            await monitor();
          });
          timer = setTimeout(() => void monitor(), 1500);
        } else {
          job3 = "";
          await crm2.draft("managed-active-job", null);
          await changed();
        }
      } catch (e) {
        if (current3()) {
          $3("allocation-job").textContent = e.message;
          timer = setTimeout(() => void monitor(), 5e3);
        }
      }
    }
    async function accepted(result2) {
      intent = null;
      await crm2.draft("managed-allocation-intent", null);
      lock();
      if (result2.status === "job_started") {
        job3 = result2.job_id;
        await crm2.draft("managed-active-job", job3);
        dialog.close();
        void monitor();
      } else {
        $3("allocation-status").textContent = `\u041F\u0440\u0438\u043C\u0435\u043D\u0435\u043D\u043E: ${result2.changes.length}; \u0431\u0435\u0437 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u0439: ${result2.no_op}.`;
        $3("allocation-submit").disabled = true;
        await changed();
      }
    }
    dialog.querySelector("form").onsubmit = async (e) => {
      e.preventDefault();
      if (busy2 || !connected || !preview && !intent) return;
      busy2 = true;
      $3("allocation-submit").disabled = true;
      try {
        if (!intent) {
          intent = { schema_version: 1, operation_id: crypto.randomUUID(), preview_id: preview.preview_id };
          const r = await crm2.draft("managed-allocation-intent", intent);
          if (!r.ok) {
            intent = null;
            throw Error(crm2.platform === "mobile" ? "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0437\u0430\u0449\u0438\u0442\u0438\u0442\u044C \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044E \u043D\u0430 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435." : "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0437\u0430\u0449\u0438\u0442\u0438\u0442\u044C \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044E \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435.");
          }
        }
        lock();
        let result2;
        try {
          result2 = await request3("GET", "assignment-receipt", void 0, { operation_id: intent.operation_id });
        } catch (e2) {
          if (e2.result?.status !== 404) throw e2;
          result2 = await request3("POST", "assignment-commit", intent);
        }
        if (current3()) await accepted(result2);
      } catch (e2) {
        if (current3()) {
          $3("allocation-status").textContent = e2.message;
          const status2 = e2.result?.status;
          if ([400, 409, 422].includes(status2)) {
            intent = null;
            await crm2.draft("managed-allocation-intent", null);
            preview = null;
          }
        }
      } finally {
        busy2 = false;
        if (current3()) {
          lock();
          $3("allocation-submit").disabled = !intent;
        }
      }
    };
    host.querySelectorAll("[data-allocation]").forEach((b) => b.onclick = protect(async () => {
      action3 = b.dataset.allocation;
      intent = (await crm2.draft("managed-allocation-intent")).data;
      preview = null;
      $3("allocation-title").textContent = { assign: "\u041D\u0430\u0437\u043D\u0430\u0447\u0438\u0442\u044C \u043E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u043E\u0433\u043E", replace: "\u0417\u0430\u043C\u0435\u043D\u0438\u0442\u044C \u043E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u043E\u0433\u043E", remove: "\u0421\u043D\u044F\u0442\u044C \u043E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u043E\u0433\u043E" }[action3];
      $3("allocation-target-label").hidden = action3 === "remove";
      $3("allocation-preview").textContent = "";
      $3("allocation-status").textContent = intent ? "\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044F \u0441 \u043D\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043D\u044B\u043C \u0438\u0441\u0445\u043E\u0434\u043E\u043C." : "";
      lock();
      $3("allocation-submit").disabled = !intent;
      dialog.showModal();
      if (!intent) await check();
    }));
    ["allocation-kind", "allocation-target"].forEach((id) => $3(id).onchange = protect(check));
    $3("allocation-note").onchange = protect(check);
    $3("allocation-close").onclick = () => dialog.close();
    $3("allocation-history").onclick = protect(async () => {
      const result2 = await request3("GET", "assignment-history");
      if (!current3()) return;
      const panel = document.createElement("dialog");
      panel.innerHTML = `<h2>\u0418\u0441\u0442\u043E\u0440\u0438\u044F \u0440\u0430\u0441\u043F\u0440\u0435\u0434\u0435\u043B\u0435\u043D\u0438\u044F</h2><div class="allocation-history">${result2.rows.map((r) => `<p>${new Date(r.at).toLocaleString("ru-RU")} \xB7 ${esc3(r.client)} \xB7 ${esc3(r.external_id)}<br>${r.capability === "call" ? "\u0417\u0432\u043E\u043D\u043A\u0438" : "\u0412\u044B\u0435\u0437\u0434\u044B"}: ${esc3(r.before_name || "\u041D\u0435 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D")} \u2192 ${esc3(r.after_name || "\u041D\u0435 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D")}</p>`).join("") || "\u0418\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u0439 \u043D\u0435\u0442."}</div><button>\u0417\u0430\u043A\u0440\u044B\u0442\u044C</button>`;
      host.append(panel);
      panel.querySelector("button").onclick = () => {
        panel.close();
        panel.remove();
      };
      panel.showModal();
    });
    const stop = crm2.onChange?.((e) => {
      if (e.type !== "live") return;
      connected = !!e.connected;
      if (!connected) {
        preview = null;
        $3("allocation-submit").disabled = true;
      } else if (e.dirty && dialog.open && !intent) void check().catch((e2) => {
        $3("allocation-status").textContent = e2.message;
      });
    });
    job3 = (await crm2.draft("managed-active-job")).data || "";
    if (job3) void monitor();
    return () => {
      disposed = true;
      turn++;
      clearTimeout(timer);
      stop?.();
    };
  }
  var esc3;
  var init_crm_allocation = __esm({
    "hard-desk/ui/crm-allocation.ts"() {
      "use strict";
      esc3 = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
    }
  });

  // hard-desk/ui/crm-registry.ts
  function typed(field, raw) {
    if (field.type === "date" && raw) {
      const parts = raw.match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
      const iso = parts ? `${parts[3]}-${parts[2]}-${parts[1]}` : raw;
      const value = /* @__PURE__ */ new Date(iso + "T00:00:00Z");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(iso) || !Number.isFinite(+value) || value.toISOString().slice(0, 10) !== iso)
        throw Error(`${field.title}: \u0443\u043A\u0430\u0436\u0438\u0442\u0435 \u0434\u0430\u0442\u0443 \u0414\u0414.\u041C\u041C.\u0413\u0413\u0413\u0413.`);
      return iso;
    }
    if (field.type === "money" && raw) {
      const value = raw.replace(/[ \u00a0]/g, "").replace(",", ".");
      if (!/^-?\d{1,15}(\.\d{1,2})?$/.test(value)) throw Error(`${field.title}: \u0441\u0443\u043C\u043C\u0430 \u0441 \u0434\u0432\u0443\u043C\u044F \u0437\u043D\u0430\u043A\u0430\u043C\u0438 \u043F\u043E\u0441\u043B\u0435 \u0437\u0430\u043F\u044F\u0442\u043E\u0439.`);
      return value;
    }
    if (field.options?.length && raw && !field.options.includes(raw)) throw Error(`${field.title}: \u0432\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0435 \u0438\u0437 \u0441\u043F\u0438\u0441\u043A\u0430.`);
    if (raw.length > 32700) throw Error(`${field.title}: \u0441\u043B\u0438\u0448\u043A\u043E\u043C \u0434\u043B\u0438\u043D\u043D\u044B\u0439 \u0442\u0435\u043A\u0441\u0442.`);
    return raw;
  }
  async function mountRegistry(host, crm2, isCurrent = () => true, options = {}) {
    host.classList.toggle("manager-register-data", !!options.manager);
    let disposed = false, generation = 0, data2, active = { row: 0, col: 0 }, anchor = { row: 0, col: 0 };
    let editing = null;
    let prefs = (await crm2.draft("registry-view")).data || {};
    let colors = (await crm2.draft("registry-colors")).data || {};
    let pending = (await crm2.draft("registry-input")).data || {};
    let entries = [], busy2 = false;
    let allColumns = [], allocationStop, allSelected = false;
    const selectedRows = new Set(Array.isArray(prefs.selectedRows) ? prefs.selectedRows : []);
    let preferenceSave;
    let resizeStop;
    const snapshots = /* @__PURE__ */ new Map();
    const validView = options.manager ? "all" : ["mine", "all"].includes(prefs.view) ? prefs.view : "mine";
    prefs = {
      view: validView,
      search: String(prefs.search || ""),
      sort: prefs.sort || "contract_id",
      direction: prefs.direction === "desc" ? "desc" : "asc",
      offset: Math.max(0, Number(prefs.offset) || 0),
      left: Math.max(0, Number(prefs.left) || 0),
      top: Math.max(0, Number(prefs.top) || 0),
      active: prefs.active,
      anchor: prefs.anchor,
      selectedRows: [...selectedRows],
      employee_uuid: String(prefs.employee_uuid || ""),
      work: ["call", "visit"].includes(prefs.work) ? prefs.work : "all",
      unassigned: prefs.unassigned === "1" ? "1" : "0",
      columnGroup: prefs.columnGroup || "main",
      widths: Object.fromEntries(Object.entries(prefs.widths || {}).filter(([key2, width2]) => key2.length < 160 && typeof width2 === "number" && Number.isFinite(width2) && width2 >= 80 && width2 <= 640)),
      zoom: [80, 90, 100, 110, 125, 150].includes(prefs.zoom) ? prefs.zoom : 100
    };
    if (disposed || !isCurrent()) return () => {
    };
    host.innerHTML = `<div class="registry"><div class="toolbar"><select id="registry-scope" aria-label="\u041E\u0431\u043B\u0430\u0441\u0442\u044C \u0440\u0435\u0435\u0441\u0442\u0440\u0430"><option value="mine">\u041C\u043E\u0439 \u0440\u0435\u0435\u0441\u0442\u0440</option><option value="all">\u041E\u0431\u0449\u0438\u0439 \u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440</option></select><input id="registry-search" type="search" placeholder="\u041A\u043B\u0438\u0435\u043D\u0442, \u0434\u043E\u0433\u043E\u0432\u043E\u0440, \u0442\u0435\u043B\u0435\u0444\u043E\u043D"><button id="registry-find">\u041D\u0430\u0439\u0442\u0438</button><button id="registry-reload">\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C</button><label>\u041C\u0430\u0441\u0448\u0442\u0430\u0431 <select id="registry-zoom">${[80, 90, 100, 110, 125, 150].map((n2) => `<option value="${n2}">${n2}%</option>`).join("")}</select></label></div>
    <div class="toolbar"><button id="registry-edit">\u0418\u0437\u043C\u0435\u043D\u0438\u0442\u044C</button><button id="registry-clear">\u041E\u0447\u0438\u0441\u0442\u0438\u0442\u044C</button><button id="registry-cancel">\u041E\u0442\u043C\u0435\u043D\u0438\u0442\u044C \u0432\u0432\u043E\u0434</button><button id="registry-save" class="primary">\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043F\u0440\u0430\u0432\u043A\u0438</button><button id="registry-review">\u0421\u0432\u0435\u0440\u0438\u0442\u044C \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0443</button><label>\u041F\u043E\u043C\u0435\u0442\u043A\u0430 \u0441\u0442\u0440\u043E\u043A\u0438 <select id="registry-color"><option value="">\u0411\u0435\u0437 \u0446\u0432\u0435\u0442\u0430</option><option value="yellow">\u0416\u0451\u043B\u0442\u044B\u0439</option><option value="red">\u041A\u0440\u0430\u0441\u043D\u044B\u0439</option><option value="blue">\u0413\u043E\u043B\u0443\u0431\u043E\u0439</option></select></label></div>
    <p id="registry-status" role="status"></p><div class="registry-scroll" tabindex="-1"></div><div class="toolbar"><button id="registry-prev">\u041D\u0430\u0437\u0430\u0434</button><span id="registry-page"></span><button id="registry-next">\u0414\u0430\u043B\u0435\u0435</button><button id="registry-reset-widths" class="link">\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C \u0448\u0438\u0440\u0438\u043D\u044B</button></div>
    <dialog id="registry-preview"><h2>\u041F\u0440\u0430\u0432\u043A\u0438 \u0440\u0435\u0435\u0441\u0442\u0440\u0430</h2><div class="registry-preview-values"></div><div class="actions"><button id="registry-apply" class="primary">\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435 \u0438 \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C</button><button id="registry-preview-cancel">\u0412\u0435\u0440\u043D\u0443\u0442\u044C\u0441\u044F \u043A \u0432\u0432\u043E\u0434\u0443</button></div></dialog></div>`;
    const $3 = (id) => host.querySelector("#" + id);
    if (options.manager) {
      host.classList.add("manager-register-data");
      $3("registry-scope").innerHTML = '<option value="all">\u041E\u0431\u0449\u0438\u0439 \u0440\u0435\u0435\u0441\u0442\u0440</option>';
      $3("registry-scope").closest(".toolbar").insertAdjacentHTML("afterend", `<div class="toolbar"><label>\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A <select id="registry-employee"><option value="">\u0412\u0441\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438</option></select></label><label>\u0420\u0430\u0431\u043E\u0442\u0430 <select id="registry-work"><option value="all">\u0412\u0441\u0435</option><option value="call">\u0417\u0432\u043E\u043D\u043A\u0438</option><option value="visit">\u0412\u044B\u0435\u0437\u0434\u044B</option></select></label><label class="check"><input id="registry-unassigned" type="checkbox">\u0411\u0435\u0437 \u043E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u043E\u0433\u043E</label><label>\u041F\u043E\u043B\u044F <select id="registry-fields"><option value="main">\u041E\u0441\u043D\u043E\u0432\u043D\u043E\u0435</option><option value="all">\u0412\u0441\u0435 \u043F\u043E\u043B\u044F</option></select></label><button id="registry-open">\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0443</button></div><div class="toolbar"><button id="registry-select-page">\u0412\u044B\u0431\u0440\u0430\u0442\u044C \u0441\u0442\u0440\u0430\u043D\u0438\u0446\u0443</button><button id="registry-select-all">\u0412\u0435\u0441\u044C \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B\u0439 \u043E\u0442\u0431\u043E\u0440</button><button id="registry-select-clear">\u0421\u043D\u044F\u0442\u044C \u0432\u044B\u0434\u0435\u043B\u0435\u043D\u0438\u0435</button><span id="registry-selected-count"></span></div><div id="registry-allocation"></div>`);
    }
    const scroll = host.querySelector(".registry-scroll");
    const protectedInput = document.createElement("div");
    $3("registry-status").after(protectedInput);
    let revokedInput = (await crm2.draft("registry-revoked-input")).data;
    function showProtectedInput() {
      protectedInput.replaceChildren();
      if (!revokedInput) return;
      const details2 = document.createElement("details"), summary = document.createElement("summary"), text2 = document.createElement("pre"), clear = document.createElement("button");
      summary.textContent = "\u0412\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D \u043F\u043E\u0441\u043B\u0435 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u044F \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F \xB7 " + revokedInput.field;
      text2.textContent = revokedInput.value;
      clear.textContent = "\u0423\u0431\u0440\u0430\u0442\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0439 \u0432\u0432\u043E\u0434";
      clear.onclick = async () => {
        const r = await crm2.draft("registry-revoked-input", null);
        if (r.ok) {
          revokedInput = null;
          showProtectedInput();
        }
      };
      details2.append(summary, text2, clear);
      protectedInput.append(details2);
    }
    showProtectedInput();
    const status2 = (text2) => {
      if (!disposed && isCurrent()) $3("registry-status").textContent = text2;
    };
    const storeInput = async () => {
      const r = await crm2.draft("registry-input", pending);
      if (!r.ok) throw Error(r.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0432\u0432\u043E\u0434 \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435.");
    };
    const protect = (fn2) => () => {
      Promise.resolve().then(fn2).catch((e) => status2(e.message));
    };
    const savePreferences = async () => {
      const result2 = await crm2.draft("registry-view", prefs);
      if (!result2.ok) throw Error(result2.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043D\u0430\u0441\u0442\u0440\u043E\u0439\u043A\u0438 \u0442\u0430\u0431\u043B\u0438\u0446\u044B.");
    };
    const schedulePreferences = () => {
      clearTimeout(preferenceSave);
      preferenceSave = setTimeout(() => {
        if (!disposed && isCurrent()) void savePreferences().catch((e) => status2(e.message));
      }, 200);
    };
    const defaultWidth = (key2) => key2 === "$state" ? crm2.platform === "mobile" ? 44 : 140 : key2.includes("comment") || key2.includes("history") ? 280 : 180;
    const width = (key2) => Object.hasOwn(prefs.widths, key2) ? prefs.widths[key2] : defaultWidth(key2);
    function layout() {
      scroll.style.setProperty("--registry-scale", String(prefs.zoom / 100));
      scroll.style.setProperty("--registry-state-width", `${width("$state") * prefs.zoom / 100}px`);
      const keys = ["$state", ...data2.columns.map((f) => f.key)];
      const table2 = scroll.querySelector("table");
      if (table2) table2.style.width = keys.reduce((n2, key2) => n2 + width(key2) * prefs.zoom / 100, 0) + "px";
      scroll.querySelectorAll("[data-column-width]").forEach((el) => {
        el.style.width = width(el.dataset.columnWidth) * prefs.zoom / 100 + "px";
      });
      scroll.querySelectorAll("[data-resize]").forEach((el) => el.setAttribute("aria-valuenow", String(width(el.dataset.resize))));
    }
    function rememberSelection() {
      const location2 = (p) => ({ contract_uuid: data2.rows[p.row]?.contract_uuid, field: data2.columns[p.col]?.key });
      prefs.active = location2(active);
      prefs.anchor = location2(anchor);
      schedulePreferences();
    }
    function restoreCell(saved, fallback) {
      const row = data2.rows.findIndex((r) => r.contract_uuid === saved?.contract_uuid), col = data2.columns.findIndex((f) => f.key === saved?.field);
      return { row: Math.max(0, Math.min(data2.rows.length - 1, row < 0 ? fallback.row : row)), col: Math.max(0, Math.min(data2.columns.length - 1, col < 0 ? fallback.col : col)) };
    }
    const bounds = () => ({ r0: Math.min(active.row, anchor.row), r1: Math.max(active.row, anchor.row), c0: Math.min(active.col, anchor.col), c1: Math.max(active.col, anchor.col) });
    const rowEntry = (id) => entries.filter((e) => e.action === "registry-edit" && e.state !== "accepted" && !e.reviewed_at && e.body.rows.some((p) => p.contract_uuid === id));
    const pendingCount = () => Object.values(pending).reduce((n2, p) => n2 + Object.keys(p.values).length, 0);
    const value = (r, key2) => {
      if (pending[r.contract_uuid] && key2 in pending[r.contract_uuid].values) return pending[r.contract_uuid].values[key2];
      const queued = rowEntry(r.contract_uuid).at(-1)?.body.rows.find((p) => p.contract_uuid === r.contract_uuid);
      return queued && key2 in queued.values ? queued.values[key2] : key2 === "contract_id" ? r.external_id : r.fields[key2];
    };
    function showValue(v, f) {
      if (v === null || v === void 0 || v === "") return "\u2014";
      if (f.type === "date" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return String(v).split("-").reverse().join(".");
      return String(v);
    }
    function select(cell, extend = false, focus = true, remember = true) {
      if (!data2?.rows.length) return;
      active = { row: Math.max(0, Math.min(data2.rows.length - 1, cell.row)), col: Math.max(0, Math.min(data2.columns.length - 1, cell.col)) };
      if (!extend) anchor = { ...active };
      const b = bounds();
      scroll.querySelectorAll("[data-cell]").forEach((el) => {
        const [r, c] = el.dataset.cell.split(":").map(Number);
        const selected2 = r >= b.r0 && r <= b.r1 && c >= b.c0 && c <= b.c1;
        el.classList.toggle("selected", selected2);
        el.setAttribute("aria-selected", String(selected2));
        el.tabIndex = r === active.row && c === active.col ? 0 : -1;
      });
      if (focus) scroll.querySelector(`[data-cell="${keyOf(active)}"]`)?.focus({ preventScroll: true });
      $3("registry-color").setAttribute("data-contract", data2.rows[active.row].contract_uuid);
      $3("registry-color").value = colors[data2.rows[active.row].contract_uuid] || "";
      if (remember) rememberSelection();
    }
    function paintStatus() {
      if (!data2) return;
      for (const row of data2.rows) {
        const queued = rowEntry(row.contract_uuid);
        const el = scroll.querySelector(`[data-row="${row.contract_uuid}"] .registry-row-state`);
        if (el) {
          const text2 = queued.some((e) => e.state === "attention") ? "\u0422\u0440\u0435\u0431\u0443\u0435\u0442 \u0441\u0432\u0435\u0440\u043A\u0438" : queued.length ? "\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E \u043D\u0430 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0435" : pending[row.contract_uuid] ? "\u0420\u0435\u0434\u0430\u043A\u0442\u0438\u0440\u0443\u0435\u0442\u0441\u044F" : "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u043E";
          if (options.manager) {
            el.innerHTML = `<label title="${text2}"><input type="checkbox" data-select-row="${row.contract_uuid}" aria-label="\u0412\u044B\u0431\u0440\u0430\u0442\u044C ${escape(row.external_id)}"${selectedRows.has(row.contract_uuid) || allSelected ? " checked" : ""}><span>${text2}</span></label>`;
            el.querySelector("input").onchange = (e) => {
              allSelected = false;
              e.target.checked ? selectedRows.add(row.contract_uuid) : selectedRows.delete(row.contract_uuid);
              selectionChanged();
            };
          } else el.textContent = text2;
        }
      }
    }
    async function sync() {
      const result2 = await crm2.queue();
      if (disposed || !isCurrent() || !result2.ok) return;
      entries = result2.data.entries || [];
      for (const entry of entries.filter((e) => e.action === "registry-edit" && e.state === "accepted")) {
        for (const ack of entry.result?.rows || []) {
          const row = data2?.rows.find((r) => r.contract_uuid === ack.contract_uuid);
          if (!row) continue;
          const newer = Object.entries(ack.revisions).some(([key2, rev]) => Number(rev) > Number(row.revisions[key2] || 0));
          if (newer) {
            Object.assign(row.fields, ack.values);
            Object.assign(row.revisions, ack.revisions);
          }
        }
      }
      paintStatus();
    }
    function paint() {
      resizeStop?.();
      editing = null;
      const resizer = (key2, title) => `<span class="registry-resizer" role="separator" tabindex="0" aria-orientation="vertical" aria-label="\u0428\u0438\u0440\u0438\u043D\u0430: ${escape(title)}" aria-valuemin="80" aria-valuemax="640" data-resize="${escape(key2)}" title="\u041F\u043E\u0442\u044F\u043D\u0438\u0442\u0435 \u0433\u0440\u0430\u043D\u0438\u0446\u0443. \u0421\u0442\u0440\u0435\u043B\u043A\u0438 \u043C\u0435\u043D\u044F\u044E\u0442 \u0448\u0438\u0440\u0438\u043D\u0443, Home \u0441\u0431\u0440\u0430\u0441\u044B\u0432\u0430\u0435\u0442."></span>`;
      scroll.innerHTML = `<table class="registry-grid" role="grid" aria-label="\u0420\u0435\u0435\u0441\u0442\u0440 \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432"><colgroup>${["$state", ...data2.columns.map((f) => f.key)].map((key2) => `<col data-column-width="${escape(key2)}">`).join("")}</colgroup><thead><tr><th class="registry-row-state">\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u0435${resizer("$state", "\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u0435")}</th>${data2.columns.map((f) => `<th><button data-sort="${escape(f.key)}">${escape(f.title)}${prefs.sort === f.key ? prefs.direction === "asc" ? " \u2191" : " \u2193" : ""}</button>${resizer(f.key, f.title)}</th>`).join("")}</tr></thead><tbody>${data2.rows.map((row, r) => `<tr data-row="${escape(row.contract_uuid)}" data-color="${colors[row.contract_uuid] || ""}"><td class="registry-row-state"></td>${data2.columns.map((f, c) => `<td role="gridcell" data-cell="${r}:${c}" data-field="${escape(f.key)}" tabindex="-1" aria-readonly="${!row.writable_fields.includes(f.key)}" class="${row.writable_fields.includes(f.key) ? "writable" : "readonly"}"><span>${escape(showValue(value(row, f.key), f))}</span>${f.key === "contract_id" ? `<small class="registry-creditor">${escape(row.fields.partner || row.creditor_id)}</small>` : ""}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      layout();
      $3("registry-page").textContent = `${data2.total ? Number(prefs.offset) + 1 : 0}\u2013${Math.min(Number(prefs.offset) + data2.rows.length, data2.total)} \u0438\u0437 ${data2.total}`;
      $3("registry-prev").disabled = !prefs.offset;
      $3("registry-next").disabled = !data2.has_more;
      $3("registry-color").disabled = !data2.rows.length;
      active = restoreCell(prefs.active, active);
      anchor = restoreCell(prefs.anchor, anchor);
      select(active, true, false, false);
      paintStatus();
      scroll.scrollLeft = prefs.left;
      scroll.scrollTop = prefs.top;
      scroll.querySelectorAll("[data-resize]").forEach((handle) => {
        const key2 = handle.dataset.resize;
        const change = (next) => {
          prefs.widths[key2] = Math.max(80, Math.min(640, Math.round(next)));
          layout();
        };
        handle.onpointerdown = (e) => {
          if (e.button !== 0) return;
          e.preventDefault();
          e.stopPropagation();
          resizeStop?.();
          const initial = width(key2), start = e.clientX, pointer = e.pointerId;
          handle.setPointerCapture(pointer);
          scroll.classList.add("resizing");
          handle.onpointermove = (next) => {
            if (next.pointerId === pointer) change(initial + (next.clientX - start) / (prefs.zoom / 100));
          };
          const finish = () => {
            handle.onpointermove = null;
            handle.onpointerup = null;
            handle.onpointercancel = null;
            handle.onlostpointercapture = null;
            scroll.classList.remove("resizing");
            resizeStop = void 0;
            if (handle.hasPointerCapture(pointer)) handle.releasePointerCapture(pointer);
            schedulePreferences();
          };
          handle.onpointerup = finish;
          handle.onpointercancel = finish;
          handle.onlostpointercapture = finish;
          resizeStop = finish;
        };
        handle.ondblclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          delete prefs.widths[key2];
          layout();
          schedulePreferences();
        };
        handle.onkeydown = (e) => {
          if (!["ArrowLeft", "ArrowRight", "Home"].includes(e.key)) return;
          e.preventDefault();
          e.stopPropagation();
          if (e.key === "Home") delete prefs.widths[key2];
          else change(width(key2) + (e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 50 : 10));
          layout();
          schedulePreferences();
        };
      });
      scroll.querySelectorAll("[data-cell]").forEach((el) => {
        const position = () => {
          const [row, col] = el.dataset.cell.split(":").map(Number);
          return { row, col };
        };
        el.onmousedown = (e) => {
          if (editing && el.contains(editing)) return;
          select(position(), e.shiftKey);
        };
        el.ondblclick = () => beginEdit(position());
      });
      scroll.querySelectorAll("[data-sort]").forEach((b) => {
        if (data2.columns.find((f) => f.key === b.dataset.sort)?.sortable === false) {
          b.disabled = true;
          b.tabIndex = -1;
          return;
        }
        b.onclick = protect(async () => {
          prefs.direction = prefs.sort === b.dataset.sort && prefs.direction === "asc" ? "desc" : "asc";
          prefs.sort = b.dataset.sort;
          prefs.offset = 0;
          await load3();
        });
      });
    }
    async function load3() {
      if (editing) editing.blur();
      const n2 = ++generation;
      await savePreferences();
      const result2 = await crm2.api("GET", "registry", void 0, { view: prefs.view, search: prefs.search, sort: prefs.sort, direction: prefs.direction, offset: prefs.offset, limit: 50, ...options.manager ? { employee_uuid: prefs.employee_uuid, work: prefs.work, unassigned: prefs.unassigned } : {} });
      if (disposed || !isCurrent() || n2 !== generation) return;
      if (!result2.ok) throw Error(result2.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u0440\u043E\u0447\u0438\u0442\u0430\u0442\u044C \u0440\u0435\u0435\u0441\u0442\u0440.");
      data2 = result2.data;
      allColumns = data2.columns;
      if (options.manager) {
        const main = ["client", "contract_id", "partner", "responsible_soft", "responsible", "debt_today", "soft_call_status", "soft_comm_date", "call_status", "visit_date"];
        allColumns = allColumns.map((f) => f.key === "responsible" ? { ...f, title: "\u041E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u044B\u0439 \u0437\u0430 \u0432\u044B\u0435\u0437\u0434\u044B" } : f);
        const groups2 = [...new Set(allColumns.map((f) => f.section))];
        $3("registry-fields").innerHTML = '<option value="main">\u041E\u0441\u043D\u043E\u0432\u043D\u043E\u0435</option><option value="all">\u0412\u0441\u0435 \u043F\u043E\u043B\u044F</option>' + groups2.map((g) => `<option value="${escape(g)}">${escape(g)}</option>`).join("");
        if (!["main", "all", ...groups2].includes(prefs.columnGroup)) prefs.columnGroup = "main";
        $3("registry-fields").value = prefs.columnGroup;
        data2.columns = prefs.columnGroup === "all" ? allColumns : prefs.columnGroup === "main" ? main.map((key2) => allColumns.find((f) => f.key === key2)).filter(Boolean) : allColumns.filter((f) => ["client", "contract_id"].includes(f.key) || f.section === prefs.columnGroup);
      }
      for (const row of data2.rows) snapshots.set(row.contract_uuid, row);
      await sync();
      if (disposed || !isCurrent() || n2 !== generation) return;
      paint();
      status2(result2.stale ? "\u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u0430\u044F \u043A\u043E\u043F\u0438\u044F. \u041F\u0440\u0430\u0432\u043A\u0438 \u0431\u0443\u0434\u0443\u0442 \u043F\u0440\u043E\u0432\u0435\u0440\u0435\u043D\u044B \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C \u043F\u043E\u0441\u043B\u0435 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u044F." : `\u041F\u0440\u0430\u0432\u043E\u043A \u0432 \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A\u0435: ${pendingCount()}`);
    }
    function stage(r, f, raw) {
      if (!r.writable_fields.includes(f.key)) throw Error(`${f.title}: \u043F\u043E\u043B\u0435 \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u043E \u0442\u043E\u043B\u044C\u043A\u043E \u0434\u043B\u044F \u0447\u0442\u0435\u043D\u0438\u044F.`);
      if (rowEntry(r.contract_uuid).length) throw Error("\u0412 \u0441\u0442\u0440\u043E\u043A\u0435 \u0435\u0441\u0442\u044C \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0430. \u0414\u043E\u0436\u0434\u0438\u0442\u0435\u0441\u044C \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F \u0438\u043B\u0438 \u0441\u0432\u0435\u0440\u044C\u0442\u0435 \u0435\u0451 \u0432 \u043E\u0447\u0435\u0440\u0435\u0434\u0438.");
      const p = pending[r.contract_uuid] ||= { values: {}, clear: [], base_revisions: {} };
      if (!(f.group in p.base_revisions)) p.base_revisions[f.group] = r.revisions[f.group] || 0;
      p.values[f.key] = raw;
      p.clear = p.clear.filter((k) => k !== f.key);
      if (raw === null) p.clear.push(f.key);
    }
    function beginEdit(p = active) {
      if (busy2 || editing || !data2?.rows.length) return;
      select(p);
      const row = data2.rows[p.row], f = data2.columns[p.col];
      if (!row.writable_fields.includes(f.key) || rowEntry(row.contract_uuid).length) {
        status2("\u042F\u0447\u0435\u0439\u043A\u0430 \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u0430 \u0442\u043E\u043B\u044C\u043A\u043E \u0434\u043B\u044F \u0447\u0442\u0435\u043D\u0438\u044F \u0438\u043B\u0438 \u043E\u0436\u0438\u0434\u0430\u0435\u0442 \u0441\u0432\u0435\u0440\u043A\u0438.");
        return;
      }
      const el = scroll.querySelector(`[data-cell="${keyOf(p)}"]`);
      const original = structuredClone(pending[row.contract_uuid] || null);
      const initial = value(row, f.key);
      const editor = document.createElement(f.options.length ? "select" : "textarea");
      if (editor instanceof HTMLSelectElement) editor.innerHTML = '<option value="">\u041F\u0443\u0441\u0442\u043E\u0439 \u0442\u0435\u043A\u0441\u0442</option>' + f.options.map((v) => `<option>${escape(v)}</option>`).join("");
      editor.value = initial ?? "";
      editor.setAttribute("aria-label", f.title);
      editing = editor;
      el.replaceChildren(editor);
      editor.focus();
      if (editor instanceof HTMLTextAreaElement) editor.select();
      let cancelled = false;
      editor.oninput = () => {
        try {
          stage(row, f, editor.value);
          void storeInput().catch((e) => status2(e.message));
          paintStatus();
        } catch (e) {
          status2(e.message);
        }
      };
      editor.onkeydown = (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          cancelled = true;
          if (original) pending[row.contract_uuid] = original;
          else delete pending[row.contract_uuid];
          void storeInput().catch((e2) => status2(e2.message));
          editor.blur();
        } else if (e.key === "Tab" || e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          editor.blur();
          select({ row: p.row + (e.key === "Enter" ? 1 : 0), col: p.col + (e.key === "Tab" ? e.shiftKey ? -1 : 1 : 0) });
        }
      };
      editor.onblur = () => {
        if (!cancelled && editor.value !== String(initial ?? "")) {
          stage(row, f, editor.value);
          void storeInput().catch((e) => status2(e.message));
        }
        editing = null;
        el.innerHTML = `<span>${escape(showValue(value(row, f.key), f))}</span>`;
        paintStatus();
        status2(cancelled ? "\u041D\u0435\u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043D\u043D\u044B\u0439 \u0432\u0432\u043E\u0434 \u043E\u0442\u043C\u0435\u043D\u0451\u043D." : `\u041F\u0440\u0430\u0432\u043E\u043A \u0432 \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A\u0435: ${pendingCount()}`);
      };
    }
    async function clearSelection() {
      if (!data2?.rows.length) return;
      if (editing) editing.blur();
      const b = bounds();
      const batch = [];
      for (let r = b.r0; r <= b.r1; r++) for (let c = b.c0; c <= b.c1; c++) {
        const row = data2.rows[r], f = data2.columns[c];
        if (!row.writable_fields.includes(f.key) || rowEntry(row.contract_uuid).length) throw Error("\u0414\u0438\u0430\u043F\u0430\u0437\u043E\u043D \u0441\u043E\u0434\u0435\u0440\u0436\u0438\u0442 \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B\u0435 \u044F\u0447\u0435\u0439\u043A\u0438. \u041D\u0438\u0447\u0435\u0433\u043E \u043D\u0435 \u043E\u0447\u0438\u0449\u0435\u043D\u043E.");
        batch.push({ row, f });
      }
      for (const { row, f } of batch) stage(row, f, null);
      await storeInput();
      paint();
      status2(`\u042F\u0432\u043D\u0430\u044F \u043E\u0447\u0438\u0441\u0442\u043A\u0430: ${batch.length} \u044F\u0447\u0435\u0435\u043A. \u041F\u0440\u043E\u0432\u0435\u0440\u044C\u0442\u0435 \u0438 \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u0435 \u043F\u0440\u0430\u0432\u043A\u0438.`);
    }
    async function preview() {
      if (editing) editing.blur();
      await storeInput();
      await sync();
      $3("registry-apply").textContent = "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435 \u0438 \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C";
      const missing = Object.keys(pending).filter((id) => !snapshots.has(id));
      if (missing.length) {
        const result2 = await crm2.api("GET", "registry", void 0, { view: "all", ids: missing.join(","), limit: 100 });
        if (!result2.ok) throw Error("\u041D\u0435\u0442 \u0441\u0432\u044F\u0437\u0438 \u0434\u043B\u044F \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0438 \u0432\u0432\u043E\u0434\u0430 \u0441 \u0434\u0440\u0443\u0433\u0438\u0445 \u0441\u0442\u0440\u0430\u043D\u0438\u0446. \u0427\u0435\u0440\u043D\u043E\u0432\u0438\u043A \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D.");
        for (const row of result2.data.rows) snapshots.set(row.contract_uuid, row);
      }
      const rows = Object.entries(pending).map(([contract_uuid, p]) => {
        const row = snapshots.get(contract_uuid);
        if (!row) throw Error("\u0415\u0441\u0442\u044C \u0432\u0432\u043E\u0434 \u043D\u0430 \u0434\u0440\u0443\u0433\u043E\u0439 \u0441\u0442\u0440\u0430\u043D\u0438\u0446\u0435. \u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u0435 \u0435\u0433\u043E \u043D\u0430 \u0438\u0441\u0445\u043E\u0434\u043D\u043E\u0439 \u0441\u0442\u0440\u0430\u043D\u0438\u0446\u0435 \u0438\u043B\u0438 \u043E\u0442\u043C\u0435\u043D\u0438\u0442\u0435 \u0432\u0432\u043E\u0434.");
        const values = {};
        for (const [key2, raw] of Object.entries(p.values)) {
          const f = allColumns.find((f2) => f2.key === key2);
          if (!f || !row.writable_fields.includes(key2) || rowEntry(contract_uuid).length) throw Error("\u041F\u0440\u0430\u0432\u043E \u044F\u0447\u0435\u0439\u043A\u0438 \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u043E\u0441\u044C. \u0412\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D; \u043E\u0442\u043A\u0440\u043E\u0439\u0442\u0435 \u043E\u0447\u0435\u0440\u0435\u0434\u044C \u0438\u043B\u0438 \u043E\u0431\u043D\u043E\u0432\u0438\u0442\u0435 \u0440\u0435\u0435\u0441\u0442\u0440.");
          values[key2] = raw === null ? null : typed(f, String(raw));
        }
        return { contract_uuid, ...p, values };
      });
      if (!rows.length) {
        status2("\u041D\u0435\u0442 \u043D\u043E\u0432\u044B\u0445 \u043F\u0440\u0430\u0432\u043E\u043A.");
        return;
      }
      if (rows.length > 100 || pendingCount() > 1e3) throw Error("\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u0435 \u043F\u0430\u043A\u0435\u0442 \u0434\u043E 100 \u0441\u0442\u0440\u043E\u043A \u0438 1000 \u044F\u0447\u0435\u0435\u043A.");
      const dialog = $3("registry-preview");
      dialog.querySelector(".registry-preview-values").innerHTML = rows.map((p) => {
        const row = snapshots.get(p.contract_uuid);
        return `<h3>${escape(row.external_id)} \xB7 ${escape(row.fields.partner || row.creditor_id)}</h3><dl>${Object.entries(p.values).map(([key2, v]) => `<dt>${escape(data2.columns.find((f) => f.key === key2).title)}</dt><dd>${escape(showValue(row.fields[key2], data2.columns.find((f) => f.key === key2)))} \u2192 ${v === null ? "\u041E\u0447\u0438\u0441\u0442\u0438\u0442\u044C" : escape(v)}</dd>`).join("")}</dl>`;
      }).join("");
      $3("registry-apply").onclick = protect(async () => {
        if (busy2) return;
        busy2 = true;
        $3("registry-apply").disabled = true;
        try {
          const result2 = await crm2.registryEdit({ schema_version: 1, operation_id: crypto.randomUUID(), rows });
          if (!result2.ok) throw Error(result2.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0437\u0430\u0449\u0438\u0442\u0438\u0442\u044C \u043F\u0440\u0430\u0432\u043A\u0438 \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435.");
          for (const row of rows) delete pending[row.contract_uuid];
          await storeInput();
          dialog.close();
          await sync();
          paint();
          status2("\u041F\u0440\u0430\u0432\u043A\u0438 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u044B \u043D\u0430 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0435. \u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435 \u0438 \u043A\u043E\u043D\u0444\u043B\u0438\u043A\u0442\u044B \u043F\u043E\u043A\u0430\u0437\u0430\u043D\u044B \u0432 \u0441\u0442\u0440\u043E\u043A\u0430\u0445.");
        } finally {
          busy2 = false;
          $3("registry-apply").disabled = false;
        }
      });
      dialog.showModal();
    }
    async function review() {
      if (editing) editing.blur();
      await sync();
      const entry = rowEntry(data2.rows[active.row]?.contract_uuid).find((e) => e.state === "attention");
      if (!entry) throw Error("\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u0442\u0440\u043E\u043A\u0443 \u0441 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u043E\u0439, \u0442\u0440\u0435\u0431\u0443\u044E\u0449\u0435\u0439 \u0441\u0432\u0435\u0440\u043A\u0438.");
      const ids = entry.body.rows.map((r) => r.contract_uuid);
      if (ids.some((id) => pending[id])) throw Error("\u0412 \u0441\u0442\u0440\u043E\u043A\u0430\u0445 \u043F\u0430\u043A\u0435\u0442\u0430 \u0443\u0436\u0435 \u0435\u0441\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A. \u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u0435 \u0438\u043B\u0438 \u043E\u0442\u043C\u0435\u043D\u0438\u0442\u0435 \u0435\u0433\u043E \u043F\u0435\u0440\u0435\u0434 \u0441\u0432\u0435\u0440\u043A\u043E\u0439.");
      const latest = await crm2.api("GET", "registry", void 0, { view: "all", ids: ids.join(","), limit: 100 });
      if (!latest.ok || latest.stale) throw Error("\u0414\u043B\u044F \u0441\u0432\u0435\u0440\u043A\u0438 \u043D\u0443\u0436\u0435\u043D \u0442\u0435\u043A\u0443\u0449\u0438\u0439 \u043E\u0442\u0432\u0435\u0442 \u0441\u0435\u0440\u0432\u0435\u0440\u0430. \u0418\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430.");
      const restored = {};
      for (const p of entry.body.rows) {
        const row = latest.data.rows.find((r) => r.contract_uuid === p.contract_uuid);
        if (!row || Object.keys(p.values).some((key2) => !row.writable_fields.includes(key2))) throw Error("\u0421\u0442\u0440\u043E\u043A\u0430 \u0438\u043B\u0438 \u043F\u043E\u043B\u0435 \u0431\u043E\u043B\u044C\u0448\u0435 \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B \u0434\u043B\u044F \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u044F. \u0418\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430.");
        restored[p.contract_uuid] = { values: p.values, clear: p.clear, base_revisions: Object.fromEntries(Object.keys(p.base_revisions).map((key2) => [key2, row.revisions[key2] || 0])) };
        snapshots.set(p.contract_uuid, row);
      }
      const dialog = $3("registry-preview");
      dialog.querySelector(".registry-preview-values").innerHTML = entry.body.rows.map((p) => {
        const row = snapshots.get(p.contract_uuid);
        return `<h3>${escape(row.external_id)} \xB7 ${escape(row.fields.partner || row.creditor_id)}</h3><dl>${Object.entries(p.values).map(([key2, v]) => `<dt>${escape(data2.columns.find((f) => f.key === key2)?.title || key2)}</dt><dd>\u0421\u0435\u0439\u0447\u0430\u0441 \u043D\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0435: ${escape(row.fields[key2] ?? "\u041F\u0443\u0441\u0442\u043E")}<br>\u0412\u0430\u0448 \u0432\u0432\u043E\u0434: ${v === null ? "\u041E\u0447\u0438\u0441\u0442\u0438\u0442\u044C" : escape(v)}</dd>`).join("")}</dl>`;
      }).join("");
      const apply = $3("registry-apply");
      apply.textContent = "\u0412\u0435\u0440\u043D\u0443\u0442\u044C \u043F\u0430\u043A\u0435\u0442 \u0432 \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A \u0434\u043B\u044F \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0438";
      apply.onclick = protect(async () => {
        Object.assign(pending, restored);
        await storeInput();
        const result2 = await crm2.queue("review-registry", entry.body.operation_id);
        if (!result2.ok) throw Error(result2.text);
        apply.textContent = "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435 \u0438 \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C";
        dialog.close();
        await sync();
        paint();
        status2("\u041F\u043E\u043B\u043D\u044B\u0439 \u043F\u0430\u043A\u0435\u0442 \u0432\u043E\u0437\u0432\u0440\u0430\u0449\u0451\u043D \u0432 \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A. \u041F\u0440\u043E\u0432\u0435\u0440\u044C\u0442\u0435 \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F \u0438 \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u0435 \u043D\u043E\u0432\u043E\u0439 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0435\u0439. \u0418\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u043E\u0448\u0438\u0431\u043A\u0430 \u043E\u0441\u0442\u0430\u0451\u0442\u0441\u044F \u0432 \u043E\u0447\u0435\u0440\u0435\u0434\u0438.");
      });
      dialog.showModal();
    }
    scroll.onkeydown = (e) => {
      if (editing || e.target.closest("[data-resize]")) return;
      const moves = { ArrowLeft: { row: 0, col: -1 }, ArrowRight: { row: 0, col: 1 }, ArrowUp: { row: -1, col: 0 }, ArrowDown: { row: 1, col: 0 }, Tab: { row: 0, col: e.shiftKey ? -1 : 1 }, Enter: { row: 1, col: 0 } };
      if (moves[e.key]) {
        e.preventDefault();
        const d = moves[e.key];
        select({ row: active.row + d.row, col: active.col + d.col }, e.shiftKey && e.key !== "Tab");
        scroll.querySelector(`[data-cell="${keyOf(active)}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
      } else if (e.key === "F2") {
        e.preventDefault();
        beginEdit();
      } else if (e.key === "Delete") {
        e.preventDefault();
        protect(clearSelection)();
      }
    };
    scroll.oncopy = (e) => {
      if (editing || !data2?.rows.length) return;
      const b = bounds(), lines = [];
      for (let r = b.r0; r <= b.r1; r++) {
        const cells = [];
        for (let c = b.c0; c <= b.c1; c++) cells.push(String(value(data2.rows[r], data2.columns[c].key) ?? "").replace(/\t/g, " ").replace(/\r?\n/g, " "));
        lines.push(cells.join("	"));
      }
      e.preventDefault();
      e.clipboardData?.setData("text/plain", lines.join("\n"));
    };
    scroll.onpaste = (e) => {
      if (editing || !data2?.rows.length) return;
      e.preventDefault();
      const raw = e.clipboardData?.getData("text/plain") || "";
      protect(async () => {
        const lines = raw.replace(/\r/g, "").replace(/\n$/, "").split("\n").map((line) => line.split("	"));
        if (!lines.length || !lines[0].length || lines.some((line) => line.length !== lines[0].length) || lines.length * lines[0].length > 1e3) throw Error("\u0412\u0441\u0442\u0430\u0432\u044C\u0442\u0435 \u043F\u0440\u044F\u043C\u043E\u0443\u0433\u043E\u043B\u044C\u043D\u044B\u0439 \u0434\u0438\u0430\u043F\u0430\u0437\u043E\u043D \u0434\u043E 1000 \u044F\u0447\u0435\u0435\u043A.");
        const batch = [];
        for (let r = 0; r < lines.length; r++) for (let c = 0; c < lines[r].length; c++) {
          const row = data2.rows[active.row + r], f = data2.columns[active.col + c];
          if (!row || !f || !row.writable_fields.includes(f.key) || rowEntry(row.contract_uuid).length) throw Error("\u0414\u0438\u0430\u043F\u0430\u0437\u043E\u043D \u0432\u044B\u0445\u043E\u0434\u0438\u0442 \u0437\u0430 \u0441\u0442\u0440\u0430\u043D\u0438\u0446\u0443 \u0438\u043B\u0438 \u0441\u043E\u0434\u0435\u0440\u0436\u0438\u0442 \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B\u0435 \u044F\u0447\u0435\u0439\u043A\u0438. \u041D\u0438\u0447\u0435\u0433\u043E \u043D\u0435 \u0432\u0441\u0442\u0430\u0432\u043B\u0435\u043D\u043E.");
          batch.push({ row, f, v: typed(f, lines[r][c]) });
        }
        for (const { row, f, v } of batch) stage(row, f, v);
        await storeInput();
        paint();
        await preview();
      })();
    };
    $3("registry-scope").value = prefs.view;
    $3("registry-search").value = prefs.search;
    $3("registry-zoom").value = String(prefs.zoom);
    $3("registry-scope").onchange = protect(async () => {
      prefs.view = $3("registry-scope").value;
      prefs.offset = 0;
      await load3();
    });
    $3("registry-zoom").onchange = protect(async () => {
      prefs.zoom = Number($3("registry-zoom").value);
      layout();
      await savePreferences();
    });
    $3("registry-reset-widths").onclick = protect(async () => {
      prefs.widths = {};
      layout();
      await savePreferences();
      status2("\u0428\u0438\u0440\u0438\u043D\u044B \u043A\u043E\u043B\u043E\u043D\u043E\u043A \u0441\u0431\u0440\u043E\u0448\u0435\u043D\u044B.");
    });
    $3("registry-color").onchange = protect(async () => {
      if (!data2?.rows.length) return;
      const b = bounds(), color = $3("registry-color").value;
      for (let r = b.r0; r <= b.r1; r++) {
        const id = data2.rows[r].contract_uuid;
        if (color) colors[id] = color;
        else delete colors[id];
        scroll.querySelector(`[data-row="${id}"]`).dataset.color = color;
      }
      const result2 = await crm2.draft("registry-colors", colors);
      if (!result2.ok) throw Error(result2.text);
      status2("\u041B\u0438\u0447\u043D\u044B\u0435 \u043F\u043E\u043C\u0435\u0442\u043A\u0438 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u044B \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435.");
    });
    $3("registry-find").onclick = protect(async () => {
      prefs.search = $3("registry-search").value;
      prefs.offset = 0;
      allSelected = false;
      await load3();
      selectionChanged();
    });
    $3("registry-search").onkeydown = (e) => {
      if (e.key === "Enter") $3("registry-find").click();
    };
    $3("registry-reload").onclick = protect(load3);
    $3("registry-prev").onclick = protect(async () => {
      prefs.offset = Math.max(0, prefs.offset - 50);
      await load3();
    });
    $3("registry-next").onclick = protect(async () => {
      prefs.offset += 50;
      await load3();
    });
    $3("registry-edit").onclick = () => beginEdit();
    $3("registry-clear").onclick = protect(clearSelection);
    $3("registry-save").onclick = protect(preview);
    $3("registry-review").onclick = protect(review);
    $3("registry-cancel").onclick = protect(async () => {
      if (editing) {
        editing.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
        return;
      }
      pending = {};
      await storeInput();
      paint();
      status2("\u041D\u0435\u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043D\u043D\u044B\u0435 \u043F\u0440\u0430\u0432\u043A\u0438 \u043E\u0442\u043C\u0435\u043D\u0435\u043D\u044B.");
    });
    $3("registry-preview-cancel").onclick = () => $3("registry-preview").close();
    function selectionChanged() {
      if (!options.manager) return;
      prefs.selectedRows = [...selectedRows];
      schedulePreferences();
      $3("registry-selected-count").textContent = allSelected ? "\u0412\u0435\u0441\u044C \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B\u0439 \u043E\u0442\u0431\u043E\u0440" : `\u0412\u044B\u0431\u0440\u0430\u043D\u043E \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432: ${selectedRows.size}`;
      paintStatus();
    }
    const filters = () => ({ search: prefs.search, employee_uuid: prefs.employee_uuid, work: prefs.work, unassigned: prefs.unassigned });
    if (options.manager) {
      $3("registry-open").onclick = () => {
        const row = data2?.rows[active.row];
        if (row?.person_uuid) options.openPerson?.(row.person_uuid);
      };
      $3("registry-select-page").onclick = () => {
        allSelected = false;
        data2.rows.forEach((r) => selectedRows.add(r.contract_uuid));
        selectionChanged();
      };
      $3("registry-select-all").onclick = () => {
        allSelected = true;
        selectedRows.clear();
        selectionChanged();
      };
      $3("registry-select-clear").onclick = () => {
        allSelected = false;
        selectedRows.clear();
        selectionChanged();
      };
      $3("registry-fields").onchange = protect(async () => {
        prefs.columnGroup = $3("registry-fields").value;
        await load3();
      });
      if (options.distribution) {
        const staff2 = await crm2.api("GET", "assignment-staff");
        if (staff2.ok) $3("registry-employee").insertAdjacentHTML("beforeend", staff2.data.staff.map((s) => `<option value="${escape(s.employee_uuid)}">${escape(s.name)}</option>`).join(""));
      }
      $3("registry-employee").value = prefs.employee_uuid;
      $3("registry-work").value = prefs.work;
      $3("registry-unassigned").checked = prefs.unassigned === "1";
      ["registry-employee", "registry-work", "registry-unassigned"].forEach((id) => $3(id).onchange = protect(async () => {
        prefs.employee_uuid = $3("registry-employee").value;
        prefs.work = $3("registry-work").value;
        prefs.unassigned = $3("registry-unassigned").checked ? "1" : "0";
        prefs.offset = 0;
        allSelected = false;
        await load3();
        selectionChanged();
      }));
    }
    let liveGeneration = 0;
    async function refreshLive() {
      if (!data2?.rows.length) return;
      const turn = ++liveGeneration, ids = data2.rows.map((r) => r.contract_uuid).join(",");
      const result2 = await crm2.api("GET", "registry", void 0, { view: "all", ids, limit: 100 });
      if (disposed || !isCurrent() || turn !== liveGeneration) return;
      if (!result2.ok || result2.stale) {
        status2("\u041D\u0435\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u043E\u0439 \u0430\u043A\u0442\u0443\u0430\u043B\u044C\u043D\u043E\u0441\u0442\u0438. \u0418\u0441\u0445\u043E\u0434\u043D\u044B\u0439 \u0432\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D.");
        return;
      }
      const latest = new Map(result2.data.rows.map((r) => [r.contract_uuid, r]));
      if (editing) {
        const cell = editing.closest("[data-cell]");
        const [r, c] = (cell?.dataset.cell || "").split(":").map(Number), row = data2.rows[r], field = data2.columns[c];
        const fresh = row && latest.get(row.contract_uuid);
        if (row && field && !fresh?.writable_fields?.includes(field.key)) {
          revokedInput = { contract_uuid: row.contract_uuid, field: field.key, value: editing.value, base_revisions: row.revisions };
          const saved = await crm2.draft("registry-revoked-input", revokedInput);
          if (!saved.ok) throw Error("\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0437\u0430\u0449\u0438\u0442\u0438\u0442\u044C \u0442\u0435\u043A\u0443\u0449\u0438\u0439 \u0432\u0432\u043E\u0434. \u041E\u0441\u0442\u0430\u0432\u044C\u0442\u0435 \u0440\u0435\u0434\u0430\u043A\u0442\u043E\u0440 \u043E\u0442\u043A\u0440\u044B\u0442\u044B\u043C.");
          showProtectedInput();
        }
      }
      for (const row of data2.rows) {
        const fresh = latest.get(row.contract_uuid);
        row.writable_fields = fresh?.writable_fields || [];
        if (fresh) {
          row.fields = fresh.fields;
          row.revisions = fresh.revisions;
        }
        scroll.querySelectorAll(`[data-row="${row.contract_uuid}"] [data-cell]`).forEach((cell) => {
          const key2 = cell.dataset.field, field = data2.columns.find((f) => f.key === key2);
          const writable = row.writable_fields.includes(key2);
          cell.setAttribute("aria-readonly", String(!writable));
          cell.classList.toggle("readonly", !writable);
          cell.classList.toggle("writable", writable);
          if (!cell.contains(editing) && !pending[row.contract_uuid] && !rowEntry(row.contract_uuid).length && field) {
            const span = cell.querySelector("span");
            if (span) span.textContent = showValue(value(row, key2), field);
          }
        });
      }
      await storeInput();
      if (options.manager && !editing && !pendingCount()) await load3();
      else status2("\u041D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F \u0438 \u043F\u0440\u0430\u0432\u0430 \u043E\u0431\u043D\u043E\u0432\u043B\u0435\u043D\u044B. \u041D\u0435\u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043D\u043D\u044B\u0439 \u0432\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D.");
    }
    const stop = crm2.onChange?.((event) => {
      if (event.type === "queue") void sync().catch((e) => status2(e.message));
      else if (event.type === "live" && event.dirty) void refreshLive().catch((e) => status2(e.message));
    });
    scroll.onscroll = () => {
      prefs.left = scroll.scrollLeft;
      prefs.top = scroll.scrollTop;
      schedulePreferences();
    };
    await load3();
    if (options.manager && isCurrent()) {
      selectionChanged();
      if (options.distribution) {
        const { mountAllocation: mountAllocation2 } = await Promise.resolve().then(() => (init_crm_allocation(), crm_allocation_exports));
        allocationStop = await mountAllocation2($3("registry-allocation"), crm2, () => ({ ids: [...selectedRows], filters: filters(), all: allSelected, rows: data2.rows }), isCurrent, load3);
      }
    }
    if (!isCurrent()) stop?.();
    return () => {
      if (editing) editing.blur();
      resizeStop?.();
      clearTimeout(preferenceSave);
      void savePreferences().catch(() => {
      });
      disposed = true;
      generation++;
      stop?.();
      allocationStop?.();
    };
  }
  var escape, keyOf;
  var init_crm_registry = __esm({
    "hard-desk/ui/crm-registry.ts"() {
      "use strict";
      escape = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
      keyOf = (p) => `${p.row}:${p.col}`;
    }
  });

  // hard-desk/ui/crm-card-editor.ts
  async function editClientCard(dialog, card2, crm2, onSaved, isCurrent) {
    const key2 = "manager-card-input:" + card2.person.person_uuid;
    const ids = card2.contracts.map((row) => row.contract_uuid);
    const queue2 = await crm2.queue();
    if (!queue2.ok) throw Error(queue2.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0443 \u043F\u0440\u0430\u0432\u043E\u043A.");
    if ((queue2.data?.entries || []).some((entry) => entry.action === "registry-edit" && entry.state !== "accepted" && !entry.reviewed_at && entry.body.rows.some((r) => ids.includes(r.contract_uuid))))
      throw Error("\u0412 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0435 \u0435\u0441\u0442\u044C \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u044F\u0435\u043C\u044B\u0435 \u0438\u043B\u0438 \u043A\u043E\u043D\u0444\u043B\u0438\u043A\u0442\u043D\u044B\u0435 \u043F\u0440\u0430\u0432\u043A\u0438. \u041E\u0442\u043A\u0440\u043E\u0439\u0442\u0435 \u043E\u0447\u0435\u0440\u0435\u0434\u044C \u0434\u043B\u044F \u0441\u0432\u0435\u0440\u043A\u0438.");
    const response = await crm2.api("GET", "registry", void 0, { view: "all", ids: ids.join(","), limit: 100 });
    if (!isCurrent()) return;
    if (!response.ok) throw Error(response.text || "\u041A\u0430\u0440\u0442\u043E\u0447\u043A\u0430 \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u0430.");
    const data2 = response.data, rows = data2.rows;
    if (rows.length !== ids.length) throw Error("\u0421\u043E\u0441\u0442\u0430\u0432 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0438 \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u0441\u044F. \u041E\u0431\u043D\u043E\u0432\u0438\u0442\u0435 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0443.");
    const saved = (await crm2.draft(key2)).data || {};
    let changes = saved.changes || {}, intent = saved.intent;
    let selected2 = rows[0].contract_uuid, busy2 = false, saveChain = Promise.resolve(), saveError = "";
    if (!isCurrent()) return;
    dialog.classList.add("card-editor");
    dialog.innerHTML = `<form><h2>\u0420\u0435\u0434\u0430\u043A\u0442\u0438\u0440\u043E\u0432\u0430\u043D\u0438\u0435 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0438</h2><label>\u0414\u043E\u0433\u043E\u0432\u043E\u0440 <select id="card-edit-contract">${rows.map((r) => `<option value="${esc4(r.contract_uuid)}">${esc4(r.external_id)} \xB7 ${esc4(r.fields.partner || r.creditor_id)}</option>`).join("")}</select></label><div class="card-editor-fields"></div><p role="status" id="card-edit-status"></p><div class="actions"><button type="button" id="card-edit-close">\u0417\u0430\u043A\u0440\u044B\u0442\u044C</button><button type="submit" class="primary" id="card-edit-save">\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043F\u0440\u0430\u0432\u043A\u0438</button></div></form>`;
    const $3 = (id) => dialog.querySelector("#" + id);
    const status2 = (message) => {
      $3("card-edit-status").textContent = message;
    };
    const remember = () => {
      const snapshot = structuredClone({ changes, intent });
      saveChain = saveChain.then(async () => {
        const r = await crm2.draft(key2, snapshot);
        if (!r.ok) throw Error(r.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0432\u0432\u043E\u0434 \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435.");
        saveError = "";
      }).catch((e) => {
        saveError = e.message;
        status2(saveError);
      });
    };
    const count = () => Object.values(changes).reduce((sum, r) => sum + Object.keys(r).length, 0);
    const setValue = (row, f, raw) => {
      const existing = changes[row.contract_uuid]?.[f.key];
      const before = existing ? existing.before : row.fields[f.key];
      if (raw === String(before ?? "")) {
        delete changes[row.contract_uuid]?.[f.key];
        if (changes[row.contract_uuid] && !Object.keys(changes[row.contract_uuid]).length) delete changes[row.contract_uuid];
      } else {
        (changes[row.contract_uuid] ||= {})[f.key] = { raw, group: f.group, revision: existing?.revision ?? row.revisions[f.group] ?? 0, before };
      }
    };
    function paint() {
      const row = rows.find((r) => r.contract_uuid === selected2), groups2 = /* @__PURE__ */ new Map();
      for (const f of data2.columns) {
        if (!row.writable_fields.includes(f.key)) continue;
        const title = f.section || "\u0421\u0432\u0435\u0434\u0435\u043D\u0438\u044F";
        if (!groups2.has(title)) groups2.set(title, []);
        groups2.get(title).push(f);
      }
      dialog.querySelector(".card-editor-fields").innerHTML = Array.from(groups2).map(([title, fields], i) => `<details${i === 0 ? " open" : ""}><summary>${esc4(title)}</summary><div class="grid">${fields.map((f) => {
        const value = changes[selected2]?.[f.key]?.raw ?? row.fields[f.key] ?? "";
        const attrs = `data-card-field="${esc4(f.key)}" data-changed="${!!changes[selected2]?.[f.key]}"${intent ? " disabled" : ""}`;
        const options = f.options || [];
        const control = options.length ? `<select ${attrs}><option value="">\u041F\u0443\u0441\u0442\u043E</option>${(!options.includes(String(value)) && value ? [value, ...options] : options).map((v) => `<option value="${esc4(v)}"${v === value ? " selected" : ""}>${esc4(v)}</option>`).join("")}</select>` : /comment|address|tips/.test(f.key) ? `<textarea ${attrs}>${esc4(value)}</textarea>` : `<input ${attrs} type="${f.type === "date" ? "date" : "text"}"${f.type === "money" ? ' inputmode="decimal"' : ""} value="${esc4(value)}">`;
        return `<label>${esc4(f.title)}${control}</label>`;
      }).join("")}</div></details>`).join("");
      dialog.querySelectorAll("[data-card-field]").forEach((control) => control.oninput = () => {
        if (intent || busy2) return;
        const f = data2.columns.find((f2) => f2.key === control.dataset.cardField);
        for (const target of f.key === "client" ? rows : [row]) setValue(target, f, control.value);
        control.dataset.changed = String(!!changes[selected2]?.[f.key]);
        remember();
        status2(`\u0418\u0437\u043C\u0435\u043D\u0435\u043D\u043E \u043F\u043E\u043B\u0435\u0439: ${count()}. \u0412\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0435\u0442\u0441\u044F \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435.`);
      });
    }
    $3("card-edit-contract").onchange = () => {
      selected2 = $3("card-edit-contract").value;
      paint();
    };
    $3("card-edit-close").onclick = () => {
      if (!busy2) dialog.close();
    };
    dialog.addEventListener("close", () => dialog.classList.remove("card-editor"), { once: true });
    dialog.querySelector("form").onsubmit = async (e) => {
      e.preventDefault();
      if (busy2 || !isCurrent()) return;
      busy2 = true;
      dialog.querySelectorAll("input,textarea,select,button").forEach((e2) => e2.disabled = true);
      try {
        await saveChain;
        if (saveError) throw Error(saveError);
        if (!intent) {
          const patches = Object.entries(changes).filter(([, value]) => Object.keys(value).length).map(([cid, fields]) => {
            const row = rows.find((r) => r.contract_uuid === cid);
            if (!row) throw Error("\u0414\u043E\u0433\u043E\u0432\u043E\u0440 \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u0435\u043D. \u0412\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D.");
            const values = {}, clear = [], base_revisions = {};
            for (const [key3, change] of Object.entries(fields)) {
              if (!row.writable_fields.includes(key3)) throw Error("\u041F\u0440\u0430\u0432\u043E \u043F\u043E\u043B\u044F \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u043E\u0441\u044C. \u0412\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D.");
              const field = data2.columns.find((f) => f.key === key3);
              let value = change.raw;
              if (value === "") {
                value = null;
                clear.push(key3);
              } else if (field.type === "money") {
                value = value.replace(/[ \u00a0]/g, "").replace(",", ".");
                if (!/^-?\d{1,15}(\.\d{1,2})?$/.test(value)) throw Error(field.title + ": \u0443\u043A\u0430\u0436\u0438\u0442\u0435 \u0441\u0443\u043C\u043C\u0443 \u0441 \u0434\u0432\u0443\u043C\u044F \u0437\u043D\u0430\u043A\u0430\u043C\u0438 \u043F\u043E\u0441\u043B\u0435 \u0437\u0430\u043F\u044F\u0442\u043E\u0439.");
              }
              values[key3] = value;
              if (base_revisions[change.group] !== void 0 && base_revisions[change.group] !== change.revision) throw Error("\u0412\u0432\u043E\u0434 \u0441\u043E\u0434\u0435\u0440\u0436\u0438\u0442 \u0440\u0430\u0437\u043D\u044B\u0435 \u0432\u0435\u0440\u0441\u0438\u0438 \u043E\u0434\u043D\u043E\u0439 \u0433\u0440\u0443\u043F\u043F\u044B. \u041E\u0442\u043A\u0440\u043E\u0439\u0442\u0435 \u0441\u0432\u0435\u0440\u043A\u0443.");
              base_revisions[change.group] = change.revision;
            }
            return { contract_uuid: cid, values, clear, base_revisions };
          });
          if (!patches.length) {
            status2("\u041D\u0435\u0442 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u0439.");
            return;
          }
          intent = { schema_version: 1, operation_id: crypto.randomUUID(), rows: patches };
          remember();
          await saveChain;
          if (saveError) throw Error(saveError);
        }
        const result2 = await crm2.registryEdit(intent);
        if (!result2.ok) throw Error(result2.text || "\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u0435 \u043D\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u043E. \u0412\u0432\u043E\u0434 \u0438 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044F \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u044B.");
        const cleared = await crm2.draft(key2, null);
        if (!cleared.ok) throw Error("\u041F\u0440\u0430\u0432\u043A\u0438 \u0443\u0436\u0435 \u0432 \u043E\u0447\u0435\u0440\u0435\u0434\u0438. \u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043E\u0447\u0438\u0441\u0442\u0438\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A.");
        if (isCurrent()) {
          dialog.close();
          onSaved();
        }
      } catch (error) {
        status2(error.message);
        paint();
      } finally {
        busy2 = false;
        if (isCurrent()) {
          $3("card-edit-save").disabled = false;
          $3("card-edit-close").disabled = false;
          $3("card-edit-contract").disabled = false;
          paint();
        }
      }
    };
    paint();
    status2(intent ? "\u0415\u0441\u0442\u044C \u043D\u0435\u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u0430\u044F \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0430. \u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u0435 \u043F\u043E\u0432\u0442\u043E\u0440\u0438\u0442 \u0442\u0443 \u0436\u0435 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044E." : count() ? `\u0412\u043E\u0441\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D \u0432\u0432\u043E\u0434: ${count()} \u043F\u043E\u043B\u0435\u0439.` : "");
    dialog.showModal();
  }
  var esc4;
  var init_crm_card_editor = __esm({
    "hard-desk/ui/crm-card-editor.ts"() {
      "use strict";
      esc4 = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
    }
  });

  // hard-desk/ui/crm-manager-reports.ts
  async function mountManagerReports(host, crm2, valid) {
    let disposed = false, turn = 0, payload, sms = false, mode = "report", selected2 = "", day = "", busy2 = false, phoneTable = false;
    const current3 = () => !disposed && valid();
    host.innerHTML = `<div class="toolbar"><label>\u0414\u0430\u0442\u0430 <input id="report-day" type="date"></label><label>\u041E\u0442\u0441\u0435\u0447\u043A\u0430 <select id="report-cutoff"><option value="">\u0421\u0435\u0439\u0447\u0430\u0441</option></select></label><button id="report-refresh">\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C</button><button id="report-export">\u0412\u044B\u0433\u0440\u0443\u0437\u0438\u0442\u044C Excel</button></div><div class="tabs"><button data-report-tab="report" class="active">\u0422\u0440\u0451\u0445\u0447\u0430\u0441\u043E\u0432\u043E\u0439 \u043E\u0442\u0447\u0451\u0442</button><button data-report-tab="dynamics">\u0414\u0438\u043D\u0430\u043C\u0438\u043A\u0430</button><button data-report-tab="month">\u0418\u0442\u043E\u0433\u0438 \u043C\u0435\u0441\u044F\u0446\u0430</button></div><div class="toolbar" id="report-channel"><button data-channel="calls" class="active">\u0417\u0432\u043E\u043D\u043A\u0438</button><button data-channel="sms">SMS</button></div><p id="report-status" role="status"></p><div id="report-body"></div>`;
    const $3 = (id) => host.querySelector("#" + id);
    const status2 = (s) => {
      if (current3()) $3("report-status").textContent = s;
    };
    const protect = (fn2) => () => {
      void Promise.resolve().then(fn2).catch((e) => status2(e.message));
    };
    const request3 = async (action3, query = {}) => {
      const r = await crm2.api("GET", action3, void 0, query);
      if (!r.ok || r.stale) throw Error(r.text || "\u041D\u0443\u0436\u043D\u0430 \u0441\u0432\u044F\u0437\u044C \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C.");
      return r.data;
    };
    function table2(headers, rows) {
      return `<div class="manager-report-scroll"><table class="manager-report-table"><thead>${headers.join("")}</thead><tbody>${rows.join("")}</tbody></table></div>`;
    }
    function evidence(index) {
      const row = payload.report.sheets.find((s) => s.sms === sms).rows[index], dialog = document.createElement("dialog");
      const records = row.current_records || [];
      dialog.className = "manager-report-detail";
      dialog.innerHTML = `<h2>${esc5(row.employee)}</h2><p>\u0414\u043E\u0433\u043E\u0432\u043E\u0440\u044B, \u043D\u0430 \u043A\u043E\u0442\u043E\u0440\u044B\u0445 \u0440\u0430\u0441\u0441\u0447\u0438\u0442\u0430\u043D \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u0439 \u0441\u0440\u0435\u0437 \xB7 ${stamp(payload.report.as_of)}</p>${table2(["<tr>" + ["\u041A\u043B\u0438\u0435\u043D\u0442", "\u0414\u043E\u0433\u043E\u0432\u043E\u0440", "\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442", "\u0414\u0430\u0442\u0430 \u0441\u0432\u044F\u0437\u0438", "\u0414\u043E\u043B\u0433", "\u041E\u0431\u0435\u0449\u0430\u043D\u0438\u0435"].map((s) => `<th>${s}</th>`).join("") + "</tr>"], records.map((r) => `<tr><td>${esc5(r.client)}</td><td>${esc5(r.contract_id)}</td><td>${esc5(r.status)}</td><td>${esc5(r.date)}</td><td>${num(r.debt)}</td><td>${num(r.promise)}</td></tr>`))}<p>${records.length} \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432. \u041F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438 \u0443\u0447\u0438\u0442\u044B\u0432\u0430\u044E\u0442 \u0434\u0430\u0442\u0443 \u0441\u0432\u044F\u0437\u0438 \u0438 \u0432\u0438\u0434 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u0430.</p><button>\u0417\u0430\u043A\u0440\u044B\u0442\u044C</button>`;
      host.append(dialog);
      dialog.querySelector("button").onclick = () => {
        dialog.close();
        dialog.remove();
      };
      dialog.showModal();
    }
    function paint() {
      if (!payload || mode === "month") return;
      const report = payload.report, rows = report.sheets.find((s) => s.sms === sms).rows;
      $3("report-channel").hidden = false;
      if (mode === "report") {
        const head = '<tr><th rowspan="2">\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A</th>' + metrics.map((m) => `<th colspan="3">${m}</th>`).join("") + "</tr>";
        const sub = "<tr>" + metrics.map(() => "<th>\u0412\u0447\u0435\u0440\u0430</th><th>\u0421\u0440\u0435\u0437</th><th>%</th>").join("") + "</tr>";
        const fullTable = table2([head, sub], rows.map((r, i) => `<tr><th><button class="link" data-report-person="${i}">${esc5(r.employee)}</button></th>${r.current.map((v, j) => `<td>${num(r.yesterday[j])}</td><td>${num(v)}</td><td>${r.yesterday[j] == null || v == null || Number(r.yesterday[j]) === 0 ? "\u2014" : num(Number(v) / Number(r.yesterday[j]) * 100) + "%"}</td>`).join("")}</tr>`));
        $3("report-body").innerHTML = crm2.platform === "mobile" && !phoneTable ? `<div class="mobile-report-cards">${rows.map((r, i) => `<section><button class="link mobile-report-person" data-report-person="${i}">${esc5(r.employee)} <span>\u2197</span></button><div class="mobile-report-stats">${[1, 3, 4, 9].map((j) => `<div><span>${metrics[j]}</span><strong>${num(r.current[j])}</strong><small>\u0412\u0447\u0435\u0440\u0430 ${num(r.yesterday[j])}</small></div>`).join("")}</div><details><summary>\u0412\u0441\u0435 \u043F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438</summary>${metrics.map((title, j) => `<div class="mobile-report-detail"><span>${title}</span><strong>${num(r.current[j])}</strong><small>\u0412\u0447\u0435\u0440\u0430 ${num(r.yesterday[j])}</small></div>`).join("")}</details></section>`).join("")}</div><button id="mobile-report-table" class="link">\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0442\u0430\u0431\u043B\u0438\u0446\u0443 \u043E\u0442\u0447\u0451\u0442\u0430</button>` : fullTable + (crm2.platform === "mobile" ? '<button id="mobile-report-table" class="link">\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u044C \u0441\u0432\u043E\u0434\u043A\u0443</button>' : "");
        host.querySelector("#mobile-report-table")?.addEventListener("click", () => {
          phoneTable = !phoneTable;
          paint();
        });
        host.querySelectorAll("[data-report-person]").forEach((b) => b.onclick = () => evidence(Number(b.dataset.reportPerson)));
        status2(`\u041D\u0430 ${stamp(report.as_of)} \xB7 ${report.currency || "\u0412\u0430\u043B\u044E\u0442\u0430 \u043D\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0430"}${report.yesterday_incomplete_contracts ? " \xB7 \u0412\u0447\u0435\u0440\u0430: \u043D\u0435\u043F\u043E\u043B\u043D\u0430\u044F \u0438\u0441\u0442\u043E\u0440\u0438\u044F \u043F\u043E " + report.yesterday_incomplete_contracts + " \u041A\u0414" : ""}`);
      } else {
        const totals = (snapshot) => snapshot.sheets.find((s) => s.sms === sms).rows.reduce((acc, r) => acc.map((v, j) => v + Number(r.current[j] || 0)), Array(10).fill(0));
        let previous;
        $3("report-body").innerHTML = table2(["<tr><th>\u0421\u043D\u0438\u043C\u043E\u043A</th>" + metrics.map((m) => `<th>${m}</th>`).join("") + "</tr>"], payload.snapshots.map((s) => {
          const values = totals(s), row = `<tr><th><button class="link" data-snapshot="${esc5(s.snapshot_id)}">${stamp(s.captured_at)}</button></th>${values.map((v, i) => `<td>${num(v)}${previous ? `<small class="report-delta">${v - previous[i] >= 0 ? "+" : ""}${num(v - previous[i])}</small>` : ""}</td>`).join("")}</tr>`;
          previous = values;
          return row;
        }));
        host.querySelectorAll("[data-snapshot]").forEach((b) => b.onclick = protect(async () => {
          selected2 = b.dataset.snapshot;
          mode = "report";
          markTabs();
          await load3();
        }));
        status2(payload.snapshots.length ? `\u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0435 \u0441\u043D\u0438\u043C\u043A\u0438 \u0437\u0430 ${payload.day}. \u041F\u043E\u0434 \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0435\u043C \u2014 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u0435 \u043A \u043F\u0440\u0435\u0434\u044B\u0434\u0443\u0449\u0435\u043C\u0443 \u0441\u043D\u0438\u043C\u043A\u0443.` : "\u0417\u0430 \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u0443\u044E \u0434\u0430\u0442\u0443 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0445 \u0441\u043D\u0438\u043C\u043A\u043E\u0432 \u043D\u0435\u0442.");
      }
    }
    function markTabs() {
      host.querySelectorAll("[data-report-tab]").forEach((b) => b.classList.toggle("active", b.dataset.reportTab === mode));
    }
    async function monthly() {
      $3("report-channel").hidden = true;
      const month = day.slice(0, 7) || (/* @__PURE__ */ new Date()).toISOString().slice(0, 7), data2 = await request3("manager-statistics", { month });
      if (!current3() || mode !== "month") return;
      $3("report-body").innerHTML = `<h2>${esc5(month)}</h2>${table2(["<tr><th>\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A</th><th>\u0417\u0432\u043E\u043D\u043A\u0438</th><th>\u0412\u044B\u0435\u0437\u0434\u044B</th><th>\u041E\u0431\u0440\u0430\u0431\u043E\u0442\u0430\u043D\u043E \u041A\u0414</th><th>\u041A\u0414 \u0441\u0435\u0439\u0447\u0430\u0441</th></tr>"], data2.staff.map((p) => `<tr><th>${esc5(p.name)}</th><td><button class="link" data-fact-employee="${esc5(p.employee_uuid)}" data-fact-kind="call">${p.facts.call}</button></td><td><button class="link" data-fact-employee="${esc5(p.employee_uuid)}" data-fact-kind="visit">${p.facts.visit}</button></td><td>${p.facts.processed_contracts}</td><td>${p.contracts}</td></tr>`))}<h2>\u0422\u0435\u043A\u0443\u0449\u0438\u0439 \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044C</h2><p>${data2.people} \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432 \xB7 ${data2.contracts} \u041A\u0414 \xB7 \u043D\u0435 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u043E ${data2.unassigned_contracts} \u041A\u0414</p>${table2(["<tr><th>\u0412\u0430\u043B\u044E\u0442\u0430</th><th>\u0414\u043E\u043B\u0433</th><th>\u041F\u043E\u0441\u0442\u0443\u043F\u043B\u0435\u043D\u0438\u044F \u0437\u0430 \u043C\u0435\u0441\u044F\u0446</th></tr>"], [.../* @__PURE__ */ new Set([...Object.keys(data2.debt), ...Object.keys(data2.payments.totals)])].map((k) => `<tr><th>${esc5(k)}</th><td>${num(data2.debt[k])}</td><td>${num(data2.payments.totals[k])}</td></tr>`))}<p>\u0411\u0435\u0437 \u0441\u0443\u043C\u043C\u044B \u0434\u043E\u043B\u0433\u0430: ${data2.unknown_debt} \u041A\u0414; \u0431\u0435\u0437 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u044B\u0445 \u043F\u043E\u0441\u0442\u0443\u043F\u043B\u0435\u043D\u0438\u0439: ${data2.payments.missing_contracts} \u041A\u0414.</p>`;
      host.querySelectorAll("[data-fact-employee]").forEach((b) => b.onclick = protect(async () => {
        const panel = document.createElement("dialog");
        panel.className = "manager-report-detail";
        host.append(panel);
        let offset = 0;
        const show = async () => {
          const result2 = await request3("manager-statistics", { month, employee_uuid: b.dataset.factEmployee, kind: b.dataset.factKind, offset });
          if (!current3()) return;
          panel.innerHTML = `<h2>${esc5(result2.employee)} \xB7 ${result2.kind === "call" ? "\u0417\u0432\u043E\u043D\u043A\u0438" : "\u0412\u044B\u0435\u0437\u0434\u044B"}</h2>${table2(["<tr><th>\u0412\u0440\u0435\u043C\u044F</th><th>\u041A\u043B\u0438\u0435\u043D\u0442\u044B / \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u044B</th></tr>"], result2.rows.map((r) => `<tr><td>${r.time_quality === "legacy_day" ? esc5(r.day) : stamp(r.at)}</td><td>${r.contracts.map((c) => esc5(c.name) + (c.external_id ? " \xB7 " + esc5(c.external_id) : "")).join("<br>")}</td></tr>`))}<p>${result2.total ? offset + 1 : 0}\u2013${offset + result2.rows.length} \u0438\u0437 ${result2.total}</p><div class="actions"><button data-page-prev${offset ? "" : " disabled"}>\u041D\u0430\u0437\u0430\u0434</button><button data-page-next${result2.has_more ? "" : " disabled"}>\u0414\u0430\u043B\u0435\u0435</button><button data-detail-close>\u0417\u0430\u043A\u0440\u044B\u0442\u044C</button></div>`;
          panel.querySelector("[data-page-prev]").onclick = protect(async () => {
            offset = Math.max(0, offset - 50);
            await show();
          });
          panel.querySelector("[data-page-next]").onclick = protect(async () => {
            offset += 50;
            await show();
          });
          panel.querySelector("[data-detail-close]").onclick = () => {
            panel.close();
            panel.remove();
          };
        };
        await show();
        if (current3()) panel.showModal();
      }));
      status2(`\u041D\u0430 ${stamp(data2.as_of)}. \u0417\u0432\u043E\u043D\u043A\u0438 \u0438 \u0432\u044B\u0435\u0437\u0434\u044B \u2014 \u043F\u043E \u0438\u0441\u0445\u043E\u0434\u043D\u044B\u043C \u0430\u0432\u0442\u043E\u0440\u0430\u043C \u0437\u0430 \u043C\u0435\u0441\u044F\u0446. \u0414\u043E\u043B\u0433 \u0438 \u043F\u043E\u0441\u0442\u0443\u043F\u043B\u0435\u043D\u0438\u044F \u2014 \u043F\u043E \u0442\u0435\u043A\u0443\u0449\u0435\u043C\u0443 \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044E.`);
    }
    async function load3() {
      const n2 = ++turn;
      if (mode === "month") {
        await monthly();
        return;
      }
      const data2 = await request3("manager-report", { ...selected2 ? { snapshot_id: selected2 } : {}, ...day ? { day } : {} });
      if (!current3() || n2 !== turn) return;
      payload = data2;
      day = data2.day;
      $3("report-day").value = day;
      $3("report-cutoff").innerHTML = '<option value="">\u0421\u0435\u0439\u0447\u0430\u0441</option>' + data2.snapshots.map((s) => `<option value="${esc5(s.snapshot_id)}">${stamp(s.captured_at)}</option>`).join("");
      $3("report-cutoff").value = selected2;
      paint();
    }
    $3("report-refresh").onclick = protect(load3);
    $3("report-day").onchange = protect(async () => {
      day = $3("report-day").value;
      selected2 = "";
      mode = "dynamics";
      markTabs();
      await load3();
    });
    $3("report-cutoff").onchange = protect(async () => {
      selected2 = $3("report-cutoff").value;
      mode = "report";
      markTabs();
      await load3();
    });
    host.querySelectorAll("[data-report-tab]").forEach((b) => b.onclick = protect(async () => {
      mode = b.dataset.reportTab;
      markTabs();
      $3("report-export").disabled = false;
      await load3();
    }));
    host.querySelectorAll("[data-channel]").forEach((b) => b.onclick = () => {
      sms = b.dataset.channel === "sms";
      host.querySelectorAll("[data-channel]").forEach((t) => t.classList.toggle("active", t === b));
      paint();
    });
    $3("report-export").onclick = protect(async () => {
      if (busy2) return;
      busy2 = true;
      $3("report-export").disabled = true;
      try {
        const r = await crm2.workbook("manager-report", { snapshot_id: selected2 || void 0, mode, day, month: day.slice(0, 7) });
        if (!r.ok && !r.cancelled) throw Error(r.text || "\u0412\u044B\u0433\u0440\u0443\u0437\u043A\u0430 \u043D\u0435 \u0432\u044B\u043F\u043E\u043B\u043D\u0435\u043D\u0430.");
        if (r.ok) status2("Excel \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D \u0438 \u043F\u0440\u043E\u0432\u0435\u0440\u0435\u043D.");
      } finally {
        busy2 = false;
        if (current3()) $3("report-export").disabled = false;
      }
    });
    const stop = crm2.onChange?.((e) => {
      if (e.type === "live" && e.dirty && !selected2 && !busy2 && !host.querySelector("dialog[open]")) void load3().catch((error) => status2(error.message));
    });
    await load3();
    return () => {
      disposed = true;
      turn++;
      stop?.();
    };
  }
  var esc5, stamp, num, metrics;
  var init_crm_manager_reports = __esm({
    "hard-desk/ui/crm-manager-reports.ts"() {
      "use strict";
      esc5 = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
      stamp = (v) => new Date(v).toLocaleString("ru-RU");
      num = (v) => v === null || v === void 0 ? "\u2014" : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 2 });
      metrics = ["\u0412\u0441\u0442\u0440\u0435\u0447\u0438", "\u0417\u0432\u043E\u043D\u043A\u0438 \xB7 \u043A\u043B\u0438\u0435\u043D\u0442\u044B", "\u041E\u0431\u0435\u0449\u0430\u043D\u0438\u044F \xB7 \u041A\u0414", "\u041E\u0431\u0435\u0449\u0430\u043D\u0438\u044F \xB7 \u0441\u0443\u043C\u043C\u0430", "\u0412 \u0440\u0430\u0431\u043E\u0442\u0435 \xB7 \u041A\u0414", "\u0412 \u0440\u0430\u0431\u043E\u0442\u0435 \xB7 \u0441\u0443\u043C\u043C\u0430", "\u0422\u0440\u0435\u0442\u044C\u0438 \u043B\u0438\u0446\u0430 \xB7 \u041A\u0414", "\u0422\u0440\u0435\u0442\u044C\u0438 \u043B\u0438\u0446\u0430 \xB7 \u0441\u0443\u043C\u043C\u0430", "\u041E\u043F\u043B\u0430\u0447\u0435\u043D\u043E \xB7 \u041A\u0414", "\u041E\u043F\u043B\u0430\u0447\u0435\u043D\u043E \xB7 \u0441\u0443\u043C\u043C\u0430"];
    }
  });

  // hard-desk/ui/crm-manager-team.ts
  async function mountManagerTeam(host, crm2, context2, valid, openPortfolio) {
    let disposed = false, generation = 0, rows = [], selected2 = "", query = "", busy2 = false, profileOpen = false;
    const current3 = () => !disposed && valid();
    host.innerHTML = `<div class="toolbar"><input id="team-search" type="search" placeholder="\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A, \u043B\u043E\u0433\u0438\u043D, \u0440\u043E\u043B\u044C"><button id="team-refresh">\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C</button><span id="team-count"></span></div><p id="team-status" role="status"></p><div class="manager-team-layout"><div id="team-list"></div><section id="team-profile"></section></div>`;
    const $3 = (id) => host.querySelector("#" + id);
    const status2 = (s) => {
      if (current3()) $3("team-status").textContent = s;
    };
    const protect = (fn2) => () => {
      void Promise.resolve().then(fn2).catch((e) => status2(e.message));
    };
    const request3 = async (method, resource, payload) => {
      const r = await crm2.api(method, "manager-access", method === "POST" ? { resource, payload } : void 0, method === "GET" ? { resource } : void 0);
      if (!r.ok || r.stale || r.data?.ok === false) throw Object.assign(Error(r.text || r.data?.error || "\u0421\u043B\u0443\u0436\u0431\u0430 \u0432\u0445\u043E\u0434\u0430 \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u0430."), { status: r.status });
      return r.data;
    };
    function paint() {
      const filtered = rows.filter((r2) => [r2.employee, r2.account?.username, roles[r2.role], r2.office].join(" ").toLocaleLowerCase().includes(query));
      $3("team-count").textContent = `${filtered.length} \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432`;
      $3("team-list").innerHTML = `<table class="team-table"><thead><tr><th>\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A</th><th>\u0420\u043E\u043B\u044C</th><th>\u0412\u0445\u043E\u0434</th></tr></thead><tbody>${filtered.map((r2) => `<tr class="${r2.key === selected2 ? "active" : ""}"><td><button class="link" data-team-person="${esc6(r2.key)}">${esc6(r2.employee)}</button><small>${esc6(r2.account?.username || "\u0410\u043A\u043A\u0430\u0443\u043D\u0442 \u043D\u0435 \u0441\u043E\u0437\u0434\u0430\u043D")}</small></td><td>${esc6(roles[r2.role] || r2.role || "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A")}</td><td>${esc6(r2.account?.password_review_pending ? "\u0421\u043C\u0435\u043D\u0430 \u043F\u0430\u0440\u043E\u043B\u044F" : states[r2.account?.status] || states[r2.login_status] || "\u041D\u0435 \u0441\u043E\u0437\u0434\u0430\u043D")}</td></tr>`).join("")}</tbody></table>`;
      host.querySelectorAll("[data-team-person]").forEach((b) => b.onclick = () => {
        selected2 = b.dataset.teamPerson;
        profileOpen = true;
        paint();
      });
      const r = rows.find((r2) => r2.key === selected2);
      if (!r) {
        $3("team-profile").innerHTML = "<p>\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430.</p>";
        return;
      }
      const a = r.account, devices = a?.devices || [];
      $3("team-profile").innerHTML = `<h2>${esc6(r.employee)}</h2><dl class="team-facts"><dt>\u0420\u043E\u043B\u044C</dt><dd>${esc6(roles[r.role] || r.role || "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A")}</dd><dt>\u041E\u0444\u0438\u0441</dt><dd>${esc6(r.office || "\u2014")}</dd><dt>\u041B\u043E\u0433\u0438\u043D</dt><dd>${esc6(a?.username || "\u0410\u043A\u043A\u0430\u0443\u043D\u0442 \u043D\u0435 \u0441\u043E\u0437\u0434\u0430\u043D")}</dd><dt>\u0412\u0445\u043E\u0434</dt><dd>${esc6(a?.password_review_pending ? a.must_change_password ? "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A \u0434\u043E\u043B\u0436\u0435\u043D \u0441\u043C\u0435\u043D\u0438\u0442\u044C \u0432\u0440\u0435\u043C\u0435\u043D\u043D\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C" : "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u0435 \u043B\u0438\u0447\u043D\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C \u043D\u0430 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0435" : states[a?.status] || states[r.login_status] || "\u041D\u0435 \u0441\u043E\u0437\u0434\u0430\u043D")}</dd><dt>\u0420\u0430\u0431\u043E\u0442\u044B</dt><dd>${(r.crm?.work_kinds || []).map((w) => w === "call" ? "\u0417\u0432\u043E\u043D\u043A\u0438" : "\u0412\u044B\u0435\u0437\u0434\u044B").join(", ") || "\u2014"}</dd><dt>\u041F\u043E\u0440\u0442\u0444\u0435\u043B\u044C</dt><dd>${r.crm ? `${r.crm.people} \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432 \xB7 ${r.crm.contracts} \u041A\u0414` : "\u2014"}</dd></dl><div class="actions">${r.crm ? '<button id="team-portfolio">\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044C</button>' : ""}${r.can_reset ? '<button id="team-password">\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C \u043F\u0430\u0440\u043E\u043B\u044C</button>' : ""}</div><h3>\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430</h3>${devices.length ? devices.map((d, i) => `<div class="team-device"><strong>${d.kind === "desktop" ? "\u041A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440" : "\u0422\u0435\u043B\u0435\u0444\u043E\u043D"} \xB7 ${esc6(d.label || "\u0411\u0435\u0437 \u043D\u0430\u0437\u0432\u0430\u043D\u0438\u044F")}</strong><p>${esc6(states[d.status] || d.status)} \xB7 \u041F\u043E\u0441\u043B\u0435\u0434\u043D\u0438\u0439 \u0432\u0445\u043E\u0434 ${stamp2(d.last_seen || d.at)}</p>${d.client_version ? `<p>\u0412\u0435\u0440\u0441\u0438\u044F ${esc6(d.client_version)}</p>` : ""}<div class="actions">${r.can_approve && d.status === "pending" ? `<button data-team-approve="${i}">\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E</button>` : ""}${r.can_revoke ? `<button data-team-revoke="${i}">\u041E\u0442\u043E\u0437\u0432\u0430\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F</button>` : ""}</div></div>`).join("") : "<p>\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430 \u043D\u0435 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u044B.</p>"}${r.can_remove ? `<details class="team-roster-actions"><summary>\u0421\u043E\u0441\u0442\u0430\u0432 \u043A\u043E\u043C\u0430\u043D\u0434\u044B</summary><button id="team-board">${r.on_board ? "\u0423\u0431\u0440\u0430\u0442\u044C \u0438\u0437 \u0441\u043F\u0438\u0441\u043A\u0430" : "\u0412\u0435\u0440\u043D\u0443\u0442\u044C \u0432 \u0441\u043F\u0438\u0441\u043E\u043A"}</button></details>` : ""}`;
      if (crm2.platform === "mobile") {
        $3("team-list").hidden = profileOpen;
        $3("team-profile").hidden = !profileOpen;
        if (profileOpen) {
          const back = document.createElement("button");
          back.className = "link mobile-team-back";
          back.textContent = "\u2190 \u041A\u043E\u043C\u0430\u043D\u0434\u0430";
          back.onclick = () => {
            profileOpen = false;
            paint();
          };
          $3("team-profile").prepend(back);
        }
      }
      if ($3("team-portfolio")) $3("team-portfolio").onclick = () => openPortfolio(r.crm.employee_uuid);
      if ($3("team-password")) $3("team-password").onclick = protect(() => password(r));
      if ($3("team-board")) $3("team-board").onclick = protect(() => confirmAction(r.on_board ? "\u0423\u0431\u0440\u0430\u0442\u044C \u0438\u0437 \u0441\u043F\u0438\u0441\u043A\u0430?" : "\u0412\u0435\u0440\u043D\u0443\u0442\u044C \u0432 \u0441\u043F\u0438\u0441\u043E\u043A?", r.employee, () => request3("POST", "board", { action: r.on_board ? "hide" : "add", employee: r.employee, operation_id: crypto.randomUUID() })));
      host.querySelectorAll("[data-team-approve]").forEach((b) => b.onclick = protect(() => {
        const d = devices[Number(b.dataset.teamApprove)];
        return confirmAction("\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E", `${r.employee} \xB7 ${d.label || "\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E"}`, () => request3("POST", "approve", { device_id: d.id, observed_pc: d.kind === "desktop" }), d.kind === "desktop" ? "\u042F \u043B\u0438\u0447\u043D\u043E \u0441\u0432\u0435\u0440\u0438\u043B \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430 \u0438 \u0435\u0433\u043E \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440" : "\u042F \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u043B \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430 \u0438 \u0435\u0433\u043E \u0442\u0435\u043B\u0435\u0444\u043E\u043D");
      }));
      host.querySelectorAll("[data-team-revoke]").forEach((b) => b.onclick = protect(() => {
        const d = devices[Number(b.dataset.teamRevoke)];
        return confirmAction("\u041E\u0442\u043E\u0437\u0432\u0430\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430", `${r.employee} \xB7 ${d.label || "\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E"}. \u0422\u0435\u043A\u0443\u0449\u0438\u0439 \u0432\u0445\u043E\u0434 \u0431\u0443\u0434\u0435\u0442 \u0437\u0430\u043A\u0440\u044B\u0442. \u0414\u043B\u044F \u043D\u043E\u0432\u043E\u0433\u043E \u0432\u0445\u043E\u0434\u0430 \u043F\u043E\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044F \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435.`, () => request3("POST", "revoke", { device_id: d.id }));
      }));
    }
    async function confirmAction(title, text2, action3, check) {
      const dialog = document.createElement("dialog");
      dialog.innerHTML = `<form><h2>${esc6(title)}</h2><p>${esc6(text2)}</p>${check ? `<label class="check"><input type="checkbox" required>${esc6(check)}</label>` : ""}<p role="status"></p><div class="actions"><button type="button">\u041E\u0442\u043C\u0435\u043D\u0430</button><button type="submit">\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C</button></div></form>`;
      host.append(dialog);
      dialog.querySelector("button").onclick = () => dialog.close();
      dialog.onclose = () => dialog.remove();
      dialog.querySelector("form").onsubmit = async (e) => {
        e.preventDefault();
        const button2 = dialog.querySelector('[type="submit"]');
        button2.disabled = true;
        try {
          await action3();
          dialog.close();
          await load3();
        } catch (e2) {
          dialog.querySelector('[role="status"]').textContent = e2.message;
          button2.disabled = false;
        }
      };
      dialog.showModal();
    }
    async function password(row) {
      const key2 = "manager-password-intent:" + row.account.username;
      let intent = (await crm2.draft(key2)).data;
      const dialog = document.createElement("dialog");
      dialog.innerHTML = `<form><h2>\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C \u043F\u0430\u0440\u043E\u043B\u044C</h2><p>${esc6(row.employee)} \xB7 ${esc6(row.account.username)}</p><p>\u0412\u0445\u043E\u0434\u044B \u0431\u0443\u0434\u0443\u0442 \u0437\u0430\u043A\u0440\u044B\u0442\u044B. \u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A \u0441\u043C\u0435\u043D\u0438\u0442 \u0432\u0440\u0435\u043C\u0435\u043D\u043D\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C \u043D\u0430 \u043B\u0438\u0447\u043D\u044B\u0439, \u0437\u0430\u0442\u0435\u043C \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u0435 \u0435\u0433\u043E \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E.</p><label>\u0412\u0440\u0435\u043C\u0435\u043D\u043D\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C <input name="password" type="password" autocomplete="new-password" minlength="8" required></label><label>\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u0435 \u043F\u0430\u0440\u043E\u043B\u044C <input name="repeat" type="password" autocomplete="new-password" minlength="8" required></label><p role="status"></p><div class="actions"><button type="button">\u0417\u0430\u043A\u0440\u044B\u0442\u044C</button><button type="submit">${intent ? "\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0438\u0441\u0445\u043E\u0434 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438" : "\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C \u043F\u0430\u0440\u043E\u043B\u044C"}</button></div></form>`;
      host.append(dialog);
      const inputs = [...dialog.querySelectorAll("input")];
      if (intent) inputs.forEach((i) => {
        i.value = intent.password;
        i.disabled = true;
      });
      dialog.querySelector("button").onclick = () => dialog.close();
      dialog.onclose = () => dialog.remove();
      dialog.querySelector("form").onsubmit = async (e) => {
        e.preventDefault();
        const button2 = dialog.querySelector('[type="submit"]');
        button2.disabled = true;
        try {
          if (!intent) {
            if (inputs[0].value !== inputs[1].value) throw Error("\u041F\u0430\u0440\u043E\u043B\u0438 \u043D\u0435 \u0441\u043E\u0432\u043F\u0430\u0434\u0430\u044E\u0442.");
            intent = { username: row.account.username, password: inputs[0].value, operation_id: crypto.randomUUID() };
            const saved = await crm2.draft(key2, intent);
            if (!saved.ok) {
              intent = null;
              throw Error(crm2.platform === "mobile" ? "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0437\u0430\u0449\u0438\u0442\u0438\u0442\u044C \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044E \u043D\u0430 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435." : "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0437\u0430\u0449\u0438\u0442\u0438\u0442\u044C \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044E \u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435.");
            }
          }
          inputs.forEach((i) => i.disabled = true);
          await request3("POST", "reset", intent);
          await crm2.draft(key2, null);
          intent = null;
          dialog.close();
          await load3();
          status2("\u0412\u0440\u0435\u043C\u0435\u043D\u043D\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C \u0443\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D. \u041F\u0435\u0440\u0435\u0434\u0430\u0439\u0442\u0435 \u0435\u0433\u043E \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0443.");
        } catch (e2) {
          if ([400, 409, 422].includes(e2.status)) {
            await crm2.draft(key2, null);
            intent = null;
            inputs.forEach((i) => i.disabled = false);
          }
          dialog.querySelector('[role="status"]').textContent = e2.message;
          button2.textContent = intent ? "\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0438\u0441\u0445\u043E\u0434 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438" : "\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C \u043F\u0430\u0440\u043E\u043B\u044C";
          button2.disabled = false;
        }
      };
      dialog.showModal();
    }
    async function load3() {
      if (busy2) return;
      busy2 = true;
      const n2 = ++generation;
      try {
        const [teamResult, staffResult] = await Promise.allSettled([request3("GET", "team"), context2.distribution?.enabled ? crm2.api("GET", "assignment-staff") : Promise.resolve({ ok: true, data: { staff: context2.staff || [] } })]);
        if (!current3() || n2 !== generation) return;
        const staff2 = staffResult.status === "fulfilled" && staffResult.value.ok ? staffResult.value.data.staff : [];
        const team = teamResult.status === "fulfilled" ? teamResult.value.employees : [];
        const used = /* @__PURE__ */ new Set();
        rows = team.map((r) => {
          const matches = staff2.filter((s) => s.actor_uids?.includes(r.account?.actor_uid));
          const person = matches.length === 1 ? matches[0] : void 0;
          if (person) used.add(person.employee_uuid);
          return { ...r, key: "primary:" + r.employee_id, crm: person, role: person?.role || r.account?.role || "employee" };
        });
        rows.push(...staff2.filter((s) => !used.has(s.employee_uuid)).map((s) => ({ key: "crm:" + s.employee_uuid, employee: s.name, role: s.role, crm: s, account: null })));
        rows.sort((a, b) => a.employee.localeCompare(b.employee, "ru"));
        if (!rows.some((r) => r.key === selected2)) selected2 = rows[0]?.key || "";
        paint();
        status2(teamResult.status === "rejected" ? teamResult.reason.message : "\u041E\u0431\u043D\u043E\u0432\u043B\u0435\u043D\u043E " + (/* @__PURE__ */ new Date()).toLocaleTimeString("ru-RU"));
      } finally {
        busy2 = false;
      }
    }
    $3("team-search").oninput = () => {
      query = $3("team-search").value.toLocaleLowerCase();
      paint();
    };
    $3("team-refresh").onclick = protect(load3);
    const focus = () => {
      if (current3() && !host.querySelector("dialog[open]")) void load3().catch((e) => status2(e.message));
    };
    window.addEventListener("focus", focus);
    const stop = crm2.onChange?.((e) => {
      if (e.type === "live" && e.dirty) focus();
    });
    await load3();
    return () => {
      disposed = true;
      generation++;
      stop?.();
      window.removeEventListener("focus", focus);
    };
  }
  var esc6, roles, states, stamp2;
  var init_crm_manager_team = __esm({
    "hard-desk/ui/crm-manager-team.ts"() {
      "use strict";
      esc6 = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
      roles = { employee: "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A", agent: "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A", manager: "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C", observer: "\u041D\u0430\u0431\u043B\u044E\u0434\u0430\u0442\u0435\u043B\u044C", courier: "\u041A\u0443\u0440\u044C\u0435\u0440", analyst: "\u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A", owner: "\u0412\u043B\u0430\u0434\u0435\u043B\u0435\u0446" };
      states = { active: "\u0414\u043E\u0441\u0442\u0443\u043F \u043E\u0442\u043A\u0440\u044B\u0442", pending: "\u0416\u0434\u0451\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F", awaiting: "\u0416\u0434\u0451\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F", disabled: "\u041E\u0442\u043A\u043B\u044E\u0447\u0451\u043D", revoked: "\u041E\u0442\u043E\u0437\u0432\u0430\u043D", none: "\u041D\u0435 \u0441\u043E\u0437\u0434\u0430\u043D", ambiguous: "\u0422\u0440\u0435\u0431\u0443\u0435\u0442 \u0441\u0432\u0435\u0440\u043A\u0438" };
      stamp2 = (v) => v ? new Date(v).toLocaleString("ru-RU") : "\u2014";
    }
  });

  // hard-desk/ui/kit/api.ts
  function employeeQuery(path, query) {
    if (!employeeProfile || !path.startsWith("/api/") || path.startsWith("/api/local/")) return query;
    const explicit = query?.mode || query?.profile;
    return { ...query, mode: explicit === "hard" || explicit === "soft" ? explicit : employeeProfile };
  }
  function setEmbeddedBridge(overrides) {
    const previous = Object.fromEntries(Object.keys(overrides).map((key2) => [key2, hard[key2]]));
    Object.assign(hard, overrides);
    return () => Object.assign(hard, previous);
  }
  function tell(level, text2) {
    for (const fn2 of listeners2) fn2({ level, text: text2 });
  }
  async function api2(path, query, method = "GET", body) {
    if (path.startsWith("/api/local/")) return hard.api(method, path, query, body);
    busy++;
    tell("busy", "\u041E\u0431\u043D\u043E\u0432\u043B\u044F\u044E\u2026");
    let r;
    try {
      r = await hard.api(method, path, query, body);
    } finally {
      busy--;
    }
    if (r.kind === "offline" || r.kind === "cert") {
      if (!lostAt) lostAt = (/* @__PURE__ */ new Date()).toTimeString().slice(0, 5);
      tell("off", r.kind === "cert" ? "\u0421\u0435\u0442\u044C \u043F\u043E\u0434\u043C\u0435\u043D\u044F\u0435\u0442 \u0441\u043E\u0435\u0434\u0438\u043D\u0435\u043D\u0438\u0435" : `\u041D\u0435\u0442 \u0441\u0432\u044F\u0437\u0438 \u0441 ${lostAt}`);
    } else if (r.kind === "revoked") {
      tell("off", "\u0414\u043E\u0441\u0442\u0443\u043F \u043E\u0442\u043E\u0437\u0432\u0430\u043D");
    } else {
      lostAt = "";
      if (!busy) tell("live", "\u041D\u0430 \u0441\u0432\u044F\u0437\u0438 \xB7 " + (/* @__PURE__ */ new Date()).toTimeString().slice(0, 5));
    }
    return r;
  }
  var bridge2, employeeProfile, hard, listeners2, lostAt, busy;
  var init_api = __esm({
    "hard-desk/ui/kit/api.ts"() {
      "use strict";
      bridge2 = window.hard;
      hard = {
        ...bridge2,
        api: (method, path, query, body) => bridge2.api(method, path, employeeQuery(path, query), body),
        image: (path, query) => bridge2.image(path, employeeQuery(path, query)),
        upload: (path, query, file, ext) => bridge2.upload(path, employeeQuery(path, query) || {}, file, ext)
      };
      listeners2 = /* @__PURE__ */ new Set();
      lostAt = "";
      busy = 0;
    }
  });

  // hard-desk/ui/kit/scroller.ts
  var FEELS;
  var init_scroller = __esm({
    "hard-desk/ui/kit/scroller.ts"() {
      "use strict";
      FEELS = {
        // Возврат после отпускания — в ВИДИМЫХ пикселях и экспонентой с первого кадра
        // (Егор 28.09: «очень долго отпускается»): 1/springW — постоянная времени, 95% пути
        // за три таких. Раньше пружина шла в «пальцевом» пространстве резины и трогалась с
        // места медленно — сжатая кривая «висела», потом доезжала рывком.
        brisk: { wheelTau: 95, padTau: 18, followTau: 110, flingTau: 325, maxFling: 7, springW: 4 / 320, rubberC: 0.5, rubberD: 0.24 },
        smooth: { wheelTau: 150, padTau: 30, followTau: 170, flingTau: 460, maxFling: 9, springW: 4 / 420, rubberC: 0.5, rubberD: 0.28 },
        syrup: { wheelTau: 210, padTau: 45, followTau: 240, flingTau: 620, maxFling: 10, springW: 4 / 520, rubberC: 0.5, rubberD: 0.32 }
      };
    }
  });

  // hard-desk/ui/kit/dom.ts
  function esc7(v) {
    return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }
  function n(v) {
    const x = Number(v);
    if (!Number.isFinite(x)) return "\u2014";
    return x.toLocaleString("ru-RU");
  }
  function icon2(name, size = 18) {
    return `<svg viewBox="0 0 18 18" width="${size}" height="${size}" aria-hidden="true">${I[name] ?? ""}</svg>`;
  }
  function toast(text2) {
    let t = $("toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "toast";
      document.body.appendChild(t);
    }
    t.textContent = text2;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => t.classList.remove("show"), 2600);
  }
  function ask(title, text2, ok = "\u0414\u0430", danger = false) {
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.id = "ask";
      box.innerHTML = `<div class="sheet" role="dialog" aria-modal="true"><h3>${esc7(title)}</h3><p>${esc7(text2)}</p>
      <div class="row"><button class="btn" data-no>\u041E\u0442\u043C\u0435\u043D\u0430</button><button class="btn ${danger ? "danger" : "primary"}" data-yes>${esc7(ok)}</button></div></div>`;
      const done = (v) => {
        box.remove();
        document.removeEventListener("keydown", key2);
        resolve(v);
      };
      const key2 = (e) => {
        if (e.key === "Escape") done(false);
        if (e.key === "Enter") done(true);
      };
      box.addEventListener("click", (e) => {
        if (e.target === box) done(false);
      });
      box.querySelector("[data-no]").addEventListener("click", () => done(false));
      box.querySelector("[data-yes]").addEventListener("click", () => done(true));
      document.addEventListener("keydown", key2);
      document.body.appendChild(box);
      box.querySelector("[data-yes]").focus();
    });
  }
  var $, I, toastTimer;
  var init_dom = __esm({
    "hard-desk/ui/kit/dom.ts"() {
      "use strict";
      $ = (id) => document.getElementById(id);
      I = {
        activity: '<path d="M2 9h3l2-5 3 10 2-5h4"/>',
        team: '<circle cx="6" cy="6" r="2.4"/><circle cx="12.5" cy="7" r="2"/><path d="M1.8 14c.6-2.6 2.3-3.8 4.2-3.8S9.6 11.4 10.2 14M10 11c.8-.7 1.6-1 2.5-1 1.6 0 2.8 1 3.2 3"/>',
        promises: '<rect x="2.5" y="3" width="13" height="12" rx="2"/><path d="M2.5 7h13M6 1.8v2.4M12 1.8v2.4M6 10.5l1.6 1.6L11 9"/>',
        control: '<path d="M9 1.8 3 4v4.6c0 3.6 2.6 6.2 6 7.6 3.4-1.4 6-4 6-7.6V4L9 1.8Z"/><path d="m6.5 9 1.8 1.8L12 7"/>',
        links: '<path d="M7.5 10.5 10.5 7.5M6 8.5 4.5 10a2.5 2.5 0 0 0 3.5 3.5L9.5 12M12 9.5 13.5 8A2.5 2.5 0 0 0 10 4.5L8.5 6"/>',
        report: '<path d="M4 1.8h6.5L14 5.3V16H4Z"/><path d="M10.5 1.8v3.5H14M6.5 9.5h5M6.5 12.5h5"/>',
        device: '<rect x="2" y="3" width="14" height="9.5" rx="1.6"/><path d="M6.5 15.5h5M9 12.5v3"/>',
        close: '<path d="M4 4l10 10M14 4 4 14"/>',
        back: '<path d="M11 3.5 5.5 9l5.5 5.5"/>',
        refresh: '<path d="M14.5 8.5a5.5 5.5 0 1 1-1.6-3.9M14.5 2.5v3.6h-3.6"/>',
        download: '<path d="M9 2.5v9M5.5 8 9 11.5 12.5 8M3 14.5h12"/>',
        copy: '<rect x="5.5" y="5.5" width="9" height="9" rx="1.6"/><path d="M3.5 11.5V4a.5.5 0 0 1 .5-.5h7.5"/>',
        registry: '<path d="M2.5 3.5h13v11h-13ZM2.5 7.5h13M2.5 11h13M7 3.5v11"/>',
        people: '<circle cx="9" cy="6" r="3"/><path d="M3 16c.8-3.2 3.2-4.8 6-4.8s5.2 1.6 6 4.8"/>',
        managers: '<circle cx="7" cy="6" r="2.6"/><path d="M2 15c.7-2.7 2.6-4 5-4 1.2 0 2.3.3 3.1 1M12.5 10.5l1.2 1.2 2.3-2.4"/>',
        enrich: '<path d="M3 13.5 9 4l6 9.5"/><path d="M5.5 10h7"/>',
        channels: '<path d="M3 5.5h12M3 9h12M3 12.5h7"/>',
        service: '<circle cx="9" cy="9" r="2.4"/><path d="M9 1.8v2M9 14.2v2M1.8 9h2M14.2 9h2M3.9 3.9l1.4 1.4M12.7 12.7l1.4 1.4M3.9 14.1l1.4-1.4M12.7 5.3l1.4-1.4"/>',
        today: '<circle cx="9" cy="9" r="6.5"/><path d="M9 5.5V9l2.5 1.5"/>',
        mail: '<rect x="2" y="3.5" width="14" height="11" rx="1.8"/><path d="m2.6 4.6 6.4 5 6.4-5"/>',
        phone: '<path d="M3.6 3.2c-.5 2.9 1 6.1 3.5 8.7s5.7 3.9 8.6 3.4c.6-.1 1-.7.8-1.3l-.6-2c-.2-.6-.8-.9-1.4-.8l-1.7.4a11.4 11.4 0 0 1-4.7-4.7l.4-1.7c.1-.6-.2-1.2-.8-1.4l-2-.6c-.6-.2-1.2.2-1.3.8Z"/>'
      };
      toastTimer = 0;
    }
  });

  // hard-desk/ui/kit/shell.ts
  function setEmbeddedShell(value) {
    embedded = value;
    return () => {
      if (embedded === value) embedded = void 0;
    };
  }
  function drawRail() {
    const item = (s) => `<button class="rail-item" type="button" data-go="${esc7(s.id)}"
      ${current2?.id === s.id ? 'aria-current="page"' : ""}>${icon2(s.icon)}<span>${esc7(s.title)}</span>
      ${s.badge ? `<span class="badge">${esc7(s.badge())}</span>` : ""}</button>`;
    $("rail-nav").innerHTML = sections.filter((s) => !s.bottom).map(item).join("");
    $("rail-bottom").innerHTML = sections.filter((s) => s.bottom).map(item).join("");
    document.querySelectorAll("#rail [data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
  }
  function setHead(title, kicker = "", tools = "") {
    if (embedded) {
      embedded.setHead(title, kicker, tools);
      return;
    }
    $("head-title").textContent = title;
    $("head-kicker").textContent = kicker;
    $("head-tools").innerHTML = tools;
  }
  async function go(id, params = {}) {
    if (embedded) return embedded.go(id, params);
    if (document.querySelector('#panel-inner [data-saving="1"]')) {
      toast("\u0414\u043E\u0436\u0434\u0438\u0442\u0435\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u044F \u0437\u0430\u043F\u0438\u0441\u0438");
      return;
    }
    const s = sections.find((x) => x.id === id) ?? sections[0];
    if (!s) return;
    if (current2 && current2 !== s) current2.leave?.();
    const same = current2 === s;
    current2 = s;
    currentParams = params;
    drawRail();
    if (!same) closePanel();
    setHead(s.title, s.kicker ?? "");
    const host = $("view-inner");
    const page = document.createElement("div");
    page.className = "page";
    const seq = ++renderSeq;
    host.replaceChildren(page);
    if (!same) viewScroller?.scrollTo(0, false);
    try {
      localStorage.setItem("hard.section", s.id);
    } catch {
    }
    await s.render(page, params);
    if (seq !== renderSeq) return;
  }
  function refresh() {
    if (embedded) return embedded.refresh();
    return current2 ? go(current2.id, currentParams) : Promise.resolve();
  }
  function openPanel(titleHtml, bodyHtml = '<div class="skeleton"></div><div class="skeleton"></div>') {
    if (embedded) return embedded.openPanel(titleHtml, bodyHtml);
    $("app").classList.remove("with-call-script");
    const focused = document.activeElement;
    if (focused?.closest("#view-inner")) panelReturn = focused;
    $("panel-title").innerHTML = titleHtml;
    const inner = $("panel-inner");
    inner.innerHTML = `<div class="page">${bodyHtml}</div>`;
    $("app").classList.add("with-panel");
    panelScroller?.scrollTo(0, false);
    return inner.firstElementChild;
  }
  function closePanel() {
    if (embedded) {
      embedded.closePanel();
      return;
    }
    if (document.querySelector('#panel-inner [data-saving="1"]')) {
      toast("\u0414\u043E\u0436\u0434\u0438\u0442\u0435\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u044F \u0437\u0430\u043F\u0438\u0441\u0438");
      return;
    }
    $("app").classList.remove("with-panel");
    $("app").classList.remove("with-call-script");
    document.querySelectorAll('[aria-selected="true"]').forEach((el) => el.removeAttribute("aria-selected"));
    const target = panelReturn?.isConnected ? panelReturn : document.querySelector("#view-inner .current-row") || document.querySelector("#view-inner [data-tsearch]");
    target?.focus({ preventScroll: true });
    panelReturn = null;
  }
  var sections, current2, currentParams, viewScroller, panelScroller, renderSeq, panelReturn, embedded;
  var init_shell = __esm({
    "hard-desk/ui/kit/shell.ts"() {
      "use strict";
      init_scroller();
      init_dom();
      init_api();
      sections = [];
      current2 = null;
      currentParams = {};
      viewScroller = null;
      panelScroller = null;
      renderSeq = 0;
      panelReturn = null;
    }
  });

  // hard-desk/ui/kit/login-links.ts
  var init_login_links = __esm({
    "hard-desk/ui/kit/login-links.ts"() {
      "use strict";
      init_api();
      init_dom();
    }
  });

  // hard-desk/ui/kit/issued.ts
  var init_issued = __esm({
    "hard-desk/ui/kit/issued.ts"() {
      "use strict";
      init_api();
      init_dom();
      init_login_links();
    }
  });

  // hard-desk/ui/analyst/links.ts
  async function issueLink(_person) {
    await go("accounts");
    toast("\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A \u0441\u043E\u0437\u0434\u0430\u0451\u0442 \u043F\u0440\u043E\u0444\u0438\u043B\u044C \u0432 \u043F\u0440\u043E\u0433\u0440\u0430\u043C\u043C\u0435 \u043D\u0430 \u0441\u0432\u043E\u0451\u043C \u041F\u041A. \u0417\u0434\u0435\u0441\u044C \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B \u043F\u0440\u043E\u0444\u0438\u043B\u0438 \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430.");
  }
  var init_links = __esm({
    "hard-desk/ui/analyst/links.ts"() {
      "use strict";
      init_shell();
      init_dom();
      init_issued();
    }
  });

  // hard-desk/ui/kit/table.ts
  function stateOf(id) {
    let s = states2.get(id);
    if (!s) {
      s = { sort: null, filters: {}, q: "", selected: /* @__PURE__ */ new Set() };
      states2.set(id, s);
    }
    return s;
  }
  function text(v) {
    if (v === null || v === void 0) return "";
    return String(v);
  }
  function numOf(v) {
    if (typeof v === "number") return v;
    const t = String(v ?? "").replace(/\s| /g, "").replace(",", ".").replace(/[^\d.\-]/g, "");
    const x = parseFloat(t);
    return Number.isFinite(x) ? x : NaN;
  }
  function tupleCols(cols, numeric = /* @__PURE__ */ new Set()) {
    return cols.map(([k, t], i) => ({ key: k, title: t, num: numeric.has(k), value: (r) => r[i] }));
  }
  var states2, searchText, FUNNEL, DataTable;
  var init_table = __esm({
    "hard-desk/ui/kit/table.ts"() {
      "use strict";
      init_api();
      init_dom();
      states2 = /* @__PURE__ */ new Map();
      searchText = (value) => value.normalize("NFKC").toLowerCase().replace(/ё/g, "\u0435");
      FUNNEL = '<svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M1.5 2h9L7 6.2V10L5 9V6.2Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';
      DataTable = class {
        constructor(host, o) {
          this.host = host;
          this.o = o;
          this.st = stateOf(o.id);
          this.st.hidden ??= o.defaultHidden || [];
        }
        rows = [];
        shown = [];
        st;
        pop = null;
        loaded = false;
        index = /* @__PURE__ */ new Map();
        watch = null;
        async restore() {
          if (!this.o.profile || this.loaded) return;
          this.loaded = true;
          const r = await api2("/api/local/preferences", { profile: this.o.profile, id: this.o.id });
          const saved = r.data?.value;
          this.st = { sort: null, filters: {}, q: "", selected: /* @__PURE__ */ new Set(), hidden: [...this.o.defaultHidden || []] };
          states2.set(this.o.id, this.st);
          if (!saved || typeof saved !== "object") return;
          const keys = new Set(this.o.cols.map((c) => c.key));
          this.st.q = typeof saved.q === "string" ? saved.q.slice(0, 2e3) : "";
          this.st.sort = saved.sort && keys.has(saved.sort.key) && [1, -1].includes(saved.sort.dir) ? saved.sort : null;
          this.st.filters = {};
          for (const [key2, value] of Object.entries(saved.filters || {})) {
            if (!keys.has(key2) || !value || typeof value !== "object") continue;
            this.st.filters[key2] = {
              values: Array.isArray(value.values) ? new Set(value.values.filter((x) => typeof x === "string")) : void 0,
              excluded: Array.isArray(value.excluded) ? new Set(value.excluded.filter((x) => typeof x === "string")) : void 0,
              min: Number.isFinite(value.min) ? value.min : void 0,
              max: Number.isFinite(value.max) ? value.max : void 0
            };
          }
          this.st.view = this.o.listHtml && saved.view === "list" ? "list" : "table";
          this.st.hidden = Array.isArray(saved.hidden) ? saved.hidden.filter((x) => keys.has(x) && x !== this.o.cols[0]?.key) : this.st.hidden;
          this.st.order = Array.isArray(saved.order) ? [...new Set(saved.order.filter((x) => keys.has(x)))] : [];
          this.st.widths = Object.fromEntries(Object.entries(saved.widths || {}).filter(([k, v]) => keys.has(k) && typeof v === "number" && v >= 80 && v <= 800));
          this.st.page = Number.isInteger(saved.page) && saved.page >= 0 ? saved.page : 0;
          this.st.active = typeof saved.active === "string" ? saved.active : "";
          this.st.scrollLeft = Number.isFinite(saved.scrollLeft) ? saved.scrollLeft : 0;
          this.st.scrollTop = Number.isFinite(saved.scrollTop) ? saved.scrollTop : 0;
        }
        persist() {
          if (!this.o.profile) return;
          const value = { ...this.st, selected: void 0, filters: Object.fromEntries(Object.entries(this.st.filters).map(([k, v]) => [k, { ...v, values: v.values ? [...v.values] : void 0, excluded: v.excluded ? [...v.excluded] : void 0 }])) };
          void api2("/api/local/preferences", void 0, "POST", { profile: this.o.profile, id: this.o.id, value }).then((r) => {
            if (!r.ok && this.host.isConnected) toast(r.text || "\u041D\u0430\u0441\u0442\u0440\u043E\u0439\u043A\u0438 \u043D\u0435 \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u043B\u0438\u0441\u044C");
          });
        }
        columns() {
          const order = this.st.order || [];
          const all2 = [...this.o.cols].sort((a, b) => (order.includes(a.key) ? order.indexOf(a.key) : order.length + this.o.cols.indexOf(a)) - (order.includes(b.key) ? order.indexOf(b.key) : order.length + this.o.cols.indexOf(b)));
          const first = this.o.cols[0];
          return [first, ...all2.filter((c) => c !== first && !this.st.hidden?.includes(c.key))].filter(Boolean);
        }
        navigate(key2, delta) {
          if (!this.host.isConnected) return void 0;
          const index = this.shown.findIndex((r) => this.o.key(r) === key2);
          return index < 0 ? void 0 : this.shown[index + delta];
        }
        setRows(rows) {
          this.rows = rows;
          this.index.clear();
          for (const row of rows) this.index.set(row, searchText(this.o.cols.map((c) => text(c.value(row))).join("\n")));
          const keys = new Set(rows.map((r) => this.o.key(r)));
          for (const k of [...this.st.selected]) if (!keys.has(k)) this.st.selected.delete(k);
          this.render();
        }
        setQuery(value) {
          this.st.q = value;
          this.st.page = 0;
          this.render();
        }
        selected() {
          return this.rows.filter((r) => this.st.selected.has(this.o.key(r)));
        }
        /** Отмеченные, а если не отмечено никого — все показанные фильтром. */
        scope() {
          const sel = this.selected();
          if (sel.length) return { rows: sel, label: `\u043E\u0442\u043C\u0435\u0447\u0435\u043D\u043D\u044B\u043C (${sel.length})`, picked: true };
          return { rows: this.shown.slice(), label: this.shown.length === this.rows.length ? `\u0432\u0441\u0435\u043C (${this.shown.length})` : `\u043F\u043E\u043A\u0430\u0437\u0430\u043D\u043D\u044B\u043C (${this.shown.length})`, picked: false };
        }
        clearSelection() {
          this.st.selected.clear();
          this.render();
        }
        passes(r) {
          for (const c of this.o.cols) {
            const f = this.st.filters[c.key];
            if (!f) continue;
            const v = c.value(r);
            if (f.values && !f.values.has(text(v))) return false;
            if (f.excluded?.has(text(v))) return false;
            if (f.min !== void 0 || f.max !== void 0) {
              const x = numOf(v);
              if (!Number.isFinite(x)) return false;
              if (f.min !== void 0 && x < f.min) return false;
              if (f.max !== void 0 && x > f.max) return false;
            }
          }
          const words = searchText(this.st.q).trim().split(/\s+/).filter(Boolean);
          if (!words.every((word) => (this.index.get(r) || "").includes(word))) return false;
          return true;
        }
        compute() {
          let out = this.rows.filter((r) => this.passes(r));
          const s = this.st.sort;
          if (s) {
            const c = this.o.cols.find((x) => x.key === s.key);
            if (c) {
              out = out.slice().sort((a, b) => {
                const va = c.value(a), vb = c.value(b);
                if (c.num) {
                  const x = numOf(va), y = numOf(vb);
                  const nx = Number.isFinite(x), ny = Number.isFinite(y);
                  if (nx !== ny) return nx ? -1 : 1;
                  return ((x || 0) - (y || 0)) * s.dir;
                }
                const ta = text(va), tb = text(vb);
                if (!ta !== !tb) return ta ? -1 : 1;
                return ta.localeCompare(tb, "ru", { numeric: true }) * s.dir;
              });
            }
          }
          return out;
        }
        render() {
          this.shown = this.compute();
          const o = this.o, st3 = this.st;
          const cols = this.columns();
          const wrap = this.host.querySelector(".table-wrap");
          if (wrap) {
            st3.scrollLeft = wrap.scrollLeft;
            st3.scrollTop = wrap.scrollTop;
          }
          const pageSize = o.pageSize || this.shown.length || 1;
          st3.page = Math.min(st3.page || 0, Math.max(0, Math.ceil(this.shown.length / pageSize) - 1));
          const pageRows = this.shown.slice(st3.page * pageSize, (st3.page + 1) * pageSize);
          const active = Object.keys(st3.filters).filter((k) => st3.filters[k]);
          const tokens = active.map((k) => {
            const c = o.cols.find((x) => x.key === k);
            const f = st3.filters[k];
            let what = "";
            if (f.excluded) {
              const list = [...f.excluded].map((v) => v || "\u043F\u0443\u0441\u0442\u043E");
              what = "\u043A\u0440\u043E\u043C\u0435 " + (list.length > 2 ? `${list.slice(0, 2).join(", ")} \u0438 \u0435\u0449\u0451 ${list.length - 2}` : list.join(", "));
            } else if (f.values) {
              const list = [...f.values].map((v) => v || "\u043F\u0443\u0441\u0442\u043E");
              what = list.length > 2 ? `${list.slice(0, 2).join(", ")} \u0438 \u0435\u0449\u0451 ${list.length - 2}` : list.join(", ");
            } else {
              what = [f.min !== void 0 ? `\u043E\u0442 ${f.min}` : "", f.max !== void 0 ? `\u0434\u043E ${f.max}` : ""].filter(Boolean).join(" ");
            }
            return `<button class="ftoken" data-unfilter="${esc7(k)}" title="\u0421\u043D\u044F\u0442\u044C \u0444\u0438\u043B\u044C\u0442\u0440">${esc7(c.title)}: ${esc7(what)} <span aria-hidden="true">\xD7</span></button>`;
          }).join("");
          const allOn = o.select && this.shown.length > 0 && this.shown.every((r) => st3.selected.has(o.key(r)));
          const th = cols.map((c) => {
            const sorted = st3.sort?.key === c.key ? st3.sort.dir > 0 ? "ascending" : "descending" : "";
            const filtered = !!st3.filters[c.key];
            const width = st3.widths?.[c.key] ? `${st3.widths[c.key]}px` : c.width;
            return `<th class="${c.num && !c.left ? "" : "l"}${filtered ? " filtered" : ""}" data-col="${esc7(c.key)}"${sorted ? ` aria-sort="${sorted}"` : ""}${width ? ` style="width:${width};min-width:${width};max-width:${width}"` : ""}>
        <span class="th-in"><button class="th-t" data-sort="${esc7(c.key)}" aria-label="\u0421\u043E\u0440\u0442\u0438\u0440\u043E\u0432\u0430\u0442\u044C: ${esc7(c.title)}">${esc7(c.title)}</button>${c.filter === false ? "" : `<button class="funnel" data-funnel="${esc7(c.key)}" title="\u0424\u0438\u043B\u044C\u0442\u0440: ${esc7(c.title)}">${FUNNEL}</button>`}</span></th>`;
          }).join("");
          const body = pageRows.map((r) => {
            const k = o.key(r);
            const box = o.select ? `<td class="l sel"><input type="checkbox" data-pick="${esc7(k)}" ${st3.selected.has(k) ? "checked" : ""}></td>` : "";
            const tds = cols.map((c) => `<td class="${c.num && !c.left ? "" : "l"}"${st3.widths?.[c.key] ? ` style="max-width:${st3.widths[c.key]}px"` : ""}>${c.html ? c.html(r) : esc7(text(c.value(r)))}</td>`).join("");
            return `<tr class="${o.onRow ? "pick" : ""} ${o.rowClass ? o.rowClass(r) : ""}${st3.active === k ? " current-row" : ""}" data-key="${esc7(k)}"${o.onRow ? ' tabindex="0"' : ""}${st3.selected.has(k) ? ' aria-selected="true"' : ""}>${box}${tds}</tr>`;
          }).join("");
          const foot = o.footer ? o.footer(this.shown) : null;
          const tfoot = foot ? `<tfoot><tr>${o.select ? "<td></td>" : ""}${foot.map((h, i) => `<td class="${o.cols[i]?.num && !o.cols[i]?.left ? "" : "l"}">${h ?? ""}</td>`).join("")}</tr></tfoot>` : "";
          const count = this.shown.length === this.rows.length ? `${n(this.rows.length)}` : `${n(this.shown.length)} \u0438\u0437 ${n(this.rows.length)}`;
          this.host.innerHTML = `
      <div class="tbar">
        ${o.listHtml ? `<div class="view-tabs" role="group" aria-label="\u041F\u0440\u0435\u0434\u0441\u0442\u0430\u0432\u043B\u0435\u043D\u0438\u0435 \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044F"><button class="btn small ${st3.view !== "list" ? "primary" : ""}" data-view="table" aria-pressed="${st3.view !== "list"}">\u0422\u0430\u0431\u043B\u0438\u0446\u0430</button><button class="btn small ${st3.view === "list" ? "primary" : ""}" data-view="list" aria-pressed="${st3.view === "list"}">\u0421\u043F\u0438\u0441\u043E\u043A</button></div>` : ""}
        ${o.toolsLeft || ""}
        <input class="tsearch" data-tsearch ${o.externalSearch ? "hidden" : ""} placeholder="${esc7(o.searchPlaceholder || "\u041D\u0430\u0439\u0442\u0438 \u0432 \u0442\u0430\u0431\u043B\u0438\u0446\u0435")}" value="${esc7(st3.q)}">
        ${tokens}${active.length > 1 ? '<button class="ftoken quiet" data-unfilter="*">\u0441\u043D\u044F\u0442\u044C \u0432\u0441\u0435</button>' : ""}
        <span class="spacer"></span>
        <span class="tcount">${count}</span>
        <button class="ticon" data-tcopy title="\u0421\u043A\u043E\u043F\u0438\u0440\u043E\u0432\u0430\u0442\u044C \u043F\u043E\u043A\u0430\u0437\u0430\u043D\u043D\u043E\u0435 (\u0432\u0441\u0442\u0430\u0432\u0438\u0442\u0441\u044F \u0432 Excel)"><svg viewBox="0 0 18 18" width="15" height="15"><rect x="5.5" y="5.5" width="9" height="9" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 11.5V4a.5.5 0 0 1 .5-.5h7.5" fill="none" stroke="currentColor" stroke-width="1.3"/></svg></button>
        <button class="ticon" data-txlsx title="\u0421\u043A\u0430\u0447\u0430\u0442\u044C \u043F\u043E\u043A\u0430\u0437\u0430\u043D\u043D\u043E\u0435 \u0432 Excel"><svg viewBox="0 0 18 18" width="15" height="15"><path d="M9 2.5v9M5.5 8 9 11.5 12.5 8M3 14.5h12" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>
      </div>
      ${o.profile ? `<details class="column-picker"><summary>\u041A\u043E\u043B\u043E\u043D\u043A\u0438 \u0438 \u043F\u043E\u0440\u044F\u0434\u043E\u043A</summary><div class="column-options">${[...cols, ...o.cols.filter((c) => !cols.includes(c))].map((c) => `<div class="row"><label class="grow"><input type="checkbox" data-column="${esc7(c.key)}" ${st3.hidden?.includes(c.key) ? "" : "checked"} ${c === o.cols[0] ? "disabled" : ""}> ${esc7(c.title)}</label><input class="input column-width" type="number" min="80" max="800" aria-label="\u0428\u0438\u0440\u0438\u043D\u0430: ${esc7(c.title)}" data-width="${esc7(c.key)}" value="${st3.widths?.[c.key] || parseInt(c.width || "160", 10)}"><button class="btn small" data-move="${esc7(c.key)}" title="\u041F\u0435\u0440\u0435\u043C\u0435\u0441\u0442\u0438\u0442\u044C \u0432\u044B\u0448\u0435" ${c === o.cols[0] ? "disabled" : ""}>\u2191</button></div>`).join("")}</div></details>` : ""}
      ${st3.view === "list" && o.listHtml ? `<div class="list-filters">${cols.map((c) => `<button class="btn small" data-sort="${esc7(c.key)}">${esc7(c.title)} ${st3.sort?.key === c.key ? st3.sort.dir === 1 ? "\u2191" : "\u2193" : ""}</button>${c.filter !== false ? `<button class="funnel" data-funnel="${esc7(c.key)}" title="\u0424\u0438\u043B\u044C\u0442\u0440: ${esc7(c.title)}">${FUNNEL}</button>` : ""}`).join("")}</div><div class="portfolio-list">${pageRows.map((r) => `<button type="button" class="portfolio-person ${st3.active === o.key(r) ? "current-row" : ""}" data-key="${esc7(o.key(r))}">${o.listHtml(r)}</button>`).join("")}</div>` : `<div class="table-wrap${o.profile ? " employee-table" : ""}"><table class="grid dt">
        <thead><tr>${o.select ? `<th class="l sel"><input type="checkbox" data-pickall ${allOn ? "checked" : ""} title="\u041E\u0442\u043C\u0435\u0442\u0438\u0442\u044C \u043F\u043E\u043A\u0430\u0437\u0430\u043D\u043D\u044B\u0445"></th>` : ""}${th}</tr></thead>
        <tbody>${body}</tbody>${tfoot}</table></div>`}
      ${o.pageSize && this.shown.length > pageSize ? `<div class="row table-pages"><button class="btn small" data-page="-1" ${st3.page === 0 ? "disabled" : ""}>\u041D\u0430\u0437\u0430\u0434</button><span class="muted">${st3.page * pageSize + 1}\u2013${Math.min((st3.page + 1) * pageSize, this.shown.length)} \u0438\u0437 ${n(this.shown.length)}</span><button class="btn small" data-page="1" ${(st3.page + 1) * pageSize >= this.shown.length ? "disabled" : ""}>\u0414\u0430\u043B\u044C\u0448\u0435</button></div>` : ""}
      ${this.shown.length ? "" : `<div class="empty" style="padding:28px 0"><b>${this.rows.length ? "\u041D\u0438\u0447\u0435\u0433\u043E \u043D\u0435 \u043F\u043E\u0434\u043E\u0448\u043B\u043E" : "\u041F\u0443\u0441\u0442\u043E"}</b>${this.rows.length ? "\u0421\u043D\u0438\u043C\u0438\u0442\u0435 \u0447\u0430\u0441\u0442\u044C \u0444\u0438\u043B\u044C\u0442\u0440\u043E\u0432." : esc7(o.empty || "")}</div>`}`;
          this.wire();
          const again = this.host.querySelector(".table-wrap");
          if (again) {
            again.scrollLeft = st3.scrollLeft || 0;
            again.scrollTop = st3.scrollTop || 0;
            again.addEventListener("scroll", () => {
              st3.scrollLeft = again.scrollLeft;
              st3.scrollTop = again.scrollTop;
              this.persist();
            }, { passive: true });
          }
          this.persist();
          o.afterRender?.(this.host);
          o.onSelect?.(new Set(st3.selected), this.shown);
        }
        wire() {
          const h = this.host, st3 = this.st, o = this.o;
          const search = h.querySelector("[data-tsearch]");
          search.addEventListener("input", () => {
            st3.q = search.value;
            st3.page = 0;
            const pos = search.selectionStart;
            this.render();
            const again = h.querySelector("[data-tsearch]");
            again.focus();
            again.setSelectionRange(pos, pos);
          });
          h.querySelectorAll("[data-view]").forEach((button2) => button2.addEventListener("click", () => {
            st3.view = button2.dataset.view;
            this.render();
          }));
          h.querySelectorAll("[data-page]").forEach((button2) => button2.addEventListener("click", () => {
            st3.page = (st3.page || 0) + Number(button2.dataset.page);
            this.render();
            h.querySelector("[data-tsearch]")?.focus();
          }));
          h.querySelectorAll("[data-column]").forEach((input) => input.addEventListener("change", () => {
            st3.hidden = this.o.cols.filter((c) => c.key !== this.o.cols[0].key && (c.key === input.dataset.column ? !input.checked : st3.hidden?.includes(c.key))).map((c) => c.key);
            this.render();
          }));
          h.querySelectorAll("[data-width]").forEach((input) => input.addEventListener("change", () => {
            st3.widths = { ...st3.widths, [input.dataset.width]: Math.max(80, Math.min(800, Number(input.value) || 160)) };
            this.render();
          }));
          h.querySelectorAll("[data-move]").forEach((button2) => button2.addEventListener("click", () => {
            const order = [...this.columns().map((c) => c.key), ...this.o.cols.filter((c) => st3.hidden?.includes(c.key)).map((c) => c.key)];
            const index = order.indexOf(button2.dataset.move);
            if (index > 1) [order[index - 1], order[index]] = [order[index], order[index - 1]];
            st3.order = order;
            this.render();
          }));
          h.querySelectorAll("[data-sort]").forEach((el) => el.addEventListener("click", () => {
            const k = el.dataset.sort;
            const c = o.cols.find((x) => x.key === k);
            if (st3.sort?.key !== k) st3.sort = { key: k, dir: c.num ? -1 : 1 };
            else if (st3.sort.dir === (c.num ? -1 : 1)) st3.sort = { key: k, dir: c.num ? 1 : -1 };
            else st3.sort = null;
            this.render();
          }));
          h.querySelectorAll("[data-funnel]").forEach((b) => b.addEventListener("click", (e) => {
            e.stopPropagation();
            this.openFilter(b.dataset.funnel, b);
          }));
          h.querySelectorAll("[data-unfilter]").forEach((b) => b.addEventListener("click", () => {
            if (b.dataset.unfilter === "*") st3.filters = {};
            else delete st3.filters[b.dataset.unfilter];
            this.render();
          }));
          h.querySelectorAll("[data-pick]").forEach((cb) => {
            cb.addEventListener("click", (e) => e.stopPropagation());
            cb.addEventListener("change", () => {
              if (cb.checked) st3.selected.add(cb.dataset.pick);
              else st3.selected.delete(cb.dataset.pick);
              this.render();
            });
          });
          h.querySelector("[data-pickall]")?.addEventListener("change", (e) => {
            const on = e.target.checked;
            for (const r of this.shown) {
              const k = o.key(r);
              if (on) st3.selected.add(k);
              else st3.selected.delete(k);
            }
            this.render();
          });
          if (o.onRow) {
            h.querySelectorAll("tbody tr[data-key]").forEach((tr) => tr.addEventListener("keydown", (e) => {
              if (e.target === tr && (e.key === "Enter" || e.key === " ")) {
                e.preventDefault();
                tr.click();
              }
            }));
            h.querySelectorAll("tbody tr[data-key],.portfolio-person[data-key]").forEach((tr) => tr.addEventListener("click", (e) => {
              if (document.querySelector('#panel-inner [data-saving="1"]')) {
                toast("\u0414\u043E\u0436\u0434\u0438\u0442\u0435\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u044F \u0437\u0430\u043F\u0438\u0441\u0438");
                return;
              }
              if (!e.target.closest("input,select,a")) tr.focus({ preventScroll: true });
              if (tr.matches(".portfolio-person") && e.target.closest(".portfolio-person") === tr) {
                const row = this.rows.find((x) => o.key(x) === tr.dataset.key);
                if (row) {
                  st3.active = o.key(row);
                  h.querySelectorAll(".current-row").forEach((el) => el.classList.remove("current-row"));
                  tr.classList.add("current-row");
                  this.persist();
                  o.onRow(row, tr);
                }
                return;
              }
              if (e.target.closest("input,button,a,select")) return;
              const r = this.rows.find((x) => o.key(x) === tr.dataset.key);
              if (r) {
                st3.active = o.key(r);
                h.querySelectorAll(".current-row").forEach((el) => el.classList.remove("current-row"));
                tr.classList.add("current-row");
                this.persist();
                o.onRow(r, tr);
              }
            }));
          }
          h.querySelector("[data-tcopy]")?.addEventListener("click", async () => {
            const cols = this.columns();
            const lines = [
              cols.map((c) => c.title).join("	"),
              ...this.shown.map((r) => cols.map((c) => text(c.value(r)).replace(/[\t\n]/g, " ")).join("	"))
            ];
            await hard.copy(lines.join("\n"));
            toast(`\u0421\u043A\u043E\u043F\u0438\u0440\u043E\u0432\u0430\u043D\u043E \u0441\u0442\u0440\u043E\u043A: ${this.shown.length}`);
          });
          h.querySelector("[data-txlsx]")?.addEventListener("click", async () => {
            const cols = this.columns();
            const res = await hard.xlsx(
              o.exportName || o.id,
              cols.map((c) => c.title),
              this.shown.map((r) => cols.map((c) => {
                const v = c.value(r);
                return typeof v === "number" ? v : text(v);
              }))
            );
            if (res.ok && res.file) {
              toast("\u041A\u043D\u0438\u0433\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430");
              void hard.open(res.file);
            }
          });
        }
        openFilter(key2, anchor) {
          this.closeFilter();
          const c = this.o.cols.find((x) => x.key === key2);
          const st3 = this.st;
          const saved = st3.filters[key2];
          delete st3.filters[key2];
          const pool = this.rows.filter((r2) => this.passes(r2));
          if (saved) st3.filters[key2] = saved;
          const counts = /* @__PURE__ */ new Map();
          for (const r2 of pool) {
            const v = text(c.value(r2));
            counts.set(v, (counts.get(v) || 0) + 1);
          }
          const values = [...counts.keys()].sort((a, b) => c.num ? (numOf(a) || 0) - (numOf(b) || 0) : a.localeCompare(b, "ru", { numeric: true }));
          const asRange = c.num && values.length > 12;
          const pop = document.createElement("div");
          pop.className = "fpop";
          const cur = saved?.values;
          const checked = new Set(values.filter((v) => cur ? cur.has(v) : !saved?.excluded?.has(v)));
          pop.innerHTML = asRange ? `<div class="fpop-h">${esc7(c.title)}</div>
         <div class="fpop-range"><input class="input" data-min placeholder="\u043E\u0442" value="${saved?.min ?? ""}"><input class="input" data-max placeholder="\u0434\u043E" value="${saved?.max ?? ""}"></div>
         <div class="fpop-foot"><button class="link" data-reset>\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C</button><span class="spacer"></span><button class="btn small primary" data-apply>\u0413\u043E\u0442\u043E\u0432\u043E</button></div>` : `<div class="fpop-h">${esc7(c.title)}</div>
         ${values.length > 8 ? '<input class="input fpop-q" data-fq placeholder="\u041D\u0430\u0439\u0442\u0438 \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0435">' : ""}
         <div class="fpop-acts"><button class="link" data-all>\u0432\u0441\u0435</button><button class="link" data-none>\u043D\u0438\u0447\u0435\u0433\u043E</button></div>
         <div class="fpop-list"></div><div class="hint" data-filter-count></div>
         <div class="fpop-foot"><button class="link" data-reset>\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C</button><span class="spacer"></span><button class="btn small primary" data-apply>\u0413\u043E\u0442\u043E\u0432\u043E</button></div>`;
          document.body.appendChild(pop);
          const r = anchor.getBoundingClientRect();
          const left = Math.min(window.innerWidth - pop.offsetWidth - 12, Math.max(12, r.left - 12));
          pop.style.left = `${left}px`;
          pop.style.top = `${Math.min(window.innerHeight - pop.offsetHeight - 12, r.bottom + 6)}px`;
          this.pop = pop;
          const apply = () => {
            if (asRange) {
              const mn = numOf(pop.querySelector("[data-min]").value);
              const mx = numOf(pop.querySelector("[data-max]").value);
              if (!Number.isFinite(mn) && !Number.isFinite(mx)) delete st3.filters[key2];
              else st3.filters[key2] = { min: Number.isFinite(mn) ? mn : void 0, max: Number.isFinite(mx) ? mx : void 0 };
            } else {
              if (checked.size === values.length) delete st3.filters[key2];
              else if (checked.size > values.length / 2) st3.filters[key2] = { excluded: new Set(values.filter((v) => !checked.has(v))) };
              else st3.filters[key2] = { values: new Set(checked) };
            }
            this.closeFilter();
            this.render();
          };
          pop.querySelector("[data-apply]").addEventListener("click", apply);
          pop.querySelector("[data-reset]").addEventListener("click", () => {
            delete st3.filters[key2];
            this.closeFilter();
            this.render();
          });
          const fq = pop.querySelector("[data-fq]");
          const matching = () => {
            const q = fq?.value.trim().toLowerCase() || "";
            return values.filter((v) => !q || v.toLowerCase().includes(q));
          };
          const drawValues = () => {
            if (asRange) return;
            const match = matching();
            pop.querySelector(".fpop-list").innerHTML = match.slice(0, 500).map((v) => `<label class="fpop-row"><input type="checkbox" value="${esc7(v)}" ${checked.has(v) ? "checked" : ""}><span class="grow">${esc7(v || "\u043F\u0443\u0441\u0442\u043E")}</span><span class="fpop-n">${n(counts.get(v))}</span></label>`).join("");
            pop.querySelector("[data-filter-count]").textContent = match.length > 500 ? `\u041F\u043E\u043A\u0430\u0437\u0430\u043D\u043E 500 \u0438\u0437 ${n(match.length)} \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0439. \u0423\u0442\u043E\u0447\u043D\u0438\u0442\u0435 \u043F\u043E\u0438\u0441\u043A. \xAB\u0412\u0441\u0435/\u043D\u0438\u0447\u0435\u0433\u043E\xBB \u0434\u0435\u0439\u0441\u0442\u0432\u0443\u0435\u0442 \u043D\u0430 \u0432\u0441\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u043D\u044B\u0435 \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F.` : "";
          };
          pop.querySelector(".fpop-list")?.addEventListener("change", (event) => {
            const input = event.target;
            if (input.checked) checked.add(input.value);
            else checked.delete(input.value);
          });
          pop.querySelector("[data-all]")?.addEventListener("click", () => {
            for (const v of matching()) checked.add(v);
            drawValues();
          });
          pop.querySelector("[data-none]")?.addEventListener("click", () => {
            for (const v of matching()) checked.delete(v);
            drawValues();
          });
          fq?.addEventListener("input", drawValues);
          drawValues();
          (fq || pop.querySelector("[data-min]"))?.focus();
          pop.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              e.stopPropagation();
              apply();
            }
            if (e.key === "Escape") {
              e.stopPropagation();
              this.closeFilter();
            }
          });
          this.watch = new MutationObserver(() => {
            if (!this.host.isConnected) this.closeFilter();
          });
          this.watch.observe(document.body, { childList: true, subtree: true });
          setTimeout(() => document.addEventListener("mousedown", this.outside, true), 0);
        }
        outside = (e) => {
          if (this.pop && !this.pop.contains(e.target)) this.closeFilter();
        };
        closeFilter() {
          this.watch?.disconnect();
          this.watch = null;
          document.removeEventListener("mousedown", this.outside, true);
          this.pop?.remove();
          this.pop = null;
        }
      };
    }
  });

  // hard-desk/ui/analyst/lib.ts
  async function adm(method, path, body, timeout = 30) {
    for (const fn2 of listeners3) fn2({ level: "busy", text: "\u041E\u0431\u043D\u043E\u0432\u043B\u044F\u044E\u2026" });
    const r = await hard.local("/admin", { method, path, body, timeout });
    if (!r.ok) {
      const off = /Нет связи|сертификат/i.test(r.error || "");
      for (const fn2 of listeners3) fn2({ level: off ? "off" : "warn", text: off ? "\u041D\u0435\u0442 \u0441\u0432\u044F\u0437\u0438 \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C" : "\u0421\u0435\u0440\u0432\u0435\u0440 \u043E\u0442\u0432\u0435\u0442\u0438\u043B \u043E\u0448\u0438\u0431\u043A\u043E\u0439" });
      throw new Fail(r.error || "\u043D\u0435 \u0432\u044B\u0448\u043B\u043E");
    }
    for (const fn2 of listeners3) fn2({ level: "live", text: "\u041D\u0430 \u0441\u0432\u044F\u0437\u0438 \xB7 " + (/* @__PURE__ */ new Date()).toTimeString().slice(0, 5) });
    return r.data;
  }
  async function fn(name, ...args) {
    const r = await hard.local("/fn", { name, args });
    if (!r.ok) throw new Fail(r.error || name);
    return r.data;
  }
  async function local(path, body = {}) {
    const r = await hard.local(path, body);
    if (!r.ok) throw new Fail(r.error || path);
    return r.data;
  }
  async function runJob(path, body, log) {
    const id = crypto.randomUUID();
    localStorage.setItem("hard:last-job", id);
    let acknowledgement;
    try {
      acknowledgement = await hard.local(path, { ...body, request_id: id });
    } catch {
      log?.(`\u041F\u0440\u043E\u0432\u0435\u0440\u044F\u044E \u0441\u043E\u0441\u0442\u043E\u044F\u043D\u0438\u0435 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438 \u2116${id} \u043F\u043E\u0441\u043B\u0435 \u043F\u043E\u0442\u0435\u0440\u0438 \u043E\u0442\u0432\u0435\u0442\u0430\u2026`);
    }
    if (acknowledgement && !acknowledgement.ok) {
      const message = acknowledgement.error || "\u041E\u043F\u0435\u0440\u0430\u0446\u0438\u044F \u043D\u0435 \u043F\u0440\u0438\u043D\u044F\u0442\u0430";
      if (!/Ответ на операцию потерян|Питон-сторона.*не отвечает/i.test(message)) throw new Fail(message);
      log?.(`\u041F\u0440\u043E\u0432\u0435\u0440\u044F\u044E \u0441\u043E\u0441\u0442\u043E\u044F\u043D\u0438\u0435 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438 \u2116${id} \u043F\u043E\u0441\u043B\u0435 \u043F\u043E\u0442\u0435\u0440\u0438 \u043E\u0442\u0432\u0435\u0442\u0430\u2026`);
    }
    for (; ; ) {
      await new Promise((r) => setTimeout(r, 700));
      let j;
      try {
        j = await local("/job", { id });
      } catch (error) {
        const message = String(error.message || error);
        if (/нет такой задачи|задача не найдена|ID задачи относится|другому контуру|другому.*запросу/i.test(message))
          throw new Fail(`\u041E\u043F\u0435\u0440\u0430\u0446\u0438\u044F \u2116${id}: ${message}. \u0421\u0432\u0435\u0440\u044C\u0442\u0435 \u0436\u0443\u0440\u043D\u0430\u043B \u0437\u0430\u0434\u0430\u0447 \u043F\u0435\u0440\u0435\u0434 \u043F\u043E\u0432\u0442\u043E\u0440\u043D\u044B\u043C \u0437\u0430\u043F\u0443\u0441\u043A\u043E\u043C.`);
        log?.(`\u0412\u043E\u0441\u0441\u0442\u0430\u043D\u0430\u0432\u043B\u0438\u0432\u0430\u044E \u0441\u0432\u044F\u0437\u044C \u0441 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0435\u0439 \u2116${id}. \u041F\u043E\u0432\u0442\u043E\u0440\u043D\u044B\u0439 \u0437\u0430\u043F\u0443\u0441\u043A \u043D\u0435 \u0432\u044B\u043F\u043E\u043B\u043D\u044F\u0435\u0442\u0441\u044F.`);
        continue;
      }
      if (!j || !["running", "done", "waiting", "attention", "failed", "uncertain"].includes(j.state)) {
        log?.(`\u041F\u0440\u043E\u0432\u0435\u0440\u044F\u044E \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438 \u2116${id}\u2026`);
        continue;
      }
      if (log && j.log?.length) log(j.log[j.log.length - 1]);
      if (["done", "waiting", "attention"].includes(j.state)) return j.result;
      if (["failed", "uncertain"].includes(j.state)) throw new Fail(`\u041E\u043F\u0435\u0440\u0430\u0446\u0438\u044F \u2116${id}: ${j.error || j.state}`);
    }
  }
  function guard(f) {
    return async (...a) => {
      try {
        await f(...a);
      } catch (e) {
        toast(String(e.message || e));
      }
    };
  }
  function tableHtml(cols, rows, opts = {}) {
    const num2 = opts.numeric ?? /* @__PURE__ */ new Set();
    const th = cols.map(([k, t]) => `<th class="${num2.has(k) ? "" : "l"}" data-col="${esc7(k)}">${esc7(t)}</th>`).join("");
    const body = rows.map((r) => {
      const key2 = String(r[opts.key ?? 0] ?? "");
      const tds = cols.map(([k], i) => `<td class="${num2.has(k) ? "" : "l"}">${esc7(r[i] ?? "")}</td>`).join("");
      const box = opts.check ? `<td class="l" style="width:28px"><input type="checkbox" data-check="${esc7(key2)}" ${opts.check.has(key2) ? "checked" : ""}></td>` : "";
      return `<tr class="${opts.pick ? "pick" : ""}" data-key="${esc7(key2)}"${opts.selected === key2 ? ' aria-selected="true"' : ""}>${box}${tds}</tr>`;
    }).join("");
    return `<div class="table-wrap"><table class="grid"><thead><tr>${opts.check ? "<th></th>" : ""}${th}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function kvHtml(pairs2) {
    return `<div class="kpis">${pairs2.map(([k, v]) => `<div class="kpi"><b>${esc7(v)}</b><span>${esc7(k)}</span></div>`).join("")}</div>`;
  }
  function chipsHtml(items, attr, toggle = false) {
    return `<div class="chips">${items.map((c) => `<button class="chip${toggle ? " toggle" : ""}" data-${attr}="${esc7(c.key)}" aria-pressed="${c.on}">${esc7(c.text)}</button>`).join("")}</div>`;
  }
  function note(text2) {
    return text2 ? `<p class="muted" style="font-size:13.5px;max-width:880px;margin:6px 0 0;line-height:1.55;white-space:pre-line">${esc7(text2)}</p>` : "";
  }
  async function fnMap(name, list) {
    const r = await hard.local("/fn", { name, map: list });
    if (!r.ok) throw new Fail(r.error || name);
    return r.data;
  }
  var listeners3, Fail;
  var init_lib = __esm({
    "hard-desk/ui/analyst/lib.ts"() {
      "use strict";
      init_api();
      init_dom();
      listeners3 = /* @__PURE__ */ new Set();
      Fail = class extends Error {
      };
    }
  });

  // hard-desk/ui/analyst/people.ts
  async function activityTab(root) {
    const d = await adm("GET", "/admin/activity");
    workers = d.workers || [];
    const latest = await fn("activity.latest_shell", workers);
    const [counts, FILTERS, COLS, summary] = await Promise.all([
      fn("activity.filter_counts", workers),
      fn("activity.FILTERS"),
      fn("activity.COLUMNS"),
      fn("activity.summary_text", workers)
    ]);
    const shown = await fnMap("activity.matches", workers.map((w) => [w, st.filter, latest]));
    const rows = await fnMap("activity.activity_row", workers.filter((_, i) => shown[i]).map((w) => [w]));
    const numeric = /* @__PURE__ */ new Set(["persons", "contracts", "today", "v7", "v30", "m7", "ph7", "idle", "shell"]);
    root.querySelector("#tab").innerHTML = `${note(summary)}
    <div class="toolbar" style="margin-top:14px">${chipsHtml(FILTERS.map(([k, t]) => ({ key: k, text: `${t} \xB7 ${counts[k] || 0}`, on: st.filter === k })), "filter")}
</div>
    ${st.selected ? `<div class="notice"><span class="grow"><b>${esc7(st.selected)}</b></span>
      <button class="btn small" data-link>\u041F\u0440\u043E\u0444\u0438\u043B\u044C \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430</button><button class="btn small" data-unassign>\u0421\u043D\u044F\u0442\u044C \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432\u2026</button></div>` : ""}
    <div id="tbl"></div>`;
    const tab2 = root.querySelector("#tab");
    tab2.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
      st.filter = b.dataset.filter;
      void activityTab(root);
    }));
    new DataTable(tab2.querySelector("#tbl"), {
      id: "analyst-activity",
      cols: tupleCols(COLS, numeric),
      key: (r) => String(r[0]),
      exportName: "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438 \u2014 \u0430\u043A\u0442\u0438\u0432\u043D\u043E\u0441\u0442\u044C",
      onRow: (r) => {
        st.selected = String(r[0]);
        void activityTab(root);
      },
      rowClass: (r) => String(r[0]) === st.selected ? "is-picked" : ""
    }).setRows(rows);
    tab2.querySelector("[data-link]")?.addEventListener("click", guard(() => issueLink({ employee: st.selected })));
    tab2.querySelector("[data-unassign]")?.addEventListener("click", () => {
      st.un = st.selected;
      st.tab = "unassign";
      void draw(root);
    });
  }
  async function rolesTab(root) {
    const [emps, offices] = await Promise.all([adm("GET", "/admin/employees"), adm("GET", "/admin/employee_cities.json").catch(() => ({}))]);
    const list = emps.employees || [];
    const cities = offices.cities || {};
    const known = offices.known || [];
    const tab2 = root.querySelector("#tab");
    tab2.innerHTML = `${note("\xAB\u041E\u0444\u0438\u0441\xBB \u2014 \u0433\u0434\u0435 \u0441\u0438\u0434\u0438\u0442 \u0441\u0430\u043C \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A (\u0414\u0443\u0448\u0430\u043D\u0431\u0435, \u0425\u0443\u0434\u0436\u0430\u043D\u0434, \u0422\u0430\u0448\u043A\u0435\u043D\u0442\u2026): \u043F\u043E \u043D\u0435\u043C\u0443 \u0441\u0447\u0438\u0442\u0430\u0435\u0442\u0441\u044F \u0448\u0442\u0430\u0442 \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044F \u0433\u043E\u0440\u043E\u0434\u0430. \xAB\u041D\u043E\u0440\u043C\u0430\xBB \u2014 \u0446\u0435\u043B\u043E\u0435 \u0447\u0438\u0441\u043B\u043E \u0432\u044B\u0435\u0437\u0434\u043E\u0432 \u0432 \u0434\u0435\u043D\u044C \u043E\u0442 1 \u0434\u043E 100; \u043F\u0443\u0441\u0442\u043E\u0435 \u043F\u043E\u043B\u0435 \u0432\u043E\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442 \u043E\u0431\u0449\u0443\u044E \u043D\u043E\u0440\u043C\u0443. \u0421\u043E\u0445\u0440\u0430\u043D\u044F\u0435\u0442\u0441\u044F \u043F\u043E\u0441\u043B\u0435 \u0432\u0432\u043E\u0434\u0430.")}
    <datalist id="offices">${known.map((k) => `<option value="${esc7(k)}">`).join("")}</datalist>
    <div id="roles-tbl" style="margin-top:10px"></div>`;
    const cols = [
      { key: "emp", title: "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A", value: (e) => e.employee, html: (e) => `<span class="who">${esc7(e.employee)}</span>` },
      { key: "contracts", title: "\u0414\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432", num: true, value: (e) => e.contracts ?? 0 },
      {
        key: "office",
        title: "\u041E\u0444\u0438\u0441",
        value: (e) => cities[e.employee] || "",
        html: (e) => `<input class="input" list="offices" data-office value="${esc7(cities[e.employee] || "")}" placeholder="\u043D\u0435 \u0437\u0430\u0434\u0430\u043D" style="width:150px;height:32px">`
      },
      {
        key: "plan",
        title: "\u041D\u043E\u0440\u043C\u0430",
        value: (e) => e.plan_personal ? String(e.plan) : "\u043E\u0431\u0449\u0430\u044F",
        html: (e) => `<input class="input" type="number" min="1" max="100" step="1" inputmode="numeric" data-plan value="${e.plan_personal ? esc7(e.plan) : ""}" placeholder="\u043E\u0431\u0449\u0430\u044F ${esc7(emps.plan_common || 10)}" aria-label="\u041D\u043E\u0440\u043C\u0430 \u0432\u044B\u0435\u0437\u0434\u043E\u0432: ${esc7(e.employee)}" style="width:115px;height:32px">`
      },
      {
        key: "kind",
        title: "\u0422\u0438\u043F",
        value: (e) => e.kind === "courier" ? "\u041A\u0443\u0440\u044C\u0435\u0440 \u0413\u041F\u0425" : "\u0412\u044B\u0435\u0437\u0434\u043D\u0438\u043A",
        html: (e) => `<select class="input" data-kind style="height:32px"><option value="agent">\u0412\u044B\u0435\u0437\u0434\u043D\u0438\u043A</option><option value="courier" ${e.kind === "courier" ? "selected" : ""}>\u041A\u0443\u0440\u044C\u0435\u0440 \u0413\u041F\u0425</option></select>`
      },
      {
        key: "bound",
        title: "\u0412\u0445\u043E\u0434",
        value: (e) => e.telegram_id ? "\u043F\u0440\u0438\u0432\u044F\u0437\u0430\u043D" : "\u043D\u0435 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D",
        html: (e) => e.telegram_id ? '<span class="tag ok">\u043F\u0440\u0438\u0432\u044F\u0437\u0430\u043D</span>' : '<span class="tag">\u043D\u0435 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D</span>'
      },
      { key: "act", title: "", filter: false, value: () => "", html: () => '<button class="btn small" data-link>\u041F\u0440\u043E\u0444\u0438\u043B\u044C \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430</button>' }
    ];
    new DataTable(tab2.querySelector("#roles-tbl"), {
      id: "analyst-roles",
      cols,
      key: (e) => e.employee,
      exportName: "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438 \u2014 \u043E\u0444\u0438\u0441\u044B \u0438 \u043D\u043E\u0440\u043C\u044B",
      afterRender: (host) => host.querySelectorAll("tr[data-key]").forEach((tr) => {
        const emp = tr.dataset.key;
        const office = tr.querySelector("[data-office]");
        office.addEventListener("change", guard(async () => {
          const city = office.value.trim();
          await adm("POST", "/admin/employee_cities.json", { cities: { [emp]: city } });
          cities[emp] = city;
          toast(city ? `${emp}: \u043E\u0444\u0438\u0441 ${city}` : `${emp}: \u043E\u0444\u0438\u0441 \u0441\u043D\u044F\u0442`);
        }));
        const plan = tr.querySelector("[data-plan]");
        plan.addEventListener("change", guard(async () => {
          if (!plan.reportValidity()) return;
          const value = plan.value === "" ? 0 : Number(plan.value);
          plan.disabled = true;
          try {
            await adm("POST", "/admin/visit_plan.json", { plan: { [emp]: value } });
            const row = list.find((e) => e.employee === emp);
            if (row) {
              row.plan_personal = value > 0;
              row.plan = value || emps.plan_common || 10;
            }
            toast(value ? `${emp}: \u043D\u043E\u0440\u043C\u0430 ${value} \u0432 \u0434\u0435\u043D\u044C` : `${emp}: \u043D\u043E\u0440\u043C\u0430 \u043E\u0431\u0449\u0430\u044F`);
          } finally {
            plan.disabled = false;
          }
        }));
        tr.querySelector("[data-link]").addEventListener("click", guard(() => issueLink({ employee: emp, kind: tr.querySelector("[data-kind]").value })));
      })
    }).setRows(list);
  }
  async function unassignTab(root) {
    const emps = (await adm("GET", "/admin/employees")).employees || [];
    const tab2 = root.querySelector("#tab");
    tab2.innerHTML = `${note("\u0414\u043E\u0433\u043E\u0432\u043E\u0440 \u043E\u0441\u0442\u0430\u0451\u0442\u0441\u044F \u0432 \u0440\u0435\u0435\u0441\u0442\u0440\u0435 \u0431\u0435\u0437 \u043E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u043E\u0433\u043E \u0438 \u043F\u0440\u043E\u043F\u0430\u0434\u0430\u0435\u0442 \u0438\u0437 \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u044F \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430. \u041E\u0442\u043C\u0435\u0442\u044C\u0442\u0435 \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u044B \u0433\u0430\u043B\u043E\u0447\u043A\u0430\u043C\u0438 \u2014 \u0444\u0438\u043B\u044C\u0442\u0440\u044B \u043F\u043E\u043C\u043E\u0433\u0443\u0442 \u043E\u0442\u043E\u0431\u0440\u0430\u0442\u044C \u043D\u0443\u0436\u043D\u044B\u0445.")}
    <div class="toolbar" style="margin-top:12px"><select class="input" id="un-emp" style="width:340px"><option value="">\u2014 \u0432\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430 \u2014</option>
      ${emps.map((e) => `<option ${e.employee === st.un ? "selected" : ""}>${esc7(e.employee)}</option>`).join("")}</select>
      <span class="spacer"></span><button class="btn danger" id="un-go" disabled>\u0421\u043D\u044F\u0442\u044C \u043E\u0442\u043C\u0435\u0447\u0435\u043D\u043D\u044B\u0445</button></div>
    <div id="un-list"></div>`;
    const go3 = tab2.querySelector("#un-go");
    const cols = [
      { key: "client", title: "\u0414\u043E\u043B\u0436\u043D\u0438\u043A", value: (c) => c.client || "\u2014" },
      { key: "cid", title: "\u0414\u043E\u0433\u043E\u0432\u043E\u0440", value: (c) => c.contract_id, html: (c) => `<span class="mono">${esc7(c.contract_id)}</span>` },
      { key: "debt", title: "\u0414\u043E\u043B\u0433", num: true, value: (c) => Number(c.debt) || 0, html: (c) => `${esc7(n(c.debt))} ${esc7(c.unit || "")}` }
    ];
    const table2 = new DataTable(tab2.querySelector("#un-list"), {
      id: "analyst-unassign",
      cols,
      key: (c) => c.contract_id,
      select: true,
      exportName: "\u0414\u043E\u0433\u043E\u0432\u043E\u0440\u044B \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430",
      empty: st.un ? "\u0417\u0430 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u043C \u0441\u0435\u0439\u0447\u0430\u0441 \u043D\u0438\u043A\u043E\u0433\u043E \u043D\u0435\u0442." : "\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430.",
      onSelect: (keys) => {
        go3.disabled = !keys.size;
        go3.textContent = keys.size ? `\u0421\u043D\u044F\u0442\u044C \u043E\u0442\u043C\u0435\u0447\u0435\u043D\u043D\u044B\u0445 (${keys.size})` : "\u0421\u043D\u044F\u0442\u044C \u043E\u0442\u043C\u0435\u0447\u0435\u043D\u043D\u044B\u0445";
      }
    });
    const pick = async () => {
      table2.clearSelection();
      table2.setRows(st.un ? (await adm("GET", "/admin/emp_clients?employee=" + encodeURIComponent(st.un))).clients || [] : []);
    };
    go3.addEventListener("click", guard(async () => {
      const ids = table2.selected().map((c) => c.contract_id);
      if (!ids.length) return;
      if (!await ask("\u0423\u0431\u0440\u0430\u0442\u044C \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432", await fn("people.unassign_text", ids.length, st.un), "\u0421\u043D\u044F\u0442\u044C", true)) return;
      const d = await adm("POST", "/admin/unassign", { employee: st.un, contract_ids: ids });
      toast(`\u0421\u043D\u044F\u0442\u043E: ${d.n || 0}`);
      await pick();
    }));
    tab2.querySelector("#un-emp").addEventListener("change", (e) => {
      st.un = e.target.value;
      void pick();
    });
    await pick();
  }
  async function draw(root) {
    root.innerHTML = `<div class="toolbar">${chipsHtml([
      { key: "activity", text: "\u0410\u043A\u0442\u0438\u0432\u043D\u043E\u0441\u0442\u044C \u0438 \u0432\u0445\u043E\u0434", on: st.tab === "activity" },
      { key: "roles", text: "\u041E\u0444\u0438\u0441, \u043D\u043E\u0440\u043C\u0430, \u0442\u0438\u043F", on: st.tab === "roles" },
      { key: "unassign", text: "\u0421\u043D\u044F\u0442\u044C \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432", on: st.tab === "unassign" }
    ], "tab")}</div>
    <div id="tab"><div class="skeleton"></div></div>`;
    root.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
      st.tab = b.dataset.tab;
      void draw(root);
    }));
    try {
      if (st.tab === "roles") await rolesTab(root);
      else if (st.tab === "unassign") await unassignTab(root);
      else await activityTab(root);
    } catch (e) {
      root.querySelector("#tab").innerHTML = `<div class="notice err">${esc7(e.message)}</div>`;
    }
  }
  var st, workers, people;
  var init_people = __esm({
    "hard-desk/ui/analyst/people.ts"() {
      "use strict";
      init_dom();
      init_links();
      init_table();
      init_lib();
      st = { tab: "activity", filter: "all", selected: "", un: "" };
      workers = [];
      people = {
        id: "people",
        title: "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438",
        icon: "people",
        kicker: "\u0430\u043A\u0442\u0438\u0432\u043D\u043E\u0441\u0442\u044C, \u043E\u0444\u0438\u0441\u044B, \u043D\u043E\u0440\u043C\u044B, \u043A\u043B\u0438\u0435\u043D\u0442\u044B",
        render(root) {
          return draw(root);
        }
      };
    }
  });

  // hard-desk/ui/analyst/managers.ts
  async function load(root) {
    const [d, offices] = await Promise.all([adm("GET", "/admin/managers"), adm("GET", "/admin/employee_cities.json")]);
    data = d;
    staff = {};
    for (const [employee, city] of Object.entries(offices.cities || {})) (staff[String(city)] ||= []).push(employee);
    root.innerHTML = note("\u041E\u0431\u043B\u0430\u0441\u0442\u0438 \u0434\u043E\u0441\u0442\u0443\u043F\u0430 \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u0435\u0439 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u044E\u0442\u0441\u044F. \u041B\u043E\u0433\u0438\u043D\u044B, \u043F\u0430\u0440\u043E\u043B\u0438, \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F \u0438 \u043E\u0442\u0437\u044B\u0432 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432 \u2014 \u0432 \u0440\u0430\u0437\u0434\u0435\u043B\u0435 \xAB\u041F\u0440\u043E\u0444\u0438\u043B\u0438 \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430\xBB.") + '<div class="toolbar"><button class="btn" data-new>\u0414\u043E\u0431\u0430\u0432\u0438\u0442\u044C \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044F</button><button class="btn" data-profiles>\u041F\u0440\u043E\u0444\u0438\u043B\u0438 \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430</button></div><div id="mgr-table"></div>';
    const rows = await Promise.all((d.managers || []).map(async (m) => ({ m, row: await fn("managers.manager_row", m) })));
    const cols = [
      { key: "name", title: "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C", value: (r) => r.m.name },
      { key: "role", title: "\u0420\u043E\u043B\u044C", value: (r) => ROLE_TITLE[r.m.role || "manager"] || r.m.role },
      { key: "scope", title: "\u041A\u043E\u043C\u0430\u043D\u0434\u0430", value: (r) => r.row.employees },
      { key: "cities", title: "\u041E\u0444\u0438\u0441\u044B", value: (r) => r.row.cities }
    ];
    new DataTable(root.querySelector("#mgr-table"), { id: "managers", cols, key: (r) => String(r.m.telegram_id), exportName: "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u0438", onRow: (r) => edit(root, r.m, String(r.m.telegram_id), []) }).setRows(rows);
    root.querySelector("[data-new]")?.addEventListener("click", guard(async () => edit(root, null, String(d.next_local_id || await fn("managers.next_local_manager_id", d.managers || [])), [])));
    root.querySelector("[data-profiles]")?.addEventListener("click", () => void go("accounts"));
  }
  function chipList(items, on, attr, count) {
    return `<div class="chips" style="gap:6px">${items.map((x) => `<button class="chip toggle" data-${attr}="${esc7(x)}" aria-pressed="${on.has(x)}">${esc7(x)}${count && count(x) ? `<span class="n">${n(count(x))}</span>` : ""}</button>`).join("")}</div>`;
  }
  function edit(root, m, tid, devices, link = null) {
    const form = {
      name: m?.name || "",
      role: m?.role || "manager",
      direction: m?.direction || "",
      offices: new Set(m?.office_cities || []),
      employees: new Set(m?.employees || []),
      cities: new Set(m?.cities || [])
    };
    const officeList = [.../* @__PURE__ */ new Set([...Object.keys(staff), ...form.offices])].sort();
    const empList = (data.employees || []).map((e) => e.employee);
    const empCount = (x) => (data.employees || []).find((e) => e.employee === x)?.contracts || 0;
    const draw3 = () => {
      const body = openPanel(
        `<h2>${esc7(m ? m.name || "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C" : "\u041D\u043E\u0432\u044B\u0439 \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C")}</h2><div class="sub">id ${esc7(tid)}</div>`,
        `<div class="block" style="margin-top:6px"><label class="muted" style="font-size:13px">\u0418\u043C\u044F</label><br>
        <input class="input" id="mg-name" value="${esc7(form.name)}" style="width:100%;margin-top:4px"></div>
      <div class="block"><h3>\u0420\u043E\u043B\u044C</h3>${ROLES.map(([k, t, h]) => `<label class="list-row" style="cursor:pointer;padding:8px 4px">
        <input type="radio" name="mg-role" value="${k}" ${form.role === k ? "checked" : ""}><span class="grow"><span class="nm">${esc7(t)}</span><br><span class="sub">${esc7(h)}</span></span></label>`).join("")}</div>
      <div class="block"><h3>\u041A\u043E\u0433\u043E \u0432\u0438\u0434\u0438\u0442</h3>
        <select class="input" id="mg-dir" style="width:100%">${[["", "\u2014 \u043F\u043E \u043E\u0444\u0438\u0441\u0430\u043C \u0438 \u0441\u043F\u0438\u0441\u043A\u0443 \u2014"], ["tj", "\u0422\u0430\u0434\u0436\u0438\u043A\u0438\u0441\u0442\u0430\u043D"], ["uz", "\u0423\u0437\u0431\u0435\u043A\u0438\u0441\u0442\u0430\u043D"], ["all", "\u0421\u041D\u0413 (\u0432\u0441\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438)"]].map(([k, t]) => `<option value="${k}" ${form.direction === k ? "selected" : ""}>${esc7(t)}</option>`).join("")}</select>
        ${form.direction ? `<p class="muted" style="font-size:13px">\u0421\u043E\u0441\u0442\u0430\u0432 \u043D\u0430\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0438\u044F \u0441\u0435\u0440\u0432\u0435\u0440 \u0441\u0447\u0438\u0442\u0430\u0435\u0442 \u0441\u0430\u043C \u2014 \u043E\u0442\u043C\u0435\u0447\u0430\u0442\u044C \u043B\u044E\u0434\u0435\u0439 \u043D\u0435 \u043D\u0443\u0436\u043D\u043E.</p>` : `
        <p class="muted" style="font-size:13px;margin:12px 0 6px">\u041E\u0444\u0438\u0441\u044B (\u0448\u0442\u0430\u0442 \u0436\u0438\u0432\u043E\u0439):</p>${chipList(officeList, form.offices, "office", (x) => (staff[x] || []).length)}
        <p class="muted" style="font-size:13px;margin:12px 0 6px">\u0418 \u043F\u043E\u0438\u043C\u0451\u043D\u043D\u043E:</p>${chipList(empList, form.employees, "emp", empCount)}`}</div>
      <p class="muted">\u041B\u0438\u0447\u043D\u044B\u0439 \u043F\u0440\u043E\u0444\u0438\u043B\u044C \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430 \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B \u0432 \u0440\u0430\u0437\u0434\u0435\u043B\u0435 \xAB\u041F\u0440\u043E\u0444\u0438\u043B\u0438 \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430\xBB.</p>
      <div class="toolbar" style="margin-top:22px"><button class="btn primary" data-save>\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C</button>
        ${m ? '<span class="spacer"></span><button class="btn danger" data-del>\u0423\u0434\u0430\u043B\u0438\u0442\u044C</button>' : ""}</div>`
      );
      body.querySelector("#mg-name").addEventListener("input", (e) => {
        form.name = e.target.value;
      });
      body.querySelectorAll("[data-office]").forEach((b) => b.addEventListener("click", () => {
        const x = b.dataset.office;
        if (form.offices.has(x)) form.offices.delete(x);
        else form.offices.add(x);
        b.setAttribute("aria-pressed", String(form.offices.has(x)));
      }));
      body.querySelectorAll("[data-emp]").forEach((b) => b.addEventListener("click", () => {
        const x = b.dataset.emp;
        if (form.employees.has(x)) form.employees.delete(x);
        else form.employees.add(x);
        b.setAttribute("aria-pressed", String(form.employees.has(x)));
      }));
      body.querySelector("[data-save]")?.addEventListener("click", guard(async () => {
        if (!form.name.trim()) {
          toast("\u0412\u043F\u0438\u0448\u0438\u0442\u0435 \u0438\u043C\u044F");
          return;
        }
        const b = await fn(
          "managers.manager_body",
          tid,
          form.name,
          [...form.cities],
          form.direction ? [] : [...form.employees],
          form.direction,
          m ? { push_visits: m.push_visits, push_digest: m.push_digest, push_promises: m.push_promises } : null,
          form.direction ? [] : [...form.offices]
        );
        await adm("POST", "/admin/manager", { ...b, role: form.role });
        toast("\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E");
        closePanel();
        await load(root);
      }));
      body.querySelector("[data-del]")?.addEventListener("click", guard(async () => {
        if (!await ask("\u0423\u0434\u0430\u043B\u0438\u0442\u044C \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044F?", await fn("managers.delete_text", tid) + " \u0415\u0433\u043E \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u044B \u0441 \u043F\u0443\u043B\u044C\u0442\u043E\u043C \u0442\u043E\u0436\u0435 \u0431\u0443\u0434\u0443\u0442 \u043E\u0442\u043A\u043B\u044E\u0447\u0435\u043D\u044B.", "\u0423\u0434\u0430\u043B\u0438\u0442\u044C", true)) return;
        await adm("POST", "/admin/manager_delete", { telegram_id: Number(tid) });
        toast("\u0423\u0434\u0430\u043B\u0451\u043D");
        closePanel();
        await load(root);
      }));
    };
    draw3();
  }
  var ROLES, ROLE_TITLE, data, staff, managers;
  var init_managers = __esm({
    "hard-desk/ui/analyst/managers.ts"() {
      "use strict";
      init_dom();
      init_shell();
      init_table();
      init_lib();
      ROLES = [["manager", "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C", "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u0435\u0442 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432 \u0441\u0432\u043E\u0435\u0439 \u043A\u043E\u043C\u0430\u043D\u0434\u044B"], ["security", "\u0421\u043B\u0443\u0436\u0431\u0430 \u0431\u0435\u0437\u043E\u043F\u0430\u0441\u043D\u043E\u0441\u0442\u0438", "\u0427\u0438\u0442\u0430\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0435 \u043A\u043E\u043C\u0430\u043D\u0434\u044B \u0438 \u043A\u043E\u043D\u0442\u0440\u043E\u043B\u044C"], ["full", "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C \u0438 \u043A\u043E\u043D\u0442\u0440\u043E\u043B\u044C", "\u0414\u043E\u0441\u0442\u0443\u043F \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044F \u0438 \u0441\u043B\u0443\u0436\u0431\u044B \u0431\u0435\u0437\u043E\u043F\u0430\u0441\u043D\u043E\u0441\u0442\u0438"]];
      ROLE_TITLE = Object.fromEntries(ROLES.map(([k, t]) => [k, t]));
      data = null;
      staff = {};
      managers = {
        id: "mgrs",
        title: "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u0438",
        icon: "managers",
        kicker: "\u0420\u043E\u043B\u0438, \u043E\u0444\u0438\u0441\u044B \u0438 \u0441\u043E\u0441\u0442\u0430\u0432 \u043A\u043E\u043C\u0430\u043D\u0434",
        async render(root) {
          root.innerHTML = '<div class="skeleton"></div>';
          try {
            await load(root);
          } catch (e) {
            root.innerHTML = `<div class="notice err">${esc7(e.message)}</div>`;
          }
        }
      };
    }
  });

  // hard-desk/ui/analyst/access-management.ts
  async function credentialFile(username, password, employee = "") {
    const name = (employee || username).replace(/[<>:"/\\|?*\x00-\x1f]/g, "_");
    const result2 = await hard.saveText(`\u0412\u0445\u043E\u0434 \u2014 ${name}.txt`, `${employee}
\u0412\u0445\u043E\u0434 \u0432 \u043F\u0440\u043E\u0444\u0438\u043B\u044C
https://hard.rimborso.ru
\u041B\u043E\u0433\u0438\u043D: ${username}
\u041F\u0430\u0440\u043E\u043B\u044C: ${password}

\u0418\u0441\u043F\u043E\u043B\u044C\u0437\u0443\u0439\u0442\u0435 \u044D\u0442\u0438 \u0434\u0430\u043D\u043D\u044B\u0435 \u0432 \u0441\u0432\u043E\u0435\u0439 \u043F\u0440\u043E\u0433\u0440\u0430\u043C\u043C\u0435 \u0438 \u043F\u043E \u043E\u0431\u0449\u0435\u0439 \u0441\u0441\u044B\u043B\u043A\u0435 \u0441 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0430. \u041F\u0440\u0438 \u043F\u0435\u0440\u0432\u043E\u043C \u0432\u0445\u043E\u0434\u0435 \u0441\u043C\u0435\u043D\u0438\u0442\u0435 \u0432\u044B\u0434\u0430\u043D\u043D\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C.
`);
    toast(result2.ok ? "\u041B\u0438\u0447\u043D\u044B\u0439 \u0444\u0430\u0439\u043B \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D." : result2.text || "\u0424\u0430\u0439\u043B \u043D\u0435 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D. \u0420\u0435\u043A\u0432\u0438\u0437\u0438\u0442\u044B \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B \u0434\u043E \u0437\u0430\u043A\u0440\u044B\u0442\u0438\u044F \u044D\u0442\u043E\u0433\u043E \u0440\u0430\u0437\u0434\u0435\u043B\u0430.");
  }
  function mountAccountActions(box, account, reload) {
    const bar = document.createElement("div");
    bar.className = "toolbar";
    if (account.owner_root) {
      bar.innerHTML = '<span class="muted">\u041F\u0435\u0440\u0432\u0438\u0447\u043D\u044B\u0439 \u043F\u0440\u043E\u0444\u0438\u043B\u044C \u0432\u043B\u0430\u0434\u0435\u043B\u044C\u0446\u0430</span><button class="btn">\u0421\u043C\u0435\u043D\u0438\u0442\u044C \u0441\u0432\u043E\u0439 \u043F\u0430\u0440\u043E\u043B\u044C \u0438\u043B\u0438 \u0432\u043E\u0441\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F</button>';
      bar.querySelector("button").onclick = () => void go("conn");
      box.prepend(bar);
      return;
    }
    bar.innerHTML = `<button class="btn" data-reset>\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C \u043F\u0430\u0440\u043E\u043B\u044C</button><button class="btn ${account.status === "disabled" ? "" : "danger"}" data-status>${account.status === "disabled" ? "\u0412\u043E\u0441\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F" : "\u0417\u0430\u043A\u0440\u044B\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F"}</button><button class="btn danger" data-delete>\u0423\u0434\u0430\u043B\u0438\u0442\u044C \u0430\u043A\u043A\u0430\u0443\u043D\u0442</button><span data-credential></span>`;
    box.prepend(bar);
    bar.querySelector("[data-reset]")?.addEventListener("click", async () => {
      if (!await ask("\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C \u043F\u0430\u0440\u043E\u043B\u044C", `\u0412\u0441\u0435 \u0442\u0435\u043A\u0443\u0449\u0438\u0435 \u0432\u0445\u043E\u0434\u044B ${account.employee} \u0431\u0443\u0434\u0443\u0442 \u0437\u0430\u043A\u0440\u044B\u0442\u044B. \u041F\u0440\u0438 \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0435 \u043E\u0442\u0437\u044B\u0432\u0430 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430 \u043E\u0447\u0438\u0441\u0442\u044F\u0442 \u043B\u043E\u043A\u0430\u043B\u044C\u043D\u044B\u0439 \u043F\u0440\u043E\u0444\u0438\u043B\u044C \u0438 \u043D\u0435\u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u043D\u0443\u044E \u0440\u0430\u0431\u043E\u0442\u0443. \u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u0435 \u043D\u043E\u0432\u044B\u0439 \u043B\u0438\u0447\u043D\u044B\u0439 \u0444\u0430\u0439\u043B \u0438 \u043F\u0435\u0440\u0435\u0434\u0430\u0439\u0442\u0435 \u0435\u0433\u043E \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0443.`, "\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C")) return;
      try {
        const result2 = await adm("POST", "/admin/account_action.json", { action: "reset", username: account.username });
        const button2 = document.createElement("button");
        button2.className = "btn primary";
        button2.textContent = "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043B\u0438\u0447\u043D\u044B\u0439 \u0444\u0430\u0439\u043B";
        button2.onclick = () => void credentialFile(account.username, result2.temporary_password, account.employee);
        bar.querySelector("[data-credential]").replaceChildren(button2);
        await credentialFile(account.username, result2.temporary_password, account.employee);
      } catch (e) {
        toast(e.message);
      }
    });
    bar.querySelector("[data-delete]")?.addEventListener("click", async () => {
      if (!await ask("\u0423\u0434\u0430\u043B\u0438\u0442\u044C \u0430\u043A\u043A\u0430\u0443\u043D\u0442", `\u0423\u0434\u0430\u043B\u0438\u0442\u044C \u0430\u043A\u043A\u0430\u0443\u043D\u0442 ${account.employee} (${account.username})? \u041B\u043E\u0433\u0438\u043D, \u043F\u0430\u0440\u043E\u043B\u044C \u0438 \u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432 \u0431\u0443\u0434\u0443\u0442 \u0443\u0434\u0430\u043B\u0435\u043D\u044B. \u0412\u0441\u0435 \u0432\u0445\u043E\u0434\u044B \u0437\u0430\u043A\u0440\u043E\u044E\u0442\u0441\u044F; \u043F\u0440\u0438 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430 \u043E\u0447\u0438\u0441\u0442\u044F\u0442 \u043B\u043E\u043A\u0430\u043B\u044C\u043D\u044B\u0435 \u0434\u0430\u043D\u043D\u044B\u0435, \u0432\u043A\u043B\u044E\u0447\u0430\u044F \u043D\u0435\u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u043D\u0443\u044E \u0440\u0430\u0431\u043E\u0442\u0443. \u041E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u043D\u044B\u0435 \u0440\u0430\u0431\u043E\u0447\u0438\u0435 \u0437\u0430\u043F\u0438\u0441\u0438 \u0438 \u0436\u0443\u0440\u043D\u0430\u043B \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0442\u0441\u044F. \u0414\u043B\u044F \u0432\u043E\u0437\u0432\u0440\u0430\u0442\u0430 \u043F\u043E\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044F \u043D\u043E\u0432\u0430\u044F \u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044F.`, "\u0423\u0434\u0430\u043B\u0438\u0442\u044C \u0430\u043A\u043A\u0430\u0443\u043D\u0442")) return;
      const button2 = bar.querySelector("[data-delete]");
      button2.disabled = true;
      try {
        await adm("POST", "/admin/account_action.json", { action: "delete", username: account.username, confirm_username: account.username, account_id: account.id });
        toast("\u0410\u043A\u043A\u0430\u0443\u043D\u0442 \u0443\u0434\u0430\u043B\u0451\u043D. \u0412\u0441\u0435 \u0435\u0433\u043E \u0432\u0445\u043E\u0434\u044B \u043E\u0442\u043E\u0437\u0432\u0430\u043D\u044B.");
        await reload();
      } catch (e) {
        toast(e.message);
      } finally {
        if (button2.isConnected) button2.disabled = false;
      }
    });
    bar.querySelector("[data-status]")?.addEventListener("click", async () => {
      if (!await ask(account.status === "disabled" ? "\u0412\u043E\u0441\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F" : "\u0417\u0430\u043A\u0440\u044B\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F", account.status === "disabled" ? `\u041F\u0440\u043E\u0444\u0438\u043B\u044C ${account.employee} \u0432\u0435\u0440\u043D\u0451\u0442\u0441\u044F \u043D\u0430 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435.` : `\u0422\u0435\u043A\u0443\u0449\u0438\u0435 \u0432\u0445\u043E\u0434\u044B ${account.employee} \u0431\u0443\u0434\u0443\u0442 \u043E\u0442\u043E\u0437\u0432\u0430\u043D\u044B.`, "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C")) return;
      try {
        await adm("POST", "/admin/account_action.json", { action: account.status === "disabled" ? "enable" : "disable", username: account.username });
        await reload();
      } catch (e) {
        toast(e.message);
      }
    });
    for (const device of account.devices || []) {
      if (device.status === "revoked") continue;
      const b = document.createElement("button");
      b.className = "btn small danger";
      b.textContent = "\u041E\u0442\u043E\u0437\u0432\u0430\u0442\u044C " + device.label;
      b.onclick = async () => {
        if (!await ask("\u041E\u0442\u043E\u0437\u0432\u0430\u0442\u044C \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E", `\u0411\u0443\u0434\u0443\u0442 \u0437\u0430\u043A\u0440\u044B\u0442\u044B \u0432\u0441\u0435 \u0432\u0445\u043E\u0434\u044B \u044D\u0442\u043E\u0433\u043E \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430: ${device.label}. \u041F\u0440\u0438 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0438 \u043E\u043D\u043E \u043E\u0447\u0438\u0441\u0442\u0438\u0442 \u043B\u043E\u043A\u0430\u043B\u044C\u043D\u044B\u0439 \u043F\u0440\u043E\u0444\u0438\u043B\u044C \u0438 \u043D\u0435\u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u043D\u0443\u044E \u0440\u0430\u0431\u043E\u0442\u0443.`, "\u041E\u0442\u043E\u0437\u0432\u0430\u0442\u044C")) return;
        try {
          await adm("POST", "/admin/account_action.json", { action: "revoke", device_id: device.id });
          await reload();
        } catch (e) {
          toast(e.message);
        }
      };
      bar.append(b);
    }
  }
  async function mountAccessPreparation(root, reload) {
    const data2 = await adm("GET", "/admin/staff_roster.json");
    const managers2 = (data2.managers || []).filter((m) => !m.role || ["manager", "full"].includes(m.role));
    const items = (data2.employees || []).map((r) => ({ employee: r.employee, office: r.office, offices: JSON.parse(r.offices || "[]"), product_names: JSON.parse(r.product_names || "{}"), direction: r.direction, products: JSON.parse(r.products), manager_uid: r.manager_uid, active: !!r.active }));
    const section2 = document.createElement("section");
    section2.className = "section";
    section2.innerHTML = `<details><summary>\u0421\u043E\u0437\u0434\u0430\u0442\u044C \u043F\u0440\u043E\u0444\u0438\u043B\u044C \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044F</summary><p class="muted">\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u0443\u044E \u043B\u0438\u0447\u043D\u043E\u0441\u0442\u044C. \u0417\u0430\u043A\u0440\u0435\u043F\u043B\u0435\u043D\u0438\u044F \u043E\u0444\u0438\u0441\u043E\u0432 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u044E\u0442\u0441\u044F. \u041D\u043E\u0432\u044B\u0439 \u043F\u0440\u043E\u0444\u0438\u043B\u044C \u043E\u0436\u0438\u0434\u0430\u0435\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F \u0430\u0434\u043C\u0438\u043D\u0438\u0441\u0442\u0440\u0430\u0442\u043E\u0440\u0430.</p>
    <div class="toolbar"><select class="input" data-manager><option value="">\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C</option>${managers2.map((m) => `<option value="${m.telegram_id}">${esc7(m.name)}</option>`).join("")}</select>
      <input class="input" data-username placeholder="\u041D\u043E\u0432\u044B\u0439 \u043B\u043E\u0433\u0438\u043D"><input class="input" data-manager-name placeholder="\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u043E\u0435 \u043F\u043E\u043B\u043D\u043E\u0435 \u0424\u0418\u041E (\u0435\u0441\u043B\u0438 \u043E\u0442\u043B\u0438\u0447\u0430\u0435\u0442\u0441\u044F)"><button class="btn" data-create>\u0421\u043E\u0437\u0434\u0430\u0442\u044C \u0438 \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043B\u0438\u0447\u043D\u044B\u0439 \u0444\u0430\u0439\u043B</button><span data-create-file></span></div><p class="hint">\u0415\u0441\u043B\u0438 \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C \u0435\u0441\u0442\u044C \u0432 \u0441\u043F\u0440\u0430\u0432\u043E\u0447\u043D\u0438\u043A\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432, \u0443\u043A\u0430\u0436\u0438\u0442\u0435 \u0424\u0418\u041E \u0442\u043E\u0447\u043D\u043E \u043A\u0430\u043A \u0432 \u0441\u043F\u0440\u0430\u0432\u043E\u0447\u043D\u0438\u043A\u0435. \u0415\u0433\u043E \u043E\u0431\u043B\u0430\u0441\u0442\u0438 \u0434\u043E\u0441\u0442\u0443\u043F\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0442\u0441\u044F.</p></details>
    <details style="margin-top:14px"><summary>\u0421\u043F\u0440\u0430\u0432\u043E\u0447\u043D\u0438\u043A \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432 (${items.length})</summary>
      <p class="muted">\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A \u0431\u0435\u0437 \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044F \u0442\u0430\u043A\u0436\u0435 \u043C\u043E\u0436\u0435\u0442 \u0441\u043E\u0437\u0434\u0430\u0442\u044C \u043F\u0440\u043E\u0444\u0438\u043B\u044C. \u0423\u043A\u0430\u0436\u0438\u0442\u0435 \u0435\u0433\u043E \u043E\u0444\u0438\u0441, \u043F\u0440\u043E\u0433\u0440\u0430\u043C\u043C\u044B \u0438 \u0441\u0432\u043E\u0435\u0433\u043E \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044F. \u041E\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u044E\u0449\u0438\u0435 \u0441\u0442\u0440\u043E\u043A\u0438 \u0430\u0432\u0442\u043E\u043C\u0430\u0442\u0438\u0447\u0435\u0441\u043A\u0438 \u043D\u0435 \u0443\u0434\u0430\u043B\u044F\u044E\u0442\u0441\u044F.</p>
      <div class="toolbar"><input class="input" data-employee placeholder="\u0424\u0418\u041E"><input class="input" data-office placeholder="\u041E\u0444\u0438\u0441">
        <select class="input" data-direction><option value="tj">\u0422\u0430\u0434\u0436\u0438\u043A\u0438\u0441\u0442\u0430\u043D</option><option value="uz">\u0423\u0437\u0431\u0435\u043A\u0438\u0441\u0442\u0430\u043D</option></select>
        <select class="input" data-roster-manager><option value="">\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C</option>${managers2.map((m) => `<option value="${m.telegram_id}">${esc7(m.name)}</option>`).join("")}</select></div>
      <div class="toolbar"><label><input type="checkbox" data-hard checked> \u0425\u0430\u0440\u0434</label><label><input type="checkbox" data-soft> \u0421\u043E\u0444\u0442</label>
        <button class="btn" data-roster-preview>\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0438 \u0434\u043E\u0431\u0430\u0432\u0438\u0442\u044C \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430</button></div>
      <div class="toolbar"><input class="input" data-offices placeholder="\u0412\u0441\u0435 \u043E\u0444\u0438\u0441\u044B \u0447\u0435\u0440\u0435\u0437 \u0442\u043E\u0447\u043A\u0443 \u0441 \u0437\u0430\u043F\u044F\u0442\u043E\u0439 (\u043D\u0435\u043E\u0431\u044F\u0437\u0430\u0442\u0435\u043B\u044C\u043D\u043E)">
        <input class="input" data-hard-name placeholder="\u0424\u0418\u041E \u0432 \u0425\u0430\u0440\u0434, \u0435\u0441\u043B\u0438 \u043E\u0442\u043B\u0438\u0447\u0430\u0435\u0442\u0441\u044F"><input class="input" data-soft-name placeholder="\u0424\u0418\u041E \u0432 \u0421\u043E\u0444\u0442, \u0435\u0441\u043B\u0438 \u043E\u0442\u043B\u0438\u0447\u0430\u0435\u0442\u0441\u044F"></div>
      <div data-preview role="status"></div>
      <details><summary>\u0417\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044C \u043F\u043E\u0434\u0433\u043E\u0442\u043E\u0432\u043B\u0435\u043D\u043D\u044B\u0439 \u0441\u043F\u0438\u0441\u043E\u043A</summary><p class="muted">\u0412\u0441\u0442\u0430\u0432\u044C\u0442\u0435 JSON-\u043C\u0430\u0441\u0441\u0438\u0432 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432 \u0438\u043B\u0438 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u043F\u0440\u0435\u0434\u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440\u0430 \u0441 \u043F\u043E\u043B\u0435\u043C items. \u0421\u043D\u0430\u0447\u0430\u043B\u0430 \u043F\u0440\u043E\u0433\u0440\u0430\u043C\u043C\u0430 \u043F\u043E\u043A\u0430\u0436\u0435\u0442 \u043A\u043E\u043B\u0438\u0447\u0435\u0441\u0442\u0432\u043E \u0437\u0430\u043F\u0438\u0441\u0435\u0439. \u041E\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u044E\u0449\u0438\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438 \u043D\u0435 \u0443\u0434\u0430\u043B\u044F\u044E\u0442\u0441\u044F.</p>
        <textarea class="input" data-roster-json rows="5" aria-label="\u041F\u043E\u0434\u0433\u043E\u0442\u043E\u0432\u043B\u0435\u043D\u043D\u044B\u0439 \u0441\u043F\u0438\u0441\u043E\u043A \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432"></textarea><button class="btn" data-roster-import>\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0441\u043F\u0438\u0441\u043E\u043A</button><div data-import-result role="status"></div></details></details>`;
    root.prepend(section2);
    const value = (name) => section2.querySelector(`[data-${name}]`).value;
    section2.querySelector("[data-roster-import]")?.addEventListener("click", async () => {
      const button2 = section2.querySelector("[data-roster-import]");
      button2.disabled = true;
      try {
        const parsed = JSON.parse(value("roster-json")), items2 = Array.isArray(parsed) ? parsed : parsed.items;
        const checked = await adm("POST", "/admin/staff_roster.json", { items: items2 });
        section2.querySelector("[data-import-result]").textContent = `\u041F\u0440\u043E\u0432\u0435\u0440\u0435\u043D\u043E: ${checked.rows} \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432. \u0422\u0435\u043A\u0443\u0449\u0438\u0439 \u0441\u043F\u0440\u0430\u0432\u043E\u0447\u043D\u0438\u043A: ${checked.current_rows}.`;
        if (!await ask("\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0441\u043F\u0440\u0430\u0432\u043E\u0447\u043D\u0438\u043A", `\u0411\u0443\u0434\u0443\u0442 \u0434\u043E\u0431\u0430\u0432\u043B\u0435\u043D\u044B \u0438\u043B\u0438 \u043E\u0431\u043D\u043E\u0432\u043B\u0435\u043D\u044B ${checked.rows} \u0437\u0430\u043F\u0438\u0441\u0435\u0439. \u041F\u0440\u043E\u0432\u0435\u0440\u044C\u0442\u0435 \u0424\u0418\u041E, \u043E\u0444\u0438\u0441\u044B \u0438 \u0441\u043E\u043E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0438\u0435 \u0438\u043C\u0451\u043D \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u0435\u0439 \u0432 \u043F\u043E\u0434\u0433\u043E\u0442\u043E\u0432\u043B\u0435\u043D\u043D\u043E\u043C \u0441\u043F\u0438\u0441\u043A\u0435. \u0414\u043E\u0433\u043E\u0432\u043E\u0440\u044B \u0438 \u0444\u0430\u043A\u0442\u044B \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0442\u0441\u044F.`, "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C")) return;
        await adm("POST", "/admin/staff_roster.json", { items: items2, apply: true, expected_sha256: checked.sha256 });
        await reload();
      } catch (e) {
        section2.querySelector("[data-import-result]").textContent = e.message;
      } finally {
        if (button2.isConnected) button2.disabled = false;
      }
    });
    section2.querySelector("[data-create]")?.addEventListener("click", async () => {
      const uid = Number(value("manager")), username = value("username").trim();
      if (!uid || !username) {
        toast("\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044F \u0438 \u0437\u0430\u0434\u0430\u0439\u0442\u0435 \u043B\u043E\u0433\u0438\u043D.");
        return;
      }
      const display_name = value("manager-name").trim();
      if (!await ask("\u0421\u043E\u0437\u0434\u0430\u0442\u044C \u043F\u0440\u043E\u0444\u0438\u043B\u044C", `\u0421\u043E\u0437\u0434\u0430\u0442\u044C \u043B\u0438\u0447\u043D\u044B\u0439 \u043F\u0440\u043E\u0444\u0438\u043B\u044C ${display_name || managers2.find((m) => m.telegram_id === uid)?.name}? \u041E\u0431\u043B\u0430\u0441\u0442\u0438 \u0434\u043E\u0441\u0442\u0443\u043F\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0442\u0441\u044F.`, "\u0421\u043E\u0437\u0434\u0430\u0442\u044C")) return;
      try {
        const r = await adm("POST", "/admin/account_action.json", { action: "create", uid, username, display_name });
        const b = document.createElement("button");
        b.className = "btn primary";
        b.textContent = "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043B\u0438\u0447\u043D\u044B\u0439 \u0444\u0430\u0439\u043B";
        b.onclick = () => void credentialFile(username, r.temporary_password, r.employee);
        section2.querySelector("[data-create-file]").replaceChildren(b);
        await credentialFile(username, r.temporary_password, r.employee);
      } catch (e) {
        toast(e.message);
      }
    });
    section2.querySelector("[data-roster-preview]")?.addEventListener("click", async () => {
      const products = ["hard", "soft"].filter((p) => section2.querySelector(`[data-${p}]`).checked);
      const employee = value("employee").trim(), office = value("office").trim(), manager = Number(value("roster-manager"));
      if (!employee || !office || !manager || !products.length) {
        toast("\u0417\u0430\u043F\u043E\u043B\u043D\u0438\u0442\u0435 \u0424\u0418\u041E, \u043E\u0444\u0438\u0441, \u043F\u0440\u043E\u0433\u0440\u0430\u043C\u043C\u044B \u0438 \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044F.");
        return;
      }
      const previous = items.find((r) => r.employee === employee), product_names = { ...previous?.product_names };
      for (const product of ["hard", "soft"]) if (value(product + "-name").trim()) product_names[product] = value(product + "-name").trim();
      const offices = value("offices").trim() ? value("offices").split(";").map((x) => x.trim()).filter(Boolean) : previous?.offices || [office];
      const next = items.filter((r) => r.employee !== employee).concat({ employee, office, offices, product_names, products, direction: value("direction"), manager_uid: manager, active: true });
      try {
        const p = await adm("POST", "/admin/staff_roster.json", { items: next });
        section2.querySelector("[data-preview]").textContent = `\u041F\u0440\u043E\u0432\u0435\u0440\u0435\u043D\u043E: ${p.rows} \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432. \u0411\u0443\u0434\u0435\u0442 \u0434\u043E\u0431\u0430\u0432\u043B\u0435\u043D\u0430 \u0438\u043B\u0438 \u043E\u0431\u043D\u043E\u0432\u043B\u0435\u043D\u0430 \u0437\u0430\u043F\u0438\u0441\u044C ${employee}.`;
        if (!await ask("\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C \u0441\u043F\u0440\u0430\u0432\u043E\u0447\u043D\u0438\u043A", `\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C ${employee}, \u043E\u0444\u0438\u0441 ${office}? \u0414\u043E\u0433\u043E\u0432\u043E\u0440\u044B \u0438 \u0444\u0430\u043A\u0442\u044B \u043D\u0435 \u0438\u0437\u043C\u0435\u043D\u044F\u044E\u0442\u0441\u044F.`, "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C")) return;
        await adm("POST", "/admin/staff_roster.json", { items: next, apply: true, expected_sha256: p.sha256 });
        toast("\u0421\u043F\u0440\u0430\u0432\u043E\u0447\u043D\u0438\u043A \u043E\u0431\u043D\u043E\u0432\u043B\u0451\u043D.");
        await reload();
      } catch (e) {
        toast(e.message);
      }
    });
  }
  var init_access_management = __esm({
    "hard-desk/ui/analyst/access-management.ts"() {
      "use strict";
      init_api();
      init_dom();
      init_lib();
      init_shell();
    }
  });

  // hard-desk/ui/analyst/accounts.ts
  function devicesLine(devs) {
    const state2 = (k) => {
      const slots = (devs || []).filter((x) => String(x?.kind) === k && x.status !== "revoked");
      return slots.some((x) => x.status === "pending") ? "\u043E\u0436\u0438\u0434\u0430\u0435\u0442" : slots.some((x) => x.status === "active") ? "\u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D" : "\u043D\u0435\u0442 \u0437\u0430\u044F\u0432\u043A\u0438";
    };
    return `\u0422\u0435\u043B\u0435\u0444\u043E\u043D: ${state2("phone")} / \u041F\u041A: ${state2("desktop")}`;
  }
  function details(root, a) {
    const box = root.querySelector("#acc");
    if (!box) return;
    const devs = a.devices || [];
    const logins = (a.logins || []).slice(0, 10);
    const awaiting = a.status === "awaiting";
    const passwordReview = a.password_review_pending === true;
    const phoneRequestOnly = !passwordReview && (a.created_by === "self-registration" || cutoverActive && a.role === "agent");
    box.innerHTML = `<div class="section" style="margin-top:18px">
    <div class="section-head"><h2 class="section-title">${esc7(a.employee || a.username || "\u0410\u043A\u043A\u0430\u0443\u043D\u0442")}</h2></div>
    <dl class="kv" style="grid-template-columns:200px 1fr;max-width:760px;font-size:15px">
      <dt>\u041B\u043E\u0433\u0438\u043D</dt><dd class="mono">${esc7(a.username || "\u2014")}</dd>
      <dt>\u041E\u0444\u0438\u0441</dt><dd>${esc7(a.office || "\u2014")}</dd>
      <dt>\u0420\u043E\u043B\u044C</dt><dd>${esc7(roleTitle(a) || "\u2014")}</dd>
      <dt>\u0421\u0442\u0430\u0442\u0443\u0441</dt><dd><span class="tag ${a.status === "active" ? "ok" : "warn"}">${esc7(statusTitle(a))}</span>${a.must_change_password ? ' <span class="sub">\u0441\u043C\u0435\u043D\u0438\u0442 \u043F\u0430\u0440\u043E\u043B\u044C \u043F\u0440\u0438 \u0432\u0445\u043E\u0434\u0435</span>' : ""}</dd>
      <dt>\u0421\u043E\u0437\u0434\u0430\u043D</dt><dd>${esc7(fmt(a.created_at) || "\u2014")}</dd>
      <dt>\u0410\u043A\u0442\u0438\u0432\u0438\u0440\u043E\u0432\u0430\u043D</dt><dd>${esc7(fmt(a.activated_at) || "\u2014")}</dd></dl>
    <div class="toolbar" style="margin-top:12px">
      ${!phoneRequestOnly && awaiting ? `<button class="btn small primary" data-open ${approver && (!passwordReview || passwordReviewOwner && !a.must_change_password) ? "" : "disabled"}>${passwordReview ? "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F \u043F\u043E\u0441\u043B\u0435 \u0441\u043C\u0435\u043D\u044B \u043F\u0430\u0440\u043E\u043B\u044F" : "\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F"}</button>` : ""}
      <span class="muted" style="font-size:13px">${phoneRequestOnly ? "\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u0435\u0442 \u0435\u0433\u043E \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C \u0432\u043E \xAB\u0412\u0445\u043E\u0434\u0435 \u043A\u043E\u043C\u0430\u043D\u0434\u044B\xBB." : approver ? `\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u0435\u0442: ${esc7(approver)}. \u0414\u043E\u0441\u0442\u0430\u0442\u043E\u0447\u043D\u043E \u043E\u0434\u043D\u043E\u0433\u043E \u0430\u0434\u043C\u0438\u043D\u0438\u0441\u0442\u0440\u0430\u0442\u043E\u0440\u0430.` : "\u0414\u043B\u044F \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0439 \u0430\u0434\u043C\u0438\u043D\u0438\u0441\u0442\u0440\u0430\u0442\u043E\u0440 \u044D\u0442\u043E\u0433\u043E \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430 \u0434\u043E\u043B\u0436\u0435\u043D \u0431\u044B\u0442\u044C \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D \u043D\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0435."}</span></div>
    ${phoneRequestOnly ? '<p class="notice">\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C \u043E\u0442\u043A\u0440\u044B\u0432\u0430\u0435\u0442 \xAB\u0412\u0445\u043E\u0434 \u043A\u043E\u043C\u0430\u043D\u0434\u044B\xBB, \u043D\u0430\u0445\u043E\u0434\u0438\u0442 \u0437\u0430\u044F\u0432\u043A\u0443 \u0438 \u043D\u0430\u0436\u0438\u043C\u0430\u0435\u0442 \xAB\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0442\u0435\u043B\u0435\u0444\u043E\u043D\xBB \u0438\u043B\u0438 \xAB\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u041F\u041A\xBB. \u0422\u0435\u043B\u0435\u0444\u043E\u043D \u043C\u043E\u0436\u043D\u043E \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0431\u0435\u0437 \u043F\u0440\u0435\u0434\u0432\u0430\u0440\u0438\u0442\u0435\u043B\u044C\u043D\u043E\u0433\u043E \u041F\u041A. \u041A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u044E\u0442 \u043F\u043E\u0441\u043B\u0435 \u043B\u0438\u0447\u043D\u043E\u0439 \u0441\u0432\u0435\u0440\u043A\u0438 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430 \u0437\u0430 \u044D\u0442\u0438\u043C \u041F\u041A. \u0413\u0435\u043E\u043F\u043E\u0437\u0438\u0446\u0438\u044F \u043D\u0435 \u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044F.</p>' : ""}
    ${passwordReview ? `<p class="notice">${a.must_change_password ? "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A \u0435\u0449\u0451 \u043D\u0435 \u0441\u043C\u0435\u043D\u0438\u043B \u0432\u0440\u0435\u043C\u0435\u043D\u043D\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C \u043D\u0430 \u043B\u0438\u0447\u043D\u044B\u0439." : "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A \u0441\u043C\u0435\u043D\u0438\u043B \u043F\u0430\u0440\u043E\u043B\u044C. \u0414\u043E\u0441\u0442\u0443\u043F \u043E\u0436\u0438\u0434\u0430\u0435\u0442 \u0432\u0430\u0448\u0435\u0433\u043E \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F."} \u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u043C\u043E\u0436\u0435\u0442 \u0442\u043E\u043B\u044C\u043A\u043E \u0432\u043B\u0430\u0434\u0435\u043B\u0435\u0446.</p>` : ""}
    <div class="block"><h3>\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430 (${n(devs.length)})</h3>
      ${devs.length ? devs.map((x) => `<div class="list-row">
        <div class="grow"><div class="nm">${esc7(x.label || kind(x.kind) || "\u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E")}</div>
          <div class="sub">${esc7(clientText(x))}</div>
          <div class="sub">${esc7(kind(x.kind))}${x.first_seen ? ` \xB7 \u0441 ${esc7(fmt(x.first_seen))}` : ""} \xB7 ${esc7(gpsText(x))}</div>
          <div class="sub">\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u043B: ${esc7(x.approved_by || "\u2014")}${x.approved_at ? ` \xB7 ${esc7(fmt(x.approved_at))}` : ""}</div></div>
        ${x.status === "revoked" ? '<span class="tag">\u043E\u0442\u043E\u0437\u0432\u0430\u043D\u043E</span>' : devApproved(x) ? '<span class="tag ok">\u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u043E</span>' : passwordReview ? '<span class="tag warn">\u0421\u043D\u0430\u0447\u0430\u043B\u0430 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u0435 \u0441\u043C\u0435\u043D\u0443 \u043F\u0430\u0440\u043E\u043B\u044F</span>' : phoneRequestOnly ? '<span class="tag warn">\u0416\u0434\u0451\u0442 \u0441\u0432\u043E\u0435\u0433\u043E \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044F</span>' : `<button class="btn small primary" data-approve="${esc7(String(x.id))}" ${approver ? "" : "disabled"}>\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C ${x.kind === "phone" ? "\u0442\u0435\u043B\u0435\u0444\u043E\u043D" : "\u041F\u041A"}</button>`}</div>`).join("") : '<p class="muted" style="font-size:13.5px">\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432 \u043D\u0435\u0442: \u043B\u043E\u0433\u0438\u043D-\u043F\u0430\u0440\u043E\u043B\u044C \u043D\u0430 \u044D\u0442\u043E\u043C \u0430\u043A\u043A\u0430\u0443\u043D\u0442\u0435 \u0435\u0449\u0451 \u043D\u0435 \u0432\u0432\u043E\u0434\u0438\u043B\u0438.</p>'}</div>
    <div class="block"><h3>\u0416\u0443\u0440\u043D\u0430\u043B \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0439 \u0434\u043E\u0441\u0442\u0443\u043F\u0430</h3>
      ${(a.approval_events || []).length ? tableHtml(
      [["at", "\u0412\u0440\u0435\u043C\u044F"], ["actor", "\u041A\u0442\u043E \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u043B"], ["action", "\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435"], ["decision", "\u0420\u0435\u0448\u0435\u043D\u0438\u0435"], ["detail", "\u0410\u043A\u043A\u0430\u0443\u043D\u0442 / \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E"], ["ip", "IP"]],
      a.approval_events.map((e) => [fmt(e.at), e.actor, e.action, e.decision, e.detail || "", e.ip || ""])
    ) : '<p class="muted">\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0439 \u043F\u043E\u043A\u0430 \u043D\u0435 \u0431\u044B\u043B\u043E.</p>'}</div>
    <div class="block"><h3>\u0416\u0443\u0440\u043D\u0430\u043B \u0432\u0445\u043E\u0434\u043E\u0432 (\u043F\u043E\u0441\u043B\u0435\u0434\u043D\u0438\u0435 ${n(Math.min(10, logins.length))})</h3>
      ${logins.length ? tableHtml(
      [["kind", "\u0422\u0438\u043F"], ["at", "\u0412\u0440\u0435\u043C\u044F"], ["entrance", "\u0412\u0445\u043E\u0434"], ["label", "\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E"], ["ip", "IP"]],
      logins.map((l) => [kind(l.kind || l.device_kind), fmt(l.at), l.entrance || "", l.label || "", l.ip || ""])
    ) : '<p class="muted" style="font-size:13.5px">\u0412\u0445\u043E\u0434\u043E\u0432 \u043F\u043E\u043A\u0430 \u043D\u0435 \u0431\u044B\u043B\u043E.</p>'}</div>
  </div>`;
    box.querySelector("[data-open]")?.addEventListener("click", guard(async () => {
      if (!await ask("\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F?", `\u0410\u043A\u043A\u0430\u0443\u043D\u0442 \xAB${a.username}\xBB (${a.employee || ""}) \u0441\u043C\u043E\u0436\u0435\u0442 \u0432\u0445\u043E\u0434\u0438\u0442\u044C \u043F\u043E \u043B\u043E\u0433\u0438\u043D\u0443 \u0438 \u043F\u0430\u0440\u043E\u043B\u044E.`, "\u041E\u0442\u043A\u0440\u044B\u0442\u044C")) return;
      const r = await adm("POST", "/admin/account_approve.json", { username: a.username });
      toast(r?.active ? "\u0414\u043E\u0441\u0442\u0443\u043F \u043E\u0442\u043A\u0440\u044B\u0442" : "\u041E\u0436\u0438\u0434\u0430\u0435\u0442\u0441\u044F \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435 \u0430\u0434\u043C\u0438\u043D\u0438\u0441\u0442\u0440\u0430\u0442\u043E\u0440\u0430.");
      await load2(root);
    }));
    box.querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", guard(async () => {
      const x = devs.find((d) => String(d.id) === b.dataset.approve);
      if (!x) return;
      if (!await ask("\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E?", `${x.label || kind(x.kind)} \u043F\u043E\u043B\u0443\u0447\u0438\u0442 \u0434\u043E\u0441\u0442\u0443\u043F \u043A \u0430\u043A\u043A\u0430\u0443\u043D\u0442\u0443 \xAB${a.username}\xBB.`, "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C")) return;
      const r = await adm("POST", "/admin/account_approve.json", { device_id: x.id });
      toast(r?.active ? "\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u043E" : "\u041E\u0436\u0438\u0434\u0430\u0435\u0442\u0441\u044F \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435 \u0430\u0434\u043C\u0438\u043D\u0438\u0441\u0442\u0440\u0430\u0442\u043E\u0440\u0430.");
      await load2(root);
    })));
    mountAccountActions(box, a, () => load2(root));
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function drawBulk(root) {
    const bar = root.querySelector("#bulk");
    if (!bar) return;
    const awaiting = all.filter((a) => a.status === "awaiting").length;
    const pending = all.reduce((s, a) => s + (a.devices || []).filter((d) => d.status === "pending").length, 0);
    bar.innerHTML = `<span class="who">\u0410\u043A\u043A\u0430\u0443\u043D\u0442\u043E\u0432: <b>${n(all.length)}</b> \xB7 \u0436\u0434\u0443\u0442 \u043E\u0442\u043A\u0440\u044B\u0442\u0438\u044F: <b>${n(awaiting)}</b> \xB7 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432 \u0436\u0434\u0443\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F: <b>${n(pending)}</b></span>
    <span class="spacer"></span>
    <button class="ticon" data-b="reload" title="\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C \u0441\u043F\u0438\u0441\u043E\u043A"><svg viewBox="0 0 18 18" width="15" height="15"><path d="M14.5 8.5a5.5 5.5 0 1 1-1.6-3.9M14.5 2.5v3.6h-3.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>`;
    bar.querySelector("[data-b=reload]")?.addEventListener("click", guard(async () => {
      await load2(root);
      toast("\u041E\u0431\u043D\u043E\u0432\u043B\u0435\u043D\u043E");
    }));
  }
  async function load2(root) {
    const d = await adm("GET", "/admin/accounts.json");
    approver = String(d.approver || "");
    cutoverActive = d.cutover_active === true;
    passwordReviewOwner = d.password_review_owner === true;
    all = (d.accounts || []).slice().sort((a, b) => String(a.username || "").localeCompare(String(b.username || ""), "ru"));
    selected = all.some((a) => String(a.username) === selected) ? selected : null;
    const pending = all.filter((a) => a.status === "awaiting" || (a.devices || []).some((d2) => d2.status === "pending"));
    root.innerHTML = `${note("\u0427\u0442\u043E\u0431\u044B \u043E\u0442\u043A\u0440\u044B\u0442\u044C \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0443 \u0438 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044F, \u043D\u0430\u0436\u043C\u0438\u0442\u0435 \xAB\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u043F\u0440\u043E\u0444\u0438\u043B\u044C\xBB \u0432 \u0441\u0442\u0440\u043E\u043A\u0435. \u0410\u043A\u043A\u0430\u0443\u043D\u0442 \u0438 \u043A\u0430\u0436\u0434\u043E\u0435 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u044E\u0442\u0441\u044F \u043E\u0442\u0434\u0435\u043B\u044C\u043D\u043E. \u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A \u043C\u043E\u0436\u0435\u0442 \u0437\u0430\u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0438\u0440\u043E\u0432\u0430\u0442\u044C\u0441\u044F \u0441 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0430 \u0438\u043B\u0438 \u043D\u0430 \u041F\u041A; \u0432\u0445\u043E\u0434 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u0435\u0442 \u0441\u0432\u043E\u0439 \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C \u0432\u043E \xAB\u0412\u0445\u043E\u0434\u0435 \u043A\u043E\u043C\u0430\u043D\u0434\u044B\xBB. \u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u0435\u0439 \u0438 \u0441\u043B\u0443\u0436\u0435\u0431\u043D\u044B\u0435 \u043F\u0440\u043E\u0444\u0438\u043B\u0438 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u0435\u0442 \u0430\u0434\u043C\u0438\u043D\u0438\u0441\u0442\u0440\u0430\u0442\u043E\u0440. \u0413\u0435\u043E\u043F\u043E\u0437\u0438\u0446\u0438\u044F \u0434\u043B\u044F \u0432\u0445\u043E\u0434\u0430 \u043D\u0435 \u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044F.")}
    <div class="section"><h3>\u0416\u0434\u0443\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F: ${pending.length} \u043F\u0440\u043E\u0444\u0438\u043B\u0435\u0439</h3>${pending.length ? pending.map((a) => `<div class="list-row"><div class="grow"><b>${esc7(a.employee || a.username)}</b><div class="sub">${esc7(devicesLine(a.devices))}</div></div><button class="btn primary" data-profile="${esc7(a.username)}">\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u043F\u0440\u043E\u0444\u0438\u043B\u044C</button></div>`).join("") : '<p class="muted">\u041D\u0435\u043E\u0431\u0440\u0430\u0431\u043E\u0442\u0430\u043D\u043D\u044B\u0445 \u0437\u0430\u044F\u0432\u043E\u043A \u043D\u0435\u0442. \u041D\u043E\u0432\u0430\u044F \u0437\u0430\u044F\u0432\u043A\u0430 \u043F\u043E\u044F\u0432\u0438\u0442\u0441\u044F \u043F\u043E\u0441\u043B\u0435 \u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u0438 \u0438\u043B\u0438 \u0432\u0445\u043E\u0434\u0430 \u043D\u0430 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0435.</p>'}</div>
    <div class="bulk" id="bulk" style="margin-top:12px"></div>
    <input class="input" type="search" id="account-search" aria-label="\u041F\u043E\u0438\u0441\u043A \u043F\u0440\u043E\u0444\u0438\u043B\u044F" placeholder="\u0424\u0418\u041E, \u043B\u043E\u0433\u0438\u043D, \u043E\u0444\u0438\u0441 \u0438\u043B\u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E" value="${esc7(accountQuery)}">
    <details id="account-all" ${accountExpanded || accountQuery ? "open" : ""}><summary>\u0412\u0441\u0435 \u043F\u0440\u043E\u0444\u0438\u043B\u0438 (${all.length})</summary><div id="tbl"></div></details>
    <div id="acc"></div>`;
    const cols = [
      { key: "login", title: "\u041B\u043E\u0433\u0438\u043D", value: (r) => r.a.username || "", html: (r) => `<span class="who mono">${esc7(r.a.username || "")}</span>` },
      { key: "emp", title: "\u0424\u0418\u041E", value: (r) => r.a.employee || "" },
      { key: "office", title: "\u041E\u0444\u0438\u0441", value: (r) => r.a.office || "" },
      { key: "role", title: "\u0420\u043E\u043B\u044C", value: (r) => roleTitle(r.a) },
      {
        key: "status",
        title: "\u0421\u0442\u0430\u0442\u0443\u0441",
        value: (r) => statusTitle(r.a),
        html: (r) => `<span class="tag ${r.a.status === "active" ? "ok" : r.a.status === "awaiting" ? "warn" : ""}">${esc7(statusTitle(r.a))}</span>`
      },
      { key: "devs", title: "\u0423\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430", value: (r) => devicesLine(r.a.devices) + " " + (r.a.devices || []).map((d2) => d2.label).join(" "), html: (r) => `<span class="who">${esc7(devicesLine(r.a.devices))}</span>` },
      { key: "actions", title: "\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u044F", value: () => "", filter: false, html: (r) => `<button class="btn small" data-profile="${esc7(r.a.username)}">\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u043F\u0440\u043E\u0444\u0438\u043B\u044C</button>` }
    ];
    const table2 = new DataTable(root.querySelector("#tbl"), {
      id: "analyst-accounts",
      cols,
      key: (r) => String(r.a.username || ""),
      exportName: "\u0410\u043A\u043A\u0430\u0443\u043D\u0442\u044B \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430",
      externalSearch: true,
      onRow: (r) => {
        selected = String(r.a.username || "");
        details(root, r.a);
      },
      empty: "\u0410\u043A\u043A\u0430\u0443\u043D\u0442\u043E\u0432 \u0441 \u043B\u043E\u0433\u0438\u043D\u043E\u043C \u0438 \u043F\u0430\u0440\u043E\u043B\u0435\u043C \u043F\u043E\u043A\u0430 \u043D\u0435\u0442."
    });
    table2.setRows(all.map((a) => ({ a })));
    table2.setQuery(accountQuery);
    const full = root.querySelector("#account-all");
    full.addEventListener("toggle", () => {
      accountExpanded = full.open;
    });
    root.querySelector("#account-search").addEventListener("input", (event) => {
      accountQuery = event.target.value;
      table2.setQuery(accountQuery);
      if (accountQuery.trim()) full.open = true;
    });
    root.querySelector("#tbl")?.addEventListener("click", (event) => {
      const button2 = event.target.closest("[data-profile]");
      const account = button2 && all.find((a) => a.username === button2.dataset.profile);
      if (account) {
        selected = account.username;
        details(root, account);
      }
    });
    root.querySelectorAll(".section [data-profile]").forEach((button2) => button2.addEventListener("click", () => {
      const account = all.find((a) => a.username === button2.dataset.profile);
      if (account) {
        selected = account.username;
        details(root, account);
      }
    }));
    const again = all.find((a) => String(a.username) === selected);
    if (again) details(root, again);
    drawBulk(root);
    await mountAccessPreparation(root, () => load2(root));
  }
  var KIND, ROLE, STATUS, fmt, kind, roleTitle, statusTitle, devApproved, clientText, gpsText, all, selected, approver, cutoverActive, passwordReviewOwner, accountQuery, accountExpanded, accounts;
  var init_accounts = __esm({
    "hard-desk/ui/analyst/accounts.ts"() {
      "use strict";
      init_dom();
      init_table();
      init_lib();
      init_access_management();
      KIND = { phone: "\u0442\u0435\u043B\u0435\u0444\u043E\u043D", desktop: "\u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440" };
      ROLE = { agent: "\u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A", employee: "\u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A", manager: "\u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C", security: "\u0441\u043B\u0443\u0436\u0431\u0430 \u0431\u0435\u0437\u043E\u043F\u0430\u0441\u043D\u043E\u0441\u0442\u0438", full: "\u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C \u0438 \u043A\u043E\u043D\u0442\u0440\u043E\u043B\u044C", analyst: "\u0430\u043D\u0430\u043B\u0438\u0442\u0438\u043A" };
      STATUS = { awaiting: "\u0436\u0434\u0451\u0442 \u043E\u0442\u043A\u0440\u044B\u0442\u0438\u044F", active: "\u043E\u0442\u043A\u0440\u044B\u0442", blocked: "\u0437\u0430\u0431\u043B\u043E\u043A\u0438\u0440\u043E\u0432\u0430\u043D", disabled: "\u043E\u0442\u043A\u043B\u044E\u0447\u0451\u043D" };
      fmt = (v) => String(v ?? "").replace("T", " ").slice(0, 16);
      kind = (k) => KIND[String(k ?? "")] || String(k ?? "");
      roleTitle = (a) => ROLE[a?.role] || a?.role || "";
      statusTitle = (a) => STATUS[a?.status] || a?.status || "\u2014";
      devApproved = (x) => !!x && x.status === "active";
      clientText = (device) => {
        const c = device.client;
        if (!c) return "\u0412\u0435\u0440\u0441\u0438\u044F \u043F\u043E\u043A\u0430 \u043D\u0435 \u0441\u043E\u043E\u0431\u0449\u0430\u043B\u0430\u0441\u044C";
        const version = c.source === "pwa" ? `\u041E\u0431\u043E\u043B\u043E\u0447\u043A\u0430 ${c.shell_version}` : `${c.source === "desktop" ? "\u041F\u0440\u043E\u0433\u0440\u0430\u043C\u043C\u0430" : "\u041A\u043B\u0438\u0435\u043D\u0442 \u0441\u043E\u043E\u0431\u0449\u0438\u043B"} ${c.client_version || "\u2014"}`;
        return `${version}${c.product ? " \xB7 " + c.product : ""} \xB7 \u0437\u0430\u043C\u0435\u0447\u0435\u043D\u0430 ${fmt(c.last_seen_at)} UTC`;
      };
      gpsText = (x) => {
        const raw = x?.gps;
        if (!raw) return "GPS \u043D\u0435\u0442";
        try {
          const g = JSON.parse(String(raw));
          return g && typeof g.lat === "number" ? `GPS ${g.lat}, ${g.lon}` : "GPS \u0435\u0441\u0442\u044C";
        } catch {
          return "GPS \u0435\u0441\u0442\u044C";
        }
      };
      all = [];
      selected = null;
      approver = "";
      cutoverActive = false;
      passwordReviewOwner = false;
      accountQuery = "";
      accountExpanded = false;
      accounts = {
        id: "accounts",
        title: "\u041F\u0440\u043E\u0444\u0438\u043B\u0438 \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430",
        icon: "device",
        kicker: "\u0420\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044F, \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F, \u043F\u0430\u0440\u043E\u043B\u0438 \u0438 \u043E\u0442\u0437\u044B\u0432 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432",
        async render(root) {
          root.innerHTML = '<div class="skeleton"></div>';
          try {
            await load2(root);
          } catch (e) {
            root.innerHTML = `<div class="notice err">${esc7(e.message)}</div>`;
          }
        }
      };
    }
  });

  // hard-desk/ui/analyst/mail.ts
  async function job(body, log) {
    return runJob("/mail/send", body, log);
  }
  var mail;
  var init_mail = __esm({
    "hard-desk/ui/analyst/mail.ts"() {
      "use strict";
      init_api();
      init_dom();
      init_shell();
      init_table();
      init_lib();
      mail = {
        id: "mail",
        title: "\u0420\u0430\u0441\u0441\u044B\u043B\u043A\u0430",
        icon: "mail",
        kicker: "\u043A\u043D\u0438\u0433\u0438 Excel \u043F\u043E\u0447\u0442\u043E\u0439 \u2014 \u0438\u0437 \u0432\u0430\u0448\u0435\u0433\u043E Outlook, \u043F\u043E \u0440\u0430\u0441\u043F\u0438\u0441\u0430\u043D\u0438\u044E",
        async render(root) {
          const s = await local("/mail/state");
          const cfg = s.config;
          const auto = await hard.autostart().catch(() => false);
          const people2 = cfg.recipients.map((p) => ({ ...p }));
          const days = new Set(cfg.days);
          const books = { ...cfg.books };
          let direction = cfg.direction || "";
          const weekdays = s.weekdays;
          const state2 = cfg.enabled ? `<span class="tag ok">\u0432\u043A\u043B\u044E\u0447\u0435\u043D\u0430</span> <span class="muted">${esc7(s.next ? "\u0441\u043B\u0435\u0434\u0443\u044E\u0449\u0430\u044F: " + s.next : "")}</span>` : '<span class="tag">\u0432\u044B\u043A\u043B\u044E\u0447\u0435\u043D\u0430</span>';
          const outlook = s.outlook ? '<span class="tag ok">Outlook \u043E\u0442\u043A\u0440\u044B\u0442</span>' : '<span class="tag warn">Outlook \u0437\u0430\u043A\u0440\u044B\u0442 \u2014 \u043F\u0438\u0441\u044C\u043C\u043E \u043E\u0442\u043A\u0440\u043E\u0435\u0442 \u0435\u0433\u043E \u0441\u0430\u043C\u043E</span>';
          root.innerHTML = `
      <div class="toolbar"><label class="check"><input type="checkbox" id="m-on" ${cfg.enabled ? "checked" : ""}> \u0420\u0430\u0441\u0441\u044B\u043B\u043A\u0430 \u0432\u043A\u043B\u044E\u0447\u0435\u043D\u0430</label>
        ${state2}<span class="spacer"></span>${outlook}</div>
      <div class="section"><div class="section-head"><h2 class="section-title">\u041A\u043E\u043C\u0443</h2></div>
        <div id="m-people"></div>
        <div class="toolbar"><button class="btn small" data-add>\u0414\u043E\u0431\u0430\u0432\u0438\u0442\u044C \u043F\u043E\u043B\u0443\u0447\u0430\u0442\u0435\u043B\u044F</button></div></div>
      <div class="section"><div class="section-head"><h2 class="section-title">\u0427\u0442\u043E \u0438 \u043A\u043E\u0433\u0434\u0430</h2></div>
        <div class="form-grid">
          <label class="check"><input type="checkbox" data-book="day" ${books.day ? "checked" : ""}> ${esc7(s.books.day)}</label>
          <div class="chips">${weekdays.map((d, i) => `<button class="chip toggle" data-day="${i}" aria-pressed="${days.has(i)}">${esc7(d)}</button>`).join("")}</div>
          <label class="check"><input type="checkbox" data-book="week" ${books.week ? "checked" : ""}> ${esc7(s.books.week)} \u2014 \u0437\u0430 7 \u0434\u043D\u0435\u0439, \u043F\u043E</label>
          <select class="input" id="m-wd" style="width:120px">${weekdays.map((d, i) => `<option value="${i}" ${i === cfg.week_day ? "selected" : ""}>${esc7(d)}</option>`).join("")}</select>
          <span class="muted">\u0412\u0440\u0435\u043C\u044F \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438</span>
          <input class="input" id="m-time" value="${esc7(cfg.time)}" style="width:90px" placeholder="19:30">
          <span class="muted">\u041A\u043E\u0433\u043E \u0441\u0447\u0438\u0442\u0430\u0442\u044C</span>
          <div class="chips">${Object.entries(s.directions).map(([k, t]) => `<button class="chip" data-dir="${esc7(k)}" aria-pressed="${k === direction}">${esc7(t)}</button>`).join("")}</div>
          <span class="muted">\u0422\u0435\u043C\u0430 \u043D\u0430\u0447\u0438\u043D\u0430\u0435\u0442\u0441\u044F \u0441</span>
          <input class="input" id="m-subj" value="${esc7(cfg.subject)}" style="width:280px">
          <span class="muted">\u0421\u0432\u043E\u044F \u0441\u0442\u0440\u043E\u043A\u0430 \u0432 \u043F\u0438\u0441\u044C\u043C\u0435</span>
          <textarea class="input" id="m-note" rows="2" style="width:520px;height:auto;padding:8px 12px" placeholder="\u043D\u0435\u043E\u0431\u044F\u0437\u0430\u0442\u0435\u043B\u044C\u043D\u043E">${esc7(cfg.note)}</textarea>
        </div>
        <div class="toolbar" style="margin-top:14px"><button class="btn primary" data-save>\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C</button>
          <span class="spacer"></span>
          <select class="input" id="m-book" style="width:170px">${Object.entries(s.books).map(([k, t]) => `<option value="${esc7(k)}">${esc7(t)}</option>`).join("")}</select>
          <button class="btn" data-show>\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u044C \u043F\u0438\u0441\u044C\u043C\u043E</button>
          <button class="btn" data-send>\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C \u0441\u0435\u0439\u0447\u0430\u0441</button></div>
        <p class="muted" id="m-log" style="font-size:13px;min-height:18px;margin:6px 0 0"></p></div>
      <div class="section"><div class="section-head"><h2 class="section-title">\u041A\u0430\u043A \u044D\u0442\u043E \u0440\u0430\u0431\u043E\u0442\u0430\u0435\u0442</h2></div>
        ${note("\u041F\u0438\u0441\u044C\u043C\u043E \u0443\u0445\u043E\u0434\u0438\u0442 \u0438\u0437 \u0432\u0430\u0448\u0435\u0433\u043E Outlook \u2014 \u043E\u0442 \u0432\u0430\u0448\u0435\u0433\u043E \u0438\u043C\u0435\u043D\u0438, \u0441 \u0432\u0430\u0448\u0435\u0439 \u043F\u043E\u0434\u043F\u0438\u0441\u044C\u044E, \u043A\u043E\u043F\u0438\u044F \u043E\u0441\u0442\u0430\u0451\u0442\u0441\u044F \u0432 \xAB\u041E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u043D\u044B\u0445\xBB. \u041A\u043D\u0438\u0433\u0443 \u043F\u0443\u043B\u044C\u0442 \u0431\u0435\u0440\u0451\u0442 \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u0430 \u0432 \u043C\u043E\u043C\u0435\u043D\u0442 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438, \u0438\u0442\u043E\u0433\u0438 \u0434\u043D\u044F \u2014 \u0432 \u0442\u0435\u043A\u0441\u0442\u0435 \u043F\u0438\u0441\u044C\u043C\u0430.\n\u041F\u0443\u043B\u044C\u0442 \u0434\u043E\u043B\u0436\u0435\u043D \u0431\u044B\u0442\u044C \u0437\u0430\u043F\u0443\u0449\u0435\u043D: \u043A\u0440\u0435\u0441\u0442\u0438\u043A \u0441\u0432\u043E\u0440\u0430\u0447\u0438\u0432\u0430\u0435\u0442 \u0435\u0433\u043E \u043A \u0447\u0430\u0441\u0430\u043C. \u0415\u0441\u043B\u0438 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440 \u0431\u044B\u043B \u0432\u044B\u043A\u043B\u044E\u0447\u0435\u043D \u0432 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u043D\u043E\u0435 \u0432\u0440\u0435\u043C\u044F, \u043F\u0438\u0441\u044C\u043C\u043E \u0443\u0439\u0434\u0451\u0442, \u043A\u043E\u0433\u0434\u0430 \u043F\u0443\u043B\u044C\u0442 \u0437\u0430\u043F\u0443\u0441\u0442\u0438\u0442\u0441\u044F \u0432 \u0442\u043E\u0442 \u0436\u0435 \u0434\u0435\u043D\u044C.")}
        <div class="toolbar" style="margin-top:10px"><label class="check"><input type="checkbox" id="m-auto" ${auto ? "checked" : ""}> \u0417\u0430\u043F\u0443\u0441\u043A\u0430\u0442\u044C \u043F\u0443\u043B\u044C\u0442 \u0432\u043C\u0435\u0441\u0442\u0435 \u0441 Windows (\u0441\u0440\u0430\u0437\u0443 \u043A \u0447\u0430\u0441\u0430\u043C)</label></div></div>
      <div class="section"><div class="section-head"><h2 class="section-title">\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0438</h2></div><div id="m-hist"></div></div>`;
          const drawPeople = () => {
            const box = root.querySelector("#m-people");
            box.innerHTML = people2.map((p, i) => `<div class="toolbar" style="margin:6px 0 0">
          <input class="input" data-pn="${i}" value="${esc7(p.name)}" placeholder="\u041A\u0442\u043E \u044D\u0442\u043E (\u0433\u0435\u043D\u0434\u0438\u0440\u0435\u043A\u0442\u043E\u0440, \u0437\u0430\u043C\u2026)" style="width:240px">
          <input class="input" data-pe="${i}" value="${esc7(p.email)}" placeholder="\u0430\u0434\u0440\u0435\u0441@\u043F\u043E\u0447\u0442\u0430" style="width:300px">
          <button class="btn small" data-del="${i}" title="\u0423\u0431\u0440\u0430\u0442\u044C">\xD7</button></div>`).join("") || '<p class="muted" style="margin:6px 0 0">\u041F\u043E\u043B\u0443\u0447\u0430\u0442\u0435\u043B\u0435\u0439 \u043F\u043E\u043A\u0430 \u043D\u0435\u0442.</p>';
            box.querySelectorAll("[data-pn]").forEach((el) => el.addEventListener("input", () => {
              people2[Number(el.dataset.pn)].name = el.value;
            }));
            box.querySelectorAll("[data-pe]").forEach((el) => el.addEventListener("input", () => {
              people2[Number(el.dataset.pe)].email = el.value.trim();
            }));
            box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => {
              people2.splice(Number(b.dataset.del), 1);
              drawPeople();
            }));
          };
          drawPeople();
          root.querySelector("[data-add]").addEventListener("click", () => {
            people2.push({ name: "", email: "" });
            drawPeople();
          });
          root.querySelectorAll("[data-day]").forEach((b) => b.addEventListener("click", () => {
            const d = Number(b.dataset.day);
            if (days.has(d)) days.delete(d);
            else days.add(d);
            b.setAttribute("aria-pressed", String(days.has(d)));
          }));
          root.querySelectorAll("[data-book]").forEach((el) => el.addEventListener("change", () => {
            books[el.dataset.book] = el.checked;
          }));
          root.querySelectorAll("[data-dir]").forEach((b) => b.addEventListener("click", () => {
            direction = b.dataset.dir || "";
            root.querySelectorAll("[data-dir]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
          }));
          const collect = (enabled) => ({
            enabled: enabled ?? root.querySelector("#m-on").checked,
            recipients: people2,
            days: [...days],
            books,
            direction,
            week_day: Number(root.querySelector("#m-wd").value),
            time: root.querySelector("#m-time").value,
            subject: root.querySelector("#m-subj").value,
            note: root.querySelector("#m-note").value
          });
          const save = async (enabled) => {
            await local("/mail/set", collect(enabled));
          };
          root.querySelector("[data-save]").addEventListener("click", guard(async () => {
            await save();
            toast("\u0420\u0430\u0441\u0441\u044B\u043B\u043A\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430");
            await refresh();
          }));
          root.querySelector("#m-on").addEventListener("change", guard(async (e) => {
            const el = e.target;
            try {
              await save(el.checked);
            } catch (err) {
              el.checked = !el.checked;
              throw err;
            }
            toast(el.checked ? "\u0420\u0430\u0441\u0441\u044B\u043B\u043A\u0430 \u0432\u043A\u043B\u044E\u0447\u0435\u043D\u0430" : "\u0420\u0430\u0441\u0441\u044B\u043B\u043A\u0430 \u0432\u044B\u043A\u043B\u044E\u0447\u0435\u043D\u0430");
            await refresh();
          }));
          root.querySelector("#m-auto").addEventListener("change", guard(async (e) => {
            const on = await hard.autostart(e.target.checked);
            toast(on ? "\u041F\u0443\u043B\u044C\u0442 \u0431\u0443\u0434\u0435\u0442 \u0437\u0430\u043F\u0443\u0441\u043A\u0430\u0442\u044C\u0441\u044F \u0432\u043C\u0435\u0441\u0442\u0435 \u0441 Windows" : "\u0410\u0432\u0442\u043E\u0437\u0430\u043F\u0443\u0441\u043A \u0432\u044B\u043A\u043B\u044E\u0447\u0435\u043D");
          }));
          const log = (t) => {
            root.querySelector("#m-log").textContent = t;
          };
          const run = (display) => guard(async () => {
            await save();
            const book = root.querySelector("#m-book").value;
            if (!display && !await ask("\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C \u0441\u0435\u0439\u0447\u0430\u0441?", `\u041F\u0438\u0441\u044C\u043C\u043E \u0441 \u043A\u043D\u0438\u0433\u043E\u0439 \xAB${s.books[book]}\xBB \u0443\u0439\u0434\u0451\u0442: ${people2.map((p) => p.email).join(", ")}.`, "\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C")) return;
            log("\u2026");
            const r = await job({ book, display }, log);
            log(display ? "\u0427\u0435\u0440\u043D\u043E\u0432\u0438\u043A \u043E\u0442\u043A\u0440\u044B\u0442 \u0432 Outlook \u2014 \u043F\u0440\u043E\u0432\u0435\u0440\u044C\u0442\u0435 \u0438 \u0437\u0430\u043A\u0440\u043E\u0439\u0442\u0435 \u0431\u0435\u0437 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438." : `\u041E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u043E: ${r.to.join(", ")}`);
            if (!display) await refresh();
          });
          root.querySelector("[data-show]").addEventListener("click", run(true));
          root.querySelector("[data-send]").addEventListener("click", run(false));
          new DataTable(root.querySelector("#m-hist"), {
            id: "analyst-mail-history",
            exportName: "\u0420\u0430\u0441\u0441\u044B\u043B\u043A\u0430 \u2014 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438",
            key: (h) => h.at + h.book,
            empty: "\u041F\u0438\u0441\u0435\u043C \u0435\u0449\u0451 \u043D\u0435 \u0431\u044B\u043B\u043E.",
            cols: [
              { key: "at", title: "\u041A\u043E\u0433\u0434\u0430", value: (h) => h.at, html: (h) => `<span class="mono">${esc7(h.at)}</span>` },
              { key: "book", title: "\u041A\u043D\u0438\u0433\u0430", value: (h) => h.book },
              { key: "state", title: "\u0418\u0442\u043E\u0433", value: (h) => h.state, html: (h) => `<span class="tag ${h.ok ? "ok" : "err"}">${esc7(h.state)}</span>` },
              { key: "to", title: "\u041A\u043E\u043C\u0443", value: (h) => h.to },
              { key: "error", title: "\u041E\u0448\u0438\u0431\u043A\u0430", filter: false, value: (h) => h.error }
            ]
          }).setRows(s.history);
        }
      };
    }
  });

  // hard-desk/ui/analyst/today.ts
  var today;
  var init_today = __esm({
    "hard-desk/ui/analyst/today.ts"() {
      "use strict";
      init_dom();
      init_links();
      init_table();
      init_lib();
      today = {
        id: "dash",
        title: "\u0421\u0435\u0433\u043E\u0434\u043D\u044F",
        icon: "today",
        kicker: "\u0434\u0435\u043D\u044C \u043A\u043E\u043C\u0430\u043D\u0434\u044B \u0446\u0435\u043B\u0438\u043A\u043E\u043C",
        async render(root) {
          root.innerHTML = '<div class="skeleton"></div><div class="skeleton" style="width:60%"></div>';
          const d = await adm("GET", "/admin/today", void 0, 120).catch((e) => {
            root.innerHTML = `<div class="notice err">${esc7(e.message)}</div>`;
            return null;
          });
          if (!d || !root.isConnected) return;
          const [tiles, TILES, bars, silentNote, regPairs, regNote, cols] = await Promise.all([
            fn("dashboard.tile_values", d),
            fn("dashboard.TILES"),
            fn("dashboard.bars", d),
            fn("dashboard.silent_note", d),
            fn("dashboard.registry_pairs", d),
            fn("dashboard.registry_note", d),
            fn("dashboard.SILENT_COLUMNS")
          ]);
          const silentRows = await fnMap("dashboard.silent_row", (d.silent || []).map((r) => [r]));
          const countries = await Promise.all((d.countries || []).map(async (c) => ({
            c,
            pairs: await fn("dashboard.country_today_pairs", c),
            chips: await fn("dashboard.country_outcome_chips", c)
          })));
          const max = Math.max(1, ...bars.map((b) => b.value));
          const draw3 = () => {
            root.innerHTML = `
        <div class="kpis">${TILES.map(([k, t]) => `<div class="kpi"><b>${esc7(tiles[k])}</b><span>${esc7(t)}</span></div>`).join("")}</div>
        <div class="days" style="height:84px">${bars.map((b, i) => `<div class="day-bar" title="${esc7(b.label)}: ${n(b.value)}">
          <i style="height:${Math.max(2, Math.round(b.value / max * 62))}px;${b.weekend ? "opacity:.45" : ""}${i === bars.length - 1 ? ";background:var(--terra)" : ""}"></i><span>${esc7(b.label)}</span></div>`).join("")}</div>
        <div class="section"><div class="section-head"><h2 class="section-title">\u0421\u0435\u0433\u043E\u0434\u043D\u044F \u043D\u0435 \u0432\u044B\u0435\u0445\u0430\u043B\u0438</h2><span class="section-note">${esc7(silentNote)}</span></div>
          <div id="silent"></div>
          <p class="muted" style="font-size:13px">\u0429\u0435\u043B\u0447\u043E\u043A \u043F\u043E \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0443 \u043E\u0442\u043A\u0440\u044B\u0432\u0430\u0435\u0442 \u0443\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0438\u0435 \u043F\u0440\u043E\u0444\u0438\u043B\u044F\u043C\u0438 \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430\u043C\u0438.</p></div>
        <div class="section"><div class="section-head"><h2 class="section-title">\u0420\u0435\u0435\u0441\u0442\u0440 \u0438 \u043E\u0431\u043E\u0433\u0430\u0449\u0435\u043D\u0438\u0435</h2></div>
          <div class="kpis">${regPairs.map(([k, v]) => `<div class="kpi"><b>${esc7(v)}</b><span>${esc7(k)}</span></div>`).join("")}</div>${note(regNote)}</div>
        <div class="section"><div class="section-head"><h2 class="section-title">\u041F\u043E \u0440\u0435\u0441\u043F\u0443\u0431\u043B\u0438\u043A\u0430\u043C</h2></div>
          <div class="grid2">${countries.map(({ c, pairs: pairs2, chips }) => `<div>
            <h3 class="hand" style="font-size:22px;margin:4px 0 8px;font-weight:400">${esc7(c.flag || "")} ${esc7(c.name)}</h3>
            <div class="kpis">${pairs2.map(([k, v]) => `<div class="kpi"><b style="font-size:22px">${esc7(v)}</b><span>${esc7(k)}</span></div>`).join("")}</div>
            <div class="flags" style="margin-top:10px">${chips.map((t) => `<span class="tag">${esc7(t)}</span>`).join("")}</div></div>`).join("")}</div></div>`;
            new DataTable(root.querySelector("#silent"), {
              id: "analyst-silent",
              cols: tupleCols(cols, /* @__PURE__ */ new Set(["portfolio", "idle"])),
              key: (r) => String(r[0]),
              exportName: "\u041D\u0435 \u0432\u044B\u0435\u0445\u0430\u043B\u0438 \u0441\u0435\u0433\u043E\u0434\u043D\u044F",
              onRow: guard(async (r) => {
                await issueLink({ employee: String(r[0]) });
              }),
              empty: "\u0421\u0435\u0433\u043E\u0434\u043D\u044F \u0432\u044B\u0435\u0445\u0430\u043B\u0438 \u0432\u0441\u0435, \u0443 \u043A\u043E\u0433\u043E \u0435\u0441\u0442\u044C \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044C."
            }).setRows(silentRows);
          };
          draw3();
        }
      };
    }
  });

  // hard-desk/ui/kit/modeswitch.ts
  var init_modeswitch = __esm({
    "hard-desk/ui/kit/modeswitch.ts"() {
      "use strict";
      init_api();
      init_dom();
    }
  });

  // hard-desk/ui/analyst/owner-access.ts
  var init_owner_access = __esm({
    "hard-desk/ui/analyst/owner-access.ts"() {
      "use strict";
      init_dom();
      init_shell();
      init_lib();
    }
  });

  // hard-desk/src/main/product.ts
  var CRM_INTERNAL, CRM_ANALYST_INTERNAL, built, PRODUCTS, PRODUCT_ID, FIXED_PRODUCT;
  var init_product = __esm({
    "hard-desk/src/main/product.ts"() {
      "use strict";
      CRM_INTERNAL = typeof __HARD_CRM_INTERNAL__ !== "undefined" && __HARD_CRM_INTERNAL__;
      CRM_ANALYST_INTERNAL = typeof __HARD_CRM_ANALYST_INTERNAL__ !== "undefined" && __HARD_CRM_ANALYST_INTERNAL__;
      built = typeof __HARD_PRODUCT__ === "undefined" ? "" : __HARD_PRODUCT__;
      PRODUCTS = {
        crm: { id: "crm", name: CRM_ANALYST_INTERNAL ? "\u0412\u0437\u044B\u0441\u043A\u0430\u043D\u0438\u0435 \u2014 \u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A (\u0432\u043D\u0443\u0442\u0440\u0435\u043D\u043D\u0438\u0439)" : CRM_INTERNAL ? "\u0412\u0437\u044B\u0441\u043A\u0430\u043D\u0438\u0435 \u2014 \u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C (\u0432\u043D\u0443\u0442\u0440\u0435\u043D\u043D\u0438\u0439)" : "\u0412\u0437\u044B\u0441\u043A\u0430\u043D\u0438\u0435 \u2014 CRM \u043F\u0438\u043B\u043E\u0442", mode: "field", appId: CRM_ANALYST_INTERNAL ? "ru.rimborso.crm.analyst.internal" : CRM_INTERNAL ? "ru.rimborso.crm.manager.internal" : "ru.rimborso.crm.pilot", dataName: CRM_ANALYST_INTERNAL ? "crm-analyst-internal" : CRM_INTERNAL ? "crm-manager-internal" : "crm-pilot" },
        hard: { id: "hard", name: "\u0412\u0437\u044B\u0441\u043A\u0430\u043D\u0438\u0435 \u0425\u0430\u0440\u0434", mode: "field", appId: "ru.rimborso.hard.field", dataName: "hard" },
        soft: { id: "soft", name: "\u0412\u0437\u044B\u0441\u043A\u0430\u043D\u0438\u0435 \u0421\u043E\u0444\u0442", mode: "field", appId: "ru.rimborso.hard.soft", dataName: "soft" },
        manager: { id: "manager", name: "\u0412\u0437\u044B\u0441\u043A\u0430\u043D\u0438\u0435 \u0421\u041D\u0413 \u2014 \u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C", mode: "manager", appId: "ru.rimborso.hard.manager", dataName: "manager" },
        analyst: { id: "analyst", name: "\u0412\u0437\u044B\u0441\u043A\u0430\u043D\u0438\u0435 \u0421\u041D\u0413 \u2014 \u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A", mode: "analyst", appId: "ru.rimborso.hard.analyst", dataName: "analyst" }
      };
      PRODUCT_ID = built in PRODUCTS ? built : null;
      FIXED_PRODUCT = PRODUCT_ID ? PRODUCTS[PRODUCT_ID] : null;
    }
  });

  // hard-desk/ui/analyst/system.ts
  var OPS_TITLE, CONFIRM, maint;
  var init_system = __esm({
    "hard-desk/ui/analyst/system.ts"() {
      "use strict";
      init_api();
      init_dom();
      init_shell();
      init_lib();
      init_modeswitch();
      init_owner_access();
      init_product();
      OPS_TITLE = { doctor: "\u0414\u0438\u0430\u0433\u043D\u043E\u0441\u0442\u0438\u043A\u0430", logs: "\u0416\u0443\u0440\u043D\u0430\u043B", backup: "\u041A\u043E\u043F\u0438\u044F", restart: "\u041F\u0435\u0440\u0435\u0437\u0430\u043F\u0443\u0441\u043A", update: "\u041E\u0431\u043D\u043E\u0432\u043B\u0435\u043D\u0438\u0435", restore: "\u0412\u043E\u0441\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D\u0438\u0435" };
      CONFIRM = {
        restart: ["\u041F\u0435\u0440\u0435\u0437\u0430\u043F\u0443\u0441\u0442\u0438\u0442\u044C \u0435\u0434\u0438\u043D\u0438\u0446\u0443?", "\u041A\u043E\u043D\u0442\u0435\u0439\u043D\u0435\u0440\u044B \u043F\u043E\u0434\u043D\u0438\u043C\u0443\u0442\u0441\u044F \u0437\u0430\u043D\u043E\u0432\u043E, \u0434\u0430\u043D\u043D\u044B\u0435 \u043D\u0435 \u0442\u0440\u043E\u0433\u0430\u044E\u0442\u0441\u044F."],
        update: ["\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C \u0435\u0434\u0438\u043D\u0438\u0446\u0443?", "\u0421\u043D\u0430\u0447\u0430\u043B\u0430 \u0441\u043D\u0438\u043C\u0435\u0442\u0441\u044F \u043A\u043E\u043F\u0438\u044F \u0431\u0430\u0437\u044B, \u043F\u043E\u0442\u043E\u043C \u043F\u0435\u0440\u0435\u0441\u0431\u043E\u0440\u043A\u0430 \u0438 \u043F\u0435\u0440\u0435\u0437\u0430\u043F\u0443\u0441\u043A."],
        restore: ["\u0412\u043E\u0441\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C \u0438\u0437 \u043A\u043E\u043F\u0438\u0438?", "\u0422\u0435\u043A\u0443\u0449\u0430\u044F \u0431\u0430\u0437\u0430 \u043E\u0442\u043B\u043E\u0436\u0438\u0442\u0441\u044F \u0440\u044F\u0434\u043E\u043C, \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u0435 \u043F\u0435\u0440\u0435\u0437\u0430\u043F\u0443\u0441\u0442\u0438\u0442\u0441\u044F."]
      };
      maint = {
        id: "maint",
        title: "\u041E\u0431\u0441\u043B\u0443\u0436\u0438\u0432\u0430\u043D\u0438\u0435",
        icon: "service",
        bottom: true,
        kicker: "\u0441\u0435\u0440\u0432\u0435\u0440 \u0431\u0435\u0437 SSH",
        async render(root) {
          root.innerHTML = '<div class="skeleton"></div>';
          let d;
          try {
            d = await adm("GET", "/ops/state.json");
          } catch (e) {
            root.innerHTML = `<div class="notice err">${esc7(e.message)}</div>`;
            return;
          }
          const agent = d.agent || {};
          const inv = d.inventory || {};
          const units = inv.units || [];
          let unit = units[0]?.unit || "";
          const buttons = d.buttons || [];
          const draw3 = () => {
            const cur = units.find((u) => u.unit === unit) || {};
            root.innerHTML = `
        <div class="notice ${agent.alive ? "" : "err"}"><span class="grow">${agent.alive ? `\u041F\u043E\u043C\u043E\u0449\u043D\u0438\u043A \u043D\u0430 \u0445\u043E\u0441\u0442\u0435 \u043E\u0442\u0432\u0435\u0447\u0430\u0435\u0442 (\u043D\u0430 \u0441\u0432\u044F\u0437\u0438 ${esc7(agent.seen || "\u2014")})` : `\u041F\u043E\u043C\u043E\u0449\u043D\u0438\u043A \u043D\u0430 \u0445\u043E\u0441\u0442\u0435 \u043D\u0435 \u043E\u0442\u0432\u0435\u0447\u0430\u0435\u0442: ${esc7(agent.reason || "\u043D\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043D\u043E")}`}</span>
          <span class="muted">\u0441\u0432\u043E\u0431\u043E\u0434\u043D\u043E \u043D\u0430 \u0434\u0438\u0441\u043A\u0435: ${esc7(inv.disk_free || "\u2014")}</span></div>
        <div class="section"><div class="section-head"><h2 class="section-title">\u0415\u0434\u0438\u043D\u0438\u0446\u044B</h2></div>
          <div class="chips">${units.map((u) => `<button class="chip" data-unit="${esc7(u.unit)}" aria-pressed="${u.unit === unit}">${esc7(u.unit)} <span class="n">${(u.backups || []).length} \u043A\u043E\u043F.</span></button>`).join("")}</div>
          <div class="toolbar" style="margin-top:12px"><span class="muted" style="font-size:13.5px">\u041A\u043E\u043F\u0438\u044F \u0434\u043B\u044F \u0432\u043E\u0441\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D\u0438\u044F:</span>
            <select class="input" id="o-backup" style="width:260px">${(cur.backups || []).map((b) => `<option>${esc7(b)}</option>`).join("")}</select></div>
          <div class="toolbar">${buttons.map((b) => `<button class="btn ${b.kind === "danger" ? "danger" : ""}" data-op="${esc7(b.action)}" title="${esc7(b.hint || "")}">${esc7(b.title || b.action)}</button>`).join("")}</div>
          ${(d.pending || []).length ? `<p class="muted">\u0412 \u043E\u0447\u0435\u0440\u0435\u0434\u0438: ${esc7(d.pending.map((p) => `${p.action}\xB7${p.unit || ""}`).join(", "))}</p>` : ""}</div>
        <div class="section"><div class="section-head"><h2 class="section-title">\u0416\u0443\u0440\u043D\u0430\u043B \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438</h2></div>
          <pre id="o-log" style="white-space:pre-wrap;font:13px/1.5 var(--mono);border:var(--lw) solid var(--line-ink);border-radius:14px;padding:12px 14px;background:var(--surface);max-height:360px;overflow:auto">\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044E.</pre></div>
        <div class="section"><h2>\u041F\u0440\u0435\u0436\u043D\u0438\u0435 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430</h2><p class="muted">\u041B\u0438\u0447\u043D\u044B\u0435 \u043F\u0440\u043E\u0444\u0438\u043B\u0438 \u0443\u043F\u0440\u0430\u0432\u043B\u044F\u044E\u0442\u0441\u044F \u0432 \u0440\u0430\u0437\u0434\u0435\u043B\u0435 \xAB\u041F\u0440\u043E\u0444\u0438\u043B\u0438 \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430\xBB. \u0421\u0442\u0430\u0440\u044B\u0435 \u043E\u043F\u0435\u0440\u0430\u0442\u043E\u0440\u0441\u043A\u0438\u0435 \u043A\u043E\u0434\u044B \u0431\u043E\u043B\u044C\u0448\u0435 \u043D\u0435 \u0432\u044B\u0434\u0430\u044E\u0442\u0441\u044F.</p><div id="o-devs"></div></div>`;
            root.querySelectorAll("[data-unit]").forEach((b) => b.addEventListener("click", () => {
              unit = b.dataset.unit;
              draw3();
            }));
            root.querySelectorAll("[data-op]").forEach((b) => b.addEventListener("click", guard(async () => {
              const action3 = b.dataset.op;
              const params = { unit };
              if (action3 === "restore") {
                const backup = root.querySelector("#o-backup").value;
                if (!backup) {
                  toast("\u041A\u043E\u043F\u0438\u0439 \u043D\u0435\u0442 \u2014 \u0441\u043D\u0430\u0447\u0430\u043B\u0430 \u0441\u043D\u0438\u043C\u0438\u0442\u0435 \u043A\u043E\u043F\u0438\u044E");
                  return;
                }
                params.backup = backup;
              }
              const c = CONFIRM[action3];
              if (c && !await ask(c[0], `\xAB${unit}\xBB: ${c[1]}`, "\u0412\u044B\u043F\u043E\u043B\u043D\u0438\u0442\u044C", true)) return;
              const r = await adm("POST", "/ops/run.json", { action: action3, params });
              const log = root.querySelector("#o-log");
              const title = `${OPS_TITLE[action3] || action3} \xB7 ${unit}`;
              log.textContent = title + ": \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0430\u2026";
              const id = r.id;
              for (let i = 0; i < 300; i++) {
                await new Promise((res) => setTimeout(res, 2e3));
                if (!root.isConnected) return;
                const x = await adm("GET", `/ops/result.json?id=${encodeURIComponent(id)}`);
                if (x.done) {
                  log.textContent = `${title} \u2014 ${x.exit === 0 ? "\u0433\u043E\u0442\u043E\u0432\u043E" : "\u043A\u043E\u0434 \u0432\u043E\u0437\u0432\u0440\u0430\u0442\u0430 " + x.exit}

${x.output || "(\u0431\u0435\u0437 \u0432\u044B\u0432\u043E\u0434\u0430)"}`;
                  break;
                }
              }
            })));
            void (async () => {
              try {
                const x = await adm("GET", "/ops/devices.json");
                const list = x.devices || [];
                const lines = await Promise.all(list.map((dv) => fn("devices.device_line", dv)));
                const box = root.querySelector("#o-devs");
                if (!box) return;
                box.innerHTML = list.map((dv, i) => `<div class="list-row"><div class="grow"><div class="nm">${esc7(lines[i][0])}</div><div class="sub">${esc7(lines[i][1])}</div></div>
            <button class="btn small danger" data-rev="${esc7(dv.id)}" data-name="${esc7(dv.name)}">\u041E\u0442\u043E\u0437\u0432\u0430\u0442\u044C</button></div>`).join("");
                box.querySelectorAll("[data-rev]").forEach((b) => b.addEventListener("click", guard(async () => {
                  if (!await ask("\u041E\u0442\u043E\u0437\u0432\u0430\u0442\u044C \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u043E?", await fn("devices.revoke_text", b.dataset.name, Number(b.dataset.rev)), "\u041E\u0442\u043E\u0437\u0432\u0430\u0442\u044C", true)) return;
                  await adm("POST", "/ops/device/revoke.json", { id: Number(b.dataset.rev) });
                  toast("\u041E\u0442\u043E\u0437\u0432\u0430\u043D\u043E");
                  await refresh();
                })));
              } catch {
              }
            })();
          };
          draw3();
        }
      };
    }
  });

  // hard-desk/ui/analyst/registry.ts
  function queueChoiceKey(op, book, index) {
    return JSON.stringify([op.id, book.review_token, book.ordinal, index]);
  }
  function reviewRows() {
    return st2.check ? [...st2.check.conflicts, ...st2.check.office] : [];
  }
  function setReviewChoice(row, choice) {
    st2.decisions[decisionKey(row)] = choice;
    if (["promise_date", "forecast_sum"].includes(row.key)) {
      for (const other of reviewRows()) if (other.book_path === row.book_path && other.contract_id === row.contract_id && ["promise_date", "forecast_sum"].includes(other.key)) {
        st2.decisions[decisionKey(other)] = choice;
      }
    }
  }
  async function showDeliveryQueue(root, generation) {
    if (!root.isConnected || activeRoot !== root || generation !== queueGeneration) return;
    const host = root.querySelector("[data-delivery-queue]");
    if (!host) return;
    try {
      const operation = await local("/enr/operation");
      if (generation !== queueGeneration || activeRoot !== root) return;
      deliveryOperation = operation?.id ? operation : null;
      if (deliveryOperation?.id !== presentedQueueRun) {
        presentedQueueRun = deliveryOperation?.id || "";
        queueDetailsOpen = !!deliveryOperation?.attention;
      }
      const signature = JSON.stringify([deliveryOperation, queueReconciliation, hiddenQueueRun]);
      if (host.dataset.signature !== signature && !(host.contains(document.activeElement) && document.activeElement?.tagName === "SELECT")) {
        host.dataset.signature = signature;
        const op = deliveryOperation;
        host.innerHTML = !op || hiddenQueueRun === op.id ? "" : `<div class="notice${op.attention ? " err" : ""}" role="status"><span class="grow">${esc7(op.remaining ? `\u041F\u0435\u0440\u0435\u043D\u043E\u0441 \u043D\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043D. \u041F\u0440\u043E\u0432\u0435\u0440\u0435\u043D\u043E \u043A\u043D\u0438\u0433: ${op.completed} \u0438\u0437 ${op.total}. \u041E\u0441\u0442\u0430\u043B\u043E\u0441\u044C: ${op.remaining}.` : `\u041E\u0431\u043E\u0433\u0430\u0449\u0435\u043D\u0438\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u043E. \u041F\u0440\u043E\u0432\u0435\u0440\u0435\u043D\u043E \u043A\u043D\u0438\u0433: ${op.total}. \u0417\u0430\u043F\u0438\u0441\u0430\u043D\u043E \u044F\u0447\u0435\u0435\u043A: ${op.cells}.`)}${op.excluded ? ` \u041F\u0440\u043E\u043F\u0443\u0449\u0435\u043D\u043E \u0441\u0442\u0440\u043E\u043A: ${n(op.excluded)}. \u0412 \u0434\u043E\u0441\u0442\u0430\u0432\u043B\u0435\u043D\u043D\u044B\u0435 \u044D\u0442\u0438 \u0441\u0442\u0440\u043E\u043A\u0438 \u043D\u0435 \u0432\u0445\u043E\u0434\u044F\u0442.` : ""}</span>${!op.remaining ? '<button class="btn small" data-queue-dismiss>\u0421\u043A\u0440\u044B\u0442\u044C</button>' : ""}</div>
        ${(op.books || []).filter((b) => b.state !== "done").map((b) => `<p data-queue-issue><b>${esc7(b.employee)}:</b> ${esc7(b.reason_code === "busy" ? `\u043E\u0442\u043A\u0440\u044B\u0442\u0430 \u043A\u043D\u0438\u0433\u0430 ${String(b.file).split(/[\\/]/).pop()}. \u0417\u0430\u043A\u0440\u043E\u0439\u0442\u0435 \u0435\u0451 \u2014 \u043F\u0435\u0440\u0435\u043D\u043E\u0441 \u043F\u0440\u043E\u0434\u043E\u043B\u0436\u0438\u0442\u0441\u044F \u0430\u0432\u0442\u043E\u043C\u0430\u0442\u0438\u0447\u0435\u0441\u043A\u0438.` : b.conflicts?.length ? `\u0438\u0437\u043C\u0435\u043D\u0438\u043B\u0438\u0441\u044C \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F \u0432 \u043A\u043D\u0438\u0433\u0435: ${b.conflicts.length}. \u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F \u043D\u0438\u0436\u0435.` : b.message || "\u041E\u0436\u0438\u0434\u0430\u0435\u0442 \u043E\u0431\u0440\u0430\u0431\u043E\u0442\u043A\u0438")}</p>`).join("")}
        ${op.processing ? '<p role="status">\u0417\u0430\u0432\u0435\u0440\u0448\u0430\u044E \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0443 Excel. \u0414\u0435\u0439\u0441\u0442\u0432\u0438\u044F \u0441\u0442\u0430\u043D\u0443\u0442 \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B \u043F\u043E\u0441\u043B\u0435 \u043E\u043A\u043E\u043D\u0447\u0430\u043D\u0438\u044F \u043F\u0440\u043E\u0445\u043E\u0434\u0430.</p>' : ""}
        <details data-queue-details ${queueDetailsOpen ? "open" : ""}><summary>\u041F\u043E\u0434\u0440\u043E\u0431\u043D\u043E\u0441\u0442\u0438 \u0438 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044F</summary>
        ${op.busy ? "<p>\u041E\u0442\u043A\u0440\u044B\u0442\u044B\u0435 \u043A\u043D\u0438\u0433\u0438 \u0431\u0443\u0434\u0443\u0442 \u043E\u0431\u0440\u0430\u0431\u043E\u0442\u0430\u043D\u044B \u043F\u043E\u0441\u043B\u0435 \u0437\u0430\u043A\u0440\u044B\u0442\u0438\u044F Excel.</p>" : ""}
        ${op.remaining ? "<p>\u0418\u043C\u043F\u043E\u0440\u0442 \u0441\u0442\u0430\u043D\u0435\u0442 \u0434\u043E\u0441\u0442\u0443\u043F\u0435\u043D \u043F\u043E\u0441\u043B\u0435 \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0438 \u043E\u0441\u0442\u0430\u0432\u0448\u0438\u0445\u0441\u044F \u043A\u043D\u0438\u0433.</p>" : ""}
        ${(op.books || []).filter((b) => b.state !== "done" || b.omissions?.length).map((b) => `<div class="list-row" style="display:block;overflow-wrap:anywhere"><b>${esc7(b.employee)} \u2014 ${esc7(String(b.file).split(/[\\/]/).pop())}</b><p>${esc7(b.message || "\u041E\u0436\u0438\u0434\u0430\u0435\u0442 \u043E\u0431\u0440\u0430\u0431\u043E\u0442\u043A\u0438")}</p>
          ${b.omissions?.length ? `<p>\u041F\u0440\u043E\u043F\u0443\u0449\u0435\u043D\u043E \u0441\u0442\u0440\u043E\u043A: ${n(b.omissions.length)}. \u0418\u0441\u0445\u043E\u0434\u043D\u044B\u0435 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u044B \u0432 \u0436\u0443\u0440\u043D\u0430\u043B\u0435 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438.</p><div class="table-wrap"><table class="grid"><thead><tr><th>\u0414\u043E\u0433\u043E\u0432\u043E\u0440</th><th>\u041F\u0440\u0438\u0447\u0438\u043D\u0430</th></tr></thead><tbody>${b.omissions.map((row) => `<tr><td>${esc7(row.contract_id)}</td><td>${esc7(row.reason === "absent_from_book" ? "\u041D\u0435\u0442 \u0441\u0442\u0440\u043E\u043A\u0438 \u0432 \u043B\u0438\u0447\u043D\u043E\u0439 \u043A\u043D\u0438\u0433\u0435" : row.reason === "absent_from_master" ? "\u0414\u043E\u0433\u043E\u0432\u043E\u0440 \u043E\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u0435\u0442" : row.current_owner ? `\u0422\u0435\u043F\u0435\u0440\u044C: ${row.current_owner}` : "\u041E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u044B\u0439 \u0441\u043D\u044F\u0442")}</td></tr>`).join("")}</tbody></table></div>` : ""}
          ${b.conflicts?.length ? `<div class="table-wrap"><table class="grid"><thead><tr><th class="l">\u0414\u043E\u0433\u043E\u0432\u043E\u0440 / \u043F\u043E\u043B\u0435</th><th class="l">\u0412 \u043A\u043D\u0438\u0433\u0435</th><th class="l">\u0412 \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u0438</th><th class="l">\u0414\u0430\u0442\u0430 \u0444\u0430\u043A\u0442\u0430</th><th class="l">\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435</th></tr></thead><tbody>${b.conflicts.map((c, index) => `<tr><td>${esc7(c.contract_id)} \xB7 ${esc7(c.column || c.key)}</td><td>${esc7(c.file)}</td><td>${esc7(c.server)}</td><td>${esc7(c.visit_date || "")}</td><td><select class="input" data-queue-choice="${b.ordinal}:${index}"><option value="file">\u041E\u0441\u0442\u0430\u0432\u0438\u0442\u044C \u0432 \u043A\u043D\u0438\u0433\u0435</option><option value="server" ${queueChoices.get(queueChoiceKey(op, b, index)) === "server" ? "selected" : ""}>\u0417\u0430\u043F\u0438\u0441\u0430\u0442\u044C \u0441\u0435\u0440\u0432\u0435\u0440</option></select></td></tr>`).join("")}</tbody></table></div><button class="btn small" data-queue-resolve="${b.ordinal}">\u041F\u0440\u0438\u043C\u0435\u043D\u0438\u0442\u044C \u0440\u0435\u0448\u0435\u043D\u0438\u044F</button>` : ""}</div>`).join("")}
        ${op.missing ? '<p>\u041E\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u044E\u0449\u0438\u0435 \u0441\u0442\u0440\u043E\u043A\u0438 \u043C\u043E\u0433\u0443\u0442 \u043E\u0442\u043D\u043E\u0441\u0438\u0442\u044C\u0441\u044F \u043A \u043F\u0440\u0435\u0436\u043D\u0438\u043C \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F\u043C. \u0421\u0432\u0435\u0440\u043A\u0430 \u043F\u043E\u043A\u0430\u0436\u0435\u0442 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u044F \u043F\u043E \u0442\u0435\u043A\u0443\u0449\u0435\u043C\u0443 \u043E\u0431\u0449\u0435\u043C\u0443 \u0440\u0435\u0435\u0441\u0442\u0440\u0443 \u0434\u043E \u043F\u0440\u0438\u043C\u0435\u043D\u0435\u043D\u0438\u044F \u0438\u0441\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0439.</p><button class="btn small" data-queue-reconcile>\u0421\u0432\u0435\u0440\u0438\u0442\u044C \u0441 \u043E\u0431\u0449\u0438\u043C \u0440\u0435\u0435\u0441\u0442\u0440\u043E\u043C\u2026</button>' : ""}
        ${queueReconciliation?.run_id === op.id ? `<div class="block"><b>\u0421\u0432\u0435\u0440\u043A\u0430: ${esc7(String(queueReconciliation.source_path).split(/[\\/]/).pop())}</b><p>\u0418\u0437\u043C\u0435\u043D\u0438\u043B\u0438\u0441\u044C \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F \u0438\u043B\u0438 \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u044B \u043E\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u044E\u0442: ${n(queueReconciliation.proposals.length)}. \u0422\u0440\u0435\u0431\u0443\u044E\u0442 \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0438 \u0441\u043E\u0441\u0442\u0430\u0432\u0430 \u043A\u043D\u0438\u0433\u0438: ${n(queueReconciliation.blocked.length)}.</p>
          <p>\u0418\u0441\u043A\u043B\u044E\u0447\u0451\u043D\u043D\u044B\u0435 \u0441\u0442\u0440\u043E\u043A\u0438 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u044E\u0442\u0441\u044F \u0432 \u043E\u0442\u0447\u0451\u0442\u0435 \u0438 \u043D\u0435 \u0441\u0447\u0438\u0442\u0430\u044E\u0442\u0441\u044F \u0434\u043E\u0441\u0442\u0430\u0432\u043B\u0435\u043D\u043D\u044B\u043C\u0438. \u041E\u0441\u0442\u0430\u043B\u044C\u043D\u044B\u0435 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B \u0431\u0443\u0434\u0443\u0442 \u043F\u0435\u0440\u0435\u043D\u0435\u0441\u0435\u043D\u044B.</p>
          <div class="table-wrap"><table class="grid"><thead><tr><th>\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A / \u0434\u043E\u0433\u043E\u0432\u043E\u0440</th><th>\u041F\u0440\u0438\u0447\u0438\u043D\u0430</th></tr></thead><tbody>${[...queueReconciliation.proposals, ...queueReconciliation.blocked].map((row) => `<tr><td>${esc7(row.employee)} \xB7 ${esc7(row.contract_id)}</td><td>${esc7(row.reason === "absent_from_master" ? "\u0414\u043E\u0433\u043E\u0432\u043E\u0440 \u043E\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u0435\u0442" : row.reason === "assignment_changed" ? row.current_owner ? `\u0422\u0435\u043F\u0435\u0440\u044C: ${row.current_owner}` : "\u041E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u044B\u0439 \u0441\u043D\u044F\u0442" : row.reason)}</td></tr>`).join("")}</tbody></table></div>
          <button class="btn small" data-queue-reconcile-apply ${queueReconciliation.proposals.length ? "" : "disabled"}>\u041F\u0440\u0438\u043D\u044F\u0442\u044C \u0438\u0441\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u044F \u0438 \u043F\u0440\u043E\u0434\u043E\u043B\u0436\u0438\u0442\u044C \u043F\u0435\u0440\u0435\u043D\u043E\u0441</button></div>` : ""}
        ${op.remaining ? '<button class="btn small" data-queue-retry>\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0443 \u043E\u0448\u0438\u0431\u043E\u043A</button>' : ""}</details>`;
        host.querySelector("[data-queue-details]")?.addEventListener("toggle", (event) => {
          queueDetailsOpen = event.target.open;
        });
        host.querySelectorAll("[data-queue-choice]").forEach((select) => select.addEventListener("change", () => {
          const [ordinal, index] = select.dataset.queueChoice.split(":").map(Number);
          const book = op.books.find((b) => b.ordinal === ordinal), row = book.conflicts[index];
          book.conflicts.forEach((other, i) => {
            if (i === index || other.contract_id === row.contract_id && ["promise_date", "forecast_sum"].includes(row.key) && ["promise_date", "forecast_sum"].includes(other.key)) {
              queueChoices.set(queueChoiceKey(op, book, i), select.value);
              host.querySelector(`[data-queue-choice="${ordinal}:${i}"]`).value = select.value;
            }
          });
        }));
        host.querySelector("[data-queue-dismiss]")?.addEventListener("click", () => {
          hiddenQueueRun = op.id;
          host.dataset.signature = "";
          void showDeliveryQueue(root, generation);
        });
        host.querySelector("[data-queue-reconcile]")?.addEventListener("click", guard(async () => {
          const path = await hard.pick("file", { title: "\u0422\u0435\u043A\u0443\u0449\u0438\u0439 \u043E\u0431\u0449\u0438\u0439 \u0440\u0435\u0435\u0441\u0442\u0440", filters: XLS });
          if (!path) return;
          queueReconciliation = await job2("/enr/reconcile-preview", { path });
          queueDetailsOpen = true;
          host.dataset.signature = "";
          await showDeliveryQueue(root, generation);
        }));
        host.querySelector("[data-queue-reconcile-apply]")?.addEventListener("click", guard(async () => {
          await local("/enr/reconcile-apply", { token: queueReconciliation.token });
          queueReconciliation = null;
          host.dataset.signature = "";
          await showDeliveryQueue(root, generation);
        }));
        host.querySelector("[data-queue-retry]")?.addEventListener("click", guard(async () => {
          await local("/enr/retry", { id: op.id });
          host.dataset.signature = "";
          await showDeliveryQueue(root, generation);
        }));
        host.querySelectorAll("[data-queue-resolve]").forEach((button2) => button2.addEventListener("click", guard(async () => {
          const book = op.books.find((b) => b.ordinal === Number(button2.dataset.queueResolve));
          const decisions = book.conflicts.map((c, index) => ({
            book_path: book.file,
            contract_id: c.contract_id,
            key: c.key,
            decision: host.querySelector(`[data-queue-choice="${book.ordinal}:${index}"]`).value
          }));
          await local("/enr/retry", { id: op.id, review_token: book.review_token, decisions });
          host.dataset.signature = "";
          await showDeliveryQueue(root, generation);
        })));
      }
      root.querySelectorAll(st2.tab === "enrich" ? "[data-apply],[data-check],[data-scan]" : "[data-apply],[data-preview]").forEach((button2) => {
        if (deliveryOperation?.remaining || deliveryOperation?.processing) {
          if (button2.dataset.deliveryBlocked === void 0) button2.dataset.deliveryBlocked = button2.disabled ? "1" : "0";
          button2.disabled = true;
        } else if (button2.dataset.deliveryBlocked !== void 0) {
          button2.disabled = button2.dataset.deliveryBlocked === "1";
          delete button2.dataset.deliveryBlocked;
        }
      });
      root.querySelectorAll("[data-pick],[data-queue-reconcile],[data-queue-reconcile-apply],[data-queue-retry]").forEach((button2) => {
        if (deliveryOperation?.processing) {
          if (button2.dataset.deliveryProcessing === void 0) button2.dataset.deliveryProcessing = button2.disabled ? "1" : "0";
          button2.disabled = true;
        } else if (button2.dataset.deliveryProcessing !== void 0) {
          button2.disabled = button2.dataset.deliveryProcessing === "1";
          delete button2.dataset.deliveryProcessing;
        }
      });
    } catch (error) {
      delete host.dataset.signature;
      host.innerHTML = `<div class="notice err">\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u043E\u0447\u0435\u0440\u0435\u0434\u044C \u043F\u0435\u0440\u0435\u043D\u043E\u0441\u0430: ${esc7(String(error.message || error))}</div>`;
    } finally {
      if (root.isConnected && generation === queueGeneration && activeRoot === root) {
        clearTimeout(queueTimer);
        queueTimer = setTimeout(() => void showDeliveryQueue(root, generation), 2e3);
      }
    }
  }
  function showProgress() {
    if (!activeRoot?.isConnected) return;
    const status2 = activeRoot.querySelector("[data-registry-progress]");
    if (status2) {
      status2.hidden = !st2.busy;
      status2.textContent = st2.progress;
    }
    activeRoot.querySelectorAll("#rtab input,#rtab textarea,#rtab select,#rtab button").forEach((control) => {
      if (st2.busy) {
        if (control.dataset.jobDisabled === void 0) control.dataset.jobDisabled = control.disabled ? "1" : "0";
        control.disabled = true;
      } else if (control.dataset.jobDisabled !== void 0) {
        control.disabled = control.dataset.jobDisabled === "1";
        delete control.dataset.jobDisabled;
      }
    });
  }
  async function job2(path, body, log) {
    if (st2.busy) throw new Error("\u041E\u043F\u0435\u0440\u0430\u0446\u0438\u044F \u0443\u0436\u0435 \u0432\u044B\u043F\u043E\u043B\u043D\u044F\u0435\u0442\u0441\u044F. \u0414\u043E\u0436\u0434\u0438\u0442\u0435\u0441\u044C \u0435\u0451 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u0430.");
    st2.busy = true;
    st2.progress = "\u041E\u043F\u0435\u0440\u0430\u0446\u0438\u044F \u0432\u044B\u043F\u043E\u043B\u043D\u044F\u0435\u0442\u0441\u044F\u2026";
    showProgress();
    try {
      return await runJob(path, body, (line) => {
        st2.progress = line;
        showProgress();
        log?.(line);
      });
    } finally {
      st2.busy = false;
      showProgress();
    }
  }
  function tabs() {
    return chipsHtml([
      ["import", "\u0418\u043C\u043F\u043E\u0440\u0442 \u0440\u0435\u0435\u0441\u0442\u0440\u0430"],
      ["enrich", "\u041E\u0431\u043E\u0433\u0430\u0449\u0435\u043D\u0438\u0435 \u043A\u043D\u0438\u0433"],
      ["columns", "\u041A\u043E\u043B\u043E\u043D\u043A\u0438 \u0440\u0435\u0435\u0441\u0442\u0440\u0430"],
      ["export", "\u0412\u044B\u0433\u0440\u0443\u0437\u043A\u0438 \u0438 \u043F\u0435\u0440\u0438\u043E\u0434"],
      ["recalled", "\u041E\u0442\u043E\u0437\u0432\u0430\u043D\u043D\u044B\u0435"]
    ].filter(([k]) => !embeddedTabs || embeddedTabs.includes(k)).map(([k, t]) => ({ key: k, text: t, on: st2.tab === k })), "tab");
  }
  function pairs(p) {
    return p && p.length ? kvHtml(p.map(([k, v]) => [k, typeof v === "number" ? n(v) : v])) : "";
  }
  async function importTab(el) {
    const book = st2.imp;
    const employees = (book?.employees || []).filter((e) => {
      const file = Number(e.file_count || 0), db = Number(e.db_count || 0);
      return (!st2.impQuery || e.name.toLocaleLowerCase("ru").includes(st2.impQuery.toLocaleLowerCase("ru"))) && (st2.impFilter === "all" || st2.impFilter === "selected" && st2.sel.has(e.key) || st2.impFilter === "existing" && file && db || st2.impFilter === "new" && file && !db || st2.impFilter === "db_only" && !file && db);
    }).sort((a, b) => {
      const numeric = st2.impSort === "file" ? b.file_count - a.file_count : st2.impSort === "db" ? b.db_count - a.db_count : st2.impSort === "selected" ? Number(st2.sel.has(b.key)) - Number(st2.sel.has(a.key)) : 0;
      return numeric || a.name.localeCompare(b.name, "ru");
    });
    const warn = [];
    warn.push("\u041F\u041E\u041B\u041D\u042B\u0419 \u0421\u0420\u0415\u0417: \u0443 \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u0445 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432 \u0431\u0443\u0434\u0443\u0442 \u0441\u043D\u044F\u0442\u044B \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u044B, \u043A\u043E\u0442\u043E\u0440\u044B\u0445 \u043D\u0435\u0442 \u0441\u0440\u0435\u0434\u0438 \u0438\u0445 \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u0445 \u0441\u0442\u0440\u043E\u043A. \u041E\u0441\u0442\u0430\u043B\u044C\u043D\u044B\u0435 \u043D\u0435 \u0437\u0430\u0442\u0440\u0430\u0433\u0438\u0432\u0430\u044E\u0442\u0441\u044F.");
    warn.push("\u0412\u041E\u0417\u0412\u0420\u0410\u0422 \u0420\u0423\u0427\u041D\u042B\u0425 \u0421\u041D\u042F\u0422\u0418\u0419: \u0432\u0435\u0440\u043D\u0443\u0442\u0441\u044F \u0442\u043E\u043B\u044C\u043A\u043E \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u044B, \u043A\u043E\u0442\u043E\u0440\u044B\u0435 \u0441\u043D\u043E\u0432\u0430 \u0435\u0441\u0442\u044C \u0432 \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u0445 \u0441\u0442\u0440\u043E\u043A\u0430\u0445 \u0441 \u0442\u0435\u043C \u0436\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u043C.");
    if (st2.comments) warn.push("\u0422\u0435\u043A\u0443\u0449\u0438\u0435 \u043A\u043E\u043C\u043C\u0435\u043D\u0442\u0430\u0440\u0438\u0438 \u0431\u0443\u0434\u0443\u0442 \u0437\u0430\u043C\u0435\u043D\u0435\u043D\u044B \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F\u043C\u0438 \u0444\u0430\u0439\u043B\u0430, \u0432\u043A\u043B\u044E\u0447\u0430\u044F \u043F\u0443\u0441\u0442\u044B\u0435.");
    warn.push("\u041E\u0444\u0438\u0441\u043D\u044B\u0435 \u043F\u0440\u0430\u0432\u043A\u0438 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u043E\u0432 \u0421\u043E\u0444\u0442\u0430 \u0431\u0443\u0434\u0443\u0442 \u043F\u0440\u0438\u043C\u0435\u043D\u0435\u043D\u044B \u043A \u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043D\u043D\u044B\u043C \u0437\u0432\u043E\u043D\u043A\u0430\u043C. \u041F\u0440\u0435\u0434\u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440 \u043F\u043E\u043A\u0430\u0436\u0435\u0442 \u0442\u043E\u0447\u043D\u044B\u0435 \u0437\u0430\u043C\u0435\u043D\u044B.");
    if (warn.length) warn.push("\u0422\u043E\u0447\u043D\u044B\u0435 \u0447\u0438\u0441\u043B\u0430 \u043F\u0443\u043B\u044C\u0442 \u043F\u043E\u043A\u0430\u0436\u0435\u0442 \u0432 \u043F\u0440\u0435\u0434\u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440\u0435 \u0434\u043E \u0437\u0430\u043F\u0438\u0441\u0438.");
    el.innerHTML = `${note("\u041E\u0431\u0449\u0438\u0439 \u0440\u0435\u0435\u0441\u0442\u0440: \u043E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u043D\u043D\u044B\u0439 master \u0438\u043B\u0438 \u0443\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u043E\u0435 \u0442\u0435\u0441\u0442\u043E\u0432\u043E\u0435 \u043F\u043E\u0434\u043C\u043D\u043E\u0436\u0435\u0441\u0442\u0432\u043E. \u041B\u0438\u0447\u043D\u044B\u0435 \u0424\u0418\u041E.xlsb \u0441\u044E\u0434\u0430 \u043D\u0435 \u0437\u0430\u0433\u0440\u0443\u0436\u0430\u044E\u0442\u0441\u044F. \u0421\u043D\u0430\u0447\u0430\u043B\u0430 \u043F\u0440\u0435\u0434\u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440 \u2014 \u0437\u0430\u043F\u0438\u0441\u044C \u043E\u0442\u0434\u0435\u043B\u044C\u043D\u043E\u0439 \u043A\u043D\u043E\u043F\u043A\u043E\u0439.")}
    <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-pick>\u0412\u044B\u0431\u0440\u0430\u0442\u044C \u0440\u0435\u0435\u0441\u0442\u0440\u2026</button><span class="muted" id="imp-cap">${esc7(book?.caption || "")}</span></div>
    ${book ? `<div class="toolbar"><span class="muted" style="font-size:13.5px">\u0422\u0438\u043F \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432:</span>
        <select class="input" data-profile style="width:auto">${[["hard", "\u0425\u0430\u0440\u0434"], ["soft", "\u0421\u043E\u0444\u0442"], ["both", "\u041E\u0431\u0430 \u0442\u0438\u043F\u0430"]].map(([k, v]) => `<option value="${k}" ${book.profile === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <span class="muted" style="font-size:13.5px">\u041A\u043E\u0433\u043E \u0433\u0440\u0443\u0437\u0438\u0442\u044C:</span>
        <button class="btn small" data-sel="none">\u0441\u043D\u044F\u0442\u044C</button><button class="btn small" data-sel="existing">\u0435\u0441\u0442\u044C \u0432 \u0411\u0414</button><button class="btn small" data-sel="all">\u0432\u0441\u0435</button>
        <span class="spacer"></span><span class="muted" style="font-size:13px">\u043E\u0442\u043C\u0435\u0447\u0435\u043D\u043E ${n(st2.sel.size)} \u0438\u0437 ${n(book.employees.length)}</span></div>
      <details><summary>\u0418\u0441\u043A\u043B\u044E\u0447\u0438\u0442\u044C \u043E\u0442\u0434\u0435\u043B\u044C\u043D\u044B\u0435 \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u044B \u0438\u0437 \u0438\u043C\u043F\u043E\u0440\u0442\u0430</summary><p class="hint">\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043D\u043E\u043C\u0435\u0440\u0430 \u0432 \u0442\u043E\u0447\u043D\u043E\u0441\u0442\u0438 \u043A\u0430\u043A \u0432 \u0438\u0441\u0445\u043E\u0434\u043D\u043E\u043C \u0444\u0430\u0439\u043B\u0435, \u043F\u043E \u043E\u0434\u043D\u043E\u043C\u0443 \u043D\u0430 \u0441\u0442\u0440\u043E\u043A\u0443. \u041F\u0440\u0435\u0434\u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440 \u043F\u043E\u043A\u0430\u0436\u0435\u0442 \u0447\u0438\u0441\u043B\u043E \u0438\u0441\u043A\u043B\u044E\u0447\u0451\u043D\u043D\u044B\u0445 \u0441\u0442\u0440\u043E\u043A. \u041E\u0441\u0442\u0430\u043B\u044C\u043D\u044B\u0435 \u0434\u0430\u043D\u043D\u044B\u0435 \u0444\u0430\u0439\u043B\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u044E\u0442\u0441\u044F.</p>
        <textarea class="input" data-excluded-ids rows="3" aria-label="\u041D\u043E\u043C\u0435\u0440\u0430 \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432 \u0434\u043B\u044F \u0438\u0441\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u044F">${esc7(st2.excludedIds)}</textarea></details>
      <div class="toolbar"><input class="input search" data-imp-query placeholder="\u041F\u043E\u0438\u0441\u043A \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430" value="${esc7(st2.impQuery)}">
        <select class="input" data-imp-filter style="width:auto">${[["all", "\u0412\u0441\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438"], ["selected", "\u041E\u0442\u043C\u0435\u0447\u0435\u043D\u043D\u044B\u0435"], ["existing", "\u0415\u0441\u0442\u044C \u0432 \u0444\u0430\u0439\u043B\u0435 \u0438 \u0411\u0414"], ["new", "\u041D\u043E\u0432\u044B\u0435 \u0434\u043B\u044F \u0411\u0414"], ["db_only", "\u0422\u043E\u043B\u044C\u043A\u043E \u0432 \u0411\u0414"]].map(([k, v]) => `<option value="${k}" ${st2.impFilter === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <select class="input" data-imp-sort style="width:auto">${[["name", "\u041F\u043E \u0424\u0418\u041E"], ["file", "\u041F\u043E \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u0430\u043C \u0444\u0430\u0439\u043B\u0430"], ["db", "\u041F\u043E \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u0430\u043C \u0432 \u0411\u0414"], ["selected", "\u0421\u043D\u0430\u0447\u0430\u043B\u0430 \u043E\u0442\u043C\u0435\u0447\u0435\u043D\u043D\u044B\u0435"]].map(([k, v]) => `<option value="${k}" ${st2.impSort === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <span class="muted">\u0432\u0438\u0434\u043D\u043E ${n(employees.length)}</span></div>
      <div class="grid2" style="max-height:340px;overflow:auto;border-top:var(--lw) solid var(--line)">${employees.map((e) => `<label class="list-row" style="cursor:pointer;padding:8px 4px">
        <input type="checkbox" data-emp="${esc7(e.key)}" ${st2.sel.has(e.key) ? "checked" : ""}><span class="grow"><span class="nm">${esc7(e.name)}</span><br><span class="sub">${esc7(e.note)}</span></span></label>`).join("")}</div>
      <div class="block">
        <label class="list-row" style="cursor:pointer"><input type="checkbox" data-opt="comments" ${st2.comments ? "checked" : ""}><span class="grow">\u041D\u0430\u0447\u0430\u043B\u043E \u043C\u0435\u0441\u044F\u0446\u0430: \u0437\u0430\u043C\u0435\u043D\u0438\u0442\u044C \u043A\u043E\u043C\u043C\u0435\u043D\u0442\u0430\u0440\u0438\u0438 \u0432\u044B\u0435\u0437\u0434\u0430 \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F\u043C\u0438 \u0444\u0430\u0439\u043B\u0430 (\u043F\u0443\u0441\u0442\u0430\u044F \u044F\u0447\u0435\u0439\u043A\u0430 \u043E\u0447\u0438\u0441\u0442\u0438\u0442 \u043A\u043E\u043C\u043C\u0435\u043D\u0442\u0430\u0440\u0438\u0439)</span></label>
        <p class="muted" style="font-size:13px">\u0418\u0441\u0442\u043E\u0440\u0438\u0447\u043D\u044B\u0435 \u043A\u043E\u043C\u043C\u0435\u043D\u0442\u0430\u0440\u0438\u0438 \u0433\u0440\u0443\u0437\u044F\u0442\u0441\u044F \u0441\u0430\u043C\u0438, \u0435\u0441\u043B\u0438 \u043A\u043E\u043B\u043E\u043D\u043A\u0430 \u0435\u0441\u0442\u044C \u0432 \u0444\u0430\u0439\u043B\u0435: \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0435 \u043F\u043E\u0432\u0442\u043E\u0440\u044F\u0435\u0442 \u0444\u0430\u0439\u043B \u0442\u043E\u0447\u043D\u043E, \u043F\u0443\u0441\u0442\u0430\u044F \u044F\u0447\u0435\u0439\u043A\u0430 \u043E\u0447\u0438\u0449\u0430\u0435\u0442.</p></div>
      ${warn.length ? `<div class="notice" style="white-space:pre-line"><span class="grow">${esc7(warn.join("\n"))}</span></div>` : ""}
      <div class="toolbar"><button class="btn primary" data-preview ${st2.sel.size ? "" : "disabled"}>\u041F\u0440\u0435\u0434\u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440 \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u0445 (${n(st2.sel.size)})</button>
        <button class="btn" data-apply ${st2.preview && !st2.preview.error ? "" : "disabled"}>${esc7(st2.preview?.apply || "\u041F\u0440\u0438\u043C\u0435\u043D\u0438\u0442\u044C \u0438\u043C\u043F\u043E\u0440\u0442")}</button>
        <span class="muted" id="imp-log"></span></div>
      ${st2.preview ? st2.preview.error ? `<div class="notice err" style="white-space:pre-line"><span class="grow">${esc7(st2.preview.error)}</span></div>` : `${pairs(st2.preview.pairs)}<div class="notice" style="white-space:pre-line;margin-top:12px"><span class="grow">${esc7((st2.preview.notes || []).join("\n"))}</span></div>` : ""}
      ${st2.applied ? `${pairs(st2.applied.pairs)}<div class="notice" style="white-space:pre-line;margin-top:12px"><span class="grow">${esc7((st2.applied.notes || []).join("\n"))}</span></div>` : ""}` : ""}`;
    const redraw = () => importTab(el);
    const invalidate = () => {
      st2.preview = null;
      st2.applied = null;
    };
    el.querySelector("[data-imp-query]")?.addEventListener("input", (event) => {
      const input = event.target;
      const pos = input.selectionStart;
      st2.impQuery = input.value;
      void redraw();
      const next = el.querySelector("[data-imp-query]");
      next?.focus();
      next?.setSelectionRange(pos, pos);
    });
    for (const [attr, key2] of [["data-imp-filter", "impFilter"], ["data-imp-sort", "impSort"]]) {
      el.querySelector(`[${attr}]`)?.addEventListener("change", (event) => {
        st2[key2] = event.target.value;
        void redraw();
      });
    }
    el.querySelector("[data-pick]")?.addEventListener("click", guard(async () => {
      const path = await hard.pick("file", { title: "\u0420\u0435\u0435\u0441\u0442\u0440", filters: XLS });
      if (!path) return;
      const cap = el.querySelector("#imp-cap");
      cap.textContent = "\u0427\u0438\u0442\u0430\u044E\u2026";
      try {
        st2.imp = await job2("/reg/pick", { path }, (s) => {
          cap.textContent = s;
        });
      } catch (e) {
        st2.imp = null;
        cap.textContent = `\u0424\u0430\u0439\u043B \u043D\u0435 \u0440\u0430\u0437\u043E\u0431\u0440\u0430\u043D: ${e.message}`;
        return;
      }
      st2.sel = new Set(st2.imp.selected);
      st2.comments = false;
      invalidate();
      redraw();
    }));
    el.querySelector("[data-profile]")?.addEventListener("change", guard(async () => {
      st2.imp = await local("/reg/select", { profile: el.querySelector("[data-profile]").value });
      st2.sel = new Set(st2.imp.selected);
      invalidate();
      redraw();
    }));
    el.querySelectorAll("[data-sel]").forEach((b) => b.addEventListener("click", guard(async () => {
      const r = await local("/reg/select", { mode: b.dataset.sel, full: true });
      st2.sel = new Set(r.selected);
      invalidate();
      redraw();
    })));
    el.querySelectorAll("[data-emp]").forEach((cb) => cb.addEventListener("change", guard(async () => {
      if (cb.checked) st2.sel.add(cb.dataset.emp);
      else st2.sel.delete(cb.dataset.emp);
      invalidate();
      await local("/reg/select", { selected: [...st2.sel] });
      redraw();
    })));
    el.querySelectorAll("[data-opt]").forEach((cb) => cb.addEventListener("change", () => {
      st2.comments = cb.checked;
      invalidate();
      redraw();
    }));
    el.querySelector("[data-excluded-ids]")?.addEventListener("input", (event) => {
      st2.excludedIds = event.target.value;
      st2.preview = null;
      el.querySelector("[data-apply]")?.setAttribute("disabled", "");
    });
    const body = () => ({
      selected: [...st2.sel],
      full: true,
      restore_manual: true,
      replace_comments: st2.comments,
      soft_edits: true,
      exclude_contracts: st2.excludedIds.split(/[\r\n,;]+/).map((x) => x.trim()).filter(Boolean)
    });
    const logEl = () => el.querySelector("#imp-log");
    el.querySelector("[data-preview]")?.addEventListener("click", guard(async () => {
      invalidate();
      logEl().textContent = "\u041F\u0440\u043E\u0432\u0435\u0440\u044F\u044E\u2026";
      st2.preview = await job2("/reg/preview", body(), (s) => {
        logEl().textContent = s;
      });
      toast(st2.preview.error ? "\u0418\u043C\u043F\u043E\u0440\u0442 \u043D\u0435 \u043F\u0440\u043E\u0448\u0451\u043B" : `\u041F\u0440\u0435\u0434\u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440 \u0433\u043E\u0442\u043E\u0432: \u0431\u0443\u0434\u0435\u0442 \u0441\u043D\u044F\u0442\u043E ${n(st2.preview.unassigned)}`);
      redraw();
    }));
    el.querySelector("[data-apply]")?.addEventListener("click", guard(async () => {
      logEl().textContent = "\u041F\u0440\u0438\u043C\u0435\u043D\u044F\u044E\u2026";
      const r = await job2("/reg/apply", body(), (s) => {
        logEl().textContent = s;
      });
      if (r.error) {
        st2.preview = { error: r.error };
        toast("\u0418\u043C\u043F\u043E\u0440\u0442 \u043D\u0435 \u043F\u0440\u043E\u0448\u0451\u043B");
        redraw();
        return;
      }
      st2.preview = null;
      st2.applied = r;
      toast(r.toast);
      redraw();
    }));
  }
  function decisionKey(c) {
    return JSON.stringify([c.book_path || "", c.contract_id, c.key]);
  }
  function conflictTable(list, decide, labels2, cols) {
    const head = cols.map(([, t]) => `<th class="l">${esc7(t)}</th>`).join("") + (decide ? '<th class="l">\u0420\u0435\u0448\u0435\u043D\u0438\u0435</th>' : "");
    return `<div class="table-wrap"><table class="grid"><thead><tr>${head}</tr></thead><tbody>${list.map((c) => {
      const k = decisionKey(c);
      return `<tr>${c.row.map((v) => `<td class="l" style="white-space:normal">${esc7(v)}</td>`).join("")}${decide ? `<td class="l">
      <select class="input" data-dec="${esc7(k)}" style="height:30px">${Object.entries(labels2).map(([dk, dt]) => `<option value="${dk}" ${(st2.decisions[k] || "file") === dk ? "selected" : ""}>${esc7(dt)}</option>`).join("")}</select></td>` : ""}</tr>`;
    }).join("")}</tbody></table></div>`;
  }
  async function enrichTab(el) {
    st2.enr ||= await local("/enr/state");
    if (!st2.since && !st2.until) {
      st2.since = st2.enr.since || "";
      st2.until = st2.enr.until || "";
    }
    const e = st2.enr;
    const cols = e.conflict_columns;
    el.innerHTML = `${note("\u041F\u0435\u0440\u0435\u043D\u043E\u0441\u0438\u0442 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043D\u044B\u0435 \u0432\u0438\u0437\u0438\u0442\u044B \u0438 \u0437\u0432\u043E\u043D\u043A\u0438 \u0432 \u043B\u0438\u0447\u043D\u044B\u0435 \u043A\u043D\u0438\u0433\u0438 \u0425\u0430\u0440\u0434 \u0438 \u0421\u043E\u0444\u0442. \u0420\u0430\u0431\u043E\u0447\u0430\u044F \u043A\u043D\u0438\u0433\u0430 \u043D\u0430\u0437\u044B\u0432\u0430\u0435\u0442\u0441\u044F \u0424\u0418\u041E.xlsb; \u043A\u043E\u043F\u0438\u0438 \u0438 \u0434\u0430\u0442\u0438\u0440\u043E\u0432\u0430\u043D\u043D\u044B\u0435 \u0444\u0430\u0439\u043B\u044B \u0440\u044F\u0434\u043E\u043C \u043D\u0435 \u0443\u0447\u0430\u0441\u0442\u0432\u0443\u044E\u0442. \u041F\u0440\u0430\u0432\u043A\u0438 \u043A\u043D\u0438\u0433\u0438 \u0437\u0430\u0449\u0438\u0449\u0435\u043D\u044B \u0440\u0435\u0435\u0441\u0442\u0440\u043E\u043C \u0434\u043E\u0441\u0442\u0430\u0432\u043A\u0438. \u0421\u043D\u0430\u0447\u0430\u043B\u0430 \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0430, \u0437\u0430\u043F\u0438\u0441\u044C \u2014 \u043E\u0442\u0434\u0435\u043B\u044C\u043D\u043E\u0439 \u043A\u043D\u043E\u043F\u043A\u043E\u0439.")}
    <div class="toolbar" style="margin-top:12px"><button class="btn" data-base>\u041F\u0430\u043F\u043A\u0430 \u0441 \u0440\u0435\u0441\u043F\u0443\u0431\u043B\u0438\u043A\u0430\u043C\u0438\u2026</button><span class="muted">${esc7(e.base ? "\u0431\u0430\u0437\u0430: " + e.base : "\u0431\u0430\u0437\u043E\u0432\u0430\u044F \u043F\u0430\u043F\u043A\u0430 \u043D\u0435 \u0432\u044B\u0431\u0440\u0430\u043D\u0430")}</span>
      <span class="spacer"></span><button class="btn primary" data-scan ${e.base ? "" : "disabled"}>\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0441\u043E\u043E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0438\u044F</button></div>
    <div class="toolbar"><span class="muted" style="font-size:13.5px">\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B \u0441</span><input class="input" id="e-since" value="${esc7(st2.since)}" placeholder="\u0414\u0414.\u041C\u041C.\u0413\u0413\u0413\u0413" style="width:130px">
      <span class="muted" style="font-size:13.5px">\u043F\u043E</span><input class="input" id="e-until" value="${esc7(st2.until)}" placeholder="\u0414\u0414.\u041C\u041C.\u0413\u0413\u0413\u0413" style="width:130px">
      <button class="btn small" data-today>\u0422\u043E\u043B\u044C\u043A\u043E \u0441\u0435\u0433\u043E\u0434\u043D\u044F</button><span class="muted" style="font-size:12.5px">\u043F\u0443\u0441\u0442\u043E = \u0431\u0435\u0437 \u0433\u0440\u0430\u043D\u0438\u0446\u044B</span></div>
    <div id="e-scan-out">${st2.scan ? `${pairs(st2.scan.pairs)}${st2.scan.warning ? `<div class="notice" style="white-space:pre-line;margin-top:12px"><span class="grow">${esc7(st2.scan.warning)}</span></div>` : ""}` : ""}</div>
    ${st2.matched.length ? `<div class="toolbar"><span class="muted" style="font-size:13.5px">\u041A\u043E\u0433\u043E \u043E\u0431\u043E\u0433\u0430\u0449\u0430\u0442\u044C:</span>
        <button class="btn small" data-esel="none">\u0441\u043D\u044F\u0442\u044C</button><button class="btn small" data-esel="all">\u0432\u0441\u0435</button>
        <span class="spacer"></span><span class="muted" style="font-size:13px">\u043E\u0442\u043C\u0435\u0447\u0435\u043D\u043E ${n(st2.enrSel.size)} \u0438\u0437 ${n(st2.matched.length)}</span></div>
      <p class="muted" style="font-size:13px">\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438 \u0441 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u0430\u043C\u0438 \u0437\u0430 \u0432\u0441\u044E \u0438\u0441\u0442\u043E\u0440\u0438\u044E. \u0412\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u0435 \u0434\u0430\u0442\u044B \u043E\u0433\u0440\u0430\u043D\u0438\u0447\u0438\u0432\u0430\u044E\u0442 \u043F\u0435\u0440\u0435\u043D\u043E\u0441 \u0444\u0430\u043A\u0442\u043E\u0432 \u0432 \u043A\u043D\u0438\u0433\u0438.</p>
      <div class="grid2">${[["hard", "\u0425\u0430\u0440\u0434"], ["soft", "\u0421\u043E\u0444\u0442"]].map(([profile, title]) => {
      const members = st2.matched.filter((m) => (m.profile || (m.key.startsWith("soft:") ? "soft" : "hard")) === profile);
      return `<div><h3>${title} \u2014 ${n(members.length)} <span class="muted" style="font-size:13px">\u043E\u0442\u043C\u0435\u0447\u0435\u043D\u043E ${n(members.filter((m) => st2.enrSel.has(m.key)).length)}</span></h3>
          <div style="max-height:260px;overflow:auto;border-top:var(--lw) solid var(--line)">${members.map((m) => `<label class="list-row" style="cursor:pointer;padding:8px 4px">
          <input type="checkbox" data-ek="${esc7(m.key)}" ${st2.enrSel.has(m.key) ? "checked" : ""}><span class="grow"><span class="nm">${esc7(m.employee)}</span><br><span class="sub">${esc7(m.note)}</span></span></label>`).join("") || '<p class="muted">\u041D\u0435\u0442 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432 \u0441 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u043C\u0438 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u0430\u043C\u0438.</p>'}</div></div>`;
    }).join("")}</div>
      <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-check ${st2.enrSel.size ? "" : "disabled"}>\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u043A\u043E\u043D\u0444\u043B\u0438\u043A\u0442\u044B (\u0431\u0435\u0437 \u0437\u0430\u043F\u0438\u0441\u0438)</button>
        ${e.writes ? `<button class="btn" data-apply ${st2.enrSel.size ? "" : "disabled"}>\u0417\u0430\u043F\u0438\u0441\u0430\u0442\u044C \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u0445 (${n(st2.enrSel.size)})</button>` : ""}
        <span class="muted" id="e-log"></span></div>` : ""}
    ${st2.check ? `<pre style="white-space:pre-wrap;font:13px/1.5 var(--mono);color:var(--ink-2)">${esc7((st2.check.lines || []).join("\n"))}</pre>
      ${reviewRows().length ? `<div class="section"><div class="section-head"><h2 class="section-title">\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F \u0434\u043E \u0437\u0430\u043F\u0438\u0441\u0438 (${n(reviewRows().length)})</h2></div>${note(e.decision_hint)}<p class="muted">\u0421\u0443\u043C\u043C\u0430 \u0438 \u0434\u0430\u0442\u0430 \u043E\u0431\u0435\u0449\u0430\u043D\u0438\u044F \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u044E\u0442\u0441\u044F \u0432\u043C\u0435\u0441\u0442\u0435.</p>
        <div class="toolbar" style="margin-top:8px"><button class="btn small" data-dall="file">\u0412\u0435\u0437\u0434\u0435 \u043E\u0441\u0442\u0430\u0432\u0438\u0442\u044C \u043A\u0430\u043A \u0432 \u043A\u043D\u0438\u0433\u0435</button><button class="btn small" data-dall="server">\u0412\u0435\u0437\u0434\u0435 \u0437\u0430\u043F\u0438\u0441\u0430\u0442\u044C \u0441\u0435\u0440\u0432\u0435\u0440</button></div>
        ${conflictTable(reviewRows(), true, e.decision_labels, cols)}</div>` : ""}` : ""}
    ${st2.applyRes ? `<div class="section"><div class="section-head"><h2 class="section-title">\u0418\u0442\u043E\u0433 \u0437\u0430\u043F\u0438\u0441\u0438</h2></div>
      <pre style="white-space:pre-wrap;font:13px/1.5 var(--mono);color:var(--ink-2)">${esc7((st2.applyRes.lines || []).join("\n"))}</pre>
      ${st2.applyRes.ledger_error ? `<div class="notice err"><span class="grow">${esc7(st2.applyRes.ledger_error)}</span><button class="btn small" data-replay>\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0443 \u0438\u0437 \u043A\u0432\u0438\u0442\u0430\u043D\u0446\u0438\u0438</button></div>` : ""}</div>` : ""}`;
    const redraw = () => {
      if (el.isConnected) return enrichTab(el);
      if (activeRoot?.isConnected && st2.tab === "enrich") return draw2(activeRoot);
    };
    const win = () => {
      st2.since = el.querySelector("#e-since").value.trim();
      st2.until = el.querySelector("#e-until").value.trim();
    };
    el.querySelector("#e-since")?.addEventListener("change", () => {
      win();
      st2.check = null;
    });
    el.querySelector("#e-until")?.addEventListener("change", () => {
      win();
      st2.check = null;
    });
    el.querySelector("[data-today]")?.addEventListener("click", () => {
      const d = /* @__PURE__ */ new Date();
      const t = `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${d.getFullYear()}`;
      st2.since = t;
      st2.until = t;
      st2.check = null;
      redraw();
    });
    el.querySelector("[data-base]")?.addEventListener("click", guard(async () => {
      const p = await hard.pick("folder", { title: "\u041F\u0430\u043F\u043A\u0430, \u0432 \u043A\u043E\u0442\u043E\u0440\u043E\u0439 \u043B\u0435\u0436\u0430\u0442 \u043F\u0430\u043F\u043A\u0438 \u0440\u0435\u0441\u043F\u0443\u0431\u043B\u0438\u043A", defaultPath: e.base || void 0 });
      if (!p) return;
      st2.enr = await local("/enr/base", { path: p });
      st2.since = st2.enr.since || "";
      st2.until = st2.enr.until || "";
      redraw();
    }));
    el.querySelector("[data-scan]")?.addEventListener("click", guard(async () => {
      const out = el.querySelector("#e-scan-out");
      out.innerHTML = '<p class="muted">\u041F\u0440\u043E\u0432\u0435\u0440\u044F\u044E: \u0434\u0430\u043D\u043D\u044B\u0435 \u0441\u0435\u0440\u0432\u0435\u0440\u0430 + \u043F\u0430\u043F\u043A\u0438 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432\u2026</p>';
      const r = await job2("/enr/preview", {}, (s) => {
        out.innerHTML = `<p class="muted">${esc7(s)}</p>`;
      });
      st2.scan = r;
      st2.matched = r.matched;
      st2.enrSel = new Set(r.matched.map((m) => m.key));
      st2.check = null;
      st2.applyRes = null;
      redraw();
    }));
    el.querySelectorAll("[data-esel]").forEach((b) => b.addEventListener("click", () => {
      st2.enrSel = b.dataset.esel === "all" ? new Set(st2.matched.map((m) => m.key)) : /* @__PURE__ */ new Set();
      st2.check = null;
      redraw();
    }));
    el.querySelectorAll("[data-ek]").forEach((cb) => cb.addEventListener("change", () => {
      if (cb.checked) st2.enrSel.add(cb.dataset.ek);
      else st2.enrSel.delete(cb.dataset.ek);
      st2.check = null;
      redraw();
    }));
    const logEl = () => el.querySelector("#e-log");
    el.querySelector("[data-check]")?.addEventListener("click", guard(async () => {
      win();
      logEl().textContent = "\u041F\u0440\u043E\u0432\u0435\u0440\u044F\u044E \u043A\u043D\u0438\u0433\u0438\u2026";
      st2.check = await job2("/enr/check", { keys: [...st2.enrSel], since: st2.since, until: st2.until }, (s) => {
        logEl().textContent = s;
      });
      st2.decisions = {};
      toast(`\u041F\u0440\u043E\u0432\u0435\u0440\u0435\u043D\u043E: \u0440\u0430\u0441\u0445\u043E\u0436\u0434\u0435\u043D\u0438\u0439 ${reviewRows().length}`);
      redraw();
    }));
    el.querySelectorAll("[data-dec]").forEach((s) => s.addEventListener("change", () => {
      const row = reviewRows().find((c) => decisionKey(c) === s.dataset.dec);
      if (row) setReviewChoice(row, s.value);
      el.querySelectorAll("[data-dec]").forEach((other) => {
        other.value = st2.decisions[other.dataset.dec] || "file";
      });
    }));
    el.querySelectorAll("[data-dall]").forEach((b) => b.addEventListener("click", () => {
      for (const c of reviewRows()) setReviewChoice(c, b.dataset.dall);
      redraw();
    }));
    el.querySelector("[data-apply]")?.addEventListener("click", guard(async () => {
      win();
      const decisions = Object.entries(st2.decisions).map(([k, d]) => {
        const [book_path, contract_id, key2] = JSON.parse(k);
        return { book_path, contract_id, key: key2, decision: d };
      });
      if (st2.check) {
        for (const c of reviewRows()) if (!st2.decisions[decisionKey(c)]) decisions.push({ book_path: c.book_path, contract_id: c.contract_id, key: c.key, decision: "file" });
      }
      const body = { keys: [...st2.enrSel], since: st2.since, until: st2.until, decisions };
      const conf = await local("/enr/confirm", body);
      if (conf.whole && !await ask(e.whole_title, e.whole_text, "\u0412\u0441\u044F \u0438\u0441\u0442\u043E\u0440\u0438\u044F", true)) return;
      if (!await ask("\u041E\u0431\u043E\u0433\u0430\u0449\u0435\u043D\u0438\u0435 \u0440\u0435\u0435\u0441\u0442\u0440\u043E\u0432", conf.text, "\u0417\u0430\u043F\u0438\u0441\u0430\u0442\u044C")) return;
      logEl().textContent = "\u041E\u0442\u043A\u0440\u044B\u0432\u0430\u044E \u043A\u043D\u0438\u0433\u0438\u2026";
      st2.applyRes = await job2("/enr/apply", body, (s) => {
        logEl().textContent = s;
      });
      st2.check = null;
      toast(st2.applyRes.toast);
      redraw();
      if (activeRoot) await showDeliveryQueue(activeRoot, queueGeneration);
    }));
    el.querySelector("[data-replay]")?.addEventListener("click", guard(async () => {
      const r = await job2("/enr/replay", {});
      toast(r.toast);
      st2.applyRes.ledger_error = "";
      redraw();
    }));
  }
  async function columnsTab(el) {
    st2.cols ||= await local("/reg/columns_reload");
    const c = st2.cols;
    const opts = c.options || [];
    el.innerHTML = `${note("\u0427\u0442\u043E \u0434\u0435\u043B\u0430\u0442\u044C, \u043A\u043E\u0433\u0434\u0430 \u043E\u0444\u0438\u0441 \u043F\u0435\u0440\u0435\u0438\u043C\u0435\u043D\u043E\u0432\u0430\u043B \u043A\u043E\u043B\u043E\u043D\u043A\u0443 \u0438\u043B\u0438 \u0434\u043E\u0431\u0430\u0432\u0438\u043B \u043D\u043E\u0432\u0443\u044E. \u041E\u0442\u043A\u0440\u043E\u0439\u0442\u0435 \u043A\u043D\u0438\u0433\u0443 \u2014 \u043F\u0443\u043B\u044C\u0442 \u043F\u043E\u043A\u0430\u0436\u0435\u0442, \u043A\u0430\u043A\u0438\u0435 \u0437\u0430\u0433\u043E\u043B\u043E\u0432\u043A\u0438 \u0441\u0438\u0441\u0442\u0435\u043C\u0430 \u043D\u0435 \u0447\u0438\u0442\u0430\u0435\u0442, \u0438 \u043F\u0440\u0435\u0434\u043B\u043E\u0436\u0438\u0442 \u0441\u0432\u044F\u0437\u0430\u0442\u044C \u0438\u0445 \u0441 \u043F\u043E\u043B\u0435\u043C. \u041F\u0440\u0430\u0432\u043A\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0435\u0442\u0441\u044F \u043D\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0435 \u0438 \u0434\u0435\u0439\u0441\u0442\u0432\u0443\u0435\u0442 \u0434\u043B\u044F \u0432\u0441\u0435\u0445 \u0437\u0430\u0433\u0440\u0443\u0437\u043E\u043A.")}
    <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-check>\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u043A\u043D\u0438\u0433\u0443\u2026</button><button class="btn" data-reload>\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C \u043A\u0430\u0440\u0442\u0443 \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u0430</button></div>
    ${c.caption ? `<p class="muted">${esc7(c.caption)}</p>` : ""}${c.state ? `<div class="notice"><span class="grow">${esc7(c.state)}</span></div>` : ""}
    ${(c.rows || []).length ? `<div class="table-wrap"><table class="grid"><thead><tr><th class="l">\u041A\u043E\u043B\u043E\u043D\u043A\u0430 \u043A\u043D\u0438\u0433\u0438</th><th class="l">\u0427\u0442\u043E \u0441 \u043D\u0435\u0439 \u0434\u0435\u043B\u0430\u0442\u044C</th><th class="l">\u041F\u043E\u0434\u043F\u0438\u0441\u044C</th></tr></thead><tbody>
      ${c.rows.map((r, i) => `<tr><td class="l">${esc7(r.title)}</td><td class="l"><select class="input" data-cd="${i}" style="height:32px;width:320px">${opts.map(([k, t]) => `<option value="${esc7(k)}" ${r.decision === k ? "selected" : ""}>${esc7(t)}</option>`).join("")}</select></td>
        <td class="l"><input class="input" data-cl="${i}" value="${esc7(r.label)}" style="height:32px;width:240px"></td></tr>`).join("")}</tbody></table></div>
      <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-save>\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043A\u0430\u0440\u0442\u0443 \u043D\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0435</button></div>` : ""}
    <p class="muted" style="font-size:13px">${esc7(c.summary || "")}</p>`;
    el.querySelector("[data-check]")?.addEventListener("click", guard(async () => {
      const path = await hard.pick("file", { title: "\u0420\u0435\u0435\u0441\u0442\u0440", filters: XLS });
      if (!path) return;
      st2.cols = await job2("/reg/columns_check", { path });
      await columnsTab(el);
    }));
    el.querySelector("[data-reload]")?.addEventListener("click", guard(async () => {
      st2.cols = await local("/reg/columns_reload");
      toast("\u041A\u0430\u0440\u0442\u0430 \u043A\u043E\u043B\u043E\u043D\u043E\u043A \u043E\u0431\u043D\u043E\u0432\u043B\u0435\u043D\u0430");
      await columnsTab(el);
    }));
    el.querySelector("[data-save]")?.addEventListener("click", guard(async () => {
      const decisions = c.rows.map((r, i) => ({
        title: r.title,
        decision: el.querySelector(`[data-cd="${i}"]`).value,
        label: el.querySelector(`[data-cl="${i}"]`).value.trim() || r.title
      }));
      st2.cols = await local("/reg/columns_save", { decisions });
      toast("\u041A\u0430\u0440\u0442\u0430 \u043A\u043E\u043B\u043E\u043D\u043E\u043A \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430");
      await columnsTab(el);
    }));
  }
  function toIso(v) {
    const t = v.trim();
    if (!t) return "";
    let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t);
    if (m) return t;
    m = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(t);
    if (m) return `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`;
    throw new Error(`\u0414\u0430\u0442\u0430 \xAB${t}\xBB: \u043D\u0443\u0436\u0435\u043D \u0413\u0413\u0413\u0413-\u041C\u041C-\u0414\u0414 \u0438\u043B\u0438 \u0414\u0414.\u041C\u041C.\u0413\u0413\u0413\u0413`);
  }
  async function exportTab(el) {
    if (!st2.expEmps.length) {
      st2.expEmps = (await local("/exp/employees")).employees;
      st2.expSel = new Set(st2.expEmps.map((x) => x.key));
    }
    const per = await local("/reg/period");
    el.innerHTML = `<div class="section"><div class="section-head"><h2 class="section-title">\u042D\u043A\u0441\u043F\u043E\u0440\u0442-\u0441\u043B\u0435\u043F\u043E\u043A</h2></div>
      ${note("\u041D\u043E\u0432\u044B\u0439 xlsx \u0438\u0437 \u0431\u0430\u0437\u044B \u0441\u0435\u0440\u0432\u0435\u0440\u0430: \u0432\u0435\u0441\u044C \u0440\u0435\u0435\u0441\u0442\u0440 \u043B\u0438\u0431\u043E \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438; \u0441\u0442\u0440\u043E\u043A\u0438 \u043C\u043E\u0436\u043D\u043E \u043E\u0433\u0440\u0430\u043D\u0438\u0447\u0438\u0442\u044C \u0432\u044B\u0435\u0437\u0434\u0430\u043C\u0438 \u0438\u043B\u0438 \u0437\u0432\u043E\u043D\u043A\u0430\u043C\u0438 \u0438\u0437 \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u044F \u0437\u0430 \u043F\u0435\u0440\u0438\u043E\u0434.")}
      <div class="toolbar" style="margin-top:10px"><button class="btn small" data-x="none">\u0441\u043D\u044F\u0442\u044C</button><button class="btn small" data-x="all">\u0432\u0441\u0435</button>
        <span class="spacer"></span><span class="muted" style="font-size:13.5px">\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B \u0441</span><input class="input" id="x-since" value="${esc7(st2.expSince)}" placeholder="\u0414\u0414.\u041C\u041C.\u0413\u0413\u0413\u0413" style="width:130px">
        <span class="muted" style="font-size:13.5px">\u043F\u043E</span><input class="input" id="x-until" value="${esc7(st2.expUntil)}" placeholder="\u0414\u0414.\u041C\u041C.\u0413\u0413\u0413\u0413" style="width:130px"></div>
      <div class="grid2" style="max-height:260px;overflow:auto;border-top:var(--lw) solid var(--line)">${st2.expEmps.map((x) => `<label class="list-row" style="cursor:pointer;padding:7px 4px">
        <input type="checkbox" data-xk="${esc7(x.key)}" ${st2.expSel.has(x.key) ? "checked" : ""}><span class="grow">${esc7(x.name)} <span class="sub">\xB7 ${n(x.count)} \u043D\u0430\u0437\u043D\u0430\u0447.</span></span></label>`).join("")}</div>
      <div class="toolbar" style="margin-top:12px"><button class="btn primary" data-export>\u041F\u043E\u0434\u0433\u043E\u0442\u043E\u0432\u0438\u0442\u044C \u0432\u044B\u0433\u0440\u0443\u0437\u043A\u0443\u2026</button><button class="btn" data-markers>\u041C\u0435\u0442\u043A\u0438 \u043A\u0443\u0440\u044C\u0435\u0440\u043E\u0432\u2026</button><span class="muted" id="x-log"></span></div></div>
    <div class="section"><div class="section-head"><h2 class="section-title">\u041F\u0435\u0440\u0438\u043E\u0434 \u0432\u044B\u0433\u0440\u0443\u0437\u043A\u0438</h2></div>
      ${note("\u041F\u043E \u0434\u0430\u0442\u0435 \u0437\u0430\u043A\u0440\u0435\u043F\u043B\u0435\u043D\u0438\u044F: \u0432\u044B\u0435\u0437\u0434\u043D\u0438\u043A\u0438 \u0432\u0438\u0434\u044F\u0442 \u0442\u043E\u043B\u044C\u043A\u043E \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432 \u0438\u0437 \u044D\u0442\u043E\u0433\u043E \u043E\u043A\u043D\u0430. \u041F\u0443\u0441\u0442\u043E \u2014 \u0432\u0438\u0434\u043D\u044B \u0432\u0441\u0435.")}
      <div class="toolbar" style="margin-top:10px"><input class="input" id="p-from" value="${esc7(per.from || "")}" placeholder="\u0441" style="width:140px"><span class="muted">\u2014</span>
        <input class="input" id="p-to" value="${esc7(per.to || "")}" placeholder="\u043F\u043E" style="width:140px">
        <button class="btn primary" data-pset>\u041F\u0440\u0438\u043C\u0435\u043D\u0438\u0442\u044C</button><button class="btn" data-pclear>\u0421\u0431\u0440\u043E\u0441\u0438\u0442\u044C</button></div>
      <p class="muted">${esc7(per.text || "")}</p></div>`;
    const redraw = () => exportTab(el);
    el.querySelectorAll("[data-x]").forEach((b) => b.addEventListener("click", () => {
      st2.expSel = b.dataset.x === "all" ? new Set(st2.expEmps.map((x) => x.key)) : /* @__PURE__ */ new Set();
      redraw();
    }));
    el.querySelectorAll("[data-xk]").forEach((cb) => cb.addEventListener("change", () => {
      if (cb.checked) st2.expSel.add(cb.dataset.xk);
      else st2.expSel.delete(cb.dataset.xk);
    }));
    el.querySelector("[data-export]")?.addEventListener("click", guard(async () => {
      st2.expSince = el.querySelector("#x-since").value.trim();
      st2.expUntil = el.querySelector("#x-until").value.trim();
      const log = el.querySelector("#x-log");
      const r = await job2("/exp/prepare", { selected: [...st2.expSel], since: st2.expSince, until: st2.expUntil }, (s2) => {
        log.textContent = s2;
      });
      log.textContent = "";
      if (!await ask("\u042D\u043A\u0441\u043F\u043E\u0440\u0442 \u0440\u0435\u0435\u0441\u0442\u0440\u0430", r.confirm, "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C")) return;
      const path = await hard.pick("save", { title: "\u041A\u0443\u0434\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0441\u043B\u0435\u043F\u043E\u043A \u0440\u0435\u0435\u0441\u0442\u0440\u0430", defaultPath: "registry_export.xlsx", filters: [{ name: "Excel", extensions: ["xlsx"] }] });
      if (!path) return;
      const s = await job2("/exp/save", { path });
      toast(s.toast);
    }));
    el.querySelector("[data-markers]")?.addEventListener("click", guard(async () => {
      const path = await hard.pick("save", { title: "\u041A\u0443\u0434\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043C\u0435\u0442\u043A\u0438", defaultPath: "markers.xlsx", filters: [{ name: "Excel", extensions: ["xlsx"] }] });
      if (!path) return;
      const s = await job2("/exp/markers", { path });
      toast(s.toast);
    }));
    el.querySelector("[data-pset]")?.addEventListener("click", guard(async () => {
      const from = toIso(el.querySelector("#p-from").value);
      const to = toIso(el.querySelector("#p-to").value);
      await local("/reg/period", { set: true, from, to });
      toast("\u041F\u0435\u0440\u0438\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D");
      redraw();
    }));
    el.querySelector("[data-pclear]")?.addEventListener("click", guard(async () => {
      await local("/reg/period", { set: true, from: "", to: "" });
      toast("\u041F\u0435\u0440\u0438\u043E\u0434 \u0441\u0431\u0440\u043E\u0448\u0435\u043D");
      redraw();
    }));
  }
  async function recalledTab(el, limit = 500) {
    const r = await local("/reg/recalled", { limit, employee: st2.recEmp });
    el.innerHTML = `${note("\u041A\u043E\u0433\u043E \u043E\u0444\u0438\u0441 \u0441\u043D\u044F\u043B \u0441 \u0440\u0430\u0431\u043E\u0442\u044B \u043A\u043E\u043B\u043E\u043D\u043A\u0430\u043C\u0438 \u043E\u0442\u0437\u044B\u0432\u0430. \u0414\u043E\u0433\u043E\u0432\u043E\u0440 \u043E\u0441\u0442\u0430\u0451\u0442\u0441\u044F \u0432 \u0431\u0430\u0437\u0435 \u0441\u043E \u0432\u0441\u0435\u0439 \u0438\u0441\u0442\u043E\u0440\u0438\u0435\u0439, \u043D\u043E \u0438\u0441\u0447\u0435\u0437\u0430\u0435\u0442 \u0438\u0437 \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u044F \u0432\u044B\u0435\u0437\u0434\u043D\u0438\u043A\u0430. \u0412\u0435\u0440\u043D\u0451\u0442\u0441\u044F, \u043A\u043E\u0433\u0434\u0430 \u043E\u0444\u0438\u0441 \u043E\u0447\u0438\u0441\u0442\u0438\u0442 \u043A\u043E\u043B\u043E\u043D\u043A\u0438 \u043E\u0442\u0437\u044B\u0432\u0430 \u0438 \u0440\u0435\u0435\u0441\u0442\u0440 \u0437\u0430\u0433\u0440\u0443\u0437\u044F\u0442 \u0441\u043D\u043E\u0432\u0430.")}
    <div class="toolbar" style="margin-top:10px"><select class="input" id="r-emp" style="width:300px"><option value="">\u0432\u0441\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438</option>${r.employees.map((x) => `<option ${x === st2.recEmp ? "selected" : ""}>${esc7(x)}</option>`).join("")}</select>
      <button class="btn small" data-all>\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u044C \u0432\u0441\u0435</button></div>
    ${pairs(r.pairs)}<div style="margin-top:14px" id="rec-tbl"></div>`;
    new DataTable(el.querySelector("#rec-tbl"), {
      id: "analyst-recalled",
      cols: tupleCols(r.columns, /* @__PURE__ */ new Set(["debt"])),
      key: (x) => String(x[0]),
      exportName: "\u041E\u0442\u043E\u0437\u0432\u0430\u043D\u043D\u044B\u0435 \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u044B"
    }).setRows(r.rows);
    el.querySelector("#r-emp")?.addEventListener("change", (e) => {
      st2.recEmp = e.target.value;
      void recalledTab(el);
    });
    el.querySelector("[data-all]")?.addEventListener("click", () => void recalledTab(el, 5e3));
  }
  async function draw2(root) {
    activeRoot = root;
    clearTimeout(queueTimer);
    const generation = ++queueGeneration;
    root.innerHTML = `<div class="toolbar">${tabs()}</div><div data-delivery-queue></div><p class="notice" data-registry-progress role="status" hidden></p><div id="rtab"><div class="skeleton"></div></div>`;
    root.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
      st2.tab = b.dataset.tab;
      void draw2(root);
    }));
    const el = root.querySelector("#rtab");
    try {
      if (st2.tab === "enrich") await enrichTab(el);
      else if (st2.tab === "columns") await columnsTab(el);
      else if (st2.tab === "export") await exportTab(el);
      else if (st2.tab === "recalled") await recalledTab(el);
      else await importTab(el);
    } catch (e) {
      el.innerHTML = `<div class="notice err"><span class="grow">${esc7(e.message)}</span></div>`;
    }
    showProgress();
    await showDeliveryQueue(root, generation);
  }
  async function mountRegistryTools(root, onlyColumns = false) {
    embeddedTabs = onlyColumns ? ["columns"] : ["import", "enrich", "export", "recalled"];
    if (!embeddedTabs.includes(st2.tab)) st2.tab = onlyColumns ? "columns" : "import";
    await draw2(root);
    return () => {
      if (activeRoot === root) {
        activeRoot = null;
        queueGeneration++;
        clearTimeout(queueTimer);
      }
      embeddedTabs = void 0;
    };
  }
  async function drawPriority(root) {
    {
      const [COLS, WEIGHTS] = await Promise.all([fn("priority.COLUMNS"), fn("priority.WEIGHTS")]);
      for (const [k, , v] of WEIGHTS) if (pr.weights[k] === void 0) pr.weights[k] = v;
      const q = await fn("priority.priority_query", pr.weights, pr.limit);
      let d;
      try {
        d = await adm("GET", "/admin/priority" + q, void 0, 60);
      } catch (e) {
        root.innerHTML = `<div class="notice err">${esc7(e.message)}</div>`;
        return;
      }
      const items = d.items || [];
      const rows = await fnMap("priority.priority_row", items.map((it, i) => [i + 1, it]));
      const chips = await fn("priority.cluster_chips", d.cities || []);
      root.innerHTML = `${note("\u0414\u043E\u043B\u0433 \u0438 \u0441\u0432\u0435\u0436\u0435\u0441\u0442\u044C \u2014 \u043F\u043E\u0441\u0442\u043E\u044F\u043D\u043D\u044B\u0435 \u0432\u0435\u0441\u0430; \u043A\u043B\u0430\u0441\u0442\u0435\u0440 \u2014 \u043D\u0430\u0441\u043A\u043E\u043B\u044C\u043A\u043E \u0432\u0430\u0436\u043D\u043E, \u0447\u0442\u043E\u0431\u044B \u0440\u044F\u0434\u043E\u043C \u0431\u044B\u043B\u0438 \u0434\u0440\u0443\u0433\u0438\u0435 \u0434\u043E\u043B\u0436\u043D\u0438\u043A\u0438.")}
      <div class="toolbar" style="margin-top:10px">${WEIGHTS.map(([k, t]) => `<span class="muted">${esc7(t)}</span>
        <input class="input" type="number" data-w="${esc7(k)}" value="${pr.weights[k]}" min="0" max="100" style="width:90px">`).join("")}
        <button class="btn primary" data-go>\u041F\u0435\u0440\u0435\u0441\u0447\u0438\u0442\u0430\u0442\u044C</button></div>
      <div class="flags" style="margin:6px 0 12px">${chips.map((t) => `<span class="tag">${esc7(t)}</span>`).join("")}</div>
      <div id="prio-tbl"></div>`;
      new DataTable(root.querySelector("#prio-tbl"), {
        id: "analyst-priority",
        cols: tupleCols(COLS, /* @__PURE__ */ new Set(["debt", "exp", "visits", "score", "n"])),
        key: (x) => String(x[0]) + String(x[1]),
        exportName: "\u041F\u0440\u0438\u043E\u0440\u0438\u0442\u0435\u0442 \u0432\u044B\u0435\u0437\u0434\u0430"
      }).setRows(rows);
      root.querySelector("[data-go]")?.addEventListener("click", () => {
        root.querySelectorAll("[data-w]").forEach((i) => {
          pr.weights[i.dataset.w] = Number(i.value) || 0;
        });
        void priority.render(root, {});
      });
    }
  }
  var XLS, st2, activeRoot, queueTimer, queueGeneration, deliveryOperation, queueDetailsOpen, queueReconciliation, hiddenQueueRun, embeddedTabs, presentedQueueRun, queueChoices, pr, priority;
  var init_registry = __esm({
    "hard-desk/ui/analyst/registry.ts"() {
      "use strict";
      init_api();
      init_dom();
      init_table();
      init_lib();
      XLS = [{ name: "Excel", extensions: ["xlsx", "xlsm", "xlsb", "xls"] }];
      st2 = {
        tab: "import",
        imp: null,
        sel: /* @__PURE__ */ new Set(),
        comments: false,
        preview: null,
        applied: null,
        impQuery: "",
        impFilter: "all",
        impSort: "name",
        excludedIds: "",
        enr: null,
        matched: [],
        enrSel: /* @__PURE__ */ new Set(),
        since: "",
        until: "",
        check: null,
        decisions: {},
        applyRes: null,
        cols: null,
        colDecisions: [],
        scan: null,
        expEmps: [],
        expSel: /* @__PURE__ */ new Set(),
        expSince: "",
        expUntil: "",
        rec: null,
        recEmp: "",
        busy: false,
        progress: ""
      };
      activeRoot = null;
      queueGeneration = 0;
      deliveryOperation = null;
      queueDetailsOpen = false;
      queueReconciliation = null;
      hiddenQueueRun = "";
      presentedQueueRun = "";
      queueChoices = /* @__PURE__ */ new Map();
      pr = { weights: {}, limit: 40 };
      priority = {
        id: "prio",
        title: "\u041F\u0440\u0438\u043E\u0440\u0438\u0442\u0435\u0442",
        icon: "enrich",
        kicker: "\u043A\u043E\u0433\u043E \u043D\u0430\u0432\u0435\u0441\u0442\u0438\u0442\u044C \u043F\u0435\u0440\u0432\u044B\u043C",
        async render(root) {
          root.innerHTML = '<div class="skeleton"></div><div class="skeleton" style="width:60%"></div>';
          try {
            await drawPriority(root);
          } catch (e) {
            root.innerHTML = `<div class="notice err"><span class="grow">${esc7(e.message)}</span></div>`;
          }
        }
      };
    }
  });

  // hard-desk/ui/views/team-work.ts
  function transport(kind2, resource) {
    const tail = resource === "board" ? "employee_board" : "staff_tasks";
    const path = kind2 === "analyst" ? `/admin/${tail}.json` : kind2 === "manager" ? `/api/manager/${tail}` : `/api/${tail}`;
    return async (body, id) => {
      if (kind2 === "analyst") return adm(body ? "POST" : "GET", path + (id ? "?id=" + id : ""), body);
      const r = await api2(path, id ? { id } : void 0, body ? "POST" : "GET", body);
      if (!r.ok || r.stale) throw new Error(r.stale ? "\u041D\u0435\u0442 \u0441\u0432\u044F\u0437\u0438. \u0414\u043B\u044F \u0440\u0430\u0431\u043E\u0442\u044B \u0441 \u0434\u043E\u0441\u043A\u043E\u0439 \u0438 \u0437\u0430\u0434\u0430\u0447\u0430\u043C\u0438 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0438\u0442\u0435\u0441\u044C \u043A \u0441\u0435\u0440\u0432\u0435\u0440\u0443." : r.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0437\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044C \u0434\u0430\u043D\u043D\u044B\u0435.");
      return r.data;
    };
  }
  async function write(send, key2, body) {
    const storageKey = "hard.team-work.pending." + key2;
    const encoded = JSON.stringify(body);
    let pending;
    try {
      pending = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    } catch {
    }
    if (!pending || pending.encoded !== encoded) pending = { encoded, operation_id: crypto.randomUUID() };
    sessionStorage.setItem(storageKey, JSON.stringify(pending));
    await send({ ...body, operation_id: pending.operation_id });
    sessionStorage.removeItem(storageKey);
  }
  function action(root, selector, fn2) {
    root.querySelectorAll(selector).forEach((button2) => button2.addEventListener("click", async () => {
      if (button2.disabled) return;
      button2.disabled = true;
      try {
        await fn2(button2);
      } catch (error) {
        toast(error.message);
      } finally {
        if (button2.isConnected) button2.disabled = false;
      }
    }));
  }
  function removalChoice(employee) {
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.id = "ask";
      box.innerHTML = `<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="remove-choice-title">
      <h3 id="remove-choice-title">\u0423\u0434\u0430\u043B\u0438\u0442\u044C \u0442\u0430\u043A\u0436\u0435 \u043F\u0440\u043E\u0444\u0438\u043B\u044C?</h3>
      <p>${esc7(employee.employee)}${employee.username ? " \xB7 " + esc7(employee.username) : ""}</p>
      <p>\u041C\u043E\u0436\u043D\u043E \u0443\u0431\u0440\u0430\u0442\u044C \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430 \u0442\u043E\u043B\u044C\u043A\u043E \u0441 \u0434\u043E\u0441\u043A\u0438: \u0430\u043A\u043A\u0430\u0443\u043D\u0442 \u0438 \u0432\u0445\u043E\u0434\u044B \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0442\u0441\u044F.
      \u041F\u0440\u0438 \u0443\u0434\u0430\u043B\u0435\u043D\u0438\u0438 \u043F\u0440\u043E\u0444\u0438\u043B\u044F \u0432\u0445\u043E\u0434\u044B \u043D\u0430 \u041F\u041A \u0438 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435 \u0431\u0443\u0434\u0443\u0442 \u0437\u0430\u043A\u0440\u044B\u0442\u044B. \u0418\u0441\u0442\u043E\u0440\u0438\u044F \u0440\u0430\u0431\u043E\u0442\u044B, \u0437\u0430\u0434\u0430\u0447\u0438 \u0438 \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u044B \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0442\u0441\u044F.</p>
      ${employee.can_delete_profile === false ? "<p>\u041F\u0440\u043E\u0444\u0438\u043B\u044C \u0442\u0440\u0435\u0431\u0443\u0435\u0442 \u0443\u0442\u043E\u0447\u043D\u0435\u043D\u0438\u044F. \u0414\u043E\u0441\u0442\u0443\u043F\u043D\u043E \u0442\u043E\u043B\u044C\u043A\u043E \u0443\u0434\u0430\u043B\u0435\u043D\u0438\u0435 \u0441 \u0434\u043E\u0441\u043A\u0438.</p>" : ""}
      <div class="row" style="flex-wrap:wrap"><button class="btn" data-cancel>\u041E\u0442\u043C\u0435\u043D\u0430</button>
      <button class="btn primary" data-keep-profile>\u0423\u0431\u0440\u0430\u0442\u044C \u0431\u0435\u0437 \u0443\u0434\u0430\u043B\u0435\u043D\u0438\u044F</button>
      <button class="btn danger" data-delete-profile ${employee.can_delete_profile === false ? "disabled" : ""}>\u0423\u0431\u0440\u0430\u0442\u044C \u0438 \u0443\u0434\u0430\u043B\u0438\u0442\u044C \u043F\u0440\u043E\u0444\u0438\u043B\u044C</button></div></div>`;
      const previousFocus = document.activeElement;
      const done = (choice) => {
        box.remove();
        document.removeEventListener("keydown", onKey);
        if (previousFocus?.isConnected) previousFocus.focus();
        resolve(choice);
      };
      const onKey = (event) => {
        if (event.key === "Escape") done(null);
        if (event.key === "Tab") {
          const buttons = [...box.querySelectorAll("button:not(:disabled)")];
          const index = buttons.indexOf(document.activeElement);
          event.preventDefault();
          buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
        }
      };
      box.querySelector("[data-cancel]").addEventListener("click", () => done(null));
      box.querySelector("[data-keep-profile]").addEventListener("click", () => done("hide"));
      box.querySelector("[data-delete-profile]").addEventListener("click", () => done("remove"));
      box.addEventListener("click", (event) => {
        if (event.target === box) done(null);
      });
      document.addEventListener("keydown", onKey);
      document.body.append(box);
      box.querySelector("[data-keep-profile]").focus();
    });
  }
  function employeeBoard(kind2) {
    const send = transport(kind2, "board");
    let query = "";
    const render = async (root) => {
      setHead("\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438");
      try {
        const d = await send();
        if (!root.isConnected) return;
        const employees = d.employees || [];
        const onBoard = (x) => x.on_board ?? !!x.active;
        const active = employees.filter(onBoard);
        const candidates = employees.filter((x) => !onBoard(x));
        root.innerHTML = `<div class="toolbar"><input class="input" type="search" data-board-search aria-label="\u041D\u0430\u0439\u0442\u0438 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430 \u0438\u043B\u0438 \u043B\u043E\u0433\u0438\u043D" placeholder="\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A \u0438\u043B\u0438 \u043B\u043E\u0433\u0438\u043D" value="${esc7(query)}"><span class="spacer"></span><button class="btn small" data-reload>\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C</button></div>
        ${d.can_edit ? `<form class="toolbar team-add" data-add-form><label>\u0414\u043E\u0431\u0430\u0432\u0438\u0442\u044C \u0438\u0437 \u0440\u0435\u0435\u0441\u0442\u0440\u0430 <select class="input" data-add-person required aria-label="\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A \u0438\u0437 \u0440\u0435\u0435\u0441\u0442\u0440\u0430"><option value="">\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430</option>${candidates.map((x, i) => `<option value="${i}">${esc7(x.employee)}${x.office ? " \xB7 " + esc7(x.office) : ""}</option>`).join("")}</select></label><button class="btn small" type="submit" ${candidates.length ? "" : "disabled"}>+ \u0414\u043E\u0431\u0430\u0432\u0438\u0442\u044C</button></form>` : ""}
        <div class="employee-board" data-board></div>`;
        const draw3 = () => {
          const shown = active.filter((x) => `${x.employee} ${x.username} ${x.office}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
          const groups2 = [["tj", "\u0422\u0430\u0434\u0436\u0438\u043A\u0438\u0441\u0442\u0430\u043D"], ["uz", "\u0423\u0437\u0431\u0435\u043A\u0438\u0441\u0442\u0430\u043D"], ...shown.some((x) => !["tj", "uz"].includes(x.direction)) ? [["", "\u0420\u0435\u0441\u043F\u0443\u0431\u043B\u0438\u043A\u0430 \u043D\u0435 \u0443\u043A\u0430\u0437\u0430\u043D\u0430"]] : []];
          const host = root.querySelector("[data-board]");
          host.innerHTML = groups2.map(([code, title]) => {
            const rows = shown.filter((x) => code ? x.direction === code : !["tj", "uz"].includes(x.direction));
            return `<section class="employee-country ${code ? "" : "unmapped"}"><div class="section-head"><h2>${title}</h2><span class="muted">${rows.length}</span></div>${rows.map((x) => {
              const index = active.indexOf(x);
              return `<article class="employee-tile"><div class="row"><b class="grow">${esc7(x.employee)}</b>${kind2 === "manager" && d.can_edit ? `<button class="btn small" data-assign="${index}">+ \u0417\u0430\u0434\u0430\u0447\u0430</button>` : ""}</div>
              ${x.office ? `<div class="sub">${esc7(x.office)} \xB7 ${esc7(x.products.map((p) => p === "soft" ? "\u0421\u043E\u0444\u0442" : "\u0425\u0430\u0440\u0434").join(" / "))}</div>` : `<div class="sub">${esc7(x.products.map((p) => p === "soft" ? "\u0421\u043E\u0444\u0442" : "\u0425\u0430\u0440\u0434").join(" / "))}</div>`}
              <div class="employee-login"><span class="mono">${esc7(x.username || "\u2014")}</span> \xB7 ${esc7(x.password_review_pending ? "\u0416\u0434\u0451\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F \u0441\u043C\u0435\u043D\u044B \u043F\u0430\u0440\u043E\u043B\u044F" : loginStatus[x.login_status] || x.login_status)}${x.must_change_password ? " \xB7 \u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044F \u0441\u043C\u0435\u043D\u0430 \u043F\u0430\u0440\u043E\u043B\u044F" : ""}<br>\u041F\u041A: ${esc7(deviceStatus[x.desktop])} \xB7 \u0422\u0435\u043B\u0435\u0444\u043E\u043D: ${esc7(deviceStatus[x.phone])}${x.last_seen ? `<br>\u041F\u043E\u0441\u043B\u0435\u0434\u043D\u044F\u044F \u0441\u0432\u044F\u0437\u044C: ${esc7(dateText(x.last_seen))}` : ""}</div>
              ${x.can_remove ? `<button class="btn small employee-remove" data-remove-person="${index}">\u0423\u0431\u0440\u0430\u0442\u044C</button>` : ""}</article>`;
            }).join("") || '<p class="muted">\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432 \u043D\u0435\u0442</p>'}</section>`;
          }).join("");
          action(host, "[data-assign]", async (b) => {
            await go("staff-tasks", { employee: active[Number(b.dataset.assign)].employee, create: true });
          });
          action(host, "[data-remove-person]", async (b) => {
            const x = active[Number(b.dataset.removePerson)];
            const choice = await removalChoice(x);
            if (!choice) return;
            await write(send, `${kind2}.${choice}.${x.employee}`, choice === "hide" ? { action: "hide", employee: x.employee } : { action: "remove", employee: x.employee, account_id: x.account_id, confirm: "remove_and_delete_profile" });
            await render(root);
          });
        };
        draw3();
        root.querySelector("[data-board-search]").addEventListener("input", (e) => {
          query = e.target.value;
          draw3();
        });
        action(root, "[data-reload]", async () => render(root));
        root.querySelector("[data-add-form]")?.addEventListener("submit", async (e) => {
          e.preventDefault();
          const select = root.querySelector("[data-add-person]");
          const button2 = root.querySelector("[data-add-form] button");
          if (!select.value || button2.disabled) return;
          button2.disabled = true;
          const x = candidates[Number(select.value)];
          try {
            await write(send, `${kind2}.add.${x.employee}`, { action: "add", employee: x.employee });
            await render(root);
          } catch (error) {
            toast(error.message);
            button2.disabled = false;
          }
        });
      } catch (error) {
        root.innerHTML = `<div class="notice err">${esc7(error.message)}</div><button class="btn" data-retry>\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C</button>`;
        action(root, "[data-retry]", async () => render(root));
      }
    };
    return { id: "employee-board", title: "\u0414\u043E\u0441\u043A\u0430 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u043E\u0432", icon: "people", render };
  }
  function staffTasks(kind2) {
    const send = transport(kind2, "tasks");
    let selected2 = 0, filter = "active", employee = "";
    const render = async (root, params = {}) => {
      if (params.employee) employee = params.employee;
      if (params.id) selected2 = Number(params.id);
      setHead("\u0417\u0430\u0434\u0430\u0447\u0438");
      try {
        if (params.create) {
          await create(root);
          return;
        }
        if (selected2) {
          await detail(root);
          return;
        }
        const d = await send();
        if (!root.isConnected) return;
        const rows = (d.tasks || []).filter((x) => (!employee || x.employee === employee) && (filter === "all" || (filter === "active" ? x.status !== "confirmed" : filter === "overdue" ? x.overdue : x.status === filter)));
        root.innerHTML = `<div class="toolbar"><select class="input" data-task-filter aria-label="\u0421\u0442\u0430\u0442\u0443\u0441 \u0437\u0430\u0434\u0430\u0447">${[["active", "\u041D\u0435\u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043D\u043D\u044B\u0435"], ["open", "\u0412 \u0440\u0430\u0431\u043E\u0442\u0435"], ["submitted", "\u0416\u0434\u0443\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F"], ["overdue", "\u041F\u0440\u043E\u0441\u0440\u043E\u0447\u0435\u043D\u043D\u044B\u0435"], ["confirmed", "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u044B\u0435"], ["all", "\u0412\u0441\u0435 \u0437\u0430\u0434\u0430\u0447\u0438"]].map(([v, label]) => `<option value="${v}" ${filter === v ? "selected" : ""}>${label}</option>`).join("")}</select>${employee ? `<button class="btn small" data-clear-employee>${esc7(employee)} \xD7</button>` : ""}<span class="spacer"></span><button class="btn small" data-reload>\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C</button>${d.can_create ? '<button class="btn primary small" data-create>+ \u0417\u0430\u0434\u0430\u0447\u0430</button>' : ""}</div>
        <div class="task-list">${rows.map((x) => `<button class="task-row" data-task="${x.id}"><span class="row"><b class="grow">${esc7(x.title)}</b><span class="tag ${x.overdue ? "warn" : x.status === "confirmed" ? "ok" : ""}">${esc7(status[x.status])}${x.overdue ? " \xB7 \u043F\u0440\u043E\u0441\u0440\u043E\u0447\u0435\u043D\u0430" : ""}</span></span><span class="sub">${esc7(x.creator)} \u2192 ${esc7(x.employee)}</span><span class="sub">\u0421\u0440\u043E\u043A: ${esc7(dateText(x.due_at))} \xB7 \u042D\u0442\u0430\u043F\u044B: ${x.steps.filter((s) => s.done).length}/${x.steps.length}</span></button>`).join("") || '<p class="muted">\u0417\u0430\u0434\u0430\u0447 \u043D\u0435\u0442</p>'}</div>`;
        action(root, "[data-task]", async (b) => {
          selected2 = Number(b.dataset.task);
          await detail(root);
        });
        action(root, "[data-create]", async () => create(root));
        action(root, "[data-clear-employee]", async () => {
          employee = "";
          await render(root);
        });
        action(root, "[data-reload]", async () => render(root));
        root.querySelector("[data-task-filter]").addEventListener("change", (e) => {
          filter = e.target.value;
          void render(root);
        });
      } catch (error) {
        root.innerHTML = `<div class="notice err">${esc7(error.message)}</div><button class="btn" data-retry>\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C</button>`;
        action(root, "[data-retry]", async () => render(root));
      }
    };
    const back = (root) => action(root, "[data-back]", async () => {
      selected2 = 0;
      await render(root);
    });
    const create = async (root) => {
      const d = await transport(kind2, "board")();
      if (!root.isConnected) return;
      const rows = (d.employees || []).filter((x) => x.active);
      root.innerHTML = `<button class="btn small" data-back>\u2190 \u0417\u0430\u0434\u0430\u0447\u0438</button><form class="task-form" data-new-task>
      <label>\u041A\u043E\u043C\u0443<select class="input" name="employee" required><option value="">\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430</option>${rows.map((x) => `<option ${employee === x.employee ? "selected" : ""} value="${esc7(x.employee)}">${esc7(x.employee)}</option>`).join("")}</select></label>
      <label>\u0417\u0430\u0434\u0430\u0447\u0430<input class="input" name="title" maxlength="240" required></label>
      <label>\u041E\u043F\u0438\u0441\u0430\u043D\u0438\u0435<textarea class="input" name="description" maxlength="8000" rows="4"></textarea></label>
      <label>\u0421\u0440\u043E\u043A \xB7 \u0432\u0440\u0435\u043C\u044F \u044D\u0442\u043E\u0433\u043E \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0430<input class="input" name="due_at" type="datetime-local" required></label><button class="btn primary" type="submit">\u041D\u0430\u0437\u043D\u0430\u0447\u0438\u0442\u044C</button><p class="err" data-error role="status"></p></form>`;
      back(root);
      const form = root.querySelector("[data-new-task]");
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const button2 = form.querySelector("button[type=submit]");
        if (button2.disabled) return;
        button2.disabled = true;
        try {
          const values = Object.fromEntries(new FormData(form));
          await write(send, kind2 + ".create-task", { action: "create", ...values, due_at: new Date(String(values.due_at)).toISOString() });
          employee = "";
          selected2 = 0;
          filter = "active";
          await render(root);
        } catch (error) {
          form.querySelector("[data-error]").textContent = error.message;
          button2.disabled = false;
        }
      });
    };
    const detail = async (root) => {
      const d = await send(void 0, selected2), x = d.task;
      if (!root.isConnected) return;
      root.innerHTML = `<div class="toolbar"><button class="btn small" data-back>\u2190 \u0417\u0430\u0434\u0430\u0447\u0438</button><span class="spacer"></span><button class="btn small" data-reload>\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C</button></div>
      <article class="task-detail"><div class="section-head"><h2>${esc7(x.title)}</h2><span class="tag ${x.overdue ? "warn" : ""}">${esc7(status[x.status])}</span></div>
      <p>${esc7(x.creator)} \u2192 ${esc7(x.employee)}</p><p class="${x.overdue ? "err" : "muted"}">\u0421\u0440\u043E\u043A: ${esc7(dateText(x.due_at))}${x.overdue ? " \xB7 \u043F\u0440\u043E\u0441\u0440\u043E\u0447\u0435\u043D\u0430" : ""}</p><p class="task-text">${esc7(x.description)}</p>
      <h3>\u042D\u0442\u0430\u043F\u044B</h3><div class="task-steps">${x.steps.map((s) => `<label class="task-step"><input type="checkbox" data-step="${s.id}" ${s.done ? "checked" : ""} ${x.can_contribute && x.status === "open" ? "" : "disabled"}><span>${esc7(s.text)}<span class="sub">${esc7(s.author)}</span></span></label>`).join("") || '<p class="muted">\u042D\u0442\u0430\u043F\u043E\u0432 \u043D\u0435\u0442</p>'}</div>
      ${x.can_contribute ? `<div class="task-compose"><label>\u0417\u0430\u043C\u0435\u0442\u043A\u0430, \u044D\u0442\u0430\u043F \u0438\u043B\u0438 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442<textarea class="input" data-task-text maxlength="4000" rows="3"></textarea></label><div class="row"><button class="btn small" data-task-action="note">+ \u0417\u0430\u043C\u0435\u0442\u043A\u0430</button>${x.status === "open" ? '<button class="btn small" data-task-action="step">+ \u042D\u0442\u0430\u043F</button>' : ""}${x.can_work && x.status === "open" ? '<button class="btn primary small" data-task-action="submit">\u0421\u0434\u0430\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442</button>' : ""}${x.can_manage && x.status === "submitted" ? '<button class="btn primary small" data-task-action="confirm">\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442</button><button class="btn small" data-task-action="return">\u0412\u0435\u0440\u043D\u0443\u0442\u044C \u0432 \u0440\u0430\u0431\u043E\u0442\u0443</button>' : ""}</div><p class="err" data-error role="status"></p></div>` : ""}
      <h3>\u0418\u0441\u0442\u043E\u0440\u0438\u044F</h3><div class="task-events">${x.events.map((e) => `<div class="task-event"><div class="sub">${esc7(dateText(e.at))} \xB7 ${esc7(e.author)} \xB7 ${esc7(eventTitles[e.kind] || e.kind)}</div><div class="task-text">${esc7(e.text)}</div></div>`).join("")}</div></article>`;
      back(root);
      action(root, "[data-reload]", async () => detail(root));
      action(root, "[data-task-action]", async (b) => {
        const text2 = root.querySelector("[data-task-text]").value.trim();
        const actionName = b.dataset.taskAction;
        if (actionName !== "confirm" && !text2) {
          toast("\u0423\u043A\u0430\u0436\u0438\u0442\u0435 \u0442\u0435\u043A\u0441\u0442.");
          return;
        }
        if (actionName === "confirm" && !await ask("\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442?", `${x.title}
${x.employee}
\u0417\u0430\u0434\u0430\u0447\u0430 \u0431\u0443\u0434\u0435\u0442 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u0430.`, "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C")) return;
        try {
          await write(send, `${kind2}.task.${x.id}`, { id: x.id, revision: x.revision, action: actionName, text: text2 });
          await detail(root);
        } catch (error) {
          root.querySelector("[data-error]").textContent = error.message;
        }
      });
      root.querySelectorAll("[data-step]").forEach((input) => input.addEventListener("change", async () => {
        input.disabled = true;
        try {
          await write(send, `${kind2}.task.${x.id}`, { id: x.id, revision: x.revision, action: "toggle_step", step_id: Number(input.dataset.step), done: input.checked });
          await detail(root);
        } catch (error) {
          input.checked = !input.checked;
          input.disabled = false;
          toast(error.message);
        }
      }));
    };
    return { id: "staff-tasks", title: "\u0417\u0430\u0434\u0430\u0447\u0438", icon: "promises", render };
  }
  var status, loginStatus, deviceStatus, eventTitles, dateText;
  var init_team_work = __esm({
    "hard-desk/ui/views/team-work.ts"() {
      "use strict";
      init_api();
      init_dom();
      init_shell();
      init_lib();
      status = { open: "\u0412 \u0440\u0430\u0431\u043E\u0442\u0435", submitted: "\u0416\u0434\u0451\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F", confirmed: "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0430" };
      loginStatus = { none: "\u041F\u0440\u043E\u0444\u0438\u043B\u044C \u043D\u0435 \u0441\u043E\u0437\u0434\u0430\u043D", active: "\u0414\u043E\u0441\u0442\u0443\u043F \u043E\u0442\u043A\u0440\u044B\u0442", awaiting: "\u0416\u0434\u0451\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F", disabled: "\u0414\u043E\u0441\u0442\u0443\u043F \u043E\u0442\u043A\u043B\u044E\u0447\u0451\u043D", blocked: "\u0414\u043E\u0441\u0442\u0443\u043F \u0437\u0430\u0431\u043B\u043E\u043A\u0438\u0440\u043E\u0432\u0430\u043D", ambiguous: "\u041D\u0443\u0436\u043D\u043E \u0443\u0442\u043E\u0447\u043D\u0438\u0442\u044C \u043F\u0440\u043E\u0444\u0438\u043B\u044C" };
      deviceStatus = { none: "\u043D\u0435\u0442 \u0432\u0445\u043E\u0434\u0430", pending: "\u0436\u0434\u0451\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F", active: "\u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D" };
      eventTitles = { create: "\u041D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0430", note: "\u0417\u0430\u043C\u0435\u0442\u043A\u0430", step: "\u0414\u043E\u0431\u0430\u0432\u043B\u0435\u043D \u044D\u0442\u0430\u043F", toggle_step: "\u042D\u0442\u0430\u043F", submit: "\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u0441\u0434\u0430\u043D", confirm: "\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D", return: "\u0412\u043E\u0437\u0432\u0440\u0430\u0449\u0435\u043D\u0430 \u0432 \u0440\u0430\u0431\u043E\u0442\u0443" };
      dateText = (value) => value ? new Date(value).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "\u2014";
    }
  });

  // hard-desk/ui/views/common.ts
  function setPersonTransport(read, image) {
    cardApi = read;
    cardImage = image;
  }
  var cardApi, cardImage;
  var init_common = __esm({
    "hard-desk/ui/views/common.ts"() {
      "use strict";
      init_api();
      init_dom();
      init_shell();
      cardApi = api2;
      cardImage = (path, query) => hard.image(path, query);
    }
  });

  // hard-desk/ui/analyst/card-access.ts
  function useAnalystCardTransport() {
    const read = async (path, query, method = "GET") => {
      if (method !== "GET" || !["/api/manager/person", "/api/manager/photos"].includes(path))
        return { ok: false, status: 403, kind: "forbidden", text: "\u041D\u0435\u0434\u043E\u043F\u0443\u0441\u0442\u0438\u043C\u044B\u0439 \u0437\u0430\u043F\u0440\u043E\u0441 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0438" };
      const qs = new URLSearchParams(Object.entries(query || {}).filter(([, value]) => value !== void 0 && value !== null && value !== "").map(([key2, value]) => [key2, String(value)])).toString();
      try {
        const data2 = await adm("GET", path.replace("/api/manager/", "/admin/control/") + (qs ? "?" + qs : ""));
        return { ok: true, status: 200, data: data2 };
      } catch (error) {
        return { ok: false, status: 0, kind: "server", text: error.message };
      }
    };
    setPersonTransport(read, async (path, query) => {
      const kind2 = path === "/api/manager/photo" ? "photo" : path === "/api/manager/receipt_photo" ? "receipt_photo" : "";
      if (!kind2 || typeof query?.file !== "string") return null;
      const result2 = await hard.local("/admin_image", { kind: kind2, file: query.file });
      return result2.ok ? result2.data?.src || null : null;
    });
  }
  var init_card_access = __esm({
    "hard-desk/ui/analyst/card-access.ts"() {
      "use strict";
      init_api();
      init_common();
      init_lib();
    }
  });

  // hard-desk/ui/crm-analyst.ts
  async function mountAnalyst(host, group, crm2, valid, options) {
    let disposed = false, sequence = 0, leave, current3 = "";
    const live = () => !disposed && valid(), request3 = async (action3, ...args) => {
      if (!live()) throw Error("\u0420\u0430\u0437\u0434\u0435\u043B \u0437\u0430\u043A\u0440\u044B\u0442.");
      const value = await crm2.admin(action3, args, options.owner);
      if (value?.ok === false && value.text && !value.error) {
        if (action3 === "local") return { ...value, error: value.text };
        throw Error(value.text);
      }
      return value;
    };
    const restoreBridge = setEmbeddedBridge({
      local: (path, body) => request3("local", path, body),
      pick: (kind2, opts) => request3("pick", kind2, opts),
      copy: (text2) => request3("copy", text2),
      saveText: (name, content) => request3("saveText", name, content),
      xlsx: (name, headers, rows) => request3("xlsx", name, headers, rows),
      open: (file) => request3("open", file),
      autostart: (on) => request3("autostart", on),
      state: () => request3("state"),
      win: (action3) => crm2.win(action3)
    });
    const closePanel2 = () => {
      const dialog = document.getElementById("analyst-panel");
      if (dialog?.querySelector('[data-saving="1"]')) return;
      dialog?.close();
      dialog?.remove();
    };
    const navigate = async (id) => {
      const target = routes[id];
      if (!target) throw Error("\u0420\u0430\u0437\u0434\u0435\u043B \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u0435\u043D \u0432 \u0440\u0430\u0431\u043E\u0447\u0435\u043C \u043C\u0435\u0441\u0442\u0435 \u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A\u0430.");
      await crm2.draft("analyst-tab-" + target[0], target[1], options.owner);
      if (target[0] === group) await show(target[1]);
      else await options.navigate(target[0]);
    };
    const restoreShell = setEmbeddedShell({
      go: navigate,
      refresh: () => show(current3),
      currentId: () => legacy[current3]?.id || current3,
      setHead: (_title, _kicker, tools) => {
        const target = host.querySelector("#head-tools");
        if (target) target.innerHTML = tools;
      },
      hasSection: (id) => !!routes[id],
      panelOpen: () => !!document.getElementById("analyst-panel"),
      closePanel: closePanel2,
      openPanel: (title, body) => {
        closePanel2();
        const dialog = document.createElement("dialog");
        dialog.id = "analyst-panel";
        dialog.className = "analyst-tools analyst-panel";
        dialog.innerHTML = `<div class="analyst-panel-head"><h2>${title}</h2><button data-panel-close>\u0417\u0430\u043A\u0440\u044B\u0442\u044C</button></div><div id="panel-inner"><div class="page">${body}</div></div>`;
        document.body.append(dialog);
        dialog.querySelector("[data-panel-close]").addEventListener("click", closePanel2);
        dialog.addEventListener("cancel", (e) => {
          if (dialog.querySelector('[data-saving="1"]')) e.preventDefault();
        });
        dialog.showModal();
        return dialog.querySelector("#panel-inner .page");
      }
    });
    useAnalystCardTransport();
    if (!document.getElementById("toast")) {
      const toast2 = document.createElement("div");
      toast2.id = "toast";
      toast2.role = "status";
      document.body.append(toast2);
    }
    host.innerHTML = `<div class="tabs analyst-tabs">${groups[group].map(([id, title]) => `<button data-analyst-tab="${id}">${title}</button>`).join("")}</div><div><div id="head-tools" class="toolbar analyst-tools"></div><div id="analyst-workspace"></div></div>`;
    async function show(id) {
      if (!live()) return;
      closePanel2();
      leave?.();
      leave = void 0;
      const turn = ++sequence;
      current3 = groups[group].some(([key2]) => key2 === id) ? id : groups[group][0][0];
      host.querySelector("#head-tools").replaceChildren();
      host.querySelectorAll("[data-analyst-tab]").forEach((b) => b.classList.toggle("active", b.dataset.analystTab === current3));
      await crm2.draft("analyst-tab-" + group, current3, options.owner);
      if (!live() || turn !== sequence) return;
      const page = document.createElement("div");
      page.id = "view-inner";
      host.querySelector("#analyst-workspace").replaceChildren(page);
      page.classList.toggle("analyst-tools", !options.core[current3]);
      const currentView = () => live() && turn === sequence && page.isConnected;
      try {
        let cleanup = void 0;
        if (current3 === "files" || current3 === "columns") cleanup = await mountRegistryTools(page, current3 === "columns");
        else if (options.core[current3]) cleanup = await options.core[current3](page, currentView);
        else await legacy[current3].render(page, {});
        if (currentView()) leave = typeof cleanup === "function" ? cleanup : void 0;
        else cleanup?.();
      } catch (e) {
        if (currentView()) page.innerHTML = `<p class="notice err" role="alert">${esc9(e.message)}</p>`;
      }
    }
    host.querySelectorAll("[data-analyst-tab]").forEach((b) => b.onclick = () => void show(b.dataset.analystTab));
    const saved = await crm2.draft("analyst-tab-" + group, void 0, options.owner);
    await show(typeof saved?.data === "string" ? saved.data : groups[group][0][0]);
    return () => {
      disposed = true;
      sequence++;
      leave?.();
      closePanel2();
      restoreShell();
      restoreBridge();
      host.classList.remove("analyst-tools");
    };
  }
  var esc9, groups, legacy, routes;
  var init_crm_analyst = __esm({
    "hard-desk/ui/crm-analyst.ts"() {
      "use strict";
      init_api();
      init_shell();
      init_people();
      init_managers();
      init_accounts();
      init_mail();
      init_today();
      init_system();
      init_registry();
      init_team_work();
      init_card_access();
      esc9 = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
      groups = {
        registry: [["table", "\u041E\u0431\u0449\u0438\u0439 \u0440\u0435\u0435\u0441\u0442\u0440"], ["columns", "\u041A\u043E\u043B\u043E\u043D\u043A\u0438"], ["priority", "\u041F\u0440\u0438\u043E\u0440\u0438\u0442\u0435\u0442"]],
        exchange: [["files", "\u0418\u043C\u043F\u043E\u0440\u0442 \u0438 \u043E\u0431\u043E\u0433\u0430\u0449\u0435\u043D\u0438\u0435"], ["crm-books", "\u041A\u043D\u0438\u0433\u0438 CRM"], ["mail", "\u0420\u0430\u0441\u0441\u044B\u043B\u043A\u0430"], ["queue", "\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0438"]],
        statistics: [["reports", "\u041E\u0442\u0447\u0451\u0442\u044B"], ["activity", "\u0410\u043A\u0442\u0438\u0432\u043D\u043E\u0441\u0442\u044C"], ["tasks", "\u0417\u0430\u0434\u0430\u0447\u0438"]],
        team: [["board", "\u0421\u043E\u0441\u0442\u0430\u0432"], ["people", "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438"], ["managers", "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u0438"], ["accounts", "\u0410\u043A\u043A\u0430\u0443\u043D\u0442\u044B \u0438 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0430"], ["scripts", "\u0421\u043A\u0440\u0438\u043F\u0442\u044B"]],
        connection: [["connection", "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435"], ["maintenance", "\u041E\u0431\u0441\u043B\u0443\u0436\u0438\u0432\u0430\u043D\u0438\u0435"]]
      };
      legacy = {
        people,
        managers,
        accounts,
        mail,
        activity: today,
        priority,
        maintenance: maint,
        board: employeeBoard("analyst"),
        tasks: staffTasks("analyst")
      };
      routes = {
        people: ["team", "people"],
        managers: ["team", "managers"],
        accounts: ["team", "accounts"],
        "employee-board": ["team", "board"],
        "staff-tasks": ["statistics", "tasks"],
        dash: ["statistics", "activity"],
        registry: ["exchange", "files"],
        prio: ["registry", "priority"],
        mail: ["exchange", "mail"],
        conn: ["connection", "connection"],
        maint: ["connection", "maintenance"]
      };
    }
  });

  // hard-desk/ui/crm-app.ts
  var crm_app_exports = {};
  function cardEntries(entries) {
    const ids = new Set(card?.contracts?.map((row) => row.contract_uuid) || []);
    return entries.filter((entry) => Array.isArray(entry.body?.contract_ids) && entry.body.contract_ids.some((id) => ids.has(id)) || entry.action === "registry-edit" && entry.body?.rows?.some((row) => ids.has(row.contract_uuid)));
  }
  async function queueEntries() {
    const result2 = await crm.queue();
    if (!result2.ok) throw new Error(result2.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u0440\u043E\u0447\u0438\u0442\u0430\u0442\u044C \u0441\u043E\u0441\u0442\u043E\u044F\u043D\u0438\u0435 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438.");
    return result2.data?.entries || [];
  }
  function paintCardSaves(entries) {
    const host = $2("card-saves");
    if (!host) return;
    const expanded = new Set(Array.from(host.querySelectorAll("[data-operation]")).filter((el) => el.querySelector("details[open]")).map((el) => el.dataset.operation));
    const othersOpen = !!host.querySelector("[data-other-saves][open]");
    const rows = cardEntries(entries).slice(-5).reverse();
    const markup = (entry) => {
      const review = entry.state === "attention" || entry.result?.projection_status === "needs_review";
      const status2 = entry.state === "pending" ? `\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E ${savedOn} \xB7 \u043E\u0436\u0438\u0434\u0430\u0435\u0442 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438` : entry.state === "attention" ? "\u0422\u0440\u0435\u0431\u0443\u0435\u0442 \u0441\u0432\u0435\u0440\u043A\u0438" : entry.result?.projection_status === "needs_review" ? "\u0418\u0441\u0442\u043E\u0440\u0438\u044F \u043F\u0440\u0438\u043D\u044F\u0442\u0430 \xB7 \u0442\u0435\u043A\u0443\u0449\u0438\u0435 \u043F\u043E\u043B\u044F \u0442\u0440\u0435\u0431\u0443\u044E\u0442 \u0441\u0432\u0435\u0440\u043A\u0438" : "\u041F\u0440\u0438\u043D\u044F\u0442\u043E \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C";
      const payload = entry.action === "registry-edit" ? { text: entry.body.rows.map((row) => Object.entries(row.values).map(([key2, value]) => (context.fields?.find((f) => f.key === key2)?.title || key2) + ": " + (value ?? "\u041E\u0447\u0438\u0449\u0435\u043D\u043E")).join("\n")).join("\n") } : entry.body.payload || {};
      const saved = payload.text || payload.title || payload.delivery_comment || Object.entries(payload.values || {}).map(([key2, value]) => (context.fields?.find((field) => field.key === key2)?.title || key2) + ": " + (value ?? "\u041E\u0447\u0438\u0449\u0435\u043D\u043E")).join("\n");
      return `<div class="card-save ${review ? "needs-review" : ""}" data-operation="${esc10(entry.body.operation_id)}" data-save-state="${entry.state}"><span>${esc10(entry.action === "registry-edit" ? "\u041F\u0440\u0430\u0432\u043A\u0430 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0438" : labels[entry.body.kind] || entry.body.kind)}</span><strong>${status2}</strong>${entry.error ? `<p>${esc10(entry.error)}</p>` : ""}${saved ? `<details><summary>\u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0439 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442</summary><p>${esc10(saved)}</p></details>` : ""}${entry.result?.projection_status === "needs_review" ? '<button class="link" data-save-history>\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0438\u0441\u0442\u043E\u0440\u0438\u044E</button>' : ""}</div>`;
    };
    const needsReview = rows.slice(1).filter((e) => e.state === "attention" || e.result?.projection_status === "needs_review").length;
    host.innerHTML = rows.length ? `<h2>\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0430 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u043E\u0432</h2>${markup(rows[0])}${rows.length > 1 ? `<details data-other-saves${othersOpen ? " open" : ""}><summary>\u041F\u0440\u0435\u0434\u044B\u0434\u0443\u0449\u0438\u0435 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438: ${rows.length - 1}${needsReview ? " \xB7 \u0442\u0440\u0435\u0431\u0443\u044E\u0442 \u0441\u0432\u0435\u0440\u043A\u0438: " + needsReview : ""}</summary>${rows.slice(1).map(markup).join("")}</details>` : ""}<button class="link" id="card-open-queue">\u0412\u0441\u0435 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438</button>` : "";
    if (rows[0]?.state === "accepted" && $2("notice").textContent === `\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E ${savedOn}.`) notice();
    bind("card-open-queue", () => go2("queue"));
    host.querySelectorAll("[data-save-history]").forEach((button2) => button2.onclick = () => {
      tab = "history";
      void renderCard();
    });
    host.querySelectorAll("[data-operation]").forEach((el) => {
      if (expanded.has(el.dataset.operation)) el.querySelector("details")?.setAttribute("open", "");
    });
  }
  function scheduleSync(force = false) {
    syncForced ||= force;
    clearTimeout(syncTimer);
    syncTimer = setTimeout(() => {
      const refresh2 = syncForced;
      syncForced = false;
      void syncCurrentView(refresh2).catch((error) => {
        if (error.name !== "AbortError") notice(error.message);
      });
    }, 100);
  }
  function retryCard(person) {
    cardRefreshPending = true;
    clearTimeout(cardRetryTimer);
    cardRetryTimer = setTimeout(() => {
      if (activePerson === person) scheduleSync(true);
    }, 7e3);
  }
  async function syncCurrentView(force = false) {
    if (!context) return;
    if (activePerson) {
      if (!card) return;
      const person = activePerson, generation = ++cardSyncGeneration;
      const entries = await queueEntries();
      if (person !== activePerson || generation !== cardSyncGeneration) return;
      paintCardSaves(entries);
      const acknowledgements = JSON.stringify(cardEntries(entries).filter((e) => e.state === "accepted").map((e) => [e.body.operation_id, e.result?.event_id, e.result?.projection_status]));
      const refresh2 = force || cardRefreshPending || acknowledgements !== lastCardAcknowledgements;
      if (!refresh2) return;
      const result2 = await crm.api("GET", "card", void 0, { person_uuid: person });
      if (person !== activePerson || generation !== cardSyncGeneration) return;
      if (!result2.ok) {
        if (result2.status === 403) {
          card.contracts.forEach((r) => {
            r.allowed_actions = [];
          });
          const form = $2("action-form");
          if (form) {
            form.querySelector('button[type="submit"]').disabled = true;
            $2("form-error").textContent = "\u041D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0435 \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u043E\u0441\u044C. \u0412\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D; \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0430 \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u0430.";
          }
          cardRefreshPending = true;
          notice("\u0418\u0437\u043C\u0435\u043D\u0438\u043B\u0441\u044F \u0434\u043E\u0441\u0442\u0443\u043F \u043A \u043A\u043B\u0438\u0435\u043D\u0442\u0443. \u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0439 \u0432\u0432\u043E\u0434 \u043E\u0441\u0442\u0430\u0451\u0442\u0441\u044F \u0443 \u0438\u0441\u0445\u043E\u0434\u043D\u043E\u0439 \u043B\u0438\u0447\u043D\u043E\u0441\u0442\u0438.");
          return;
        }
        retryCard(person);
        cardRefreshError = result2.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043E\u0431\u043D\u043E\u0432\u0438\u0442\u044C \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0443.";
        throw new Error(cardRefreshError);
      }
      if (formOpening || $2("dialog").open) {
        cardRefreshPending = true;
        const latest = new Map(result2.data.contracts.map((r) => [r.contract_uuid, r.allowed_actions]));
        card.contracts.forEach((r) => {
          r.allowed_actions = latest.get(r.contract_uuid) || [];
        });
        const form = $2("action-form");
        if (form) {
          const kind2 = form.dataset.work;
          const inputs = [...form.querySelectorAll('input[name="contracts"]:checked')];
          if (kind2 && inputs.some((input) => !contractCan(card.contracts.find((r) => r.contract_uuid === input.value) || { allowed_actions: [] }, kind2))) {
            form.querySelector('button[type="submit"]').disabled = true;
            $2("form-error").textContent = "\u041D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0435 \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u043E\u0441\u044C. \u0412\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D \u0443 \u0432\u0430\u0441; \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0430 \u044D\u0442\u043E\u0439 \u0440\u0430\u0431\u043E\u0442\u044B \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u0430.";
          }
        }
        return;
      }
      const expanded = Array.from(document.querySelectorAll("[data-contract-details]")).filter((el) => el.querySelector("details[open]")).map((el) => el.dataset.contractDetails);
      const openSaves = Array.from(document.querySelectorAll("[data-operation]")).filter((el) => el.querySelector("details[open]")).map((el) => el.dataset.operation);
      const othersOpen = !!document.querySelector("[data-other-saves][open]");
      const main = document.querySelector("main");
      const scroll = main.scrollTop;
      clearTimeout(cardRetryTimer);
      card = result2.data;
      cardRefreshPending = !!result2.stale;
      if (result2.stale) {
        $2("connection").textContent = "\u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u0430\u044F \u043A\u043E\u043F\u0438\u044F \xB7 " + date(result2.stale);
        retryCard(person);
      } else {
        lastCardAcknowledgements = acknowledgements;
        if (cardRefreshError && $2("notice").textContent === cardRefreshError) notice();
        cardRefreshError = "";
        $2("connection").textContent = (state.connection.mode === "synthetic" ? "\u0421\u0438\u043D\u0442\u0435\u0442\u0438\u0447\u0435\u0441\u043A\u0438\u0435 \u0434\u0430\u043D\u043D\u044B\u0435" : state.connection.server) + (state.offline ? " \xB7 \u043E\u0444\u043B\u0430\u0439\u043D" : "");
      }
      await renderCard();
      for (const host of document.querySelectorAll("[data-contract-details]"))
        if (expanded.includes(host.dataset.contractDetails)) host.querySelector("details")?.setAttribute("open", "");
      for (const host of document.querySelectorAll("[data-operation]"))
        if (openSaves.includes(host.dataset.operation)) host.querySelector("details")?.setAttribute("open", "");
      if (othersOpen) document.querySelector("[data-other-saves]")?.setAttribute("open", "");
      main.scrollTop = scroll;
    } else if (section === "tasks" && !$2("dialog").open) {
      await tasksPage();
    } else if (section === "queue") {
      await queuePage();
    }
  }
  async function request2(method, action3, body, query) {
    const generation = renderGeneration;
    const result2 = await crm.api(method, action3, body, query);
    if (generation !== renderGeneration)
      throw new DOMException("\u042D\u043A\u0440\u0430\u043D \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u0441\u044F.", "AbortError");
    if (!result2.ok)
      throw new Error(
        result2.text || result2.data?.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0432\u044B\u043F\u043E\u043B\u043D\u0438\u0442\u044C \u0437\u0430\u043F\u0440\u043E\u0441."
      );
    if (result2.stale)
      $2("connection").textContent = "\u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u0430\u044F \u043A\u043E\u043F\u0438\u044F \xB7 " + date(result2.stale);
    return result2.data;
  }
  function can(cap) {
    return context?.owner || context?.capabilities?.includes(cap);
  }
  function contractCan(contract, kind2) {
    return !Array.isArray(contract.allowed_actions) || contract.allowed_actions.includes(kind2);
  }
  function repaintWatermark(data2) {
    let host = document.querySelector(".card-script-watermark");
    if (!host && data2?.script) {
      const workspace = document.querySelector(".card-workspace");
      if (workspace) {
        workspace.classList.add("with-script");
        workspace.insertAdjacentHTML("beforeend", '<aside class="card-script-watermark" aria-label="\u0421\u043A\u0440\u0438\u043F\u0442 \u0440\u0430\u0437\u0433\u043E\u0432\u043E\u0440\u0430"></aside>');
        host = workspace.querySelector(".card-script-watermark");
      }
    }
    if (host && data2?.script) host.innerHTML = `<h2>${esc10(data2.script.title)}</h2>${data2.script.steps.map((step) => `<section><h3>${esc10(step.title)}</h3><p>${esc10(step.text)}</p></section>`).join("")}<small>\u0412\u0435\u0440\u0441\u0438\u044F ${esc10(data2.published_revision)}</small>`;
  }
  function button(id, title, primary = false) {
    return `<button id="${id}"${primary ? ' class="primary"' : ""}>${esc10(title)}</button>`;
  }
  function bind(id, fn2) {
    $2(id)?.addEventListener("click", () => {
      notice();
      Promise.resolve().then(fn2).catch((e) => {
        if (e.name !== "AbortError") notice(e.message);
      });
    });
  }
  async function boot() {
    await crm.ready?.();
    state = await crm.state();
    document.title = state.product || "\u0412\u0437\u044B\u0441\u043A\u0430\u043D\u0438\u0435";
    if (analystIdentity && analystIdentity !== state.owner) {
      location.reload();
      return;
    }
    context = state.context;
    const mobileTarget = crm.initialSection?.();
    if (mobileTarget) section = mobileTarget;
    document.body.classList.toggle("crm-analyst", context?.role === "analyst");
    if (context?.role === "analyst") analystIdentity = state.owner;
    if (context?.role === "analyst" && !["registry", "exchange", "statistics", "team", "connection"].includes(section)) section = "registry";
    if (portfolioIdentity !== context?.employee_uuid) {
      sectionScroll.clear();
      taskSelectClient = false;
      newTaskAssignee = "";
      cardOrigin = "portfolio";
      portfolioIdentity = context?.employee_uuid || "";
      portfolioView = ["manager", "owner", "analyst", "observer"].includes(context?.role) ? "all" : "mine";
      portfolioEmployee = context?.employee_uuid || "";
      portfolioWork = "all";
      portfolioSearch = "";
      portfolioOffset = 0;
      taskStatus = "open";
      taskEmployee = ["manager", "analyst", "observer", "owner"].includes(context?.role) ? "all" : context?.employee_uuid;
      taskWork = "all";
      taskSearch = "";
      taskOffset = 0;
      const savedTasks = (await crm.draft("task-view")).data;
      if (savedTasks) {
        if (["open", "today", "overdue", "done", "all"].includes(savedTasks.status)) taskStatus = savedTasks.status;
        if (["all", "call", "visit", "delivery"].includes(savedTasks.work)) taskWork = savedTasks.work;
        if (savedTasks.employee === "all" && ["manager", "analyst", "observer", "owner"].includes(context?.role) || (context?.staff || []).some((p) => p.employee_uuid === savedTasks.employee)) taskEmployee = savedTasks.employee;
      }
      const saved = (await crm.draft("portfolio-view")).data;
      if (saved && ["mine", "all"].includes(saved.view)) {
        portfolioView = saved.view;
        portfolioWork = ["all", "call", "visit", "both", "delivery"].includes(saved.work) ? saved.work : "all";
        if ((context?.staff || []).some((p) => p.employee_uuid === saved.employee) || saved.employee === context?.employee_uuid) portfolioEmployee = saved.employee;
      }
    }
    $2("identity").textContent = context ? { manager: "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C", observer: "\u041D\u0430\u0431\u043B\u044E\u0434\u0430\u0442\u0435\u043B\u044C", courier: "\u041A\u0443\u0440\u044C\u0435\u0440", analyst: "\u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A" }[context.role] || "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A" : "\u0412\u0445\u043E\u0434 \u0432 CRM";
    $2("version").textContent = state.version;
    $2("connection").textContent = (state.connection.mode === "synthetic" ? "\u0421\u0438\u043D\u0442\u0435\u0442\u0438\u0447\u0435\u0441\u043A\u0438\u0435 \u0434\u0430\u043D\u043D\u044B\u0435" : state.connection.mode === "unconfigured" ? "\u041D\u0435 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u043E" : state.connection.server) + (state.offline ? " \xB7 \u043E\u0444\u043B\u0430\u0439\u043D" : "");
    const manager = ["manager", "owner"].includes(context?.role);
    const sections2 = context?.role === "analyst" ? [
      ["registry", "\u0420\u0435\u0435\u0441\u0442\u0440"],
      ["exchange", "\u041E\u0431\u043C\u0435\u043D"],
      ["statistics", "\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438"],
      ["team", "\u041A\u043E\u043C\u0430\u043D\u0434\u0430"],
      ["connection", "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435"]
    ] : context?.role === "employee" ? [
      ["portfolio", "\u041F\u043E\u0440\u0442\u0444\u0435\u043B\u044C"],
      ["registry", "\u0420\u0435\u0435\u0441\u0442\u0440"],
      ["cabinet", "\u041C\u043E\u0438 \u043F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438"],
      ["active-time", "\u0410\u043A\u0442\u0438\u0432\u043D\u043E\u0435 \u0432\u0440\u0435\u043C\u044F"],
      ["connection", "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435"]
    ] : manager ? [
      ["portfolio", "\u041F\u043E\u0440\u0442\u0444\u0435\u043B\u044C"],
      ["registry", "\u0420\u0435\u0435\u0441\u0442\u0440"],
      ["manager-statistics", "\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438"],
      ["team", "\u041A\u043E\u043C\u0430\u043D\u0434\u0430"],
      ["script", "\u0421\u043A\u0440\u0438\u043F\u0442\u044B \u0438 \u0437\u0430\u0434\u0430\u0447\u0438"],
      ["connection", "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435"]
    ] : crm.platform === "mobile" && context?.role === "courier" ? [
      ["portfolio", "\u041F\u0435\u0440\u0435\u0434\u0430\u0447\u0438"],
      ["tasks", "\u0417\u0430\u0434\u0430\u0447\u0438"],
      ["cabinet", "\u041C\u043E\u0438 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B"],
      ["queue", "\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0438"],
      ["shift", "\u0421\u043C\u0435\u043D\u0430"],
      ["connection", "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435"],
      ["help", "\u0421\u043F\u0440\u0430\u0432\u043A\u0430"]
    ] : crm.platform === "mobile" && context?.role === "observer" ? [
      ["portfolio", "\u041F\u043E\u0440\u0442\u0444\u0435\u043B\u044C"],
      ["cabinet", "\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438"],
      ["tasks", "\u0417\u0430\u0434\u0430\u0447\u0438"],
      ["connection", "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435"],
      ["help", "\u0421\u043F\u0440\u0430\u0432\u043A\u0430"]
    ] : [
      ["portfolio", "\u041F\u043E\u0440\u0442\u0444\u0435\u043B\u044C"],
      ["tasks", "\u0417\u0430\u0434\u0430\u0447\u0438"],
      ["cabinet", "\u041C\u043E\u0438 \u043F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438"],
      ["shift", "\u0421\u043C\u0435\u043D\u0430"],
      ["script", "\u0421\u043A\u0440\u0438\u043F\u0442"],
      ["queue", "\u041E\u0447\u0435\u0440\u0435\u0434\u044C"],
      ...can("exchange") ? [["exchange", "\u0420\u0435\u0435\u0441\u0442\u0440\u044B"]] : [],
      ...can("analytics") ? [["adoption", "\u0412\u043D\u0435\u0434\u0440\u0435\u043D\u0438\u0435"]] : [],
      ["connection", "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435"],
      ["help", "\u0421\u043F\u0440\u0430\u0432\u043A\u0430"]
    ];
    $2("navigation").innerHTML = sections2.filter(([id]) => context || ["connection", "help"].includes(id)).map(
      ([id, label]) => `<button data-section="${id}" class="${section === id ? "active" : ""}">${label}</button>`
    ).join("");
    document.querySelectorAll("[data-section]").forEach((b) => b.onclick = () => void go2(b.dataset.section));
    await go2(context ? section : "connection");
    const initialPerson2 = crm.initialPerson?.();
    if (context && initialPerson2) await openCard(initialPerson2);
    telemetry("client_ready", "shell", {
      duration_ms: Math.round(performance.now())
    });
  }
  async function go2(next) {
    if (next === "overview") next = "portfolio";
    disposeSection?.();
    disposeSection = void 0;
    const generation = ++renderGeneration;
    if (!activePerson) sectionScroll.set(section, document.querySelector("main").scrollTop);
    clearTimeout(cardRetryTimer);
    activePerson = "";
    cardSyncGeneration++;
    lastCardAcknowledgements = "";
    cardRefreshPending = false;
    taskSelectClient = false;
    newTaskAssignee = "";
    section = next;
    if (context) crm.navigated?.({ section: next });
    document.getElementById("manager-tools-tabs")?.remove();
    const manager = ["manager", "owner"].includes(context?.role);
    if (manager && ["script", "tasks"].includes(next)) {
      const tabs2 = document.createElement("div");
      tabs2.id = "manager-tools-tabs";
      tabs2.className = "tabs";
      tabs2.innerHTML = `<button data-tool="script" class="${next === "script" ? "active" : ""}">\u0421\u043A\u0440\u0438\u043F\u0442\u044B</button><button data-tool="tasks" class="${next === "tasks" ? "active" : ""}">\u0417\u0430\u0434\u0430\u0447\u0438</button>`;
      $2("view").before(tabs2);
      tabs2.querySelectorAll("button").forEach((b) => b.onclick = () => void go2(b.dataset.tool));
    }
    notice();
    $2("title").textContent = {
      portfolio: "\u041F\u043E\u0440\u0442\u0444\u0435\u043B\u044C",
      overview: "\u041E\u0431\u0437\u043E\u0440",
      "manager-statistics": "\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438 \u043A\u043E\u043C\u0430\u043D\u0434\u044B",
      statistics: "\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438",
      team: "\u041A\u043E\u043C\u0430\u043D\u0434\u0430",
      registry: "\u0420\u0435\u0435\u0441\u0442\u0440",
      "active-time": "\u0410\u043A\u0442\u0438\u0432\u043D\u043E\u0435 \u0432\u0440\u0435\u043C\u044F",
      tasks: "\u0417\u0430\u0434\u0430\u0447\u0438",
      shift: "\u0421\u043C\u0435\u043D\u0430",
      cabinet: "\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u0435\u043B\u0438 \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430",
      script: "\u0421\u043A\u0440\u0438\u043F\u0442 \u0437\u0432\u043E\u043D\u043A\u0430",
      queue: "\u041E\u0447\u0435\u0440\u0435\u0434\u044C",
      exchange: context?.role === "analyst" ? "\u041E\u0431\u043C\u0435\u043D" : "\u0420\u0435\u0435\u0441\u0442\u0440\u044B",
      adoption: "\u0412\u043D\u0435\u0434\u0440\u0435\u043D\u0438\u0435",
      connection: "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435",
      help: "\u0421\u043F\u0440\u0430\u0432\u043A\u0430"
    }[next];
    document.querySelectorAll("[data-section]").forEach(
      (b) => b.classList.toggle("active", b.dataset.section === (manager && next === "tasks" ? "script" : next))
    );
    telemetry(
      "feature_opened",
      {
        queue: "shell",
        connection: "shell",
        help: "shell",
        shift: "shift",
        script: "script",
        exchange: "exchange",
        adoption: "adoption",
        portfolio: "portfolio",
        registry: "portfolio",
        "active-time": "shell",
        tasks: "task",
        cabinet: "portfolio"
      }[next]
    );
    try {
      if (context?.role === "analyst" && ["registry", "exchange", "statistics", "team", "connection"].includes(next)) {
        const expectedOwner = state.owner, valid = () => generation === renderGeneration;
        const api4 = {
          ...crm,
          api: (method, action3, body, query) => crm.api(method, action3 === "manager-report" ? "analyst-report" : action3 === "manager-statistics" ? "analyst-statistics" : action3, body, query),
          workbook: (action3, input) => crm.workbook(action3 === "manager-report" ? "analyst-report" : action3, input),
          draft: (key2, value) => crm.draft(key2, value, expectedOwner),
          registryEdit: (body) => crm.registryEdit(body, expectedOwner),
          queue: (action3, id) => crm.queue(action3, id, expectedOwner)
        };
        const stop = await mountAnalyst($2("view"), next, api4, valid, { owner: expectedOwner, navigate: go2, core: {
          table: async (host, current3) => mountRegistry(host, api4, current3, { manager: true, distribution: context.distribution?.enabled, openPerson: (id) => void openCard(id).catch((e) => notice(e.message)) }),
          reports: async (host, current3) => mountManagerReports(host, api4, current3),
          scripts: async (host) => script(host),
          "crm-books": async (host) => exchangePage(host),
          queue: async (host) => queuePage(host),
          connection: async (host) => connection(host)
        } });
        if (valid()) disposeSection = stop;
        else stop();
        return;
      }
      await {
        portfolio,
        "manager-statistics": managerStatistics,
        team: managerTeam,
        registry: async () => {
          if (["manager", "owner"].includes(context.role)) {
            await managerRegistry();
            return;
          }
          const expectedOwner = state.owner;
          const stop = await mountRegistry($2("view"), { ...crm, draft: (key2, value) => crm.draft(key2, value, expectedOwner), registryEdit: (body) => crm.registryEdit(body, expectedOwner), queue: (action3, id) => crm.queue(action3, id, expectedOwner) }, () => generation === renderGeneration);
          if (generation === renderGeneration) disposeSection = stop;
          else stop();
        },
        "active-time": activeTimePage,
        tasks: tasksPage,
        cabinet,
        shift,
        script,
        queue: queuePage,
        exchange: exchangePage,
        adoption,
        connection,
        help
      }[next]();
    } catch (e) {
      if (e.name !== "AbortError") notice(e.message);
    }
    if (generation === renderGeneration && !activePerson) document.querySelector("main").scrollTop = sectionScroll.get(next) || 0;
  }
  function calendarDate(value) {
    const day = String(value || "").slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(day) ? esc10((/* @__PURE__ */ new Date(day + "T00:00:00")).toLocaleDateString("ru-RU")) : esc10(value || "\u041D\u0435 \u0443\u043A\u0430\u0437\u0430\u043D\u043E");
  }
  function deliveryMarkup(fields) {
    return fields.delivery_result ? `<div class="delivery-fact"><dt>\u041F\u0435\u0440\u0435\u0434\u0430\u0447\u0430 \u043A\u043E\u043D\u0432\u0435\u0440\u0442\u0430</dt><dd data-field="delivery-result">${esc10(fields.delivery_result)}${fields.delivery_address ? "<br>" + esc10(fields.delivery_address) : ""}<br>${esc10(fields.delivery_phone || "-")}<br>${esc10(fields.delivery_comment)}<br><span class="muted">\u041F\u0440\u0438\u043D\u044F\u0442\u043E ${date(fields.delivery_received_at)} \xB7 ${esc10(fields.delivery_author)}</span></dd></div>` : "";
  }
  function contractDetails(contract) {
    const fields = contract.fields;
    const title = (key2) => context.fields?.find((field) => field.key === key2)?.title || key2;
    const articles = debtKeys.filter((key2) => fields[key2] !== null && fields[key2] !== void 0 && fields[key2] !== "");
    const people2 = new Map((contract.assignments || []).map((row) => [row.employee_uuid, row.name]));
    const responsibility = people2.size ? Array.from(people2.values()).join(", ") : Array.from(new Set([fields.responsible, fields.responsible_soft].filter(Boolean))).join(", ");
    return `<article class="contract-details" data-contract-details="${esc10(contract.contract_uuid)}"><h3>${esc10(contract.external_id)} \xB7 ${esc10(contract.creditor_id)}</h3><dl class="contract-facts"><div><dt>\u0414\u0430\u0442\u0430 \u041A\u0414</dt><dd data-field="kd_date">${calendarDate(fields.kd_date)}</dd></div><div><dt>\u0412\u044B\u0434\u0430\u043D\u043D\u0430\u044F \u0441\u0443\u043C\u043C\u0430</dt><dd data-field="issued_sum">${amount(fields.issued_sum, contract.currency || "\u0412\u0430\u043B\u044E\u0442\u0430 \u043D\u0435 \u0437\u0430\u0434\u0430\u043D\u0430")}</dd></div><div><dt>\u0414\u043D\u0435\u0439 \u043F\u0440\u043E\u0441\u0440\u043E\u0447\u043A\u0438</dt><dd data-field="dp">${esc10(fields.dp ?? "\u041D\u0435 \u0443\u043A\u0430\u0437\u0430\u043D\u043E")}</dd></div><div><dt>${people2.size ? "\u041E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u044B\u0435" : "\u041E\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043D\u043D\u044B\u0435 \u0438\u0437 \u0440\u0435\u0435\u0441\u0442\u0440\u0430"}</dt><dd data-field="responsibility">${esc10(responsibility || "\u041D\u0435 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u044B")}</dd></div><div><dt>\u041F\u043E\u0441\u043B\u0435\u0434\u043D\u044F\u044F \u043E\u043F\u043B\u0430\u0442\u0430</dt><dd>${amount(fields.last_payment_sum, contract.currency || "")}${fields.last_payment_date ? " \xB7 " + calendarDate(fields.last_payment_date) : ""}</dd></div>${deliveryMarkup(fields)}</dl><details><summary>\u0421\u0442\u0440\u0443\u043A\u0442\u0443\u0440\u0430 \u0437\u0430\u0434\u043E\u043B\u0436\u0435\u043D\u043D\u043E\u0441\u0442\u0438</summary>${articles.length ? `<dl class="debt-articles">${articles.map((key2) => `<div><dt>${esc10(title(key2))}</dt><dd data-field="${esc10(key2)}">${amount(fields[key2], contract.currency || "")}</dd></div>`).join("")}</dl>` : '<p class="muted">\u0420\u0430\u0437\u0431\u0438\u0432\u043A\u0430 \u0432 \u0438\u0441\u0442\u043E\u0447\u043D\u0438\u043A\u0435 \u043D\u0435 \u0443\u043A\u0430\u0437\u0430\u043D\u0430</p>'}</details></article>`;
  }
  function fullBusiness(contract) {
    const groups2 = /* @__PURE__ */ new Map();
    const available = new Set(contract.business_source?.present || Object.keys(contract.fields));
    for (const f of context.fields || []) {
      const section2 = f.section || { call: "\u0417\u0432\u043E\u043D\u043A\u0438", visit: "\u0412\u044B\u0435\u0437\u0434\u044B", contact: "\u041A\u043E\u043D\u0442\u0430\u043A\u0442\u044B", forecast: "\u041E\u0431\u0435\u0449\u0430\u043D\u0438\u044F", delivery: "\u041F\u0435\u0440\u0435\u0434\u0430\u0447\u0430 \u043A\u043E\u043D\u0432\u0435\u0440\u0442\u0430" }[f.group] || "\u0420\u0435\u043A\u0432\u0438\u0437\u0438\u0442\u044B \u0438 \u0443\u0447\u0451\u0442";
      if (!groups2.has(section2)) groups2.set(section2, []);
      const value = contract.fields[f.key];
      const text2 = !available.has(f.key) ? "\u041D\u0435\u0442 \u0432 \u0438\u0441\u0442\u043E\u0447\u043D\u0438\u043A\u0435" : value === null || value === void 0 || value === "" ? "\u041F\u0443\u0441\u0442\u043E" : f.type === "date" ? calendarDate(value) : esc10(value);
      groups2.get(section2).push(`<div data-business-key="${esc10(f.key)}"><dt>${esc10(f.title)}</dt><dd>${text2}</dd></div>`);
    }
    const extras = (contract.business_source?.extras || []).map((f) => ({ ...f, value: f.value === null ? null : String(f.value) + (f.currency ? " " + f.currency : "") }));
    return `<details class="business-details"><summary>\u0412\u0441\u0435 \u0441\u0432\u0435\u0434\u0435\u043D\u0438\u044F \xB7 ${esc10(contract.external_id)} \xB7 ${esc10(contract.fields.partner || contract.creditor_id)}${contract.currency ? " \xB7 " + esc10(contract.currency) : ""}</summary>${Array.from(groups2).map(([title, rows]) => `<section><h3>${esc10(title)}</h3><dl class="contract-facts">${rows.join("")}</dl></section>`).join("")}${extras.length ? `<section><h3>\u041F\u0440\u043E\u0447\u0438\u0435 \u0441\u0432\u0435\u0434\u0435\u043D\u0438\u044F \u0438\u0441\u0442\u043E\u0447\u043D\u0438\u043A\u0430</h3><dl class="contract-facts">${extras.map((f) => `<div><dt>${esc10(f.title)}</dt><dd>${f.value === null || f.value === "" ? "\u041F\u0443\u0441\u0442\u043E" : esc10(f.value)}</dd></div>`).join("")}</dl></section>` : ""}</details>`;
  }
  async function activeTimePage() {
    const activity2 = (await crm.state()).activity;
    const today2 = (/* @__PURE__ */ new Date()).toLocaleDateString("en-CA", { timeZone: context.timezone || "Europe/Samara" });
    $2("view").innerHTML = `<div class="toolbar"><label>\u0421 <input id="active-since" type="date" value="${today2}"></label><label>\u041F\u043E <input id="active-until" type="date" value="${today2}"></label>${button("active-load", "\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u044C")}${button("active-shift", "\u0417\u0430\u044F\u0432\u043B\u0435\u043D\u043D\u0430\u044F \u0441\u043C\u0435\u043D\u0430")}${button("active-queue", "\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0430 \u0438\u043D\u0442\u0435\u0440\u0432\u0430\u043B\u043E\u0432")}</div><p>\u0421\u0447\u0438\u0442\u0430\u0435\u0442\u0441\u044F \u0432\u0440\u0435\u043C\u044F \u0432 \u0432\u0438\u0434\u0438\u043C\u043E\u043C \u043E\u043A\u043D\u0435 CRM \u0432 \u0444\u043E\u043A\u0443\u0441\u0435. \u041F\u043E\u0441\u043B\u0435 120 \u0441\u0435\u043A\u0443\u043D\u0434 \u0431\u0435\u0437 \u0432\u0437\u0430\u0438\u043C\u043E\u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044F \u043D\u0430\u0447\u0438\u043D\u0430\u0435\u0442\u0441\u044F \u043F\u0440\u043E\u0441\u0442\u043E\u0439. \u0411\u043B\u043E\u043A\u0438\u0440\u043E\u0432\u043A\u0430, \u0441\u043E\u043D \u0438 \u0440\u0430\u0431\u043E\u0442\u0430 \u0432 \u0434\u0440\u0443\u0433\u043E\u043C \u043E\u043A\u043D\u0435 \u043D\u0435 \u0443\u0447\u0438\u0442\u044B\u0432\u0430\u044E\u0442\u0441\u044F. \u0427\u0442\u0435\u043D\u0438\u0435 \u0438 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u043D\u044B\u0439 \u0440\u0430\u0437\u0433\u043E\u0432\u043E\u0440 \u0431\u0435\u0437 \u0432\u0432\u043E\u0434\u0430 \u043C\u043E\u0433\u0443\u0442 \u043F\u043E\u043F\u0430\u0441\u0442\u044C \u0432 \u043F\u0440\u043E\u0441\u0442\u043E\u0439.</p><div id="active-result"></div>`;
    if (activity2?.clock_quality === "unconfirmed") $2("view").insertAdjacentHTML("afterbegin", '<p role="status">\u041D\u0435\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u043E\u0439 \u0442\u043E\u0447\u043A\u0438 \u043E\u0442\u0441\u0447\u0451\u0442\u0430. \u041D\u043E\u0432\u044B\u0435 \u0438\u043D\u0442\u0435\u0440\u0432\u0430\u043B\u044B \u043F\u043E\u043A\u0430 \u043D\u0435 \u0443\u0447\u0438\u0442\u044B\u0432\u0430\u044E\u0442\u0441\u044F. \u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0438\u0442\u0435\u0441\u044C \u043A API; \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0435 \u0438\u043D\u0442\u0435\u0440\u0432\u0430\u043B\u044B \u043E\u0441\u0442\u0430\u044E\u0442\u0441\u044F \u0432 \u043E\u0447\u0435\u0440\u0435\u0434\u0438.</p>');
    const duration = (ms) => `${Math.floor(ms / 36e5)} \u0447 ${Math.floor(ms / 6e4) % 60} \u043C\u0438\u043D ${Math.floor(ms / 1e3) % 60} \u0441`;
    const load3 = async () => {
      const data2 = await request2("GET", "active-time", void 0, { since: $2("active-since").value, until: $2("active-until").value });
      $2("active-result").innerHTML = `<div class="summary"><div class="metric">\u0410\u043A\u0442\u0438\u0432\u043D\u043E\u0435 \u0432\u0440\u0435\u043C\u044F<strong>${duration(data2.active_ms)}</strong></div><div class="metric">\u041F\u0440\u043E\u0441\u0442\u043E\u0439 \u0432 \u043E\u043A\u043D\u0435 CRM<strong>${duration(data2.idle_ms)}</strong></div></div><p>\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u043E \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C \xB7 ${esc10(data2.timezone)} \xB7 ${date(data2.as_of)}. \u041F\u0435\u0440\u0435\u0441\u0435\u043A\u0430\u044E\u0449\u0438\u0435\u0441\u044F \u0438\u043D\u0442\u0435\u0440\u0432\u0430\u043B\u044B \u043D\u0435\u0441\u043A\u043E\u043B\u044C\u043A\u0438\u0445 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432 \u0443\u0447\u0438\u0442\u044B\u0432\u0430\u044E\u0442\u0441\u044F \u043E\u0434\u0438\u043D \u0440\u0430\u0437.</p>` + table(["\u0414\u0435\u043D\u044C", "\u0410\u043A\u0442\u0438\u0432\u043D\u043E", "\u041F\u0440\u043E\u0441\u0442\u043E\u0439"], data2.days.map((d) => `<tr><td>${calendarDate(d.day)}</td><td>${duration(d.active_ms)}</td><td>${duration(d.idle_ms)}</td></tr>`));
    };
    bind("active-load", load3);
    bind("active-shift", () => go2("shift"));
    bind("active-queue", () => go2("queue"));
    await load3();
  }
  async function managerStatistics() {
    const generation = renderGeneration, expectedOwner = state.owner;
    const valid = () => generation === renderGeneration && section === "manager-statistics";
    const stop = await mountManagerReports($2("view"), { ...crm, draft: (key2, value) => crm.draft(key2, value, expectedOwner) }, valid);
    if (valid()) disposeSection = stop;
    else stop();
  }
  async function managerTeam() {
    const generation = renderGeneration, expectedOwner = state.owner;
    const valid = () => generation === renderGeneration && section === "team";
    const stop = await mountManagerTeam($2("view"), { ...crm, draft: (key2, value) => crm.draft(key2, value, expectedOwner) }, context, valid, async (id) => {
      const saved = (await crm.draft("registry-view")).data || {};
      await crm.draft("registry-view", { ...saved, search: "", employee_uuid: id, work: "all", unassigned: "0", offset: 0 }, expectedOwner);
      await go2("registry");
    });
    if (valid()) disposeSection = stop;
    else stop();
  }
  async function managerRegistry() {
    const generation = renderGeneration, expectedOwner = state.owner;
    const valid = () => generation === renderGeneration && section === "registry" && !activePerson;
    const api4 = { ...crm, draft: (key2, value) => crm.draft(key2, value, expectedOwner), registryEdit: (body) => crm.registryEdit(body, expectedOwner), queue: (action3, id) => crm.queue(action3, id, expectedOwner) };
    const stop = await mountRegistry($2("view"), api4, valid, { manager: true, distribution: context.distribution?.enabled, openPerson: (id) => void openCard(id).catch((e) => notice(e.message)) });
    if (valid()) disposeSection = stop;
    else stop();
  }
  async function portfolio(search = portfolioSearch, offset = portfolioOffset) {
    const generation = ++portfolioGeneration;
    portfolioSearch = search;
    portfolioOffset = offset;
    await crm.draft("portfolio-view", { view: portfolioView, work: portfolioWork, employee: portfolioEmployee });
    const personal = portfolioView === "mine";
    const data2 = await request2(
      "GET",
      personal ? "work-portfolio" : "portfolio",
      void 0,
      personal ? { search, work: portfolioWork, employee_uuid: portfolioEmployee, offset, limit: 100 } : { search, limit: 500 }
    );
    if (generation !== portfolioGeneration || section !== "portfolio") return;
    const modes = `<div class="tabs portfolio-modes"><button data-portfolio-view="mine" class="${personal ? "active" : ""}">\u041C\u043E\u0439 \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044C</button><button data-portfolio-view="all" class="${personal ? "" : "active"}">\u0412\u0441\u0435 \u043A\u043B\u0438\u0435\u043D\u0442\u044B</button></div>`;
    const staff2 = context.staff || [];
    const selector = personal && staff2.length > 1 ? `<label class="portfolio-person">\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A <select id="portfolio-employee">${staff2.map((p) => `<option value="${esc10(p.employee_uuid)}"${p.employee_uuid === portfolioEmployee ? " selected" : ""}>${esc10(p.name)}</option>`).join("")}</select></label>` : "";
    if (personal) {
      const options = [
        ["all", "\u0412\u0441\u0435", data2.counts.all],
        ["call", "\u0417\u0432\u043E\u043D\u043A\u0438", data2.counts.call],
        ["visit", "\u0412\u044B\u0435\u0437\u0434\u044B", data2.counts.visit],
        ["both", "\u041E\u0431\u0430", data2.counts.both],
        ...data2.counts.delivery || context.role === "courier" ? [["delivery", "\u0414\u043E\u0441\u0442\u0430\u0432\u043A\u0430", data2.counts.delivery]] : []
      ];
      const filters = `<div class="work-filters">${options.map(([key2, title, count]) => `<button data-work-filter="${key2}" class="${portfolioWork === key2 ? "active" : ""}" aria-pressed="${portfolioWork === key2}">${title} <span>${count}</span></button>`).join("")}</div>`;
      const debt = Object.entries(data2.currencies || {}).map(([unit, value]) => amount(value, unit)).join(" \xB7 ");
      $2("view").innerHTML = modes + selector + filters + `<div class="toolbar"><input type="search" id="search" placeholder="\u0418\u043C\u044F, \u0434\u043E\u0433\u043E\u0432\u043E\u0440, \u0442\u0435\u043B\u0435\u0444\u043E\u043D, \u0430\u0434\u0440\u0435\u0441" value="${esc10(search)}">${button("search-go", "\u041D\u0430\u0439\u0442\u0438")}${button("refresh", "\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C")}</div><p class="muted" data-work-total>\u0414\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432: ${data2.total_contracts} \xB7 \u041A\u043B\u0438\u0435\u043D\u0442\u043E\u0432: ${data2.total_people}${debt ? " \xB7 \u0414\u043E\u043B\u0433: " + debt : ""}${data2.unknown_amounts ? " \xB7 \u0411\u0435\u0437 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u043E\u0439 \u0441\u0443\u043C\u043C\u044B \u0438\u043B\u0438 \u0432\u0430\u043B\u044E\u0442\u044B: " + data2.unknown_amounts : ""}</p>` + table(["\u041A\u043B\u0438\u0435\u043D\u0442", "\u0414\u043E\u0433\u043E\u0432\u043E\u0440 / \u043A\u0440\u0435\u0434\u0438\u0442\u043E\u0440", "\u0420\u0430\u0431\u043E\u0442\u0430", "\u041F\u043E\u0441\u043B\u0435\u0434\u043D\u0438\u0439 \u0437\u0432\u043E\u043D\u043E\u043A", "\u041F\u043E\u0441\u043B\u0435\u0434\u043D\u0438\u0439 \u0432\u044B\u0435\u0437\u0434", "\u0414\u043E\u043B\u0433"], data2.contracts.map((row) => {
        const f = row.fields;
        return `<tr data-work-contract="${esc10(row.contract_uuid)}"><td><button class="link" data-person="${esc10(row.person_uuid)}">${esc10(row.client_name || "\u0411\u0435\u0437 \u0438\u043C\u0435\u043D\u0438")}</button></td><td>${esc10(row.external_id)}<br><span class="muted">${esc10(f.partner || row.creditor_id)}</span></td><td>${row.work_kinds.map((kind2) => `<span class="work-kind">${esc10(labels[kind2] || kind2)}</span>`).join("")}</td><td>${esc10(f.soft_call_status || "\u2014")}${f.soft_comm_date ? "<br>" + calendarDate(f.soft_comm_date) : ""}${f.soft_comment ? '<div class="queue-comment">' + esc10(f.soft_comment) + "</div>" : ""}</td><td>${esc10(f.call_status || "\u2014")}${f.visit_date ? "<br>" + calendarDate(f.visit_date) : ""}${f.visit_comment ? '<div class="queue-comment">' + esc10(f.visit_comment) + "</div>" : ""}</td><td>${amount(f.debt_today ?? f.debt_total, row.currency || "\u0412\u0430\u043B\u044E\u0442\u0430 \u043D\u0435 \u0437\u0430\u0434\u0430\u043D\u0430")}</td></tr>`;
      })) + (data2.contracts.length ? "" : '<p class="empty">\u041F\u043E \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u043C \u0443\u0441\u043B\u043E\u0432\u0438\u044F\u043C \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432 \u043D\u0435\u0442</p>') + `<div class="toolbar portfolio-pages"><button id="work-previous"${offset === 0 ? " disabled" : ""}>\u041D\u0430\u0437\u0430\u0434</button><span>${data2.total_contracts ? offset + 1 : 0}\u2013${Math.min(offset + data2.contracts.length, data2.total_contracts)} \u0438\u0437 ${data2.total_contracts}</span><button id="work-next"${data2.has_more ? "" : " disabled"}>\u0414\u0430\u043B\u0435\u0435</button></div>`;
      bind("work-previous", () => portfolio(portfolioSearch, Math.max(0, portfolioOffset - 100)));
      bind("work-next", () => portfolio(portfolioSearch, portfolioOffset + 100));
      $2("view").querySelectorAll("[data-work-filter]").forEach((b) => b.onclick = () => {
        portfolioWork = b.dataset.workFilter;
        void portfolio(portfolioSearch, 0).catch((e) => notice(e.message));
      });
      const employee = $2("portfolio-employee");
      if (employee) employee.onchange = () => {
        portfolioEmployee = employee.value;
        portfolioWork = "all";
        void portfolio(portfolioSearch, 0).catch((e) => notice(e.message));
      };
    } else {
      $2("view").innerHTML = modes + `<div class="toolbar"><input type="search" id="search" placeholder="\u0418\u043C\u044F, \u0434\u043E\u0433\u043E\u0432\u043E\u0440, \u0442\u0435\u043B\u0435\u0444\u043E\u043D, \u0430\u0434\u0440\u0435\u0441" value="${esc10(search)}">${button("search-go", "\u041D\u0430\u0439\u0442\u0438")}${button("refresh", "\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C")}</div><div class="muted">\u041A\u043B\u0438\u0435\u043D\u0442\u043E\u0432: ${data2.total_people} \xB7 \u0414\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432: ${data2.total_contracts}${data2.truncated ? " \xB7 \u0423\u0442\u043E\u0447\u043D\u0438\u0442\u0435 \u043F\u043E\u0438\u0441\u043A" : ""}</div>` + table(
        ["\u041A\u043B\u0438\u0435\u043D\u0442", "\u0414\u043E\u0433\u043E\u0432\u043E\u0440\u044B", "\u041E\u0441\u0442\u0430\u0442\u043E\u043A \u043F\u043E \u0432\u0430\u043B\u044E\u0442\u0430\u043C"],
        data2.people.map(
          (p) => `<tr><td><button class="link" data-person="${esc10(p.person_uuid)}">${esc10(p.name || "\u0411\u0435\u0437 \u0438\u043C\u0435\u043D\u0438")}</button></td><td>${p.contracts}</td><td>${Object.keys(p.currencies || {}).length ? Object.entries(p.currencies).map(([k, v]) => amount(v, k)).join("<br>") : "\u041E\u0442\u043A\u0440\u043E\u0439\u0442\u0435 \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u044B"}</td></tr>`
        )
      );
    }
    $2("view").querySelectorAll("[data-portfolio-view]").forEach((b) => b.onclick = () => {
      portfolioView = b.dataset.portfolioView;
      void portfolio(portfolioSearch, 0).catch((e) => notice(e.message));
    });
    bind("search-go", () => portfolio($2("search").value, 0));
    bind("refresh", () => portfolio());
    $2("search").onkeydown = (e) => {
      if (e.key === "Enter") void portfolio($2("search").value, 0).catch((error) => notice(error.message));
    };
    if (context.role === "employee") {
      $2("view").insertAdjacentHTML("afterbegin", `<div class="toolbar portfolio-context">${button("portfolio-tasks", "\u0417\u0430\u0434\u0430\u0447\u0438")}${button("portfolio-queue", "\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0430 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u043E\u0432")}${button("portfolio-script", "\u0421\u043A\u0440\u0438\u043F\u0442")}${button("portfolio-help", "\u0421\u043F\u0440\u0430\u0432\u043A\u0430")}</div>`);
      bind("portfolio-tasks", () => go2("tasks"));
      bind("portfolio-queue", () => go2("queue"));
      bind("portfolio-script", () => go2("script"));
      bind("portfolio-help", () => go2("help"));
    }
    document.querySelectorAll("[data-person]").forEach((b) => b.onclick = () => void openCard(b.dataset.person));
  }
  async function openCard(person) {
    clearTimeout(cardRetryTimer);
    clearTimeout(syncTimer);
    syncForced = false;
    if (!activePerson) sectionScroll.set(section, document.querySelector("main").scrollTop);
    portfolioGeneration++;
    taskGeneration++;
    const generation = ++renderGeneration;
    activePerson = person;
    cardSyncGeneration++;
    lastCardAcknowledgements = "";
    cardRefreshPending = false;
    crm.navigated?.({ section: section === "registry" ? "registry" : section === "tasks" ? "tasks" : "portfolio", person });
    card = null;
    cardScript = null;
    cardRefreshError = "";
    const createTask = taskSelectClient;
    cardOrigin = section === "tasks" || createTask ? "tasks" : section === "registry" ? "registry" : "portfolio";
    taskSelectClient = false;
    $2("view").innerHTML = '<p role="status">\u0417\u0430\u0433\u0440\u0443\u0437\u043A\u0430 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0438\u2026</p>';
    try {
      const loaded = await request2("GET", "card", void 0, { person_uuid: person });
      let loadedScript = null;
      if (can("call")) {
        try {
          loadedScript = await request2("GET", "script");
        } catch (error) {
          if (error.name === "AbortError") throw error;
        }
      }
      if (generation !== renderGeneration || activePerson !== person) return;
      card = loaded;
      cardScript = loadedScript;
      tab = "overview";
      telemetry("feature_opened", "card");
      await renderCard();
      document.querySelector("main").scrollTop = 0;
      scheduleSync();
      if (createTask) {
        if (can("task") && card.contracts.some((entry) => contractCan(entry, "task"))) await action2("task");
        else notice("\u041D\u0435\u0442 \u043F\u0440\u0430\u0432\u0430 \u043D\u0430\u0437\u043D\u0430\u0447\u0430\u0442\u044C \u0437\u0430\u0434\u0430\u0447\u0443 \u043F\u043E \u044D\u0442\u043E\u043C\u0443 \u043A\u043B\u0438\u0435\u043D\u0442\u0443.");
      }
    } catch (e) {
      if (generation !== renderGeneration || e.name === "AbortError") return;
      activePerson = "";
      card = null;
      cardScript = null;
      $2("view").innerHTML = button("card-load-back", cardOrigin === "tasks" ? "\u0412\u0435\u0440\u043D\u0443\u0442\u044C\u0441\u044F \u043A \u0437\u0430\u0434\u0430\u0447\u0430\u043C" : "\u0412\u0435\u0440\u043D\u0443\u0442\u044C\u0441\u044F \u043A \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044E");
      bind("card-load-back", () => go2(cardOrigin));
      notice(e.message);
    }
  }
  async function renderCard() {
    $2("title").textContent = card.person.name || "\u041A\u0430\u0440\u0442\u043E\u0447\u043A\u0430 \u043A\u043B\u0438\u0435\u043D\u0442\u0430";
    const actions = [
      "call",
      "visit",
      "delivery",
      "note",
      "contact",
      "promise",
      "task",
      "reported_payment",
      "document"
    ].filter((kind2) => !(context.role === "courier" && kind2 === "task") && can(kind2) && card.contracts.some((contract) => !Array.isArray(contract.allowed_actions) || contract.allowed_actions.includes(kind2)));
    const tabs2 = [
      ["overview", "\u041E\u0431\u0437\u043E\u0440"],
      ["contracts", "\u0414\u043E\u0433\u043E\u0432\u043E\u0440\u044B"],
      ["history", "\u0418\u0441\u0442\u043E\u0440\u0438\u044F"],
      ["tasks", "\u0417\u0430\u0434\u0430\u0447\u0438"],
      ["promises", "\u041E\u0431\u0435\u0449\u0430\u043D\u0438\u044F"],
      ["payments", "\u041E\u043F\u043B\u0430\u0442\u044B"],
      ["documents", "\u0414\u043E\u043A\u0443\u043C\u0435\u043D\u0442\u044B"]
    ];
    $2("view").innerHTML = `<div class="toolbar">${button("back", cardOrigin === "tasks" ? "\u2190 \u0417\u0430\u0434\u0430\u0447\u0438" : "\u2190 \u041F\u043E\u0440\u0442\u0444\u0435\u043B\u044C")}${button("card-refresh", "\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0443")}${actions.map((k) => `<button data-action="${k}"${k === "call" ? ' class="primary"' : ""}>${labels[k]}</button>`).join("")}</div><section id="card-saves" aria-label="\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0430 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u043E\u0432"></section><div class="tabs">${tabs2.map(([id, title]) => `<button data-tab="${id}" class="${tab === id ? "active" : ""}">${title}</button>`).join("")}</div><div class="card-workspace${cardScript?.script ? " with-script" : ""}"><div id="card-content"></div>${cardScript?.script ? `<aside class="card-script-watermark" aria-label="\u0421\u043A\u0440\u0438\u043F\u0442 \u0440\u0430\u0437\u0433\u043E\u0432\u043E\u0440\u0430"><h2>${esc10(cardScript.script.title)}</h2>${cardScript.script.steps.map((step) => `<section><h3>${esc10(step.title)}</h3><p>${esc10(step.text)}</p></section>`).join("")}<small>\u0412\u0435\u0440\u0441\u0438\u044F ${esc10(cardScript.published_revision)}</small></aside>` : ""}</div>`;
    $2("back").textContent = cardOrigin === "tasks" ? "\u2190 \u0417\u0430\u0434\u0430\u0447\u0438" : cardOrigin === "registry" ? "\u2190 \u0420\u0435\u0435\u0441\u0442\u0440" : "\u2190 \u041F\u043E\u0440\u0442\u0444\u0435\u043B\u044C";
    bind("back", () => go2(cardOrigin));
    if (["manager", "owner", "analyst"].includes(context.role) && card.contracts.some((r) => r.writable_fields?.length)) {
      $2("card-refresh").insertAdjacentHTML("beforebegin", button("card-edit", "\u0420\u0435\u0434\u0430\u043A\u0442\u0438\u0440\u043E\u0432\u0430\u0442\u044C \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0443", true));
      bind("card-edit", async () => {
        const generation = renderGeneration, expectedOwner = state.owner;
        await editClientCard(
          $2("dialog"),
          card,
          { ...crm, draft: (key2, value) => crm.draft(key2, value, expectedOwner), registryEdit: (body) => crm.registryEdit(body, expectedOwner), queue: () => crm.queue(void 0, void 0, expectedOwner) },
          () => {
            if (generation === renderGeneration) {
              notice(`\u041F\u0440\u0430\u0432\u043A\u0438 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u044B ${savedOn} \u0438 \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u044F\u044E\u0442\u0441\u044F. \u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435 \u2014 \u0432 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0435 \u0438 \u043E\u0447\u0435\u0440\u0435\u0434\u0438.`);
              scheduleSync(true);
            }
          },
          () => generation === renderGeneration && state.owner === expectedOwner
        );
      });
    }
    bind("card-refresh", () => syncCurrentView(true));
    document.querySelectorAll("[data-tab]").forEach(
      (b) => b.onclick = () => {
        tab = b.dataset.tab;
        void renderCard();
      }
    );
    document.querySelectorAll("[data-action]").forEach((b) => b.onclick = () => void action2(b.dataset.action));
    const host = $2("card-content");
    if (tab === "overview" || tab === "contracts") {
      host.innerHTML = table(
        ["\u0414\u043E\u0433\u043E\u0432\u043E\u0440 / \u043A\u0440\u0435\u0434\u0438\u0442\u043E\u0440", "\u041A\u043E\u043D\u0442\u0430\u043A\u0442\u044B", "\u041E\u0441\u0442\u0430\u0442\u043E\u043A", "\u041F\u043E\u0441\u043B\u0435\u0434\u043D\u0438\u0439 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442"],
        card.contracts.map(
          (r) => `<tr><td><b>${esc10(r.external_id)}</b><br><span class="muted">${esc10(r.creditor_id)}</span></td><td>${esc10(r.fields.phone || r.fields.phone_main || "\u2014")}<br>${esc10(r.fields.address || "\u2014")}</td><td class="amount">${amount(r.fields.debt_today ?? r.fields.debt_total, r.currency || "\u0412\u0430\u043B\u044E\u0442\u0430 \u043D\u0435 \u0437\u0430\u0434\u0430\u043D\u0430")}</td><td>${r.fields.soft_call_status || r.fields.soft_comment ? `<div>\u0417\u0432\u043E\u043D\u043E\u043A: ${esc10(r.fields.soft_call_status || "\u2014")}<br><span class="muted">${esc10(r.fields.soft_comment || "")}</span></div>` : ""}${r.fields.call_status || r.fields.visit_comment ? `<div>\u0412\u044B\u0435\u0437\u0434: ${esc10(r.fields.call_status || "\u2014")}<br><span class="muted">${esc10(r.fields.visit_comment || "")}</span></div>` : ""}</td></tr>`
        )
      );
      host.innerHTML += card.contracts.map((c) => contractDetails(c) + fullBusiness(c)).join("");
      if (tab === "overview")
        host.innerHTML += `<div class="summary"><div class="metric">\u0414\u043E\u0433\u043E\u0432\u043E\u0440\u044B<strong>${card.contracts.length}</strong></div><div class="metric">\u041E\u0442\u043A\u0440\u044B\u0442\u044B\u0435 \u0437\u0430\u0434\u0430\u0447\u0438<strong>${card.entities.filter((e) => e.kind === "task" && e.status === "open").length}</strong></div><div class="metric">\u0417\u0430\u044F\u0432\u043B\u0435\u043D\u043D\u044B\u0435 \u043E\u043F\u043B\u0430\u0442\u044B<strong>${card.entities.filter((e) => e.kind === "reported_payment").length}</strong><small>\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435 \u043F\u043E\u0441\u0442\u0443\u043F\u043B\u0435\u043D\u0438\u044F \u0445\u0440\u0430\u043D\u0438\u0442\u0441\u044F \u043E\u0442\u0434\u0435\u043B\u044C\u043D\u043E</small></div></div>`;
    } else if (tab === "history") {
      host.innerHTML = card.history.length ? card.history.map(
        (e) => `<article class="record"><h3>${esc10(labels[e.kind] || e.kind)} <span class="tag">${esc10({ legacy: "\u041F\u0440\u0435\u0436\u043D\u0435\u0435 \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u0435", app: "\u041F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u0435", excel: "\u0420\u0435\u0435\u0441\u0442\u0440", unknown: "\u0418\u0441\u0442\u043E\u0447\u043D\u0438\u043A \u043D\u0435 \u043E\u043F\u0440\u0435\u0434\u0435\u043B\u0451\u043D" }[e.origin] || e.origin)}</span></h3><div class="muted">${historyWhen(e)} \xB7 ${esc10(e.author || e.payload.legacy_employee || "\u0410\u0432\u0442\u043E\u0440 \u043D\u0435 \u0443\u043A\u0430\u0437\u0430\u043D")} \xB7 ${e.contract_ids.length} \u0434\u043E\u0433. ${e.projection_status === "needs_review" ? "\xB7 \u0422\u0440\u0435\u0431\u0443\u0435\u0442 \u0441\u0432\u0435\u0440\u043A\u0438" : ""}</div><p>${esc10(
          (e.kind === "delivery" ? [e.payload.delivered ? "\u041A\u043E\u043D\u0432\u0435\u0440\u0442 \u043F\u0435\u0440\u0435\u0434\u0430\u043D" : "\u041A\u043E\u043D\u0432\u0435\u0440\u0442 \u043D\u0435 \u043F\u0435\u0440\u0435\u0434\u0430\u043D", e.payload.delivery_address, e.payload.delivery_phone, e.payload.delivery_comment].filter(Boolean).join("\n") : "") || e.payload.text || e.payload.title || Object.entries(e.payload.values || {}).map(
            ([k, v]) => (context.fields.find((f) => f.key === k)?.title || k) + ": " + (v ?? "\u041E\u0447\u0438\u0449\u0435\u043D\u043E")
          ).join("\n")
        )}</p>${e.payload.script_ref ? `<p class="muted">\u041F\u043E\u0434\u0441\u043A\u0430\u0437\u043A\u0430: \u0432\u0435\u0440\u0441\u0438\u044F ${esc10(e.payload.script_ref.published_revision)}</p>` : ""}${e.reason ? `<p>\u041F\u0440\u0438\u0447\u0438\u043D\u0430: ${esc10(e.reason)}</p>` : ""}${["call", "visit", "note", "contact"].includes(e.kind) && can("correct") && e.can_correct !== false ? `<button data-correct="${e.event_id}">\u0418\u0441\u043F\u0440\u0430\u0432\u0438\u0442\u044C</button>` : ""}</article>`
      ).join("") : '<div class="empty">\u0418\u0441\u0442\u043E\u0440\u0438\u044F \u043F\u043E\u043A\u0430 \u043F\u0443\u0441\u0442\u0430</div>';
      document.querySelectorAll("[data-correct]").forEach(
        (b) => b.onclick = () => void action2(
          "correction",
          card.history.find((e) => e.event_id === b.dataset.correct)
        )
      );
    } else {
      const kinds = {
        tasks: ["task"],
        promises: ["promise"],
        payments: ["reported_payment", "payment_confirmed"],
        documents: ["document"]
      }[tab];
      const rows = card.entities.filter((e) => kinds.includes(e.kind));
      host.innerHTML = rows.length ? table(
        ["\u0412\u0438\u0434", "\u0421\u043E\u0434\u0435\u0440\u0436\u0430\u043D\u0438\u0435", "\u0421\u0440\u043E\u043A / \u0441\u0443\u043C\u043C\u0430", "\u0421\u043E\u0441\u0442\u043E\u044F\u043D\u0438\u0435"],
        rows.map(
          (e) => `<tr><td>${esc10(labels[e.kind])}</td><td>${esc10(e.data.title || e.data.filename || e.data.text || "\u2014")}${e.assignee ? `<br><span class="muted">${esc10(e.assignee)}</span>` : ""}</td><td>${e.data.amount != null ? amount(e.data.amount, e.data.currency) : date(e.data.due_at)}</td><td>${e.status === "done" ? "\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u043E" : e.kind === "task" ? "\u0412 \u0440\u0430\u0431\u043E\u0442\u0435" : "\u0417\u0430\u0444\u0438\u043A\u0441\u0438\u0440\u043E\u0432\u0430\u043D\u043E"}${e.kind === "document" ? `<br><button class="link" data-document="${e.entity_id}">\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0434\u043E\u043A\u0443\u043C\u0435\u043D\u0442</button>` : ""}${e.kind === "task" && e.status === "open" && e.can_complete !== false ? `<br><button class="link" data-complete="${e.entity_id}">\u0412\u044B\u043F\u043E\u043B\u043D\u0435\u043D\u043E</button>` : ""}</td></tr>`
        )
      ) : '<div class="empty">\u0417\u0430\u043F\u0438\u0441\u0435\u0439 \u043F\u043E\u043A\u0430 \u043D\u0435\u0442</div>';
      document.querySelectorAll("[data-document]").forEach(
        (b) => b.onclick = async () => {
          const entity = rows.find(
            (v) => v.entity_id === b.dataset.document
          );
          const result2 = await crm.document(
            entity.data.filename,
            entity.data.document_id
          );
          if (!result2.ok && !result2.cancelled) notice(result2.text);
        }
      );
      document.querySelectorAll("[data-complete]").forEach(
        (b) => b.onclick = () => {
          const e = rows.find((v) => v.entity_id === b.dataset.complete);
          void submitCommand({
            kind: "task_complete",
            contract_ids: [e.contract_uuid],
            payload: { entity_id: e.entity_id, entity_revision: e.revision }
          });
        }
      );
    }
    try {
      paintCardSaves(await queueEntries());
    } catch (error) {
      if ($2("card-saves")) $2("card-saves").textContent = error.message;
    }
  }
  async function submitCommand(raw, expectedOwner = state.owner) {
    const body = { schema_version: 1, operation_id: crypto.randomUUID(), ...raw };
    const result2 = await crm.command(body, expectedOwner);
    if (!result2.ok) throw new Error(result2.text);
    telemetry(
      "command_queued",
      raw.kind === "task_complete" ? "task" : raw.kind === "correction" ? "card" : raw.kind,
      { operation_id: body.operation_id }
    );
    notice(`\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E ${savedOn}.`, "status");
    scheduleSync();
  }
  async function action2(kind2, original) {
    if (formOpening || $2("dialog").open) return;
    formOpening = true;
    try {
      await actionForm(kind2, original, renderGeneration);
    } finally {
      formOpening = false;
      if (cardRefreshPending && !$2("dialog").open) scheduleSync();
    }
  }
  async function actionForm(kind2, original, viewGeneration) {
    const expectedOwner = state.owner;
    const formAPI = { draft: (key2, value) => crm.draft(key2, value, expectedOwner), command: (body) => crm.command(body, expectedOwner), document: (contract) => crm.document(contract, void 0, expectedOwner) };
    const formRevisions = structuredClone(card.contracts.map((c) => [c.contract_uuid, c.revisions]));
    const target = kind2 === "correction" ? original.kind : kind2;
    telemetry("workflow_started", target);
    const dialog = $2("dialog");
    if (kind2 === "delivery") {
      await mountDeliveryForm(
        dialog,
        card.contracts.map((row) => ({ ...row, client_name: card.person.name })),
        { fields: context.fields, draft: (key2, ...value) => formAPI.draft(key2, ...value), command: (body) => formAPI.command(body), close: () => dialog.close() }
      );
      dialog.showModal();
      return;
    }
    if (target === "document") {
      const available = card.contracts.filter((entry) => contractCan(entry, "document"));
      const selected2 = available[0];
      if (!selected2) {
        notice("\u041D\u0435\u0442 \u043F\u0440\u0430\u0432\u0430 \u043F\u0440\u0438\u043A\u0440\u0435\u043F\u043B\u044F\u0442\u044C \u0434\u043E\u043A\u0443\u043C\u0435\u043D\u0442 \u043A \u044D\u0442\u043E\u043C\u0443 \u043A\u043B\u0438\u0435\u043D\u0442\u0443.");
        return;
      }
      if (available.length > 1) {
        await documentForm();
        return;
      }
      const uploaded = await formAPI.document(selected2.contract_uuid);
      if (!uploaded.ok) {
        if (!uploaded.cancelled) notice(uploaded.text);
        return;
      }
      await submitCommand({
        kind: "document",
        contract_ids: [selected2.contract_uuid],
        payload: {
          document_id: uploaded.data.document_id,
          sha256: uploaded.data.sha256,
          filename: uploaded.data.filename
        }
      }, expectedOwner);
      return;
    }
    const map = {
      call: ["soft_call_status", "soft_comment", "soft_comm_date"],
      visit: [
        "call_status",
        "visit_date",
        "visit_comment",
        "contact_with",
        "agreed_payment",
        "who_at_address"
      ],
      contact: ["phone", "phone_main", "address", "extra_address"],
      note: [],
      promise: [],
      task: [],
      reported_payment: []
    };
    const keys = map[target] || [], singular = ["promise", "task", "reported_payment"].includes(target);
    const draftKey = card.person.person_uuid + ":" + kind2 + (original ? ":" + original.event_id : "");
    const saved = (await formAPI.draft(draftKey)).data;
    let callScript = saved?.script_snapshot ?? (kind2 === "call" && cardScript?.script ? structuredClone(cardScript) : null);
    let scriptError = "";
    if (kind2 === "call" && !callScript) {
      try {
        const published = await request2("GET", "script");
        callScript = {
          script: published.script,
          published_revision: published.published_revision,
          published_at: published.published_at,
          script_ref: published.script_ref ?? null
        };
      } catch (error) {
        scriptError = error.message;
      }
    }
    if (kind2 === "call" && callScript?.script) {
      cardScript = callScript;
      repaintWatermark(callScript);
    }
    let scriptShown = saved?.script_shown === true || kind2 === "call" && !!callScript?.script && !!document.querySelector(".card-script-watermark");
    const controls = keys.map((key2) => {
      const def = context.fields.find((f) => f.key === key2);
      const value = saved?.values?.[key2] ?? original?.payload?.values?.[key2] ?? "";
      const options = context.dropdowns[key2];
      return `<div class="field ${key2.includes("comment") ? "span" : ""}"><label for="v-${key2}">${esc10(def?.title || key2)}</label>${options ? `<select id="v-${key2}" data-field="${key2}"><option value="">\u041D\u0435 \u0432\u044B\u0431\u0440\u0430\u043D\u043E</option>${options.map((v) => `<option${v === value ? " selected" : ""}>${esc10(v)}</option>`).join("")}</select>` : key2.includes("comment") ? `<textarea id="v-${key2}" data-field="${key2}">${esc10(value)}</textarea>` : `<input id="v-${key2}" data-field="${key2}" type="${def?.type === "date" ? "date" : "text"}" value="${esc10(value)}">`}<label class="check"><input type="checkbox" data-clear="${key2}">\u041E\u0447\u0438\u0441\u0442\u0438\u0442\u044C \u043F\u043E\u043B\u0435</label></div>`;
    }).join("");
    dialog.innerHTML = `<form id="action-form"><h2>${esc10(labels[kind2])}</h2><div class="contracts">${card.contracts.map((c, i) => `<label class="check"><input type="${singular ? "radio" : "checkbox"}" name="contracts" value="${c.contract_uuid}"${!singular || i === 0 ? " checked" : ""}>${esc10(c.external_id)} \xB7 ${esc10(c.creditor_id)}</label>`).join("")}</div><div class="grid">${controls}${["note", "task"].includes(target) ? `<div class="field span"><label>${target === "task" ? "\u0417\u0430\u0434\u0430\u0447\u0430" : "\u0417\u0430\u043C\u0435\u0442\u043A\u0430"}</label><textarea id="free-text">${esc10(saved?.text || original?.payload?.text || "")}</textarea></div>` : ""}${["promise", "reported_payment"].includes(target) ? `<div class="field"><label>\u0421\u0443\u043C\u043C\u0430</label><input id="money" inputmode="decimal" value="${esc10(saved?.amount || "")}"></div><div class="field"><label>\u0412\u0430\u043B\u044E\u0442\u0430</label><select id="currency"><option>RUB</option><option>TJS</option><option>UZS</option><option>USD</option></select></div>` : ""}${["promise", "task"].includes(target) ? `<div class="field"><label>\u0421\u0440\u043E\u043A \xB7 ${esc10(Intl.DateTimeFormat().resolvedOptions().timeZone)}</label><input id="due" type="datetime-local" value="${esc10(saved?.due || "")}"></div>` : ""}${kind2 === "correction" ? '<div class="field span"><label>\u041F\u0440\u0438\u0447\u0438\u043D\u0430 \u0438\u0441\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0438\u044F</label><textarea id="reason" required></textarea></div>' : ""}</div><div class="error" id="form-error"></div><div class="actions">${button("cancel", "\u0417\u0430\u043A\u0440\u044B\u0442\u044C")}${kind2 === "call" ? button("show-script", "\u0421\u043A\u0440\u0438\u043F\u0442") : ""}<button type="submit" class="primary">\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435</button></div><div id="script-inline"></div></form>`;
    const selectedIds = saved?.contract_ids ?? original?.contract_ids;
    const permitted = new Set(card.contracts.filter((entry) => contractCan(entry, target) && (kind2 !== "correction" || contractCan(entry, "correct"))).map((entry) => entry.contract_uuid));
    let needsContractSelection = Array.isArray(selectedIds) && selectedIds.some((id) => !permitted.has(id));
    if (target === "task") {
      const staff2 = (context.staff?.length ? context.staff : [{
        employee_uuid: context.employee_uuid,
        name: "\u042F",
        work_kinds: ["call", "visit", "delivery"].filter((key2) => can(key2))
      }]).filter((person) => person.work_kinds?.length);
      dialog.querySelector(".grid").insertAdjacentHTML("beforeend", `<div class="field"><label for="task-assignee">\u0418\u0441\u043F\u043E\u043B\u043D\u0438\u0442\u0435\u043B\u044C</label><select id="task-assignee">${staff2.map((person) => `<option value="${esc10(person.employee_uuid)}">${esc10(person.name)}</option>`).join("")}</select></div><div class="field"><label for="task-work">\u0412\u0438\u0434 \u0440\u0430\u0431\u043E\u0442\u044B</label><select id="task-work"></select></div>`);
      const assignee = $2("task-assignee"), work = $2("task-work");
      assignee.value = saved?.assignee_uuid ?? original?.payload?.assignee_uuid ?? (newTaskAssignee || context.employee_uuid);
      newTaskAssignee = "";
      const fillWork = () => {
        const person = staff2.find((entry) => entry.employee_uuid === assignee.value);
        work.innerHTML = (person?.work_kinds || ["call", "visit"]).map((key2) => `<option value="${esc10(key2)}">${esc10(key2 === "delivery" ? "\u041F\u0435\u0440\u0435\u0434\u0430\u0442\u044C \u043A\u043E\u043D\u0432\u0435\u0440\u0442" : labels[key2])}</option>`).join("");
      };
      fillWork();
      work.value = saved?.work_kind ?? original?.payload?.work_kind ?? work.value;
      assignee.addEventListener("change", () => {
        fillWork();
        void formAPI.draft(draftKey, capture());
      });
      if (!saved?.due && !original) {
        const today2 = /* @__PURE__ */ new Date();
        $2("due").value = `${today2.getFullYear()}-${String(today2.getMonth() + 1).padStart(2, "0")}-${String(today2.getDate()).padStart(2, "0")}T23:59`;
      }
    }
    const firstPermitted = card.contracts.find((entry) => permitted.has(entry.contract_uuid))?.contract_uuid;
    dialog.querySelectorAll("input[name=contracts]").forEach((el) => {
      el.checked = permitted.has(el.value) && (Array.isArray(selectedIds) ? selectedIds.includes(el.value) : !singular || el.value === firstPermitted);
      el.disabled = kind2 === "correction" || !permitted.has(el.value);
      el.addEventListener("input", () => {
        needsContractSelection = false;
      });
    });
    if (needsContractSelection) $2("form-error").textContent = "\u041F\u0440\u0430\u0432\u0430 \u043F\u043E \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u0443 \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u0438\u0441\u044C. \u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B\u0439 \u0434\u043E\u0433\u043E\u0432\u043E\u0440; \u043F\u0440\u0435\u0436\u043D\u0438\u0439 \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D.";
    dialog.querySelectorAll("[data-clear]").forEach((el) => {
      el.checked = (saved?.clear ?? original?.payload?.clear ?? []).includes(el.dataset.clear);
    });
    const freeText = $2("free-text");
    if (freeText) freeText.value = saved?.text ?? original?.payload?.[target === "task" ? "title" : "text"] ?? "";
    const currencyInput = $2("currency");
    if (currencyInput) {
      const selected2 = dialog.querySelector("input[name=contracts]:checked")?.value;
      const currentCurrency = saved?.currency ?? original?.payload?.currency ?? card.contracts.find((c) => c.contract_uuid === selected2)?.currency ?? "";
      if (!Array.from(currencyInput.options).some((o) => o.value === currentCurrency))
        currencyInput.add(new Option(currentCurrency || "\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0432\u0430\u043B\u044E\u0442\u0443", currentCurrency));
      currencyInput.value = currentCurrency;
    }
    const reasonInput = $2("reason");
    if (reasonInput) reasonInput.value = saved?.reason ?? "";
    const capture = () => ({
      contract_ids: needsContractSelection ? selectedIds : Array.from(dialog.querySelectorAll("input[name=contracts]:checked")).map((el) => el.value),
      clear: Array.from(dialog.querySelectorAll("[data-clear]:checked")).map((el) => el.dataset.clear),
      values: Object.fromEntries(
        keys.map((k) => [k, $2("v-" + k).value])
      ),
      text: $2("free-text")?.value,
      amount: $2("money")?.value,
      due: $2("due")?.value,
      currency: currencyInput?.value,
      reason: reasonInput?.value,
      script_snapshot: callScript,
      script_shown: scriptShown,
      assignee_uuid: $2("task-assignee")?.value,
      work_kind: $2("task-work")?.value
    });
    if (kind2 === "call" && callScript) await formAPI.draft(draftKey, capture());
    dialog.querySelectorAll("input,select,textarea").forEach(
      (el) => el.addEventListener("input", () => {
        void formAPI.draft(draftKey, capture());
      })
    );
    $2("cancel").onclick = (e) => {
      e.preventDefault();
      void formAPI.draft(draftKey, capture());
      dialog.close();
    };
    if (kind2 === "call")
      $2("show-script").onclick = async (e) => {
        e.preventDefault();
        if (!callScript) {
          $2("script-inline").textContent = scriptError || "\u0414\u043B\u044F \u0438\u0441\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0438\u044F \u0441\u043E\u0431\u044B\u0442\u0438\u044F \u043F\u043E\u0434\u0441\u043A\u0430\u0437\u043A\u0430 \u043D\u043E\u0432\u043E\u0433\u043E \u0437\u0432\u043E\u043D\u043A\u0430 \u043D\u0435 \u043E\u0442\u043A\u0440\u044B\u0432\u0430\u0435\u0442\u0441\u044F.";
          return;
        }
        $2("script-inline").innerHTML = scriptMarkup(callScript);
        scriptShown = !!callScript.script;
        await formAPI.draft(draftKey, capture());
        telemetry("script_shown", "script");
      };
    $2("action-form").dataset.work = target;
    $2("action-form").onsubmit = async (e) => {
      e.preventDefault();
      try {
        if (needsContractSelection) throw new Error("\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B\u0439 \u0434\u043E\u0433\u043E\u0432\u043E\u0440 \u043F\u043E\u0441\u043B\u0435 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u044F \u043F\u0440\u0430\u0432.");
        const contract_ids = Array.from(
          dialog.querySelectorAll(
            "input[name=contracts]:checked"
          )
        ).map((el) => el.value);
        if (!contract_ids.length) throw new Error("\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u043E\u0433\u043E\u0432\u043E\u0440.");
        if (contract_ids.some((id) => !contractCan(card.contracts.find((r) => r.contract_uuid === id) || { allowed_actions: [] }, target)))
          throw new Error("\u0418\u0437\u043C\u0435\u043D\u0438\u043B\u043E\u0441\u044C \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0435 \u044D\u0442\u043E\u0439 \u0440\u0430\u0431\u043E\u0442\u044B. \u0412\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D \u0443 \u0438\u0441\u0445\u043E\u0434\u043D\u043E\u0439 \u043B\u0438\u0447\u043D\u043E\u0441\u0442\u0438.");
        const values = {}, clear = [];
        for (const key2 of keys) {
          const value = $2("v-" + key2).value;
          if (dialog.querySelector(`[data-clear="${key2}"]`).checked) {
            values[key2] = null;
            clear.push(key2);
          } else if (value !== "") values[key2] = value;
        }
        const payload = { values, clear };
        if (kind2 === "call" && scriptShown && callScript?.script_ref)
          payload.script_ref = callScript.script_ref;
        if (target === "note")
          payload.text = $2("free-text").value;
        if (target === "task") {
          payload.title = $2("free-text").value;
          payload.assignee_uuid = $2("task-assignee").value;
          payload.work_kind = $2("task-work").value;
        }
        if (["promise", "reported_payment"].includes(target)) {
          payload.amount = $2("money").value.replace(",", ".");
          payload.currency = $2("currency").value;
          if (!payload.currency) throw new Error("\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0432\u0430\u043B\u044E\u0442\u0443.");
        }
        if (["promise", "task"].includes(target)) {
          const dateValue = $2("due").value;
          if (!dateValue) throw new Error("\u0423\u043A\u0430\u0436\u0438\u0442\u0435 \u0441\u0440\u043E\u043A.");
          payload.due_at = new Date(dateValue).toISOString();
        }
        if (kind2 === "correction") payload.target_kind = target;
        const raw = {
          kind: kind2,
          contract_ids,
          payload,
          base_revisions: Object.fromEntries(
            formRevisions
          )
        };
        if (kind2 === "correction") {
          raw.correction_of = original.event_id;
          raw.reason = $2("reason").value;
        }
        const existing = (await formAPI.draft(draftKey + ":pending")).data;
        const content = JSON.stringify(raw);
        const operation_id = existing?.content === content ? existing.operation_id : crypto.randomUUID();
        await formAPI.draft(draftKey + ":pending", { content, operation_id });
        const result2 = await formAPI.command({
          schema_version: 1,
          operation_id,
          ...raw
        });
        if (!result2.ok) throw new Error(result2.text);
        await formAPI.draft(draftKey, null);
        await formAPI.draft(draftKey + ":pending", null);
        dialog.close();
        telemetry("command_queued", target, { operation_id });
        if (target === "task") await go2("tasks");
        notice(`\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E ${savedOn}.`, "status");
        scheduleSync();
      } catch (error) {
        $2("form-error").textContent = error.message;
      }
    };
    if (viewGeneration === renderGeneration) dialog.showModal();
  }
  async function documentForm() {
    const expectedOwner = state.owner;
    const dialog = $2("dialog");
    dialog.innerHTML = `<h2>\u0414\u043E\u043A\u0443\u043C\u0435\u043D\u0442</h2><div class="field"><label>\u0414\u043E\u0433\u043E\u0432\u043E\u0440</label><select id="document-contract">${card.contracts.map((c) => `<option value="${c.contract_uuid}">${esc10(c.external_id)} \xB7 ${esc10(c.creditor_id)}</option>`).join("")}</select></div><div class="actions">${button("document-close", "\u0417\u0430\u043A\u0440\u044B\u0442\u044C")}${button("document-pick", "\u0412\u044B\u0431\u0440\u0430\u0442\u044C \u0444\u0430\u0439\u043B", true)}</div>`;
    dialog.querySelectorAll("#document-contract option").forEach((option) => {
      if (!card.contracts.some((entry) => entry.contract_uuid === option.value && contractCan(entry, "document"))) option.remove();
    });
    bind("document-close", () => dialog.close());
    bind("document-pick", async () => {
      const id = $2("document-contract").value;
      const result2 = await crm.document(id, void 0, expectedOwner);
      if (!result2.ok) {
        if (!result2.cancelled) throw new Error(result2.text);
        return;
      }
      await submitCommand({
        kind: "document",
        contract_ids: [id],
        payload: {
          document_id: result2.data.document_id,
          sha256: result2.data.sha256,
          filename: result2.data.filename
        }
      }, expectedOwner);
      dialog.close();
    });
    dialog.showModal();
  }
  async function queuePage(host = document.getElementById("view")) {
    const $3 = (id) => id === "view" ? host : host.querySelector("#" + id);
    const result2 = await crm.queue();
    if (!result2.ok) throw new Error(result2.text);
    const entries = result2.data.entries;
    const pending = entries.filter((e) => e.state !== "accepted" && !e.reviewed_at);
    const exportErrors = (result2.data.exports || []).filter(
      (v) => !v.completed
    );
    $3("view").innerHTML = `<div class="toolbar">${button("queue-refresh", "\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C")}${button("queue-send", "\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C", true)}</div><div class="muted">\u041E\u0436\u0438\u0434\u0430\u044E\u0442 \u0440\u0435\u0448\u0435\u043D\u0438\u044F \u0438\u043B\u0438 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438: ${pending.length} \xB7 \u041D\u0435\u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043D\u043D\u044B\u0445 \u0432\u044B\u0433\u0440\u0443\u0437\u043E\u043A: ${exportErrors.length} \xB7 \u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u043E \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C: ${entries.filter((e) => e.state === "accepted").length}</div>` + table(
      ["\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435", "\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E", "\u0421\u043E\u0441\u0442\u043E\u044F\u043D\u0438\u0435", "\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442"],
      entries.slice().reverse().map(
        (e) => `<tr><td>${esc10({ receipt: "\u041A\u0432\u0438\u0442\u0430\u043D\u0446\u0438\u044F \u0440\u0435\u0435\u0441\u0442\u0440\u0430", "registry-edit": "\u041F\u0440\u0430\u0432\u043A\u0430 \u0440\u0435\u0435\u0441\u0442\u0440\u0430", "active-time": e.body.kind === "idle" ? "\u041F\u0440\u043E\u0441\u0442\u043E\u0439 \u0432 CRM" : "\u0410\u043A\u0442\u0438\u0432\u043D\u043E\u0435 \u0432\u0440\u0435\u043C\u044F" }[e.action] || labels[e.body.kind] || e.body.kind)}</td><td>${date(e.queued_at)}</td><td>${e.reviewed_at ? "\u0412\u043E\u0437\u0432\u0440\u0430\u0449\u0435\u043D\u043E \u0432 \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A" : e.state === "accepted" ? "\u041F\u0440\u0438\u043D\u044F\u0442\u043E \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C" : e.state === "attention" ? "\u0422\u0440\u0435\u0431\u0443\u0435\u0442 \u0440\u0435\u0448\u0435\u043D\u0438\u044F" : "\u041E\u0436\u0438\u0434\u0430\u0435\u0442 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438"}</td><td>${esc10(e.error || (e.result?.projection_status === "needs_review" ? "\u0418\u0441\u0442\u043E\u0440\u0438\u044F \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430; \u0442\u0435\u043A\u0443\u0449\u0438\u0435 \u043F\u043E\u043B\u044F \u0442\u0440\u0435\u0431\u0443\u044E\u0442 \u0441\u0432\u0435\u0440\u043A\u0438" : e.state === "accepted" ? "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u043E \u0442\u043E\u0447\u043D\u044B\u043C ID \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438" : ""))}${e.state === "attention" && !e.reviewed_at ? e.action === "registry-edit" ? "<br>\u0421\u0432\u0435\u0440\u044C\u0442\u0435 \u043F\u0430\u043A\u0435\u0442 \u0432 \u0440\u0430\u0437\u0434\u0435\u043B\u0435 \xAB\u0420\u0435\u0435\u0441\u0442\u0440\xBB." : `<br><button class="link" data-retry="${e.body.operation_id || e.body.receipt_id}">\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C \u0438\u0441\u0445\u043E\u0434\u043D\u044B\u0439 \u0437\u0430\u043F\u0440\u043E\u0441</button>` : ""}</td></tr>`
      )
    );
    $3("view").insertAdjacentHTML(
      "beforeend",
      exportErrors.map(
        (v) => `<article class="record"><h3>\u0412\u044B\u0433\u0440\u0443\u0437\u043A\u0430 \u0442\u0440\u0435\u0431\u0443\u0435\u0442 \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0438</h3><p>${esc10(v.path)}</p><p>${esc10(v.error || "\u0417\u0430\u043F\u0438\u0441\u044C \u0444\u0430\u0439\u043B\u0430 \u0435\u0449\u0451 \u043D\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0430.")}</p>${crm.platform === "mobile" && v.operation_id ? `<button data-export-retry="${esc10(v.operation_id)}">\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0438 \u0441\u043A\u0430\u0447\u0430\u0442\u044C \u0444\u0430\u0439\u043B</button>` : ""}</article>`
      ).join("")
    );
    host.querySelectorAll("[data-export-retry]").forEach((b) => b.onclick = () => {
      b.disabled = true;
      void crm.workbook("resume", { operation_id: b.dataset.exportRetry }).then(() => queuePage(host)).catch((e) => {
        notice(e.message);
        b.disabled = false;
      });
    });
    bind("queue-refresh", () => queuePage(host));
    bind("queue-send", async () => {
      await crm.queue("flush");
      await queuePage(host);
    });
    document.querySelectorAll("[data-retry]").forEach(
      (b) => b.onclick = async () => {
        await crm.queue("retry", b.dataset.retry);
        await queuePage(host);
      }
    );
  }
  async function tasksPage() {
    const generation = ++taskGeneration;
    const [data2, queue2] = await Promise.all([request2("GET", "tasks", void 0, {
      status: taskStatus,
      employee_uuid: taskEmployee,
      work: taskWork,
      search: taskSearch,
      offset: taskOffset,
      limit: 100
    }), crm.queue()]);
    if (generation !== taskGeneration || section !== "tasks") return;
    const entries = queue2.ok ? queue2.data?.entries || [] : [];
    const pending = entries.filter((e) => ["task", "task_complete"].includes(e.body?.kind) && e.state !== "accepted");
    const team = ["manager", "owner", "analyst", "observer"].includes(context.role);
    const filters = [["open", "\u041E\u0442\u043A\u0440\u044B\u0442\u044B\u0435"], ["today", "\u041D\u0430 \u0441\u0435\u0433\u043E\u0434\u043D\u044F"], ["overdue", "\u041F\u0440\u043E\u0441\u0440\u043E\u0447\u0435\u043D\u043D\u044B\u0435"], ["done", "\u0417\u0430\u0432\u0435\u0440\u0448\u0451\u043D\u043D\u044B\u0435"], ["all", "\u0412\u0441\u0435"]];
    const due = (value) => value ? new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short", timeZone: data2.timezone }).format(new Date(value)) : "\u0421\u0440\u043E\u043A \u043D\u0435 \u0443\u043A\u0430\u0437\u0430\u043D";
    $2("title").textContent = "\u0417\u0430\u0434\u0430\u0447\u0438";
    $2("view").innerHTML = `<div class="toolbar"><input id="task-search" type="search" placeholder="\u041A\u043B\u0438\u0435\u043D\u0442, \u0434\u043E\u0433\u043E\u0432\u043E\u0440 \u0438\u043B\u0438 \u0437\u0430\u0434\u0430\u0447\u0430" aria-label="\u041F\u043E\u0438\u0441\u043A \u0437\u0430\u0434\u0430\u0447" value="${esc10(taskSearch)}">${team ? `<label>\u0418\u0441\u043F\u043E\u043B\u043D\u0438\u0442\u0435\u043B\u044C <select id="task-employee"><option value="all">\u0412\u0441\u044F \u043A\u043E\u043C\u0430\u043D\u0434\u0430</option>${(context.staff || []).map((p) => `<option value="${esc10(p.employee_uuid)}"${p.employee_uuid === taskEmployee ? " selected" : ""}>${esc10(p.name)}</option>`).join("")}</select></label>` : ""}<label>\u0420\u0430\u0431\u043E\u0442\u0430 <select id="task-work">${[["all", "\u041B\u044E\u0431\u0430\u044F"], ["call", "\u0417\u0432\u043E\u043D\u043E\u043A"], ["visit", "\u0412\u044B\u0435\u0437\u0434"], ["delivery", "\u041F\u0435\u0440\u0435\u0434\u0430\u0442\u044C \u043A\u043E\u043D\u0432\u0435\u0440\u0442"]].map(([id, label]) => `<option value="${id}"${id === taskWork ? " selected" : ""}>${label}</option>`).join("")}</select></label>${button("task-refresh", "\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C")}${can("task") && context.role !== "courier" ? button("task-create", team ? "\u041D\u0430\u0437\u043D\u0430\u0447\u0438\u0442\u044C \u0437\u0430\u0434\u0430\u0447\u0443" : "\u041D\u0430\u043F\u043E\u043C\u043D\u0438\u0442\u044C", true) : ""}</div><div class="work-filters">${filters.map(([id, label]) => `<button data-task-filter="${id}" class="${id === taskStatus ? "active" : ""}">${label} <span>${data2.counts[id] || 0}</span></button>`).join("")}</div><div id="task-send-state">${!queue2.ok ? '<p role="alert">\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u0440\u043E\u0447\u0438\u0442\u0430\u0442\u044C \u043E\u0447\u0435\u0440\u0435\u0434\u044C. \u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u0438\u0435 \u0437\u0430\u0434\u0430\u0447 \u0432\u0440\u0435\u043C\u0435\u043D\u043D\u043E \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u043E.</p>' : pending.length ? `<div class="task-pending"><strong>\u041E\u0436\u0438\u0434\u0430\u044E\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F: ${pending.length}</strong>${pending.map((e) => `<div>${esc10(e.body.kind === "task" ? e.body.payload?.title : "\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u0438\u0435 \u0437\u0430\u0434\u0430\u0447\u0438")} \xB7 ${e.state === "attention" ? "\u0422\u0440\u0435\u0431\u0443\u0435\u0442 \u0441\u0432\u0435\u0440\u043A\u0438" : "\u041E\u0436\u0438\u0434\u0430\u0435\u0442 \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0438"}</div>`).join("")}<button id="tasks-queue" class="link">\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u043E\u0447\u0435\u0440\u0435\u0434\u044C</button></div>` : ""}</div><p class="muted">${data2.total} \u0437\u0430\u0434\u0430\u0447 \xB7 \u0441\u0440\u043E\u043A\u0438 \u043F\u043E ${esc10(data2.timezone)}</p>${data2.tasks.length ? table(["\u041A\u043B\u0438\u0435\u043D\u0442 / \u0434\u043E\u0433\u043E\u0432\u043E\u0440", "\u0417\u0430\u0434\u0430\u0447\u0430", "\u0418\u0441\u043F\u043E\u043B\u043D\u0438\u0442\u0435\u043B\u044C", "\u0421\u0440\u043E\u043A", "\u0421\u043E\u0441\u0442\u043E\u044F\u043D\u0438\u0435"], data2.tasks.map((row) => {
      const queued = entries.slice().reverse().find((e) => e.body?.kind === "task_complete" && e.body.payload?.entity_id === row.entity_id);
      const sending = row.status === "open" && queued;
      const status2 = row.status === "done" ? "\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u0430" : sending ? queued.state === "attention" ? "\u0422\u0440\u0435\u0431\u0443\u0435\u0442 \u0441\u0432\u0435\u0440\u043A\u0438" : "\u041E\u0436\u0438\u0434\u0430\u0435\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F" : row.overdue ? "\u041F\u0440\u043E\u0441\u0440\u043E\u0447\u0435\u043D\u0430" : row.due_today ? "\u041D\u0430 \u0441\u0435\u0433\u043E\u0434\u043D\u044F" : "\u041E\u0442\u043A\u0440\u044B\u0442\u0430";
      return `<tr data-task-row="${esc10(row.entity_id)}"><td><button class="link" data-task-person="${esc10(row.person_uuid)}">${esc10(row.client)}</button><br>${esc10(row.external_id)}<br><span class="muted">${esc10(row.creditor)}</span></td><td>${esc10(row.title)}<br><span class="muted">${esc10(labels[row.work_kind] || row.work_kind)}</span></td><td>${esc10(row.assignee)}</td><td>${esc10(due(row.due_at))}</td><td>${status2}${row.can_complete && !sending && queue2.ok ? `<br><button class="link" data-task-complete="${esc10(row.entity_id)}">\u0412\u044B\u043F\u043E\u043B\u043D\u0435\u043D\u043E</button>` : ""}</td></tr>`;
    })) : '<div class="empty">\u0417\u0430\u0434\u0430\u0447 \u043F\u043E \u0432\u044B\u0431\u0440\u0430\u043D\u043D\u044B\u043C \u0443\u0441\u043B\u043E\u0432\u0438\u044F\u043C \u043D\u0435\u0442</div>'}<div class="toolbar portfolio-pages">${button("task-prev", "\u041D\u0430\u0437\u0430\u0434")}${button("task-next", "\u0414\u0430\u043B\u0435\u0435")}</div>`;
    const persist = () => crm.draft("task-view", { status: taskStatus, employee: taskEmployee, work: taskWork });
    const refresh2 = async () => {
      taskSearch = $2("task-search").value;
      taskOffset = 0;
      await persist();
      await tasksPage();
    };
    bind("task-refresh", refresh2);
    $2("task-search").addEventListener("keydown", (event) => {
      if (event.key === "Enter") void refresh2().catch((e) => notice(e.message));
    });
    const select = async () => {
      taskEmployee = $2("task-employee")?.value || context.employee_uuid;
      taskWork = $2("task-work").value;
      await refresh2();
    };
    $2("task-employee")?.addEventListener("change", () => void select().catch((e) => notice(e.message)));
    $2("task-work").addEventListener("change", () => void select().catch((e) => notice(e.message)));
    document.querySelectorAll("[data-task-filter]").forEach((b) => b.onclick = () => {
      taskStatus = b.dataset.taskFilter;
      void refresh2().catch((e) => notice(e.message));
    });
    document.querySelectorAll("[data-task-person]").forEach((b) => b.onclick = () => void openCard(b.dataset.taskPerson));
    document.querySelectorAll("[data-task-complete]").forEach((b) => b.onclick = async () => {
      b.disabled = true;
      const row = data2.tasks.find((r) => r.entity_id === b.dataset.taskComplete);
      try {
        await submitCommand({ kind: "task_complete", contract_ids: [row.contract_uuid], payload: { entity_id: row.entity_id, entity_revision: row.revision } });
        await tasksPage();
        notice("\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u0438\u0435 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u043E. \u041E\u0436\u0438\u0434\u0430\u0435\u0442\u0441\u044F \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435 \u0441\u0435\u0440\u0432\u0435\u0440\u0430.", "status");
      } catch (error) {
        notice(error.message);
        b.disabled = false;
      }
    });
    bind("tasks-queue", () => go2("queue"));
    bind("task-create", async () => {
      const selected2 = taskEmployee;
      await go2("portfolio");
      newTaskAssignee = selected2 === "all" ? "" : selected2;
      taskSelectClient = true;
      notice("\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u043A\u043B\u0438\u0435\u043D\u0442\u0430 \u0434\u043B\u044F \u0437\u0430\u0434\u0430\u0447\u0438.");
    });
    $2("task-prev").disabled = taskOffset === 0;
    $2("task-next").disabled = !data2.has_more;
    bind("task-prev", async () => {
      taskOffset = Math.max(0, taskOffset - 100);
      await tasksPage();
    });
    bind("task-next", async () => {
      taskOffset += 100;
      await tasksPage();
    });
  }
  async function cabinet() {
    const month = (/* @__PURE__ */ new Date()).toLocaleDateString("sv-SE").slice(0, 7);
    const staff2 = context.staff || [];
    $2("view").innerHTML = `<div class="toolbar"><label>\u041C\u0435\u0441\u044F\u0446 <input id="cabinet-month" type="month" value="${month}"></label>${staff2.length > 1 ? `<label>\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A <select id="cabinet-employee">${staff2.map((person) => `<option value="${esc10(person.employee_uuid)}"${person.employee_uuid === context.employee_uuid ? " selected" : ""}>${esc10(person.name)}</option>`).join("")}</select></label>` : ""}${button("cabinet-refresh", "\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u044C")}</div><div id="cabinet-result"></div>`;
    const show = async () => {
      const data2 = await request2("GET", "cabinet", void 0, {
        month: $2("cabinet-month").value,
        employee_uuid: $2("cabinet-employee")?.value || context.employee_uuid
      });
      const stats = data2.stats, payments = data2.payments;
      $2("cabinet-result").innerHTML = `<h2>${esc10(data2.employee)}</h2><div class="summary">${[["\u0417\u0432\u043E\u043D\u043A\u0438", stats.call || 0], ["\u0412\u044B\u0435\u0437\u0434\u044B", stats.visit || 0], ["\u041F\u043E\u043F\u044B\u0442\u043A\u0438 \u043F\u0435\u0440\u0435\u0434\u0430\u0447\u0438", stats.delivery || 0], ["\u041E\u0431\u0435\u0449\u0430\u043D\u0438\u044F", stats.promise || 0], ["\u0417\u0430\u0434\u0430\u0447\u0438 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u044B", stats.task_complete || 0], ["\u041E\u0431\u0440\u0430\u0431\u043E\u0442\u0430\u043D\u043E \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432", stats.processed_contracts], ["\u0414\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432 \u0432 \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u0435", stats.portfolio_contracts]].map(([title, value]) => `<div class="metric">${esc10(title)}<strong>${esc10(value)}</strong></div>`).join("")}</div><h2>\u041E\u043F\u043B\u0430\u0442\u044B \u0437\u0430 \u043C\u0435\u0441\u044F\u0446 \u043F\u043E \u0440\u0435\u0435\u0441\u0442\u0440\u0443</h2><p class="muted">\u0422\u0435\u043A\u0443\u0449\u0438\u0439 \u043F\u043E\u0441\u0442\u043E\u044F\u043D\u043D\u044B\u0439 \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044C \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430. \u0414\u0430\u043D\u043D\u044B\u0435 \u043D\u0430 ${payments.source_as_of.length ? payments.source_as_of.map(date).join(", ") : "\u2014"}. \u0414\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432 \u0441 \u043C\u0435\u0441\u044F\u0447\u043D\u043E\u0439 \u0441\u0443\u043C\u043C\u043E\u0439: ${payments.known_contracts}; \u0431\u0435\u0437 \u0434\u0430\u043D\u043D\u044B\u0445: ${payments.missing_contracts}.</p>${payments.totals.length ? table(["\u0412\u0430\u043B\u044E\u0442\u0430", "\u0421\u0443\u043C\u043C\u0430"], payments.totals.map((row) => `<tr><td>${esc10(row.currency || "\u0412\u0430\u043B\u044E\u0442\u0430 \u043D\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0430: " + row.unit.replace("unconfirmed:registry-partner:", "").replace("unconfirmed:", ""))}</td><td>${amount(row.amount, row.currency || "")}</td></tr>`)) : '<div class="empty">\u041C\u0435\u0441\u044F\u0447\u043D\u044B\u0435 \u0441\u0443\u043C\u043C\u044B \u0432 \u0438\u0441\u0442\u043E\u0447\u043D\u0438\u043A\u0435 \u043E\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u044E\u0442</div>'}${table(["\u041A\u043B\u0438\u0435\u043D\u0442", "\u0414\u043E\u0433\u043E\u0432\u043E\u0440", "\u041A\u0440\u0435\u0434\u0438\u0442\u043E\u0440", "\u0421\u0443\u043C\u043C\u0430"], payments.rows.map((row) => `<tr><td><button class="link" data-cabinet-person="${esc10(row.person_uuid)}">${esc10(row.client)}</button></td><td>${esc10(row.external_id)}</td><td>${esc10(row.creditor)}</td><td>${amount(row.amount, row.currency || "\u0412\u0430\u043B\u044E\u0442\u0430 \u043D\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0430")}</td></tr>`))}<h2>\u041C\u043E\u0438 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438 \u0437\u0430 \u043C\u0435\u0441\u044F\u0446</h2><p class="muted">\u0410\u0432\u0442\u043E\u0440\u0441\u0442\u0432\u043E \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0435\u0442\u0441\u044F \u043F\u0440\u0438 \u043F\u0435\u0440\u0435\u0434\u0430\u0447\u0435 \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u0430.${data2.history_without_time ? " \u0418\u0441\u0442\u043E\u0440\u0438\u0447\u0435\u0441\u043A\u0438\u0445 \u0437\u0430\u043F\u0438\u0441\u0435\u0439 \u0431\u0435\u0437 \u0442\u043E\u0447\u043D\u043E\u0433\u043E \u0432\u0440\u0435\u043C\u0435\u043D\u0438: " + data2.history_without_time + "." : ""}${data2.operations_truncated ? " \u041F\u043E\u043A\u0430\u0437\u0430\u043D\u044B \u043F\u043E\u0441\u043B\u0435\u0434\u043D\u0438\u0435 500 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0439." : ""}</p>${table(["\u0412\u0440\u0435\u043C\u044F", "\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435", "\u0414\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432", "\u0421\u043E\u0434\u0435\u0440\u0436\u0430\u043D\u0438\u0435"], data2.operations.map((row) => `<tr><td>${row.workday ? calendarDate(row.workday) : date(row.occurred_at)}</td><td>${esc10(labels[row.kind] || row.kind)}</td><td>${row.contract_ids.length}</td><td>${esc10(row.text)}</td></tr>`))}`;
      document.querySelectorAll("[data-cabinet-person]").forEach((button2) => button2.onclick = () => void openCard(button2.dataset.cabinetPerson));
    };
    bind("cabinet-refresh", show);
    await show();
  }
  async function shift() {
    const data2 = await request2("GET", "shifts");
    const current3 = data2.shifts.find((s) => !s.ends_at);
    $2("view").innerHTML = `<div class="toolbar">${!current3 ? button("shift-start", "\u041D\u0430\u0447\u0430\u0442\u044C \u0441\u043C\u0435\u043D\u0443", true) : button("shift-pause", current3.state === "break" ? "\u041F\u0440\u043E\u0434\u043E\u043B\u0436\u0438\u0442\u044C" : "\u041F\u0435\u0440\u0435\u0440\u044B\u0432") + button("shift-finish", "\u0417\u0430\u0432\u0435\u0440\u0448\u0438\u0442\u044C \u0441\u043C\u0435\u043D\u0443", true)}</div><p class="muted">${esc10(data2.meaning)}</p>` + table(
      [
        "\u0414\u0430\u0442\u0430 \u0441\u043C\u0435\u043D\u044B",
        "\u041D\u0430\u0447\u0430\u043B\u043E",
        "\u041E\u043A\u043E\u043D\u0447\u0430\u043D\u0438\u0435",
        "\u041F\u0435\u0440\u0435\u0440\u044B\u0432\u044B",
        "\u0417\u0430\u044F\u0432\u043B\u0435\u043D\u043D\u0430\u044F \u0434\u043B\u0438\u0442\u0435\u043B\u044C\u043D\u043E\u0441\u0442\u044C"
      ],
      data2.shifts.map(
        (s) => `<tr><td>${esc10(s.workday)}</td><td>${date(s.starts_at)}</td><td>${s.unclosed ? "\u041D\u0435 \u0437\u0430\u043A\u0440\u044B\u0442\u0430" : date(s.ends_at)}</td><td>${Math.floor(s.break_seconds / 60)} \u043C\u0438\u043D.</td><td>${Math.floor(s.declared_seconds / 3600)} \u0447. ${Math.floor(s.declared_seconds / 60) % 60} \u043C\u0438\u043D.</td></tr>`
      )
    );
    const send = async (action3) => {
      await request2("POST", "shift", {
        operation_id: crypto.randomUUID(),
        action: action3,
        ...current3 ? { shift_id: current3.shift_id, revision: current3.revision } : {}
      });
      await shift();
    };
    bind("shift-start", () => send("start"));
    bind(
      "shift-pause",
      () => send(current3.state === "break" ? "resume" : "break")
    );
    bind("shift-finish", () => send("finish"));
    const today2 = (/* @__PURE__ */ new Date()).toLocaleDateString("sv-SE");
    $2("view").insertAdjacentHTML("beforeend", `<h2>\u041E\u0442\u0447\u0451\u0442 \u043F\u043E \u0441\u043C\u0435\u043D\u0430\u043C</h2><div class="toolbar"><label>\u0421 <input id="shift-since" type="date" value="${today2.slice(0, 8) + "01"}"></label><label>\u041F\u043E <input id="shift-until" type="date" value="${today2}"></label>${button("shift-report-show", "\u041F\u043E\u043A\u0430\u0437\u0430\u0442\u044C")}${button("shift-report-export", "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C Excel")}</div><div id="shift-report-result"></div>`);
    let report;
    const reportShow = async () => {
      report = await request2("GET", "shift-report", void 0, { since: $2("shift-since").value, until: $2("shift-until").value });
      $2("shift-report-result").innerHTML = `<p class="muted">${esc10(report.meaning)} \u0427\u0430\u0441\u043E\u0432\u043E\u0439 \u043F\u043E\u044F\u0441: ${esc10(report.timezone)}.</p>` + table(["\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A", "\u0421\u043C\u0435\u043D", "\u0420\u0430\u0431\u043E\u0442\u0430", "\u041F\u0435\u0440\u0435\u0440\u044B\u0432\u044B", "\u041D\u0435 \u0437\u0430\u043A\u0440\u044B\u0442\u043E"], report.employees.map((row) => `<tr><td>${esc10(row.employee)}</td><td>${row.shifts}</td><td>${Math.floor(row.declared_seconds / 3600)} \u0447. ${Math.floor(row.declared_seconds / 60) % 60} \u043C\u0438\u043D.</td><td>${Math.floor(row.break_seconds / 60)} \u043C\u0438\u043D.</td><td>${row.unclosed}</td></tr>`));
    };
    bind("shift-report-show", reportShow);
    bind("shift-report-export", async () => {
      await reportShow();
      const result2 = await crm.workbook("shift-report", { report });
      if (!result2.ok && !result2.cancelled) throw new Error(result2.text);
    });
    await reportShow();
  }
  function scriptMarkup(data2) {
    return data2.script ? `<h3>${esc10(data2.script.title)}</h3><p class="muted">\u0412\u0435\u0440\u0441\u0438\u044F ${data2.published_revision} \xB7 ${date(data2.published_at)}</p>${data2.script.steps.map((s, i) => `<article class="script-step"><h3>${i + 1}. ${esc10(s.title)}</h3><p>${esc10(s.text)}</p><button type="button" data-script-used>\u0428\u0430\u0433 \u0438\u0441\u043F\u043E\u043B\u044C\u0437\u043E\u0432\u0430\u043D</button></article>`).join("")}` : '<div class="empty">\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C \u0435\u0449\u0451 \u043D\u0435 \u043E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u043B \u0441\u043A\u0440\u0438\u043F\u0442</div>';
  }
  async function script(host = document.getElementById("view")) {
    const $3 = (id) => id === "view" ? host : host.querySelector("#" + id);
    const data2 = await request2("GET", "script");
    scriptOpened = data2;
    telemetry("script_shown", "script");
    $3("view").innerHTML = scriptMarkup(data2) + (data2.can_edit ? `<div class="actions">${button("script-edit", "\u0420\u0435\u0434\u0430\u043A\u0442\u0438\u0440\u043E\u0432\u0430\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A")}${button("script-publish", "\u041E\u043F\u0443\u0431\u043B\u0438\u043A\u043E\u0432\u0430\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A", true)}${button("script-withdraw", "\u0421\u043D\u044F\u0442\u044C \u043F\u0443\u0431\u043B\u0438\u043A\u0430\u0446\u0438\u044E")}</div>` : "");
    bind("script-publish", async () => {
      await request2("POST", "script", {
        operation_id: crypto.randomUUID(),
        action: "publish",
        base_revision: data2.revision
      });
      await script(host);
    });
    bind("script-withdraw", async () => {
      await request2("POST", "script", {
        operation_id: crypto.randomUUID(),
        action: "withdraw",
        base_revision: data2.revision
      });
      await script(host);
    });
    bind("script-edit", () => scriptEditor(data2, () => script(host)));
  }
  function scriptEditor(data2, refresh2 = () => script()) {
    const dialog = $2("dialog");
    const content = data2.draft || data2.script || { title: "", steps: [{ title: "", text: "" }] };
    dialog.innerHTML = `<form id="script-form"><h2>\u0427\u0435\u0440\u043D\u043E\u0432\u0438\u043A \u0441\u043A\u0440\u0438\u043F\u0442\u0430</h2><div class="field"><label>\u041D\u0430\u0437\u0432\u0430\u043D\u0438\u0435</label><input id="script-title" maxlength="100" value="${esc10(content.title)}"></div><div id="script-steps">${content.steps.map((s) => stepEditor(s)).join("")}</div>${button("script-add", "\u0414\u043E\u0431\u0430\u0432\u0438\u0442\u044C \u0448\u0430\u0433")}<div class="error" id="script-error"></div><div class="actions">${button("script-close", "\u0417\u0430\u043A\u0440\u044B\u0442\u044C")}<button class="primary" type="submit">\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A</button></div></form>`;
    $2("script-add").onclick = (e) => {
      e.preventDefault();
      if (dialog.querySelectorAll("[data-script-step]").length < 12)
        $2("script-steps").insertAdjacentHTML(
          "beforeend",
          stepEditor({ title: "", text: "" })
        );
    };
    $2("script-close").onclick = (e) => {
      e.preventDefault();
      dialog.close();
    };
    $2("script-form").onsubmit = async (e) => {
      e.preventDefault();
      try {
        const content2 = {
          title: $2("script-title").value,
          steps: Array.from(dialog.querySelectorAll("[data-script-step]")).map(
            (el) => ({
              title: el.querySelector("input").value,
              text: el.querySelector("textarea").value
            })
          )
        };
        await request2("POST", "script", {
          operation_id: crypto.randomUUID(),
          action: "save",
          base_revision: data2.revision,
          content: content2
        });
        dialog.close();
        await refresh2();
      } catch (error) {
        $2("script-error").textContent = error.message;
      }
    };
    dialog.showModal();
  }
  function stepEditor(s) {
    return `<div class="field script-step" data-script-step><label>\u0428\u0430\u0433</label><input maxlength="80" value="${esc10(s.title)}"><textarea maxlength="6000">${esc10(s.text)}</textarea></div>`;
  }
  async function exchangePage(host = document.getElementById("view")) {
    const $3 = (id) => id === "view" ? host : host.querySelector("#" + id);
    const expectedOwner = state.owner;
    const intent = () => crm.draft("workbook-import-intent", void 0, expectedOwner);
    const applySaved = async () => {
      const stored = await intent();
      const body = stored.data;
      if (!body) throw Error("\u041D\u0435\u0442 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u043E\u0439 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438 \u0441\u0432\u0435\u0440\u043A\u0438.");
      const result2 = await crm.api("POST", "import-apply", body);
      if (result2.ok) {
        await crm.draft("workbook-import-intent", null, expectedOwner);
        $3("import-recovery")?.replaceChildren();
        $3("exchange-result").innerHTML = `<article class="record"><h3>\u0418\u043C\u043F\u043E\u0440\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D</h3><p>\u0421\u0442\u0440\u043E\u043A: ${result2.data.changed_rows} \xB7 \u041F\u043E\u043B\u0435\u0439: ${result2.data.changed_cells}</p></article>`;
        return;
      }
      if ([400, 409, 422].includes(result2.status)) await crm.draft("workbook-import-intent", null, expectedOwner);
      else if ($3("import-recovery")) $3("import-recovery").innerHTML = `<p>\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u0441\u0432\u0435\u0440\u043A\u0438 \u0435\u0449\u0451 \u043D\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D. \u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D \u043F\u0440\u0435\u0436\u043D\u0438\u0439 \u043D\u043E\u043C\u0435\u0440 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438.</p>${button("check-import", "\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u0441\u0432\u0435\u0440\u043A\u0438")}`;
      bind("check-import", applySaved);
      throw Error(result2.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u0441\u0432\u0435\u0440\u043A\u0438.");
    };
    const period = (/* @__PURE__ */ new Date()).toISOString().slice(0, 7);
    const target = state.connection.shared ? '<select id="target"><option value="general:pilot">\u041E\u0431\u0449\u0438\u0439 \u0440\u0435\u0435\u0441\u0442\u0440 \u043F\u0438\u043B\u043E\u0442\u0430</option></select>' : `<input id="target" value="${state.connection.mode === "synthetic" ? "general:demo" : ""}" placeholder="\u0418\u0434\u0435\u043D\u0442\u0438\u0444\u0438\u043A\u0430\u0442\u043E\u0440 \u0441\u043E\u0433\u043B\u0430\u0441\u043E\u0432\u0430\u043D\u043D\u043E\u0433\u043E \u0440\u0435\u0435\u0441\u0442\u0440\u0430">`;
    $3("view").innerHTML = `<div id="import-recovery"></div><div class="grid"><div class="field"><label>\u041F\u0435\u0440\u0438\u043E\u0434</label><input id="period" type="month" value="${period}"></div><div class="field"><label>\u0420\u0435\u0435\u0441\u0442\u0440</label>${target}</div></div><div class="toolbar" style="margin-top:20px">${button("export-new", "\u0421\u043E\u0437\u0434\u0430\u0442\u044C \u0440\u0435\u0435\u0441\u0442\u0440", true)}${button("export-template", "\u0421\u043E\u0437\u0434\u0430\u0442\u044C \u043A\u043E\u043F\u0438\u044E \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u044E\u0449\u0435\u0439 \u043A\u043D\u0438\u0433\u0438")}${button("import-file", "\u0421\u0432\u0435\u0440\u0438\u0442\u044C \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u044F Excel")}</div><div id="exchange-result"></div><p class="muted">\u041F\u0443\u0441\u0442\u0430\u044F \u044F\u0447\u0435\u0439\u043A\u0430 \u043D\u0435 \u0441\u0442\u0438\u0440\u0430\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0435. \u0414\u043B\u044F \u044F\u0432\u043D\u043E\u0439 \u043E\u0447\u0438\u0441\u0442\u043A\u0438 \u0432\u0432\u0435\u0434\u0438\u0442\u0435 [[CLEAR]]. \u0418\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u043A\u043D\u0438\u0433\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0435\u0442\u0441\u044F.</p>`;
    if ((await intent()).data) {
      $3("import-recovery").innerHTML = `<p>\u0415\u0441\u0442\u044C \u0441\u0432\u0435\u0440\u043A\u0430 \u0431\u0435\u0437 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u043E\u0433\u043E \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u0430.</p>${button("check-import", "\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u0441\u0432\u0435\u0440\u043A\u0438")}`;
      bind("check-import", applySaved);
    }
    const write2 = async (use_template) => {
      const snapshot = await request2("POST", "export", {
        operation_id: crypto.randomUUID(),
        period: $3("period").value,
        target_id: $3("target").value
      });
      const result2 = await crm.workbook("write", { snapshot, use_template });
      if (!result2.ok) {
        if (!result2.cancelled) throw new Error(result2.text);
        return;
      }
      const receiptText = crm.platform === "mobile" ? "\u0424\u0430\u0439\u043B \u0441\u043A\u0430\u0447\u0430\u043D. \u0421\u0442\u0430\u0442\u0443\u0441 \u043A\u0432\u0438\u0442\u0430\u043D\u0446\u0438\u0438 \u0434\u043E\u0441\u0442\u0430\u0432\u043A\u0438 \u2014 \u0432 \u0440\u0430\u0437\u0434\u0435\u043B\u0435 \xAB\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0438\xBB." : "\u041A\u0432\u0438\u0442\u0430\u043D\u0446\u0438\u044F \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430 \u0432 \u0437\u0430\u0449\u0438\u0449\u0451\u043D\u043D\u043E\u0439 \u043E\u0447\u0435\u0440\u0435\u0434\u0438. \u0421\u0442\u0430\u0442\u0443\u0441 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F \u2014 \u0432 \u0440\u0430\u0437\u0434\u0435\u043B\u0435 \xAB\u041E\u0447\u0435\u0440\u0435\u0434\u044C\xBB.";
      $3("exchange-result").innerHTML = `<article class="record"><h3>\u041A\u043D\u0438\u0433\u0430 \u0437\u0430\u043F\u0438\u0441\u0430\u043D\u0430 \u0438 \u043F\u043E\u0432\u0442\u043E\u0440\u043D\u043E \u043F\u0440\u043E\u0447\u0438\u0442\u0430\u043D\u0430</h3><p>${esc10(result2.data.path)}</p><p>${esc10(receiptText)}</p></article>`;
    };
    bind("export-new", () => write2(false));
    bind("export-template", () => write2(true));
    bind("import-file", async () => {
      if ((await intent()).data) throw Error("\u0421\u043D\u0430\u0447\u0430\u043B\u0430 \u043F\u0440\u043E\u0432\u0435\u0440\u044C\u0442\u0435 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u043E\u0439 \u0441\u0432\u0435\u0440\u043A\u0438.");
      const selected2 = await crm.workbook("read");
      if (!selected2.ok) {
        if (!selected2.cancelled) throw new Error(selected2.text);
        return;
      }
      const { filename, ...input } = selected2.data;
      const plan = await request2("POST", "import-preview", input);
      $3("exchange-result").innerHTML = `<h2>\u0421\u0432\u0435\u0440\u043A\u0430: ${esc10(filename)}</h2><p class="muted">\u0418\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u0439: ${plan.changes.length} \xB7 \u041A\u043E\u043D\u0444\u043B\u0438\u043A\u0442\u043E\u0432: ${plan.conflicts} \xB7 \u041E\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0443\u044E\u0449\u0438\u0445 \u0441\u0442\u0440\u043E\u043A: ${plan.missing_rows} (\u0431\u0435\u0437 \u0441\u043D\u044F\u0442\u0438\u044F \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0439)</p>` + table(
        ["\u041F\u043E\u043B\u0435", "\u0418\u0441\u0445\u043E\u0434\u043D\u043E\u0435", "\u0421\u0435\u0439\u0447\u0430\u0441 \u043D\u0430 \u0441\u0435\u0440\u0432\u0435\u0440\u0435", "\u0418\u0437 Excel", "\u0420\u0435\u0448\u0435\u043D\u0438\u0435"],
        plan.changes.map(
          (c, i) => `<tr><td>${esc10(context.fields.find((f) => f.key === c.key)?.title || c.key)}</td><td>${esc10(c.baseline ?? "\u2014")}</td><td>${esc10(c.current ?? "\u2014")}</td><td>${esc10(c.incoming ?? "\u041E\u0447\u0438\u0441\u0442\u0438\u0442\u044C")}</td><td>${c.conflict ? `<select data-resolution="${i}"><option value="">\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435</option><option value="server">\u041E\u0441\u0442\u0430\u0432\u0438\u0442\u044C \u0441\u0435\u0440\u0432\u0435\u0440</option><option value="excel">\u041F\u0440\u0438\u043C\u0435\u043D\u0438\u0442\u044C Excel</option></select>` : "\u041F\u0440\u0438\u043C\u0435\u043D\u0438\u0442\u044C"}</td></tr>`
        )
      ) + `<div class="actions">${button("apply-import", "\u041F\u0440\u0438\u043C\u0435\u043D\u0438\u0442\u044C \u0441\u0432\u0435\u0440\u0435\u043D\u043D\u044B\u0435 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u044F", true)}</div>`;
      bind("apply-import", async () => {
        const applyButton = $3("apply-import");
        if (applyButton.disabled) return;
        applyButton.disabled = true;
        try {
          const resolutions = {};
          for (const el of Array.from(
            document.querySelectorAll("[data-resolution]")
          )) {
            if (!el.value) throw new Error("\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0440\u0435\u0448\u0435\u043D\u0438\u0435 \u043A\u0430\u0436\u0434\u043E\u0433\u043E \u043A\u043E\u043D\u0444\u043B\u0438\u043A\u0442\u0430.");
            const c = plan.changes[Number(el.dataset.resolution)];
            resolutions[c.contract_uuid + ":" + c.key] = el.value;
          }
          const saved = {
            operation_id: crypto.randomUUID(),
            preview_id: plan.preview_id,
            file_sha256: input.file_sha256,
            resolutions
          };
          if (!(await intent()).data) await crm.draft("workbook-import-intent", saved, expectedOwner);
          await applySaved();
        } finally {
          if (applyButton.isConnected) applyButton.disabled = false;
        }
      });
    });
  }
  async function adoption() {
    if (!context.cohorts?.length) {
      $2("view").innerHTML = '<div class="empty">\u0413\u0440\u0443\u043F\u043F\u0430 \u0432\u043D\u0435\u0434\u0440\u0435\u043D\u0438\u044F \u0435\u0449\u0451 \u043D\u0435 \u0437\u0430\u0434\u0430\u043D\u0430. \u0421\u043E\u0441\u0442\u0430\u0432 \u0438 \u043F\u043B\u0430\u043D\u043E\u0432\u044B\u0435 \u0441\u043C\u0435\u043D\u044B \u0437\u0430\u0433\u0440\u0443\u0436\u0430\u044E\u0442\u0441\u044F \u0438\u0437 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u043E\u0433\u043E \u043A\u0430\u0434\u0440\u043E\u0432\u043E\u0433\u043E \u0438\u0441\u0442\u043E\u0447\u043D\u0438\u043A\u0430.</div>';
      return;
    }
    const until = /* @__PURE__ */ new Date(), since = new Date(Date.now() - 28 * 864e5);
    $2("view").innerHTML = `<div class="toolbar"><select id="cohort">${context.cohorts.map((c) => `<option value="${c.cohort_id}">${esc10(c.name)}</option>`).join("")}</select><input id="since" type="date" value="${since.toISOString().slice(0, 10)}"><input id="until" type="date" value="${until.toISOString().slice(0, 10)}"><select id="filter-role"><option value="">\u0412\u0441\u0435 \u0440\u043E\u043B\u0438</option><option value="employee">\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0438</option><option value="manager">\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u0438</option><option value="observer">\u041D\u0430\u0431\u043B\u044E\u0434\u0430\u0442\u0435\u043B\u0438</option></select><input id="filter-team" placeholder="ID \u043A\u043E\u043C\u0430\u043D\u0434\u044B"><select id="filter-feature"><option value="">\u0412\u0441\u0435 \u0444\u0443\u043D\u043A\u0446\u0438\u0438</option><option value="call">\u0417\u0432\u043E\u043D\u043A\u0438</option><option value="visit">\u0412\u044B\u0435\u0437\u0434\u044B</option><option value="task">\u0417\u0430\u0434\u0430\u0447\u0438</option></select><input id="filter-version" placeholder="\u0412\u0435\u0440\u0441\u0438\u044F \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u044F">${button("adoption-refresh", "\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C")}</div><div id="adoption-report"></div>`;
    const load3 = async () => {
      const report = await request2("POST", "adoption", {
        cohort_id: $2("cohort").value,
        role: $2("filter-role").value,
        team_id: $2("filter-team").value,
        feature: $2("filter-feature").value,
        version: $2("filter-version").value,
        since: $2("since").value + "T00:00:00Z",
        until: new Date(
          (/* @__PURE__ */ new Date($2("until").value + "T00:00:00Z")).getTime() + 864e5
        ).toISOString()
      });
      const metric = (title, value) => `<div class="metric">${title}<strong>${value.value == null ? "\u2014" : Math.round(value.value * 100) + "%"}</strong><small>${value.numerator} / ${value.denominator}</small></div>`;
      $2("adoption-report").innerHTML = `<p class="muted">\u0414\u0430\u043D\u043D\u044B\u0435 \u043D\u0430 ${date(report.as_of)} \xB7 ${esc10(report.completeness)}</p><div class="summary">${metric("\u0410\u043A\u0442\u0438\u0432\u0430\u0446\u0438\u044F", report.activation)}${metric("\u0420\u0435\u0433\u0443\u043B\u044F\u0440\u043D\u043E\u0441\u0442\u044C \u043F\u043E \u0441\u043C\u0435\u043D\u0430\u043C", report.regularity)}${metric("\u0414\u043E\u0441\u0442\u0430\u0432\u043A\u0430 \u0432 \u0440\u0435\u0435\u0441\u0442\u0440\u044B", report.delivery)}${metric("\u0414\u0438\u0430\u0433\u043D\u043E\u0441\u0442\u0438\u0447\u0435\u0441\u043A\u043E\u0435 \u043F\u043E\u043A\u0440\u044B\u0442\u0438\u0435", report.coverage)}</div><p class="muted">\u0413\u0440\u0430\u0444\u0438\u043A \u043D\u0435 \u0437\u0430\u0434\u0430\u043D: ${report.counts.unknown} \xB7 \u041F\u0435\u0440\u0432\u0430\u044F \u0441\u043C\u0435\u043D\u0430 \u0432\u043F\u0435\u0440\u0435\u0434\u0438: ${report.counts.not_started} \xB7 \u041F\u0440\u043E\u0441\u0440\u043E\u0447\u0435\u043D\u043D\u0430\u044F \u0434\u043E\u0441\u0442\u0430\u0432\u043A\u0430: ${report.delivery.overdue} \xB7 \u0411\u0435\u0437 \u0441\u0440\u043E\u043A\u0430 \u0434\u043E\u0441\u0442\u0430\u0432\u043A\u0438: ${report.delivery.without_sla}</p><h2>\u0420\u0430\u0431\u043E\u0447\u0438\u0435 \u0434\u043D\u0438</h2><div class="trend">${report.trend.map((d) => `<div style="height:${Math.max(3, d.planned ? 100 * d.active / d.planned : 3)}%" title="${esc10(d.workday)}: ${d.active}/${d.planned}"></div>`).join("")}</div>` + table(
        [
          "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A",
          "\u041F\u043B\u0430\u043D\u043E\u0432\u044B\u0435 / \u0430\u043A\u0442\u0438\u0432\u043D\u044B\u0435 \u0441\u043C\u0435\u043D\u044B",
          "\u041F\u043E\u0441\u043B\u0435\u0434\u043D\u044F\u044F \u0440\u0430\u0431\u043E\u0442\u0430",
          "\u0412\u0435\u0440\u0441\u0438\u0438",
          "\u041E\u0436\u0438\u0434\u0430\u0435\u0442 \u0440\u0435\u0435\u0441\u0442\u0440\u0430",
          "\u041F\u043E\u043B\u043D\u043E\u0442\u0430"
        ],
        report.employees.map(
          (e) => `<tr><td>${esc10(e.name)}</td><td>${e.planned_shifts} / ${e.active_shifts}</td><td>${date(e.last_work)}</td><td>${esc10(e.versions.join(", ") || "\u041D\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0445")}</td><td>${e.pending_delivery}</td><td>${esc10(e.reason || "\u0414\u0430\u043D\u043D\u044B\u0435 \u043F\u043E\u043B\u0443\u0447\u0435\u043D\u044B")}</td></tr>`
        )
      ) + `<h2 style="margin-top:28px">\u0417\u0430\u0444\u0438\u043A\u0441\u0438\u0440\u043E\u0432\u0430\u043D\u043D\u0430\u044F \u0440\u0430\u0431\u043E\u0442\u0430</h2>` + table(
        ["\u0412\u0438\u0434", "\u0427\u0435\u0440\u0435\u0437 \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u0435", "\u0414\u043E\u043B\u044F"],
        Object.entries(report.origins).map(
          ([kind2, v]) => `<tr><td>${esc10(labels[kind2] || kind2)}</td><td>${v.counts.app}</td><td>${v.app_share?.value != null ? Math.round(v.app_share.value * 100) + "%" : esc10(v.reason)}</td></tr>`
        )
      ) + `<p class="muted">\u041C\u0435\u0442\u0440\u0438\u043A\u0438 \u0432\u0435\u0440\u0441\u0438\u0438 ${report.metric_version}. \u0426\u0435\u043B\u0435\u0432\u044B\u0435 \u043F\u043E\u0440\u043E\u0433\u0438: ${Object.keys(report.cohort.thresholds).length ? "\u0437\u0430\u0434\u0430\u043D\u044B \u0434\u043B\u044F \u0433\u0440\u0443\u043F\u043F\u044B" : "\u043D\u0435 \u0437\u0430\u0434\u0430\u043D\u044B"}. \u0414\u0438\u0430\u0433\u043D\u043E\u0441\u0442\u0438\u043A\u0430 \u0438 \u0437\u0430\u044F\u0432\u043B\u0435\u043D\u043D\u0430\u044F \u0434\u043B\u0438\u0442\u0435\u043B\u044C\u043D\u043E\u0441\u0442\u044C \u0441\u043C\u0435\u043D\u044B \u043D\u0435 \u044F\u0432\u043B\u044F\u044E\u0442\u0441\u044F \u0442\u0430\u0431\u0435\u043B\u0435\u043C.</p>`;
      telemetry("report_viewed", "adoption");
    };
    bind("adoption-refresh", load3);
    await load3();
  }
  async function connection(host = document.getElementById("view")) {
    if (crm.renderConnection) {
      await crm.renderConnection(host, boot);
      return;
    }
    const $3 = (id) => id === "view" ? host : host.querySelector("#" + id);
    state = await crm.state();
    const savedLogins = state.existingLogins || [{ source: "manager", label: "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C", available: state.existingManager?.available }];
    const available = savedLogins.filter((entry) => entry.available);
    const current3 = state.context ? `<h2>\u0422\u0435\u043A\u0443\u0449\u0435\u0435 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435</h2><p>${state.connection.mode === "synthetic" ? "\u041F\u0440\u043E\u0432\u0435\u0440\u043E\u0447\u043D\u0430\u044F \u0431\u0430\u0437\u0430 \xB7 \u0441\u0438\u043D\u0442\u0435\u0442\u0438\u0447\u0435\u0441\u043A\u0438\u0435 \u0434\u0430\u043D\u043D\u044B\u0435" : esc10(state.connection.server)}</p>${button("connection-continue", "\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044C", true)}` : "";
    $3("view").innerHTML = `<div class="help connection-page">${current3}<h2>\u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0439 \u0432\u0445\u043E\u0434</h2><p>${available.length ? "\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0432\u0445\u043E\u0434 \u0438\u0437 \u0443\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D\u043D\u043E\u0439 \u0440\u0430\u0431\u043E\u0447\u0435\u0439 \u043F\u0440\u043E\u0433\u0440\u0430\u043C\u043C\u044B. \u0421\u0435\u0440\u0432\u0435\u0440 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442 \u043B\u0438\u0447\u043D\u043E\u0441\u0442\u044C \u0438 \u043F\u0440\u0430\u0432\u0430." : "\u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0439 \u0432\u0445\u043E\u0434 \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D. \u0412\u043E\u0439\u0434\u0438\u0442\u0435 \u0432 \u0443\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D\u043D\u0443\u044E \u0440\u0430\u0431\u043E\u0447\u0443\u044E \u043F\u0440\u043E\u0433\u0440\u0430\u043C\u043C\u0443 \u0438 \u043F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u0435 \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0443."}</p><div class="actions">${available.map((entry) => `<button ${entry.source === "manager" ? 'id="existing-manager-connect" ' : ""}data-existing-source="${esc10(entry.source)}" class="primary">${esc10(entry.label)}</button>`).join("")}${button("connection-refresh", "\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0441\u043D\u043E\u0432\u0430")}</div>${state.connectionNotice ? `<p role="status">${esc10(state.connectionNotice)}</p>` : ""}<details class="connection-testing"><summary>\u041F\u0440\u043E\u0432\u0435\u0440\u043E\u0447\u043D\u0430\u044F \u0441\u0440\u0435\u0434\u0430</summary><h2>\u0421\u0438\u043D\u0442\u0435\u0442\u0438\u0447\u0435\u0441\u043A\u0438\u0435 \u0434\u0430\u043D\u043D\u044B\u0435</h2><p>\u041E\u0442\u0434\u0435\u043B\u044C\u043D\u0430\u044F \u0431\u0430\u0437\u0430 \u043D\u0430 \u044D\u0442\u043E\u043C \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435. \u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B \u043D\u0435 \u043F\u043E\u043F\u0430\u0434\u0430\u044E\u0442 \u0432 \u0440\u0430\u0431\u043E\u0447\u0438\u0439 \u043F\u043E\u0440\u0442\u0444\u0435\u043B\u044C.</p><div class="toolbar"><select id="demo-role" aria-label="\u0420\u043E\u043B\u044C \u0432 \u043F\u0440\u043E\u0432\u0435\u0440\u043E\u0447\u043D\u043E\u0439 \u0431\u0430\u0437\u0435"><option value="employee">\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A</option><option value="manager">\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C</option><option value="observer">\u041D\u0430\u0431\u043B\u044E\u0434\u0430\u0442\u0435\u043B\u044C \u0434\u0440\u0443\u0433\u043E\u0439 \u043A\u043E\u043C\u0430\u043D\u0434\u044B</option><option value="courier">\u041A\u0443\u0440\u044C\u0435\u0440</option><option value="analyst">\u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A</option></select>${button("demo-connect", "\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0441\u0446\u0435\u043D\u0430\u0440\u0438\u0439")}</div><label class="check"><input id="offline" type="checkbox"${state.offline ? " checked" : ""}>\u041F\u0440\u0438\u043E\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0443 \u0434\u043B\u044F \u043F\u0440\u043E\u0432\u0435\u0440\u043A\u0438 \u043E\u0444\u043B\u0430\u0439\u043D</label><h2>\u041E\u0442\u0434\u0435\u043B\u044C\u043D\u044B\u0439 \u0441\u0435\u0440\u0432\u0435\u0440</h2><div class="grid"><div class="field span"><label for="server">HTTPS-\u0430\u0434\u0440\u0435\u0441</label><input id="server" placeholder="\u0410\u0434\u0440\u0435\u0441 \u043F\u0440\u043E\u0432\u0435\u0440\u043E\u0447\u043D\u043E\u0433\u043E \u0441\u0435\u0440\u0432\u0435\u0440\u0430"></div><div class="field"><label for="remote-role">\u0420\u043E\u043B\u044C \u0432\u0445\u043E\u0434\u0430</label><select id="remote-role"><option value="employee">\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A</option><option value="manager">\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C</option></select></div><div class="field"><label for="login">\u041B\u043E\u0433\u0438\u043D</label><input id="login" autocomplete="username"></div><div class="field"><label for="password">\u041F\u0430\u0440\u043E\u043B\u044C</label><input id="password" type="password" autocomplete="current-password"></div></div><div class="actions">${button("remote-connect", "\u0412\u043E\u0439\u0442\u0438")}</div></details></div>`;
    bind("connection-continue", () => go2("portfolio"));
    $3("view").insertAdjacentHTML("afterbegin", `<section class="diagnostics"><h2>\u0414\u0438\u0430\u0433\u043D\u043E\u0441\u0442\u0438\u043A\u0430 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u044F</h2><div class="actions">${button("diagnostic-run", "\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0438 \u0432\u043E\u0441\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C")}${button("diagnostic-queue", "\u041E\u0447\u0435\u0440\u0435\u0434\u044C \u0438 \u0441\u0432\u0435\u0440\u043A\u0430")}</div><div id="diagnostic-result" role="status"></div></section>`);
    const diagnose = async (repair = false) => {
      if (!crm.diagnostics) return;
      const generation = renderGeneration, button2 = $3("diagnostic-run");
      button2.disabled = true;
      try {
        const result2 = await crm.diagnostics(repair);
        if (generation !== renderGeneration || section !== "connection") return;
        if (!result2.ok) throw Error(result2.text || "\u0414\u0438\u0430\u0433\u043D\u043E\u0441\u0442\u0438\u043A\u0430 \u043D\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0438\u043B\u0430\u0441\u044C.");
        $3("diagnostic-result").innerHTML = table(["\u041F\u0440\u043E\u0432\u0435\u0440\u043A\u0430", "\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442"], result2.data.checks.map((c) => `<tr><td>${esc10({ main: "\u0417\u0430\u043F\u0443\u0441\u043A", renderer: "\u0418\u043D\u0442\u0435\u0440\u0444\u0435\u0439\u0441", disk: "\u0414\u0438\u0441\u043A", profile: "\u0417\u0430\u0449\u0438\u0449\u0451\u043D\u043D\u044B\u0439 \u043F\u0440\u043E\u0444\u0438\u043B\u044C", helper: "\u041B\u043E\u043A\u0430\u043B\u044C\u043D\u044B\u0439 \u043A\u043E\u043C\u043F\u043E\u043D\u0435\u043D\u0442", network: "\u0421\u0435\u0442\u044C \u0438 API", identity: "\u0412\u0445\u043E\u0434 \u0438 \u043F\u0440\u0430\u0432\u0430", queue: "\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u0435 \u0438 \u043E\u0447\u0435\u0440\u0435\u0434\u044C" }[c.stage] || c.stage)}<br><small>${esc10(c.code)}</small></td><td>${c.ok ? "\u2713" : "\u2014"} ${esc10(c.text)}</td></tr>`));
      } finally {
        if (generation === renderGeneration) button2.disabled = false;
      }
    };
    $3("diagnostic-run").insertAdjacentHTML("afterend", button("diagnostic-report", "\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043E\u0442\u0447\u0451\u0442"));
    bind("diagnostic-report", async () => {
      const result2 = await crm.diagnosticsReport?.();
      if (result2 && !result2.ok) throw Error(result2.text);
    });
    bind("diagnostic-run", () => diagnose(true));
    bind("diagnostic-queue", () => go2("queue"));
    void diagnose().catch((e) => notice(e.message));
    bind("connection-refresh", () => connection());
    $3("view").querySelectorAll("[data-existing-source]").forEach((element) => {
      bind(element.id || (element.id = "saved-login-" + element.dataset.existingSource), async () => {
        const buttons = $3("view").querySelectorAll("button");
        buttons.forEach((button2) => button2.disabled = true);
        try {
          const result2 = await crm.connect({ action: "existing-login", source: element.dataset.existingSource });
          if (!result2.ok) throw new Error(result2.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044C \u0432\u0445\u043E\u0434.");
          section = "portfolio";
          await boot();
        } finally {
          buttons.forEach((button2) => button2.disabled = false);
        }
      });
    });
    $3("demo-role").value = state.connection.role;
    $3("offline").onchange = async () => {
      await crm.connect({
        action: "offline",
        value: $3("offline").checked
      });
      state = await crm.state();
      $3("connection").textContent = state.offline ? "\u041F\u0440\u043E\u0432\u0435\u0440\u043A\u0430 \u043E\u0444\u043B\u0430\u0439\u043D: \u043E\u0442\u043F\u0440\u0430\u0432\u043A\u0430 \u043F\u0440\u0438\u043E\u0441\u0442\u0430\u043D\u043E\u0432\u043B\u0435\u043D\u0430" : "\u0421\u0432\u044F\u0437\u044C \u0432\u043A\u043B\u044E\u0447\u0435\u043D\u0430";
    };
    bind("demo-connect", async () => {
      const r = await crm.connect({
        mode: "synthetic",
        role: $3("demo-role").value
      });
      if (!r.ok) throw new Error(r.text);
      telemetry("login_ok", "shell");
      section = "portfolio";
      await boot();
    });
    bind("remote-connect", async () => {
      const r = await crm.connect({
        mode: "remote",
        role: $3("remote-role").value,
        server: $3("server").value,
        username: $3("login").value,
        password: $3("password").value
      });
      $3("password").value = "";
      if (!r.ok) throw new Error(r.text);
      telemetry("login_ok", "shell");
      section = "portfolio";
      await boot();
    });
  }
  async function help() {
    $2("view").innerHTML = `<div class="help"><h2>\u0421\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u0435 \u0438 \u043E\u0431\u043C\u0435\u043D</h2><p>\u0417\u0432\u043E\u043D\u043A\u0438, \u0432\u044B\u0435\u0437\u0434\u044B \u0438 \u043E\u0441\u0442\u0430\u043B\u044C\u043D\u044B\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044F \u0441\u043D\u0430\u0447\u0430\u043B\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u044E\u0442\u0441\u044F \u0432 \u0437\u0430\u0448\u0438\u0444\u0440\u043E\u0432\u0430\u043D\u043D\u043E\u0439 \u043E\u0447\u0435\u0440\u0435\u0434\u0438 ${savedOn}. \u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435 \u0441\u0435\u0440\u0432\u0435\u0440\u0430 \u0432\u0438\u0434\u043D\u043E \u0432 \u043E\u0447\u0435\u0440\u0435\u0434\u0438. \u041F\u0440\u0438 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u0438 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u044F \u0438\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u0437\u0430\u043F\u0438\u0441\u044C \u043E\u0441\u0442\u0430\u0451\u0442\u0441\u044F \u0434\u043B\u044F \u0441\u0432\u0435\u0440\u043A\u0438.</p><p>\u0423\u0441\u0442\u0430\u0440\u0435\u0432\u0448\u0435\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0435\u0442\u0441\u044F \u0432 \u0438\u0441\u0442\u043E\u0440\u0438\u0438. \u041E\u043D\u043E \u043D\u0435 \u0437\u0430\u0442\u0438\u0440\u0430\u0435\u0442 \u0431\u043E\u043B\u0435\u0435 \u0441\u0432\u0435\u0436\u0438\u0439 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0438. \u0418\u0441\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0438\u0435 \u043E\u0444\u043E\u0440\u043C\u043B\u044F\u0435\u0442\u0441\u044F \u043D\u043E\u0432\u044B\u043C \u0441\u043E\u0431\u044B\u0442\u0438\u0435\u043C \u0441 \u043F\u0440\u0438\u0447\u0438\u043D\u043E\u0439.</p><p>Excel \u043F\u043E\u043B\u0443\u0447\u0430\u0435\u0442 \u0432\u0435\u0440\u0441\u0438\u044E \u0432\u044B\u0433\u0440\u0443\u0437\u043A\u0438 \u0438 \u0432\u043D\u0443\u0442\u0440\u0435\u043D\u043D\u0438\u0435 ID \u0434\u043E\u0433\u043E\u0432\u043E\u0440\u043E\u0432. \u0420\u0443\u0447\u043D\u044B\u0435 \u0438\u0437\u043C\u0435\u043D\u0435\u043D\u0438\u044F \u043F\u0440\u043E\u0445\u043E\u0434\u044F\u0442 \u0441\u0432\u0435\u0440\u043A\u0443 \u0441 \u0438\u0441\u0445\u043E\u0434\u043D\u043E\u0439 \u0432\u0435\u0440\u0441\u0438\u0435\u0439 \u0438 \u0442\u0435\u043A\u0443\u0449\u0438\u043C \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C. \u041F\u0440\u043E\u043F\u0443\u0449\u0435\u043D\u043D\u0430\u044F \u0441\u0442\u0440\u043E\u043A\u0430 \u043D\u0435 \u0441\u043D\u0438\u043C\u0430\u0435\u0442 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0438\u0435. \u0418\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u043A\u043D\u0438\u0433\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u044F\u0435\u0442\u0441\u044F, \u0437\u0430\u043F\u0438\u0441\u044C \u0438 \u0447\u0442\u0435\u043D\u0438\u0435 \u043D\u043E\u0432\u043E\u0439 \u043A\u043E\u043F\u0438\u0438 \u043F\u0440\u043E\u0432\u0435\u0440\u044F\u044E\u0442\u0441\u044F.</p><h2>\u0421\u043C\u0435\u043D\u0430 \u0438 \u0434\u0438\u0430\u0433\u043D\u043E\u0441\u0442\u0438\u043A\u0430</h2><p>\u041D\u0430\u0447\u0430\u043B\u043E, \u043F\u0435\u0440\u0435\u0440\u044B\u0432 \u0438 \u043E\u043A\u043E\u043D\u0447\u0430\u043D\u0438\u0435 \u0441\u043C\u0435\u043D\u044B \u0444\u0438\u043A\u0441\u0438\u0440\u0443\u044E\u0442\u0441\u044F \u0441\u0435\u0440\u0432\u0435\u0440\u043D\u044B\u043C \u0432\u0440\u0435\u043C\u0435\u043D\u0435\u043C. \u042D\u0442\u043E \u0437\u0430\u044F\u0432\u043B\u0435\u043D\u043D\u0430\u044F \u0434\u043B\u0438\u0442\u0435\u043B\u044C\u043D\u043E\u0441\u0442\u044C, \u0430 \u043D\u0435 \u0440\u0430\u0441\u0447\u0451\u0442 \u043E\u043F\u043B\u0430\u0442\u044B.</p><p>\u0414\u043B\u044F \u043F\u0430\u043D\u0435\u043B\u0438 \u0432\u043D\u0435\u0434\u0440\u0435\u043D\u0438\u044F \u0443\u0447\u0438\u0442\u044B\u0432\u0430\u044E\u0442\u0441\u044F \u043F\u0440\u0438\u043D\u044F\u0442\u044B\u0435 \u0440\u0430\u0431\u043E\u0447\u0438\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044F, \u0437\u0430\u0434\u0430\u043D\u043D\u044B\u0435 \u043F\u043B\u0430\u043D\u043E\u0432\u044B\u0435 \u0441\u043C\u0435\u043D\u044B, \u043A\u0432\u0438\u0442\u0430\u043D\u0446\u0438\u0438 \u0434\u043E\u0441\u0442\u0430\u0432\u043A\u0438 \u0438 \u043E\u0433\u0440\u0430\u043D\u0438\u0447\u0435\u043D\u043D\u044B\u0435 \u0442\u0435\u0445\u043D\u0438\u0447\u0435\u0441\u043A\u0438\u0435 \u0441\u043E\u0431\u044B\u0442\u0438\u044F: \u043E\u0442\u043A\u0440\u044B\u0442\u0438\u0435 \u0444\u0443\u043D\u043A\u0446\u0438\u0439, \u0433\u043E\u0442\u043E\u0432\u043D\u043E\u0441\u0442\u044C \u044D\u043A\u0440\u0430\u043D\u0430, \u043A\u043E\u0434\u044B \u043E\u0448\u0438\u0431\u043E\u043A \u0438 \u0432\u0435\u0440\u0441\u0438\u044F. \u0422\u0435\u043A\u0441\u0442\u044B \u0437\u0430\u043C\u0435\u0442\u043E\u043A, \u043A\u043E\u043D\u0442\u0430\u043A\u0442\u044B \u043A\u043B\u0438\u0435\u043D\u0442\u043E\u0432, \u043D\u0430\u0436\u0430\u0442\u0438\u044F \u043A\u043B\u0430\u0432\u0438\u0448, \u0441\u043D\u0438\u043C\u043A\u0438 \u0438 \u0433\u0435\u043E\u043B\u043E\u043A\u0430\u0446\u0438\u044F \u0432 \u0434\u0438\u0430\u0433\u043D\u043E\u0441\u0442\u0438\u043A\u0443 \u043D\u0435 \u043F\u0435\u0440\u0435\u0434\u0430\u044E\u0442\u0441\u044F.</p><p>\u0421\u044B\u0440\u044B\u0435 \u0442\u0435\u0445\u043D\u0438\u0447\u0435\u0441\u043A\u0438\u0435 \u0441\u043E\u0431\u044B\u0442\u0438\u044F \u043C\u043E\u0436\u043D\u043E \u0445\u0440\u0430\u043D\u0438\u0442\u044C 30\u2013365 \u0434\u043D\u0435\u0439 \u043F\u043E \u043D\u0430\u0441\u0442\u0440\u043E\u0439\u043A\u0435 \u0432\u043B\u0430\u0434\u0435\u043B\u044C\u0446\u0430. \u041F\u0440\u0435\u0434\u043B\u0430\u0433\u0430\u0435\u0442\u0441\u044F 90 \u0434\u043D\u0435\u0439; \u0430\u0432\u0442\u043E\u043C\u0430\u0442\u0438\u0447\u0435\u0441\u043A\u0430\u044F \u043E\u0447\u0438\u0441\u0442\u043A\u0430 \u0434\u043E \u0432\u044B\u0431\u043E\u0440\u0430 \u043F\u043E\u043B\u0438\u0442\u0438\u043A\u0438 \u043E\u0442\u043A\u043B\u044E\u0447\u0435\u043D\u0430. \u0411\u0438\u0437\u043D\u0435\u0441-\u0438\u0441\u0442\u043E\u0440\u0438\u044F \u0445\u0440\u0430\u043D\u0438\u0442\u0441\u044F \u043E\u0442\u0434\u0435\u043B\u044C\u043D\u043E.</p></div>`;
  }
  var crm, savedOn, $2, esc10, date, amount, labels, historyWhen, state, context, section, card, tab, scriptOpened, cardScript, renderGeneration, portfolioView, portfolioWork, portfolioEmployee, portfolioSearch, portfolioOffset, disposeSection, portfolioIdentity, portfolioGeneration, analystIdentity, sectionScroll, taskStatus, taskEmployee, taskWork, taskSearch, taskOffset, taskGeneration, cardOrigin, taskSelectClient, newTaskAssignee, activePerson, lastCardAcknowledgements, cardSyncGeneration, formOpening, cardRefreshPending, cardRefreshError, syncTimer, cardRetryTimer, syncForced, notice, telemetry, table, debtKeys;
  var init_crm_app = __esm({
    "hard-desk/ui/crm-app.ts"() {
      "use strict";
      init_crm_delivery();
      init_crm_registry();
      init_crm_card_editor();
      init_crm_manager_reports();
      init_crm_manager_team();
      init_crm_analyst();
      crm = window.crm;
      savedOn = crm.platform === "mobile" ? "\u043D\u0430 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435" : "\u043D\u0430 \u043A\u043E\u043C\u043F\u044C\u044E\u0442\u0435\u0440\u0435";
      $2 = (id) => document.getElementById(id);
      esc10 = (value) => String(value ?? "").replace(
        /[&<>"']/g,
        (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
      );
      date = (value) => value ? new Date(value).toLocaleString("ru-RU") : "\u2014";
      amount = (value, currency = "") => value == null || value === "" ? "\u041D\u0435 \u0443\u043A\u0430\u0437\u0430\u043D\u043E" : esc10(value) + (currency ? " " + esc10(currency) : "");
      labels = {
        call: "\u0417\u0432\u043E\u043D\u043E\u043A",
        source_sync: "\u0421\u0432\u0435\u0440\u043A\u0430 \u043F\u0440\u0435\u0436\u043D\u0435\u0433\u043E \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u044F",
        registry_edit: "\u041F\u0440\u0430\u0432\u043A\u0430 \u0440\u0435\u0435\u0441\u0442\u0440\u0430",
        visit: "\u0412\u044B\u0435\u0437\u0434",
        delivery: "\u041F\u0435\u0440\u0435\u0434\u0430\u0442\u044C \u043A\u043E\u043D\u0432\u0435\u0440\u0442",
        note: "\u0417\u0430\u043C\u0435\u0442\u043A\u0430",
        contact: "\u041A\u043E\u043D\u0442\u0430\u043A\u0442\u044B",
        promise: "\u041E\u0431\u0435\u0449\u0430\u043D\u0438\u0435",
        task: "\u0417\u0430\u0434\u0430\u0447\u0430",
        reported_payment: "\u0417\u0430\u044F\u0432\u043B\u0435\u043D\u043D\u0430\u044F \u043E\u043F\u043B\u0430\u0442\u0430",
        payment_confirmed: "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u043E\u0435 \u043F\u043E\u0441\u0442\u0443\u043F\u043B\u0435\u043D\u0438\u0435",
        document: "\u0414\u043E\u043A\u0443\u043C\u0435\u043D\u0442",
        attachment: "\u0424\u0430\u0439\u043B \u0434\u043E\u043A\u0443\u043C\u0435\u043D\u0442\u0430",
        correction: "\u0418\u0441\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0438\u0435",
        manual_edit: "\u0420\u0443\u0447\u043D\u0430\u044F \u043F\u0440\u0430\u0432\u043A\u0430 Excel",
        task_complete: "\u0417\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u0438\u0435 \u0437\u0430\u0434\u0430\u0447\u0438",
        script_publish: "\u041F\u0443\u0431\u043B\u0438\u043A\u0430\u0446\u0438\u044F \u0441\u043A\u0440\u0438\u043F\u0442\u0430"
      };
      historyWhen = (event) => event.time_quality === "legacy_day" ? calendarDate(event.payload.legacy_workday) : date(event.time_quality === "legacy_unknown" ? event.payload.legacy_created_at : event.received_at);
      section = "portfolio";
      tab = "overview";
      cardScript = null;
      renderGeneration = 0;
      portfolioView = "mine";
      portfolioWork = "all";
      portfolioEmployee = "";
      portfolioSearch = "";
      portfolioOffset = 0;
      portfolioIdentity = "";
      portfolioGeneration = 0;
      analystIdentity = "";
      sectionScroll = /* @__PURE__ */ new Map();
      taskStatus = "open";
      taskEmployee = "all";
      taskWork = "all";
      taskSearch = "";
      taskOffset = 0;
      taskGeneration = 0;
      cardOrigin = "portfolio";
      taskSelectClient = false;
      newTaskAssignee = "";
      activePerson = "";
      lastCardAcknowledgements = "";
      cardSyncGeneration = 0;
      formOpening = false;
      cardRefreshPending = false;
      cardRefreshError = "";
      syncForced = false;
      notice = (text2 = "", tone = "error") => {
        $2("notice").textContent = text2;
        $2("notice").dataset.tone = tone;
      };
      telemetry = (event_type, feature, extra = {}) => {
        void crm.telemetry({ event_type, feature, ...extra }).catch(() => {
        });
      };
      table = (heads, rows) => `<div class="table-wrap"><table><thead><tr>${heads.map((x) => `<th>${esc10(x)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
      debtKeys = [
        "debt_overdue",
        "interest_overdue",
        "penalty",
        "duty",
        "dtp",
        "rent",
        "camera_fine",
        "dirt",
        "trash",
        "new_damage",
        "evacuation",
        "peni",
        "other"
      ];
      document.addEventListener("click", (e) => {
        const target = e.target.closest(
          "[data-script-used]"
        );
        if (target && !target.disabled) {
          telemetry("script_step_used", "script");
          target.disabled = true;
          target.textContent = "\u0418\u0441\u043F\u043E\u043B\u044C\u0437\u043E\u0432\u0430\u043D";
        }
      });
      document.querySelectorAll("[data-win]").forEach((b) => b.onclick = () => crm.win(b.dataset.win));
      $2("dialog").addEventListener("close", () => scheduleSync());
      crm.onChange?.((event) => {
        if (event.type === "storage-error") {
          notice(event.text);
        } else if (event.type === "identity") {
          clearTimeout(cardRetryTimer);
          renderGeneration++;
          activePerson = "";
          cardSyncGeneration++;
          card = null;
          context = null;
          $2("dialog").close();
          void boot().catch((error) => notice(error.message));
        } else if (event.type === "live") {
          const environment = context?.distribution?.environment === "isolated" ? "\u0418\u0437\u043E\u043B\u0438\u0440\u043E\u0432\u0430\u043D\u043D\u044B\u0439 \u0441\u0442\u0435\u043D\u0434 \xB7 " : state?.connection?.mode === "synthetic" ? "\u0421\u0438\u043D\u0442\u0435\u0442\u0438\u0447\u0435\u0441\u043A\u0438\u0435 \u0434\u0430\u043D\u043D\u044B\u0435 \xB7 " : "";
          $2("connection").textContent = environment + (event.connected ? "\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043D\u043D\u0430\u044F \u0441\u0432\u044F\u0437\u044C \xB7 " + date(event.as_of) : `\u0421\u0432\u044F\u0437\u044C \u043D\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0430 \xB7 \u0432\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D ${savedOn}`);
          if (event.dirty) {
            if (activePerson) scheduleSync(true);
            else if (section === "portfolio") void portfolio().catch((error) => notice(error.message));
          }
        } else scheduleSync();
      });
      crm.onNavigate?.(async (route) => {
        $2("dialog").close();
        if (!document.querySelector(`#navigation [data-section="${CSS.escape(route.section)}"]`) && !["queue", "tasks", "shift", "script", "help"].includes(route.section)) return;
        await go2(route.section);
        if (route.person) await openCard(route.person);
      });
      window.addEventListener("focus", () => {
        if (activePerson) scheduleSync(true);
      });
      void boot().catch((e) => {
        notice(e.message);
        if (crm.platform === "mobile") {
          $2("title").textContent = "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435";
          $2("view").innerHTML = '<p>\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043E\u0442\u043A\u0440\u044B\u0442\u044C \u0440\u0430\u0431\u043E\u0447\u0435\u0435 \u043C\u0435\u0441\u0442\u043E. \u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0435 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B \u043E\u0441\u0442\u0430\u044E\u0442\u0441\u044F \u043D\u0430 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435.</p><button id="retry-start">\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C \u0437\u0430\u043F\u0443\u0441\u043A</button>';
          $2("retry-start").onclick = () => location.reload();
        }
      });
    }
  });

  // hard-desk/ui/mobile/store.ts
  var opening;
  function database() {
    return opening ??= new Promise((resolve, reject) => {
      const request3 = indexedDB.open("rimborso-crm-mobile-v1", 1);
      request3.onupgradeneeded = () => {
        request3.result.createObjectStore("vault");
        request3.result.createObjectStore("keys");
      };
      request3.onsuccess = () => resolve(request3.result);
      request3.onerror = () => reject(request3.error);
    });
  }
  var result = (request3) => new Promise((resolve, reject) => {
    request3.onsuccess = () => resolve(request3.result);
    request3.onerror = () => reject(request3.error);
  });
  var complete = (tx) => new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onabort = tx.onerror = () => reject(tx.error || Error("\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0434\u0430\u043D\u043D\u044B\u0435 \u043D\u0430 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435."));
  });
  var keyPromise;
  async function key() {
    return keyPromise ??= (async () => {
      const db = await database();
      let saved = await result(db.transaction("keys").objectStore("keys").get("device"));
      if (saved) return saved;
      const generated = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
      try {
        const tx = db.transaction("keys", "readwrite"), done = complete(tx);
        tx.objectStore("keys").add(generated, "device");
        await done;
        return generated;
      } catch {
        saved = await result(db.transaction("keys").objectStore("keys").get("device"));
        if (!saved) throw Error("\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043E\u0442\u043A\u0440\u044B\u0442\u044C \u0437\u0430\u0449\u0438\u0449\u0451\u043D\u043D\u043E\u0435 \u0445\u0440\u0430\u043D\u0438\u043B\u0438\u0449\u0435.");
        return saved;
      }
    })();
  }
  var address = async (owner, name) => owner + "\n" + (name.startsWith("read:") ? "read:" : "record:") + Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(name)))).map((v) => v.toString(16).padStart(2, "0")).join("");
  async function decode(record, id) {
    if (!record) return null;
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: new Uint8Array(record.iv), additionalData: new TextEncoder().encode(location.origin + "\n" + id) }, await key(), record.cipher);
    return JSON.parse(new TextDecoder().decode(plain));
  }
  async function get(owner, name) {
    const id = await address(owner, name), db = await database();
    const record = await result(db.transaction("vault").objectStore("vault").get(id));
    if (record) return decode(record, id);
    if (await result(db.transaction("vault").objectStore("vault").get(owner + "\n" + name))) return update(owner, name, (value) => value);
    return null;
  }
  async function update(owner, name, change) {
    const id = await address(owner, name), legacy2 = owner + "\n" + name, db = await database();
    for (let attempt = 0; attempt < 20; attempt++) {
      const before = await result(db.transaction("vault").objectStore("vault").get(id));
      const old = before ? null : await result(db.transaction("vault").objectStore("vault").get(legacy2));
      const value = change(await decode(before || old, before ? id : legacy2)), iv = crypto.getRandomValues(new Uint8Array(12));
      const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode(location.origin + "\n" + id) }, await key(), new TextEncoder().encode(JSON.stringify(value)));
      const tx = db.transaction("vault", "readwrite"), done = complete(tx);
      let conflict = false;
      const read = tx.objectStore("vault").get(id);
      read.onsuccess = () => {
        if ((read.result?.revision || 0) !== (before?.revision || 0)) {
          conflict = true;
          tx.abort();
        } else {
          tx.objectStore("vault").put({ revision: (before?.revision || 0) + 1, iv, cipher }, id);
          if (old) tx.objectStore("vault").delete(legacy2);
        }
      };
      try {
        await done;
        return value;
      } catch (error) {
        if (!conflict) throw error;
      }
    }
    throw Error("\u0414\u0430\u043D\u043D\u044B\u0435 \u043C\u0435\u043D\u044F\u044E\u0442\u0441\u044F \u0432 \u0434\u0440\u0443\u0433\u043E\u0439 \u0432\u043A\u043B\u0430\u0434\u043A\u0435. \u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u0435 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0438\u0435.");
  }
  var put = (owner, name, value) => update(owner, name, () => value);
  async function clearReads(owner) {
    const db = await database(), tx = db.transaction("vault", "readwrite"), done = complete(tx);
    const cursor = tx.objectStore("vault").openKeyCursor();
    cursor.onsuccess = () => {
      const row = cursor.result;
      if (!row) return;
      if (String(row.key).startsWith(owner + "\nread:")) tx.objectStore("vault").delete(row.key);
      row.continue();
    };
    await done;
  }

  // hard-desk/ui/mobile/connection.ts
  var esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  var passwordForm = (temporary = false) => '<form data-mobile-password class="mobile-login"><h2>' + (temporary ? "\u0421\u043C\u0435\u043D\u0438\u0442\u0435 \u0432\u0440\u0435\u043C\u0435\u043D\u043D\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C" : "\u0418\u0437\u043C\u0435\u043D\u0438\u0442\u044C \u043F\u0430\u0440\u043E\u043B\u044C") + '</h2><label>\u0422\u0435\u043A\u0443\u0449\u0438\u0439 \u043F\u0430\u0440\u043E\u043B\u044C<input name="current" type="password" autocomplete="current-password" required></label><label>\u041D\u043E\u0432\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C<input name="new" type="password" autocomplete="new-password" minlength="8" required></label><label>\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u0435 \u043D\u043E\u0432\u044B\u0439 \u043F\u0430\u0440\u043E\u043B\u044C<input name="repeat" type="password" autocomplete="new-password" minlength="8" required></label><button type="submit" class="primary">\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u043F\u0430\u0440\u043E\u043B\u044C</button></form>';
  async function mountConnection(host, crm2, boot2) {
    const [response, state2] = await Promise.all([boot2(), crm2.state()]);
    const connected = !!state2.context, pending = response.data?.pending, network = !!response.ok, synthetic = response.data?.synthetic === true || state2.connection.mode === "synthetic";
    const choosingRole = synthetic && !connected && !pending;
    let account = "";
    if (connected) account = '<section class="mobile-account"><div class="mobile-avatar">\u0412</div><div><h2>' + esc({ employee: "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A", manager: "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C", analyst: "\u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A", courier: "\u041A\u0443\u0440\u044C\u0435\u0440", observer: "\u041D\u0430\u0431\u043B\u044E\u0434\u0430\u0442\u0435\u043B\u044C" }[state2.context.role] || "\u0420\u0430\u0431\u043E\u0447\u0435\u0435 \u043C\u0435\u0441\u0442\u043E") + "</h2><p>" + (state2.connection.mode === "synthetic" ? "\u041F\u0440\u043E\u0432\u0435\u0440\u043E\u0447\u043D\u0430\u044F \u0441\u0440\u0435\u0434\u0430" : esc(location.host)) + '</p></div><button id="mobile-logout" class="link">\u0412\u044B\u0439\u0442\u0438</button></section>';
    else if (pending) account = '<section class="mobile-pending"><h2>' + esc(pending.employee) + "</h2><p>" + esc(pending.username) + "</p>" + (pending.status === "password" ? passwordForm(true) : '<p>\u0422\u0435\u043B\u0435\u0444\u043E\u043D \u043E\u0436\u0438\u0434\u0430\u0435\u0442 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u044F \u0440\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u0435\u043C \u0438\u043B\u0438 \u0430\u0434\u043C\u0438\u043D\u0438\u0441\u0442\u0440\u0430\u0442\u043E\u0440\u043E\u043C. \u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435 \u043F\u0440\u043E\u0432\u0435\u0440\u044F\u0435\u0442\u0441\u044F \u0430\u0432\u0442\u043E\u043C\u0430\u0442\u0438\u0447\u0435\u0441\u043A\u0438.</p><button id="mobile-refresh">\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0434\u043E\u0441\u0442\u0443\u043F</button>') + '<button id="mobile-logout" class="link">\u0421\u043C\u0435\u043D\u0438\u0442\u044C \u0432\u0445\u043E\u0434</button></section>';
    else if (!synthetic) account = '<form id="mobile-login" class="mobile-login"><h2>\u0412\u0445\u043E\u0434 \u0432 \u0440\u0430\u0431\u043E\u0447\u0435\u0435 \u043C\u0435\u0441\u0442\u043E</h2><label>\u041B\u043E\u0433\u0438\u043D<input name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required></label><label>\u041F\u0430\u0440\u043E\u043B\u044C<input name="password" type="password" autocomplete="current-password" required></label><button type="submit" class="primary">\u0412\u043E\u0439\u0442\u0438</button></form>';
    const choices = '<p>\u0421\u0438\u043D\u0442\u0435\u0442\u0438\u0447\u0435\u0441\u043A\u0438\u0435 \u0434\u0430\u043D\u043D\u044B\u0435</p><div class="mobile-role-options">' + [["employee", "\u0421\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A"], ["courier", "\u041A\u0443\u0440\u044C\u0435\u0440"], ["manager", "\u0420\u0443\u043A\u043E\u0432\u043E\u0434\u0438\u0442\u0435\u043B\u044C"], ["analyst", "\u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A"], ["observer", "\u041D\u0430\u0431\u043B\u044E\u0434\u0430\u0442\u0435\u043B\u044C"]].map(([id, name]) => '<button data-mobile-role="' + id + '">' + name + '<span aria-hidden="true">\u2197</span></button>').join("") + "</div>";
    const demo = synthetic ? choosingRole ? '<section class="mobile-demo mobile-demo-entry" aria-label="\u0412\u044B\u0431\u043E\u0440 \u0440\u043E\u043B\u0438"><h2>\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0440\u043E\u043B\u044C</h2>' + choices + "</section>" : '<details class="mobile-demo"><summary>\u0421\u043C\u0435\u043D\u0438\u0442\u044C \u0440\u043E\u043B\u044C</summary>' + choices + "</details>" : "";
    host.innerHTML = '<div class="mobile-connection">' + account + (choosingRole ? demo : "") + '<section class="mobile-connection-status"><span class="mobile-signal ' + (network ? "online" : "") + '"></span><div><strong>' + (network ? "\u0421\u0435\u0440\u0432\u0435\u0440 \u043E\u0442\u0432\u0435\u0447\u0430\u0435\u0442" : "\u0421\u0432\u044F\u0437\u044C \u043D\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0430") + "</strong><p>" + (network ? "\u0421\u043E\u0441\u0442\u043E\u044F\u043D\u0438\u0435 \u043E\u0442\u043F\u0440\u0430\u0432\u043E\u043A \u2014 \u0432 \u043E\u0447\u0435\u0440\u0435\u0434\u0438." : "\u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0435 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B \u043E\u0441\u0442\u0430\u044E\u0442\u0441\u044F \u043D\u0430 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435.") + "</p></div></section>" + (connected ? '<button id="mobile-send" class="mobile-wide">\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u0438 \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C \u043E\u0447\u0435\u0440\u0435\u0434\u044C</button>' : "") + (connected && !synthetic ? '<details class="mobile-diagnostics"><summary>\u0418\u0437\u043C\u0435\u043D\u0438\u0442\u044C \u043F\u0430\u0440\u043E\u043B\u044C</summary>' + passwordForm() + "</details>" : "") + (!choosingRole ? demo : "") + '<details class="mobile-diagnostics"><summary>\u0414\u0438\u0430\u0433\u043D\u043E\u0441\u0442\u0438\u043A\u0430</summary><p id="mobile-diagnostic-status"></p><button id="mobile-check">\u041F\u0440\u043E\u0432\u0435\u0440\u0438\u0442\u044C \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435</button></details><p id="mobile-connect-result" role="status" aria-live="polite"></p><small class="mobile-build">' + esc(state2.version) + "</small></div>";
    const root = host.firstElementChild, feedback = host.querySelector("#mobile-connect-result");
    const safe = (fn2) => async () => {
      try {
        feedback.textContent = "";
        await fn2();
      } catch (e) {
        feedback.textContent = e.message;
        feedback.scrollIntoView({ block: "nearest" });
      }
    };
    host.querySelectorAll("[data-mobile-role]").forEach((b) => b.onclick = safe(async () => {
      b.disabled = true;
      const r = await crm2.connect({ action: "synthetic", role: b.dataset.mobileRole });
      if (!r.ok) {
        b.disabled = false;
        throw Error(r.text || "\u0412\u0445\u043E\u0434 \u043D\u0435 \u0432\u044B\u043F\u043E\u043B\u043D\u0435\u043D.");
      }
    }));
    const form = host.querySelector("#mobile-login");
    if (form) form.onsubmit = (e) => {
      e.preventDefault();
      void safe(async () => {
        const b = form.querySelector("[type=submit]"), data2 = new FormData(form);
        b.disabled = true;
        try {
          const r = await crm2.connect({ action: "login", username: data2.get("username"), password: data2.get("password") });
          if (!r.ok) throw Error(r.text || "\u0412\u0445\u043E\u0434 \u043D\u0435 \u0432\u044B\u043F\u043E\u043B\u043D\u0435\u043D.");
        } finally {
          b.disabled = false;
        }
      })();
    };
    host.querySelectorAll("[data-mobile-password]").forEach((form2) => {
      form2.onsubmit = (e) => {
        e.preventDefault();
        void safe(async () => {
          const data2 = new FormData(form2), b = form2.querySelector("[type=submit]");
          if (data2.get("new") !== data2.get("repeat")) throw Error("\u041F\u0430\u0440\u043E\u043B\u0438 \u043D\u0435 \u0441\u043E\u0432\u043F\u0430\u0434\u0430\u044E\u0442.");
          b.disabled = true;
          try {
            const r = await crm2.connect({ action: "password", current: data2.get("current"), new: data2.get("new") });
            if (!r.ok) throw Error(r.text || "\u041F\u0430\u0440\u043E\u043B\u044C \u043D\u0435 \u0438\u0437\u043C\u0435\u043D\u0451\u043D.");
            form2.reset();
            feedback.textContent = "\u041F\u0430\u0440\u043E\u043B\u044C \u0438\u0437\u043C\u0435\u043D\u0451\u043D.";
          } finally {
            b.disabled = false;
          }
        })();
      };
    });
    host.querySelector("#mobile-logout")?.addEventListener("click", safe(async () => {
      const r = await crm2.connect({ action: "logout" });
      if (!r.ok) throw Error(r.text);
      location.reload();
    }));
    host.querySelector("#mobile-send")?.addEventListener("click", safe(async () => {
      await crm2.queue("flush");
      const q = await crm2.queue();
      feedback.textContent = q.data.entries.some((e) => e.state === "pending") ? "\u0415\u0441\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B, \u043E\u0436\u0438\u0434\u0430\u044E\u0449\u0438\u0435 \u0441\u0432\u044F\u0437\u0438." : q.data.entries.some((e) => e.state === "attention") ? "\u0415\u0441\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B, \u0442\u0440\u0435\u0431\u0443\u044E\u0449\u0438\u0435 \u0441\u0432\u0435\u0440\u043A\u0438 \u0432 \u043E\u0447\u0435\u0440\u0435\u0434\u0438." : "\u041E\u0447\u0435\u0440\u0435\u0434\u044C \u043F\u0440\u043E\u0432\u0435\u0440\u0435\u043D\u0430.";
    }));
    const refresh2 = safe(async () => {
      await crm2.connect({ action: "refresh" });
    });
    host.querySelector("#mobile-refresh")?.addEventListener("click", refresh2);
    if (pending?.status === "device") {
      const tick = async () => {
        if (!root.isConnected) return;
        await refresh2();
        if (root.isConnected) setTimeout(() => void tick(), 5e3);
      };
      setTimeout(() => void tick(), 5e3);
    }
    host.querySelector("#mobile-check").onclick = safe(async () => {
      const r = await boot2();
      host.querySelector("#mobile-diagnostic-status").textContent = r.ok ? "\u0421\u0435\u0440\u0432\u0435\u0440 \u043E\u0442\u0432\u0435\u0447\u0430\u0435\u0442. \u0425\u0440\u0430\u043D\u0438\u043B\u0438\u0449\u0435 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0430 \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u043E." : r.text || "\u041D\u0435\u0442 \u0441\u0432\u044F\u0437\u0438 \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C.";
    });
  }

  // hard-desk/ui/mobile/activity.ts
  var MobileActivity = class {
    constructor(api4, enqueue2, failed = () => {
    }) {
      this.api = api4;
      this.enqueue = enqueue2;
      this.failed = failed;
      for (const name of ["pointerdown", "keydown", "scroll"]) document.addEventListener(name, () => {
        this.lastInput = performance.now();
      }, { passive: true, capture: true });
      setInterval(() => {
        void this.run(() => this.tick()).catch(this.failed);
      }, 2e3);
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) void this.run(() => this.pause()).catch(this.failed);
      });
      window.addEventListener("pagehide", () => {
        void this.run(() => this.pause()).catch(this.failed);
      });
    }
    previous = null;
    open = null;
    anchor = null;
    owner = "";
    lastInput = performance.now();
    serial = Promise.resolve();
    run(work) {
      const next = this.serial.catch(() => {
      }).then(work);
      this.serial = next;
      return next;
    }
    start(owner, installation) {
      return this.run(async () => {
        await this.finish();
        this.previous = null;
        this.anchor = null;
        this.owner = owner;
        const legacy2 = await get(owner, "activity-prefix");
        if (legacy2) {
          await this.enqueue("active-time", legacy2, owner);
          await put(owner, "activity-prefix", null);
        }
        await this.recover(owner);
        const result2 = await this.api("POST", "anchor", { installation_id: installation });
        if (result2.ok) this.anchor = { ...result2.data, monotonic: performance.now() };
      });
    }
    async pause() {
      this.previous = null;
      await this.finish();
    }
    stop() {
      return this.run(async () => {
        await this.pause();
        this.owner = "";
        this.anchor = null;
      });
    }
    state() {
      return { clock_quality: this.anchor ? "anchored" : "unconfirmed", idle_threshold_seconds: 120 };
    }
    clock() {
      return this.anchor ? { anchor_id: this.anchor.anchor_id, installation_id: this.anchor.installation_id, elapsed_ms: Math.max(0, Math.round(performance.now() - this.anchor.monotonic)) } : null;
    }
    async deliver(owner, record) {
      await this.enqueue("active-time", record.body, owner);
      await update(owner, "activity-prefixes", (rows) => {
        rows ||= {};
        const id = record.body.operation_id;
        if (rows[id]?.sealed) delete rows[id];
        return rows;
      });
    }
    async recover(owner) {
      const now = Date.now();
      const rows = await update(owner, "activity-prefixes", (rows2) => {
        rows2 ||= {};
        for (const [id, row] of Object.entries(rows2)) if (id !== this.open?.body.operation_id && (row.sealed || now - row.at > 6e3 || row.at > now + 6e4)) row.sealed = true;
        return rows2;
      });
      for (const row of Object.values(rows)) if (row.sealed) await this.deliver(owner, row);
    }
    async finish() {
      const opened = this.open;
      this.open = null;
      if (!opened) return;
      const rows = await update(opened.owner, "activity-prefixes", (rows2) => {
        rows2 ||= {};
        const row2 = rows2[opened.body.operation_id];
        if (row2) row2.sealed = true;
        return rows2;
      });
      const row = rows[opened.body.operation_id];
      if (row) await this.deliver(opened.owner, row);
    }
    async tick() {
      if (this.owner) await this.recover(this.owner);
      const clock = this.clock(), kind2 = !document.hidden && document.hasFocus() ? performance.now() - this.lastInput < 12e4 ? "active" : "idle" : null;
      const current3 = clock && this.owner ? { ...clock, owner: this.owner, kind: kind2 } : null, prior = this.previous;
      this.previous = current3;
      if (!prior || !current3 || !kind2 || prior.kind !== kind2 || prior.owner !== current3.owner || prior.anchor_id !== current3.anchor_id || current3.elapsed_ms <= prior.elapsed_ms || current3.elapsed_ms - prior.elapsed_ms > 5e3) {
        await this.finish();
        return;
      }
      if (this.open && (this.open.body.kind !== kind2 || current3.elapsed_ms - this.open.body.start_ms > 3e4)) await this.finish();
      if (this.open) {
        const id = this.open.body.operation_id;
        const updated = await update(current3.owner, "activity-prefixes", (rows) => {
          rows ||= {};
          if (rows[id] && !rows[id].sealed) {
            rows[id].body = { ...rows[id].body, end_ms: current3.elapsed_ms };
            rows[id].at = Date.now();
          }
          return rows;
        });
        if (!updated[id] || updated[id].sealed) this.open = null;
        else this.open.body = updated[id].body;
      }
      if (!this.open) {
        const body = { schema_version: 1, operation_id: crypto.randomUUID(), anchor_id: clock.anchor_id, installation_id: clock.installation_id, start_ms: prior.elapsed_ms, end_ms: current3.elapsed_ms, kind: kind2 };
        await update(current3.owner, "activity-prefixes", (rows) => ({ ...rows, [body.operation_id]: { body, at: Date.now(), sealed: false } }));
        this.open = { owner: current3.owner, body };
      }
      if (this.open.body.end_ms - this.open.body.start_ms >= 28e3) await this.finish();
    }
  };

  // hard-desk/ui/mobile/bridge.ts
  var listeners = /* @__PURE__ */ new Set();
  var canonical = (v) => JSON.stringify(v, (_, x) => x && typeof x === "object" && !Array.isArray(x) ? Object.fromEntries(Object.keys(x).sort().map((k) => [k, x[k]])) : x);
  var background = (work) => {
    void work.catch(() => emit({ type: "storage-error", text: "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0434\u0430\u043D\u043D\u044B\u0435 \u043D\u0430 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435. \u041E\u0441\u0442\u0430\u0432\u044C\u0442\u0435 \u043E\u043A\u043D\u043E \u043E\u0442\u043A\u0440\u044B\u0442\u044B\u043C \u0438 \u043F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435." }));
  };
  var current = null;
  var csrf = "";
  var offline = false;
  var flushing = null;
  var liveTimer;
  var polling = false;
  var navigationTarget = "";
  var initialPerson = "";
  var navigating = false;
  var routeHandler;
  window.addEventListener("popstate", () => {
    const route = history.state?.crmMobile;
    if (!route || route.owner !== current?.owner || !routeHandler) return;
    navigating = true;
    background(routeHandler(route).finally(() => {
      navigating = false;
    }));
  });
  var channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("crm-mobile-events") : null;
  var activity = new MobileActivity(api, async (action3, body, owner) => persistEntry(owner, action3, body), () => emit({ type: "storage-error", text: "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0430\u043A\u0442\u0438\u0432\u043D\u044B\u0439 \u0438\u043D\u0442\u0435\u0440\u0432\u0430\u043B. \u041E\u0441\u0442\u0430\u0432\u044C\u0442\u0435 \u043E\u043A\u043D\u043E \u043E\u0442\u043A\u0440\u044B\u0442\u044B\u043C \u0438 \u043F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u0435 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435." }));
  var emit = (event, broadcast = false) => {
    for (const fn2 of listeners) fn2(event);
    if (broadcast) channel?.postMessage(event);
  };
  channel?.addEventListener("message", (event) => {
    if (event.data?.owner === current?.owner) emit(event.data);
    else if (event.data?.type === "identity") background(initialize().then(() => emit({ type: "identity" })));
  });
  function authority(expected) {
    if (!current?.owner || expected && expected !== current.owner) throw Error("\u0412\u0445\u043E\u0434 \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u0441\u044F. \u0412\u0432\u043E\u0434 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D \u0443 \u0438\u0441\u0445\u043E\u0434\u043D\u043E\u0433\u043E \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430.");
    return current.owner;
  }
  async function revoke(status2, expected = current?.owner) {
    if (status2 === 401 && expected && current?.owner === expected) {
      await update("device", "last-state", (saved) => saved?.owner === expected ? null : saved);
      if (current?.owner !== expected) return;
      current = null;
      background(activity.stop());
      emit({ type: "identity" });
    }
  }
  async function request(path, body) {
    const startedOwner = current?.owner;
    if (offline || !navigator.onLine) return { ok: false, status: 0, text: "\u041D\u0435\u0442 \u0441\u0432\u044F\u0437\u0438. \u0414\u0430\u043D\u043D\u044B\u0435 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u044B \u043D\u0430 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435." };
    try {
      const response = await fetch("./" + path, { method: body === void 0 ? "GET" : "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", "X-CRM-CSRF": csrf }, body: body === void 0 ? void 0 : JSON.stringify(body), signal: AbortSignal.timeout(45e3) });
      const data2 = await response.json();
      if (current?.owner !== startedOwner) return { ok: false, status: 401, text: "\u0412\u0445\u043E\u0434 \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u0441\u044F. \u0418\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u0440\u0430\u0431\u043E\u0442\u0430 \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430." };
      await revoke(response.status, startedOwner);
      if (response.status === 403 && data2.error === "csrf") {
        csrf = "";
        return { ok: false, status: 0, text: "\u041F\u043E\u0434\u043A\u043B\u044E\u0447\u0435\u043D\u0438\u0435 \u043E\u0431\u043D\u043E\u0432\u043B\u044F\u0435\u0442\u0441\u044F. \u0418\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044F \u043E\u0441\u0442\u0430\u0451\u0442\u0441\u044F \u0432 \u043E\u0447\u0435\u0440\u0435\u0434\u0438." };
      }
      return { ok: response.ok, status: response.status, data: data2, text: data2.text || data2.error };
    } catch {
      return { ok: false, status: 0, text: "\u041E\u0442\u0432\u0435\u0442 \u043D\u0435 \u043F\u043E\u043B\u0443\u0447\u0435\u043D. \u0418\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u044F \u0441\u043E\u0445\u0440\u0430\u043D\u0435\u043D\u0430 \u043D\u0430 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0435." };
    }
  }
  async function initialize() {
    if (!await get("device", "installation")) await put("device", "installation", crypto.randomUUID());
    const response = await request("boot");
    if (response.ok) {
      csrf = response.data.csrf;
      current = response.data.session || null;
      if (current) await put("device", "last-state", current);
      else await put("device", "last-state", null);
    } else if (response.status === 0) {
      const saved = await get("device", "last-state");
      current = saved && saved.expires_at > Date.now() ? saved : null;
    }
    if (current?.owner && response.ok && !document.hidden) await activity.start(current.owner, await get("device", "installation"));
    else if (!current) await activity.stop();
    startPolling();
    return current;
  }
  async function api(method, action3, body, query) {
    const owner = authority(), key2 = "read:" + action3 + ":" + JSON.stringify(query || {});
    let exportKey = "";
    if (method === "POST" && action3 === "export") {
      const { operation_id, ...selection } = body;
      exportKey = "export-intent:" + canonical(selection);
      const prior = await get(owner, exportKey);
      if (prior) body = prior;
      else await put(owner, exportKey, body);
    }
    const response = await request("api", { method, action: action3, body: body || {}, query: query || {} });
    if (exportKey) {
      if (response.ok) await put(owner, "export-key:" + response.data.manifest.export_id, exportKey);
      else if ([400, 409, 422].includes(response.status)) await put(owner, exportKey, null);
    }
    if (current?.owner !== owner) return { ok: false, status: 401, text: "\u0412\u0445\u043E\u0434 \u0438\u0437\u043C\u0435\u043D\u0438\u043B\u0441\u044F." };
    if (method === "GET" && response.ok) await put(owner, key2, { at: (/* @__PURE__ */ new Date()).toISOString(), data: response.data });
    if (method === "GET" && response.status === 0 && !["changes", "assignment-job", "assignment-receipt"].includes(action3)) {
      const saved = await get(owner, key2);
      if (saved) return { ok: true, status: 200, data: saved.data, stale: saved.at };
    }
    return response;
  }
  async function enqueue(action3, body, expected) {
    const owner = authority(expected);
    return persistEntry(owner, action3, body);
  }
  async function persistEntry(owner, action3, body) {
    if (!body?.operation_id) throw Error("\u041D\u0435 \u0443\u043A\u0430\u0437\u0430\u043D \u043D\u043E\u043C\u0435\u0440 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438.");
    await update(owner, "queue", (rows) => {
      rows ||= [];
      const prior = rows.find((r) => r.body.operation_id === body.operation_id);
      if (prior) {
        if (canonical(prior.body) !== canonical(body) || prior.action !== action3) throw Error("\u041D\u043E\u043C\u0435\u0440 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438 \u0443\u0436\u0435 \u0441\u0432\u044F\u0437\u0430\u043D \u0441 \u0434\u0440\u0443\u0433\u0438\u043C\u0438 \u0434\u0430\u043D\u043D\u044B\u043C\u0438.");
        return rows;
      }
      return [...rows, { action: action3, body, queued_at: (/* @__PURE__ */ new Date()).toISOString(), state: "pending" }];
    });
    if (current?.owner === owner) {
      emit({ type: "queue", owner }, true);
      background(flush());
    }
    return { ok: true, status: 202, data: { operation_id: body.operation_id, state: "pending" } };
  }
  async function command(body, expected) {
    const owner = authority(expected), entries = await get(owner, "queue") || [], old = entries.find((e) => e.body.operation_id === body.operation_id);
    if (old) {
      const original = { ...old.body };
      if (!body.clock && !body.occurred_at) {
        delete original.clock;
        delete original.occurred_at;
      }
      if (canonical(original) !== canonical(body)) throw Error("\u041D\u043E\u043C\u0435\u0440 \u043E\u043F\u0435\u0440\u0430\u0446\u0438\u0438 \u0443\u0436\u0435 \u0441\u0432\u044F\u0437\u0430\u043D \u0441 \u0434\u0440\u0443\u0433\u0438\u043C\u0438 \u0434\u0430\u043D\u043D\u044B\u043C\u0438.");
      background(flush());
      return { ok: true, status: 202, data: { operation_id: body.operation_id } };
    }
    const clock = activity.clock();
    return enqueue("command", body.clock || body.occurred_at ? body : clock ? { ...body, clock } : { ...body, occurred_at: (/* @__PURE__ */ new Date()).toISOString() }, owner);
  }
  async function flush() {
    if (flushing) return flushing;
    const run = async () => {
      if (!current?.owner || offline || !navigator.onLine) return;
      const owner = authority();
      if (!csrf) {
        await initialize();
        if (current?.owner !== owner || !csrf) return;
      }
      const rows = await get(owner, "queue") || [];
      for (const entry of rows.filter((e) => e.state === "pending")) {
        if (current?.owner !== owner) break;
        if (entry.action === "command" && entry.body.kind === "document") {
          const latest = await get(owner, "queue") || [];
          if (latest.some((e) => e.action === "document" && e.body.document_id === entry.body.payload.document_id && e.state !== "accepted")) continue;
        }
        const response = await api("POST", entry.action, entry.body);
        if (!response.status || response.status >= 500 || response.status === 401) break;
        await update(owner, "queue", (latest) => latest.map((e) => e.body.operation_id !== entry.body.operation_id ? e : { ...e, state: response.ok ? "accepted" : "attention", result: response.data, error: response.ok ? void 0 : response.text, code: response.data?.error }));
        emit({ type: "queue", owner }, true);
      }
      if (current?.owner === owner && !offline) await request("receipts", { action: "flush" });
    };
    flushing = (navigator.locks ? navigator.locks.request("crm-mobile-send", run) : run()).finally(() => {
      flushing = null;
    });
    return flushing;
  }
  async function queue(action3, id, expected) {
    const owner = authority(expected);
    if (action3 === "retry" || action3 === "review-registry") await update(owner, "queue", (rows) => (rows || []).map((e) => e.body.operation_id === id ? { ...e, state: action3 === "retry" ? "pending" : "attention", reviewed_at: action3 === "review-registry" ? (/* @__PURE__ */ new Date()).toISOString() : void 0 } : e));
    if (action3 === "retry") await request("receipts", { action: "retry", operation_id: id });
    if (action3 === "retry" || action3 === "flush") await flush();
    const entries = await get(owner, "queue") || [];
    const server = await request("receipts");
    if (server.ok) {
      await put(owner, "server-receipts", server.data.entries);
      entries.push(...server.data.entries);
    } else entries.push(...await get(owner, "server-receipts") || []);
    return { ok: true, status: 200, data: { entries: entries.map((e) => {
      if (e.action !== "document") return e;
      const { content_base64, ...body } = e.body;
      return { ...e, body };
    }), exports: (await get(owner, "download-jobs") || []).map(({ intentKey, ...job3 }) => job3) } };
  }
  function startPolling() {
    clearTimeout(liveTimer);
    liveTimer = setTimeout(async () => {
      if (!polling && current?.owner && !document.hidden) {
        polling = true;
        try {
          const owner = current.owner;
          await flush();
          if (current?.owner !== owner) return;
          const cursor = await get(owner, "live-cursor") || { assignments: 0, facts: 0 };
          const response = await request("api", { method: "GET", action: "changes", query: cursor });
          if (current?.owner !== owner) return;
          if (response.ok) {
            await put(owner, "live-cursor", response.data.cursor);
            if (response.data.dirty) {
              if (response.data.resync || response.data.cursor.assignments !== cursor.assignments || cursor.scope && response.data.cursor.scope !== cursor.scope) {
                await clearReads(owner);
                await api("GET", "work-portfolio");
              }
              const context2 = await request("api", { method: "GET", action: "context" });
              if (context2.ok && current?.owner === owner) {
                current.context = context2.data;
                await put("device", "last-state", current);
              }
            }
          }
          emit({ type: "live", connected: response.ok, dirty: !!response.data?.dirty, resync: !!response.data?.resync, as_of: response.data?.as_of || (/* @__PURE__ */ new Date()).toISOString() });
        } catch {
          emit({ type: "live", connected: false, dirty: false });
        } finally {
          polling = false;
        }
      }
      startPolling();
    }, document.hidden ? 15e3 : 2e3);
  }
  function chooseFile(accept = "", multiple = false, folder = false) {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = accept;
      input.multiple = multiple;
      input.hidden = true;
      document.body.append(input);
      if (folder) input.setAttribute("webkitdirectory", "");
      let settled = false;
      const finish = (files) => {
        if (settled) return;
        settled = true;
        input.remove();
        resolve(files);
      };
      input.onchange = () => finish(Array.from(input.files || []));
      input.addEventListener("cancel", () => finish([]));
      input.click();
    });
  }
  async function pickSource(kind2, expected) {
    authority(expected);
    return new Promise((resolve) => {
      const dialog = document.createElement("dialog");
      dialog.className = "mobile-source-picker";
      dialog.innerHTML = '<h2>\u0418\u0441\u0442\u043E\u0447\u043D\u0438\u043A</h2><div data-source-list></div><p data-source-status role="status"></p><div class="actions"><button data-source-phone class="primary">\u0417\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044C \u0441 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0430</button><button data-source-close>\u041E\u0442\u043C\u0435\u043D\u0430</button></div>';
      const list = dialog.querySelector("[data-source-list]"), status2 = dialog.querySelector("[data-source-status]");
      let settled = false;
      const changed = (event) => {
        if (event.type === "identity") finish("");
      };
      const finish = (value) => {
        if (settled) return;
        settled = true;
        listeners.delete(changed);
        dialog.close();
        dialog.remove();
        resolve(value);
      };
      listeners.add(changed);
      const load3 = async (parent) => {
        status2.textContent = "";
        const response = await request("sources?kind=" + kind2 + (parent ? "&parent=" + encodeURIComponent(parent) : ""));
        if (!response.ok) {
          status2.textContent = response.text || "\u041A\u043E\u0440\u043F\u043E\u0440\u0430\u0442\u0438\u0432\u043D\u044B\u0435 \u0438\u0441\u0442\u043E\u0447\u043D\u0438\u043A\u0438 \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B.";
          return;
        }
        list.replaceChildren();
        if (parent) {
          const back = document.createElement("button");
          back.textContent = "\u2190 \u0418\u0441\u0442\u043E\u0447\u043D\u0438\u043A\u0438";
          back.onclick = () => {
            void load3();
          };
          list.append(back);
        }
        if (parent && kind2 === "folder") {
          const choose = document.createElement("button");
          choose.textContent = "\u0412\u044B\u0431\u0440\u0430\u0442\u044C \u044D\u0442\u0443 \u043F\u0430\u043F\u043A\u0443";
          choose.className = "primary";
          choose.onclick = () => finish(parent);
          list.append(choose);
        }
        for (const entry of response.data.entries) {
          const button2 = document.createElement("button");
          button2.textContent = entry.name + (entry.directory ? "  \u203A" : "");
          button2.onclick = () => {
            if (entry.directory) void load3(entry.file);
            else finish(entry.file);
          };
          list.append(button2);
        }
        if (!parent && !response.data.entries.length) status2.textContent = "\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0444\u0430\u0439\u043B \u0438\u043B\u0438 \u043F\u0430\u043F\u043A\u0443 \u0441 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0430.";
      };
      dialog.querySelector("[data-source-phone]").onclick = async () => {
        const files = await chooseFile(kind2 === "folder" ? "" : ".xlsx,.xlsm,.xlsb,.xls,.csv,.txt", kind2 === "folder", kind2 === "folder");
        if (!files.length) return;
        const button2 = dialog.querySelector("[data-source-phone]");
        button2.disabled = true;
        try {
          authority(expected);
          if (files.length > 500 || files.reduce((n2, f) => n2 + f.size, 0) > 512 * 1024 * 1024) throw Error("\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u043E 500 \u0444\u0430\u0439\u043B\u043E\u0432 \u043E\u0431\u0449\u0438\u043C \u0440\u0430\u0437\u043C\u0435\u0440\u043E\u043C \u0434\u043E 512 \u041C\u0411.");
          const handles = [];
          for (const [i, file] of files.entries()) {
            status2.textContent = `\u0417\u0430\u0433\u0440\u0443\u0437\u043A\u0430 ${i + 1} \u0438\u0437 ${files.length} \xB7 ${file.name}`;
            const handle = await upload(file, expected);
            handles.push(kind2 === "folder" ? { file: handle, relative_path: file.webkitRelativePath?.split("/").slice(1).join("/") || file.name } : handle);
          }
          authority(expected);
          if (kind2 === "folder") {
            const response = await request("admin", { action: "folder", args: handles });
            if (!response.ok) throw Error(response.text);
            finish(response.data.file);
          } else finish(handles[0]);
        } catch (error) {
          status2.textContent = error.message;
          button2.disabled = false;
        }
      };
      dialog.querySelector("[data-source-close]").addEventListener("click", () => finish(""));
      dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        finish("");
      });
      document.body.append(dialog);
      dialog.showModal();
      void load3();
    });
  }
  async function upload(file, expected) {
    authority(expected);
    if (file.size > 64 * 1024 * 1024) throw Error("\u0424\u0430\u0439\u043B \u0431\u043E\u043B\u044C\u0448\u0435 64 \u041C\u0411.");
    const response = await fetch("./upload", { method: "POST", credentials: "same-origin", headers: { "X-CRM-CSRF": csrf, "X-CRM-Filename": encodeURIComponent(file.name) }, body: file });
    await revoke(response.status, expected);
    authority(expected);
    const value = await response.json();
    if (!response.ok) throw Error(value.text || "\u0424\u0430\u0439\u043B \u043D\u0435 \u0437\u0430\u0433\u0440\u0443\u0436\u0435\u043D.");
    return value.file;
  }
  async function download(response, name, expected = current?.owner) {
    authority(expected);
    await revoke(response.status, expected);
    if (!response.ok) {
      const value = await response.json();
      throw Error(value.text || "\u0424\u0430\u0439\u043B \u043D\u0435 \u043F\u043E\u0434\u0433\u043E\u0442\u043E\u0432\u043B\u0435\u043D.");
    }
    const blob = await response.blob();
    authority(expected);
    const url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url;
    a.download = name || decodeURIComponent(response.headers.get("X-CRM-Filename") || "\u0412\u044B\u0433\u0440\u0443\u0437\u043A\u0430.xlsx");
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 6e4);
    return { ok: true, status: 200, data: { downloaded: true } };
  }
  async function workbook(action3, input = {}) {
    const owner = authority(), resumed = action3 === "resume" ? (await get(owner, "download-jobs") || []).find((j) => j.operation_id === input.operation_id) : null;
    if (action3 === "resume" && !resumed) throw Error("\u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u0430\u044F \u0432\u044B\u0433\u0440\u0443\u0437\u043A\u0430 \u043D\u0435 \u043D\u0430\u0439\u0434\u0435\u043D\u0430 \u0443 \u044D\u0442\u043E\u0433\u043E \u0441\u043E\u0442\u0440\u0443\u0434\u043D\u0438\u043A\u0430.");
    if (resumed) action3 = resumed.action;
    const intentKey = resumed?.intentKey || "download-intent:" + action3 + ":" + JSON.stringify(input);
    const prior = action3 === "read" ? null : await get(owner, intentKey);
    let payload = prior?.input || { ...input };
    if (resumed && !prior) throw Error("\u0418\u0441\u0445\u043E\u0434\u043D\u0430\u044F \u0432\u044B\u0433\u0440\u0443\u0437\u043A\u0430 \u0443\u0436\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043D\u0430.");
    if (action3 === "read") {
      const [file] = await chooseFile(".xlsx,.xlsm,.xlsb,.xls");
      if (!file) return { ok: false, cancelled: true };
      payload.file = await upload(file, owner);
    }
    if (!prior && action3 === "write" && input.use_template) {
      const [file] = await chooseFile(".xlsx,.xlsm,.xlsb,.xls");
      if (!file) return { ok: false, cancelled: true };
      payload.template = await upload(file, owner);
    }
    const operation_id = prior?.operation_id || crypto.randomUUID();
    if (action3 !== "read" && !prior) await put(owner, intentKey, { operation_id, input: payload });
    if (action3 !== "read") await update(owner, "download-jobs", (jobs) => {
      jobs ||= [];
      return jobs.some((j) => j.operation_id === operation_id) ? jobs : [...jobs, { operation_id, action: action3, intentKey, completed: false, path: input.snapshot ? "\u0420\u0435\u0435\u0441\u0442\u0440 CRM \xB7 " + input.snapshot.manifest.period : input.name || "\u041E\u0442\u0447\u0451\u0442 CRM" }];
    });
    try {
      authority(owner);
      const response = await fetch("./workbook", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CRM-CSRF": csrf }, body: JSON.stringify({ action: action3, input: payload, operation_id }), signal: AbortSignal.timeout(18e4) });
      await revoke(response.status, owner);
      authority(owner);
      if (action3 === "read") {
        const value = await response.json();
        return { ok: response.ok, status: response.status, data: value, text: value.text };
      }
      if ([400, 409, 422].includes(response.status)) await put(owner, intentKey, null);
      const result2 = await download(response, void 0, owner);
      await put(owner, intentKey, null);
      await update(owner, "download-jobs", (jobs) => jobs.map((j) => j.operation_id === operation_id ? { ...j, completed: true, error: null } : j));
      if (payload.snapshot?.manifest?.export_id) {
        const key2 = await get(owner, "export-key:" + payload.snapshot.manifest.export_id);
        if (key2) await put(owner, key2, null);
      }
      return { ...result2, data: { ...result2.data, path: "\u0424\u0430\u0439\u043B \u0441\u043A\u0430\u0447\u0430\u043D", receipt_state: response.headers.get("X-CRM-Receipt-State") } };
    } catch (error) {
      if (action3 !== "read") await update(owner, "download-jobs", (jobs) => jobs.map((j) => j.operation_id === operation_id ? { ...j, error: "\u0421\u043A\u0430\u0447\u0438\u0432\u0430\u043D\u0438\u0435 \u043D\u0435 \u043F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u043E. \u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u0435 \u0438\u0441\u0445\u043E\u0434\u043D\u0443\u044E \u0432\u044B\u0433\u0440\u0443\u0437\u043A\u0443." } : j));
      throw error;
    }
  }
  async function phoneDocument(input) {
    const ext = input.name.split(".").at(-1)?.toLowerCase() || "", known = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", pdf: "application/pdf", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" };
    const type = input.type || known[ext] || "", file = new File([input], input.name, { type, lastModified: input.lastModified });
    if (!file.size) throw Error("\u0424\u0430\u0439\u043B \u043F\u0443\u0441\u0442. \u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0440\u0443\u0433\u043E\u0439 \u0434\u043E\u043A\u0443\u043C\u0435\u043D\u0442.");
    const image = type.startsWith("image/") || ["heic", "heif", "webp", "avif"].includes(ext);
    if (image && (file.size > 10 * 1024 * 1024 || !["image/jpeg", "image/png"].includes(type))) {
      const url = URL.createObjectURL(file), picture = new Image();
      try {
        picture.src = url;
        await picture.decode();
        const scale = Math.min(1, 2400 / Math.max(picture.naturalWidth, picture.naturalHeight)), canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(picture.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(picture.naturalHeight * scale));
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(picture, 0, 0, canvas.width, canvas.height);
        const jpeg = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
        if (!jpeg) throw Error();
        if (jpeg.size > 10 * 1024 * 1024) throw Error();
        return new File([jpeg], input.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" });
      } catch {
        throw Error("\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u0434\u0433\u043E\u0442\u043E\u0432\u0438\u0442\u044C \u0444\u043E\u0442\u043E. \u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 JPEG, PNG \u0438\u043B\u0438 PDF.");
      } finally {
        URL.revokeObjectURL(url);
      }
    }
    if (!Object.values(known).includes(type)) throw Error("\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u0438\u0437\u043E\u0431\u0440\u0430\u0436\u0435\u043D\u0438\u0435, PDF \u0438\u043B\u0438 DOCX.");
    if (file.size > 10 * 1024 * 1024) throw Error("\u0414\u043E\u043A\u0443\u043C\u0435\u043D\u0442 \u0431\u043E\u043B\u044C\u0448\u0435 10 \u041C\u0411.");
    return file;
  }
  async function documentFile(contract, id, expected) {
    const owner = authority(expected);
    if (id) {
      const response = await api("GET", "document-read", void 0, { document_id: id });
      if (!response.ok) return response;
      return download(new Response(Uint8Array.from(atob(response.data.content_base64), (c) => c.charCodeAt(0))), response.data.filename, owner);
    }
    const [selected2] = await chooseFile("image/*,.pdf,.docx");
    if (!selected2) return { ok: false, cancelled: true };
    const file = await phoneDocument(selected2);
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    const document_id = crypto.randomUUID(), checksum = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map((v) => v.toString(16).padStart(2, "0")).join("");
    await enqueue("document", { operation_id: document_id, kind: "attachment", document_id, contract_uuid: contract, filename: file.name, media_type: file.type || "application/octet-stream", content_base64: btoa(binary) }, owner);
    return { ok: true, status: 202, data: { document_id, sha256: checksum, filename: file.name } };
  }
  async function admin(action3, args, expected) {
    authority(expected);
    if (action3 === "copy") {
      await navigator.clipboard.writeText(String(args[0]));
      return true;
    }
    if (action3 === "pick") {
      const [kind2, opts = {}] = args;
      if (kind2 === "save") return await request("admin", { action: "reserve", args: [opts.defaultPath || "\u0412\u044B\u0433\u0440\u0443\u0437\u043A\u0430.xlsx"] }).then((r) => r.data?.file || "");
      return pickSource(kind2, expected);
    }
    if (action3 === "saveText") {
      return download(new Response(String(args[1]), { headers: { "Content-Type": "text/plain;charset=utf-8" } }), String(args[0]));
    }
    if (action3 === "xlsx") return workbook("table", { name: args[0], headers: args[1], rows: args[2] });
    if (action3 === "open") {
      return download(await fetch("./file/" + encodeURIComponent(args[0]), { credentials: "same-origin" }), void 0, expected);
    }
    if (action3 === "autostart") return false;
    if (action3 === "state") return { mode: "analyst", productId: "crm", fixedProduct: true, product: "\u0410\u043D\u0430\u043B\u0438\u0442\u0438\u043A", version: "3.3.0-mobile.1", platform: "mobile" };
    const response = await request("admin", { action: action3, args });
    return response.ok ? response.data : { ok: false, error: response.text, text: response.text };
  }
  var bridge = {
    platform: "mobile",
    ready: initialize,
    navigated: (route) => {
      if (navigating || !current?.owner) return;
      const next = { ...route, owner: current.owner };
      if (canonical(history.state?.crmMobile) === canonical(next)) return;
      const method = history.state?.crmMobile?.owner === current.owner ? "pushState" : "replaceState";
      history[method]({ crmMobile: next }, "", location.pathname + "#" + route.section + (route.person ? "/" + route.person : ""));
    },
    onNavigate: (handler) => {
      routeHandler = handler;
    },
    initialSection: () => {
      const target = navigationTarget;
      navigationTarget = "";
      initialPerson = "";
      const route = history.state?.crmMobile;
      if (!target && route && current?.owner && route.owner === current.owner) {
        navigating = true;
        initialPerson = route.person || "";
        return route.section;
      }
      return target;
    },
    initialPerson: () => {
      navigating = false;
      const person = initialPerson;
      initialPerson = "";
      return person;
    },
    state: async () => ({
      owner: current?.owner || "",
      context: current?.context || null,
      version: "3.3.0-mobile.1",
      product: "\u0412\u0437\u044B\u0441\u043A\u0430\u043D\u0438\u0435",
      offline: offline || !navigator.onLine,
      connection: { mode: current?.synthetic ? "synthetic" : current ? "mobile" : "unconfigured", role: current?.context?.role || "employee", server: location.origin, shared: !current?.synthetic },
      existingLogins: [],
      activity: activity.state()
    }),
    api,
    command,
    registryEdit: (body, owner) => enqueue("registry-edit", body, owner),
    queue,
    draft: async (name, value, expected) => {
      if (!current?.owner && value === void 0) return { ok: true, data: null };
      const owner = authority(expected);
      return { ok: true, data: value === void 0 ? await get(owner, "draft:" + name) : await put(owner, "draft:" + name, value) };
    },
    onChange: (fn2) => {
      listeners.add(fn2);
      return () => listeners.delete(fn2);
    },
    telemetry: async (event) => current ? api("POST", "telemetry", event) : { ok: true },
    connect: async (input) => {
      if (input.action === "refresh") {
        const before = current?.owner;
        await initialize();
        if (current?.owner && before !== current.owner) {
          navigationTarget = current.context.role === "analyst" ? "registry" : "portfolio";
          emit({ type: "identity", owner: current.owner }, true);
        }
        return { ok: true };
      }
      if (input.action === "offline") {
        offline = !!input.value;
        if (!offline) background(flush());
        return { ok: true };
      }
      const installation = await get("device", "installation");
      const device_id = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(installation)))).map((v) => v.toString(16).padStart(2, "0")).join("");
      const response = await request(input.action === "logout" ? "logout" : input.action === "password" ? "password" : "login", { ...input, device_id, device_label: navigator.userAgent.slice(0, 120) });
      if (response.ok) {
        await initialize();
        navigationTarget = current?.context?.role === "analyst" ? "registry" : current ? "portfolio" : "connection";
        emit({ type: "identity", owner: current?.owner }, true);
        background(flush());
      }
      return response;
    },
    workbook,
    document: documentFile,
    admin,
    win: () => {
    },
    diagnostics: async () => ({ ok: true, data: { checks: [{ title: "\u0425\u0440\u0430\u043D\u0438\u043B\u0438\u0449\u0435 \u0442\u0435\u043B\u0435\u0444\u043E\u043D\u0430", code: "storage_ready", ok: !!await get("device", "installation") }, { title: "\u0421\u0432\u044F\u0437\u044C", code: navigator.onLine ? "network_ready" : "offline", ok: navigator.onLine }] } }),
    renderConnection: async (host) => mountConnection(host, bridge, () => request("boot"))
  };
  window.addEventListener("online", () => {
    background(initialize().then(() => flush()));
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) background(initialize().then(() => flush()));
  });

  // hard-desk/ui/mobile/shell.ts
  var paths = { portfolio: "M4 6h16v14H4z M8 6V3h8v3 M4 11h16", registry: "M5 3h14v18H5z M9 8h6 M9 12h6 M9 16h4", statistics: "M4 19h16 M7 15V9 M12 15V4 M17 15v-7", team: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M15 4a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0", exchange: "M4 7h15l-4-4 M20 17H5l4 4 M19 7l-4 4 M5 17l4-4", connection: "M8 5h8v14H8z M11 16h2", tasks: "M9 5h11 M9 12h11 M9 19h11 M3 5h1 M3 12h1 M3 19h1", script: "M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h4", "active-time": "M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0", queue: "M4 12h5l3 4 3-4h5 M6 4h12l3 8v8H3v-8z" };
  paths.cabinet = paths.statistics;
  paths["manager-statistics"] = paths.statistics;
  var icon = (id) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[id] || "M5 7h14 M5 12h14 M5 17h14"}"/></svg>`;
  function mobileShell() {
    document.body.classList.add("mobile-crm");
    const nav = document.querySelector("#navigation"), view = document.querySelector("#view");
    const expandedActions = /* @__PURE__ */ new Map();
    const decorate = () => {
      const buttons = Array.from(nav.querySelectorAll("[data-section]"));
      buttons.forEach((b, i) => {
        if (!b.dataset.mobileLabel) {
          b.dataset.mobileLabel = b.textContent || "";
          b.innerHTML = icon(b.dataset.section) + `<span>${b.textContent}</span>`;
        }
        b.dataset.overflow = String(i > 3);
      });
      let more = nav.querySelector("#mobile-more");
      if (buttons.length > 4 && !more) {
        more = document.createElement("button");
        more.id = "mobile-more";
        more.setAttribute("aria-label", "\u0412\u0441\u0435 \u0440\u0430\u0437\u0434\u0435\u043B\u044B");
        more.innerHTML = icon("more") + "<span>\u0415\u0449\u0451</span>";
        more.onclick = () => menu(buttons);
        nav.append(more);
      }
      if (buttons.length <= 4) more?.remove();
      more?.classList.toggle("active", buttons.slice(4).some((b) => b.classList.contains("active")));
    };
    function menu(buttons) {
      const d = document.createElement("dialog");
      d.className = "mobile-menu";
      const h = document.createElement("h2");
      h.textContent = "\u0420\u0430\u0431\u043E\u0447\u0435\u0435 \u043C\u0435\u0441\u0442\u043E";
      d.append(h);
      buttons.forEach((source) => {
        const b = document.createElement("button");
        b.innerHTML = icon(source.dataset.section) + `<span>${source.dataset.mobileLabel}</span><span aria-hidden="true">\u2197</span>`;
        b.onclick = () => {
          d.close();
          source.click();
        };
        d.append(b);
      });
      const close = document.createElement("button");
      close.textContent = "\u0417\u0430\u043A\u0440\u044B\u0442\u044C";
      close.className = "mobile-menu-close";
      close.onclick = () => d.close();
      d.append(close);
      d.onclose = () => d.remove();
      document.body.append(d);
      d.showModal();
    }
    new MutationObserver((records) => {
      if (records.some((r) => r.target.id !== "mobile-more")) decorate();
    }).observe(nav, { childList: true, attributes: true, attributeFilter: ["class"], subtree: true });
    const adapt = () => {
      const accountSearch = view.querySelector("#account-search"), intro = accountSearch?.parentElement?.querySelector(":scope > p.muted");
      if (intro) {
        const help2 = document.createElement("details");
        help2.className = "mobile-inline-help";
        help2.innerHTML = "<summary>\u041F\u043E\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043D\u0438\u0435 \u0434\u043E\u0441\u0442\u0443\u043F\u0430</summary>";
        intro.before(help2);
        help2.append(intro);
      }
      const registry = view.querySelector(".registry");
      const cardBar = view.querySelector(":scope > .toolbar:has([data-action])");
      if (cardBar && !cardBar.dataset.mobileReady) {
        cardBar.dataset.mobileReady = "1";
        cardBar.classList.add("mobile-card-actions");
        const secondary = Array.from(cardBar.querySelectorAll("[data-action]")).filter((b) => !["call", "visit", "delivery"].includes(b.dataset.action));
        if (secondary.length) {
          const key2 = history.state?.crmMobile?.owner + ":" + history.state?.crmMobile?.person, more = document.createElement("details");
          more.className = "mobile-card-more";
          more.innerHTML = '<summary>\u0415\u0449\u0451 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044F <span aria-hidden="true">\uFF0B</span></summary><div></div>';
          more.open = !!expandedActions.get(key2);
          more.addEventListener("toggle", () => expandedActions.set(key2, more.open));
          secondary.forEach((b) => more.lastElementChild.append(b));
          cardBar.append(more);
        }
        const refresh2 = cardBar.querySelector("#card-refresh");
        if (refresh2) {
          refresh2.textContent = "\u21BB";
          refresh2.setAttribute("aria-label", "\u041E\u0431\u043D\u043E\u0432\u0438\u0442\u044C \u043A\u0430\u0440\u0442\u043E\u0447\u043A\u0443");
        }
      }
      if (registry && !registry.dataset.mobileReady) {
        registry.dataset.mobileReady = "1";
        const tools = document.createElement("details");
        tools.className = "mobile-registry-tools";
        tools.innerHTML = '<summary>\u0424\u0438\u043B\u044C\u0442\u0440\u044B \u0438 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044F<span aria-hidden="true">\uFF0B</span></summary><div class="mobile-tools-body"></div>';
        const body = tools.lastElementChild, bars = Array.from(registry.querySelectorAll(":scope > .toolbar")), search = bars[0];
        search?.classList.add("mobile-registry-search");
        search?.after(tools);
        search?.querySelectorAll("select,label").forEach((el) => {
          if (!el.closest("label") || el.tagName === "LABEL") body.append(el);
        });
        for (const bar of bars.slice(1, -1)) body.append(bar);
        const allocation = registry.querySelector("#registry-allocation");
        if (allocation) body.append(allocation);
        bars.at(-1)?.classList.add("mobile-registry-pagination");
        const save = registry.querySelector("#registry-save");
        if (save) {
          const strip2 = document.createElement("div");
          strip2.className = "mobile-save-strip";
          strip2.append(save);
          tools.after(strip2);
        }
      }
      view.querySelectorAll("table:not(.registry-grid):not(.manager-report-table)").forEach((t) => {
        if (t.querySelector("[data-person]")) {
          t.classList.add("mobile-portfolio-table");
          t.classList.toggle("mobile-manager-portfolio", !t.querySelector("[data-work-contract]"));
        }
        const headers = Array.from(t.querySelectorAll("thead th")).map((h) => h.textContent || "");
        t.querySelectorAll("tbody tr").forEach((r) => Array.from(r.children).forEach((c, i) => {
          if (!c.hasAttribute("data-label")) c.setAttribute("data-label", headers[i] || "");
        }));
      });
      const text2 = document.querySelector("#registry-status")?.textContent || "", strip = view.querySelector(".mobile-save-strip");
      if (strip) strip.hidden = !text2 || /черновике:\s*0(?:\D|$)/.test(text2);
    };
    let scheduled = false;
    new MutationObserver(() => {
      if (!scheduled) {
        scheduled = true;
        requestAnimationFrame(() => {
          scheduled = false;
          adapt();
        });
      }
    }).observe(view, { childList: true, subtree: true });
    decorate();
    adapt();
    const viewport = window.visualViewport;
    viewport?.addEventListener("resize", () => document.body.classList.toggle("mobile-keyboard", !!viewport && viewport.height < window.innerHeight * 0.72));
    document.addEventListener("click", (e) => {
      if (e.target.closest("[data-section]")) document.querySelector("main")?.scrollTo(0, 0);
    });
    if ("serviceWorker" in navigator) void navigator.serviceWorker.register("./sw.js", { scope: "./" });
  }

  // hard-desk/ui/mobile/main.ts
  window.crm = bridge;
  mobileShell();
  void Promise.resolve().then(() => (init_crm_app(), crm_app_exports));
})();
