import { useEffect, useRef, useState, type ReactNode } from "react";
import { apiRequest, type ConnectorProvider } from "../lib/api";
import { activeSources, INFO, LIVE, usable, type Sources, type State } from "../lib/sources";
import { EventsDialog } from "./EventsDialog";
import { CloseIcon } from "./ui";
import { toast } from "../lib/toast";

const LABEL: Record<State, string> = {
  revoking: "Disconnecting…",
  connected: "Connected", saved: "Session saved", connecting: "Signing in…", needs_login: "Sign-in needed",
  not_configured: "Not connected", configured: "Not connected", unavailable: "Coming soon",
};
const DOT: Record<State, string> = {
  revoking: "bg-gold",
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
  // Connect or resume the private sign-in view from the existing menu.
  const connectNow = (provider: ConnectorProvider) => {
    void sources.connect(provider, "connect");
    toast(`Opening ${INFO[provider].name} sign-in…`);
  };
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
          {LIVE.map((provider) => {
            const state = sources.stateOf(provider);
            return usable(state) ? (
              <div key={provider} className={ROW}>
                <MenuRow id={provider} note={LABEL[state]}>
                  <Switch label={`Use ${INFO[provider].name} in chat`} on={selected.includes(provider)} onChange={(on) => toggle(provider, on)} />
                </MenuRow>
              </div>
            ) : (
              <div key={provider} className={ROW} title={sources.messageOf(provider) ?? undefined}>
                <MenuRow id={provider} note={LABEL[state]}>
                  {sources.busy.includes(provider)
                    ? <span className="flex-none text-[13px] text-muted">Signing in…</span>
                    : <button type="button" className={LINK} onClick={() => connectNow(provider)}>{state === "connecting" ? "Continue sign-in" : state === "needs_login" ? "Reconnect" : "Connect"}</button>}
                </MenuRow>
              </div>
            );
          })}
          <div className={ROW}>
            <MenuRow id="engagesc" note="Campus events">
              <Switch label="Use EngageSC in chat" on={Boolean(sources.engage)} onChange={(on) => void sources.setEngage(on)}
                disabled={sources.engage === null || sources.busy.includes("engagesc")} />
            </MenuRow>
          </div>
          <div className={ROW}>
            <MenuRow id="calendar" note={sources.calendar?.connected ? "Connected" : "Not connected"}>
              {!sources.calendar?.connected && (
                <button type="button" className={LINK} disabled={!sources.calendar?.configured || sources.busy.includes("calendar")}
                  onClick={() => void sources.setCalendar(true)}>
                  {sources.busy.includes("calendar") ? "Opening…" : "Connect"}
                </button>
              )}
            </MenuRow>
          </div>
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

/** The Sources window: one row per source, with one control each. */
export function SourcesDialog({ sources, selected, onChange, onClose }: {
  sources: Sources;
  selected: ConnectorProvider[];
  onChange: (providers: ConnectorProvider[]) => void;
  onClose: () => void;
}) {
  const [showEvents, setShowEvents] = useState(false);
  const { calendar, engage } = sources;
  const toggle = (provider: ConnectorProvider, on: boolean) =>
    onChange(on ? [...selected.filter((item) => item !== provider), provider] : selected.filter((item) => item !== provider));

  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !showEvents) onClose(); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [showEvents, onClose]);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 px-4 py-6"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section role="dialog" aria-modal="true" aria-labelledby="sources-title"
        className="flex max-h-full w-full max-w-md flex-col gap-4 overflow-y-auto rounded-2xl bg-surface p-5 text-fg shadow-xl">
        <div className="flex items-center justify-between">
          <h2 id="sources-title" className="text-lg font-semibold tracking-[-0.01em]">Sources</h2>
          <button type="button" aria-label="Close" onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-subtle hover:text-fg">
            <CloseIcon />
          </button>
        </div>

        <ul className="flex flex-col divide-y divide-line rounded-xl border border-line">
          {LIVE.map((provider) => {
            const state = sources.stateOf(provider);
            const waiting = sources.busy.includes(provider) || state === "revoking";
            return (
              <Row key={provider} id={provider} status={<Status state={state} />} hint={sources.messageOf(provider)}>
                {usable(state) && !waiting
                  ? <Switch label={`Use ${INFO[provider].name} in chat`} on={selected.includes(provider)} onChange={(on) => toggle(provider, on)} />
                  : <SmallButton disabled={waiting || !sources.connectors} onClick={() => void sources.connect(provider, "connect")}>
                      {waiting ? LABEL[state] : state === "connecting" ? "Continue sign-in" : state === "needs_login" ? "Reconnect" : "Connect"}
                    </SmallButton>}
              </Row>
            );
          })}

          <Row id="calendar" status={<span className="truncate">{calendar?.connected ? calendar.calendar : calendar && !calendar.configured ? "Not set up" : "Not connected"}</span>}>
            {calendar?.connected
              ? <button type="button" className={LINK} disabled={sources.busy.includes("calendar")} onClick={() => void sources.setCalendar(false)}>Disconnect</button>
              : <SmallButton disabled={!calendar?.configured || sources.busy.includes("calendar")} onClick={() => void sources.setCalendar(true)}>
                  {sources.busy.includes("calendar") ? "Opening…" : "Connect"}
                </SmallButton>}
          </Row>

          <Row id="engagesc" status={engage ? <button type="button" className="text-accent-fg hover:underline" onClick={() => setShowEvents(true)}>Browse events</button> : "Campus events"}>
            <Switch label="Use EngageSC in chat" on={Boolean(engage)} disabled={engage === null || sources.busy.includes("engagesc")}
              onChange={(on) => void sources.setEngage(on)} />
          </Row>

          <WebRegRow />
        </ul>

        {sources.error && <p role="alert" className="text-sm text-error">{sources.error}</p>}
      </section>
      {showEvents && <EventsDialog onClose={() => setShowEvents(false)} />}
    </div>
  );
}

