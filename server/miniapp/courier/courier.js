"use strict";
(() => {
  // ui/crm-delivery.ts
  var esc = (value) => String(value ?? "").replace(
    /[&<>"']/g,
    (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]
  );
  async function mountDeliveryForm(host, initial, api) {
    let contracts = [...initial];
    const draft = async (key2, ...value) => {
      const result = await api.draft(key2, ...value);
      if (result?.ok !== true) throw new Error(result?.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0447\u0435\u0440\u043D\u043E\u0432\u0438\u043A \u043D\u0430 \u0443\u0441\u0442\u0440\u043E\u0439\u0441\u0442\u0432\u0435.");
      return result;
    };
    const key = "courier:delivery", saved = (await draft(key)).data || {};
    host.innerHTML = `<form data-courier-form><div class="field"><label for="courier-contract">\u041D\u043E\u043C\u0435\u0440 \u041A\u0414</label>${api.search ? '<input id="courier-search" type="search" placeholder="\u041D\u0430\u0439\u0442\u0438 \u041A\u0414 \u0438\u043B\u0438 \u043A\u043B\u0438\u0435\u043D\u0442\u0430">' : ""}<select id="courier-contract" required></select><output id="courier-name"></output></div><div class="field"><label for="courier-address">\u0410\u0434\u0440\u0435\u0441 \u043F\u0435\u0440\u0435\u0434\u0430\u0447\u0438 \u043A\u043E\u043D\u0432\u0435\u0440\u0442\u0430</label><input id="courier-address" list="courier-addresses" autocomplete="street-address"><datalist id="courier-addresses"></datalist><small>\u0415\u0441\u043B\u0438 \u043A\u043E\u043D\u0432\u0435\u0440\u0442 \u043D\u0435 \u043F\u0435\u0440\u0435\u0434\u0430\u043D, \u043E\u0441\u0442\u0430\u0432\u044C\u0442\u0435 \u0430\u0434\u0440\u0435\u0441 \u043F\u0443\u0441\u0442\u044B\u043C</small></div><div class="field"><label for="courier-phone">\u0422\u0435\u043B\u0435\u0444\u043E\u043D \u0438\u043B\u0438 \u043F\u0440\u043E\u0447\u0435\u0440\u043A</label><input id="courier-phone" type="tel" maxlength="100" placeholder="\u2014"></div><div class="field"><label for="courier-comment">\u041A\u043E\u043C\u0443 \u043F\u0435\u0440\u0435\u0434\u0430\u043D \u043A\u043E\u043D\u0432\u0435\u0440\u0442 \u0438\u043B\u0438 \u043F\u043E\u0447\u0435\u043C\u0443 \u043F\u0435\u0440\u0435\u0434\u0430\u0442\u044C \u043D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C</label><textarea id="courier-comment" required maxlength="4000"></textarea></div><p data-courier-status role="status"></p><div class="actions">${api.close ? '<button type="button" data-courier-close>\u0417\u0430\u043A\u0440\u044B\u0442\u044C</button>' : ""}<button type="submit" class="primary">\u0421\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C</button></div></form>`;
    const find = (selector) => host.querySelector(selector);
    const select = find("#courier-contract"), address = find("#courier-address");
    const info = document.createElement("details");
    info.className = "courier-client-info";
    find("#courier-name").after(info);
    const phone = find("#courier-phone"), comment = find("#courier-comment");
    const status = find("[data-courier-status]"), save = find("button[type=submit]");
    const chosen = () => contracts.find((row) => row.contract_uuid === select.value);
    const capture = () => ({ contract_uuid: select.value, address: address.value, phone: phone.value, comment: comment.value });
    let draftWrites = Promise.resolve();
    const persist = () => {
      const value = capture();
      draftWrites = draftWrites.catch(() => {
      }).then(async () => {
        if (value.contract_uuid) await draft(key + ":" + value.contract_uuid, value);
        await draft(key, value);
      });
      return draftWrites;
    };
    const paint = () => {
      const selected = chosen();
      find("#courier-name").textContent = selected?.client_name || selected?.fields?.client || "";
      const fields = selected?.fields || {};
      find("#courier-addresses").innerHTML = Array.from(new Set([fields.address, fields.address_customer, fields.extra_address].filter(Boolean))).map((value) => `<option value="${esc(value)}"></option>`).join("");
      info.innerHTML = selected ? "<summary>\u0421\u0432\u0435\u0434\u0435\u043D\u0438\u044F \u043E \u043A\u043B\u0438\u0435\u043D\u0442\u0435</summary><dl>" + (api.fields || []).filter((field) => fields[field.key] !== void 0 && fields[field.key] !== null && fields[field.key] !== "").map((field) => `<dt>${esc(field.title)}</dt><dd>${esc(fields[field.key])}</dd>`).join("") + "</dl>" : "";
      const writable = selected && Array.isArray(selected.allowed_actions) && selected.allowed_actions.includes("delivery");
      save.disabled = !writable;
      status.textContent = selected && !writable ? "\u0414\u0430\u043D\u043D\u044B\u0435 \u0434\u043E\u0441\u0442\u0443\u043F\u043D\u044B \u0434\u043B\u044F \u043F\u0440\u043E\u0441\u043C\u043E\u0442\u0440\u0430. \u041F\u0435\u0440\u0435\u0434\u0430\u0447\u0430 \u043A\u043E\u043D\u0432\u0435\u0440\u0442\u0430 \u043D\u0435 \u043D\u0430\u0437\u043D\u0430\u0447\u0435\u043D\u0430." : "";
    };
    const options = (preferred) => {
      select.innerHTML = '<option value="">\u0412\u044B\u0431\u0435\u0440\u0438\u0442\u0435 \u041A\u0414</option>' + contracts.map((row) => `<option value="${esc(row.contract_uuid)}">${esc(row.external_id)} \xB7 ${esc(row.creditor_id)}</option>`).join("");
      select.value = preferred || "";
      paint();
    };
    options(saved.contract_uuid);
    address.value = saved.address || "";
    phone.value = saved.phone ?? "-";
    comment.value = saved.comment || "";
    let activeContract = select.value, selectionGeneration = 0;
    select.addEventListener("change", async () => {
      const selected = select.value, current = ++selectionGeneration;
      const previous = { contract_uuid: activeContract, address: address.value, phone: phone.value, comment: comment.value };
      const controls = Array.from(host.querySelectorAll("input,select,textarea,button")).filter((control) => control !== save).map((control) => ({ control, disabled: control.disabled }));
      controls.forEach(({ control }) => control.disabled = true);
      save.disabled = true;
      try {
        await draftWrites.catch(() => {
        });
        if (activeContract) await draft(key + ":" + activeContract, previous);
        const restored = selected ? (await draft(key + ":" + selected)).data : null;
        if (current !== selectionGeneration || !host.isConnected) return;
        activeContract = selected;
        address.value = restored?.address || "";
        comment.value = restored?.comment || "";
        phone.value = restored?.phone ?? chosen()?.fields?.phone ?? "-";
        paint();
        await persist();
      } catch (error) {
        select.value = activeContract;
        paint();
        status.textContent = error.message;
      } finally {
        controls.forEach(({ control, disabled }) => control.disabled = disabled);
      }
    });
    for (const input of [address, phone, comment]) input.addEventListener("input", () => {
      void persist().catch((error) => {
        status.textContent = error.message;
      });
    });
    const close = host.querySelector("[data-courier-close]");
    if (close) close.onclick = () => {
      void persist().then(() => api.close?.()).catch((error) => {
        status.textContent = error.message;
      });
    };
    const search = host.querySelector("#courier-search");
    let generation = 0, timer;
    if (search) search.oninput = () => {
      const current = ++generation;
      clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          const rows = await api.search(search.value);
          if (current !== generation || !host.isConnected) return;
          const previous = chosen();
          contracts = rows;
          if (previous && !contracts.some((row) => row.contract_uuid === previous.contract_uuid)) contracts.unshift(previous);
          options(previous?.contract_uuid);
        } catch {
          if (current === generation) status.textContent = "\u041F\u043E\u0438\u0441\u043A \u043D\u0435\u0434\u043E\u0441\u0442\u0443\u043F\u0435\u043D. \u0421\u043E\u0445\u0440\u0430\u043D\u0451\u043D\u043D\u044B\u0435 \u041A\u0414 \u043E\u0441\u0442\u0430\u044E\u0442\u0441\u044F \u0432 \u0441\u043F\u0438\u0441\u043A\u0435.";
        }
      }, 250);
    };
    find("form").onsubmit = async (event) => {
      event.preventDefault();
      if (save.disabled) return;
      const selected = chosen();
      if (!selected) return;
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
          contract_ids: [selected.contract_uuid],
          base_revisions: {},
          payload: {
            delivered: !!address.value.trim(),
            delivery_address: address.value.trim(),
            delivery_phone: phone.value.trim() || "-",
            delivery_comment: comment.value.trim()
          }
        };
        const recordKey = key + ":" + selected.contract_uuid;
        const content = JSON.stringify(raw), pending = (await draft(recordKey + ":pending")).data;
        const operation_id = pending?.content === content ? pending.operation_id : crypto.randomUUID();
        await draft(recordKey + ":pending", { content, operation_id });
        const result = await api.command({ ...raw, operation_id });
        if (!result.ok) throw new Error(result.text || "\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0441\u043E\u0445\u0440\u0430\u043D\u0438\u0442\u044C \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442.");
        await draft(recordKey, null);
        await draft(key, null);
        await draft(recordKey + ":pending", null);
        comment.value = "";
        address.value = "";
        phone.value = "-";
        select.value = "";
        activeContract = "";
        paint();
        status.textContent = "\u0420\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442 \u0441\u043E\u0445\u0440\u0430\u043D\u0451\u043D";
      } catch (error) {
        paint();
        status.textContent = error.message;
      }
    };
  }

  // ui/crm-delivery-browser.ts
  window.CRMDelivery = { mount: mountDeliveryForm };
})();
