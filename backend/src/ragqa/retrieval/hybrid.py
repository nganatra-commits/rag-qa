"""Hybrid retrieval orchestrator.

Pipeline:
    query -> dense embed + sparse encode -> alpha-scale -> Pinecone hybrid query
          -> top-K candidates -> cross-encoder rerank -> top-N
"""
from __future__ import annotations

from ragqa.config import Settings
from ragqa.core.logging import get_logger
from ragqa.models.chunks import RetrievalHit
from ragqa.retrieval.embeddings import DenseEmbedder, SparseEncoder, hybrid_scale
from ragqa.retrieval.rerank import CrossEncoderReranker
from ragqa.retrieval.vectorstore import PineconeVectorStore

log = get_logger(__name__)


class HybridRetriever:
    def __init__(
        self,
        settings: Settings,
        store: PineconeVectorStore,
        dense: DenseEmbedder,
        sparse: SparseEncoder,
        reranker: CrossEncoderReranker | None,
    ):
        self._settings = settings
        self._store = store
        self._dense = dense
        self._sparse = sparse
        self._reranker = reranker

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        rerank_top_k: int | None = None,
        alpha: float | None = None,
        doc_filter: list[str] | None = None,
        expanded_queries: list[str] | None = None,
    ) -> list[RetrievalHit]:
        """Hybrid retrieve with optional multi-query expansion.

        When `expanded_queries` is provided (typically the original query
        plus 1–3 LLM-rewritten alternatives), each phrasing runs the
        full hybrid query → vector store path. Candidates are merged by
        chunk_id keeping the highest-scoring hit for each chunk. The
        reranker runs once at the end against the original query —
        rewrites only widen the *recall* net, not the relevance score.
        """
        s = self._settings
        top_k = top_k or s.top_k
        rerank_top_k = rerank_top_k or s.rerank_top_k
        alpha = s.hybrid_alpha if alpha is None else alpha

        # Build the search list. Always include the original verbatim.
        searches: list[str] = [query]
        if expanded_queries:
            seen = {query.casefold()}
            for q in expanded_queries:
                if q and q.casefold() not in seen:
                    seen.add(q.casefold())
                    searches.append(q)

        log.info("retrieve.start", query=query[:120], top_k=top_k,
                 rerank_top_k=rerank_top_k, alpha=alpha,
                 doc_filter=doc_filter or "ALL",
                 search_phrasings=len(searches))

        # Per-phrasing top_k can be smaller — we'll merge across phrasings
        # before reranking. Keep enough headroom for the reranker.
        per_query_k = top_k if len(searches) == 1 else max(top_k, 12)

        merged: dict[str, RetrievalHit] = {}
        for sq in searches:
            dense_q = self._dense.embed_query(sq)
            sparse_q = self._sparse.encode_query(sq)
            d_scaled, s_scaled = hybrid_scale(dense_q, sparse_q, alpha)
            candidates = self._store.query_hybrid(
                dense_vec=d_scaled,
                sparse_vec=s_scaled,
                top_k=per_query_k,
                doc_filter=doc_filter,
            )
            for c in candidates:
                key = c.chunk.chunk_id
                prev = merged.get(key)
                if prev is None or (c.score or 0.0) > (prev.score or 0.0):
                    merged[key] = c

        candidates = sorted(merged.values(),
                            key=lambda h: (h.score or 0.0),
                            reverse=True)[:max(top_k, per_query_k)]
        log.info("retrieve.candidates", n=len(candidates),
                 deduped_from_phrasings=len(searches))

        if self._reranker is None or not candidates:
            return candidates[:rerank_top_k]

        # Rerank against the ORIGINAL query — not the rewrites — so the
        # final score reflects relevance to what the user actually asked.
        reranked = self._reranker.rerank(query, candidates, top_k=rerank_top_k)
        log.info("retrieve.reranked", n=len(reranked))
        return reranked
