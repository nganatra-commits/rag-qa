import { NextResponse } from "next/server";
import { backend } from "@/lib/api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const { query, top_k, rerank_top_k, alpha, doc_filter, max_images } = body ?? {};
    if (typeof query !== "string" || query.trim().length === 0) {
      return NextResponse.json({ error: "query required" }, { status: 400 });
    }
    const data = await backend.answer({
      query,
      top_k,
      rerank_top_k,
      alpha,
      doc_filter,
      max_images,
    });
    return NextResponse.json(data);
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown error";
    console.error("chat route failed:", message);
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
