"use client";

import * as React from "react";
import { ChatInterface, type Turn } from "@/components/chat-interface";
import { ChatSidebar } from "@/components/chat-sidebar";
import {
  deleteChat as deleteChatLocal,
  deriveTitle,
  loadChats as loadChatsLocal,
  upsertChat as upsertChatLocal,
  type StoredChat,
} from "@/lib/chat-history";
import { backend } from "@/lib/api";

/**
 * Full-page ChatGPT-like layout: persistent left sidebar with the entire
 * chat history (server-backed by DynamoDB via /api/chats), main pane with
 * the conversation. Each chat has a shareable URL (?chat=<id>) so any
 * conversation can be re-opened later, on any device, by hitting the link.
 *
 * Persistence strategy:
 *   - Writes go to the server (PUT /api/chats/<id>) and a localStorage
 *     mirror for offline/resilience.
 *   - On load we read from the server; if it fails we fall back to local.
 *   - Sidebar shows ALL chats stored on the server (capped via the API's
 *     own limit setting).
 */
export default function Page() {
  const [chats, setChats] = React.useState<StoredChat[]>([]);
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [docFilter, setDocFilter] = React.useState<string[]>([]);
  const [mobileSidebarOpen, setMobileSidebarOpen] = React.useState(false);
  const hydratedRef = React.useRef(false);
  // We avoid fighting the user's edits with a remote refresh while they're
  // typing into a chat — only resync the sidebar list, never the active turns.

  // ---- Initial load: read sidebar list + active chat (URL or most recent).
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1. Sidebar list (server first, fall back to local).
      let list: StoredChat[] = [];
      try {
        const remote = await backend.listChats();
        list = remote.map((r) => ({
          id: r.id,
          title: r.title,
          updatedAt: r.updated_at,
          turns: [],
          docFilter: r.doc_filter,
        }));
      } catch {
        list = loadChatsLocal();
      }
      if (cancelled) return;
      setChats(list);

      // 2. Active chat: URL first, then most recent, else fresh.
      const urlId =
        typeof window !== "undefined"
          ? new URLSearchParams(window.location.search).get("chat")
          : null;
      const wantedId = urlId || list[0]?.id || null;
      if (wantedId) {
        try {
          const remote = await backend.getChat(wantedId);
          if (cancelled) return;
          setActiveId(remote.id);
          setTurns(remote.turns as Turn[]);
          setDocFilter(remote.doc_filter ?? []);
          syncUrl(remote.id);
        } catch {
          const local = loadChatsLocal().find((c) => c.id === wantedId);
          if (cancelled) return;
          if (local) {
            setActiveId(local.id);
            setTurns(local.turns);
            setDocFilter(local.docFilter ?? []);
            syncUrl(local.id);
          } else {
            const fresh = crypto.randomUUID();
            setActiveId(fresh);
            syncUrl(fresh);
          }
        }
      } else {
        const fresh = crypto.randomUUID();
        setActiveId(fresh);
        syncUrl(fresh);
      }
      hydratedRef.current = true;
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- Persist on every meaningful change ----------------------------------
  // Mirror to localStorage immediately; debounce the server write so rapid
  // turns/edits don't cause a write per keystroke.
  const lastSyncedRef = React.useRef<number>(0);
  React.useEffect(() => {
    if (!hydratedRef.current) return;
    if (!activeId) return;
    if (turns.length === 0) return;

    const title = deriveTitle(turns);
    const chat: StoredChat = {
      id: activeId,
      title,
      updatedAt: Date.now(),
      turns,
      docFilter,
    };

    // local mirror is cheap and synchronous
    setChats(upsertChatLocal(chat));

    // remote write — fire-and-forget; debounce by activeId+content fingerprint
    const fingerprint = turns.length;
    const now = Date.now();
    lastSyncedRef.current = now;
    const debounce = window.setTimeout(() => {
      // skip if a newer write has been queued
      if (lastSyncedRef.current !== now) return;
      backend
        .upsertChat(activeId, {
          title,
          turns,
          doc_filter: docFilter,
        })
        .then((rec) => {
          // Reflect the server's view in the sidebar (server is authoritative
          // for updated_at). Don't touch turns — user may have typed more.
          setChats((prev) => {
            const without = prev.filter((c) => c.id !== rec.id);
            return [
              {
                id: rec.id,
                title: rec.title,
                updatedAt: rec.updated_at,
                turns: [],
                docFilter: rec.doc_filter,
              },
              ...without,
            ];
          });
        })
        .catch((err) => {
          console.warn("upsertChat failed (kept local):", err, fingerprint);
        });
    }, 600);
    return () => window.clearTimeout(debounce);
  }, [turns, docFilter, activeId]);

  const handleNewChat = React.useCallback(() => {
    const fresh = crypto.randomUUID();
    setActiveId(fresh);
    setTurns([]);
    setDocFilter([]);
    syncUrl(fresh);
  }, []);

  const handleSelectChat = React.useCallback(async (id: string) => {
    // Optimistically update the URL & active id; load body from server.
    setActiveId(id);
    syncUrl(id);
    try {
      const remote = await backend.getChat(id);
      setTurns(remote.turns as Turn[]);
      setDocFilter(remote.doc_filter ?? []);
    } catch {
      const local = loadChatsLocal().find((c) => c.id === id);
      if (local) {
        setTurns(local.turns);
        setDocFilter(local.docFilter ?? []);
      } else {
        setTurns([]);
        setDocFilter([]);
      }
    }
  }, []);

  const handleDeleteChat = React.useCallback(
    async (id: string) => {
      const next = deleteChatLocal(id);
      setChats(next);
      try {
        await backend.deleteChat(id);
      } catch (err) {
        console.warn("deleteChat (remote) failed:", err);
      }
      if (id === activeId) {
        if (next.length > 0) {
          handleSelectChat(next[0].id);
        } else {
          handleNewChat();
        }
      }
    },
    [activeId, handleNewChat, handleSelectChat]
  );

  return (
    <div className="flex h-dvh">
      <ChatSidebar
        chats={chats}
        activeId={activeId}
        onSelect={handleSelectChat}
        onNew={handleNewChat}
        onDelete={handleDeleteChat}
        mobileOpen={mobileSidebarOpen}
        onMobileClose={() => setMobileSidebarOpen(false)}
      />
      <div className="flex-1 min-w-0 flex flex-col">
        <ChatInterface
          variant="page"
          turns={turns}
          setTurns={setTurns}
          docFilter={docFilter}
          setDocFilter={setDocFilter}
          onNewChat={handleNewChat}
          onToggleSidebar={() => setMobileSidebarOpen((v) => !v)}
        />
      </div>
    </div>
  );
}

/** Reflect the active chat id into the URL without navigation. */
function syncUrl(chatId: string) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (url.searchParams.get("chat") === chatId) return;
  url.searchParams.set("chat", chatId);
  window.history.replaceState(null, "", url.toString());
}
