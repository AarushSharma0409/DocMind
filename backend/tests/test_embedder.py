"""
test_embedder.py - Phase 1, Chunk 3 tests

WHY THIS EXISTS:
embedder.py is the bridge between chunked text and ChromaDB storage. If
metadata (chunk_id, page_number, locator_type) gets dropped or
mismatched while attaching embeddings, citations break at this stage
even if loaders.py and chunker.py are both correct.

WHY A MOCK MODEL: loading the real sentence-transformers model requires
a one-time network download (~90MB) and real compute time per test run.
Using a fake model with a fixed, predictable output lets these tests run
in milliseconds and verify the LOGIC around the model (filtering,
batching, metadata propagation, contract shape) without depending on
network access or burning real model-loading time on every test run.

The real model's actual embedding quality (does it produce good
vectors) isn't something a unit test should verify anyway - that's a
property of the pretrained model itself, not code we wrote. What we
DO need to verify is that our code correctly wires inputs to outputs.
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "ingestion"))
import embedder


class FakeModel:
    """
    A stand-in for SentenceTransformer that returns deterministic,
    predictably-shaped fake vectors instead of running real inference.
    """

    def encode(self, texts, show_progress_bar=False):
        import numpy as np
        # Each fake vector is filled with the input's index, so we can
        # verify correspondence between input order and output order.
        return np.array([
            [float(i)] * embedder.EMBEDDING_DIMENSION
            for i in range(len(texts))
        ])


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    """
    Auto-applied to every test in this file: replaces the real model
    with FakeModel so no test accidentally triggers a real download or
    depends on network access.
    """
    monkeypatch.setattr(embedder, "_model", FakeModel())
    yield
    monkeypatch.setattr(embedder, "_model", None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_chunk(chunk_id: int, text: str, page_number: int = 1,
               locator_type: str = "page") -> dict:
    return {
        "chunk_id": chunk_id,
        "page_number": page_number,
        "locator_type": locator_type,
        "text": text,
    }


# ---------------------------------------------------------------------------
# embed_chunks - basic contract
# ---------------------------------------------------------------------------

def test_embed_chunks_returns_same_count_for_valid_input():
    chunks = [make_chunk(0, "First chunk text"), make_chunk(1, "Second chunk text")]
    result = embedder.embed_chunks(chunks)
    assert len(result) == 2


def test_embed_chunks_attaches_embedding_key():
    chunks = [make_chunk(0, "Some text")]
    result = embedder.embed_chunks(chunks)
    assert "embedding" in result[0]


def test_embed_chunks_embedding_has_correct_dimension():
    chunks = [make_chunk(0, "Some text")]
    result = embedder.embed_chunks(chunks)
    assert len(result[0]["embedding"]) == embedder.EMBEDDING_DIMENSION


def test_embed_chunks_embedding_is_plain_list_not_numpy():
    """
    ChromaDB (next piece) expects plain Python lists, not numpy arrays.
    This locks in that .tolist() conversion actually happens.
    """
    chunks = [make_chunk(0, "Some text")]
    result = embedder.embed_chunks(chunks)
    assert isinstance(result[0]["embedding"], list)
    assert all(isinstance(v, float) for v in result[0]["embedding"])


# ---------------------------------------------------------------------------
# embed_chunks - metadata preservation (the most important contract)
# ---------------------------------------------------------------------------

def test_embed_chunks_preserves_all_original_metadata():
    """
    THE CORE METADATA TEST.

    Every original key (chunk_id, page_number, locator_type, text) must
    survive into the output, alongside the new "embedding" key. Losing
    any of these here breaks citations downstream, the same class of bug
    we caught in loaders.py and guarded against in chunker.py.
    """
    chunks = [make_chunk(5, "Citation-relevant text", page_number=12, locator_type="paragraph_index")]
    result = embedder.embed_chunks(chunks)

    assert result[0]["chunk_id"] == 5
    assert result[0]["page_number"] == 12
    assert result[0]["locator_type"] == "paragraph_index"
    assert result[0]["text"] == "Citation-relevant text"


def test_embed_chunks_preserves_order_and_correspondence():
    """
    Chunk N's embedding must correspond to chunk N's text - not be
    accidentally shuffled during the batch encode/zip step.
    """
    chunks = [
        make_chunk(0, "Alpha text", page_number=1),
        make_chunk(1, "Beta text", page_number=2),
        make_chunk(2, "Gamma text", page_number=3),
    ]
    result = embedder.embed_chunks(chunks)

    # FakeModel returns vectors filled with the input's positional index,
    # so embedding[0] == [0.0, 0.0, ...], embedding[1] == [1.0, 1.0, ...], etc.
    # This lets us verify input order maps correctly to output order.
    assert result[0]["embedding"][0] == 0.0
    assert result[1]["embedding"][0] == 1.0
    assert result[2]["embedding"][0] == 2.0

    assert result[0]["page_number"] == 1
    assert result[1]["page_number"] == 2
    assert result[2]["page_number"] == 3


def test_embed_chunks_does_not_mutate_input_chunks():
    """
    embed_chunks should return NEW dicts (via the **chunk spread), not
    mutate the caller's original chunk dicts in place - mutating shared
    input is a common source of confusing bugs elsewhere in a pipeline.
    """
    original_chunk = make_chunk(0, "Some text")
    original_keys_before = set(original_chunk.keys())

    embedder.embed_chunks([original_chunk])

    assert set(original_chunk.keys()) == original_keys_before
    assert "embedding" not in original_chunk


# ---------------------------------------------------------------------------
# embed_chunks - filtering empty/whitespace input
# ---------------------------------------------------------------------------

def test_embed_chunks_filters_out_empty_text():
    chunks = [make_chunk(0, "Real content"), make_chunk(1, "")]
    result = embedder.embed_chunks(chunks)
    assert len(result) == 1
    assert result[0]["chunk_id"] == 0


def test_embed_chunks_filters_out_whitespace_only_text():
    chunks = [make_chunk(0, "Real content"), make_chunk(1, "   \n  ")]
    result = embedder.embed_chunks(chunks)
    assert len(result) == 1
    assert result[0]["chunk_id"] == 0


def test_embed_chunks_all_empty_input_returns_empty_list():
    chunks = [make_chunk(0, ""), make_chunk(1, "   ")]
    result = embedder.embed_chunks(chunks)
    assert result == []


def test_embed_chunks_empty_list_input_returns_empty_list():
    assert embedder.embed_chunks([]) == []


# ---------------------------------------------------------------------------
# get_model - error handling for load failures
# ---------------------------------------------------------------------------

def test_get_model_raises_clear_error_on_load_failure(monkeypatch):
    """
    If the underlying SentenceTransformer constructor fails (e.g. no
    network access to download the model), get_model() should raise a
    RuntimeError with a clear, actionable message - not let a cryptic
    low-level exception propagate unexplained.
    """
    monkeypatch.setattr(embedder, "_model", None)  # force a fresh load attempt

    def raise_error(*args, **kwargs):
        raise OSError("simulated network failure")

    monkeypatch.setattr(embedder, "SentenceTransformer", raise_error)

    with pytest.raises(RuntimeError, match="Failed to load embedding model"):
        embedder.get_model()


def test_get_model_reuses_loaded_model_instance():
    """
    The lazy singleton pattern: calling get_model() twice should return
    the SAME object, not load/create a new one each time.
    """
    first_call = embedder.get_model()
    second_call = embedder.get_model()
    assert first_call is second_call
