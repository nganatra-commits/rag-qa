"use client";

import * as React from "react";
import { MessageSquare, X } from "lucide-react";
import { ChatInterface, type Turn } from "@/components/chat-interface";
import { cn } from "@/lib/utils";

/**
 * Floating chat widget. Kept around for embedding the assistant on third-
 * party pages. The default app experience is the full-page layout in
 * src/app/page.tsx; this component is only mounted when imported explicitly.
 *
 * State (turns / doc filter) is persisted across open/close cycles so a
 * stray Esc keypress does not wipe the conversation.
 */
export function ChatWidget() {
  const [open, setOpen] = React.useState(false);
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [docFilter, setDocFilter] = React.useState<string[]>([]);

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

      <div
        role="dialog"
        aria-modal={false}
        aria-label="QA Assistant"
        className={cn(
          "fixed z-40 bg-[var(--background)] border border-[var(--border)]",
          "shadow-2xl overflow-hidden",
          "inset-3 sm:inset-auto",
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
        {open && (
          <ChatInterface
            variant="widget"
            onClose={() => setOpen(false)}
            turns={turns}
            setTurns={setTurns}
            docFilter={docFilter}
            setDocFilter={setDocFilter}
            onNewChat={() => {
              setTurns([]);
              setDocFilter([]);
            }}
          />
        )}
      </div>
    </>
  );
}
