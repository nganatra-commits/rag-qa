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
    ) -> list[RetrievalHit]:
        s = self._settings
        top_k = top_k or s.top_k
        rerank_top_k = rerank_top_k or s.rerank_top_k
        alpha = s.hybrid_alpha if alpha is None else alpha

        log.info("retrieve.start", query=query[:120], top_k=top_k,
                 rerank_top_k=rerank_top_k, alpha=alpha,
                 doc_filter=doc_filter or "ALL")

        dense_q = self._dense.embed_query(query)
        sparse_q = self._sparse.encode_query(query)
        d_scaled, s_scaled = hybrid_scale(dense_q, sparse_q, alpha)

        candidates = self._store.query_hybrid(
            dense_vec=d_scaled,
            sparse_vec=s_scaled,
            top_k=top_k,
            doc_filter=doc_filter,
        )
        log.info("retrieve.candidates", n=len(candidates))

        if self._reranker is None or not candidates:
            return candidates[:rerank_top_k]

        reranked = self._reranker.rerank(query, candidates, top_k=rerank_top_k)
        log.info("retrieve.reranked", n=len(reranked))
        return reranked
