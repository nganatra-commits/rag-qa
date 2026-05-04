# NWA QA Knowledge Assistant

Image-friendly, multimodal Retrieval-Augmented Generation over the NWA Quality Analyst documentation set (Installation Guide, Tutorial, User's Manual). Built as two independent projects:

```
rag-qa/
├── backend/      Python 3.12 · FastAPI · Docling · Pinecone · OpenAI SDK
├── frontend/     Next.js 15 · React 19 · TypeScript · Tailwind v4
└── docker-compose.yml      One-command local dev
```

## Architecture

```
┌─────────────────┐    HTTPS    ┌─────────────────────┐
│  Next.js UI     │ ──────────▶ │  FastAPI backend    │
│  Chat + inline  │             │  /retrieve /answer  │
│  images         │ ◀────────── │  /ingest  /health   │
└─────────────────┘    JSON     └─────────┬───────────┘
                                          │
              ┌───────────────────────────┼────────────────────────────┐
              ▼                           ▼                            ▼
      ┌────────────────┐        ┌──────────────────┐         ┌──────────────────┐
      │   Pinecone     │        │   OpenAI API     │         │  Local images/   │
      │  serverless    │        │  gpt-4o vision   │         │  /data/images/   │
      │  sparse-dense  │        │  (caption+answer │         │  served by       │
      │  hybrid index  │        │   multimodal)    │         │  /api/images/... │
      │  (namespaced   │        │                  │         │                  │
      │   per version) │        │                  │         │                  │
      └────────────────┘        └──────────────────┘         └──────────────────┘
```

Persistent **image↔chunk binding** (4-rule cascade: explicit-reference → caption → layout-anchor → section-floor) carried through ingestion → indexing → retrieval → both the UI render and the multimodal LLM call.

## Quick start (local dev)

```powershell
# 1. one-time: place source PDFs (see backend/README for the cleaned versions)
copy C:\Users\nilay\rag\pdf-rewrite\out\*.cleaned.pdf C:\rag-qa\backend\data\source-pdfs\

# 2. backend
cd C:\rag-qa\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env       # then edit OPENAI_API_KEY and PINECONE_API_KEY
python scripts/ingest_pdfs.py   # one-time, ~30 min and ~$120 in API spend
uvicorn ragqa.main:app --reload --port 8000

# 3. frontend (new terminal)
cd C:\rag-qa\frontend
copy .env.example .env.local
npm install
npm run dev
# open http://localhost:3000
```

Or with Docker:

```bash
docker compose up
```

## Project boundaries

| Concern | Backend | Frontend |
|---|---|---|
| PDF parsing, embedding, indexing | ✅ | ❌ |
| LLM calls, prompt construction | ✅ | ❌ |
| Auth, rate limiting | ✅ | passes JWT |
| Image bytes serving | ✅ via `/api/images/{id}` | ❌ |
| Chat state | ❌ (stateless API) | ✅ (client+SSE) |
| Citation rendering, image inlining | ❌ | ✅ |

The frontend has no model API keys. All LLM/vector calls go through the backend.

## Production lifecycle

Tracked in `docs/synthetic-forging-falcon.md` (the source plan). TL;DR: blue-green Pinecone **namespaces** within a single index (`v1` → `v2`), eval-gated promotion via golden Q&A set, namespace swap for instant rollback (just flip `RAGQA_PINECONE_NAMESPACE`), OpenTelemetry → Langfuse for tracing.
