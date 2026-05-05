"use client";

import * as React from "react";
import { X, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

/**
 * In-page PDF popup. Renders the cleaned source PDF served by
 * GET /api/pdfs/{doc_id}, deep-linked to a specific page via #page=N.
 *
 * Browsers honor #page=N in their built-in PDF viewer, so we just feed an
 * <iframe> the right URL and let the viewer handle pagination.
 */

export interface PdfTarget {
  docId: string;
  /** 1-based page index. */
  page: number;
  /** Optional human-readable label shown in the popup header. */
  label?: string;
}

interface PdfViewerContextValue {
  open: (target: PdfTarget) => void;
}

const PdfViewerContext = React.createContext<PdfViewerContextValue | null>(null);

export function usePdfViewer(): PdfViewerContextValue {
  const ctx = React.useContext(PdfViewerContext);
  if (!ctx) {
    throw new Error("usePdfViewer must be used inside <PdfViewerProvider>");
  }
  return ctx;
}

function pdfUrl(docId: string, page: number): string {
  // FitH = fit page width; toolbar=1 keeps the built-in viewer chrome.
  return `${BACKEND_URL}/api/pdfs/${encodeURIComponent(docId)}#page=${page}&zoom=page-width`;
}

export function PdfViewerProvider({ children }: { children: React.ReactNode }) {
  const [target, setTarget] = React.useState<PdfTarget | null>(null);

  const open = React.useCallback((t: PdfTarget) => setTarget(t), []);
  const close = React.useCallback(() => setTarget(null), []);

  React.useEffect(() => {
    if (!target) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [target, close]);

  React.useEffect(() => {
    if (!target) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [target]);

  const ctx = React.useMemo(() => ({ open }), [open]);

  return (
    <PdfViewerContext.Provider value={ctx}>
      {children}
      {target && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={target.label ?? `${target.docId} page ${target.page}`}
          className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/50 backdrop-blur-[2px]"
          onClick={close}
        >
          <div
            className={cn(
              "relative bg-[var(--background)] text-[var(--foreground)] rounded-xl shadow-2xl",
              "border border-[var(--border)] overflow-hidden",
              "w-full max-w-[1100px] h-[90vh]",
              "flex flex-col"
            )}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-[var(--border)]">
              <div className="flex items-center gap-2 min-w-0 text-xs">
                <span className="font-medium shrink-0 uppercase">
                  {target.docId}
                </span>
                <span className="text-[var(--muted-foreground)] shrink-0">·</span>
                <span className="font-medium shrink-0">page {target.page}</span>
                {target.label && (
                  <>
                    <span className="text-[var(--muted-foreground)] shrink-0">·</span>
                    <span className="truncate text-[var(--muted-foreground)]">
                      {target.label}
                    </span>
                  </>
                )}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <a
                  href={pdfUrl(target.docId, target.page)}
                  target="_blank"
                  rel="noreferrer"
                  className="p-1.5 rounded hover:bg-[var(--muted)] text-[var(--muted-foreground)]"
                  aria-label="Open in new tab"
                  title="Open in new tab"
                >
                  <ExternalLink className="size-4" />
                </a>
                <button
                  onClick={close}
                  className="p-1.5 rounded hover:bg-[var(--muted)]"
                  aria-label="Close"
                  title="Close (Esc)"
                >
                  <X className="size-4" />
                </button>
              </div>
            </div>

            <div className="flex-1 min-h-0 bg-[var(--muted)]">
              <iframe
                src={pdfUrl(target.docId, target.page)}
                className="w-full h-full border-0"
                title={`${target.docId} page ${target.page}`}
              />
            </div>
          </div>
        </div>
      )}
    </PdfViewerContext.Provider>
  );
}
