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
    hybrid_alpha: float = 0.6  # 0=BM25, 1=dense (legacy Pinecone-side knob)

    # --- BM25 (in-process sparse index, fused into hybrid via RRF) ---
    # The legacy Pinecone-side sparse channel is a no-op (pinecone-text BM25
    # imports NLTK which hangs on Windows). Instead we maintain an in-memory
    # BM25 over chunks_v1.jsonl at startup and fuse its rankings with the
    # dense lists via Reciprocal Rank Fusion. Disable to fall back to
    # dense-only retrieval.
    bm25_enabled: bool = True
    bm25_top_k: int = 20    # candidates BM25 contributes per query
    rrf_k: int = 60          # RRF damping constant; standard literature value

    # --- Generation ---
    max_output_tokens: int = 1024
    temperature: float = 0.2          # gpt-4o family only (ignored by gpt-5.x)
    # gpt-5.x can't take `temperature`; these are the determinism/focus
    # knobs instead. reasoning_effort minimal|low|medium|high; "low" keeps
    # procedural doc answers focused + more stable. top_p None = don't send.
    reasoning_effort: str = "low"
    top_p: float | None = None
    max_images_per_answer: int = 6

    # --- Chat history (DynamoDB; blank table name disables persistence) ---
    chats_table: str = ""
    chats_region: str = "us-east-1"
    chats_max_list: int = 50

    # --- SME feedback (DynamoDB; blank table name disables persistence) ---
    # Per-turn 👍/👎 with optional comment. Same DynamoDB connection pattern
    # as chats; blank table name → log-only fallback (no regression from the
    # pre-Ring-3 stub /feedback route).
    feedback_table: str = ""
    feedback_region: str = "us-east-1"

    # --- Query rewriting / expansion (multi-query retrieval) ---
    # Disable to skip the rewrite call and fall back to single-query retrieval.
    query_rewrite_enabled: bool = True
    query_rewrite_model: str = "gpt-4o-mini"

    # --- Question understanding + LLM rerank + answerability gate ---
    # Precision stage between retrieval and generation: classifies the
    # question intent, reranks candidate chunks by LLM-judged relevance,
    # and reports an answerability confidence used for a deterministic
    # in-code refusal. Fails open (answers as before) on any error.
    understand_enabled: bool = True
    understand_model: str = "gpt-5.4"
    understand_max_tokens: int = 2000          # reasoning-token headroom
    understand_candidate_k: int = 15           # candidates fed to reranker
    understand_candidate_chars: int = 320      # chars of each chunk shown
    rerank_keep: int = 5                        # best-N kept after rerank
    # VERY low floor — only catches garbage. Live data: gibberish ≈0.01,
    # real questions ≥0.18, and gpt-5.x self-scores answerability harshly.
    # Keep this well below the real-question band so score variance never
    # flips an in-scope question to a refusal. out_of_scope intent is the
    # primary refuse signal (see routes.py); this is the garbage floor.
    answerability_threshold: float = 0.05

    # --- Faithfulness verifier (post-generation) ---
    # A second cheaper LLM call reads the question + answer + chunks and
    # scores how grounded the answer is. The frontend renders a low-confidence
    # badge when faithfulness < verify_threshold. Fails open (returns 1.0) on
    # any error, so disabling has zero risk.
    verify_enabled: bool = True
    verify_model: str = "gpt-5.4-mini"
    verify_max_tokens: int = 2500              # reasoning-token + output headroom
    # The answer LLM saw FULL chunks; if we hand the verifier a truncated
    # view it over-flags "unsupported" simply because the supporting text
    # is past the cutoff. 2000 chars covers virtually every chunk in this
    # corpus (most are 800–1500 chars). If chunks grow, raise this.
    verify_chunk_chars: int = 2000
    verify_threshold: float = 0.7              # below -> low_confidence flag

    # --- Build metadata (baked into image at docker build time via ARGs) ---
    # Exposed via /version and on each /answer response so reviewers can
    # tag analysis notes with the exact build that produced an answer.
    build_git_sha: str = "unknown"
    build_time: str = "unknown"

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
