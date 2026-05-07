"""HTTP routes: /health, /retrieve, /answer, /feedback, /api/images/{id}, /api/pdfs/{id}, /api/chats."""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse

from ragqa import __version__
from ragqa.api.deps import (
    get_answerer,
    get_blank_ids,
    get_retriever,
    get_store,
    require_api_key,
)
from ragqa.api.schemas import (
    AnswerCitation,
    AnswerImage,
    AnswerRequest,
    AnswerResponse,
    ChatPutRequest,
    ChatRecord,
    ChatSummary,
    FeedbackRequest,
    HealthResponse,
    HistoryTurn,
    RetrieveRequest,
    RetrieveResponse,
)
from ragqa.config import Settings, get_settings
from ragqa.core.logging import get_logger
from ragqa.generation.llm import MultimodalAnswerer
from ragqa.retrieval.hybrid import HybridRetriever
from ragqa.retrieval.vectorstore import PineconeVectorStore
from ragqa.storage import ChatStoreUnavailable, get_chat_store

log = get_logger(__name__)
router = APIRouter()


# Captions like "solid black square", "blank rectangle", "no visible content"
# describe images that are useless to a user. The pixel-std-dev scan catches
# most, but a few escape (mostly-black with a thin colored border, etc.). The
# captioner already labelled them honestly - re-use that signal.
import re as _re
from ragqa.models.chunks import ImageRef as _ImageRef

_BLANK_CAPTION_RE = _re.compile(
    r"\b(solid (?:black|white)|"
    r"black (?:square|rectangle|box|image)|"
    r"white (?:square|rectangle|box|image)|"
    r"empty (?:image|frame|placeholder)|"
    r"blank (?:image|space|placeholder)|"
    r"no (?:visible content|readable text|content)|"
    r"placeholder|"
    r"image is (?:black|blank|empty))\b",
    _re.IGNORECASE,
)


def _is_blank_caption(img: _ImageRef) -> bool:
    blob = " ".join((img.caption or "", img.alt_text or "")).strip()
    if not blob:
        return False
    return bool(_BLANK_CAPTION_RE.search(blob))


# Tiny PNGs are almost always toolbar icons / decorative bullets / cursor
# graphics, not real dialog screenshots. They confuse the LLM into inlining
# a "floppy-disk emoji" for the Save step. Real screenshots are >= ~3 KB.
_MIN_IMAGE_BYTES = 3 * 1024


def _is_tiny_icon(img: _ImageRef) -> bool:
    try:
        from pathlib import Path as _Path
        p = _Path(img.uri)
        return p.exists() and p.stat().st_size < _MIN_IMAGE_BYTES
    except Exception:
        return False


# Captioner failures often store raw truncated JSON as the alt_text. Fix at
# response time (clean_captions.py only fixes the cache for past failures).
_JSON_LEAK_RE = _re.compile(r'^\s*[{\[]|^\s*"(alt_text|ocr_text|caption)"\s*:')


def _repair_json_leak(img: _ImageRef) -> None:
    """If alt_text/caption look like raw JSON, try to extract the real fields."""
    for source_field in ("alt_text", "caption"):
        s = getattr(img, source_field, "") or ""
        if not _JSON_LEAK_RE.match(s):
            continue
        # try to pull "alt_text": "...", "caption": "..." out of the string
        m_alt = _re.search(r'"alt_text"\s*:\s*"((?:\\.|[^"\\])*)"', s, _re.DOTALL)
        m_cap = _re.search(r'"caption"\s*:\s*"((?:\\.|[^"\\])*)"', s, _re.DOTALL)
        m_ocr = _re.search(r'"ocr_text"\s*:\s*"((?:\\.|[^"\\])*)"', s, _re.DOTALL)
        try:
            import json as _json
            if m_alt:
                img.alt_text = _json.loads(f'"{m_alt.group(1)}"')
            if m_cap:
                img.caption = _json.loads(f'"{m_cap.group(1)}"')
            if m_ocr and not img.ocr_text:
                img.ocr_text = _json.loads(f'"{m_ocr.group(1)}"')
        except Exception:
            pass
        return  # only repair once


