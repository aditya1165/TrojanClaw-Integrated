import { useEffect, useMemo } from "react";
import type { AccountProfile, ConnectorProvider } from "../lib/api";
import { browserTimezone, saveSettings, useSettings } from "../lib/settings";
import type { Theme } from "../theme";
import { Button, CloseIcon } from "./ui";
import { AssistantConnection } from "./AssistantConsent";
import { SiriConnection } from "./SiriConnection";

const SHORTCUTS: [string, string][] = [["⌘ K", "New chat"], ["⌘ /", "Type a message"], ["⌘ ⇧ F", "Search chats"], ["Esc", "Close a window"]];
const SOURCES: [ConnectorProvider, string][] = [["brightspace", "Brightspace"], ["handshake", "Handshake"], ["piazza", "Piazza"]];

interface SettingsDialogProps {
  account: AccountProfile;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  onSignOut: () => void;
  signingOut: boolean;
  onClose: () => void;
}

export default function SettingsDialog({ account, theme, onTheme, onSignOut, signingOut, onClose }: SettingsDialogProps) {
  const settings = useSettings();
  const zones = useMemo(() => {
    const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
    return supported.length ? supported : ["America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York", "UTC"];
  }, []);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  const toggleSource = (provider: ConnectorProvider, on: boolean) => saveSettings({
    defaultSources: on ? [...settings.defaultSources.filter((item) => item !== provider), provider]
      : settings.defaultSources.filter((item) => item !== provider),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section role="dialog" aria-modal="true" aria-labelledby="settings-title"
        className="flex max-h-full w-full max-w-lg flex-col gap-6 overflow-y-auto rounded-2xl bg-surface p-6 text-fg shadow-xl sm:p-7">
        <div className="flex items-center justify-between gap-4">
          <h2 id="settings-title" className="text-[22px] font-semibold tracking-[-0.02em]">Settings</h2>
          <button type="button" aria-label="Close settings" onClick={onClose}
            className="flex h-11 w-11 items-center justify-center rounded-xl bg-subtle text-muted hover:text-fg"><CloseIcon /></button>
        </div>

        <Section title="Appearance">
          <div role="radiogroup" aria-label="Theme" className="grid grid-cols-2 gap-1 rounded-xl bg-subtle p-1">
            {(["light", "dark"] as Theme[]).map((value) => (
              <button key={value} type="button" role="radio" aria-checked={theme === value} onClick={() => onTheme(value)}
                className={`min-h-10 rounded-lg text-sm font-medium capitalize ${theme === value ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg"}`}>
                {value}
              </button>
            ))}
          </div>
        </Section>

        <Section title="Time zone" note="Used for dates and times in chat and for calendar drafts.">
          <select aria-label="Time zone" value={settings.timezone} onChange={(event) => saveSettings({ timezone: event.target.value })}
            className="min-h-11 w-full rounded-xl border border-line bg-bg px-3 text-sm text-fg">
            <option value="">Automatic ({browserTimezone()})</option>
            {zones.map((zone) => <option key={zone} value={zone}>{zone.replaceAll("_", " ")}</option>)}
          </select>
        </Section>

        <Section title="Default sources" note="Turned on in chat when they connect. You can still change them in any chat.">
          {SOURCES.map(([provider, label]) => (
            <label key={provider} className="flex min-h-10 cursor-pointer items-center justify-between text-sm">
              {label}
              <input type="checkbox" className="h-[18px] w-[18px] accent-accent" checked={settings.defaultSources.includes(provider)}
                onChange={(event) => toggleSource(provider, event.target.checked)} />
            </label>
          ))}
        </Section>

        <AssistantConnection key={`assistant-${account.id}`} />
        <SiriConnection key={`siri-${account.id}`} />

        <Section title="Keyboard shortcuts" note="Use Ctrl instead of ⌘ on Windows.">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            {SHORTCUTS.map(([keys, action]) => (
              <div key={keys} className="contents">
                <dt><kbd className="rounded-md border border-line bg-subtle px-1.5 py-0.5 font-mono text-xs">{keys}</kbd></dt>
                <dd className="text-muted">{action}</dd>
              </div>
            ))}
          </dl>
        </Section>

        <Section title="Account">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0 text-sm"><p className="truncate font-medium">{account.name}</p><p className="truncate text-xs text-muted">@{account.username}</p></div>
            <Button variant="secondary" onClick={onSignOut} disabled={signingOut}>{signingOut ? "Signing out…" : "Sign out"}</Button>
          </div>
        </Section>
      </section>
    </div>
  );
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5">
      <div>
        <h3 className="text-xs font-semibold tracking-[0.06em] text-muted uppercase">{title}</h3>
        {note && <p className="mt-1 text-xs text-muted">{note}</p>}
      </div>
      {children}
    </section>
  );
}
