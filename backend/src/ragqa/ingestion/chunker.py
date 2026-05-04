"""Element-aware chunker.

- Walks the parsed document in reading order.
- Maintains a section_path stack from heading elements.
- Greedy-packs paragraphs/list items into chunks until the token-ish budget
  is hit, then starts a new chunk. Tables are emitted as standalone chunks.
- For each image binding (elem_id -> image_id), inject a [FIGURE: image_id]
  marker into the chunk that contains the bound element.
- Each chunk's `images[]` is hydrated from the image registry so the
  retrieval response can return image bytes inline.

Output: list[Chunk] ready for embedding + indexing.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ragqa.core.logging import get_logger
from ragqa.ingestion.binder import Binding
from ragqa.ingestion.captioner import ImageCaption
from ragqa.ingestion.parser_types import ParsedDocument, ParsedElement, ParsedImage
from ragqa.models.chunks import BindingMethod, Chunk, ImageRef

log = get_logger(__name__)


def _approx_tokens(text: str) -> int:
    # Cheap, model-agnostic token estimate. Good enough for chunk-size budgets.
    return max(1, len(text) // 4)


@dataclass
class _Buffer:
    elements: list[ParsedElement] = field(default_factory=list)
    image_ids: set[str] = field(default_factory=set)
    tokens: int = 0
    page_start: int = 0
    page_end: int = 0


class ElementAwareChunker:
    def __init__(
        self,
        target_tokens: int = 700,
        max_tokens: int = 1024,
        overlap_tokens: int = 100,
    ):
        self.target_tokens = target_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(
        self,
        doc: ParsedDocument,
        bindings: list[Binding],
        captions: dict[str, ImageCaption],
        images_index: dict[str, ParsedImage],
        embedding_model: str,
        vlm_model: str,
    ) -> list[Chunk]:
        # elem_id -> [Binding...]
        binding_by_elem: dict[str, list[Binding]] = {}
        for b in bindings:
            binding_by_elem.setdefault(b.elem_id, []).append(b)

        section_stack: list[str] = []   # current section_path
        chunks: list[Chunk] = []
        buf = _Buffer()
        chunk_idx = 0

        def flush() -> None:
            nonlocal buf, chunk_idx
            if not buf.elements:
                return
            chunk = self._materialize_chunk(
                doc=doc, idx=chunk_idx, buf=buf, section_path=list(section_stack),
                binding_by_elem=binding_by_elem, captions=captions,
                images_index=images_index, embedding_model=embedding_model,
                vlm_model=vlm_model,
            )
            chunks.append(chunk)
            chunk_idx += 1

            # Carry overlap: keep the last few elements as a starting buffer
            tail: list[ParsedElement] = []
            tail_tokens = 0
            for e in reversed(buf.elements):
                t = _approx_tokens(e.text)
                if tail_tokens + t > self.overlap_tokens:
                    break
                tail.insert(0, e)
                tail_tokens += t
            buf = _Buffer(elements=tail, tokens=tail_tokens,
                          page_start=tail[0].page if tail else 0,
                          page_end=tail[-1].page if tail else 0,
                          image_ids={iid for e in tail
                                     for b in binding_by_elem.get(e.elem_id, [])
                                     for iid in [b.image_id]})

        for e in doc.elements:
            role = e.role.lower()

            # Headings: update section stack, flush before/after to keep boundaries clean
            if role in ("title", "section_header"):
                flush()
                level = max(1, e.level or 1)
                # Pop stack to this level - 1, then push
                while len(section_stack) >= level:
                    section_stack.pop()
                section_stack.append(e.text.strip())
                # Emit the header itself as a small standalone chunk so it's
                # findable and inherits any images bound to it (section-floor)
                buf.elements.append(e)
                buf.tokens += _approx_tokens(e.text)
                buf.page_start = buf.page_start or e.page
                buf.page_end = e.page
                for b in binding_by_elem.get(e.elem_id, []):
                    buf.image_ids.add(b.image_id)
                flush()
                continue

            # Tables emit as their own chunks (atomic) regardless of size
            if role == "table":
                flush()
                buf.elements.append(e)
                buf.tokens += _approx_tokens(e.text)
                buf.page_start = buf.page_start or e.page
                buf.page_end = e.page
                for b in binding_by_elem.get(e.elem_id, []):
                    buf.image_ids.add(b.image_id)
                flush()
                continue

            # Captions: skipped from body text — they're consumed by the binder
            if role == "caption":
                continue

            # Normal text element: pack into buffer
            t = _approx_tokens(e.text)
            if buf.tokens + t > self.max_tokens:
                flush()
            if not buf.elements:
                buf.page_start = e.page
            buf.elements.append(e)
            buf.tokens += t
            buf.page_end = e.page
            for b in binding_by_elem.get(e.elem_id, []):
                buf.image_ids.add(b.image_id)

            if buf.tokens >= self.target_tokens:
                flush()

        flush()

        log.info("chunker.done", doc_id=doc.doc_id, chunks=len(chunks),
                 avg_tokens=int(sum(_approx_tokens(c.text) for c in chunks) / max(1, len(chunks))))
        return chunks

    def _materialize_chunk(
        self,
        *,
        doc: ParsedDocument,
        idx: int,
        buf: _Buffer,
        section_path: list[str],
        binding_by_elem: dict[str, list[Binding]],
        captions: dict[str, ImageCaption],
        images_index: dict[str, ParsedImage],
        embedding_model: str,
        vlm_model: str,
    ) -> Chunk:
        # Stitch text with [FIGURE: id] markers placed where the bound element sits
        text_parts: list[str] = []
        for e in buf.elements:
            text_parts.append(e.text.strip())
            for b in binding_by_elem.get(e.elem_id, []):
                cap = captions.get(b.image_id, ImageCaption())
                marker_bits = [f"[FIGURE: {b.image_id}]"]
                if cap.caption:
                    marker_bits.append(cap.caption)
                if cap.ocr_text:
                    marker_bits.append(f"(text in image: {cap.ocr_text})")
                text_parts.append(" ".join(marker_bits))
        text = "\n\n".join(text_parts).strip()

        # Hydrate ImageRef list
        image_refs: list[ImageRef] = []
        seen_ids: set[str] = set()
        for e in buf.elements:
            for b in binding_by_elem.get(e.elem_id, []):
                if b.image_id in seen_ids:
                    continue
                seen_ids.add(b.image_id)
                pi = images_index.get(b.image_id)
                if pi is None:
                    continue
                cap = captions.get(b.image_id, ImageCaption())
                image_refs.append(ImageRef(
                    image_id=b.image_id,
                    uri=str(pi.file_path),
                    cdn_url=f"/api/images/{b.image_id}",
                    page=pi.page,
                    bbox=list(pi.bbox),
                    alt_text=cap.alt_text,
                    ocr_text=cap.ocr_text,
                    caption=cap.caption,
                    binding_method=b.method,
                    binding_score=b.score,
                ))

        chunk_id = f"{doc.doc_id}_p{buf.page_start:04d}_c{idx:04d}"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

        return Chunk(
            chunk_id=chunk_id,
            doc_id=doc.doc_id,
            source_file=str(doc.source_path),
            page_start=buf.page_start,
            page_end=buf.page_end,
            section_path=section_path,
            element_type="composite",
            text=text,
            images=image_refs,
            embedding_model=embedding_model,
            parser_version=doc.parser_version,
            vlm_version=vlm_model,
            content_hash=content_hash,
        )
