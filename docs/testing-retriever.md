# Testing Notes - `retriever.py`

This documents what's tested in Phase 2, Chunk 1 (basic similarity
retrieval) and why - including a real bug found and fixed during
development that affects every downstream piece of Phase 2.

> This is a written record for context and interview prep. The actual
> automated tests live in `tests/test_retriever.py` - run them with
> `python -m pytest tests/test_retriever.py -v`.

---

## What `retriever.py` does

Takes a user's query, embeds it with the same model used for documents,
and returns the most similar stored chunks from ChromaDB:

```
retrieve(query, top_k=5) -> [
  {"text": "...", "source_file": "report.pdf", "page_number": 4,
   "locator_type": "page", "similarity": 0.83},
  ...
]
```

This is the "R" in RAG - the LLM's eventual answer (`generator.py`) is
only as good as what this function retrieves. If ranking, similarity
math, or citation metadata is wrong here, every later piece (query
routing's downstream use, generation, confidence signaling) inherits
that brokenness silently.

---

## Why a fake model with controlled vectors, not the real one

Real semantic ranking quality - does "customer attrition" genuinely
match "churn" well - is a property of the pretrained embedding model
itself, not code written for this project. That's not something a unit
test should try to verify (it's already been validated by the model's
publishers). What these tests DO need to verify is that *this project's
code* correctly: embeds the query, queries ChromaDB, orders results,
converts distance to similarity correctly, and preserves citation
metadata through the round trip.

`FakeModel` maps text to one of three deliberately orthogonal unit
vectors based on keyword presence (churn/attrition/retention vs.
revenue/profit/earnings vs. everything else). This lets tests assert
*exact* expected similarity scores (`1.0` for an identical-topic match,
`0.0` for an orthogonal one) - something a real model's organic,
continuous-valued output never allows, since real embeddings rarely
land on perfectly clean numbers.

---

## The cosine distance bug - the most consequential bug in this phase

**What happened:** every similarity score came back as `0.0`, including
for a query that was an exact topical match to a stored chunk. Result
*ordering* was still correct (the genuinely relevant chunk still ranked
first), which is exactly why this was easy to miss at first - the
system "worked" in the sense of returning sensible-looking top results,
it just couldn't tell you *how* confident any given match was.

**Root cause, found by direct empirical testing, not assumption:**
`retrieve()`'s similarity conversion (`1 - distance`) is only
mathematically valid for cosine distance specifically. ChromaDB's
actual default distance metric, when not explicitly configured, is
**squared L2 (Euclidean) distance**, not cosine. This was confirmed by
querying ChromaDB directly with known orthogonal and opposite unit
vectors: an orthogonal pair returned distance `2.0` and an opposite pair
returned `4.0` - values that match neither cosine distance (which would
give `1.0` and `2.0`) nor standard (non-squared) L2 distance (which
would give `1.41` and `2.0`). Only squared L2 produces exactly `2.0` and
`4.0` for those vector relationships.

**Why this matters beyond just "the numbers were wrong":** the
confidence-signaling layer (Phase 2, Chunk 4) depends entirely on
similarity scores being meaningful. "If all results have low
similarity, say so" is meaningless if similarity is always computed as
`0.0` regardless of actual match quality. Catching this bug at Chunk 1,
before building confidence signaling on top of broken numbers, avoided
building an entire feature on a foundation that silently didn't work.

**The fix:** `vector_store.py`'s `get_collection()` now explicitly sets
`metadata={"hnsw:space": "cosine"}` when creating the ChromaDB
collection. This is a one-line fix once the actual root cause was
understood, but finding that root cause required reading ChromaDB's
real behavior empirically rather than trusting an assumed default -
a good example of debugging at the library-contract level rather than
just application logic.

**Operational note:** since this changed how the collection is created,
any data stored before the fix used the wrong distance metric and
should be deleted and re-ingested rather than mixed with correctly
configured data.

---

## How the fix is verified, specifically

Two regression tests target this exact bug directly, not just
incidentally:

- **`test_similarity_score_for_exact_match_is_one`** - a query
  topically identical to a stored chunk (same `FakeModel` vector) must
  produce `similarity == 1.0` exactly. The test's own failure message
  explicitly explains what a regression would look like and why, so a
  future failure is self-diagnosing, not just a bare assertion error.
- **`test_similarity_score_for_orthogonal_topics_is_zero`** - the
  companion case: genuinely unrelated topics must score `0.0`, not some
  other arbitrary low number that happens to still rank correctly.

Together, these two tests would catch this bug coming back even if
result *ordering* still looked correct, which is exactly the property
that let it slip past manual testing the first time.

---

## Why `top_k=5` by default

A tradeoff between precision and recall. Top 3 is tighter but riskier -
with a smaller local embedding model (384-dim, not the largest
available), the single most relevant chunk could plausibly rank 4th
rather than 1st-3rd due to imperfect semantic matching, and a too-narrow
result set could miss it entirely. Top 5 gives the LLM (in
`generator.py`) enough surrounding context to synthesize a good answer,
and gives the confidence-signaling layer more signal to work with - "all
5 results have low similarity" is a more reliable weak-retrieval signal
than "all 3 results have low similarity." Configurable via the `top_k`
parameter, not hardcoded, since the right number is something to tune
once real retrieval quality is observed on real documents.

Verified with `test_retrieve_respects_top_k` (requesting fewer results
than exist) and `test_retrieve_caps_top_k_at_collection_size`
(requesting more results than exist - handled gracefully via
`effective_top_k = min(top_k, collection.count())`, not an error).

---

## Citation metadata preservation

Every result must carry `source_file`, `page_number`, and `locator_type`
through the retrieval round trip - this is what makes `generator.py`'s
citations possible. Verified for both PDF-sourced (`locator_type:
"page"`) and DOCX-sourced (`locator_type: "paragraph_index"`) chunks,
and for distinguishing chunks that share the same `chunk_id` but come
from different `source_file` values (the same multi-document disambiguation
concern `vector_store.py` was built to solve).

---

## `_format_results` - the ChromaDB response-unwrapping helper

ChromaDB's raw `query()` response nests results one level deeper than
expected: `documents`, `metadatas`, and `distances` are each a list of
lists (one inner list per query embedding submitted in a batch).
`retrieve()` only ever submits one query embedding at a time, so
`_format_results()` isolates the `[0]` unwrapping logic in one place
rather than repeating it inline.

Tested directly with a hand-built raw ChromaDB-shaped response
(`test_format_results_unwraps_nested_chromadb_response`), independent of
any real ChromaDB call - confirms the unwrapping and the distance-to-
similarity conversion both work correctly in isolation.

Also tested: similarity clamping (`test_format_results_clamps_similarity_to_valid_range`)
- cosine distance can technically exceed the typical range in floating-
point edge cases, and an out-of-range similarity score would be
confusing to any downstream consumer reasoning about "how confident is
this." A distance of `3.5` (which would produce an unclamped similarity
of `-2.5`) is correctly clamped to `0.0`.

---

## Edge cases

- Empty query string and whitespace-only query both return `[]`
  immediately, without attempting an embedding call or a ChromaDB query
  - callers shouldn't need a try/except for "no results."
- Querying a collection with zero stored chunks returns `[]` cleanly,
  not an error - a realistic state (no documents uploaded yet) that
  shouldn't require special-case handling by every caller.

---

## Test suite summary

16 automated tests in `tests/test_retriever.py`, covering:

- Basic retrieval behavior (result count, `top_k` respected and capped)
- Semantic ranking correctness (a synonym query correctly ranks the
  semantically related chunk first)
- Similarity score math, including the two cosine-distance-bug
  regression tests
- Citation metadata preservation across PDF and DOCX source types, and
  across multiple documents
- Edge cases (empty query, whitespace-only query, empty collection)
- `_format_results`' response-unwrapping and similarity-clamping logic,
  tested in isolation from any real ChromaDB call

Run alongside the rest of the suite:

```bash
python -m pytest tests/ -v -m "not integration"
```

135 tests pass together as of this writing (16 loaders + 26 chunker +
13 embedder + 14 vector_store + 16 retriever + 23 query_router + 27
generator unit tests).
