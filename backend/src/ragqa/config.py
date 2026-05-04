"""Centralised configuration via Pydantic Settings.

All knobs that vary between dev / staging / prod live here. No defaults
that bake in environment-specific values (paths, hosts, secrets).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAGQA_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- OpenAI ---
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    vlm_model: str = "gpt-4o"
    llm_model: str = "gpt-4o"

    # --- Pinecone ---
    pinecone_api_key: str = Field(..., alias="PINECONE_API_KEY")
    pinecone_index: str = "ragqa-chunks"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_namespace: str = "v1"
    pinecone_metric: str = "dotproduct"  # required for sparse-dense hybrid
    pinecone_batch_size: int = 64

    # --- Embedding (OpenAI) ---
    embedding_model: str = "text-embedding-3-large"
    embedding_dim: int = 3072
    # Reranker is optional; blank disables (hybrid search alone is plenty for
    # this corpus). Set to a sentence-transformers cross-encoder name to enable.
    reranker_model: str = ""

    # --- Storage (local-only files: source PDFs + extracted images + chunk JSONL) ---
    data_dir: Path = Path("./data")

    # --- API ---
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:3000"
    api_key: str = ""  # blank disables auth

    # --- Retrieval ---
    top_k: int = 20
    rerank_top_k: int = 5
    hybrid_alpha: float = 0.6  # 0=BM25, 1=dense

    # --- Generation ---
    max_output_tokens: int = 1024
    temperature: float = 0.2
    max_images_per_answer: int = 4

    # --- Observability ---
    log_level: str = "INFO"
    log_json: bool = False

    # --- Derived ---
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def source_pdfs_dir(self) -> Path:
        return self.data_dir / "source-pdfs"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def chunks_jsonl(self) -> Path:
        return self.data_dir / f"chunks_{self.pinecone_namespace}.jsonl"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.source_pdfs_dir, self.images_dir,
                  self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings instance. Cached so .env is read once."""
    return Settings()  # type: ignore[call-arg]
