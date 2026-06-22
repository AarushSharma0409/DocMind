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
    ├── loaders.py      — PDF / DOCX parsing
    ├── chunker.py      — token-based splitting with overlap
    ├── embedder.py     — local sentence-transformers model
    └── vector_store.py — ChromaDB persistent storage
    ↓
Query pipeline
    ├── query_router.py  — Gemini classifies: vector / web / hybrid
    ├── retriever.py     — cosine similarity search in ChromaDB
    ├── generator.py     — Gemini synthesizes answer with citations
    └── confidence.py    — judges retrieval quality, signals low confidence
    ↓
Cited response + confidence level → frontend
```

---

## Phase 1 — Document Ingestion & Chunking

**Status:** Complete

### What it does

Accepts a PDF or DOCX file and produces a set of embedded, metadata-tagged
chunks stored persistently in ChromaDB. Every chunk carries the information
needed for a traceable citation: which file it came from, and where in that
file it lives.

### Loaders (`loaders.py`)

Both `load_pdf()` and `load_docx()` return the same output shape:

```
[{"page_number": int, "locator_type": str, "text": str}, ...]
```

This unified shape means nothing downstream needs to know which loader
produced the data. The `locator_type` field encodes the difference:

- PDFs use `"page"` — real, fixed page numbers that match what the user sees.
- DOCX files use `"paragraph_index"` — because Word pages depend on fonts and
  margins not stored in the file, so true page numbers are unavailable. Paragraph
  position is a stable proxy that the citation UI (Phase 4) can render as `"¶12"`.

**Bug caught before shipping:** the original DOCX loader used a dense counter
that only incremented on non-empty paragraphs, producing positions `[1, 2, 3]`
instead of true positions `[1, 3, 6]` when blank paragraphs were skipped. A
citation pointing to position 2 when the content is actually at position 3 is
silently wrong — no crash, no error, just a user who can't verify the source.
Fixed by using `enumerate()` to preserve true position regardless of blank gaps.

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
across a chunk boundary loses meaning in both halves. The overlap is behaviorally
tested — not just documented — to ensure a future refactor can't silently drop it.

**Sentence fallback:** dense technical writing with no paragraph breaks produces
oversized "paragraphs." The chunker detects this and falls back to sentence-level
splitting rather than truncating or producing one giant chunk.

**Metadata propagation:** every chunk produced from a page inherits that page's
`page_number` and `locator_type`. This is the most critical contract in the file —
if it silently breaks, citations point to the wrong source with no visible symptom.

**Production edge case fixed:** `tiktoken` downloads its encoding file from
OpenAI's servers on first use, which fails in network-restricted environments
(Docker build steps, some CI runners). `count_tokens()` wraps this in a
try/except and falls back to `len(text) // 4` — a standard characters-per-token
approximation — so chunking can proceed rather than crashing the whole pipeline
over a token-counting utility.

### Embedder (`embedder.py`)

Attaches a vector embedding to every chunk.

