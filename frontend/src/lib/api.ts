export type ChatRole = "user" | "assistant";

export interface ConversationSummary {
  id: string;
  title: string | null;
  updated_at: string;
  pinned: boolean;
}

/** Where part of a reply came from: a deadline, post, announcement, event, page or connector. */
export interface Source {
  title: string;
  url: string | null;
  course: string | null;
  kind: string | null;
}

/** A file attached to a chat message (kept in the private Supabase bucket). */
export interface Attachment {
  id: string;
  name: string;
  mime: string;
  size: number;
  kind: "pdf" | "image" | "text" | "docx" | "pptx" | "xlsx";
}

export interface ConversationMessage {
  id: string;
  role: ChatRole;
  content: string;
  created_at: string;
  sources: Source[];
  attachments: Attachment[];
}

/** Sent after each reply is saved, so the chat list can update. */
export interface ChatSaved {
  conversation_id: string;
  title: string | null;
}

export interface CalendarEventDraft {
  title: string;
  start: string;
  end: string;
  timezone: string;
  description: string;
  location: string;
  recurrence?: { frequency: "DAILY" | "WEEKLY" | "MONTHLY"; interval: number; weekdays: string[]; count: number | null; until: string | null } | null;
  reminder_minutes?: number | null;
}

export interface CalendarProposal {
  id: string;
  version: number;
  event: CalendarEventDraft;
  calendar: string;
  status: "pending" | "approved" | "applied" | "cancelled";
  expires_at: string;
  event_url: string | null;
  operation?: "create" | "update";
  scope?: "event" | "series";
  before?: { summary?: string; start?: { dateTime?: string }; end?: { dateTime?: string }; location?: string; description?: string; recurrence?: string[]; reminders?: object } | null;
  occurrences?: { start: string; end: string }[];
  recurrence_changed?: boolean;
  reminder_changed?: boolean;
}

export interface CalendarStatus {
  configured: boolean;
  connected: boolean;
  calendar: string | null;
}

export interface ChatResponse {
  response: string;
  calendar_proposals?: CalendarProposal[];
  sources?: Source[];
  conversation_id?: string;
  title?: string | null;
}

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "/api";

export type ConnectorProvider = "piazza" | "brightspace" | "handshake";
export interface RemoteLogin { login_id: string; live_url: string; state?: string }
export interface ConnectorStatus {
  provider: ConnectorProvider;
  state: "not_configured" | "configured" | "saved" | "connecting" | "connected" | "needs_login" | "unavailable" | "revoking";
  message?: string | null;
}

export async function getConnectors(): Promise<ConnectorStatus[]> {
  const response = await fetch(`${API_BASE_URL}/integrations`, { credentials: "include", cache: "no-store" });
  if (!response.ok) throw new Error("Could not load connectors.");
  return response.json() as Promise<ConnectorStatus[]>;
}

export async function connectProvider(provider: ConnectorProvider, action: "connect" | "verify"): Promise<RemoteLogin | undefined> {
  const response = await fetch(`${API_BASE_URL}/integrations/${provider}/${action}`, {
    credentials: "include",
    method: "POST", headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    const body = await response.json() as { detail?: string };
    throw new Error(body.detail ?? "Could not connect. Please try again.");
  }
  const result = await response.json();
  return result.live_url ? result as RemoteLogin : undefined;
}

export interface AccountProfile {
  id: string;
  name: string;
  username: string;
}

export interface UscLoginStatus {
  status: "waiting" | "connecting" | "interaction_required" | "failed" | "done";
  phase?: "brightspace" | "handshake";
  detail?: string | null;
  account?: AccountProfile | null;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function apiRequest<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "include",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("auth-expired"));
    const data = await response.json().catch(() => ({}));
    const detail = typeof data.detail === "string"
      ? data.detail
      : Array.isArray(data.detail)
        ? data.detail.map((item: { msg: string }) => item.msg.replace(/^Value error, /, "")).join(" ")
        : "Could not reach your account. Please try again.";
    throw new ApiError(detail, response.status);
  }
  return response.status === 204 ? undefined as T : response.json();
}

export function authRequest<T>(path: string, body?: object): Promise<T> {
  return apiRequest<T>(`/auth/${path}`, body);
}


export const listConversations = (search = "") =>
  apiRequest<ConversationSummary[]>(`/conversations${search ? `?q=${encodeURIComponent(search)}` : ""}`);
export const pinConversation = (id: string, pinned: boolean) =>
  apiRequest<ConversationSummary>(`/conversations/${encodeURIComponent(id)}/pin`, { pinned });
export const getConversationMessages = (id: string) =>
  apiRequest<ConversationMessage[]>(`/conversations/${encodeURIComponent(id)}/messages`);
export const renameConversation = (id: string, title: string) =>
  apiRequest<ConversationSummary>(`/conversations/${encodeURIComponent(id)}/rename`, { title });
export const deleteConversation = (id: string) =>
  apiRequest<void>(`/conversations/${encodeURIComponent(id)}/delete`, {});

export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
export const MAX_ATTACHMENTS = 5;
export const UPLOAD_ACCEPT = ".pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.md,.csv,.json,.py,.js,.ts,.java,.c,.cpp,.html,.css,.sql,.docx,.pptx,.xlsx";
export const uploadUrl = (id: string) => `${API_BASE_URL}/uploads/${encodeURIComponent(id)}`;

/** Upload one file for the next chat message. */
export async function uploadFile(file: File): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE_URL}/uploads`, { method: "POST", body: form, credentials: "include" });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("auth-expired"));
    const data = await response.json().catch(() => ({})) as { detail?: unknown };
    throw new ApiError(typeof data.detail === "string" ? data.detail : "Couldn't upload this file.", response.status);
  }
  return response.json();
}
