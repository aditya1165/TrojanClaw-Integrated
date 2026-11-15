import { useEffect, useState } from "react";
import { apiRequest, type AccountProfile } from "../lib/api";
import { Button } from "./ui";
import { clearAssistantRequest } from "../lib/assistantRequest";

export function AssistantConsent({ request, account }: { request: string; account: AccountProfile }) {
  const [client, setClient] = useState("");
  const [calendarDrafts, setCalendarDrafts] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    apiRequest<{ client_name: string; calendar_drafts: boolean }>("/assistant/request", { request })
      .then(value => { if (active) { setClient(value.client_name); setCalendarDrafts(value.calendar_drafts); } })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "Could not load the request."); });
    return () => { active = false; };
  }, [request]);
  const approve = async () => {
    setBusy(true); setError("");
    try {
      const result = await apiRequest<{ redirect_url: string }>("/assistant/approve", { request });
      clearAssistantRequest();
      window.location.assign(result.redirect_url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not connect the assistant.");
      setBusy(false);
    }
  };
  return <main className="min-h-screen bg-bg p-6 text-fg">
    <section className="mx-auto mt-16 flex max-w-lg flex-col gap-5 rounded-2xl border border-line bg-surface p-6">
      <h1 className="display-title text-[26px]">Connect your assistant to TrojanClaw</h1>
      <p>Signed in as {account.name} · @{account.username}</p>
      <p>{client ? `Request from: ${client}.` : "Checking connection request…"} This name is supplied by the requesting app.</p>
      <p>Allow this assistant to ask TrojanClaw questions using your academic data and your saved Brightspace, Handshake and Piazza connections. Answers and source links will be shared with the assistant.</p>
      {calendarDrafts && <p>This connection can also prepare calendar drafts. Every addition requires you to open TrojanClaw and approve the event before it is saved to Google Calendar.</p>}
      <p className="text-sm text-muted">Access lasts up to seven days. Direct calendar changes and connector sign-ins are unavailable. You can revoke access from Settings.</p>
      {error && <p role="alert" className="text-error">{error}</p>}
      <Button disabled={!client || busy} onClick={() => void approve()}>{busy ? "Connecting…" : "Allow assistant access"}</Button>
      <Button variant="ghost" disabled={busy} onClick={() => { clearAssistantRequest(); window.location.assign("/"); }}>Cancel</Button>
    </section>
  </main>;
}

export function AssistantConnection() {
  const [status, setStatus] = useState<{ connected: boolean; mcp_url: string } | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  useEffect(() => {
    let active = true;
    apiRequest<{ connected: boolean; mcp_url: string }>("/assistant/status")
      .then(value => { if (active) setStatus(value); })
      .catch(reason => { if (active) setMessage(reason instanceof Error ? reason.message : "Could not check assistant access."); });
    return () => { active = false; };
  }, []);
  const disconnect = async () => {
    setBusy(true);
    try {
      await apiRequest("/assistant/disconnect", {});
      setStatus(previous => previous ? { ...previous, connected: false } : previous);
      setConfirmDisconnect(false);
      setMessage("Access revoked. Gemini cannot use TrojanClaw until you reconnect it in Gemini Connected Apps.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not disconnect."); }
    finally { setBusy(false); }
  };
  return <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 text-sm" aria-label="External assistants">
    <h2 className="font-medium">Gemini and external assistants</h2>
    <p className="text-muted">{status ? status.connected ? "Assistant access is active for this account. Gemini must also be enabled in its Connected Apps settings." : "No active assistant access for this account. Reconnect TrojanClaw in Gemini to grant access." : "Checking assistant access…"}</p>
    <p className="text-xs text-muted">To connect: open Gemini → Settings → Connected Apps → Add a custom app. Enter the MCP URL below, then finish the TrojanClaw approval screen by choosing Allow assistant access.</p>
    {status?.mcp_url && <label className="flex flex-col gap-1 text-xs text-muted">MCP URL
      <input readOnly value={status.mcp_url} className="w-full rounded-xl border border-line bg-bg p-3 font-mono text-xs text-fg" onFocus={event => event.currentTarget.select()} />
    </label>}
    {status?.connected && (confirmDisconnect ? <div className="flex flex-col gap-2">
      <p className="text-xs text-error">This blocks Gemini and all other linked assistants immediately. It does not reconnect them.</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" disabled={busy} onClick={() => void disconnect()}>{busy ? "Revoking…" : "Revoke all assistant access"}</Button>
        <Button variant="ghost" disabled={busy} onClick={() => setConfirmDisconnect(false)}>Keep connected</Button>
      </div>
    </div> : <Button variant="ghost" onClick={() => setConfirmDisconnect(true)}>Disconnect assistants…</Button>)}
    {message && <p role="status">{message}</p>}
  </section>;
}
