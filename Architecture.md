# DocMind - Architecture & Design Decisions

This document is updated after every phase. It's not just a diagram - it's a record of *why* each decision was made, so it can double as interview prep later.

---

## System Overview

```
React frontend -> FastAPI backend -> ingestion/chunking -> ChromaDB (persistent)
-> retrieval + query routing -> LLM (Anthropic API) -> cited response
```

---

## Phase 1 - Document Ingestion & Chunking

**Status:** COMPLETE - all 4 pieces done, 69 automated tests passing

### Sub-pieces

| Piece | Status | File |
|---|---|---|
| Document loading (PDF/DOCX parsing) | Done | `app/ingestion/loaders.py` |
| Chunking (token-sized, with overlap) | Done | `app/ingestion/chunker.py` |
| Embedding generation | Done | `app/ingestion/embedder.py` |
| ChromaDB persistent storage | Done | `app/storage/vector_store.py` |

### Document loading - design decisions

Two loaders, one shared output contract:

```
load_pdf(path)  -> [{"page_number": int, "locator_type": "page", "text": str}, ...]
load_docx(path) -> [{"page_number": int, "locator_type": "paragraph_index", "text": str}, ...]
```

**Why `locator_type` exists:** PDFs have real, fixed pages. DOCX files
don't - "pages" in Word depend on fonts, margins, and the rendering
engine, none of which are stored in the file. So `load_docx` uses
paragraph position as a stable proxy instead, and every entry is tagged
with `locator_type` so downstream code (the citation UI in Phase 4)
knows whether `page_number` means a true page or a paragraph index, and
can render "Page 4" vs. "Para. 12" correctly without inspecting the file
extension itself.

**A real bug caught and fixed:** an early version of `load_docx` used a
counter that only incremented on non-empty paragraphs, making
`page_number` a count of "non-blank paragraphs seen so far" rather than
the paragraph's true position in the document. This was a silent bug -
no crash, no error - it just meant any citation pointing past the first
skipped blank paragraph would be wrong by however many blanks preceded
it. Fixed by using the true index from `enumerate()` instead, gaps from
skipped blanks preserved, not compacted. This is now a permanent
regression test (`test_load_docx_preserves_true_position` in
`tests/test_loaders.py`) so it can't silently come back.

### Chunking - design decisions

**Why chunking is needed at all:** a single page can be too large to
embed meaningfully (embedding models work best on focused chunks of a
few hundred tokens, not entire pages) and too coarse for precise
retrieval (a page covering 3 topics shouldn't have a query about topic 1
retrieve irrelevant topics 2 and 3 along with it).

**Strategy: recursive splitting with overlap.**
- Split on paragraph breaks first (cleanest semantic boundary)
- Fall back to sentence breaks if a single paragraph is itself larger
  than the chunk size (rare, but happens with dense or oddly-formatted
  text that has no paragraph breaks at all)
- Consecutive chunks share a small overlap of tokens, so content sitting
  right at a chunk boundary still has full context in at least one of
  the two chunks it spans

**Parameters chosen:** `CHUNK_SIZE_TOKENS = 500`, `CHUNK_OVERLAP_TOKENS = 75`
- A reasonable middle ground: large enough to preserve context within a
  chunk, small enough to keep retrieval precise

**Why tokens, not characters:** chunk size is measured in tokens because
that's what actually maps to an embedding model's input limit. A
"2000 character" chunk could be 300 or 600 tokens depending on the text;
a "500 token" chunk stays consistent with what the model actually
processes.

**A real deployment edge case found and handled:** `tiktoken` (the
tokenizer library used for counting) downloads its encoding file from
OpenAI's servers on first use and caches it locally. This means token
counting can fail in network-restricted environments - some Docker
build steps, certain CI runners, sandboxed environments. `count_tokens()`
wraps the encoding load in a try/except and falls back to a
characters-per-token approximation (`len(text) // 4`, a standard rule of
thumb for English text) if the network call fails, so chunking degrades
gracefully instead of crashing the whole ingestion pipeline over a
token-counting utility.

**Metadata propagation:** every chunk produced from a page inherits that
page's `page_number` and `locator_type`. When a single page is long
enough to produce multiple chunks, *all* of those chunks still carry the
correct page metadata - verified with a dedicated test
(`test_chunk_document_propagates_page_number_to_every_chunk`) since this
is the exact mechanism that keeps citations accurate once a page needs
splitting.

**`source_file` added (during the storage piece, retrofitted here):**
`chunk_document()` accepts an optional `source_file` parameter,
propagated into every chunk it produces. This was added once ChromaDB
storage made it clear that `page_number` alone is ambiguous once a
single collection holds chunks from *multiple* documents - "page 4" of
which file? `source_file` makes every chunk traceable to a specific
document, not just a specific position within whichever document it
came from. Defaults to `None` so existing callers aren't broken.

