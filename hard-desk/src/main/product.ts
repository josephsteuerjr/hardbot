export type ProductId = "hard" | "soft" | "manager" | "analyst" | "crm";
declare const __HARD_PRODUCT__: string;
const built = typeof __HARD_PRODUCT__ === "undefined" ? "" : __HARD_PRODUCT__;
export const PRODUCTS = {
  crm: { id: "crm", name: "Взыскание — CRM пилот", mode: "field", appId: "ru.rimborso.crm.pilot", dataName: "crm-pilot" },
  hard: { id: "hard", name: "Взыскание Хард", mode: "field", appId: "ru.rimborso.hard.field", dataName: "hard" },
  soft: { id: "soft", name: "Взыскание Софт", mode: "field", appId: "ru.rimborso.hard.soft", dataName: "soft" },
  manager: { id: "manager", name: "Взыскание СНГ — Руководитель", mode: "manager", appId: "ru.rimborso.hard.manager", dataName: "manager" },
  analyst: { id: "analyst", name: "Взыскание СНГ — Аналитик", mode: "analyst", appId: "ru.rimborso.hard.analyst", dataName: "analyst" },
} as const;
export const PRODUCT_ID = built in PRODUCTS ? built as ProductId : null;
export const FIXED_PRODUCT = PRODUCT_ID ? PRODUCTS[PRODUCT_ID] : null;
export const DEFAULT_SERVER = "https://hard.rimborso.ru";
export const RELEASE_URL = "https://github.com/josephsteuerjr/hard-desktop-releases/releases/latest/download/";
