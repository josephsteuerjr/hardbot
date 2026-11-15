const internal=process.env.HARD_CRM_MANAGER_INTERNAL==='1';
const analystInternal=process.env.HARD_CRM_ANALYST_INTERNAL==='1';
export const products = {
  crm: { name: analystInternal?'Взыскание — Аналитик (внутренний)':internal?'Взыскание — Руководитель (внутренний)':'Взыскание — CRM пилот', appId: analystInternal?'ru.rimborso.crm.analyst.internal':internal?'ru.rimborso.crm.manager.internal':'ru.rimborso.crm.pilot', description: 'Отдельный кандидат CRM' },
  hard: { name: 'Взыскание Хард', appId: 'ru.rimborso.hard.field', description: 'Портфель и работа выездника' },
  soft: { name: 'Взыскание Софт', appId: 'ru.rimborso.hard.soft', description: 'Очередь звонков и результаты Софта' },
  manager: { name: 'Взыскание СНГ — Руководитель', appId: 'ru.rimborso.hard.manager', description: 'Рабочий CRM-обзор, команда и служба безопасности' },
  analyst: { name: 'Взыскание СНГ — Аналитик', appId: 'ru.rimborso.hard.analyst', description: 'Реестры, обогащение, отчёты и контуры' },
};
export const releaseUrl = 'https://github.com/josephsteuerjr/hard-desktop-releases/releases/latest/download/';
