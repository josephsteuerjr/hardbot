type API = {
  state(): Promise<any>;
  api(method: string, action: string, body?: any, query?: any): Promise<any>;
  command(body: any): Promise<any>;
  queue(action?: string, id?: string): Promise<any>;
  draft(key: string, value?: any): Promise<any>;
  telemetry(event: any): Promise<any>;
  connect(input: any): Promise<any>;
  workbook(action: string, input?: any): Promise<any>;
  document(contract: string, id?: string): Promise<any>;
  win(action: string): void;
};
const crm = (window as any).crm as API;
const $ = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.getElementById(id) as T;
const esc = (value: any) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
const date = (value: any) =>
  value ? new Date(value).toLocaleString("ru-RU") : "—";
const amount = (value: any, currency = "") =>
  value == null || value === ""
    ? "Не указано"
    : esc(value) + (currency ? " " + esc(currency) : "");
const labels: Record<string, string> = {
  call: "Звонок",
  visit: "Выезд",
  note: "Заметка",
  contact: "Контакты",
  promise: "Обещание",
  task: "Задача",
  reported_payment: "Заявленная оплата",
  payment_confirmed: "Подтверждённое поступление",
  document: "Документ",
  correction: "Исправление",
  manual_edit: "Ручная правка Excel",
  task_complete: "Завершение задачи",
  script_publish: "Публикация скрипта",
};
let state: any,
  context: any,
  section = "portfolio",
  card: any,
  tab = "overview",
  scriptOpened: any,
  renderGeneration = 0;
