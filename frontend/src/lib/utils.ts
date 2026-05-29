import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatTokens(n: number) {
  if (n < 1000) return `${n} tok`;
  return `${(n / 1000).toFixed(1)}k tok`;
}

export function formatLatency(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/**
 * Copy text to the clipboard, surviving non-secure contexts.
 * navigator.clipboard requires a secure context (HTTPS/localhost) — the same
 * constraint that breaks crypto.randomUUID inside an HTTP-parent iframe. Fall
 * back to the legacy execCommand path so Copy works in the Insights embed.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to legacy path */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/**
 * UUID v4 generator that survives non-secure contexts.
 *
 * `crypto.randomUUID()` is only available in a secure context (HTTPS,
 * localhost, file://). When the chatbot is loaded in an iframe whose
 * top-level parent is HTTP (e.g. Insights at http://10.1.188.20:3000),
 * the iframe is NOT a secure context per the WHATWG spec, and
 * `crypto.randomUUID` is undefined — the UI loads but throws
 * "crypto.randomUUID is not a function" on the first interaction.
 *
 * `crypto.getRandomValues()` IS available in non-secure contexts, so
 * we prefer that for entropy and only fall back to `Math.random` when
 * even `crypto` is missing. The IDs we generate are React keys + local
 * chat/turn identifiers — not security-critical, so the Math.random
 * fallback is acceptable.
 */
export function randomId(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.getRandomValues === "function"
  ) {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = (n: number) => n.toString(16).padStart(2, "0");
  return (
    hex(bytes[0]) + hex(bytes[1]) + hex(bytes[2]) + hex(bytes[3]) + "-" +
    hex(bytes[4]) + hex(bytes[5]) + "-" +
    hex(bytes[6]) + hex(bytes[7]) + "-" +
    hex(bytes[8]) + hex(bytes[9]) + "-" +
    hex(bytes[10]) + hex(bytes[11]) + hex(bytes[12]) +
    hex(bytes[13]) + hex(bytes[14]) + hex(bytes[15])
  );
}
