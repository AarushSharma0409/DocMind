"""
loaders.py — Phase 1, Chunk 1

WHY THIS EXISTS:
Before we can chunk or embed anything, we need to extract raw text from
documents while preserving WHERE that text came from (which page). This
page-level metadata is what makes citations possible later — without it,
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
        [{"page_number": 1, "locator_type": "page", "text": "..."}, ...]

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
    one blob: DOCX files have no fixed page boundaries — "pages" depend
    on fonts, margins, and the rendering engine. Paragraph index is the
    finest *deterministic* locator we can extract from the XML, and it
    gives downstream citation code a stable reference to point users back
    to a specific location in the source document.

    Returns a list of dicts like:
        [{"page_number": 1, "locator_type": "paragraph_index", "text": "..."}, ...]

    The "page_number" field holds the 1-indexed paragraph position.
    "locator_type" tells downstream consumers this is a structural proxy,
    not a true rendered page — so the citation UI can render "¶12"
    instead of "Page 12".

    Empty paragraphs (whitespace-only, blank lines between sections) are
    skipped, same rationale as load_pdf skipping pages with no text.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"No file found at {file_path}")

    doc = Document(str(path))
    paragraphs = []

    for i, para in enumerate(doc.paragraphs):
        text = para.text
        if text and text.strip():
            paragraphs.append({
                # i + 1 keeps the TRUE position in the document, not a dense count.
                # This matches load_pdf's pattern: if paragraphs 2 and 3 are blank,
                # the next non-empty paragraph is still "¶4", not "¶2". Without this,
                # citation traceability breaks — the user can't count to the right
                # paragraph in the source file.
                "page_number": i + 1,
                "locator_type": "paragraph_index",  # not a true page — see docstring
                "text": text.strip()
            })

    return paragraphs


if __name__ == "__main__":
    # Quick manual test — run this file directly with a sample PDF to sanity check.
    # Usage: python loaders.py path/to/test.pdf
    import sys

    if len(sys.argv) < 2:
        print("Usage: python loaders.py <path_to_pdf>")
        sys.exit(1)

    result = load_pdf(sys.argv[1])
    print(f"Extracted {len(result)} pages with text.")
    if result:
        print(f"--- Page {result[0]['page_number']} preview ---")
        print(result[0]["text"][:300])
