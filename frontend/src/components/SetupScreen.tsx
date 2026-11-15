import { useEffect, useState } from "react";
import { getConnectors, type ConnectorProvider, type ConnectorStatus } from "../lib/api";
import { Button } from "./ui";

// Sources the backend connects automatically after Sign in with USC.
const SETUP: { provider: ConnectorProvider; label: string }[] = [
  { provider: "brightspace", label: "Brightspace" },
  { provider: "handshake", label: "Handshake" },
];

interface SetupScreenProps {
  // Called with the sources that ended up connected (they are turned on for chat).
  onDone: (connected: ConnectorProvider[]) => void;
}

export default function SetupScreen({ onDone }: SetupScreenProps) {
  const [statuses, setStatuses] = useState<ConnectorStatus[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const items = await getConnectors();
        if (active) { setStatuses(items); setError(""); }
      } catch {
        if (active) setError("Could not check your sources. Is the backend running?");
      }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const stateOf = (provider: ConnectorProvider) => statuses?.find((item) => item.provider === provider);
  const finished = statuses !== null && SETUP.every(({ provider }) => stateOf(provider)?.state !== "connecting");
  const connected = SETUP.map(({ provider }) => provider).filter((provider) => stateOf(provider)?.state === "connected");
  const allConnected = finished && connected.length === SETUP.length;

  // Everything worked: continue by itself after a moment.
  useEffect(() => {
    if (!allConnected) return;
    const timer = window.setTimeout(() => onDone(connected), 1200);
    return () => window.clearTimeout(timer);
  }, [allConnected]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 px-4 backdrop-blur-sm">
      <section role="dialog" aria-modal="true" aria-labelledby="setup-title"
        className="flex w-full max-w-md flex-col gap-6 rounded-2xl border border-line bg-surface p-8 text-fg shadow-xl">
        <div className="flex flex-col gap-2">
          <h2 id="setup-title" className="text-2xl font-semibold tracking-[-0.02em]">
            {finished ? (allConnected ? "You’re all set" : "Almost done") : "Setting things up for you…"}
          </h2>
          <p className="text-sm leading-relaxed text-muted">
            {finished
              ? allConnected ? "Your sources are connected." : "You can connect the rest later from your sources."
              : "Connecting your USC sources with the login you just did. This takes a few seconds."}
          </p>
        </div>

        <ul className="flex flex-col gap-3" aria-live="polite">
          {SETUP.map(({ provider, label }) => {
            const status = stateOf(provider);
            const state = status?.state ?? "connecting";
            return (
              <li key={provider} className="flex items-center gap-3 rounded-xl bg-subtle px-4 py-3">
                <StatusMark state={state} />
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="text-[15px] font-medium">{label}</span>
                  <span className="text-[13px] text-muted">
                    {state === "connected" ? "Connected"
                      : state === "connecting" ? (status?.message ?? "Setting up…")
                      : (status?.message ?? "Connect later")}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>

        {error && <p role="alert" className="text-sm text-error">{error}</p>}

        <div className="flex justify-end gap-2">
          {finished
            ? <Button onClick={() => onDone(connected)}>Continue</Button>
            : <Button variant="ghost" onClick={() => onDone(connected)}>Skip for now</Button>}
        </div>
      </section>
    </div>
  );
}

function StatusMark({ state }: { state: ConnectorStatus["state"] }) {
  if (state === "connected") {
    return (
      <span className="flex h-7 w-7 flex-none items-center justify-center rounded-full bg-ok-soft text-ok">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12l5 5 9-10" /></svg>
      </span>
    );
  }
  if (state === "connecting") {
    return (
      <span className="flex h-7 w-7 flex-none items-center justify-center text-accent-fg">
        <svg className="animate-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2.4" strokeLinecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9" /></svg>
      </span>
    );
  }
  return <span className="flex h-7 w-7 flex-none items-center justify-center rounded-full bg-line text-muted" aria-hidden="true">–</span>;
}