### Embedding generation - design decisions

**Why a local model, not a hosted API:** `all-MiniLM-L6-v2` via
`sentence-transformers`, run entirely on-machine - free, no per-call
cost, no API key needed. This matters specifically during development,
where test documents get re-embedded constantly while debugging; a
hosted API would mean real cost and latency on every iteration. The
module isolates this decision (model choice lives in one place), so
swapping to a hosted embedding API later - for a production deployment
story, or higher embedding quality - wouldn't require touching
`chunker.py` or anything upstream.

**The tradeoff being made:** 384-dimensional vectors, smaller than some
hosted alternatives (e.g. OpenAI's `text-embedding-3-small` is
1536-dim). Less nuance captured per chunk, in exchange for speed and
zero cost - a real, explainable tradeoff, not an accidental limitation.

**Batched, not per-chunk:** all chunk texts from a document are encoded
in a single `model.encode()` call rather than looped one at a time -
meaningfully faster, and a detail worth being able to explain in an
interview ("I batch embeddings instead of making N separate calls").

**A real deployment edge case found and handled (same class of issue as
tiktoken):** `sentence-transformers` downloads model weights from
HuggingFace's hub on first use and caches them locally. Unlike
`tiktoken`'s token-counting fallback, there's no meaningful "approximate
embedding" - an embedding *is* the model's output, no cheap substitute
exists. So instead of silently degrading, `get_model()` fails loudly
with a clear, actionable error message on load failure (likely a
network issue) rather than letting a cryptic low-level exception
propagate, or worse, silently producing garbage vectors.

**A real Windows-specific issue hit and resolved during development:**
`torch` (a dependency of `sentence-transformers`) requires the Microsoft
Visual C++ Redistributable to load its compiled DLLs on Windows -
without it, import fails with `OSError: [WinError 126]`. Installing the
redistributable and restarting the machine resolved it. Worth knowing
as a real setup consideration for anyone else running this project on
Windows, not just a one-off personal hiccup.

**Verified on real hardware, not just mocked:** the real model was
downloaded and run for real on the development machine - confirmed
genuine 384-dimensional embeddings produced correctly. The automated
test suite uses a mock model (`FakeModel`) to test the surrounding logic
quickly and without network dependency, but the actual integration was
also confirmed working end-to-end on real infrastructure.

### ChromaDB persistent storage - design decisions

**Why ChromaDB, and why a persistent client specifically:** ChromaDB
handles similarity-search math (cosine distance between vectors)
internally, so Phase 2's retrieval code won't need to implement that
itself. A persistent client (not in-memory) writes to disk, so the
knowledge base survives app restarts - without this, every uploaded
document would need re-embedding from scratch on every restart.

**A real bug caught and fixed: client caching across different paths.**
The original design used a single global client variable, set once and
reused - the same lazy-singleton pattern used in `embedder.py`'s
`get_model()`. This breaks silently the moment the client is requested
for a *second, different* storage path: the first path's client gets
returned regardless of what was actually asked for, pointing at the
wrong data with no error. This wasn't just a hypothetical risk - it was
caught specifically *because* the test suite needed isolated temporary
directories per test, and the bug would have made tests silently share
state and pass/fail based on execution order rather than their actual
logic. Fixed by caching clients in a dict keyed by `persist_dir`, so
different paths correctly produce different client instances while
still reusing the same instance for repeated calls to the same path.
Now a permanent regression test
(`test_get_client_returns_different_instances_for_different_paths`).

**The ID collision problem, and how it's solved:** ChromaDB requires a
unique string ID per record. `chunk_id` alone is only unique *within*
one document - every document's first chunk has `chunk_id=0`. Storing
chunks from multiple documents in one collection means every document's
chunk 0 would try to claim the same ID. Solved by building the actual
ChromaDB record ID as `f"{source_file}::{chunk_id}"`, combining the two
fields added specifically to solve this kind of ambiguity. Verified with
a test that stores `chunk_id=0` from two different documents and
confirms both land as genuinely separate records.

**Metadata type constraints:** ChromaDB's metadata fields only accept
`str`, `int`, `float`, or `bool` - not `None`. Since `source_file`
legitimately defaults to `None` for backward-compatible callers, storage
explicitly converts `None` to the string `"unknown"` before writing,
rather than letting an otherwise-valid chunk fail to store over a type
mismatch.

**Test coverage:** 14 tests in `test_vector_store.py`, using real
ChromaDB (not mocked, since it requires no network access) with an
isolated temporary directory per test. Covers the client-caching fix,
basic store/count behavior, persistence across a simulated app restart,
multi-document ID isolation, scoped deletion, and metadata sanitization.

