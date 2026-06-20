"""
test_chunker.py - Phase 1, Chunk 2 tests

WHY THIS EXISTS:
chunker.py is the second link in the ingestion pipeline - if chunk
boundaries are wrong, overlap doesn't actually overlap, or metadata
(page_number/locator_type) doesn't propagate from loaders.py output into
every resulting chunk, citations break in the same silent way the
loaders.py bug did. These tests catch that class of regression before
it reaches embeddings or ChromaDB.

Key things under test:
- Chunks stay within CHUNK_SIZE_TOKENS (with small tolerance for the
  oversized-single-unit edge case)
- Overlap between consecutive chunks is real and non-trivial, not just
  claimed in a comment
- page_number / locator_type propagate correctly from input to every
  chunk produced from that input, including when one page produces
  multiple chunks
- The sentence-splitting fallback actually engages for paragraphs that
  are individually larger than CHUNK_SIZE_TOKENS
- Edge cases: empty input, single short page, multiple pages
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "ingestion"))
from chunker import (
    count_tokens,
    split_into_paragraphs,
    split_into_sentences,
    chunk_text,
    chunk_document,
    CHUNK_SIZE_TOKENS,
    CHUNK_OVERLAP_TOKENS,
)


# ---------------------------------------------------------------------------
# Helpers - build text of a controlled, known token size so assertions
# can check against real numbers rather than vague "is it roughly right".
# ---------------------------------------------------------------------------

def make_paragraph(label: str, sentence_count: int = 2) -> str:
    """Build a short, readable paragraph with a known label for tracing."""
    sentences = [
        f"This is sentence {i} of paragraph {label}, added to give it length."
        for i in range(1, sentence_count + 1)
    ]
    return " ".join(sentences)


def make_multi_paragraph_text(paragraph_count: int) -> str:
    """Build text with `paragraph_count` distinct, blank-line-separated paragraphs."""
    paragraphs = [make_paragraph(str(i)) for i in range(1, paragraph_count + 1)]
    return "\n\n".join(paragraphs)


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------

def test_count_tokens_returns_positive_int_for_nonempty_text():
    result = count_tokens("Hello world, this is a test sentence.")
    assert isinstance(result, int)
    assert result > 0


def test_count_tokens_scales_with_text_length():
    """A longer string should never produce a smaller token count."""
    short = "Hello world."
    long = "Hello world. " * 20
    assert count_tokens(long) > count_tokens(short)


def test_count_tokens_empty_string_is_minimal():
    """
    Empty string should produce a minimal token count. Note: in fallback
    mode (no tiktoken network access), count_tokens uses max(1, len//4),
    so an empty string returns 1, not 0 - this floor exists to avoid
    chunk_text() treating a near-empty unit as "free" to pack endlessly.
    With real tiktoken encoding, an empty string correctly encodes to 0
    tokens. Either way, the count must be small (<=1), never large.
    """
    assert count_tokens("") <= 1


# ---------------------------------------------------------------------------
# split_into_paragraphs
# ---------------------------------------------------------------------------

def test_split_into_paragraphs_basic():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    result = split_into_paragraphs(text)
    assert result == ["First paragraph.", "Second paragraph.", "Third paragraph."]


def test_split_into_paragraphs_handles_messy_whitespace_between_blanks():
    """
    A blank 'line' with stray whitespace (not a perfectly clean \\n\\n)
    should still be recognized as a paragraph break - this is the whole
    reason a regex is used instead of a plain string split.
    """
    text = "First paragraph.\n   \nSecond paragraph."
    result = split_into_paragraphs(text)
    assert result == ["First paragraph.", "Second paragraph."]


def test_split_into_paragraphs_no_blank_lines_returns_single_paragraph():
    text = "Just one continuous block of text with no blank lines anywhere."
    result = split_into_paragraphs(text)
    assert result == [text]


def test_split_into_paragraphs_empty_text_returns_empty_list():
    assert split_into_paragraphs("") == []
    assert split_into_paragraphs("   \n\n   ") == []


# ---------------------------------------------------------------------------
# split_into_sentences
# ---------------------------------------------------------------------------

def test_split_into_sentences_basic():
    text = "First sentence. Second sentence! Third sentence?"
    result = split_into_sentences(text)
    assert result == ["First sentence.", "Second sentence!", "Third sentence?"]


def test_split_into_sentences_preserves_punctuation():
    """Punctuation should stay attached to the sentence it ends, not be stripped."""
    result = split_into_sentences("Is this a question? Yes it is.")
    assert result[0].endswith("?")
    assert result[1].endswith(".")


def test_split_into_sentences_single_sentence_returns_single_item():
    text = "Just one sentence here."
    assert split_into_sentences(text) == [text]


# ---------------------------------------------------------------------------
# chunk_text - size limits and overlap
# ---------------------------------------------------------------------------

def test_chunk_text_small_input_produces_single_chunk():
    """Text well under CHUNK_SIZE_TOKENS shouldn't be split at all."""
    text = make_multi_paragraph_text(3)  # small, well under 500 tokens
    chunks = chunk_text(text)
    assert len(chunks) == 1


def test_chunk_text_large_input_produces_multiple_chunks():
    """
    Enough paragraphs to comfortably exceed CHUNK_SIZE_TOKENS should
    produce more than one chunk.
    """
    text = make_multi_paragraph_text(30)  # large enough to force splitting
    assert count_tokens(text) > CHUNK_SIZE_TOKENS  # sanity check the test setup itself
    chunks = chunk_text(text)
    assert len(chunks) > 1


def test_chunk_text_chunks_stay_within_size_limit():
    """
    Every chunk should be at or near CHUNK_SIZE_TOKENS - not wildly over.
    Small tolerance allowed since the packing loop adds one unit at a
    time and may slightly exceed the limit before stopping.
    """
    text = make_multi_paragraph_text(30)
    chunks = chunk_text(text)
    tolerance = 50  # generous buffer for one extra paragraph-sized unit
    for chunk in chunks:
        assert count_tokens(chunk) <= CHUNK_SIZE_TOKENS + tolerance, (
            f"Chunk exceeded size limit by more than tolerance: "
            f"{count_tokens(chunk)} tokens"
        )


def test_chunk_text_no_chunk_is_empty():
    text = make_multi_paragraph_text(30)
    chunks = chunk_text(text)
    assert all(chunk.strip() != "" for chunk in chunks)


def test_chunk_text_consecutive_chunks_actually_overlap():
    """
    THE CORE OVERLAP TEST.

    This proves overlap isn't just claimed in a docstring - it checks
    that some real text content appears at the end of one chunk AND the
    start of the next chunk, which is what overlap is supposed to do.
    Without this test, a future refactor could silently break overlap
    (e.g. set CHUNK_OVERLAP_TOKENS usage to 0) and nothing would catch it.
    """
    text = make_multi_paragraph_text(30)
    chunks = chunk_text(text)
    assert len(chunks) >= 2, "Test setup must produce at least 2 chunks to test overlap"

    # The last paragraph-sized unit of chunk N should reappear at the
    # start of chunk N+1. We check this by looking for a shared
    # substring - specifically, the last "sentence" of chunk N.
    first_chunk_end = chunks[0][-100:]  # last ~100 chars of chunk 0
    second_chunk_start = chunks[1][:200]  # first ~200 chars of chunk 1

    # Find a meaningfully-sized shared fragment rather than requiring
    # exact alignment (word boundaries may shift slightly with slicing).
    # We check that at least one full "sentence" from the tail of chunk 0
    # appears verbatim in the head of chunk 1.
    last_sentences = split_into_sentences(chunks[0])
    overlap_found = any(
        sentence in chunks[1] for sentence in last_sentences[-2:]
    )
    assert overlap_found, (
        "Expected at least one sentence from the end of chunk 0 to "
        "reappear at the start of chunk 1 - overlap is not working."
    )


def test_chunk_text_oversized_single_paragraph_triggers_sentence_fallback():
    """
    A single paragraph (no blank lines at all) larger than
    CHUNK_SIZE_TOKENS must still get split - via the sentence-level
    fallback - rather than being returned as one giant oversized chunk.
    """
    huge_paragraph = " ".join(
        f"This is sentence {i} in one giant paragraph with no breaks."
        for i in range(1, 60)
    )
    assert count_tokens(huge_paragraph) > CHUNK_SIZE_TOKENS  # sanity check setup

    chunks = chunk_text(huge_paragraph)
    assert len(chunks) > 1, (
        "A single oversized paragraph should be split via sentence "
        "fallback, not returned as one giant chunk."
    )


def test_chunk_text_empty_input_returns_empty_list():
    assert chunk_text("") == []


# ---------------------------------------------------------------------------
# chunk_document - metadata propagation, the most important contract
# ---------------------------------------------------------------------------

def test_chunk_document_propagates_page_number_to_every_chunk():
    """
    THE CORE METADATA TEST.

    When a single page is large enough to produce multiple chunks, every
    one of those chunks must still carry the correct page_number. This
    is the exact mechanism that keeps citations working once a page gets
    split - losing this silently breaks every citation from a multi-chunk
    page.
    """
    large_text = make_multi_paragraph_text(30)
    loaded_pages = [{"page_number": 7, "locator_type": "page", "text": large_text}]

    result = chunk_document(loaded_pages)
    assert len(result) > 1, "Test setup must produce multiple chunks from one page"
    assert all(c["page_number"] == 7 for c in result), (
        "All chunks from page 7 must report page_number=7, even when "
        "split into multiple chunks."
    )
    assert all(c["locator_type"] == "page" for c in result)


def test_chunk_document_keeps_chunks_from_different_pages_separate():
    """
    Chunks from page 1 and page 2 should never report the wrong page
    number - each chunk's metadata must match its true source page.
    """
    loaded_pages = [
        {"page_number": 1, "locator_type": "page", "text": make_multi_paragraph_text(2)},
        {"page_number": 2, "locator_type": "page", "text": make_multi_paragraph_text(2)},
    ]
    result = chunk_document(loaded_pages)

    page_1_chunks = [c for c in result if c["page_number"] == 1]
    page_2_chunks = [c for c in result if c["page_number"] == 2]

    assert len(page_1_chunks) > 0
    assert len(page_2_chunks) > 0
    assert len(page_1_chunks) + len(page_2_chunks) == len(result)


def test_chunk_document_preserves_locator_type_for_docx_source():
    """
    A page with locator_type="paragraph_index" (i.e. DOCX-sourced) must
    keep that locator_type through chunking - chunking must not silently
    convert it to "page" or drop the field.
    """
    loaded_pages = [
        {"page_number": 3, "locator_type": "paragraph_index", "text": make_multi_paragraph_text(2)}
    ]
    result = chunk_document(loaded_pages)
    assert len(result) > 0
    assert all(c["locator_type"] == "paragraph_index" for c in result)


def test_chunk_document_assigns_unique_sequential_chunk_ids():
    """
    chunk_id should be a stable, unique, running index across the whole
    document - used later as an identifier in ChromaDB.
    """
    loaded_pages = [
        {"page_number": 1, "locator_type": "page", "text": make_multi_paragraph_text(2)},
        {"page_number": 2, "locator_type": "page", "text": make_multi_paragraph_text(2)},
    ]
    result = chunk_document(loaded_pages)
    chunk_ids = [c["chunk_id"] for c in result]

    assert chunk_ids == list(range(len(result))), (
        "chunk_id should be a sequential, gap-free index starting at 0."
    )
    assert len(set(chunk_ids)) == len(chunk_ids), "chunk_id values must be unique."


def test_chunk_document_empty_input_returns_empty_list():
    assert chunk_document([]) == []


def test_chunk_document_page_with_no_extractable_chunks_contributes_nothing():
    """
    A page with empty/whitespace-only text (already an edge case
    loaders.py is supposed to filter out, but defensively testing here
    too) should contribute zero chunks, not a chunk with empty text.
    """
    loaded_pages = [{"page_number": 1, "locator_type": "page", "text": ""}]
    result = chunk_document(loaded_pages)
    assert result == []


def test_chunk_document_output_keys_match_expected_contract():
    """Every chunk dict must have exactly these five keys - nothing missing, nothing extra."""
    loaded_pages = [{"page_number": 1, "locator_type": "page", "text": make_multi_paragraph_text(2)}]
    result = chunk_document(loaded_pages)
    assert len(result) > 0
    expected_keys = {"chunk_id", "page_number", "locator_type", "source_file", "text"}
    assert set(result[0].keys()) == expected_keys


def test_chunk_document_propagates_source_file_to_every_chunk():
    """
    source_file disambiguates chunks across documents in the same
    ChromaDB collection - "page 4" is meaningless without knowing which
    file. Every chunk from a document must carry its source filename.
    """
    large_text = make_multi_paragraph_text(30)
    loaded_pages = [{"page_number": 1, "locator_type": "page", "text": large_text}]

    result = chunk_document(loaded_pages, source_file="quarterly_report.pdf")
    assert len(result) > 1, "Test setup must produce multiple chunks"
    assert all(c["source_file"] == "quarterly_report.pdf" for c in result)


def test_chunk_document_source_file_defaults_to_none():
    """Existing callers that don't pass source_file shouldn't be forced to."""
    loaded_pages = [{"page_number": 1, "locator_type": "page", "text": make_multi_paragraph_text(2)}]
    result = chunk_document(loaded_pages)
    assert all(c["source_file"] is None for c in result)