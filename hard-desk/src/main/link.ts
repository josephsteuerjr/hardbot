/** Accept one personal URL, including a labelled line copied from the pult. */
import { URL, URLSearchParams } from "node:url";
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
    if (url.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) url.protocol = "https:";
    return { base: url.origin, code };
  } catch { return null; }
}