**Overall Phase 1 test coverage:** 69 automated tests across all four
modules (16 + 26 + 13 + 14), all passing. Two genuine bugs were found
and fixed along the way (the DOCX position-indexing bug, and the
ChromaDB client-caching bug) - both are now permanent regression tests,
not just one-off fixes.

---

## Phase 2 - Retrieval, Query Routing & Generation

**Status:** Not started

_To be filled in: retrieval strategy, query routing logic, citation extraction approach, confidence signaling design._

---

## Phase 3 - API Layer

**Status:** Not started

_To be filled in: endpoint design, error handling approach._

---

## Phase 4 - Frontend

**Status:** Not started

_To be filled in: UI decisions, how citations are surfaced to the user._

---

## Phase 5 - Deployment

**Status:** Not started

_To be filled in: deployment setup, any production considerations._

---

## Key Design Decisions Log

| Decision | Choice | Reasoning |
|---|---|---|
| Locator metadata for DOCX | Paragraph index, tagged with `locator_type` | DOCX has no fixed pages; paragraph index is the finest deterministic, traceable proxy available |
| Chunk size unit | Tokens, not characters | Tokens map directly to the embedding model's input limit; characters don't |
| Chunk size / overlap | 500 tokens / 75 tokens | Balances context richness against retrieval precision |
| Chunking strategy | Recursive: paragraph -> sentence -> raw fallback | Avoids cutting text mid-thought; only degrades to cruder splitting when necessary |
| Token counting on no network | Fall back to chars/4 approximation | Keeps ingestion working in restricted environments instead of crashing on a utility function |
| Metadata propagation | Every chunk inherits source page's `page_number`/`locator_type` | Citations must stay accurate even when one page produces multiple chunks |
| Document-level traceability | Added `source_file` to every chunk | `page_number` alone is ambiguous once a knowledge base holds multiple documents |
| Embedding model | `all-MiniLM-L6-v2`, local, via `sentence-transformers` | Free, fast dev iteration, no API cost/key; swappable later for a hosted model |
| Embedding batching | One batched `encode()` call per document | Meaningfully faster than per-chunk calls |
| Vector storage | ChromaDB, `PersistentClient` (not in-memory) | Knowledge base must survive app restarts |
| ChromaDB record ID | `f"{source_file}::{chunk_id}"` | `chunk_id` alone collides across documents (every doc's first chunk is 0) |
| ChromaDB client caching | Dict keyed by `persist_dir`, not a single global | A single shared client silently broke when used with multiple storage paths |

---

## Future Scope (deliberately deferred, not forgotten)

Ideas considered during development that were intentionally scoped OUT
of the current build, with reasoning for why - so they can be revisited
later without re-litigating the decision from scratch.

### Web-augmented retrieval (hybrid RAG)

**The idea:** extend DocMind beyond answering questions only from
user-uploaded documents, to also optionally pull in live web content -
e.g. "ask about this report, and also bring in current context from the
web."

**Why it's a genuinely good idea, not a bad one:** hybrid RAG (combining
private/uploaded documents with live web search) is a real, advanced
RAG pattern, not a gimmick. It would be a legitimate showcase of harder
system design: deciding when a query needs document context vs. web
context vs. both, merging and ranking results from two very different
source types, and handling all the reliability problems live web data
introduces (failed fetches, JS-rendered pages, rate limits, stale or
contradictory content).

**Why it was deferred rather than built now:**
1. It changes what DocMind fundamentally *is* - the current pitch
   ("upload documents, ask questions, get cited answers") is clean and
   demoable. Adding a second data source muddies that story and roughly
   doubles the design surface (new ingestion path, new error handling,
   likely a search API dependency, a routing layer to decide which
   source(s) a query needs).
2. It competes directly with the existing timeline - DocMind is the
   first of three planned portfolio projects (DocMind -> DomainLLM ->
   AutoAgent) on a ~13-week schedule. Depth on the current scope beats
   breadth that risks leaving multiple things half-finished.
3. There's already a natural home for this idea once it's time to build
   it: the query-routing layer planned for Phase 2 ("does this query
   even need vector search, or can it be answered directly") is
   conceptually the same kind of decision as "does this query need
   document retrieval, web retrieval, or both" - so this isn't a wasted
   idea, it's a v2 extension of a pattern already being built.

**When to revisit:** after DocMind's core 5 phases are complete and
genuinely polished, and only if there's real spare time before
DomainLLM/AutoAgent need to start - framed explicitly as "v2: I built
the core system, then extended it with hybrid retrieval," which is a
stronger interview narrative than attempting it prematurely and ending
up with three unfinished projects instead of two excellent ones.