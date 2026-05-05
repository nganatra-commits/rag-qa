"use client";

import * as React from "react";
import { ChevronLeft, ChevronRight, X, Download } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AnswerImage } from "@/types/api";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

function absUrl(url: string | null | undefined): string {
  if (!url) return "";
  if (/^https?:\/\//.test(url)) return url;
  return `${BACKEND_URL}${url.startsWith("/") ? "" : "/"}${url}`;
}

/**
 * In-page lightbox/gallery for the screenshots returned with an answer.
 * Backdrop click, ESC, and the × button all close it. Arrow keys navigate.
 */

interface GalleryContextValue {
  open: (images: AnswerImage[], startIndex: number) => void;
}

const GalleryContext = React.createContext<GalleryContextValue | null>(null);

export function useGallery(): GalleryContextValue {
  const ctx = React.useContext(GalleryContext);
  if (!ctx) {
    throw new Error("useGallery must be used inside <GalleryProvider>");
  }
  return ctx;
}

export function GalleryProvider({ children }: { children: React.ReactNode }) {
  const [images, setImages] = React.useState<AnswerImage[]>([]);
  const [index, setIndex] = React.useState(0);
  const [isOpen, setIsOpen] = React.useState(false);

  const open = React.useCallback(
    (imgs: AnswerImage[], startIndex: number) => {
      if (!imgs.length) return;
      setImages(imgs);
      setIndex(Math.max(0, Math.min(imgs.length - 1, startIndex)));
      setIsOpen(true);
    },
    []
  );

  const close = React.useCallback(() => setIsOpen(false), []);
  const prev = React.useCallback(
    () => setIndex((i) => (i - 1 + images.length) % images.length),
    [images.length]
  );
  const next = React.useCallback(
    () => setIndex((i) => (i + 1) % images.length),
    [images.length]
  );

  // Keyboard navigation
  React.useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowLeft") prev();
      else if (e.key === "ArrowRight") next();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, close, prev, next]);

  // Lock body scroll while open
  React.useEffect(() => {
    if (!isOpen) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prevOverflow;
    };
  }, [isOpen]);

  const ctx = React.useMemo(() => ({ open }), [open]);

  const current = images[index];

  return (
    <GalleryContext.Provider value={ctx}>
      {children}
      {isOpen && current && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={current.alt_text || "Screenshot"}
          className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/40 backdrop-blur-[2px]"
          onClick={close}
        >
          <div
            className={cn(
              "relative bg-[var(--background)] text-[var(--foreground)] rounded-xl shadow-2xl",
              "border border-[var(--border)] overflow-hidden",
              "w-full max-w-[720px] max-h-[80vh]",
              "flex flex-col"
            )}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header bar */}
            <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-[var(--border)]">
              <div className="flex items-center gap-2 min-w-0 text-xs">
                <span className="font-medium shrink-0">
                  {index + 1} / {images.length}
                </span>
                <span className="text-[var(--muted-foreground)] shrink-0">·</span>
                <span className="font-medium shrink-0">p. {current.page}</span>
                <span className="text-[var(--muted-foreground)] truncate">
                  {current.caption || current.alt_text}
                </span>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <a
                  href={absUrl(current.cdn_url)}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="p-1.5 rounded hover:bg-[var(--muted)] text-[var(--muted-foreground)]"
                  aria-label="Open full size in new tab"
                  title="Open full size"
                >
                  <Download className="size-4" />
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

            {/* Image area */}
            <div className="relative flex-1 min-h-0 flex items-center justify-center bg-[var(--muted)] p-4">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={absUrl(current.cdn_url)}
                alt={current.alt_text || current.caption || "screenshot"}
                className="max-w-full max-h-full object-contain rounded shadow-sm bg-white"
              />

              {/* Prev / Next overlay buttons */}
              {images.length > 1 && (
                <>
                  <button
                    onClick={prev}
                    className="absolute left-2 top-1/2 -translate-y-1/2 p-1.5 rounded-full bg-[var(--background)]/90 border border-[var(--border)] hover:bg-[var(--background)] shadow"
                    aria-label="Previous (←)"
                  >
                    <ChevronLeft className="size-4" />
                  </button>
                  <button
                    onClick={next}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded-full bg-[var(--background)]/90 border border-[var(--border)] hover:bg-[var(--background)] shadow"
                    aria-label="Next (→)"
                  >
                    <ChevronRight className="size-4" />
                  </button>
                </>
              )}
            </div>

            {/* Thumbnail strip */}
            {images.length > 1 && (
              <div className="border-t border-[var(--border)] px-2 py-2 bg-[var(--background)]">
                <div className="flex gap-1.5 overflow-x-auto">
                  {images.map((img, i) => (
                    <button
                      key={img.image_id}
                      onClick={() => setIndex(i)}
                      aria-label={`Go to image ${i + 1}`}
                      className={cn(
                        "size-10 shrink-0 rounded overflow-hidden border-2 transition",
                        i === index
                          ? "border-[var(--accent)] opacity-100"
                          : "border-transparent opacity-60 hover:opacity-100"
                      )}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={absUrl(img.cdn_url)}
                        alt=""
                        className="size-full object-cover bg-white"
                      />
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </GalleryContext.Provider>
  );
}
