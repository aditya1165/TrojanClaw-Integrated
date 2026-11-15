import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "./api";
import type { CalendarProposal, ChatResponse, ChatSaved, ConversationMessage, Source } from "./api";

export interface ChatItem {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
}

export type ChatStatus = "ready" | "submitted" | "streaming" | "error";

type StreamEvent =
  | { type: "activity"; text: string }
  | { type: "text"; text: string }
  | { type: "reset" }
  | { type: "error"; message: string }
  | ({ type: "done" } & ChatResponse);

export const fromSaved = (message: ConversationMessage): ChatItem => ({
  id: message.id, role: message.role, content: message.content, sources: message.sources ?? [],
});

/**
 * One chat's messages and the reply being written. The server streams
 * newline-delimited JSON events (see backend/app/api/routes/chat.py).
 * `body` builds the request for a new message.
 */
export function useChatStream(initial: ChatItem[], body: (message: string) => object) {
  const [messages, setMessages] = useState(initial);
  const [status, setStatus] = useState<ChatStatus>("ready");
  const [activity, setActivity] = useState("");
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const latest = useRef({ body, messages });
  useEffect(() => { latest.current = { body, messages }; });

  const stop = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
  }, []);
  useEffect(() => () => controller.current?.abort(), []);

  const send = useCallback(async (text: string) => {
    const question = text.trim();
    if (!question || controller.current) return;
    const replyId = `reply-${crypto.randomUUID()}`;
    const setReply = (update: (reply: ChatItem) => ChatItem) => setMessages((current) => {
      const index = current.findIndex((item) => item.id === replyId);
      if (index === -1) return [...current, update({ id: replyId, role: "assistant", content: "", sources: [] })];
      const next = current.slice();
      next[index] = update(current[index]);
      return next;
    });

    setMessages((current) => [...current, { id: `user-${crypto.randomUUID()}`, role: "user", content: question, sources: [] }]);
    setStatus("submitted");
    setActivity("");
    setError("");
    window.dispatchEvent(new Event("calendar-chat-started"));
    const abort = new AbortController();
    controller.current = abort;

    try {
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(latest.current.body(question)),
        signal: abort.signal,
      });
      if (!response.ok || !response.body) {
        if (response.status === 401) window.dispatchEvent(new Event("auth-expired"));
        const data = await response.json().catch(() => null) as { detail?: unknown } | null;
        throw new Error(typeof data?.detail === "string" ? data.detail : "Could not send your message. Please try again.");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split("\n");
        buffer = done ? "" : lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line) as StreamEvent;
          if (event.type === "activity") setActivity(event.text);
          else if (event.type === "text") {
            setStatus("streaming");
            setReply((reply) => ({ ...reply, content: reply.content + event.text }));
          } else if (event.type === "reset") setReply((reply) => ({ ...reply, content: "" }));
          else if (event.type === "error") throw new Error(event.message);
          else if (event.type === "done") {
            // The saved reply is final; it can differ from the streamed text.
            setReply((reply) => ({ ...reply, content: event.response, sources: event.sources ?? [] }));
            window.dispatchEvent(new CustomEvent<CalendarProposal[]>("calendar-drafted", { detail: event.calendar_proposals ?? [] }));
            if (event.conversation_id) {
              window.dispatchEvent(new CustomEvent<ChatSaved>("chat-saved", {
                detail: { conversation_id: event.conversation_id, title: event.title ?? null },
              }));
            }
          }
        }
        if (done) break;
      }
      setStatus("ready");
    } catch (reason) {
      if (abort.signal.aborted) {
        setStatus("ready"); // stopped: keep what was written so far
      } else {
        setMessages((current) => current.filter((item) => item.id !== replyId)); // nothing was saved
        setError(reason instanceof Error ? reason.message : "Could not reach TrojanClaw.");
        setStatus("error");
      }
    } finally {
      setActivity("");
      if (controller.current === abort) controller.current = null;
    }
  }, []);

  /** Send the last question again; a failed question is not saved, so this is safe. */
  const retry = useCallback(() => {
    const items = latest.current.messages;
    const index = items.map((item) => item.role).lastIndexOf("user");
    if (index === -1) return;
    setMessages(items.slice(0, index));
    void send(items[index].content);
  }, [send]);

  return { messages, status, activity, error, send, stop, retry };
}
