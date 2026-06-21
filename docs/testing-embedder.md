# Testing Notes - `embedder.py`

This documents what's tested in Phase 1, Chunk 3 (embedding generation) and why.

> This is a written record for context and interview prep. The actual
> automated tests live in `tests/test_embedder.py` - run them with
> `python -m pytest tests/test_embedder.py -v`.

---

## What `embedder.py` does

Takes chunked output (from `chunker.py`) and attaches a vector embedding
to every chunk, producing the final shape needed for ChromaDB storage:

```
embed_chunks(chunks) ->
  [{"chunk_id": 0, "page_number": 1, "locator_type": "page",
    "source_file": "report.pdf", "text": "...",
    "embedding": [0.123, -0.045, ...]}, ...]
```

| Function | Purpose |
|---|---|
| `get_model()` | Lazily loads (once) and returns the embedding model |
| `embed_chunks(chunks)` | Filters invalid input, batch-encodes valid chunks, attaches vectors |

---

## Why a local model, not a hosted API

`all-MiniLM-L6-v2` via `sentence-transformers`, run entirely on-machine:
free, no per-call cost, no API key needed - important during
development, where test documents get re-embedded constantly while
debugging. Produces 384-dimensional vectors, smaller than some hosted
alternatives (e.g. OpenAI's `text-embedding-3-small` is 1536-dim), a
real quality/size tradeoff: less nuance per vector, in exchange for
speed and zero cost. The module isolates this decision, so swapping to
a hosted model later (for a production deployment story) wouldn't
require touching `chunker.py` or anything upstream.

---

## Why a mock model in tests, not the real one

The real model requires a one-time network download (~90MB) and real
compute per call. A `FakeModel` class with deterministic, fixed-shape
output lets the test suite verify the **logic around** the model
(filtering, batching, metadata propagation, contract shape) in
milliseconds, without depending on network access or burning real
model-loading time on every test run.

This is also the philosophically correct scope for these tests: whether
the *pretrained model* produces good embeddings isn't something a unit
test should verify (that's a property of the model itself, already
validated by its publishers) - what needs verifying is that *our code*
correctly wires inputs to outputs.

---

## The core contract: metadata must survive embedding

The most important thing this module has to get right: every original
field (`chunk_id`, `page_number`, `locator_type`, `source_file`, `text`)
must still be present and correct after a chunk passes through
`embed_chunks()` - only `embedding` should be newly added.

**Verified with `test_embed_chunks_preserves_all_original_metadata`:**
built a chunk with deliberately distinctive values (`chunk_id=5`,
`page_number=12`, `locator_type="paragraph_index"`), confirmed every
single one survives into the output untouched.

**Also verified: order/correspondence isn't accidentally shuffled.**
`test_embed_chunks_preserves_order_and_correspondence` uses `FakeModel`'s
deterministic output (vector `i` is filled with value `i`) to prove
chunk 0's embedding actually corresponds to chunk 0's text, not silently
mismatched during the batch encode + zip step. This matters because a
silent misalignment here would mean every chunk gets the *wrong*
embedding - text about topic A would be searchable under topic B's
vector, a failure mode that wouldn't crash anything, just quietly
corrupt every search result.

**Also verified: input chunks aren't mutated.** `embed_chunks` builds new
dicts via `**chunk` spread rather than modifying the caller's original
dicts in place - `test_embed_chunks_does_not_mutate_input_chunks` locks
this in, since mutating shared input is a common source of confusing
bugs elsewhere in a pipeline (e.g. if the same chunk list gets reused
or compared against later).

---

## Defensive filtering

Same principle used throughout `loaders.py` and `chunker.py`: empty or
whitespace-only chunk text gets filtered out before embedding, rather
than producing a meaningless vector for empty input that could falsely
match unrelated search queries later. Verified for: a single empty
chunk mixed with valid ones, a whitespace-only chunk, an all-empty input
list, and a completely empty input list - all four return correctly
filtered/empty results rather than erroring or embedding garbage.

---

## Error handling: failing loudly instead of silently

Unlike `chunker.py`'s `count_tokens()` (which has a meaningful
characters-per-token fallback when `tiktoken` can't reach the network),
there's no equivalent fallback that makes sense for embeddings - an
embedding *is* the model's output, there's no cheap approximation.
So `get_model()` wraps the model-loading call and re-raises any failure
as a `RuntimeError` with a clear, actionable message (network access
likely missing, here's what to check), rather than letting a cryptic
low-level exception propagate, and rather than silently returning
garbage vectors, which would corrupt retrieval quality without any
visible symptom.

**Verified with `test_get_model_raises_clear_error_on_load_failure`:**
simulates a model-load failure and confirms the resulting error message
actually contains the expected explanatory text, not just any exception.

---

## Lazy singleton pattern

Same reasoning as `chunker.py`'s tokenizer encoding and `loaders.py`'s
general design: loading a transformer model is expensive (real seconds,
not milliseconds), so it should happen once per process and be reused,
not reloaded on every call. Verified with
`test_get_model_reuses_loaded_model_instance`, which confirms two calls
to `get_model()` return the exact same object, not two separately
loaded instances.

---

## Manually verified on a real machine (not just mocked)

The `__main__` block in `embedder.py` was run for real (not mocked) on
the actual development machine: the real model downloaded successfully
from HuggingFace's hub (~91MB), loaded, and produced two genuine
384-dimensional embeddings matching the expected dimension exactly. This
confirms the real integration works end-to-end, on top of the mocked
unit tests confirming the surrounding logic is correct.

A real environment issue was hit and resolved along the way: on
Windows, `torch` (a dependency of `sentence-transformers`) requires the
Microsoft Visual C++ Redistributable to load its compiled DLLs - without
it, import fails with `OSError: [WinError 126]`. Installing the
redistributable and restarting resolved it. Worth knowing about as a
real deployment/setup consideration, not just a personal hiccup -
anyone else setting up this project on Windows would hit the same issue.

---

## Test suite summary

13 automated tests in `tests/test_embedder.py`, covering:
- Basic contract (count preserved, embedding key attached, correct dimension, plain list not numpy array)
- Metadata preservation across all original fields
- Order/correspondence between input chunks and output embeddings
- Input chunks not mutated
- Empty/whitespace filtering (single chunk, all chunks, empty list)
- Clear error messages on model-load failure
- Singleton reuse (no redundant model loads)

Run alongside `test_loaders.py` and `test_chunker.py`:

```bash
python -m pytest tests/ -v
```

All 55 tests (16 + 24 + 13 + 2 new `source_file` tests in `test_chunker.py`)
pass together as of this writing.
