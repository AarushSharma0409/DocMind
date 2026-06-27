# DocMind — Architecture & Design Decisions

This document is updated after every phase. It's not just a diagram — it's
a record of *why* each decision was made, so it can double as interview prep later.

---

## System Overview

```
React frontend
    ↓
FastAPI backend
    ↓
Document ingestion pipeline
    ├── loaders.py      — PDF (text + OCR fallback) / DOCX / TXT parsing
    ├── chunker.py      — token-based splitting with overlap
    ├── embedder.py     — local sentence-transformers model
    └── vector_store.py — ChromaDB persistent storage
    ↓
Query pipeline
    ├── query_router.py  — Gemini classifies: retrieve / full_document / no_retrieval
    ├── retriever.py     — cosine similarity search in ChromaDB
    ├── generator.py     — Gemini synthesizes answer with citations
    └── confidence.py    — judges retrieval quality, signals low confidence
    ↓
FastAPI API layer
    ├── POST /documents/upload  — ingest PDF / DOCX / TXT
    ├── GET  /documents/        — list ingested documents
    └── POST /query/            — full RAG pipeline, returns answer + citations + confidence
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

**OCR fallback (added Phase 3):** scanned PDFs contain images of text rather
than embedded text — `pypdf` returns empty strings for these pages. `load_pdf()`
now detects image-only pages and runs Tesseract OCR via `pdf2image` as a
per-page fallback. Text-based pages still take the fast path with no OCR
overhead. A 15-page fully scanned document was successfully ingested and queried
end-to-end after this addition.

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

**Lazy singleton:** the model loads once per process on first call and is
reused. Loading a transformer model takes real seconds; reloading it on every
embed call would make ingestion unusably slow.

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
still needs to tell the user "retrieval quality was low." Burying confidence
inside `generate()` would silence that signal on every generation failure.

**Thresholds:** `HIGH_SIMILARITY_THRESHOLD = 0.65`, `LOW_SIMILARITY_THRESHOLD = 0.35`,
`MIN_CHUNKS_FOR_HIGH_CONFIDENCE = 2`. Named constants, not magic numbers.

**Single-chunk rule:** one highly similar chunk gets `"medium"` not `"high"` —
it could be a precise answer or the only partial hit in a weak retrieval.

**Max, not average:** `assess_confidence()` uses `max(similarities)`. One strong
hit among weak ones means something relevant was found — averaging would
incorrectly drag the score down and signal low confidence.

**Pure function:** no API calls, no I/O. 32 tests run in under 0.1 seconds.

---

## Phase 3 — API Layer

**Status:** ✅ Complete

### What it does

Three FastAPI endpoints that wire the ingestion and query pipelines together
and expose them over HTTP for the React frontend.

### Endpoints

```
POST /documents/upload  — ingest a PDF, DOCX, or TXT file
GET  /documents/        — list all ingested source files
POST /query/            — run the full RAG pipeline
GET  /health            — liveness check
```

### Upload endpoint (`documents.py`)

File type validation happens at the boundary before any pipeline work. The
uploaded file is written to a temp file (loaders need a path, not a byte
stream), then the full ingestion pipeline runs: load → chunk → embed → store.
Re-uploading the same filename deletes existing chunks first — replace, never
duplicate. Temp file is always cleaned up in a `finally` block.

**Supported types:** `.pdf`, `.docx`, `.txt`. CSV and XLSX were considered
and deliberately excluded — structured tabular data embeds poorly as raw text
because rows like `"Q3,4200000,12%"` have no natural language context for the
embedding model to work with.

### Query endpoint (`query.py`)

Pipeline order is deliberate:

1. `route_query()` — classify the query
2. `retrieve()` — get relevant chunks
3. `assess_confidence()` — judge retrieval quality **before** generation
4. `generate()` — synthesize answer

Confidence is assessed before generation so the signal survives even if
`generate()` raises `GenerationError`. This was verified in practice: a
Gemini 429 quota error returned a useful confidence signal (`medium, 0.55`)
alongside the generation failure, rather than losing both.

### CORS

Configured at startup to allow `*` origins — required for the React frontend
running on a different port during development. Replace with the actual
frontend domain in production.

### Error handling contract

| Condition | HTTP status | Notes |
|---|---|---|
| Unsupported file type | 415 | Rejected before pipeline runs |
| File parses but has no text | 422 | After load, before chunk |
| Generation failed | 500 with confidence | Confidence returned even on failure |
| Retrieval failed | 500 | |
| No chunks found | 200 with empty answer | Not an error — valid state |

---

## Phase 4 — Frontend

**Status:** ✅ Complete

### What it does

A single-page React workspace with two panels: document management on the left,
an interactive chat assistant on the right. Built on a Lovable-generated base
(`hero-hues-shine`) using TanStack Router, then extended with all DocMind-specific
functionality.

### Stack

- **TanStack Router** — file-based routing, `/workspace` route for the main UI
- **Framer Motion** — upload zone animations, message entry, confidence badge reveal
- **shadcn/ui + Tailwind v4** — component primitives and utility classes
- **Video background** — ambient looping video with fade-in/fade-out at clip boundaries

### Left panel — Document Center

- Drag-and-drop upload zone backed by an `<input type="file">` (not a bare `div`)
  so the label remains fully accessible and clickable
- Upload progress bar via `XMLHttpRequest` `onprogress` — `fetch` doesn't expose
  upload progress
- Per-document indexed/indexing status badge
- Per-document delete with spinner during in-flight request
- Clear all with confirmation step before the destructive action
- Documents load from `GET /documents/` on mount and stay in sync with uploads/deletes

### Right panel — Interactive Assistant

- Chat history with user and assistant bubbles, Framer Motion entry animations
- Typing indicator with staggered dot animation while awaiting response
- **Confidence badge** — `high / medium / low` with color coding (emerald/amber/rose),
  animated in with a spring scale effect
- **Citations panel** — collapsible per-message, each citation shows filename and page
  number. Opening citations dispatches a `docmind:highlight` custom event — the left
  panel listens and highlights the relevant document card, scrolling it into view
- Quick-prompt chips for common queries
- `Enter` to send, `Shift+Enter` pass-through for future multiline support

### API integration decisions

**`crypto.randomUUID()` replaced with `genId()`** — `crypto.randomUUID()` requires
HTTPS (or localhost). The app runs on plain `http://` in development, which would
throw silently or crash in some browsers. A `Math.random() + Date.now()` fallback
is collision-resistant enough for UI keys.

