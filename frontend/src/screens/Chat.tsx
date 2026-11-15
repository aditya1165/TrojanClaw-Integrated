import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  deleteConversation, getConversationMessages, listConversations, pinConversation, renameConversation,
} from "../lib/api";
import type { AccountProfile, ChatSaved, ConnectorProvider, ConversationSummary, Source } from "../lib/api";
import { fromSaved, useChatStream, type ChatItem } from "../lib/chatStream";
import { effectiveTimezone, useSettings } from "../lib/settings";
import { activeSources, INFO, useSources, USC, type Sources } from "../lib/sources";
import { toast } from "../lib/toast";
import { CalendarProposals } from "../components/Calendar";
import ChatList from "../components/ChatList";
import SettingsDialog from "../components/SettingsDialog";
import { SourcesDialog, SourcesMenu } from "../components/Sources";
import type { Theme } from "../theme";
import {
  ArrowUpIcon, Button, CloseIcon, ExternalIcon, GearIcon, Logo, MenuIcon, PlusIcon, StopIcon, ThemeToggle,
} from "../components/ui";

// Each saved chat has its own address, so refresh and back/forward keep your place.
const CHAT_PATH = /^\/chat\/([0-9a-f-]{36})\/?$/i;
const chatIdFromPath = () => window.location.pathname.match(CHAT_PATH)?.[1] ?? null;
const newThreadKey = () => `new-${crypto.randomUUID()}`;

/** `key` keeps one chat view alive while a new chat gets its saved id. `messages` is null while loading. */
interface Thread { key: string; id: string | null; messages: ChatItem[] | null }

const threadFor = (id: string | null): Thread =>
  id ? { key: id, id, messages: null } : { key: newThreadKey(), id: null, messages: [] };

const focusComposer = () => document.getElementById("composer")?.focus();

interface ChatProps {
  selectedConnectors: ConnectorProvider[];
  onConnectorsChange: (providers: ConnectorProvider[]) => void;
  theme: Theme;
  onToggleTheme: () => void;
  onTheme: (theme: Theme) => void;
  onHowItWorks: () => void;
  account: AccountProfile;
  onSignOut: () => void;
  signingOut: boolean;
}

