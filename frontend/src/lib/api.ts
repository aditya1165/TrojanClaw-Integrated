export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface ChatRequest {
  messages: ChatMessage[];
  connectors?: string[];
  timezone?: string;
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
}

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type ConnectorProvider = "piazza" | "brightspace" | "handshake";
export interface ConnectorStatus {
  provider: ConnectorProvider;
  state: "not_configured" | "configured" | "saved" | "connecting" | "connected" | "needs_login" | "unavailable";
  message?: string | null;
}

export async function getConnectors(): Promise<ConnectorStatus[]> {
  const response = await fetch(`${API_BASE_URL}/integrations`, { credentials: "include", cache: "no-store" });
  if (!response.ok) throw new Error("Could not load connectors.");
  return response.json() as Promise<ConnectorStatus[]>;
}

export async function connectProvider(provider: ConnectorProvider, action: "connect" | "verify"): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/integrations/${provider}/${action}`, {
    credentials: "include",
    method: "POST", headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    const body = await response.json() as { detail?: string };
    throw new Error(body.detail ?? "Could not connect. Please try again.");
  }
}

export interface AccountProfile {
  id: string;
  name: string;
  username: string;
}

export interface UscLoginStatus {
  status: "waiting" | "failed" | "done";
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

export async function sendChatMessage(
  request: ChatRequest,
): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const fallbackMessage = `Request failed with status ${response.status}`;

    try {
      const errorBody = (await response.json()) as { detail?: string };
      throw new Error(errorBody.detail ?? fallbackMessage);
    } catch {
      throw new Error(fallbackMessage);
    }
  }

  return (await response.json()) as ChatResponse;
}

export async function chatEndpointFetch(
  _input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  window.dispatchEvent(new Event("calendar-chat-started"));
  const response = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: init?.body,
    signal: init?.signal,
    credentials: "include",
  });

  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("auth-expired"));
    const data = await response.json().catch(() => null) as { detail?: unknown } | null;
    const message = typeof data?.detail === "string" ? data.detail : "Could not send your message. Please try again.";
    return new Response(message, { status: response.status, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }

  const data = (await response.json()) as ChatResponse;

  window.dispatchEvent(new CustomEvent<CalendarProposal[]>("calendar-drafted", {
    detail: data.calendar_proposals ?? [],
  }));

  return new Response(data.response, {
    status: response.status,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}
