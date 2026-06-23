"""
loaders.py - Phase 1, Chunk 1 (updated with OCR fallback)

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

OCR FALLBACK (added Phase 3):
Some PDFs contain scanned images rather than embedded text. pypdf returns
an empty string for these pages. When that happens, load_pdf now falls
back to OCR via Tesseract (pytesseract + pdf2image) on a per-page basis:
- Pages with extractable text: fast path, no OCR
- Pages with no extractable text: converted to image, run through OCR
This means one PDF can mix text-based and image-based pages and both
will be handled correctly.

WHY HARDCODE THE TESSERACT PATH ON WINDOWS:
pytesseract.pytesseract.tesseract_cmd lets us point directly at the
executable rather than relying on it being on PATH. On Windows, PATH
changes often don't propagate reliably across shells, making the
hardcoded path the more robust choice for a dev environment.
"""

import os
from pathlib import Path

from pypdf import PdfReader
from docx import Document

# --- OCR setup ---
# Tesseract must be installed at the system level. On Windows it's not
# always on PATH even after installation, so we point directly at the
# executable. If the path is wrong or Tesseract isn't installed, OCR
# will raise a clear TesseractNotFoundError rather than silently
# returning empty text.
try:
    import pytesseract
    from pdf2image import convert_from_path

    # Windows default install location — adjust if you installed elsewhere
    TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


def _ocr_pdf_page(file_path: str, page_number: int) -> str:
    """
    Run OCR on a single page of a PDF and return the extracted text.

    Args:
        file_path:   Path to the PDF file.
        page_number: 1-indexed page number to OCR.

    Returns:
        Extracted text string, or empty string if OCR produces nothing.

    WHY PER-PAGE OCR, NOT WHOLE-DOCUMENT:
    Converting the entire PDF to images upfront is memory-intensive for
    large documents. Per-page conversion means we only pay the cost for
    pages that actually need OCR — most documents are either fully
    text-based (no OCR needed at all) or have just a few image pages.
    """
    if not OCR_AVAILABLE:
        return ""

    try:
        # Convert only the specific page to an image (first_page and
        # last_page are 1-indexed, matching our page_number convention)
        images = convert_from_path(
            file_path,
            first_page=page_number,
            last_page=page_number,
            dpi=300,  # 300 DPI is the standard for readable OCR output;
                      # lower DPI degrades accuracy, higher adds cost with
                      # diminishing returns
            poppler_path=r"C:\Program Files\poppler-26.02.0\Library\bin",
        )
        if not images:
            return ""

        text = pytesseract.image_to_string(images[0])
        return text.strip()

    except Exception:
        # OCR failure on one page shouldn't crash the whole ingestion —
        # return empty string and let the caller decide whether to skip.
        return ""


def load_pdf(file_path: str) -> list[dict]:
    """
    Extract text from a PDF, one entry per page.

    For pages with extractable text (text-based PDFs), uses pypdf directly.
    For pages with no extractable text (scanned/image PDFs), falls back to
    Tesseract OCR via pdf2image if available.

    Returns a list of dicts like:
        [{"page_number": 2, "locator_type": "page", "text": "..."}, ...]

    Pages with no text after both extraction and OCR are skipped.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"No file found at {file_path}")

    reader = PdfReader(str(path))
    pages = []

    for i, page in enumerate(reader.pages):
        page_number = i + 1  # 1-indexed, matches how humans reference pages
        text = page.extract_text()

        if text and text.strip():
            # Fast path — page has real embedded text, no OCR needed
            pages.append({
                "page_number": page_number,
                "locator_type": "page",
                "text": text.strip(),
            })
        else:
            # Fallback — page appears to be image-only, try OCR
            ocr_text = _ocr_pdf_page(file_path, page_number)
            if ocr_text:
                pages.append({
                    "page_number": page_number,
                    "locator_type": "page",
                    "text": ocr_text,
                })
            # If OCR also returns nothing, skip the page entirely —
            # same behavior as the original loader for empty pages

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

    for i, para in enumerate(doc.paragraphs):
        text = para.text
        if text and text.strip():
            paragraphs.append({
                "page_number": i + 1,
                "locator_type": "paragraph_index",
                "text": text.strip(),
            })

    return paragraphs


if __name__ == "__main__":
    # Quick manual test - run this file directly with a sample file to sanity check.
    # Usage: python -m app.ingestion.loaders path/to/test.pdf
    #        python -m app.ingestion.loaders path/to/test.docx
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m app.ingestion.loaders <path_to_file>")
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
        print(f"--- {label.capitalize()} {first['page_number']} "
              f"(locator_type={first['locator_type']}) preview ---")
        print(first["text"][:300])