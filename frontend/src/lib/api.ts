/**
 * Browser-side API client. Calls the FastAPI backend directly using
 * NEXT_PUBLIC_BACKEND_URL. Backend has CORS configured to allow this.
 */
import type {
  AnswerResponse,
  ChatPutRequest,
  ChatRecord,
  ChatSummary,
  HealthResponse,
  HistoryTurn,
  RetrieveResponse,
} from "@/types/api";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

async function callBackend<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(
      `backend ${res.status} on ${path}: ${body.slice(0, 500)}`
    );
  }
  return (await res.json()) as T;
}

export const backend = {
  health: () => callBackend<HealthResponse>("/health"),

  retrieve: (body: {
    query: string;
    top_k?: number;
    rerank_top_k?: number;
    alpha?: number;
    doc_filter?: string[];
  }) =>
    callBackend<RetrieveResponse>("/retrieve", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  answer: (body: {
    query: string;
    top_k?: number;
    rerank_top_k?: number;
    alpha?: number;
    doc_filter?: string[];
    max_images?: number;
    history?: HistoryTurn[];
  }) =>
    callBackend<AnswerResponse>("/answer", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  feedback: (body: { request_id: string; rating: number; note?: string }) =>
    callBackend<{ ok: boolean }>("/feedback", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // --- Chat history (server-side, DynamoDB-backed) ---
  listChats: (limit?: number) =>
    callBackend<ChatSummary[]>(
      `/api/chats${limit ? `?limit=${limit}` : ""}`
    ),

  getChat: (id: string) => callBackend<ChatRecord>(`/api/chats/${encodeURIComponent(id)}`),

  upsertChat: (id: string, body: ChatPutRequest) =>
    callBackend<ChatRecord>(`/api/chats/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  deleteChat: (id: string) =>
    callBackend<{ ok: boolean }>(`/api/chats/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
};
