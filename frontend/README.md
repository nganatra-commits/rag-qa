# RagQA — Frontend

Next.js 15 (App Router) chat UI. Pure presentation layer: every model API
key, secret, and Pinecone call lives behind the FastAPI backend. The browser
talks only to the Next server (`/api/chat`, `/api/retrieve`, `/api/images/:id`),
which proxies to the backend.

## Stack

- **Next.js 15** + React 19 (App Router, TypeScript strict)
- **Tailwind v4** for styling (no UI lib — primitives in `src/components/ui/`)
- `react-markdown` + `remark-gfm` for safe Markdown rendering
- `lucide-react` for icons; `clsx` + `tailwind-merge` for class composition
- `zustand` ready for chat-state persistence (not used yet — single page in v1)

## Layout

```
src/
├── app/
│   ├── layout.tsx, globals.css, page.tsx        the chat page
│   └── api/
│       ├── chat/route.ts                        POST /api/chat -> backend /answer
│       ├── retrieve/route.ts                    POST /api/retrieve -> backend /retrieve
│       └── images/...                           rewritten via next.config.ts
├── components/
│   ├── chat-interface.tsx                       message list, input, doc filter
│   ├── message.tsx                              renders [FIGURE: id] inline
│   ├── citation.tsx                             "Sources" panel
│   └── ui/button.tsx                            primitive
├── lib/
│   ├── api.ts                                   server-side backend client
│   └── utils.ts
└── types/
    └── api.ts                                   wire types mirroring backend Pydantic
```

## Key UI behaviour

The assistant turn renders the answer prose through Markdown, but with a
small wrinkle: any `[FIGURE: <image_id>]` marker the LLM produces is replaced
with the actual `<img src="/api/images/<id>">` from the response payload. The
binding method (`explicit_reference`, `captioned`, `layout_anchored`,
`section_floor`) and confidence score are shown beneath each figure for
transparency. Images the LLM explicitly cited get a colored ring.

## Local dev

```powershell
cd C:\rag-qa\frontend
copy .env.example .env.local       # adjust BACKEND_URL if not localhost:8000
npm install
npm run dev
# http://localhost:3000
```

The backend must be running and ingested first — see `../backend/README.md`.

## Production notes

- `next/image` is intentionally NOT used for retrieved screenshots; image_ids
  change with every reindex and we don't want Next to cache them.
- The Next server forwards the optional `X-API-Key` header server-side; keys
  never reach the browser.
- For prod, add an auth layer (NextAuth + Cognito IdP) at the route handlers
  and a cache (CDN) in front of `/api/images/*` since image bytes are immutable
  per-version.
