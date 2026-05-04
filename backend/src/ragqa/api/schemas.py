"""Wire-format Pydantic models for the public API.

The frontend mirrors these in src/types/api.ts. Keep them simple and stable.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ragqa.models.chunks import Chunk, RetrievalHit


class HealthResponse(BaseModel):
    status: str
    version: str
    index: str
    namespace: str
    indexed_chunks: int
    indexed_vectors: int


class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    rerank_top_k: int | None = Field(default=None, ge=1, le=20)
    alpha: float | None = Field(default=None, ge=0.0, le=1.0)
    doc_filter: list[str] | None = None


class RetrieveResponse(BaseModel):
    query: str
    hits: list[RetrievalHit]
    latency_ms: int


class AnswerRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    rerank_top_k: int | None = Field(default=None, ge=1, le=20)
    alpha: float | None = Field(default=None, ge=0.0, le=1.0)
    doc_filter: list[str] | None = None
    max_images: int | None = Field(default=None, ge=0, le=8)


class AnswerImage(BaseModel):
    image_id: str
    cdn_url: str
    page: int
    caption: str
    alt_text: str
    chunk_id: str
    binding_method: str
    binding_score: float


class AnswerCitation(BaseModel):
    chunk_id: str
    doc_id: str
    section_path: list[str]
    page_start: int
    page_end: int


class AnswerResponse(BaseModel):
    query: str
    answer: str
    citations: list[AnswerCitation]
    images: list[AnswerImage]
    referenced_image_ids: list[str]
    chunks: list[Chunk]
    is_refusal: bool = False
    input_tokens: int
    output_tokens: int
    latency_ms: int


class FeedbackRequest(BaseModel):
    request_id: str
    rating: int = Field(..., ge=-1, le=1)  # -1 thumbs down, 1 thumbs up, 0 neutral
    note: str = Field(default="", max_length=2000)
