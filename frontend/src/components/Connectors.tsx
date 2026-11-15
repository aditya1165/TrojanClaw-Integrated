import { useEffect, useRef, useState } from "react";
import { connectProvider, getConnectors } from "../lib/api";
import type { ConnectorProvider, ConnectorStatus } from "../lib/api";

const labels = { brightspace: "Brightspace", handshake: "Handshake", piazza: "Piazza" };
const order: ConnectorProvider[] = ["brightspace", "handshake", "piazza"];
const stateLabels: Record<ConnectorStatus["state"], string> = {
  not_configured: "Not connected", configured: "Ready to sign in", saved: "Session saved",
  connecting: "Waiting for sign-in…", connected: "Connected", needs_login: "Sign-in needed",
  unavailable: "Coming soon",
};

export function Connectors({ selected, onChange }: {
  selected: ConnectorProvider[];
  onChange: (providers: ConnectorProvider[]) => void;
}) {
  const [connectors, setConnectors] = useState<ConnectorStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<ConnectorProvider[]>([]);
  const previousConnected = useRef(new Set<ConnectorProvider>());

  useEffect(() => {
    if (!connectors.length) return;
    const ready = new Set(connectors.filter((item) => item.state === "connected").map((item) => item.provider));
    const usable = new Set(connectors.filter((item) => item.state === "connected" || item.state === "saved").map((item) => item.provider));
    const next = selected.filter((provider) => usable.has(provider));
    ready.forEach((provider) => {
      if (!previousConnected.current.has(provider) && !next.includes(provider)) next.push(provider);
    });
    previousConnected.current = ready;
    if (next.join() !== selected.join()) onChange(next);
  }, [connectors, selected, onChange]);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const items = await getConnectors();
        if (active) { setConnectors(items); setError(null); }
      } catch {
        if (active) setError("Connectors are unavailable. Check that the local backend is running.");
      }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const act = async (provider: ConnectorProvider, action: "connect" | "verify") => {
    setBusy((items) => [...items, provider]);
    setError(null);
    try {
      await connectProvider(provider, action);
      setConnectors(await getConnectors());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not connect.");
    } finally {
      setBusy((items) => items.filter((item) => item !== provider));
    }
  };

  return <section className="connectors-panel" aria-label="Chat connectors">
    <div className="connectors-heading"><strong>Your connections</strong><span>Sign in once. Reuse your session.</span></div>
    <div className="connector-grid">
      {order.map((provider) => {
        const connector = connectors.find((item) => item.provider === provider);
        const state = connector?.state ?? "not_configured";
        const waiting = busy.includes(provider) || state === "connecting";
        const disabled = !connector;
        const enabled = state === "connected" || state === "saved";
        return <article className={`connector-card ${state}`} key={provider}>
          <div className="connector-title"><strong>{labels[provider]}</strong><span className="connector-dot" /></div>
          <p role="status">{stateLabels[state]}</p>
          <>
            <div className="connector-actions">
              <button type="button" disabled={disabled || waiting}
                onClick={() => void act(provider, "connect")}>
                {waiting ? "Signing in…" : enabled ? "Sign in again" : "Connect"}
              </button>
              {enabled && <button type="button" disabled={waiting}
                onClick={() => void act(provider, "verify")}>Check session</button>}
            </div>
            <label className="connector-toggle"><input type="checkbox"
              checked={selected.includes(provider) && enabled} disabled={!enabled || waiting}
              onChange={(event) => onChange(event.target.checked
                ? [...selected.filter((item) => item !== provider), provider]
                : selected.filter((item) => item !== provider))} />Use in chat</label>
          </>
          {connector?.message && state !== "unavailable" && <small>{connector.message}</small>}
        </article>;
      })}
    </div>
    <p className="connector-note">Sign in with USC connects Brightspace and Handshake automatically. Piazza has a separate sign-in in Chromium. Reading Piazza threads tries to preserve unread status.</p>
    {error && <p className="error-text" role="alert">{error}</p>}
  </section>;
}
