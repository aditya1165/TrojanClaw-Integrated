import { useEffect, useRef, type FormEvent, type KeyboardEvent } from "react";
import { useChat } from "@ai-sdk/react";
import type { UIMessage } from "ai";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_BASE_URL, chatEndpointFetch } from "../lib/api";
import type { AccountProfile } from "../lib/api";
import EngageSCPanel from "../EngageSCPanel";
import type { Theme } from "../theme";
import { ArrowUpIcon, Button, Logo, PlusIcon, StopIcon, ThemeToggle } from "../components/ui";

// Connecting is not wired to the backend yet, so every source shows "Not connected".
const SOURCES = ["Brightspace", "Piazza", "Google Calendar"];

const getMessageText = (message: UIMessage): string =>
  message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();

interface ChatProps {
  theme: Theme;
  onToggleTheme: () => void;
  onHome: () => void;
  onConnect: () => void;
  account: AccountProfile;
  onSignOut: () => void;
  signingOut: boolean;
}

export default function Chat({ theme, onToggleTheme, onHome, onConnect, account, onSignOut, signingOut }: ChatProps) {
  const endOfMessagesRef = useRef<HTMLDivElement>(null);

  const { messages, input, status, error, handleInputChange, handleSubmit, stop, reload, setMessages } =
    useChat({
      api: `${API_BASE_URL}/chat`,
      streamProtocol: "text",
      experimental_prepareRequestBody: ({ messages: uiMessages }) => ({
        messages: uiMessages
          .map((message) => ({
            role: message.role === "assistant" ? "assistant" : "user",
            content: getMessageText(message),
          }))
          .filter((message) => message.content.length > 0),
      }),
      fetch: chatEndpointFetch,
    });

  const isLoading = status === "submitted" || status === "streaming";

  useEffect(() => () => stop(), [stop]);

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  const submit = (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    if (!input.trim() || isLoading || signingOut) return;
    handleSubmit(event);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg md:h-screen md:flex-row">
      <aside className="flex flex-col gap-5 overflow-y-auto border-b border-line bg-surface p-4 md:w-64 md:flex-none md:border-r md:border-b-0">
        <div className="flex items-center justify-between gap-2">
          <button type="button" onClick={onHome} aria-label="Go to home page" className="rounded-lg">
            <Logo />
          </button>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        </div>

        <Button variant="secondary" onClick={() => setMessages([])} className="justify-start">
          <PlusIcon />
          New chat
        </Button>
        <Button variant="ghost" onClick={onConnect} disabled={signingOut} className="justify-start">Manage sources</Button>
        <EngageSCPanel key={account.id} signedIn onSignIn={onHome} />

        <div className="flex flex-col gap-2.5 border-t border-line px-1 pt-4 md:mt-auto">
          <span className="text-xs font-medium text-muted">Sources</span>
          {SOURCES.map((source) => (
            <div key={source} className="flex items-center gap-2 text-[13px]">
              <span className="h-1.5 w-1.5 rounded-full bg-line" />
              <span className="flex-1">{source}</span>
              <span className="text-muted">Not connected</span>
            </div>
          ))}
        </div>
        <div className="flex items-center justify-between gap-2 border-t border-line pt-4">
          <div className="min-w-0 text-sm"><p className="truncate font-medium">{account.name}</p><p className="truncate text-xs text-muted">@{account.username}</p></div>
          <Button variant="ghost" onClick={onSignOut} disabled={signingOut}>{signingOut ? "Signing out..." : "Sign out"}</Button>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-3xl flex-col gap-7 px-6 py-8">
            {messages.length === 0 && (
              <div className="py-[14vh] text-center">
                <h1 className="text-2xl font-semibold tracking-[-0.03em]">What can I help you with?</h1>
                <p className="mt-2 text-sm text-muted">Ask about your courses, deadlines or campus.</p>
              </div>
            )}

            {messages.map((message) =>
              message.role === "user" ? (
                <div
                  key={message.id}
                  className="max-w-[80%] self-end rounded-2xl bg-subtle px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap"
                >
                  {getMessageText(message)}
                </div>
              ) : (
                <article key={message.id} className="markdown-content">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{getMessageText(message)}</ReactMarkdown>
                </article>
              ),
            )}

            {status === "submitted" && (
              <p role="status" className="text-sm text-muted">
                Thinking…
              </p>
            )}

            {error && (
              <div className="flex items-center justify-between gap-3 rounded-xl bg-error-soft px-4 py-3 text-sm text-error">
                <span>{error.message || "Could not reach the TrojanClaw backend."}</span>
                <Button variant="ghost" disabled={isLoading || signingOut} onClick={() => reload()} className="text-error underline">
                  Retry
                </Button>
              </div>
            )}

            <div ref={endOfMessagesRef} />
          </div>
        </div>

        <div className="mx-auto flex w-full max-w-3xl flex-col gap-2.5 px-6 pb-5">
          <form
            onSubmit={submit}
            className="flex items-end gap-3 rounded-2xl border border-line bg-surface py-3 pr-3 pl-4 focus-within:border-muted"
          >
            <textarea
              aria-label="Message TrojanClaw"
              rows={2}
              value={input}
              disabled={signingOut}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder="Ask about deadlines, courses or campus…"
              className="flex-1 resize-none bg-transparent text-[15px] leading-normal text-fg outline-none placeholder:text-muted"
            />
            {isLoading ? (
              <button
                type="button"
                onClick={stop}
                aria-label="Stop"
                className="flex h-11 w-11 flex-none items-center justify-center rounded-xl bg-subtle text-fg"
              >
                <StopIcon />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim() || signingOut}
                aria-label="Send"
                className="flex h-11 w-11 flex-none items-center justify-center rounded-xl bg-accent text-on-accent transition hover:brightness-110 disabled:opacity-40"
              >
                <ArrowUpIcon />
              </button>
            )}
          </form>
          <p className="text-center text-xs text-muted">
            TrojanClaw only reads your data. Double-check important dates in Brightspace.
          </p>
        </div>
      </main>
    </div>
  );
}
