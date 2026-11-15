import { useEffect, useState } from "react";
import type { ConnectorProvider } from "./api";

/** Preferences kept in this browser. */
export interface Settings {
  timezone: string; // "" follows this computer's time zone
  defaultSources: ConnectorProvider[]; // turned on for chat when they connect
  sidebarWidth: number; // px, on screens wide enough to show the sidebar
}

const KEY = "tc-settings";
export const SIDEBAR = { min: 220, max: 440, default: 256 };
const DEFAULTS: Settings = { timezone: "", defaultSources: ["brightspace", "handshake", "piazza"], sidebarWidth: SIDEBAR.default };

export function readSettings(): Settings {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "{}") as Partial<Settings>;
    return {
      timezone: typeof saved.timezone === "string" ? saved.timezone : DEFAULTS.timezone,
      defaultSources: Array.isArray(saved.defaultSources) ? saved.defaultSources : DEFAULTS.defaultSources,
      sidebarWidth: typeof saved.sidebarWidth === "number"
        ? Math.min(SIDEBAR.max, Math.max(SIDEBAR.min, saved.sidebarWidth)) : DEFAULTS.sidebarWidth,
    };
  } catch {
    return DEFAULTS;
  }
}

export function saveSettings(change: Partial<Settings>) {
  const next = { ...readSettings(), ...change };
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Not saved; it still applies until the page reloads.
  }
  window.dispatchEvent(new CustomEvent<Settings>("tc-settings", { detail: next }));
}

export function useSettings(): Settings {
  const [settings, setSettings] = useState(readSettings);
  useEffect(() => {
    const update = (event: Event) => setSettings((event as CustomEvent<Settings>).detail);
    window.addEventListener("tc-settings", update);
    return () => window.removeEventListener("tc-settings", update);
  }, []);
  return settings;
}

export const browserTimezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
export const effectiveTimezone = (settings: Settings) => settings.timezone || browserTimezone();
