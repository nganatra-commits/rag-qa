"use client";

import * as React from "react";
import { Copy, Send, X, Check, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn, copyText } from "@/lib/utils";

/** A curated, category-tagged set of questions the chatbot answers well.
 * These are drawn from the validated QA test set so "Send" reliably returns
 * a good answer. Update the list here to change the library. */
type LibQuestion = { q: string; cat: Category };
type Category =
  | "Install" | "Tutorial" | "Charts" | "Data Sets" | "Troubleshooting" | "Run Files";

const CATEGORY_ORDER: Category[] = [
  "Install", "Tutorial", "Charts", "Data Sets", "Troubleshooting", "Run Files",
];

const QUESTIONS: LibQuestion[] = [
  // Charts
  { q: "How to add a new control limit region and how to fix a region based on data?", cat: "Charts" },
  { q: "How to add a new control limit region", cat: "Charts" },
  { q: "How do I show DATE on the x-axis of my charts?", cat: "Charts" },
  { q: "How do I show dates on the chart x-axis?", cat: "Charts" },
  { q: "How do I create a histogram?", cat: "Charts" },
  // Data Sets
  { q: "How do I get data from Excel into QA and create a chart?", cat: "Data Sets" },
  { q: "How do I get data from an Excel XLSX file into QA and create a chart?", cat: "Data Sets" },
  { q: "How do I get data from Access into QA and create a chart?", cat: "Data Sets" },
  // Troubleshooting
  { q: "X-axis are not showing DATE/TIME, how to fix it?", cat: "Troubleshooting" },
  { q: "Why out-of-control keeping showing as not acknowledged in my dashboard?", cat: "Troubleshooting" },
  { q: "I used the hyperlink in the chart to access a QA file. Why do I not see my ACCA list?", cat: "Troubleshooting" },
  { q: "The histogram doesn't calculate some statistics I want.", cat: "Troubleshooting" },
  { q: "How to remove an out-of-control point from the control limit calculation?", cat: "Troubleshooting" },
  // Run Files
  { q: "How do I automate charting?", cat: "Run Files" },
  { q: "How do I create charts automatically?", cat: "Run Files" },
  { q: "How do I automate creating a histogram?", cat: "Run Files" },
];

interface QuestionLibraryProps {
  open: boolean;
  onClose: () => void;
  /** Send the chosen question straight into the chat. */
  onSend: (question: string) => void;
}

export function QuestionLibrary({ open, onClose, onSend }: QuestionLibraryProps) {
  const [query, setQuery] = React.useState("");
  const [cat, setCat] = React.useState<Category | "All">("All");
  const [copied, setCopied] = React.useState<string | null>(null);

  // Close on Escape.
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Reset transient state when opened.
  React.useEffect(() => {
    if (open) { setQuery(""); setCat("All"); setCopied(null); }
  }, [open]);

  const counts = React.useMemo(() => {
    const m: Record<string, number> = {};
    for (const c of CATEGORY_ORDER) m[c] = QUESTIONS.filter((x) => x.cat === c).length;
    return m;
  }, []);

  // Only show chips for categories that actually have questions.
  const presentCats = React.useMemo(
    () => CATEGORY_ORDER.filter((c) => counts[c] > 0),
    [counts]
  );

  const filtered = React.useMemo(() => {
    const ql = query.trim().toLowerCase();
    return QUESTIONS.filter(
      (x) =>
        (cat === "All" || x.cat === cat) &&
        (!ql || x.q.toLowerCase().includes(ql))
    );
  }, [query, cat]);

  const handleCopy = async (q: string) => {
    await copyText(q);
    setCopied(q);
    window.setTimeout(() => setCopied((c) => (c === q ? null : c)), 1500);
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 sm:p-8"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Question library"
    >
      <div
        className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-lg bg-[var(--surface-elevated)] shadow-xl border border-[var(--border)] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--border)]">
          <span className="text-[var(--icon)]">📋</span>
          <h2 className="font-semibold text-sm">Question library</h2>
          <span className="text-xs text-[var(--muted-foreground)]">
            {filtered.length} of {QUESTIONS.length} questions
          </span>
          <button
            onClick={onClose}
            aria-label="Close"
            className="ml-auto rounded-md p-1 hover:bg-[var(--muted)] text-[var(--muted-foreground)]"
          >
            <X className="size-4" />
          </button>
        </div>

        {/* Search */}
        <div className="px-4 pt-3">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-[var(--muted-foreground)]" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search questions…"
              className="w-full rounded-md border border-[var(--border)] bg-[var(--background)] pl-8 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
            />
          </div>
        </div>

        {/* Category chips */}
        <div className="px-4 py-3 flex gap-1.5 flex-wrap">
          {(["All", ...presentCats] as const).map((c) => {
            const active = cat === c;
            const n = c === "All" ? QUESTIONS.length : counts[c];
            return (
              <button
                key={c}
                onClick={() => setCat(c as Category | "All")}
                className={cn(
                  "rounded-full px-2.5 py-1 text-xs border transition-colors flex items-center gap-1",
                  active
                    ? "bg-[var(--accent)] text-[var(--accent-foreground)] border-[var(--accent)]"
                    : "bg-[var(--background)] text-[var(--foreground)] border-[var(--border)] hover:bg-[var(--muted)]"
                )}
              >
                {c}
                <span className={cn(
                  "rounded-full px-1.5 text-[10px]",
                  active ? "bg-white/25" : "bg-[var(--muted)] text-[var(--muted-foreground)]"
                )}>{n}</span>
              </button>
            );
          })}
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto px-4 pb-4 space-y-1.5">
          {filtered.length === 0 && (
            <p className="text-sm text-[var(--muted-foreground)] py-8 text-center">
              No questions match “{query}”.
            </p>
          )}
          {filtered.map((item) => (
            <div
              key={item.q}
              className="group flex items-center gap-3 rounded-md border border-transparent hover:border-[var(--border)] hover:bg-[var(--muted)] px-3 py-2.5 transition-colors"
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm text-[var(--foreground)]">{item.q}</p>
                <span className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
                  {item.cat}
                </span>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void handleCopy(item.q)}
                className="h-7 px-2 text-xs shrink-0"
                title="Copy question"
              >
                {copied === item.q ? (
                  <><Check className="size-3.5 mr-1" />Copied</>
                ) : (
                  <><Copy className="size-3.5 mr-1" />Copy</>
                )}
              </Button>
              <Button
                size="sm"
                onClick={() => onSend(item.q)}
                className="h-7 px-2.5 text-xs shrink-0 bg-cta-gradient text-white border-0 hover:opacity-95"
                title="Send to chat"
              >
                <Send className="size-3.5 mr-1" />Send
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
