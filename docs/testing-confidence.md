# Testing Notes — `confidence.py`

This documents what's tested in Phase 2, Chunk 4 (confidence signaling) and why.

> This is a written record for context and interview prep. The actual
> automated tests live in `tests/test_confidence.py` — run them with
> `python -m pytest tests/test_confidence.py -v`.

---

## What `confidence.py` does

Takes the output of `retriever.retrieve()` and returns a structured
confidence assessment — a judgment about whether the retrieved chunks
are strong enough to trust the generated answer:

```
assess_confidence(chunks) ->
  {"level": "high" | "medium" | "low", "reason": str}
```

This is the piece that powers DocMind's second key differentiator:
instead of always answering confidently, the system tells the user
*how much to trust* the answer based on how well the retrieved chunks
actually matched the query.

---

## Why this is a separate module, not part of `generator.py`

The obvious place to put confidence signaling is inside `generate()` —
it already has the chunks, it produces the answer, why not add a
confidence field to its output?

Two reasons this is wrong:

**First**, it mixes two responsibilities: generating an answer and
judging retrieval quality. These are independent concerns — retrieval
can be weak even when generation succeeds, and generation can fail even
when retrieval was strong. Keeping them separate means each can be
tested and reasoned about in isolation.

**Second**, the API layer (Phase 3) needs to surface confidence
*independently* of whether generation succeeded. If `generate()` raises
a `GenerationError`, confidence information is lost along with it — but
"retrieval was weak, which may be why generation failed" is exactly the
message the user needs to see. A standalone `assess_confidence()` call
before `generate()` means that signal is never silenced by a downstream
failure.

---

## Why three levels, not a raw score

Returning the raw `max_similarity` float to the frontend would require
the UI to decide what the number means — and that decision would be
implicit, untested, and potentially different in every place the value
is consumed. Three named levels (`high`, `medium`, `low`) make the
judgment explicit, in one place, with named thresholds that can be
tuned independently of the UI.

The thresholds chosen:

| Constant | Value | Meaning |
|---|---|---|
| `HIGH_SIMILARITY_THRESHOLD` | 0.65 | Above this, retrieval is strong |
| `LOW_SIMILARITY_THRESHOLD` | 0.35 | Below this, retrieval is weak |
| `MIN_CHUNKS_FOR_HIGH_CONFIDENCE` | 2 | Minimum chunks needed for "high" |

These are starting hypotheses, not ground truth. The right values come
from observing real queries on real documents. They are named constants
at the top of the file — not magic numbers scattered through
if-statements — so tuning them later means one change in one place.

---

## The single-chunk rule, and why it matters

A query with one highly-similar chunk (say, `similarity=0.91`) gets
`"medium"` confidence, not `"high"`. This is deliberate.

One chunk can mean two very different things: a precise factual answer
to a targeted query ("what was Q3 revenue?") or the only partial hit in
a weak retrieval where nothing really matched. The similarity score
alone can't distinguish them. Two or more chunks independently
corroborating a high score is a much stronger structural signal that the
retrieval genuinely found what the query was looking for.

**Verified with `test_high_requires_min_chunk_count`**, which explicitly
asserts that a single chunk with `similarity=0.99` still returns
`"medium"`. The test's failure message explains the reasoning, so a
future change to this rule is deliberate, not accidental.

---

## Why max similarity, not average

`assess_confidence()` uses `max(similarities)` to determine level, not
the mean. This is the right call: a retrieval with one strong hit
(`0.82`) and four weak ones (`0.10`, `0.08`, `0.12`, `0.09`) has
genuinely found something relevant — averaging would drag the score
down to `~0.24` and incorrectly signal "low" confidence. The strongest
match is what determines whether the answer has a real grounding in the
documents.

**Verified with `test_one_strong_chunk_among_weak_ones_lifts_to_medium`**:
four chunks with similarities `[0.60, 0.05, 0.05, 0.05]` — average
`~0.19`, but max `0.60` — correctly produces `"medium"`, not `"low"`.

---

## This is a pure function — and that's the point

`assess_confidence()` has no API calls, no I/O, no side effects. It
reads a list, does arithmetic, returns a dict. This makes it:

- **Fully deterministic** — same input always produces same output
- **Trivially testable** — no mocking needed anywhere in the test suite
- **Fast** — 32 tests run in under 0.1 seconds
- **Safe to call anywhere** — no risk of side effects if called multiple
  times or in unexpected order

Every other module in Phase 2 required mocking (`generator.py`,
`query_router.py`) or a fake embedding model (`retriever.py`). This one
needs neither. That simplicity is a feature of the design, not an
accident.

---

## The empty-input guard

`assess_confidence([])` must return `{"level": "low", ...}` with a
reason explaining no chunks were found — not crash with `ValueError:
max() arg is an empty sequence`, which is what `max([])` raises natively.

This is a realistic state: a user uploads no documents and asks a
question, or uploads documents that contain nothing matching the query.
The calling code (Phase 3 API layer) shouldn't need a try/except to
handle "no documents uploaded yet."

**Verified with `test_empty_list_does_not_crash`**, which explicitly
catches any exception and fails with a clear message if one is raised.

---

## Boundary condition testing

The threshold boundaries are tested explicitly because off-by-one errors
at boundaries are the most common way threshold logic breaks silently:

- `sim == HIGH_SIMILARITY_THRESHOLD` with 2+ chunks → `"high"` ✓
- `sim == HIGH_SIMILARITY_THRESHOLD - 0.001` with 2+ chunks → not `"high"` ✓
- `sim == LOW_SIMILARITY_THRESHOLD` → not `"low"` (boundary is inclusive upward) ✓
- `sim == LOW_SIMILARITY_THRESHOLD - 0.001` → `"low"` ✓

Without these, a future change to the threshold constants (e.g. `0.65`
→ `0.60`) could silently break boundary behavior without any test
catching it.

---

## Verified against the real retriever output shape

`test_real_retriever_output_shape_works` builds chunk dicts with the
exact fields `retriever.retrieve()` actually returns — `text`,
`source_file`, `page_number`, `locator_type`, `similarity` — and
confirms `assess_confidence()` handles them correctly without being
confused by the extra fields it doesn't need.

This is the same contract-verification pattern used throughout Phase 1:
test against the real shape of upstream output, not an idealized
minimal version of it.

---

## Test suite summary

32 automated tests in `tests/test_confidence.py`, covering:

- Output schema (dict shape, valid level enum, non-empty reason, no extra keys)
- `"high"` confidence (strong similarity + enough chunks, reason content,
  single-chunk rule enforced)
- `"medium"` confidence (between thresholds, single high-sim chunk, exact
  boundary, reason content)
- `"low"` confidence (below threshold, zero similarity, reason content)
- Empty input (returns low, reason mentions no chunks, no crash)
- Boundary conditions (exact threshold values, just above and below)
- Max-not-average behavior (one strong chunk among weak ones)
- Robustness to extra fields (real retriever output shape passes through)

Run alongside the rest of Phase 2:

```bash
python -m pytest tests/ -v -m "not integration"
```

167 tests pass together as of this writing (16 loaders + 26 chunker +
13 embedder + 14 vector_store + 16 retriever + 23 query_router + 27
generator unit tests + 32 confidence).
