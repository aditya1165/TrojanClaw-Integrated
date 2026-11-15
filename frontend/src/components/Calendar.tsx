import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { apiRequest } from "../lib/api";
import type { CalendarEventDraft, CalendarProposal, CalendarStatus } from "../lib/api";
import { Button, CalendarIcon } from "./ui";

export function CalendarConnection() {
  const [status, setStatus] = useState<CalendarStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(() => new URLSearchParams(window.location.search).get("calendar_error") || "");
  const load = useCallback(() => apiRequest<CalendarStatus>("/calendar/status").then(setStatus), []);

  useEffect(() => {
    let active = true;
    apiRequest<CalendarStatus>("/calendar/status")
      .then((value) => { if (active) setStatus(value); })
      .catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, []);

  const act = async (action: "connect" | "disconnect") => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (action === "connect") {
        const { url } = await apiRequest<{ url: string }>("/calendar/connect", {});
        window.location.assign(url);
      } else {
        await apiRequest<void>("/calendar/disconnect", {});
        await load();
        window.dispatchEvent(new Event("calendar-updated"));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update calendar connection.");
    } finally {
      setBusy(false);
    }
  };

  return <section aria-label="Google Calendar" className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 text-sm">
    <div className="flex items-center gap-2 font-medium"><CalendarIcon />Google Calendar</div>
    <p className="break-words text-xs text-muted">{status?.connected ? `Connected: ${status.calendar}` : status?.configured ? "Connect to add events from chat." : status ? "Calendar connection needs setup." : "Checking connection…"}</p>
    <p className="text-xs text-muted">Every calendar change needs your approval.</p>
    {error && <p role="alert" className="text-xs text-error">{error}</p>}
    <div className="flex flex-wrap gap-2">
      <Button variant="secondary" disabled={!status?.configured || busy} onClick={() => act("connect")}>{busy ? "Working…" : status?.connected ? "Reconnect" : "Connect"}</Button>
      {status?.connected && <Button variant="ghost" disabled={busy} onClick={() => act("disconnect")}>Disconnect</Button>}
    </div>
    {status?.connected && <p className="text-xs text-muted">Disconnecting removes access and drafts. Events already saved stay in Google Calendar.</p>}
  </section>;
}

function formatTime(value: string, timezone: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: timezone }).format(new Date(value));
}

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

