/**
 * Renders an assistant message with [FIGURE: <image_id>] markers replaced
 * by inline images served from /api/images/<id>.
 *
 * Defensive measures (because LLMs occasionally ignore the system-prompt rule):
 *  - Markdown image syntax ![alt](url) is intercepted. If the URL points at
 *    /api/images/<id> we render it; otherwise we swallow it (so the user
 *    doesn't see a broken-image icon) and surface the alt text as a chip.
 *  - Any bound images from the response that the model did NOT inline get
 *    rendered in a "Related screenshots" tray at the bottom of the message.
 */
"use client";

import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ImageIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useGallery } from "@/components/image-gallery";
import type { AnswerImage } from "@/types/api";

const FIGURE_RE = /\[FIGURE:\s*([A-Za-z0-9_\-]+)\s*\]/g;
const IMAGE_ID_FROM_URL_RE = /\/api\/images\/([A-Za-z0-9_\-]+)/;

/**
 * Backend serves images at /api/images/{id}. Since the frontend is now a
 * static site (no Next.js rewrite proxy), we prepend the backend's public
 * URL when the cdn_url is relative.
 */
const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

function absoluteImageUrl(url: string | null | undefined): string {
  if (!url) return "";
  if (/^https?:\/\//.test(url)) return url;
  return `${BACKEND_URL}${url.startsWith("/") ? "" : "/"}${url}`;
}

interface AssistantMessageProps {
  text: string;
  images: AnswerImage[];
  referencedImageIds: string[];
  isRefusal?: boolean;
  /** When false, strip [FIGURE: id] markers from text and hide all images. */
  imagesEnabled?: boolean;
}

export function AssistantMessage({
  text: rawText,
  images: rawImages,
  referencedImageIds,
  imagesEnabled = true,
}: AssistantMessageProps) {
  // When images are disabled, strip the markers from the body so the user
  // sees clean prose, and treat images[] as empty so the tray + inline render
  // are both suppressed.
  const text = imagesEnabled
    ? rawText
    : rawText.replace(FIGURE_RE, "").replace(/\n{3,}/g, "\n\n");

  // Dedupe by image_id - the same image can be bound to multiple retrieved
  // chunks, so the flattened array has duplicates. Keep the first occurrence.
  // When imagesEnabled is false, return an empty list so all rendering is gated.
  const images = React.useMemo<AnswerImage[]>(() => {
    if (!imagesEnabled) return [];
    const seen = new Set<string>();
    const out: AnswerImage[] = [];
    for (const img of rawImages) {
      if (seen.has(img.image_id)) continue;
      seen.add(img.image_id);
      out.push(img);
    }
    return out;
  }, [rawImages, imagesEnabled]);

  const imageById = React.useMemo(() => {
    const m = new Map<string, AnswerImage>();
    images.forEach((img) => m.set(img.image_id, img));
    return m;
  }, [images]);

  const parts = React.useMemo(() => splitOnFigures(text), [text]);

  // Tray policy: disabled. Images only appear inline at their [FIGURE: id]
  // markers. Un-inlined bound images are hidden (still discoverable via
  // /retrieve in the API for power users / debugging).

  // Ordered list of images for the gallery: ONLY the ones actually inlined
  // in the answer body (in marker order). Un-inlined "related" images from
  // the retrieved set are intentionally excluded — they were confusing in
  // the lightbox ("3/11 images" when only 3 are in the steps).
  const galleryOrder = React.useMemo<AnswerImage[]>(() => {
    const seen = new Set<string>();
    const out: AnswerImage[] = [];
    for (const part of parts) {
      if (part.type === "figure") {
        const img = imageById.get(part.imageId);
        if (img && !seen.has(img.image_id)) {
          seen.add(img.image_id);
          out.push(img);
        }
      }
    }
    return out;
  }, [parts, imageById]);

  return (
    <div className="prose prose-sm max-w-none dark:prose-invert">
      {parts.map((part, idx) => {
        if (part.type === "text") {
          return (
            <ReactMarkdown
              key={`t-${idx}`}
              remarkPlugins={[remarkGfm]}
              components={makeMarkdownComponents(imageById, referencedImageIds, galleryOrder)}
            >
              {part.value}
            </ReactMarkdown>
          );
        }
        const img = imageById.get(part.imageId);
        if (!img) {
          return (
            <span
              key={`m-${idx}`}
              className="inline-block px-1.5 py-0.5 text-xs rounded bg-[var(--muted)] text-[var(--muted-foreground)]"
              title="image not in retrieved set"
            >
              [missing image: {part.imageId}]
            </span>
          );
        }
        return (
          <InlineImage
            key={`m-${idx}`}
            img={img}
            highlighted={referencedImageIds.includes(img.image_id)}
            allImages={galleryOrder}
          />
        );
      })}

    </div>
  );
}

function InlineImage({
  img,
  highlighted,
  allImages,
}: {
  img: AnswerImage;
  highlighted: boolean;
  allImages: AnswerImage[];
}) {
  const gallery = useGallery();
  const handleOpen = () => {
    const idx = allImages.findIndex((x) => x.image_id === img.image_id);
    gallery.open(allImages, idx >= 0 ? idx : 0);
  };
  return (
    <figure
      className={cn(
        "not-prose my-2.5 rounded-lg border border-[var(--border)] bg-[var(--background)] overflow-hidden",
        "shadow-sm",
        highlighted && "ring-2 ring-[var(--accent)] ring-offset-1 ring-offset-[var(--muted)]"
      )}
    >
      <button
        type="button"
        onClick={handleOpen}
        className="block w-full text-left cursor-zoom-in focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        aria-label="Open in gallery"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={absoluteImageUrl(img.cdn_url)}
          alt={img.alt_text || img.caption || "screenshot"}
          loading="lazy"
          className="block w-full h-auto"
        />
      </button>
      <figcaption className="px-2.5 py-1.5 text-[11px] text-[var(--muted-foreground)] border-t border-[var(--border)] flex items-center gap-2 flex-wrap">
        <span className="font-medium text-[var(--foreground)]">p. {img.page}</span>
        <span className="truncate flex-1 min-w-0">
          {img.caption || img.alt_text}
        </span>
      </figcaption>
    </figure>
  );
}

function makeMarkdownComponents(
  imageById: Map<string, AnswerImage>,
  referencedImageIds: string[],
  galleryOrder: AnswerImage[]
) {
  return {
    h1: ({ children, ...p }: React.HTMLAttributes<HTMLHeadingElement>) => (
      <h2 className="text-base font-semibold mt-4 mb-2 first:mt-0 text-[var(--foreground)]" {...p}>
        {children}
      </h2>
    ),
    h2: ({ children, ...p }: React.HTMLAttributes<HTMLHeadingElement>) => (
      <h2 className="text-base font-semibold mt-4 mb-2 first:mt-0 text-[var(--foreground)]" {...p}>
        {children}
      </h2>
    ),
    h3: ({ children, ...p }: React.HTMLAttributes<HTMLHeadingElement>) => (
      <h3 className="text-[13px] font-semibold uppercase tracking-wide mt-4 mb-1.5 first:mt-0 text-[var(--muted-foreground)]" {...p}>
        {children}
      </h3>
    ),
    h4: ({ children, ...p }: React.HTMLAttributes<HTMLHeadingElement>) => (
      <h4 className="text-sm font-semibold mt-3 mb-1.5 first:mt-0" {...p}>
        {children}
      </h4>
    ),
    p: ({ children, ...p }: React.HTMLAttributes<HTMLParagraphElement>) => (
      <p className="my-1.5 leading-relaxed text-[13.5px]" {...p}>
        {children}
      </p>
    ),
    ul: ({ children, ...p }: React.HTMLAttributes<HTMLUListElement>) => (
      <ul className="my-1.5 ml-5 list-disc space-y-1 text-[13.5px]" {...p}>
        {children}
      </ul>
    ),
    ol: ({ children, ...p }: React.HTMLAttributes<HTMLOListElement>) => (
      <ol className="my-1.5 ml-5 list-decimal space-y-1.5 text-[13.5px] marker:text-[var(--muted-foreground)] marker:font-medium" {...p}>
        {children}
      </ol>
    ),
    li: ({ children, ...p }: React.HTMLAttributes<HTMLLIElement>) => (
      <li className="leading-relaxed pl-0.5" {...p}>
        {children}
      </li>
    ),
    strong: ({ children, ...p }: React.HTMLAttributes<HTMLElement>) => (
      <strong className="font-semibold text-[var(--foreground)]" {...p}>
        {children}
      </strong>
    ),
    em: ({ children, ...p }: React.HTMLAttributes<HTMLElement>) => (
      <em className="italic" {...p}>
        {children}
      </em>
    ),
    a: ({ children, href, ...p }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="text-[var(--accent)] underline decoration-[var(--accent)]/40 underline-offset-2 hover:decoration-[var(--accent)]"
        {...p}
      >
        {children}
      </a>
    ),
    blockquote: ({ children, ...p }: React.HTMLAttributes<HTMLQuoteElement>) => (
      <blockquote
        className="my-2 border-l-2 border-[var(--accent)]/40 pl-3 italic text-[var(--muted-foreground)]"
        {...p}
      >
        {children}
      </blockquote>
    ),
    hr: () => <hr className="my-3 border-[var(--border)]" />,
    table: ({ children, ...p }: React.HTMLAttributes<HTMLTableElement>) => (
      <div className="my-3 overflow-x-auto">
        <table className="w-full text-[12.5px] border-collapse" {...p}>
          {children}
        </table>
      </div>
    ),
    th: ({ children, ...p }: React.HTMLAttributes<HTMLTableCellElement>) => (
      <th
        className="text-left font-semibold px-2 py-1.5 border-b border-[var(--border)] bg-[var(--background)]"
        {...p}
      >
        {children}
      </th>
    ),
    td: ({ children, ...p }: React.HTMLAttributes<HTMLTableCellElement>) => (
      <td className="px-2 py-1.5 border-b border-[var(--border)] align-top" {...p}>
        {children}
      </td>
    ),
    // react-markdown v9 dropped the `inline` prop. Block code has a
    // language-* className from the fenced ``` block; inline code has no
    // className. Render only the inline form here — block code is handled
    // by the `pre` component below (so we don't nest <pre> inside <p>).
    code: ({
      className,
      children,
      ...props
    }: React.HTMLAttributes<HTMLElement>) => (
      <code
        className={cn(
          "px-1.5 py-0.5 rounded bg-[var(--background)] border border-[var(--border)] font-mono text-[0.82em] text-[var(--foreground)]",
          className
        )}
        {...props}
      >
        {children}
      </code>
    ),
    pre: ({
      className,
      children,
      ...props
    }: React.HTMLAttributes<HTMLPreElement>) => (
      <pre
        className={cn(
          "my-2 rounded-md bg-[var(--background)] border border-[var(--border)] p-3 overflow-x-auto font-mono text-[12px] leading-relaxed",
          className
        )}
        {...props}
      >
        {children}
      </pre>
    ),

    // Defensive: if the model wrote ![alt](url) instead of [FIGURE: id]
    img: ({
      src,
      alt,
    }: React.ImgHTMLAttributes<HTMLImageElement>) => {
      const url = typeof src === "string" ? src : "";
      const m = url.match(IMAGE_ID_FROM_URL_RE);
      if (m && imageById.has(m[1])) {
        const img = imageById.get(m[1])!;
        return (
          <InlineImage
            img={img}
            highlighted={referencedImageIds.includes(img.image_id)}
            allImages={galleryOrder}
          />
        );
      }
      return (
        <span
          className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded bg-[var(--muted)] text-[var(--muted-foreground)] align-baseline"
          title="model produced an invalid image link; see the Additional screenshots tray below"
        >
          <ImageIcon className="size-3" />
          {alt || "screenshot"}
        </span>
      );
    },
  };
}

type Part =
  | { type: "text"; value: string }
  | { type: "figure"; imageId: string };

function splitOnFigures(text: string): Part[] {
  const parts: Part[] = [];
  let lastIndex = 0;
  for (const m of text.matchAll(FIGURE_RE)) {
    if (m.index === undefined) continue;
    if (m.index > lastIndex) {
      parts.push({ type: "text", value: text.slice(lastIndex, m.index) });
    }
    parts.push({ type: "figure", imageId: m[1] });
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < text.length) {
    parts.push({ type: "text", value: text.slice(lastIndex) });
  }
  return parts;
}
