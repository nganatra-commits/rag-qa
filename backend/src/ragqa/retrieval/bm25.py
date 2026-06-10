"""In-memory BM25 sparse index over the chunk corpus.

The legacy Pinecone-side sparse channel (`SparseEncoder.encode_query`) is a
no-op because the BM25 encoder in `pinecone-text` pulls NLTK, which hangs
during import on Windows. Rather than re-enable that path (which would also
require re-ingesting all chunks with sparse vectors), we maintain a lightweight
BM25 index in-process: load `chunks_v<ns>.jsonl` once at startup, tokenize each
chunk's text with a simple regex tokenizer, build a `BM25Okapi` instance, and
expose a `search(query, top_k)` method. Memory cost for 451 chunks is a few MB.

The caller (`HybridRetriever`) fuses BM25-ranked chunk_ids with the per-query
dense ranked lists via Reciprocal Rank Fusion.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from rank_bm25 import BM25Okapi

from ragqa.core.logging import get_logger
from ragqa.models.chunks import Chunk

log = get_logger(__name__)


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + alphanumeric-word tokenizer.

    Keeps short tokens (a 2-char command name like `XR` matters), keeps
    digits (page numbers / type codes), drops everything else. No stemming
    or stopword removal: the QA manuals are command-heavy and stopwords
    occasionally carry signal (e.g., "open data file" vs "open file").
    """
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class Bm25Index:
    """BM25 over the chunk corpus, with chunk metadata retained for hydration."""

    def __init__(self, chunks: list[Chunk]):
        self._chunks_by_id: dict[str, Chunk] = {c.chunk_id: c for c in chunks}
        self._chunk_ids: list[str] = [c.chunk_id for c in chunks]
        tokenized: list[list[str]] = [_tokenize(c.text) for c in chunks]
        # rank_bm25 raises ZeroDivisionError on an empty corpus — guard
        # against that explicitly so a partially-baked dev environment fails
        # loud at startup instead of mysteriously later.
        if not tokenized:
            raise ValueError("Bm25Index: empty chunk corpus")
        self._bm25 = BM25Okapi(tokenized)
        log.info("bm25.index.built", chunks=len(chunks),
                 avg_tokens=sum(len(t) for t in tokenized) // max(len(tokenized), 1))

    @classmethod
    def from_jsonl(cls, path: Path) -> "Bm25Index":
        if not path.exists():
            raise FileNotFoundError(f"chunks JSONL not found at {path}")
        chunks: list[Chunk] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                chunks.append(Chunk.model_validate_json(line))
        return cls(chunks)

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """Return [(chunk_id, score)] in descending score order, capped at top_k.

        Chunks with a 0 score are dropped so they cannot contribute to RRF.
        """
        toks = _tokenize(query)
        if not toks:
            return []
        scores = self._bm25.get_scores(toks)
        # argpartition would be faster but the corpus is tiny (~451 docs) and
        # the readability of a single sorted() pass is worth more here.
        ranked = sorted(
            ((self._chunk_ids[i], float(scores[i])) for i in range(len(scores))),
            key=lambda x: x[1],
            reverse=True,
        )
        return [r for r in ranked[:top_k] if r[1] > 0.0]

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self._chunks_by_id.get(chunk_id)

    def __len__(self) -> int:
        return len(self._chunk_ids)

    @property
    def chunk_ids(self) -> Iterable[str]:
        return self._chunk_ids
