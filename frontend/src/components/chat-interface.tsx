"use client";

import * as React from "react";
import { Send, Loader2, Bot, User, Image as ImageIcon, ImageOff, X, Menu, BookOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Toggle } from "@/components/ui/toggle";
import { AssistantMessage } from "@/components/message";
import { Citations } from "@/components/citation";
import { QuestionLibrary } from "@/components/question-library";
import { cn, formatLatency, formatTokens, randomId } from "@/lib/utils";
import { backend } from "@/lib/api";
import type { AnswerResponse, BuildInfo, HistoryTurn } from "@/types/api";
import type { StoredTurn } from "@/lib/chat-history";
import {
  buildQueryWithContext,
  installEmbedListener,
  postToParent,
  stripQueryFromUrl,
} from "@/lib/embed";

/** Max prior turns to include in the LLM context. Each is a few hundred
 * tokens at worst, so 8 keeps follow-up coherence without blowing tokens. */
const HISTORY_TURN_LIMIT = 8;
/** Trim past assistant answers to this many chars to keep input cheap. */
const HISTORY_CHAR_BUDGET = 1500;

export type Turn = StoredTurn;

const DOCS = [
  { id: "qasetup", label: "Install" },
  { id: "qatutor", label: "Tutorial" },
  { id: "qaman", label: "Manual" },
];

const IMAGES_PREF_KEY = "ragqa.imagesEnabled";

interface ChatInterfaceProps {
  /** Page = full-viewport ChatGPT-like layout. Widget = floating panel. */
  variant?: "page" | "widget";
  onClose?: () => void;
  /** Optional sidebar toggle (page variant only). */
  onToggleSidebar?: () => void;
  /** Lifted state so the parent can persist it to history. */
  turns: Turn[];
  setTurns: React.Dispatch<React.SetStateAction<Turn[]>>;
  docFilter: string[];
  setDocFilter: React.Dispatch<React.SetStateAction<string[]>>;
  /** Called when the user clicks "New chat". */
  onNewChat?: () => void;
  /** Embed: pre-filled question from ?q=. Auto-submitted unless autoSubmit=false. */
  initialQuery?: string | null;
  /** Embed: structured key/value context passed from the host. Prepended to each
   *  outgoing query so the LLM sees it; the user turn UI shows the raw text. */
  initialContext?: Record<string, string>;
  /** Embed: when initialQuery is set, fire send() automatically. */
  autoSubmit?: boolean;
}

