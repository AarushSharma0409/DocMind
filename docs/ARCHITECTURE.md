# DocMind — Architecture & Design Decisions

This document is updated after every phase. It's not just a diagram — it's
a record of *why* each decision was made, so it can double as interview prep later.

---

## System Overview

```
React frontend (workspace.tsx)
    ↓  HTTP + X-API-Key header
FastAPI backend (main.py)
    ├── Auth middleware        — X-API-Key checked on every request
    ├── Rate limiter           — slowapi, per-endpoint per-IP limits
    ↓
Document ingestion pipeline
    ├── loaders.py      — PDF (text + OCR fallback) / DOCX / TXT parsing
    ├── chunker.py      — token-based splitting with overlap
    ├── embedder.py     — local sentence-transformers model (eager-loaded at startup)
    └── vector_store.py — ChromaDB persistent storage
    ↓
Query pipeline
    ├── query_router.py  — Gemini classifies: retrieve / full_document / no_retrieval
    ├── retriever.py     — cosine similarity search in ChromaDB
    ├── confidence.py    — judges retrieval quality before generation
    └── generator.py     — Gemini synthesizes answer with citations
    ↓
FastAPI API layer
    ├── POST /documents/upload  — validate + ingest (202, background task)
    ├── GET  /documents/        — list ingested documents
    ├── GET  /documents/status  — per-file ingestion status (indexing/indexed/failed)
    ├── DELETE /documents/{f}   — remove one document
    ├── DELETE /documents/      — remove all documents
    ├── POST /query/            — full RAG pipeline
    └── GET  /health            — liveness check (no auth required)
    ↓
Cited response + confidence level → React frontend
```

---

## Phase 1 — Document Ingestion & Chunking

**Status:** ✅ Complete

### What it does

Accepts a PDF, DOCX, or TXT file and produces a set of embedded,
metadata-tagged chunks stored persistently in ChromaDB. Every chunk carries
the information needed for a traceable citation: which file it came from,
and where in that file it lives.

### Loaders (`loaders.py`)

All loaders return the same output shape:

```python
[{"page_number": int, "locator_type": str, "text": str}, ...]
```

This unified shape means nothing downstream needs to know which loader
produced the data. The `locator_type` field encodes the difference:

- PDFs use `"page"` — real, fixed page numbers that match what the user sees.
- DOCX files use `"paragraph_index"` — because Word pages depend on fonts and
  margins not stored in the file, so true page numbers are unavailable. Paragraph
  position is a stable proxy that the citation UI can render as `"¶12"`.
- TXT files use `"page"` with `page_number: 1` — treated as a single page.

**Bug caught before shipping:** the original DOCX loader used a dense counter
that only incremented on non-empty paragraphs, producing positions `[1, 2, 3]`
instead of true positions `[1, 3, 6]` when blank paragraphs were skipped. Fixed
by using `enumerate()` to preserve true position regardless of blank gaps.

**OCR fallback:** scanned PDFs contain images of text rather than embedded text —
`pypdf` returns empty strings for these pages. `load_pdf()` detects image-only
pages and runs Tesseract OCR via `pdf2image` as a per-page fallback. Text-based
pages still take the fast path with no OCR overhead.

### Chunker (`chunker.py`)

Splits loader output into token-bounded chunks suitable for embedding.

**Why token-based, not character-based:** chunk size in characters is
meaningless to an embedding model — what matters is tokens, which is what the
model actually processes. A 2000-character chunk could be 300 or 600 tokens
depending on the text.

**Parameters:** `CHUNK_SIZE_TOKENS = 500`, `CHUNK_OVERLAP_TOKENS = 75`.
Large enough to preserve context, small enough for precise retrieval.

**Overlap:** each chunk carries the last ~75 tokens of the previous chunk
(in whole sentence units, never mid-sentence). Without this, a sentence split
across a chunk boundary loses meaning in both halves.