const notice = (text = "") => {
  $("notice").textContent = text;
};
const telemetry = (event_type: string, feature: string, extra: any = {}) => {
  void crm.telemetry({ event_type, feature, ...extra }).catch(() => {});
};
async function request(
  method: string,
  action: string,
  body?: any,
  query?: any,
) {
  const generation = renderGeneration;
  const result = await crm.api(method, action, body, query);
  if (generation !== renderGeneration)
    throw new DOMException("Экран изменился.", "AbortError");
  if (!result.ok)
    throw new Error(
      result.text || result.data?.text || "Не удалось выполнить запрос.",
    );
  if (result.stale)
    $("connection").textContent = "Сохранённая копия · " + date(result.stale);
  return result.data;
}
function can(cap: string) {
  return context?.owner || context?.capabilities?.includes(cap);
}
function button(id: string, title: string, primary = false) {
  return `<button id="${id}"${primary ? ' class="primary"' : ""}>${esc(title)}</button>`;
}
function bind(id: string, fn: () => any) {
  $(id)?.addEventListener("click", () => {
    notice();
    Promise.resolve()
      .then(fn)
      .catch((e) => notice(e.message));
  });
}
const table = (heads: string[], rows: string[]) =>
  `<div class="table-wrap"><table><thead><tr>${heads.map((x) => `<th>${esc(x)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
async function boot() {
  state = await crm.state();
  context = state.context;
  $("identity").textContent =
    context?.role === "manager"
      ? "Руководитель"
      : context?.role === "observer"
        ? "Наблюдатель"
        : "Сотрудник";
  $("version").textContent = state.version;
  $("connection").textContent =
    (state.connection.mode === "synthetic"
      ? "Синтетические данные"
      : state.connection.server) + (state.offline ? " · офлайн" : "");
  const sections = [
    ["portfolio", "Портфель"],
    ["shift", "Смена"],
    ["script", "Скрипт"],
    ["queue", "Очередь"],
    ...(can("exchange") ? [["exchange", "Реестры"]] : []),
    ...(can("analytics") ? [["adoption", "Внедрение"]] : []),
    ["connection", "Подключение"],
    ["help", "Справка"],
  ];
  $("navigation").innerHTML = sections
    .map(
      ([id, label]) =>
        `<button data-section="${id}" class="${section === id ? "active" : ""}">${label}</button>`,
    )
    .join("");
  document
    .querySelectorAll<HTMLButtonElement>("[data-section]")
    .forEach((b) => (b.onclick = () => void go(b.dataset.section!)));
  await go(context ? section : "connection");
  telemetry("client_ready", "shell", {
    duration_ms: Math.round(performance.now()),
  });
}
async function go(next: string) {
  renderGeneration++;
  section = next;
  notice();
  $("title").textContent = (
    {
      portfolio: "Портфель",
      shift: "Смена",
      script: "Скрипт звонка",
      queue: "Очередь",
      exchange: "Реестры",
      adoption: "Внедрение",
      connection: "Подключение",
      help: "Справка",
    } as any
  )[next];
  document
    .querySelectorAll("[data-section]")
    .forEach((b) =>
      b.classList.toggle("active", (b as HTMLElement).dataset.section === next),
    );
  telemetry(
    "feature_opened",
    (
      {
        queue: "shell",
        connection: "shell",
        help: "shell",
        shift: "shift",
        script: "script",
        exchange: "exchange",
        adoption: "adoption",
        portfolio: "portfolio",
      } as any
    )[next],
  );
  try {
    await (
      {
        portfolio,
        shift,
        script,
        queue: queuePage,
        exchange: exchangePage,
        adoption,
        connection,
        help,
      } as any
    )[next]();
  } catch (e) {
    if ((e as Error).name !== "AbortError") notice((e as Error).message);
  }
}

async function portfolio(search = "") {
  const data = await request("GET", "portfolio", undefined, {
    search,
    limit: 500,
  });
  $("view").innerHTML =
    `<div class="toolbar"><input type="search" id="search" placeholder="Имя, договор, телефон, адрес" value="${esc(search)}">${button("search-go", "Найти")}${button("refresh", "Обновить")}</div><div class="muted">Клиентов: ${data.total_people} · Договоров: ${data.total_contracts}${data.truncated ? " · Уточните поиск" : ""}</div>` +
    table(
      ["Клиент", "Договоры", "Остаток по валютам"],
      data.people.map(
        (p: any) =>
          `<tr><td><button class="link" data-person="${esc(p.person_uuid)}">${esc(p.name || "Без имени")}</button></td><td>${p.contracts}</td><td>${
            Object.keys(p.currencies || {}).length
              ? Object.entries(p.currencies)
                  .map(([k, v]) => amount(v, k))
                  .join("<br>")
              : "Откройте договоры"
          }</td></tr>`,
      ),
    );
  bind("search-go", () => portfolio($<HTMLInputElement>("search").value));
  bind("refresh", () => portfolio());
  $<HTMLInputElement>("search").onkeydown = (e) => {
    if (e.key === "Enter") void portfolio($<HTMLInputElement>("search").value);
  };
  document
    .querySelectorAll<HTMLButtonElement>("[data-person]")
    .forEach((b) => (b.onclick = () => void openCard(b.dataset.person!)));
}

async function openCard(person: string) {
  try {
    card = await request("GET", "card", undefined, { person_uuid: person });
    tab = "overview";
    telemetry("feature_opened", "card");
    await renderCard();
  } catch (e) {
    notice((e as Error).message);
  }
}
async function renderCard() {
  $("title").textContent = card.person.name || "Карточка клиента";
  const actions = [
    "call",
    "visit",
    "note",
    "contact",
    "promise",
    "task",
    "reported_payment",
    "document",
  ].filter(can);
  const tabs = [
    ["overview", "Обзор"],
    ["contracts", "Договоры"],
    ["history", "История"],
    ["tasks", "Задачи"],
    ["promises", "Обещания"],
    ["payments", "Оплаты"],
    ["documents", "Документы"],
  ];
  $("view").innerHTML =
    `<div class="toolbar">${button("back", "← Портфель")}${actions.map((k) => `<button data-action="${k}"${k === "call" ? ' class="primary"' : ""}>${labels[k]}</button>`).join("")}</div><div class="tabs">${tabs.map(([id, title]) => `<button data-tab="${id}" class="${tab === id ? "active" : ""}">${title}</button>`).join("")}</div><div id="card-content"></div>`;
  bind("back", () => portfolio());
  document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        tab = b.dataset.tab!;
        void renderCard();
      }),
  );
  document
    .querySelectorAll<HTMLButtonElement>("[data-action]")
    .forEach((b) => (b.onclick = () => void action(b.dataset.action!)));
  const host = $("card-content");
  if (tab === "overview" || tab === "contracts") {
    host.innerHTML = table(
      ["Договор / кредитор", "Контакты", "Остаток", "Последний результат"],
      card.contracts.map(
        (r: any) =>
          `<tr><td><b>${esc(r.external_id)}</b><br><span class="muted">${esc(r.creditor_id)}</span></td><td>${esc(r.fields.phone || r.fields.phone_main || "—")}<br>${esc(r.fields.address || "—")}</td><td class="amount">${amount(r.fields.debt_today ?? r.fields.debt_total, r.currency || "Валюта не задана")}</td><td>${esc(r.fields.soft_call_status || r.fields.call_status || "—")}<br><span class="muted">${esc(r.fields.soft_comment || r.fields.visit_comment || "")}</span></td></tr>`,
      ),
    );
    if (tab === "overview")
      host.innerHTML += `<div class="summary"><div class="metric">Договоры<strong>${card.contracts.length}</strong></div><div class="metric">Открытые задачи<strong>${card.entities.filter((e: any) => e.kind === "task" && e.status === "open").length}</strong></div><div class="metric">Заявленные оплаты<strong>${card.entities.filter((e: any) => e.kind === "reported_payment").length}</strong><small>Подтверждение поступления хранится отдельно</small></div></div>`;
  } else if (tab === "history") {
    host.innerHTML = card.history.length
      ? card.history
          .map(
            (e: any) =>
              `<article class="record"><h3>${esc(labels[e.kind] || e.kind)} <span class="tag">${esc(e.origin)}</span></h3><div class="muted">${date(e.time_quality === "legacy_unknown" ? e.payload.legacy_created_at : e.received_at)} · ${esc(e.author || "Автор не указан")} · ${e.contract_ids.length} дог. ${e.projection_status === "needs_review" ? "· Требует сверки" : ""}</div><p>${esc(
                e.payload.text ||
                  e.payload.title ||
                  Object.entries(e.payload.values || {})
                    .map(
                      ([k, v]) =>
                        (context.fields.find((f: any) => f.key === k)?.title ||
                          k) +
                        ": " +
                        (v ?? "Очищено"),
                    )
                    .join("\n"),
              )}</p>${e.reason ? `<p>Причина: ${esc(e.reason)}</p>` : ""}${["call", "visit", "note", "contact"].includes(e.kind) && can("correct") ? `<button data-correct="${e.event_id}">Исправить</button>` : ""}</article>`,
          )
          .join("")
      : '<div class="empty">История пока пуста</div>';
    document.querySelectorAll<HTMLButtonElement>("[data-correct]").forEach(
      (b) =>
        (b.onclick = () =>
          void action(
            "correction",
            card.history.find((e: any) => e.event_id === b.dataset.correct),
          )),
    );
  } else {
    const kinds = (
      {
        tasks: ["task"],
        promises: ["promise"],
        payments: ["reported_payment", "payment_confirmed"],
        documents: ["document"],
      } as any
    )[tab];
    const rows = card.entities.filter((e: any) => kinds.includes(e.kind));
    host.innerHTML = rows.length
      ? table(
          ["Вид", "Содержание", "Срок / сумма", "Состояние"],
          rows.map(
            (e: any) =>
              `<tr><td>${esc(labels[e.kind])}</td><td>${esc(e.data.title || e.data.filename || e.data.text || "—")}</td><td>${e.data.amount != null ? amount(e.data.amount, e.data.currency) : date(e.data.due_at)}</td><td>${e.status === "done" ? "Завершено" : "Зафиксировано"}${e.kind === "document" ? `<br><button class="link" data-document="${e.entity_id}">Сохранить документ</button>` : ""}${e.kind === "task" && e.status === "open" ? `<br><button class="link" data-complete="${e.entity_id}">Выполнено</button>` : ""}</td></tr>`,
          ),
        )
      : '<div class="empty">Записей пока нет</div>';
    document.querySelectorAll<HTMLButtonElement>("[data-document]").forEach(
      (b) =>
        (b.onclick = async () => {
          const entity = rows.find(
            (v: any) => v.entity_id === b.dataset.document,
          );
          const result = await crm.document(
            entity.data.filename,
            entity.data.document_id,
          );
          if (!result.ok && !result.cancelled) notice(result.text);
        }),
    );
    document.querySelectorAll<HTMLButtonElement>("[data-complete]").forEach(
      (b) =>
        (b.onclick = () => {
          const e = rows.find((v: any) => v.entity_id === b.dataset.complete);
          void submitCommand({
            kind: "task_complete",
            contract_ids: [e.contract_uuid],
            payload: { entity_id: e.entity_id, entity_revision: e.revision },
          });
        }),
    );
  }
}

