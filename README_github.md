# DocMind

**Multi-document RAG with citations, confidence signaling, query routing, and a full security layer.**

DocMind lets you upload a set of documents and ask questions across all of them. Every answer tells you exactly which document and page it came from, and tells you honestly when it isn't sure.

> Built as an AI/ML portfolio project — every module was built, tested, and understood from scratch.

---

## What makes this different

Most RAG demos answer questions and stop there. DocMind is built around four things most student projects skip:

**Source citations** — every answer traces back to the exact document and page (or paragraph, for DOCX) it came from. Not just the filename — the specific location, so you can verify it yourself.

**Confidence signaling** — if retrieval quality is weak, DocMind says so. You get a `high / medium / low` confidence badge alongside every answer, derived from actual similarity scores — not a post-hoc guess.

**Query routing** — before touching the vector database, DocMind classifies the query. "Summarize the report" shouldn't trigger a similarity search. "What did the Q3 report say about churn versus recent benchmarks?" needs document context and reasoning. The router decides.

**Security layer** — API key authentication, rate limiting, file size limits, magic byte validation, and filename sanitisation — all implemented and reasoned through, not just bolted on.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        React Frontend                           │
│    Upload zone · Chat UI · Citations · Confidence badges        │
│    X-API-Key header on every backend call                       │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP
┌────────────────────────────▼────────────────────────────────────┐
│                  FastAPI Backend (main.py)                       │
│  Auth middleware · Rate limiter · Lifespan model warm           │
└──────┬──────────────────────────────────────┬───────────────────┘
       │ Ingest                               │ Query
┌──────▼──────────────┐           ┌───────────▼───────────────────┐
│  Ingestion Pipeline │           │        Query Pipeline          │
│                     │           │                               │
│  loaders.py         │           │  query_router.py              │
│  PDF / DOCX / TXT   │           │  Gemini classifies query      │
│  + OCR fallback     │           │          ↓                    │
│         ↓           │           │  retriever.py                 │
│  chunker.py         │           │  cosine similarity search     │
│  500-token chunks   │           │          ↓                    │
│  75-token overlap   │           │  confidence.py                │
│         ↓           │           │  assessed before generation   │
│  embedder.py        │           │          ↓                    │
│  all-MiniLM-L6-v2  │           │  generator.py                 │
│  (local, no API)    │           │  Gemini synthesizes answer    │
│         ↓           │           │  with citations by index      │
│  vector_store.py    │           └───────────────────────────────┘
│  ChromaDB on disk   │
└─────────────────────┘
         ↕
   ChromaDB (persistent — survives restarts)
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React · TypeScript · Vite · TanStack Router · Tailwind v4 · Framer Motion · shadcn/ui |
| Backend | Python · FastAPI · Uvicorn |
| Embeddings | `sentence-transformers` · `all-MiniLM-L6-v2` (local, no API key) |
| Vector store | ChromaDB (persistent client, cosine similarity) |
| LLM | Google Gemini (query routing + generation) |
| OCR | Tesseract + Poppler (scanned PDF fallback) |
| Document parsing | `pypdf` · `python-docx` |
| Rate limiting | `slowapi` |
| Testing | `pytest` · `unittest.mock` · 167 tests across 8 modules |

---

## Project structure

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── main.py          # FastAPI app, auth middleware, rate limiter, lifespan
│   │   │   └── routers/
│   │   │       ├── documents.py # Upload, list, status, delete endpoints
│   │   │       └── query.py     # Query endpoint — full RAG pipeline
│   │   ├── ingestion/
│   │   │   ├── loaders.py       # PDF / DOCX / TXT parsing + OCR fallback
│   │   │   ├── chunker.py       # Token-based splitting with overlap
│   │   │   └── embedder.py      # Local sentence-transformer embeddings
│   │   ├── storage/
│   │   │   └── vector_store.py  # ChromaDB persistent client + CRUD
│   │   └── retrieval/
│   │       ├── retriever.py     # Cosine similarity search
│   │       ├── query_router.py  # Gemini query classifier
│   │       ├── generator.py     # Gemini answer synthesis + citations
│   │       └── confidence.py    # Retrieval quality signal
│   ├── tests/                   # 167 tests across all modules
│   └── .env                     # GEMINI_API_KEY + DOCMIND_API_KEY (not committed)
├── frontend/
│   └── src/
│       └── routes/
│           └── workspace.tsx    # Upload panel + chat panel
└── docs/
    └── ARCHITECTURE.md          # Design decisions with reasoning
```

---

## Setup

### Prerequisites

- Python 3.10+
- Node.js 18+
- A [Google Gemini API key](https://aistudio.google.com/) (free tier works)
- For OCR on scanned PDFs: [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and [Poppler](https://github.com/oschwartz10612/poppler-windows/releases/)

### Backend

```bash
cd DocMind/backend

pip install -r requirements.txt

# Create .env — both keys required
echo "GEMINI_API_KEY=your_gemini_key" > .env
echo "DOCMIND_API_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')" >> .env

uvicorn app.api.main:app --reload
# → http://127.0.0.1:8000
# You'll see "Model ready" before the server accepts requests
```

### Frontend

```bash
cd DocMind/frontend

npm install

# Create .env.local — must match DOCMIND_API_KEY in backend/.env
echo "VITE_DOCMIND_API_KEY=your_same_api_key" > .env.local

