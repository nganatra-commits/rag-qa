"use client";

import * as React from "react";
import { ChatInterface, type Turn } from "@/components/chat-interface";
import { ChatSidebar } from "@/components/chat-sidebar";
import {
  deleteChat as deleteChatStorage,
  deriveTitle,
  loadChats,
  upsertChat,
  type StoredChat,
} from "@/lib/chat-history";

/**
 * Full-page ChatGPT-like layout: persistent left sidebar with chat history,
 * main pane with the conversation. Default landing experience — no floating
 * widget. Chats are persisted to localStorage so closing the tab or hitting
 * Esc does not lose the conversation.
 */
export default function Page() {
  const [chats, setChats] = React.useState<StoredChat[]>([]);
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [docFilter, setDocFilter] = React.useState<string[]>([]);
  const [mobileSidebarOpen, setMobileSidebarOpen] = React.useState(false);
  const hydratedRef = React.useRef(false);

  // Load history once on mount and adopt the most-recent chat (if any) so
  // the user comes back to where they left off.
  React.useEffect(() => {
    const loaded = loadChats();
    setChats(loaded);
    if (loaded.length > 0) {
      const top = loaded[0];
      setActiveId(top.id);
      setTurns(top.turns);
      setDocFilter(top.docFilter ?? []);
    } else {
      setActiveId(crypto.randomUUID());
    }
    hydratedRef.current = true;
  }, []);

  // Persist on every meaningful change. We only write when there's at least
  // one turn so we don't fill history with empty placeholders.
  React.useEffect(() => {
    if (!hydratedRef.current) return;
    if (!activeId) return;
    if (turns.length === 0) return;
    const chat: StoredChat = {
      id: activeId,
      title: deriveTitle(turns),
      updatedAt: Date.now(),
      turns,
      docFilter,
    };
    setChats(upsertChat(chat));
  }, [turns, docFilter, activeId]);

  const handleNewChat = React.useCallback(() => {
    setActiveId(crypto.randomUUID());
    setTurns([]);
    setDocFilter([]);
  }, []);

  const handleSelectChat = React.useCallback(
    (id: string) => {
      const chat = chats.find((c) => c.id === id);
      if (!chat) return;
      setActiveId(chat.id);
      setTurns(chat.turns);
      setDocFilter(chat.docFilter ?? []);
    },
    [chats]
  );

  const handleDeleteChat = React.useCallback(
    (id: string) => {
      const next = deleteChatStorage(id);
      setChats(next);
      if (id === activeId) {
        if (next.length > 0) {
          const top = next[0];
          setActiveId(top.id);
          setTurns(top.turns);
          setDocFilter(top.docFilter ?? []);
        } else {
          handleNewChat();
        }
      }
    },
    [activeId, handleNewChat]
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
