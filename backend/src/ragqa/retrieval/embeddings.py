"""Dense + sparse encoders for sparse-dense hybrid search on Pinecone.

Dense:  OpenAI text-embedding-3-large (3072-dim) via the OpenAI API.
        We avoid sentence-transformers to dodge the torch dependency entirely
        - much smaller footprint, no GPU drivers, no Windows DLL hangs.
Sparse: BM25 from pinecone-text. Fitted on the corpus during ingestion;
        the fitted encoder is persisted under data/cache/sparse_bm25.json
        so the API process can load and use it for query encoding.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ragqa.core.logging import get_logger
from ragqa.core.openai_http import OpenAIClient

log = get_logger(__name__)


# Dimensionality of each supported model.
_DIM_BY_MODEL: dict[str, int] = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
}


class DenseEmbedder:
    """Wrap OpenAI embeddings with batched encoding."""

    def __init__(
        self,
        model_name: str = "text-embedding-3-large",
        api_key: str | None = None,
        base_url: str | None = None,
        batch_size: int = 96,
    ):
        self.model_name = model_name
        self._batch_size = batch_size
        if api_key is None:
            raise ValueError("DenseEmbedder requires api_key")
        self._client = OpenAIClient(api_key=api_key, base_url=base_url)
        if model_name not in _DIM_BY_MODEL:
            log.warning("embedder.unknown_model_dim",
                        model=model_name,
                        note="dim() will probe via 1-sample call")

    @property
    def dim(self) -> int:
        if self.model_name in _DIM_BY_MODEL:
            return _DIM_BY_MODEL[self.model_name]
        # Probe
        v = self._encode_batch(["x"])
        return int(v.shape[1])

    def embed_passages(self, texts: list[str], batch_size: int | None = None) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        bsz = batch_size or self._batch_size
        out: list[np.ndarray] = []
        for i in range(0, len(texts), bsz):
            chunk = texts[i:i + bsz]
            out.append(self._encode_batch(chunk))
            if (i // bsz) % 10 == 0:
                log.info("embed.progress", done=i + len(chunk), total=len(texts))
        return np.vstack(out).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        v = self._encode_batch([text])
        return v[0]

    def _encode_batch(self, texts: list[str]) -> np.ndarray:
        # OpenAI rejects empty strings; replace with a space.
        cleaned = [t if t.strip() else " " for t in texts]
        embeddings = self._client.embeddings(model=self.model_name, inputs=cleaned)
        vecs = np.asarray(embeddings, dtype=np.float32)
        # OpenAI v3 embeddings are unit-norm by spec, but enforce defensively
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


class SparseEncoder:
    """No-op sparse encoder for v1 (dense-only Pinecone).

    pinecone-text's BM25Encoder pulls in NLTK which has a Windows import hang.
    Dense-only retrieval with text-embedding-3-large is plenty for our corpus
    size; we can wire in a custom BM25 (no NLTK) later if needed.
    """

    def __init__(self, cache_path: Path):
        self._cache_path = cache_path
        self.enabled = False

    def fit(self, corpus_texts: list[str]) -> None:
        log.info("sparse.skipped", reason="dense-only retrieval (v1)",
                 corpus_size=len(corpus_texts))

    def load(self) -> None:
        return

    def encode_documents(self, texts: list[str]) -> list[dict]:
        return [{"indices": [], "values": []} for _ in texts]

    def encode_query(self, text: str) -> dict:
        return {"indices": [], "values": []}


def hybrid_scale(
    dense_vec: np.ndarray,
    sparse_vec: dict,
    alpha: float,
) -> tuple[np.ndarray, dict]:
    """Pinecone's documented sparse-dense hybrid scaling."""
    if not 0 <= alpha <= 1:
        raise ValueError("alpha must be in [0, 1]")
    hd = dense_vec * alpha
    hs = {
        "indices": sparse_vec["indices"],
        "values": [v * (1 - alpha) for v in sparse_vec["values"]],
    }
    return hd, hs
