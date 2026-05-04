"use client";

import * as React from "react";
import { MessageSquare, X } from "lucide-react";
import { ChatInterface } from "@/components/chat-interface";
import { cn } from "@/lib/utils";

/**
 * Floating chat widget. Renders a circular launcher in the bottom-right
 * corner that expands to a chat panel. Mobile-friendly: panel goes
 * full-screen on small viewports.
 *
 * Drop into any page with: <ChatWidget />
 */
export function ChatWidget() {
  const [open, setOpen] = React.useState(false);

  // Close on Escape
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      {/* Launcher (FAB) */}
      <button
        type="button"
        aria-label={open ? "Close chat" : "Open chat"}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "fixed bottom-5 right-5 z-50 size-14 rounded-full shadow-lg",
          "flex items-center justify-center",
          "bg-[var(--accent)] text-[var(--accent-foreground)]",
          "transition-transform hover:scale-105 active:scale-95",
          "focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[var(--accent)]/40"
        )}
      >
        {open ? <X className="size-6" /> : <MessageSquare className="size-6" />}
      </button>

      {/* Panel */}
      <div
        role="dialog"
        aria-modal={false}
        aria-label="QA Assistant"
        className={cn(
          "fixed z-40 bg-[var(--background)] border border-[var(--border)]",
          "shadow-2xl overflow-hidden",
          // mobile: nearly fullscreen
          "inset-3 sm:inset-auto",
          // desktop: roomier bottom-right panel; grows on larger viewports
          "sm:bottom-24 sm:right-5",
          "sm:w-[560px] md:w-[640px] lg:w-[720px]",
          "sm:h-[760px] sm:max-h-[calc(100vh-7rem)]",
          "sm:rounded-2xl rounded-2xl",
          "transition-all duration-200 origin-bottom-right",
          open
            ? "opacity-100 scale-100 pointer-events-auto"
            : "opacity-0 scale-95 pointer-events-none"
        )}
      >
        {/* Render the chat only while open so /api/chat doesn't fire on mount
            and the SSE/streaming logic can clean up between sessions. */}
        {open && (
          <ChatInterface variant="widget" onClose={() => setOpen(false)} />
        )}
      </div>
    </>
  );
}
