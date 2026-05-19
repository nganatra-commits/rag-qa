# Embedding rag-qa in another app

rag-qa can be embedded in any host app via:

1. **Iframe** with URL params (works today — no auth, no `X-Frame-Options`).
2. **`postMessage`** for live context updates and incoming questions.

Static-export Next.js + a stateless FastAPI backend mean there is no cookie or session plumbing to negotiate. The only thing the host needs is to be in the backend's CORS allowlist.

---

## URL contract

| Param | Purpose |
|---|---|
| `q` | Pre-filled question. Auto-submitted on mount (unless `autosubmit=0`). Stripped from the URL after firing so refresh doesn't re-submit. |
| `chat` | Existing chat UUID to restore. If both `q` and `chat` are set, history is restored first, then `q` is asked as a new turn. |
| `autosubmit` | `0` = prefill the input but do not auto-fire. Default `1`. |
| *any other key* | Treated as **host context**. Folded into a structured prefix on every outgoing question (`[Host context: key1=v1; key2=v2]\n\nuser question`). The UI displays the raw user question; only the LLM sees the prefix. |

### Example

```html
<iframe
  src="https://rag-qa.example.com/?q=What+is+the+alarm+count&chat=<uuid>&enterprise=PlantA&page=ops"
  style="width:100%;height:100%;border:0"
></iframe>
```

---

## postMessage contract

The embedded app listens for `window.message` events from the parent and posts events back.

### Host → embed

```ts
// Update host context (replaces the entire context map)
iframe.contentWindow.postMessage(
  { type: "SET_CONTEXT", context: { enterprise: "PlantB", page: "alarms" } },
  "https://rag-qa.example.com"  // targetOrigin — set this strictly
);

// Ask a question (skips the user typing)
iframe.contentWindow.postMessage(
  { type: "ASK", query: "Summarise the alarms in the last hour" },
  "https://rag-qa.example.com"
);
```

### Embed → host

```ts
window.addEventListener("message", (e) => {
  // Validate origin — your host MUST do this, we do not do it for you.
  if (e.origin !== "https://rag-qa.example.com") return;
  switch (e.data?.type) {
    case "READY":    /* iframe loaded and listening */         break;
    case "ANSWER":   /* { text } — chatbot finished a turn */  break;
    case "ERROR":    /* { message } — request failed */         break;
  }
});
```

Wait for `READY` before posting `SET_CONTEXT` / `ASK` to avoid a race where the host posts before the iframe's listener is installed.

### Origin allowlist (recommended for production)

Set `NEXT_PUBLIC_EMBED_ALLOWED_ORIGINS` at build time to a comma-separated list of origins you trust to send messages into the iframe:

```sh
NEXT_PUBLIC_EMBED_ALLOWED_ORIGINS=https://insights.example.com,https://other-host.example.com
```

Default is `*`, which accepts any parent. Fine for dev, tighten for prod.

---

## Backend CORS

Add the embedding host's origin to `RAGQA_CORS_ORIGINS` (comma-separated). On AWS this is the `frontend_origin` Terraform variable — set it to the deployed frontend origin *plus* any host that will embed.

```hcl
# infra/aws/terraform/terraform.tfvars
frontend_origin = "https://rag-qa.example.com,https://insights.example.com"
```

The backend's CORS middleware reads this env var and allows requests from those origins. The iframe itself does not need to be in this list — the iframe runs as the rag-qa frontend origin and calls the backend from there.

---

## Notes / caveats

- **Auto-submit on mount** also fires for direct visits with `?q=`. If a user bookmarks `/?q=...`, they will re-ask that question on load.
- **Chat history (`/api/chats/*`) is unauthenticated.** Anyone with a chat UUID can read/write it. Acceptable for trusted-host embeds; pass freshly minted UUIDs from the host to avoid leakage.
- **No backend rate limiting.** Add WAF / Cloudfront throttling separately if the embed is exposed broadly.
- **Host context lives as a text prefix to each user question.** It is not a separate field on the `/answer` API. Subsequent turns reuse current context, not historical context — so changing context via `SET_CONTEXT` affects the next turn forward only.
