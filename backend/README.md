# RagQA — Backend

FastAPI backend for image-friendly RAG over the NWA Quality Analyst documentation set.

## Stack

- **Python 3.12**, **FastAPI**, Pydantic v2 + pydantic-settings
- **Docling** for layout-aware PDF parsing (text + images + bboxes)
- **OpenAI SDK** (`gpt-4o`) — Chat Completions with vision for VLM image captioning *and* multimodal answer generation
- **Pinecone serverless** with sparse-dense hybrid search; **BM25** sparse via `pinecone-text`
- **sentence-transformers** for dense embeddings (BAAI/bge-large-en-v1.5)
- **mxbai-rerank-large-v2** cross-encoder reranker
- **structlog** for JSON-friendly logs; OTel instrumentation hooks ready for prod
- **pytest**, **ruff**, **mypy** for the dev loop

## Layout

```
src/ragqa/
├── main.py              FastAPI app entrypoint
├── config.py            pydantic-settings, single source of truth for knobs
├── api/                 routes, schemas, deps (auth, DI)
├── core/                logging + domain errors
├── models/              Chunk, ImageRef, BindingMethod, RetrievalHit
├── ingestion/           parser → binder → captioner → chunker → pipeline
├── retrieval/           dense + sparse encoders, Pinecone store, hybrid orchestrator, reranker
└── generation/          multimodal Anthropic answerer + prompt templates
scripts/
└── ingest_pdfs.py       one-shot CLI for full ingestion run
tests/
└── test_health.py       smoke test
```

## Quick start

```powershell
cd C:\rag-qa\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
# edit .env to set OPENAI_API_KEY and PINECONE_API_KEY

# place PDFs (any of these locations work; first-found wins):
#   C:\rag-qa\backend\data\source-pdfs\{QAsetup,QATutor,QAman}.cleaned.pdf
#   C:\Users\nilay\rag\pdf-rewrite\out\*.cleaned.pdf            (Track-0 output)
#   C:\Users\nilay\Downloads\*.pdf                              (originals)

# Run ingestion (one-time; ~30-60 min wall clock, ~$80-150 in API spend)
python scripts/ingest_pdfs.py --wipe-namespace

# Start API
uvicorn ragqa.main:app --reload --port 8000
# OpenAPI: http://localhost:8000/docs
```

## API surface

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | liveness + index size, no auth |
| POST | `/retrieve` | hybrid search → top-K reranked chunks (each carries its `images[]`) |
| POST | `/answer` | retrieve + multimodal LLM call → grounded answer + citations + image refs |
| POST | `/feedback` | user feedback hook (request_id, rating, note) |
| GET  | `/api/images/{image_id}` | serve extracted image bytes by id |

All RAG endpoints are guarded by `X-API-Key` if `RAGQA_API_KEY` is set.

## Image-bound retrieval contract

Every chunk row carries `images[]`. Each `ImageRef` has:
- `image_id`, `cdn_url` (`/api/images/{id}`)
- `alt_text`, `ocr_text`, `caption` (VLM-generated, cached by image hash)
- `binding_method` ∈ {explicit_reference, captioned, layout_anchored, section_floor, unbound}
- `binding_score` ∈ [0, 1]

Chunks contain inline `[FIGURE: <image_id>]` markers at the binding site,
so the frontend can render the image inline at the exact text position.
The `/answer` response also exposes `referenced_image_ids` — the subset
the LLM actually cited in its reply — which the UI can highlight.

## Production notes (from `docs/synthetic-forging-falcon.md`)

- **Blue-green via namespaces.** Build new index into `RAGQA_PINECONE_NAMESPACE=v2`, run eval set, flip env var to switch — instant rollback.
- **Eval gate.** `pytest -m eval` runs the golden Q&A set against the active namespace; CI blocks promotion on regressions to recall@5 or image-presence@5.
- **Observability.** Structured logs include `request_id`, retrieved `chunk_ids`, `referenced_image_ids`, token counts, and latency per span.
- **Auth.** Static `X-API-Key` for v1; replace with Cognito JWT verifier in `api/deps.py:require_api_key` for prod.