**Sentence fallback:** dense technical writing with no paragraph breaks produces
oversized "paragraphs." The chunker detects this and falls back to sentence-level
splitting rather than truncating or producing one giant chunk.

**Metadata propagation:** every chunk produced from a page inherits that page's
`page_number` and `locator_type`. If this silently breaks, citations point to
the wrong source with no visible symptom.

**Production edge case fixed:** `tiktoken` downloads its encoding file from
OpenAI's servers on first use, which fails in network-restricted environments.
`count_tokens()` wraps this in a try/except and falls back to `len(text) // 4`.

### Embedder (`embedder.py`)

**Model:** `all-MiniLM-L6-v2` via `sentence-transformers`, run locally.
Free, no API key, no per-call cost. Produces 384-dimensional vectors. The
module isolates this decision — swapping to a hosted model later requires
changing only `embedder.py`.

**Lazy singleton → Eager startup load:** the model was originally loaded lazily
on first use. This made the first upload pay a 2–5 second model-load cost on top
of ingestion time. Fixed by calling `get_model()` in FastAPI's lifespan hook at
startup — the model is ready before the first request arrives, and every upload
after that pays zero load cost.

### Vector Store (`vector_store.py`)

**Why persistent, not in-memory:** an in-memory ChromaDB instance loses all
data when the process restarts. Persistent storage means uploaded documents
survive app restarts without re-ingestion.

**Bug caught: client caching across different paths.** Fixed by caching clients
in a dict keyed by path rather than a single global instance.

**ID collision prevention:** record IDs are built as `f"{source_file}::{chunk_id}"`
to guarantee uniqueness across the entire knowledge base.

**Distance metric:** collection is created with `metadata={"hnsw:space": "cosine"}`
explicitly. ChromaDB's default is squared L2, not cosine — discovered when
similarity scores were all returning `0.0`.

---

## Phase 2 — Retrieval, Query Routing & Generation

**Status:** ✅ Complete

### Query Router (`query_router.py`)

Classifies queries using Gemini structured JSON output into three routes:

- `retrieve` — similarity search over ingested chunks
- `full_document` — query asks to summarize a specific named document
- `no_retrieval` — general chat, no document context needed

**Why classify first:** sending every query through vector search wastes
compute on queries unrelated to uploaded documents, and produces confidently
wrong answers when the documents don't contain the relevant information.

**Failure handling:** on any API failure, falls back silently to `"retrieve"`.
Wrong routing degrades gracefully — worst case, a similarity search runs on
a query it won't answer well.

### Retriever (`retriever.py`)

Embeds the query with the same model used for documents, then queries ChromaDB
for the most similar stored chunks.

**Why `top_k=5`:** with a 384-dim local model, the most relevant chunk could
plausibly rank 4th. Top 5 gives the generator enough context and gives
confidence signaling more signal to work with.

**Critical bug found and fixed:** `retrieve()` converted ChromaDB distances to
similarity using `1 - distance`, only valid for cosine distance. ChromaDB's
actual default is squared L2. Fixed by setting `{"hnsw:space": "cosine"}` on
collection creation. Before the fix, every similarity score returned as `0.0`.

### Generator (`generator.py`)

**LLM provider:** Gemini throughout, consistent with query router. An earlier
draft used Groq — reversed because two LLM providers means two SDKs and two
auth setups in one pipeline.

**Citation by chunk index:** the model cites chunks by position in the prompt,
not by `chunk_id` — because `retriever.py` doesn't return a `chunk_id`.

**Failure handling:** raises `GenerationError` on any failure. No safe default
answer exists — silent fallback would actively mislead the user.

**`.env` loading fixed:** all modules resolve `.env` relative to the file's
own location using `Path(__file__).resolve().parent...`, making it
working-directory-independent.

### Confidence Signaling (`confidence.py`)

Reads retrieval output and returns a judgment about how much to trust the answer.

**Why a separate module:** if `generate()` raises `GenerationError`, the API
still needs to tell the user something meaningful. Burying confidence inside
`generate()` would silence that signal on every generation failure.

