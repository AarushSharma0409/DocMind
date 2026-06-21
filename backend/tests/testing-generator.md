# Testing Notes - `generator.py`

This documents what's tested in Phase 2, Chunk 3 (generation & citation
extraction) and why - including a provider-consistency decision and two
real bugs found and fixed during development.

> This is a written record for context and interview prep. The actual
> automated tests live in `tests/test_generator.py` - run them with
> `python -m pytest tests/test_generator.py -v -m "not integration"`.

---

## What `generator.py` does

Takes a user's query plus retrieved chunks (from `retriever.py`) and
produces a synthesized, cited answer:

```
generate(query, chunks) ->
  {"answer": "...",
   "citations": [{"source_file": "report.pdf", "page_number": 4,
                  "locator_type": "page", "excerpt": "..."}, ...]}
```

This is the piece that turns "here are 5 relevant text chunks" into
"here's an actual answer to your question, and here's exactly where it
came from" - the second half of that sentence (traceable citations) is
one of the project's three stated differentiators.

---

## Episode: a provider-consistency decision, not a silent drift

A first draft of this module was built against the Groq API
(`llama-3.1-8b-instant`), independently of `query_router.py`'s Gemini
setup. This was caught and reconsidered before being adopted, for a
concrete reason: it would have meant maintaining two different LLM
providers, two SDKs, and two separate auth setups for two halves of one
pipeline - real, ongoing maintenance cost, not just an aesthetic
inconsistency. An interviewer asking "why two different LLM providers
in one small project" deserves a better answer than "it happened that
way."

