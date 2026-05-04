"""Cross-encoder reranker (mxbai-rerank-large-v2 by default).

Takes the top-K candidates from hybrid search and re-orders by a fresh
query-document scoring pass. Lifts NDCG@5 ~10-15pts on documentation corpora.
"""
from __future__ import annotations

from ragqa.core.logging import get_logger
from ragqa.models.chunks import RetrievalHit

log = get_logger(__name__)


class CrossEncoderReranker:
    def __init__(self, model_name: str, device: str | None = None,
                 max_length: int = 512):
        self.model_name = model_name
        self._device = device
        self._max_length = max_length
        self._model = None

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder
            log.info("reranker.load", model=self.model_name)
            self._model = CrossEncoder(self.model_name, device=self._device,
                                       max_length=self._max_length)
        return self._model

    def rerank(self, query: str, hits: list[RetrievalHit],
               top_k: int) -> list[RetrievalHit]:
        if not hits:
            return hits
        m = self._ensure()
        pairs = [(query, h.chunk.text[:4000]) for h in hits]
        scores = m.predict(pairs, show_progress_bar=False)
        for h, s in zip(hits, scores):
            h.rerank_score = float(s)
        hits.sort(key=lambda h: (h.rerank_score or 0.0), reverse=True)
        for rank, h in enumerate(hits[:top_k], start=1):
            h.rank = rank
        return hits[:top_k]
