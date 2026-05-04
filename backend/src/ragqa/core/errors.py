"""Domain exceptions surfaced to the API layer."""
from __future__ import annotations


class RagQaError(Exception):
    """Base."""

    status_code: int = 500
    code: str = "internal_error"


class IngestionError(RagQaError):
    status_code = 422
    code = "ingestion_error"


class RetrievalError(RagQaError):
    status_code = 502
    code = "retrieval_error"


class IndexNotFoundError(RagQaError):
    status_code = 503
    code = "index_not_ready"


class GenerationError(RagQaError):
    status_code = 502
    code = "generation_error"


class AuthError(RagQaError):
    status_code = 401
    code = "unauthorized"
