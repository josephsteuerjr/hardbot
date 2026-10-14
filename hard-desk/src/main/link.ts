/** Accept one personal URL, including a labelled line copied from the pult. */
import { URL, URLSearchParams } from "node:url";
import { isIP } from "node:net";

// Без TLS остаёмся только на loopback и приватных адресах (внутренний контур
// компании/VPN без сертификата); публичные адреса и любые имена — всегда https.
function plainHttpAllowed(hostname: string): boolean {
  if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]") return true;
  const bare = hostname.startsWith("[") && hostname.endsWith("]") ? hostname.slice(1, -1) : hostname;
  if (isIP(bare) === 4) {
    const [a, b] = bare.split(".").map(Number);
    return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
  }
  if (isIP(bare) === 6) {
    const first = Number.parseInt(bare.split(":")[0], 16);
    return Number.isFinite(first) && (first & 0xfe00) === 0xfc00; // fc00::/7 (ULA)
  }
  return false;
}

export function splitLink(raw: string): { base: string; code: string } | null {
  const text = String(raw || "").trim().replace(/^[<"']+|[>"']+$/g, "");
  if (/^[A-Za-z0-9_-]{6,}$/.test(text)) return { base: "", code: text };
  const urls = text.match(/https?:\/\/[^\s<>"']+/gi) || [];
  if (urls.length !== 1) return null;
  try {
    const url = new URL(urls[0]);
    if (url.username || url.password) return null;
    const path = /^\/i\/([A-Za-z0-9_-]{6,})\/?$/.exec(url.pathname);
    const code = path?.[1] || new URLSearchParams(url.hash.slice(1)).get("link") || url.searchParams.get("link");
    if (!code || !/^[A-Za-z0-9_-]{6,}$/.test(code)) return null;
    if (url.protocol === "http:" && !plainHttpAllowed(url.hostname)) url.protocol = "https:";
    return { base: url.origin, code };
  } catch { return null; }
}
