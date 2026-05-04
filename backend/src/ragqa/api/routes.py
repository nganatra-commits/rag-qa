"""HTTP routes: /health, /retrieve, /answer, /feedback, /api/images/{id}."""
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
    FeedbackRequest,
    HealthResponse,
    RetrieveRequest,
    RetrieveResponse,
)
from ragqa.config import Settings, get_settings
from ragqa.core.logging import get_logger
from ragqa.generation.llm import MultimodalAnswerer
from ragqa.retrieval.hybrid import HybridRetriever
from ragqa.retrieval.vectorstore import PineconeVectorStore

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

    hits = retriever.retrieve(
        query=body.query,
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

    result = answerer.answer(query=body.query, hits=hits)

    citations = [
        AnswerCitation(
            chunk_id=h.chunk.chunk_id,
            doc_id=h.chunk.doc_id,
            section_path=h.chunk.section_path,
            page_start=h.chunk.page_start,
            page_end=h.chunk.page_end,
        )
        for h in hits
    ]
    images = [
        AnswerImage(
            image_id=img.image_id,
            cdn_url=img.cdn_url or f"/api/images/{img.image_id}",
            page=img.page,
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


def _is_safe_id(image_id: str) -> bool:
    return all(c.isalnum() or c in ("_", "-") for c in image_id) and len(image_id) <= 200