**Thresholds:** `HIGH_SIMILARITY_THRESHOLD = 0.65`, `LOW_SIMILARITY_THRESHOLD = 0.35`,
`MIN_CHUNKS_FOR_HIGH_CONFIDENCE = 2`. Named constants, not magic numbers.

**Single-chunk rule:** one highly similar chunk gets `"medium"` not `"high"` —
it could be a precise answer or the only partial hit in a weak retrieval.

**Max, not average:** uses `max(similarities)`. One strong hit among weak ones
means something relevant was found — averaging would incorrectly drag the score
down and signal low confidence.

**Pure function:** no API calls, no I/O. 32 tests run in under 0.1 seconds.

---

## Phase 3 — API Layer

**Status:** ✅ Complete

### Endpoints

```
POST /documents/upload       — validate + ingest (202, background)
GET  /documents/             — list all ingested source files
GET  /documents/status       — per-file ingestion status
DELETE /documents/{filename} — remove one document
DELETE /documents/           — remove all documents
POST /query/                 — full RAG pipeline
GET  /health                 — liveness check (auth-exempt)
```

### Upload endpoint (`documents.py`)

Security checks run in cheapest-first order before any pipeline work:

1. Extension check — reject unsupported types before reading bytes
2. Read bytes into memory
3. Size check — reject files over 25 MB with 413
4. Magic byte validation — inspect actual file header, not just extension
5. Filename sanitisation — strip directory traversal and unsafe characters
6. Write to temp file, queue background ingestion, return 202 immediately

**Background ingestion:** `BackgroundTasks` runs the full pipeline after the
response is sent. The browser gets 202 in ~50ms; ingestion completes in the
background. Why `BackgroundTasks` not a task queue: simpler, no extra
dependencies, appropriate for a workload that won't see concurrent heavy
ingestion. A production system with many simultaneous users would use Celery or RQ.

**Re-upload behavior:** delete existing chunks first, then re-ingest.
Replace, never silently duplicate.

**Supported types:** `.pdf`, `.docx`, `.txt`. CSV and XLSX deliberately
excluded — tabular rows embed poorly as unstructured text.

### Query endpoint (`query.py`)

Pipeline order is deliberate:

1. `route_query()` — classify the query
2. `retrieve()` — get relevant chunks
3. `assess_confidence()` — judge retrieval quality **before** generation
4. `generate()` — synthesize answer

Confidence before generation means the signal survives even if `generate()`
raises `GenerationError`. Verified in practice: a Gemini 429 quota error
returned a useful confidence signal alongside the generation failure.

### Security layer (`main.py`)

**API key authentication (middleware):** every request must carry `X-API-Key`
matching `DOCMIND_API_KEY` from `.env`. Implemented as middleware rather than
a `Depends()` — middleware runs unconditionally on every request, so new routes
are protected automatically without remembering to add a dependency.
`/health`, `/docs`, `/openapi.json`, `/redoc` are exempt.

**Rate limiting (slowapi):** `SlowAPIMiddleware` registered at app level.
Per-endpoint limits via `@limiter.limit()` decorators:
- Upload: 10/minute — prevents disk-fill loops
- Query: 20/minute — protects Gemini free-tier quota

**Startup model warm:** `lifespan` hook calls `get_model()` before accepting
requests. Moves the 2–5 second model-load cost from the first upload to server
startup where it belongs.

### Error handling contract

| Condition | HTTP status | Notes |
|---|---|---|
| Wrong/missing API key | 401 | Before any router logic |
| Rate limit exceeded | 429 | Automatic via slowapi |
| Unsupported file type (extension) | 415 | Before reading bytes |
| File too large | 413 | After read, before pipeline |
| Magic byte mismatch | 415 | After read, before pipeline |
| File has no extractable text | 422 | After load, before chunk |
| Generation failed | 500 + confidence | Confidence returned even on failure |
| Retrieval failed | 500 | |
| No chunks found | 200 with empty answer | Valid state, not an error |

