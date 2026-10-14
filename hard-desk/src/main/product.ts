export type ProductId = "hard" | "soft" | "manager" | "analyst";
declare const __HARD_PRODUCT__: string;
const built = typeof __HARD_PRODUCT__ === "undefined" ? "" : __HARD_PRODUCT__;
export const PRODUCTS = {
  hard: { id: "hard", name: "Хард", mode: "field", appId: "ru.rimborso.hard.field", dataName: "hard" },
  soft: { id: "soft", name: "Софт", mode: "field", appId: "ru.rimborso.hard.soft", dataName: "soft" },
  manager: { id: "manager", name: "Хард — Руководитель", mode: "manager", appId: "ru.rimborso.hard.manager", dataName: "manager" },
  analyst: { id: "analyst", name: "Хард — Аналитик", mode: "analyst", appId: "ru.rimborso.hard.analyst", dataName: "analyst" },
} as const;
export const PRODUCT_ID = built in PRODUCTS ? built as ProductId : null;
export const FIXED_PRODUCT = PRODUCT_ID ? PRODUCTS[PRODUCT_ID] : null;
export const RELEASE_URL = "https://github.com/josephsteuerjr/hard-desktop-releases/releases/latest/download/";
