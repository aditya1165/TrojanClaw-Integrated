import { useEffect, useState } from "react";
import { apiRequest, type AccountProfile } from "../lib/api";
import { Button } from "./ui";

export function AssistantConsent({ request, account }: { request: string; account: AccountProfile }) {
  const [client, setClient] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    apiRequest<{ client_name: string }>("/assistant/request", { request })
      .then(value => { if (active) setClient(value.client_name); })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "Could not load the request."); });
    return () => { active = false; };
  }, [request]);
  const approve = async () => {
    setBusy(true); setError("");
    try {
      const result = await apiRequest<{ redirect_url: string }>("/assistant/approve", { request });
      window.location.assign(result.redirect_url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not connect the assistant.");
      setBusy(false);
    }
  };
  return <main className="min-h-screen bg-bg p-6 text-fg">
    <section className="mx-auto mt-16 flex max-w-lg flex-col gap-5 rounded-2xl border border-line bg-surface p-6">
      <h1 className="text-2xl font-semibold">Connect your assistant to TrojanClaw</h1>
      <p>Signed in as {account.name} · @{account.username}</p>
      <p>{client ? `Request from: ${client}.` : "Checking connection request…"} This name is supplied by the requesting app.</p>
      <p>Allow this assistant to ask TrojanClaw questions using your academic data and your saved Brightspace, Handshake and Piazza connections. Answers and source links will be shared with the assistant.</p>
      <p className="text-sm text-muted">Access lasts up to seven days. Calendar changes and connector sign-ins are unavailable. You can revoke access from Settings.</p>
      {error && <p role="alert" className="text-error">{error}</p>}
      <Button disabled={!client || busy} onClick={() => void approve()}>{busy ? "Connecting…" : "Allow assistant access"}</Button>
      <Button variant="ghost" disabled={busy} onClick={() => window.location.assign("/")}>Cancel</Button>
    </section>
  </main>;
}

export function AssistantConnection() {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const disconnect = async () => {
    setBusy(true);
    try {
      await apiRequest("/assistant/disconnect", {});
      setMessage("All external assistant connections were revoked.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not disconnect."); }
    finally { setBusy(false); }
  };
  return <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 text-sm" aria-label="External assistants">
    <h2 className="font-medium">Gemini and external assistants</h2>
    <p className="text-muted">Revoke assistant access to your TrojanClaw account. Your website and Android sign-ins stay active.</p>
    <Button variant="secondary" disabled={busy} onClick={() => void disconnect()}>{busy ? "Disconnecting…" : "Disconnect all assistants"}</Button>
    {message && <p role="status">{message}</p>}
  </section>;
}
