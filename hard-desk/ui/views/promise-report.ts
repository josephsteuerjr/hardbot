import { api, hard } from "../kit/api";
import { esc, n, toast } from "../kit/dom";
import { staleNote } from "./common";

const localDay = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
};
const fields = { from: localDay(), to: localDay(), asof: localDay(), due: "", country: "" };

export function mountPromiseReport(root: HTMLElement) {
  root.innerHTML = `<div class="section"><div class="section-head"><h2 class="section-title">Выполнение обещаний Софта</h2></div>
    <div class="toolbar">
      <label>Обещания с <input class="input" type="date" data-promise-report="from" value="${esc(fields.from)}"></label>
      <label>По <input class="input" type="date" data-promise-report="to" value="${esc(fields.to)}"></label>
      <label>Оплаты на дату <input class="input" type="date" data-promise-report="asof" max="${localDay()}" value="${esc(fields.asof)}"></label>
      <label>Срок оплаты <input class="input" type="date" data-promise-report="due" value="${esc(fields.due)}"></label>
      <label>Страна <select class="input" data-promise-report="country"><option value="">Все</option><option value="tj">Таджикистан</option><option value="uz">Узбекистан</option></select></label>
    </div>
    <div class="toolbar"><button class="btn" data-promise-preview>Показать</button><button class="btn primary" data-promise-download>Скачать Excel</button></div>
    <div data-promise-result></div></div>`;
  root.querySelector<HTMLSelectElement>('[data-promise-report="country"]')!.value = fields.country;
  const result = root.querySelector<HTMLElement>('[data-promise-result]')!;
  for (const control of root.querySelectorAll<HTMLInputElement | HTMLSelectElement>('[data-promise-report]')) {
    control.addEventListener('change', () => {
      const key = control.dataset.promiseReport as keyof typeof fields;
      fields[key] = control.value;
      result.innerHTML = '';
    });
  }
  let busy = false;
  const run = async (download: boolean) => {
    if (busy) return;
    if (!fields.from || !fields.to || !fields.asof || fields.from > fields.to || fields.to > fields.asof || fields.asof > localDay()) {
      toast('Проверьте период обещаний и дату проверки оплат'); return;
    }
    const query = { ...fields };
    busy = true; if (!download) result.innerHTML = '';
    const controls = [...root.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>('button,input,select')];
    controls.forEach(control => control.disabled = true);
    try {
      if (download) {
        const response = await hard.download('/api/manager/promise_report.xlsx', query, 'Выполнение обещаний.xlsx');
        if (response.ok && response.file) { toast('Отчёт сформирован'); void hard.open(response.file); }
        else if (response.text) toast(response.text);
        return;
      }
      const response = await api('/api/manager/promise_report', query);
      if (!response.ok) { result.textContent = response.text || 'Не удалось загрузить отчёт'; return; }
      if (JSON.stringify(fields) !== JSON.stringify(query)) return;
      const data = response.data, totals = data.totals;
      const ratio = totals.kp_rate == null ? '—' : `${(totals.kp_rate*100).toFixed(1)}%`;
      const stamp = data.payment_data.last_import_at;
      const imported = stamp ? new Date(stamp).toLocaleString('ru-RU',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : 'нет сведений';
      result.innerHTML = `${staleNote(response.stale)}<p>Последний успешный импорт: ${esc(imported)}. По сохранённым реестровым оплатам.</p>
        ${data.open_deadlines ? '<p>Срез промежуточный: у части обещаний срок оплаты ещё не завершён.</p>' : ''}
        <div class="chips"><span class="chip">Обещаний: ${n(totals.ptp)}</span><span class="chip">Выполнено (KP): ${n(totals.kept)}</span>
          <span class="chip">Частичная оплата: ${n(totals.partial)}</span><span class="chip">Ожидаем срока: ${n(totals.waiting)}</span>
          <span class="chip">Срок прошёл, оплаты нет: ${n(totals.unpaid)}</span><span class="chip">Недостаточно данных: ${n(totals.incomplete)}</span><span class="chip">KP от PTP: ${ratio}</span></div>`;
    } catch (error) {
      result.textContent = error instanceof Error ? error.message : 'Не удалось сформировать отчёт';
    } finally {
      busy = false; controls.forEach(control => control.disabled = false);
    }
  };
  root.querySelector('[data-promise-preview]')!.addEventListener('click', () => void run(false));
  root.querySelector('[data-promise-download]')!.addEventListener('click', () => void run(true));
}
