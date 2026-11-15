/**
 * Result cards inside a reply. Claude writes lists of jobs, events, deadlines, courses or posts
 * as a ```cards JSON block (see prompts/ai_instructions.md); the backend keeps only links a tool
 * returned (backend/app/services/answer_cards.py). components/AnswerCards.tsx renders them.
 */

export type CardKind = "job" | "event" | "deadline" | "course" | "post" | "announcement" | "employer" | "grade" | "link";

export interface AnswerCard {
  kind: CardKind;
  title: string;
  subtitle?: string;
  when?: string;
  location?: string;
  tags?: string[];
  status?: string;
  note?: string;
  url?: string;
}

export interface CardBlockData { title?: string; items: AnswerCard[] }

export function parseCards(raw: string): CardBlockData | null {
  try {
    const data: unknown = JSON.parse(raw);
    const block = Array.isArray(data) ? { items: data } : data;
    if (!block || typeof block !== "object" || !Array.isArray((block as CardBlockData).items)) return null;
    const items = (block as CardBlockData).items.filter(
      (item): item is AnswerCard => !!item && typeof item === "object" && typeof item.title === "string" && !!item.title.trim(),
    );
    const title = (block as CardBlockData).title;
    return items.length ? { title: typeof title === "string" ? title : undefined, items } : null;
  } catch {
    return null;
  }
}

/** Every link used by the cards in a reply, so the Sources row can skip them. */
export function cardLinks(markdown: string): Set<string> {
  const links = new Set<string>();
  for (const match of markdown.matchAll(/```cards[^\S\n]*\n([\s\S]*?)\n?```/g)) {
    parseCards(match[1])?.items.forEach((item) => item.url && links.add(item.url));
  }
  return links;
}

/** "Handshake", "Brightspace", ... from a link, for the Open button. */
export function siteName(url: string): string {
  let host = "";
  try {
    host = new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "link";
  }
  const known: [RegExp, string][] = [
    [/(^|\.)joinhandshake\.com$/, "Handshake"],
    [/^brightspace\.usc\.edu$/, "Brightspace"],
    [/(^|\.)piazza\.com$/, "Piazza"],
    [/^engage\.usc\.edu$/, "EngageSC"],
    [/^calendar\.google\.com$/, "Google Calendar"],
    [/^webreg\.usc\.edu$/, "WebReg"],
    [/(^|\.)usc\.edu$/, "USC"],
  ];
  return known.find(([pattern]) => pattern.test(host))?.[1] ?? host;
}
