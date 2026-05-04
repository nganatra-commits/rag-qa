"""FastAPI dependencies: settings, auth, retriever singleton."""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from ragqa.config import Settings, get_settings
from ragqa.core.errors import IndexNotFoundError
from ragqa.core.logging import get_logger
from ragqa.generation.llm import MultimodalAnswerer
from ragqa.retrieval.embeddings import DenseEmbedder, SparseEncoder
from ragqa.retrieval.hybrid import HybridRetriever
from ragqa.retrieval.rerank import CrossEncoderReranker
from ragqa.retrieval.vectorstore import PineconeVectorStore

log = get_logger(__name__)


@lru_cache(maxsize=1)
def _load_blank_ids() -> frozenset[str]:
    """Load the blank-image deny list (built by scripts/scan_blank_images.py).

    These ids correspond to PNGs that are mostly-uniform pixels (soft masks,
    alpha channels, CMYK separations - PyMuPDF artifacts). We hide them from
    /answer responses so the UI never shows broken-looking thumbnails.
    """
    s = get_settings()
    p = s.cache_dir / "blank_image_ids.txt"
    if not p.exists():
        return frozenset()
    return frozenset(line.strip() for line in p.read_text(encoding="utf-8").splitlines()
                     if line.strip())


def get_blank_ids() -> frozenset[str]:
    return _load_blank_ids()


@lru_cache(maxsize=1)
def _build_components() -> dict:
    s = get_settings()
    s.ensure_dirs()

    dense = DenseEmbedder(
        model_name=s.embedding_model,
        api_key=s.openai_api_key,
        base_url=s.openai_base_url,
    )
    sparse = SparseEncoder(cache_path=s.cache_dir / "sparse_bm25.json")

    store = PineconeVectorStore(
        api_key=s.pinecone_api_key,
        index_name=s.pinecone_index,
        cloud=s.pinecone_cloud,
        region=s.pinecone_region,
        namespace=s.pinecone_namespace,
        dimension=s.embedding_dim,
        metric=s.pinecone_metric,
    )
    reranker = CrossEncoderReranker(model_name=s.reranker_model) if s.reranker_model else None

    retriever = HybridRetriever(
        settings=s, store=store, dense=dense, sparse=sparse, reranker=reranker
    )

    answerer = MultimodalAnswerer(
        api_key=s.openai_api_key,
        base_url=s.openai_base_url,
        model=s.llm_model,
        max_output_tokens=s.max_output_tokens,
        temperature=s.temperature,
        max_images=s.max_images_per_answer,
    )
    return {"retriever": retriever, "answerer": answerer, "store": store}


def get_retriever() -> HybridRetriever:
    try:
        comps = _build_components()
    except FileNotFoundError as e:
        raise IndexNotFoundError(str(e)) from e
    return comps["retriever"]


def get_answerer() -> MultimodalAnswerer:
    return _build_components()["answerer"]


def get_store() -> PineconeVectorStore:
    return _build_components()["store"]


def require_api_key(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Optional API-key gate. If RAGQA_API_KEY is set, requests must match."""
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