**Model:** `all-MiniLM-L6-v2` via `sentence-transformers`, run locally.
Free, no API key, no per-call cost — important during development where test
documents are re-embedded constantly. Produces 384-dimensional vectors.
Tradeoff vs hosted alternatives (e.g. OpenAI's 1536-dim): less semantic nuance,
in exchange for zero cost and no network dependency. The module isolates this
decision — swapping to a hosted model later requires changing only `embedder.py`.

**Lazy singleton:** the model loads once per process on first call and is reused.
Loading a transformer model takes real seconds; reloading it on every embed call
would make the ingestion pipeline unusably slow.

### Vector Store (`vector_store.py`)

Persists embedded chunks to ChromaDB on disk.

**Why persistent, not in-memory:** an in-memory ChromaDB instance loses all data
when the process restarts. Persistent storage means uploaded documents survive
app restarts without re-ingestion.

**Bug caught: client caching across different paths.** The original design cached
a single global ChromaDB client. When the test suite called `get_client()` with a
different temporary directory per test, the cached client for the first test was
silently returned for all subsequent tests — making tests depend on run order
rather than their own logic. Fixed by caching clients in a dict keyed by path.

**ID collision prevention:** ChromaDB requires unique string IDs. `chunk_id` is
only unique within one document — every document's first chunk has `chunk_id=0`.
Record IDs are built as `f"{source_file}::{chunk_id}"` to guarantee uniqueness
across the entire knowledge base.

**Distance metric:** collection is created with `metadata={"hnsw:space": "cosine"}`
explicitly. ChromaDB's default is squared L2, not cosine — this was discovered
empirically when similarity scores were all returning `0.0` (see Phase 2 bug below).

**Metadata constraint:** ChromaDB rejects `None` in metadata fields. `source_file`
defaults to `None` for chunks without a known source; this is converted to
`"unknown"` before storage.

---

## Phase 2 — Retrieval, Query Routing & Generation

**Status:** Complete

### What it does

Takes a user's query, decides how to handle it, retrieves relevant chunks,
generates a cited answer, and signals how much to trust that answer.

### Query Router (`query_router.py`)

Before any retrieval happens, the query is classified using Gemini with
structured JSON output into one of three routes:

- `vector` — query is answerable from ingested documents
- `web` — query requires current or external knowledge
- `hybrid` — query needs both document context and external information

**Why classify first:** sending every query through vector search wastes
compute on queries that have nothing to do with the uploaded documents
("what's the weather today?"), and produces confidently wrong answers when
the documents don't contain the relevant information.

**Why Gemini with structured output:** classification needs to be reliable
and schema-constrained, not freeform. Structured JSON output from Gemini
guarantees the route field is always present and always one of the valid enum
values — no string parsing, no "maybe vector, maybe web" ambiguity.

**Failure handling:** on any API failure, the router falls back silently to
`"vector"` rather than raising. Routing wrong degrades gracefully — worst case,
a similarity search runs on a query it won't answer well. This is different from
generation (below), where silent fallback would actively mislead the user.

### Retriever (`retriever.py`)

Embeds the query with the same model used for documents, then queries ChromaDB
for the most similar stored chunks.

```
retrieve(query, top_k=5) ->
  [{"text": "...", "source_file": "...", "page_number": int,
    "locator_type": "...", "similarity": float}, ...]
```

**Why `top_k=5`:** with a 384-dim local embedding model, the most relevant chunk
could plausibly rank 4th rather than 1st due to imperfect semantic matching.
Top 5 gives the generator enough context and gives confidence signaling more
signal to work with. Configurable, not hardcoded.

**Critical bug found and fixed: ChromaDB's default distance metric.**
`retrieve()` converted ChromaDB distances to similarity using `1 - distance`,
which is only valid for cosine distance. ChromaDB's actual default is squared L2
distance. An orthogonal vector pair returned distance `2.0`; an opposite pair
returned `4.0` — which matches squared L2, not cosine. The fix was setting
`{"hnsw:space": "cosine"}` on collection creation. Before the fix, every
similarity score returned as `0.0`, which would have made confidence signaling
completely meaningless. Any data stored before this fix used the wrong metric
and must be re-ingested.

**Similarity clamping:** cosine distance can exceed the expected range in
floating-point edge cases. Similarity is clamped to `[0.0, 1.0]` to prevent
confusing negative scores reaching downstream consumers.

### Generator (`generator.py`)

Takes the query and retrieved chunks and produces a synthesized, cited answer.

```
generate(query, chunks) ->
  {"answer": "...",
   "citations": [{"source_file": "...", "page_number": int,
                  "locator_type": "...", "excerpt": "..."}, ...]}
```

**LLM provider decision:** an early draft was built on Groq (`llama-3.1-8b-instant`),
independently of the router's Gemini setup. This was caught and deliberately
reversed — two LLM providers means two SDKs, two auth setups, two failure modes
to maintain in one small pipeline. Both modules now use Gemini. The Groq version
is preserved as `generator_groq_backup.py` as a documented fallback if Gemini's
free tier becomes limiting.

**Citation by chunk index, not chunk ID:** the model cites chunks by their
position in the prompt (0-based index), not by a `chunk_id` field — because
`retriever.py` doesn't return a `chunk_id`. Position-based citation is actually
more robust: it requires no ID field to exist, only knowledge of which chunk by
position supported a claim. `_parse_generation_response()` hydrates the index
back into full citation metadata by looking up `chunks[chunk_index]` directly.

**Why LLM-side citation, not post-processing:** matching generated sentences back
to source chunks via fuzzy string matching is brittle — paraphrased answers won't
match chunk text verbatim. Instead, the model is constrained via structured output
schema to cite only chunks it was actually given, by index. This prevents
hallucinated sources structurally rather than trying to detect them after the fact.

**Hallucination guard:** the schema guarantees citation shape but not that
`chunk_index` is within range. Any out-of-range, negative, or non-integer index
is silently dropped rather than crashing the whole response.

**Failure handling:** `generate()` raises `GenerationError` on any failure —
unlike the router's silent fallback. There is no safe default answer to substitute
when generation fails. Raising one exception type (not five) means callers handle
one conceptual failure mode: "generation didn't work."

**`.env` loading fixed:** `load_dotenv()` with no arguments resolves relative to
the current working directory, which varies depending on how the script is invoked.
All modules now resolve `.env` relative to the file's own location using
`Path(__file__).resolve().parent...`, making it working-directory-independent.
This fix was applied to both `generator.py` and `query_router.py`.

### Confidence Signaling (`confidence.py`)

Reads retrieval output and returns a judgment about how much to trust the answer.

```
assess_confidence(chunks) ->
  {"level": "high" | "medium" | "low", "reason": str}
```

**Why a separate module:** confidence needs to be assessable independently of
generation. If `generate()` raises `GenerationError`, the API layer still needs
to tell the user "retrieval quality was low, which may be why." Burying confidence
inside `generate()` would silence that signal on every generation failure.

**Why three named levels, not a raw score:** returning a float to the frontend
pushes the judgment ("what does 0.43 mean?") into the UI, where it will be
implicit and untested. Three named levels make the judgment explicit, in one place,
with named threshold constants that can be tuned independently of the UI.

**Thresholds:**

| Constant | Value |
|---|---|
| `HIGH_SIMILARITY_THRESHOLD` | 0.65 |
| `LOW_SIMILARITY_THRESHOLD` | 0.35 |
| `MIN_CHUNKS_FOR_HIGH_CONFIDENCE` | 2 |

These are starting hypotheses. The right values come from observing real queries
on real documents. They are named constants, not magic numbers.

**Why max similarity, not average:** one strong hit among weak ones means
something genuinely relevant was found. Averaging would drag the score down and
incorrectly signal low confidence when the retrieval actually succeeded.

**Single-chunk rule:** one highly similar chunk gets `"medium"`, not `"high"`.
A single hit could be a precise answer or the only partial match in a weak
retrieval — the score alone can't distinguish them. Two or more chunks
independently at high similarity is a much stronger structural signal.

**Pure function:** `assess_confidence()` has no API calls, no I/O, no side
effects. Same input always produces same output. No mocking needed in tests.

---

## Phase 3 — API Layer

**Status:** Not started

_To be filled in: endpoint design, error handling approach._

---

## Phase 4 — Frontend

**Status:** Not started

_To be filled in: UI decisions, how citations and confidence are surfaced to the user._

---

## Phase 5 — Deployment

**Status:** Not started

_To be filled in: deployment setup, any production considerations._

---

## Key Design Decisions Log

| Decision | Choice | Reasoning |
|---|---|---|
| Chunk size | 500 tokens | Large enough to preserve context, small enough for precise retrieval |
| Chunk overlap | 75 tokens | Prevents meaning loss at chunk boundaries; carried in whole sentence units |
| Chunk sizing unit | Tokens, not characters | Maps directly to what the embedding model processes |
| tiktoken failure | Fallback to `len//4` | Avoids crashing in network-restricted environments (Docker, CI) |
| Embedding model | `all-MiniLM-L6-v2` local | Free, no API key, isolated — swap to hosted later without touching upstream code |
| Vector store | ChromaDB persistent | Data survives app restarts; in-memory would require re-ingestion on every restart |
| ChromaDB client cache | Dict keyed by path | Single global client silently returns wrong data when called with different paths |
| ChromaDB record IDs | `source_file::chunk_id` | `chunk_id` alone is not unique across multiple documents |
| ChromaDB distance metric | Cosine (explicit) | Default is squared L2; `1 - distance` similarity conversion only works for cosine |
| DOCX locator | Paragraph index | True page numbers unavailable in DOCX; paragraph position is a stable, honest proxy |
| Query routing | Gemini structured JSON | Schema-constrained output guarantees valid route values without string parsing |
| Router failure | Silent fallback to `vector` | Wrong routing degrades gracefully; raising here would block all queries on transient errors |
| LLM provider | Gemini (both modules) | One provider, one SDK, one auth setup; consistency outweighs any per-module optimisation |
| Citation mechanism | LLM cites by chunk index | Position-based; no `chunk_id` field exists in retriever output; prevents hallucinated sources structurally |
| Generation failure | Raise `GenerationError` | No safe default answer exists; silent fallback would actively mislead the user |
| `.env` loading | `Path(__file__)`-relative | Working-directory-relative `load_dotenv()` breaks when invoked from different directories |
| Confidence output | Three named levels | Explicit judgment in one place; raw float pushes untested interpretation into the UI |
| Confidence placement | Separate module | Keeps judgment independent of generation; API layer can surface it even when generation fails |
| Confidence metric | Max similarity | One strong hit means something relevant was found; average is dragged down by irrelevant results |