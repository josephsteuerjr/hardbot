import { api } from "../kit/api";
import { esc, toast } from "../kit/dom";
import { lockCard } from "./employee";

export function paymentHtml(d: any): string {
  const today = d.payment_today || new Date(Date.now()+5*3600000).toISOString().slice(0,10);
  return `<div class="section"><div class="section-head"><h3 class="section-title">Записать оплату вручную</h3></div>
    <label class="muted" style="display:block">Договор</label><select class="input" id="p-contract" style="width:100%">${(d.contracts || []).map((c: any) => `<option value="${esc(c.contract_id)}">${esc(c.contract_id)} · ${esc(c.unit || '')}</option>`).join('')}</select>
    <div class="row" style="margin-top:8px;flex-wrap:wrap"><div style="flex:1;min-width:150px"><label class="muted" style="display:block">Дата оплаты</label><input class="input" id="p-date" type="date" value="${esc(today)}" max="${esc(today)}" style="width:100%"></div>
    <div style="flex:1;min-width:150px"><label class="muted" style="display:block">Сумма оплаты</label><input class="input" id="p-amount" inputmode="decimal" placeholder="0" style="width:100%"></div></div>
    <label class="muted" style="display:block;margin-top:8px">Результат по выбранному договору</label><select class="input" id="p-result" style="width:100%"><option value="">Оставить текущий статус</option><option value="Оплачен" ${d.payment_revision ? '' : 'disabled'}>Оплачен</option></select>
    <button class="btn primary" id="p-save" style="margin-top:8px">Сохранить оплату</button>
    <p class="notice err" id="p-error" role="status" hidden></p>
    <button class="btn small" id="p-reload" hidden>Обновить и сверить</button>
    <p class="muted">${d.payment_revision ? 'Сумма и дата попадут в колонки последнего платежа и комментарий Софта. Выбранный результат сохранится вместе с оплатой.' : 'Для результата «Оплачен» и переноса в колонки требуется обновление сервера и аналитического комплекта. На этом сервере доступна прежняя запись оплаты.'}</p>
    ${(d.reported_payments || []).slice().reverse().map((p: any) => `<div class="list-row"><div class="grow"><div class="nm">${esc(p.paid_at)} · ${esc(Number(p.amount).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}))} ${esc(p.currency || '')}</div><div class="sub">КД ${esc(p.contract_id)} · записал ${esc(p.employee || '')}${p.result_status ? ` · ${esc(p.result_status)}` : ''}</div></div></div>`).join('')}</div>`;
}

export function wirePayment(panel: HTMLElement, refresh: () => void | Promise<void>, d: any): void {
  panel.querySelector('#p-reload')?.addEventListener('click', refresh);
  const button = panel.querySelector<HTMLButtonElement>('#p-save');
  button?.addEventListener('click', async () => {
    const amount = (panel.querySelector('#p-amount') as HTMLInputElement).value.replace(/\s/g, '').replace(',', '.');
    const paid_at = (panel.querySelector('#p-date') as HTMLInputElement).value;
    const rep = (panel.querySelector('#p-contract') as HTMLSelectElement).value;
    const result_status = (panel.querySelector('#p-result') as HTMLSelectElement).value;
    if (result_status && !d.payment_revision) { toast('Для сохранения результата оплаты требуется обновление сервера'); return; }
    if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0 || Number(amount) > 1e9 || !paid_at || paid_at>(d.payment_today || new Date(Date.now()+5*3600000).toISOString().slice(0,10))) { toast('Укажите дату и сумму оплаты'); return; }
    button.disabled = true;
    const error=panel.querySelector<HTMLElement>("#p-error");if(error)error.hidden=true;
    const op_id='desk-payment-'+crypto.randomUUID();
    panel.dispatchEvent(new CustomEvent("hard-form-submitting",{detail:{op_id}}));lockCard(panel,true);
    try {
      const result = await api('/api/save', undefined, 'POST', {kind:'payment', finish:true,
        rep, amount, paid_at, result_status, payment_revision:d.payment_revision,
        draft_rep:panel.dataset.softRep || rep,op_id, occurred_at:Date.now()});
      lockCard(panel,false);
      if (result.ok) {
        (panel.querySelector('#p-amount') as HTMLInputElement).value = '';
        (panel.querySelector('#p-result') as HTMLSelectElement).value = '';
        panel.dispatchEvent(new CustomEvent("hard-form-saved", { detail: {fields:["p-amount", "p-date", "p-contract", "p-result"],queued:!!(result.data as any)?.queued} }));
        toast((result.data as any)?.queued ? 'Оплата сохранена на компьютере и ждёт отправки' : 'Оплата записана');
        await refresh();
      } else {panel.dispatchEvent(new Event("hard-form-failed"));if(error){error.textContent=result.text || 'Оплата не записана';error.hidden=false}if(result.status===409)panel.querySelector<HTMLElement>('#p-reload')!.hidden=false;toast(result.text || 'Оплата не записана');}
    } finally { lockCard(panel,false);if (button.isConnected) button.disabled = false; }
  });
}