export default function Chat({ theme, onToggleTheme, onTheme, onHowItWorks, account, onSignOut, signingOut, selectedConnectors, onConnectorsChange }: ChatProps) {
  const [showSources, setShowSources] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [drawer, setDrawer] = useState(false); // the sidebar on phones
  const sources = useSources(selectedConnectors, onConnectorsChange);
  const sourceCount = activeSources(sources, selectedConnectors).length;

  const [thread, setThread] = useState<Thread>(() => threadFor(chatIdFromPath()));
  const threadRef = useRef(thread);
  useEffect(() => { threadRef.current = thread; }, [thread]);
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [listError, setListError] = useState("");
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<ConversationSummary[] | null>(null);

  const refreshList = useCallback(() => {
    listConversations()
      .then((items) => { setConversations(items); setListError(""); })
      .catch(() => setListError("Couldn't load your chats."));
  }, []);

  useEffect(() => { refreshList(); }, [refreshList]);

  // Search titles and messages on the server, a moment after typing stops.
  useEffect(() => {
    const query = search.trim();
    if (!query) return;
    let active = true;
    const timer = window.setTimeout(() => {
      listConversations(query)
        .then((items) => { if (active) setResults(items); })
        .catch(() => { if (active) setResults([]); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [search]);

  const openChat = useCallback((id: string | null, push = true) => {
    setDrawer(false);
    if (threadRef.current.id === id && id !== null) return;
    setThread(threadFor(id));
    const path = id ? `/chat/${id}` : "/";
    if (push && window.location.pathname !== path) window.history.pushState(null, "", path);
    if (!id) window.setTimeout(focusComposer, 0);
  }, []);

  // Back and forward buttons.
  useEffect(() => {
    const onPop = () => openChat(chatIdFromPath(), false);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [openChat]);

  // Keyboard shortcuts: ⌘K new chat, ⌘/ type a message, ⌘⇧F search chats.
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "k" && !event.shiftKey) { event.preventDefault(); openChat(null); }
      else if (key === "/" && !event.shiftKey) { event.preventDefault(); focusComposer(); }
      else if (key === "f" && event.shiftKey) {
        event.preventDefault();
        setDrawer(true);
        window.setTimeout(() => document.getElementById("chat-search")?.focus(), 0);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openChat]);

  // Load a saved chat's messages.
  useEffect(() => {
    if (thread.messages !== null || !thread.id) return;
    let active = true;
    getConversationMessages(thread.id)
      .then((items) => {
        if (active) setThread((current) => current.key === thread.key ? { ...current, messages: items.map(fromSaved) } : current);
      })
      .catch((reason: Error) => {
        if (!active) return;
        toast(reason.message || "That chat couldn't be opened.", "error");
        setThread(threadFor(null));
        window.history.replaceState(null, "", "/");
      });
    return () => { active = false; };
  }, [thread]);

  // After each reply is saved: a new chat gets its id and address, and the list updates.
  useEffect(() => {
    const onSaved = (event: Event) => {
      const { conversation_id: id } = (event as CustomEvent<ChatSaved>).detail;
      const wasNew = threadRef.current.id === null;
      if (wasNew) {
        setThread((current) => current.id ? current : { ...current, id });
        window.history.replaceState(null, "", `/chat/${id}`);
      }
      refreshList();
      if (wasNew) window.setTimeout(refreshList, 4000); // the AI-written title arrives a moment later
    };
    window.addEventListener("chat-saved", onSaved);
    return () => window.removeEventListener("chat-saved", onSaved);
  }, [refreshList]);

  const replace = (updated: ConversationSummary) => {
    const swap = (items: ConversationSummary[] | null) => items?.map((item) => item.id === updated.id ? updated : item) ?? items;
    setConversations(swap);
    setResults(swap);
  };

  const rename = async (id: string, title: string) => {
    try {
      replace(await renameConversation(id, title));
      toast("Chat renamed");
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : "Couldn't rename that chat.", "error");
    }
  };

  const pin = async (id: string, pinned: boolean) => {
    try {
      await pinConversation(id, pinned);
      refreshList(); // pinned chats move to the top
      toast(pinned ? "Chat pinned" : "Chat unpinned");
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : "Couldn't pin that chat.", "error");
    }
  };

  const remove = async (id: string) => {
    try {
      await deleteConversation(id);
      const drop = (items: ConversationSummary[] | null) => items?.filter((item) => item.id !== id) ?? items;
      setConversations(drop);
      setResults(drop);
      if (threadRef.current.id === id) openChat(null);
      toast("Chat deleted");
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : "Couldn't delete that chat.", "error");
    }
  };

  const activeTitle = conversations?.find((chat) => chat.id === thread.id)?.title || "New chat";

  return (
    <div className="flex h-dvh flex-col bg-bg text-fg md:flex-row">
      {/* Phones: a top bar opens the sidebar as a drawer. */}
      <header className="flex h-14 flex-none items-center gap-2 border-b border-line bg-surface px-2 md:hidden">
        <button type="button" aria-label="Open menu" onClick={() => setDrawer(true)}
          className="flex h-11 w-11 items-center justify-center rounded-xl text-muted hover:text-fg"><MenuIcon /></button>
        <p className="min-w-0 flex-1 truncate text-center text-sm font-medium">{activeTitle}</p>
        <button type="button" aria-label="New chat" onClick={() => openChat(null)}
          className="flex h-11 w-11 items-center justify-center rounded-xl text-muted hover:text-fg"><PlusIcon /></button>
      </header>
      {drawer && <div className="fixed inset-0 z-30 bg-black/40 md:hidden" aria-hidden="true" onClick={() => setDrawer(false)} />}

      <aside aria-label="Sidebar"
        className={`fixed inset-y-0 left-0 z-40 flex w-72 max-w-[85vw] flex-col gap-2 border-r border-line bg-surface p-4 transition-transform md:static md:z-auto md:w-64 md:max-w-none md:flex-none md:translate-x-0 ${drawer ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="mb-3 flex items-center justify-between gap-2">
          <button type="button" onClick={() => openChat(null)} aria-label="Start a new chat" className="rounded-lg">
            <Logo />
          </button>
          <div className="flex items-center">
            <ThemeToggle theme={theme} onToggle={onToggleTheme} />
            <button type="button" aria-label="Close menu" onClick={() => setDrawer(false)}
              className="flex h-11 w-11 items-center justify-center rounded-xl text-muted hover:text-fg md:hidden"><CloseIcon /></button>
          </div>
        </div>

        <Button variant="secondary" onClick={() => openChat(null)} className="justify-start" title="New chat (⌘K)">
          <PlusIcon />
          New chat
        </Button>
        <Button variant="ghost" onClick={() => { setDrawer(false); setShowSources(true); }} disabled={signingOut} className="justify-start">
          Sources
          <span className="ml-auto text-xs font-normal">{sourceCount} on</span>
        </Button>

        <ChatList conversations={search.trim() ? results : conversations} search={search}
          onSearch={(value) => { setSearch(value); if (!value.trim()) setResults(null); }}
          activeId={thread.id} error={listError}
          onOpen={(id) => openChat(id)} onRename={rename} onPin={pin} onDelete={remove} />

        <Button variant="ghost" onClick={onHowItWorks} className="justify-start">How it works</Button>
        <div className="flex items-center gap-2 border-t border-line pt-4">
          <div className="min-w-0 flex-1 text-sm"><p className="truncate font-medium">{account.name}</p><p className="truncate text-xs text-muted">@{account.username}</p></div>
          <button type="button" aria-label="Settings" title="Settings" onClick={() => { setDrawer(false); setShowSettings(true); }}
            className="flex h-10 w-10 flex-none items-center justify-center rounded-xl text-muted hover:bg-subtle hover:text-fg">
            <GearIcon />
          </button>
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1">
        {thread.messages === null ? (
          <main className="flex min-w-0 flex-1 items-center justify-center">
            <p role="status" className="text-sm text-muted">Loading chat…</p>
          </main>
        ) : (
          <ChatThread key={thread.key} conversationId={thread.id} initialMessages={thread.messages}
            sources={sources} selectedConnectors={selectedConnectors} onConnectorsChange={onConnectorsChange}
            onManageSources={() => setShowSources(true)} signingOut={signingOut} />
        )}
      </div>

      {showSources && <SourcesDialog sources={sources} selected={selectedConnectors} onChange={onConnectorsChange} onClose={() => setShowSources(false)} />}
      {showSettings && <SettingsDialog account={account} theme={theme} onTheme={onTheme}
        onSignOut={() => { setShowSettings(false); onSignOut(); }} signingOut={signingOut} onClose={() => setShowSettings(false)} />}
    </div>
  );
}

interface ChatThreadProps {
  conversationId: string | null;
  initialMessages: ChatItem[];
  sources: Sources;
  selectedConnectors: ConnectorProvider[];
  onConnectorsChange: (providers: ConnectorProvider[]) => void;
  onManageSources: () => void;
  signingOut: boolean;
}

/** One chat: its messages, calendar drafts and the message box. */
function ChatThread({ conversationId, initialMessages, sources, selectedConnectors, onConnectorsChange, onManageSources, signingOut }: ChatThreadProps) {
  const endOfMessagesRef = useRef<HTMLDivElement>(null);
  const [input, setInput] = useState("");
  const sourceNames = activeSources(sources, selectedConnectors);
  const timezone = effectiveTimezone(useSettings());

  // The server keeps the history; send only the new message and which chat it belongs to.
  const { messages, status, activity, error, send, stop, retry } = useChatStream(initialMessages, (message) => ({
    conversation_id: conversationId,
    message,
    connectors: selectedConnectors,
    timezone,
  }));

  const isLoading = status === "submitted" || status === "streaming";
  const last = messages[messages.length - 1];
  const waitingForText = isLoading && !(last?.role === "assistant" && last.content);

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status, activity]);

  const submit = (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    if (!input.trim() || isLoading || signingOut) return;
    void send(input);
    setInput("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <main className="flex min-w-0 flex-1 flex-col">
      <ReconnectBanner sources={sources} />
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
              <div key={message.id}
                className="max-w-[80%] self-end rounded-2xl bg-subtle px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap">
                {message.content}
              </div>
            ) : message.content ? (
              <article key={message.id} className="flex flex-col gap-3">
                <div className="markdown-content">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                </div>
                {message.sources.length > 0 && <SourceChips sources={message.sources} />}
              </article>
            ) : null,
          )}

          {waitingForText && (
            <p role="status" className="flex items-center gap-2 text-sm text-muted">
              <span className="h-3.5 w-3.5 flex-none animate-spin rounded-full border-2 border-line border-t-accent-fg" />
              {activity || "Thinking…"}
            </p>
          )}

          {error && (
            <div className="flex items-center justify-between gap-3 rounded-xl bg-error-soft px-4 py-3 text-sm text-error">
              <span>{error}</span>
              <Button variant="ghost" disabled={isLoading || signingOut} onClick={retry} className="text-error underline">
                Retry
              </Button>
            </div>
          )}

          <CalendarProposals refreshKey={`${status}:${messages.length}`} conversationId={conversationId} />
          <div ref={endOfMessagesRef} />
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-3xl flex-col gap-2.5 px-4 pb-4 sm:px-6 sm:pb-5">
        <form onSubmit={submit}
          className="flex flex-col gap-2 rounded-2xl border border-line bg-surface py-3 pr-3 pl-4 focus-within:border-muted">
          <textarea id="composer" aria-label="Message TrojanClaw" rows={2} value={input} disabled={signingOut}
            onChange={(event) => setInput(event.target.value)} onKeyDown={handleKeyDown}
            placeholder="Ask about deadlines, courses or campus…"
            className="w-full resize-none bg-transparent text-[15px] leading-normal text-fg outline-none placeholder:text-muted" />
          <div className="flex items-center gap-2">
            <SourcesMenu sources={sources} selected={selectedConnectors} onChange={onConnectorsChange} onManage={onManageSources} />
            <span className="min-w-0 flex-1 truncate text-xs text-muted">{sourceNames.join(", ") || "No sources on"}</span>
            {isLoading ? (
              <button type="button" onClick={stop} aria-label="Stop"
                className="flex h-11 w-11 flex-none items-center justify-center rounded-xl bg-subtle text-fg">
                <StopIcon />
              </button>
            ) : (
              <button type="submit" disabled={!input.trim() || signingOut} aria-label="Send"
                className="flex h-11 w-11 flex-none items-center justify-center rounded-xl bg-accent text-on-accent transition hover:brightness-110 disabled:opacity-40">
                <ArrowUpIcon />
              </button>
            )}
          </div>
        </form>
        <p className="text-center text-xs text-muted">
          Calendar additions require your approval. Double-check important dates in Brightspace.
        </p>
      </div>
    </main>
  );
}

/** Links the reply cited, or the sources it checked. */
function SourceChips({ sources }: { sources: Source[] }) {
  return (
    <ul aria-label="Sources" className="flex flex-wrap gap-2">
      {sources.map((source) => {
        const provider = source.provider in INFO ? INFO[source.provider as keyof typeof INFO].name : source.provider === "usc" ? "USC" : source.provider;
        const text = source.url ? `${provider} · ${source.label}` : `Checked ${source.label}`;
        const chip = "inline-flex max-w-full items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-muted";
        return (
          <li key={`${source.provider}-${source.url ?? source.label}`} className="max-w-full">
            {source.url ? (
              <a href={source.url} target="_blank" rel="noopener noreferrer" className={`${chip} hover:text-fg`}>
                <span className="max-w-[18rem] truncate">{text}</span>
                <ExternalIcon />
              </a>
            ) : <span className={chip}>{text}</span>}
          </li>
        );
      })}
    </ul>
  );
}

/** Shown when USC signs you out of a source, until you reconnect or dismiss it. */
function ReconnectBanner({ sources }: { sources: Sources }) {
  const [dismissed, setDismissed] = useState<ConnectorProvider[]>([]);
  const signedOut = USC.filter((provider) => sources.stateOf(provider) === "needs_login" && !dismissed.includes(provider));
  if (!signedOut.length) return null;
  const names = signedOut.map((provider) => INFO[provider].name).join(" and ");

  const reconnect = () => {
    signedOut.forEach((provider) => void sources.connect(provider, "connect"));
    toast("A sign-in window opened on this computer.");
  };

  return (
    <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-accent-soft px-4 py-2.5 text-sm">
      <span className="min-w-0 flex-1">{names} signed you out. Reconnect to use {signedOut.length > 1 ? "them" : "it"} in chat.</span>
      <button type="button" onClick={reconnect} disabled={signedOut.some((provider) => sources.busy.includes(provider))}
        className="min-h-9 font-medium text-accent-fg hover:underline disabled:opacity-50">Reconnect</button>
      <button type="button" aria-label="Dismiss" onClick={() => setDismissed([...dismissed, ...signedOut])}
        className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:text-fg"><CloseIcon /></button>
    </div>
  );
}
