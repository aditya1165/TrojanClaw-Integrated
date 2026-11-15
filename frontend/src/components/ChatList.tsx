import { useEffect, useRef, useState } from "react";
import type { ConversationSummary } from "../lib/api";
import { PinIcon, SearchIcon } from "./ui";

interface ChatListProps {
  conversations: ConversationSummary[] | null;
  search: string;
  onSearch: (value: string) => void;
  activeId: string | null;
  error: string;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onPin: (id: string, pinned: boolean) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

/** The sidebar's saved chats: search, then pinned chats, then the rest, newest first. */
export default function ChatList({ conversations, search, onSearch, activeId, error, onOpen, onRename, onPin, onDelete }: ChatListProps) {
  const pinned = search ? [] : conversations?.filter((chat) => chat.pinned) ?? [];
  const rest = search ? conversations ?? [] : conversations?.filter((chat) => !chat.pinned) ?? [];
  const item = (chat: ConversationSummary) => (
    <ChatItem key={chat.id} chat={chat} active={chat.id === activeId}
      onOpen={() => onOpen(chat.id)}
      onRename={(title) => onRename(chat.id, title)}
      onPin={() => onPin(chat.id, !chat.pinned)}
      onDelete={() => onDelete(chat.id)} />
  );

  return (
    <nav aria-label="Chats" className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
      <label className="mx-1 mt-3 mb-1 flex min-h-10 items-center gap-2 rounded-lg border border-line bg-bg px-2.5 text-muted focus-within:border-muted">
        <SearchIcon />
        <input id="chat-search" type="search" value={search} onChange={(event) => onSearch(event.target.value)}
          placeholder="Search chats" aria-label="Search chats" maxLength={100}
          className="min-w-0 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-muted" />
      </label>
      {error ? <p role="alert" className="px-3 text-xs text-error">{error}</p>
        : conversations === null ? <p className="px-3 text-xs text-muted">Loading…</p>
        : search && rest.length === 0 ? <p className="px-3 text-xs text-muted">No chats match “{search}”.</p>
        : conversations.length === 0 ? <p className="px-3 text-xs text-muted">Your chats will show up here.</p>
        : <>
          {pinned.length > 0 && <p className="px-3 pt-2 pb-1 text-xs font-medium text-muted">Pinned</p>}
          {pinned.map(item)}
          {rest.length > 0 && <p className="px-3 pt-2 pb-1 text-xs font-medium text-muted">{search ? "Results" : "Recent"}</p>}
          {rest.map(item)}
        </>}
    </nav>
  );
}

function ChatItem({ chat, active, onOpen, onRename, onPin, onDelete }: {
  chat: ConversationSummary;
  active: boolean;
  onOpen: () => void;
  onRename: (title: string) => Promise<void>;
  onPin: () => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const title = chat.title || "New chat";

  useEffect(() => {
    if (!menu) return;
    const outside = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) setMenu(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, [menu]);

  const run = async (task: () => Promise<void>) => {
    setBusy(true);
    try { await task(); } finally { setBusy(false); }
  };

  const save = (value: string) => {
    setEditing(false);
    const next = value.trim();
    if (next && next !== chat.title) void run(() => onRename(next));
  };

  const remove = () => {
    setMenu(false);
    if (window.confirm(`Delete "${title}"? This can't be undone.`)) void run(onDelete);
  };

  if (editing) {
    return (
      <input autoFocus defaultValue={chat.title ?? ""} maxLength={120} aria-label="Chat name"
        className="mx-1 min-h-10 rounded-lg border border-line bg-bg px-2 text-sm text-fg outline-none focus:border-muted"
        onBlur={(event) => save(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") setEditing(false);
        }} />
    );
  }

  return (
    <div ref={ref} className={`group relative flex items-center rounded-lg ${active ? "bg-subtle" : "hover:bg-subtle"}`}>
      <button type="button" onClick={onOpen} disabled={busy} aria-current={active ? "page" : undefined}
        className={`flex min-h-10 min-w-0 flex-1 items-center gap-1.5 px-3 text-left text-sm ${active ? "font-medium text-fg" : "text-muted group-hover:text-fg"}`}>
        {chat.pinned && <span className="flex-none text-muted" aria-label="Pinned"><PinIcon /></span>}
        <span className="truncate">{title}</span>
      </button>
      <button type="button" aria-label={`Options for ${title}`} aria-expanded={menu} aria-haspopup="true"
        onClick={() => setMenu(!menu)}
        className={`mr-1 flex h-8 w-8 flex-none items-center justify-center rounded-md text-muted hover:text-fg focus:opacity-100 ${menu || active ? "opacity-100" : "opacity-100 md:opacity-0 md:group-hover:opacity-100"}`}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" />
        </svg>
      </button>
      {menu && (
        <div role="menu" className="absolute top-full right-1 z-30 mt-1 flex w-36 flex-col rounded-xl border border-line bg-surface p-1 shadow-lg">
          <button type="button" role="menuitem" onClick={() => { setMenu(false); void run(onPin); }}
            className="min-h-9 rounded-lg px-3 text-left text-sm hover:bg-subtle">{chat.pinned ? "Unpin" : "Pin"}</button>
          <button type="button" role="menuitem" onClick={() => { setMenu(false); setEditing(true); }}
            className="min-h-9 rounded-lg px-3 text-left text-sm hover:bg-subtle">Rename</button>
          <button type="button" role="menuitem" onClick={remove}
            className="min-h-9 rounded-lg px-3 text-left text-sm text-error hover:bg-error-soft">Delete</button>
        </div>
      )}
    </div>
  );
}