npm run dev
# → http://localhost:5173
```

### Run tests

```bash
cd backend

# All unit tests (no API calls)
python -m pytest tests/ -v -m "not integration"

# Including real Gemini API tests
python -m pytest tests/ -v
```

---

## API reference

All endpoints (except `/health`) require the header:
```
X-API-Key: your_api_key
```

### `POST /documents/upload`

```
Content-Type: multipart/form-data
Body: file (PDF, DOCX, TXT — max 25 MB)
```

Returns `202 Accepted` immediately. Ingestion runs in the background.

```json
{ "message": "File accepted. Ingestion running in background.", "filename": "report.pdf", "status": "indexing" }
```

### `GET /documents/`

```json
{ "documents": ["report.pdf", "notes.docx"], "count": 2 }
```

### `GET /documents/status`

Per-file ingestion status. Poll this to detect failures.

```json
{ "status": { "report.pdf": "indexed", "notes.docx": "failed" } }
```

### `POST /query/`

```json
{ "query": "What were the key findings in the Q3 report?" }
```

```json
{
  "answer": "The Q3 report identified three key findings...",
  "citations": [
    { "source_file": "q3_report.pdf", "page_number": 7, "locator_type": "page", "excerpt": "..." }
  ],
  "confidence": { "level": "high", "reason": "Strong similarity across multiple chunks." },
  "route": "retrieve"
}
```

### Error responses

| Status | Meaning |
|---|---|
| 401 | Missing or wrong `X-API-Key` |
| 413 | File over 25 MB |
| 415 | Unsupported type or magic byte mismatch |
| 422 | Empty query or no extractable text |
| 429 | Rate limit hit (10/min upload, 20/min query) |
| 500 | Pipeline failure (confidence still returned on generation failure) |

---

## Security

| Layer | Implementation |
|---|---|
| Authentication | `X-API-Key` middleware — all endpoints, automatic on new routes |
| Rate limiting | `slowapi` — 10/min upload, 20/min query, per IP |
| File size | 25 MB limit checked after read (`Content-Length` is client-controlled) |
| Magic bytes | PDF (`%PDF`), DOCX (`PK\x03\x04`), TXT (UTF-8 decodable) — checked before pipeline |
| Filename | `Path().name` strips traversal, regex strips shell metacharacters |
| Secrets | `.env` and `chroma_store/` in `.gitignore` — never committed |
| CORS | `*` in development — tighten to frontend domain at deployment |

---

## What was built and tested

| Module | Tests | What's verified |
|---|---|---|
| `loaders.py` | 16 | True paragraph positions, OCR fallback, locator types |
| `chunker.py` | 26 | Token limits, real overlap, metadata propagation, sentence fallback |
| `embedder.py` | 13 | Metadata survival, order correspondence, lazy singleton |
| `vector_store.py` | 14 | Persistence across restarts, ID collision prevention, client caching |
| `retriever.py` | 16 | Cosine similarity correctness, metadata round-trip, top_k capping |
| `query_router.py` | 23 | Schema validation, classification accuracy, failure handling |
| `generator.py` | 27 + 4 integration | Citation by index, hallucination defense, error handling |
| `confidence.py` | 32 | Thresholds, single-chunk rule, max-not-average, empty input |

**167 unit tests total.**

---

## Key engineering decisions

**ChromaDB distance metric** — ChromaDB's default is squared L2, not cosine. `1 - distance` is only valid for cosine. Produced all-zero similarity scores until setting `{"hnsw:space": "cosine"}` explicitly. Caught by tests asserting exact similarity values, not just result ordering.

**Confidence before generation** — the query pipeline assesses confidence before calling `generate()`. If generation fails (e.g. Gemini 429), the API still returns a meaningful confidence signal. Verified in practice.

**Magic bytes over extension** — extension checking is bypassed by renaming files. Magic bytes inspect the actual file header. Implemented without `python-magic` (requires a system binary) — PDF and DOCX signatures are stable and simple enough to check inline.

**Background ingestion** — `BackgroundTasks` returns 202 in ~50ms. The full pipeline (load → chunk → embed → store) runs after the response. Failure state is tracked in `_ingestion_status` and exposed via `GET /documents/status` so the frontend can show a "Failed" badge instead of leaving the user waiting indefinitely.

**Auth as middleware not Depends()** — `Depends()` requires adding the dependency to every endpoint. Middleware runs unconditionally, so new routes are protected automatically without remembering to add anything.

**Two LLM providers → one** — an early draft of `generator.py` used Groq while `query_router.py` used Gemini. Consolidated to Gemini: one provider, one SDK, one auth setup, one failure mode.

**Citations by chunk index** — the LLM cites chunks by position in the prompt, not by `chunk_id`. This is because `retriever.py` doesn't return a `chunk_id`. Caught by checking the actual output contract of each module before building the next.

---

## Supported file types

| Format | How it's parsed | Citation locator |
|---|---|---|
| PDF (text-based) | `pypdf` | Page number |
| PDF (scanned) | `pypdf` + Tesseract OCR per page | Page number |
| DOCX | `python-docx` | Paragraph index (true position) |
| TXT | Direct read | Single page |
| CSV / XLSX | Not supported | Tabular rows embed poorly as unstructured text |

---

## License

MIT — see [LICENSE](LICENSE)