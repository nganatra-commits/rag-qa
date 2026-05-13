"""Query rewriting / multi-query expansion before retrieval.

The deployed bot has a vocabulary-sensitivity bug: when a user asks
"How do I show DATE on my charts?" the literal phrase matches the
**Breakdown** dialog (which talks about date breakdown options) instead
of the actual answer (File Parameters > Description Variables on the
x-axis). The right chunks just don't surface highly enough for the user's
wording.

Fix: ask a fast LLM to expand the user's query into 2–3 alternative
phrasings that use *manual vocabulary* ("x-axis description variables",
"file parameters dialog", etc). The retriever then embeds each phrasing
separately, dedupes hits at the chunk level, and takes the best score
across all phrasings.

Cost guard: results are LRU-cached by the literal query so repeat
questions don't re-spend.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from ragqa.config import Settings, get_settings
from ragqa.core.logging import get_logger
from ragqa.core.openai_http import OpenAIClient, OpenAIError

log = get_logger(__name__)


_REWRITE_SYSTEM_PROMPT = """\
You are a query-rewrite helper for a RAG system grounded in the NWA
Quality Analyst 8 manuals (User's Manual, Tutorials, Installation Guide).

Given a user's question, produce 2–3 alternative search phrasings that
would retrieve the right manual section. The user often uses everyday
English ("show DATE on my charts") but the manual uses specific UI labels
("Description Variables", "X-Axis Description Variables", "File Parameters
dialog", "Run files", "Multivariate Parameters form"). Your job is to
bridge that vocabulary gap.

Rules:
- Output a JSON object with a single key "queries" containing a list of
  2 to 3 alternative phrasings as strings.
- Each phrasing should be 4–10 words. Use manual vocabulary when likely,
  technical SPC vocabulary when relevant.
- Do not repeat the original query verbatim.
- Do not invent product names. Stick to NWA QA / Quality Analyst.
- Do not add commentary outside the JSON.

**Special-case vocabulary you should reach for** (these surface chunks
the literal user query often misses):

- For "X is not updating / not refreshing / I don't see new data" type
  troubleshooting questions on charts or connected Data Sets, include
  one phrasing about diagnostics: "QAConnectivity.log database
  connection diagnostic", "Data > Query Database refresh connected Data
  Set", "Variable Parameters Data Ranges analysis row range".

- For "out-of-control" + "acknowledge" / "annotate" type questions,
  include: "Assignable Cause Corrective Action ACCA right-click data
  point", "Enter/Edit Cause and Action plotting symbol".

- For "ACCA list not visible" / "hyperlink to QA file ACCA" questions,
  include: "ACCA category configured Settings variable assignment key
  columns", "KnowledgeBase Acknowledgements destination Data Set".

- For "tag" / "exclude one point" / "remove out-of-control point from
  limit calculation" questions, include: "Tag Data Ctrl+T Edit menu
  individual cell", "Tagged Data Handling Treat as Missing Plot but
  Don't Analyze".

- For "automate" / "schedule" / "batch" type questions, include:
  "Run file commands script automation", "Task Scheduler run file
  Windows".

- For "data from Excel" / "import XLSX" / "load spreadsheet" /
  "load CSV" / "Excel into QA" type questions, surface BOTH the
  preferred connected path AND the CSV fallback: "Create Connected
  Data Set External Database Setup Excel ODBC", "Import CSV
  Utilities menu Editor", "Connection list Access Excel ODBC driver".

- For "why doesn't sigma / 3-sigma match standard deviation" /
  "standard deviation different on chart vs stats" questions,
  include: "control chart sigma calculation method average subgroup
  range AIAG", "Capability Parameters Standard Deviation Calculation
  Method Sample N-1", "within-subgroup vs between-subgroup
  variation".

- For "histogram statistics missing" / "histogram doesn't show
  stat X" / "histogram doesn't calculate" questions, surface BOTH
  the per-variable and global surfaces: "Histogram Statistics tab
  Process Capability Histogram Parameters footer per-variable",
  "Settings histogram default statistics global Maximum Footer
  Lines".

Examples:

User: "How do I show DATE on my charts?"
{"queries": ["x-axis description variables date", "configure DATE label on chart x-axis", "File Parameters Description Variables DATE"]}

User: "How do I automate charting?"
{"queries": ["Run files automate chart generation", "create charts via Run file script", "Run file commands for charts"]}

User: "How do I get data from Excel into QA and create a chart?"
{"queries": ["Create Connected Data Set External Database Setup Excel ODBC", "Import CSV Utilities Editor Quality Analyst", "connect to Excel spreadsheet Quality Analyst chart"]}

User: "Why doesn't 3 sigma match Standard deviation?"
{"queries": ["control chart sigma average subgroup range AIAG", "Capability Parameters Standard Deviation Calculation Method", "within-subgroup vs between-subgroup variation N-1"]}

User: "The histogram doesn't calculate some statistics I want"
{"queries": ["Process Capability Histogram Parameters Histogram Statistics tab footer", "Settings histogram default statistics global Maximum Footer Lines", "per-variable histogram statistics selection"]}

User: "remove out-of-control point from limit calculation"
{"queries": ["tag data point exclude from limit calculation", "Tagged Data Handling treat as missing", "Ctrl+T tag data exclude analysis"]}

User: "Why is my dashboard not refreshing?"
{"queries": ["Query Database refresh connected Data Set", "QAConnectivity.log database connection diagnostic", "regenerate chart Graphics Viewer Variable tab"]}

User: "I used the hyperlink in the chart but I don't see my ACCA list"
{"queries": ["ACCA category Settings variable assignment hyperlink", "KnowledgeBase Acknowledgements destination Data Set", "key columns assignable cause persist"]}

User: "Why out-of-control still showing as not acknowledged?"
{"queries": ["Enter/Edit Cause and Action right-click data point", "Assignable Cause Corrective Action ACCA assign", "plotting symbol annotated point"]}
"""


@lru_cache(maxsize=1024)
def _cached_rewrite(query: str, model: str, base_url: str | None,
                    api_key: str) -> tuple[str, ...]:
    """Internal cache. Cache key includes model + endpoint so we don't
    serve stale results when those change. api_key is part of the key
    only because lru_cache needs hashable args; not used for security."""
    client = OpenAIClient(api_key=api_key, base_url=base_url, timeout=15.0)
    try:
        resp = client.chat_completion(
            model=model,
            messages=[
                {"role": "system", "content": _REWRITE_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            max_tokens=200,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        log.warning("query_rewrite.openai_error", err=str(e), query=query[:80])
        return ()
    except Exception as e:
        log.warning("query_rewrite.unexpected", err=repr(e), query=query[:80])
        return ()

    text = ""
    choices = resp.get("choices") or []
    if choices:
        text = (choices[0].get("message", {}).get("content") or "").strip()
    queries = _parse_queries(text)
    return tuple(queries)


def _parse_queries(text: str) -> list[str]:
    """Extract the queries list from the LLM's JSON response. Defensive
    against minor formatting drift (stray code fences, leading prose)."""
    if not text:
        return []
    # Strip code fences if the model produced them despite response_format.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract a {...} blob.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    raw = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for q in raw:
        if isinstance(q, str):
            q = q.strip()
            if 1 <= len(q) <= 200:
                out.append(q)
        if len(out) >= 3:
            break
    return out


def expand_query(query: str, settings: Settings | None = None) -> list[str]:
    """Return the original query plus up to 3 alternative phrasings.

    Always includes the original at index 0 — even if the LLM call fails
    we degrade gracefully to the single-query path. Caller is responsible
    for embedding each, retrieving, and merging.
    """
    s = settings or get_settings()
    if not s.query_rewrite_enabled:
        return [query]
    q = (query or "").strip()
    if not q:
        return [q]
    try:
        alts = _cached_rewrite(q, s.query_rewrite_model, s.openai_base_url,
                               s.openai_api_key)
    except Exception as e:
        log.warning("query_rewrite.cache_miss_fail", err=repr(e), query=q[:80])
        return [q]

    # Drop alternatives that are case-insensitive duplicates of the original.
    norm_q = q.casefold()
    seen: set[str] = {norm_q}
    out: list[str] = [q]
    for a in alts:
        n = a.casefold()
        if n not in seen:
            seen.add(n)
            out.append(a)
    log.info("query_rewrite.expanded", original=q[:80],
             alts=len(out) - 1, total=len(out))
    return out
