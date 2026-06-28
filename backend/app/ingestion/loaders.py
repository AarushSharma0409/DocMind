"""
loaders.py - Phase 1, Chunk 1 (updated with OCR fallback + cross-platform paths)

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

CROSS-PLATFORM PATH HANDLING (added Phase 5):
Tesseract and Poppler were previously hardcoded to Windows paths
(C:\\Program Files\\...). In the Docker container (Ubuntu), these
binaries are installed via apt-get and available on PATH — no explicit
path needed. On Windows (local dev), we still point directly at the
executables since PATH propagation is unreliable there.

The detection is os.name == 'nt' (Windows) vs anything else (Linux/Mac).
On non-Windows: pass no path to pytesseract (uses PATH), and omit
poppler_path from convert_from_path (also uses PATH).
On Windows: use the hardcoded install locations.
"""

import os
import sys
from pathlib import Path

from pypdf import PdfReader
from docx import Document

# --- OCR setup ---
# On Linux (Docker): Tesseract and Poppler are on PATH via apt-get.
# On Windows (dev):  PATH is unreliable, so we point at the executables directly.
#
# IS_WINDOWS controls which path strategy is used throughout this file.
IS_WINDOWS = os.name == "nt"

# Windows install paths — adjust if you installed to a non-default location
_TESSERACT_WIN = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_POPPLER_WIN   = r"C:\Program Files\poppler-26.02.0\Library\bin"

try:
    import pytesseract
    from pdf2image import convert_from_path

    if IS_WINDOWS and os.path.exists(_TESSERACT_WIN):
        # Point directly at the executable — avoids PATH propagation issues
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_WIN
    # On Linux: pytesseract finds tesseract on PATH automatically, no config needed

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

    POPPLER PATH:
    On Windows: passed explicitly as _POPPLER_WIN (PATH unreliable).
    On Linux:   omitted entirely — pdf2image finds pdftoppm on PATH.
    """
    if not OCR_AVAILABLE:
        return ""

    try:
        convert_kwargs = dict(
            first_page=page_number,
            last_page=page_number,
            dpi=300,  # 300 DPI is the standard for readable OCR output
        )
        if IS_WINDOWS:
            convert_kwargs["poppler_path"] = _POPPLER_WIN

        images = convert_from_path(file_path, **convert_kwargs)
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
            # If OCR also returns nothing, skip the page entirely

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

    The "page_number" field holds the 1-indexed paragraph TRUE position —
    not a count of only non-empty ones. Blank paragraphs are skipped from
    the output but not from the index count, so citation traceability is
    preserved.
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