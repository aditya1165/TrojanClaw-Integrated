export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface ChatRequest {
  messages: ChatMessage[];
  connectors?: string[];
}

export interface ChatResponse {
  response: string;
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
    return response;
  }

  const data = (await response.json()) as ChatResponse;

  return new Response(data.response, {
    status: response.status,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}
