// «Сотрудники» аналитика: активность и вход одной таблицей, офис и норма прямо в
// строке (сохраняются по выбору), снятие клиентов.
import { ask, esc, n, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { issueLink } from "./links";
import { DataTable, tupleCols, type Col } from "../kit/table";
import { adm, chipsHtml, fn, fnMap, guard, note } from "./lib";

const st = { tab: "activity" as "activity" | "roles" | "unassign", filter: "all", selected: "", un: "" };
let workers: any[] = [];

async function activityTab(root: HTMLElement) {
  const d = await adm("GET", "/admin/activity");
  workers = d.workers || [];
  const latest = await fn<number>("activity.latest_shell", workers);
  const [counts, FILTERS, COLS, summary] = await Promise.all([
    fn("activity.filter_counts", workers), fn("activity.FILTERS"), fn("activity.COLUMNS"), fn("activity.summary_text", workers)]);
  const shown = await fnMap<boolean>("activity.matches", workers.map((w) => [w, st.filter, latest]));
  const rows = await fnMap<unknown[]>("activity.activity_row", workers.filter((_, i) => shown[i]).map((w) => [w]));
  const numeric = new Set(["persons", "contracts", "today", "v7", "v30", "m7", "ph7", "idle", "shell"]);
  root.querySelector("#tab")!.innerHTML = `${note(summary)}
    <div class="toolbar" style="margin-top:14px">${chipsHtml((FILTERS as [string, string][]).map(([k, t]) => ({ key: k, text: `${t} · ${counts[k] || 0}`, on: st.filter === k })), "filter")}
</div>
    ${st.selected ? `<div class="notice"><span class="grow"><b>${esc(st.selected)}</b></span>
      <button class="btn small" data-link>Профиль и устройства</button><button class="btn small" data-unassign>Снять клиентов…</button></div>` : ""}
    <div id="tbl"></div>`;
  const tab = root.querySelector<HTMLElement>("#tab")!;
  tab.querySelectorAll<HTMLButtonElement>("[data-filter]").forEach((b) => b.addEventListener("click", () => { st.filter = b.dataset.filter!; void activityTab(root); }));
  new DataTable<unknown[]>(tab.querySelector("#tbl")!, {
    id: "analyst-activity", cols: tupleCols(COLS, numeric), key: (r) => String(r[0]), exportName: "Сотрудники — активность",
    onRow: (r) => { st.selected = String(r[0]); void activityTab(root); },
    rowClass: (r) => (String(r[0]) === st.selected ? "is-picked" : ""),
  }).setRows(rows);
  tab.querySelector("[data-link]")?.addEventListener("click", guard(() => issueLink({ employee: st.selected })));
  tab.querySelector("[data-unassign]")?.addEventListener("click", () => { st.un = st.selected; st.tab = "unassign"; void draw(root); });
}

async function rolesTab(root: HTMLElement) {
  const [emps, offices] = await Promise.all([adm("GET", "/admin/employees"), adm("GET", "/admin/employee_cities.json").catch(() => ({}))]);
  const list = (emps.employees || []) as any[];
  const cities: Record<string, string> = offices.cities || {};
  const known: string[] = offices.known || [];
  const tab = root.querySelector<HTMLElement>("#tab")!;
  tab.innerHTML = `${note("«Офис» — где сидит сам сотрудник (Душанбе, Худжанд, Ташкент…): по нему считается штат руководителя города. «Норма» — целое число выездов в день от 1 до 100; пустое поле возвращает общую норму. Сохраняется после ввода.")}
    <datalist id="offices">${known.map((k) => `<option value="${esc(k)}">`).join("")}</datalist>
    <div id="roles-tbl" style="margin-top:10px"></div>`;
  const cols: Col<any>[] = [
    { key: "emp", title: "Сотрудник", value: (e) => e.employee, html: (e) => `<span class="who">${esc(e.employee)}</span>` },
    { key: "contracts", title: "Договоров", num: true, value: (e) => e.contracts ?? 0 },
    { key: "office", title: "Офис", value: (e) => cities[e.employee] || "",
      html: (e) => `<input class="input" list="offices" data-office value="${esc(cities[e.employee] || "")}" placeholder="не задан" style="width:150px;height:32px">` },
    { key: "plan", title: "Норма", value: (e) => (e.plan_personal ? String(e.plan) : "общая"),
      html: (e) => `<input class="input" type="number" min="1" max="100" step="1" inputmode="numeric" data-plan value="${e.plan_personal ? esc(e.plan) : ""}" placeholder="общая ${esc(emps.plan_common || 10)}" aria-label="Норма выездов: ${esc(e.employee)}" style="width:115px;height:32px">` },
    { key: "kind", title: "Тип", value: (e) => (e.kind === "courier" ? "Курьер ГПХ" : "Выездник"),
      html: (e) => `<select class="input" data-kind style="height:32px"><option value="agent">Выездник</option><option value="courier" ${e.kind === "courier" ? "selected" : ""}>Курьер ГПХ</option></select>` },
    { key: "bound", title: "Вход", value: (e) => (e.telegram_id ? "привязан" : "не назначен"),
      html: (e) => (e.telegram_id ? '<span class="tag ok">привязан</span>' : '<span class="tag">не назначен</span>') },
    { key: "act", title: "", filter: false, value: () => "", html: () => '<button class="btn small" data-link>Профиль и устройства</button>' },
  ];
  new DataTable<any>(tab.querySelector("#roles-tbl")!, {
    id: "analyst-roles", cols, key: (e) => e.employee, exportName: "Сотрудники — офисы и нормы",
    afterRender: (host) => host.querySelectorAll<HTMLElement>("tr[data-key]").forEach((tr) => {
      const emp = tr.dataset.key!;
      const office = tr.querySelector<HTMLInputElement>("[data-office]")!;
      office.addEventListener("change", guard(async () => {
        const city = office.value.trim();
        await adm("POST", "/admin/employee_cities.json", { cities: { [emp]: city } });
        cities[emp] = city;
        toast(city ? `${emp}: офис ${city}` : `${emp}: офис снят`);
      }));
      const plan = tr.querySelector<HTMLInputElement>("[data-plan]")!;
      plan.addEventListener("change", guard(async () => {
        if (!plan.reportValidity()) return;
        const value = plan.value === "" ? 0 : Number(plan.value);
        plan.disabled = true;
        try {
          await adm("POST", "/admin/visit_plan.json", { plan: { [emp]: value } });
          const row = list.find((e) => e.employee === emp);
          if (row) { row.plan_personal = value > 0; row.plan = value || emps.plan_common || 10; }
          toast(value ? `${emp}: норма ${value} в день` : `${emp}: норма общая`);
        } finally { plan.disabled = false; }
      }));
      tr.querySelector("[data-link]")!.addEventListener("click", guard(() =>
        issueLink({ employee: emp, kind: tr.querySelector<HTMLSelectElement>("[data-kind]")!.value })));
    }),
  }).setRows(list);
}

async function unassignTab(root: HTMLElement) {
  const emps = ((await adm("GET", "/admin/employees")).employees || []) as any[];
  const tab = root.querySelector<HTMLElement>("#tab")!;
  tab.innerHTML = `${note("Договор остаётся в реестре без ответственного и пропадает из приложения сотрудника. Отметьте договоры галочками — фильтры помогут отобрать нужных.")}
    <div class="toolbar" style="margin-top:12px"><select class="input" id="un-emp" style="width:340px"><option value="">— выберите сотрудника —</option>
      ${emps.map((e) => `<option ${e.employee === st.un ? "selected" : ""}>${esc(e.employee)}</option>`).join("")}</select>
      <span class="spacer"></span><button class="btn danger" id="un-go" disabled>Снять отмеченных</button></div>
    <div id="un-list"></div>`;
  const go = tab.querySelector<HTMLButtonElement>("#un-go")!;
  const cols: Col<any>[] = [
    { key: "client", title: "Должник", value: (c) => c.client || "—" },
    { key: "cid", title: "Договор", value: (c) => c.contract_id, html: (c) => `<span class="mono">${esc(c.contract_id)}</span>` },
    { key: "debt", title: "Долг", num: true, value: (c) => Number(c.debt) || 0, html: (c) => `${esc(n(c.debt))} ${esc(c.unit || "")}` },
  ];
  const table = new DataTable<any>(tab.querySelector("#un-list")!, {
    id: "analyst-unassign", cols, key: (c) => c.contract_id, select: true, exportName: "Договоры сотрудника",
    empty: st.un ? "За сотрудником сейчас никого нет." : "Выберите сотрудника.",
    onSelect: (keys) => { go.disabled = !keys.size; go.textContent = keys.size ? `Снять отмеченных (${keys.size})` : "Снять отмеченных"; },
  });
  const pick = async () => {
    table.clearSelection();
    table.setRows(st.un ? ((await adm("GET", "/admin/emp_clients?employee=" + encodeURIComponent(st.un))).clients || []) : []);
  };
  go.addEventListener("click", guard(async () => {
    const ids = table.selected().map((c) => c.contract_id);
    if (!ids.length) return;
    if (!(await ask("Убрать клиентов", await fn("people.unassign_text", ids.length, st.un), "Снять", true))) return;
    const d = await adm("POST", "/admin/unassign", { employee: st.un, contract_ids: ids });
    toast(`Снято: ${d.n || 0}`);
    await pick();
  }));
  tab.querySelector<HTMLSelectElement>("#un-emp")!.addEventListener("change", (e) => { st.un = (e.target as HTMLSelectElement).value; void pick(); });
  await pick();
}

async function draw(root: HTMLElement) {
  root.innerHTML = `<div class="toolbar">${chipsHtml([{ key: "activity", text: "Активность и вход", on: st.tab === "activity" },
    { key: "roles", text: "Офис, норма, тип", on: st.tab === "roles" }, { key: "unassign", text: "Снять клиентов", on: st.tab === "unassign" }], "tab")}</div>
    <div id="tab"><div class="skeleton"></div></div>`;
  root.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((b) => b.addEventListener("click", () => { st.tab = b.dataset.tab as typeof st.tab; void draw(root); }));
  try {
    if (st.tab === "roles") await rolesTab(root);
    else if (st.tab === "unassign") await unassignTab(root);
    else await activityTab(root);
  } catch (e) {
    root.querySelector("#tab")!.innerHTML = `<div class="notice err">${esc((e as Error).message)}</div>`;
  }
}

export const people: Section = {
  id: "people",
  title: "Сотрудники",
  icon: "people",
  kicker: "активность, офисы, нормы, клиенты",
  render(root) { return draw(root); },
};
