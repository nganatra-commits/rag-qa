"use client";

import * as React from "react";
import { MessageSquarePlus, Trash2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { StoredChat } from "@/lib/chat-history";

interface ChatSidebarProps {
  chats: StoredChat[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  /** Mobile: rendered as an overlay drawer when open. */
  mobileOpen: boolean;
  onMobileClose: () => void;
}

export function ChatSidebar({
  chats,
  activeId,
  onSelect,
  onNew,
  onDelete,
  mobileOpen,
  onMobileClose,
}: ChatSidebarProps) {
  return (
    <>
      {/* Mobile backdrop */}
      <div
        className={cn(
          "fixed inset-0 z-30 bg-black/40 md:hidden transition-opacity",
          mobileOpen ? "opacity-100" : "opacity-0 pointer-events-none"
        )}
        onClick={onMobileClose}
      />
      <aside
        className={cn(
          "z-40 flex flex-col bg-[var(--muted)] border-r border-[var(--border)]",
          // Desktop: persistent left rail
          "md:static md:translate-x-0 md:w-72 md:shrink-0",
          // Mobile: slide-in drawer
          "fixed top-0 left-0 h-dvh w-72 transition-transform",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        )}
        aria-label="Chat history"
      >
        <div className="flex items-center justify-between gap-2 px-3 py-3 border-b border-[var(--border)]">
          <button
            type="button"
            onClick={() => {
              onNew();
              onMobileClose();
            }}
            className="flex-1 inline-flex items-center justify-center gap-2 rounded-md border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm font-medium hover:bg-[var(--background)]/80 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <MessageSquarePlus className="size-4" />
            New chat
          </button>
          <button
            type="button"
            onClick={onMobileClose}
            className="md:hidden p-2 rounded hover:bg-[var(--background)]"
            aria-label="Close history"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-2">
          <h2 className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-foreground)]">
            Recent chats
          </h2>
          {chats.length === 0 ? (
            <p className="px-2 py-3 text-xs text-[var(--muted-foreground)] italic">
              No chats yet — ask something to start.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {chats.map((c) => (
                <li key={c.id}>
                  <div
                    className={cn(
                      "group flex items-center gap-1 rounded-md transition-colors",
                      activeId === c.id
                        ? "bg-[var(--background)]"
                        : "hover:bg-[var(--background)]/60"
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        onSelect(c.id);
                        onMobileClose();
                      }}
                      className="flex-1 min-w-0 text-left px-2.5 py-2 text-xs"
                      title={c.title}
                    >
                      <span className="block truncate">{c.title}</span>
                      <span className="block text-[10px] text-[var(--muted-foreground)] mt-0.5">
                        {formatRelative(c.updatedAt)}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(c.id);
                      }}
                      className="opacity-0 group-hover:opacity-100 focus:opacity-100 p-1.5 mr-1 rounded text-[var(--muted-foreground)] hover:text-red-500 transition-opacity"
                      aria-label="Delete chat"
                      title="Delete chat"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <footer className="px-3 py-2.5 border-t border-[var(--border)] text-[10px] text-[var(--muted-foreground)]">
          Chats are saved to the server. Open any with its URL.
        </footer>
      </aside>
    </>
  );
}

function formatRelative(ts: number): string {
  const ms = Date.now() - ts;
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return new Date(ts).toLocaleDateString();
}
