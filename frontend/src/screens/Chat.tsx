import { isValidElement, useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  deleteConversation, getConversationMessages, listConversations, MAX_ATTACHMENTS, MAX_UPLOAD_BYTES, pinConversation,
  renameConversation, UPLOAD_ACCEPT, uploadFile, uploadUrl,
} from "../lib/api";
import type { AccountProfile, Attachment, ChatSaved, ConnectorProvider, ConversationSummary, Source } from "../lib/api";
import { speechSupported, useSpeech } from "../lib/speech";
import { fromSaved, useChatStream, type ChatItem } from "../lib/chatStream";
import { effectiveTimezone, readSettings, saveSettings, SIDEBAR, useSettings } from "../lib/settings";
import { activeSources, INFO, LIVE, useSources, type Sources } from "../lib/sources";
import { toast } from "../lib/toast";
import { CardBlock } from "../components/AnswerCards";
import { cardLinks } from "../lib/cards";
import { CalendarProposals } from "../components/Calendar";
import ChatList from "../components/ChatList";
import CampusWelcome from "../components/CampusWelcome";
import SettingsDialog from "../components/SettingsDialog";
import { SourcesDialog, SourcesMenu } from "../components/Sources";
import type { Theme } from "../theme";
import {
  ArrowUpIcon, Button, CloseIcon, ExternalIcon, FileIcon, GearIcon, Logo, MenuIcon, MicIcon, PaperclipIcon, PlusIcon,
  StopIcon, ThemeToggle,
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
  const [sidebarWidth, setSidebarWidth] = useState(() => readSettings().sidebarWidth);
  const asideRef = useRef<HTMLElement>(null);
  const clampWidth = (value: number) => Math.min(SIDEBAR.max, Math.max(SIDEBAR.min, Math.round(value)));
  const resizeTo = (value: number, save = false) => {
    const width = clampWidth(value);
    setSidebarWidth(width);
    if (save) saveSettings({ sidebarWidth: width });
  };
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
    <div className="workspace-shell flex h-dvh flex-col bg-bg text-fg md:flex-row">
      {/* Phones: a top bar opens the sidebar as a drawer. */}
      <header className="workspace-mobile-header flex h-14 flex-none items-center gap-2 border-b border-line bg-surface px-2 md:hidden">
        <button type="button" aria-label="Open menu" onClick={() => setDrawer(true)}
          className="flex h-11 w-11 items-center justify-center rounded-xl text-muted hover:text-fg"><MenuIcon /></button>
        <p className="min-w-0 flex-1 truncate text-center text-sm font-medium">{activeTitle}</p>
        <button type="button" aria-label="New chat" onClick={() => openChat(null)}
          className="flex h-11 w-11 items-center justify-center rounded-xl text-muted hover:text-fg"><PlusIcon /></button>
      </header>
      {drawer && <div className="fixed inset-0 z-30 bg-black/40 md:hidden" aria-hidden="true" onClick={() => setDrawer(false)} />}

      <aside ref={asideRef} aria-label="Sidebar" style={{ "--sidebar-w": `${sidebarWidth}px` } as React.CSSProperties}
        className={`workspace-sidebar fixed inset-y-0 left-0 z-40 flex w-72 max-w-[85vw] flex-col gap-2 border-r border-line bg-surface p-4 transition-transform md:relative md:z-auto md:w-[var(--sidebar-w)] md:max-w-none md:flex-none md:translate-x-0 ${drawer ? "translate-x-0" : "-translate-x-full"}`}>
        {/* Drag (or use the arrow keys) to resize; double-click to reset. */}
        <div role="separator" aria-orientation="vertical" aria-label="Resize sidebar" tabIndex={0}
          aria-valuemin={SIDEBAR.min} aria-valuemax={SIDEBAR.max} aria-valuenow={sidebarWidth}
          className="absolute inset-y-0 -right-1 z-10 hidden w-2 cursor-col-resize touch-none hover:bg-line focus-visible:bg-line md:block"
          onDoubleClick={() => resizeTo(SIDEBAR.default, true)}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
              event.preventDefault();
              resizeTo(sidebarWidth + (event.key === "ArrowRight" ? 16 : -16), true);
            }
          }}
          onPointerDown={(event) => {
            event.preventDefault();
            const left = asideRef.current?.getBoundingClientRect().left ?? 0;
            const handle = event.currentTarget;
            handle.setPointerCapture(event.pointerId);
            const move = (moveEvent: PointerEvent) => resizeTo(moveEvent.clientX - left);
            const up = (upEvent: PointerEvent) => {
              resizeTo(upEvent.clientX - left, true);
              handle.removeEventListener("pointermove", move);
              handle.removeEventListener("pointerup", up);
            };
            handle.addEventListener("pointermove", move);
            handle.addEventListener("pointerup", up);
          }} />
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
        <p className="sidebar-edition">A little more connected <span>USC</span></p>

        <Button variant="secondary" onClick={() => openChat(null)} className="sidebar-new-chat justify-start" title="New chat (⌘K)">
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
        <div className="sidebar-profile flex items-center gap-2 border-t border-line pt-4">
          <span className="profile-avatar" aria-hidden="true">{account.name.trim().slice(0, 1).toUpperCase()}</span>
          <div className="min-w-0 flex-1 text-sm"><p className="truncate font-medium">{account.name}</p><p className="truncate text-xs text-muted">@{account.username}</p></div>
          <button type="button" aria-label="Settings" title="Settings" onClick={() => { setDrawer(false); setShowSettings(true); }}
            className="flex h-10 w-10 flex-none items-center justify-center rounded-xl text-muted hover:bg-subtle hover:text-fg">
            <GearIcon />
          </button>
        </div>
      </aside>

      <div className="workspace-content flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="workspace-topbar hidden md:flex">
          <div className="workspace-breadcrumb"><span>Your workspace</span><span aria-hidden="true">/</span><span className="truncate">{activeTitle}</span></div>
          <button type="button" onClick={() => setShowSources(true)} className="workspace-source-status">
            <span className={sourceCount ? "source-dot is-ready" : "source-dot"} />{sourceCount ? `${sourceCount} source${sourceCount === 1 ? "" : "s"} ready` : "Connect your sources"}
          </button>
        </header>
        {thread.messages === null ? (
          <main className="flex min-w-0 flex-1 items-center justify-center">
            <p role="status" className="text-sm text-muted">Loading chat…</p>
          </main>
        ) : (
          <ChatThread key={thread.key} conversationId={thread.id} initialMessages={thread.messages}
            firstName={account.name.trim().split(/\s+/)[0] || ""}
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
  firstName: string;
  conversationId: string | null;
  initialMessages: ChatItem[];
  sources: Sources;
  selectedConnectors: ConnectorProvider[];
  onConnectorsChange: (providers: ConnectorProvider[]) => void;
  onManageSources: () => void;
  signingOut: boolean;
}

/** One chat: its messages, calendar drafts and the message box. */
function ChatThread({ firstName, conversationId, initialMessages, sources, selectedConnectors, onConnectorsChange, onManageSources, signingOut }: ChatThreadProps) {
  const endOfMessagesRef = useRef<HTMLDivElement>(null);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const sourceNames = activeSources(sources, selectedConnectors);
  const timezone = effectiveTimezone(useSettings());
  const speech = useSpeech(setInput, (message) => toast(message, "error"));

  // The server keeps the history; send only the new message, its files and which chat it belongs to.
  const { messages, status, activity, error, send, stop, retry } = useChatStream(initialMessages, (message, attachments) => ({
    conversation_id: conversationId,
    message,
    attachments: attachments.map((file) => file.id),
    connectors: selectedConnectors,
    timezone,
  }));

  // Upload each picked, dropped or pasted file straight away, so sending is instant.
  const addFiles = (picked: File[]) => {
    const room = MAX_ATTACHMENTS - files.length;
    if (picked.length > room) toast(`You can attach up to ${MAX_ATTACHMENTS} files to a message.`, "error");
    for (const file of picked.slice(0, Math.max(room, 0))) {
      const key = crypto.randomUUID();
      if (file.size > MAX_UPLOAD_BYTES) {
        toast(`"${file.name}" is larger than 5 MB.`, "error");
        continue;
      }
      setFiles((current) => [...current, { key, name: file.name, size: file.size, state: "uploading" }]);
      uploadFile(file)
        .then((attachment) => setFiles((current) => current.map((item) => item.key === key ? { ...item, state: "ready", attachment } : item)))
        .catch((reason: Error) => setFiles((current) => current.map((item) => item.key === key ? { ...item, state: "error", error: reason.message } : item)));
    }
  };
  const uploading = files.some((file) => file.state === "uploading");
  const ready = files.flatMap((file) => file.state === "ready" && file.attachment ? [file.attachment] : []);

  const isLoading = status === "submitted" || status === "streaming";
  const last = messages[messages.length - 1];
  const waitingForText = isLoading && !(last?.role === "assistant" && last.content);

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status, activity]);

  // Ready to type when a chat opens (not on phones, where it would pop up the keyboard).
  useEffect(() => {
    if (window.matchMedia("(min-width: 768px)").matches) focusComposer();
  }, []);

  const canSend = (input.trim() || ready.length > 0) && !uploading && !isLoading && !signingOut;
  const submit = (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    if (!canSend) return;
    if (speech.listening) speech.stop();
    void send(input.trim() || `Please look at the attached file${ready.length > 1 ? "s" : ""}.`, ready);
    setInput("");
    setFiles([]);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <main className={`chat-thread flex min-h-0 min-w-0 flex-1 flex-col ${messages.length ? "has-messages" : "is-empty"}`}>
      <ReconnectBanner sources={sources} />
      <div className="chat-scroll flex-1 overflow-y-auto">
        <div className="chat-messages mx-auto flex max-w-3xl flex-col gap-7 px-6 py-8">
          {messages.length === 0 && (
            <CampusWelcome firstName={firstName} timezone={timezone} disabled={signingOut} onPrompt={prompt => { setInput(prompt); focusComposer(); }} />
          )}

          {messages.map((message) =>
            message.role === "user" ? (
              <div key={message.id} className="flex max-w-[80%] flex-col items-end gap-1.5 self-end">
                {message.attachments.length > 0 && (
                  <ul aria-label="Attached files" className="flex flex-wrap justify-end gap-1.5">
                    {message.attachments.map((file) => (
                      <li key={file.id}>
                        <a href={uploadUrl(file.id)} download={file.name}
                          className="inline-flex max-w-[16rem] items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-muted hover:text-fg">
                          <FileIcon /><span className="truncate">{file.name}</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="user-message rounded-2xl bg-subtle px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap">{message.content}</div>
              </div>
            ) : message.content ? (
              <Answer key={message.id} content={message.content} sources={message.sources}
                final={!(isLoading && message === last)} />
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

      <div className="composer-dock mx-auto flex w-full max-w-3xl flex-col gap-2.5 px-4 pb-4 sm:px-6 sm:pb-5">
        <form onSubmit={submit}
          onDragOver={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); setDragging(true); } }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles([...event.dataTransfer.files]); }}
          className={`chat-composer flex flex-col gap-2 rounded-2xl border bg-surface py-3 pr-3 pl-4 focus-within:border-muted ${dragging ? "border-accent-fg border-dashed" : "border-line"}`}>
          {files.length > 0 && (
            <ul aria-label="Files to send" className="flex flex-wrap gap-2">
              {files.map((file) => (
                <li key={file.key} title={file.error}
                  className={`flex max-w-[15rem] items-center gap-2 rounded-xl border px-2.5 py-1.5 text-xs ${file.state === "error" ? "border-error/40 bg-error-soft text-error" : "border-line bg-subtle text-fg"}`}>
                  {file.state === "uploading"
                    ? <span className="h-3 w-3 flex-none animate-spin rounded-full border-2 border-line border-t-accent-fg" aria-label="Uploading" />
                    : <FileIcon />}
                  <span className="min-w-0 flex-1 truncate">{file.state === "error" ? `${file.name}: ${file.error}` : file.name}</span>
                  <span className="flex-none text-muted">{formatSize(file.size)}</span>
                  <button type="button" aria-label={`Remove ${file.name}`} onClick={() => setFiles((current) => current.filter((item) => item.key !== file.key))}
                    className="flex h-6 w-6 flex-none items-center justify-center rounded-md text-muted hover:text-fg"><CloseIcon /></button>
                </li>
              ))}
            </ul>
          )}
          <textarea id="composer" aria-label="Message TrojanClaw" rows={2} value={input} disabled={signingOut}
            onChange={(event) => setInput(event.target.value)} onKeyDown={handleKeyDown}
            onPaste={(event) => { const pasted = [...event.clipboardData.files]; if (pasted.length) { event.preventDefault(); addFiles(pasted); } }}
            placeholder={speech.listening ? "Listening… speak now" : "Ask about deadlines, courses or campus…"}
            className="w-full resize-none bg-transparent text-[15px] leading-normal text-fg outline-none placeholder:text-muted" />
          <div className="flex items-center gap-2">
            <input ref={fileInput} type="file" multiple accept={UPLOAD_ACCEPT} className="hidden"
              onChange={(event) => { addFiles([...(event.target.files ?? [])]); event.target.value = ""; }} />
            <button type="button" aria-label="Attach files" title="Attach files (PDF, images, text, Word, PowerPoint, Excel; up to 5 MB each)"
              onClick={() => fileInput.current?.click()} disabled={files.length >= MAX_ATTACHMENTS || signingOut}
              className="flex h-9 w-9 flex-none items-center justify-center rounded-full border border-line text-muted hover:text-fg disabled:opacity-40">
              <PaperclipIcon />
            </button>
            {speechSupported && (
              <button type="button" aria-label={speech.listening ? "Stop voice input" : "Voice input"} aria-pressed={speech.listening}
                title={speech.listening ? "Stop voice input" : "Speak your message"}
                onClick={() => speech.listening ? speech.stop() : speech.start(input)} disabled={signingOut}
                className={`flex h-9 w-9 flex-none items-center justify-center rounded-full border ${speech.listening ? "animate-pulse border-accent-fg bg-accent-soft text-accent-fg" : "border-line text-muted hover:text-fg"}`}>
                <MicIcon />
              </button>
            )}
            <SourcesMenu sources={sources} selected={selectedConnectors} onChange={onConnectorsChange} onManage={onManageSources} />
            <span className="min-w-0 flex-1 truncate text-xs text-muted">{sourceNames.join(", ") || "No sources on"}</span>
            {isLoading ? (
              <button type="button" onClick={stop} aria-label="Stop"
                className="flex h-11 w-11 flex-none items-center justify-center rounded-xl bg-subtle text-fg">
                <StopIcon />
              </button>
            ) : (
              <button type="submit" disabled={!canSend} aria-label={uploading ? "Wait for files to upload" : "Send"}
                className="flex h-11 w-11 flex-none items-center justify-center rounded-xl bg-accent text-on-accent transition hover:brightness-110 disabled:opacity-40">
                <ArrowUpIcon />
              </button>
            )}
          </div>
        </form>
        <p className="composer-note text-center text-xs text-muted">
          Calendar changes require your approval. Double-check important dates in Brightspace.
        </p>
      </div>
    </main>
  );
}

/** A file picked for the next message: uploading, ready (with its attachment), or failed. */
interface PendingFile { key: string; name: string; size: number; state: "uploading" | "ready" | "error"; attachment?: Attachment; error?: string }

function formatSize(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** An assistant reply: markdown with result cards, then where it came from. */
function Answer({ content, sources, final }: { content: string; sources: Source[]; final: boolean }) {
  // Cards already link to their pages; list only the other sources underneath.
  const carded = cardLinks(content);
  const rest = sources.filter((source) => !source.url || !carded.has(source.url));
  return (
    <article className="assistant-answer flex flex-col gap-3">
      <div className="markdown-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents(final)}>{content}</ReactMarkdown>
      </div>
      {final && rest.length > 0 && <SourceChips sources={rest} />}
    </article>
  );
}

const isCards = (className?: string) => !!className && /\blanguage-cards\b/.test(className);

/** ```cards blocks become result cards; links open in a new tab. */
function markdownComponents(final: boolean): Components {
  return {
    pre: ({ children }) => {
      const child = children as ReactNode;
      if (isValidElement<{ className?: string }>(child) && isCards(child.props.className)) return <>{child}</>;
      return <pre>{children}</pre>;
    },
    code: ({ className, children }) =>
      isCards(className)
        ? <CardBlock raw={String(children ?? "")} final={final} />
        : <code className={className}>{children}</code>,
    a: ({ href, title, children }) => {
      const external = !!href && /^https?:\/\//.test(href);
      return (
        <a href={href} title={title} {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>
          {children}
          {external && <span className="ml-0.5 inline-block align-[-1px] opacity-70"><ExternalIcon /></span>}
        </a>
      );
    },
  };
}

/** Where the reply came from: links it relied on, or the connectors it checked. */
function SourceChips({ sources }: { sources: Source[] }) {
  const chip = "inline-flex max-w-full items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-muted";
  return (
    <ul aria-label="Sources" className="flex flex-wrap gap-2">
      {sources.slice(0, 8).map((source) => {
        const text = source.course ? `${source.course} · ${source.title}` : source.title;
        return (
          <li key={`${source.title}-${source.url ?? ""}`} className="max-w-full">
            {source.url ? (
              <a href={source.url} target="_blank" rel="noopener noreferrer" className={`${chip} hover:text-fg`}>
                <span className="max-w-[18rem] truncate">{text}</span>
                <ExternalIcon />
              </a>
            ) : <span className={chip}><span className="max-w-[18rem] truncate">{text}</span></span>}
          </li>
        );
      })}
    </ul>
  );
}

/** Shown when USC signs you out of a source, until you reconnect or dismiss it. */
function ReconnectBanner({ sources }: { sources: Sources }) {
  const [dismissed, setDismissed] = useState<ConnectorProvider[]>([]);
  const signedOut = LIVE.filter((provider) => sources.stateOf(provider) === "needs_login" && !dismissed.includes(provider));
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
