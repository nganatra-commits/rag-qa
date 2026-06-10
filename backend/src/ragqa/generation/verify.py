"""Faithfulness verifier: scores whether the generated answer's claims are
grounded in the retrieved chunks.

This is the trust stage of the retrieve→rerank→gate→answer→VERIFY pipeline.
A second, cheaper LLM call (gpt-5.4-mini by default) reads the question,
the answer, and the chunks the answer was based on, then identifies
specific factual claims that are NOT supported by any chunk. The caller
surfaces the resulting faithfulness score on `AnswerResponse` so the
frontend can render a low-confidence badge when needed.

Design mirrors `retrieval/understand.py`: reuse `OpenAIClient`, JSON mode,
LRU cache, fail OPEN (on any error return faithfulness=1.0 so we never
degrade vs the no-verify path).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from ragqa.config import Settings, get_settings
from ragqa.core.logging import get_logger
from ragqa.core.openai_http import OpenAIClient, OpenAIError
from ragqa.models.chunks import RetrievalHit

log = get_logger(__name__)


_SYSTEM_PROMPT = """\
You are the faithfulness verifier for a RAG system over the NWA Quality
Analyst 8 manuals. You receive a user question, an assistant answer, and
the source chunks the answer was supposed to be based on.

Your job: identify SPECIFIC factual claims in the answer that are NOT
supported by any source chunk. Then output a single faithfulness score
in [0.0, 1.0] reflecting your overall judgement.

What counts as a claim worth checking:
  - Specific command names, option names, or argument names
  - Specific UI labels (dialog names, menu paths, button names)
  - Specific page references, section names, file names
  - Numeric values (limits, defaults, thresholds)
  - Sequential steps in a procedure

What does NOT count as a claim:
  - Connective tissue ("This means...", "In summary...")
  - Reasonable paraphrase that preserves the chunk's meaning
  - General domain knowledge already present in the chunks

How to score faithfulness:
  1.0  : every factual claim is supported by at least one chunk
  0.85 : minor paraphrase that slightly loses precision but stays true
  0.7  : one or two specific terms (labels, args) not in the chunks
  0.5  : a procedural step or numeric value not in the chunks
  0.3  : a fabricated command name, option, or page reference
  0.0  : an answer mostly invented from outside the chunks

Refusal answers ("I could not find...") are vacuously faithful: 1.0.

Output JSON ONLY, no prose, with EXACTLY this shape:
{"faithfulness": 0.0-1.0,
 "unsupported_claims": ["claim 1 from the answer", "claim 2", ...]}

If there are no unsupported claims, return [] for unsupported_claims.
"""


@dataclass
class VerifyResult:
    faithfulness: float
    unsupported_claims: list[str] = field(default_factory=list)
    used_llm: bool = False  # False when we failed open or skipped


def _compact_chunks(hits: list[RetrievalHit], max_chars: int) -> str:
    lines = []
    for i, h in enumerate(hits, start=1):
        c = h.chunk
        section = " > ".join(c.section_path) if c.section_path else "(no section)"
        text = " ".join((c.text or "").split())[:max_chars]
        lines.append(f"[{i}] {c.doc_id} | {section} | pp. {c.page_start}-{c.page_end}\n{text}")
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


@lru_cache(maxsize=512)
def _cached_call(answer_hash: str, chunk_key: str, model: str,
                 base_url: str | None, api_key: str, max_tokens: int,
                 user_block: str, reasoning_effort: str) -> str:
    client = OpenAIClient(api_key=api_key, base_url=base_url, timeout=30.0)
    try:
        resp = client.chat_completion(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_block},
            ],
            max_tokens=max_tokens,
            temperature=0.0,  # ignored for gpt-5 family (fixed at 1.0)
            reasoning_effort=reasoning_effort or None,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        log.warning("verify.openai_error", err=str(e), key=answer_hash[:12])
        return ""
    except Exception as e:
        log.warning("verify.unexpected", err=repr(e), key=answer_hash[:12])
        return ""
    choices = resp.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message", {}).get("content") or "").strip()


def verify_answer(query: str, answer: str, hits: list[RetrievalHit],
                  settings: Settings | None = None) -> VerifyResult:
    """Return a faithfulness score in [0, 1] for `answer` against `hits`.

    Skips (returns 1.0, used_llm=False) when:
      - verify is disabled by config,
      - the answer is empty or very short (likely a refusal),
      - no hits were retrieved (refusal case).

    Fails OPEN on any error: returns faithfulness=1.0, used_llm=False.
    """
    s = settings or get_settings()
    if not s.verify_enabled or not answer or not hits:
        return VerifyResult(faithfulness=1.0, used_llm=False)

    # Skip refusal-looking answers — they're trivially faithful and a verify
    # call would just be cost. Same heuristic as routes._looks_like_refusal
    # but kept local so this module has no FastAPI coupling.
    head = answer.strip().splitlines()[0] if answer.strip() else ""
    if re.match(
        r"^\s*(?:i\s+(?:could\s+not|cannot|can't)|"
        r"(?:unfortunately|sorry),?\s+i\s+(?:could not|cannot|can't))",
        head, re.IGNORECASE,
    ):
        return VerifyResult(faithfulness=1.0, used_llm=False)

    chunk_key = "|".join(h.chunk.chunk_id for h in hits)
    answer_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()
    block = _compact_chunks(hits, s.verify_chunk_chars)
    user = (
        f"Question:\n{query}\n\n"
        f"Assistant answer:\n{answer}\n\n"
        f"Source chunks:\n{block}"
    )

    raw = _cached_call(
        answer_hash, chunk_key, s.verify_model, s.openai_base_url,
        s.openai_api_key, s.verify_max_tokens, user, s.reasoning_effort or "",
    )
    data = _parse(raw)
    if not data:
        return VerifyResult(faithfulness=1.0, used_llm=False)

    try:
        faith = float(data.get("faithfulness"))
    except (TypeError, ValueError):
        faith = 1.0
    faith = max(0.0, min(1.0, faith))

    unsupported = data.get("unsupported_claims") or []
    if not isinstance(unsupported, list):
        unsupported = []
    unsupported = [str(c) for c in unsupported if c]

    log.info("verify.done", faithfulness=round(faith, 3),
             unsupported=len(unsupported), query=query[:80])

    return VerifyResult(faithfulness=faith, unsupported_claims=unsupported,
                        used_llm=True)
