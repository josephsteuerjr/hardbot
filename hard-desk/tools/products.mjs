const internal=process.env.HARD_CRM_MANAGER_INTERNAL==='1';
const analystInternal=process.env.HARD_CRM_ANALYST_INTERNAL==='1';
const rehearsal=process.env.HARD_CRM_REHEARSAL==='1';
export const products = {
  crm: { name: analystInternal?'Взыскание — Аналитик (внутренний)':internal?'Взыскание — Руководитель (внутренний)':'Взыскание — CRM СНГ', appId: analystInternal?'ru.rimborso.crm.analyst.internal':internal?'ru.rimborso.crm.manager.internal':'ru.rimborso.crm.pilot', description: 'CRM СНГ' },
  hard: { name: 'Взыскание СНГ', appId: 'ru.rimborso.hard.field', description: 'Портфель, звонки и выезды' },
  soft: { name: 'Взыскание Софт', appId: 'ru.rimborso.hard.soft', description: 'Очередь звонков и результаты Софта' },
  manager: { name: 'Взыскание СНГ — Руководитель', appId: 'ru.rimborso.hard.manager', description: 'Рабочий CRM-обзор, команда и служба безопасности' },
  analyst: { name: 'Взыскание СНГ — Аналитик', appId: 'ru.rimborso.hard.analyst', description: 'Реестры, обогащение, отчёты и контуры' },
};
export const releaseUrl = 'https://github.com/josephsteuerjr/hard-desktop-releases/releases/latest/download/';
const rehearsalNames={hard:'CRM Тест — Сотрудник',manager:'CRM Тест — Руководитель',analyst:'CRM Тест — Аналитик'};
if(rehearsal)for(const [id,name] of Object.entries(rehearsalNames)){
  products[id]={...products[id],name,appId:'ru.rimborso.rehearsal.'+id};
}
const analystServerReleaseUrl = 'https://hard.rimborso.ru/updates/cis-collection-analyst/';
export const stableProducts = ['hard', 'soft', 'manager', 'analyst'];
export const usesCrm = id => id === 'crm' || (stableProducts.includes(id) && process.env.HARD_CRM_STABLE === '1');
export const includesBackend = id => id === 'analyst' || usesCrm(id);
export function productReleaseUrl(id) {
  if (rehearsal) return 'https://invalid.local/crm-rehearsal/';
  if (id === 'crm') return 'https://invalid.local/crm-pilot-unpublished/';
  if (id === 'analyst' && process.env.HARD_ANALYST_SERVER_CHANNEL === '1') return analystServerReleaseUrl;
  return releaseUrl;
}
export const productDefines = id => ({
  __HARD_PRODUCT__: JSON.stringify(id),
  __HARD_RELEASE_URL__: JSON.stringify(productReleaseUrl(id)),
  __HARD_CRM_REHEARSAL__: String(rehearsal),
  __HARD_PRODUCT_NAME__: JSON.stringify(products[id]?.name || ''),
  __HARD_APP_ID__: JSON.stringify(products[id]?.appId || ''),
  __HARD_CRM_INTERNAL__: String(internal),
  __HARD_CRM_ANALYST_INTERNAL__: String(analystInternal),
  __HARD_CRM_STABLE__: String(usesCrm(id) && id !== 'crm'),
});
