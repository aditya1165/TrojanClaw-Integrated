import { useEffect, useState } from "react";
import { apiRequest } from "../lib/api";
import { effectiveTimezone, readSettings } from "../lib/settings";
import { Button } from "./ui";

interface SiriStatus {
  configured: boolean;
  connected: boolean;
  chat_url: string | null;
  expires_at: string | null;
  key_days: number;
}
interface SiriKey { access_key: string; chat_url: string; expires_at: string }

export function SiriConnection() {
  const [status, setStatus] = useState<SiriStatus | null>(null);
  const [key, setKey] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    apiRequest<SiriStatus>("/siri/status")
      .then(value => { if (active) setStatus(value); })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "Could not check Siri access."); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!key) return;
    const timer = window.setTimeout(() => { setKey(""); setVisible(false); }, 120000);
    return () => window.clearTimeout(timer);
  }, [key]);

  const connect = async () => {
    setBusy(true); setError(""); setMessage(""); setKey(""); setVisible(false);
    try {
      const result = await apiRequest<SiriKey>("/siri/connect", {});
      setKey(result.access_key);
      setStatus({ configured: true, connected: true, chat_url: result.chat_url, expires_at: result.expires_at, key_days: 30 });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not connect Siri."); }
    finally { setBusy(false); }
  };

  const disconnect = async () => {
    setBusy(true); setError("");
    try {
      await apiRequest("/siri/disconnect", {});
      setKey(""); setVisible(false);
      setStatus(previous => previous ? { ...previous, connected: false, expires_at: null } : previous);
      setMessage("Siri access revoked. Your other connections are unchanged.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not disconnect Siri."); }
    finally { setBusy(false); }
  };

  const copy = async (value: string, label: string) => {
    try { await navigator.clipboard.writeText(value); setMessage(`${label} copied.`); }
    catch { setError("Could not copy automatically. Select the field and copy it manually."); }
  };

  return <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 text-sm" aria-label="Siri Shortcuts">
    <h2 className="font-medium">Ask TrojanClaw with Siri</h2>
    <p className="text-muted">Use an iPhone Shortcut to ask questions and hear answers. No TrojanClaw app is required.</p>
    {status?.connected && <p className="text-xs text-muted">Connected until {status.expires_at ? new Date(status.expires_at).toLocaleDateString() : "the key expires"}.</p>}
    {status && !status.configured && <p className="text-xs text-muted">A public HTTPS address needs to be configured before Siri can connect.</p>}
    <p className="text-xs text-muted">Creating a key allows question-and-answer access to your account’s saved sources for {status?.key_days ?? 30} days. It cannot sign in to sources or change calendars. Replacing a key disconnects shortcuts using the old key.</p>
    <div className="flex flex-wrap gap-2">
      <Button variant="secondary" disabled={!status?.configured || busy} onClick={() => void connect()}>{busy ? "Working…" : status?.connected ? "Replace Siri key" : "Create Siri access key"}</Button>
      {status?.connected && <Button variant="ghost" disabled={busy} onClick={() => void disconnect()}>Disconnect Siri</Button>}
    </div>
    {key && <div className="flex flex-col gap-2">
      <label className="text-xs font-medium" htmlFor="siri-authorization">Shortcut Authorization header</label>
      <input id="siri-authorization" type={visible ? "text" : "password"} readOnly value={`Bearer ${key}`} autoComplete="off" spellCheck={false}
        className="w-full rounded-xl border border-line bg-bg p-3 font-mono text-xs text-fg" />
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => void copy(`Bearer ${key}`, "Authorization header")}>Copy header</Button>
        <Button variant="ghost" onClick={() => setVisible(value => !value)}>{visible ? "Hide" : "Show"}</Button>
        <Button variant="ghost" onClick={() => { setKey(""); setVisible(false); }}>Clear from screen</Button>
      </div>
      <p className="text-xs text-muted">Copy this now; it clears from this screen after two minutes. The key is visible to anyone who can inspect your configured shortcut. Keep it private and don’t share a shortcut containing your key.</p>
    </div>}
    {status?.chat_url && <>
      <label className="text-xs font-medium" htmlFor="siri-chat-url">Shortcut URL</label>
      <input id="siri-chat-url" readOnly value={status.chat_url} className="w-full rounded-xl border border-line bg-bg p-3 font-mono text-xs text-fg" />
      <Button variant="ghost" onClick={() => void copy(status.chat_url!, "Shortcut URL")}>Copy URL</Button>
      <details className="text-xs leading-relaxed">
        <summary className="cursor-pointer font-medium">Set up “Ask TrojanClaw” on iPhone</summary>
        <p className="mt-3">In Apple Shortcuts, create a shortcut named <strong>Ask TrojanClaw</strong>. Add these actions in order:</p>
        <ol className="my-3 list-decimal space-y-3 pl-5">
          <li><strong>Ask for Input</strong>: type Text; prompt “What would you like to ask TrojanClaw?”</li>
          <li><strong>Get Contents of URL</strong>: paste the Shortcut URL above. Set Method to POST. Add headers <strong>Authorization</strong> (paste the copied Bearer value) and <strong>Content-Type</strong> = application/json. Set Request Body to JSON. Add <strong>question</strong> and choose the <strong>Provided Input</strong> variable from the first action, not those literal words. Add <strong>timezone</strong> = {effectiveTimezone(readSettings())}.</li>
          <li><strong>Get Dictionary Value</strong>: get <strong>answer</strong> from the URL response.</li>
          <li><strong>Speak Text</strong>: speak that dictionary value.</li>
        </ol>
        <p>Run it once with the phone unlocked and allow the requested access. Then say “Hey Siri, Ask TrojanClaw.” Each run asks one question. Keep the server online. Slow answers or a locked phone may require you to open Shortcuts.</p>
      </details>
    </>}
    {error && <p role="alert" className="text-xs text-error">{error}</p>}
    {message && <p role="status" className="text-xs text-muted">{message}</p>}
  </section>;
}