**The decision:** rebuild `generator.py` on Gemini, consistent with
`query_router.py`. The original Groq-based work was **not discarded** -
it's preserved as `generator_groq_backup.py`, explicitly labeled as a
documented fallback option (clean Groq free-tier access is genuinely
useful to have in reserve if Gemini's free tier ever becomes limiting),
with its known issues (see below) called out rather than silently
carried forward.

This is worth narrating honestly in an interview as a real engineering
decision: noticing scope/consistency drift, weighing the tradeoff, and
choosing deliberately rather than defaulting to whichever code was
already written.

---

## Bug #1: an incorrect assumption about `retriever.py`'s output contract

**What happened:** the original Groq-based draft assumed every
retrieved chunk has a `chunk_id` field, and built its citation logic
(`chunk_id` lookup, hallucination guard against a `valid_chunk_ids` set)
around that assumption.

**Root cause:** `retriever.py`'s actual `retrieve()` function does NOT
return a `chunk_id` field. Its real output shape is `{"text",
"source_file", "page_number", "locator_type", "similarity"}` - the
Groq draft was written without checking against the actual, current
contract of the module it was meant to consume.

**The fix, in the Gemini rebuild:** citations are built around
`chunk_index` - the chunk's *position* in the list passed to the prompt
(0-based) - rather than a `chunk_id` that doesn't exist. The model cites
"chunk_index: 2" (meaning "the third chunk you gave me"), and
`_parse_generation_response()` hydrates that index back into the full
citation record (`source_file`, `page_number`, `locator_type`) by
looking up `chunks[chunk_index]` directly. This is actually a more
robust design than a `chunk_id` lookup would have been, since it doesn't
require any ID field to exist at all - it only requires knowing which
chunk, by position, supported a claim.

**Verified with `test_uses_chunk_index_not_chunk_id`**, which explicitly
asserts the word "chunk_id" never appears in the generated prompt - a
direct regression check against this exact mistake recurring.

---

## Bug #2: `.env` loading depended on the current working directory

**What happened:** the manual smoke test (`__main__` block) worked
correctly when run one way, then failed with `ValueError: No API key
was provided` when run a different way, from a different directory,
even though the API key was genuinely present in `backend/.env`.

**Root cause:** `load_dotenv()` called with no arguments searches
upward from the *current working directory* - this is fragile, since
running a script as `python -m app.retrieval.generator` from `backend/`
and running the same script directly from inside
`backend/app/retrieval/` are two different working directories, and the
upward search isn't guaranteed to find `.env` reliably from both.

**The fix:** resolve the path to `backend/.env` explicitly, relative to
the *file's own location* (`Path(__file__).resolve().parent.parent.parent
/ ".env"`), not the working directory. This makes `.env` loading behave
identically no matter how or from where the script is invoked. The same
fix was retroactively applied to `query_router.py`, which had the
identical fragile pattern and would have hit the same bug eventually.

**Verified manually** (not as an automated test, since it's inherently
about process working-directory behavior) by loading a test `.env` file
from a working directory completely unrelated to the script's location
and confirming the key still loaded correctly.

---

## Why LLM-side citation, not post-processing

Matching generated answer sentences back to source chunks via
post-processing (e.g. fuzzy string matching) is brittle - a paraphrased
answer won't match chunk text verbatim, and the matching logic becomes
its own hard engineering problem. Instead, the model itself is
constrained (via the prompt and the structured-output schema) to only
cite chunks it was actually given, by index, which prevents hallucinated
sources structurally rather than trying to detect hallucination after
the fact.

---

## Why a single exception type (`GenerationError`), unlike `query_router.py`'s silent fallback

`query_router.py` silently falls back to a safe default route on any
failure, because routing *wrong* (still attempting a similarity search)
degrades gracefully - worst case, generic chunks come back instead of
perfectly-targeted ones. Generation is different: if the LLM call
genuinely fails, there's no safe "default answer" to silently
substitute. Pretending an answer was generated when it wasn't would
actively mislead the user. So `generate()` raises `GenerationError`
rather than returning a fallback value - callers need to handle exactly
one failure case ("generation didn't work"), not enumerate every
possible underlying cause (the original Groq draft used five separate
exception types - `ValueError`, `EnvironmentError`,
`GeneratorParseError`, `TimeoutError`, `RuntimeError` - for what's
really one conceptual failure mode from a caller's perspective).

---

## The hallucination defense: out-of-range `chunk_index`

Even with a schema-constrained response guaranteeing *shape*
(`citations` is a list of `{chunk_index: int, excerpt: str}`), the
schema can't guarantee `chunk_index` is actually *within range* of the
chunks provided. `_parse_generation_response()` checks each
`chunk_index` against `0 <= chunk_index < len(chunks)` and silently
drops any citation that fails this check, rather than crashing the
whole response over one bad citation - the same defensive principle as
`query_router.py`'s hallucinated-filename guard, applied here to
citation indices.

**Verified with three dedicated tests:** an out-of-range positive index,
a negative index, and a non-integer index - all correctly dropped
without crashing, with valid citations alongside them preserved.

---

## Test suite structure: mocked unit tests + optional real-API integration tests

This pattern was adapted from the original Groq draft, which used it
well: `TestBuildPrompt` and `TestParseGenerationResponse` test pure
logic with no API involvement at all. `TestGenerateMocked` tests
`generate()`'s orchestration (chunk capping, error handling, schema
shape) using a mocked Gemini client - no network, no cost, fully
deterministic. `TestGenerateIntegration` is marked
`@pytest.mark.integration` and makes real Gemini API calls to verify
actual model behavior (is the answer genuinely grounded in chunk
content, are citations non-empty) - excluded from the default test run
via `pytest.ini`'s marker registration, run explicitly with
`pytest -m integration` when real-model verification is wanted.

**Verified manually, beyond the test suite:** the `__main__` smoke test
was run for real against the live Gemini API on the development
machine - confirmed a genuinely synthesized, accurate answer ("12%
year-over-year revenue growth... driven by expansion in APAC markets")
with a correctly traced citation back to the exact source chunk,
including a verbatim excerpt. Notably, the model correctly cited only
the relevant chunk and did not force an irrelevant citation from the
second (operating-expenses) chunk that was also provided - a good sign
the prompt's grounding instructions are working as intended, not just
producing technically-valid but over-eager citations.

---

## Test suite summary

31 total tests in `tests/test_generator.py`:

- **27 mocked unit tests** (no API calls, run by default):
  - Prompt construction (query/chunk text/metadata inclusion, the chunk_index-not-chunk_id regression check, valid index range stated)
  - Response parsing (valid shape, citation hydration from chunk_index, locator_type preservation across source types)
  - The hallucination defense (out-of-range, negative, and non-integer chunk_index all correctly dropped)
  - Edge cases (empty citations list, multiple citations from one chunk, malformed citation entries skipped)
  - Error handling (malformed JSON, missing fields, wrong types all raise `GenerationError`)
  - `generate()` orchestration (chunk capping at `MAX_CHUNKS_IN_PROMPT`, input validation, API-failure wrapping)

- **4 integration tests** (real Gemini API calls, excluded by default):
  - End-to-end smoke test
  - Answer grounding verification
  - Citation excerpt quality
  - Graceful handling of an unanswerable query

Run the default (mocked-only) suite alongside the rest of the project:

```bash
python -m pytest tests/ -v -m "not integration"
```

135 tests pass together as of this writing (16 loaders + 26 chunker +
13 embedder + 14 vector_store + 16 retriever + 23 query_router + 27
generator unit tests), with 4 generator integration tests available to
run explicitly when real-model verification is needed.