# Heuristic: when the model says "I could not find...", the related-screenshots
# tray (which shows top-K images regardless of relevance) is misleading.
# We surface a "is_refusal" flag so the frontend can suppress the tray.
_REFUSAL_RE = _re.compile(
    r"^\s*(?:i\s+(?:could\s+not|cannot|can't|could not)|"
    r"(?:unfortunately|sorry),?\s+i\s+(?:could not|cannot|can't))",
    _re.IGNORECASE,
)


def _looks_like_refusal(answer: str) -> bool:
    head = (answer or "").strip().splitlines()[0] if answer else ""
    return bool(_REFUSAL_RE.match(head))


@router.get("/health", response_model=HealthResponse, tags=["meta"])
def health(
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """Liveness + index visibility. No auth required."""
    indexed_chunks = 0
    indexed_vectors = 0
    try:
        store = get_store()
        store.ensure_index()
        stats = store.stats()
        # Pinecone SDK v8 uses camelCase ('vectorCount') in namespace stats; older
        # API versions used snake_case ('vector_count'). Try both.
        if hasattr(stats, "namespaces"):
            ns = stats.namespaces or {}
        else:
            ns = stats.get("namespaces", {}) if isinstance(stats, dict) else {}
        ns_stats = ns.get(settings.pinecone_namespace) if isinstance(ns, dict) else None
        if ns_stats is None:
            indexed_vectors = 0
        elif isinstance(ns_stats, dict):
            indexed_vectors = int(ns_stats.get("vectorCount") or ns_stats.get("vector_count") or 0)
        else:
            indexed_vectors = int(getattr(ns_stats, "vector_count", 0) or
                                  getattr(ns_stats, "vectorCount", 0) or 0)
        indexed_chunks = indexed_vectors  # 1:1 in our schema
    except Exception as e:
        log.warning("health.index.unavailable", err=repr(e))

    return HealthResponse(
        status="ok",
        version=__version__,
        index=settings.pinecone_index,
        namespace=settings.pinecone_namespace,
        indexed_chunks=indexed_chunks,
        indexed_vectors=indexed_vectors,
    )


@router.post("/retrieve", response_model=RetrieveResponse, tags=["rag"],
             dependencies=[Depends(require_api_key)])
def retrieve(
    body: RetrieveRequest,
    retriever: HybridRetriever = Depends(get_retriever),
) -> RetrieveResponse:
    t0 = time.perf_counter()
    hits = retriever.retrieve(
        query=body.query,
        top_k=body.top_k,
        rerank_top_k=body.rerank_top_k,
        alpha=body.alpha,
        doc_filter=body.doc_filter,
    )
    blanks = get_blank_ids()
    before = sum(len(h.chunk.images) for h in hits)
    for h in hits:
        # 1. drop blanks (pixel std-dev) and blank-captioned ones
        # 2. drop tiny icons (almost always toolbar decorations, not dialogs)
        # 3. repair raw-JSON caption leaks in survivors
        # 4. strip [FIGURE: id] markers from chunk text for any dropped image
        #    (otherwise the LLM still sees the marker and produces a citation
        #     for an image we filtered out -> frontend shows [missing image: ...])
        kept = []
        dropped_ids: set[str] = set()
        for im in h.chunk.images:
            if im.image_id in blanks or _is_blank_caption(im) or _is_tiny_icon(im):
                dropped_ids.add(im.image_id)
                continue
            _repair_json_leak(im)
            kept.append(im)
        h.chunk.images = kept
        if dropped_ids and h.chunk.text:
            # Remove any [FIGURE: <dropped-id>] occurrence (case-sensitive).
            # Also collapse a left-over double newline if the marker was alone
            # on a line.
            for did in dropped_ids:
                pattern = _re.compile(rf"\s*\[FIGURE:\s*{_re.escape(did)}\s*\][^\n]*\n?")
                h.chunk.text = pattern.sub("", h.chunk.text)
            h.chunk.text = _re.sub(r"\n{3,}", "\n\n", h.chunk.text)
    after = sum(len(h.chunk.images) for h in hits)
    if before != after:
        log.info("image_filter", before=before, after=after, removed=before - after)
    return RetrieveResponse(
        query=body.query,
        hits=hits,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


@router.post("/answer", response_model=AnswerResponse, tags=["rag"],
             dependencies=[Depends(require_api_key)])
def answer(
    body: AnswerRequest,
    retriever: HybridRetriever = Depends(get_retriever),
    answerer: MultimodalAnswerer = Depends(get_answerer),
) -> AnswerResponse:
    t0 = time.perf_counter()
    request_id = str(uuid.uuid4())

    # Corpus-coverage gate: AKS / Operator Dashboard topics aren't documented
    # in our manuals. Refuse cleanly before retrieval so the LLM is never
    # tempted to fabricate a workflow from adjacent chart-display features.
    if _looks_like_aks_topic(body.query):
        log.info("answer.refused.aks_topic", request_id=request_id, query=body.query[:120])
        return _aks_refusal_response(body.query, t0)

    # History relevance gate: drop prior turns when the new query is on a
    # different topic (token overlap below threshold AND no follow-up prefix).
    # When kept, also prepend the most recent user turn to the retrieval
    # query so embeddings see the full subject for follow-ups like
    # "what about step 3?".
    keep_history = _should_keep_history(body.query, body.history)
    retrieval_query = body.query
    if keep_history and body.history:
        prior_user = next(
            (h.content for h in reversed(body.history) if h.role == "user"),
            "",
        )
        if prior_user:
            retrieval_query = f"{prior_user.strip()}\n{body.query}"
    if not keep_history and body.history:
        log.info("answer.history.dropped", request_id=request_id,
                 history_turns=len(body.history), query=body.query[:120])

    hits = retriever.retrieve(
        query=retrieval_query,
        top_k=body.top_k,
        rerank_top_k=body.rerank_top_k,
        alpha=body.alpha,
        doc_filter=body.doc_filter,
    )
    # Strip blank/uniform images from each hit's chunk before they reach
    # the LLM and the response payload. Same Chunk objects come from the
    # Pinecone metadata round-trip, so editing here is local and safe.
    blanks = get_blank_ids()
    before = sum(len(h.chunk.images) for h in hits)
    for h in hits:
        # 1. drop blanks (pixel std-dev) and blank-captioned ones
        # 2. drop tiny icons (almost always toolbar decorations, not dialogs)
        # 3. repair raw-JSON caption leaks in survivors
        # 4. strip [FIGURE: id] markers from chunk text for any dropped image
        #    (otherwise the LLM still sees the marker and produces a citation
        #     for an image we filtered out -> frontend shows [missing image: ...])
        kept = []
        dropped_ids: set[str] = set()
        for im in h.chunk.images:
            if im.image_id in blanks or _is_blank_caption(im) or _is_tiny_icon(im):
                dropped_ids.add(im.image_id)
                continue
            _repair_json_leak(im)
            kept.append(im)
        h.chunk.images = kept
        if dropped_ids and h.chunk.text:
            # Remove any [FIGURE: <dropped-id>] occurrence (case-sensitive).
            # Also collapse a left-over double newline if the marker was alone
            # on a line.
            for did in dropped_ids:
                pattern = _re.compile(rf"\s*\[FIGURE:\s*{_re.escape(did)}\s*\][^\n]*\n?")
                h.chunk.text = pattern.sub("", h.chunk.text)
            h.chunk.text = _re.sub(r"\n{3,}", "\n\n", h.chunk.text)
    after = sum(len(h.chunk.images) for h in hits)
    if before != after:
        log.info("image_filter", before=before, after=after, removed=before - after)

    if not hits:
        return AnswerResponse(
            query=body.query,
            answer="I could not find anything in the manuals matching your question.",
            citations=[], images=[], referenced_image_ids=[], chunks=[],
            input_tokens=0, output_tokens=0,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    if body.max_images is not None:
        answerer._max_images = body.max_images  # noqa: SLF001 (knob override)

    history_pairs = (
        [(t.role, t.content) for t in (body.history or [])]
        if keep_history else []
    )
    result = answerer.answer(query=body.query, hits=hits, history=history_pairs)

    citations = [
        AnswerCitation(
            chunk_id=h.chunk.chunk_id,
            doc_id=h.chunk.doc_id,
            section_path=h.chunk.section_path,
            page_start=_printed_page(h.chunk.doc_id, h.chunk.page_start),
            page_end=_printed_page(h.chunk.doc_id, h.chunk.page_end),
        )
        for h in hits
    ]
    images = [
        AnswerImage(
            image_id=img.image_id,
            cdn_url=img.cdn_url or f"/api/images/{img.image_id}",
            page=_printed_page(h.chunk.doc_id, img.page),
            caption=img.caption,
            alt_text=img.alt_text,
            chunk_id=h.chunk.chunk_id,
            binding_method=img.binding_method.value,
            binding_score=img.binding_score,
        )
        for h in hits for img in h.chunk.images
    ]

    log.info("answer.served",
             request_id=request_id,
             query=body.query[:120],
             chunks=len(hits),
             images=len(images),
             referenced_images=result.cited_image_ids,
             input_tokens=result.input_tokens,
             output_tokens=result.output_tokens)

    return AnswerResponse(
        query=body.query,
        answer=result.answer,
        citations=citations,
        images=images,
        referenced_image_ids=result.cited_image_ids,
        chunks=[h.chunk for h in hits],
        is_refusal=_looks_like_refusal(result.answer),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


@router.post("/feedback", tags=["rag"],
             dependencies=[Depends(require_api_key)])
def feedback(body: FeedbackRequest) -> JSONResponse:
    log.info("feedback.received", request_id=body.request_id,
             rating=body.rating, note=body.note[:200])
    # TODO(prod): persist to a feedback store (Postgres, S3 jsonl, Langfuse)
    return JSONResponse({"ok": True})


@router.get("/api/images/{image_id}", tags=["images"])
def serve_image(
    image_id: str,
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Serve extracted page-image bytes by id.

    image_id encodes the doc_id prefix, so we look under data/images/{doc}/{id}.png.
    """
    if not _is_safe_id(image_id):
        raise HTTPException(status_code=400, detail="invalid image_id")
    doc_id = image_id.split("_", 1)[0]
    path = settings.images_dir / doc_id / f"{image_id}.png"
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


# Maps the lower-case doc_id used throughout the index to the actual
# filename of the cleaned PDF on disk. Kept in sync with scripts/ingest_pdfs.py.
_PDF_FILENAMES: dict[str, str] = {
    "qasetup": "QAsetup.cleaned.pdf",
    "qatutor": "QATutor.cleaned.pdf",
    "qaman":   "QAman.cleaned.pdf",
}


@router.get("/api/pdfs/{doc_id}", tags=["pdfs"])
def serve_pdf(
    doc_id: str,
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Serve the cleaned source PDF inline so citation links can deep-link
    to a specific page via #page=N in the browser PDF viewer."""
    if not _is_safe_id(doc_id):
        raise HTTPException(status_code=400, detail="invalid doc_id")
    filename = _PDF_FILENAMES.get(doc_id.lower())
    if filename is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown doc_id")
    path = settings.source_pdfs_dir / filename
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pdf not found")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Cache-Control": "public, max-age=3600",
            # inline so the browser's PDF viewer renders it (allows #page=N).
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


def _is_safe_id(image_id: str) -> bool:
    return all(c.isalnum() or c in ("_", "-") for c in image_id) and len(image_id) <= 200


# ---------------------------------------------------------------------------
# Corpus-coverage gate for AKS / Operator Dashboard topics.
#
# The QAman / QATutor / QASetup corpus contains zero documentation for
# Alarms, Operator Dashboard, Dashboard Designer, alarm acknowledgement, or
# "out of service" notifications — those belong to the NWA Analytics
# Knowledge Suite (AKS) module. Without an explicit gate the bot retrieves
# adjacent chart-display features (External Source Data Filters, Hide Points
# with Events, Default Chart Limits) and confidently fabricates a workflow.
# This gate short-circuits before retrieval/LLM and returns a clean refusal.
# ---------------------------------------------------------------------------

_AKS_REFUSAL = (
    "I couldn't find documentation for this in the NWA Quality Analyst "
    "manuals. Alarms, the Operator Dashboard, and Dashboard Designer are "
    "part of NWA Analytics Knowledge Suite (AKS), which uses separate "
    "documentation. If you're trying to do something in standalone Quality "
    "Analyst (charts, limits, ACCA), please rephrase. Otherwise, please "
    "consult AKS documentation or your administrator."
)

# Strict tokens — any hit refuses immediately. Word boundaries are mandatory
# so legitimate SPC vocabulary (e.g. "alarm limit") is not blocked unless
# the rest of the query is also AKS-flavored (handled by the soft tokens
# below).
_AKS_STRICT_RE = _re.compile(
    r"\b("
    r"operator\s+dashboard|"
    r"dashboard\s+designer|"
    r"dashboard\s+alarm[s]?|"
    r"alarm\s+priority|"
    r"alarm\s+history|"
    r"alarm\s+for\b|"          # "alarm for" — Dashboard Designer setting
    r"point\s+list|"
    r"shift\s+summary"
    r")\b",
    _re.IGNORECASE,
)

# Soft tokens — only refuse when a "dashboard" word appears together with an
# alarm/acknowledge concept, or when "out of service" appears next to an
# instrument/tag word. Keeps "alarm limit" SPC questions answering normally.
_AKS_DASH_RE = _re.compile(r"\bdashboard[s]?\b", _re.IGNORECASE)
_AKS_ALARM_RE = _re.compile(r"\balarm[s]?\b", _re.IGNORECASE)
_AKS_ACK_RE = _re.compile(r"\back(?:nowledge(?:d|ment)?|nowledg)?\b|\back\b", _re.IGNORECASE)
_AKS_OOS_RE = _re.compile(
    r"\bout\s+of\s+service\b.*?\b(instrument|tag|alarm|sensor)\b"
    r"|\b(instrument|tag|alarm|sensor)\b.*?\bout\s+of\s+service\b",
    _re.IGNORECASE | _re.DOTALL,
)


def _looks_like_aks_topic(query: str) -> bool:
    if _AKS_STRICT_RE.search(query):
        return True
    has_dash = bool(_AKS_DASH_RE.search(query))
    has_alarm = bool(_AKS_ALARM_RE.search(query))
    has_ack = bool(_AKS_ACK_RE.search(query))
    if has_dash and (has_alarm or has_ack):
        return True
    if _AKS_OOS_RE.search(query):
        return True
    return False


def _aks_refusal_response(query: str, started_at: float) -> AnswerResponse:
    return AnswerResponse(
        query=query,
        answer=_AKS_REFUSAL,
        citations=[], images=[], referenced_image_ids=[], chunks=[],
        is_refusal=True,
        input_tokens=0, output_tokens=0,
        latency_ms=int((time.perf_counter() - started_at) * 1000),
    )


# ---------------------------------------------------------------------------
# History relevance gate — drop prior turns when the new query is on a
# different topic. Uses a cheap Jaccard overlap between the current query
# tokens and the union of the last two user turns. Explicit follow-up
# starters bypass the gate so "what about step 3?" still works.
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "a an the and or but if then so to of in on at by for with about as is "
    "are was were be been being do does did how why what when where which "
    "who whom that this these those i you we they it me my your our their "
    "can could should would will may might shall must have has had not no "
    "yes from into out up down over under between during before after also "
    "any all some each every other another more most less few many much".split()
)
_FOLLOWUP_STARTERS = (
    "what about", "and ", "and?", "also ", "also,", "then ", "then,",
    "now ", "now,", "next ", "next,", "what's", "whats", "tell me more",
)
_HISTORY_KEEP_THRESHOLD = 0.20


def _tokenize_for_overlap(text: str) -> set[str]:
    """Lowercase, drop AKS markers + stopwords, simple suffix-strip."""
    s = text or ""
    s = _re.sub(r"\[FIGURE:\s*[A-Za-z0-9_\-]+\s*\]", "", s)
    s = _re.sub(r"\[\d+\]", "", s)
    raw = _re.findall(r"[A-Za-z][A-Za-z0-9_]+", s.lower())
    out: set[str] = set()
    for t in raw:
        if t in _STOPWORDS:
            continue
        # very simple stem so "charts" and "chart" overlap
        for suffix in ("ies", "es", "s", "ing", "ed"):
            if len(t) > len(suffix) + 2 and t.endswith(suffix):
                t = t[: -len(suffix)]
                break
        if len(t) >= 3:
            out.add(t)
    return out


def _should_keep_history(
    current_query: str,
    history: list[HistoryTurn] | None,
) -> bool:
    if not history:
        return False
    q_lower = (current_query or "").strip().lower()
    if any(q_lower.startswith(p) for p in _FOLLOWUP_STARTERS):
        return True
    cur = _tokenize_for_overlap(current_query)
    if not cur:
        return False
    prior_user_tokens: set[str] = set()
    seen = 0
    for turn in reversed(history):
        if turn.role != "user":
            continue
        prior_user_tokens |= _tokenize_for_overlap(turn.content)
        seen += 1
        if seen >= 2:
            break
    if not prior_user_tokens:
        return False
    overlap = len(cur & prior_user_tokens) / max(1, len(cur | prior_user_tokens))
    return overlap >= _HISTORY_KEEP_THRESHOLD


# ---------------------------------------------------------------------------
# Page-number offset between PyMuPDF/Docling 1-based PDF indices (what we
# store on chunks at ingestion time) and the printed page number shown in
# the manual's footer (what users actually see and search by). Verified by
# spot-checking footers across each PDF:
#
#   QAman.cleaned.pdf   PDF p.14  ↔ printed p.1     -> +13
#   QAtutor.cleaned.pdf PDF p.5   ↔ printed p.2     -> +3
#   QAsetup.cleaned.pdf PDF p.5   ↔ printed p.5     -> 0
#
# Constants verified at multiple positions in QAman (chapters 1/2/3/9 and
# Appendix I), so a single offset is safe as a stopgap. The proper fix is
# to extract the printed page number from each PDF page footer at ingestion
# time and store both pdf_page and printed_page on the chunk — tracked as a
# follow-up below.
# TODO: replace with per-page printed_page extracted at ingestion time.
# ---------------------------------------------------------------------------

_DOC_PAGE_OFFSET: dict[str, int] = {
    "qaman":   13,
    "qatutor": 3,
    "qasetup": 0,
}


def _printed_page(doc_id: str, raw: int) -> int:
    """Convert 1-based PDF index to printed manual page number."""
    return max(1, int(raw) - _DOC_PAGE_OFFSET.get((doc_id or "").lower(), 0))


# --- Chat history endpoints ----------------------------------------------

def _chat_store_or_503():
    try:
        return get_chat_store()
    except ChatStoreUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="chat history not configured",
        ) from e


@router.get("/api/chats", response_model=list[ChatSummary], tags=["chats"])
def list_chats(limit: int | None = None) -> list[ChatSummary]:
    store = _chat_store_or_503()
    rows = store.list_recent(limit=limit)
    return [ChatSummary(**r) for r in rows]


@router.get("/api/chats/{chat_id}", response_model=ChatRecord, tags=["chats"])
def get_chat(chat_id: str) -> ChatRecord:
    if not _is_safe_id(chat_id):
        raise HTTPException(status_code=400, detail="invalid chat id")
    store = _chat_store_or_503()
    item = store.get(chat_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chat not found")
    return ChatRecord(**item)


@router.put("/api/chats/{chat_id}", response_model=ChatRecord, tags=["chats"])
def upsert_chat(chat_id: str, body: ChatPutRequest) -> ChatRecord:
    if not _is_safe_id(chat_id):
        raise HTTPException(status_code=400, detail="invalid chat id")
    store = _chat_store_or_503()
    record = store.put({
        "id":         chat_id,
        "title":      body.title,
        "turns":      body.turns,
        "doc_filter": body.doc_filter,
        "created_at": body.created_at,
    })
    log.info("chats.upsert", chat_id=chat_id, turns=len(body.turns))
    return ChatRecord(**record)


@router.delete("/api/chats/{chat_id}", tags=["chats"])
def delete_chat(chat_id: str) -> JSONResponse:
    if not _is_safe_id(chat_id):
        raise HTTPException(status_code=400, detail="invalid chat id")
    store = _chat_store_or_503()
    store.delete(chat_id)
    log.info("chats.delete", chat_id=chat_id)
    return JSONResponse({"ok": True})