function ProposalCard({ proposal, onChange }: { proposal: CalendarProposal; onChange: (proposal: CalendarProposal) => void }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  const event = proposal.event;
  const pending = proposal.status === "pending";
  const expired = pending && new Date(proposal.expires_at).getTime() <= Date.now();

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

  const inputClass = "w-full rounded-lg border border-line bg-bg px-3 py-2 text-fg";
  return <article aria-label={`Calendar proposal: ${event.title}`} className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-5 text-sm">
    <div className="flex items-center gap-2 font-semibold"><CalendarIcon />{proposal.status === "applied" ? proposal.operation === "update" ? "Updated in Google Calendar" : "Added to Google Calendar" : proposal.status === "cancelled" ? "Proposal cancelled" : proposal.operation === "update" ? "Review calendar changes" : "Review calendar addition"}</div>
    {proposal.before && <div className="rounded-lg border border-line p-3 text-muted">
      <p className="font-medium">Before: {proposal.before.summary || "Untitled event"}</p>
      {proposal.before.start?.dateTime && proposal.before.end?.dateTime && <p>{formatTime(proposal.before.start.dateTime, event.timezone)} – {formatTime(proposal.before.end.dateTime, event.timezone)}</p>}
      {proposal.before.location && <p>Location: {proposal.before.location}</p>}
      {proposal.before.description && <p className="whitespace-pre-wrap">{proposal.before.description}</p>}
      {!!proposal.before.recurrence?.length && <p>This is a recurring series. Its repeat rule stays unchanged unless a new rule is shown below.</p>}
      <p className="mt-2 font-medium">Scope: {proposal.scope === "series" ? "Entire recurring series, including past occurrences" : "This event or occurrence only"}</p>
      <p>After approval, the details below replace the event details shown above. Existing guests stay unchanged.</p>
    </div>}
    <p className="font-medium">{event.title}</p>
    <p>{formatTime(event.start, event.timezone)} – {formatTime(event.end, event.timezone)}</p>
    {event.recurrence && <div className="rounded-lg border border-line p-3">
      <p>Repeats: every {event.recurrence.interval === 1 ? "" : `${event.recurrence.interval} `}{({ DAILY: "day", WEEKLY: "week", MONTHLY: "month" })[event.recurrence.frequency]}{event.recurrence.interval > 1 ? "s" : ""}{event.recurrence.weekdays.length ? ` on ${event.recurrence.weekdays.map(day => weekdayNames[day] || day).join(", ")}` : ""}.</p>
      <p>{event.recurrence.count ? `${event.recurrence.count} occurrences` : `Ends on ${event.recurrence.until}`}. {proposal.occurrences?.length ? `${proposal.occurrences.length} scheduled occurrences.` : ""}</p>
      {!!proposal.occurrences?.length && <details><summary className="cursor-pointer underline">Review every occurrence</summary>{proposal.occurrences.map(slot => <p key={slot.start}>{formatTime(slot.start, event.timezone)} – {formatTime(slot.end, event.timezone)}</p>)}</details>}
    </div>}
    {event.reminder_minutes != null && <p>Popup reminder: {event.reminder_minutes === 0 ? "At event start" : `${event.reminder_minutes} minutes before start`}.</p>}
    {proposal.operation === "update" && proposal.reminder_changed && event.reminder_minutes == null && <p>After approval: remove existing reminders.</p>}
    {proposal.operation === "update" && proposal.recurrence_changed && !event.recurrence && <p>After approval: stop repeating; keep one event.</p>}
    <p className="break-words text-muted">Time zone: {event.timezone}<br />Calendar: {proposal.calendar}</p>
    {event.location && <p className="whitespace-pre-wrap">Location: {event.location}</p>}
    {event.description && <p className="whitespace-pre-wrap">{event.description}</p>}
    {(pending || proposal.status === "approved") && <p className="text-xs text-muted">{proposal.operation === "update" ? "Existing recurrence and reminder settings stay unchanged unless a new setting is shown above. Guest notifications are not sent. " : "No guests. "}{pending ? "Nothing has been changed in Google Calendar yet." : "Saving was requested. Retry to confirm the result safely."}</p>}
    {expired && <p role="status" className="text-muted">This proposal expired. Ask chat for a new proposal.</p>}
    {error && <p role="alert" className="text-error">{error}</p>}
    {editing ? <form onSubmit={saveEdit} className="flex flex-col gap-3">
      <label>Title<input name="title" required maxLength={200} defaultValue={event.title} className={inputClass} /></label>
      <p className="text-xs text-muted">Times are in {event.timezone}. To use another time zone, ask chat for a new proposal.</p>
      <label>Start<input type="datetime-local" name="start" required defaultValue={localTime(new Date(event.start), event.timezone)} className={inputClass} /></label>
      <label>End<input type="datetime-local" name="end" required defaultValue={localTime(new Date(event.end), event.timezone)} className={inputClass} /></label>
      <label>Location<input name="location" maxLength={500} defaultValue={event.location} className={inputClass} /></label>
      <label>Description<textarea name="description" maxLength={4000} defaultValue={event.description} className={inputClass} /></label>
      <div className="flex gap-2"><Button type="submit" disabled={busy}>Save draft for review</Button><Button variant="ghost" disabled={busy} onClick={() => setEditing(false)}>Back</Button></div>
    </form> : <div className="flex flex-wrap gap-2">
      {(pending || proposal.status === "approved") && <Button disabled={busy || expired} onClick={() => act("approve")}>{busy ? "Working…" : proposal.status === "approved" ? "Retry save" : proposal.operation === "update" ? "Approve changes" : "Approve addition"}</Button>}
      {pending && <><Button variant="secondary" disabled={busy || expired} onClick={() => setEditing(true)}>Edit</Button><Button variant="ghost" disabled={busy} onClick={() => act("cancel")}>Cancel</Button></>}
      {proposal.status === "applied" && proposal.event_url && <a href={proposal.event_url} target="_blank" rel="noopener noreferrer" className="py-2 underline">Open in Google Calendar</a>}
    </div>}
  </article>;
}

export function CalendarProposals({ refreshKey }: { refreshKey: string }) {
  const [proposals, setProposals] = useState<CalendarProposal[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const requestSequence = useRef(0);
  const [replyProposalIds, setReplyProposalIds] = useState<Set<string> | null>(null);
  const [recentResult, setRecentResult] = useState<{ id: string; refreshKey: string } | null>(null);
  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1);
    const started = () => {
      requestSequence.current++;
      setReplyProposalIds(new Set());
      setRecentResult(null);
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
    apiRequest<CalendarProposal[]>("/calendar/proposals")
      .then((values) => { if (active && sequence === requestSequence.current) { setProposals(values); setError(""); } })
      .catch((reason: Error) => { if (active && sequence === requestSequence.current) setError(reason.message); });
    return () => { active = false; };
  }, [refreshKey, revision]);

  const recentId = recentResult?.refreshKey === refreshKey ? recentResult.id : null;
  const replyProposals = proposals.filter((proposal) => replyProposalIds === null || replyProposalIds.has(proposal.id));
  const needsReview = replyProposals.filter((proposal) => proposal.status === "pending" || proposal.status === "approved");
  const activeProposals = needsReview.length ? needsReview : proposals.filter((proposal) => proposal.id === recentId);
  const update = (updated: CalendarProposal) => {
    requestSequence.current++;
    setProposals((values) => values.map((value) => value.id === updated.id ? updated : value));
    if (updated.status === "applied" || updated.status === "cancelled") setRecentResult({ id: updated.id, refreshKey });
  };
  const card = (proposal: CalendarProposal) => <ProposalCard key={`${proposal.id}:${proposal.version}`} proposal={proposal} onChange={update} />;

  return <section aria-label="Calendar proposals" className="flex flex-col gap-4">
    {error && <div role="alert" className="text-sm text-error">Calendar proposals: {error} <button type="button" className="underline" onClick={() => setRevision((value) => value + 1)}>Refresh</button></div>}
    {activeProposals.map(card)}
  </section>;
}
