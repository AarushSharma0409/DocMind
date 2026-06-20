"""
test_retriever.py - Phase 2, Chunk 1 tests

WHY THIS EXISTS:
retriever.py is the first piece of Phase 2 - if it returns wrong
results, wrong ordering, or wrong similarity scores, every later piece
(query routing, generation, citation, confidence signaling) inherits
that brokenness silently.

WHY A FAKE MODEL WITH CONTROLLED VECTORS: real semantic ranking quality
(does "customer attrition" really match "churn" well) is a property of
the pretrained embedding model, not code we wrote - not something a unit
test should try to verify. What these tests DO need to verify is that
OUR code correctly: embeds the query, queries ChromaDB, orders results,
converts distance to similarity correctly, and preserves citation
metadata. A fake model with hand-picked vectors (orthogonal, identical,
etc.) lets us assert EXACT expected similarity scores, which a real
model's organic output never would, making the math itself directly
checkable.

THE COSINE DISTANCE BUG, AND WHY IT'S TESTED DIRECTLY: a real bug was
found during development - ChromaDB's default distance metric (if not
explicitly configured) is squared L2, not cosine, which silently broke
every similarity score (all came back as 0.0) while leaving result
ORDERING correct - making it easy to miss. test_similarity_score_*
tests below assert exact similarity values for known vector
relationships, specifically to catch this class of regression if the
cosine configuration in vector_store.py's get_collection() is ever
accidentally removed.
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import app.ingestion.embedder as embedder_module
from app.ingestion.embedder import embed_chunks
from app.storage.vector_store import store_chunks
from app.retrieval.retriever import retrieve, _format_results


class FakeModel:
    """
    Maps text to ONE of three orthogonal unit vectors based on keyword
    presence, so similarity relationships between query and stored
    chunks are fully predictable and exactly assertable - unlike a real
    model's organic output.
    """

    TOPIC_A_WORDS = ("churn", "attrition", "retention")
    TOPIC_B_WORDS = ("revenue", "profit", "earnings")

    def encode(self, texts, show_progress_bar=False):
        import numpy as np
        vectors = []
        for t in texts:
            t_lower = t.lower()
            if any(w in t_lower for w in self.TOPIC_A_WORDS):
                vectors.append([1.0, 0.0, 0.0] + [0.0] * 381)
            elif any(w in t_lower for w in self.TOPIC_B_WORDS):
                vectors.append([0.0, 1.0, 0.0] + [0.0] * 381)
            else:
                vectors.append([0.0, 0.0, 1.0] + [0.0] * 381)
        return np.array(vectors)


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    """Auto-applied to every test: no real model download/inference needed."""
    monkeypatch.setattr(embedder_module, "_model", FakeModel())
    yield
    monkeypatch.setattr(embedder_module, "_model", None)


@pytest.fixture
def store_dir(tmp_path) -> str:
    return str(tmp_path / "chroma_test_db")


def make_chunk(chunk_id: int, text: str, source_file: str = "doc.pdf",
               page_number: int = 1, locator_type: str = "page") -> dict:
    return {
        "chunk_id": chunk_id,
        "page_number": page_number,
        "locator_type": locator_type,
        "source_file": source_file,
        "text": text,
    }


def seed_store(store_dir: str, chunks: list[dict]) -> None:
    """Embed and store a list of raw chunks into the given store_dir."""
    embedded = embed_chunks(chunks)
    store_chunks(embedded, persist_dir=store_dir)


# ---------------------------------------------------------------------------
# Basic retrieval behavior
# ---------------------------------------------------------------------------

def test_retrieve_returns_results_for_valid_query(store_dir):
    seed_store(store_dir, [make_chunk(0, "Customer churn is rising.")])
    results = retrieve("What about churn?", persist_dir=store_dir)
    assert len(results) == 1


def test_retrieve_respects_top_k(store_dir):
    chunks = [make_chunk(i, f"Customer churn note number {i}.") for i in range(10)]
    seed_store(store_dir, chunks)

    results = retrieve("churn question", top_k=3, persist_dir=store_dir)
    assert len(results) == 3


def test_retrieve_caps_top_k_at_collection_size(store_dir):
    """Asking for more results than exist shouldn't error - just return what's there."""
    seed_store(store_dir, [make_chunk(0, "Customer churn note.")])
    results = retrieve("churn question", top_k=50, persist_dir=store_dir)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Ranking correctness
# ---------------------------------------------------------------------------

def test_retrieve_ranks_matching_topic_first(store_dir):
    """
    The core semantic-ranking test: a query about churn/attrition should
    rank the churn-related chunk above unrelated chunks.
    """
    chunks = [
        make_chunk(0, "Quarterly revenue grew significantly."),
        make_chunk(1, "Customer churn increased last quarter."),
        make_chunk(2, "Preheat the oven to 350 degrees."),
    ]
    seed_store(store_dir, chunks)

    results = retrieve("What did the report say about customer attrition?", persist_dir=store_dir)

    assert "churn" in results[0]["text"].lower(), (
        f"Expected churn-related chunk to rank first, got: {results[0]['text']!r}"
    )


def test_retrieve_results_ordered_by_descending_similarity(store_dir):
    chunks = [
        make_chunk(0, "Quarterly revenue grew significantly."),  # topic B
        make_chunk(1, "Customer churn increased last quarter."),  # topic A (exact query match)
        make_chunk(2, "Preheat the oven to 350 degrees."),  # topic C (unrelated)
    ]
    seed_store(store_dir, chunks)

    results = retrieve("customer retention question", persist_dir=store_dir)

    similarities = [r["similarity"] for r in results]
    assert similarities == sorted(similarities, reverse=True), (
        f"Results not in descending similarity order: {similarities}"
    )


# ---------------------------------------------------------------------------
# Similarity score math - the cosine distance bug regression tests
# ---------------------------------------------------------------------------

def test_similarity_score_for_exact_match_is_one(store_dir):
    """
    REGRESSION TEST for the cosine-distance bug.

    A query identical in topic to a stored chunk (same FakeModel vector)
    should produce similarity == 1.0 exactly. Before the fix (ChromaDB
    defaulting to squared L2 instead of cosine), this came back as 0.0
    even for a perfect match - silently breaking confidence signaling
    while leaving result ORDERING deceptively correct.
    """
    seed_store(store_dir, [make_chunk(0, "Customer churn increased.")])
    results = retrieve("churn retention attrition", persist_dir=store_dir)

    assert results[0]["similarity"] == pytest.approx(1.0, abs=1e-6), (
        f"Expected similarity=1.0 for an exact topic match, got "
        f"{results[0]['similarity']} - the cosine distance configuration "
        f"may have been reverted (ChromaDB defaults to squared L2, not "
        f"cosine, unless explicitly configured in vector_store.py)."
    )


def test_similarity_score_for_orthogonal_topics_is_zero(store_dir):
    """
    Companion to the exact-match test: two genuinely unrelated topics
    (orthogonal vectors under FakeModel) should score similarity == 0.0,
    not some other arbitrary low number.
    """
    seed_store(store_dir, [make_chunk(0, "Preheat the oven to 350 degrees.")])
    results = retrieve("churn retention attrition", persist_dir=store_dir)

    assert results[0]["similarity"] == pytest.approx(0.0, abs=1e-6)


def test_similarity_scores_are_within_valid_range(store_dir):
    """All returned similarity scores must fall within [0, 1], never outside it."""
    chunks = [
        make_chunk(0, "Customer churn increased."),
        make_chunk(1, "Quarterly revenue grew."),
        make_chunk(2, "Preheat the oven."),
    ]
    seed_store(store_dir, chunks)

    results = retrieve("a general question", persist_dir=store_dir)
    for r in results:
        assert 0.0 <= r["similarity"] <= 1.0, f"Similarity out of range: {r['similarity']}"


# ---------------------------------------------------------------------------
# Citation metadata preservation
# ---------------------------------------------------------------------------

def test_retrieve_results_include_full_citation_metadata(store_dir):
    seed_store(store_dir, [
        make_chunk(0, "Customer churn increased.", source_file="report.pdf",
                   page_number=7, locator_type="page")
    ])
    results = retrieve("churn question", persist_dir=store_dir)

    assert results[0]["source_file"] == "report.pdf"
    assert results[0]["page_number"] == 7
    assert results[0]["locator_type"] == "page"


def test_retrieve_preserves_locator_type_for_docx_source(store_dir):
    seed_store(store_dir, [
        make_chunk(0, "Customer churn increased.", source_file="notes.docx",
                   page_number=3, locator_type="paragraph_index")
    ])
    results = retrieve("churn question", persist_dir=store_dir)

    assert results[0]["locator_type"] == "paragraph_index"


def test_retrieve_distinguishes_chunks_from_different_source_files(store_dir):
    chunks = [
        make_chunk(0, "Customer churn increased.", source_file="report_a.pdf"),
        make_chunk(0, "Customer churn also rose here.", source_file="report_b.pdf"),
    ]
    seed_store(store_dir, chunks)

    results = retrieve("churn question", persist_dir=store_dir)
    source_files = {r["source_file"] for r in results}
    assert source_files == {"report_a.pdf", "report_b.pdf"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_retrieve_empty_query_returns_empty_list(store_dir):
    seed_store(store_dir, [make_chunk(0, "Some content.")])
    assert retrieve("", persist_dir=store_dir) == []


def test_retrieve_whitespace_only_query_returns_empty_list(store_dir):
    seed_store(store_dir, [make_chunk(0, "Some content.")])
    assert retrieve("   \n  ", persist_dir=store_dir) == []


def test_retrieve_against_empty_collection_returns_empty_list(store_dir):
    """No documents stored yet - should return [] cleanly, not error."""
    results = retrieve("any question at all", persist_dir=store_dir)
    assert results == []


# ---------------------------------------------------------------------------
# _format_results - the ChromaDB response-unwrapping helper
# ---------------------------------------------------------------------------

def test_format_results_unwraps_nested_chromadb_response():
    """
    ChromaDB's raw response nests results one level deeper than expected
    (a list of lists, for batch query support) - this confirms the
    unwrapping happens correctly for a single query.
    """
    raw_chroma_response = {
        "documents": [["First chunk text.", "Second chunk text."]],
        "metadatas": [[
            {"source_file": "a.pdf", "page_number": 1, "locator_type": "page"},
            {"source_file": "b.pdf", "page_number": 2, "locator_type": "page"},
        ]],
        "distances": [[0.1, 0.5]],
    }

    formatted = _format_results(raw_chroma_response)

    assert len(formatted) == 2
    assert formatted[0]["text"] == "First chunk text."
    assert formatted[0]["source_file"] == "a.pdf"
    assert formatted[0]["similarity"] == pytest.approx(0.9, abs=1e-6)
    assert formatted[1]["similarity"] == pytest.approx(0.5, abs=1e-6)


def test_format_results_clamps_similarity_to_valid_range():
    """
    Cosine distance can technically exceed the typical [0, 2] range in
    edge cases (floating point quirks) - similarity should always be
    clamped to [0, 1], never a confusing out-of-range value.
    """
    raw_chroma_response = {
        "documents": [["Some text."]],
        "metadatas": [[{"source_file": "a.pdf", "page_number": 1, "locator_type": "page"}]],
        "distances": [[3.5]],  # would produce similarity = -2.5 unclamped
    }

    formatted = _format_results(raw_chroma_response)
    assert formatted[0]["similarity"] == 0.0