**Response parsing** — the query response shape from FastAPI is
`{answer, citations, confidence: {level, reason}, route}`. The frontend maps
`confidence.level` → `"high" | "medium" | "low"` and normalizes the citation
fields (`source_file` → `source`, `page_number` → `page`) for display.

**Backend status polling** — `GET /documents/` is polled every 15 seconds and used
as a liveness check. The header shows an animated green/amber/red pill.

### Visual design decisions

- **Dark glass aesthetic** — near-transparent panels (`bg-white/[0.015]`) over a
  looping video background. No `backdrop-blur` on any component — removed to keep
  the background fully visible.
- **Violet accent** — send button, drag-over glow, citation number badges, highlighted
  document cards all use `violet-400/500` for visual consistency.
- **Zero decoration on the functional chrome** — the header, status pill, and panel
  borders are intentionally quiet so the video and chat content dominate.

---

## Phase 5 — Deployment

**Status:** Not started

*To be filled in: Dockerization, deployment target, production considerations
(ChromaDB persistence on the target platform, secrets management, OCR binary paths).*

---

## Key Design Decisions Log

| Decision | Choice | Reasoning |
|---|---|---|
| Chunk size | 500 tokens | Large enough to preserve context, small enough for precise retrieval |
| Chunk overlap | 75 tokens | Prevents meaning loss at chunk boundaries |
| Chunk sizing unit | Tokens, not characters | Maps directly to what the embedding model processes |
| tiktoken failure | Fallback to `len//4` | Avoids crashing in network-restricted environments |
| Embedding model | `all-MiniLM-L6-v2` local | Free, no API key, isolated — swap to hosted later without touching upstream |
| Vector store | ChromaDB persistent | Data survives app restarts |
| ChromaDB client cache | Dict keyed by path | Single global client silently returns wrong data for different paths |
| ChromaDB record IDs | `source_file::chunk_id` | `chunk_id` alone is not unique across multiple documents |
| ChromaDB distance metric | Cosine (explicit) | Default is squared L2; `1 - distance` only works for cosine |
| DOCX locator | Paragraph index | True page numbers unavailable in DOCX |
| OCR fallback | Per-page, Tesseract | Scanned PDFs common in enterprise; per-page avoids loading all images at once |
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
| CSV/XLSX support | Excluded | Tabular rows embed poorly; needs row-to-sentence serialization before being RAG-ready |
| Upload progress | `XMLHttpRequest` | `fetch` API does not expose upload progress events |
| UUID generation | Custom `genId()` | `crypto.randomUUID()` requires HTTPS; fails in plain HTTP dev environments |
| Confidence badge placement | Per-message, frontend | Each answer carries its own confidence signal, not a global state |
| Citation highlight | Custom DOM event | Decouples chat panel from document panel without prop drilling or shared state |
| `backdrop-blur` | Removed from all panels | Keeps video background fully visible; glass effect achieved through opacity alone |