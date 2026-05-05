"use client";

import * as React from "react";
import type { AnswerCitation } from "@/types/api";
import { usePdfViewer } from "@/components/pdf-viewer";

interface CitationsProps {
  citations: AnswerCitation[];
  /** The full answer text — used to filter to only sources actually cited. */
  answerText?: string;
}

/**
 * Show only the citations the model actually referenced via [N] markers in
 * the answer body. If the answer has zero markers, render nothing — the
 * unused retrieved chunks would just be noise.
 *
 * The page reference itself is a button: clicking it opens the source PDF
 * inline (popup) at the cited page via the global <PdfViewerProvider>.
 */
export function Citations({ citations, answerText = "" }: CitationsProps) {
  const pdf = usePdfViewer();
  const referencedIndexes = React.useMemo(() => {
    const out = new Set<number>();
    for (const m of answerText.matchAll(/\[(\d+)\]/g)) {
      const i = Number(m[1]);
      if (Number.isFinite(i) && i >= 1 && i <= citations.length) {
        out.add(i);
      }
    }
    return out;
  }, [answerText, citations.length]);

  // Preserve the original [N] numbering so the inline markers in the answer
  // still match the displayed list.
  const cited = citations
    .map((c, idx) => ({ c, n: idx + 1 }))
    .filter(({ n }) => referencedIndexes.has(n));

  // If the model cited specific chunks, show only those.
  // If the model cited nothing, fall back to showing what was searched
  // (so the user still sees provenance), under a different label.
  const usedFallback = cited.length === 0;
  const shown = usedFallback
    ? citations.slice(0, 4).map((c, idx) => ({ c, n: idx + 1 }))
    : cited;

  if (shown.length === 0) return null;

  return (
    <div className="mt-4 border-t border-[var(--border)] pt-3">
      <h3 className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)] mb-2">
        {usedFallback ? "Searched in" : "Sources"}
      </h3>
      <ol className="space-y-1.5 text-xs list-none p-0">
        {shown.map(({ c, n }) => {
          const pageLabel =
            c.page_end !== c.page_start
              ? `pp. ${c.page_start}–${c.page_end}`
              : `p. ${c.page_start}`;
          const sectionLabel = c.section_path.join(" › ");
          return (
            <li key={c.chunk_id} className="flex items-baseline gap-2">
              <span className="font-mono text-[var(--muted-foreground)] shrink-0">
                [{n}]
              </span>
              <span>
                <span className="font-medium">{c.doc_id}</span>
                {c.section_path.length > 0 && (
                  <span className="text-[var(--muted-foreground)]">
                    {" · "}
                    {sectionLabel}
                  </span>
                )}
                {" · "}
                <button
                  type="button"
                  onClick={() =>
                    pdf.open({
                      docId: c.doc_id,
                      page: c.page_start,
                      label: sectionLabel || undefined,
                    })
                  }
                  className="text-[var(--accent)] underline decoration-[var(--accent)]/40 underline-offset-2 hover:decoration-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded-sm"
                  title="Open PDF at this page"
                >
                  {pageLabel}
                </button>
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
