"""Pinecone serverless vector store wrapper.

- Single index, multiple namespaces (one per ingestion version: v1, v2, ...).
- Sparse-dense hybrid index (metric=dotproduct).
- Per-vector metadata carries the full chunk JSON so retrieval is one round trip.
  (Pinecone allows up to 40KB metadata per vector; chunks are well under.)
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable

import numpy as np
from pinecone import Pinecone, ServerlessSpec
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ragqa.core.errors import IndexNotFoundError, RetrievalError
from ragqa.core.logging import get_logger
from ragqa.models.chunks import Chunk, ImageRef, RetrievalHit, BindingMethod

log = get_logger(__name__)


class PineconeVectorStore:
    def __init__(
        self,
        api_key: str,
        index_name: str,
        cloud: str,
        region: str,
        namespace: str,
        dimension: int,
        metric: str = "dotproduct",
    ):
        self._pc = Pinecone(api_key=api_key)
        self._index_name = index_name
        self._namespace = namespace
        self._dimension = dimension
        self._metric = metric
        self._cloud = cloud
        self._region = region
        self._index = None  # lazy

    # --- Index lifecycle ---

    def ensure_index(self) -> None:
        existing = {i["name"] for i in self._pc.list_indexes().get("indexes", [])}
        if self._index_name not in existing:
            log.info("pinecone.create_index", name=self._index_name,
                     cloud=self._cloud, region=self._region, metric=self._metric)
            self._pc.create_index(
                name=self._index_name,
                dimension=self._dimension,
                metric=self._metric,
                spec=ServerlessSpec(cloud=self._cloud, region=self._region),
            )
            # Wait for ready
            for _ in range(60):
                desc = self._pc.describe_index(self._index_name)
                if desc.get("status", {}).get("ready"):
                    break
                time.sleep(2)
        self._index = self._pc.Index(self._index_name)

    @property
    def index(self):
        if self._index is None:
            self._index = self._pc.Index(self._index_name)
        return self._index

    def stats(self) -> dict[str, Any]:
        return self.index.describe_index_stats(namespace=self._namespace)

    def delete_namespace(self) -> None:
        try:
            self.index.delete(delete_all=True, namespace=self._namespace)
            log.info("pinecone.namespace.cleared", namespace=self._namespace)
        except Exception as e:
            log.warning("pinecone.namespace.delete.fail", err=repr(e))

    # --- Upsert ---

    def upsert_chunks(
        self,
        chunks: list[Chunk],
        dense_vectors: np.ndarray,
        sparse_vectors: list[dict] | None = None,
        batch_size: int = 64,
    ) -> int:
        if len(chunks) != len(dense_vectors):
            raise ValueError("chunks/dense length mismatch")
        if sparse_vectors is not None and len(chunks) != len(sparse_vectors):
            raise ValueError("chunks/sparse length mismatch")
        if dense_vectors.shape[1] != self._dimension:
            raise ValueError(
                f"dense dim {dense_vectors.shape[1]} != index dim {self._dimension}"
            )

        # Pinecone rejects empty sparse_values; only attach when populated
        def _has_sparse(svec: dict | None) -> bool:
            return bool(svec and svec.get("indices"))

        n_upserted = 0
        items = list(zip(
            chunks,
            dense_vectors,
            sparse_vectors if sparse_vectors is not None else [None] * len(chunks),
        ))
        for batch in _batched(items, batch_size):
            vectors = []
            for chunk, dvec, svec in batch:
                v: dict = {
                    "id": chunk.chunk_id,
                    "values": dvec.tolist(),
                    "metadata": _chunk_to_metadata(chunk),
                }
                if _has_sparse(svec):
                    v["sparse_values"] = svec
                vectors.append(v)
            self._upsert_with_retry(vectors)
            n_upserted += len(vectors)
            log.info("pinecone.upsert.batch", n=n_upserted, namespace=self._namespace)
        return n_upserted

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    def _upsert_with_retry(self, vectors: list[dict]) -> None:
        self.index.upsert(vectors=vectors, namespace=self._namespace)

    # --- Query ---

    def query_hybrid(
        self,
        dense_vec: np.ndarray,
        sparse_vec: dict | None,
        top_k: int,
        doc_filter: list[str] | None = None,
    ) -> list[RetrievalHit]:
        flt = {"doc_id": {"$in": doc_filter}} if doc_filter else None
        kwargs: dict = {
            "vector": dense_vec.tolist(),
            "top_k": top_k,
            "namespace": self._namespace,
            "include_metadata": True,
        }
        if flt is not None:
            kwargs["filter"] = flt
        if sparse_vec and sparse_vec.get("indices"):
            kwargs["sparse_vector"] = sparse_vec
        try:
            res = self.index.query(**kwargs)
        except Exception as e:
            raise RetrievalError(f"pinecone query failed: {e}") from e

        matches = res.get("matches") if isinstance(res, dict) else res.matches
        hits: list[RetrievalHit] = []
        for rank, m in enumerate(matches, start=1):
            md = m["metadata"] if isinstance(m, dict) else m.metadata
            score = float(m["score"] if isinstance(m, dict) else m.score)
            chunk = _metadata_to_chunk(md)
            hits.append(RetrievalHit(chunk=chunk, score=score, rank=rank))
        return hits


def _batched(iterable: Iterable, n: int):
    batch: list = []
    for x in iterable:
        batch.append(x)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


def _chunk_to_metadata(chunk: Chunk) -> dict[str, Any]:
    """Pinecone metadata must be flat-ish (str/num/bool/list-of-str).
    We pack the full chunk JSON into a single string for round-tripping."""
    md: dict[str, Any] = {
        "chunk_id":     chunk.chunk_id,
        "doc_id":       chunk.doc_id,
        "doc_version":  chunk.doc_version,
        "page_start":   chunk.page_start,
        "page_end":     chunk.page_end,
        "section":      " > ".join(chunk.section_path)[:500],
        "image_count":  len(chunk.images),
        "image_ids":    [img.image_id for img in chunk.images][:20],  # filterable
        "text":         chunk.text[:35000],   # well under 40KB metadata cap
        "_payload":     chunk.model_dump_json(exclude={"text"}),
    }
    return md


def _metadata_to_chunk(md: dict[str, Any]) -> Chunk:
    payload = json.loads(md["_payload"])
    payload["text"] = md.get("text", "")
    return Chunk(**payload)
