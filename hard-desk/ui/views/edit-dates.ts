import { esc } from "../kit/dom";

function day(value: unknown): string {
  const text = String(value || "");
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T|$)/.exec(text);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : "—";
}

function stamp(value: unknown): string {
  const text = String(value || "");
  if (!text) return "—";
  const date = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text) ? text : text + "Z");
  return Number.isFinite(date.valueOf()) ? date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—";
}

export function editDatesHtml(data: any): string {
  const rows: any[] = Array.isArray(data.edit_dates) ? data.edit_dates : [];
  if (!rows.length) return "";
  return `<details class="section"><summary>Даты изменения результатов</summary>${rows.map(row =>
    `<div class="section"><b>КД ${esc(row.contract_id)}</b><table class="edit-dates-table"><thead><tr><th>Блок</th><th>Ручная правка Excel</th><th>Правка в приложении</th></tr></thead><tbody>${[
      ["Выезд", "excel_visit_edited_on", "app_visit_edited_at"],
      ["Звонок", "excel_call_edited_on", "app_call_edited_at"],
    ].map(([label, excel, app]) => `<tr><td>${label}</td><td>${day(row[excel])}</td><td>${stamp(row[app])}</td></tr>`).join("")}</tbody></table></div>`
  ).join("")}</details>`;
}