function Row({ id, status, hint, children }: { id: keyof typeof INFO | "webreg"; status: ReactNode; hint?: string | null; children?: ReactNode }) {
  return (
    <li className="flex min-h-14 items-center gap-3 px-3.5 py-2.5" title={hint ?? undefined}>
      {id === "webreg"
        ? <span aria-hidden="true" className="flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-[#7a1f1f] text-xs font-bold text-white">W</span>
        : <Tile id={id} small />}
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="text-sm font-medium">{id === "webreg" ? "WebReg" : INFO[id].name}</span>
        <span className="flex min-w-0 text-xs text-muted">{status}</span>
      </div>
      {children}
    </li>
  );
}

function Switch({ on, onChange, label, disabled = false }: { on: boolean; onChange: (on: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)}
      className={`relative h-6 w-10 flex-none rounded-full transition disabled:opacity-50 ${on ? "bg-accent" : "bg-line"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

function SmallButton({ children, ...props }: { children: ReactNode; disabled?: boolean; onClick: () => void }) {
  return (
    <button type="button" {...props}
      className="min-h-8 flex-none rounded-lg border border-line px-3 text-[13px] font-medium hover:bg-subtle disabled:opacity-50">
      {children}
    </button>
  );
}

/** WebReg: the course bin is read at each Sign in with USC (read-only), so there is nothing to toggle. */
function WebRegRow() {
  const [info, setInfo] = useState<{ connected: boolean; terms: { term: string; courses: number }[] } | null>(null);
  useEffect(() => {
    let active = true;
    apiRequest<typeof info>("/integrations/webreg").then((value) => { if (active) setInfo(value); }).catch(() => {});
    return () => { active = false; };
  }, []);
  const term = info?.terms.find((item) => item.courses > 0);
  return (
    <Row id="webreg" status={!info ? "Checking…" : !info.connected ? "Loads when you sign in"
      : term ? `${term.term} · ${term.courses} course${term.courses === 1 ? "" : "s"}` : "Course bin is empty"} />
  );
}
