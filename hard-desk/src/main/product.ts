export type ProductId = "hard" | "soft" | "manager" | "analyst" | "crm";
declare const __HARD_PRODUCT__: string;
declare const __HARD_CRM_INTERNAL__: boolean;
declare const __HARD_CRM_ANALYST_INTERNAL__: boolean;
declare const __HARD_CRM_STABLE__: boolean;
declare const __HARD_RELEASE_URL__: string;
declare const __HARD_CRM_REHEARSAL__: boolean;
declare const __HARD_PRODUCT_NAME__: string;
declare const __HARD_APP_ID__: string;
export const CRM_REHEARSAL = typeof __HARD_CRM_REHEARSAL__ !== 'undefined' && __HARD_CRM_REHEARSAL__;
export const CRM_INTERNAL=typeof __HARD_CRM_INTERNAL__!=='undefined'&&__HARD_CRM_INTERNAL__;
export const CRM_ANALYST_INTERNAL=typeof __HARD_CRM_ANALYST_INTERNAL__!=='undefined'&&__HARD_CRM_ANALYST_INTERNAL__;
const built = typeof __HARD_PRODUCT__ === "undefined" ? "" : __HARD_PRODUCT__;
export const PRODUCTS = {
  crm: { id: "crm", name: CRM_ANALYST_INTERNAL?'Взыскание — Аналитик (внутренний)':CRM_INTERNAL?'Взыскание — Руководитель (внутренний)':"Взыскание — CRM СНГ", mode: "field", appId: CRM_ANALYST_INTERNAL?'ru.rimborso.crm.analyst.internal':CRM_INTERNAL?'ru.rimborso.crm.manager.internal':"ru.rimborso.crm.pilot", dataName: CRM_ANALYST_INTERNAL?'crm-analyst-internal':CRM_INTERNAL?'crm-manager-internal':"crm-pilot" },
  hard: { id: "hard", name: "Взыскание СНГ", mode: "field", appId: "ru.rimborso.hard.field", dataName: "hard" },
  soft: { id: "soft", name: "Взыскание Софт", mode: "field", appId: "ru.rimborso.hard.soft", dataName: "soft" },
  manager: { id: "manager", name: "Взыскание СНГ — Руководитель", mode: "manager", appId: "ru.rimborso.hard.manager", dataName: "manager" },
  analyst: { id: "analyst", name: "Взыскание СНГ — Аналитик", mode: "analyst", appId: "ru.rimborso.hard.analyst", dataName: "analyst" },
} as const;
export const PRODUCT_ID = built in PRODUCTS ? built as ProductId : null;
const configuredProduct = PRODUCT_ID ? PRODUCTS[PRODUCT_ID] : null;
export const FIXED_PRODUCT = CRM_REHEARSAL && configuredProduct
  ? {...configuredProduct,name:__HARD_PRODUCT_NAME__,appId:__HARD_APP_ID__,dataName:'rehearsal-'+PRODUCT_ID}
  : configuredProduct;
export const STABLE_CRM = !!PRODUCT_ID && PRODUCT_ID !== "crm" &&
  typeof __HARD_CRM_STABLE__ !== "undefined" && __HARD_CRM_STABLE__;
export const DEFAULT_SERVER = "https://hard.rimborso.ru";
export const RELEASE_URL = typeof __HARD_RELEASE_URL__ === 'undefined'
  ? 'https://github.com/josephsteuerjr/hard-desktop-releases/releases/latest/download/'
  : __HARD_RELEASE_URL__;
