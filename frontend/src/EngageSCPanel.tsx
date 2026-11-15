import { useEffect, useRef, useState, type FormEvent } from "react";
import { apiRequest } from "./lib/api";
import { CloseIcon } from "./components/ui";

interface CampusEvent {
  external_key: string;
  title: string;
  host: string | null;
  starts_at: string;
  ends_at: string | null;
  all_day: boolean;
  location: string | null;
  url: string;
  description: string;
}

interface EventList {
  events: CampusEvent[];
  fetched_at: string;
  stale: boolean;
}

const dateLabel = (event: CampusEvent) => new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Los_Angeles", month: "short", day: "numeric", weekday: "short",
  ...(event.all_day ? {} : { hour: "numeric", minute: "2-digit" }),
}).format(new Date(event.starts_at)) + (event.all_day ? " · All day" : " PT");

export function EventsDialog({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [days, setDays] = useState("30");
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<EventList | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  useEffect(() => {
    let active = true;
    const params = new URLSearchParams({ search: query, days, limit: "100" });
    apiRequest<EventList>(`/integrations/engagesc/events?${params}`)
      .then((data) => { if (active) setResult(data); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "Could not load events."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [query, days, reload]);

  const handleSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);
    setError("");
    setQuery(search.trim());
    setReload((value) => value + 1);
  };

  return (
    <dialog ref={dialogRef} className="events-dialog" onCancel={onClose} aria-labelledby="events-title">
      <button className="auth-close" aria-label="Close events" onClick={onClose}><CloseIcon /></button>
      <h2 id="events-title">Explore EngageSC</h2>
      <p className="events-subtitle">Public campus events · Times in Los Angeles</p>
      <form className="events-search" onSubmit={handleSearch}>
        <input aria-label="Search events" placeholder="Search events or organizations" value={search} maxLength={100} onChange={(event) => setSearch(event.target.value)} />
        <select aria-label="Event date range" value={days} onChange={(event) => { setLoading(true); setError(""); setDays(event.target.value); }}>
          <option value="7">Next 7 days</option><option value="30">Next 30 days</option><option value="90">Next 90 days</option>
        </select>
        <button type="submit">Search</button>
      </form>
      {loading ? <p className="events-notice" role="status">Loading public events...</p> : error ? (
        <div className="events-notice" role="alert"><p>{error}</p><button className="events-text-button" onClick={() => { setLoading(true); setError(""); setReload((value) => value + 1); }}>Try again</button></div>
      ) : result && (
        <>
          {result.stale && <p className="events-notice" role="status">EngageSC is temporarily unavailable. Showing the last saved events.</p>}
          <p className="events-count">{result.events.length === 100 ? "Showing up to 100 events. Search to narrow the list." : `${result.events.length} events`}</p>
          {result.events.length === 0 && <p className="events-notice">No public events match. Try a different search or date range.</p>}
          <div className="events-list">
            {result.events.map((event) => (
              <article key={event.external_key} className="event-item">
                <p className="event-date">{dateLabel(event)}</p>
                <h3><a href={event.url} target="_blank" rel="noopener noreferrer">{event.title}</a></h3>
                {event.host && <p className="event-host">{event.host}</p>}
                {event.location && <p className="event-location">{event.location}</p>}
                {event.description && <p className="event-description">{event.description.slice(0, 220)}{event.description.length > 220 ? "…" : ""}</p>}
              </article>
            ))}
          </div>
          <p className="events-footer">Updated {new Date(result.fetched_at).toLocaleString("en-US", { timeZone: "America/Los_Angeles" })} PT. Public feed refreshes hourly. <a href="https://engage.usc.edu/events" target="_blank" rel="noopener noreferrer">Open EngageSC</a></p>
        </>
      )}
    </dialog>
  );
}
