import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ConnectorProvider } from "../lib/api";
import { activeSources, INFO, usable, USC, type Sources, type State } from "../lib/sources";
import { EventsDialog } from "../EngageSCPanel";
import { Button, CloseIcon, LockIcon } from "./ui";

const LABEL: Record<State, string> = {
  connected: "Connected", saved: "Session saved", connecting: "Signing in…", needs_login: "Sign-in needed",
  not_configured: "Not connected", configured: "Not connected", unavailable: "Coming soon",
};
const DOT: Record<State, string> = {
  connected: "bg-ok", saved: "bg-gold", connecting: "bg-gold", needs_login: "bg-error",
  not_configured: "bg-muted", configured: "bg-muted", unavailable: "bg-muted",
};

function Tile({ id, small = false }: { id: keyof typeof INFO; small?: boolean }) {
  return (
    <span aria-hidden="true" className={`flex flex-none items-center justify-center font-bold text-white ${INFO[id].tile} ${small ? "h-7 w-7 rounded-lg text-xs" : "h-10 w-10 rounded-xl text-sm"}`}>
      {INFO[id].initial}
    </span>
  );
}

function Status({ state }: { state: State }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      <span className={`h-1.5 w-1.5 rounded-full ${DOT[state]}`} />
      {LABEL[state]}
    </span>
  );
}

function MenuRow({ id, note, children }: { id: keyof typeof INFO; note: string; children?: ReactNode }) {
  return (
    <>
      <Tile id={id} small />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-sm font-medium">{INFO[id].name}</span>
        <span className="truncate text-xs text-muted">{note}</span>
      </span>
      {children}
    </>
  );
}

const ROW = "flex min-h-12 items-center gap-3 rounded-xl px-3";
const CHECK = "h-[18px] w-[18px] flex-none accent-accent";
const LINK = "min-h-9 flex-none text-[13px] font-medium text-accent-fg hover:underline disabled:opacity-50";

