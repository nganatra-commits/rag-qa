/**
 * Server-side API client used by Next.js Route Handlers.
 *
 * Browser code never calls the FastAPI backend directly — it goes through
 * /api/* route handlers in this app. Two reasons:
 *  1. We can attach the (optional) X-API-Key on the server, never shipped to
 *     the browser.
 *  2. Same-origin for the browser; CORS only needs to allow this Next server.
 */
import type {
  AnswerResponse,
  HealthResponse,
  RetrieveResponse,
} from "@/types/api";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const API_KEY = process.env.RAGQA_API_KEY ?? "";

function headers() {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (API_KEY) h["X-API-Key"] = API_KEY;
  return h;
}

async function callBackend<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: { ...headers(), ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(
      `backend ${res.status} ${res.statusText} on ${path}: ${body.slice(0, 500)}`
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
};
