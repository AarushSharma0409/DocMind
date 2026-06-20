# Testing Notes — `chunker.py`

This documents what's tested in Phase 1, Chunk 2 (chunking) and why.

> This is a written record for context and interview prep. The actual
> automated tests live in `tests/test_chunker.py` — run them with
> `python -m pytest tests/test_chunker.py -v`.

---

## What `chunker.py` does

Takes loader output (from `loaders.py`) — page/paragraph-level text — and
splits it into smaller, token-sized chunks suitable for embedding, while
propagating citation metadata (`page_number`, `locator_type`) into every
chunk produced.

| Function | Purpose |
|---|---|
| `count_tokens(text)` | Token count via tiktoken, with an offline fallback |
| `split_into_paragraphs(text)` | First-level split, on blank-line boundaries |
| `split_into_sentences(text)` | Fallback split, for paragraphs too large on their own |
| `chunk_text(text)` | Core packing algorithm — produces overlapping, size-bounded chunks |
| `chunk_document(loaded_pages)` | Ties it together — chunks a full document, propagating metadata |

---

## Why token-based sizing, not character-based

Chunk size is measured in **tokens**, not characters, because tokens are
what actually maps to an embedding model's input limit. A "2000 character"
chunk could be 300 tokens or 600 tokens depending on the text — token
count stays consistent with what the model actually processes.

`CHUNK_SIZE_TOKENS = 500`, `CHUNK_OVERLAP_TOKENS = 75` — chosen as a
reasonable middle ground: large enough to preserve context within a
chunk, small enough to keep retrieval precise.

---

## A real deployment edge case found and fixed: tiktoken's network dependency

`tiktoken` downloads its encoding file from OpenAI's servers on first
use and caches it locally. This means token counting can fail in
network-restricted environments — some Docker build steps, certain CI
runners, sandboxed environments.

**Fix:** `count_tokens()` wraps the tiktoken encoding load in a
try/except. If it fails, it falls back to a characters-per-token
approximation (`len(text) // 4`, a standard rule of thumb for English
text), with a `max(1, ...)` floor so short strings don't round down to
zero tokens. This means chunking can proceed with a reasonable estimate
rather than crashing the whole ingestion pipeline over a token-counting
utility.

This is genuinely worth mentioning in an interview — it's the kind of
production-readiness detail that distinguishes "ran a tutorial" from
"thought about what happens when this runs somewhere other than my own
machine."

---

## The overlap mechanism, and how it's verified

**How it works:** when a chunk fills up, the next chunk doesn't start
from scratch — it carries over the last `~75` tokens (in whole sentence/
paragraph units, never mid-sentence) from the end of the previous chunk.
This means content sitting right at a chunk boundary still has full
context in at least one of the two chunks it spans.

**Why this matters:** without overlap, a sentence split across a chunk
boundary could lose meaning in both halves — imagine the sentence "the
defendant was found not guilty" split as "...the defendant was found"
in chunk N and "not guilty..." in chunk N+1. Neither chunk alone
preserves the actual meaning.

**How it's tested:** `test_chunk_text_consecutive_chunks_actually_overlap`
doesn't just trust the docstring's claim — it builds text large enough to
force multiple chunks, then checks that a real sentence from the end of
chunk N actually reappears verbatim at the start of chunk N+1. This is
deliberately a behavioral test, not a structural one: if a future
refactor silently breaks overlap (e.g. someone "simplifies" the packing
loop and drops the carry-over logic), this test fails loudly instead of
the bug going unnoticed until citations start looking wrong.

---

## The sentence-fallback edge case

Most paragraphs fit comfortably under 500 tokens, but dense technical
writing or oddly formatted documents (zero paragraph breaks at all) can
produce a single "paragraph" larger than one chunk. Verified with a test
case: 1000+ tokens of text with **zero blank lines anywhere**. Confirmed
this correctly falls back to sentence-level splitting rather than either
truncating content or producing one giant oversized chunk.

---

## Metadata propagation — the most important contract in this file

**The core question this code has to get right:** when one page produces
multiple chunks (because it's too long for one), does every resulting
chunk still know which page it came from?

**Verified with `test_chunk_document_propagates_page_number_to_every_chunk`:**
built a page large enough to require multiple chunks, confirmed every
single chunk produced from it still reports the correct `page_number`
and `locator_type` — not just the first chunk, all of them.

Also verified: chunks from different pages never cross-contaminate
(`test_chunk_document_keeps_chunks_from_different_pages_separate`), and
the `paragraph_index` locator type (DOCX-sourced content) survives
chunking just as correctly as the `page` locator type (PDF-sourced).

This matters for the same reason the `loaders.py` position-indexing bug
mattered: if this propagation silently breaks, DocMind's citations point
to the wrong (or no) source, and nothing crashes to tell you.

---

## Test suite summary

24 automated tests in `tests/test_chunker.py`, covering:
- Token counting (including the empty-string edge case)
- Paragraph and sentence splitting, including messy whitespace
- Chunk size limits being respected
- Overlap being real, not just claimed
- The oversized-paragraph sentence-fallback actually engaging
- Metadata propagation across single-page and multi-page documents
- Empty-input handling at every level (`chunk_text("")`, `chunk_document([])`)
- The exact output contract (dict keys) downstream code depends on

Run alongside `test_loaders.py`:

```bash
python -m pytest tests/ -v
```

All 40 tests (16 from `test_loaders.py` + 24 from `test_chunker.py`)
pass together as of this writing.
