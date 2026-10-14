/** One deadline covers headers AND response body. Authenticated requests never
 * follow redirects: custom token headers must not leave the selected origin. */
export async function fetchBuffered(fetcher: (url: string, init: RequestInit) => Promise<Response>,
                                    url: string, init: RequestInit, timeoutMs = 30000): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetcher(url, { ...init, signal: controller.signal, redirect: "error" });
    const bytes = await res.arrayBuffer();
    return new Response([204, 205, 304].includes(res.status) ? null : bytes,
      { status: res.status, statusText: res.statusText, headers: res.headers });
  } finally { clearTimeout(timer); }
}

export function localPath(path: string): boolean {
  return /^\/[a-z][a-z0-9_]*(?:\/[a-z][a-z0-9_]*)*$/.test(path);
}

export function trustedUiUrl(url: string, page = "index.html"): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "hard:" && parsed.hostname === "app" && !parsed.username && !parsed.password
      && parsed.port === "" && parsed.pathname === "/" + page;
  } catch { return false; }
}

// All local routes use POST, including reads. Only explicit reads can replay
// after a lost response. Enroll/import/Excel/mail must run at most once.
export function retryLocalRead(path: string, body: unknown): boolean {
  if (["/state", "/busy", "/fn", "/job", "/jobs", "/mail/state"].includes(path)) return true;
  return path === "/admin" && String((body as { method?: string })?.method || "GET").toUpperCase() === "GET";
}
