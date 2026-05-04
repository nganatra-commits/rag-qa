"""Top-level ingestion orchestrator.

For each source PDF:
  1. Parse with Docling (text elements + extracted images)
  2. Bind images <-> text elements via the 4-rule cascade
  3. Caption every image with the VLM (cached by image hash)
  4. Element-aware chunking, preserving image bindings as [FIGURE: id] markers
  5. Persist chunks.jsonl as the durable single source of truth
  6. Fit BM25 sparse encoder on the corpus
  7. Embed dense vectors
  8. Encode sparse vectors
  9. Upsert into Pinecone (indexed namespace)

Idempotent: rerunning re-uses caches; running with a new namespace does a
clean-room rebuild without disturbing the live one (blue-green pattern).
"""
from __future__ import annotations

import gc
import json
import time
from pathlib import Path

from ragqa.config import Settings
from ragqa.core.logging import get_logger
from ragqa.ingestion.binder import ImageBinder
from ragqa.ingestion.captioner import ImageCaption, VLMCaptioner
from ragqa.ingestion.chunker import ElementAwareChunker
from ragqa.ingestion.parser_pymupdf import PyMuPDFParser
from ragqa.ingestion.parser_types import ParsedImage
from ragqa.models.chunks import Chunk
from ragqa.retrieval.embeddings import DenseEmbedder, SparseEncoder
from ragqa.retrieval.vectorstore import PineconeVectorStore

log = get_logger(__name__)


class IngestionPipeline:
    def __init__(self, settings: Settings):
        self._s = settings
        settings.ensure_dirs()

        self._parser = PyMuPDFParser(images_dir=settings.images_dir)
        self._binder = ImageBinder()
        self._captioner = VLMCaptioner(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            cache_dir=settings.cache_dir,
            model=settings.vlm_model,
        )
        self._chunker = ElementAwareChunker()

        self._dense = DenseEmbedder(
            model_name=settings.embedding_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
        self._sparse = SparseEncoder(cache_path=settings.cache_dir / "sparse_bm25.json")

        self._store = PineconeVectorStore(
            api_key=settings.pinecone_api_key,
            index_name=settings.pinecone_index,
            cloud=settings.pinecone_cloud,
            region=settings.pinecone_region,
            namespace=settings.pinecone_namespace,
            dimension=settings.embedding_dim,
            metric=settings.pinecone_metric,
        )

    def run(self, pdfs: list[tuple[str, Path]],
            wipe_namespace: bool = False) -> dict:
        """Run the full pipeline.

        Args:
            pdfs: list of (doc_id, source_path) tuples.
            wipe_namespace: clear the target namespace before upserting (clean rebuild).
        """
        t0 = time.time()
        self._store.ensure_index()
        if wipe_namespace:
            self._store.delete_namespace()

        all_chunks: list[Chunk] = []

        for doc_id, src in pdfs:
            log.info("ingest.doc.start", doc_id=doc_id, src=str(src))
            parsed = self._parser.parse(src, doc_id=doc_id)

            bindings = self._binder.bind(parsed)

            # Caption only images that are actually bound (skip orphans)
            bound_image_ids = {b.image_id for b in bindings}
            bound_paths = [pi.file_path for pi in parsed.images
                           if pi.image_id in bound_image_ids]
            captions_by_path = self._captioner.caption_many(bound_paths)
            captions_by_id: dict[str, ImageCaption] = {}
            for pi in parsed.images:
                cap = captions_by_path.get(pi.file_path)
                if cap is not None:
                    captions_by_id[pi.image_id] = cap

            images_index: dict[str, ParsedImage] = {pi.image_id: pi for pi in parsed.images}

            chunks = self._chunker.chunk(
                doc=parsed,
                bindings=bindings,
                captions=captions_by_id,
                images_index=images_index,
                embedding_model=self._s.embedding_model,
                vlm_model=self._s.vlm_model,
            )
            all_chunks.extend(chunks)
            log.info("ingest.doc.done", doc_id=doc_id, chunks=len(chunks))

            # Free per-doc memory before moving to the next PDF.
            # Docling holds rendered page tensors and IBM layout-model state;
            # without explicit GC we OOM on long PDFs (QAman: 608 pp).
            del parsed, bindings, bound_paths, captions_by_path, captions_by_id, images_index, chunks
            gc.collect()

        # Persist single source of truth
        self._write_chunks_jsonl(all_chunks)

        # Fit sparse encoder on the full corpus
        self._sparse.fit([c.text for c in all_chunks])

        # Dense embed + sparse encode + upsert
        log.info("ingest.embed.start", n=len(all_chunks))
        dense_vecs = self._dense.embed_passages([c.text for c in all_chunks])
        sparse_vecs = self._sparse.encode_documents([c.text for c in all_chunks])
        log.info("ingest.upsert.start", n=len(all_chunks),
                 namespace=self._s.pinecone_namespace)
        n = self._store.upsert_chunks(
            chunks=all_chunks,
            dense_vectors=dense_vecs,
            sparse_vectors=sparse_vecs,
            batch_size=self._s.pinecone_batch_size,
        )

        elapsed = time.time() - t0
        summary = {
            "docs":       len(pdfs),
            "chunks":     len(all_chunks),
            "upserted":   n,
            "namespace":  self._s.pinecone_namespace,
            "index":      self._s.pinecone_index,
            "elapsed_s":  round(elapsed, 1),
        }
        log.info("ingest.done", **summary)
        return summary

    def _write_chunks_jsonl(self, chunks: list[Chunk]) -> None:
        path = self._s.chunks_jsonl
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for c in chunks:
                f.write(c.model_dump_json() + "\n")
        log.info("ingest.chunks_jsonl", path=str(path), n=len(chunks))
