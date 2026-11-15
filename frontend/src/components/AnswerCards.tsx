import { useState, type ReactNode } from "react";
import { parseCards, siteName, type AnswerCard, type CardKind } from "../lib/cards";

const COLLAPSED = 4;

/** A ```cards block. `final` is false while the reply is still being written. */
export function CardBlock({ raw, final }: { raw: string; final: boolean }) {
  const data = parseCards(raw);
  const [expanded, setExpanded] = useState(false);
  if (!data) return final ? null : <CardSkeleton />;
  const shown = expanded ? data.items : data.items.slice(0, COLLAPSED);
  const hidden = data.items.length - shown.length;
  return (
    <section className="not-prose my-1 flex flex-col gap-2" aria-label={data.title ?? "Results"}>
      {data.title && (
        <h3 className="flex items-baseline gap-2 text-[13px] font-semibold tracking-wide text-muted uppercase">
          {data.title}
          <span className="font-normal normal-case tracking-normal">· {data.items.length}</span>
        </h3>
      )}
      <ul className="flex flex-col gap-2">
        {shown.map((item, index) => <li key={`${item.title}-${item.url ?? index}`}><Card item={item} final={final} /></li>)}
      </ul>
      {(hidden > 0 || expanded) && data.items.length > COLLAPSED && (
        <button type="button" onClick={() => setExpanded(!expanded)}
          className="self-start rounded-lg px-1 py-1 text-sm font-medium text-accent-fg hover:underline">
          {expanded ? "Show fewer" : `Show ${hidden} more`}
        </button>
      )}
    </section>
  );
}

function Card({ item, final }: { item: AnswerCard; final: boolean }) {
  // Links appear once the reply is finished and checked by the server.
  const url = final && item.url ? item.url : null;
  const meta = [item.when, item.location].filter(Boolean).join(" · ");
  const body: ReactNode = (
    <>
      <span aria-hidden="true"
        className="flex h-10 w-10 flex-none items-center justify-center rounded-xl bg-accent-soft text-accent-fg">
        <KindIcon kind={item.kind} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex flex-wrap items-start gap-x-2 gap-y-1">
          {/* basis-48: on narrow screens the status badge wraps under the title instead of squeezing it */}
          <span className="min-w-0 flex-1 basis-48 text-[15px] leading-snug font-semibold text-fg">{item.title}</span>
          {item.status && <StatusBadge status={item.status} />}
        </span>
        {item.subtitle && <span className="text-sm leading-snug text-muted">{item.subtitle}</span>}
        {meta && (
          <span className="flex items-start gap-1.5 text-sm leading-snug text-fg/90">
            <ClockIcon />
            <span className="min-w-0">{meta}</span>
          </span>
        )}
        {item.tags && item.tags.length > 0 && (
          <span className="mt-0.5 flex flex-wrap gap-1.5">
            {item.tags.map((tag) => (
              <span key={tag} className="rounded-md bg-subtle px-2 py-0.5 text-xs font-medium text-muted">{tag}</span>
            ))}
          </span>
        )}
        {item.note && <span className="mt-0.5 text-sm leading-snug text-muted italic">{item.note}</span>}
      </span>
      {url && (
        <span className="flex flex-none items-center gap-1 self-center rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium whitespace-nowrap text-fg transition group-hover:border-accent-fg group-hover:text-accent-fg max-sm:hidden">
          Open in {siteName(url)}
          <ArrowIcon />
        </span>
      )}
      {url && <span className="flex-none self-center text-muted group-hover:text-accent-fg sm:hidden"><ArrowIcon /></span>}
    </>
  );
  const frame = "flex w-full items-start gap-3 rounded-2xl border border-line bg-surface p-3.5 text-left";
  if (!url) return <div className={frame}>{body}</div>;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" title={`Open in ${siteName(url)}`}
      className={`group ${frame} transition hover:border-muted hover:shadow-sm focus-visible:border-accent-fg`}>
      {body}
    </a>
  );
}

function StatusBadge({ status }: { status: string }) {
  const value = status.toLowerCase();
  const negated = /\b(not|no|un)\b|^un|^not\b/.test(value); // "Not applied", "Unsubmitted"
  const tone = !negated && /(applied|registered|submitted|done|complete|accepted|graded)/.test(value)
    ? "bg-ok-soft text-ok"
    : /(due|overdue|closing|late|urgent|today|tomorrow|missing)/.test(value)
      ? "bg-error-soft text-error"
      : "bg-subtle text-muted";
  return <span className={`flex-none rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>{status}</span>;
}

function CardSkeleton() {
  return (
    <div role="status" aria-label="Loading results" className="flex flex-col gap-2">
      {[0, 1, 2].map((row) => (
        <div key={row} className="flex animate-pulse items-start gap-3 rounded-2xl border border-line bg-surface p-3.5">
          <span className="h-10 w-10 flex-none rounded-xl bg-subtle" />
          <span className="flex flex-1 flex-col gap-2 pt-1">
            <span className="h-3.5 w-3/5 rounded bg-subtle" />
            <span className="h-3 w-2/5 rounded bg-subtle" />
          </span>
        </div>
      ))}
    </div>
  );
}

const svg = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.9,
  strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };

const ICONS: Record<CardKind, ReactNode> = {
  job: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18" /></>,
  event: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18" /></>,
  deadline: <><circle cx="12" cy="13" r="8" /><path d="M12 9v4l2.5 2.5M9 2h6" /></>,
  course: <path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 21V5M8 7h7" />,
  post: <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />,
  announcement: <path d="M3 11v2a1 1 0 0 0 1 1h3l5 4V6L7 10H4a1 1 0 0 0-1 1zM16 8a5 5 0 0 1 0 8M19 5a9 9 0 0 1 0 14" />,
  employer: <path d="M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16M16 9h2a2 2 0 0 1 2 2v10M2 21h20M8 7h4M8 11h4M8 15h4" />,
  grade: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  link: <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />,
};

const KindIcon = ({ kind }: { kind: CardKind }) => <svg {...svg}>{ICONS[kind] ?? ICONS.link}</svg>;
const ClockIcon = () => <svg {...svg} width={14} height={14} className="mt-[3px] flex-none text-muted"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;
const ArrowIcon = () => <svg {...svg} width={13} height={13} className="transition group-hover:translate-x-0.5 group-hover:-translate-y-0.5"><path d="M7 17 17 7M8 7h9v9" /></svg>;