async function submitCommand(raw: any) {
  const body = { schema_version: 1, operation_id: crypto.randomUUID(), ...raw };
  const result = await crm.command(body);
  if (!result.ok) throw new Error(result.text);
  telemetry(
    "command_queued",
    raw.kind === "task_complete"
      ? "task"
      : raw.kind === "correction"
        ? "card"
        : raw.kind,
    { operation_id: body.operation_id },
  );
  notice("Действие сохранено на компьютере. Статус отправки — в очереди.");
}

async function action(kind: string, original?: any) {
  const target = kind === "correction" ? original.kind : kind;
  telemetry("workflow_started", target);
  const dialog = $<HTMLDialogElement>("dialog");
  if (target === "document") {
    const selected = card.contracts[0];
    if (card.contracts.length > 1) {
      await documentForm();
      return;
    }
    const uploaded = await crm.document(selected.contract_uuid);
    if (!uploaded.ok) {
      if (!uploaded.cancelled) notice(uploaded.text);
      return;
    }
    await submitCommand({
      kind: "document",
      contract_ids: [selected.contract_uuid],
      payload: {
        document_id: uploaded.data.document_id,
        sha256: uploaded.data.sha256,
        filename: uploaded.data.filename,
      },
    });
    return;
  }
  const map: Record<string, string[]> = {
    call: ["soft_call_status", "soft_comment", "soft_comm_date"],
    visit: [
      "call_status",
      "visit_date",
      "visit_comment",
      "contact_with",
      "agreed_payment",
      "who_at_address",
    ],
    contact: ["phone", "phone_main", "address", "extra_address"],
    note: [],
    promise: [],
    task: [],
    reported_payment: [],
  };
  const keys = map[target] || [],
    singular = ["promise", "task", "reported_payment"].includes(target);
  const draftKey =
    card.person.person_uuid +
    ":" +
    kind +
    (original ? ":" + original.event_id : "");
  const saved = (await crm.draft(draftKey)).data;
  const controls = keys
    .map((key) => {
      const def = context.fields.find((f: any) => f.key === key);
      const value =
        saved?.values?.[key] ?? original?.payload?.values?.[key] ?? "";
      const options = context.dropdowns[key];
      return `<div class="field ${key.includes("comment") ? "span" : ""}"><label for="v-${key}">${esc(def?.title || key)}</label>${options ? `<select id="v-${key}" data-field="${key}"><option value="">Не выбрано</option>${options.map((v: string) => `<option${v === value ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>` : key.includes("comment") ? `<textarea id="v-${key}" data-field="${key}">${esc(value)}</textarea>` : `<input id="v-${key}" data-field="${key}" type="${def?.type === "date" ? "date" : "text"}" value="${esc(value)}">`}<label class="check"><input type="checkbox" data-clear="${key}">Очистить поле</label></div>`;
    })
    .join("");
  dialog.innerHTML = `<form id="action-form"><h2>${esc(labels[kind])}</h2><div class="contracts">${card.contracts.map((c: any, i: number) => `<label class="check"><input type="${singular ? "radio" : "checkbox"}" name="contracts" value="${c.contract_uuid}"${!singular || i === 0 ? " checked" : ""}>${esc(c.external_id)} · ${esc(c.creditor_id)}</label>`).join("")}</div><div class="grid">${controls}${["note", "task"].includes(target) ? `<div class="field span"><label>${target === "task" ? "Задача" : "Заметка"}</label><textarea id="free-text">${esc(saved?.text || original?.payload?.text || "")}</textarea></div>` : ""}${["promise", "reported_payment"].includes(target) ? `<div class="field"><label>Сумма</label><input id="money" inputmode="decimal" value="${esc(saved?.amount || "")}"></div><div class="field"><label>Валюта</label><select id="currency"><option>RUB</option><option>TJS</option><option>UZS</option><option>USD</option></select></div>` : ""}${["promise", "task"].includes(target) ? `<div class="field"><label>Срок · ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}</label><input id="due" type="datetime-local" value="${esc(saved?.due || "")}"></div>` : ""}${kind === "correction" ? '<div class="field span"><label>Причина исправления</label><textarea id="reason" required></textarea></div>' : ""}</div><div class="error" id="form-error"></div><div class="actions">${button("cancel", "Закрыть")}${target === "call" ? button("show-script", "Скрипт") : ""}<button type="submit" class="primary">Сохранить действие</button></div><div id="script-inline"></div></form>`;
  const capture = () => ({
    values: Object.fromEntries(
      keys.map((k) => [k, ($("v-" + k) as HTMLInputElement).value]),
    ),
    text: ($("free-text") as HTMLTextAreaElement)?.value,
    amount: ($("money") as HTMLInputElement)?.value,
    due: ($("due") as HTMLInputElement)?.value,
  });
  dialog.querySelectorAll("input,select,textarea").forEach((el) =>
    el.addEventListener("input", () => {
      void crm.draft(draftKey, capture());
    }),
  );
  $("cancel").onclick = (e) => {
    e.preventDefault();
    dialog.close();
  };
  if (target === "call")
    $("show-script").onclick = async (e) => {
      e.preventDefault();
      const data = await request("GET", "script");
      scriptOpened = data;
      $("script-inline").innerHTML = scriptMarkup(data);
      telemetry("script_shown", "script");
    };
  $("action-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const contract_ids = Array.from(
        dialog.querySelectorAll<HTMLInputElement>(
          "input[name=contracts]:checked",
        ),
      ).map((el) => el.value);
      if (!contract_ids.length) throw new Error("Выберите договор.");
      const values: any = {},
        clear: string[] = [];
      for (const key of keys) {
        const value = ($("v-" + key) as HTMLInputElement).value;
        if (
          (dialog.querySelector(`[data-clear="${key}"]`) as HTMLInputElement)
            .checked
        ) {
          values[key] = null;
          clear.push(key);
        } else if (value !== "") values[key] = value;
      }
      const payload: any = { values, clear };
      if (target === "note")
        payload.text = $<HTMLTextAreaElement>("free-text").value;
      if (target === "task")
        payload.title = $<HTMLTextAreaElement>("free-text").value;
      if (["promise", "reported_payment"].includes(target)) {
        payload.amount = $<HTMLInputElement>("money").value.replace(",", ".");
        payload.currency = $<HTMLSelectElement>("currency").value;
      }
      if (["promise", "task"].includes(target)) {
        const dateValue = $<HTMLInputElement>("due").value;
        if (!dateValue) throw new Error("Укажите срок.");
        payload.due_at = new Date(dateValue).toISOString();
      }
      if (kind === "correction") payload.target_kind = target;
      const raw: any = {
        kind,
        contract_ids,
        payload,
        base_revisions: Object.fromEntries(
          card.contracts.map((c: any) => [c.contract_uuid, c.revisions]),
        ),
      };
      if (kind === "correction") {
        raw.correction_of = original.event_id;
        raw.reason = $<HTMLTextAreaElement>("reason").value;
      }
      const existing = (await crm.draft(draftKey + ":pending")).data;
      const content = JSON.stringify(raw);
      const operation_id =
        existing?.content === content
          ? existing.operation_id
          : crypto.randomUUID();
      await crm.draft(draftKey + ":pending", { content, operation_id });
      const result = await crm.command({
        schema_version: 1,
        operation_id,
        ...raw,
      });
      if (!result.ok) throw new Error(result.text);
      await crm.draft(draftKey, null);
      await crm.draft(draftKey + ":pending", null);
      dialog.close();
      telemetry("command_queued", target, { operation_id });
      notice("Действие сохранено. Отправка и подтверждение — в очереди.");
    } catch (error) {
      $("form-error").textContent = (error as Error).message;
    }
  };
  dialog.showModal();
}

async function documentForm() {
  const dialog = $<HTMLDialogElement>("dialog");
  dialog.innerHTML = `<h2>Документ</h2><div class="field"><label>Договор</label><select id="document-contract">${card.contracts.map((c: any) => `<option value="${c.contract_uuid}">${esc(c.external_id)} · ${esc(c.creditor_id)}</option>`).join("")}</select></div><div class="actions">${button("document-close", "Закрыть")}${button("document-pick", "Выбрать файл", true)}</div>`;
  bind("document-close", () => dialog.close());
  bind("document-pick", async () => {
    const id = $<HTMLSelectElement>("document-contract").value;
    const result = await crm.document(id);
    if (!result.ok) {
      if (!result.cancelled) throw new Error(result.text);
      return;
    }
    await submitCommand({
      kind: "document",
      contract_ids: [id],
      payload: {
        document_id: result.data.document_id,
        sha256: result.data.sha256,
        filename: result.data.filename,
      },
    });
    dialog.close();
  });
  dialog.showModal();
}

async function queuePage() {
  const result = await crm.queue();
  if (!result.ok) throw new Error(result.text);
  const entries = result.data.entries;
  const pending = entries.filter((e: any) => e.state !== "accepted");
  const exportErrors = (result.data.exports || []).filter(
    (v: any) => !v.completed,
  );
  $("view").innerHTML =
    `<div class="toolbar">${button("queue-refresh", "Обновить")}${button("queue-send", "Отправить", true)}</div><div class="muted">На компьютере: ${pending.length} · Незавершённых выгрузок: ${exportErrors.length} · Подтверждено сервером: ${entries.length - pending.length}</div>` +
    table(
      ["Действие", "Сохранено", "Состояние", "Результат"],
      entries
        .slice()
        .reverse()
        .map(
          (e: any) =>
            `<tr><td>${esc(e.action === "receipt" ? "Квитанция реестра" : labels[e.body.kind] || e.body.kind)}</td><td>${date(e.queued_at)}</td><td>${e.state === "accepted" ? "Принято сервером" : e.state === "attention" ? "Требует решения" : "Ожидает отправки"}</td><td>${esc(e.error || (e.result?.projection_status === "needs_review" ? "История сохранена; текущие поля требуют сверки" : e.result?.event_id ? "Подтверждено точным ID операции" : ""))}${e.state === "attention" ? `<br><button class="link" data-retry="${e.body.operation_id || e.body.receipt_id}">Повторить исходный запрос</button>` : ""}</td></tr>`,
        ),
    );
  $("view").insertAdjacentHTML(
    "beforeend",
    exportErrors
      .map(
        (v: any) =>
          `<article class="record"><h3>Выгрузка требует проверки</h3><p>${esc(v.path)}</p><p>${esc(v.error || "Запись файла ещё не подтверждена.")}</p></article>`,
      )
      .join(""),
  );
  bind("queue-refresh", queuePage);
  bind("queue-send", async () => {
    await crm.queue("flush");
    await queuePage();
  });
  document.querySelectorAll<HTMLButtonElement>("[data-retry]").forEach(
    (b) =>
      (b.onclick = async () => {
        await crm.queue("retry", b.dataset.retry);
        await queuePage();
      }),
  );
}

async function shift() {
  const data = await request("GET", "shifts");
  const current = data.shifts.find((s: any) => !s.ends_at);
  $("view").innerHTML =
    `<div class="toolbar">${!current ? button("shift-start", "Начать смену", true) : button("shift-pause", current.state === "break" ? "Продолжить" : "Перерыв") + button("shift-finish", "Завершить смену", true)}</div><p class="muted">${esc(data.meaning)}</p>` +
    table(
      [
        "Дата смены",
        "Начало",
        "Окончание",
        "Перерывы",
        "Заявленная длительность",
      ],
      data.shifts.map(
        (s: any) =>
          `<tr><td>${esc(s.workday)}</td><td>${date(s.starts_at)}</td><td>${s.unclosed ? "Не закрыта" : date(s.ends_at)}</td><td>${Math.floor(s.break_seconds / 60)} мин.</td><td>${Math.floor(s.declared_seconds / 3600)} ч. ${Math.floor(s.declared_seconds / 60) % 60} мин.</td></tr>`,
      ),
    );
  const send = async (action: string) => {
    await request("POST", "shift", {
      operation_id: crypto.randomUUID(),
      action,
      ...(current
        ? { shift_id: current.shift_id, revision: current.revision }
        : {}),
    });
    await shift();
  };
  bind("shift-start", () => send("start"));
  bind("shift-pause", () =>
    send(current.state === "break" ? "resume" : "break"),
  );
  bind("shift-finish", () => send("finish"));
}

function scriptMarkup(data: any) {
  return data.script
    ? `<h3>${esc(data.script.title)}</h3><p class="muted">Версия ${data.published_revision} · ${date(data.published_at)}</p>${data.script.steps.map((s: any, i: number) => `<article class="script-step"><h3>${i + 1}. ${esc(s.title)}</h3><p>${esc(s.text)}</p><button type="button" data-script-used>Шаг использован</button></article>`).join("")}`
    : '<div class="empty">Руководитель ещё не опубликовал скрипт</div>';
}
async function script() {
  const data = await request("GET", "script");
  scriptOpened = data;
  telemetry("script_shown", "script");
  $("view").innerHTML =
    scriptMarkup(data) +
    (data.can_edit
      ? `<div class="actions">${button("script-edit", "Редактировать черновик")}${button("script-publish", "Опубликовать черновик", true)}${button("script-withdraw", "Снять публикацию")}</div>`
      : "");
  bind("script-publish", async () => {
    await request("POST", "script", {
      operation_id: crypto.randomUUID(),
      action: "publish",
      base_revision: data.revision,
    });
    await script();
  });
  bind("script-withdraw", async () => {
    await request("POST", "script", {
      operation_id: crypto.randomUUID(),
      action: "withdraw",
      base_revision: data.revision,
    });
    await script();
  });
  bind("script-edit", () => scriptEditor(data));
}
function scriptEditor(data: any) {
  const dialog = $<HTMLDialogElement>("dialog");
  const content = data.draft ||
    data.script || { title: "", steps: [{ title: "", text: "" }] };
  dialog.innerHTML = `<form id="script-form"><h2>Черновик скрипта</h2><div class="field"><label>Название</label><input id="script-title" maxlength="100" value="${esc(content.title)}"></div><div id="script-steps">${content.steps.map((s: any) => stepEditor(s)).join("")}</div>${button("script-add", "Добавить шаг")}<div class="error" id="script-error"></div><div class="actions">${button("script-close", "Закрыть")}<button class="primary" type="submit">Сохранить черновик</button></div></form>`;
  $("script-add").onclick = (e) => {
    e.preventDefault();
    if (dialog.querySelectorAll("[data-script-step]").length < 12)
      $("script-steps").insertAdjacentHTML(
        "beforeend",
        stepEditor({ title: "", text: "" }),
      );
  };
  $("script-close").onclick = (e) => {
    e.preventDefault();
    dialog.close();
  };
  $("script-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const content = {
        title: $<HTMLInputElement>("script-title").value,
        steps: Array.from(dialog.querySelectorAll("[data-script-step]")).map(
          (el) => ({
            title: (el.querySelector("input") as HTMLInputElement).value,
            text: (el.querySelector("textarea") as HTMLTextAreaElement).value,
          }),
        ),
      };
      await request("POST", "script", {
        operation_id: crypto.randomUUID(),
        action: "save",
        base_revision: data.revision,
        content,
      });
      dialog.close();
      await script();
    } catch (error) {
      $("script-error").textContent = (error as Error).message;
    }
  };
  dialog.showModal();
}
function stepEditor(s: any) {
  return `<div class="field script-step" data-script-step><label>Шаг</label><input maxlength="80" value="${esc(s.title)}"><textarea maxlength="6000">${esc(s.text)}</textarea></div>`;
}

async function exchangePage() {
  const period = new Date().toISOString().slice(0, 7);
  $("view").innerHTML =
    `<div class="grid"><div class="field"><label>Период</label><input id="period" type="month" value="${period}"></div><div class="field"><label>Маршрут</label><input id="target" value="${state.connection.mode === "synthetic" ? "general:demo" : ""}" placeholder="Идентификатор согласованного реестра"></div></div><div class="toolbar" style="margin-top:20px">${button("export-new", "Создать реестр", true)}${button("export-template", "Создать копию существующей книги")}${button("import-file", "Сверить изменения Excel")}</div><div id="exchange-result"></div><p class="muted">Пустая ячейка не стирает данные. Для явной очистки введите [[CLEAR]]. Исходная книга сохраняется.</p>`;
  const write = async (use_template: boolean) => {
    const snapshot = await request("POST", "export", {
      operation_id: crypto.randomUUID(),
      period: $<HTMLInputElement>("period").value,
      target_id: $<HTMLInputElement>("target").value,
    });
    const result = await crm.workbook("write", { snapshot, use_template });
    if (!result.ok) {
      if (!result.cancelled) throw new Error(result.text);
      return;
    }
    const receiptText =
      "Квитанция сохранена в защищённой очереди. Статус подтверждения — в разделе «Очередь».";
    $("exchange-result").innerHTML =
      `<article class="record"><h3>Книга записана и повторно прочитана</h3><p>${esc(result.data.path)}</p><p>${esc(receiptText)}</p></article>`;
  };
  bind("export-new", () => write(false));
  bind("export-template", () => write(true));
  bind("import-file", async () => {
    const selected = await crm.workbook("read");
    if (!selected.ok) {
      if (!selected.cancelled) throw new Error(selected.text);
      return;
    }
    const { filename, ...input } = selected.data;
    const plan = await request("POST", "import-preview", input);
    $("exchange-result").innerHTML =
      `<h2>Сверка: ${esc(filename)}</h2><p class="muted">Изменений: ${plan.changes.length} · Конфликтов: ${plan.conflicts} · Отсутствующих строк: ${plan.missing_rows} (без снятия назначений)</p>` +
      table(
        ["Поле", "Исходное", "Сейчас на сервере", "Из Excel", "Решение"],
        plan.changes.map(
          (c: any, i: number) =>
            `<tr><td>${esc(context.fields.find((f: any) => f.key === c.key)?.title || c.key)}</td><td>${esc(c.baseline ?? "—")}</td><td>${esc(c.current ?? "—")}</td><td>${esc(c.incoming ?? "Очистить")}</td><td>${c.conflict ? `<select data-resolution="${i}"><option value="">Выберите</option><option value="server">Оставить сервер</option><option value="excel">Применить Excel</option></select>` : "Применить"}</td></tr>`,
        ),
      ) +
      `<div class="actions">${button("apply-import", "Применить сверенные изменения", true)}</div>`;
    bind("apply-import", async () => {
      const resolutions: any = {};
      for (const el of Array.from(
        document.querySelectorAll<HTMLSelectElement>("[data-resolution]"),
      )) {
        if (!el.value) throw new Error("Выберите решение каждого конфликта.");
        const c = plan.changes[Number(el.dataset.resolution)];
        resolutions[c.contract_uuid + ":" + c.key] = el.value;
      }
      const result = await request("POST", "import-apply", {
        operation_id: crypto.randomUUID(),
        preview_id: plan.preview_id,
        file_sha256: input.file_sha256,
        resolutions,
      });
      $("exchange-result").innerHTML =
        `<article class="record"><h3>Импорт подтверждён</h3><p>Строк: ${result.changed_rows} · Полей: ${result.changed_cells}</p></article>`;
    });
  });
}

async function adoption() {
  if (!context.cohorts?.length) {
    $("view").innerHTML =
      '<div class="empty">Группа внедрения ещё не задана. Состав и плановые смены загружаются из подтверждённого кадрового источника.</div>';
    return;
  }
  const until = new Date(),
    since = new Date(Date.now() - 28 * 86400000);
  $("view").innerHTML =
    `<div class="toolbar"><select id="cohort">${context.cohorts.map((c: any) => `<option value="${c.cohort_id}">${esc(c.name)}</option>`).join("")}</select><input id="since" type="date" value="${since.toISOString().slice(0, 10)}"><input id="until" type="date" value="${until.toISOString().slice(0, 10)}"><select id="filter-role"><option value="">Все роли</option><option value="employee">Сотрудники</option><option value="manager">Руководители</option><option value="observer">Наблюдатели</option></select><input id="filter-team" placeholder="ID команды"><select id="filter-feature"><option value="">Все функции</option><option value="call">Звонки</option><option value="visit">Выезды</option><option value="task">Задачи</option></select><input id="filter-version" placeholder="Версия приложения">${button("adoption-refresh", "Обновить")}</div><div id="adoption-report"></div>`;
  const load = async () => {
    const report = await request("POST", "adoption", {
      cohort_id: $<HTMLSelectElement>("cohort").value,
      role: $<HTMLSelectElement>("filter-role").value,
      team_id: $<HTMLInputElement>("filter-team").value,
      feature: $<HTMLSelectElement>("filter-feature").value,
      version: $<HTMLInputElement>("filter-version").value,
      since: $<HTMLInputElement>("since").value + "T00:00:00Z",
      until: new Date(
        new Date($<HTMLInputElement>("until").value + "T00:00:00Z").getTime() +
          86400000,
      ).toISOString(),
    });
    const metric = (title: string, value: any) =>
      `<div class="metric">${title}<strong>${value.value == null ? "—" : Math.round(value.value * 100) + "%"}</strong><small>${value.numerator} / ${value.denominator}</small></div>`;
    $("adoption-report").innerHTML =
      `<p class="muted">Данные на ${date(report.as_of)} · ${esc(report.completeness)}</p><div class="summary">${metric("Активация", report.activation)}${metric("Регулярность по сменам", report.regularity)}${metric("Доставка в реестры", report.delivery)}${metric("Диагностическое покрытие", report.coverage)}</div><p class="muted">График не задан: ${report.counts.unknown} · Первая смена впереди: ${report.counts.not_started} · Просроченная доставка: ${report.delivery.overdue} · Без срока доставки: ${report.delivery.without_sla}</p><h2>Рабочие дни</h2><div class="trend">${report.trend.map((d: any) => `<div style="height:${Math.max(3, d.planned ? (100 * d.active) / d.planned : 3)}%" title="${esc(d.workday)}: ${d.active}/${d.planned}"></div>`).join("")}</div>` +
      table(
        [
          "Сотрудник",
          "Плановые / активные смены",
          "Последняя работа",
          "Версии",
          "Ожидает реестра",
          "Полнота",
        ],
        report.employees.map(
          (e: any) =>
            `<tr><td>${esc(e.name)}</td><td>${e.planned_shifts} / ${e.active_shifts}</td><td>${date(e.last_work)}</td><td>${esc(e.versions.join(", ") || "Нет данных")}</td><td>${e.pending_delivery}</td><td>${esc(e.reason || "Данные получены")}</td></tr>`,
        ),
      ) +
      `<h2 style="margin-top:28px">Зафиксированная работа</h2>` +
      table(
        ["Вид", "Через приложение", "Доля"],
        Object.entries(report.origins).map(
          ([kind, v]: any) =>
            `<tr><td>${esc(labels[kind] || kind)}</td><td>${v.counts.app}</td><td>${v.app_share?.value != null ? Math.round(v.app_share.value * 100) + "%" : esc(v.reason)}</td></tr>`,
        ),
      ) +
      `<p class="muted">Метрики версии ${report.metric_version}. Целевые пороги: ${Object.keys(report.cohort.thresholds).length ? "заданы для группы" : "не заданы"}. Диагностика и заявленная длительность смены не являются табелем.</p>`;
    telemetry("report_viewed", "adoption");
  };
  bind("adoption-refresh", load);
  await load();
}

async function connection() {
  state = await crm.state();
  $("view").innerHTML =
    `<div class="help"><h2>Проверочный сценарий</h2><p>Синтетическая база хранится отдельно на этом компьютере.</p><div class="toolbar"><select id="demo-role"><option value="employee">Сотрудник</option><option value="manager">Руководитель</option><option value="observer">Наблюдатель другой команды</option></select>${button("demo-connect", "Открыть сценарий")}</div><label class="check"><input id="offline" type="checkbox"${state.offline ? " checked" : ""}>Проверить работу без связи</label><h2 style="margin-top:30px">Отдельный пилотный сервер</h2><div class="grid"><div class="field span"><label>HTTPS-адрес</label><input id="server" placeholder="Адрес подготовленного пилотного стенда"></div><div class="field"><label>Роль входа</label><select id="remote-role"><option value="employee">Сотрудник</option><option value="manager">Руководитель</option></select></div><div class="field"><label>Логин</label><input id="login" autocomplete="username"></div><div class="field"><label>Пароль</label><input id="password" type="password" autocomplete="current-password"></div></div><div class="actions">${button("remote-connect", "Войти", true)}</div></div>`;
  $<HTMLSelectElement>("demo-role").value = state.connection.role;
  $<HTMLInputElement>("offline").onchange = async () => {
    await crm.connect({
      action: "offline",
      value: $<HTMLInputElement>("offline").checked,
    });
    state = await crm.state();
    $("connection").textContent = state.offline
      ? "Проверка офлайн: отправка приостановлена"
      : "Связь включена";
  };
  bind("demo-connect", async () => {
    const r = await crm.connect({
      mode: "synthetic",
      role: $<HTMLSelectElement>("demo-role").value,
    });
    if (!r.ok) throw new Error(r.text);
    telemetry("login_ok", "shell");
    section = "portfolio";
    await boot();
  });
  bind("remote-connect", async () => {
    const r = await crm.connect({
      mode: "remote",
      role: $<HTMLSelectElement>("remote-role").value,
      server: $<HTMLInputElement>("server").value,
      username: $<HTMLInputElement>("login").value,
      password: $<HTMLInputElement>("password").value,
    });
    $<HTMLInputElement>("password").value = "";
    if (!r.ok) throw new Error(r.text);
    telemetry("login_ok", "shell");
    section = "portfolio";
    await boot();
  });
}
async function help() {
  $("view").innerHTML =
    `<div class="help"><h2>Сохранение и обмен</h2><p>Звонки, выезды и остальные действия сначала сохраняются в зашифрованной очереди на этом компьютере. Подтверждение сервера видно в очереди. При изменении назначения исходная запись остаётся для сверки.</p><p>Устаревшее действие сохраняется в истории. Оно не затирает более свежий результат карточки. Исправление оформляется новым событием с причиной.</p><p>Excel получает версию выгрузки и внутренние ID договоров. Ручные изменения проходят сверку с исходной версией и текущим сервером. Пропущенная строка не снимает назначение. Исходная книга сохраняется, запись и чтение новой копии проверяются.</p><h2>Смена и диагностика</h2><p>Начало, перерыв и окончание смены фиксируются серверным временем. Это заявленная длительность, а не расчёт оплаты.</p><p>Для панели внедрения учитываются принятые рабочие действия, заданные плановые смены, квитанции доставки и ограниченные технические события: открытие функций, готовность экрана, коды ошибок и версия. Тексты заметок, контакты клиентов, нажатия клавиш, снимки и геолокация в диагностику не передаются.</p><p>Сырые технические события можно хранить 30–365 дней по настройке владельца. Предлагается 90 дней; автоматическая очистка до выбора политики отключена. Бизнес-история хранится отдельно.</p></div>`;
}
document.addEventListener("click", (e) => {
  const target = (e.target as HTMLElement).closest(
    "[data-script-used]",
  ) as HTMLButtonElement | null;
  if (target && !target.disabled) {
    telemetry("script_step_used", "script");
    target.disabled = true;
    target.textContent = "Использован";
  }
});
document
  .querySelectorAll<HTMLButtonElement>("[data-win]")
  .forEach((b) => (b.onclick = () => crm.win(b.dataset.win!)));
void boot().catch((e) => notice(e.message));
