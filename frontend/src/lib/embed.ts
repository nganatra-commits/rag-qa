/**
 * Embed integration: lets a host app (iframe / popup) prefill a question via
 * ?q=, push arbitrary key/value context via additional URL params or
 * postMessage, and receive an answer event after each turn.
 *
 * Reserved URL params: q, chat, autosubmit. Everything else is host context.
 */

const RESERVED_PARAM_KEYS = new Set(["q", "chat", "autosubmit"]);

export interface EmbedUrlContext {
  initialQuery: string | null;
  hostContext: Record<string, string>;
  autoSubmit: boolean;
}

export function readUrlContext(): EmbedUrlContext {
  if (typeof window === "undefined") {
    return { initialQuery: null, hostContext: {}, autoSubmit: true };
  }
  const params = new URLSearchParams(window.location.search);
  const hostContext: Record<string, string> = {};
  for (const [key, value] of params.entries()) {
    if (!RESERVED_PARAM_KEYS.has(key)) hostContext[key] = value;
  }
  const q = params.get("q");
  return {
    initialQuery: q && q.trim() ? q : null,
    hostContext,
    autoSubmit: params.get("autosubmit") !== "0",
  };
}

/**
 * Prepend host context as a structured prefix to the user's question. The
 * entire string is sent through the existing /answer `query` field, so no
 * backend schema change is required. Empty context returns the query unchanged.
 */
export function buildQueryWithContext(
  query: string,
  context: Record<string, string>
): string {
  const keys = Object.keys(context);
  if (keys.length === 0) return query;
  const parts = keys.map((k) => `${k}=${context[k]}`).join("; ");
  return `[Host context: ${parts}]\n\n${query}`;
}

/** Strip ?q= from the URL after auto-submit so a refresh doesn't re-fire it. */
export function stripQueryFromUrl(): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (!url.searchParams.has("q")) return;
  url.searchParams.delete("q");
  window.history.replaceState(null, "", url.toString());
}

export type IncomingEmbedMessage =
  | { type: "SET_CONTEXT"; context: Record<string, string> }
  | { type: "ASK"; query: string };

export type OutgoingEmbedMessage =
  | { type: "READY" }
  | { type: "ANSWER"; text: string }
  | { type: "ERROR"; message: string };

/**
 * Allowed parent origins for postMessage. `*` accepts any origin and is only
 * safe in dev / trusted setups. Set NEXT_PUBLIC_EMBED_ALLOWED_ORIGINS to a
 * comma-separated allowlist for production.
 */
function getAllowedOrigins(): string[] | "*" {
  const raw = process.env.NEXT_PUBLIC_EMBED_ALLOWED_ORIGINS ?? "*";
  if (raw === "*") return "*";
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function installEmbedListener(
  onMessage: (msg: IncomingEmbedMessage) => void
): () => void {
  if (typeof window === "undefined") return () => {};
  const allowed = getAllowedOrigins();
  const handler = (event: MessageEvent) => {
    if (allowed !== "*" && !allowed.includes(event.origin)) return;
    const data = event.data;
    if (!data || typeof data !== "object") return;
    const type = (data as { type?: unknown }).type;
    if (type === "SET_CONTEXT") {
      const ctx = (data as { context?: unknown }).context;
      if (ctx && typeof ctx === "object") {
        const safe: Record<string, string> = {};
        for (const [k, v] of Object.entries(ctx as Record<string, unknown>)) {
          if (typeof v === "string") safe[k] = v;
        }
        onMessage({ type: "SET_CONTEXT", context: safe });
      }
    } else if (type === "ASK") {
      const q = (data as { query?: unknown }).query;
      if (typeof q === "string" && q.trim()) {
        onMessage({ type: "ASK", query: q });
      }
    }
  };
  window.addEventListener("message", handler);
  return () => window.removeEventListener("message", handler);
}

/**
 * targetOrigin="*" is intentional: the embedded app doesn't know the parent's
 * origin a priori. The host is expected to validate `event.origin` on its
 * own receiver. Messages here are non-sensitive (READY / ANSWER text / errors).
 */
export function postToParent(msg: OutgoingEmbedMessage): void {
  if (typeof window === "undefined") return;
  if (window.parent === window) return;
  try {
    window.parent.postMessage(msg, "*");
  } catch {
    // parent may have been torn down
  }
}

export function isEmbedded(): boolean {
  if (typeof window === "undefined") return false;
  return window.parent !== window;
}
