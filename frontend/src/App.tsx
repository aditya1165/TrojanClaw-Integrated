import { useEffect, useRef } from "react";
import { useChat } from "@ai-sdk/react";
import type { UIMessage } from "ai";
import type { FormEvent } from "react";
import type { KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_BASE_URL, chatEndpointFetch } from "./lib/api";
import "./App.css";

const publicIcons = [
  "/Generated Image April 04, 2026 - 1_08PM.png",
  "/Generated Image April 04, 2026 - 1_09PM (1).png",
  "/Generated Image April 04, 2026 - 1_09PM (2).png",
  "/Generated Image April 04, 2026 - 1_09PM.png",
];
const randomIconIndex = Math.floor(Math.random() * publicIcons.length);
const selectedIcon = encodeURI(publicIcons[randomIconIndex]);

const getMessageText = (message: UIMessage): string => {
  const text = message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();

  return text || "";
};

function App() {
  const endOfMessagesRef = useRef<HTMLDivElement>(null);

  const {
    messages,
    input,
    status,
    error,
    handleInputChange,
    handleSubmit,
    stop,
    reload,
  } = useChat({
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

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "end",
    });
  }, [messages, status]);

  const hasHistory = messages.length > 0;
  const isLoading = status === "submitted" || status === "streaming";
  const handlePromptSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    handleSubmit(event);
  };

  const handlePromptKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key !== "Enter" ||
      event.shiftKey ||
      event.nativeEvent.isComposing
    ) {
      return;
    }

    event.preventDefault();

    if (!input.trim() || isLoading) {
      return;
    }

    handleSubmit();
  };

  return (
    <div className="page-shell">
      <main className="chat-stage">
        <header className="top-header">
          <div />
          <div className="header-actions">
            <button
              type="button"
              className="refresh-btn"
              onClick={() => void reload()}
              disabled={messages.length === 0 || isLoading}
            >
              Regenerate
            </button>
            <div className="user-icon" aria-label="User profile" role="img">
              <span>A</span>
            </div>
          </div>
        </header>

        <section
          className={`conversation ${hasHistory ? "with-history" : ""}`}
          aria-busy={isLoading}
        >
          {!hasHistory && (
            <div className="hero-branding">
              <img
                className="hero-logo"
                src={selectedIcon}
                alt="TrojanClaw logo"
              />
              <h1 className="hero-title">TrojanClaw</h1>
              <p className="hero-subtitle">USC AI Concierge</p>
              <p className="hero-description">
                Ask about schedules, campus resources, study spaces, and student
                life in one place.
              </p>
              {isLoading && (
                <div className="hero-skeletons" aria-hidden="true">
                  <div className="skeleton skeleton-pill" />
                  <div className="skeleton skeleton-pill short" />
                </div>
              )}
            </div>
          )}

          {hasHistory && (
            <div className="history" aria-live="polite">
              {messages.map((message) => (
                <article key={message.id} className={`bubble ${message.role}`}>
                  <div className="bubble-meta">
                    {message.role === "user" ? "You" : "TrojanClaw"}
                  </div>
                  {message.role === "assistant" ? (
                    <div className="markdown-body">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {getMessageText(message)}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <p>{getMessageText(message)}</p>
                  )}
                </article>
              ))}
              {isLoading && (
                <article className="bubble assistant loading">
                  <div className="bubble-meta">TrojanClaw</div>
                  <div className="skeleton skeleton-line" />
                  <div className="skeleton skeleton-line short" />
                </article>
              )}
              <div ref={endOfMessagesRef} />
            </div>
          )}

          <form className="prompt-card" onSubmit={handlePromptSubmit}>
            <label htmlFor="prompt" className="sr-only">
              Enter your prompt
            </label>
            <textarea
              id="prompt"
              value={input}
              onChange={handleInputChange}
              onKeyDown={handlePromptKeyDown}
              rows={3}
              placeholder="How can I help you today?"
            />

            <div className="prompt-bottom">
              <button
                type="submit"
                className="send-btn"
                disabled={!input.trim() || isLoading}
              >
                {isLoading ? "Sending..." : "Send"}
              </button>
              {isLoading && (
                <button type="button" className="stop-btn" onClick={stop}>
                  Stop
                </button>
              )}
            </div>

          </form>

          {isLoading && (
            <p className="loading-indicator" role="status" aria-live="polite">
              TrojanClaw is searching USC sources...
            </p>
          )}

          {error && <p className="error-text">{error.message}</p>}
        </section>
      </main>
    </div>
  );
}

export default App;
