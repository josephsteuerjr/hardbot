const choiceLabels = [
  ['partner', 'Контрагент', 'Все контрагенты', 'Без контрагента'],
  ['pool', 'Пул', 'Все пулы', 'Без пула'],
  ['overall_result', 'Общий статус', 'Все статусы', 'Без статуса'],
  ['call_status', 'Статус звонка', 'Все статусы', 'Без статуса'],
  ['visit_status', 'Статус выезда', 'Все статусы', 'Без статуса'],
  ['debt_currency', 'Валюта', 'Все валюты', ''],
] as const;
const fields = [...choiceLabels.map(([key]) => key), 'debt_from', 'debt_to', 'sort'] as const;
type Key = typeof fields[number];
export type PortfolioFilters = Partial<Record<Key, string>>;
type Options = Partial<Record<Key, string[]>>;

const escape = (value: string) => value.replace(/[&<>"']/g, char =>
  ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]!));

export function restorePortfolioFilters(saved: unknown): PortfolioFilters {
  if (!saved || typeof saved !== 'object') return {};
  const values = saved as Record<string, unknown>;
  return Object.fromEntries(fields.flatMap(key => typeof values[key] === 'string' ? [[key, values[key]]] : []));
}

export function portfolioFiltersMarkup(filters: PortfolioFilters, options: Options, expanded: boolean) {
  const count = Object.keys(filters).length;
  const menus = choiceLabels.map(([key, label, all, blank]) => {
    const current = filters[key];
    if (!options[key] && current === undefined) return '';
    const values = [...new Set([...(options[key] || []), ...(current === undefined ? [] : [current])])];
    if (key === 'debt_currency' && values.length < 2 && current === undefined) return '';
    values.sort((a, b) => a.localeCompare(b, 'ru', {numeric: true}));
    return `<label>${label}<select name="${key}"><option value="">${all}</option>${values.map(value =>
      `<option value="${escape(JSON.stringify(value))}"${value === current ? ' selected' : ''}>${escape(value || blank)}</option>`).join('')}</select></label>`;
  }).join('');
  return `<details id="portfolio-filters" class="portfolio-filters"${expanded ? ' open' : ''}>
    <summary>Фильтры${count ? ` (${count})` : ''}</summary>
    <form class="portfolio-filter-form">
      ${menus}
      <label>Приоритет<select name="sort"><option value="priority"${filters.sort === undefined || filters.sort === 'priority' ? ' selected' : ''}>По сроку</option><option value="call_date"${filters.sort === 'call_date' ? ' selected' : ''}>Сначала ближайший звонок</option><option value="debt_desc"${filters.sort === 'debt_desc' ? ' selected' : ''}>Сначала крупный долг</option><option value="client"${filters.sort === 'client' ? ' selected' : ''}>По имени</option></select></label>
      <label>Долг от<input name="debt_from" inputmode="decimal" maxlength="24" value="${escape(filters.debt_from || '')}"></label>
      <label>Долг до<input name="debt_to" inputmode="decimal" maxlength="24" value="${escape(filters.debt_to || '')}"></label>
      <button type="submit">Применить</button><button type="reset">Сбросить</button>
    </form><p class="muted" data-filter-message role="status" hidden></p></details>`;
}

export function bindPortfolioFilters(panel: HTMLDetailsElement, apply: (filters: PortfolioFilters) => Promise<void>) {
  const form = panel.querySelector('form')!;
  const message = panel.querySelector<HTMLElement>('[data-filter-message]')!;
  const readAmount = (data: FormData, key: 'debt_from' | 'debt_to') => {
    const value = String(data.get(key) || '').trim().replace(/[ \u00a0\u202f]/g, '').replace(',', '.');
    if (value && !/^-?\d{1,15}(\.\d{1,2})?$/.test(value)) throw Error('Введите сумму с точностью до копеек.');
    return value;
  };
  const submit = async (reset: boolean) => {
    message.hidden = true;
    try {
      const values: PortfolioFilters = {};
      if (!reset) {
        const data = new FormData(form);
        for (const [key] of choiceLabels) {
          const choice = data.get(key);
          if (typeof choice === 'string' && choice) values[key] = JSON.parse(choice);
        }
        for (const key of ['debt_from', 'debt_to'] as const) {
          const amount = readAmount(data, key);
          if (amount) values[key] = amount;
        }
        values.sort = String(data.get('sort') || 'priority');
      }
      await apply(values);
    } catch (error) {
      if ((error as Error).name === 'AbortError') return;
      message.textContent = (error as Error).message;
      message.hidden = false;
    }
  };
  form.oninput = () => { form.dataset.edited = ''; };
  form.onsubmit = event => { event.preventDefault(); void submit(false); };
  form.onreset = event => { event.preventDefault(); void submit(true); };
}