### Ingestion status visibility (`GET /documents/status`)

`_ingestion_status` is an in-memory dict tracking per-file state:
`"indexing" → "indexed" | "failed"`. Updated at every exit point in
`_run_ingestion`. Exposed via `GET /documents/status` so the frontend
can surface failures as a red "Failed" badge instead of leaving the
user waiting indefinitely for a file that silently failed.
Resets on server restart — acceptable for a portfolio project.

---

## Phase 4 — Frontend

**Status:** ✅ Complete

### What it does

A single-page React workspace with two panels: document management on the left,
an interactive chat assistant on the right.

### Stack

- **TanStack Router** — file-based routing, `/workspace` route for the main UI
- **Framer Motion** — upload zone animations, message entry, confidence badge reveal
- **shadcn/ui + Tailwind v4** — component primitives and utility classes
- **Video background** — ambient looping video with fade-in/fade-out at clip boundaries

### Left panel — Document Center

- Drag-and-drop upload zone backed by `<input type="file">` — accessible and clickable
- Upload progress bar via `XMLHttpRequest` `onprogress` — `fetch` doesn't expose upload progress
- Per-document status badge: `Indexed` (emerald) / `Indexing…` (amber) / `Failed` (rose)
- Status polling: `GET /documents/status` polled every 3 seconds while any doc is
  `"indexing"` — stops polling once all docs settle, zero unnecessary requests
- Per-document delete with spinner during in-flight request
- Clear all with confirmation step before the destructive action
- `docmind:highlight` custom event listener — highlights the matching document card
  when citations are opened in the chat panel

### Right panel — Interactive Assistant

- Chat history with user and assistant bubbles, Framer Motion entry animations
- Typing indicator with staggered dot animation while awaiting response
- **Confidence badge** — `high / medium / low` with emerald/amber/rose color coding,
  spring-animated on entry
- **Citations panel** — collapsible per-message, each citation shows filename and page
  number. Opening dispatches `docmind:highlight` to the left panel
- Quick-prompt chips for common queries
- `Enter` to send

### API integration decisions

**Auth headers on every call:** `X-API-Key` added to all 7 backend call sites.
`AUTH_HEADERS` constant defined once, spread into `fetch()` options. XHR upload
uses `xhr.setRequestHeader()` separately — `Content-Type` is deliberately NOT
set on FormData XHR so the browser sets it with the correct multipart boundary.

**`crypto.randomUUID()` replaced with `genId()`** — requires HTTPS; fails on
plain `http://` in some browsers during local development.

**Backend status polling** — `GET /documents/` polled every 15 seconds as a
liveness check. Header shows animated green/amber/red pill.

**Ingestion failure polling** — `GET /documents/status` polled every 3 seconds
only while at least one document is in `"indexing"` state. Stops automatically.

### Visual design decisions

- **No `backdrop-blur`** on any component — removed so the video background
  stays fully visible. Glass effect achieved through low-opacity fills alone.
- **Violet accent** throughout — send button, drag-over glow, citation badges,
  highlighted document cards.
- **Citation ↔ document panel decoupling** via custom DOM events — no prop
  drilling or shared state between panels.

---

## Phase 5 — Deployment

**Status:** Not started

*To be filled in: Dockerization, deployment target, production considerations
(ChromaDB persistence on target platform, secrets management, OCR binary
paths for Linux, CORS tightened to actual frontend domain).*

---

## Key Design Decisions Log

