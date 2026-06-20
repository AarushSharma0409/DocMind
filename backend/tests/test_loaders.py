"""
test_loaders.py — Phase 1, Chunk 1 tests

WHY THIS EXISTS:
loaders.py is the foundation everything else (chunking, embeddings,
citations) gets built on top of. If page/paragraph numbering is wrong
here, every citation DocMind ever shows will quietly point to the wrong
place — and nothing will crash to tell you. These tests exist to catch
that class of silent bug before it propagates.

The most important test in this file is test_load_docx_preserves_true_position,
which is a regression test for a real bug we found: an earlier version of
load_docx used a counter that only incremented on non-empty paragraphs,
so skipped blank paragraphs silently shifted every later position number.
"""

import pytest
from pathlib import Path
from docx import Document as DocxDocument
from reportlab.pdfgen import canvas

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "ingestion"))
from loaders import load_pdf, load_docx


# ---------------------------------------------------------------------------
# Fixtures — build known-structure test files so we know the "correct"
# answer before running anything, rather than just eyeballing output.
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_docx(tmp_path) -> str:
    """A DOCX with no blanks — the easy case."""
    path = tmp_path / "simple.docx"
    doc = DocxDocument()
    doc.add_paragraph("First paragraph.")
    doc.add_paragraph("Second paragraph.")
    doc.save(str(path))
    return str(path)


@pytest.fixture
def docx_with_gaps(tmp_path) -> str:
    """
    A DOCX with blank paragraphs deliberately scattered through it.
    True positions of real content: 1, 3, 6.
    Blanks at: 2, 4, 5.
    This is the file that exposes the indexing bug if it's reintroduced.
    """
    path = tmp_path / "gaps.docx"
    doc = DocxDocument()
    doc.add_paragraph("Paragraph A - has content")   # true position 1
    doc.add_paragraph("")                              # true position 2 - blank
    doc.add_paragraph("Paragraph C - has content")    # true position 3
    doc.add_paragraph("")                              # true position 4 - blank
    doc.add_paragraph("")                              # true position 5 - blank
    doc.add_paragraph("Paragraph F - has content")    # true position 6
    doc.save(str(path))
    return str(path)


@pytest.fixture
def simple_pdf(tmp_path) -> str:
    """A 2-page PDF with simple text content on each page."""
    path = tmp_path / "simple.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, "Page one content")
    c.showPage()
    c.drawString(100, 750, "Page two content")
    c.showPage()
    c.save()
    return str(path)


@pytest.fixture
def pdf_with_blank_page(tmp_path) -> str:
    """
    A 3-page PDF where the middle page is genuinely blank (no text drawn).
    True positions of real content: 1, 3. Page 2 should be skipped.
    """
    path = tmp_path / "blank_page.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, "Page one content")
    c.showPage()
    # page two intentionally left blank — no drawString call
    c.showPage()
    c.drawString(100, 750, "Page three content")
    c.showPage()
    c.save()
    return str(path)


# ---------------------------------------------------------------------------
# load_pdf tests
# ---------------------------------------------------------------------------

def test_load_pdf_returns_correct_page_count(simple_pdf):
    result = load_pdf(simple_pdf)
    assert len(result) == 2


def test_load_pdf_preserves_text_content(simple_pdf):
    result = load_pdf(simple_pdf)
    assert "Page one content" in result[0]["text"]
    assert "Page two content" in result[1]["text"]


def test_load_pdf_sets_locator_type_to_page(simple_pdf):
    result = load_pdf(simple_pdf)
    assert all(entry["locator_type"] == "page" for entry in result)


def test_load_pdf_page_numbers_are_one_indexed(simple_pdf):
    result = load_pdf(simple_pdf)
    assert result[0]["page_number"] == 1
    assert result[1]["page_number"] == 2


def test_load_pdf_skips_blank_pages_but_preserves_true_position(pdf_with_blank_page):
    """
    The middle page has no text. It should be excluded from the output,
    but page 3's page_number should still read 3, not 2 — proving the
    blank page wasn't silently compacted out of the numbering.
    """
    result = load_pdf(pdf_with_blank_page)
    page_numbers = [entry["page_number"] for entry in result]
    assert page_numbers == [1, 3], (
        f"Expected true positions [1, 3] with page 2 skipped, got {page_numbers}"
    )


def test_load_pdf_raises_on_missing_file():
    with pytest.raises(FileNotFoundError):
        load_pdf("/this/path/does/not/exist.pdf")


# ---------------------------------------------------------------------------
# load_docx tests
# ---------------------------------------------------------------------------

def test_load_docx_returns_correct_paragraph_count(simple_docx):
    result = load_docx(simple_docx)
    assert len(result) == 2


def test_load_docx_preserves_text_content(simple_docx):
    result = load_docx(simple_docx)
    assert result[0]["text"] == "First paragraph."
    assert result[1]["text"] == "Second paragraph."


def test_load_docx_sets_locator_type_to_paragraph_index(simple_docx):
    result = load_docx(simple_docx)
    assert all(entry["locator_type"] == "paragraph_index" for entry in result)


def test_load_docx_raises_on_missing_file():
    with pytest.raises(FileNotFoundError):
        load_docx("/this/path/does/not/exist.docx")


def test_load_docx_preserves_true_position(docx_with_gaps):
    """
    REGRESSION TEST for the indexing bug found during development.

    An earlier version of load_docx used a counter that only incremented
    on non-empty paragraphs. That made "page_number" a count of "how many
    non-blank paragraphs we've seen so far" rather than the paragraph's
    actual position in the document. The bug was silent — no crash, no
    error — it just meant every citation pointing past the first blank
    paragraph would be wrong by however many blanks preceded it.

    This test uses a file with known gaps (blanks at positions 2, 4, 5)
    and asserts the real content lands at its TRUE positions: 1, 3, 6 —
    not the compacted [1, 2, 3] the buggy version would have produced.
    """
    result = load_docx(docx_with_gaps)
    page_numbers = [entry["page_number"] for entry in result]
    assert page_numbers == [1, 3, 6], (
        f"Expected true positions [1, 3, 6] with blanks at 2/4/5 skipped, "
        f"got {page_numbers}. If this is [1, 2, 3], the dense-counter bug "
        f"has been reintroduced."
    )


def test_load_docx_empty_paragraphs_excluded_from_output(docx_with_gaps):
    """Blank paragraphs should not appear as empty entries in the output."""
    result = load_docx(docx_with_gaps)
    assert all(entry["text"].strip() != "" for entry in result)


# ---------------------------------------------------------------------------
# Cross-loader consistency — both loaders must return the same shape so
# downstream code (chunker, citation UI) can treat them uniformly.
# ---------------------------------------------------------------------------

def test_pdf_and_docx_return_same_dict_keys(simple_pdf, simple_docx):
    pdf_result = load_pdf(simple_pdf)
    docx_result = load_docx(simple_docx)
    assert set(pdf_result[0].keys()) == set(docx_result[0].keys()) == {
        "page_number", "locator_type", "text"
    }
