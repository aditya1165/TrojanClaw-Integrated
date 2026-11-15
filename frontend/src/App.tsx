import { useState, useRef, useEffect, type FormEvent, type KeyboardEvent } from "react";
import { useChat } from "@ai-sdk/react";
import type { UIMessage } from "ai";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_BASE_URL, chatEndpointFetch, authRequest, ApiError, type AccountProfile } from "./lib/api";
import AuthDialog from "./AuthDialog";
import EngageSCPanel from "./EngageSCPanel";
import "./App.css";

const getMessageText = (message: UIMessage): string => {
  const text = message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();

  return text || "";
};

export default function App() {
  const [account, setAccount] = useState<AccountProfile | null>(null);
  const [isCheckingSession, setIsCheckingSession] = useState(true);
  const [isSigningOut, setIsSigningOut] = useState(false);
  const isSignedIn = account !== null;
  const userName = account?.name || "";
  const username = account?.username || "";
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [syncStatusNote, setSyncStatusNote] = useState<string | null>(null);
  const [fileAttached, setFileAttached] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
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
    setInput,
    setMessages,
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

  const isLoading = status === "submitted" || status === "streaming";
  const hasHistory = messages.length > 0;

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "end",
    });
  }, [messages, status]);

  useEffect(() => {
    let active = true;
    authRequest<AccountProfile>("me")
      .then((profile) => { if (active) setAccount(profile); })
      .catch((error) => {
        if (active && !(error instanceof ApiError && error.status === 401)) {
          setSyncStatusNote("Could not restore your session. Please try signing in.");
        }
      })
      .finally(() => { if (active) setIsCheckingSession(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const expireSession = () => {
      stop();
      setAccount(null);
      setMessages([]);
      setFileAttached(null);
      setShowAuthModal(true);
    };
    window.addEventListener("auth-expired", expireSession);
    return () => window.removeEventListener("auth-expired", expireSession);
  }, [stop, setMessages]);

  const handleSignOut = async () => {
    setIsSigningOut(true);
    try {
      await authRequest<void>("logout", {});
      stop();
      setMessages([]);
      setInput("");
      setFileAttached(null);
      setAccount(null);
      setSyncStatusNote(null);
    } catch {
      setSyncStatusNote("Could not sign out. Please try again.");
    } finally {
      setIsSigningOut(false);
    }
  };

  const handlePromptSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isSignedIn) { setShowAuthModal(true); return; }
    if (!input.trim() || isLoading || isCheckingSession || isSigningOut) return;
    handleSubmit(event);
  };

  const handlePromptKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!isSignedIn) { setShowAuthModal(true); return; }
      if (!input.trim() || isLoading || isCheckingSession || isSigningOut) return;
      handleSubmit();
    }
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setFileAttached(file.name);
      if (setInput && !input) {
        setInput(`I have uploaded ${file.name}. Can you analyze deadlines and schedule study time?`);
      }
    }
  };

  return (
    <div className="flex flex-col w-full h-screen overflow-hidden text-on-surface bg-surface-bright">
      <div className="flex w-full h-full overflow-hidden">
        {/* LEFT SIDEBAR (~280px) */}
        <aside className="w-[280px] flex-shrink-0 bg-surface-container-lowest flex flex-col justify-between p-4 h-full overflow-y-auto border-r border-surface-container-high shadow-xs z-20">
          {/* Top Section */}
          <div className="flex flex-col gap-4">
            {/* Logo & Brand Header */}
            <div className="flex items-center gap-3 pb-3 border-b border-surface-container-high">
              <div className="w-10 h-10 rounded-lg overflow-hidden flex-shrink-0 flex items-center justify-center bg-primary-container/10 p-1">
                <img
                  alt="TrojanClaw USC Logo"
                  className="w-full h-full object-contain"
                  src="/logo.png"
                  onError={(e) => {
                    (e.currentTarget as HTMLImageElement).src =
                      "https://lh3.googleusercontent.com/aida/ALJJyrB8xm7S9KWu0EGScOwhTPocjZhoh_F5M7chVKztOMuzFiPm8Cl9ekXkbhDrC-_oU7gH-hryDkJNH-6LMoandrJ58UokHBu_z3h2lyTso2uswGNoUdxLsHeTzc5-xdIblfHUgaeXBwoc49NV7zutrkhke0teej2uWFpjw5gMcg0lqUSpkjIF8FG_h4KKO5R46AayzzA4fWKpbE8z6z9SDsdoI9Bt5b-zefyEKQkxlkR2";
                  }}
                />
              </div>
              <div className="flex flex-col min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="font-headline-sm text-headline-sm text-primary font-bold tracking-tight">
                    TrojanClaw
                  </span>
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-secondary-container text-on-secondary-container">
                    USC
                  </span>
                </div>
                <p className="font-label-sm text-label-sm text-on-surface-variant">USC Academic Copilot</p>
              </div>
            </div>

            <span className="text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">Extensions</span>
            <div className="flex flex-col gap-2">
              {[
                { name: "Brightspace", icon: "school" },
                { name: "Piazza", icon: "chat_bubble" },
                { name: "Google Calendar", icon: "event" },
              ].map((extension) => (
                <div key={extension.name} className="p-3 rounded-lg bg-surface-container-low border border-surface-container-high/60">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <span className="material-symbols-outlined text-primary text-[18px]" aria-hidden="true">{extension.icon}</span>
                    {extension.name}
                  </div>
                  <p className="mt-1 text-[11px] text-on-surface-variant">Not linked</p>
                </div>
              ))}
              <EngageSCPanel key={account?.id || "guest"} signedIn={isSignedIn} onSignIn={() => setShowAuthModal(true)} />
              <p className="text-[11px] leading-relaxed text-on-surface-variant">More extension connections coming soon.</p>
            </div>
          </div>

          {/* Bottom Profile Block */}
          <div className="pt-3 mt-3 border-t border-surface-container-high">
            {isSignedIn ? (
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="w-8 h-8 rounded-full bg-primary-container text-on-primary flex items-center justify-center font-bold text-xs flex-shrink-0 shadow-xs">
                    {userName.charAt(0)}
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span className="font-label-md text-label-md font-semibold text-on-surface truncate">
                      {userName}
                    </span>
                    <span className="font-body-sm text-body-sm text-on-surface-variant text-[11px] truncate">
                      @{username}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-1 text-on-surface-variant">
                  <button
                    onClick={() => setShowAuthModal(true)}
                    className="p-1 hover:text-primary transition-colors rounded hover:bg-surface-container"
                    title="Account settings"
                  >
                    <span className="material-symbols-outlined text-[18px]">settings</span>
                  </button>
                  <button
                    onClick={handleSignOut}
                    disabled={isSigningOut}
                    className="p-1 hover:text-error transition-colors rounded hover:bg-surface-container"
                    title="Sign out"
                  >
                    <span className="material-symbols-outlined text-[18px]">logout</span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-2.5">
                <div className="flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-full bg-surface-container-high text-on-surface-variant flex items-center justify-center font-bold text-xs flex-shrink-0">
                    <span className="material-symbols-outlined text-[18px]">account_circle</span>
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span className="font-label-md text-label-md font-semibold text-on-surface truncate">
                      Guest Visitor
                    </span>
                    <span className="font-body-sm text-body-sm text-on-surface-variant text-[11px] truncate">
                      Session not synced
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => setShowAuthModal(true)}
                  className="w-full py-2 px-3 rounded-lg bg-primary text-on-primary font-label-sm text-label-sm font-semibold hover:bg-primary-container transition-all flex items-center justify-center gap-1.5 shadow-xs"
                >
                  <span className="material-symbols-outlined text-[16px]">login</span>
                  Sign In / Get Started
                </button>
              </div>
            )}
          </div>
        </aside>

        {/* CENTER / MAIN WORKSPACE */}
        <main className="flex-1 bg-surface-bright flex flex-col justify-between h-full overflow-hidden">
          {/* Expanded Copilot Header */}
          <header className="bg-surface-container-lowest border-b border-surface-container-high px-6 py-4 flex items-center justify-between flex-shrink-0 shadow-xs">
            <div className="flex items-center gap-3.5">
              <div className="w-10 h-10 rounded-xl bg-secondary-container flex items-center justify-center text-on-secondary-fixed shadow-xs">
                <span className="material-symbols-outlined text-[24px]">psychology</span>
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="font-headline-sm text-headline-sm font-bold text-on-surface">Study Copilot</h1>
                  {isSignedIn ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping"></span>
                      Active
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-surface-container-high text-on-surface-variant">
                      <span className="w-1.5 h-1.5 rounded-full bg-amber-500"></span>
                      Guest Preview
                    </span>
                  )}
                </div>
                <p className="font-body-sm text-body-sm text-on-surface-variant flex items-center gap-2 mt-0.5">
                  <span>{isCheckingSession ? "Checking your session..." : isSignedIn ? "Your academic copilot" : "Sign in to start a conversation"}</span>
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              {syncStatusNote && (
                <span className="text-xs bg-emerald-100 text-emerald-800 font-medium px-3 py-1 rounded-full animate-fade-in">
                  {syncStatusNote}
                </span>
              )}
              {hasHistory && isSignedIn && (
                <button
                  type="button"
                  onClick={() => reload()}
                  disabled={isLoading}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-surface-container-high text-on-surface text-label-md font-medium hover:bg-surface-container transition-colors"
                  title="Regenerate last response"
                >
                  <span className="material-symbols-outlined text-[16px]">refresh</span>
                  <span>Regenerate</span>
                </button>
              )}
              {!isSignedIn ? (
                <>
                  <button
                    onClick={() => setShowAuthModal(true)}
                    className="hidden md:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-surface-container-high text-on-surface text-label-md font-medium hover:bg-surface-container transition-colors"
                  >
                    <span className="material-symbols-outlined text-[16px]">help_outline</span>
                    <span>Tour</span>
                  </button>
                  <button
                    onClick={() => setShowAuthModal(true)}
                    className="flex items-center gap-2 px-4 py-2 rounded-xl bg-primary text-on-primary font-label-md text-label-md font-bold hover:bg-primary-container transition-all shadow-xs"
                  >
                    <span className="material-symbols-outlined text-[18px]">lock_open</span>
                    <span>Sign in</span>
                  </button>
                </>
              ) : (
                <button
                  onClick={handleSignOut}
                    disabled={isSigningOut}
                  className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-surface-container-high text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors"
                >
                  {isSigningOut ? "Signing out..." : "Sign out"}
                </button>
              )}
            </div>
          </header>

          {/* Main Interactive Chat Timeline */}
          <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
            {/* Signed-out banner notification */}
            {!isSignedIn && (
              <div className="p-3.5 rounded-xl bg-surface-container-high border border-surface-container flex items-center justify-between gap-3 text-on-surface shadow-xs">
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="material-symbols-outlined text-secondary text-[22px] flex-shrink-0">info</span>
                  <p className="text-[12px] leading-tight text-on-surface-variant">
                    <strong className="text-on-surface font-semibold">You are in Preview Mode.</strong> Sign in with your
                    username and password to access your account.
                  </p>
                </div>
                <button
                  onClick={() => setShowAuthModal(true)}
                  className="flex-shrink-0 px-3 py-1.5 rounded-lg bg-secondary-container text-on-secondary-fixed text-label-sm font-bold hover:brightness-95 transition-all shadow-xs"
                >
                  Sign In
                </button>
              </div>
            )}

            <div className="chat-thread">
              {!hasHistory && (
                <div className="chat-empty">
                  <h2>What can I help you with?</h2>
                  <p>Ask about your courses, deadlines, or study plans.</p>
                </div>
              )}
              {messages.map((message) => {
                const isUser = message.role === "user";
                return (
                  <article key={message.id} className={`chat-message ${isUser ? "chat-message-user" : "chat-message-assistant"}`}>
                    <p className="chat-message-author">{isUser ? "You" : "TrojanClaw"}</p>
                    <div className="markdown-content">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{getMessageText(message)}</ReactMarkdown>
                    </div>
                  </article>
                );
              })}
              {status === "submitted" && <p role="status" className="chat-thinking">Thinking...</p>}
            </div>

            {/* Error Message */}
            {error && (
              <div className="p-3.5 rounded-xl bg-error-container text-on-error-container text-xs font-medium border border-error/30 flex items-center justify-between">
                <span>{error.message || "Failed to contact TrojanClaw backend."}</span>
                <button onClick={() => reload()} className="underline font-bold ml-2">
                  Retry
                </button>
              </div>
            )}

            <div ref={endOfMessagesRef} />
          </div>

          {/* Bottom Controls & Expansive Input Container */}
          <footer className="bg-surface-container-lowest border-t border-surface-container-high px-8 py-4 flex flex-col gap-3 flex-shrink-0">
            {/* Attachment preview if present */}
            {fileAttached && (
              <div className="flex items-center gap-2 text-xs bg-surface-container-low px-3 py-1 rounded-lg border border-surface-container w-fit">
                <span className="material-symbols-outlined text-[16px] text-primary">description</span>
                <span className="font-medium text-on-surface">{fileAttached}</span>
                <button onClick={() => setFileAttached(null)} className="text-on-surface-variant hover:text-error ml-2">
                  ×
                </button>
              </div>
            )}

            {/* Hidden File Input */}
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileUpload}
              className="hidden"
              accept=".pdf,.txt,.doc,.docx,.ics"
            />

            {/* Full-Width Input Box */}
            <form onSubmit={handlePromptSubmit}>
              <div className="relative bg-surface-container-low rounded-2xl p-2.5 flex items-center gap-2 shadow-xs border border-surface-container-high focus-within:ring-2 focus-within:ring-secondary-container focus-within:border-transparent transition-all">
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="p-2 text-on-surface-variant hover:text-on-surface rounded-xl hover:bg-surface-container transition-colors"
                  title="Upload syllabus or assignment"
                >
                  <span className="material-symbols-outlined text-[20px]">attach_file</span>
                </button>
                <input
                  aria-label="Message TrojanClaw"
                  value={input}
                  onChange={handleInputChange}
                  onKeyDown={handlePromptKeyDown}
                  className="flex-1 min-w-0 bg-transparent text-[13px] text-on-surface placeholder:text-on-surface-variant outline-none px-2 py-1"
                  placeholder="Message TrojanClaw..."
                  type="text"
                />
                {isLoading ? (
                  <button
                    type="button"
                    onClick={stop}
                    className="px-4 py-2 rounded-xl bg-surface-container-highest text-on-surface-variant font-label-md text-label-md font-semibold flex items-center gap-1.5 shadow-xs hover:bg-surface-container transition-all flex-shrink-0"
                  >
                    <span>Stop</span>
                    <span className="material-symbols-outlined text-[16px]">stop</span>
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!input.trim() || isCheckingSession || isSigningOut}
                    className="px-4 py-2 rounded-xl bg-primary text-on-primary font-label-md text-label-md font-semibold flex items-center gap-1.5 shadow-xs hover:bg-primary-container disabled:opacity-50 disabled:cursor-not-allowed transition-all flex-shrink-0"
                  >
                    <span>Send</span>
                    <span className="material-symbols-outlined text-[16px]">arrow_upward</span>
                  </button>
                )}
              </div>
            </form>
          </footer>
        </main>
      </div>

      {showAuthModal && (
        <AuthDialog
          onClose={() => setShowAuthModal(false)}
          onSubmit={(profile) => {
            stop();
            setMessages([]);
            setInput("");
            setAccount(profile);
            setSyncStatusNote(null);
            setShowAuthModal(false);
          }}
        />
      )}
    </div>
  );
}
