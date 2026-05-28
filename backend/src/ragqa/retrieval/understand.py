"""Question-understanding + LLM rerank + answerability gate.

This is the precision stage of the retrieve→rerank→gate pipeline. After
high-recall retrieval (hybrid + multi-query expansion) hands us a wide
candidate set, ONE LLM call does three things at once:

  1. understands the question (intent classification),
  2. scores each candidate chunk's relevance to the question,
  3. reports how confident it is that these chunks can answer at all.

The caller (routes.py /answer) uses (2) to keep only the best chunks
and (3) to decide answer-vs-refuse BEFORE spending the expensive answer
call — so refusals become deterministic instead of relying on the
answer model to self-assess.

Design mirrors query_rewriter.py: reuse OpenAIClient, JSON mode,
LRU cache, and fail OPEN (on any error, return the hits unchanged with
full confidence so we never behave worse than the no-understand path).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from ragqa.config import Settings, get_settings
from ragqa.core.logging import get_logger
from ragqa.core.openai_http import OpenAIClient, OpenAIError
from ragqa.models.chunks import RetrievalHit

log = get_logger(__name__)


_VALID_INTENTS = {
    "procedural", "definitional", "diagnostic", "out_of_scope", "unanswerable",
}

_SYSTEM_PROMPT = """\
You are the retrieval-precision stage of a RAG system for the NWA
Quality Analyst 8 manuals (QAman, QATutor, QAsetup). You are given a
user question and a numbered list of candidate manual chunks. Do three
things and return ONLY JSON.

1. intent — classify the question as exactly one of:
   - "procedural"   : how do I do X / steps to accomplish something
   - "definitional" : what is X / what does Y do
   - "diagnostic"   : why doesn't X match Y / why isn't X working
   - "out_of_scope" : about the AKS Operator Dashboard / Dashboard
                      Designer / dashboard alarms — NOT documented in
                      these manuals
   - "unanswerable" : a reasonable QA question, but NONE of the
                      candidate chunks actually contain the answer

2. answerable — a float 0.0–1.0: how confident are you that the
   candidate chunks below actually let you answer THIS question.
   High (>0.7) when at least one chunk squarely covers it. Low (<0.3)
   when the chunks only share vocabulary but describe a different
   feature, or don't address the question.

3. chunk_scores — for EACH candidate, its relevance to the question
   as a float 0.0–1.0. Score by whether the chunk's content answers
   the question, not mere keyword overlap.

Output JSON exactly:
{"intent":"procedural","answerable":0.0-1.0,
 "chunk_scores":[{"index":1,"relevance":0.0-1.0}, ...]}

Score every candidate index you are given. No prose outside the JSON.
"""


@dataclass
class UnderstandResult:
    reranked_hits: list[RetrievalHit]
    intent: str
    answerable: float
    used_llm: bool  # False when we failed open


def _compact_candidates(hits: list[RetrievalHit], max_chars: int) -> str:
    lines = []
    for i, h in enumerate(hits, start=1):
        c = h.chunk
        section = " > ".join(c.section_path) if c.section_path else "(no section)"
        text = " ".join((c.text or "").split())[:max_chars]
        lines.append(f"[{i}] {c.doc_id} | {section}\n{text}")
    return "\n\n".join(lines)


def _parse(text: str) -> dict | None:
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


@lru_cache(maxsize=1024)
def _cached_call(query: str, chunk_key: str, model: str, base_url: str | None,
                 api_key: str, max_tokens: int, cand_chars: int,
                 candidates_block: str, reasoning_effort: str) -> str:
    """Returns raw JSON string from the model, or '' on error. Cached by
    (query, chunk_key) so repeats are free. chunk_key is the ordered
    tuple of chunk_ids; candidates_block is passed through (not part of
    the semantic key beyond chunk_key, but kept as an arg so the cache
    is consistent for a given candidate set)."""
    client = OpenAIClient(api_key=api_key, base_url=base_url, timeout=30.0)
    try:
        resp = client.chat_completion(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",
                 "content": f"Question: {query}\n\nCandidates:\n{candidates_block}"},
            ],
            max_tokens=max_tokens,
            temperature=0.0,  # ignored for gpt-5 family (fixed at 1.0)
            reasoning_effort=reasoning_effort or None,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        log.warning("understand.openai_error", err=str(e), query=query[:80])
        return ""
    except Exception as e:
        log.warning("understand.unexpected", err=repr(e), query=query[:80])
        return ""
    choices = resp.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message", {}).get("content") or "").strip()


def understand_and_rerank(query: str, hits: list[RetrievalHit],
                          settings: Settings | None = None) -> UnderstandResult:
    """Understand the question, rerank `hits` by LLM-judged relevance,
    and report an answerability confidence. Fails OPEN."""
    s = settings or get_settings()
    keep = max(1, s.rerank_keep)

    if not s.understand_enabled or not hits:
        return UnderstandResult(hits[:keep], "unknown", 1.0, used_llm=False)

    candidates = hits[: s.understand_candidate_k]
    chunk_key = "|".join(h.chunk.chunk_id for h in candidates)
    block = _compact_candidates(candidates, s.understand_candidate_chars)

    raw = _cached_call(
        query.strip(), chunk_key, s.understand_model, s.openai_base_url,
        s.openai_api_key, s.understand_max_tokens, s.understand_candidate_chars,
        block, s.reasoning_effort or "",
    )
    data = _parse(raw)
    if not data:
        # Fail open: keep original order, full confidence.
        return UnderstandResult(candidates[:keep], "unknown", 1.0, used_llm=False)

    intent = str(data.get("intent", "unknown")).strip().lower()
    if intent not in _VALID_INTENTS:
        intent = "unknown"
    try:
        answerable = float(data.get("answerable"))
    except (TypeError, ValueError):
        answerable = 1.0
    answerable = max(0.0, min(1.0, answerable))

    # Map index→relevance and reorder.
    scores: dict[int, float] = {}
    for item in (data.get("chunk_scores") or []):
        try:
            idx = int(item.get("index"))
            rel = float(item.get("relevance"))
        except (TypeError, ValueError):
            continue
        scores[idx] = max(0.0, min(1.0, rel))

    # Attach rerank_score and sort. Candidates not scored keep 0.0 so
    # they sink below scored ones but remain available.
    for i, h in enumerate(candidates, start=1):
        h.rerank_score = scores.get(i, 0.0)
    reranked = sorted(candidates, key=lambda h: h.rerank_score or 0.0, reverse=True)

    log.info("understand.done", query=query[:80], intent=intent,
             answerable=round(answerable, 3),
             top_rel=round(reranked[0].rerank_score or 0.0, 3) if reranked else 0.0,
             n=len(candidates))

    return UnderstandResult(reranked[:keep], intent, answerable, used_llm=True)