| Decision | Choice | Reasoning |
|---|---|---|
| Chunk size | 500 tokens | Large enough to preserve context, small enough for precise retrieval |
| Chunk overlap | 75 tokens | Prevents meaning loss at chunk boundaries |
| Chunk sizing unit | Tokens, not characters | Maps directly to what the embedding model processes |
| tiktoken failure | Fallback to `len//4` | Avoids crashing in network-restricted environments |
| Embedding model | `all-MiniLM-L6-v2` local | Free, no API key, isolated — swap to hosted later without touching upstream |
| Model loading | Eager at startup via lifespan | First upload was paying 2–5s load cost; moved to startup where it belongs |
| Vector store | ChromaDB persistent | Data survives app restarts |
| ChromaDB client cache | Dict keyed by path | Single global client silently returns wrong data for different paths |
| ChromaDB record IDs | `source_file::chunk_id` | `chunk_id` alone is not unique across multiple documents |
| ChromaDB distance metric | Cosine (explicit) | Default is squared L2; `1 - distance` only works for cosine |
| DOCX locator | Paragraph index | True page numbers unavailable in DOCX |
| OCR fallback | Per-page, Tesseract | Scanned PDFs common; per-page avoids loading all images at once |
| OCR paths | Hardcoded, not PATH | Windows PATH unreliable across shells and venvs |
| TXT support | Single page, no loader | Plain text needs no parsing; wrapped in standard page dict shape |
| Query routing | Gemini structured JSON | Schema-constrained output guarantees valid route values |
| Router failure | Silent fallback to `retrieve` | Wrong routing degrades gracefully |
| LLM provider | Gemini (both modules) | One provider, one SDK, one auth setup |
| Citation mechanism | LLM cites by chunk index | Position-based; no `chunk_id` in retriever output |
| Generation failure | Raise `GenerationError` | No safe default answer; silent fallback would mislead |
| `.env` loading | `Path(__file__)`-relative | Working-directory-relative `load_dotenv()` breaks for different invocation paths |
| Confidence output | Three named levels | Explicit judgment in one place; raw float pushes interpretation into UI |
| Confidence placement | Separate module | API can surface it even when generation fails |
| Confidence metric | Max similarity | One strong hit means something relevant was found |
| Confidence ordering | Assessed before generation | Signal survives `GenerationError` — verified in practice with Gemini 429 |
| Re-upload behavior | Replace, not reject | Delete existing chunks then re-ingest; no silent duplication |
| CSV/XLSX support | Excluded | Tabular rows embed poorly; needs row-to-sentence serialization first |
| Background ingestion | BackgroundTasks | Returns 202 immediately; pipeline runs after response is sent |
| Upload response code | 202 Accepted | Signals async processing; browser unblocks immediately |
| Ingestion status | In-memory dict + /status endpoint | Surfaces failures as "Failed" badge; frontend polls only while needed |
| File size limit | 25 MB, checked after read | Content-Length is client-controlled; `len(contents)` is reliable |
| Magic byte validation | Inline, no library | No `libmagic` system dependency; PDF/DOCX/TXT headers are stable and simple |
| Filename sanitisation | `Path().name` + regex strip | Strips traversal and shell metacharacters before use as DB key |
| API key auth | HTTP middleware | Middleware protects all routes automatically; `Depends()` requires per-endpoint decoration |
| Rate limiting | slowapi, per-endpoint | Per-endpoint lets upload (10/min) and query (20/min) have different budgets |
| Upload rate limit | 10/minute | Prevents disk-fill loops |
| Query rate limit | 20/minute | Protects Gemini free-tier quota |
| Auth exemptions | /health, /docs, /openapi.json | Liveness check and local dev browsing must work without a key |
| Upload progress | `XMLHttpRequest` | `fetch` API does not expose upload progress events |
| XHR Content-Type | Not set manually | Browser sets multipart boundary automatically; manual override breaks it |
| UUID generation | Custom `genId()` | `crypto.randomUUID()` requires HTTPS; fails in plain HTTP dev environments |
| Confidence badge | Per-message, frontend | Each answer carries its own confidence signal |
| Citation highlight | Custom DOM event | Decouples chat panel from document panel without prop drilling |
| `backdrop-blur` | Removed from all panels | Video background fully visible; glass effect via opacity alone |
| Status poll frequency | 3s while indexing, stops after | Zero unnecessary requests once all docs settle |
| CORS | `*` in dev | Tighten to actual frontend domain at deployment |