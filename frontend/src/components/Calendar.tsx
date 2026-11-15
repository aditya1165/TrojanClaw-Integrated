import { useEffect, useRef, useState, type FormEvent } from "react";
import { apiRequest } from "../lib/api";
import type { CalendarEventDraft, CalendarProposal } from "../lib/api";
import { Button, CalendarIcon } from "./ui";

const weekdayNames: Record<string, string> = { MO: "Monday", TU: "Tuesday", WE: "Wednesday", TH: "Thursday", FR: "Friday", SA: "Saturday", SU: "Sunday" };

function localTime(value: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(value);
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

function zonedTime(value: string, timezone: string) {
  // Resolve wall time using the selected zone on that date, including DST.
  // Ambiguous/nonexistent times require a different time instead of guessing.
  const wall = new Date(`${value}Z`).getTime();
  const matches = new Set<number>();
  for (const hours of [-24, 0, 24]) {
    const sample = wall + hours * 3600000;
    const offset = new Date(`${localTime(new Date(sample), timezone)}Z`).getTime() - sample;
    const instant = wall - offset;
    if (localTime(new Date(instant), timezone) === value) matches.add(instant);
  }
  if (matches.size !== 1) throw new Error("This time is skipped or occurs twice because clocks change. Choose another time.");
  const instant = [...matches][0];
  const offsetMinutes = (wall - instant) / 60000;
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const hour = String(Math.floor(Math.abs(offsetMinutes) / 60)).padStart(2, "0");
  const minute = String(Math.abs(offsetMinutes) % 60).padStart(2, "0");
  return `${value}:00${sign}${hour}:${minute}`;
}

/** Parts of an event's time for display: "Mon, Nov 16" · "3:00 – 3:20 PM" · "PST" · "20 min". */
function when(start: string, end: string, timezone: string) {
  const a = new Date(start), b = new Date(end);
  const part = (value: Date, options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat("en-US", { timeZone: timezone, ...options }).format(value);
  const day = (value: Date) => part(value, { weekday: "short", month: "short", day: "numeric" });
  const clock = (value: Date) => part(value, { hour: "numeric", minute: "2-digit" });
  const sameDay = day(a) === day(b);
  const period = (value: Date) => clock(value).slice(-2);
  const time = sameDay
    ? `${period(a) === period(b) ? clock(a).slice(0, -3) : clock(a)} – ${clock(b)}`
    : `${clock(a)} – ${day(b)}, ${clock(b)}`;
  const zone = part(a, { timeZoneName: "short" }).split(" ").pop() ?? timezone;
  const minutes = Math.round((b.getTime() - a.getTime()) / 60000);
  const length = minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} hr${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
  return {
    date: day(a), time, zone, length,
    month: part(a, { month: "short" }).toUpperCase(), dayNumber: part(a, { day: "numeric" }), weekday: part(a, { weekday: "short" }),
  };
}

function repeatText(event: CalendarEventDraft) {
  const rule = event.recurrence;
  if (!rule) return null;
  const unit = ({ DAILY: "day", WEEKLY: "week", MONTHLY: "month" })[rule.frequency];
  const every = rule.interval === 1 ? `Every ${unit}` : `Every ${rule.interval} ${unit}s`;
  const days = rule.weekdays.length ? ` on ${rule.weekdays.map((day) => weekdayNames[day]?.slice(0, 3) || day).join(", ")}` : "";
  const ends = rule.count ? `, ${rule.count} times` : rule.until ? `, until ${rule.until}` : "";
  return every + days + ends;
}

type Tone = "review" | "saving" | "done" | "muted";
const TONES: Record<Tone, string> = {
  review: "bg-accent-soft text-accent-fg",
  saving: "bg-subtle text-fg",
  done: "bg-ok-soft text-ok",
  muted: "bg-subtle text-muted",
};

function ProposalCard({ proposal, onChange }: { proposal: CalendarProposal; onChange: (proposal: CalendarProposal) => void }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [moreText, setMoreText] = useState(false);
  const lock = useRef(false);
  const event = proposal.event;
  const pending = proposal.status === "pending";
  const update = proposal.operation === "update";
  const expired = pending && new Date(proposal.expires_at).getTime() <= Date.now();
  const time = when(event.start, event.end, event.timezone);
  const repeats = repeatText(event);

  const act = async (action: "approve" | "cancel" | "edit", draft?: CalendarEventDraft) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await apiRequest<CalendarProposal>(`/calendar/proposals/${proposal.id}/${action}`, {
        version: proposal.version, ...(draft ? { event: draft } : {}),
      });
      onChange(result);
      setEditing(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update the proposal.");
      window.dispatchEvent(new Event("calendar-updated"));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  const saveEdit = (formEvent: FormEvent<HTMLFormElement>) => {
    formEvent.preventDefault();
    const form = new FormData(formEvent.currentTarget);
    try {
      void act("edit", {
        ...event,
        title: String(form.get("title")), start: zonedTime(String(form.get("start")), event.timezone),
        end: zonedTime(String(form.get("end")), event.timezone), timezone: event.timezone,
        description: String(form.get("description")), location: String(form.get("location")),
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Check the event times.");
    }
  };

  const [tone, label]: [Tone, string] =
    proposal.status === "applied" ? ["done", update ? "Updated in Google Calendar" : "Added to Google Calendar"]
      : proposal.status === "cancelled" ? ["muted", "Cancelled"]
        : proposal.status === "approved" ? ["saving", "Saving to Google Calendar"]
          : expired ? ["muted", "Draft expired"]
            : ["review", update ? "Review changes" : "Review new event"];
  const inactive = proposal.status === "cancelled" || expired;
  const field = "w-full rounded-xl border border-line bg-bg px-3 py-2.5 text-[15px] text-fg outline-none focus:border-muted";

  return <article aria-label={`Calendar proposal: ${event.title}`}
    className={`flex flex-col overflow-hidden rounded-2xl border bg-surface text-sm transition ${tone === "review" ? "border-accent-fg/40 shadow-sm" : "border-line"}`}>
    <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${TONES[tone]}`}>
        {tone === "done" ? <CheckIcon /> : tone === "saving" ? <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-current" /> : <CalendarIcon />}
        {label}
      </span>
      <span className="min-w-0 truncate text-xs text-muted" title={`Calendar: ${proposal.calendar}`}>{proposal.calendar}</span>
    </header>

    <div className="flex gap-4 p-4">
      <div aria-hidden="true" className={`flex h-16 w-14 flex-none flex-col items-center justify-center overflow-hidden rounded-xl border ${inactive ? "border-line text-muted" : "border-accent-fg/30"}`}>
        <span className={`w-full py-0.5 text-center text-[10px] font-bold tracking-wider ${inactive ? "bg-subtle" : "bg-accent text-on-accent"}`}>{time.month}</span>
        <span className="text-xl leading-tight font-semibold">{time.dayNumber}</span>
        <span className="text-[10px] text-muted">{time.weekday}</span>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <h3 className={`text-base leading-snug font-semibold ${proposal.status === "cancelled" ? "text-muted line-through" : ""}`}>{event.title}</h3>
        <p className="flex items-start gap-2 text-[15px]">
          <ClockIcon />
          <span>
            {time.date} · {time.time} <span className="text-muted">{time.zone}</span>
            <span className="ml-2 inline-block rounded-md bg-subtle px-1.5 py-0.5 align-[1px] text-xs font-medium text-muted">{time.length}</span>
          </span>
        </p>
        {event.location && <p className="flex items-start gap-2 text-muted"><PinIcon /><span className="min-w-0 whitespace-pre-wrap">{event.location}</span></p>}
        {repeats && <p className="flex items-start gap-2 text-muted"><RepeatIcon /><span>{repeats}</span></p>}
        {event.reminder_minutes != null && <p className="flex items-start gap-2 text-muted"><BellIcon /><span>{event.reminder_minutes === 0 ? "Reminder at the start" : `Reminder ${event.reminder_minutes} min before`}</span></p>}
        {update && proposal.reminder_changed && event.reminder_minutes == null && <p className="flex items-start gap-2 text-muted"><BellIcon /><span>Existing reminders will be removed</span></p>}
        {update && proposal.recurrence_changed && !event.recurrence && <p className="flex items-start gap-2 text-muted"><RepeatIcon /><span>Stops repeating; keeps one event</span></p>}
        {event.description && <div className="text-muted">
          <p className={`whitespace-pre-wrap ${moreText ? "" : "line-clamp-3"}`}>{event.description}</p>
          {event.description.length > 100 && <button type="button" onClick={() => setMoreText(!moreText)} className="mt-0.5 text-xs font-medium text-accent-fg hover:underline">{moreText ? "Show less" : "Show more"}</button>}
        </div>}
        {!!proposal.occurrences?.length && <details className="group mt-1">
          <summary className="cursor-pointer list-none text-xs font-medium text-accent-fg hover:underline">
            <span className="group-open:hidden">See all {proposal.occurrences.length} dates</span><span className="hidden group-open:inline">Hide dates</span>
          </summary>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {proposal.occurrences.map((slot) => {
              const occurrence = when(slot.start, slot.end, event.timezone);
              return <li key={slot.start} className="rounded-md bg-subtle px-2 py-1 text-xs">{occurrence.date} · {occurrence.time}</li>;
            })}
          </ul>
        </details>}
      </div>
    </div>

    {proposal.before && <div className="mx-4 mb-4 rounded-xl border border-dashed border-line bg-bg/60 p-3 text-muted">
      <p className="mb-1 text-[11px] font-semibold tracking-wider uppercase">Before</p>
      <p className="font-medium text-fg/80">{proposal.before.summary || "Untitled event"}</p>
      {proposal.before.start?.dateTime && proposal.before.end?.dateTime && (() => {
        const old = when(proposal.before.start.dateTime, proposal.before.end.dateTime, event.timezone);
        return <p className="line-through decoration-muted/60">{old.date} · {old.time} {old.zone}</p>;
      })()}
      {proposal.before.location && <p>{proposal.before.location}</p>}
      {!!proposal.before.recurrence?.length && <p>Recurring series. Its repeat rule stays the same unless a new one is shown above.</p>}
      <p className="mt-2 text-xs"><span className="font-medium text-fg/80">{proposal.scope === "series" ? "Changes the entire series" : "Changes this event only"}</span>{proposal.scope === "series" ? ", including past occurrences" : ""}. Guests stay unchanged.</p>
    </div>}

    {(error || expired) && <div className="mx-4 mb-3 flex flex-col gap-2">
      {expired && <p role="status" className="rounded-xl bg-subtle px-3 py-2 text-muted">This draft expired. Ask in chat for a new one.</p>}
      {error && <p role="alert" className="rounded-xl bg-error-soft px-3 py-2 text-error">{error}</p>}
    </div>}

    {editing ? <form onSubmit={saveEdit} className="flex flex-col gap-3 border-t border-line bg-bg/40 p-4">
      <label className="flex flex-col gap-1 text-xs font-medium text-muted">Title<input name="title" required maxLength={200} defaultValue={event.title} className={field} /></label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">Starts<input type="datetime-local" name="start" required defaultValue={localTime(new Date(event.start), event.timezone)} className={field} /></label>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">Ends<input type="datetime-local" name="end" required defaultValue={localTime(new Date(event.end), event.timezone)} className={field} /></label>
      </div>
      <p className="-mt-1 text-xs text-muted">Times are in {event.timezone}. For another time zone, ask in chat for a new draft.</p>
      <label className="flex flex-col gap-1 text-xs font-medium text-muted">Location<input name="location" maxLength={500} defaultValue={event.location} className={field} /></label>
      <label className="flex flex-col gap-1 text-xs font-medium text-muted">Notes<textarea name="description" rows={3} maxLength={4000} defaultValue={event.description} className={field} /></label>
      <div className="flex flex-wrap gap-2"><Button type="submit" disabled={busy}>{busy ? "Saving…" : "Save draft"}</Button><Button variant="ghost" disabled={busy} onClick={() => setEditing(false)}>Back</Button></div>
    </form> : <footer className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
      {(pending || proposal.status === "approved") && <Button disabled={busy || expired} onClick={() => act("approve")}>
        {busy ? "Working…" : proposal.status === "approved" ? "Retry save" : update ? "Approve changes" : "Approve addition"}
      </Button>}
      {pending && <>
        <Button variant="secondary" disabled={busy || expired} onClick={() => setEditing(true)}>Edit</Button>
        <Button variant="ghost" disabled={busy} onClick={() => act("cancel")}>Cancel</Button>
      </>}
      {proposal.status === "applied" && proposal.event_url && <a href={proposal.event_url} target="_blank" rel="noopener noreferrer"
        className="group inline-flex min-h-11 items-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-medium text-fg transition hover:border-muted hover:bg-subtle">
        Open in Google Calendar<ArrowIcon />
      </a>}
      {proposal.status === "applied" && !proposal.event_url && <span className="text-muted">Saved. It's in your Google Calendar now.</span>}
      {proposal.status === "cancelled" && <span className="text-muted">Nothing was {update ? "changed" : "added"} in Google Calendar.</span>}
      {(pending || proposal.status === "approved") && !expired && <p className="w-full text-xs text-muted sm:ml-auto sm:w-auto">
        {pending ? "Nothing changes in Google Calendar until you approve." : "Saving was requested. Retry to confirm it safely."}
        {update ? " Guests aren't notified." : " No guests are invited."}
      </p>}
    </footer>}
  </article>;
}

const icon = { width: 15, height: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2,
  strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true, className: "mt-[2px] flex-none" };
const ClockIcon = () => <svg {...icon}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;
const PinIcon = () => <svg {...icon}><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z" /><circle cx="12" cy="9.5" r="2.5" /></svg>;
const RepeatIcon = () => <svg {...icon}><path d="M17 2l3 3-3 3M3 11V9a4 4 0 0 1 4-4h13M7 22l-3-3 3-3M21 13v2a4 4 0 0 1-4 4H4" /></svg>;
const BellIcon = () => <svg {...icon}><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0" /></svg>;
const CrossIcon = () => <svg {...icon} className="flex-none"><path d="M6 6l12 12M18 6 6 18" /></svg>;
const CheckIcon = () => <svg {...icon} className="flex-none"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>;
const ArrowIcon = () => <svg {...icon} className="flex-none transition group-hover:translate-x-0.5 group-hover:-translate-y-0.5"><path d="M7 17 17 7M8 7h9v9" /></svg>;

/** Several new study blocks from one reply: one card, grouped by day, approved together. */
function StudyPlan({ proposals, onChange }: { proposals: CalendarProposal[]; onChange: (proposal: CalendarProposal) => void }) {
  const [working, setWorking] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [details, setDetails] = useState<string | null>(null);
  const timezone = proposals[0].event.timezone;
  const sorted = [...proposals].sort((a, b) => a.event.start.localeCompare(b.event.start));
  const expired = (proposal: CalendarProposal) => proposal.status === "pending" && new Date(proposal.expires_at).getTime() <= Date.now();
  const waiting = sorted.filter((proposal) => (proposal.status === "pending" || proposal.status === "approved") && !expired(proposal));
  const added = sorted.filter((proposal) => proposal.status === "applied");
  const live = sorted.filter((proposal) => proposal.status !== "cancelled");
  const minutes = live.reduce((sum, proposal) => sum + (new Date(proposal.event.end).getTime() - new Date(proposal.event.start).getTime()) / 60000, 0);
  const total = minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} hr${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
  const days = sorted.reduce<[string, CalendarProposal[]][]>((groups, proposal) => {
    const day = when(proposal.event.start, proposal.event.end, timezone).date;
    const group = groups.find(([name]) => name === day);
    if (group) group[1].push(proposal); else groups.push([day, [proposal]]);
    return groups;
  }, []);

  const run = async (items: CalendarProposal[], action: "approve" | "cancel") => {
    for (const proposal of items) {
      setWorking(proposal.id);
      try {
        onChange(await apiRequest<CalendarProposal>(`/calendar/proposals/${proposal.id}/${action}`, { version: proposal.version }));
        setErrors((current) => { const next = { ...current }; delete next[proposal.id]; return next; });
      } catch (reason) {
        // Keep going: one block that now conflicts should not stop the others.
        setErrors((current) => ({ ...current, [proposal.id]: reason instanceof Error ? reason.message : "Could not update this block." }));
      }
    }
    setWorking(null);
  };
  const busy = working !== null;
  const done = waiting.length === 0 && added.length > 0;
  const [tone, label]: [Tone, string] = done ? ["done", added.length === live.length ? "Added to Google Calendar" : `${added.length} of ${live.length} added`]
    : busy ? ["saving", "Adding to Google Calendar"] : live.length === 0 ? ["muted", "Plan discarded"]
      : added.length ? ["review", `${added.length} of ${live.length} added`] : ["review", "Review study plan"];

  return <article aria-label="Study plan" className={`flex flex-col overflow-hidden rounded-2xl border bg-surface text-sm ${tone === "review" ? "border-accent-fg/40 shadow-sm" : "border-line"}`}>
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${TONES[tone]}`}>
        {tone === "done" ? <CheckIcon /> : tone === "saving" ? <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-current" /> : <CalendarIcon />}
        {label}
      </span>
      <span className="text-xs text-muted">{live.length} block{live.length === 1 ? "" : "s"} · {total} · {proposals[0].calendar}</span>
    </header>

    <ol className="flex flex-col">
      {days.map(([day, items]) => <li key={day} className="border-b border-line last:border-b-0">
        <p className="bg-subtle/60 px-4 py-1.5 text-xs font-semibold tracking-wide text-muted uppercase">{day}</p>
        <ul>
          {items.map((proposal) => {
            const time = when(proposal.event.start, proposal.event.end, timezone);
            const removed = proposal.status === "cancelled";
            const note = proposal.event.description.split("\n")[0].replace("Planned with TrojanClaw.", "").trim();
            return <li key={proposal.id} className="border-t border-line first:border-t-0">
              <div className="flex items-start gap-3 px-4 py-3">
                <div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:gap-3">
                <span className={`flex-none pt-0.5 text-[13px] font-medium whitespace-nowrap tabular-nums sm:w-36 ${removed ? "text-muted line-through" : "text-muted sm:text-fg"}`}>{time.time}</span>
                <div className="min-w-0 flex-1">
                  <p className={`leading-snug font-semibold ${removed ? "text-muted line-through" : ""}`}>{proposal.event.title}</p>
                  {note && !removed && <p className="mt-0.5 truncate text-muted">{note}</p>}
                  {errors[proposal.id] && <p role="alert" className="mt-1 text-error">{errors[proposal.id]}</p>}
                  {expired(proposal) && <p className="mt-1 text-muted">Expired. Ask in chat for a new plan.</p>}
                </div>
                </div>
                <span className="mt-0.5 flex-none rounded-md bg-subtle px-1.5 py-0.5 text-xs font-medium text-muted">{time.length}</span>
                <span className="flex flex-none items-center gap-1">
                  {working === proposal.id && <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent-fg" aria-label="Working" />}
                  {proposal.status === "applied" && (proposal.event_url
                    ? <a href={proposal.event_url} target="_blank" rel="noopener noreferrer" title="Open in Google Calendar"
                      className="group inline-flex h-8 items-center gap-1 rounded-lg px-2 text-xs font-medium text-ok hover:bg-ok-soft"><CheckIcon />Open<ArrowIcon /></a>
                    : <span className="inline-flex h-8 items-center gap-1 px-2 text-xs font-medium text-ok"><CheckIcon />Added</span>)}
                  {proposal.status === "pending" && !busy && !expired(proposal) && <>
                    <button type="button" onClick={() => setDetails(details === proposal.id ? null : proposal.id)} aria-expanded={details === proposal.id}
                      className="h-8 rounded-lg px-2 text-xs font-medium text-muted hover:bg-subtle hover:text-fg">{details === proposal.id ? "Close" : "Edit"}</button>
                    <button type="button" onClick={() => void run([proposal], "cancel")} aria-label={`Remove ${proposal.event.title}`} title="Remove from plan"
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-subtle hover:text-fg"><CrossIcon /></button>
                  </>}
                </span>
              </div>
              {details === proposal.id && proposal.status === "pending" && <div className="px-4 pb-4">
                <ProposalCard key={`${proposal.id}:${proposal.version}`} proposal={proposal} onChange={(next) => { onChange(next); if (next.status !== "pending") setDetails(null); }} />
              </div>}
            </li>;
          })}
        </ul>
      </li>)}
    </ol>

    {waiting.length > 0 && <footer className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
      <Button disabled={busy} onClick={() => void run(waiting, "approve")}>
        {busy ? "Adding…" : waiting.length === live.length ? `Add all ${waiting.length} to Google Calendar` : `Add the other ${waiting.length}`}
      </Button>
      <Button variant="ghost" disabled={busy} onClick={() => void run(waiting.filter((proposal) => proposal.status === "pending"), "cancel")}>Discard plan</Button>
      <p className="w-full text-xs text-muted sm:ml-auto sm:w-auto">Nothing changes until you approve. Each block is checked against your calendar again first.</p>
    </footer>}
  </article>;
}

/** Drafts created by one chat. A new chat with no saved messages has none. */
export function CalendarProposals({ refreshKey, conversationId }: { refreshKey: string; conversationId: string | null }) {
  const [proposals, setProposals] = useState<CalendarProposal[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const requestSequence = useRef(0);
  const [replyProposalIds, setReplyProposalIds] = useState<Set<string> | null>(null);
  // Drafts approved or cancelled since the last message stay visible with their result.
  const [recent, setRecent] = useState<{ ids: Set<string>; refreshKey: string } | null>(null);
  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1);
    const started = () => {
      requestSequence.current++;
      setReplyProposalIds(new Set());
      setRecent(null);
      setError("");
    };
    const drafted = (event: Event) => {
      // Invalidate older list requests synchronously so they cannot overwrite
      // drafts delivered by the chat response.
      requestSequence.current++;
      const drafts = (event as CustomEvent<CalendarProposal[]>).detail;
      setReplyProposalIds(new Set((drafts ?? []).map((draft) => draft.id)));
      if (drafts?.length) {
        const ids = new Set(drafts.map((draft) => draft.id));
        setProposals((values) => [...drafts, ...values.filter((value) => !ids.has(value.id))]);
        setError("");
      }
      refresh();
    };
    window.addEventListener("calendar-updated", refresh);
    window.addEventListener("calendar-chat-started", started);
    window.addEventListener("calendar-drafted", drafted);
    return () => {
      window.removeEventListener("calendar-updated", refresh);
      window.removeEventListener("calendar-chat-started", started);
      window.removeEventListener("calendar-drafted", drafted);
    };
  }, []);
  useEffect(() => {
    let active = true;
    const sequence = ++requestSequence.current;
    if (!conversationId) return () => { active = false; };
    apiRequest<CalendarProposal[]>(`/calendar/proposals?conversation_id=${encodeURIComponent(conversationId)}`)
      .then((values) => { if (active && sequence === requestSequence.current) { setProposals(values); setError(""); } })
      .catch((reason: Error) => { if (active && sequence === requestSequence.current) setError(reason.message); });
    return () => { active = false; };
  }, [refreshKey, revision, conversationId]);

  const recentIds = recent?.refreshKey === refreshKey ? recent.ids : new Set<string>();
  const replyProposals = proposals.filter((proposal) => replyProposalIds === null || replyProposalIds.has(proposal.id));
  const needsReview = replyProposals.filter((proposal) => proposal.status === "pending" || proposal.status === "approved");
  // Drafts still waiting for review, plus the one just approved or cancelled, so its result
  // (and the Open in Google Calendar button) stays visible even while others still wait.
  const activeProposals = proposals.filter((proposal) => needsReview.includes(proposal) || recentIds.has(proposal.id));
  const update = (updated: CalendarProposal) => {
    requestSequence.current++;
    setProposals((values) => values.map((value) => value.id === updated.id ? updated : value));
    if (updated.status === "applied" || updated.status === "cancelled") {
      setRecent((current) => ({ refreshKey, ids: new Set([...(current?.refreshKey === refreshKey ? current.ids : []), updated.id]) }));
    }
  };
  // New one-off events from the same reply (a study plan) are shown together.
  const planItems = activeProposals.filter((proposal) => (proposal.operation ?? "create") === "create" && !proposal.event.recurrence);
  const card = (proposal: CalendarProposal) => <ProposalCard key={`${proposal.id}:${proposal.version}`} proposal={proposal} onChange={update} />;

  return <section aria-label="Calendar proposals" className="flex flex-col gap-4">
    {error && <div role="alert" className="text-sm text-error">Calendar proposals: {error} <button type="button" className="underline" onClick={() => setRevision((value) => value + 1)}>Refresh</button></div>}
    {planItems.length >= 2 && <StudyPlan proposals={planItems} onChange={update} />}
    {activeProposals.filter((proposal) => !(planItems.length >= 2 && planItems.includes(proposal))).map(card)}
  </section>;
}
