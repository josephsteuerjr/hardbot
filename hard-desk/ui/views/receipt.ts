import { api, hard } from "../kit/api";
import { esc } from "../kit/dom";
import { toast } from "../kit/dom";

/** The same receipt fields on both employee cards; only a visit link differs. */
export function receiptHtml(d: any, visitLink: boolean): string {
  const receipts: any[] = d.receipts || [];
  return `<div class="section"><div class="section-head"><h3 class="section-title">Чек об оплате</h3></div>
    <label>Договор <select class="input" id="f-rc-contract"><option value="">Выберите договор</option>${(d.contracts || []).map((c:any)=>`<option value="${esc(c.contract_id)}" ${(d.contracts || []).length===1?"selected":""}>${esc(c.contract_id)} · ${esc(c.unit || "валюта не указана")}</option>`).join("")}</select></label>
    <div class="row"><input class="input" id="f-rc-sum" placeholder="Сумма из чека" inputmode="decimal" style="max-width:160px">
    <input class="input" id="f-rc-date" type="date" style="max-width:160px"></div>
    <div class="row" style="margin-top:6px"><label><input type="checkbox" id="f-rc-other"> платил не должник</label>
    <input class="input" id="f-rc-who" placeholder="кто и почему платил" style="flex:1"></div>
    <button class="btn primary" id="f-rc-add" style="margin-top:8px">Выбрать снимок чека и приложить</button>
    ${visitLink && d.last_visit_op ? '<label class="list-row"><input id="f-rc-visit" type="checkbox"> Чек относится к последнему выезду</label>' : ''}
    ${receipts.length ? `<div class="list">${receipts.map(x => `<div class="list-row"><div class="grow"><div class="nm">${esc(x.paid_at || x.created_at || '')} · ${esc(x.amount || '—')} ${esc(x.currency || '')}</div><div class="sub">${x.payer_is_client ? 'платил должник' : 'другое лицо' + (x.payer_note ? ': ' + esc(x.payer_note) : '')}</div></div></div>`).join('')}</div>` : ''}
    <p class="muted" style="font-size:13px">СБ увидит чек и данные платежа из реестра. Приложенный снимок требует сверки с платежами.</p></div>`;
}

export function wireReceipt(panel: HTMLElement, rep: string, d: any, refresh: () => void): void {
  panel.querySelector("#f-rc-add")?.addEventListener("click", async () => {
    const contract=(panel.querySelector("#f-rc-contract") as HTMLSelectElement).value;
    if(!(d.contracts || []).some((c:any)=>c.contract_id===contract)){toast("Выберите договор для чека");return}
    const amount = (panel.querySelector("#f-rc-sum") as HTMLInputElement).value.replace(/\s/g, '').replace(',', '.');
    const paid = (panel.querySelector("#f-rc-date") as HTMLInputElement).value;
    const other = (panel.querySelector("#f-rc-other") as HTMLInputElement).checked;
    const who = (panel.querySelector("#f-rc-who") as HTMLInputElement).value.trim();
    if (!/^\d+(\.\d{1,2})?$/.test(amount) || Number(amount) <= 0) { toast("Укажите сумму платежа"); return; }
    if (!paid) { toast("Укажите дату платежа из чека"); return; }
    if (other && !who) { toast("Укажите, кто платил вместо должника"); return; }
    const file = await hard.pick("file", { title: "Снимок чека", filters: [{ name: "Изображение", extensions: ["jpg", "jpeg", "png", "webp"] }] });
    if (!file) return;
    const query: Record<string, unknown> = { rep:contract, op: 'desk-receipt-' + crypto.randomUUID(), amount, paid_at: paid, payer: other ? 'other' : 'client' };
    if (other) query.note = who;
    if ((panel.querySelector("#f-rc-visit") as HTMLInputElement | null)?.checked) query.visit = d.last_visit_op;
    const button = panel.querySelector<HTMLButtonElement>("#f-rc-add")!;
    button.disabled = true;
    try {
      const result = await hard.upload("/api/receipt", query, file, file.slice(file.lastIndexOf('.')).toLowerCase());
      if (result.ok) {
        panel.querySelectorAll<HTMLInputElement>('[id^="f-rc-"]').forEach(input=>{if(input.type==="checkbox")input.checked=false;else input.value="";delete input.dataset.hardDirty});
        panel.dispatchEvent(new CustomEvent("hard-form-saved", { detail: ["f-rc-contract", "f-rc-sum", "f-rc-date", "f-rc-other", "f-rc-who", "f-rc-visit"] }));
        toast("Чек приложен"); refresh();
      } else toast(result.text || "Чек не принят");
    } finally { if (button.isConnected) button.disabled = false; }
  });
}