export function ChatInterface({
  variant = "page",
  onClose,
  onToggleSidebar,
  turns,
  setTurns,
  docFilter,
  setDocFilter,
  onNewChat,
  initialQuery = null,
  initialContext,
  autoSubmit = true,
}: ChatInterfaceProps) {
  const [input, setInput] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [imagesEnabled, setImagesEnabled] = React.useState<boolean>(true);
  const [build, setBuild] = React.useState<BuildInfo | null>(null);
  const [libOpen, setLibOpen] = React.useState(false);
  const [hostContext, setHostContext] = React.useState<Record<string, string>>(
    initialContext ?? {}
  );
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const didAutoSubmitRef = React.useRef(false);

  React.useEffect(() => {
    let cancelled = false;
    backend.version()
      .then((b) => { if (!cancelled) setBuild(b); })
      .catch(() => { /* /version unavailable; just hide the pill */ });
    return () => { cancelled = true; };
  }, []);

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

  const sendWithText = async (rawText: string) => {
    const q = rawText.trim();
    if (!q || busy) return;
    // Display the raw question in the UI; only the LLM sees the
    // host-context-prefixed version. Subsequent turns' history stays clean.
    const composed = buildQueryWithContext(q, hostContext);
    const userTurn: Turn = { role: "user", content: q, id: randomId() };
    setTurns((t) => [...t, userTurn]);
    setInput("");
    setBusy(true);
    const turnImagesEnabled = imagesEnabled;

    try {
      const data = await backend.answer({
        query: composed,
        doc_filter: docFilter.length ? docFilter : undefined,
        // When images are off, don't send any to the LLM (saves tokens) AND
        // hide on render. We only need the chunk text.
        max_images: turnImagesEnabled ? undefined : 0,
        history: buildHistory(turns),
      });
      setTurns((t) => [
        ...t,
        { role: "assistant", data, imagesEnabled: turnImagesEnabled, id: randomId() },
      ]);
      postToParent({ type: "ANSWER", text: data.answer });
    } catch (err) {
      const message = err instanceof Error ? err.message : "request failed";
      setTurns((t) => [
        ...t,
        { role: "error", message, id: randomId() },
      ]);
      postToParent({ type: "ERROR", message });
    } finally {
      setBusy(false);
    }
  };

  const send = () => sendWithText(input);

  // Latest sendWithText for the embed listener, which is installed once and
  // would otherwise capture a stale closure.
  const sendRef = React.useRef(sendWithText);
  sendRef.current = sendWithText;

  React.useEffect(() => {
    const teardown = installEmbedListener((msg) => {
      if (msg.type === "SET_CONTEXT") {
        setHostContext(msg.context);
      } else if (msg.type === "ASK") {
        void sendRef.current(msg.query);
      }
    });
    postToParent({ type: "READY" });
    return teardown;
  }, []);

  React.useEffect(() => {
    if (didAutoSubmitRef.current) return;
    if (!initialQuery) return;
    didAutoSubmitRef.current = true;
    setInput(initialQuery);
    if (autoSubmit) {
      stripQueryFromUrl();
      void sendRef.current(initialQuery);
    }
  }, [initialQuery, autoSubmit]);

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
          "bg-header-gradient text-on-gradient",
          "px-4 py-2.5 flex items-center justify-between gap-3",
          !isWidget && "px-4 sm:px-6 py-3"
        )}
      >
        <div className="flex items-center gap-2 min-w-0">
          {!isWidget && onToggleSidebar && (
            <Button
              size="icon"
              variant="ghost"
              onClick={onToggleSidebar}
              aria-label="Toggle chat history"
              title="Chat history"
              className="h-8 w-8 md:hidden text-white hover:bg-white/10"
            >
              <Menu className="size-4" />
            </Button>
          )}
          <div className="min-w-0">
            <h1 className="font-semibold text-sm truncate text-white">NWA QA Assistant</h1>
            {!isWidget && (
              <p className="text-xs text-white/70 hidden sm:block">
                Image-friendly RAG · grounded in the QA docs
              </p>
            )}
          </div>
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
          {onNewChat && (
            <Button
              size="sm"
              variant="outline"
              onClick={onNewChat}
              aria-label="New chat"
              title="New chat"
              disabled={busy}
              className="h-8"
            >
              New chat
            </Button>
          )}
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
        !isWidget && "px-4 sm:px-6"
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

      {docFilter.length > 0 && (() => {
        const selectedLabels = docFilter
          .map((id) => DOCS.find((d) => d.id === id)?.label ?? id)
          .join(", ");
        const excluded = DOCS
          .filter((d) => !docFilter.includes(d.id))
          .map((d) => d.label);
        return (
          <div
            role="status"
            className={cn(
              "border-b border-amber-300 dark:border-amber-700/60 bg-amber-50 dark:bg-amber-950/40",
              "px-4 py-2 flex items-center gap-3 text-xs",
              !isWidget && "px-4 sm:px-6"
            )}
          >
            <span className="font-medium text-amber-900 dark:text-amber-200">
              ⚠ Search is restricted to: {selectedLabels}
            </span>
            {excluded.length > 0 && (
              <span className="text-amber-800/80 dark:text-amber-300/80">
                · {excluded.join(" + ")} excluded
              </span>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setDocFilter([])}
              className="h-6 px-2 text-[11px] ml-auto border-amber-400 text-amber-900 hover:bg-amber-100 dark:text-amber-100 dark:hover:bg-amber-900/30"
            >
              Search all docs
            </Button>
          </div>
        );
      })()}

      <div ref={scrollRef} className={cn(
        "flex-1 overflow-y-auto",
        isWidget ? "px-3 py-3" : "px-4 sm:px-6 py-6"
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
        isWidget ? "px-3 py-2.5" : "px-4 sm:px-6 py-4"
      )}>
        <div className={cn("flex gap-2 items-end", !isWidget && "max-w-3xl mx-auto")}>
          <Button
            variant="outline"
            size="icon"
            onClick={() => setLibOpen(true)}
            aria-label="Open question library"
            title="Question library"
            className="shrink-0 h-9 w-9 text-[var(--icon)] border-[var(--border)]"
          >
            <BookOpen className="size-4" />
          </Button>
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
          <Button
            onClick={() => void send()}
            disabled={busy || !input.trim()}
            className="bg-cta-gradient text-white border-0 hover:opacity-95"
          >
            <Send className="size-4" />
          </Button>
        </div>
        {build && (
          <div className={cn(
            "mt-1.5 text-[10px] text-[var(--muted-foreground)]",
            !isWidget && "max-w-3xl mx-auto"
          )} title={`built ${build.build_time} · instance started ${build.image_started_at}`}>
            Build <code>{build.git_sha}</code> · v{build.version}
          </div>
        )}
      </footer>

      <QuestionLibrary
        open={libOpen}
        onClose={() => setLibOpen(false)}
        onSend={(q) => { setLibOpen(false); void sendWithText(q); }}
      />
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  if (turn.role === "user") {
    return (
      <div className="flex gap-2.5">
        <Avatar role="user" />
        <div className="rounded-lg bg-[var(--accent)] text-[var(--accent-foreground)] px-3 py-2 text-sm">
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
  const data: AnswerResponse = turn.data;
  const imagesEnabled: boolean = turn.imagesEnabled;
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
          {data.build && (
            <>
              <span>·</span>
              <span
                className="font-mono"
                title={`built ${data.build.build_time} · model ${data.build.llm_model}`}
              >
                build {data.build.git_sha}
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** Build the prior-turn context the backend feeds to the LLM. We only ship
 * role + plain text — drop images, citation [N] markers, and the [FIGURE: id]
 * tags, since past assistant turns referenced a different retrieval set. */
function buildHistory(turns: Turn[]): HistoryTurn[] {
  const out: HistoryTurn[] = [];
  for (const t of turns) {
    if (t.role === "user") {
      out.push({ role: "user", content: t.content });
    } else if (t.role === "assistant") {
      const text = (t.data?.answer ?? "")
        .replace(/\[FIGURE:\s*[A-Za-z0-9_\-]+\s*\]/g, "")
        .replace(/\[\d+\]/g, "")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
      if (text) {
        out.push({
          role: "assistant",
          content: text.length > HISTORY_CHAR_BUDGET
            ? text.slice(0, HISTORY_CHAR_BUDGET) + "…"
            : text,
        });
      }
    }
    // Skip "error" turns — never useful as context.
  }
  // Most recent N turns only, oldest-first.
  return out.slice(-HISTORY_TURN_LIMIT);
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
