import { hard } from "./api";
import { esc, shortDate, toast } from "./dom";

export function usableLink(row: any): boolean {
  return !!row && row.link_status !== "expired" && !!(row.alive || row.desktop_available);
}
export function linkStatus(row: any): string {
  if (!row?.code) return "ссылки нет";
  if (usableLink(row)) return "ссылка действует";
  return row.link_status === "used_up" ? "входы использованы" : "срок истёк";
}
export function linkQuota(row: any): string {
  if (!row?.code) return "";
  const phones = row.phone_remaining ?? Math.max(0, Math.min(2, row.max_uses || 2) - (row.uses || 0));
  return `Телефон: осталось ${phones} из 2 · ПК: ${row.desktop_available ? "доступен 1 вход" : row.bonus_desktop ? "вход использован" : "недоступен"}`;
}
export function linkDetails(row: any): string {
  return `<p class="muted">${esc(linkStatus(row))}${row?.expires_at ? " · до " + esc(shortDate(row.expires_at)) : ""}</p>
    ${row?.code ? `<p class="muted">${esc(linkQuota(row))}</p>` : ""}
    ${row?.url ? `<div class="url">${esc(row.url)}</div>` : ""}`;
}
export async function copyLink(url: string): Promise<void> {
  try { toast(await hard.copy(url) ? "Ссылка скопирована" : "Не удалось скопировать ссылку"); }
  catch { toast("Не удалось скопировать ссылку"); }
}
