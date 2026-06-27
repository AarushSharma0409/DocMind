# DocMind

**Multi-document RAG with citations, confidence signaling, and intelligent query routing.**

DocMind lets you upload a set of documents and ask questions across all of them. Every answer tells you exactly which document and page it came from, and tells you honestly when it isn't sure.

> Built as an AI/ML portfolio project — every module was built, tested, and understood from scratch, not assembled from tutorials.

---

## What makes this different

Most RAG demos answer questions and stop there. DocMind is built around three things most student projects skip:

**Source citations** — every answer traces back to the exact document and page (or paragraph, for DOCX) it came from. Not just the filename — the specific location, so you can verify it yourself.

**Confidence signaling** — if retrieval quality is weak, DocMind says so. You get a `high / medium / low` confidence badge alongside every answer, derived from the actual similarity scores of what was retrieved — not a post-hoc guess.

**Query routing** — before touching the vector database, DocMind classifies the query. "Summarize the report" shouldn't trigger a similarity search. "What did the Q3 report say about churn versus recent industry benchmarks?" needs document context *and* reasoning. The router decides.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        React Frontend                           │
│         Upload zone · Chat UI · Citations · Confidence          │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP
┌────────────────────────────▼────────────────────────────────────┐
│                       FastAPI Backend                           │
│   POST /documents/upload · GET /documents/ · POST /query/       │
└──────┬──────────────────────────────────────────┬───────────────┘
       │ Ingest                                   │ Query
┌──────▼──────────────┐               ┌───────────▼───────────────┐
│  Ingestion Pipeline │               │      Query Pipeline        │
│                     │               │                           │
│  loaders.py         │               │  query_router.py          │
│  PDF/DOCX/TXT +OCR  │               │  Gemini classifies query  │
│         ↓           │               │         ↓                 │
│  chunker.py         │               │  retriever.py             │
│  500-token chunks   │               │  cosine similarity search │
│  75-token overlap   │               │         ↓                 │
│         ↓           │               │  confidence.py            │
│  embedder.py        │               │  judges retrieval quality │
│  all-MiniLM-L6-v2  │               │         ↓                 │
│         ↓           │               │  generator.py             │
│  vector_store.py    │               │  Gemini synthesizes answer│
│  ChromaDB on disk   │               │  with citations by index  │
└─────────────────────┘               └───────────────────────────┘
                             │
                    ChromaDB (persistent)
                    survives app restarts
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
| Testing | `pytest` · `unittest.mock` · 167 tests across 7 modules |

---

## Project structure

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── main.py          # FastAPI app, CORS, startup
│   │   │   ├── documents.py     # Upload + list endpoints
│   │   │   └── query.py         # Query endpoint — full RAG pipeline
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
│   └── .env                     # GEMINI_API_KEY (not committed)
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
- For OCR support on scanned PDFs: [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and [Poppler](https://github.com/oschwartz10612/poppler-windows/releases/)

### Backend

```bash
# Clone the repo
git clone https://github.com/AarushSharma0409/DocMind.git
cd DocMind/backend

# Install dependencies
pip install -r requirements.txt

# Create .env
echo "GEMINI_API_KEY=your_key_here" > .env

# Start the server
uvicorn app.api.main:app --reload
# → running at http://127.0.0.1:8000
```

### Frontend

```bash
cd DocMind/frontend

npm install
npm run dev
# → running at http://localhost:5173
```

### Run tests

```bash
cd backend

# All unit tests (no API calls, fast)
python -m pytest tests/ -v -m "not integration"

# With real Gemini API (integration tests)
python -m pytest tests/ -v
```

---

## API reference

### `POST /documents/upload`

Upload a document for ingestion.

```
Content-Type: multipart/form-data
Body: file (PDF, DOCX, or TXT — up to 25MB)
```

```json
{
  "message": "report.pdf ingested successfully",
  "chunks_stored": 42,
  "source_file": "report.pdf"
}
```

### `GET /documents/`

List all currently indexed documents.

```json
{
  "documents": ["report.pdf", "notes.docx", "transcript.txt"]
}
```

### `POST /query/`

Ask a question across all indexed documents.

```json
{ "query": "What were the key findings in the Q3 report?" }
```

```json
{
  "answer": "The Q3 report identified three key findings...",
  "citations": [
    {
      "source_file": "q3_report.pdf",
      "page_number": 7,
      "locator_type": "page",
      "excerpt": "Revenue grew 12% year-over-year..."
    }
  ],
  "confidence": {
    "level": "high",
    "reason": "Strong similarity scores across multiple retrieved chunks."
  },
  "route": "retrieve"
}
```

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

**167 unit tests total.** Every module was built with tests before the next module started.

---

## Key engineering decisions

A few decisions that are worth calling out explicitly, because they're the ones an interviewer would ask about:

**ChromaDB distance metric** — ChromaDB's default distance metric is squared L2, not cosine. `1 - distance` is only valid for cosine. This produced all-zero similarity scores until explicitly setting `{"hnsw:space": "cosine"}` on collection creation. Caught by writing tests that asserted exact similarity values, not just result ordering.

**Confidence before generation** — the query pipeline assesses confidence *before* calling `generate()`. This means if generation fails (e.g. a Gemini 429 rate-limit), the API still returns a useful confidence signal. Verified in practice — a real Gemini quota error returned `confidence: medium, score: 0.55` alongside the generation failure.

**OCR as a per-page fallback** — `pypdf` returns empty strings for scanned PDF pages. Rather than failing or requiring a separate OCR-only path, `load_pdf()` detects image-only pages and falls back to Tesseract per page. Text pages take the fast path with zero OCR overhead.

**Citations by chunk index, not chunk_id** — the LLM is prompted to cite chunks by their position in the prompt (`chunk_index: 2`), not by a `chunk_id` field. This is because `retriever.py` doesn't return a `chunk_id`. Caught by checking the actual output contract of each module before building the next one.

**Two LLM providers → one** — an early draft of `generator.py` used Groq (`llama-3.1-8b-instant`) while `query_router.py` used Gemini. Reversed deliberately before the code shipped: two providers means two SDKs, two auth setups, two failure modes in one pipeline.

---

## Supported file types

| Format | How it's parsed | Citation locator |
|---|---|---|
| PDF (text-based) | `pypdf` | Page number |
| PDF (scanned) | `pypdf` + Tesseract OCR fallback | Page number |
| DOCX | `python-docx` | Paragraph index (true position) |
| TXT | Direct read | Single page |
| CSV / XLSX | ❌ Not supported | Tabular rows embed poorly as unstructured text |

---

## License

MIT — see [LICENSE](LICENSE)