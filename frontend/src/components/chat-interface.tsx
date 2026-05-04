"use client";

import * as React from "react";
import { Send, Loader2, Bot, User, Image as ImageIcon, ImageOff, RotateCcw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Toggle } from "@/components/ui/toggle";
import { AssistantMessage } from "@/components/message";
import { Citations } from "@/components/citation";
import { cn, formatLatency, formatTokens } from "@/lib/utils";
import type { AnswerResponse } from "@/types/api";

type Turn =
  | { role: "user"; content: string; id: string }
  | { role: "assistant"; data: AnswerResponse; imagesEnabled: boolean; id: string }
  | { role: "error"; message: string; id: string };

const DOCS = [
  { id: "qasetup", label: "Install" },
  { id: "qatutor", label: "Tutorial" },
  { id: "qaman", label: "Manual" },
];

const IMAGES_PREF_KEY = "ragqa.imagesEnabled";

interface ChatInterfaceProps {
  /** When true, render in widget-friendly mode (no full viewport height). */
  variant?: "page" | "widget";
  onClose?: () => void;
}

export function ChatInterface({ variant = "page", onClose }: ChatInterfaceProps) {
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [input, setInput] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [docFilter, setDocFilter] = React.useState<string[]>([]);
  const [imagesEnabled, setImagesEnabled] = React.useState<boolean>(true);
  const scrollRef = React.useRef<HTMLDivElement>(null);

  // Load image preference from localStorage on mount
  React.useEffect(() => {
    try {
      const v = window.localStorage.getItem(IMAGES_PREF_KEY);
      if (v !== null) setImagesEnabled(v === "true");
    } catch {}
  }, []);

  React.useEffect(() => {
    try {
      window.localStorage.setItem(IMAGES_PREF_KEY, String(imagesEnabled));
    } catch {}
  }, [imagesEnabled]);

  React.useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [turns]);

  const send = async () => {
    const q = input.trim();
    if (!q || busy) return;
    const userTurn: Turn = { role: "user", content: q, id: crypto.randomUUID() };
    setTurns((t) => [...t, userTurn]);
    setInput("");
    setBusy(true);
    const turnImagesEnabled = imagesEnabled;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: q,
          doc_filter: docFilter.length ? docFilter : undefined,
          // When images are off, don't send any to the LLM (saves tokens) AND
          // hide on render. We only need the chunk text.
          max_images: turnImagesEnabled ? undefined : 0,
        }),
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err);
      }
      const data = (await res.json()) as AnswerResponse;
      setTurns((t) => [
        ...t,
        { role: "assistant", data, imagesEnabled: turnImagesEnabled, id: crypto.randomUUID() },
      ]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "request failed";
      setTurns((t) => [
        ...t,
        { role: "error", message, id: crypto.randomUUID() },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const toggleDoc = (id: string) => {
    setDocFilter((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const isWidget = variant === "widget";

  return (
    <div className={cn("flex flex-col", isWidget ? "h-full" : "h-dvh")}>
      <header
        className={cn(
          "border-b border-[var(--border)] px-4 py-2.5 flex items-center justify-between gap-3 bg-[var(--background)]",
          !isWidget && "px-6 py-3"
        )}
      >
        <div className="min-w-0">
          <h1 className="font-semibold text-sm truncate">NWA QA Assistant</h1>
          {!isWidget && (
            <p className="text-xs text-[var(--muted-foreground)]">
              Image-friendly RAG · grounded in the QA docs
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Toggle
            checked={imagesEnabled}
            onCheckedChange={setImagesEnabled}
            ariaLabel="Show images in answers"
          />
          <span className="text-xs text-[var(--muted-foreground)] hidden sm:inline-flex items-center gap-1">
            {imagesEnabled ? <ImageIcon className="size-3.5" /> : <ImageOff className="size-3.5" />}
            {imagesEnabled ? "Images" : "Text only"}
          </span>
          <Button
            size="icon"
            variant="ghost"
            onClick={() => {
              setTurns([]);
              setInput("");
              setDocFilter([]);
            }}
            aria-label="New chat"
            title="New chat"
            disabled={busy || (turns.length === 0 && input === "")}
            className="h-8 w-8"
          >
            <RotateCcw className="size-4" />
          </Button>
          {isWidget && onClose && (
            <Button
              size="icon"
              variant="ghost"
              onClick={onClose}
              aria-label="Close chat"
              title="Close (Esc)"
              className="h-8 w-8"
            >
              <X className="size-4" />
            </Button>
          )}
        </div>
      </header>

      <div className={cn(
        "border-b border-[var(--border)] px-4 py-2 flex gap-1.5 flex-wrap",
        !isWidget && "px-6"
      )}>
        {DOCS.map((d) => (
          <Button
            key={d.id}
            size="sm"
            variant={docFilter.includes(d.id) ? "default" : "outline"}
            onClick={() => toggleDoc(d.id)}
            className="h-7 px-2.5 text-[11px]"
          >
            {d.label}
          </Button>
        ))}
        {docFilter.length > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setDocFilter([])}
            className="h-7 px-2 text-[11px] text-[var(--muted-foreground)]">
            Clear
          </Button>
        )}
      </div>

      <div ref={scrollRef} className={cn(
        "flex-1 overflow-y-auto",
        isWidget ? "px-3 py-3" : "px-6 py-6"
      )}>
        <div className={cn("space-y-5", !isWidget && "max-w-3xl mx-auto space-y-6")}>
          {turns.length === 0 && <EmptyState />}
          {turns.map((t) => (
            <TurnView key={t.id} turn={t} />
          ))}
          {busy && (
            <div className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
              <Loader2 className="size-4 animate-spin" />
              retrieving…
            </div>
          )}
        </div>
      </div>

      <footer className={cn(
        "border-t border-[var(--border)] bg-[var(--background)]",
        isWidget ? "px-3 py-2.5" : "px-6 py-4"
      )}>
        <div className={cn("flex gap-2", !isWidget && "max-w-3xl mx-auto")}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Ask about the QA docs…"
            rows={isWidget ? 1 : 2}
            disabled={busy}
            className={cn(
              "flex-1 resize-none rounded-md border border-[var(--border)] bg-[var(--background)]",
              "px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            )}
          />
          <Button onClick={() => void send()} disabled={busy || !input.trim()}>
            <Send className="size-4" />
          </Button>
        </div>
      </footer>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  if (turn.role === "user") {
    return (
      <div className="flex gap-2.5">
        <Avatar role="user" />
        <div className="rounded-lg bg-[var(--muted)] px-3 py-2 text-sm">
          {turn.content}
        </div>
      </div>
    );
  }
  if (turn.role === "error") {
    return (
      <div className="flex gap-2.5">
        <Avatar role="assistant" />
        <div className="rounded-lg border border-red-300 bg-red-50 dark:bg-red-950 dark:border-red-900 px-3 py-2 text-sm">
          <p className="font-medium">Something went wrong.</p>
          <p className="text-xs mt-1 opacity-80">{turn.message}</p>
        </div>
      </div>
    );
  }
  const { data, imagesEnabled } = turn;
  return (
    <div className="flex gap-2.5">
      <Avatar role="assistant" />
      <div className="flex-1 rounded-lg bg-[var(--muted)] px-3 py-2.5">
        <AssistantMessage
          text={data.answer}
          images={imagesEnabled ? data.images : []}
          referencedImageIds={data.referenced_image_ids}
          isRefusal={data.is_refusal}
          imagesEnabled={imagesEnabled}
        />
        {!data.is_refusal && (
          <Citations citations={data.citations} answerText={data.answer} />
        )}
        <div className="mt-3 flex items-center gap-3 text-[10px] text-[var(--muted-foreground)] flex-wrap">
          <span>{formatLatency(data.latency_ms)}</span>
          <span>·</span>
          <span>
            {formatTokens(data.input_tokens)} in / {formatTokens(data.output_tokens)} out
          </span>
          {imagesEnabled && (
            <>
              <span>·</span>
              <span>{data.images.length} image{data.images.length !== 1 ? "s" : ""}</span>
            </>
          )}
          {!imagesEnabled && (
            <>
              <span>·</span>
              <span className="italic">images suppressed</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Avatar({ role }: { role: "user" | "assistant" }) {
  return (
    <div className="size-7 shrink-0 rounded-full border border-[var(--border)] flex items-center justify-center bg-[var(--background)]">
      {role === "user" ? (
        <User className="size-3.5 text-[var(--muted-foreground)]" />
      ) : (
        <Bot className="size-3.5 text-[var(--accent)]" />
      )}
    </div>
  );
}

function EmptyState() {
  const examples = [
    "How do I install QA on Windows?",
    "Walk me through Tutorial Exercise 1.",
    "Explain Creating and Editing Data Sets.",
  ];
  return (
    <div className="text-center py-10">
      <Bot className="size-8 mx-auto text-[var(--muted-foreground)] mb-2" />
      <p className="text-sm text-[var(--muted-foreground)] mb-4">
        Ask about anything in the QA manuals.
      </p>
      <ul className="text-xs space-y-1 text-[var(--muted-foreground)]">
        {examples.map((e) => (
          <li key={e} className="italic">"{e}"</li>
        ))}
      </ul>
    </div>
  );
}
