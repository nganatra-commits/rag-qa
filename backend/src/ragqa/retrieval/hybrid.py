"""Hybrid retrieval orchestrator.

Pipeline:
    query -> dense embed per expanded query  ──┐
                                               ├─→ RRF fusion ──→ top-N
            BM25 over original query  ─────────┘
                              ↓
             optional cross-encoder rerank → top-N

We keep the legacy `SparseEncoder` / `hybrid_scale` plumbing in place because
the Pinecone vector store API expects (dense_vec, sparse_vec) tuples, but the
sparse channel is a no-op stub (NLTK hangs on Windows). The active sparse
contribution comes from the in-process `Bm25Index`, fused with the per-query
dense lists via Reciprocal Rank Fusion (k=60, standard literature value).
"""
from __future__ import annotations

from ragqa.config import Settings
from ragqa.core.logging import get_logger
from ragqa.models.chunks import RetrievalHit
from ragqa.retrieval.bm25 import Bm25Index
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
        bm25: Bm25Index | None = None,
    ):
        self._settings = settings
        self._store = store
        self._dense = dense
        self._sparse = sparse
        self._reranker = reranker
        self._bm25 = bm25

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        rerank_top_k: int | None = None,
        alpha: float | None = None,
        doc_filter: list[str] | None = None,
        expanded_queries: list[str] | None = None,
    ) -> list[RetrievalHit]:
        """Hybrid retrieve with multi-query expansion + BM25 + RRF fusion."""
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
                 search_phrasings=len(searches),
                 bm25=bool(self._bm25))

        # Per-phrasing top_k: enough headroom for the reranker after fusion.
        per_query_k = top_k if len(searches) == 1 else max(top_k, 12)

        # Each entry is an ordered list of chunk_ids in rank order (rank 1 first).
        ranked_lists: list[list[str]] = []
        # Cache of any RetrievalHit we've seen — we'll prefer dense hits for
        # final hydration since they carry Pinecone scores + the right
        # `Chunk` payload as returned by the store.
        hit_cache: dict[str, RetrievalHit] = {}

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
            order: list[str] = []
            for c in candidates:
                cid = c.chunk.chunk_id
                order.append(cid)
                # First sighting wins for hit_cache: Pinecone-side ordering is
                # already the best available dense score for that chunk.
                hit_cache.setdefault(cid, c)
            ranked_lists.append(order)

        bm25_order: list[str] = []
        if self._bm25 is not None:
            bm25_hits = self._bm25.search(query, top_k=s.bm25_top_k)
            for cid, _ in bm25_hits:
                bm25_order.append(cid)
                if cid not in hit_cache:
                    chunk = self._bm25.get_chunk(cid)
                    if chunk is not None:
                        # BM25-only winner: hydrate a RetrievalHit with score=0.0
                        # so the LLM-rerank stage doesn't get a noisy raw signal.
                        # The fused RRF score is what we sort by below.
                        hit_cache[cid] = RetrievalHit(chunk=chunk, score=0.0, rank=0)
            if bm25_order:
                ranked_lists.append(bm25_order)

        # Reciprocal Rank Fusion. score(d) = Σ 1 / (k + rank_i(d))
        rrf_k = s.rrf_k
        fused: dict[str, float] = {}
        for order in ranked_lists:
            for rank, cid in enumerate(order, start=1):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (rrf_k + rank)

        if not fused:
            log.info("retrieve.candidates", n=0, deduped_from_phrasings=len(searches))
            return []

        # Sort by RRF score desc, take a candidate window large enough to feed
        # the reranker / understand stage.
        keep_n = max(top_k, per_query_k, rerank_top_k)
        fused_ids = sorted(fused.keys(), key=lambda c: fused[c], reverse=True)[:keep_n]

        # Hydrate to RetrievalHit, overwriting `score` with the fused RRF score
        # so the downstream understand stage sees a comparable scalar.
        candidates: list[RetrievalHit] = []
        for cid in fused_ids:
            hit = hit_cache[cid]
            candidates.append(hit.model_copy(update={"score": fused[cid]}))

        log.info("retrieve.candidates", n=len(candidates),
                 deduped_from_phrasings=len(searches),
                 bm25_top=len(bm25_order))

        if self._reranker is None or not candidates:
            return candidates[:rerank_top_k]

        # Rerank against the ORIGINAL query — not the rewrites — so the
        # final score reflects relevance to what the user actually asked.
        reranked = self._reranker.rerank(query, candidates, top_k=rerank_top_k)
        log.info("retrieve.reranked", n=len(reranked))
        return reranked
