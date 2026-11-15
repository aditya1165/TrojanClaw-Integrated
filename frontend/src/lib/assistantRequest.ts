const STORAGE_KEY = "tc-pending-assistant";
const MAX_AGE = 10 * 60 * 1000;
type SessionStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function sessionStore(): SessionStore | undefined {
  try { return window.sessionStorage; } catch { return undefined; }
}

/** Keep the short-lived consent request across Google navigation in this tab.
 * This is encrypted request metadata, not a website session or MCP access token.
 */
export function readAssistantRequest(search: string, storage = sessionStore(), now = Date.now()): string | null {
  const current = new URLSearchParams(search).get("mcp_request");
  if (current && current.length <= 24000) {
    try { storage?.setItem(STORAGE_KEY, JSON.stringify({ request: current, expires: now + MAX_AGE })); } catch { /* URL still works. */ }
    return current;
  }
  try {
    const saved = JSON.parse(storage?.getItem(STORAGE_KEY) || "null");
    if (saved && typeof saved.request === "string" && saved.request.length <= 24000 && saved.expires > now) return saved.request;
    storage?.removeItem(STORAGE_KEY);
  } catch { /* Unavailable or malformed session storage cannot authorize anything. */ }
  return null;
}

export function clearAssistantRequest(storage = sessionStore()) {
  try { storage?.removeItem(STORAGE_KEY); } catch { /* Nothing to restore. */ }
}
