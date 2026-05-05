/**
 * Lightweight client-side chat history. We keep the most recent N chats in
 * localStorage so the user can re-open them after navigation, page reload,
 * or accidental "New chat" clicks. Stays entirely in the browser — no
 * backend persistence, no cookies, no PII shipped anywhere.
 */
import type { AnswerResponse } from "@/types/api";

export const HISTORY_LIMIT = 5;
const STORAGE_KEY = "ragqa.chats.v1";

export type StoredTurn =
  | { role: "user"; content: string; id: string }
  | { role: "assistant"; data: AnswerResponse; imagesEnabled: boolean; id: string }
  | { role: "error"; message: string; id: string };

export interface StoredChat {
  id: string;
  title: string;
  updatedAt: number;
  turns: StoredTurn[];
  docFilter: string[];
}

function isBrowser(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

export function loadChats(): StoredChat[] {
  if (!isBrowser()) return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (c): c is StoredChat =>
          !!c &&
          typeof c === "object" &&
          typeof (c as StoredChat).id === "string" &&
          Array.isArray((c as StoredChat).turns)
      )
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, HISTORY_LIMIT);
  } catch {
    return [];
  }
}

export function saveChats(chats: StoredChat[]): void {
  if (!isBrowser()) return;
  try {
    const trimmed = chats
      .slice()
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, HISTORY_LIMIT);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // quota or serialization failure — ignore. History is best-effort.
  }
}

export function upsertChat(chat: StoredChat): StoredChat[] {
  const chats = loadChats();
  const idx = chats.findIndex((c) => c.id === chat.id);
  const next = idx >= 0 ? [...chats.slice(0, idx), chat, ...chats.slice(idx + 1)] : [chat, ...chats];
  saveChats(next);
  return loadChats();
}

export function deleteChat(id: string): StoredChat[] {
  const chats = loadChats().filter((c) => c.id !== id);
  saveChats(chats);
  return chats;
}

export function deriveTitle(turns: StoredTurn[]): string {
  const firstUser = turns.find((t) => t.role === "user");
  if (firstUser && firstUser.role === "user") {
    const t = firstUser.content.trim().replace(/\s+/g, " ");
    return t.length > 60 ? t.slice(0, 57) + "…" : t || "New chat";
  }
  return "New chat";
}
