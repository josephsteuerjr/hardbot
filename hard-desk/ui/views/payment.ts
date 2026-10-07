import { api } from "../kit/api";
import { esc, toast } from "../kit/dom";

export function paymentHtml(d: any): string {
  return `<div class="section"><div class="section-head"><h3 class="section-title">Записать оплату</h3></div>
    <label class="muted" style="display:block">Договор</label><select class="input" id="p-contract" style="width:100%">${(d.contracts || []).map((c: any) => `<option value="${esc(c.contract_id)}">${esc(c.contract_id)} · ${esc(c.unit || '')}</option>`).join('')}</select>
    <div class="row" style="margin-top:8px;flex-wrap:wrap"><div style="flex:1;min-width:150px"><label class="muted" style="display:block">Дата оплаты</label><input class="input" id="p-date" type="date" style="width:100%"></div>
    <div style="flex:1;min-width:150px"><label class="muted" style="display:block">Сумма оплаты</label><input class="input" id="p-amount" inputmode="decimal" placeholder="0" style="width:100%"></div></div>
    <button class="btn primary" id="p-save" style="margin-top:8px">Сохранить оплату</button>
    <p class="muted">Дата и сумма попадут в комментарий Софта при обогащении Excel.</p>
    ${(d.reported_payments || []).slice().reverse().map((p: any) => `<div class="list-row"><div class="grow"><div class="nm">${esc(p.paid_at)} · ${esc(Number(p.amount).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}))} ${esc(p.currency || '')}</div><div class="sub">КД ${esc(p.contract_id)} · записал ${esc(p.employee || '')}</div></div></div>`).join('')}</div>`;
}

export function wirePayment(panel: HTMLElement, refresh: () => void): void {
  const button = panel.querySelector<HTMLButtonElement>('#p-save');
  button?.addEventListener('click', async () => {
    const amount = (panel.querySelector('#p-amount') as HTMLInputElement).value.replace(/\s/g, '').replace(',', '.');
    const paid_at = (panel.querySelector('#p-date') as HTMLInputElement).value;
    const rep = (panel.querySelector('#p-contract') as HTMLSelectElement).value;
    if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0 || Number(amount) > 1e9 || !paid_at) { toast('Укажите дату и сумму оплаты'); return; }
    button.disabled = true;
    try {
      const result = await api('/api/save', undefined, 'POST', {kind:'payment', finish:true,
        rep, amount, paid_at, op_id:'desk-payment-'+crypto.randomUUID(), occurred_at:Date.now()});
      if (result.ok) {
        (panel.querySelector('#p-amount') as HTMLInputElement).value = '';
        toast((result.data as any)?.queued ? 'Оплата сохранена на компьютере и ждёт отправки' : 'Оплата записана');
        refresh();
      } else toast(result.text || 'Оплата не записана');
    } finally { if (button.isConnected) button.disabled = false; }
  });
}
