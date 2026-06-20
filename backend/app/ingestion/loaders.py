"""
loaders.py - Phase 1, Chunk 1

WHY THIS EXISTS:
Before we can chunk or embed anything, we need to extract raw text from
documents while preserving WHERE that text came from (which page). This
page-level metadata is what makes citations possible later - without it,
DocMind could only ever say "this came from somewhere in the document,"
not "this came from page 4."

Design decision: we extract page-by-page rather than dumping the whole
PDF into one giant string. This costs a little extra complexity now but
is the difference between a toy RAG demo and one that can actually point
to a source.

Each loader returns dicts with a "locator_type" field so downstream code
(especially the citation UI in Phase 4) knows whether "page_number" is
a true rendered page or a structural proxy like paragraph index.
"""

from pathlib import Path
from pypdf import PdfReader
from docx import Document


def load_pdf(file_path: str) -> list[dict]:
    """
    Extract text from a PDF, one entry per page.

    Returns a list of dicts like:
        [{"page_number": 2, "locator_type": "page", "text": "..."}, ...]

    Pages with no extractable text (e.g. scanned images with no OCR) are
    skipped, not silently included as empty chunks later.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"No file found at {file_path}")

    reader = PdfReader(str(path))
    pages = []

    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            pages.append({
                "page_number": i + 1,  # 1-indexed, matches how humans reference pages
                "locator_type": "page",  # true rendered page from the PDF
                "text": text.strip()
            })

    return pages


def load_docx(file_path: str) -> list[dict]:
    """
    Extract text from a DOCX file, one entry per non-empty paragraph.

    WHY paragraph-level granularity instead of treating the whole doc as
    one blob: DOCX files have no fixed page boundaries - "pages" depend
    on fonts, margins, and the rendering engine. Paragraph index is the
    finest *deterministic* locator we can extract from the XML, and it
    gives downstream citation code a stable reference to point users back
    to a specific location in the source document.

    Returns a list of dicts like:
        [{"page_number": 5, "locator_type": "paragraph_index", "text": "..."}, ...]

    The "page_number" field holds the 1-indexed paragraph position -
    specifically, the paragraph's TRUE position among all paragraphs in
    the document (same indexing principle as load_pdf's page_number),
    not a count of only the non-empty ones. This matters for citation
    traceability: if blank paragraphs were silently excluded from the
    count, "page_number: 4" might not correspond to the actual 4th
    paragraph in the file, and anyone verifying a citation by opening the
    document and counting paragraphs would land on the wrong spot - a
    silent, hard-to-detect error rather than an obvious one.

    "locator_type" tells downstream consumers this is a structural proxy,
    not a true rendered page - so the citation UI can render "para. 12"
    instead of "Page 12".

    Empty paragraphs (whitespace-only, blank lines between sections) are
    skipped from the *output*, same rationale as load_pdf skipping pages
    with no text - but NOT skipped from the index count, since that
    would break true-position traceability.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"No file found at {file_path}")

    doc = Document(str(path))
    paragraphs = []

    # Use the TRUE index from enumerate (i + 1), same pattern as load_pdf's
    # page_number. Gaps from skipped blank paragraphs are preserved, not
    # compacted - see docstring above for why this matters.
    for i, para in enumerate(doc.paragraphs):
        text = para.text
        if text and text.strip():
            paragraphs.append({
                "page_number": i + 1,  # true 1-indexed position, gaps preserved
                "locator_type": "paragraph_index",  # not a true page - see docstring
                "text": text.strip()
            })

    return paragraphs


if __name__ == "__main__":
    # Quick manual test - run this file directly with a sample file to sanity check.
    # Usage: python loaders.py path/to/test.pdf
    #        python loaders.py path/to/test.docx
    import sys

    if len(sys.argv) < 2:
        print("Usage: python loaders.py <path_to_pdf_or_docx>")
        sys.exit(1)

    test_path = sys.argv[1]

    if test_path.lower().endswith(".pdf"):
        result = load_pdf(test_path)
        label = "page"
    elif test_path.lower().endswith(".docx"):
        result = load_docx(test_path)
        label = "paragraph"
    else:
        print("Unsupported file type - pass a .pdf or .docx file")
        sys.exit(1)

    print(f"Extracted {len(result)} {label}(s) with text.")
    if result:
        first = result[0]
        print(f"--- {label.capitalize()} {first['page_number']} (locator_type={first['locator_type']}) preview ---")
        print(first["text"][:300])
