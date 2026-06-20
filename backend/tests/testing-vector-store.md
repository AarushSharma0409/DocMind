# Testing Notes - `vector_store.py`

This documents what's tested in Phase 1, Chunk 4 (ChromaDB persistent
storage) and why - the final piece of the Phase 1 ingestion pipeline.

> This is a written record for context and interview prep. The actual
> automated tests live in `tests/test_vector_store.py` - run them with
> `python -m pytest tests/test_vector_store.py -v`.

---

## What `vector_store.py` does

Takes embedded chunks (from `embedder.py`) and persists them to disk via
ChromaDB, making them durably storable and ready for similarity search
in Phase 2.

| Function | Purpose |
|---|---|
| `get_client(persist_dir)` | Returns a persistent ChromaDB client for a given path, cached per path |
| `get_collection(...)` | Gets or creates the collection chunks are stored in |
| `store_chunks(embedded_chunks, ...)` | Batch-stores embedded chunks with metadata |
| `delete_by_source_file(source_file, ...)` | Removes all chunks for a given document (supports clean re-ingestion) |
| `count_chunks(...)` | Returns total chunks currently stored |

---

## Why ChromaDB, and why a persistent client specifically

ChromaDB handles the similarity-search math (cosine distance between
vectors) internally, so Phase 2's retrieval code doesn't need to
implement that itself. A **persistent** client (not in-memory) writes
data to disk at a configured path, so the knowledge base survives app
restarts - without this, every uploaded document would need to be
re-embedded from scratch every time the backend process restarted,
which would be both slow and a poor user experience.

---

## A real bug caught and fixed: client caching across different paths

**The original design:** a single global `_client` variable, set once
on first call and reused after that (mirroring the lazy-singleton
pattern used in `embedder.py`'s `get_model()`).

**The problem:** this pattern works fine when there's only ever one
`persist_dir` in play, but breaks silently the moment `get_client()` is
called with a *different* path. The first call's client gets cached and
returned for every subsequent call - regardless of what path was
actually requested - so a second call with a different `persist_dir`
would silently return a client pointed at the wrong data, with no error
to flag it.

**Why this mattered immediately, not just hypothetically:** the test
suite needs an isolated temporary directory per test (so tests don't
leak data into each other or depend on run order). With the original
single-client design, the second test's `get_client()` call would have
silently returned the first test's client - tests would have appeared
to pass or fail based on execution order and leftover state, not on
their actual logic. This is exactly the kind of bug that's invisible
until you try to write proper isolated tests, which is part of why
building the test suite is valuable even beyond catching "real" bugs.

**The fix:** cache clients in a dict keyed by `persist_dir`, so calling
`get_client()` with two different paths correctly returns two different
client instances, while still reusing the same instance for repeated
calls with the same path.

**Verified with two dedicated tests:**
- `test_get_client_returns_same_instance_for_same_path` - confirms
  caching still works for the common case
- `test_get_client_returns_different_instances_for_different_paths` -
  the regression test that locks in the actual fix; if this bug is ever
  reintroduced, this test fails immediately

---

## The ID collision problem, and how it's solved

**The problem:** ChromaDB requires a unique string ID per stored
record. `chunker.py`'s `chunk_id` is only unique *within* one document -
every document's first chunk has `chunk_id=0`. Storing chunks from
multiple documents in the same collection would mean every document's
chunk 0 tries to claim the same ID.

**The fix:** `_build_record_id()` combines `source_file` and `chunk_id`
(`f"{source_file}::{chunk_id}"`) to produce an ID that's unique across
the whole knowledge base, not just within one document.

**Verified with `test_chunk_id_zero_from_two_documents_does_not_collide`:**
stores a chunk with `chunk_id=0` from `doc_a.pdf`, then a chunk with
`chunk_id=0` from `doc_b.pdf` - confirms both are stored as genuinely
separate records (collection count is 2, not 1, and no error is raised
from a duplicate-ID conflict).

**Also verified: deletion respects document boundaries.**
`test_delete_by_source_file_only_removes_that_documents_chunks` stores
chunks from two documents, deletes one document's chunks, and confirms
the other document's chunks are untouched - this is what makes clean
re-ingestion of a single document possible without nuking the whole
knowledge base.

---

## Metadata type constraints: the None -> "unknown" conversion

ChromaDB's metadata fields only accept `str`, `int`, `float`, or `bool` -
`None` is rejected at write time. `chunker.py`'s `source_file` defaults
to `None` for backward-compatible callers that don't supply it.
`_sanitize_metadata()` converts `None` to the string `"unknown"` before
storing, so a chunk without a known source file still stores
successfully rather than failing with a cryptic ChromaDB type error.

**Verified with `test_none_source_file_is_stored_as_unknown_string`:**
stores a chunk with `source_file=None`, confirms storage succeeds (does
not raise), and confirms the actual stored metadata reads
`"source_file": "unknown"` when read back from the collection.

Also verified: `text` and `embedding` are deliberately excluded from the
metadata dict (`test_sanitize_metadata_excludes_text_and_embedding`) -
ChromaDB stores those as separate first-class fields (the indexed
vector and the "document" text respectively), so including them again
inside metadata would be redundant duplication, not an error, but still
worth explicitly testing to keep the metadata schema clean and
intentional.

---

## Persistence - the core promise of this module

**The single most important property to verify:** does data actually
survive being written to disk and read back by a fresh client instance,
simulating an app restart?

**Verified with `test_data_persists_across_separate_client_instances`:**
stores a chunk, then explicitly clears the in-memory client cache for
that path (simulating the Python process ending and a new one starting
up), then confirms a fresh `get_client()` call pointed at the same disk
path can still read the previously stored data. If this test fails,
persistence is fundamentally broken and every uploaded document would
vanish on every restart - which would defeat the entire reason a
persistent (rather than in-memory) client was chosen.

---

## Why real ChromaDB in tests, not mocked

Unlike `test_embedder.py` (which mocks the embedding model because it
requires network access and real compute), `test_vector_store.py` uses
the **real** ChromaDB library throughout. ChromaDB doesn't require
network access to run locally, so there's no reliability or speed cost
to testing against the real thing - and doing so means these tests
verify ChromaDB's actual behavior around ID collisions, metadata
constraints, and persistence, rather than verifying a mock's assumptions
about how ChromaDB behaves (which could easily drift from reality and
give false confidence).

Each test gets its own isolated temporary directory (via pytest's
`tmp_path` fixture), made safe specifically because of the client-
caching fix described above - without that fix, isolated per-test
directories would have silently shared the first test's client.

---

## Test suite summary

14 automated tests in `tests/test_vector_store.py`, covering:
- Client caching correctness (same path reused, different paths isolated)
- Basic store/count behavior, including empty input
- Persistence across a simulated app restart
- Multi-document ID collision prevention
- Deletion scoped correctly to one document, including a no-op case
- Metadata type sanitization (`None` -> `"unknown"`)
- Record ID construction
- Metadata schema correctness (text/embedding excluded)

Run alongside the rest of Phase 1's suite:

```bash
python -m pytest tests/ -v
```

All 69 tests (16 loaders + 26 chunker + 13 embedder + 14 vector_store)
pass together as of this writing - completing Phase 1 of DocMind.
