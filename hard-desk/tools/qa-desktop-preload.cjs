const today = '2026-10-09';
const range = { preset: 'today', from: today, to: today, today, label: '9 октября', days: 1, next: false };
const people = [
  { employee: 'Сотрудник А', office: 'north', country: 'TJ', profile: 'hard', visits: 12, persons: 10, portfolio: 150, paid: 2, promised: 4, confirmed: 9, overdue: 1, plan_per_day: 10 },
  { employee: 'Сотрудник Б', office: 'south', country: 'UZ', profile: 'hard', visits: 0, persons: 0, portfolio: 80, paid: 0, promised: 0, confirmed: 0, overdue: 0, plan_per_day: 10 },
  { employee: 'Сотрудник В', office: 'north', country: 'TJ', profile: 'hard', visits: 8, persons: 8, portfolio: 100, paid: 1, promised: 2, confirmed: 7, overdue: 0, plan_per_day: 10 },
];
const soft = [
  { employee: 'Оператор А', office: 'north', country: 'TJ', calls: 32, persons: 20, portfolio: 180, contracts: 210, rpc: 18, ptp: 7, kp: 3, paid_sum_text: '24 500 TJS', wait_text: '18 500 TJS' },
  { employee: 'Оператор Б', office: 'south', country: 'UZ', calls: 28, persons: 18, portfolio: 140, contracts: 160, rpc: 14, ptp: 5, kp: 2, paid_sum_text: '350 000 UZS', wait_text: '200 000 UZS' },
];
const role = process.env.HARD_QA_ROLE || 'manager';
const events = [{ id: 'ev1', employee: people[0].employee, office: 'north', kind: 'visit', title: 'Обещает оплатить', debtor: 'Синтетический клиент', rep: 'TEST-01', day: today, time: '10:21' }];
const offices = [{ code: 'north', name: 'Северный офис', label: 'Северный офис', employees: 2 }, { code: 'south', name: 'Южный офис', label: 'Южный офис', employees: 1 }];
window.__requests = [];
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.message)));
window.addEventListener('unhandledrejection', e => window.__errors.push(String(e.reason)));
window.hard = {
  state: async () => ({ mode: 'manager', productId: 'manager', fixedProduct: true, product: 'Хард — Руководитель', version: '3.0.0', enrolled: true, theme: process.env.HARD_QA_THEME || 'light', deviceName: 'Стенд', kit: false }),
  api: async (method, path, query) => {
    window.__requests.push({ method, path, query });
    let data;
    if (path.endsWith('/me')) data = { caps: { control: role === 'security' || role === 'full', team_links: role !== 'security' }, me: { name: 'Руководитель стенда', role, scope: 'Доступные офисы' } };
    else if (path === '/api/manager/stats') data = { range, scope: 'Доступные офисы', pay_asof: '2026-10-08', employees: people, soft: { employees: soft }, totals: {}, by_day: [{ date: today, day: 9, weekend: false }], offices, countries: [{ code: 'TJ', short: 'Таджикистан' }, { code: 'UZ', short: 'Узбекистан' }] };
    else if (path === '/api/manager/promises') data = { totals: { persons: 1 }, employees: [{ employee: people[0].employee, items: [{ rep: 'TEST-01', client: 'Синтетический клиент', days_overdue: 3, promise_date: '2026-10-06', money_text: '1 200 TJS' }] }] };
    else if (path === '/api/manager/activity') data = { range, employees: people.map(p => ({ ...p, calls: 0 })), events, offices };
    else if (path === '/api/manager/stats_employee') data = { range, ...people.find(p => p.employee === query.employee), profile: query.mode, portfolio: 150, calls: 32, rpc: 18, ptp: 7, kp: 3,
      profiles: [query.mode], cases: [{ date: today, day_label: '9 октября', client: 'Синтетический клиент', rep: 'TEST-01', outcome: 'Обещает оплатить', evidence_level: 'confirmed', comment: 'Тестовая история', money_text: '1 200 TJS' }] };
    else if (path === '/api/control/overview') data = { range, rows: [], offices, how: [], legend: [] };
    else if (path === '/api/manager/person') data = { client: 'Синтетический клиент', rows: [], history: [], photos: [], contracts: [], visits: [] };
    else return { ok: false, status: 404, text: 'Не предусмотрено стендом: ' + path };
    return { ok: true, status: 200, data };
  },
  edit: action => window.__requests.push({ edit: action }),
  update: async () => ({ phase: 'idle', text: 'Установлена актуальная версия' }),
  copy: async () => true, xlsx: async () => ({ ok: true }), download: async () => ({ ok: true }),
  open: async () => '', on: () => {}, win: () => {}, autostart: async () => false,
};
