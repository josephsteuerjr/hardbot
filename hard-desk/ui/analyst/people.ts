// «Сотрудники» аналитика: активность и вход одной таблицей, офис и норма прямо в
// строке (сохраняются по выбору), снятие клиентов.
import { ask, esc, n, toast } from "../kit/dom";
import type { Section } from "../kit/shell";
import { issueLink } from "./links";
import { adm, chipsHtml, fn, fnMap, guard, note, sortRows, tableHtml } from "./lib";

const st = { tab: "activity" as "activity" | "roles" | "unassign", filter: "all", q: "", sort: { col: 7, dir: -1 }, selected: "", un: "" };
let workers: any[] = [];

async function activityTab(root: HTMLElement) {
  const d = await adm("GET", "/admin/activity");
  workers = d.workers || [];
  const latest = await fn<number>("activity.latest_shell", workers);
  const [counts, FILTERS, COLS, summary] = await Promise.all([
    fn("activity.filter_counts", workers), fn("activity.FILTERS"), fn("activity.COLUMNS"), fn("activity.summary_text", workers)]);
  const shown = await fnMap<boolean>("activity.matches", workers.map((w) => [w, st.filter, latest]));
  let rows = await fnMap<unknown[]>("activity.activity_row", workers.filter((_, i) => shown[i]).map((w) => [w]));
  if (st.q) rows = rows.filter((r) => r.join(" ").toLowerCase().includes(st.q.toLowerCase()));
  rows = sortRows(rows, st.sort.col, st.sort.dir);
  const numeric = new Set(["persons", "contracts", "today", "v7", "v30", "m7", "ph7", "idle", "shell"]);
  root.querySelector("#tab")!.innerHTML = `${note(summary)}
    <div class="toolbar" style="margin-top:14px">${chipsHtml((FILTERS as [string, string][]).map(([k, t]) => ({ key: k, text: `${t} · ${counts[k] || 0}`, on: st.filter === k })), "filter")}
      <span class="spacer"></span><input class="input search" id="p-q" placeholder="Поиск по ФИО, статусу" value="${esc(st.q)}"></div>
    ${st.selected ? `<div class="notice"><span class="grow"><b>${esc(st.selected)}</b></span>
      <button class="btn small" data-link>Ссылка входа</button><button class="btn small" data-unassign>Снять клиентов…</button></div>` : ""}
    <div id="tbl">${tableHtml(COLS, rows, { numeric, pick: true, selected: st.selected })}</div>`;
  const tab = root.querySelector<HTMLElement>("#tab")!;
  tab.querySelectorAll<HTMLButtonElement>("[data-filter]").forEach((b) => b.addEventListener("click", () => { st.filter = b.dataset.filter!; void activityTab(root); }));
  const q = tab.querySelector<HTMLInputElement>("#p-q");
  q?.addEventListener("change", () => { st.q = q.value; void activityTab(root); });
  tab.querySelectorAll<HTMLElement>("#tbl th").forEach((th, i) => th.addEventListener("click", () => {
    st.sort = { col: i, dir: st.sort.col === i ? -st.sort.dir : -1 }; void activityTab(root);
  }));
  tab.querySelectorAll<HTMLElement>("#tbl tr[data-key]").forEach((tr) => tr.addEventListener("click", () => { st.selected = tr.dataset.key!; void activityTab(root); }));
  tab.querySelector("[data-link]")?.addEventListener("click", guard(() => issueLink({ employee: st.selected })));
  tab.querySelector("[data-unassign]")?.addEventListener("click", () => { st.un = st.selected; st.tab = "unassign"; void draw(root); });
}

