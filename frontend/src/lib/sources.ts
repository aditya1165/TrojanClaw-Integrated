import { useEffect, useRef, useState } from "react";
import { apiRequest, connectProvider, getConnectors } from "./api";
import type { CalendarStatus, ConnectorProvider, ConnectorStatus } from "./api";
import { readSettings } from "./settings";

export type State = ConnectorStatus["state"];

export const INFO: Record<ConnectorProvider | "calendar" | "engagesc", { name: string; initial: string; tile: string; about: string }> = {
  brightspace: { name: "Brightspace", initial: "B", tile: "bg-[#2b5f8a]", about: "Courses, assignments, grades and announcements." },
  handshake: { name: "Handshake", initial: "H", tile: "bg-[#1f7a4d]", about: "Jobs, internships and career events." },
  piazza: { name: "Piazza", initial: "P", tile: "bg-[#3e6fb0]", about: "Class Q&A threads. Signs in separately, not with your USC login." },
  calendar: { name: "Google Calendar", initial: "G", tile: "bg-[#5c5752]", about: "Add events to your calendar after you approve them." },
  engagesc: { name: "EngageSC", initial: "E", tile: "bg-[#990000]", about: "Public USC campus events. No sign-in needed." },
};
export const USC: ConnectorProvider[] = ["brightspace", "handshake"]; // connected by Sign in with USC
export const LIVE: ConnectorProvider[] = ["brightspace", "handshake", "piazza"]; // usable in chat

export const usable = (state?: State) => state === "connected" || state === "saved";

/** Connection state for every source, shared by the chat-box menu and the Sources window. */
export function useSources(selected: ConnectorProvider[], onChange: (providers: ConnectorProvider[]) => void) {
  const [connectors, setConnectors] = useState<ConnectorStatus[] | null>(null);
  const [engage, setEngageState] = useState<boolean | null>(null);
  const [calendar, setCalendarState] = useState<CalendarStatus | null>(null);
  const [error, setError] = useState(() => new URLSearchParams(window.location.search).get("calendar_error") || "");
  const [busy, setBusy] = useState<string[]>([]);
  const previousConnected = useRef(new Set<ConnectorProvider>());

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const items = await getConnectors();
        if (active) setConnectors(items);
      } catch {
        if (active) setError("Could not check your sources. Is the backend running?");
      }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let active = true;
    apiRequest<{ connected: boolean }>("/integrations/engagesc")
      .then((data) => { if (active) setEngageState(data.connected); })
      .catch(() => { if (active) setEngageState(false); });
    apiRequest<CalendarStatus>("/calendar/status")
      .then((value) => { if (active) setCalendarState(value); })
      .catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, []);

  // Drop sources that stopped working; turn on ones that just connected.
  useEffect(() => {
    if (!connectors) return;
    // Saved sessions count too: after a restart they show "Session saved" and chat should still use them.
    const ready = new Set(connectors.filter((item) => usable(item.state)).map((item) => item.provider));
    const next = selected.filter((provider) => usable(connectors.find((item) => item.provider === provider)?.state));
    ready.forEach((provider) => {
      // Newly connected sources are turned on if Settings lists them as defaults.
      if (!previousConnected.current.has(provider) && !next.includes(provider) && readSettings().defaultSources.includes(provider)) next.push(provider);
    });
    previousConnected.current = ready;
    if (next.join() !== selected.join()) onChange(next);
  }, [connectors, selected, onChange]);

  const run = async (key: string, task: () => Promise<void>) => {
    setBusy((items) => [...items, key]);
    setError("");
    try {
      await task();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Something went wrong. Please try again.");
    } finally {
      setBusy((items) => items.filter((item) => item !== key));
    }
  };

  return {
    connectors, engage, calendar, error, busy,
    stateOf: (provider: ConnectorProvider): State =>
      connectors?.find((item) => item.provider === provider)?.state ?? "not_configured",
    messageOf: (provider: ConnectorProvider) => connectors?.find((item) => item.provider === provider)?.message ?? null,
    connect: (provider: ConnectorProvider, action: "connect" | "verify") => run(provider, async () => {
      await connectProvider(provider, action);
      setConnectors(await getConnectors());
    }),
    setEngage: (link: boolean) => run("engagesc", async () => {
      const status = await apiRequest<{ connected: boolean }>(`/integrations/engagesc/${link ? "connect" : "disconnect"}`, {});
      setEngageState(status.connected);
    }),
    setCalendar: (connect: boolean) => run("calendar", async () => {
      if (connect) {
        const { url } = await apiRequest<{ url: string }>("/calendar/connect", {});
        window.location.assign(url);
        return;
      }
      await apiRequest<void>("/calendar/disconnect", {});
      setCalendarState(await apiRequest<CalendarStatus>("/calendar/status"));
      window.dispatchEvent(new Event("calendar-updated"));
    }),
  };
}

export type Sources = ReturnType<typeof useSources>;

/** Names of the sources the next message will use. */
export function activeSources(sources: Sources, selected: ConnectorProvider[]): string[] {
  const names = LIVE.filter((provider) => selected.includes(provider) && usable(sources.stateOf(provider))).map((provider) => INFO[provider].name);
  if (sources.engage) names.push(INFO.engagesc.name);
  if (sources.calendar?.connected) names.push(INFO.calendar.name);
  return names;
}

