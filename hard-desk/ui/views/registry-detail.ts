import { esc } from "../kit/dom";
export function registryMoney(value: unknown): string {
  return value == null || value === "" || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("ru-RU", {minimumFractionDigits:2,maximumFractionDigits:2});
}
export function registryDate(value: unknown): string {
  const text=String(value || "");
  return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0,10).split("-").reverse().join(".") : text || "—";
}
export function registryContracts(d:any):string {
  return `<section class="section registry-contracts"><h3 class="section-title">Договоры и структура долга</h3>${(d.contracts || []).map((c:any)=>`
    <article class="registry-contract"><div class="section-head"><b class="mono">${esc(c.contract_id)}</b><span>${esc(c.partner || "Контрагент не указан")}</span></div>
    <dl class="registry-facts"><dt>Дата выдачи / оформления</dt><dd>${esc(registryDate(c.kd_date))}</dd><dt>Выданная сумма</dt><dd>${esc(registryMoney(c.issued_sum))} ${esc(c.unit || "")}</dd>
    <dt>Дни просрочки (ДП)</dt><dd>${esc(c.dp == null || c.dp === "" ? "—" : c.dp)}</dd><dt>Текущий долг</dt><dd><b>${esc(registryMoney(c.total))} ${esc(c.unit || "")}</b></dd></dl>
    <dl class="registry-facts registry-debt">${(c.debt || []).map((item:any)=>`<dt>${esc(item.title)}</dt><dd>${esc(registryMoney(item.value))} ${esc(c.unit || "")}</dd>`).join("") || '<dt>Структура долга</dt><dd>Нет данных в реестре</dd>'}</dl></article>`).join("")}</section>`;
}