async function rolesTab(root: HTMLElement) {
  const [emps, offices] = await Promise.all([adm("GET", "/admin/employees"), adm("GET", "/admin/employee_cities.json").catch(() => ({}))]);
  const list = (emps.employees || []) as any[];
  const cities: Record<string, string> = offices.cities || {};
  const known: string[] = offices.known || [];
  const groups: number[] = emps.plan_groups || [8, 10, 12];
  const tab = root.querySelector<HTMLElement>("#tab")!;
  tab.innerHTML = `${note("«Офис» — где сидит сам сотрудник (Душанбе, Худжанд, Ташкент…): по нему считается штат руководителя города. «Норма» — выездов в день: от неё план в сводке. Сохраняется сразу по выбору.")}
    <datalist id="offices">${known.map((k) => `<option value="${esc(k)}">`).join("")}</datalist>
    <div class="table-wrap" style="margin-top:12px"><table class="grid"><thead><tr><th class="l">Сотрудник</th><th>Договоров</th><th class="l">Офис</th><th class="l">Норма</th><th class="l">Тип</th><th class="l">Вход</th><th class="l"></th></tr></thead>
    <tbody>${list.map((e) => `<tr data-emp="${esc(e.employee)}"><td class="l"><span class="who">${esc(e.employee)}</span></td><td>${n(e.contracts)}</td>
      <td class="l"><input class="input" list="offices" data-office value="${esc(cities[e.employee] || "")}" placeholder="не задан" style="width:150px;height:32px"></td>
      <td class="l"><select class="input" data-plan style="height:32px"><option value="0">общая</option>${groups.map((g) => `<option value="${g}" ${e.plan_personal && e.plan === g ? "selected" : ""}>${g}</option>`).join("")}</select></td>
      <td class="l"><select class="input" data-kind style="height:32px"><option value="agent">Выездник</option><option value="courier" ${e.kind === "courier" ? "selected" : ""}>Курьер ГПХ</option></select></td>
      <td class="l">${e.telegram_id ? '<span class="tag ok">привязан</span>' : '<span class="tag">не назначен</span>'}</td>
      <td class="l"><button class="btn small" data-link>Ссылка входа</button></td></tr>`).join("")}</tbody></table></div>`;
  tab.querySelectorAll<HTMLElement>("tr[data-emp]").forEach((tr) => {
    const emp = tr.dataset.emp!;
    const office = tr.querySelector<HTMLInputElement>("[data-office]")!;
    office.addEventListener("change", guard(async () => {
      const city = office.value.trim();
      await adm("POST", "/admin/employee_cities.json", { cities: { [emp]: city } });
      toast(city ? `${emp}: офис ${city}` : `${emp}: офис снят`);
    }));
    const plan = tr.querySelector<HTMLSelectElement>("[data-plan]")!;
    plan.addEventListener("change", guard(async () => {
      await adm("POST", "/admin/visit_plan.json", { plan: { [emp]: Number(plan.value) } });
      toast(Number(plan.value) ? `${emp}: норма ${plan.value} в день` : `${emp}: норма общая`);
    }));
    tr.querySelector("[data-link]")!.addEventListener("click", guard(() =>
      issueLink({ employee: emp, kind: tr.querySelector<HTMLSelectElement>("[data-kind]")!.value })));
  });
}

async function unassignTab(root: HTMLElement) {
  const emps = ((await adm("GET", "/admin/employees")).employees || []) as any[];
  const tab = root.querySelector<HTMLElement>("#tab")!;
  const checked = new Set<string>();
  let clients: any[] = [];
  const drawList = () => {
    const box = tab.querySelector("#un-list")!;
    box.innerHTML = clients.length ? `<div class="toolbar"><span class="muted">Договоров: ${n(clients.length)}</span><span class="spacer"></span>
        <button class="btn small" data-none>Ничего</button><button class="btn small" data-allc>Все</button>
        <button class="btn danger" data-go ${checked.size ? "" : "disabled"}>Снять выбранных (${checked.size})</button></div>
      ${clients.map((c) => `<label class="list-row" style="cursor:pointer"><input type="checkbox" data-cid="${esc(c.contract_id)}" ${checked.has(c.contract_id) ? "checked" : ""}>
        <span class="grow"><span class="nm">${esc(c.client || "—")}</span> <span class="sub mono">КД ${esc(c.contract_id)}</span></span>
        <span class="sub">${esc(n(c.debt))} ${esc(c.unit || "")}</span></label>`).join("")}`
      : st.un ? '<div class="empty"><b>Пусто</b>За сотрудником сейчас никого нет.</div>' : "";
    box.querySelectorAll<HTMLInputElement>("[data-cid]").forEach((cb) => cb.addEventListener("change", () => {
      if (cb.checked) checked.add(cb.dataset.cid!); else checked.delete(cb.dataset.cid!); drawList();
    }));
    box.querySelector("[data-none]")?.addEventListener("click", () => { checked.clear(); drawList(); });
    box.querySelector("[data-allc]")?.addEventListener("click", () => { clients.forEach((c) => checked.add(c.contract_id)); drawList(); });
    box.querySelector("[data-go]")?.addEventListener("click", guard(async () => {
      if (!(await ask("Убрать клиентов", await fn("people.unassign_text", checked.size, st.un), "Снять", true))) return;
      const d = await adm("POST", "/admin/unassign", { employee: st.un, contract_ids: [...checked] });
      toast(`Снято: ${d.n || 0}`); checked.clear(); await pick();
    }));
  };
  const pick = async () => {
    if (!st.un) return;
    clients = ((await adm("GET", "/admin/emp_clients?employee=" + encodeURIComponent(st.un))).clients || []);
    drawList();
  };
  tab.innerHTML = `${note("Договор остаётся в реестре без ответственного и пропадает из приложения сотрудника.")}
    <div class="toolbar" style="margin-top:12px"><select class="input" id="un-emp" style="width:340px"><option value="">— выберите сотрудника —</option>
      ${emps.map((e) => `<option ${e.employee === st.un ? "selected" : ""}>${esc(e.employee)}</option>`).join("")}</select></div><div id="un-list"></div>`;
  tab.querySelector<HTMLSelectElement>("#un-emp")!.addEventListener("change", (e) => { st.un = (e.target as HTMLSelectElement).value; checked.clear(); void pick(); });
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
