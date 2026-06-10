"""Offline eval harness for the RAG pipeline.

Loads `backend/eval/qa_eval.jsonl`, runs each question end-to-end through the
in-process pipeline (retrieve → understand+rerank+gate → answer → verify),
and computes:

  - gate_correctness : did the gate behavior match `expected_behavior`?
  - phrase_recall    : fraction of expected_phrases present in the answer
  - faithfulness     : verifier score (only for answered cases)
  - pass             : gate_correct AND phrase_recall ≥ 0.5 AND
                       (expected_behavior != "answer" OR faithfulness ≥ 0.7)

Writes a JSON report to `backend/eval/eval-report-<sha>-<timestamp>.json`
and prints a one-line summary to stdout. Designed to run with no HTTP layer
so it can exercise the whole pipeline without spinning up uvicorn.

Usage:
    cd backend
    ./.venv/Scripts/python scripts/run_eval.py
    # or: make eval
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

# Allow running as a plain script (no `python -m`) from backend/.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))

from ragqa.api.deps import _build_components  # noqa: E402
from ragqa.config import get_settings  # noqa: E402
from ragqa.core.logging import get_logger  # noqa: E402
from ragqa.generation.verify import verify_answer  # noqa: E402
from ragqa.retrieval.query_rewriter import expand_query  # noqa: E402
from ragqa.retrieval.understand import understand_and_rerank  # noqa: E402

log = get_logger("eval")


_AKS_RE = re.compile(
    r"\b(aks|operator\s+dashboard|dashboard\s+designer|dashboard\s+alarm)s?\b",
    re.IGNORECASE,
)
_NOT_FOUND_RE = re.compile(
    r"i\s+(?:could\s+not|cannot|can't)\s+find", re.IGNORECASE,
)


def _classify_outcome(answer: str, intent: str | None,
                      answerable: float | None,
                      settings: Any) -> str:
    """Map the live response to one of: answer / aks_refuse / gate_refuse."""
    if not answer:
        return "gate_refuse"
    head = answer.strip().splitlines()[0]
    if _AKS_RE.search(head):
        # The AKS refusal text begins with the AKS topic name.
        return "aks_refuse"
    if _NOT_FOUND_RE.search(head):
        return "gate_refuse"
    return "answer"


def _phrase_recall(answer: str, phrases: list[str]) -> float:
    if not phrases:
        return 1.0
    al = (answer or "").lower()
    hits = sum(1 for p in phrases if p.lower() in al)
    return hits / len(phrases)


def _run_one(rec: dict[str, Any], retriever, answerer, settings) -> dict[str, Any]:
    q = rec["question"]
    expected = rec.get("expected_behavior", "answer")
    expected_phrases = rec.get("expected_phrases", []) or []

    t0 = time.perf_counter()
    # Mirror the live /answer path: AKS gate is a regex pre-retrieval check,
    # so we replicate it inline rather than importing from routes (which
    # depends on FastAPI). The eval is about pipeline behavior; matching the
    # live gate exactly is what matters.
    if re.search(
        r"\baks\b|operator\s+dashboard|dashboard\s+designer|dashboard\s+alarm",
        q, re.IGNORECASE,
    ):
        return {
            "id":              rec["id"],
            "question":        q,
            "expected":        expected,
            "actual":          "aks_refuse",
            "gate_correct":    expected == "aks_refuse",
            "phrase_recall":   1.0,
            "faithfulness":    1.0,
            "answer_excerpt":  "(AKS refusal)",
            "latency_ms":      int((time.perf_counter() - t0) * 1000),
            "pass":            expected == "aks_refuse",
        }

    # Retrieval (+ multi-query expansion)
    expanded = expand_query(q)
    hits = retriever.retrieve(
        query=q,
        rerank_top_k=max(settings.understand_candidate_k, 5),
        expanded_queries=expanded,
    )

    # Understand + rerank + gate
    intent: str | None = None
    answerable: float | None = None
    if settings.understand_enabled and hits:
        u = understand_and_rerank(q, hits, settings)
        hits = u.reranked_hits
        intent, answerable = u.intent, u.answerable
        if u.used_llm and (
            u.intent == "out_of_scope"
            or u.answerable < settings.answerability_threshold
        ):
            phrase_recall = _phrase_recall("", expected_phrases) if expected != "answer" else 0.0
            return {
                "id":             rec["id"],
                "question":       q,
                "expected":       expected,
                "actual":         "gate_refuse",
                "gate_correct":   expected == "gate_refuse",
                "phrase_recall":  1.0 if expected == "gate_refuse" else 0.0,
                "faithfulness":   1.0,
                "intent":         intent,
                "answerable":     round(answerable, 3) if answerable is not None else None,
                "answer_excerpt": "(gate refusal)",
                "latency_ms":     int((time.perf_counter() - t0) * 1000),
                "pass":           expected == "gate_refuse",
            }

    if not hits:
        return {
            "id":             rec["id"],
            "question":       q,
            "expected":       expected,
            "actual":         "gate_refuse",
            "gate_correct":   expected == "gate_refuse",
            "phrase_recall":  1.0 if expected == "gate_refuse" else 0.0,
            "faithfulness":   1.0,
            "answer_excerpt": "(no hits)",
            "latency_ms":     int((time.perf_counter() - t0) * 1000),
            "pass":           expected == "gate_refuse",
        }

    # Answer + verify
    result = answerer.answer(query=q, hits=hits, history=[])
    v = verify_answer(query=q, answer=result.answer, hits=hits, settings=settings)
    actual = _classify_outcome(result.answer, intent, answerable, settings)
    phrase_recall = _phrase_recall(result.answer, expected_phrases)

    pass_ok = (
        actual == expected
        and phrase_recall >= 0.5
        and (expected != "answer" or v.faithfulness >= 0.7)
    )
    return {
        "id":             rec["id"],
        "question":       q,
        "expected":       expected,
        "actual":         actual,
        "gate_correct":   actual == expected,
        "phrase_recall":  round(phrase_recall, 3),
        "faithfulness":   round(v.faithfulness, 3),
        "intent":         intent,
        "answerable":     round(answerable, 3) if answerable is not None else None,
        "answer_excerpt": (result.answer or "")[:240],
        "latency_ms":     int((time.perf_counter() - t0) * 1000),
        "pass":           pass_ok,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="in_path", type=Path,
                   default=Path("eval/qa_eval.jsonl"))
    p.add_argument("--out", dest="out_path", type=Path, default=None,
                   help="Override report path (default: eval/eval-report-<sha>-<ts>.json)")
    p.add_argument("--ids", type=str, default="",
                   help="Comma-separated subset of question ids to run")
    args = p.parse_args()

    settings = get_settings()
    settings.ensure_dirs()
    comps = _build_components()
    retriever = comps["retriever"]
    answerer = comps["answerer"]

    records: list[dict[str, Any]] = []
    with args.in_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            records.append(json.loads(line))
    if args.ids:
        wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
        records = [r for r in records if r["id"] in wanted]

    results: list[dict[str, Any]] = []
    print(f"Running {len(records)} eval questions...")
    for rec in records:
        print(f"  [{rec['id']}] {rec['question'][:60]}", end=" ", flush=True)
        try:
            row = _run_one(rec, retriever, answerer, settings)
        except Exception as e:
            row = {
                "id": rec["id"], "question": rec["question"],
                "expected": rec.get("expected_behavior"),
                "actual": "error", "gate_correct": False,
                "phrase_recall": 0.0, "faithfulness": 0.0,
                "answer_excerpt": f"ERROR: {type(e).__name__}: {e}"[:240],
                "latency_ms": 0, "pass": False,
            }
        results.append(row)
        flag = "PASS" if row["pass"] else "FAIL"
        print(f"-> {flag} ({row['actual']}, faith={row.get('faithfulness', 0):.2f}, "
              f"phrase={row.get('phrase_recall', 0):.2f})")

    # Aggregates
    n = len(results)
    n_pass = sum(1 for r in results if r["pass"])
    n_gate = sum(1 for r in results if r["gate_correct"])
    answered = [r for r in results if r["expected"] == "answer"]
    faith_mean = (
        sum(r["faithfulness"] for r in answered) / max(len(answered), 1)
        if answered else 1.0
    )
    phrase_mean = (
        sum(r["phrase_recall"] for r in answered) / max(len(answered), 1)
        if answered else 1.0
    )

    sha = settings.build_git_sha or "dev"
    ts = int(time.time())
    out_path = args.out_path or (args.in_path.parent / f"eval-report-{sha}-{ts}.json")
    report = {
        "build_sha":      sha,
        "build_time":     settings.build_time,
        "llm_model":      settings.llm_model,
        "understand_model": settings.understand_model,
        "verify_model":   settings.verify_model,
        "bm25_enabled":   settings.bm25_enabled,
        "ran_at":         ts,
        "n_total":        n,
        "n_pass":         n_pass,
        "pass_rate":      round(n_pass / max(n, 1), 3),
        "gate_correct":   n_gate,
        "gate_accuracy":  round(n_gate / max(n, 1), 3),
        "faithfulness_mean": round(faith_mean, 3),
        "phrase_recall_mean": round(phrase_mean, 3),
        "results":        results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"\nEVAL SUMMARY: {n_pass}/{n} pass "
        f"({report['pass_rate']*100:.0f}%), "
        f"gate {report['gate_accuracy']*100:.0f}%, "
        f"faithfulness {faith_mean:.2f}, "
        f"phrase recall {phrase_mean:.2f}\n"
        f"Report: {out_path}"
    )
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    sys.exit(main())
