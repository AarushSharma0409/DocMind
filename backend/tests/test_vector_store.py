"""
test_vector_store.py - Phase 1, Chunk 4 tests

WHY THIS EXISTS:
vector_store.py is the final piece of the Phase 1 ingestion pipeline -
if storage silently loses data, collides IDs across documents, or fails
to persist, every chunk that survived loaders.py, chunker.py, and
embedder.py correctly is still lost or corrupted at the last step.

WHY REAL CHROMADB, NOT MOCKED (unlike test_embedder.py's FakeModel):
ChromaDB doesn't require network access to run, so there's no
reliability or speed cost to using the real thing in tests. Using the
real database also means these tests genuinely verify ChromaDB's actual
behavior around our usage (ID collisions, metadata type constraints,
persistence) rather than verifying a mock's assumptions about how
ChromaDB behaves.

WHY EVERY TEST GETS ITS OWN TEMP DIRECTORY: vector_store.py caches
clients per persist_dir (see the _clients dict and the bug it fixes -
documented in vector_store.py itself). Giving each test its own
tmp_path-based directory means tests are fully isolated from each other
- no test's data leaks into another's, and no test depends on run order.
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "storage"))
import vector_store as vs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_embedded_chunk(chunk_id: int, source_file: str | None = "doc.pdf",
                         page_number: int = 1, locator_type: str = "page",
                         text: str = "Some chunk text.") -> dict:
    return {
        "chunk_id": chunk_id,
        "page_number": page_number,
        "locator_type": locator_type,
        "source_file": source_file,
        "text": text,
        "embedding": [0.1] * 384,
    }


@pytest.fixture
def store_dir(tmp_path) -> str:
    """A fresh, isolated ChromaDB persist directory for each test."""
    return str(tmp_path / "chroma_test_db")


# ---------------------------------------------------------------------------
# get_client - the singleton-per-path fix
# ---------------------------------------------------------------------------

def test_get_client_returns_same_instance_for_same_path(store_dir):
    """Calling get_client twice with the SAME path should reuse the client."""
    client_1 = vs.get_client(store_dir)
    client_2 = vs.get_client(store_dir)
    assert client_1 is client_2


def test_get_client_returns_different_instances_for_different_paths(tmp_path):
    """
    REGRESSION TEST for the client-caching bug found during development.

    An earlier version cached a single global client regardless of which
    persist_dir was requested, so calling get_client with a second,
    different path would silently return the FIRST path's client -
    pointing at the wrong data with no error. This test locks in that
    two different paths produce two genuinely different client instances.
    """
    dir_a = str(tmp_path / "db_a")
    dir_b = str(tmp_path / "db_b")

    client_a = vs.get_client(dir_a)
    client_b = vs.get_client(dir_b)

    assert client_a is not client_b, (
        "get_client returned the same instance for two different "
        "persist_dir values - the client-caching bug has been reintroduced."
    )


# ---------------------------------------------------------------------------
# store_chunks - basic behavior
# ---------------------------------------------------------------------------

def test_store_chunks_returns_correct_count(store_dir):
    chunks = [make_embedded_chunk(0), make_embedded_chunk(1)]
    stored = vs.store_chunks(chunks, persist_dir=store_dir)
    assert stored == 2


def test_store_chunks_empty_input_returns_zero(store_dir):
    stored = vs.store_chunks([], persist_dir=store_dir)
    assert stored == 0


def test_store_chunks_increases_collection_count(store_dir):
    chunks = [make_embedded_chunk(0), make_embedded_chunk(1), make_embedded_chunk(2)]
    vs.store_chunks(chunks, persist_dir=store_dir)
    assert vs.count_chunks(persist_dir=store_dir) == 3


# ---------------------------------------------------------------------------
# Persistence - the entire point of using PersistentClient
# ---------------------------------------------------------------------------

def test_data_persists_across_separate_client_instances(store_dir):
    """
    THE CORE PERSISTENCE TEST.

    Simulates an app restart: store data, clear the in-memory client
    cache (as if the Python process ended and restarted), then confirm
    the data is still readable from disk via a fresh client pointed at
    the same path. This is the entire reason PersistentClient was chosen
    over an in-memory client - if this test fails, persistence is broken
    and every uploaded document would vanish on every app restart.
    """
    chunks = [make_embedded_chunk(0)]
    vs.store_chunks(chunks, persist_dir=store_dir)

    # Simulate a fresh process by clearing the cached client for this path
    vs._clients.pop(store_dir, None)

    count_after_simulated_restart = vs.count_chunks(persist_dir=store_dir)
    assert count_after_simulated_restart == 1, (
        "Data did not survive a simulated restart - persistence is broken."
    )


# ---------------------------------------------------------------------------
# Multi-document ID isolation - the reason source_file/_build_record_id exist
# ---------------------------------------------------------------------------

def test_chunk_id_zero_from_two_documents_does_not_collide(store_dir):
    """
    THE CORE ID-COLLISION TEST.

    Two different documents both produce a chunk with chunk_id=0 (every
    document's first chunk does). Without source_file disambiguating the
    record ID, the second document's chunk_id=0 would either overwrite
    the first's or raise a duplicate-ID error. This test proves both
    chunks are stored as genuinely separate records.
    """
    doc_a_chunk = make_embedded_chunk(0, source_file="doc_a.pdf")
    doc_b_chunk = make_embedded_chunk(0, source_file="doc_b.pdf")  # same chunk_id!

    vs.store_chunks([doc_a_chunk], persist_dir=store_dir)
    vs.store_chunks([doc_b_chunk], persist_dir=store_dir)  # would raise if IDs collided

    assert vs.count_chunks(persist_dir=store_dir) == 2, (
        "Expected 2 separate chunks (chunk_id=0 from two different "
        "documents), but got a different count - IDs likely collided."
    )


def test_delete_by_source_file_only_removes_that_documents_chunks(store_dir):
    """Deleting one document's chunks must not affect another document's chunks."""
    doc_a_chunks = [make_embedded_chunk(0, source_file="doc_a.pdf"),
                    make_embedded_chunk(1, source_file="doc_a.pdf")]
    doc_b_chunks = [make_embedded_chunk(0, source_file="doc_b.pdf")]

    vs.store_chunks(doc_a_chunks, persist_dir=store_dir)
    vs.store_chunks(doc_b_chunks, persist_dir=store_dir)
    assert vs.count_chunks(persist_dir=store_dir) == 3

    vs.delete_by_source_file("doc_a.pdf", persist_dir=store_dir)

    assert vs.count_chunks(persist_dir=store_dir) == 1, (
        "Expected only doc_b's 1 chunk to remain after deleting doc_a's chunks."
    )


def test_delete_by_source_file_with_no_matching_chunks_is_a_no_op(store_dir):
    """Deleting a source_file that was never stored should not error."""
    chunks = [make_embedded_chunk(0, source_file="real_doc.pdf")]
    vs.store_chunks(chunks, persist_dir=store_dir)

    vs.delete_by_source_file("nonexistent_doc.pdf", persist_dir=store_dir)  # should not raise

    assert vs.count_chunks(persist_dir=store_dir) == 1


def test_delete_all_chunks_removes_every_document(store_dir):
    chunks = [
        make_embedded_chunk(0, source_file="doc_a.pdf"),
        make_embedded_chunk(1, source_file="doc_a.pdf"),
        make_embedded_chunk(0, source_file="doc_b.pdf"),
    ]
    vs.store_chunks(chunks, persist_dir=store_dir)

    deleted = vs.delete_all_chunks(persist_dir=store_dir)

    assert deleted == 3
    assert vs.count_chunks(persist_dir=store_dir) == 0


def test_delete_all_chunks_on_empty_collection_returns_zero(store_dir):
    deleted = vs.delete_all_chunks(persist_dir=store_dir)

    assert deleted == 0
    assert vs.count_chunks(persist_dir=store_dir) == 0


# ---------------------------------------------------------------------------
# Metadata sanitization - the None -> "unknown" conversion
# ---------------------------------------------------------------------------

def test_none_source_file_is_stored_as_unknown_string(store_dir):
    """
    ChromaDB metadata rejects None values. chunker.py's source_file
    legitimately defaults to None for backward-compatible callers - this
    must be converted to a valid metadata value ("unknown") rather than
    causing a storage error on an otherwise valid chunk.
    """
    chunk = make_embedded_chunk(0, source_file=None)

    # Should not raise - storing a None source_file must succeed.
    stored = vs.store_chunks([chunk], persist_dir=store_dir)
    assert stored == 1

    collection = vs.get_collection(persist_dir=store_dir)
    result = collection.get(ids=["unknown::0"])
    assert result["metadatas"][0]["source_file"] == "unknown"


def test_build_record_id_combines_source_file_and_chunk_id():
    chunk = make_embedded_chunk(7, source_file="my_report.pdf")
    record_id = vs._build_record_id(chunk)
    assert record_id == "my_report.pdf::7"


def test_sanitize_metadata_excludes_text_and_embedding():
    """
    text and embedding are stored separately by ChromaDB (as the
    document and the indexed vector, respectively) - they should NOT
    also appear duplicated inside the metadata dict.
    """
    chunk = make_embedded_chunk(0)
    metadata = vs._sanitize_metadata(chunk)
    assert "text" not in metadata
    assert "embedding" not in metadata
    assert set(metadata.keys()) == {"chunk_id", "page_number", "locator_type", "source_file"}


# ---------------------------------------------------------------------------
# count_chunks
# ---------------------------------------------------------------------------

def test_count_chunks_on_empty_collection_is_zero(store_dir):
    assert vs.count_chunks(persist_dir=store_dir) == 0


def test_count_chunks_reflects_stored_documents_text_and_embeddings(store_dir):
    """Sanity check that stored documents' actual text is retrievable, not just counted."""
    chunk = make_embedded_chunk(0, text="A very specific sentence to look for.")
    vs.store_chunks([chunk], persist_dir=store_dir)

    collection = vs.get_collection(persist_dir=store_dir)
    result = collection.get(ids=[vs._build_record_id(chunk)])
    assert result["documents"][0] == "A very specific sentence to look for."