/** Option C: the "Sources" button in the chat box and its checklist. */
export function SourcesMenu({ sources, selected, onChange, onManage }: {
  sources: Sources;
  selected: ConnectorProvider[];
  onChange: (providers: ConnectorProvider[]) => void;
  onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const count = activeSources(sources, selected).length;

  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  const manage = () => { setOpen(false); onManage(); };
  const toggle = (provider: ConnectorProvider, on: boolean) =>
    onChange(on ? [...selected.filter((item) => item !== provider), provider] : selected.filter((item) => item !== provider));

  return (
    <div ref={ref} className="relative">
      <button type="button" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}
        className={`flex min-h-9 items-center rounded-full border px-3 text-[13px] font-medium transition ${count ? "border-accent-fg/40 bg-accent-soft text-accent-fg" : "border-line text-muted hover:text-fg"}`}>
        Sources{count ? ` · ${count}` : ""}
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-20 mb-2 flex w-80 max-w-[calc(100vw-3rem)] flex-col rounded-2xl border border-line bg-surface p-2 shadow-xl">
          <p className="px-3 py-2 text-xs font-semibold text-muted">Use in chat</p>
          {USC.map((provider) => {
            const state = sources.stateOf(provider);
            return usable(state) ? (
              <label key={provider} className={`${ROW} cursor-pointer hover:bg-subtle`}>
                <MenuRow id={provider} note={LABEL[state]} />
                <input type="checkbox" className={CHECK} checked={selected.includes(provider)}
                  onChange={(event) => toggle(provider, event.target.checked)} />
              </label>
            ) : (
              <div key={provider} className={ROW}>
                <MenuRow id={provider} note={LABEL[state]}>
                  {state !== "connecting" && <button type="button" className={LINK} onClick={manage}>Connect</button>}
                </MenuRow>
              </div>
            );
          })}
          <label className={`${ROW} cursor-pointer hover:bg-subtle`}>
            <MenuRow id="engagesc" note="Public campus events" />
            <input type="checkbox" className={CHECK} checked={Boolean(sources.engage)}
              disabled={sources.engage === null || sources.busy.includes("engagesc")}
              onChange={(event) => void sources.setEngage(event.target.checked)} />
          </label>
          <div className={ROW}>
            <MenuRow id="calendar" note={sources.calendar?.connected ? "Drafts events for your approval" : "Not connected"}>
              {!sources.calendar?.connected && <button type="button" className={LINK} onClick={manage}>Connect</button>}
            </MenuRow>
          </div>
          <div className={ROW}><MenuRow id="piazza" note="Coming soon" /></div>
          {sources.error && <p role="alert" className="px-3 py-2 text-xs text-error">{sources.error}</p>}
          <div className="mt-1 border-t border-line pt-1">
            <button type="button" onClick={manage} className="flex min-h-11 w-full items-center rounded-xl px-3 text-[13px] text-muted hover:bg-subtle hover:text-fg">
              Manage connections
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Option A: the Sources window for connecting, reconnecting and turning sources on or off. */
export function SourcesDialog({ sources, selected, onChange, onClose }: {
  sources: Sources;
  selected: ConnectorProvider[];
  onChange: (providers: ConnectorProvider[]) => void;
  onClose: () => void;
}) {
  const [showEvents, setShowEvents] = useState(false);
  const { calendar, engage } = sources;

  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !showEvents) onClose(); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [showEvents, onClose]);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 px-4 py-6"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section role="dialog" aria-modal="true" aria-labelledby="sources-title"
        className="flex max-h-full w-full max-w-xl flex-col gap-5 overflow-y-auto rounded-2xl bg-surface p-6 text-fg shadow-xl sm:p-7">
        <div className="flex items-start gap-4">
          <div className="flex flex-1 flex-col gap-1.5">
            <h2 id="sources-title" className="text-[22px] font-semibold tracking-[-0.02em]">Your sources</h2>
            <p className="text-sm leading-relaxed text-muted">TrojanClaw reads these to answer you. Turn one off to leave it out of chat.</p>
          </div>
          <button type="button" aria-label="Close" onClick={onClose}
            className="flex h-11 w-11 flex-none items-center justify-center rounded-xl bg-subtle text-muted hover:text-fg">
            <CloseIcon />
          </button>
        </div>

        <h3 className="text-xs font-semibold tracking-[0.06em] text-muted uppercase">Connected with your USC login</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          {USC.map((provider) => {
            const state = sources.stateOf(provider);
            const message = sources.messageOf(provider);
            const waiting = state === "connecting" || sources.busy.includes(provider);
            return (
              <article key={provider} className="flex flex-col gap-3 rounded-2xl border border-line p-4">
                <div className="flex items-center gap-3">
                  <Tile id={provider} />
                  <div className="flex min-w-0 flex-col gap-0.5">
                    <strong className="text-[15px] font-semibold">{INFO[provider].name}</strong>
                    <Status state={state} />
                  </div>
                </div>
                <p className="text-[13px] leading-snug text-muted">{INFO[provider].about}</p>
                {message && state !== "connected" && <p className="text-xs break-words text-muted">{message}</p>}
                {usable(state) ? (
                  <>
                    <label className="flex min-h-8 cursor-pointer items-center justify-between text-[13px]">
                      Use in chat
                      <input type="checkbox" className={CHECK} checked={selected.includes(provider)}
                        onChange={(event) => onChange(event.target.checked
                          ? [...selected.filter((item) => item !== provider), provider]
                          : selected.filter((item) => item !== provider))} />
                    </label>
                    <div className="flex flex-wrap gap-x-4">
                      <button type="button" className={LINK} disabled={waiting} onClick={() => void sources.connect(provider, "verify")}>Check session</button>
                      <button type="button" className={LINK} disabled={waiting} onClick={() => void sources.connect(provider, "connect")}>Sign in again</button>
                    </div>
                  </>
                ) : (
                  <Button variant="secondary" disabled={waiting || !sources.connectors} onClick={() => void sources.connect(provider, "connect")}>
                    {waiting ? "Signing in…" : state === "needs_login" ? "Sign in again" : "Connect"}
                  </Button>
                )}
              </article>
            );
          })}
        </div>

        <h3 className="mt-1 text-xs font-semibold tracking-[0.06em] text-muted uppercase">More sources</h3>
        <div className="flex flex-col divide-y divide-line rounded-2xl border border-line">
          <div className="flex flex-wrap items-center gap-3 px-4 py-3">
            <Tile id="calendar" />
            <div className="flex min-w-[180px] flex-1 flex-col gap-0.5">
              <strong className="text-sm font-semibold">{INFO.calendar.name}</strong>
              <span className="text-xs break-words text-muted">
                {calendar?.connected ? `Connected: ${calendar.calendar}` : calendar && !calendar.configured ? "Needs setup on the server." : INFO.calendar.about}
              </span>
            </div>
            {calendar?.connected ? (
              <button type="button" className={LINK} disabled={sources.busy.includes("calendar")} onClick={() => void sources.setCalendar(false)}>Disconnect</button>
            ) : (
              <Button variant="secondary" className="min-h-9 px-3 text-[13px]" disabled={!calendar?.configured || sources.busy.includes("calendar")}
                onClick={() => void sources.setCalendar(true)}>
                {sources.busy.includes("calendar") ? "Opening…" : "Connect"}
              </Button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3 px-4 py-3">
            <Tile id="engagesc" />
            <div className="flex min-w-[180px] flex-1 flex-col gap-0.5">
              <strong className="text-sm font-semibold">{INFO.engagesc.name}</strong>
              <span className="text-xs text-muted">{INFO.engagesc.about}</span>
            </div>
            {engage && <button type="button" className={LINK} onClick={() => setShowEvents(true)}>View events</button>}
            <button type="button" className={LINK} disabled={engage === null || sources.busy.includes("engagesc")}
              onClick={() => void sources.setEngage(!engage)}>
              {engage ? "Turn off" : "Turn on"}
            </button>
          </div>
          <div className="flex items-center gap-3 px-4 py-3">
            <Tile id="piazza" />
            <div className="flex flex-1 flex-col gap-0.5">
              <strong className="text-sm font-semibold">{INFO.piazza.name}</strong>
              <span className="text-xs text-muted">{INFO.piazza.about}</span>
            </div>
            <span className="rounded-full bg-subtle px-2.5 py-1 text-xs text-muted">Coming soon</span>
          </div>
        </div>

        {sources.error && <p role="alert" className="text-sm text-error">{sources.error}</p>}

        <div className="flex flex-wrap items-center gap-3">
          <p className="flex min-w-[220px] flex-1 items-center gap-2 text-xs text-muted">
            <LockIcon />
            Read-only. Calendar changes always need your approval.
          </p>
          <Button onClick={onClose} className="px-6">Done</Button>
        </div>
      </section>
      {showEvents && <EventsDialog onClose={() => setShowEvents(false)} />}
    </div>
  );
}
