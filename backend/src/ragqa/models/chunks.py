"""Domain types — single source of truth for chunk + image binding shape.

These models are persisted to LanceDB (as flattened rows), serialized to
chunks.jsonl, and returned to the frontend. Frontend mirrors the shape
in src/types/api.ts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class BindingMethod(str, Enum):
    EXPLICIT_REFERENCE = "explicit_reference"  # text says "Figure 4.2", "see screenshot below"
    CAPTIONED          = "captioned"           # image has a Caption element directly beneath
    LAYOUT_ANCHORED    = "layout_anchored"     # bbox sits inside a section's reading-order span
    SECTION_FLOOR      = "section_floor"       # last resort: bind to enclosing heading
    UNBOUND            = "unbound"             # could not bind — should be vanishingly rare


class ImageRef(BaseModel):
    image_id: str
    uri: str                            # absolute path or s3://... URI to the source PNG
    cdn_url: str | None = None          # backend-served URL: /api/images/{image_id}
    page: int
    bbox: list[float] = Field(default_factory=list)  # [x0,y0,x1,y1] in PDF units
    alt_text: str = ""
    ocr_text: str = ""
    caption: str = ""
    binding_method: BindingMethod = BindingMethod.SECTION_FLOOR
    binding_score: float = 0.5


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    doc_version: str = "v1"
    source_file: str
    page_start: int
    page_end: int
    section_path: list[str] = Field(default_factory=list)
    element_type: str = "NarrativeText"
    text: str
    images: list[ImageRef] = Field(default_factory=list)

    embedding_model: str = ""
    parser_version: str = ""
    vlm_version: str = ""
    indexed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str = ""

    @property
    def image_ids(self) -> list[str]:
        return [img.image_id for img in self.images]

    @property
    def section_breadcrumb(self) -> str:
        return " > ".join(self.section_path) if self.section_path else "(no section)"


class RetrievalHit(BaseModel):
    """A scored retrieval result, with the underlying chunk hydrated."""
    chunk: Chunk
    score: float
    rerank_score: float | None = None
    rank: int
